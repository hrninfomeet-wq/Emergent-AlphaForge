"""Walk-forward validation — rolling train/test splits, OOS stitching, divergence detection."""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from typing import Any, Dict, List
from app.strategies.base import StrategyBase
from app.backtest import run_backtest, build_equity_curve, compute_metrics, Trade

#: Win-rate decay (IS minus OOS, percentage POINTS) that raises a CAUTION.
#: Twice in two days a real signal hid just under the hard cut — 9.44 and 9.65
#: points, each followed by an out-of-sample failure — because the panel only
#: ever showed a boolean. The hard thresholds are deliberately NOT retuned:
#: doing so would rewrite what every already-saved run meant. This softer band,
#: plus the signed delta, make the decay impossible to miss.
SOFT_DIVERGENCE_PTS = 5.0
#: The premium hard cut. NB the spot path historically uses a two-sided 15-point
#: rule (`abs(delta) > 15`), so the same decay is judged differently by family —
#: recorded in docs/BACKTEST_AUDIT_2026-07-30.md rather than silently unified.
HARD_DIVERGENCE_PTS = 10.0


def _divergence_fields(avg_is_wr, avg_oos_wr, hard: bool) -> Dict[str, Any]:
    """The signed decay + both flags, shared by the two engines so the panel
    needs no per-family special case."""
    if avg_is_wr is None or avg_oos_wr is None:
        return {"avg_win_rate_delta": None, "divergence_soft": None,
                "divergence_warning": None}
    delta = round(float(avg_is_wr) - float(avg_oos_wr), 2)
    return {
        "avg_win_rate_delta": delta,
        "divergence_soft": bool(delta >= SOFT_DIVERGENCE_PTS),
        "divergence_warning": bool(hard),
    }


def _ist_session(ts_ms: Any) -> str:
    """Epoch-ms -> IST session date. Sessions are the atomic unit for a premium
    walk-forward: each locks its strikes once at `reference_time`, so a fold
    boundary must never cut one in half."""
    return (pd.to_datetime(int(ts_ms), unit="ms", utc=True)
            .tz_convert("Asia/Kolkata").strftime("%Y-%m-%d"))


def _premium_fold_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Same metric contract the spot folds report, computed on RUPEE option P&L
    (a premium run is rupee-native; a points win-rate would be a second units lie)."""
    n = len(trades)
    if n == 0:
        return {"trade_count": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "profit_factor": None, "total_pnl_pts": 0.0, "max_dd_pts": 0.0,
                "sharpe": None}
    pnl = np.array([float(t.get("option_pnl_value", 0.0) or 0.0) for t in trades])
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gross_loss = abs(float(losses.sum())) if len(losses) else 0.0
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(eq)
    return {
        "trade_count": n,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(len(wins) / n * 100, 2),
        "profit_factor": (round(float(wins.sum()) / gross_loss, 3)
                          if gross_loss > 0 else None),
        "total_pnl_pts": round(float(np.sum(
            [float(t.get("option_pnl_pts", 0.0) or 0.0) for t in trades])), 3),
        "max_dd_pts": round(float((eq - peak).min()), 2) if len(eq) else 0.0,
        "sharpe": (round(float(pnl.mean() / pnl.std() * math.sqrt(252)), 3)
                   if pnl.std() > 0 else None),
    }


def _unmeasured(note: str) -> Dict[str, Any]:
    """A walk-forward that measured nothing must SAY so. Reporting
    `divergence_warning: False` off zero trades reads as a pass — which is what a
    user saw next to a +41.87% headline on 2026-07-29."""
    return {
        "measured": False,
        "note": note,
        "folds": [],
        "is_vs_oos": {"avg_is_win_rate": None, "avg_oos_win_rate": None,
                      "avg_is_profit_factor": None, "avg_oos_profit_factor": None,
                      "avg_win_rate_delta": None, "divergence_soft": None,
                      "divergence_warning": None, "fold_count": 0},
        "stitched_oos_equity": [],
        "stitched_oos_trade_count": 0,
    }


def premium_walk_forward(
    option_trades: List[Dict[str, Any]],
    n_folds: int = 3,
    train_pct: float = 0.6,
) -> Dict[str, Any]:
    """Walk-forward for a premium-native run, over its OPTION trades.

    `walk_forward` runs the SPOT path, whose `evaluate()` is a deliberate inert
    stub for these strategies — so it produced 0 trades in every fold and then
    reported "no divergence".

    This is a faithful equivalent rather than a new method: the spot version
    never re-optimizes either, it re-runs the SAME params on time slices. Since
    premium sessions are independent (strikes lock once per session at
    `reference_time`), partitioning the produced trades by session date gives
    exactly the trades those slices would have produced.

    Returns the same shape as `walk_forward`, plus `measured`/`note`, so the UI
    needs no special case.
    """
    paired = [t for t in (option_trades or []) if t.get("status") == "PAIRED"]
    if not paired:
        return _unmeasured("0 trades — nothing to validate out of sample.")

    by_session: Dict[str, List[Dict[str, Any]]] = {}
    for t in paired:
        ts = t.get("option_exit_ts") or t.get("option_entry_ts")
        if ts is None:
            continue
        by_session.setdefault(_ist_session(ts), []).append(t)
    sessions = sorted(by_session)

    fold_size = len(sessions) // max(1, int(n_folds))
    if fold_size < 2:
        return _unmeasured(
            f"only {len(sessions)} trading sessions — too few to split into "
            f"{n_folds} train/test folds.")

    folds: List[Dict[str, Any]] = []
    stitched: List[Dict[str, Any]] = []
    for k in range(int(n_folds)):
        start = k * fold_size
        end = min((k + 1) * fold_size, len(sessions)) if k < n_folds - 1 else len(sessions)
        chunk = sessions[start:end]
        if len(chunk) < 2:
            continue
        cut = max(1, int(len(chunk) * train_pct))
        if cut >= len(chunk):
            cut = len(chunk) - 1
        train_days, test_days = chunk[:cut], chunk[cut:]
        tr = [t for d in train_days for t in by_session[d]]
        te = [t for d in test_days for t in by_session[d]]
        if not tr or not te:
            continue
        folds.append({
            "fold": k + 1,
            "train_range": [train_days[0], train_days[-1]],
            "test_range": [test_days[0], test_days[-1]],
            "train_sessions": train_days,
            "test_sessions": test_days,
            "is_metrics": _premium_fold_metrics(tr),
            "oos_metrics": _premium_fold_metrics(te),
        })
        stitched.extend(te)

    if not folds:
        return _unmeasured("could not form a single train/test fold with trades on both sides.")

    def _avg(key, which):
        vals = [f[which].get(key) for f in folds]
        vals = [float(v) for v in vals if v is not None]
        return round(float(np.mean(vals)), 3) if vals else None

    avg_is_wr = _avg("win_rate", "is_metrics")
    avg_oos_wr = _avg("win_rate", "oos_metrics")
    # Same rule the spot version uses: a materially lower OOS win rate is the
    # overfitting signal. Only assertable because folds really were measured.
    diverging = bool(avg_is_wr is not None and avg_oos_wr is not None
                     and (avg_is_wr - avg_oos_wr) >= 10.0)

    eq = 0.0
    peak = 0.0
    curve = []
    for t in sorted(stitched, key=lambda x: int(x.get("option_exit_ts") or 0)):
        eq += float(t.get("option_pnl_value", 0.0) or 0.0)
        peak = max(peak, eq)
        curve.append({"ts": t.get("option_exit_ts"),
                      "equity_pts": round(eq, 2),
                      "drawdown_pts": round(eq - peak, 2)})

    return {
        "measured": True,
        "note": None,
        "folds": folds,
        "is_vs_oos": {
            "avg_is_win_rate": avg_is_wr,
            "avg_oos_win_rate": avg_oos_wr,
            "avg_is_profit_factor": _avg("profit_factor", "is_metrics"),
            "avg_oos_profit_factor": _avg("profit_factor", "oos_metrics"),
            **_divergence_fields(avg_is_wr, avg_oos_wr, diverging),
            "fold_count": len(folds),
        },
        "stitched_oos_equity": curve,
        "stitched_oos_trade_count": len(stitched),
    }


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
            # Two-sided 15-point rule preserved verbatim; the signed delta and
            # the soft band are additive.
            **_divergence_fields(avg_is_wr, avg_oos_wr,
                                 abs(avg_is_wr - avg_oos_wr) > 15),
            "fold_count": len(folds),
        },
        "stitched_oos_equity": stitched_curve,
        "stitched_oos_trade_count": len(stitched_trades),
    }
