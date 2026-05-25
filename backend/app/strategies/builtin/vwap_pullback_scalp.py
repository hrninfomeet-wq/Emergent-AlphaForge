"""VWAP Pullback Scalp — ranked highest in 2026 research for index option scalping.
Trend filter: above/below VWAP. Entry: pullback to EMA9 with confirmation candle.
"""
from __future__ import annotations
import pandas as pd
from app.strategies.base import StrategyBase, Signal


class VWAPPullbackScalp(StrategyBase):
    id = "vwap_pullback_scalp"
    name = "VWAP Pullback Scalp"
    version = "1.0.0"
    description = "Trend = side of VWAP. Entry = pullback to EMA9 with confirming candle. Default for scalping."
    supported_modes = ["SCALP"]
    parameter_schema = {
        "ema_fast": {"type": "int", "min": 5, "max": 21, "default": 9},
        "ema_slow": {"type": "int", "min": 13, "max": 50, "default": 21},
        "min_distance_from_vwap_pts": {"type": "float", "min": 0, "max": 100, "default": 8},
        "signal_threshold": {"type": "int", "min": 40, "max": 90, "default": 55},
        "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 5},
        "spot_target_pts": {"type": "float", "min": 5, "max": 100, "default": 25},
        "spot_stop_pts": {"type": "float", "min": 3, "max": 60, "default": 12},
    }

    def evaluate(self, row, prev, params, ctx) -> Signal:
        req = ["close", "vwap", "ema9", "ema21"]
        if any(pd.isna(row.get(k)) for k in req):
            return Signal(direction="NONE", blockers=["warming up"])
        close, vwap = float(row["close"]), float(row["vwap"])
        ema_f, ema_s = float(row["ema9"]), float(row["ema21"])
        score = 0
        reasons = []
        direction = "NONE"
        min_dist = params["min_distance_from_vwap_pts"]

        # Bullish setup: above VWAP, EMA stack bull, pullback touch to EMA9 with bullish close
        if close > vwap + min_dist and ema_f > ema_s:
            if row["low"] <= ema_f <= row["high"] and close > row["open"]:
                direction = "CE"
                score = 65
                reasons = ["above VWAP", "EMA stack bull", "pullback bounce on EMA9"]
                if float(row.get("rsi", 50)) > 50:
                    score += 8
                    reasons.append("RSI>50")
        # Bearish setup: below VWAP, EMA stack bear, pullback touch to EMA9 with bearish close
        elif close < vwap - min_dist and ema_f < ema_s:
            if row["low"] <= ema_f <= row["high"] and close < row["open"]:
                direction = "PE"
                score = 65
                reasons = ["below VWAP", "EMA stack bear", "rejection at EMA9"]
                if float(row.get("rsi", 50)) < 50:
                    score += 8
                    reasons.append("RSI<50")
        return Signal(
            direction=direction,
            score=score,
            reasons=reasons,
            spot_target_pts=params["spot_target_pts"],
            spot_stop_pts=params["spot_stop_pts"],
        )
