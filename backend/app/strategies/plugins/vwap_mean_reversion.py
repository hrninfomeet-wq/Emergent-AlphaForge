"""VWAP Mean Reversion — best in RANGING regimes. Fades extreme deviations from VWAP back to it."""
from __future__ import annotations
import pandas as pd
from app.strategies.base import StrategyBase, Signal


class VWAPMeanReversion(StrategyBase):
    id = "vwap_mean_reversion"
    name = "VWAP Mean Reversion"
    version = "1.0.0"
    description = "Fade extreme stretches from VWAP back to mean. Best in CHOP/range regimes; not for trends."
    supported_modes = ["SCALP", "INTRADAY"]
    parameter_schema = {
        "stretch_atr_mult": {"type": "float", "min": 1.0, "max": 4.0, "default": 2.0},
        "rsi_overbought": {"type": "float", "min": 65, "max": 85, "default": 72},
        "rsi_oversold": {"type": "float", "min": 15, "max": 35, "default": 28},
        "only_in_chop_regime": {"type": "bool", "default": True},
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 55},
        "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 6},
        "spot_target_pts": {"type": "float", "min": 5, "max": 80, "default": 18},
        "spot_stop_pts": {"type": "float", "min": 3, "max": 60, "default": 12},
    }

    def evaluate(self, row, prev, params, ctx) -> Signal:
        required = ["close", "vwap", "atr", "rsi"]
        if any(pd.isna(row.get(k)) for k in required):
            return Signal(direction="NONE", blockers=["warming up"])
        close = float(row["close"])
        vwap = float(row["vwap"])
        atr_val = float(row["atr"])
        rsi_val = float(row["rsi"])
        stretch = close - vwap
        thresh = params["stretch_atr_mult"] * atr_val
        blockers = []
        if params["only_in_chop_regime"]:
            regime = row.get("regime", "UNKNOWN")
            if regime not in ("CHOP", "VOLATILE_CHOP", "MIXED"):
                blockers.append(f"regime {regime} not chop")
        # Stretched far above VWAP + overbought → short (PE)
        if stretch > thresh and rsi_val > params["rsi_overbought"]:
            return Signal(
                direction="PE",
                score=62,
                reasons=[f"stretch +{stretch:.1f} > {thresh:.1f}", f"RSI {rsi_val:.0f} overbought"],
                blockers=blockers,
                spot_target_pts=params["spot_target_pts"],
                spot_stop_pts=params["spot_stop_pts"],
            )
        if stretch < -thresh and rsi_val < params["rsi_oversold"]:
            return Signal(
                direction="CE",
                score=62,
                reasons=[f"stretch {stretch:.1f} < -{thresh:.1f}", f"RSI {rsi_val:.0f} oversold"],
                blockers=blockers,
                spot_target_pts=params["spot_target_pts"],
                spot_stop_pts=params["spot_stop_pts"],
            )
        return Signal(direction="NONE")
