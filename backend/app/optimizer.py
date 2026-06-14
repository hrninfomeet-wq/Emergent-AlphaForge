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
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import optuna
import pandas as pd

from app.backtest import run_backtest
from app.db import get_db
from app.indicators import precompute_all_indicators
from app.regime import classify_regime_series
from app.strategies.base import get_registry
from app.warehouse import load_candles_df
from app.option_backtest import simulate_paired_option_trades
from app.options_universe import select_contract_for_signal
from app.dte import compute_dte, normalize_dte_filter
from app.survival import survival_verdict, SurvivalConfig, oos_fold_index_ranges

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


def _evaluate(get_enriched, strategy, params: Dict[str, Any], instrument: str, costs: bool, pretrade: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Run one backtest with given params and return (metrics, merged_params).

    `get_enriched(merged)` returns the indicator+regime enriched dataframe for
    the merged params (recomputing when indicator periods change). Direction
    counts (ce/pe) are folded into the metrics so the guard rails and UI can
    detect one-sided solutions."""
    merged = strategy.merged_params(params)
    df_enriched = get_enriched(merged)
    res = run_backtest(df_enriched, strategy, merged, instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade)
    metrics = dict(res["metrics"])
    trades = res.get("trades", []) or []
    ce = sum(1 for t in trades if str(t.get("direction", "")).upper() == "CE")
    metrics["ce_count"] = int(ce)
    metrics["pe_count"] = int(len(trades) - ce)
    return metrics, merged


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


async def _save_best_as_backtest(job_id: str, payload: Dict[str, Any], strategy, df_enriched: pd.DataFrame, best_params: Dict[str, Any], instrument: str, costs_enabled: bool, pretrade: Dict[str, Any], run_walkforward: bool = True, option_config: Optional[Dict[str, Any]] = None) -> Optional[str]:
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


async def _survival_eval_oos(
    strategy, df_enriched, merged_params, contracts, candles_df,
    instrument, costs, pretrade, option_cfg, sc, n_folds=3, train_pct=0.6,
):
    """Evaluate one finalist's survival on each walk-forward OOS slice. Floor + DD%
    must hold per fold (per sc.min_oos_folds); RoR runs on the stitched OOS rupee
    series. Returns the survival_verdict dict augmented with folds_ok/fold_pass."""
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
    spot_total = paired_total = 0
    for _fold, a, b in oos_fold_index_ranges(len(df_enriched), n_folds, train_pct):
        test_df = df_enriched.iloc[a:b].reset_index(drop=True)
        res = await asyncio.to_thread(
            run_backtest, test_df, strategy, merged_params,
            instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade)
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
        )
        port = sim.get("portfolio") or {}
        cov = sim.get("coverage") or {}
        spot_total += int(cov.get("spot_trade_count", 0) or 0)
        paired_total += int(cov.get("paired_trade_count", 0) or 0)
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
        coverage={"spot_trade_count": spot_total, "paired_trade_count": paired_total},
        capital=capital)
    verdict["folds_ok"] = folds_ok
    verdict["fold_pass"] = fold_pass
    verdict["survived"] = bool(verdict["survived"] and folds_ok)
    return verdict


async def _option_rerank(
    db, strategy, get_enriched, candidates: List[Dict[str, Any]],
    instrument: str, costs: bool, pretrade: Dict[str, Any], option_cfg: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Any]:  # (ranked, contracts, candles_df)
    """Stage 2: re-score the top-K spot candidates on REAL paired-option net
    rupee. Option contracts + candles are loaded from the DB ONCE (over the
    union of all candidates' needed strikes), then each candidate is simulated
    in-memory. Returns candidates ranked by option net-rupee P&L."""
    moneyness = str(option_cfg.get("moneyness") or "atm")
    lots = int(option_cfg.get("lots") or 1)
    fixed_expiry = option_cfg.get("expiry_date")
    dte_target = normalize_dte_filter(option_cfg.get("dte_filter"))
    exit_mode = option_cfg.get("exit_mode") or "spot_exit"
    cost_config = option_cfg.get("cost_config")
    sizing_config = option_cfg.get("sizing_config")
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
                  "coverage": {}} for c in candidates], [], pd.DataFrame())

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
    ranked: List[Dict[str, Any]] = []
    for cand, pc in zip(candidates, per_cand):
        sim = await asyncio.to_thread(
            simulate_paired_option_trades,
            spot_trades=pc["trades"], contracts=contracts, option_candles=candles_df,
            underlying=instrument, moneyness=moneyness, lots=lots,
            entry_max_age_sec=entry_max_age, exit_max_age_sec=exit_max_age,
            expiry_by_trade=pc["expiry_by_trade"], fixed_expiry_date=fixed_expiry,
            exit_mode=exit_mode, option_target_pts=opt_tp, option_stop_pts=opt_sp,
            option_target_pct=opt_tpct, option_stop_pct=opt_spct,
            cost_config=cost_config, sizing_config=sizing_config,
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
    # Rank by option net rupee; candidates with no paired trades sink to the bottom.
    ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)
    # Also return the loaded contracts + candle frame so the survival evaluator can
    # reuse the single (multi-million-row) option-candle load instead of re-querying.
    return ranked, contracts, candles_df


async def run_optimization(job_id: str, payload: Dict[str, Any], resume: bool = False) -> None:
    """Main async optimizer worker. Runs in a background task."""
    try:
        instrument = payload["instrument"].upper()
        strategy_id = payload["strategy_id"]
        method = payload.get("method", "bayesian")  # bayesian | grid | genetic
        objective = payload.get("objective", "risk_adjusted")
        n_trials = int(payload.get("n_trials", 200))
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
        enriched_cache: Dict[Tuple, pd.DataFrame] = {}

        def get_enriched(merged: Dict[str, Any]) -> pd.DataFrame:
            key = _indicator_key(merged)
            cached = enriched_cache.get(key)
            if cached is not None:
                return cached
            enr = precompute_all_indicators(raw_df, merged)
            enr["regime"] = classify_regime_series(enr)
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

        # Guard-aware closures used by every trial / analysis below.
        def evaluate(params: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            return _evaluate(get_enriched, strategy, params, instrument, costs, pretrade)

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
            study = optuna.create_study(
                direction="maximize", sampler=_make_sampler(method),
                study_name=f"alphaforge_{job_id}",
            )
            trial_history = []
            best_so_far = {"value": -float("inf"), "params": {}, "metrics": {}, "trial_num": -1}
            completed = 0

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
                metrics, merged = await asyncio.to_thread(evaluate, params)
                val = obj(metrics)
                trial_history.append({"params": params, "metrics": metrics, "objective_value": round(val, 4)})
                if val > best_so_far["value"]:
                    best_so_far = {"value": val, "params": dict(params), "metrics": metrics, "trial_num": completed}
                completed += 1
                if completed % 5 == 0:
                    await _update_job(job_id, {
                        "n_trials_completed": completed,
                        "best_so_far": {"value": round(best_so_far["value"], 4), "params": best_so_far["params"], "metrics": best_so_far["metrics"], "trial_num": best_so_far["trial_num"]},
                    })
                if completed % 50 == 0:
                    await _flush_trial_log(job_id, trial_history, best_so_far, completed)
        else:
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
                if completed % 5 == 0 or completed == n_trials:
                    await _update_job(job_id, {
                        "n_trials_completed": completed,
                        "best_so_far": {"value": round(best_so_far["value"], 4), "params": best_so_far["params"], "metrics": best_so_far["metrics"], "trial_num": best_so_far["trial_num"]},
                    })
                if completed % 50 == 0:
                    await _flush_trial_log(job_id, trial_history, best_so_far, completed)

        # Final analyses. If the user cancelled, finalize FAST: skip the
        # expensive heatmap + robustness passes (each runs dozens of extra
        # backtests), which otherwise leave the job sitting in "analyzing" for
        # a long time after Stop. Best-so-far + cheap importance are kept.
        cancelled_flag = await _is_cancelled(job_id)
        await _update_job(job_id, {"status": "analyzing", "n_trials_completed": completed})

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
                    ranked, rerank_contracts, rerank_candles = await _option_rerank(
                        get_db(), strategy, get_enriched, candidates,
                        instrument, costs, pretrade, option_cfg)
                except Exception as e:
                    log.warning(f"option re-rank failed: {e}")
                    ranked = []
            if survival.enabled and ranked:
                # Survival gate: evaluate each finalist's per-fold OOS rupee survival,
                # keep PROFITABLE survivors, rank by the chosen objective. Reuses the
                # contracts + candles already loaded by _option_rerank.
                await _update_job(job_id, {"rerank_progress": {"stage": "survival", "candidates": len(ranked)}})
                for r in ranked:
                    try:
                        merged = strategy.merged_params(r["params"])
                        df_enr = get_enriched(merged)
                        r["survival"] = await _survival_eval_oos(
                            strategy, df_enr, merged, rerank_contracts, rerank_candles,
                            instrument, costs, pretrade, option_cfg, survival)
                    except Exception as e:
                        log.warning(f"survival eval failed: {e}")
                        r["survival"] = {"survived": False, "reason": "eval_error"}
                survivors = [r for r in ranked if r.get("survival", {}).get("survived")
                             and (r["survival"].get("total_return_pct") or 0) > 0]
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
                    survival_summary = {"survivors": len(survivors), "evaluated": len(ranked),
                                        "objective": survival.objective}
                else:
                    # Zero survivors: do NOT promote a disqualified candidate as "best".
                    reasons: Dict[str, int] = {}
                    for r in ranked:
                        rs = r.get("survival", {}).get("reason", "unknown")
                        reasons[rs] = reasons.get(rs, 0) + 1
                    best_so_far = {"value": -1e9, "params": {}, "metrics": {}, "trial_num": -1}
                    survival_summary = {
                        "survivors": 0, "evaluated": len(ranked), "reason_counts": reasons,
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
            rerank_info = {
                "top_k": rerank_top_k,
                "diversity": rerank_diversity,
                "candidates": len(candidates),
                "evaluated": len(ranked),
                "option_config": option_cfg,
                "ranked": ranked[:50],
                "survival_summary": survival_summary,
            }

        # Heatmap + robustness — spot-objective analyses; skipped on cancellation
        # and in option re-rank mode (the re-rank table is the relevant analysis).
        heatmap = None
        robustness = None
        if not cancelled_flag and evaluation_mode == "spot":
            try:
                heatmap = await asyncio.to_thread(_heatmap, evaluate, obj, best_so_far["params"], importance, space)
            except Exception as e:
                log.warning(f"heatmap failed: {e}")
            try:
                robustness = await asyncio.to_thread(_robustness_score, evaluate, obj, best_so_far["params"], space)
            except Exception as e:
                log.warning(f"robustness failed: {e}")

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
                option_config=(option_cfg if evaluation_mode == "option_rerank" else None),
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
            "trial_log": [],
        }
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
