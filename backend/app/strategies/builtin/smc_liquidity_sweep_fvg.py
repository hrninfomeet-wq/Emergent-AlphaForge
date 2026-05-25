"""SMC Liquidity Sweep + FVG Entry. Sweep equal-highs/lows, then enter on FVG retrace."""
from __future__ import annotations
import pandas as pd
from app.strategies.base import StrategyBase, Signal


class SMCLiquiditySweepFVG(StrategyBase):
    id = "smc_liquidity_sweep_fvg"
    name = "SMC Liquidity Sweep + FVG"
    version = "1.0.0"
    description = "Sweep of prior swing low/high → displacement → retrace into FVG. SMC concept."
    supported_modes = ["SCALP", "INTRADAY"]
    parameter_schema = {
        "sweep_lookback": {"type": "int", "min": 10, "max": 120, "default": 30},
        "min_displacement_pct": {"type": "float", "min": 0.05, "max": 1.0, "default": 0.10},
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 55},
        "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 8},
        "spot_target_pts": {"type": "float", "min": 10, "max": 200, "default": 30},
        "spot_stop_pts": {"type": "float", "min": 5, "max": 80, "default": 14},
    }

    def evaluate(self, row, prev, params, ctx) -> Signal:
        lookback = params["sweep_lookback"]
        history = ctx.get("history_df")
        if history is None or len(history) < lookback + 3:
            return Signal(direction="NONE", blockers=["insufficient history"])
        i = ctx.get("i", -1)
        if i < lookback + 2:
            return Signal(direction="NONE")
        window = history.iloc[i - lookback : i]
        prior_high = float(window["high"].max())
        prior_low = float(window["low"].min())
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        atr_val = float(row.get("atr", 0) or 0)
        min_disp = params["min_displacement_pct"] / 100 * close

        # Bullish: sweep low then reclaim AND there is a recent UP fvg
        if low < prior_low and close > prior_low and (close - low) > min_disp:
            fvg_recent = history["fvg"].iloc[max(0, i - 5) : i + 1].tolist()
            if "UP" in fvg_recent:
                return Signal(
                    direction="CE",
                    score=70,
                    reasons=["sweep low+reclaim", "recent UP-FVG", f"displacement {(close-low):.1f}pts"],
                    spot_target_pts=params["spot_target_pts"],
                    spot_stop_pts=params["spot_stop_pts"],
                )
        # Bearish: sweep high then reject AND recent DOWN fvg
        if high > prior_high and close < prior_high and (high - close) > min_disp:
            fvg_recent = history["fvg"].iloc[max(0, i - 5) : i + 1].tolist()
            if "DOWN" in fvg_recent:
                return Signal(
                    direction="PE",
                    score=70,
                    reasons=["sweep high+reject", "recent DOWN-FVG", f"displacement {(high-close):.1f}pts"],
                    spot_target_pts=params["spot_target_pts"],
                    spot_stop_pts=params["spot_stop_pts"],
                )
        return Signal(direction="NONE")
