"""Opening-Range Fade/Break (ORF) — trapped-liquidity + contraction-selectivity.

Marks the first N-minute opening range, then routes the SAME event by regime:
breakout on trend / NR7 days, fade a FAILED breakout on range days. Opening
window only. Built on AdaptiveStrategyBase.
"""
from __future__ import annotations
import pandas as pd
from app.strategies.adaptive_base import AdaptiveStrategyBase
from app.strategies.session_features import opening_range_by_session


class OpeningRangeAdaptive(AdaptiveStrategyBase):
    id = "opening_range_adaptive"
    name = "Opening-Range Fade/Break"
    version = "1.0.0"
    description = ("First-N-min opening range: breakout on trend/NR7 days, fade failed "
                   "breakouts on range days. Trapped-liquidity + contraction-selectivity edge.")
    extra_params = {
        "or_minutes": {"type": "int", "min": 5, "max": 30, "default": 15},
        "break_buffer_atr": {"type": "float", "min": 0.0, "max": 0.5, "default": 0.1},
        "or_window_end_hhmm": {"type": "str", "default": "10:45"},
        "require_nr7_for_break": {"type": "bool", "default": False},
    }

    def _core_signal(self, row, prev, params, ctx):
        t = str(row.get("ist_time") or "")
        if not t or t > str(params["or_window_end_hhmm"]):
            return ("NONE", 0, [], ["outside opening window"], "momentum")
        if pd.isna(row.get("atr")):
            return ("NONE", 0, [], ["warming up"], "momentum")
        orr = self._opening_range(row, ctx, int(params["or_minutes"]))
        if orr is None:
            return ("NONE", 0, [], ["OR forming / not ready"], "momentum")
        or_hi, or_lo = orr
        buf = float(params["break_buffer_atr"]) * float(row["atr"])
        close = float(row["close"])
        rs = float(row.get("regime_score") or 0.0)
        dt = str(row.get("day_type", "NEUTRAL"))
        nr7 = bool(row.get("nr7"))
        prev_close = float(prev["close"]) if (prev is not None and not pd.isna(prev.get("close"))) else close
        trend_day = rs > 0 or dt == "TREND" or nr7
        range_day = rs < 0 or dt == "RANGE"
        if trend_day and (not params["require_nr7_for_break"] or nr7):
            if close > or_hi + buf:
                return ("CE", 65, [f"OR breakout up day={dt}"], [], "momentum")
            if close < or_lo - buf:
                return ("PE", 65, [f"OR breakout down day={dt}"], [], "momentum")
        if range_day:
            if prev_close > or_hi >= close:
                return ("PE", 60, ["failed up-break -> fade"], [], "reversion")
            if prev_close < or_lo <= close:
                return ("CE", 60, ["failed down-break -> fade"], [], "reversion")
        return ("NONE", 0, [], ["no OR setup"], "momentum")

    def session_precompute(self, df, params):
        # Per-session opening range + "ready" gate, computed once so the hot
        # per-bar loop looks them up O(1) instead of re-deriving per bar.
        return opening_range_by_session(df, int(params["or_minutes"]))

    @staticmethod
    def _opening_range(row, ctx, or_minutes):
        if not ctx:
            return None
        sess = row.get("session_date")
        # Fast path: per-session range precomputed by run_backtest (O(1)). The
        # range is only valid once the session has accumulated more than
        # or_minutes bars; or_ready_idx holds that threshold global bar index.
        hi_map = ctx.get("or_hi")
        lo_map = ctx.get("or_lo")
        ready = ctx.get("or_ready_idx")
        if hi_map is not None and lo_map is not None and ready is not None:
            i = ctx.get("i")
            r = ready.get(sess)
            if r is None or i is None or int(i) < int(r):
                return None  # still forming the OR (or never forms this session)
            hi = hi_map.get(sess)
            lo = lo_map.get(sess)
            return (hi, lo) if (hi is not None and lo is not None) else None
        # Fallback: derive per bar (callers that don't precompute, e.g. live
        # single-bar evaluation). Byte-identical to the fast path.
        hist = ctx.get("history_df")
        i = ctx.get("i")
        if hist is None or i is None or "session_date" not in getattr(hist, "columns", []):
            return None
        upto = hist.iloc[: int(i) + 1]
        sess_bars = upto[upto["session_date"] == sess]
        if len(sess_bars) <= or_minutes:
            return None  # still forming the OR — do not trade yet
        or_bars = sess_bars.iloc[:or_minutes]
        return float(or_bars["high"].max()), float(or_bars["low"].min())
