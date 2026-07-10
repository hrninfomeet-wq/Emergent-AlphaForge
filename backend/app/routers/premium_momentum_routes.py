"""Premium-momentum contingency backtest + tuning routes (Phase 1, Tasks 1.4/1.3).

Thin I/O wrappers around the pure sim/tuner:

  POST /premium-momentum/backtest  {instrument, start_ts, end_ts, params}
  POST /premium-momentum/tune      {instrument, start_ts, end_ts, base_params, grid}

Loading (shared): spot candles (candles_1m) with derived session_date/ist_time;
contracts across the window's expiry range (each SESSION resolves its own nearest
weekly expiry — the blueprint's "current weekly"); per-session pre-lock of the
reference-time strikes (same helper + expiry resolution as the sim, so keys agree
exactly); the locked strikes' FULL-DAY options_1m series under CANONICAL keys
(dated 3-part metadata keys vs plain 2-part candle keys — root cause #3).

All entry/exit/strike/tuning semantics live in the pure modules; this file is glue.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_db
from app.instruments import canonical_instrument_key
from app.premium_momentum import lock_reference_strike
from app.premium_momentum_backtest import (
    _sides_for, expiry_for_session, run_premium_momentum_backtest,
)
from app.premium_momentum_tuner import MAX_CONFIGS_DEFAULT, tune_premium_momentum
from app.runtime import OPTION_CANDLE_LOAD_CAP
from app.warehouse import load_candles_df

api = APIRouter()

# Grid keys the tune endpoint accepts. reference_time/side sweeps are deliberately
# NOT tunable in v1 (they change which strikes get pre-locked per config; moneyness
# IS supported because the loader pre-locks the union of requested moneyness).
TUNABLE_KEYS = {
    "momentum_pct", "momentum_pts", "stop_pct", "stop_pts",
    "target_pct", "target_pts", "trail_x", "trail_y", "moneyness",
}


class PremiumMomentumBacktestReq(BaseModel):
    instrument: str
    start_ts: int
    end_ts: int
    params: Dict[str, Any] = Field(default_factory=dict)


class PremiumMomentumTuneReq(BaseModel):
    instrument: str
    start_ts: int
    end_ts: int
    base_params: Dict[str, Any] = Field(default_factory=dict)
    grid: Dict[str, List[Any]] = Field(default_factory=dict)
    train_frac: float = 0.7


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
        "summary": {"lot_size": 1, "lots": 1, "costs_enabled": False,
                    "gross_pnl_pts": 0.0, "net_pnl_pts": 0.0,
                    "net_pnl_rupees": 0.0, "charges_rupees": 0.0},
        "params": dict(params),
    }


async def _load_window(instrument: str, start_ts: int, end_ts: int, *,
                       ref_time: str, moneynesses: List[str],
                       sides: List[str]) -> Optional[Tuple[pd.DataFrame, pd.DataFrame, List[Dict[str, Any]]]]:
    """Shared loader: (spot_df, option_candles, contracts) for the window, or None
    when there are no spot candles. Pre-locks the UNION of (session x side x
    moneyness) strikes so every config a sweep evaluates finds its candles."""
    db = get_db()
    spot_df = await load_candles_df(instrument, start_ts, end_ts)
    if spot_df.empty:
        return None
    _dt = pd.to_datetime(spot_df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    spot_df["session_date"] = _dt.dt.strftime("%Y-%m-%d")
    spot_df["ist_time"] = _dt.dt.strftime("%H:%M")

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
        return spot_df, pd.DataFrame(), []

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
            for m in moneynesses:
                locked = lock_reference_strike(contracts=sess_contracts, underlying=instrument,
                                               spot_at_ref=spot_at_ref, side=side, moneyness=m)
                if locked:
                    locked_keys.add(str(locked["instrument_key"]))

    option_rows: List[Dict[str, Any]] = []
    if locked_keys:
        canon_keys = sorted({canonical_instrument_key(k) for k in locked_keys})
        option_rows = await db.options_1m.find(
            {"instrument_key": {"$in": canon_keys},
             "ts": {"$gte": int(start_ts), "$lte": int(end_ts)}},
            {"_id": 0},
        ).sort("ts", 1).to_list(length=OPTION_CANDLE_LOAD_CAP)
    return spot_df, pd.DataFrame(option_rows), contracts


@api.post("/premium-momentum/backtest")
async def premium_momentum_backtest(req: PremiumMomentumBacktestReq) -> Dict[str, Any]:
    instrument = req.instrument.upper()
    params = dict(req.params or {})
    loaded = await _load_window(
        instrument, req.start_ts, req.end_ts,
        ref_time=str(params.get("reference_time") or "09:31"),
        moneynesses=[str(params.get("moneyness") or "itm1")],
        sides=_sides_for(params.get("side")),
    )
    if loaded is None:
        return _empty_result(params)
    spot_df, option_candles, contracts = loaded
    return _json_safe(run_premium_momentum_backtest(
        spot_df=spot_df, option_candles=option_candles,
        contracts=contracts, instrument=instrument, params=params,
    ))


@api.post("/premium-momentum/tune")
async def premium_momentum_tune(req: PremiumMomentumTuneReq) -> Dict[str, Any]:
    instrument = req.instrument.upper()
    base_params = dict(req.base_params or {})
    grid = {k: list(v) for k, v in (req.grid or {}).items() if v}
    bad = sorted(set(grid) - TUNABLE_KEYS)
    if bad:
        raise HTTPException(400, f"non-tunable grid keys: {bad} (tunable: {sorted(TUNABLE_KEYS)})")
    moneynesses = sorted({str(m) for m in
                          ([base_params.get("moneyness") or "itm1"] + list(grid.get("moneyness", [])))})
    loaded = await _load_window(
        instrument, req.start_ts, req.end_ts,
        ref_time=str(base_params.get("reference_time") or "09:31"),
        moneynesses=moneynesses,
        sides=_sides_for(base_params.get("side")),
    )
    if loaded is None:
        raise HTTPException(400, "no spot candles in the window")
    spot_df, option_candles, contracts = loaded
    try:
        out = tune_premium_momentum(
            spot_df=spot_df, option_candles=option_candles, contracts=contracts,
            instrument=instrument, base_params=base_params, grid=grid,
            train_frac=req.train_frac, max_configs=MAX_CONFIGS_DEFAULT,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _json_safe(out)
