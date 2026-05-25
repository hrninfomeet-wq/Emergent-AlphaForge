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

log = logging.getLogger(__name__)
optuna.logging.set_verbosity(optuna.logging.WARNING)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _objective_value(metrics: Dict[str, Any], objective: str) -> float:
    if metrics.get("trade_count", 0) == 0:
        return -1e9  # heavy penalty for no trades
    if objective == "sharpe":
        v = metrics.get("sharpe")
        return float(v) if v is not None else -1e9
    if objective == "profit_factor":
        v = metrics.get("profit_factor")
        return float(v) if v is not None else 0.0
    if objective == "total_pnl_pts":
        return float(metrics.get("total_pnl_pts", 0) or 0)
    if objective == "win_rate":
        return float(metrics.get("win_rate", 0) or 0)
    if objective == "neg_max_dd":
        return -abs(float(metrics.get("max_dd_pts", 0) or 0))
    # risk_adjusted (default)
    sharpe = float(metrics.get("sharpe") or 0)
    dd = abs(float(metrics.get("max_dd_pts") or 1))
    return sharpe / max(1.0, dd / 100.0)


def _build_param_space(parameter_schema: Dict[str, Any], overrides: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    """Return space {name: {type, min, max, step?, default, fixed?}} after applying user overrides.
    overrides[name] can be {min, max, fixed} to widen/narrow/lock a param."""
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


def _evaluate(df_enriched: pd.DataFrame, strategy, params: Dict[str, Any], instrument: str, costs: bool, pretrade: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Run one backtest with given params and return (metrics, merged_params). Pure sync."""
    merged = strategy.merged_params(params)
    res = run_backtest(df_enriched, strategy, merged, instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade)
    return res["metrics"], merged


def _robustness_score(df_enriched, strategy, instrument, costs, pretrade, best_params: Dict[str, Any], space: Dict[str, Dict[str, Any]], objective: str) -> Dict[str, Any]:
    """Perturb each numeric param by ±10% and ±20%; count fraction that stay 'profitable'.
    Returns {score_0_100, perturbation_results}.
    """
    base_metrics, _ = _evaluate(df_enriched, strategy, best_params, instrument, costs, pretrade)
    base_val = _objective_value(base_metrics, objective)
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
            metrics, _ = _evaluate(df_enriched, strategy, test_params, instrument, costs, pretrade)
            val = _objective_value(metrics, objective)
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


def _heatmap(df_enriched, strategy, instrument, costs, pretrade, best_params, importance, space, objective, grid_n=8) -> Optional[Dict[str, Any]]:
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
            metrics, _ = _evaluate(df_enriched, strategy, test, instrument, costs, pretrade)
            row.append({
                "val": round(_objective_value(metrics, objective), 3),
                "trades": int(metrics.get("trade_count", 0)),
            })
        grid.append(row)
    return {
        "param_a": pa, "param_b": pb,
        "a_values": [round(float(x), 3) for x in a_vals],
        "b_values": [round(float(x), 3) for x in b_vals],
        "grid": grid,
    }


async def run_optimization(job_id: str, payload: Dict[str, Any]) -> None:
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

        strategy = get_registry().get(strategy_id)
        if not strategy:
            await _update_job(job_id, {"status": "failed", "error": f"Strategy {strategy_id} not found", "finished_at": datetime.now(timezone.utc).isoformat()})
            return

        df = await load_candles_df(instrument, start_ts, end_ts)
        if df.empty or len(df) < 100:
            await _update_job(job_id, {"status": "failed", "error": f"Insufficient candles for {instrument} ({len(df)})", "finished_at": datetime.now(timezone.utc).isoformat()})
            return

        # Pre-compute indicators ONCE (massive speedup)
        df_enriched = precompute_all_indicators(df, strategy.merged_params({}))
        df_enriched["regime"] = classify_regime_series(df_enriched)

        space = _build_param_space(strategy.parameter_schema, param_overrides)
        await _update_job(job_id, {
            "status": "running", "n_trials_total": n_trials,
            "param_space": space, "started_at": datetime.now(timezone.utc).isoformat(),
        })

        # Build / configure study
        sampler = _make_sampler(method)
        study = optuna.create_study(
            direction="maximize",
            sampler=sampler,
            study_name=f"alphaforge_{job_id}",
        )

        trial_history: List[Dict[str, Any]] = []
        best_so_far = {"value": -float("inf"), "params": {}, "metrics": {}, "trial_num": -1}
        completed = 0

        if method == "grid":
            combos = _grid_combinations(space, n_trials)
            for params in combos:
                metrics, merged = _evaluate(df_enriched, strategy, params, instrument, costs, pretrade)
                val = _objective_value(metrics, objective)
                trial_history.append({"params": params, "metrics": metrics, "objective_value": round(val, 4)})
                if val > best_so_far["value"]:
                    best_so_far = {"value": val, "params": dict(params), "metrics": metrics, "trial_num": completed}
                completed += 1
                if completed % 5 == 0:
                    await _update_job(job_id, {
                        "n_trials_completed": completed,
                        "best_so_far": {"value": round(best_so_far["value"], 4), "params": best_so_far["params"], "metrics": best_so_far["metrics"], "trial_num": best_so_far["trial_num"]},
                    })
        else:
            def objective_fn(trial: optuna.Trial) -> float:
                params = _suggest(trial, space)
                metrics, merged = _evaluate(df_enriched, strategy, params, instrument, costs, pretrade)
                val = _objective_value(metrics, objective)
                trial_history.append({"params": params, "metrics": metrics, "objective_value": round(val, 4)})
                return val

            for i in range(n_trials):
                study.optimize(objective_fn, n_trials=1, catch=(Exception,))
                completed += 1
                if study.best_value > best_so_far["value"]:
                    best_so_far = {
                        "value": study.best_value, "params": dict(study.best_params),
                        "metrics": trial_history[-1]["metrics"] if trial_history else {},
                        "trial_num": completed - 1,
                    }
                if completed % 5 == 0 or completed == n_trials:
                    await _update_job(job_id, {
                        "n_trials_completed": completed,
                        "best_so_far": {"value": round(best_so_far["value"], 4), "params": best_so_far["params"], "metrics": best_so_far["metrics"], "trial_num": best_so_far["trial_num"]},
                    })

        # Final analyses
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

        # Heatmap on top-2 important params
        heatmap = None
        try:
            heatmap = _heatmap(df_enriched, strategy, instrument, costs, pretrade, best_so_far["params"], importance, space, objective)
        except Exception as e:
            log.warning(f"heatmap failed: {e}")

        # Robustness on best params
        robustness = None
        try:
            robustness = _robustness_score(df_enriched, strategy, instrument, costs, pretrade, best_so_far["params"], space, objective)
        except Exception as e:
            log.warning(f"robustness failed: {e}")

        finished = {
            "status": "done",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "n_trials_completed": completed,
            "best_params": best_so_far["params"],
            "best_value": round(best_so_far["value"], 4),
            "best_metrics": best_so_far["metrics"],
            "top_n_alternatives": [{"params": t["params"], "metrics": t["metrics"], "objective_value": t["objective_value"]} for t in top_n],
            "parameter_importance": importance,
            "heatmap": heatmap,
            "robustness": robustness,
        }
        await _update_job(job_id, finished)
        log.info(f"Optimization {job_id} done: best={best_so_far['value']:.4f}")
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
