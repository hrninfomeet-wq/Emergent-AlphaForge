"""Conditional-order market data boundary. No credentials or order operations.

JSON instrument metadata supplies identity/lot/freeze/segment. The official CSV
supplies the rupee tick, joined by full instrument key and contract identity:
the JSON documentation does not explicitly state tick units. We never infer a
tick from the underlying or silently use a default. Live execution independently
cross-checks the resulting tick against Flattrade SearchScrip.
"""
from __future__ import annotations

import asyncio
import csv
import gzip
import io
import json
import math
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

IST = timezone(timedelta(hours=5, minutes=30))
MASTER_ROOT = "https://assets.upstox.com/market-quote/instruments/exchange/"
SUPPORTED = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
# Translation of provider segment nomenclature, NOT an underlying->segment rule.
BROKER_SEGMENTS = {"NSE_FO": "NFO", "BSE_FO": "BFO"}


def positive(value: Any, field: str, *, integer: bool = False) -> float | int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive number")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{field} missing or invalid") from None
    if not math.isfinite(number) or number <= 0 or (integer and not number.is_integer()):
        raise ValueError(f"{field} must be positive{' integer' if integer else ''}")
    return int(number) if integer else number


def normalize_contract(row: dict, tick_row: dict, *, asof: float) -> dict:
    """Join current provider masters, refusing ambiguous unit/identity changes."""
    key = str(row.get("instrument_key") or "")
    segment = str(row.get("segment") or "")
    underlying = str(row.get("underlying_symbol") or "").upper()
    option_type = row.get("instrument_type")
    if underlying not in SUPPORTED or option_type not in ("CE", "PE"):
        raise ValueError("unsupported contract")
    if segment not in BROKER_SEGMENTS or not key.startswith(segment + "|"):
        raise ValueError("master segment/key mismatch")
    if row.get("exchange") != segment.split("_", 1)[0]:
        raise ValueError("master exchange/segment mismatch")
    expiry = datetime.fromtimestamp(positive(row.get("expiry"), "expiry") / 1000, IST).date().isoformat()
    strike = positive(row.get("strike_price"), "strike")
    lot = positive(row.get("lot_size"), "lot_size", integer=True)
    freeze = positive(row.get("freeze_quantity"), "freeze_quantity", integer=True)
    if freeze < lot:
        raise ValueError("freeze quantity below one lot")
    if (tick_row.get("instrument_key") != key or tick_row.get("exchange") != segment
            or tick_row.get("option_type") != option_type
            or tick_row.get("expiry") != expiry
            or positive(tick_row.get("strike"), "CSV strike") != strike
            or positive(tick_row.get("lot_size"), "CSV lot", integer=True) != lot):
        raise ValueError("JSON/CSV contract identity mismatch")
    tick = positive(tick_row.get("tick_size"), "CSV rupee tick")
    raw_tick = positive(row.get("tick_size"), "JSON tick")
    # Check the two documented representations against each other, without
    # selecting a hardcoded tick or an assumed underlying-specific increment.
    if not (math.isclose(raw_tick, tick, rel_tol=1e-12)
            or math.isclose(raw_tick, tick * 100, rel_tol=1e-12)):
        raise ValueError("JSON/CSV tick units disagree")
    underlying_key = str(row.get("underlying_key") or "")
    if not underlying_key or "|" not in underlying_key:
        raise ValueError("underlying key missing")
    return dict(instrument_key=key, underlying=underlying, option_type=option_type,
                expiry=expiry, strike=strike, lot_size=lot, tick_size=tick,
                freeze_quantity=freeze, segment=segment,
                exchange=BROKER_SEGMENTS[segment], underlying_key=underlying_key,
                trading_symbol=str(row.get("trading_symbol") or ""),
                metadata_asof=asof, metadata_source="upstox_json_csv_identity_join")


async def _download(url: str) -> tuple[bytes, float]:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        response = await client.get(url)
        response.raise_for_status()
    modified = response.headers.get("last-modified")
    if not modified:
        raise ValueError("instrument master has no Last-Modified freshness evidence")
    return response.content, parsedate_to_datetime(modified).timestamp()


def _decode(data: bytes) -> str:
    return (gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data).decode("utf-8-sig")


class ProductionMarket:
    def __init__(self, *, downloader=None, stream=None, clock=time.time,
                 refresh_seconds: float = 3600, max_master_age: float = 36 * 3600):
        self._download = downloader or _download
        self._stream = stream
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._max_master_age = max_master_age
        self._loaded = 0.0
        self._rows: list[dict] = []
        self._lock = asyncio.Lock()
        self._entry_queue = None
        self._entry_history = defaultdict(lambda: deque(maxlen=2048))
        self._sequence = 0
        self._history_gap = 0.0

    def _manager(self):
        if self._stream is None:
            from app.runtime import upstox_stream_manager
            self._stream = upstox_stream_manager
        return self._stream

    async def contracts(self, underlying: str) -> list[dict]:
        if underlying not in SUPPORTED:
            raise ValueError("unsupported underlying")
        now = self._clock()
        if not self._rows or now - self._loaded >= self._refresh_seconds:
            async with self._lock:
                if not self._rows or now - self._loaded >= self._refresh_seconds:
                    blobs = await asyncio.gather(
                        self._download(MASTER_ROOT + "complete.json.gz"),
                        self._download(MASTER_ROOT + "complete.csv.gz"))
                    for _, asof in blobs:
                        if asof > now + 300 or now - asof > self._max_master_age:
                            raise ValueError("instrument master is stale or future-dated")
                    raw = json.loads(_decode(blobs[0][0]))
                    if not isinstance(raw, list):
                        raise ValueError("instrument JSON must be an array")
                    csv_rows: dict[str, dict] = {}
                    duplicates = set()
                    for row in csv.DictReader(io.StringIO(_decode(blobs[1][0]))):
                        key = row.get("instrument_key")
                        if key in csv_rows:
                            duplicates.add(key)
                        csv_rows[key] = row
                    selected, seen = [], set()
                    today = datetime.fromtimestamp(now, IST).date().isoformat()
                    for row in raw:
                        if (row.get("underlying_symbol") not in SUPPORTED
                                or row.get("instrument_type") not in ("CE", "PE")):
                            continue
                        key = row.get("instrument_key")
                        if key in seen or key in duplicates:
                            raise ValueError("duplicate instrument-master identity")
                        seen.add(key)
                        contract = normalize_contract(row, csv_rows.get(key, {}),
                                                      asof=min(blobs[0][1], blobs[1][1]))
                        if contract["expiry"] >= today:
                            selected.append(contract)
                    if not selected:
                        raise ValueError("instrument master has no usable index options")
                    self._rows, self._loaded = selected, now
        result = [dict(row) for row in self._rows if row["underlying"] == underlying]
        if not result:
            raise ValueError("no current contracts for underlying")
        if any(now - row["metadata_asof"] > self._max_master_age for row in result):
            raise ValueError("cached instrument master is stale")
        return result

    async def ensure_subscription(self, contracts: list[dict | str]) -> None:
        if self._entry_queue is None:
            self._entry_queue = self._manager().subscribe(max_queue=8192)
        keys = sorted({key for c in contracts for key in
                       ((c,) if isinstance(c, str) else
                        (c.get("instrument_key"), c.get("underlying_key"))) if key})
        await self._manager().ensure_instruments(keys)

    async def entry_snapshots(self, spec, reference_contract, after_ts):
        """Replay received spot edges in order, with premium known at that edge.

        Bounded-queue overflow invalidates the crossing baseline. It cannot be
        interpreted as a crossing over ticks that were not observed.
        """
        await self.ensure_subscription([reference_contract])
        queue = self._entry_queue
        if queue.full():
            self._entry_history.clear()
            self._history_gap = self._clock()
        while not queue.empty():
            row = queue.get_nowait()
            try:
                price = positive(row.get('last_price'), 'tick price')
                stamp = positive(row.get('ingest_ts'), 'tick timestamp') / 1000
                key = row['instrument_key']
            except (ValueError, KeyError, TypeError):
                continue
            self._sequence += 1
            self._entry_history[key].append({'price': price, 'ts': stamp, 'seq': self._sequence})
        spots = self._entry_history[reference_contract['underlying_key']]
        premiums = self._entry_history[reference_contract['instrument_key']]
        frames = []
        history_lost = bool(spots and after_ts and spots[0]['ts'] > after_ts and len(spots) == spots.maxlen)
        for spot in spots:
            if spot['ts'] <= (after_ts or 0):
                continue
            reference = next((p for p in reversed(premiums) if p['seq'] <= spot['seq']), None)
            frames.append({'spot': spot, 'reference': reference,
                           'reset_cross': history_lost or (after_ts or 0) < self._history_gap})
            history_lost = False
        # A premium-only/level condition can become true without a new spot tick.
        # Cross mode still requires a new timestamp in the engine.
        current = await self.snapshot(spec, reference_contract=reference_contract)
        current['reset_cross'] = (after_ts or 0) < self._history_gap
        frames.append(current)
        return frames

    async def close(self):
        if self._entry_queue is not None:
            self._manager().unsubscribe(self._entry_queue)
            self._entry_queue = None

    async def snapshot(self, spec: dict, reference_contract=None, execution_contract=None) -> dict:
        contracts = [c for c in (reference_contract, execution_contract) if c]
        if not contracts:
            available = await self.contracts(spec["underlying"])
            spot_key = available[0]["underlying_key"]
            await self.ensure_subscription([spot_key])
        else:
            await self.ensure_subscription(contracts)
            spot_key = contracts[0]["underlying_key"]
        ticks = self._manager().latest_tick_map()
        now = self._clock()
        def read(key):
            tick = ticks.get(key) or {}
            try:
                price = positive(tick.get("last_price"), "tick price")
                stamp = positive(tick.get("ingest_ts"), "local tick timestamp") / 1000
            except ValueError:
                return {"price": None, "ts": None}
            # Never promote broker timestamps or missing timestamps to fresh.
            if stamp > now + 1:
                return {"price": None, "ts": None}
            return {"price": price, "ts": stamp}
        return {"spot": read(spot_key),
                "reference": read((reference_contract or {}).get("instrument_key")),
                "execution": read((execution_contract or {}).get("instrument_key"))}
