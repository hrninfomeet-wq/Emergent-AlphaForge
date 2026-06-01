"""Build a small live option subscription universe for read-only Upstox ticks.

The forward-testing path needs fresh option LTPs for marking recommendations and
paper trades. This module deliberately keeps the live universe narrow: nearest
stored expiry, ATM-centered strike band, and a hard cap on option instrument keys.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.instruments import INSTRUMENT_KEYS
from app.options_universe import select_atm_band


IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_LIVE_OPTION_UNDERLYINGS = ["NIFTY", "BANKNIFTY", "SENSEX"]
DEFAULT_LIVE_OPTION_RADIUS = 1
DEFAULT_MAX_OPTION_KEYS = 60


def normalize_underlyings(value: Optional[Iterable[str]]) -> List[str]:
    """Return supported underlyings in stable order without duplicates."""
    raw_values = list(DEFAULT_LIVE_OPTION_UNDERLYINGS if value is None else value)
    normalized: List[str] = []
    for raw in raw_values:
        key = str(raw or "").strip().upper()
        if key and key in INSTRUMENT_KEYS and key not in normalized:
            normalized.append(key)
    return normalized


def _today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _spot_from_latest_tick(
    latest_ticks: Dict[str, Dict[str, Any]],
    underlying: str,
) -> Tuple[Optional[float], Optional[int]]:
    tick = latest_ticks.get(INSTRUMENT_KEYS[underlying]) or {}
    spot = _to_float(tick.get("last_price") or tick.get("ltp"))
    if spot is None:
        return None, None
    ts_raw = tick.get("received_ts") or tick.get("ts")
    try:
        ts = int(ts_raw) if ts_raw is not None else None
    except (TypeError, ValueError):
        ts = None
    return spot, ts


async def _spot_from_latest_candle(db: Any, underlying: str) -> Tuple[Optional[float], Optional[int]]:
    cursor = db.candles_1m.find(
        {"instrument": underlying},
        {"_id": 0, "close": 1, "ts": 1},
    ).sort("ts", -1).limit(1)
    rows = await cursor.to_list(length=1)
    if not rows:
        return None, None
    row = rows[0]
    return _to_float(row.get("close")), int(row.get("ts") or 0)


async def _next_expiry(db: Any, underlying: str, today: str) -> Optional[str]:
    expiries = await db.option_contracts.distinct(
        "expiry_date",
        {"underlying": underlying, "expiry_date": {"$gte": today}},
    )
    normalized = sorted({str(expiry)[:10] for expiry in expiries if str(expiry or "")[:10] >= today})
    return normalized[0] if normalized else None


async def _contracts_for_expiry(db: Any, underlying: str, expiry_date: str) -> List[Dict[str, Any]]:
    cursor = db.option_contracts.find(
        {"underlying": underlying, "expiry_date": expiry_date},
        {"_id": 0},
    ).sort([("strike", 1), ("side", 1), ("instrument_key", 1)])
    return await cursor.to_list(length=5000)


async def build_live_option_universe(
    db: Any,
    *,
    latest_ticks: Optional[Dict[str, Dict[str, Any]]] = None,
    underlyings: Optional[Sequence[str]] = None,
    radius: int = DEFAULT_LIVE_OPTION_RADIUS,
    max_option_keys: int = DEFAULT_MAX_OPTION_KEYS,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the current ATM option keys that should be added to the WS stream."""
    selected_underlyings = normalize_underlyings(underlyings)
    bounded_radius = max(0, min(int(radius or 0), 5))
    bounded_max = max(2, min(int(max_option_keys or DEFAULT_MAX_OPTION_KEYS), 200))
    today_iso = today or _today_ist()
    tick_map = latest_ticks or {}

    instrument_keys: List[str] = []
    per_underlying: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for underlying in selected_underlyings:
        spot, spot_ts = _spot_from_latest_tick(tick_map, underlying)
        spot_source = "stream_tick" if spot is not None else None
        if spot is None:
            spot, spot_ts = await _spot_from_latest_candle(db, underlying)
            spot_source = "candles_1m" if spot is not None else None

        detail: Dict[str, Any] = {
            "underlying": underlying,
            "status": "ready",
            "spot_price": spot,
            "spot_ts": spot_ts,
            "spot_source": spot_source,
            "expiry_date": None,
            "atm": None,
            "contract_count": 0,
            "selected_count": 0,
        }

        if spot is None:
            detail["status"] = "missing_spot"
            warnings.append(f"{underlying}: no live spot tick or stored 1m candle available")
            per_underlying.append(detail)
            continue

        expiry = await _next_expiry(db, underlying, today_iso)
        detail["expiry_date"] = expiry
        if not expiry:
            detail["status"] = "missing_contracts"
            warnings.append(f"{underlying}: no option_contracts with expiry_date >= {today_iso}")
            per_underlying.append(detail)
            continue

        contracts = await _contracts_for_expiry(db, underlying, expiry)
        detail["contract_count"] = len(contracts)
        band = select_atm_band(
            contracts=contracts,
            underlying=underlying,
            spot_price=float(spot),
            radius=bounded_radius,
        )
        if band:
            detail["atm"] = band[0].get("atm")
        detail["selected_count"] = len(band)
        detail["contracts"] = band
        if not band:
            detail["status"] = "empty_atm_band"
            warnings.append(f"{underlying}: no CE/PE contracts found in ATM +/- {bounded_radius} band")
            per_underlying.append(detail)
            continue

        for contract in band:
            key = str(contract.get("instrument_key") or "")
            if key and key not in instrument_keys:
                instrument_keys.append(key)
        per_underlying.append(detail)

    capped = instrument_keys[:bounded_max]
    if len(capped) < len(instrument_keys):
        warnings.append(f"option universe capped at {bounded_max} keys from {len(instrument_keys)} candidates")

    return {
        "status": "ready" if capped else "needs_attention",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "today": today_iso,
        "radius": bounded_radius,
        "max_option_keys": bounded_max,
        "underlyings": per_underlying,
        "instrument_keys": capped,
        "option_key_count": len(capped),
        "warnings": warnings,
    }
