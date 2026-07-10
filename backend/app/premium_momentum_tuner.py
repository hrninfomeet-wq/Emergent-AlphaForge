# backend/app/premium_momentum_tuner.py
"""Honest tuning harness for the premium-momentum strategy (Phase 1.3).

Grid search over the PURE sim with the discipline the project's optimizer work
established:
  - COSTS ARE MANDATORY: tuning gross P&L selects configs whose "edge" is
    smaller than the friction it ignores (the survival-gate precedent) — a
    disabled cost model raises, it does not warn.
  - CHRONOLOGICAL train/test split: selection happens on TRAIN only; the test
    half is REPORTED for the selected configs, never used to select (selecting
    on OOS is just overfitting one level up).
  - BOUNDED grid (max_configs) — a runaway product is an error, not a queue.
  - OVERFIT FLAG: train-positive but test-non-positive configs are marked.

Pure: callers pass the already-loaded frames (the route loads once, sweeps
in-process). No DB/tick I/O here.
"""
from __future__ import annotations

import itertools
from typing import Any, Dict, List, Tuple

import pandas as pd

from app.option_costs import CostConfig
from app.premium_momentum_backtest import run_premium_momentum_backtest

MAX_CONFIGS_DEFAULT = 500


def split_sessions(sessions: List[str], train_frac: float = 0.7) -> Tuple[List[str], List[str]]:
    """Chronological split: first ceil(n*train_frac) sessions train, rest test."""
    ordered = sorted(sessions)
    cut = max(1, min(len(ordered) - 1, int(round(len(ordered) * float(train_frac)))))
    return ordered[:cut], ordered[cut:]


def _run_slice(spot_df: pd.DataFrame, sessions: List[str], **kw) -> Dict[str, Any]:
    sliced = spot_df[spot_df["session_date"].isin(sessions)]
    out = run_premium_momentum_backtest(spot_df=sliced, **kw)
    s = out["summary"]
    trades = out["trades"]
    wins = [t for t in trades if t["net_pnl_pts"] > 0]
    return {
        "trades": len(trades),
        "net_pnl_pts": s["net_pnl_pts"],
        "net_pnl_rupees": s["net_pnl_rupees"],
        "win_rate": round(100.0 * len(wins) / len(trades), 1) if trades else 0.0,
        "sessions_excluded": out["coverage"]["sessions_excluded"],
    }


def tune_premium_momentum(*, spot_df: pd.DataFrame, option_candles: pd.DataFrame,
                          contracts: List[Dict[str, Any]], instrument: str,
                          base_params: Dict[str, Any], grid: Dict[str, List[Any]],
                          train_frac: float = 0.7,
                          max_configs: int = MAX_CONFIGS_DEFAULT) -> Dict[str, Any]:
    cost_cfg = CostConfig.from_dict((base_params or {}).get("cost_config"))
    if not cost_cfg.enabled:
        raise ValueError(
            "tuning requires the cost model enabled (base_params.cost_config.enabled=true) "
            "— selecting configs on gross P&L finds edges smaller than the friction they ignore")
    grid = {k: list(v) for k, v in (grid or {}).items() if v}
    if not grid:
        raise ValueError("empty grid — pass at least one parameter with candidate values")
    n_configs = 1
    for v in grid.values():
        n_configs *= len(v)
    if n_configs > max_configs:
        raise ValueError(f"grid explodes to {n_configs} configs > cap {max_configs} — narrow it")

    sessions = sorted(spot_df["session_date"].unique().tolist())
    if len(sessions) < 4:
        raise ValueError("need at least 4 sessions to form an honest train/test split")
    train_s, test_s = split_sessions(sessions, train_frac)

    keys = sorted(grid.keys())
    results = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = {**base_params, **dict(zip(keys, combo))}
        common = dict(option_candles=option_candles, contracts=contracts,
                      instrument=instrument, params=params)
        train = _run_slice(spot_df, train_s, **common)
        test = _run_slice(spot_df, test_s, **common)
        results.append({
            "params": {k: params[k] for k in keys},
            "train": train,
            "test": test,
            # Train-positive but test-non-positive (or test-silent) = the classic
            # overfit signature. Flagged loudly; ranking stays train-only.
            "overfit_warning": bool(train["net_pnl_pts"] > 0
                                    and (test["trades"] == 0 or test["net_pnl_pts"] <= 0)),
        })

    results.sort(key=lambda r: r["train"]["net_pnl_pts"], reverse=True)
    return {
        "configs": results,
        "best_by_train": results[0],
        "split": {"train_sessions": len(train_s), "test_sessions": len(test_s),
                  "train_range": [train_s[0], train_s[-1]],
                  "test_range": [test_s[0], test_s[-1]],
                  "train_frac": float(train_frac)},
        "grid_keys": keys,
        "n_configs": n_configs,
    }
