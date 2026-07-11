# backend/app/premium_momentum_live.py
"""Track B per-bar session engine for premium-momentum deployments.

Called from the deployment evaluator's Track B branch once per closed bar. Owns
the session state machine over the premium_locks store:

    pre_reference -> (lock strikes at the ref bar's close, capture refs from
    FRESH ticks) -> monitoring -> triggered (first side to cross) -> the
    EVALUATOR journals the signal + latches; entry/exit/done transitions are
    driven by auto_live + the guard's confirmed-flat hook, never here.

Uses the SAME pure helpers as the backtest (lock_reference_strike,
momentum_triggered) and the SAME live price contract as entries
(option_premium.resolve_premium, fresh-only). Stale/absent ticks HOLD — this
module never invents a price. It does NOT latch the trigger (spec: latch only
after the signal journals clean) and never touches order placement."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.live.option_premium import resolve_premium
from app.premium_lock_store import (
    capture_ref, get_lock, get_or_create_lock, mark_done,
)
from app.premium_momentum import lock_reference_strike

log = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)


def _ist_hhmm(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc) + IST_OFFSET
    return dt.strftime("%H:%M")


def _ist_session_date(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc) + IST_OFFSET
    return dt.strftime("%Y-%m-%d")


def _fresh_premium(latest_tick_map: Callable[[], Dict[str, Any]],
                   instrument_key: str, now_ts: float) -> Optional[Dict[str, Any]]:
    """FRESH tick premium via the canonical resolver, else None (HOLD)."""
    try:
        tick = (latest_tick_map() or {}).get(instrument_key)
    except Exception:
        tick = None
    res = resolve_premium(instrument_key=instrument_key, tick=tick,
                          candle_close=None, now_ts=now_ts)
    if res.get("fresh") is True and res.get("premium") is not None:
        return {"premium": float(res["premium"]), "ts": int(res.get("tick_ts") or 0)}
    return None


def _sides(params: Dict[str, Any]) -> List[str]:
    p = str(params.get("side") or "first_to_trigger").lower()
    if p == "ce":
        return ["CE"]
    if p == "pe":
        return ["PE"]
    return ["CE", "PE"]


async def evaluate_premium_momentum_bar(
    *, locks_col: Any, deployment: Dict[str, Any], instrument: str,
    candle_ts: int, spot_close: float, contracts: List[Dict[str, Any]],
    latest_tick_map: Callable[[], Dict[str, Any]], now_ts: float,
) -> Dict[str, Any]:
    """One bar of the premium-momentum session machine. Returns
    {"outcome": pre_reference|awaiting_ref|monitoring|triggered|holding_position|done,
     and on triggered: direction, contract, ref_premium, premium_now, blockers[]}."""
    dep_id = str(deployment.get("id") or "")
    params = dict(deployment.get("params") or {})
    ref_time = str(params.get("reference_time") or "09:31")
    cutoff = str(params.get("late_lock_cutoff") or "10:15")
    moneyness = str(params.get("moneyness") or "itm1")
    sides = _sides(params)
    bar_hhmm = _ist_hhmm(candle_ts)
    session = _ist_session_date(candle_ts)

    if bar_hhmm < ref_time:
        return {"outcome": "pre_reference"}

    lock = await get_lock(locks_col, deployment_id=dep_id, session_date=session)

    # --- session terminal states first ---
    if lock and lock.get("done_for_day"):
        return {"outcome": "done", "reason": lock.get("done_reason")}
    if lock and (lock.get("triggered_side") or lock.get("entered_norenordno")):
        return {"outcome": "holding_position"}

    # --- create the lock at/after the reference bar (strikes from THIS close) ---
    if lock is None:
        if bar_hhmm > cutoff:
            # never locked and past the cutoff: the session is honestly dead.
            await get_or_create_lock(locks_col, deployment_id=dep_id,
                                     session_date=session, payload={})
            await mark_done(locks_col, deployment_id=dep_id, session_date=session,
                            reason="no_lock")
            return {"outcome": "done", "reason": "no_lock"}
        payload: Dict[str, Any] = {"spot_at_ref": float(spot_close),
                                   "reference_bar_ts": int(candle_ts)}
        for side in sides:
            locked = lock_reference_strike(contracts=contracts, underlying=instrument,
                                           spot_at_ref=float(spot_close), side=side,
                                           moneyness=moneyness)
            if not locked:
                await get_or_create_lock(locks_col, deployment_id=dep_id,
                                         session_date=session, payload=payload)
                await mark_done(locks_col, deployment_id=dep_id, session_date=session,
                                reason="strike_lock_failed")
                return {"outcome": "done", "reason": "strike_lock_failed",
                        "blockers": [f"strike_lock_failed ({side} {moneyness})"]}
            # persist the FULL contract doc for the side (entry + audit need it)
            full = next((c for c in contracts
                         if str(c.get("instrument_key")) == locked["instrument_key"]), {})
            payload[side.lower()] = {**full, **locked}
        lock = await get_or_create_lock(locks_col, deployment_id=dep_id,
                                        session_date=session, payload=payload)

    # --- capture refs from FRESH ticks (first fresh tick wins; stale = HOLD) ---
    missing_ref = False
    for side in sides:
        s = side.lower()
        if lock.get(f"{s}_ref_premium") is not None:
            continue
        key = str(((lock.get(s) or {}).get("instrument_key")) or "")
        fp = _fresh_premium(latest_tick_map, key, now_ts) if key else None
        if fp is None:
            missing_ref = True
            continue
        await capture_ref(locks_col, deployment_id=dep_id, session_date=session,
                          side=s, ref_premium=fp["premium"], ref_ts=fp["ts"])
        lock[f"{s}_ref_premium"] = fp["premium"]
    if missing_ref:
        if bar_hhmm > cutoff:
            await mark_done(locks_col, deployment_id=dep_id, session_date=session,
                            reason="no_lock")
            return {"outcome": "done", "reason": "no_lock"}
        return {"outcome": "awaiting_ref",
                "blockers": ["ref_premium_unavailable (stale/absent tick — holding)"]}

    # --- monitor: first side to cross wins (CE first on a same-bar tie) ---
    from app.premium_momentum import momentum_triggered
    mom_pct = params.get("momentum_pct")
    mom_pts = params.get("momentum_pts")
    for side in sides:
        s = side.lower()
        key = str(((lock.get(s) or {}).get("instrument_key")) or "")
        fp = _fresh_premium(latest_tick_map, key, now_ts) if key else None
        if fp is None:
            continue   # this side's feed is stale THIS bar — hold it, try the other
        ref = float(lock[f"{s}_ref_premium"])
        if momentum_triggered(premium_now=fp["premium"], ref_premium=ref,
                              pct=mom_pct, pts=mom_pts):
            return {"outcome": "triggered", "direction": side,
                    "contract": dict(lock.get(s) or {}),
                    "ref_premium": ref, "premium_now": fp["premium"],
                    "blockers": []}
    return {"outcome": "monitoring"}
