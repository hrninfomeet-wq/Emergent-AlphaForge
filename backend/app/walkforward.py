"""Walk-forward validation — rolling train/test splits, OOS stitching, divergence detection."""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Any, Dict, List
from app.strategies.base import StrategyBase
from app.backtest import run_backtest, build_equity_curve, compute_metrics, Trade


def walk_forward(
    df: pd.DataFrame,
    strategy: StrategyBase,
    params: Dict[str, Any],
    instrument: str = "NIFTY",
    costs_enabled: bool = True,
    pretrade_filters: Dict[str, Any] | None = None,
    train_pct: float = 0.6,
    n_folds: int = 3,
    trade_window_start: str = "09:25",
    trade_window_end: str = "15:00",
) -> Dict[str, Any]:
    if df.empty or len(df) < 200:
        return {"folds": [], "is_vs_oos": {}, "stitched_oos_equity": [], "stitched_oos_trade_count": 0}

    folds = []
    stitched_trades = []
    fold_size = len(df) // n_folds
    for k in range(n_folds):
        start = k * fold_size
        end = min((k + 1) * fold_size, len(df))
        if end - start < 100:
            continue
        slice_df = df.iloc[start:end].reset_index(drop=True)
        train_end = int(len(slice_df) * train_pct)
        train_df = slice_df.iloc[:train_end].reset_index(drop=True)
        test_df = slice_df.iloc[train_end:].reset_index(drop=True)
        if len(train_df) < 50 or len(test_df) < 30:
            continue
        is_res = run_backtest(train_df, strategy, params, instrument, costs_enabled, pretrade_filters,
                              trade_window_start=trade_window_start, trade_window_end=trade_window_end)
        oos_res = run_backtest(test_df, strategy, params, instrument, costs_enabled, pretrade_filters,
                               trade_window_start=trade_window_start, trade_window_end=trade_window_end)
        folds.append({
            "fold": k + 1,
            "train_range": [str(train_df.iloc[0].get("datetime", "")), str(train_df.iloc[-1].get("datetime", ""))],
            "test_range": [str(test_df.iloc[0].get("datetime", "")), str(test_df.iloc[-1].get("datetime", ""))],
            "is_metrics": is_res["metrics"],
            "oos_metrics": oos_res["metrics"],
        })
        stitched_trades.extend(oos_res["trades"])

    if not folds:
        return {"folds": [], "is_vs_oos": {}, "stitched_oos_equity": [], "stitched_oos_trade_count": 0}

    avg_is_wr = float(np.mean([f["is_metrics"]["win_rate"] for f in folds]))
    avg_oos_wr = float(np.mean([f["oos_metrics"]["win_rate"] for f in folds]))
    avg_is_pf = float(np.mean([f["is_metrics"].get("profit_factor") or 0 for f in folds]))
    avg_oos_pf = float(np.mean([f["oos_metrics"].get("profit_factor") or 0 for f in folds]))

    # Stitched OOS equity from trade dicts
    eq = 0.0; peak = 0.0
    stitched_curve = []
    for t in stitched_trades:
        eq += t["pnl_pts"]
        peak = max(peak, eq)
        stitched_curve.append({
            "ts": t["exit_ts"], "datetime": t.get("exit_datetime", ""),
            "equity_pts": round(eq, 2), "drawdown_pts": round(eq - peak, 2),
            "pnl_pts": round(t["pnl_pts"], 2),
        })

    return {
        "folds": folds,
        "is_vs_oos": {
            "avg_is_win_rate": round(avg_is_wr, 2),
            "avg_oos_win_rate": round(avg_oos_wr, 2),
            "avg_is_profit_factor": round(avg_is_pf, 3),
            "avg_oos_profit_factor": round(avg_oos_pf, 3),
            "divergence_warning": abs(avg_is_wr - avg_oos_wr) > 15,
            "fold_count": len(folds),
        },
        "stitched_oos_equity": stitched_curve,
        "stitched_oos_trade_count": len(stitched_trades),
    }
