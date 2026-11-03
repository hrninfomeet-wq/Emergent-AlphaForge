"""Read-only chart catalogue and bounded history, independent of execution symbols.

Public master and candle caches live in memory only. Broker access is exclusively
the existing authenticated GET helper; no login, subscriptions, or persistence.
"""
from __future__ import annotations

import asyncio
import copy
import gzip
import io
import json
import math
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote

import httpx

IST = timezone(timedelta(hours=5, minutes=30))
DAY = 86400
TIMEFRAME_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900,
                     "30m": 1800, "1h": 3600, "1d": DAY, "1w": 7 * DAY}
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
MAX_HISTORY_BARS = 2000
MAX_MASTER_BYTES = 96 * 1024 * 1024
MAX_MASTER_ROWS = 300000
MAX_HISTORY_REQUESTS = 3  # plus one current-day request
MAX_WAREHOUSE_DAYS = 90  # below the existing loader's 200,000-row cap
MAX_RESPONSE_ROWS = 20000
CATALOG_TTL = 3600
CATALOG_RETRY = 60
HISTORY_CACHE_SIZE = 48
NSE_EQUITY_TYPES = frozenset({"EQ", "BE", "BZ", "SM", "ST", "SZ", "E1", "IV", "RR", "IT"})
BSE_EQUITY_TYPES = frozenset({"EQ", "A", "B", "X", "XT", "T", "Z", "P", "ZP",
                              "M", "MT", "TS", "MS", "R", "E", "IF"})

_SAVED = (
    ("NIFTY", "Nifty 50", "NSE_INDEX|Nifty 50"),
    ("BANKNIFTY", "Nifty Bank", "NSE_INDEX|Nifty Bank"),
    ("SENSEX", "SENSEX", "BSE_INDEX|SENSEX"),
)


def _saved_instruments() -> dict[str, dict]:
    return {key: {"instrument_key": key, "symbol": symbol, "name": name,
                  "exchange": key[:3], "segment": key.split("|", 1)[0],
                  "kind": "index", "volume_available": False, "warehouse_symbol": symbol}
            for symbol, name, key in _SAVED}


def chart_bucket_time(seconds: float, timeframe: str) -> int:
    """UTC seconds: intraday anchored at 09:15 IST, days/weeks at IST midnight."""
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError("Unsupported chart timeframe")
    if isinstance(seconds, bool) or not math.isfinite(float(seconds)):
        raise ValueError("Chart timestamp must be finite UTC seconds")
    dt = datetime.fromtimestamp(float(seconds), IST)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if timeframe == "1w":
        return int((midnight - timedelta(days=dt.weekday())).timestamp())
    if timeframe == "1d":
        return int(midnight.timestamp())
    anchor = int(midnight.timestamp()) + (9 * 60 + 15) * 60
    interval = TIMEFRAME_SECONDS[timeframe]
    return anchor + math.floor((float(seconds) - anchor) / interval) * interval


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp(value: Any, *, milliseconds: bool = False) -> int:
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Timezone missing")
        return int(dt.timestamp())
    number = _finite(value)
    if number is None or number <= 0:
        raise ValueError("Invalid timestamp")
    # Warehouse timestamps are explicitly milliseconds. Broker numeric stamps
    # are seconds; do not silently guess the unit of malformed vendor data.
    return int(number / 1000 if milliseconds else number)


def _bar(stamp: int, values: list, volume_available: bool) -> dict | None:
    prices = [_finite(v) for v in values[:4]]
    if len(prices) != 4 or any(v is None or v <= 0 for v in prices):
        return None
    o, h, l, c = prices
    if h < max(o, l, c) or l > min(o, h, c):
        return None
    volume = _finite(values[4]) if len(values) > 4 and volume_available else None
    if volume is not None and volume < 0:
        volume = None
    return {"time": stamp, "open": o, "high": h, "low": l, "close": c, "volume": volume}


def _aggregate(rows: list[dict], timeframe: str) -> list[dict]:
    """Deduplicate before aggregation so overlapping responses cannot double volume."""
    unique = {row["time"]: row for row in rows}
    buckets: dict[int, dict] = {}
    for stamp in sorted(unique):
        row = unique[stamp]
        bucket = chart_bucket_time(stamp, timeframe)
        if bucket not in buckets:
            buckets[bucket] = {**row, "time": bucket}
            continue
        out = buckets[bucket]
        out["high"] = max(out["high"], row["high"])
        out["low"] = min(out["low"], row["low"])
        out["close"] = row["close"]
        out["volume"] = (out["volume"] + row["volume"]
                         if out["volume"] is not None and row["volume"] is not None else None)
    return list(buckets.values())


def _instrument(row: Any, today: str) -> dict | None:
    if not isinstance(row, dict):
        return None
    segment = str(row.get("segment") or "")
    key = str(row.get("instrument_key") or "")
    exchange = segment.split("_", 1)[0]
    typ = str(row.get("instrument_type") or "").upper()
    if (exchange not in {"NSE", "BSE"} or row.get("exchange") != exchange
            or not key.startswith(segment + "|") or len(key) > 200 or key.count("|") != 1):
        return None
    if segment in {"NSE_INDEX", "BSE_INDEX"} and typ == "INDEX":
        kind = "index"
    elif (segment == "NSE_EQ" and typ in NSE_EQUITY_TYPES
          or segment == "BSE_EQ" and typ in BSE_EQUITY_TYPES):
        kind = "equity"
    elif segment in {"NSE_FO", "BSE_FO"} and typ in {"CE", "PE", "FUT"}:
        kind = "future" if typ == "FUT" else "option"
    else:
        return None
    symbol = str(row.get("trading_symbol") or "").strip()
    if not symbol:
        return None
    result = {"instrument_key": key, "symbol": symbol, "name": str(row.get("name") or symbol),
              "exchange": exchange, "segment": segment, "kind": kind,
              "volume_available": kind != "index"}
    token = str(row.get("exchange_token") or "").strip()
    if token:
        result["exchange_token"] = token
    underlying = str(row.get("underlying_symbol") or "").strip()
    if underlying:
        result["underlying"] = underlying
    if kind in {"option", "future"}:
        try:
            expiry = datetime.fromtimestamp(_timestamp(row.get("expiry"), milliseconds=True), IST).date().isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            return None
        if expiry < today:
            return None
        result["expiry"] = expiry
        if kind == "option":
            strike = _finite(row.get("strike_price"))
            if strike is None or strike <= 0:
                return None
            result.update(strike=strike, option_type=typ)
    lot = _finite(row.get("lot_size"))
    if lot is not None and lot > 0 and lot.is_integer():
        result["lot_size"] = int(lot)
    # JSON tick_size units are undocumented. The field is optional; do not
    # mislabel its paise-looking value as rupees on a read-only chart.
    return result


async def _download_master() -> list:
    blob = bytearray()
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        async with client.stream("GET", MASTER_URL) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                blob.extend(chunk)
                if len(blob) > MAX_MASTER_BYTES:
                    raise ValueError("Instrument master exceeds memory limit")
    if blob[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as handle:
            decoded = handle.read(MAX_MASTER_BYTES + 1)
    else:
        decoded = bytes(blob)
    if len(decoded) > MAX_MASTER_BYTES:
        raise ValueError("Instrument master exceeds memory limit")
    rows = json.loads(decoded)
    if not isinstance(rows, list) or len(rows) > MAX_MASTER_ROWS:
        raise ValueError("Invalid instrument master")
    return rows


async def _default_get(url: str) -> dict:
    from app.upstox_client import _authenticated_get
    return await _authenticated_get(url)


async def _default_warehouse(symbol: str, start_ts: int, end_ts: int):
    from app.db import get_db
    from app.warehouse import load_candles_df
    latest = await get_db().candles_1m.find_one(
        {"instrument": symbol.upper(), "ts": {"$lte": end_ts}}, {"_id": 0, "ts": 1},
        sort=[("ts", -1)])
    if not latest:
        return []
    # A disconnected account may have no recent candles. Anchor the same bounded
    # read at the last saved timestamp rather than hiding an older warehouse.
    latest_ts = min(end_ts, int(latest["ts"]))
    width = max(0, end_ts - start_ts)
    start_ts = min(start_ts, latest_ts - width)
    return await load_candles_df(symbol, start_ts=start_ts, end_ts=latest_ts,
                                 include_off_session=True)


class ChartDataService:
    def __init__(self, *, master_loader: Callable | None = None,
                 authenticated_get: Callable | None = None, warehouse_loader: Callable | None = None,
                 clock: Callable = time.time):
        self._master_loader = master_loader or _download_master
        self._get = authenticated_get or _default_get
        self._warehouse = warehouse_loader or _default_warehouse
        self._clock = clock
        self._catalog = _saved_instruments()
        self._catalog_loaded = None
        self._catalog_retry_at = 0.0
        self._catalog_warnings: list[str] = []
        self._catalog_lock = asyncio.Lock()
        self._history_slots = asyncio.Semaphore(2)
        self._history_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
        self._last_history_asof_ms = 0

    async def _ensure_catalog(self):
        now = self._clock()
        if (now < self._catalog_retry_at
                or self._catalog_loaded is not None and now - self._catalog_loaded < CATALOG_TTL):
            return
        async with self._catalog_lock:
            now = self._clock()
            if (now < self._catalog_retry_at
                    or self._catalog_loaded is not None and now - self._catalog_loaded < CATALOG_TTL):
                return
            try:
                rows = await self._master_loader()
                if not isinstance(rows, list) or not rows or len(rows) > MAX_MASTER_ROWS:
                    raise ValueError("Invalid instrument master")
                today = datetime.fromtimestamp(now, IST).date().isoformat()
                catalog, ambiguous = {}, set()
                for row in rows:
                    item = _instrument(row, today)
                    if item:
                        key = item["instrument_key"]
                        if key in catalog and catalog[key] != item:
                            ambiguous.add(key)
                        catalog[key] = item
                for key in ambiguous:
                    catalog.pop(key, None)
                if not catalog:
                    raise ValueError("No supported instruments")
                catalog.update(_saved_instruments())
                self._catalog = catalog
                self._catalog_loaded = now
                self._catalog_warnings = (["Ambiguous instrument identities were omitted from the catalogue."]
                                          if ambiguous else [])
            except Exception:
                # Do not disclose broker bodies, URLs, credentials or internals.
                self._catalog_retry_at = now + CATALOG_RETRY
                self._catalog_warnings = ["Instrument catalogue unavailable; saved indices and any cached instruments remain available."]

    def _current(self, item: dict) -> bool:
        today = datetime.fromtimestamp(self._clock(), IST).date().isoformat()
        return not item.get("expiry") or item["expiry"] >= today

    async def search(self, query: str = "", limit: int = 30) -> dict:
        limit = _bounded_int(limit, "Search limit", 1, 100)
        query = str(query or "").strip().upper()
        if len(query) > 120:
            raise ValueError("Search query is too long")
        await self._ensure_catalog()
        tokens = query.split()
        matches = []
        for item in self._catalog.values():
            if not self._current(item):
                continue
            text = " ".join(str(item.get(k, "")) for k in ("symbol", "name", "exchange", "segment", "instrument_key", "expiry", "option_type")).upper()
            if all(token in text for token in tokens):
                matches.append(item)
        def rank(item):
            symbol = item["symbol"].upper()
            return (0 if symbol == query else 1, 0 if item.get("warehouse_symbol") else 1,
                    0 if symbol.startswith(query) else 1,
                    {"index": 0, "equity": 1, "future": 2, "option": 3}[item["kind"]],
                    item.get("expiry", ""), symbol, item["instrument_key"])
        matches.sort(key=rank)
        return {"items": copy.deepcopy(matches[:limit]), "warnings": list(self._catalog_warnings),
                "as_of_ms": int(self._clock() * 1000)}

    async def resolve(self, key: str) -> dict:
        key = str(key or "").strip()
        if not key or len(key) > 200:
            raise ValueError("Unknown chart instrument")
        saved = _saved_instruments()
        aliases = {v["symbol"]: k for k, v in saved.items()}
        key = aliases.get(key.upper(), key)
        if key in saved:
            return saved[key]
        await self._ensure_catalog()
        item = self._catalog.get(key)
        if not item or not self._current(item):
            raise ValueError("Unknown or expired chart instrument; choose a current catalogue instrument")
        return dict(item)

    async def history(self, key: str, timeframe: str = "1m", limit: int = 600,
                      before: int | None = None) -> dict:
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported chart timeframe")
        limit = _bounded_int(limit, "History limit", 1, MAX_HISTORY_BARS)
        if before is not None:
            before = _bounded_int(before, "Before timestamp", 1, 253402300799)
        instrument = await self.resolve(key)
        cache_key = (instrument["instrument_key"], timeframe, limit, before)
        ttl = 15 if before is None or before >= chart_bucket_time(self._clock(), "1d") else 300
        def cached():
            entry = self._history_cache.get(cache_key)
            if entry and self._clock() - entry[0] < ttl:
                self._history_cache.move_to_end(cache_key)
                return copy.deepcopy(entry[1])
            return None
        result = cached()
        if result is not None:
            return result
        async with self._history_slots:
            result = cached()
            if result is not None:
                return result
            result = await self._history(instrument, timeframe, limit, before)
            self._history_cache[cache_key] = (self._clock(), copy.deepcopy(result))
            self._history_cache.move_to_end(cache_key)
            while len(self._history_cache) > HISTORY_CACHE_SIZE:
                self._history_cache.popitem(last=False)
            return result

    def peek_history(self, key: str, timeframe: str = "1m", limit: int = 600,
                     before: int | None = None, after_as_of_ms: float | None = None) -> dict | None:
        """No I/O: expose a detached last REST snapshot for stream reconciliation.

        Expired entries remain useful as a reconciliation watermark. This method
        does not refresh their TTL or claim that their market data is fresh.
        """
        key = str(key or "").strip()
        aliases = {symbol: instrument_key for symbol, _, instrument_key in _SAVED}
        key = aliases.get(key.upper(), key)
        entry = self._history_cache.get((key, timeframe, limit, before))
        if entry and after_as_of_ms is not None and entry[1].get("as_of_ms", 0) <= after_as_of_ms:
            return None  # Avoid copying hundreds of bars for every unchanged tick.
        return copy.deepcopy(entry[1]) if entry else None

    async def _history(self, instrument: dict, timeframe: str, limit: int, before: int | None) -> dict:
        now = self._clock()
        ceiling = min(before if before is not None else math.floor(now) + 1, math.floor(now) + 1)
        today = datetime.fromtimestamp(now, IST).date()
        end_day = datetime.fromtimestamp(ceiling - 1, IST).date()
        base_tf = "1d" if timeframe == "1w" else timeframe
        span = _lookback_days(timeframe, limit + 1)
        floor_day = date(2000, 1, 1) if base_tf == "1d" else date(2022, 1, 1)
        start_day = max(floor_day, end_day - timedelta(days=span))
        if timeframe == "1w":
            start_day -= timedelta(days=start_day.weekday())
        saved, remote = {}, {}
        warnings = list(self._catalog_warnings) if not instrument.get("warehouse_symbol") else []
        read_floor = int(datetime.combine(start_day, datetime.min.time(), IST).timestamp())
        if instrument.get("warehouse_symbol"):
            warehouse_start = max(start_day, end_day - timedelta(days=MAX_WAREHOUSE_DAYS - 1))
            if timeframe == "1w":
                # Stay inside the read cap while retaining whole weekly buckets.
                warehouse_start += timedelta(days=(-warehouse_start.weekday()) % 7)
            try:
                frame = await self._warehouse(instrument["warehouse_symbol"],
                    int(datetime.combine(warehouse_start, datetime.min.time(), IST).timestamp()) * 1000,
                    ceiling * 1000 - 1)
                raw = frame.to_dict(orient="records") if hasattr(frame, "to_dict") else list(frame or [])
                rows, invalid = _warehouse_bars(raw, ceiling)
                saved = {b["time"]: b for b in _aggregate(rows, base_tf)}
                if invalid:
                    warnings.append("Invalid saved candles were omitted.")
                if any(not _regular_time(r["time"]) for r in rows):
                    warnings.append("Saved candles include times outside regular hours; these may be special sessions or feed artifacts.")
                if rows and max(r["time"] for r in rows) < read_floor:
                    warnings.append("The latest saved candles predate the recent request window.")
            except Exception:
                warnings.append("Saved chart history is unavailable.")
        encoded = quote(instrument["instrument_key"], safe="")
        from app.upstox_client import _base_url
        base = _base_url().rstrip("/") + "/v3/historical-candle"
        unit, interval = _native_interval(base_tf)
        failed = False
        if end_day >= today:
            try:
                payload = await self._get(f"{base}/intraday/{encoded}/{unit}/{interval}")
                rows, invalid = _broker_bars(payload, base_tf, instrument["volume_available"], ceiling)
                # An intraday response must not replace an older session.
                rows = [r for r in rows if datetime.fromtimestamp(r["time"], IST).date() == today]
                remote.update((r["time"], r) for r in rows)
                if invalid:
                    warnings.append("Invalid or misaligned broker candles were omitted.")
            except Exception:
                failed = True
        cursor = min(end_day, today - timedelta(days=1))
        max_span = 3600 if base_tf == "1d" else (28 if TIMEFRAME_SECONDS[base_tf] <= 900 else 84)
        # Use at most three bounded historical windows. Each is within the
        # documented V3 interval-specific range; pagination can continue later.
        for _ in range(MAX_HISTORY_REQUESTS):
            if failed or cursor < floor_day:
                break
            window_start = max(floor_day, cursor - timedelta(days=min(span, max_span) - 1))
            if timeframe == "1w":
                window_start += timedelta(days=(-window_start.weekday()) % 7)
                if window_start > cursor:
                    window_start -= timedelta(days=7)
            read_floor = int(datetime.combine(window_start, datetime.min.time(), IST).timestamp())
            try:
                payload = await self._get(f"{base}/{encoded}/{unit}/{interval}/{cursor.isoformat()}/{window_start.isoformat()}")
                rows, invalid = _broker_bars(payload, base_tf, instrument["volume_available"], ceiling)
                rows = [r for r in rows if window_start <= datetime.fromtimestamp(r["time"], IST).date() <= cursor]
                # Current-day data already in remote has no historical overlap.
                remote.update((r["time"], r) for r in rows)
                if invalid:
                    warnings.append("Invalid or misaligned broker candles were omitted.")
            except Exception:
                failed = True
                break
            combined = _aggregate(_merge_sources(saved, remote, base_tf, now), timeframe)
            if len([r for r in combined if r["time"] < ceiling]) > limit:
                break
            cursor = window_start - timedelta(days=1)
        if failed:
            warnings.append("Upstox candle data is unavailable; saved history remains available where present.")
        combined = _aggregate(_merge_sources(saved, remote, base_tf, now), timeframe)
        combined = [b for b in combined if b["time"] < ceiling]
        has_extra = len(combined) > limit
        bars = combined[-limit:]
        # The extra bar proves more history. Otherwise an earlier bounded window
        # may still contain data; advertise it honestly, without fabricating bars.
        floor_seconds = int(datetime.combine(floor_day, datetime.min.time(), IST).timestamp())
        can_look_earlier = read_floor > floor_seconds and bool(bars)
        has_more = has_extra or can_look_earlier
        if has_more and not has_extra:
            warnings.append("History retrieval is bounded; earlier candles may be available.")
        if saved and base_tf != "1m":
            warnings.append("Saved candles are aggregated from available minutes; missing minutes are not synthesized.")
        if saved and remote:
            warnings.append("Saved finalized buckets take precedence; Upstox fills gaps and reconciles the currently forming bucket.")
        if not instrument["volume_available"]:
            warnings.append("Volume is unavailable for indices.")
        source = "warehouse+upstox" if saved and remote else "warehouse" if saved else "upstox" if remote else "unavailable"
        # A strictly increasing completion watermark distinguishes two refreshes
        # that finish in the same millisecond for the shared stream accumulator.
        self._last_history_asof_ms = max(int(self._clock() * 1000), self._last_history_asof_ms + 1)
        return {"instrument": instrument, "timeframe": timeframe, "bars": bars, "source": source,
                "warnings": list(dict.fromkeys(warnings)), "next_before": bars[0]["time"] if bars and has_more else None,
                "has_more": has_more, "as_of_ms": self._last_history_asof_ms,
                "volume_available": instrument["volume_available"]}


def _bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    number = _finite(value)
    if number is None or not number.is_integer() or not minimum <= number <= maximum:
        raise ValueError(f"{label} must be an integer between {minimum} and {maximum}")
    return int(number)


def _lookback_days(timeframe: str, limit: int) -> int:
    if timeframe == "1w":
        return limit * 7 + 14
    if timeframe == "1d":
        return limit * 2 + 14
    bars_per_day = math.ceil(375 * 60 / TIMEFRAME_SECONDS[timeframe])
    return max(14, math.ceil(limit / bars_per_day) * 2 + 7)


def _native_interval(timeframe: str) -> tuple[str, int]:
    if timeframe == "1d":
        return "days", 1
    if timeframe == "1h":
        return "hours", 1
    return "minutes", TIMEFRAME_SECONDS[timeframe] // 60


def _merge_sources(saved: dict, remote: dict, timeframe: str, now: float) -> list[dict]:
    current_bucket = chart_bucket_time(now, timeframe)
    combined = {**remote, **saved}
    # Keep the warehouse's finalized history reproducible. A forming bucket can
    # be partial when the roller attached late; REST reconciliation is explicit.
    for stamp, row in remote.items():
        if stamp >= current_bucket:
            combined[stamp] = row
    return list(combined.values())


def _regular_time(stamp: int) -> bool:
    local = datetime.fromtimestamp(stamp, IST)
    return 9 * 60 + 15 <= local.hour * 60 + local.minute < 15 * 60 + 30


def _warehouse_bars(rows: list, ceiling: int) -> tuple[list[dict], bool]:
    out, invalid = {}, False
    for row in rows:
        try:
            stamp = _timestamp(row.get("ts"), milliseconds=True)
            if stamp >= ceiling:
                continue
            item = _bar(stamp, [row.get(k) for k in ("open", "high", "low", "close", "volume")], False)
            if item is None or stamp != chart_bucket_time(stamp, "1m"):
                invalid = True
                continue
            out[stamp] = item
        except (TypeError, ValueError, OverflowError, OSError, AttributeError):
            invalid = True
    return list(out.values()), invalid


def _broker_bars(payload: dict, timeframe: str, volume_available: bool, ceiling: int) -> tuple[list[dict], bool]:
    if not isinstance(payload, dict) or payload.get("status", "success") != "success":
        raise ValueError("Invalid candle response")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("candles"), list):
        raise ValueError("Invalid candle response")
    rows = data["candles"]
    if len(rows) > MAX_RESPONSE_ROWS:
        raise ValueError("Candle response exceeds memory limit")
    out, invalid = {}, False
    for row in rows:
        try:
            if not isinstance(row, (tuple, list)) or len(row) < 5:
                raise ValueError("Invalid candle")
            stamp = _timestamp(row[0])
            if stamp >= ceiling:
                continue
            bucket = chart_bucket_time(stamp, timeframe)
            if timeframe != "1d" and stamp != bucket:
                raise ValueError("Native interval does not match chart alignment")
            item = _bar(bucket, list(row[1:6]), volume_available)
            if item is None:
                raise ValueError("Invalid OHLC")
            out[bucket] = item
        except (TypeError, ValueError, OverflowError, OSError):
            invalid = True
    return list(out.values()), invalid


_service: ChartDataService | None = None


def get_chart_data_service() -> ChartDataService:
    global _service
    if _service is None:
        _service = ChartDataService()
    return _service
