"""Opening Range Breakout (ORB) — first 15 minutes range, breakout with volume/momentum confirmation.
Classic + robust strategy.
"""
from __future__ import annotations
import pandas as pd
from app.strategies.base import StrategyBase, Signal
from app.strategies.session_features import orb_range_by_session


class OpeningRangeBreakout(StrategyBase):
    id = "opening_range_breakout"
    name = "Opening Range Breakout (ORB)"
    version = "1.0.0"
    description = "Marks first 15-min H/L, enters on breakout with VWAP+momentum confirm. Works mornings (09:30–11:30)."
    supported_modes = ["SCALP", "INTRADAY"]
    parameter_schema = {
        "range_minutes": {"type": "int", "min": 5, "max": 60, "default": 15},
        "buffer_pts": {"type": "float", "min": 0, "max": 20, "default": 2},
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 50},
        "breakout_end_time": {"type": "str", "default": "11:30"},
        "spot_target_pts": {"type": "float", "min": 10, "max": 200, "default": 40},
        "spot_stop_pts": {"type": "float", "min": 5, "max": 100, "default": 18},
    }

    def session_precompute(self, df, params):
        # Per-session opening range, computed once so evaluate() looks it up O(1).
        rng = int(params.get("range_minutes", 15))
        return orb_range_by_session(df, range_minutes=rng)

    def evaluate(self, row, prev, params, ctx) -> Signal:
        # ORB is special: it needs the SESSION'S opening range, computed in ctx
        session_date = row.get("session_date")
        if not session_date:
            return Signal(direction="NONE")
        orb_hi = ctx.get("orb_hi", {}).get(session_date)
        orb_lo = ctx.get("orb_lo", {}).get(session_date)
        if orb_hi is None or orb_lo is None:
            return Signal(direction="NONE", blockers=["opening range not yet formed"])

        ist = row.get("ist_time", "")
        if ist > params["breakout_end_time"]:
            return Signal(direction="NONE", blockers=["past breakout window"])

        close = float(row["close"])
        buf = params["buffer_pts"]
        vwap = float(row.get("vwap", close))
        rsi_val = float(row.get("rsi", 50))
        if pd.isna(rsi_val):
            rsi_val = 50

        if close > orb_hi + buf and close > vwap:
            score = 60
            reasons = [f"break above ORB-H {orb_hi:.1f}", "close > VWAP"]
            if rsi_val > 55:
                score += 10
                reasons.append(f"RSI {rsi_val:.0f}>55")
            return Signal(direction="CE", score=score, reasons=reasons,
                          spot_target_pts=params["spot_target_pts"],
                          spot_stop_pts=params["spot_stop_pts"])
        if close < orb_lo - buf and close < vwap:
            score = 60
            reasons = [f"break below ORB-L {orb_lo:.1f}", "close < VWAP"]
            if rsi_val < 45:
                score += 10
                reasons.append(f"RSI {rsi_val:.0f}<45")
            return Signal(direction="PE", score=score, reasons=reasons,
                          spot_target_pts=params["spot_target_pts"],
                          spot_stop_pts=params["spot_stop_pts"])
        return Signal(direction="NONE")
