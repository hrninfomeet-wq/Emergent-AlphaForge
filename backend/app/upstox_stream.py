"""Upstox V3 market-data WebSocket stream helpers.

This module is read-only market data plumbing. It never places orders and it
does not return access tokens or raw broker frames to API clients.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import struct
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

try:
    from pymongo import UpdateOne
except ModuleNotFoundError:  # Local unit-test fallback; Docker installs pymongo.
    class UpdateOne:  # type: ignore[no-redef]
        def __init__(self, filter: Dict[str, Any], doc: Dict[str, Any], upsert: bool = False):
            self._filter = filter
            self._doc = doc
            self._upsert = upsert

log = logging.getLogger(__name__)

# AlphaForge's forward-evidence cohort needs the decision-time executable
# surface, not only the last trade.  Upstox Full is comfortably within the
# documented 1,500-key combined limit for our ATM-band universe and supplies
# five depth levels, OI/IV and Greeks.  Callers may still request ltpc explicitly.
DEFAULT_STREAM_MODE = "full"
ALLOWED_STREAM_MODES = {"ltpc", "full", "option_greeks", "full_d30"}
_MODE_TO_REQUEST_MODE = {
    0: "ltpc",
    1: "full",
    2: "option_greeks",
    3: "full_d30",
}
_TYPE_LABELS = {
    0: "initial_feed",
    1: "live_feed",
    2: "market_info",
}
_MARKET_STATUS_LABELS = {
    0: "PRE_OPEN_START",
    1: "PRE_OPEN_END",
    2: "NORMAL_OPEN",
    3: "NORMAL_CLOSE",
    4: "CLOSING_START",
    5: "CLOSING_END",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_subscription_message(guid: str, instrument_keys: Iterable[str], mode: str = DEFAULT_STREAM_MODE) -> bytes:
    """Build the binary JSON subscription message required by Upstox V3."""
    clean_keys = [str(key) for key in instrument_keys if str(key or "").strip()]
    if not clean_keys:
        raise ValueError("At least one instrument key is required")
    mode = str(mode or DEFAULT_STREAM_MODE).lower()
    if mode not in ALLOWED_STREAM_MODES:
        raise ValueError(f"Unsupported Upstox stream mode: {mode}")
    payload = {
        "guid": guid,
        "method": "sub",
        "data": {
            "mode": mode,
            "instrumentKeys": clean_keys,
        },
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class _ProtoReader:
    def __init__(self, data: bytes):
        self.data = bytes(data)
        self.pos = 0

    def eof(self) -> bool:
        return self.pos >= len(self.data)

    def read_varint(self) -> int:
        shift = 0
        result = 0
        while True:
            if self.pos >= len(self.data):
                raise ValueError("Unexpected end of protobuf varint")
            byte = self.data[self.pos]
            self.pos += 1
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result
            shift += 7
            if shift > 70:
                raise ValueError("Protobuf varint is too long")

    def read_double(self) -> float:
        if self.pos + 8 > len(self.data):
            raise ValueError("Unexpected end of protobuf double")
        value = struct.unpack("<d", self.data[self.pos:self.pos + 8])[0]
        self.pos += 8
        return value

    def read_bytes(self) -> bytes:
        size = self.read_varint()
        if self.pos + size > len(self.data):
            raise ValueError("Unexpected end of protobuf bytes")
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def read_string(self) -> str:
        return self.read_bytes().decode("utf-8", errors="replace")

    def skip(self, wire_type: int) -> None:
        if wire_type == 0:
            self.read_varint()
        elif wire_type == 1:
            self.pos += 8
        elif wire_type == 2:
            self.read_bytes()
        elif wire_type == 5:
            self.pos += 4
        else:
            raise ValueError(f"Unsupported protobuf wire type: {wire_type}")
        if self.pos > len(self.data):
            raise ValueError("Unexpected end while skipping protobuf field")


def _fields(data: bytes):
    reader = _ProtoReader(data)
    while not reader.eof():
        tag = reader.read_varint()
        yield tag >> 3, tag & 0x07, reader


def _decode_ltpc(data: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 1:
            out["ltp"] = reader.read_double()
        elif field == 2 and wire == 0:
            out["ltt"] = reader.read_varint()
        elif field == 3 and wire == 0:
            out["ltq"] = reader.read_varint()
        elif field == 4 and wire == 1:
            out["cp"] = reader.read_double()
        else:
            reader.skip(wire)
    return out


def _decode_quote(data: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 0:
            out["bidQ"] = reader.read_varint()
        elif field == 2 and wire == 1:
            out["bidP"] = reader.read_double()
        elif field == 3 and wire == 0:
            out["askQ"] = reader.read_varint()
        elif field == 4 and wire == 1:
            out["askP"] = reader.read_double()
        else:
            reader.skip(wire)
    return out


def _decode_option_greeks(data: bytes) -> Dict[str, Any]:
    names = {1: "delta", 2: "theta", 3: "gamma", 4: "vega", 5: "rho"}
    out: Dict[str, Any] = {}
    for field, wire, reader in _fields(data):
        if field in names and wire == 1:
            out[names[field]] = reader.read_double()
        else:
            reader.skip(wire)
    return out


def _decode_ohlc(data: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 2:
            out["interval"] = reader.read_string()
        elif field == 2 and wire == 1:
            out["open"] = reader.read_double()
        elif field == 3 and wire == 1:
            out["high"] = reader.read_double()
        elif field == 4 and wire == 1:
            out["low"] = reader.read_double()
        elif field == 5 and wire == 1:
            out["close"] = reader.read_double()
        elif field == 6 and wire == 0:
            out["vol"] = reader.read_varint()
        elif field == 7 and wire == 0:
            out["ts"] = reader.read_varint()
        else:
            reader.skip(wire)
    return out


def _decode_market_ohlc(data: bytes) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 2:
            rows.append(_decode_ohlc(reader.read_bytes()))
        else:
            reader.skip(wire)
    return {"ohlc": rows}


def _decode_market_level(data: bytes) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 2:
            rows.append(_decode_quote(reader.read_bytes()))
        else:
            reader.skip(wire)
    return {"bidAskQuote": rows}


def _decode_market_full_feed(data: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 2:
            out["ltpc"] = _decode_ltpc(reader.read_bytes())
        elif field == 2 and wire == 2:
            out["marketLevel"] = _decode_market_level(reader.read_bytes())
        elif field == 3 and wire == 2:
            out["optionGreeks"] = _decode_option_greeks(reader.read_bytes())
        elif field == 4 and wire == 2:
            out["marketOHLC"] = _decode_market_ohlc(reader.read_bytes())
        elif field == 5 and wire == 1:
            out["atp"] = reader.read_double()
        elif field == 6 and wire == 0:
            out["vtt"] = reader.read_varint()
        elif field == 7 and wire == 1:
            out["oi"] = reader.read_double()
        elif field == 8 and wire == 1:
            out["iv"] = reader.read_double()
        elif field == 9 and wire == 1:
            out["tbq"] = reader.read_double()
        elif field == 10 and wire == 1:
            out["tsq"] = reader.read_double()
        else:
            reader.skip(wire)
    return out


def _decode_index_full_feed(data: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 2:
            out["ltpc"] = _decode_ltpc(reader.read_bytes())
        elif field == 2 and wire == 2:
            out["marketOHLC"] = _decode_market_ohlc(reader.read_bytes())
        else:
            reader.skip(wire)
    return out


def _decode_full_feed(data: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 2:
            out["marketFF"] = _decode_market_full_feed(reader.read_bytes())
        elif field == 2 and wire == 2:
            out["indexFF"] = _decode_index_full_feed(reader.read_bytes())
        else:
            reader.skip(wire)
    return out


def _decode_first_level_with_greeks(data: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 2:
            out["ltpc"] = _decode_ltpc(reader.read_bytes())
        elif field == 2 and wire == 2:
            out["firstDepth"] = _decode_quote(reader.read_bytes())
        elif field == 3 and wire == 2:
            out["optionGreeks"] = _decode_option_greeks(reader.read_bytes())
        elif field == 4 and wire == 0:
            out["vtt"] = reader.read_varint()
        elif field == 5 and wire == 1:
            out["oi"] = reader.read_double()
        elif field == 6 and wire == 1:
            out["iv"] = reader.read_double()
        else:
            reader.skip(wire)
    return out


def _decode_feed(data: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    request_mode: Optional[int] = None
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 2:
            out["ltpc"] = _decode_ltpc(reader.read_bytes())
        elif field == 2 and wire == 2:
            out["fullFeed"] = _decode_full_feed(reader.read_bytes())
        elif field == 3 and wire == 2:
            out["firstLevelWithGreeks"] = _decode_first_level_with_greeks(reader.read_bytes())
        elif field == 4 and wire == 0:
            request_mode = reader.read_varint()
            out["requestMode"] = _MODE_TO_REQUEST_MODE.get(request_mode, str(request_mode))
        else:
            reader.skip(wire)
    return out


def _decode_feed_map_entry(data: bytes) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    key: Optional[str] = None
    feed: Optional[Dict[str, Any]] = None
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 2:
            key = reader.read_string()
        elif field == 2 and wire == 2:
            feed = _decode_feed(reader.read_bytes())
        else:
            reader.skip(wire)
    return key, feed


def _decode_segment_status_entry(data: bytes) -> tuple[Optional[str], Optional[str]]:
    key: Optional[str] = None
    value: Optional[str] = None
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 2:
            key = reader.read_string()
        elif field == 2 and wire == 0:
            value = _MARKET_STATUS_LABELS.get(reader.read_varint(), "UNKNOWN")
        else:
            reader.skip(wire)
    return key, value


def _decode_market_info(data: bytes) -> Dict[str, Any]:
    segment_status: Dict[str, str] = {}
    for field, wire, reader in _fields(data):
        if field == 1 and wire == 2:
            key, value = _decode_segment_status_entry(reader.read_bytes())
            if key and value:
                segment_status[key] = value
        else:
            reader.skip(wire)
    return {"segmentStatus": segment_status}


def decode_market_data_feed(payload: bytes) -> Dict[str, Any]:
    """Decode an Upstox V3 MarketDataFeed FeedResponse protobuf frame."""
    if not isinstance(payload, (bytes, bytearray)):
        raise ValueError("Upstox market feed payload must be bytes")
    decoded: Dict[str, Any] = {"type": "", "feeds": {}, "currentTs": None}
    for field, wire, reader in _fields(bytes(payload)):
        if field == 1 and wire == 0:
            decoded["type"] = _TYPE_LABELS.get(reader.read_varint(), "unknown")
        elif field == 2 and wire == 2:
            key, feed = _decode_feed_map_entry(reader.read_bytes())
            if key and feed is not None:
                decoded.setdefault("feeds", {})[key] = feed
        elif field == 3 and wire == 0:
            decoded["currentTs"] = reader.read_varint()
        elif field == 4 and wire == 2:
            decoded["marketInfo"] = _decode_market_info(reader.read_bytes())
        else:
            reader.skip(wire)
    return decoded


def _ltpc_from_feed(feed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if feed.get("ltpc"):
        return feed["ltpc"]
    first_level = feed.get("firstLevelWithGreeks") or {}
    if first_level.get("ltpc"):
        return first_level["ltpc"]
    full_feed = feed.get("fullFeed") or {}
    market_ff = full_feed.get("marketFF") or {}
    if market_ff.get("ltpc"):
        return market_ff["ltpc"]
    index_ff = full_feed.get("indexFF") or {}
    if index_ff.get("ltpc"):
        return index_ff["ltpc"]
    return None


def _day_ohlc_from_feed(feed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pull the day (1d) OHLC bucket from a feed's marketOHLC, if present.

    Only `full`/`full_d30` stream modes carry marketOHLC; `ltpc` does not. We
    pick the bucket whose interval denotes a full day ("1d"/"1day"/"I1") so the
    header tile can draw a session low->high range bar. Returns None when no
    day bucket is available.
    """
    full_feed = feed.get("fullFeed") or {}
    market_ohlc = (
        feed.get("marketOHLC")
        or (full_feed.get("marketFF") or {}).get("marketOHLC")
        or (full_feed.get("indexFF") or {}).get("marketOHLC")
        or {}
    )
    rows = market_ohlc.get("ohlc") if isinstance(market_ohlc, dict) else None
    if not rows:
        return None
    day_markers = {"1d", "1day", "i1", "day"}
    chosen = None
    for row in rows:
        interval = str(row.get("interval") or "").lower()
        if interval in day_markers:
            chosen = row
            break
    if chosen is None:
        # Fall back to the last bucket (Upstox orders intraday then day).
        chosen = rows[-1]
    high = _to_float(chosen.get("high"))
    low = _to_float(chosen.get("low"))
    open_ = _to_float(chosen.get("open"))
    if high is None and low is None:
        return None
    return {"open": open_, "high": high, "low": low}


def _market_surface_from_feed(feed: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten the auditable option surface already decoded from the V3 feed."""
    first = feed.get("firstLevelWithGreeks") or {}
    market = ((feed.get("fullFeed") or {}).get("marketFF") or {})
    depth = list(((market.get("marketLevel") or {}).get("bidAskQuote") or []))
    if not depth and first.get("firstDepth"):
        depth = [first["firstDepth"]]
    clean_depth = []
    for level in depth:
        clean = {
            "bid_quantity": _to_int(level.get("bidQ")),
            "bid_price": _to_float(level.get("bidP")),
            "ask_quantity": _to_int(level.get("askQ")),
            "ask_price": _to_float(level.get("askP")),
        }
        if any(value is not None for value in clean.values()):
            clean_depth.append(clean)
    out: Dict[str, Any] = {}
    if clean_depth:
        out["market_depth"] = clean_depth
        out.update({
            "best_bid_quantity": clean_depth[0].get("bid_quantity"),
            "best_bid_price": clean_depth[0].get("bid_price"),
            "best_ask_quantity": clean_depth[0].get("ask_quantity"),
            "best_ask_price": clean_depth[0].get("ask_price"),
        })
    greeks = market.get("optionGreeks") or first.get("optionGreeks") or {}
    clean_greeks = {
        key: _to_float(greeks.get(key))
        for key in ("delta", "theta", "gamma", "vega", "rho")
        if _to_float(greeks.get(key)) is not None
    }
    if clean_greeks:
        out["option_greeks"] = clean_greeks
    numeric_fields = {
        "average_trade_price": market.get("atp"),
        "volume_traded_today": market.get("vtt", first.get("vtt")),
        "open_interest": market.get("oi", first.get("oi")),
        "implied_volatility": market.get("iv", first.get("iv")),
        "total_buy_quantity": market.get("tbq"),
        "total_sell_quantity": market.get("tsq"),
    }
    for name, value in numeric_fields.items():
        parsed = _to_float(value)
        if parsed is not None:
            out[name] = parsed
    return out


def normalize_feed_response(decoded: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize decoded feed response into sanitized tick documents."""
    ticks: List[Dict[str, Any]] = []
    received_ts = _to_int(decoded.get("currentTs"))
    for instrument_key, feed in (decoded.get("feeds") or {}).items():
        ltpc = _ltpc_from_feed(feed or {})
        if not ltpc:
            continue
        ltp = _to_float(ltpc.get("ltp"))
        ts = _to_int(ltpc.get("ltt")) or received_ts
        if ltp is None or ts is None:
            continue
        tick = {
            "instrument_key": str(instrument_key),
            "ts": ts,
            "received_ts": received_ts or ts,
            "last_price": ltp,
            "last_trade_quantity": _to_int(ltpc.get("ltq")),
            "close_price": _to_float(ltpc.get("cp")),
            "source": "upstox_ws_v3",
            "mode": str((feed or {}).get("requestMode") or DEFAULT_STREAM_MODE),
        }
        tick.update(_market_surface_from_feed(feed or {}))
        day_ohlc = _day_ohlc_from_feed(feed or {})
        if day_ohlc:
            tick.update({
                "open": day_ohlc.get("open"),
                "high": day_ohlc.get("high"),
                "low": day_ohlc.get("low"),
            })
        ticks.append(tick)
    return ticks


async def persist_ticks(db: Any, ticks: List[Dict[str, Any]], session_id: str) -> Dict[str, Any]:
    """Upsert sanitized ticks into MongoDB without storing raw broker frames."""
    if not ticks:
        return {"upserted": 0, "modified": 0, "matched": 0, "operations": []}
    operations = []
    # BSON datetime supports the TTL index.  The durable evidence needed for a
    # promotion cohort is copied onto paper trades; the high-frequency raw tick
    # tape is operational audit data with bounded retention.
    stored_at = datetime.now(timezone.utc)
    for tick in ticks:
        doc = dict(tick)
        doc["session_id"] = session_id
        doc["stored_at"] = stored_at
        operations.append(UpdateOne(
            {
                "instrument_key": doc["instrument_key"],
                "ts": int(doc["ts"]),
                "session_id": session_id,
            },
            {"$set": doc},
            upsert=True,
        ))
    result = await db.ticks.bulk_write(operations, ordered=False)
    return {
        "upserted": int(getattr(result, "upserted_count", 0) or 0),
        "modified": int(getattr(result, "modified_count", 0) or 0),
        "matched": int(getattr(result, "matched_count", 0) or 0),
        "operations": operations,
    }


AuthorizeUrlFetcher = Callable[[], Awaitable[str]]
WebsocketConnector = Callable[[str], Any]


def _get_db():
    from app.db import get_db

    return get_db()


class UpstoxMarketStreamManager:
    """Single-process WebSocket stream manager for the local desktop app."""

    def __init__(
        self,
        *,
        db_factory: Callable[[], Any] = _get_db,
        authorize_url_fetcher: Optional[AuthorizeUrlFetcher] = None,
        websocket_connector: Optional[WebsocketConnector] = None,
    ):
        self._db_factory = db_factory
        self._authorize_url_fetcher = authorize_url_fetcher
        self._websocket_connector = websocket_connector
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._latest_ticks: Dict[str, Dict[str, Any]] = {}
        # Per-subscriber asyncio.Queue for fan-out pub/sub (used by SSE/WebSocket consumers).
        self._tick_subscribers: "set[asyncio.Queue]" = set()
        self._session: Dict[str, Any] = {
            "running": False,
            "session_id": None,
            "mode": DEFAULT_STREAM_MODE,
            "instrument_keys": [],
            "persist": True,
            "started_at": None,
            "updated_at": None,
            "last_tick_at": None,
            "tick_count": 0,
            "reconnect_count": 0,
            "last_error": None,
        }

    def configure_session(
        self,
        *,
        session_id: str,
        instrument_keys: List[str],
        mode: str = DEFAULT_STREAM_MODE,
        persist: bool = True,
    ) -> None:
        mode = str(mode or DEFAULT_STREAM_MODE).lower()
        if mode not in ALLOWED_STREAM_MODES:
            raise ValueError(f"Unsupported Upstox stream mode: {mode}")
        self._session.update({
            "session_id": session_id,
            "mode": mode,
            "instrument_keys": list(dict.fromkeys(instrument_keys)),
            "persist": bool(persist),
            "updated_at": _now_iso(),
            "last_error": None,
        })

    def status(self) -> Dict[str, Any]:
        status = dict(self._session)
        status["running"] = bool(self._task and not self._task.done())
        status["instrument_count"] = len(status.get("instrument_keys") or [])
        status["latest_tick_count"] = len(self._latest_ticks)
        return status

    def latest_ticks(self, limit: int = 50) -> List[Dict[str, Any]]:
        ticks = sorted(
            self._latest_ticks.values(),
            key=lambda tick: int(tick.get("received_ts") or tick.get("ts") or 0),
            reverse=True,
        )
        return [dict(tick) for tick in ticks[: max(1, int(limit or 1))]]

    def latest_tick_map(self) -> Dict[str, Dict[str, Any]]:
        return {key: dict(value) for key, value in self._latest_ticks.items()}

    # --- pub/sub for SSE/WebSocket consumers ---------------------------------

    def subscribe(self, *, max_queue: int = 256) -> "asyncio.Queue":
        """Register a tick subscriber. Returns an asyncio.Queue receiving normalized ticks.

        The queue has a bounded size; if the consumer is too slow, oldest ticks are dropped
        so the producer never blocks. Always pair with unsubscribe() in a try/finally.
        """
        queue: "asyncio.Queue" = asyncio.Queue(maxsize=max_queue)
        self._tick_subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue") -> None:
        self._tick_subscribers.discard(queue)

    def _broadcast(self, ticks: List[Dict[str, Any]]) -> None:
        if not self._tick_subscribers or not ticks:
            return
        for queue in list(self._tick_subscribers):
            for tick in ticks:
                try:
                    queue.put_nowait(tick)
                except asyncio.QueueFull:
                    # Drop oldest, keep newest. Slow consumer should not stall the producer.
                    try:
                        queue.get_nowait()
                        queue.put_nowait(tick)
                    except Exception:
                        pass
                except Exception:
                    # A failed consumer should never crash the broadcaster.
                    self._tick_subscribers.discard(queue)
                    break

    async def start(
        self,
        *,
        instrument_keys: List[str],
        mode: str = DEFAULT_STREAM_MODE,
        persist: bool = True,
    ) -> Dict[str, Any]:
        if self._task and not self._task.done():
            return self.status()
        session_id = str(uuid.uuid4())
        self.configure_session(
            session_id=session_id,
            instrument_keys=list(dict.fromkeys(instrument_keys)),
            mode=mode,
            persist=persist,
        )
        self._session.update({
            "running": True,
            "started_at": _now_iso(),
            "tick_count": 0,
            "reconnect_count": 0,
            "last_tick_at": None,
            "last_error": None,
        })
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name=f"upstox-market-stream-{session_id}")
        return self.status()

    async def stop(self) -> Dict[str, Any]:
        if self._stop_event:
            self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._session["running"] = False
        self._session["updated_at"] = _now_iso()
        return self.status()

    async def _resolve_authorized_url(self) -> str:
        if self._authorize_url_fetcher:
            return await self._authorize_url_fetcher()
        from app import upstox_client

        return await upstox_client.fetch_market_data_feed_authorize_url()

    async def _connect(self, url: str):
        if self._websocket_connector:
            return self._websocket_connector(url)
        import websockets

        return websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5)

    async def _run(self) -> None:
        assert self._stop_event is not None
        attempt = 0
        while not self._stop_event.is_set():
            try:
                url = await self._resolve_authorized_url()
                connector = await self._connect(url)
                async with connector as websocket:
                    attempt = 0
                    self._session["running"] = True
                    self._session["updated_at"] = _now_iso()
                    await websocket.send(build_subscription_message(
                        guid=str(uuid.uuid4()),
                        instrument_keys=self._session["instrument_keys"],
                        mode=self._session["mode"],
                    ))
                    while not self._stop_event.is_set():
                        frame = await websocket.recv()
                        if not isinstance(frame, (bytes, bytearray)):
                            continue
                        decoded = decode_market_data_feed(frame)
                        ticks = normalize_feed_response(decoded)
                        if not ticks:
                            continue
                        for tick in ticks:
                            self._latest_ticks[tick["instrument_key"]] = tick
                        self._session["tick_count"] = int(self._session.get("tick_count") or 0) + len(ticks)
                        self._session["last_tick_at"] = _now_iso()
                        self._session["updated_at"] = self._session["last_tick_at"]
                        self._broadcast(ticks)
                        if self._session.get("persist"):
                            await persist_ticks(self._db_factory(), ticks, str(self._session["session_id"]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                self._session["running"] = False
                self._session["reconnect_count"] = int(self._session.get("reconnect_count") or 0) + 1
                self._session["last_error"] = str(exc)[:240]
                self._session["updated_at"] = _now_iso()
                log.warning("Upstox market stream interrupted: %s", exc)
                delay = min(30.0, max(1.0, 2.0 ** min(attempt, 5)))
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    continue
        self._session["running"] = False
        self._session["updated_at"] = _now_iso()
