"""Auto-Optimizer — Optuna Bayesian (TPE) + Grid Search + Genetic (CMA-ES) + Walk-Forward.

Single-entry async runner. Persists job state + results to MongoDB so frontend can poll.

Objective options:
  - sharpe       (maximize)
  - profit_factor (maximize)
  - total_pnl_pts (maximize)
  - win_rate     (maximize)
  - neg_max_dd   (maximize = minimize abs drawdown)
  - risk_adjusted (default: sharpe / max(1, abs(maxDD/100)))

Robustness: for the top trial, perturb each numeric param by ±10% and ±20% and re-evaluate.
Heatmap: pick top 2 params by importance, build a 2D grid over their bounds.
"""
from __future__ import annotations
import asyncio
import json
import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_OPT_TIMING = os.environ.get("AF_OPT_TIMING") == "1"

import numpy as np
import optuna
import pandas as pd

from app.backtest import run_backtest
from app.db import get_db
from app.indicator_groups import enrich_with_cache
from app.parallel_eval import effective_workers, start_pool, shutdown_pool, parallel_backtest
from app.strategies.base import get_registry
from app.warehouse import load_candles_df
from app.option_backtest import simulate_paired_option_trades, build_candles_by_key
from app.options_universe import select_contract_for_signal
from app.deployment_quality import compute_spot_option_correlation
from app.dte import compute_dte, normalize_dte_filter
from app.survival import survival_verdict, SurvivalConfig, oos_fold_index_ranges
from app.early_stop import is_significant_improvement, should_early_stop, effective_warmup_patience
from app.analyze_budget import over_budget, ewma, eta_seconds

log = logging.getLogger(__name__)
optuna.logging.set_verbosity(optuna.logging.WARNING)

# Heavy penalty used to disqualify degenerate / guard-failing trials.
from app.rerank_select import DISQUALIFY as _DISQUALIFY, select_rerank_candidates  # noqa: E402


# Indicator-period params that `precompute_all_indicators` actually consumes.
# When ANY of these change between trials, the enriched dataframe (and the
# regime series derived from it) MUST be recomputed — otherwise the optimizer
# silently optimizes against indicators frozen at their default periods.
INDICATOR_PARAM_KEYS = (
    "ema_fast", "ema_slow", "rsi_length",
    "macd_fast", "macd_slow", "macd_signal",
    "atr_length", "adx_length", "chop_length", "swing_lookback",
    # adaptive toolkit Plan 1 — every param precompute_all_indicators reads
    "vel_n", "vel_z_window", "vr_q", "vr_lookback", "vr_scale",
    "bb_len", "bb_mult", "kc_len", "kc_atr_mult", "sqz_mom_len",
    "st_period", "st_mult",
    "cpr_narrow_pctile", "cpr_wide_pctile", "cpr_pctile_window",
    "or_minutes",
    "tod_lookback_sessions", "tod_min_atr_frac",
)

# Invariant: every memoized indicator group's tuning params (source of truth:
# `app.indicator_groups.GROUPS`) MUST appear in INDICATOR_PARAM_KEYS above, or
# tuning them silently reuses a stale enriched frame. This import-time guard
# prevents the two lists from drifting as new keyed groups are added.
from app.indicator_groups import GROUPS as _GROUPS  # noqa: E402
from app.indicator_groups import SHARED_INDICATOR_PARAM_KEYS as _SHARED_KEYS  # noqa: E402

_missing_indicator_keys = {
    k for grp in _GROUPS for k in grp.param_keys
} - set(INDICATOR_PARAM_KEYS)
if _missing_indicator_keys:
    raise RuntimeError(
        f"INDICATOR_PARAM_KEYS missing memoized-group params {_missing_indicator_keys}; "
        "tuning them would reuse a stale enriched frame. Add them to "
        "INDICATOR_PARAM_KEYS in app/optimizer.py."
    )

# merged_params() accepts exactly SHARED_INDICATOR_PARAM_KEYS so optimizer-tuned
# periods flow through every evaluation path. The two tuples must never drift,
# or a tuned param would either be dropped again (no-op) or key the cache
# without reaching the enrichment.
if set(INDICATOR_PARAM_KEYS) != set(_SHARED_KEYS):
    raise RuntimeError(
        "INDICATOR_PARAM_KEYS (optimizer.py) and SHARED_INDICATOR_PARAM_KEYS "
        f"(indicator_groups.py) drifted: {set(INDICATOR_PARAM_KEYS) ^ set(_SHARED_KEYS)}"
    )

# Catalog of indicator-period params the optimizer can inject into the search
# space on request (sensible intraday bounds). Only added when the user enables
# "optimize indicator periods" AND the param isn't already in the strategy's
# own schema (the strategy's bounds win).
INDICATOR_PARAM_CATALOG: Dict[str, Dict[str, Any]] = {
    "ema_fast": {"type": "int", "min": 3, "max": 20, "default": 9},
    "ema_slow": {"type": "int", "min": 15, "max": 80, "default": 21},
    "rsi_length": {"type": "int", "min": 5, "max": 30, "default": 14},
    "macd_fast": {"type": "int", "min": 5, "max": 20, "default": 12},
    "macd_slow": {"type": "int", "min": 20, "max": 60, "default": 26},
    "macd_signal": {"type": "int", "min": 5, "max": 15, "default": 9},
    "atr_length": {"type": "int", "min": 7, "max": 30, "default": 14},
    "adx_length": {"type": "int", "min": 7, "max": 30, "default": 14},
    "chop_length": {"type": "int", "min": 7, "max": 30, "default": 14},
    "swing_lookback": {"type": "int", "min": 3, "max": 15, "default": 5},
}

# Fallback lot sizes (used only if no contract metadata is found in the DB).
_DEFAULT_LOT_SIZE = {"NIFTY": 75, "BANKNIFTY": 35, "SENSEX": 20}

# Max number of distinct indicator-period combinations to keep enriched frames
# for. Bounds memory while still giving big speedups when only signal-threshold
# params vary (common with TPE).
# Bounded: indicator-period search caches one enriched frame per period combo;
# long windows make each frame tens of MB, so keep the cap memory-safe.
_MAX_ENRICHED_CACHE = 16


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _objective_value(
    metrics: Dict[str, Any],
    objective: str,
    *,
    lot_size: int = 1,
    min_trades: int = 0,
    min_direction_share: float = 0.0,
) -> float:
    """Score a trial. Returns _DISQUALIFY for trials that fail the guard rails
    (no trades / too few trades / too one-sided) so the optimizer steers away
    from the degenerate solutions the user hit (e.g. 1-trade or all-PE runs)."""
    tc = int(metrics.get("trade_count", 0) or 0)
    if tc == 0:
        return _DISQUALIFY  # no trades at all
    if min_trades and tc < min_trades:
        return _DISQUALIFY  # statistically meaningless sample
    # One-sided guard: require the minority direction (CE vs PE) to hold at
    # least `min_direction_share` of trades. Off when share <= 0.
    if min_direction_share and min_direction_share > 0:
        ce = int(metrics.get("ce_count", 0) or 0)
        pe = int(metrics.get("pe_count", 0) or 0)
        tot = ce + pe
        if tot > 0 and (min(ce, pe) / tot) < min_direction_share:
            return _DISQUALIFY

    if objective == "sharpe":
        v = metrics.get("sharpe")
        return float(v) if v is not None else _DISQUALIFY
    if objective == "profit_factor":
        v = metrics.get("profit_factor")
        return float(v) if v is not None else 0.0
    if objective == "total_pnl_pts":
        return float(metrics.get("total_pnl_pts", 0) or 0)
    if objective == "net_pnl_inr":
        # Net rupee P&L = net points (already cost-adjusted when costs_enabled)
        # × lot size. This is an honest index-point→rupee conversion; it does
        # not model option premium decay (see option-aware mode, future slice).
        return float(metrics.get("total_pnl_pts", 0) or 0) * float(lot_size)
    if objective == "win_rate":
        return float(metrics.get("win_rate", 0) or 0)
    if objective == "neg_max_dd":
        return -abs(float(metrics.get("max_dd_pts", 0) or 0))
    # risk_adjusted (default)
    sharpe = float(metrics.get("sharpe") or 0)
    dd = abs(float(metrics.get("max_dd_pts") or 1))
    return sharpe / max(1.0, dd / 100.0)


def _indicator_key(merged: Dict[str, Any]) -> Tuple:
    """Cache key capturing only the params that change indicator computation."""
    return tuple((k, merged.get(k)) for k in INDICATOR_PARAM_KEYS)


def _build_param_space(
    parameter_schema: Dict[str, Any],
    overrides: Dict[str, Any] | None,
    include_indicator_periods: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Return space {name: {type, min, max, step?, default, fixed?}} after applying user overrides.
    overrides[name] can be {min, max, fixed} to widen/narrow/lock a param.

    When include_indicator_periods=True, inject the standard indicator-period
    params (RSI/MACD/ATR/EMA/ADX/CHOP/swing) that the strategy doesn't already
    declare, so they become tunable (their changes trigger indicator recompute)."""
    overrides = overrides or {}
    space: Dict[str, Dict[str, Any]] = {}
    for name, defn in parameter_schema.items():
        t = defn.get("type")
        if t not in ("int", "float", "bool"):
            # ignore string params for optimization
            continue
        info = dict(defn)
        ov = overrides.get(name, {})
        if "fixed" in ov:
            info["fixed"] = ov["fixed"]
        if "min" in ov:
            info["min"] = ov["min"]
        if "max" in ov:
            info["max"] = ov["max"]
        space[name] = info

    if include_indicator_periods:
        for name, defn in INDICATOR_PARAM_CATALOG.items():
            if name in space:
                continue  # strategy already exposes it — respect its bounds
            info = dict(defn)
            ov = overrides.get(name, {})
            if "fixed" in ov:
                info["fixed"] = ov["fixed"]
            if "min" in ov:
                info["min"] = ov["min"]
            if "max" in ov:
                info["max"] = ov["max"]
            info["_indicator_period"] = True  # tag for UI/debugging
            space[name] = info
    return space


def _suggest(trial: optuna.Trial, space: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out = {}
    for name, info in space.items():
        if "fixed" in info:
            out[name] = info["fixed"]
            continue
        t = info["type"]
        if t == "int":
            lo, hi = int(info.get("min", 0)), int(info.get("max", 100))
            out[name] = trial.suggest_int(name, lo, hi)
        elif t == "float":
            lo, hi = float(info.get("min", 0.0)), float(info.get("max", 1.0))
            out[name] = trial.suggest_float(name, lo, hi)
        elif t == "bool":
            out[name] = trial.suggest_categorical(name, [True, False])
    return out


def _grid_combinations(space: Dict[str, Dict[str, Any]], max_trials: int) -> List[Dict[str, Any]]:
    """Build grid; if too large, sub-sample uniformly."""
    axes: List[List[Tuple[str, Any]]] = []
    for name, info in space.items():
        if "fixed" in info:
            axes.append([(name, info["fixed"])])
            continue
        t = info["type"]
        if t == "int":
            lo, hi = int(info["min"]), int(info["max"])
            step = max(1, (hi - lo) // 6)
            vals = list(range(lo, hi + 1, step))
        elif t == "float":
            lo, hi = float(info["min"]), float(info["max"])
            vals = list(np.linspace(lo, hi, num=6))
        elif t == "bool":
            vals = [True, False]
        else:
            vals = [info.get("default")]
        axes.append([(name, v) for v in vals])

    # Cartesian product (capped)
    combos = [{}]
    for axis in axes:
        nxt = []
        for c in combos:
            for k, v in axis:
                cc = dict(c); cc[k] = v
                nxt.append(cc)
        combos = nxt
        if len(combos) > max_trials * 10:
            # too big — sub-sample to keep things tractable
            np.random.seed(42)
            idx = np.random.choice(len(combos), size=max_trials * 5, replace=False)
            combos = [combos[i] for i in idx]
    if len(combos) > max_trials:
        np.random.seed(42)
        idx = np.random.choice(len(combos), size=max_trials, replace=False)
        combos = [combos[int(i)] for i in idx]
    return combos


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

async def _update_job(job_id: str, patch: Dict[str, Any]) -> None:
    db = get_db()
    await db.optimization_jobs.update_one({"id": job_id}, {"$set": patch})


def _evaluate(get_enriched, strategy, params: Dict[str, Any], instrument: str, costs: bool, pretrade: Dict[str, Any],
              trade_window_start: Optional[str] = None, trade_window_end: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Run one backtest with given params and return (metrics, merged_params).

    `get_enriched(merged)` returns the indicator+regime enriched dataframe for
    the merged params (recomputing when indicator periods change). Direction
    counts (ce/pe) are folded into the metrics so the guard rails and UI can
    detect one-sided solutions. trade_window_* (O6): when both set, restrict entries
    to the live-effective IST window; None → run_backtest's own 09:25–15:00 default
    (byte-identical for pre-O6 callers)."""
    merged = strategy.merged_params(params)
    df_enriched = get_enriched(merged)
    _tw = ({"trade_window_start": trade_window_start, "trade_window_end": trade_window_end}
           if trade_window_start and trade_window_end else {})
    res = run_backtest(df_enriched, strategy, merged, instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade, **_tw)
    metrics = dict(res["metrics"])
    trades = res.get("trades", []) or []
    ce = sum(1 for t in trades if str(t.get("direction", "")).upper() == "CE")
    metrics["ce_count"] = int(ce)
    metrics["pe_count"] = int(len(trades) - ce)
    return metrics, merged


def _premium_zero_metrics() -> Dict[str, Any]:
    """Zero-trade metrics for a premium trial whose config is invalid or whose
    data window failed to preload — shaped so _objective_value's existing
    trade_count==0 guard disqualifies it exactly like a real no-trade result."""
    return {"trade_count": 0, "ce_count": 0, "pe_count": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "sharpe": 0.0, "profit_factor": None,
            "total_pnl_pts": 0.0, "max_dd_pts": 0.0, "total_option_pnl_value": 0.0}


def _evaluate_premium_trigger(
    strategy, merged_params: Dict[str, Any], spot_df, option_candles, contracts,
    instrument: str, objective: str, lot_size: int, min_trades: int,
    min_direction_share: float,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """premium_momentum variant of the per-trial `_evaluate` (same return shape:
    (metrics, merged_params), a drop-in). strategy.evaluate() is a deliberate
    stub for this strategy (the real logic lives only in deployment_evaluator.py's
    Track B branch), so the spot path scores EVERY trial trade_count=0 and
    _objective_value's unconditional zero-trade guard disqualified all of them —
    Stage 1 could never surface a candidate for the (already fixed) Stage-2
    re-rank. Instead dispatch the trial straight to the option-native sim and
    reshape its envelope into the metrics contract _objective_value /
    _RESUME_METRIC_KEYS / _robustness_score / _heatmap already consume.

    objective/lot_size/min_trades/min_direction_share carry the call site's
    scoring context for signature parity with the evaluate closure's inputs;
    the actual guarding/scoring still happens in _objective_value (the caller's
    `obj`), so the guard rails behave identically to every other strategy.
    """
    from app.premium_trigger_dispatch import dispatch_full_backtest

    if spot_df is None or getattr(spot_df, "empty", True):
        # Preload failed (see run_optimization's _load_window block) — honest
        # zero-trade disqualification, never a crash mid-trial-loop.
        return _premium_zero_metrics(), merged_params
    result = dispatch_full_backtest(
        strategy_id=strategy.id, merged_params=merged_params,
        spot_df=spot_df, option_candles=option_candles, contracts=contracts,
        instrument=instrument)
    if result is None:
        # Invalid PremiumTriggerConfig (e.g. no entry trigger) — same honest path.
        return _premium_zero_metrics(), merged_params

    m = result.get("metrics") or {}
    port = result.get("portfolio") or {}
    paired = [t for t in result.get("trades", []) or [] if t.get("status") == "PAIRED"]
    pnls = [float(t.get("option_pnl_value", 0.0) or 0.0) for t in paired]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    if gross_loss > 0:
        profit_factor = round(gross_win / gross_loss, 3)
    elif gross_win > 0:
        profit_factor = 999.0  # only wins: large-but-finite (inf breaks JSON/ranking)
    else:
        profit_factor = None   # no wins and no losses -> undefined (scored 0.0)
    ce = sum(1 for t in paired if str(t.get("direction", "")).upper() == "CE")
    sharpe = port.get("sharpe_daily")
    metrics = {
        "trade_count": int(m.get("paired_trade_count", 0) or 0),
        "ce_count": int(ce),
        "pe_count": int(len(paired) - ce),
        "wins": int(m.get("wins", 0) or 0),
        "losses": int(m.get("losses", 0) or 0),
        # 0-100 scale — option_backtest._compute_metrics (wins/paired*100) matches
        # backtest.py's spot win_rate scale, so the win_rate objective is comparable.
        "win_rate": float(m.get("win_rate", 0.0) or 0.0),
        "sharpe": float(sharpe) if sharpe is not None else 0.0,
        "profit_factor": profit_factor,
        "total_pnl_pts": float(m.get("total_option_pnl_pts", 0.0) or 0.0),
        # NOT a true unit match: rupee max-drawdown substituted where the spot
        # formula expects index points (a rupee-native premium strategy has no
        # index-points drawdown concept) — an honest, documented proxy.
        "max_dd_pts": abs(float(port.get("max_drawdown_value", 0.0) or 0.0)),
        "total_option_pnl_value": float(m.get("total_option_pnl_value", 0.0) or 0.0),
    }
    return metrics, merged_params


def _robustness_score(evaluate_fn, obj_fn, best_params: Dict[str, Any], space: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Perturb each numeric param by ±10% and ±20%; count fraction that stay 'profitable'.
    Returns {score_0_100, perturbation_results}.

    evaluate_fn(params) -> (metrics, merged); obj_fn(metrics) -> objective value
    (already guard-aware).
    """
    base_metrics, _ = evaluate_fn(best_params)
    base_val = obj_fn(base_metrics)
    n_total = 0
    n_ok = 0
    perturbations = []
    for name, info in space.items():
        if "fixed" in info or info["type"] == "bool":
            continue
        if name not in best_params:
            continue
        base_v = float(best_params[name])
        for pct in (-0.20, -0.10, 0.10, 0.20):
            t_v = base_v * (1 + pct)
            t_v = max(float(info["min"]), min(float(info["max"]), t_v))
            if info["type"] == "int":
                t_v = int(round(t_v))
            test_params = dict(best_params)
            test_params[name] = t_v
            metrics, _ = evaluate_fn(test_params)
            val = obj_fn(metrics)
            ok = val >= base_val * 0.85 and metrics.get("trade_count", 0) >= 5
            n_total += 1
            if ok:
                n_ok += 1
            perturbations.append({
                "param": name, "shift_pct": int(pct * 100),
                "value": t_v, "objective": round(val, 3),
                "trades": metrics.get("trade_count", 0), "ok": bool(ok),
            })
    score = round((n_ok / n_total * 100) if n_total > 0 else 0, 1)
    return {"score": score, "perturbations": perturbations, "base_objective": round(base_val, 4)}


def _param_importance(study: optuna.Study, space: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        imp = optuna.importance.get_param_importances(study)
        return [{"param": k, "importance": round(float(v), 4)} for k, v in imp.items()]
    except Exception as e:
        log.warning(f"param_importance failed: {e}")
        return []


def _heatmap(evaluate_fn, obj_fn, best_params, importance, space, grid_n=8) -> Optional[Dict[str, Any]]:
    """Build a 2D heatmap over top-2 numeric params from importance."""
    numeric = [it["param"] for it in importance if it["param"] in space and space[it["param"]]["type"] != "bool"]
    if len(numeric) < 2:
        # Fallback: pick first two numeric params from space
        numeric = [k for k, v in space.items() if v["type"] != "bool" and "fixed" not in v][:2]
    if len(numeric) < 2:
        return None
    pa, pb = numeric[0], numeric[1]
    info_a, info_b = space[pa], space[pb]
    a_vals = np.linspace(info_a["min"], info_a["max"], grid_n)
    b_vals = np.linspace(info_b["min"], info_b["max"], grid_n)
    grid = []
    for av in a_vals:
        row = []
        for bv in b_vals:
            test = dict(best_params)
            test[pa] = int(round(av)) if info_a["type"] == "int" else float(av)
            test[pb] = int(round(bv)) if info_b["type"] == "int" else float(bv)
            metrics, _ = evaluate_fn(test)
            row.append({
                "val": round(obj_fn(metrics), 3),
                "trades": int(metrics.get("trade_count", 0)),
            })
        grid.append(row)
    return {
        "param_a": pa, "param_b": pb,
        "a_values": [round(float(x), 3) for x in a_vals],
        "b_values": [round(float(x), 3) for x in b_vals],
        "grid": grid,
    }


async def _is_cancelled(job_id: str) -> bool:
    db = get_db()
    doc = await db.optimization_jobs.find_one({"id": job_id}, {"cancelled": 1})
    return bool(doc and doc.get("cancelled"))


async def _job_control(job_id: str) -> Tuple[bool, bool]:
    """Single-query read of the (cancelled, paused) control flags. A deleted job
    is treated as cancelled so the worker stops promptly."""
    doc = await get_db().optimization_jobs.find_one({"id": job_id}, {"cancelled": 1, "paused": 1})
    if not doc:
        return (True, False)
    return (bool(doc.get("cancelled")), bool(doc.get("paused")))


# Per-trial metrics kept in the persisted resume log (bounds doc size while
# still covering everything the UI's Top-Alternatives table shows).
_RESUME_METRIC_KEYS = (
    "trade_count", "win_rate", "profit_factor", "total_pnl_pts",
    "max_dd_pts", "sharpe", "ce_count", "pe_count",
)


def _compact_trial(t: Dict[str, Any]) -> Dict[str, Any]:
    m = t.get("metrics") or {}
    return {
        "params": t.get("params") or {},
        "objective_value": t.get("objective_value"),
        "metrics": {k: m.get(k) for k in _RESUME_METRIC_KEYS if k in m},
    }


async def _flush_trial_log(job_id: str, trial_history: List[Dict[str, Any]], best_so_far: Dict[str, Any], completed: int) -> None:
    """Persist a compact trial log + best-so-far so a paused or crashed job can
    resume from its last saved stage instead of starting over."""
    await _update_job(job_id, {
        "trial_log": [_compact_trial(t) for t in trial_history],
        "n_trials_completed": completed,
        "best_so_far": {
            "value": round(best_so_far["value"], 4) if best_so_far["value"] > -1e8 else None,
            "params": best_so_far["params"], "metrics": best_so_far["metrics"],
            "trial_num": best_so_far["trial_num"],
        },
    })


def _rebuild_study(method: str, space: Dict[str, Dict[str, Any]], trial_history: List[Dict[str, Any]]):
    """Recreate an Optuna study and re-seed it with previously completed trials
    so the sampler (TPE/CMA-ES) continues intelligently after a resume. Best
    effort — any malformed record is skipped, and grid needs no seeding."""
    study = optuna.create_study(direction="maximize", sampler=_make_sampler(method))
    if method == "grid":
        return study
    dists: Dict[str, Any] = {}
    for name, info in space.items():
        if "fixed" in info:
            continue
        t = info.get("type")
        try:
            if t == "int":
                dists[name] = optuna.distributions.IntDistribution(int(info["min"]), int(info["max"]))
            elif t == "float":
                dists[name] = optuna.distributions.FloatDistribution(float(info["min"]), float(info["max"]))
            elif t == "bool":
                dists[name] = optuna.distributions.CategoricalDistribution([True, False])
        except Exception:
            continue
    for rec in trial_history:
        try:
            p = rec.get("params") or {}
            params = {k: p[k] for k in dists if k in p}
            if len(params) != len(dists):
                continue
            v = rec.get("objective_value")
            v = float(v) if v is not None else _DISQUALIFY
            study.add_trial(optuna.trial.create_trial(params=params, distributions=dists, value=v))
        except Exception:
            continue
    return study


async def _save_best_as_backtest(job_id: str, payload: Dict[str, Any], strategy, df_enriched: pd.DataFrame, best_params: Dict[str, Any], instrument: str, costs_enabled: bool, pretrade: Dict[str, Any], run_walkforward: bool = True, option_config: Optional[Dict[str, Any]] = None, n_trials: Optional[int] = None) -> Optional[str]:
    """Run a final full backtest with best params and persist as a backtest_run.
    Returns the new backtest_run_id (or None on failure). When run_walkforward
    is False (e.g. on cancellation) the slow multi-fold walk-forward is skipped
    so the result is saved quickly."""
    try:
        from app.walkforward import walk_forward
        from app.backtest import stat_significance
        merged = strategy.merged_params(best_params)
        res = await asyncio.to_thread(run_backtest, df_enriched, strategy, merged, instrument=instrument, costs_enabled=costs_enabled, pretrade_filters=pretrade)
        metrics = res["metrics"]
        wf = None
        if run_walkforward and len(df_enriched) >= 200:
            wf = await asyncio.to_thread(walk_forward, df_enriched, strategy, merged, instrument=instrument, costs_enabled=costs_enabled, pretrade_filters=pretrade)
        sig = stat_significance(metrics["trade_count"], metrics["win_rate"], metrics.get("profit_factor"))
        regime_dist = df_enriched["regime"].value_counts().to_dict()
        regime_dist = {str(k): int(v) for k, v in regime_dist.items()}
        run_name = f"Optimized · {payload.get('name', 'run')}"
        # Fix-A: compute the PROMOTED config's full-window paired-option result so the
        # saved run carries the honest exact-params option-rupee numbers. validate=False
        # (the grid-derived spot_exit+exit_controls overlay is internally trusted and would
        # otherwise 400); auto_fetch off so the replay pairs against the same warehouse
        # snapshot the re-rank scored on.
        option_result = None
        if option_config:
            try:
                from app.runtime import _run_paired_option_backtest
                from app.schemas import BacktestReq, OptionBacktestReq
                _opt_req = BacktestReq(
                    instrument=instrument, strategy_id=strategy.id, params=best_params,
                    start_ts=payload.get("start_ts"), end_ts=payload.get("end_ts"),
                    costs_enabled=costs_enabled, walkforward=False, pretrade_filters=pretrade,
                    option_backtest=OptionBacktestReq(**{**option_config, "enabled": True, "auto_fetch": False}),
                )
                option_result = await _run_paired_option_backtest(_opt_req, res["trades"], validate=False)
            except Exception as e:
                log.warning(f"save_best option backtest failed: {e}")
        doc = {
            "id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "name": run_name,
            "config": {
                "instrument": instrument,
                "mode": payload.get("mode", "SCALP"),
                "strategy_id": strategy.id,
                "timeframe": "1m",
                "params": best_params,
                "costs_enabled": costs_enabled,
                "walkforward": True,
                "start_ts": payload.get("start_ts"),
                "end_ts": payload.get("end_ts"),
                "pretrade_filters": pretrade,
                "source": "auto-from-optimizer",
                "optimization_job_id": job_id,
                **({"option_backtest": {**option_config, "enabled": True}} if option_config else {}),
            },
            "params_applied": merged,
            "metrics": metrics,
            "trades": res["trades"],
            "equity_curve": res["equity_curve"],
            "walkforward": wf,
            "significance": sig,
            "candle_count": int(len(df_enriched)),
            "regime_distribution": regime_dist,
            "signal_funnel": res["signal_funnel"],
            "instrument": instrument,
            "strategy_id": strategy.id,
            **({"n_trials": int(n_trials)} if n_trials else {}),
            # Fix-A: top-level option result (conditional key -> spot mode byte-identical).
            **({"option_backtest": option_result} if option_config else {}),
        }
        db = get_db()
        await db.backtest_runs.insert_one(doc)
        return doc["id"]
    except Exception as e:
        log.warning(f"save_best_as_backtest failed: {e}")
        return None


def _ts_to_ist_date(ts_ms: int) -> str:
    return pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").tz_convert("Asia/Kolkata").date().isoformat()


def _resolve_expiry_by_trade(spot_trades, contracts, fixed_expiry_date=None) -> Dict[int, str]:
    """Nearest contract expiry on/after each trade's IST entry date (or a fixed
    override). Mirrors the backtest route's resolver so re-rank pairs the same
    contracts the Backtest Lab would."""
    if fixed_expiry_date:
        return {i: fixed_expiry_date for i in range(len(spot_trades))}
    expiries = sorted({str(c.get("expiry_date")) for c in contracts if c.get("expiry_date")})
    out: Dict[int, str] = {}
    for idx, t in enumerate(spot_trades):
        ets = t.get("entry_ts")
        if ets is None:
            continue
        td = _ts_to_ist_date(int(ets))
        r = next((e for e in expiries if e >= td), None)
        if r:
            out[idx] = r
    return out


async def _survival_eval_oos_premium_trigger(
    strategy, df_enriched, merged_params, contracts, candles_df,
    instrument, option_cfg, sc, n_folds=3, train_pct=0.6,
):
    """premium_momentum variant of _survival_eval_oos. No spot signal exists to
    pair (evaluate() is a deliberate stub), so each fold dispatches straight to
    the option-native sim via dispatch_full_backtest instead of run_backtest +
    simulate_paired_option_trades. Reuses the contracts/candles the caller
    already loaded (no extra DB round-trip) — only needs session_date/ist_time
    columns added to each fold's slice, mirroring premium_momentum_routes.py's
    _load_window (the sim's session-iteration relies on them)."""
    from app.portfolio import build_rupee_equity_curve
    from app.premium_trigger_dispatch import dispatch_full_backtest

    capital = float((option_cfg.get("sizing_config") or {}).get("capital", 200_000) or 200_000)
    all_paired: List[Dict[str, Any]] = []
    fold_pass: List[bool] = []
    spot_total = paired_total = skipped_total = 0
    for _fold, a, b in oos_fold_index_ranges(len(df_enriched), n_folds, train_pct):
        test_df = df_enriched.iloc[a:b].reset_index(drop=True).copy()
        if not test_df.empty and "session_date" not in test_df.columns:
            _dt = pd.to_datetime(test_df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
            test_df["session_date"] = _dt.dt.strftime("%Y-%m-%d")
            test_df["ist_time"] = _dt.dt.strftime("%H:%M")
        pm_result = await asyncio.to_thread(
            dispatch_full_backtest, strategy_id=strategy.id, merged_params=merged_params,
            spot_df=test_df, option_candles=candles_df, contracts=contracts,
            instrument=instrument, capital=capital,
        )
        if pm_result is None:
            fold_pass.append(False)
            continue
        port = pm_result.get("portfolio") or {}
        cov = pm_result.get("coverage") or {}
        spot_total += int(cov.get("spot_trade_count", 0) or 0)
        paired_total += int(cov.get("paired_trade_count", 0) or 0)
        skipped_total += int(cov.get("skipped_by_cap", 0) or 0)
        all_paired.extend(t for t in pm_result.get("trades", []) if t.get("status") == "PAIRED")
        curve = port.get("curve") or []
        eqs = [c.get("equity_value") for c in curve if c.get("equity_value") is not None]
        floor_ok = (min(eqs) > sc.min_equity) if eqs else False
        dd = port.get("max_drawdown_pct")
        dd_ok = dd is not None and abs(float(dd)) <= sc.max_drawdown_pct
        fold_pass.append(bool(floor_ok and dd_ok))

    folds_ok = (all(fold_pass) if sc.min_oos_folds == "all"
                else sum(fold_pass) > len(fold_pass) / 2) if fold_pass else False
    stitched_port = build_rupee_equity_curve(all_paired, capital=capital)
    trade_pnls = [float(t.get("option_pnl_value", 0.0)) for t in all_paired]
    verdict = survival_verdict(
        portfolio=stitched_port, trade_pnls=trade_pnls, cfg=sc,
        coverage={"spot_trade_count": spot_total, "paired_trade_count": paired_total,
                  "skipped_by_cap": skipped_total},
        capital=capital)
    verdict["folds_ok"] = folds_ok
    verdict["fold_pass"] = fold_pass
    verdict["survived"] = bool(verdict["survived"] and folds_ok)
    return verdict


async def _survival_eval_oos(
    strategy, df_enriched, merged_params, contracts, candles_df,
    instrument, costs, pretrade, option_cfg, sc, n_folds=3, train_pct=0.6,
    candles_by_key=None, trade_window_start=None, trade_window_end=None,
):
    """Evaluate one finalist's survival on each walk-forward OOS slice. Floor + DD%
    must hold per fold (per sc.min_oos_folds); RoR runs on the stitched OOS rupee
    series. Returns the survival_verdict dict augmented with folds_ok/fold_pass."""
    if getattr(strategy, "id", None) == "premium_momentum":
        return await _survival_eval_oos_premium_trigger(
            strategy, df_enriched, merged_params, contracts, candles_df,
            instrument, option_cfg, sc, n_folds=n_folds, train_pct=train_pct,
        )

    from app.portfolio import build_rupee_equity_curve
    moneyness = str(option_cfg.get("moneyness") or "atm")
    lots = int(option_cfg.get("lots") or 1)
    fixed_expiry = option_cfg.get("expiry_date")
    capital = float((option_cfg.get("sizing_config") or {}).get("capital", 200_000) or 200_000)
    # Apply the SAME DTE filter as _option_rerank, so survival is evaluated on the
    # exact contract set the finalist was ranked on (else a dte_filter run would
    # pair a broader set and give a non-comparable verdict).
    dte_target = normalize_dte_filter(option_cfg.get("dte_filter"))
    expiry_dates_sorted = sorted({str(c.get("expiry_date")) for c in contracts if c.get("expiry_date")})
    all_paired = []
    fold_pass = []
    spot_total = paired_total = skipped_total = 0
    for _fold, a, b in oos_fold_index_ranges(len(df_enriched), n_folds, train_pct):
        test_df = df_enriched.iloc[a:b].reset_index(drop=True)
        _tw = ({"trade_window_start": trade_window_start, "trade_window_end": trade_window_end}
               if trade_window_start and trade_window_end else {})
        res = await asyncio.to_thread(
            run_backtest, test_df, strategy, merged_params,
            instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade, **_tw)
        spot_trades = res.get("trades", []) or []
        if dte_target is not None:
            spot_trades = [t for t in spot_trades if t.get("entry_ts") is not None
                           and compute_dte(_ts_to_ist_date(int(t["entry_ts"])), expiry_dates_sorted) in dte_target]
        if not spot_trades:
            # Conservative: a fold with no (DTE-matching) signals yields no survival
            # evidence -> fail it. Under min_oos_folds="all" an idle fold fails the
            # candidate; the stitched-OOS sample guard also requires enough total trades.
            fold_pass.append(False)
            continue
        ebt = _resolve_expiry_by_trade(spot_trades, contracts, fixed_expiry)
        sim = await asyncio.to_thread(
            simulate_paired_option_trades,
            spot_trades=spot_trades, contracts=contracts, option_candles=candles_df,
            candles_by_key=candles_by_key,
            underlying=instrument, moneyness=moneyness, lots=lots,
            entry_max_age_sec=int(option_cfg.get("entry_max_age_sec") or 120),
            exit_max_age_sec=int(option_cfg.get("exit_max_age_sec") or 180),
            expiry_by_trade=ebt, fixed_expiry_date=fixed_expiry,
            exit_mode=option_cfg.get("exit_mode") or "spot_exit",
            option_target_pts=option_cfg.get("option_target_pts"),
            option_stop_pts=option_cfg.get("option_stop_pts"),
            option_target_pct=option_cfg.get("option_target_pct"),
            option_stop_pct=option_cfg.get("option_stop_pct"),
            cost_config=option_cfg.get("cost_config"),
            sizing_config=option_cfg.get("sizing_config"),
            exit_controls=option_cfg.get("exit_controls"),
            daily_caps=option_cfg.get("daily_caps"),
        )
        port = sim.get("portfolio") or {}
        cov = sim.get("coverage") or {}
        spot_total += int(cov.get("spot_trade_count", 0) or 0)
        paired_total += int(cov.get("paired_trade_count", 0) or 0)
        skipped_total += int(cov.get("skipped_by_cap", 0) or 0)
        all_paired.extend(t for t in sim.get("trades", []) if t.get("status") == "PAIRED")
        curve = port.get("curve") or []
        eqs = [c.get("equity_value") for c in curve if c.get("equity_value") is not None]
        floor_ok = (min(eqs) > sc.min_equity) if eqs else False
        dd = port.get("max_drawdown_pct")
        dd_ok = dd is not None and abs(float(dd)) <= sc.max_drawdown_pct
        fold_pass.append(bool(floor_ok and dd_ok))

    folds_ok = (all(fold_pass) if sc.min_oos_folds == "all"
                else sum(fold_pass) > len(fold_pass) / 2) if fold_pass else False
    stitched_port = build_rupee_equity_curve(all_paired, capital=capital)
    trade_pnls = [float(t.get("option_pnl_value", 0.0)) for t in all_paired]
    verdict = survival_verdict(
        portfolio=stitched_port, trade_pnls=trade_pnls, cfg=sc,
        coverage={"spot_trade_count": spot_total, "paired_trade_count": paired_total,
                  "skipped_by_cap": skipped_total},
        capital=capital)
    verdict["folds_ok"] = folds_ok
    verdict["fold_pass"] = fold_pass
    verdict["survived"] = bool(verdict["survived"] and folds_ok)
    return verdict


async def _option_rerank_premium_trigger(
    candidates: List[Dict[str, Any]], get_enriched, strategy, instrument: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Any, bool]:
    """premium_momentum's Stage-2 re-rank. `strategy.evaluate()` is a deliberate
    stub (real logic lives only in deployment_evaluator.py's dedicated branch), so
    there is no spot signal to pair — dispatching each candidate through
    run_backtest + simulate_paired_option_trades always produced zero paired
    trades (the "Option re-rank produced no paired results" bug). Instead dispatch
    each candidate straight to the option-native sim via dispatch_full_backtest.
    Loads the shared warehouse window ONCE (same historical period for every
    candidate in the sweep) covering the union of moneyness values any candidate
    might need, mirroring _option_rerank's own "load once, simulate per-candidate
    in-memory" shape so this stays a drop-in Stage-2 substitute."""
    from app.premium_trigger_dispatch import dispatch_full_backtest
    from app.routers.premium_momentum_routes import _load_window

    def _degenerate(cand: Dict[str, Any]) -> Dict[str, Any]:
        return {"params": cand["params"], "spot_objective": cand["objective_value"],
                "spot_metrics": cand["metrics"], "option_pnl_value": 0.0, "option_pnl_pts": 0.0,
                "option_win_rate": 0.0, "paired_trade_count": 0, "spot_trade_count": 0,
                "coverage": {}}

    if not candidates:
        return ([], [], pd.DataFrame(), False)

    merged_list = [strategy.merged_params(c["params"]) for c in candidates]
    enr0 = get_enriched(merged_list[0])
    if enr0.empty or "ts" not in enr0.columns:
        return ([_degenerate(c) for c in candidates], [], pd.DataFrame(), False)

    start_ts, end_ts = int(enr0["ts"].min()), int(enr0["ts"].max())
    moneynesses = sorted({str(m.get("moneyness") or "itm1") for m in merged_list})
    ref_time = str(merged_list[0].get("reference_time") or "09:31")
    loaded = await _load_window(instrument, start_ts, end_ts, ref_time=ref_time,
                                moneynesses=moneynesses, sides=["CE", "PE"])
    if loaded is None:
        return ([_degenerate(c) for c in candidates], [], pd.DataFrame(), False)
    spot_df, option_candles, contracts = loaded

    ranked: List[Dict[str, Any]] = []
    for cand, merged in zip(candidates, merged_list):
        pm_result = await asyncio.to_thread(
            dispatch_full_backtest, strategy_id=strategy.id, merged_params=merged,
            spot_df=spot_df, option_candles=option_candles, contracts=contracts,
            instrument=instrument,
        )
        if pm_result is None:
            ranked.append(_degenerate(cand))
            continue
        m = pm_result["metrics"]
        ranked.append({
            "params": cand["params"], "spot_objective": cand["objective_value"],
            "spot_metrics": cand["metrics"],
            "option_pnl_value": float(m.get("total_option_pnl_value", 0.0) or 0.0),
            "option_pnl_pts": float(m.get("total_option_pnl_pts", 0.0) or 0.0),
            "option_win_rate": float(m.get("win_rate", 0.0) or 0.0),
            "paired_trade_count": int(m.get("paired_trade_count", 0) or 0),
            "spot_trade_count": int(m.get("paired_trade_count", 0) or 0),
            "coverage": pm_result["coverage"],
        })
    ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)
    return ranked, contracts, option_candles, False


async def _option_rerank(
    db, strategy, get_enriched, candidates: List[Dict[str, Any]],
    instrument: str, costs: bool, pretrade: Dict[str, Any], option_cfg: Dict[str, Any],
    *, analyze_t0: Optional[float] = None, analyze_budget_sec: int = 0, progress_cb=None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Any, bool]:  # (ranked, contracts, candles_df, budget_hit)
    """Stage 2: re-score the top-K spot candidates on REAL paired-option net
    rupee. Option contracts + candles are loaded from the DB ONCE (over the
    union of all candidates' needed strikes), then each candidate is simulated
    in-memory. Returns candidates ranked by option net-rupee P&L."""
    if getattr(strategy, "id", None) == "premium_momentum":
        return await _option_rerank_premium_trigger(candidates, get_enriched, strategy, instrument)

    moneyness = str(option_cfg.get("moneyness") or "atm")
    lots = int(option_cfg.get("lots") or 1)
    fixed_expiry = option_cfg.get("expiry_date")
    dte_target = normalize_dte_filter(option_cfg.get("dte_filter"))
    exit_mode = option_cfg.get("exit_mode") or "spot_exit"
    cost_config = option_cfg.get("cost_config")
    sizing_config = option_cfg.get("sizing_config")
    exit_controls = option_cfg.get("exit_controls")
    daily_caps = option_cfg.get("daily_caps")
    entry_max_age = int(option_cfg.get("entry_max_age_sec") or 120)
    exit_max_age = int(option_cfg.get("exit_max_age_sec") or 180)
    opt_tp, opt_sp = option_cfg.get("option_target_pts"), option_cfg.get("option_stop_pts")
    opt_tpct, opt_spct = option_cfg.get("option_target_pct"), option_cfg.get("option_stop_pct")

    # 1. Spot backtest each candidate (fast post Slice-1) to get its trades.
    cand_trades: List[List[Dict[str, Any]]] = []
    for cand in candidates:
        merged = strategy.merged_params(cand["params"])
        enr = get_enriched(merged)
        res = await asyncio.to_thread(
            run_backtest, enr, strategy, merged,
            instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade,
        )
        cand_trades.append(res.get("trades", []) or [])

    all_ts = [int(t["entry_ts"]) for tr in cand_trades for t in tr if t.get("entry_ts") is not None]
    all_xt = [int(t["exit_ts"]) for tr in cand_trades for t in tr if t.get("exit_ts") is not None]
    if not all_ts:
        return ([{"params": c["params"], "spot_objective": c["objective_value"],
                  "spot_metrics": c["metrics"], "option_pnl_value": 0.0, "option_pnl_pts": 0.0,
                  "option_win_rate": 0.0, "paired_trade_count": 0, "spot_trade_count": 0,
                  "coverage": {}} for c in candidates], [], pd.DataFrame(), False)

    # 2. Load contracts once (windowed by the candidates' trade span + margin).
    contract_query: Dict[str, Any] = {"underlying": instrument}
    if fixed_expiry:
        contract_query["expiry_date"] = fixed_expiry
    else:
        win_start = _ts_to_ist_date(min(all_ts))
        last_ms = max(all_xt) if all_xt else max(all_ts)
        win_end = _ts_to_ist_date(last_ms + 21 * 24 * 3600 * 1000)
        contract_query["expiry_date"] = {"$gte": win_start, "$lte": win_end}
    contracts = await db.option_contracts.find(contract_query, {"_id": 0}).sort(
        [("expiry_date", 1), ("strike", 1), ("side", 1)]
    ).to_list(length=None)
    expiry_dates_sorted = sorted({str(c.get("expiry_date")) for c in contracts if c.get("expiry_date")})

    # 3. Per candidate: DTE filter + expiry resolution + needed contract keys.
    per_cand: List[Dict[str, Any]] = []
    union_keys: set = set()
    for trades in cand_trades:
        ft = trades
        if dte_target is not None:
            ft = [t for t in trades if t.get("entry_ts") is not None
                  and compute_dte(_ts_to_ist_date(int(t["entry_ts"])), expiry_dates_sorted) in dte_target]
        ebt = _resolve_expiry_by_trade(ft, contracts, fixed_expiry)
        for idx, t in enumerate(ft):
            rexp = fixed_expiry or ebt.get(idx)
            if not rexp:
                continue
            elig = [c for c in contracts if str(c.get("expiry_date", "")) == str(rexp)]
            try:
                sel = select_contract_for_signal(
                    contracts=elig, underlying=instrument,
                    spot_price=float(t.get("entry_price", 0.0)),
                    direction=str(t.get("direction", "")).upper(), moneyness=moneyness,
                )
            except Exception:
                sel = None
            if sel and sel.get("instrument_key"):
                union_keys.add(str(sel["instrument_key"]))
        per_cand.append({"trades": ft, "expiry_by_trade": ebt})

    # 4. Load option candles once for the union of needed contracts.
    candles_df = pd.DataFrame()
    if union_keys:
        # Query BOTH key forms: candles are stored canonical (2-part) but a
        # selected contract doc may carry the dated 3-part key. Harmless
        # post-migration; required while any legacy rows remain.
        from app.instruments import canonical_instrument_key
        query_keys = sorted({k for key in union_keys for k in (str(key), canonical_instrument_key(str(key)))})
        cq = {"instrument_key": {"$in": query_keys},
              "ts": {"$gte": min(all_ts) - entry_max_age * 1000, "$lte": (max(all_xt) if all_xt else max(all_ts))}}
        rows = await db.options_1m.find(cq, {"_id": 0}).sort("ts", 1).to_list(length=4000000)
        if len(rows) >= 4000000:
            # Hitting the cap means later trades would silently lose pairing
            # data — surface it instead of letting coverage quietly degrade.
            log.warning("option re-rank candle load hit the 4M-row cap (%d keys); "
                        "results beyond the cap window are not paired", len(union_keys))
        if rows:
            candles_df = pd.DataFrame(rows)

    # 5. Simulate each candidate in-memory and collect option metrics.
    # Pre-group the (single, shared) candle frame ONCE here instead of letting
    # each of the ~150 per-candidate sims re-group the identical frame internally
    # (the groupby was O(candidates x candles)). simulate_paired_option_trades
    # consumes candles_by_key verbatim; build_candles_by_key IS its internal
    # grouping, so behaviour is byte-identical.
    candles_by_key = build_candles_by_key(candles_df)
    ranked: List[Dict[str, Any]] = []
    budget_hit = False
    _per_item: Optional[float] = None
    for cand, pc in zip(candidates, per_cand):
        _c_t = time.monotonic()
        sim = await asyncio.to_thread(
            simulate_paired_option_trades,
            spot_trades=pc["trades"], contracts=contracts, option_candles=candles_df,
            underlying=instrument, moneyness=moneyness, lots=lots,
            entry_max_age_sec=entry_max_age, exit_max_age_sec=exit_max_age,
            expiry_by_trade=pc["expiry_by_trade"], fixed_expiry_date=fixed_expiry,
            exit_mode=exit_mode, option_target_pts=opt_tp, option_stop_pts=opt_sp,
            option_target_pct=opt_tpct, option_stop_pct=opt_spct,
            cost_config=cost_config, sizing_config=sizing_config,
            exit_controls=exit_controls, daily_caps=daily_caps,
            candles_by_key=candles_by_key,
        )
        m = sim.get("metrics", {})
        cov = sim.get("coverage", {})
        ranked.append({
            "params": cand["params"],
            "spot_objective": cand["objective_value"],
            "spot_metrics": cand["metrics"],
            "option_pnl_value": float(m.get("total_option_pnl_value", 0.0) or 0.0),
            "option_pnl_pts": float(m.get("total_option_pnl_pts", 0.0) or 0.0),
            "option_win_rate": float(m.get("win_rate", 0.0) or 0.0),
            "paired_trade_count": int(m.get("paired_trade_count", 0) or 0),
            "spot_trade_count": len(pc["trades"]),
            "coverage": cov,
        })
        _per_item = ewma(_per_item, time.monotonic() - _c_t)
        if len(ranked) % 10 == 0:
            log.info("rerank %d/%d", len(ranked), len(candidates))
        if progress_cb is not None:
            await progress_cb("option_rerank", len(ranked), len(candidates), _per_item)
        if analyze_t0 is not None and over_budget(
                elapsed=time.monotonic() - analyze_t0, budget_sec=analyze_budget_sec):
            budget_hit = True
            break
    # Rank by option net rupee; candidates with no paired trades sink to the bottom.
    ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)
    # Also return the loaded contracts + candle frame so the survival evaluator can
    # reuse the single (multi-million-row) option-candle load instead of re-querying.
    return ranked, contracts, candles_df, budget_hit


async def run_optimization(job_id: str, payload: Dict[str, Any], resume: bool = False) -> None:
    """Main async optimizer worker. Runs in a background task."""
    try:
        instrument = payload["instrument"].upper()
        strategy_id = payload["strategy_id"]
        method = payload.get("method", "bayesian")  # bayesian | grid | genetic
        objective = payload.get("objective", "risk_adjusted")
        n_trials = int(payload.get("n_trials", 200))
        early_stop = bool(payload.get("early_stop", True))
        es_warmup = int(payload.get("early_stop_warmup", 200) or 0)
        es_patience = int(payload.get("early_stop_patience", 200) or 0)
        es_min_delta = float(payload.get("early_stop_min_delta", 0.001) or 0.0)
        # Scale the ceiling warmup/patience to this run's budget so the default-ON
        # auto-stop actually fires (200/200 never fires at the UI's 150-trial default).
        es_warmup, es_patience = effective_warmup_patience(
            n_trials=n_trials, warmup=es_warmup, patience=es_patience)
        costs = payload.get("costs_enabled", True)
        pretrade = payload.get("pretrade_filters", {})
        param_overrides = payload.get("param_overrides", {})
        start_ts = payload.get("start_ts")
        end_ts = payload.get("end_ts")
        mode = payload.get("mode", "SCALP")

        # --- Guard rails (prevent the degenerate 1-trade / all-PE solutions) ---
        min_trades = int(payload.get("min_trades", 10) or 0)
        # minority direction (CE vs PE) must hold at least this share of trades;
        # 0 disables the one-sided guard.
        min_direction_share = float(payload.get("min_direction_share", 0.0) or 0.0)
        optimize_indicator_periods = bool(payload.get("optimize_indicator_periods", False))
        opt_workers = int(payload.get("opt_workers", 1) or 1)  # opt-in multi-core; 1 = sequential (default)
        # O6: live-effective entry window (IST). Threaded into EVERY optimizer
        # backtest (trials, survival folds, parallel workers) so selection + the
        # survival gate agree and never reward 14:50–15:00 entries live can't take.
        trade_window_start = payload.get("trade_window_start") or None
        trade_window_end = payload.get("trade_window_end") or None

        # Two-stage option re-rank (opt-in). "spot" keeps the original behavior.
        evaluation_mode = str(payload.get("evaluation_mode", "spot"))
        rerank_top_k = int(payload.get("rerank_top_k", 50) or 50)
        # Opt-in: broaden the re-rank shortlist beyond the top-K spot performers
        # with a diversity sample, so an option-profitable-but-spot-mediocre config
        # can surface. Default off -> identical to the historical top-K selection.
        rerank_diversity = bool(payload.get("rerank_diversity", False))
        option_cfg = payload.get("option_config") or {}
        # Capital-aware survival gate (off by default -> identical to legacy behavior).
        survival = SurvivalConfig.from_dict(payload.get("survival_config"))

        strategy = get_registry().get(strategy_id)
        if not strategy:
            await _update_job(job_id, {"status": "failed", "error": f"Strategy {strategy_id} not found", "finished_at": datetime.now(timezone.utc).isoformat()})
            return

        df = await load_candles_df(instrument, start_ts, end_ts)
        if df.empty or len(df) < 100:
            await _update_job(job_id, {"status": "failed", "error": f"Insufficient candles for {instrument} ({len(df)})", "finished_at": datetime.now(timezone.utc).isoformat()})
            return

        # Indicators depend on params (rsi_length, macd_*, atr_length, …). We
        # therefore enrich LAZILY per indicator-period combination and cache the
        # result — recomputing only when those periods actually change. This
        # fixes the long-standing bug where indicator-period params were
        # silently ignored (indicators were frozen at defaults for every trial).
        raw_df = df
        # Per-group indicator memoization: the top-level `enriched_cache` (keyed
        # on the full `_indicator_key`) is the assembled-frame memo for full
        # cache HITS; on a miss we recompute via `enrich_with_cache`, which
        # recomputes ONLY the indicator groups whose params changed (reusing the
        # rest from `_group_caches`). Byte-identical — see
        # tests/test_indicator_equivalence.py.
        enriched_cache: Dict[Tuple, pd.DataFrame] = {}
        _group_caches: Dict[str, Dict] = {}
        _TIMING = {"precompute_s": 0.0, "precompute_n": 0, "backtest_s": 0.0, "backtest_n": 0}

        def get_enriched(merged: Dict[str, Any]) -> pd.DataFrame:
            key = _indicator_key(merged)
            cached = enriched_cache.get(key)
            if cached is not None:
                return cached
            if _OPT_TIMING:
                import time as _t
                _t0 = _t.perf_counter()
            enr = enrich_with_cache(raw_df, merged, _group_caches)
            if _OPT_TIMING:
                _TIMING["precompute_s"] += _t.perf_counter() - _t0
                _TIMING["precompute_n"] += 1
            if len(enriched_cache) < _MAX_ENRICHED_CACHE:
                enriched_cache[key] = enr
            return enr

        # Lot size from the MOST RECENT contract metadata (lot sizes change over
        # time; use the latest expiry so the rupee figure reflects today's lot).
        lot_size = _DEFAULT_LOT_SIZE.get(instrument, 1)
        try:
            lot_doc = await get_db().option_contracts.find_one(
                {"underlying": instrument, "lot_size": {"$gt": 0}},
                {"lot_size": 1},
                sort=[("expiry_date", -1)],
            )
            if lot_doc and lot_doc.get("lot_size"):
                lot_size = int(lot_doc["lot_size"])
        except Exception:
            pass

        # premium_momentum Stage-1 fix: strategy.evaluate() is a deliberate stub
        # (real logic lives only in deployment_evaluator.py's Track B branch), so
        # the spot scorer returned trade_count=0 for EVERY trial and
        # _objective_value's zero-trade guard disqualified all of them — Stage 1
        # could never select a candidate even though Stage 2 (re-rank/survival)
        # now scores correctly. Preload the shared warehouse window ONCE (same
        # _load_window pattern as _option_rerank_premium_trigger) and score each
        # trial through dispatch_full_backtest via _evaluate_premium_trigger.
        pm_spot_df: Optional[pd.DataFrame] = None
        pm_option_candles: Optional[pd.DataFrame] = None
        pm_contracts: List[Dict[str, Any]] = []
        if strategy.id == "premium_momentum":
            from app.routers.premium_momentum_routes import _load_window

            # NOTE: reference_time/moneyness are string params, and
            # _build_param_space (above) skips non-int/float/bool params before
            # it ever looks at param_overrides — so a "fixed" override on either
            # field is structurally never honored by ANY trial's real params
            # (true for every strategy, not just this one; _suggest() never
            # emits these keys, so strategy.merged_params(params) always falls
            # back to the schema default regardless of param_overrides). The
            # preload must match what trials actually get, not what an override
            # claims — derive both from strategy.merged_params({}) (the exact
            # same source _option_rerank_premium_trigger already uses), not from
            # param_overrides, or the preloaded candle window can silently
            # mismatch every trial's real strike/ref-time and bias results
            # toward whichever sessions happen to overlap by coincidence.
            _pm_defaults = strategy.merged_params({})
            _pm_ref = str(_pm_defaults.get("reference_time") or "09:31")
            _pm_money = str(_pm_defaults.get("moneyness") or "itm1")
            try:
                _pm_loaded = await _load_window(
                    instrument, int(raw_df["ts"].min()), int(raw_df["ts"].max()),
                    ref_time=_pm_ref, moneynesses=[_pm_money], sides=["CE", "PE"])
            except Exception as e:
                log.warning(f"premium_momentum optimizer preload failed: {e}")
                _pm_loaded = None
            if _pm_loaded is None:
                # Job still completes — every trial scores as an honest zero-trade
                # result (disqualified), surfaced as a job-level warning, not a crash.
                log.warning("premium_momentum: no spot/option window for %s — all trials will disqualify", instrument)
                await _update_job(job_id, {
                    "warning": ("premium_momentum: could not load the spot/option "
                                "window — every trial scored as zero-trade")})
            else:
                pm_spot_df, pm_option_candles, pm_contracts = _pm_loaded

        # Guard-aware closures used by every trial / analysis below.
        def evaluate(params: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            if strategy.id == "premium_momentum":
                # Option-native Stage-1 scoring (the spot path is a stub — see
                # the preload block above). Same (metrics, merged) contract.
                return _evaluate_premium_trigger(
                    strategy, strategy.merged_params(params), pm_spot_df,
                    pm_option_candles, pm_contracts, instrument,
                    objective, lot_size, min_trades, min_direction_share)
            if _OPT_TIMING:
                import time as _t
                _t0 = _t.perf_counter()
                out = _evaluate(get_enriched, strategy, params, instrument, costs, pretrade,
                                trade_window_start, trade_window_end)
                _TIMING["backtest_s"] += _t.perf_counter() - _t0
                _TIMING["backtest_n"] += 1
                return out
            return _evaluate(get_enriched, strategy, params, instrument, costs, pretrade,
                             trade_window_start, trade_window_end)

        def obj(metrics: Dict[str, Any]) -> float:
            return _objective_value(
                metrics, objective, lot_size=lot_size,
                min_trades=min_trades, min_direction_share=min_direction_share,
            )

        space = _build_param_space(
            strategy.parameter_schema, param_overrides,
            include_indicator_periods=optimize_indicator_periods,
        )
        if resume:
            # Rehydrate prior progress and continue from the last saved stage.
            rdoc = await get_db().optimization_jobs.find_one(
                {"id": job_id}, {"trial_log": 1, "best_so_far": 1, "n_trials_completed": 1}
            ) or {}
            trial_history = list(rdoc.get("trial_log") or [])
            completed = int(rdoc.get("n_trials_completed") or len(trial_history))
            bsf = rdoc.get("best_so_far") or {}
            best_so_far = {
                "value": bsf.get("value") if bsf.get("value") is not None else -float("inf"),
                "params": bsf.get("params") or {},
                "metrics": bsf.get("metrics") or {},
                "trial_num": bsf.get("trial_num", -1),
            }
            anchor_value = best_so_far["value"]
            last_improve_trial = best_so_far["trial_num"] if best_so_far["trial_num"] >= 0 else 0
            study = _rebuild_study(method, space, trial_history)
            await _update_job(job_id, {
                "status": "running", "paused": False, "cancelled": False,
                "resumed_at": datetime.now(timezone.utc).isoformat(),
                "param_space": space, "lot_size": lot_size,
            })
            log.info(f"Resuming optimization {job_id} from trial {completed}/{n_trials}")
        else:
            await _update_job(job_id, {
                "status": "running", "n_trials_total": n_trials,
                "param_space": space, "started_at": datetime.now(timezone.utc).isoformat(),
                "lot_size": lot_size,
                "guards": {
                    "min_trades": min_trades,
                    "min_direction_share": min_direction_share,
                    "optimize_indicator_periods": optimize_indicator_periods,
                },
            })
            # premium_momentum is pinned sequential: the parallel path scores
            # trials via parallel_backtest in worker processes (spot stub ->
            # trade_count=0 -> every trial disqualified), bypassing the
            # premium-native evaluate closure entirely.
            _fresh_workers = (effective_workers(opt_workers)
                              if method == "bayesian" and strategy.id != "premium_momentum" else 1)
            _sampler = (optuna.samplers.TPESampler(seed=42, n_startup_trials=10, constant_liar=True)
                        if _fresh_workers > 1 else _make_sampler(method))
            study = optuna.create_study(
                direction="maximize", sampler=_sampler,
                study_name=f"alphaforge_{job_id}",
            )
            trial_history = []
            best_so_far = {"value": -float("inf"), "params": {}, "metrics": {}, "trial_num": -1}
            completed = 0
            anchor_value = best_so_far["value"]
            last_improve_trial = best_so_far["trial_num"] if best_so_far["trial_num"] >= 0 else 0

        # Convergence early-stop flag — visible after the loop. Both branches share this scope.
        early_stopped = False

        # Opt-in multi-core: bayesian-only; 1 = sequential (unchanged byte-identical path).
        # premium_momentum is pinned sequential — parallel_backtest's worker
        # processes run the spot stub (see the preload block above).
        _workers = (effective_workers(opt_workers)
                    if method == "bayesian" and strategy.id != "premium_momentum" else 1)

        async def _maybe_pause() -> bool:
            """Persist progress and mark paused if the user paused the job.
            Returns True when the worker should stop (caller returns)."""
            await _flush_trial_log(job_id, trial_history, best_so_far, completed)
            await _update_job(job_id, {
                "status": "paused", "paused": False,
                "paused_at": datetime.now(timezone.utc).isoformat(),
            })
            log.info(f"Optimization {job_id} paused at trial {completed}/{n_trials}")
            return True

        if method == "grid":
            combos = _grid_combinations(space, n_trials)
            for params in combos[completed:]:
                cf, pf = await _job_control(job_id)
                if cf:
                    log.info(f"Job {job_id} cancelled by user at trial {completed}/{len(combos)}")
                    break
                if pf and await _maybe_pause():
                    return
                # O14: a single raising combo must NOT crash the whole job (resume
                # then deterministically re-hits the same combo forever). Mirror the
                # bayesian study.optimize(catch=Exception): disqualify + continue.
                try:
                    metrics, merged = await asyncio.to_thread(evaluate, params)
                    val = obj(metrics)
                except Exception as exc:
                    log.warning("grid trial %d raised (%s) — disqualified, continuing",
                                completed, exc)
                    trial_history.append({"params": params, "metrics": None,
                                          "objective_value": None, "error": str(exc)[:200]})
                    completed += 1
                    continue
                trial_history.append({"params": params, "metrics": metrics, "objective_value": round(val, 4)})
                if val > best_so_far["value"]:
                    best_so_far = {"value": val, "params": dict(params), "metrics": metrics, "trial_num": completed}
                completed += 1
                if early_stop:
                    if is_significant_improvement(best_so_far["value"], anchor_value, es_min_delta):
                        anchor_value = best_so_far["value"]
                        last_improve_trial = completed
                    if should_early_stop(completed=completed, last_improve_trial=last_improve_trial,
                                         warmup=es_warmup, patience=es_patience):
                        early_stopped = True
                        break
                if completed % 5 == 0:
                    await _update_job(job_id, {
                        "n_trials_completed": completed,
                        "best_so_far": {"value": round(best_so_far["value"], 4), "params": best_so_far["params"], "metrics": best_so_far["metrics"], "trial_num": best_so_far["trial_num"]},
                    })
                if completed % 50 == 0:
                    await _flush_trial_log(job_id, trial_history, best_so_far, completed)
        elif _workers <= 1:
            # SEQUENTIAL (workers==1) — UNCHANGED. Do NOT refactor to ask/tell. (spec §4 byte-identical)
            def objective_fn(trial: optuna.Trial) -> float:
                params = _suggest(trial, space)
                metrics, merged = evaluate(params)
                val = obj(metrics)
                trial_history.append({"params": params, "metrics": metrics, "objective_value": round(val, 4)})
                return val

            for i in range(completed, n_trials):
                cf, pf = await _job_control(job_id)
                if cf:
                    log.info(f"Job {job_id} cancelled by user at trial {completed}/{n_trials}")
                    break
                if pf and await _maybe_pause():
                    return
                await asyncio.to_thread(study.optimize, objective_fn, n_trials=1, catch=(Exception,))
                completed += 1
                # study.best_value raises if no trial has completed successfully
                # (e.g. every trial errored) — guard so the job doesn't crash.
                try:
                    study_best_val = study.best_value
                    study_best_params = dict(study.best_params)
                except Exception:
                    study_best_val = None
                    study_best_params = {}
                if study_best_val is not None and study_best_val > best_so_far["value"]:
                    best_so_far = {
                        "value": study_best_val, "params": study_best_params,
                        "metrics": trial_history[-1]["metrics"] if trial_history else {},
                        "trial_num": completed - 1,
                    }
                if early_stop:
                    if is_significant_improvement(best_so_far["value"], anchor_value, es_min_delta):
                        anchor_value = best_so_far["value"]
                        last_improve_trial = completed
                    if should_early_stop(completed=completed, last_improve_trial=last_improve_trial,
                                         warmup=es_warmup, patience=es_patience):
                        early_stopped = True
                        break
                if completed % 5 == 0 or completed == n_trials:
                    await _update_job(job_id, {
                        "n_trials_completed": completed,
                        "best_so_far": {"value": round(best_so_far["value"], 4), "params": best_so_far["params"], "metrics": best_so_far["metrics"], "trial_num": best_so_far["trial_num"]},
                    })
                if completed % 50 == 0:
                    await _flush_trial_log(job_id, trial_history, best_so_far, completed)
        else:
            # PARALLEL (workers>1) — opt-in batched ask/tell; non-deterministic (spec §4/§8).
            pool = start_pool(raw_df, _workers)   # None -> concurrent parallel job active -> sequential in-process
            try:
                prior = completed
                while completed < n_trials:
                    cf, pf = await _job_control(job_id)
                    if cf:
                        log.info(f"Job {job_id} cancelled by user at trial {completed}/{n_trials}")
                        break
                    if pf and await _maybe_pause():
                        return
                    B = min(_workers, n_trials - completed)
                    trials = [study.ask() for _ in range(B)]
                    param_list = [_suggest(t, space) for t in trials]
                    param_sets = [(strategy.id, strategy.merged_params(p), None) for p in param_list]
                    results = await asyncio.to_thread(
                        parallel_backtest, pool, param_sets,
                        raw_df=raw_df, instrument=instrument, costs=costs, pretrade=pretrade,
                        trade_window_start=trade_window_start, trade_window_end=trade_window_end)
                    # Atomic flush: tell+append ALL in ask-order, THEN best, THEN checkpoint.
                    for trial, params, (metrics, _m) in zip(trials, param_list, results):
                        if metrics is None:
                            study.tell(trial, None, state=optuna.trial.TrialState.FAIL)
                        else:
                            val = obj(metrics)
                            study.tell(trial, val)
                            trial_history.append({"params": params, "metrics": metrics, "objective_value": round(val, 4)})
                    completed += B
                    try:
                        study_best_val = study.best_value
                        study_best_params = dict(study.best_params)
                    except Exception:
                        study_best_val = None
                        study_best_params = {}
                    if study_best_val is not None and study_best_val > best_so_far["value"]:
                        best_metrics = next((t["metrics"] for t in reversed(trial_history)
                                             if t["params"] == study_best_params), best_so_far["metrics"])
                        best_so_far = {"value": study_best_val, "params": study_best_params,
                                       "metrics": best_metrics, "trial_num": completed - 1}
                    if early_stop:
                        if is_significant_improvement(best_so_far["value"], anchor_value, es_min_delta):
                            anchor_value = best_so_far["value"]
                            last_improve_trial = completed
                        if should_early_stop(completed=completed, last_improve_trial=last_improve_trial,
                                             warmup=es_warmup, patience=es_patience):
                            early_stopped = True
                            break
                    if (completed // 5) > (prior // 5) or completed >= n_trials:
                        await _update_job(job_id, {
                            "n_trials_completed": completed,
                            "best_so_far": {"value": round(best_so_far["value"], 4), "params": best_so_far["params"], "metrics": best_so_far["metrics"], "trial_num": best_so_far["trial_num"]},
                        })
                    if (completed // 50) > (prior // 50):
                        await _flush_trial_log(job_id, trial_history, best_so_far, completed)
                    prior = completed
            finally:
                shutdown_pool()

        # Final analyses. If the user cancelled, finalize FAST: skip the
        # expensive heatmap + robustness passes (each runs dozens of extra
        # backtests), which otherwise leave the job sitting in "analyzing" for
        # a long time after Stop. Best-so-far + cheap importance are kept.
        cancelled_flag = await _is_cancelled(job_id)
        await _update_job(job_id, {"status": "analyzing", "n_trials_completed": completed,
                                   "early_stopped": early_stopped, "stopped_at_trial": completed,
                                   "trials_ceiling": n_trials})

        # Analyzing-stage governance: a wall-clock budget + live per-candidate
        # progress/ETA + graceful partial results. INVARIANT: when the budget is
        # 0 (or never hit) the only added behaviour is `rerank_progress` writes —
        # no `break` fires, so the ranking/survival results are byte-identical.
        analyze_budget_sec = int(payload.get("analyze_budget_sec", 1800) or 0)
        _an_t0 = time.monotonic()
        analyze_budget_hit = False
        analyzed_candidates = None
        _last_progress = [0.0]

        async def _analyze_should_stop() -> bool:
            """O13: analyze-stage stop signal — over-budget OR a user cancel/pause
            that landed AFTER the trial loop (the single cancel read at the top of
            the analyze stage misses those). On stop the caller breaks and the job
            finalizes with PARTIAL results (best-so-far + whatever ranked/survived).
            Byte-identical when nobody stops and the budget is 0/unhit."""
            nonlocal analyze_budget_hit
            if over_budget(elapsed=time.monotonic() - _an_t0, budget_sec=analyze_budget_sec):
                analyze_budget_hit = True
                return True
            try:
                cf, pf = await _job_control(job_id)
            except Exception:
                return False
            return bool(cf or pf)

        async def _an_progress(stage, done, total, per_item):
            now = time.monotonic()
            if now - _last_progress[0] < 1.0 and done < total:   # throttle ~1/sec
                return
            _last_progress[0] = now
            upd = {"rerank_progress": {
                "stage": stage, "done": done, "total": total,
                "elapsed_sec": round(now - _an_t0, 1),
                "per_item_sec": (round(per_item, 2) if per_item else None),
                "eta_sec": eta_seconds(done=done, total=total, per_item_sec=per_item)}}
            await _update_job(job_id, upd)

        # Top-N alternatives
        sorted_trials = sorted(trial_history, key=lambda t: t["objective_value"], reverse=True)
        top_n = sorted_trials[:10]

        # Param importance (only for Bayesian/Genetic)
        importance = []
        if method != "grid":
            try:
                importance = _param_importance(study, space)
            except Exception:
                pass
        # Fallback importance from grid: variance per axis
        if not importance and len(trial_history) > 5:
            try:
                axis_var = {}
                for name in space:
                    vals = [(t["params"].get(name), t["objective_value"]) for t in trial_history if name in t["params"]]
                    if not vals:
                        continue
                    # Bin by param value to compute response variance
                    by_v: Dict[Any, List[float]] = {}
                    for k, v in vals:
                        by_v.setdefault(k, []).append(v)
                    if len(by_v) >= 2:
                        means = [np.mean(vv) for vv in by_v.values()]
                        axis_var[name] = float(np.std(means))
                total = sum(axis_var.values()) or 1.0
                importance = [{"param": k, "importance": round(v / total, 4)} for k, v in sorted(axis_var.items(), key=lambda kv: -kv[1])]
            except Exception:
                pass

        # Two-stage option re-rank: re-score the top-K spot candidates on REAL
        # paired-option net rupee and pick the option-best as the final best.
        rerank_info = None
        spot_option_corr = None
        survival_summary = None  # defined for all paths (spot mode never enters the block below)
        if evaluation_mode == "option_rerank" and not cancelled_flag and sorted_trials:
            candidates = select_rerank_candidates(
                sorted_trials, top_k=rerank_top_k, diversity=rerank_diversity)
            ranked: List[Dict[str, Any]] = []
            rerank_contracts: List[Dict[str, Any]] = []
            rerank_candles = pd.DataFrame()
            if candidates:
                await _update_job(job_id, {"rerank_progress": {"stage": "option_rerank", "candidates": len(candidates)}})
                try:
                    ranked, rerank_contracts, rerank_candles, _rr_hit = await _option_rerank(
                        get_db(), strategy, get_enriched, candidates,
                        instrument, costs, pretrade, option_cfg,
                        analyze_t0=_an_t0, analyze_budget_sec=analyze_budget_sec,
                        progress_cb=_an_progress)
                    analyze_budget_hit = analyze_budget_hit or _rr_hit
                except Exception as e:
                    log.warning(f"option re-rank failed: {e}")
                    ranked = []
            if survival.enabled and ranked:
                # Survival gate: evaluate each finalist's per-fold OOS rupee survival,
                # keep PROFITABLE survivors, rank by the chosen objective. Reuses the
                # contracts + candles already loaded by _option_rerank.
                await _update_job(job_id, {"rerank_progress": {"stage": "survival", "candidates": len(ranked)}})
                # Pre-group the shared option-candle frame ONCE (up to ~150s of
                # per-sim copy+sort+groupby otherwise: K finalists x folds, plus
                # the exit-control grid). Byte-identical to each sim rebuilding it.
                rerank_by_key = build_candles_by_key(rerank_candles)
                _per_item_surv: Optional[float] = None
                for i, r in enumerate(ranked):
                    _s_t = time.monotonic()
                    try:
                        merged = strategy.merged_params(r["params"])
                        df_enr = get_enriched(merged)
                        r["survival"] = await _survival_eval_oos(
                            strategy, df_enr, merged, rerank_contracts, rerank_candles,
                            instrument, costs, pretrade, option_cfg, survival,
                            candles_by_key=rerank_by_key,
                            trade_window_start=trade_window_start, trade_window_end=trade_window_end)
                    except Exception as e:
                        log.warning(f"survival eval failed: {e}")
                        r["survival"] = {"survived": False, "reason": "eval_error"}
                    _per_item_surv = ewma(_per_item_surv, time.monotonic() - _s_t)
                    if (i + 1) % 10 == 0:
                        log.info("rerank %d/%d", i + 1, len(ranked))
                    await _an_progress("survival", i + 1, len(ranked), _per_item_surv)
                    if await _analyze_should_stop():  # O13: budget OR cancel/pause
                        break
                survivors = [r for r in ranked if r.get("survival", {}).get("survived")
                             and (r["survival"].get("total_return_pct") or 0) > 0]
                if payload.get("search_exit_controls"):
                    from app.exit_controls import exit_control_grid
                    grid = exit_control_grid(option_cfg.get("exit_control_search"))
                    for r in survivors:
                        if await _analyze_should_stop():  # O13: grid was ungoverned
                            break
                        try:
                            merged = strategy.merged_params(r["params"])
                            df_enr = get_enriched(merged)
                            for gc in grid:
                                v = await _survival_eval_oos(
                                    strategy, df_enr, merged, rerank_contracts, rerank_candles,
                                    instrument, costs, pretrade, {**option_cfg, "exit_controls": gc}, survival,
                                    candles_by_key=rerank_by_key,
                                    trade_window_start=trade_window_start, trade_window_end=trade_window_end)
                                better = (v.get("calmar") or -1e9) > (r["survival"].get("calmar") or -1e9)
                                if v.get("survived") and (v.get("total_return_pct") or 0) > 0 and better:
                                    r["survival"] = v
                                    r["chosen_exit_controls"] = gc
                        except Exception as e:
                            log.warning(f"exit-control search failed for a finalist: {e}")
                if survival.objective == "calmar":
                    survivors.sort(key=lambda r: (r["survival"].get("calmar") or -1e9, r["option_pnl_value"]), reverse=True)
                else:
                    survivors.sort(key=lambda r: (r["option_pnl_value"], r["survival"].get("calmar") or -1e9), reverse=True)
                if survivors:
                    best = survivors[0]
                    best_so_far = {
                        "value": (best["survival"].get("calmar") if survival.objective == "calmar"
                                  else best["option_pnl_value"]),
                        "params": best["params"],
                        "metrics": {
                            **(best.get("spot_metrics") or {}),
                            "option_pnl_value": best["option_pnl_value"],
                            "option_pnl_pts": best["option_pnl_pts"],
                            "option_win_rate": best["option_win_rate"],
                            "paired_trade_count": best["paired_trade_count"],
                            "survival": best["survival"],
                        },
                        "trial_num": -1,
                    }
                    best_so_far["exit_controls"] = best.get("chosen_exit_controls") or option_cfg.get("exit_controls")
                    best_so_far["daily_caps"] = option_cfg.get("daily_caps")
                    survival_summary = {"survivors": len(survivors), "evaluated": len(ranked),
                                        "objective": survival.objective,
                                        # O2: record the capital the gate actually
                                        # scaled DD%/RoR against (defaults 200k) so
                                        # the UI shows the basis instead of an unseen
                                        # phantom account.
                                        "capital": float((option_cfg.get("sizing_config") or {}).get("capital", 200_000) or 200_000)}
                else:
                    # Zero survivors: do NOT promote a disqualified candidate as "best".
                    reasons: Dict[str, int] = {}
                    for r in ranked:
                        rs = r.get("survival", {}).get("reason", "unknown")
                        reasons[rs] = reasons.get(rs, 0) + 1
                    best_so_far = {"value": -1e9, "params": {}, "metrics": {}, "trial_num": -1}
                    survival_summary = {
                        "survivors": 0, "evaluated": len(ranked), "reason_counts": reasons,
                        "capital": float((option_cfg.get("sizing_config") or {}).get("capital", 200_000) or 200_000),
                        "suggestions": ["loosen max_drawdown_pct or max_ror_pct",
                                        "widen parameter bounds / increase rerank_top_k",
                                        "extend the date range for more OOS trades"]}
            elif ranked and ranked[0]["paired_trade_count"] > 0:
                best = ranked[0]
                best_so_far = {
                    "value": best["option_pnl_value"],
                    "params": best["params"],
                    "metrics": {
                        **(best.get("spot_metrics") or {}),
                        "option_pnl_value": best["option_pnl_value"],
                        "option_pnl_pts": best["option_pnl_pts"],
                        "option_win_rate": best["option_win_rate"],
                        "paired_trade_count": best["paired_trade_count"],
                    },
                    "trial_num": -1,
                }
                best_so_far["exit_controls"] = option_cfg.get("exit_controls")
                best_so_far["daily_caps"] = option_cfg.get("daily_caps")
            spot_option_corr = compute_spot_option_correlation(ranked)
            analyzed_candidates = f"{len(ranked)}"
            rerank_info = {
                "top_k": rerank_top_k,
                "diversity": rerank_diversity,
                "candidates": len(candidates),
                "evaluated": len(ranked),
                "option_config": option_cfg,
                "ranked": ranked[:50],
                "survival_summary": survival_summary,
                "spot_option_correlation": spot_option_corr,
            }

        # Heatmap + robustness — spot-objective analyses; skipped on cancellation
        # and in option re-rank mode (the re-rank table is the relevant analysis).
        # Also skipped once the analyzing budget is spent (the cheap-but-not-free
        # tail): partial results are still finalized below.
        heatmap = None
        robustness = None
        # O13: a cancel that landed DURING the analyze stage (rerank/survival) was
        # silently discarded — the cancel flag was read once before analysis. Refresh
        # it so the spot-mode heatmap/robustness tail is skipped on a mid-analyze stop.
        if not cancelled_flag:
            cancelled_flag = await _is_cancelled(job_id)
        if not cancelled_flag and evaluation_mode == "spot" and not over_budget(
                elapsed=time.monotonic() - _an_t0, budget_sec=analyze_budget_sec):
            try:
                heatmap = await asyncio.to_thread(_heatmap, evaluate, obj, best_so_far["params"], importance, space)
            except Exception as e:
                log.warning(f"heatmap failed: {e}")
            try:
                robustness = await asyncio.to_thread(_robustness_score, evaluate, obj, best_so_far["params"], space)
            except Exception as e:
                log.warning(f"robustness failed: {e}")
        elif over_budget(elapsed=time.monotonic() - _an_t0, budget_sec=analyze_budget_sec):
            analyze_budget_hit = True

        # Persist final best as a full backtest_run with trades + equity + walkforward.
        # Re-enrich for the BEST indicator periods so the saved run matches what
        # was optimized (not the default-period indicators).
        best_backtest_run_id = None
        if best_so_far["params"]:
            best_merged = strategy.merged_params(best_so_far["params"])
            df_best = get_enriched(best_merged)
            best_backtest_run_id = await _save_best_as_backtest(
                job_id, payload, strategy, df_best, best_so_far["params"],
                instrument, costs, pretrade, run_walkforward=not cancelled_flag,
                option_config={**(option_cfg or {}),
                               "exit_controls": best_so_far.get("exit_controls"),
                               "daily_caps": best_so_far.get("daily_caps")} if evaluation_mode == "option_rerank" else None,
                n_trials=n_trials,
            )

        # Determine final status — cancelled if user cancelled before completion;
        # distinct done_no_survivor when survival mode found nothing deployable.
        cancelled_flag = await _is_cancelled(job_id)
        if survival.enabled and survival_summary is not None and survival_summary.get("survivors") == 0:
            final_status = "done_no_survivor"
        else:
            final_status = "cancelled" if cancelled_flag and completed < n_trials else "done"

        finished = {
            "status": final_status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "n_trials_completed": completed,
            "evaluation_mode": evaluation_mode,
            "best_params": best_so_far["params"],
            "best_value": round(best_so_far["value"], 4) if best_so_far["value"] > -1e8 else None,
            "best_metrics": best_so_far["metrics"],
            "best_backtest_run_id": best_backtest_run_id,
            "top_n_alternatives": [{"params": t["params"], "metrics": t["metrics"], "objective_value": t["objective_value"]} for t in top_n],
            "parameter_importance": importance,
            "heatmap": heatmap,
            "robustness": robustness,
            "rerank": rerank_info,
            "survival_summary": survival_summary,
            "best_exit_controls": best_so_far.get("exit_controls"),
            "best_daily_caps": best_so_far.get("daily_caps"),
            "analyze_budget_hit": analyze_budget_hit,
            "analyzed_candidates": analyzed_candidates,
            "trial_log": [],
            "timing": ({
                "precompute_s": round(_TIMING["precompute_s"], 3),
                "precompute_n": _TIMING["precompute_n"],
                "evaluate_s": round(_TIMING["backtest_s"], 3),
                "evaluate_n": _TIMING["backtest_n"],
                "bar_loop_s": round(_TIMING["backtest_s"] - _TIMING["precompute_s"], 3),
            } if _OPT_TIMING else None),
        }
        # Fix-A/Fix-C: read the promoted full-window option net (for Fix-D's deploy gate)
        # and compute the trust verdict, both off the already-saved best run. best_run is
        # None for done_no_survivor / save-failure -> both keys simply omitted (no crash).
        best_run = best_backtest_run_id and await get_db().backtest_runs.find_one({"id": best_backtest_run_id}, {"_id": 0})
        if best_run:
            from app.deployment_quality import evaluate_source_quality
            finished["best_option_pnl_value"] = ((best_run.get("option_backtest") or {}).get("portfolio") or {}).get("net_pnl_value")
            # O4: the survival folds are IN-SAMPLE w.r.t. finalist selection (finalists
            # are filtered on them and the exit grid is tuned against them), so
            # survival.total_return_pct is NOT a clean out-of-sample signal. Do NOT
            # feed it as oos_return_pct — that would let the deploy-quality verdict
            # claim "positive out-of-sample" for a number the search already fit.
            # Carry it separately as stress_return_pct (advisory). True OOS is WFO-only.
            _stress = (best_so_far.get("metrics") or {}).get("survival", {}).get("total_return_pct")
            finished["best_quality"] = evaluate_source_quality(
                best_run,
                evidence={"oos_return_pct": None, "stress_return_pct": _stress,
                          "n_trials": n_trials, "spot_option_correlation": spot_option_corr})
        await _update_job(job_id, finished)
        log.info(f"Optimization {job_id} {final_status}: best={best_so_far['value']:.4f} run_id={best_backtest_run_id}")
    except Exception as e:
        log.exception(f"optimization {job_id} crashed")
        await _update_job(job_id, {"status": "failed", "error": str(e), "finished_at": datetime.now(timezone.utc).isoformat()})


def _make_sampler(method: str):
    if method == "genetic":
        return optuna.samplers.CmaEsSampler(seed=42, n_startup_trials=8)
    if method == "bayesian":
        return optuna.samplers.TPESampler(seed=42, n_startup_trials=10)
    # grid handled separately
    return optuna.samplers.TPESampler(seed=42)


async def create_job(payload: Dict[str, Any]) -> str:
    job_id = str(uuid.uuid4())
    db = get_db()
    doc = {
        "id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "queued",
        "instrument": payload.get("instrument", "NIFTY"),
        "strategy_id": payload.get("strategy_id"),
        "method": payload.get("method", "bayesian"),
        "objective": payload.get("objective", "risk_adjusted"),
        "n_trials_total": int(payload.get("n_trials", 200)),
        "n_trials_completed": 0,
        "config": payload,
        "best_so_far": None,
    }
    await db.optimization_jobs.insert_one(doc)
    # Fire-and-forget
    asyncio.create_task(run_optimization(job_id, payload))
    return job_id


async def resume_optimization(job_id: str) -> bool:
    """Re-launch the worker for a paused / interrupted / failed job, continuing
    from its last persisted stage. Returns False if the job can't be resumed."""
    db = get_db()
    doc = await db.optimization_jobs.find_one({"id": job_id}, {"_id": 0})
    if not doc:
        return False
    if doc.get("status") not in ("paused", "interrupted", "failed"):
        return False
    payload = doc.get("config") or {}
    if not payload.get("strategy_id"):
        return False
    await db.optimization_jobs.update_one(
        {"id": job_id},
        {"$set": {"status": "running", "paused": False, "cancelled": False, "error": None}},
    )
    asyncio.create_task(run_optimization(job_id, payload, resume=True))
    return True
