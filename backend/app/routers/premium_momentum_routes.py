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
from app.premium_momentum import lock_reference_strike, normalize_hhmm
from app.premium_momentum_backtest import (
    _sides_for, expiry_for_session, preload_scope, run_premium_momentum_backtest,
)
from app.premium_momentum_tuner import MAX_CONFIGS_DEFAULT, tune_premium_momentum
from app.premium_trigger_config import PremiumTriggerConfig, config_from_dict
from app.premium_trigger_dispatch import dispatch_backtest
from app.runtime import OPTION_CANDLE_LOAD_CAP
from app.vix import VIX_INSTRUMENT, vix_by_session_map
from app.warehouse import load_candles_df

api = APIRouter()

# Grid keys the tune endpoint accepts. reference_time/side sweeps are deliberately
# NOT tunable in v1 (they change which strikes get pre-locked per config; moneyness
# IS supported because the loader pre-locks the union of requested moneyness).
TUNABLE_KEYS = {
    "momentum_pct", "momentum_pts", "stop_pct", "stop_pts",
    "target_pct", "target_pts", "trail_x", "trail_y", "moneyness",
    "lazy_momentum_pct", "lazy_stop_pct", "lazy_target_pct",
    "trail_x_pct", "trail_y_pct", "lazy_trail_x_pct", "lazy_trail_y_pct",
    # Phase 5A.2 overlays (session day-stop + VIX gate; entry_cutoff/exit_time
    # are strings -- the tune grid passes values verbatim into params, nothing
    # about the preload depends on them). reference_time stays NON-tunable.
    "session_max_loss_rupees", "session_max_profit_rupees",
    "vix_min", "vix_max", "entry_cutoff", "exit_time",
}

# The VIX gate's asof fallback window (route section 2 of the 5A.2 plan):
# "previous session's last close within 5 calendar days".
VIX_ASOF_STALENESS_MS = 5 * 24 * 3600 * 1000

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
                       sides: List[str],
                       lazy_enabled: bool = False) -> Optional[Tuple[pd.DataFrame, pd.DataFrame, List[Dict[str, Any]]]]:
    """Shared loader: (spot_df, option_candles, contracts) for the window, or None
    when there are no spot candles. Pre-locks the UNION of (session x side x
    moneyness) strikes so every config a sweep evaluates finds its candles.
    When lazy_enabled, widens the preload to the full warehouse moneyness band
    AND BOTH SIDES via the pure preload_scope helper -- the reversal leg is
    opposite-side with a fresh strike locked from spot at an unknown future
    bar; widening moneyness alone would leave a single-side run with zero
    opposite-side candles and 100% lazy_excluded_no_data (review finding C1)."""
    moneynesses, sides = preload_scope(moneynesses, sides, lazy_enabled)
    ref_time = normalize_hhmm(ref_time) or "09:31"   # review C1: unpadded fail-open
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


async def _build_vix_by_session(spot_df: pd.DataFrame, start_ts: int, end_ts: int, *,
                                ref_time: str) -> Dict[str, float]:
    """Load INDIAVIX candles_1m for [start_ts - 5 days, end_ts] and build the
    session_date -> gate-value map (Phase 5A.2 §2). ONLY called when the
    request actually configures vix_min/vix_max -- no gate, no VIX query,
    zero overhead."""
    vix_df = await load_candles_df(VIX_INSTRUMENT, int(start_ts) - VIX_ASOF_STALENESS_MS, int(end_ts))
    if vix_df.empty:
        return {}
    vix_candles = vix_df[["ts", "close"]].to_dict("records")
    return vix_by_session_map(spot_df, vix_candles, ref_time=ref_time,
                              max_staleness_ms=VIX_ASOF_STALENESS_MS)


@api.post("/premium-momentum/backtest")
async def premium_momentum_backtest(req: PremiumMomentumBacktestReq) -> Dict[str, Any]:
    instrument = req.instrument.upper()
    params = dict(req.params or {})
    ref_time = str(params.get("reference_time") or "09:31")
    loaded = await _load_window(
        instrument, req.start_ts, req.end_ts,
        ref_time=ref_time,
        moneynesses=[str(params.get("moneyness") or "itm1")],
        sides=_sides_for(params.get("side")),
        lazy_enabled=bool(params.get("lazy_enabled") or False),
    )
    if loaded is None:
        return _empty_result(params)
    spot_df, option_candles, contracts = loaded
    vix_by_session = None
    if params.get("vix_min") is not None or params.get("vix_max") is not None:
        vix_by_session = await _build_vix_by_session(spot_df, req.start_ts, req.end_ts, ref_time=ref_time)
    return _json_safe(run_premium_momentum_backtest(
        spot_df=spot_df, option_candles=option_candles,
        contracts=contracts, instrument=instrument, params=params,
        vix_by_session=vix_by_session,
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
    ref_time = str(base_params.get("reference_time") or "09:31")
    loaded = await _load_window(
        instrument, req.start_ts, req.end_ts,
        ref_time=ref_time,
        moneynesses=moneynesses,
        sides=_sides_for(base_params.get("side")),
        lazy_enabled=bool(base_params.get("lazy_enabled") or False),
    )
    if loaded is None:
        raise HTTPException(400, "no spot candles in the window")
    spot_df, option_candles, contracts = loaded
    vix_by_session = None
    vix_gate_grid_values = list(grid.get("vix_min", [])) + list(grid.get("vix_max", []))
    if (base_params.get("vix_min") is not None or base_params.get("vix_max") is not None
            or vix_gate_grid_values):
        vix_by_session = await _build_vix_by_session(spot_df, req.start_ts, req.end_ts, ref_time=ref_time)
    try:
        out = tune_premium_momentum(
            spot_df=spot_df, option_candles=option_candles, contracts=contracts,
            instrument=instrument, base_params=base_params, grid=grid,
            train_frac=req.train_frac, max_configs=MAX_CONFIGS_DEFAULT,
            vix_by_session=vix_by_session,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _json_safe(out)


# ---------------------------------------------------------------------------
# Phase 4 engine dispatch (Session 2) — backtest path only.
#
# Same underlying sim as /premium-momentum/backtest, but dispatched from a
# declarative PremiumTriggerConfig instead of a bare params dict. This is the
# runtime side of the promise the AI feasibility classifier now makes
# ("premium_trigger_config is buildable"). Byte-identical parity to the bespoke
# route is a HARD invariant — covered by tests/test_premium_trigger_dispatch_parity.py.
#
# Deferred to follow-up sessions:
#   * live/deployment_evaluator dispatch on the same config schema
#   * Optimizer tuner dispatch on the same config schema
#   * frontend config-block builder on the deployment/preset UI
# ---------------------------------------------------------------------------
class PremiumTriggerBacktestReq(BaseModel):
    """Request body for the config-driven backtest route.

    `premium_trigger`: the declarative config block. Same schema whether it's
                       coming from the deployment record, the Optimizer, the AI
                       authoring wizard, or the frontend builder.
    """
    instrument: str
    start_ts: int
    end_ts: int
    premium_trigger: Dict[str, Any] = Field(default_factory=dict)


@api.post("/premium-trigger/backtest")
async def premium_trigger_backtest(req: PremiumTriggerBacktestReq) -> Dict[str, Any]:
    """Config-driven premium-trigger backtest. Byte-identical to
    /premium-momentum/backtest when given the equivalent params — see the
    parity test."""
    instrument = req.instrument.upper()
    try:
        cfg = config_from_dict(req.premium_trigger)
    except Exception as exc:
        # Pydantic ValidationError -> user-friendly 400 (any typo in a field
        # name or an out-of-range value must surface here, not at sim time).
        raise HTTPException(400, f"invalid premium_trigger config: {exc}") from exc

    params_for_load = cfg.to_backtest_params()
    loaded = await _load_window(
        instrument, req.start_ts, req.end_ts,
        ref_time=str(params_for_load.get("reference_time") or "09:31"),
        moneynesses=[str(params_for_load.get("moneyness") or "itm1")],
        sides=_sides_for(params_for_load.get("side")),
    )
    if loaded is None:
        # Same shape as _empty_result, augmented with the config for
        # traceability (so the caller doesn't lose their input on empty runs).
        empty = _empty_result(params_for_load)
        empty["premium_trigger_config"] = cfg.model_dump(mode="json")
        empty["dispatch"] = "premium_trigger_config"
        return empty
    spot_df, option_candles, contracts = loaded
    return _json_safe(dispatch_backtest(
        cfg=cfg, spot_df=spot_df, option_candles=option_candles,
        contracts=contracts, instrument=instrument,
    ))
