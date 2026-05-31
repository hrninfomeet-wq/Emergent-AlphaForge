"""Point-in-time warehouse lookup.

Given an instrument, date, and time (IST), return what the LOCAL warehouse has
stored for that minute: the index spot candle, the ATM strike derived from that
spot, the nearest expiry on/after the date, and the ATM CE/PE option candles.

This is a data-trust tool: it lets a human cross-check a timestamped warehouse
value against a real broker terminal. It reads only from local collections
(candles_1m, options_1m, option_contracts) and never calls the broker.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.options_universe import round_to_step, strike_step_for

IST = timezone(timedelta(hours=5, minutes=30))


def ist_datetime_to_ms(date_str: str, time_str: str) -> int:
    """Convert an IST date (YYYY-MM-DD) + time (HH:MM) to a UTC epoch-ms value.

    Candles are stored keyed on `ts` = UTC epoch milliseconds at the start of
    the minute, so we truncate to the minute.
    """
    hh, mm = 9, 15
    if time_str:
        parts = str(time_str).split(":")
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 else 0
    y, m, d = (int(x) for x in str(date_str).split("-"))
    dt_ist = datetime(y, m, d, hh, mm, 0, tzinfo=IST)
    return int(dt_ist.astimezone(timezone.utc).timestamp() * 1000)


def _ms_to_ist_str(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


def _clean_candle(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    out = {
        "ts": int(doc["ts"]) if doc.get("ts") is not None else None,
        "ist_time": _ms_to_ist_str(doc.get("ts")),
        "open": doc.get("open"),
        "high": doc.get("high"),
        "low": doc.get("low"),
        "close": doc.get("close"),
        "volume": doc.get("volume"),
    }
    if "oi" in doc:
        out["oi"] = doc.get("oi")
    return out


async def _candle_at_or_before(
    coll: Any,
    base_query: Dict[str, Any],
    target_ts: int,
    *,
    max_lookback_ms: int = 5 * 60 * 1000,
) -> tuple[Optional[Dict[str, Any]], bool]:
    """Return the candle exactly at target_ts, else the most recent one within
    `max_lookback_ms` before it. Returns (candle, exact_match)."""
    exact = await coll.find_one({**base_query, "ts": int(target_ts)}, {"_id": 0})
    if exact:
        return exact, True
    # Fall back to the latest candle within the lookback window (handles a
    # missing exact minute without silently jumping to an unrelated bar).
    cursor = coll.find(
        {**base_query, "ts": {"$gte": int(target_ts) - int(max_lookback_ms), "$lt": int(target_ts)}},
        {"_id": 0},
    ).sort("ts", -1).limit(1)
    rows = await cursor.to_list(length=1)
    return (rows[0] if rows else None), False


async def _nearest_expiry_on_or_after(db: Any, underlying: str, date_str: str) -> Optional[str]:
    """Nearest stored option expiry on/after the given date for the underlying."""
    doc = await db.option_contracts.find_one(
        {"underlying": underlying.upper(), "expiry_date": {"$gte": date_str}},
        {"_id": 0, "expiry_date": 1},
        sort=[("expiry_date", 1)],
    )
    return doc.get("expiry_date") if doc else None


async def _option_contract(
    db: Any, underlying: str, expiry: str, strike: int, side: str
) -> Optional[Dict[str, Any]]:
    return await db.option_contracts.find_one(
        {
            "underlying": underlying.upper(),
            "expiry_date": expiry,
            "strike": float(strike),
            "side": side,
        },
        {"_id": 0},
    )


async def lookup_market_snapshot(
    db: Any,
    *,
    underlying: str,
    date_str: str,
    time_str: str,
) -> Dict[str, Any]:
    """Build the point-in-time snapshot from local warehouse data only.

    Returns spot candle, derived ATM strike, resolved expiry, and the ATM
    CE/PE option candles (each with an exact/nearest-bar flag).
    """
    underlying = str(underlying).upper()
    target_ts = ist_datetime_to_ms(date_str, time_str)

    result: Dict[str, Any] = {
        "underlying": underlying,
        "date": date_str,
        "time": time_str,
        "target_ts": target_ts,
        "target_ist": _ms_to_ist_str(target_ts),
        "spot": None,
        "spot_exact": False,
        "atm_strike": None,
        "expiry": None,
        "legs": {},
        "notes": [],
    }

    # 1. Spot candle for the requested minute.
    spot_doc, spot_exact = await _candle_at_or_before(
        db.candles_1m, {"instrument": underlying}, target_ts
    )
    result["spot"] = _clean_candle(spot_doc)
    result["spot_exact"] = spot_exact
    if not spot_doc:
        result["notes"].append("No spot candle stored at or near this minute. Check the date/time and that the index is ingested.")
        return result
    if not spot_exact:
        result["notes"].append("Exact minute missing for spot; showing the most recent bar within 5 minutes before it.")

    # 2. ATM strike from the spot close.
    step = strike_step_for(underlying)
    spot_close = float(spot_doc.get("close") or 0)
    atm_strike = round_to_step(spot_close, step)
    result["atm_strike"] = atm_strike

    # 3. Nearest expiry on/after the date.
    expiry = await _nearest_expiry_on_or_after(db, underlying, date_str)
    result["expiry"] = expiry
    if not expiry:
        result["notes"].append("No option contract metadata with an expiry on/after this date. Run the option contract backfill.")
        return result

    # 4. ATM CE and PE candles for that expiry/strike.
    for side in ("CE", "PE"):
        contract = await _option_contract(db, underlying, expiry, atm_strike, side)
        if not contract:
            result["legs"][side] = {
                "available": False,
                "reason": "contract_metadata_missing",
                "strike": atm_strike,
            }
            result["notes"].append(f"No {side} contract metadata for {atm_strike} {expiry}.")
            continue
        candle, exact = await _candle_at_or_before(
            db.options_1m, {"instrument_key": contract.get("instrument_key")}, target_ts
        )
        leg: Dict[str, Any] = {
            "available": candle is not None,
            "strike": atm_strike,
            "side": side,
            "expiry": expiry,
            "instrument_key": contract.get("instrument_key"),
            "trading_symbol": contract.get("trading_symbol"),
            "lot_size": contract.get("lot_size"),
            "exact": exact,
            "candle": _clean_candle(candle),
        }
        if candle is None:
            leg["reason"] = "option_candle_missing"
            result["notes"].append(f"No stored {side} candle at/near this minute for strike {atm_strike}.")
        elif not exact:
            result["notes"].append(f"Exact minute missing for {side}; showing the most recent bar within 5 minutes before it.")
        result["legs"][side] = leg

    return result
