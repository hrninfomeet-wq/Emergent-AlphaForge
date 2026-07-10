"""Premium-momentum contingency backtest route (Phase 1, Task 1.4).

Thin I/O wrapper around the pure sim in app.premium_momentum_backtest:

  POST /premium-momentum/backtest  {instrument, start_ts, end_ts, params}

It loads spot candles (candles_1m) for the window and derives session_date +
ist_time, resolves the CHOSEN weekly expiry (nearest expiry >= the first
session date, mirroring the option_backtest expiry policy), pre-locks the
reference-time strikes per session to learn which option instrument_keys the
sim will trade, loads those locked strikes' FULL-DAY options_1m series
(non-trade-driven — the whole window, not bounded by any signal), then calls
run_premium_momentum_backtest and returns its {trades, coverage} report so a
shrunk sample stays visible.

All entry/exit/strike semantics live in the pure helpers; this file is glue.
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.db import get_db
from app.premium_momentum import lock_reference_strike
from app.premium_momentum_backtest import (
    _sides_for, expiry_for_session, run_premium_momentum_backtest,
)
from app.runtime import OPTION_CANDLE_LOAD_CAP
from app.warehouse import load_candles_df

api = APIRouter()


class PremiumMomentumBacktestReq(BaseModel):
    instrument: str
    start_ts: int
    end_ts: int
    params: Dict[str, Any] = Field(default_factory=dict)


def _json_safe(obj: Any) -> Any:
    """Recursively convert numpy scalars to native Python for JSON serialization.
    The pure sim walks numpy-backed premium series, so trade `entry_ts`/`exit_ts`
    come back as numpy.int64 — fine for host asserts, but FastAPI cannot encode
    them. This route is the JSON boundary, so it normalizes here."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


def _empty_result(params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "trades": [],
        "coverage": {"sessions_total": 0, "sessions_traded": 0, "sessions_excluded": 0,
                     "sessions_no_signal": 0, "exclude_reasons": {}},
        "params": dict(params),
    }


@api.post("/premium-momentum/backtest")
async def premium_momentum_backtest(req: PremiumMomentumBacktestReq) -> Dict[str, Any]:
    db = get_db()
    instrument = req.instrument.upper()
    params = dict(req.params or {})

    # 1) Spot candles for the window; derive session_date + ist_time (canonical,
    #    matching indicators.compute so the sim's reference-bar selection agrees).
    spot_df = await load_candles_df(instrument, req.start_ts, req.end_ts)
    if spot_df.empty:
        return _empty_result(params)
    _dt = pd.to_datetime(spot_df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    spot_df["session_date"] = _dt.dt.strftime("%Y-%m-%d")
    spot_df["ist_time"] = _dt.dt.strftime("%H:%M")

    # 2) Contracts for the window: every expiry from the first session up to a
    #    little past the last (each SESSION resolves its own nearest weekly expiry
    #    inside the sim — the blueprint's "current weekly" — so a multi-week window
    #    must carry every week's contracts, not just the first week's).
    first_session = str(spot_df["session_date"].min())
    last_session = str(spot_df["session_date"].max())
    expiry_ceiling = (pd.Timestamp(last_session) + pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    contracts = await db.option_contracts.find(
        {"underlying": instrument,
         "expiry_date": {"$gte": first_session, "$lte": expiry_ceiling}},
        {"_id": 0},
    ).sort([("expiry_date", 1), ("strike", 1), ("side", 1)]).to_list(length=None)
    expiries = sorted({str(c.get("expiry_date")) for c in contracts if c.get("expiry_date")})
    if not expiries:
        return _json_safe(run_premium_momentum_backtest(
            spot_df=spot_df, option_candles=pd.DataFrame(), contracts=[],
            instrument=instrument, params=params,
        ))

    # 3) Pre-lock each session's reference-time strikes (per side) to learn which
    #    instrument_keys the sim will trade, so we can pull their candles. Uses
    #    the SAME lock helper AND the SAME per-session expiry resolution as the
    #    sim -> the keys agree exactly.
    ref_time = str(params.get("reference_time") or "09:31")
    moneyness = str(params.get("moneyness") or "itm1")
    sides = _sides_for(params.get("side"))
    locked_keys: set[str] = set()
    for session, sdf in spot_df.groupby("session_date"):
        ref_rows = sdf[sdf["ist_time"] >= ref_time].sort_values("ts")
        if ref_rows.empty:
            continue
        sess_expiry = expiry_for_session(str(session), expiries)
        if sess_expiry is None:
            continue
        sess_contracts = [c for c in contracts if str(c.get("expiry_date")) == sess_expiry]
        spot_at_ref = float(ref_rows.iloc[0]["close"])
        for side in sides:
            locked = lock_reference_strike(contracts=sess_contracts, underlying=instrument,
                                           spot_at_ref=spot_at_ref, side=side, moneyness=moneyness)
            if locked:
                locked_keys.add(str(locked["instrument_key"]))

    # 4) Full-day options_1m for the locked strikes across the whole window
    #    (non-trade-driven: the entire session series, not a trade-bounded slice).
    #    Query by the exact locked keys so the sim's exact-match lock finds them.
    option_rows: List[Dict[str, Any]] = []
    if locked_keys:
        option_rows = await db.options_1m.find(
            {"instrument_key": {"$in": sorted(locked_keys)},
             "ts": {"$gte": int(req.start_ts), "$lte": int(req.end_ts)}},
            {"_id": 0},
        ).sort("ts", 1).to_list(length=OPTION_CANDLE_LOAD_CAP)

    # 5) Run the pure sim and return its {trades, coverage, params}.
    return _json_safe(run_premium_momentum_backtest(
        spot_df=spot_df, option_candles=pd.DataFrame(option_rows),
        contracts=contracts, instrument=instrument, params=params,
    ))
