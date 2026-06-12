"""Honest walk-forward optimization (WFO).

The single-window optimizer (app/optimizer.py) maximizes an objective over the
FULL requested date range, so its best parameters are in-sample by definition;
the post-hoc walk_forward() report re-runs those same params on train/test
slices, which measures stability but NOT selection bias.

This module does the honest version: split the data into chronological
train/test windows, re-optimize on each train window only, evaluate that
window's best params on its UNSEEN test window, and stitch the test-window
trades into one out-of-sample equity curve. The stitched OOS result is the
number a trader should believe; the in-sample numbers are reported only to
compute walk-forward efficiency (how much of the optimized edge survives out
of sample).

Window arithmetic is in TRADING DAYS actually present in the data (so NSE
holidays and missing sessions never silently shrink a window). Indicators are
computed once on the full frame — every indicator in app/indicators.py is
causal (trailing windows only; see detect_swing_points' docstring), so slicing
the enriched frame cannot leak future data into a train window, and test
windows keep realistic warmup history, exactly like live evaluation would.

Jobs persist to the same `optimization_jobs` collection with kind="wfo" so the
existing job-history UI, cancel route, and apply-as-preset flow work unchanged.
Pause/resume operates at window granularity: completed windows are persisted
and skipped on resume; a window interrupted mid-optimization is re-run.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# NOTE: optuna and app.optimizer (which imports optuna at module level) are
# imported lazily inside the runner so this module's pure functions stay
# importable in test environments without optuna installed — same reason the
# existing test suite never imports app.optimizer directly.

log = logging.getLogger(__name__)

# Compact metric set persisted per window (bounds job-doc size).
_WINDOW_METRIC_KEYS = (
    "trade_count", "win_rate", "profit_factor", "total_pnl_pts",
    "max_dd_pts", "sharpe", "ce_count", "pe_count",
)

_MAX_STITCHED_EQUITY_POINTS = 5000


# ---------------------------------------------------------------------------
# Pure functions (unit-testable without DB)
# ---------------------------------------------------------------------------

def split_windows(
    session_dates: List[str],
    train_days: int,
    test_days: int,
    step_days: Optional[int] = None,
    wf_mode: str = "rolling",
    max_windows: int = 12,
) -> Dict[str, Any]:
    """Split sorted unique ISO session dates into train/test windows.

    rolling:  train start slides forward by step (default = test_days, so OOS
              segments are contiguous and non-overlapping).
    anchored: train always starts at the first date and grows; the test window
              follows the train end, sliding by step.

    When more windows fit than max_windows, the OLDEST are dropped — the most
    recent windows matter most and the final window must end at the newest data
    so the deployable params come from the most recent train period.
    """
    n = len(session_dates)
    step = int(step_days) if step_days is not None else int(test_days)
    if step <= 0 or train_days <= 0 or test_days <= 0 or n < train_days + test_days:
        return {"windows": [], "dropped_oldest": 0}

    windows: List[Dict[str, Any]] = []
    if wf_mode == "anchored":
        train_end = train_days  # exclusive index into session_dates
        while train_end + test_days <= n:
            windows.append({
                "train_start": session_dates[0],
                "train_end": session_dates[train_end - 1],
                "test_start": session_dates[train_end],
                "test_end": session_dates[train_end + test_days - 1],
                "train_day_count": train_end,
                "test_day_count": test_days,
            })
            train_end += step
    else:  # rolling
        start = 0
        while start + train_days + test_days <= n:
            windows.append({
                "train_start": session_dates[start],
                "train_end": session_dates[start + train_days - 1],
                "test_start": session_dates[start + train_days],
                "test_end": session_dates[start + train_days + test_days - 1],
                "train_day_count": train_days,
                "test_day_count": test_days,
            })
            start += step

    dropped = max(0, len(windows) - int(max_windows))
    if dropped:
        windows = windows[dropped:]
    for i, w in enumerate(windows):
        w["index"] = i
    return {"windows": windows, "dropped_oldest": dropped}


def stitch_oos_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Metrics over the stitched OOS trades — same formulas as
    backtest.compute_metrics, applied to trade dicts in exit-time order."""
    n = len(trades)
    if n == 0:
        return {
            "trade_count": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "profit_factor": None, "avg_pnl_pts": 0.0, "max_dd_pts": 0.0,
            "sharpe": None, "total_pnl_pts": 0.0,
        }
    pnls = np.array([float(t.get("pnl_pts", 0.0) or 0.0) for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(losses.sum()) if len(losses) else 0.0
    sharpe = float(pnls.mean() / pnls.std() * math.sqrt(252)) if pnls.std() > 0 else None
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    max_dd = float((eq - peak).min()) if len(eq) else 0.0
    return {
        "trade_count": n,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(len(wins) / n * 100, 2),
        "profit_factor": round(gross_profit / abs(gross_loss), 3) if gross_loss < 0 else None,
        "avg_pnl_pts": round(float(pnls.mean()), 3),
        "max_dd_pts": round(max_dd, 2),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "total_pnl_pts": round(float(pnls.sum()), 2),
    }


def stitch_equity_curve(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    eq = 0.0
    peak = 0.0
    curve = []
    for t in trades:
        pnl = float(t.get("pnl_pts", 0.0) or 0.0)
        eq += pnl
        peak = max(peak, eq)
        curve.append({
            "ts": t.get("exit_ts"),
            "datetime": t.get("exit_datetime", ""),
            "equity_pts": round(eq, 2),
            "drawdown_pts": round(eq - peak, 2),
            "pnl_pts": round(pnl, 2),
        })
    if len(curve) > _MAX_STITCHED_EQUITY_POINTS:
        idx = np.linspace(0, len(curve) - 1, _MAX_STITCHED_EQUITY_POINTS).astype(int)
        curve = [curve[i] for i in idx]
    return curve


def _ts_ms_to_ist_date(ts_ms: Any) -> str:
    """Epoch-ms → IST calendar date (ISO). Empty string on garbage."""
    try:
        from datetime import timedelta
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc) + timedelta(hours=5, minutes=30)
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def bucket_option_pnl_by_window(
    paired_trades: List[Dict[str, Any]],
    windows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per-window rupee P&L for the option-aware OOS stitch.

    Buckets PAIRED option trades into WFO test windows by the spot signal's
    entry date (IST). Returns one row per window with the window's net rupee
    P&L and paired-trade count — the rupee analogue of OOS consistency.
    """
    rows: List[Dict[str, Any]] = []
    for w in windows:
        a = str(w.get("test_start") or "")
        b = str(w.get("test_end") or "")
        pnl = 0.0
        n = 0
        for t in paired_trades:
            d = _ts_ms_to_ist_date(t.get("signal_entry_ts"))
            if a and b and d and a <= d <= b:
                pnl += float(t.get("option_pnl_value", 0.0) or 0.0)
                n += 1
        rows.append({
            "index": w.get("index"),
            "test_start": a,
            "test_end": b,
            "pnl_value": round(pnl, 2),
            "paired_trade_count": n,
        })
    return rows


def option_oos_summary(
    sim_result: Dict[str, Any],
    windows: List[Dict[str, Any]],
    option_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Shape the paired-simulation output into the compact `wfo.option_oos`
    block: headline rupee metrics, per-window rupee consistency, and a small
    rupee equity curve. Pure — testable without a DB."""
    metrics = sim_result.get("metrics") or {}
    coverage = sim_result.get("coverage") or {}
    paired = [t for t in (sim_result.get("trades") or []) if t.get("status") == "PAIRED"]
    paired_sorted = sorted(paired, key=lambda t: int(t.get("option_exit_ts") or t.get("signal_exit_ts") or 0))
    eq = 0.0
    curve: List[Dict[str, Any]] = []
    for t in paired_sorted:
        eq += float(t.get("option_pnl_value", 0.0) or 0.0)
        curve.append({
            "ts": t.get("option_exit_ts") or t.get("signal_exit_ts"),
            "equity_value": round(eq, 2),
        })
    if len(curve) > _MAX_STITCHED_EQUITY_POINTS:
        idx = np.linspace(0, len(curve) - 1, _MAX_STITCHED_EQUITY_POINTS).astype(int)
        curve = [curve[i] for i in idx]
    per_window = bucket_option_pnl_by_window(paired_sorted, windows)
    with_trades = [r for r in per_window if r["paired_trade_count"] > 0]
    positive = sum(1 for r in with_trades if r["pnl_value"] > 0)
    return {
        "net_pnl_value": float(metrics.get("total_option_pnl_value", 0.0) or 0.0),
        "win_rate": float(metrics.get("win_rate", 0.0) or 0.0),
        "paired_trade_count": int(metrics.get("paired_trade_count", 0) or 0),
        "total_charges": float(metrics.get("total_charges", 0.0) or 0.0),
        "coverage": coverage,
        "per_window": per_window,
        "rupee_consistency": {
            "windows_with_trades": len(with_trades),
            "positive_windows": positive,
            "consistency_pct": round(positive / len(with_trades) * 100, 1) if with_trades else 0.0,
        },
        "equity": curve,
        "config": {
            "moneyness": option_cfg.get("moneyness", "atm"),
            "dte_filter": option_cfg.get("dte_filter"),
            "lots": option_cfg.get("lots", 1),
            "exit_mode": option_cfg.get("exit_mode", "spot_exit"),
            "option_target_pts": option_cfg.get("option_target_pts"),
            "option_stop_pts": option_cfg.get("option_stop_pts"),
            "option_target_pct": option_cfg.get("option_target_pct"),
            "option_stop_pct": option_cfg.get("option_stop_pct"),
            "costs_enabled": bool((option_cfg.get("cost_config") or {}).get("enabled")),
        },
    }


def walk_forward_efficiency(windows: List[Dict[str, Any]]) -> Optional[float]:
    """OOS pnl-per-test-day divided by IS pnl-per-train-day, summed over all
    completed windows. ~1.0 means the optimized edge fully survived out of
    sample; <0.5 is a strong overfit warning. None when IS pnl <= 0 (ratio
    meaningless)."""
    is_pnl = sum(float((w.get("is_metrics") or {}).get("total_pnl_pts", 0.0) or 0.0) for w in windows)
    oos_pnl = sum(float((w.get("oos_metrics") or {}).get("total_pnl_pts", 0.0) or 0.0) for w in windows)
    train_days = sum(int(w.get("train_day_count", 0) or 0) for w in windows)
    test_days = sum(int(w.get("test_day_count", 0) or 0) for w in windows)
    if is_pnl <= 0 or train_days <= 0 or test_days <= 0:
        return None
    is_rate = is_pnl / train_days
    oos_rate = oos_pnl / test_days
    return round(oos_rate / is_rate, 3)


def oos_consistency(windows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(windows)
    positive = sum(
        1 for w in windows
        if float((w.get("oos_metrics") or {}).get("total_pnl_pts", 0.0) or 0.0) > 0
    )
    return {
        "windows": total,
        "positive_windows": positive,
        "consistency_pct": round(positive / total * 100, 1) if total else 0.0,
    }


def param_stability(
    per_window_params: List[Dict[str, Any]],
    space: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """How much each optimized param wanders across windows. rel_spread is the
    chosen-value range as a fraction of the search-space range — near 0 means
    the optimizer keeps finding the same value (a robust param), near 1 means
    it lands anywhere (a fitted-to-noise param)."""
    out: List[Dict[str, Any]] = []
    if not per_window_params:
        return out
    for name, info in space.items():
        if "fixed" in info:
            continue
        values = [p.get(name) for p in per_window_params if p.get(name) is not None]
        if not values:
            continue
        if info.get("type") == "bool":
            true_share = sum(1 for v in values if bool(v)) / len(values)
            agreement = max(true_share, 1 - true_share)
            out.append({
                "param": name, "type": "bool", "values": values,
                "agreement_pct": round(agreement * 100, 1),
                "rel_spread": round(1 - agreement, 3),
            })
            continue
        lo, hi = float(info.get("min", 0)), float(info.get("max", 1))
        span = hi - lo
        vals = [float(v) for v in values]
        spread = (max(vals) - min(vals)) / span if span > 0 else 0.0
        out.append({
            "param": name, "type": info.get("type"), "values": values,
            "median": round(float(np.median(vals)), 4),
            "min": round(min(vals), 4), "max": round(max(vals), 4),
            "rel_spread": round(spread, 3),
        })
    out.sort(key=lambda r: -r["rel_spread"])
    return out


def _compact_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    return {k: metrics.get(k) for k in _WINDOW_METRIC_KEYS if k in metrics}


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

async def _update_job(job_id: str, patch: Dict[str, Any]) -> None:
    from app.db import get_db
    await get_db().optimization_jobs.update_one({"id": job_id}, {"$set": patch})


async def _job_control(job_id: str) -> Tuple[bool, bool]:
    from app.db import get_db
    doc = await get_db().optimization_jobs.find_one({"id": job_id}, {"cancelled": 1, "paused": 1})
    if not doc:
        return (True, False)
    return (bool(doc.get("cancelled")), bool(doc.get("paused")))


def _evaluate_slice(
    enr_full: pd.DataFrame, a: int, b: int, strategy, merged: Dict[str, Any],
    instrument: str, costs: bool, pretrade: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Backtest one [a:b) row slice of the enriched frame. Returns (metrics
    with ce/pe counts folded in, trades)."""
    from app.backtest import run_backtest
    df_slice = enr_full.iloc[a:b].reset_index(drop=True)
    res = run_backtest(df_slice, strategy, merged, instrument=instrument,
                       costs_enabled=costs, pretrade_filters=pretrade)
    metrics = dict(res["metrics"])
    trades = res.get("trades", []) or []
    ce = sum(1 for t in trades if str(t.get("direction", "")).upper() == "CE")
    metrics["ce_count"] = int(ce)
    metrics["pe_count"] = int(len(trades) - ce)
    return metrics, trades


async def _pair_oos_with_options(
    db, oos_trades: List[Dict[str, Any]], instrument: str, option_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Pair the stitched OOS spot trades with REAL option candles once — the
    same engine and data-loading pattern as the optimizer's option re-rank,
    but for a single trade list. Returns the raw simulate() result
    ({metrics, coverage, trades}). Heavy imports stay lazy (see module note).
    """
    from app.optimizer import _resolve_expiry_by_trade
    from app.options_universe import select_contract_for_signal
    from app.option_backtest import simulate_paired_option_trades
    from app.dte import normalize_dte_filter, compute_dte

    moneyness = str(option_cfg.get("moneyness") or "atm")
    lots = int(option_cfg.get("lots") or 1)
    fixed_expiry = option_cfg.get("expiry_date")
    dte_target = normalize_dte_filter(option_cfg.get("dte_filter"))
    entry_max_age = int(option_cfg.get("entry_max_age_sec") or 120)
    exit_max_age = int(option_cfg.get("exit_max_age_sec") or 180)

    trades = [t for t in oos_trades if t.get("entry_ts") is not None]
    all_ts = [int(t["entry_ts"]) for t in trades]
    all_xt = [int(t["exit_ts"]) for t in trades if t.get("exit_ts") is not None]
    if not all_ts:
        return {"metrics": {}, "coverage": {}, "trades": []}

    # Contracts windowed over the OOS span (+expiry margin), like the re-rank.
    contract_query: Dict[str, Any] = {"underlying": instrument}
    if fixed_expiry:
        contract_query["expiry_date"] = fixed_expiry
    else:
        win_start = _ts_ms_to_ist_date(min(all_ts))
        last_ms = max(all_xt) if all_xt else max(all_ts)
        win_end = _ts_ms_to_ist_date(last_ms + 21 * 24 * 3600 * 1000)
        contract_query["expiry_date"] = {"$gte": win_start, "$lte": win_end}
    contracts = await db.option_contracts.find(contract_query, {"_id": 0}).sort(
        [("expiry_date", 1), ("strike", 1), ("side", 1)]
    ).to_list(length=None)
    expiry_dates_sorted = sorted({str(c.get("expiry_date")) for c in contracts if c.get("expiry_date")})

    if dte_target is not None:
        trades = [t for t in trades
                  if compute_dte(_ts_ms_to_ist_date(int(t["entry_ts"])), expiry_dates_sorted) in dte_target]
    if not trades:
        return {"metrics": {}, "coverage": {}, "trades": []}

    expiry_by_trade = _resolve_expiry_by_trade(trades, contracts, fixed_expiry)
    union_keys: set = set()
    for idx, t in enumerate(trades):
        rexp = fixed_expiry or expiry_by_trade.get(idx)
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

    candles_df = pd.DataFrame()
    if union_keys:
        # Both key forms — candles are stored canonical (2-part); contract docs
        # may carry dated 3-part keys (see instruments.canonical_instrument_key).
        from app.instruments import canonical_instrument_key
        query_keys = sorted({k for key in union_keys for k in (str(key), canonical_instrument_key(str(key)))})
        cq = {"instrument_key": {"$in": query_keys},
              "ts": {"$gte": min(all_ts) - entry_max_age * 1000,
                     "$lte": (max(all_xt) if all_xt else max(all_ts))}}
        rows = await db.options_1m.find(cq, {"_id": 0}).sort("ts", 1).to_list(length=4000000)
        if len(rows) >= 4000000:
            log.warning("WFO option-OOS candle load hit the 4M-row cap; "
                        "pairing beyond the cap window will be missing")
        if rows:
            candles_df = pd.DataFrame(rows)

    return await asyncio.to_thread(
        simulate_paired_option_trades,
        spot_trades=trades, contracts=contracts, option_candles=candles_df,
        underlying=instrument, moneyness=moneyness, lots=lots,
        entry_max_age_sec=entry_max_age, exit_max_age_sec=exit_max_age,
        expiry_by_trade=expiry_by_trade, fixed_expiry_date=fixed_expiry,
        exit_mode=option_cfg.get("exit_mode") or "spot_exit",
        option_target_pts=option_cfg.get("option_target_pts"),
        option_stop_pts=option_cfg.get("option_stop_pts"),
        option_target_pct=option_cfg.get("option_target_pct"),
        option_stop_pct=option_cfg.get("option_stop_pct"),
        cost_config=option_cfg.get("cost_config"),
        sizing_config=option_cfg.get("sizing_config"),
    )


async def run_wfo(job_id: str, payload: Dict[str, Any], resume: bool = False) -> None:
    """Walk-forward optimization worker. Persists progress per window."""
    try:
        import optuna
        from app.db import get_db
        from app.indicators import precompute_all_indicators
        from app.optimizer import (
            _DEFAULT_LOT_SIZE,
            _DISQUALIFY,
            _MAX_ENRICHED_CACHE,
            _build_param_space,
            _indicator_key,
            _make_sampler,
            _objective_value,
            _save_best_as_backtest,
            _suggest,
        )
        from app.regime import classify_regime_series
        from app.strategies.base import get_registry
        from app.warehouse import load_candles_df

        instrument = str(payload["instrument"]).upper()
        strategy_id = payload["strategy_id"]
        objective = payload.get("objective", "risk_adjusted")
        costs = payload.get("costs_enabled", True)
        pretrade = payload.get("pretrade_filters", {})
        param_overrides = payload.get("param_overrides", {})
        start_ts = payload.get("start_ts")
        end_ts = payload.get("end_ts")
        method = payload.get("method", "bayesian")
        if method == "grid":
            method = "bayesian"  # grid per window is not supported; TPE is

        min_trades = int(payload.get("min_trades", 10) or 0)
        min_direction_share = float(payload.get("min_direction_share", 0.0) or 0.0)
        optimize_indicator_periods = bool(payload.get("optimize_indicator_periods", False))

        train_days = int(payload.get("train_days", 60))
        test_days = int(payload.get("test_days", 20))
        step_days = payload.get("step_days")
        wf_mode = str(payload.get("wf_mode", "rolling"))
        n_trials_per_window = int(payload.get("n_trials_per_window", 40))
        max_windows = int(payload.get("max_windows", 12))

        strategy = get_registry().get(strategy_id)
        if not strategy:
            await _update_job(job_id, {"status": "failed", "error": f"Strategy {strategy_id} not found",
                                       "finished_at": datetime.now(timezone.utc).isoformat()})
            return

        df = await load_candles_df(instrument, start_ts, end_ts)
        if df.empty or len(df) < 100:
            await _update_job(job_id, {"status": "failed",
                                       "error": f"Insufficient candles for {instrument} ({len(df)})",
                                       "finished_at": datetime.now(timezone.utc).isoformat()})
            return

        # Per-row IST session date (sorted ascending because ts is sorted; ISO
        # strings sort chronologically) → row ranges per window via searchsorted.
        dates_arr = (
            pd.to_datetime(df["ts"], unit="ms", utc=True)
            .dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d").to_numpy()
        )
        session_dates = sorted(set(dates_arr.tolist()))
        split = split_windows(session_dates, train_days, test_days, step_days, wf_mode, max_windows)
        windows = split["windows"]
        if len(windows) < 2:
            await _update_job(job_id, {
                "status": "failed",
                "error": (f"Not enough data for walk-forward: {len(session_dates)} sessions in range, "
                          f"need at least {train_days + test_days} for 1 window and "
                          f"{train_days + test_days + (step_days or test_days)} for 2"),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            return

        def row_range(first_date: str, last_date: str) -> Tuple[int, int]:
            a = int(np.searchsorted(dates_arr, first_date, side="left"))
            b = int(np.searchsorted(dates_arr, last_date, side="right"))
            return a, b

        # Indicator enrichment over the FULL frame, cached per indicator-period
        # combo (all indicators are causal — see module docstring).
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

        lot_size = _DEFAULT_LOT_SIZE.get(instrument, 1)
        try:
            lot_doc = await get_db().option_contracts.find_one(
                {"underlying": instrument, "lot_size": {"$gt": 0}},
                {"lot_size": 1}, sort=[("expiry_date", -1)],
            )
            if lot_doc and lot_doc.get("lot_size"):
                lot_size = int(lot_doc["lot_size"])
        except Exception:
            pass

        def obj(metrics: Dict[str, Any]) -> float:
            return _objective_value(metrics, objective, lot_size=lot_size,
                                    min_trades=min_trades, min_direction_share=min_direction_share)

        space = _build_param_space(strategy.parameter_schema, param_overrides,
                                   include_indicator_periods=optimize_indicator_periods)

        completed_windows: List[Dict[str, Any]] = []
        oos_trades_all: List[Dict[str, Any]] = []
        if resume:
            rdoc = await get_db().optimization_jobs.find_one(
                {"id": job_id}, {"wfo_windows": 1, "wfo_oos_trades": 1}) or {}
            completed_windows = list(rdoc.get("wfo_windows") or [])
            oos_trades_all = list(rdoc.get("wfo_oos_trades") or [])
            log.info(f"Resuming WFO {job_id} from window {len(completed_windows)}/{len(windows)}")

        await _update_job(job_id, {
            "status": "running", "kind": "wfo", "paused": False, "cancelled": False,
            "lot_size": lot_size, "param_space": space,
            **({"resumed_at": datetime.now(timezone.utc).isoformat()} if resume else
               {"started_at": datetime.now(timezone.utc).isoformat()}),
            "wfo_config": {
                "train_days": train_days, "test_days": test_days,
                "step_days": step_days or test_days, "wf_mode": wf_mode,
                "n_trials_per_window": n_trials_per_window, "max_windows": max_windows,
                "window_count": len(windows), "dropped_oldest": split["dropped_oldest"],
                "session_count": len(session_dates), "method": method,
            },
            "n_trials_total": len(windows) * n_trials_per_window,
            "guards": {"min_trades": min_trades, "min_direction_share": min_direction_share,
                       "optimize_indicator_periods": optimize_indicator_periods},
        })

        cancelled = False
        for w in windows[len(completed_windows):]:
            tr_a, tr_b = row_range(w["train_start"], w["train_end"])
            te_a, te_b = row_range(w["test_start"], w["test_end"])

            study = optuna.create_study(direction="maximize", sampler=_make_sampler(method))
            window_best = {"value": -float("inf"), "params": {}, "metrics": {}}

            def objective_fn(trial: optuna.Trial) -> float:
                params = _suggest(trial, space)
                merged = strategy.merged_params(params)
                enr = get_enriched(merged)
                metrics, _ = _evaluate_slice(enr, tr_a, tr_b, strategy, merged,
                                             instrument, costs, pretrade)
                val = obj(metrics)
                if val > window_best["value"]:
                    window_best.update({"value": val, "params": dict(params), "metrics": metrics})
                return val

            paused = False
            for i in range(n_trials_per_window):
                cf, pf = await _job_control(job_id)
                if cf:
                    cancelled = True
                    break
                if pf:
                    paused = True
                    break
                await asyncio.to_thread(study.optimize, objective_fn, n_trials=1, catch=(Exception,))
                if (i + 1) % 5 == 0 or i == n_trials_per_window - 1:
                    await _update_job(job_id, {
                        "wfo_progress": {"window": w["index"] + 1, "window_count": len(windows),
                                         "trial": i + 1, "trials_per_window": n_trials_per_window},
                        "n_trials_completed": w["index"] * n_trials_per_window + i + 1,
                    })

            if paused:
                await _update_job(job_id, {
                    "status": "paused", "paused": False,
                    "paused_at": datetime.now(timezone.utc).isoformat(),
                    "wfo_windows": completed_windows, "wfo_oos_trades": oos_trades_all,
                })
                log.info(f"WFO {job_id} paused at window {w['index'] + 1}/{len(windows)}")
                return
            if cancelled:
                log.info(f"WFO {job_id} cancelled at window {w['index'] + 1}/{len(windows)}")
                break

            if window_best["value"] <= _DISQUALIFY or not window_best["params"]:
                # No qualifying trial in this train window — record it honestly
                # as a no-trade window (an OOS gap, not a silent skip).
                completed_windows.append({
                    **{k: w[k] for k in ("index", "train_start", "train_end", "test_start",
                                         "test_end", "train_day_count", "test_day_count")},
                    "no_qualifying_params": True,
                    "best_params": None, "is_objective": None,
                    "is_metrics": {}, "oos_metrics": {}, "oos_trade_count": 0,
                })
            else:
                merged_best = strategy.merged_params(window_best["params"])
                enr_best = get_enriched(merged_best)
                oos_metrics, oos_trades = await asyncio.to_thread(
                    _evaluate_slice, enr_best, te_a, te_b, strategy, merged_best,
                    instrument, costs, pretrade)
                oos_trades_all.extend(oos_trades)
                completed_windows.append({
                    **{k: w[k] for k in ("index", "train_start", "train_end", "test_start",
                                         "test_end", "train_day_count", "test_day_count")},
                    "no_qualifying_params": False,
                    "best_params": window_best["params"],
                    "is_objective": round(float(window_best["value"]), 4),
                    "is_metrics": _compact_metrics(window_best["metrics"]),
                    "oos_metrics": _compact_metrics(oos_metrics),
                    "oos_trade_count": len(oos_trades),
                })
            await _update_job(job_id, {"wfo_windows": completed_windows,
                                       "wfo_oos_trades": oos_trades_all})

        # ---- Final analysis over completed windows ----
        await _update_job(job_id, {"status": "analyzing"})
        usable = [w for w in completed_windows if not w.get("no_qualifying_params")]
        oos_sorted = sorted(oos_trades_all, key=lambda t: (t.get("exit_ts") or 0))
        stitched = stitch_oos_metrics(oos_sorted)
        equity = stitch_equity_curve(oos_sorted)
        efficiency = walk_forward_efficiency(usable)
        consistency = oos_consistency(usable)
        stability = param_stability([w["best_params"] for w in usable], space)

        final_params = usable[-1]["best_params"] if usable else None

        best_backtest_run_id = None
        if final_params and not cancelled:
            merged_final = strategy.merged_params(final_params)
            df_final = get_enriched(merged_final)
            best_backtest_run_id = await _save_best_as_backtest(
                job_id, payload, strategy, df_final, final_params,
                instrument, costs, pretrade, run_walkforward=True, option_config=None)

        # ---- Option-aware OOS (v2): rupee reality check on the stitch ----
        # Pair the stitched OOS trades with real option candles once and report
        # net rupee + per-window rupee consistency next to the spot stitch.
        # Never fails the job: data gaps degrade to an `error`/low-coverage
        # block the UI can show honestly.
        option_oos = None
        if payload.get("option_aware") and (payload.get("option_config") or {}) and oos_sorted:
            try:
                from app.db import get_db
                opt_cfg = payload.get("option_config") or {}
                sim = await _pair_oos_with_options(get_db(), oos_sorted, instrument, opt_cfg)
                option_oos = option_oos_summary(sim, usable, opt_cfg)
            except Exception as e:
                log.exception(f"WFO {job_id}: option-aware OOS pairing failed")
                option_oos = {"error": str(e)}

        final_status = "cancelled" if (cancelled and len(completed_windows) < len(windows)) else "done"
        await _update_job(job_id, {
            "status": final_status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "kind": "wfo",
            "best_params": final_params or {},
            "best_value": stitched.get("total_pnl_pts"),
            "best_metrics": {**stitched, "source": "stitched_oos"},
            "best_backtest_run_id": best_backtest_run_id,
            "wfo": {
                "windows": completed_windows,
                "stitched_oos": stitched,
                "stitched_oos_equity": equity,
                "efficiency": efficiency,
                "consistency": consistency,
                "param_stability": stability,
                "final_params": final_params,
                "final_params_window": usable[-1]["index"] if usable else None,
                "option_oos": option_oos,
            },
            # Bulky intermediates are no longer needed once `wfo` is written.
            "wfo_oos_trades": [],
        })
        log.info(f"WFO {job_id} {final_status}: windows={len(completed_windows)}/{len(windows)} "
                 f"stitched_oos_pnl={stitched.get('total_pnl_pts')} efficiency={efficiency}")
    except Exception as e:
        log.exception(f"WFO {job_id} crashed")
        await _update_job(job_id, {"status": "failed", "error": str(e),
                                   "finished_at": datetime.now(timezone.utc).isoformat()})


async def create_wfo_job(payload: Dict[str, Any]) -> str:
    import uuid
    from app.db import get_db
    job_id = str(uuid.uuid4())
    db = get_db()
    doc = {
        "id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "queued",
        "kind": "wfo",
        "instrument": payload.get("instrument", "NIFTY"),
        "strategy_id": payload.get("strategy_id"),
        "method": payload.get("method", "bayesian"),
        "objective": payload.get("objective", "risk_adjusted"),
        "n_trials_total": int(payload.get("max_windows", 12)) * int(payload.get("n_trials_per_window", 40)),
        "n_trials_completed": 0,
        "config": payload,
        "best_so_far": None,
    }
    await db.optimization_jobs.insert_one(doc)
    asyncio.create_task(run_wfo(job_id, payload))
    return job_id


async def resume_wfo_job(job_id: str) -> bool:
    """Re-launch a paused / interrupted / failed WFO job, skipping windows that
    were fully completed and persisted. Returns False if not resumable."""
    from app.db import get_db
    db = get_db()
    doc = await db.optimization_jobs.find_one({"id": job_id}, {"_id": 0})
    if not doc or doc.get("kind") != "wfo":
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
    asyncio.create_task(run_wfo(job_id, payload, resume=True))
    return True
