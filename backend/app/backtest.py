"""Vectorized SPOT-mode backtest engine.
Driven by strategy plugin's evaluate() per bar; manages position lifecycle, exits, cooldowns.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from app.strategies.base import StrategyBase, Signal
from app.costs import apply_round_trip_cost
from app.exit_engine import intrabar_exit

TRADE_WINDOW_START = "09:25"
# Default session end for entries: 15:00 IST. Combined with the 09:25 start this
# implements the discipline rule of no entries in the first 10 min or last 30 min
# (09:15-09:25 and 15:00-15:30). Both ends are overridable per backtest.
TRADE_WINDOW_END = "15:00"


@dataclass
class Trade:
    direction: str
    entry_ts: int
    entry_price: float
    entry_datetime: str = ""
    exit_ts: Optional[int] = None
    exit_price: Optional[float] = None
    exit_datetime: str = ""
    exit_reason: str = ""
    pnl_pts: float = 0.0
    pnl_pct: float = 0.0
    mfe_pts: float = 0.0
    mae_pts: float = 0.0
    score: int = 0
    reasons: List[str] = field(default_factory=list)
    bars_held: int = 0
    # Market-context snapshot at entry (regime + IST time). Used downstream to
    # tag option trades and analyze where a strategy actually has edge.
    regime: str = ""
    ist_time: str = ""
    scenario: str = ""
    spot_target_level: Optional[float] = None
    # Per-trade exit overrides + entry bar index. Previously stashed via
    # __dict__[...] inside the hot loop; promoted to real (optional) fields so
    # reads/writes are plain attribute access. Defaults preserve prior behavior
    # (None -> fall back to the run-level default target/stop / current bar).
    target_pts_override: Optional[float] = None
    stop_pts_override: Optional[float] = None
    entry_bar: Optional[int] = None


def _in_window(ist: str, start: str, end: str) -> bool:
    return start <= ist < end


def _compute_orb_for_session(df: pd.DataFrame, range_minutes: int = 15) -> Dict[str, Dict]:
    """Compute opening range (first N minutes) high/low for each session_date."""
    orb_hi: Dict[str, float] = {}
    orb_lo: Dict[str, float] = {}
    for date, grp in df.groupby("session_date"):
        first_bars = grp.head(range_minutes)
        if len(first_bars) > 0:
            orb_hi[date] = float(first_bars["high"].max())
            orb_lo[date] = float(first_bars["low"].min())
    return {"orb_hi": orb_hi, "orb_lo": orb_lo}


def _apply_pretrade_filter(signal: Signal, row: pd.Series, filters: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (passes, blocker_reason). Returns blocker_reason='' on pass."""
    if not filters:
        return True, ""
    min_score = filters.get("min_confidence_score", 0)
    if signal.score < min_score:
        return False, f"min_score {signal.score}<{min_score}"
    regime_filter = filters.get("allowed_regimes")
    if regime_filter and row.get("regime") not in regime_filter:
        return False, f"regime {row.get('regime')} not in allowed"
    return True, ""


def run_backtest(
    df: pd.DataFrame,
    strategy: StrategyBase,
    params: Dict[str, Any],
    instrument: str = "NIFTY",
    costs_enabled: bool = True,
    pretrade_filters: Optional[Dict[str, Any]] = None,
    trade_window_start: str = TRADE_WINDOW_START,
    trade_window_end: str = TRADE_WINDOW_END,
) -> Dict[str, Any]:
    if df.empty or len(df) < 50:
        return {
            "trades": [], "metrics": _empty_metrics(),
            "equity_curve": [], "signal_funnel": _empty_funnel()
        }

    if not df.index.equals(pd.RangeIndex(len(df))):
        df = df.reset_index(drop=True)
    # Pre-materialize rows as plain dicts ONCE. Indexing df.iloc[i] inside the
    # hot loop builds a fresh pandas Series every bar (very slow and GIL-heavy);
    # a list of dicts is 5-20x faster and is fully compatible with strategies,
    # which only use row["col"] / row.get("col"). history_df in ctx stays a
    # DataFrame for strategies that need windowed lookback.
    records = df.to_dict("records")
    trades: List[Trade] = []
    open_trade: Optional[Trade] = None
    last_signal_bar = -10_000
    cooldown = max(1, int(params.get("cooldown_bars", 5)))
    target_pts_default = float(params.get("spot_target_pts", 30))
    stop_pts_default = float(params.get("spot_stop_pts", 15))
    threshold = int(params.get("signal_threshold", 55))

    # Funnel counters
    funnel = {"evaluated": 0, "score_below_threshold": 0, "blocked_by_strategy": 0,
              "blocked_by_pretrade": 0, "in_cooldown": 0, "out_of_window": 0,
              "position_open": 0, "signals_fired": 0}

    # Strategy-level context
    ctx_global: Dict[str, Any] = {"history_df": df, "instrument": instrument}
    if strategy.id == "opening_range_breakout":
        rng = int(params.get("range_minutes", 15))
        ctx_global.update(_compute_orb_for_session(df, range_minutes=rng))

    for i in range(1, len(df)):
        row = records[i]
        prev = records[i - 1]
        ts = int(row["ts"])
        ist = row.get("ist_time", "")

        # --- manage open position ---
        if open_trade is not None:
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            entry = open_trade.entry_price
            fav = (high - entry) if open_trade.direction == "CE" else (entry - low)
            adv = (entry - low) if open_trade.direction == "CE" else (high - entry)
            open_trade.mfe_pts = max(open_trade.mfe_pts, max(0.0, fav))
            open_trade.mae_pts = max(open_trade.mae_pts, max(0.0, adv))
            tgt_p = open_trade.target_pts_override
            if tgt_p is None:
                tgt_p = target_pts_default
            stp_p = open_trade.stop_pts_override
            if stp_p is None:
                stp_p = stop_pts_default
            stop = entry - stp_p if open_trade.direction == "CE" else entry + stp_p
            target = entry + tgt_p if open_trade.direction == "CE" else entry - tgt_p
            if open_trade.spot_target_level is not None:
                target = float(open_trade.spot_target_level)   # absolute level (VOLATILE_FADE)
            # Shared intrabar exit decision (stop-first, pessimistic). Used by
            # both the spot and option engines so the rule never drifts.
            exit_price, exit_reason = intrabar_exit(
                high=high, low=low, stop=stop, target=target,
                is_long=(open_trade.direction == "CE"),
            )
            if exit_price is None and ist >= trade_window_end:
                exit_price, exit_reason = close, "TIME_EXIT"
            if exit_price is not None:
                open_trade.exit_ts = ts
                open_trade.exit_price = exit_price
                open_trade.exit_datetime = str(row.get("datetime", ""))
                open_trade.exit_reason = exit_reason
                eb = open_trade.entry_bar
                open_trade.bars_held = i - (i if eb is None else int(eb))
                gross = (exit_price - entry) if open_trade.direction == "CE" else (entry - exit_price)
                net = apply_round_trip_cost(gross, instrument, costs_enabled)
                open_trade.pnl_pts = round(net, 3)
                open_trade.pnl_pct = round((net / entry) * 100, 4) if entry > 0 else 0
                trades.append(open_trade)
                open_trade = None

        # --- look for new entry ---
        if open_trade is not None:
            funnel["position_open"] += 1
            continue
        if not _in_window(ist, trade_window_start, trade_window_end):
            funnel["out_of_window"] += 1
            continue
        if i - last_signal_bar < cooldown:
            funnel["in_cooldown"] += 1
            continue

        funnel["evaluated"] += 1
        # Reuse ctx_global in place instead of rebuilding {**ctx_global, "i": i}
        # every entry-eval bar. Verified safe: ALL strategies only READ ctx via
        # ctx.get(...) within evaluate() -- none retain a reference, assign it to
        # self, or write into it across calls (grep of backend/app/strategies).
        ctx_global["i"] = i
        sig: Signal = strategy.evaluate(row, prev, params, ctx_global)
        if sig.direction not in ("CE", "PE"):
            continue
        if sig.blockers:
            funnel["blocked_by_strategy"] += 1
            continue
        if sig.score < threshold:
            funnel["score_below_threshold"] += 1
            continue
        passes, why = _apply_pretrade_filter(sig, row, pretrade_filters or {})
        if not passes:
            funnel["blocked_by_pretrade"] += 1
            continue

        funnel["signals_fired"] += 1
        open_trade = Trade(
            direction=sig.direction,
            entry_ts=ts,
            entry_price=float(row["close"]),
            entry_datetime=str(row.get("datetime", "")),
            score=sig.score,
            reasons=sig.reasons,
            regime=str(row.get("regime", "") or ""),
            ist_time=str(ist or ""),
            scenario=str(getattr(sig, "scenario", "") or ""),
            spot_target_level=getattr(sig, "spot_target_level", None),
        )
        open_trade.target_pts_override = sig.spot_target_pts or target_pts_default
        open_trade.stop_pts_override = sig.spot_stop_pts or stop_pts_default
        open_trade.entry_bar = i
        last_signal_bar = i

    # Close any trailing trade at EOD
    if open_trade is not None:
        last = records[-1]
        exit_price = float(last["close"])
        entry = open_trade.entry_price
        gross = (exit_price - entry) if open_trade.direction == "CE" else (entry - exit_price)
        net = apply_round_trip_cost(gross, instrument, costs_enabled)
        open_trade.exit_ts = int(last["ts"])
        open_trade.exit_price = exit_price
        open_trade.exit_datetime = str(last.get("datetime", ""))
        open_trade.exit_reason = "EOD"
        open_trade.pnl_pts = round(net, 3)
        open_trade.pnl_pct = round((net / entry) * 100, 4) if entry > 0 else 0
        trades.append(open_trade)

    metrics = compute_metrics(trades)
    equity = build_equity_curve(trades)
    return {
        "trades": [_clean_trade_dict(t) for t in trades],
        "metrics": metrics,
        "equity_curve": equity,
        "signal_funnel": funnel,
    }


def _clean_trade_dict(t: Trade) -> Dict[str, Any]:
    d = asdict(t)
    # The override/entry-bar fields are loop-internal bookkeeping (formerly
    # stashed via __dict__, so never serialized). Keep the emitted trade dict
    # byte-identical to the pre-optimization shape by dropping them here.
    d.pop("target_pts_override", None)
    d.pop("stop_pts_override", None)
    d.pop("entry_bar", None)
    d.pop("spot_target_level", None)   # internal bookkeeping; not serialized
    return d


def _empty_metrics() -> Dict[str, Any]:
    return {
        "trade_count": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
        "profit_factor": None, "avg_pnl_pts": 0.0, "expectancy_pts": 0.0,
        "max_dd_pts": 0.0, "sharpe": None, "best_pts": 0.0, "worst_pts": 0.0,
        "target_exits": 0, "stop_exits": 0, "time_exits": 0, "total_pnl_pts": 0.0,
        "avg_bars_held": 0.0,
    }


def _empty_funnel() -> Dict[str, int]:
    return {"evaluated": 0, "score_below_threshold": 0, "blocked_by_strategy": 0,
            "blocked_by_pretrade": 0, "in_cooldown": 0, "out_of_window": 0,
            "position_open": 0, "signals_fired": 0}


def compute_metrics(trades: List[Trade]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return _empty_metrics()
    pnls = np.array([t.pnl_pts for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_loss = float(losses.sum()) if len(losses) > 0 else 0.0
    sharpe = float(pnls.mean() / pnls.std() * math.sqrt(252)) if pnls.std() > 0 else None
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    max_dd = float(dd.min()) if len(dd) else 0.0
    avg_bars = float(np.mean([t.bars_held for t in trades if t.bars_held > 0])) if any(t.bars_held > 0 for t in trades) else 0.0
    return {
        "trade_count": n,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(len(wins) / n * 100, 2),
        "profit_factor": round(gross_profit / abs(gross_loss), 3) if gross_loss < 0 else None,
        "avg_pnl_pts": round(float(pnls.mean()), 3),
        "expectancy_pts": round(float(pnls.mean()), 3),
        "max_dd_pts": round(max_dd, 2),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "best_pts": round(float(pnls.max()), 2),
        "worst_pts": round(float(pnls.min()), 2),
        "target_exits": int(sum(1 for t in trades if t.exit_reason == "TARGET")),
        "stop_exits": int(sum(1 for t in trades if t.exit_reason == "STOP")),
        "time_exits": int(sum(1 for t in trades if t.exit_reason in ("TIME_EXIT", "EOD"))),
        "total_pnl_pts": round(float(pnls.sum()), 2),
        "avg_bars_held": round(avg_bars, 1),
    }


def build_equity_curve(trades: List[Trade]) -> List[Dict[str, Any]]:
    eq = 0.0
    peak = 0.0
    curve = []
    for t in trades:
        eq += t.pnl_pts
        peak = max(peak, eq)
        curve.append({
            "ts": t.exit_ts,
            "datetime": t.exit_datetime,
            "equity_pts": round(eq, 2),
            "drawdown_pts": round(eq - peak, 2),
            "pnl_pts": round(t.pnl_pts, 2),
        })
    return curve


def stat_significance(n: int, win_rate_pct: float, profit_factor: Optional[float]) -> Dict[str, Any]:
    if n == 0:
        return {"badge": "INSUFFICIENT", "ci95_win_rate": [0, 0], "note": "0 trades"}
    p = win_rate_pct / 100
    z = 1.96
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo = max(0, (centre - margin) * 100)
    hi = min(100, (centre + margin) * 100)
    pf_ok = profit_factor is not None and profit_factor >= 1.3
    if n >= 100 and pf_ok:
        badge = "SIGNIFICANT"
    elif n >= 30 and (profit_factor or 0) >= 1.0:
        badge = "TENTATIVE"
    else:
        badge = "INSUFFICIENT"
    return {
        "badge": badge,
        "ci95_win_rate": [round(lo, 1), round(hi, 1)],
        "note": f"95% CI of win rate based on {n} trades",
    }
