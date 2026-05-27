"""Market header quote aggregation for the persistent terminal ticker band."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional


MarketItem = Dict[str, Any]
QuoteFetcher = Callable[[MarketItem], Awaitable[Dict[str, Any]]]


PRIMARY_ITEMS: List[MarketItem] = [
    {"key": "nifty50", "label": "NIFTY 50", "group": "primary", "source": "upstox", "instrument_key": "NSE_INDEX|Nifty 50", "source_label": "Upstox"},
    {"key": "sensex", "label": "SENSEX", "group": "primary", "source": "upstox", "instrument_key": "BSE_INDEX|SENSEX", "source_label": "Upstox"},
    {"key": "banknifty", "label": "BANKNIFTY", "group": "primary", "source": "upstox", "instrument_key": "NSE_INDEX|Nifty Bank", "source_label": "Upstox"},
    {"key": "gold_fut", "label": "GOLD FUT", "group": "primary", "source": "fallback", "fallback_symbol": "GC=F", "source_label": "Yahoo"},
    {"key": "btcusd", "label": "BTCUSD", "group": "primary", "source": "fallback", "fallback_symbol": "BTC-USD", "source_label": "Yahoo"},
    {"key": "usdinr", "label": "USDINR", "group": "primary", "source": "upstox", "instrument_key": "GLOBAL_INDICATOR|USDINR", "fallback_symbol": "INR=X", "source_label": "Upstox Global"},
    {"key": "gift_nifty", "label": "GIFT NIFTY", "group": "primary", "source": "upstox", "instrument_key": "GLOBAL_INDEX|SGX NIFTY", "source_label": "Upstox Global"},
    {"key": "midcpnifty", "label": "MIDCPNIFTY", "group": "primary", "source": "upstox", "instrument_key": "NSE_INDEX|NIFTY MID SELECT", "source_label": "Upstox"},
]

GLOBAL_ITEMS: List[MarketItem] = [
    {"key": "nasdaq_fut", "label": "Nasdaq Fut", "group": "global", "source": "upstox", "instrument_key": "GLOBAL_INDEX|IXIX", "fallback_symbol": "NQ=F", "source_label": "Upstox Global"},
    {"key": "dow_fut", "label": "Dow Fut", "group": "global", "source": "upstox", "instrument_key": "GLOBAL_INDEX|DOW FUTURES", "fallback_symbol": "YM=F", "source_label": "Upstox Global"},
    {"key": "sp_fut", "label": "S&P Fut", "group": "global", "source": "fallback", "fallback_symbol": "ES=F", "source_label": "Yahoo"},
    {"key": "nikkei", "label": "Nikkei 225", "group": "global", "source": "upstox", "instrument_key": "GLOBAL_INDEX|^N225", "fallback_symbol": "^N225", "source_label": "Upstox Global"},
    {"key": "hang_seng", "label": "Hang Seng", "group": "global", "source": "upstox", "instrument_key": "GLOBAL_INDEX|^HSI", "fallback_symbol": "^HSI", "source_label": "Upstox Global"},
    {"key": "dax", "label": "DAX", "group": "global", "source": "upstox", "instrument_key": "GLOBAL_INDEX|^GDAXI", "fallback_symbol": "^GDAXI", "source_label": "Upstox Global"},
    {"key": "crudeoil", "label": "CRUDEOIL", "group": "global", "source": "fallback", "fallback_symbol": "CL=F", "source_label": "Yahoo"},
]

DEFAULT_ITEMS: List[MarketItem] = PRIMARY_ITEMS + GLOBAL_ITEMS


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


def _round_or_none(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def pct_change(last_price: Any, previous_close: Any) -> Optional[float]:
    last = _to_float(last_price)
    prev = _to_float(previous_close)
    if last is None or prev in (None, 0):
        return None
    return round(((last - prev) / prev) * 100, 2)


def normalize_quote(
    raw: Dict[str, Any],
    *,
    label: str,
    key: str,
    group: str,
    source_label: str,
) -> Dict[str, Any]:
    """Normalize Upstox or fallback quotes into the header tile contract."""
    ohlc = raw.get("ohlc") or {}
    last_price = _to_float(raw.get("last_price") or raw.get("regular_market_price"))
    previous_close = _to_float(
        raw.get("previous_close")
        or raw.get("close_price")
        or raw.get("regular_market_previous_close")
        or ohlc.get("close")
    )
    change = _to_float(raw.get("change"))
    if change is None:
        change = _to_float(raw.get("net_change"))
    if change is None and last_price is not None and previous_close is not None:
        change = last_price - previous_close

    change_pct = _to_float(raw.get("change_pct") or raw.get("regular_market_change_pct"))
    if change_pct is None:
        if change is not None and last_price is not None:
            previous_from_change = last_price - change
            change_pct = None if previous_from_change == 0 else round((change / previous_from_change) * 100, 2)
        else:
            change_pct = pct_change(last_price, previous_close)

    timestamp = raw.get("timestamp") or raw.get("last_trade_time") or datetime.now(timezone.utc).isoformat()

    return {
        "key": key,
        "label": label,
        "group": group,
        "last_price": _round_or_none(last_price, 4),
        "change": _round_or_none(change, 4),
        "change_pct": _round_or_none(change_pct, 2),
        "timestamp": str(timestamp),
        "source": source_label,
        "status": "ok" if last_price is not None else "error",
        "raw_source": raw.get("source") or "",
    }


async def fetch_upstox_item(item: MarketItem) -> Dict[str, Any]:
    instrument_key = item.get("instrument_key")
    if not instrument_key:
        raise RuntimeError("missing Upstox instrument key")
    return await _cached_fetch_upstox(str(instrument_key), str(item.get("key") or ""))


# ── Upstox REST cache — used only when WS tick is missing or stale ──

_UPSTOX_CACHE: Dict[str, Dict[str, Any]] = {}
_UPSTOX_CACHE_TTL_S = 2.0
_UPSTOX_LOCKS: Dict[str, asyncio.Lock] = {}


async def _cached_fetch_upstox(instrument_key: str, instrument: str) -> Dict[str, Any]:
    cache_key = instrument_key
    now = datetime.now(timezone.utc).timestamp()
    cached = _UPSTOX_CACHE.get(cache_key)
    if cached and (now - cached.get("_cached_at", 0)) < _UPSTOX_CACHE_TTL_S:
        return {k: v for k, v in cached.items() if not k.startswith("_")}
    lock = _UPSTOX_LOCKS.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = _UPSTOX_CACHE.get(cache_key)
        if cached and (datetime.now(timezone.utc).timestamp() - cached.get("_cached_at", 0)) < _UPSTOX_CACHE_TTL_S:
            return {k: v for k, v in cached.items() if not k.startswith("_")}
        from app import upstox_client
        result = await upstox_client.fetch_market_quote_by_key(instrument_key, instrument=instrument)
        _UPSTOX_CACHE[cache_key] = {**result, "_cached_at": datetime.now(timezone.utc).timestamp()}
        return result


def _fetch_yfinance_sync(symbol: str) -> Dict[str, Any]:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    info = {}
    try:
        fast = ticker.fast_info
        info = {
            "last_price": getattr(fast, "last_price", None) or fast.get("last_price"),
            "previous_close": getattr(fast, "previous_close", None) or fast.get("previous_close"),
        }
    except Exception:
        info = {}

    if info.get("last_price") is None:
        history = ticker.history(period="2d", interval="1m")
        if not history.empty:
            last_close = float(history["Close"].dropna().iloc[-1])
            prev_close = float(history["Close"].dropna().iloc[0])
            info = {"last_price": last_close, "previous_close": prev_close}

    last_price = _to_float(info.get("last_price"))
    previous_close = _to_float(info.get("previous_close"))
    change = None if last_price is None or previous_close is None else last_price - previous_close
    return {
        "last_price": last_price,
        "previous_close": previous_close,
        "change": change,
        "change_pct": pct_change(last_price, previous_close),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "yfinance",
    }


async def fetch_fallback_item(item: MarketItem) -> Dict[str, Any]:
    symbol = item.get("fallback_symbol")
    if not symbol:
        raise RuntimeError("no fallback symbol configured")
    return await _cached_fetch_fallback(str(symbol))


# ── fallback (Yahoo) cache — yfinance is rate-limited; cache 10s and single-flight ──

_FALLBACK_CACHE: Dict[str, Dict[str, Any]] = {}
_FALLBACK_CACHE_TTL_S = 10.0
_FALLBACK_LOCKS: Dict[str, asyncio.Lock] = {}


async def _cached_fetch_fallback(symbol: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).timestamp()
    cached = _FALLBACK_CACHE.get(symbol)
    if cached and (now - cached.get("_cached_at", 0)) < _FALLBACK_CACHE_TTL_S:
        return {k: v for k, v in cached.items() if not k.startswith("_")}
    lock = _FALLBACK_LOCKS.setdefault(symbol, asyncio.Lock())
    async with lock:
        # Recheck under lock — first waiter populated cache while we waited.
        cached = _FALLBACK_CACHE.get(symbol)
        if cached and (datetime.now(timezone.utc).timestamp() - cached.get("_cached_at", 0)) < _FALLBACK_CACHE_TTL_S:
            return {k: v for k, v in cached.items() if not k.startswith("_")}
        result = await asyncio.to_thread(_fetch_yfinance_sync, symbol)
        _FALLBACK_CACHE[symbol] = {**result, "_cached_at": datetime.now(timezone.utc).timestamp()}
        return result


def _error_quote(item: MarketItem, message: str) -> Dict[str, Any]:
    return {
        "key": item.get("key"),
        "label": item.get("label"),
        "group": item.get("group", "primary"),
        "last_price": None,
        "change": None,
        "change_pct": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": item.get("source_label") or item.get("source") or "",
        "status": "error",
        "error": message[:180],
    }


async def _fetch_item(item: MarketItem, fetch_upstox: QuoteFetcher, fetch_fallback: QuoteFetcher) -> Dict[str, Any]:
    errors: List[str] = []
    if item.get("source") == "upstox":
        try:
            return normalize_quote(
                await fetch_upstox(item),
                label=str(item.get("label") or item.get("key")),
                key=str(item.get("key")),
                group=str(item.get("group") or "primary"),
                source_label=str(item.get("source_label") or "Upstox"),
            )
        except Exception as exc:
            errors.append(str(exc))
            if not item.get("fallback_symbol"):
                return _error_quote(item, "; ".join(errors))

    try:
        return normalize_quote(
            await fetch_fallback(item),
            label=str(item.get("label") or item.get("key")),
            key=str(item.get("key")),
            group=str(item.get("group") or "primary"),
            source_label=str(item.get("source_label") if item.get("source") != "upstox" else "Fallback"),
        )
    except Exception as exc:
        errors.append(str(exc))
        return _error_quote(item, "; ".join(errors))


def _is_fresh_tick(raw: Dict[str, Any], max_age_seconds: int = 120) -> bool:
    received_ts = raw.get("received_ts") or raw.get("ts")
    try:
        tick_dt = datetime.fromtimestamp(int(received_ts) / 1000, timezone.utc)
    except Exception:
        return False
    return (datetime.now(timezone.utc) - tick_dt).total_seconds() <= max_age_seconds


async def _fetch_item_with_tick_preference(
    item: MarketItem,
    fetch_upstox: QuoteFetcher,
    fetch_fallback: QuoteFetcher,
    latest_ticks: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    instrument_key = str(item.get("instrument_key") or "")
    tick = latest_ticks.get(instrument_key)
    if tick and _is_fresh_tick(tick):
        return normalize_quote(
            tick,
            label=str(item.get("label") or item.get("key")),
            key=str(item.get("key")),
            group=str(item.get("group") or "primary"),
            source_label="Upstox WS",
        )
    return await _fetch_item(item, fetch_upstox, fetch_fallback)


async def build_market_header_snapshot(
    items: Optional[Iterable[MarketItem]] = None,
    fetch_upstox: QuoteFetcher = fetch_upstox_item,
    fetch_fallback: QuoteFetcher = fetch_fallback_item,
    latest_ticks: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    selected = list(items or DEFAULT_ITEMS)
    tick_map = latest_ticks or {}
    quotes = await asyncio.gather(
        *(_fetch_item_with_tick_preference(item, fetch_upstox, fetch_fallback, tick_map) for item in selected)
    )
    has_live_tick = any(item.get("raw_source") == "upstox_ws_v3" for item in quotes)
    return {
        "source_mode": "live_ticks" if has_live_tick else "api_fallback",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": quotes,
    }
