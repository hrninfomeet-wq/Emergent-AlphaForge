"""Gap-Fade (GAP) — direction edge (mean-reversion of the opening gap).

Large/emotional opening gaps tend to mean-revert; fade the chasers after a
confirmation window, UNLESS the gap is with a strong accelerating trend
(breakaway, which keeps running). Built on AdaptiveStrategyBase (time-gate +
mode-aware speed-confirm + ATR exits). Reads the day-open and prior-session
close from ctx history (self-contained, no toolkit change).
"""
from __future__ import annotations
import pandas as pd
from app.strategies.adaptive_base import AdaptiveStrategyBase
from app.strategies.session_features import gap_by_session


class GapFade(AdaptiveStrategyBase):
    id = "gap_fade"
    name = "Gap-Fade"
    version = "1.0.0"
    description = ("Fade large opening gaps back toward prior close/VWAP after a "
                   "confirmation window; skip breakaway gaps (gap with strong accel). "
                   "Mean-reversion direction edge.")
    extra_params = {
        "g_min_atr": {"type": "float", "min": 0.5, "max": 3.0, "default": 1.0},
        "rsi_ob": {"type": "float", "min": 60, "max": 85, "default": 70},
        "rsi_os": {"type": "float", "min": 15, "max": 40, "default": 30},
        "confirm_hhmm": {"type": "str", "default": "09:45"},
    }

    def _core_signal(self, row, prev, params, ctx):
        t = str(row.get("ist_time") or "")
        if not t or t < str(params["confirm_hhmm"]):
            return ("NONE", 0, [], ["pre-confirm window"], "reversion")
        for k in ("atr", "rsi", "close", "regime_score"):
            if pd.isna(row.get(k)):
                return ("NONE", 0, [], ["warming up"], "reversion")
        g = self._gap(row, ctx)
        if g is None:
            return ("NONE", 0, [], ["no gap data"], "reversion")
        day_open, prev_close = g
        atr = float(row["atr"])
        gap_atr = (day_open - prev_close) / atr if atr > 0 else 0.0
        gmin = float(params["g_min_atr"])
        rsi = float(row["rsi"])
        rs = float(row.get("regime_score") or 0.0)
        accel = float(row.get("accel_z") or 0.0)
        # skip breakaway: a large gap WITH a strong, accelerating same-direction trend
        if gap_atr > gmin and rs > 0.3 and accel > 0.5:
            return ("NONE", 0, [], ["breakaway gap up"], "reversion")
        if gap_atr < -gmin and rs < -0.3 and accel < -0.5:
            return ("NONE", 0, [], ["breakaway gap down"], "reversion")
        if gap_atr > gmin and rsi > float(params["rsi_ob"]):
            return ("PE", 60, [f"fade gap-up {gap_atr:.1f}ATR rsi{rsi:.0f}"], [], "reversion")
        if gap_atr < -gmin and rsi < float(params["rsi_os"]):
            return ("CE", 60, [f"fade gap-down {gap_atr:.1f}ATR rsi{rsi:.0f}"], [], "reversion")
        return ("NONE", 0, [], ["no gap-fade setup"], "reversion")

    def session_precompute(self, df, params):
        # Per-session day-open + prior-session close, computed once so the hot
        # per-bar loop looks them up O(1) instead of re-deriving per bar.
        return gap_by_session(df)

    @staticmethod
    def _gap(row, ctx):
        if not ctx:
            return None
        sess = row.get("session_date")
        # Fast path: per-session constants precomputed by run_backtest (O(1)).
        do_map = ctx.get("day_open")
        pc_map = ctx.get("prev_close")
        if do_map is not None and pc_map is not None:
            do = do_map.get(sess)
            pc = pc_map.get(sess)
            return (do, pc) if (do is not None and pc is not None) else None
        # Fallback: derive per bar (callers that don't precompute, e.g. live
        # single-bar evaluation). Byte-identical to the fast path.
        hist = ctx.get("history_df")
        i = ctx.get("i")
        if hist is None or i is None or "session_date" not in getattr(hist, "columns", []):
            return None
        upto = hist.iloc[: int(i) + 1]
        cur = upto[upto["session_date"] == sess]
        prior = upto[upto["session_date"] != sess]
        if len(cur) < 1 or len(prior) < 1:
            return None
        return float(cur["open"].iloc[0]), float(prior["close"].iloc[-1])
