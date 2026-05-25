"""Fibonacci Pullback — enter at 0.382/0.618 retracement of recent impulse with trend filter."""
from __future__ import annotations
import pandas as pd
from app.strategies.base import StrategyBase, Signal
from app.indicators import fibonacci_levels


class FibonacciPullback(StrategyBase):
    id = "fibonacci_pullback"
    name = "Fibonacci Pullback"
    version = "1.0.0"
    description = "Trend continuation entry at 0.382 / 0.618 retracement of recent impulse swing."
    supported_modes = ["SCALP", "INTRADAY"]
    parameter_schema = {
        "swing_lookback": {"type": "int", "min": 10, "max": 60, "default": 20},
        "fib_entry_low": {"type": "float", "min": 0.3, "max": 0.5, "default": 0.382},
        "fib_entry_high": {"type": "float", "min": 0.5, "max": 0.75, "default": 0.618},
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 55},
        "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 6},
        "spot_target_pts": {"type": "float", "min": 5, "max": 150, "default": 28},
        "spot_stop_pts": {"type": "float", "min": 3, "max": 80, "default": 14},
    }

    def evaluate(self, row, prev, params, ctx) -> Signal:
        history = ctx.get("history_df")
        i = ctx.get("i", -1)
        lookback = params["swing_lookback"]
        if history is None or i < lookback + 5:
            return Signal(direction="NONE")
        window = history.iloc[i - lookback : i]
        swing_hi = float(window["high"].max())
        swing_lo = float(window["low"].min())
        close = float(row["close"])
        ema_s = float(row.get("ema21", close))
        if pd.isna(ema_s):
            return Signal(direction="NONE")
        levels = fibonacci_levels(swing_hi, swing_lo)
        fib_lo, fib_hi = params["fib_entry_low"], params["fib_entry_high"]
        # bullish trend + retracement zone reached
        if ema_s > swing_lo + (swing_hi - swing_lo) * 0.5:  # uptrend bias
            zone_lo = swing_lo + (swing_hi - swing_lo) * (1 - fib_hi)
            zone_hi = swing_lo + (swing_hi - swing_lo) * (1 - fib_lo)
            if zone_lo <= close <= zone_hi and close > row["open"]:
                return Signal(
                    direction="CE",
                    score=60,
                    reasons=[
                        f"uptrend, retrace to {fib_lo:.3f}-{fib_hi:.3f}",
                        f"swing {swing_lo:.0f}-{swing_hi:.0f}",
                    ],
                    spot_target_pts=params["spot_target_pts"],
                    spot_stop_pts=params["spot_stop_pts"],
                )
        # bearish trend + retracement zone reached
        if ema_s < swing_hi - (swing_hi - swing_lo) * 0.5:  # downtrend bias
            zone_lo = swing_lo + (swing_hi - swing_lo) * fib_lo
            zone_hi = swing_lo + (swing_hi - swing_lo) * fib_hi
            if zone_lo <= close <= zone_hi and close < row["open"]:
                return Signal(
                    direction="PE",
                    score=60,
                    reasons=[
                        f"downtrend, retrace to {fib_lo:.3f}-{fib_hi:.3f}",
                        f"swing {swing_lo:.0f}-{swing_hi:.0f}",
                    ],
                    spot_target_pts=params["spot_target_pts"],
                    spot_stop_pts=params["spot_stop_pts"],
                )
        return Signal(direction="NONE")
