"""SMC Liquidity Sweep + FVG Entry. Sweep equal-highs/lows, then enter on FVG retrace."""
from __future__ import annotations
import pandas as pd
from app.strategies.base import StrategyBase, Signal


class SMCLiquiditySweepFVG(StrategyBase):
    id = "smc_liquidity_sweep_fvg"
    name = "SMC Liquidity Sweep + FVG"
    version = "1.1.0"
    description = "Sweep of prior swing low/high → displacement → retrace into FVG. SMC concept."
    supported_modes = ["SCALP", "INTRADAY"]
    parameter_schema = {
        "sweep_lookback": {"type": "int", "min": 10, "max": 120, "default": 30},
        # Displacement is measured as a multiple of ATR (volatility-adaptive) so the
        # filter scales with the timeframe instead of a fixed % of price that is far
        # too large for 1-minute bars. 0.5*ATR ~= a genuine rejection wick.
        "min_displacement_atr": {"type": "float", "min": 0.1, "max": 3.0, "default": 0.5},
        # FVG confluence window (bars). A wider window makes the sweep+FVG
        # confluence realistic on 1m data instead of near-zero.
        "fvg_lookback_bars": {"type": "int", "min": 3, "max": 30, "default": 10},
        # Optional: require an FVG for confluence. Off by default on 1m where the
        # strict 3-condition confluence is extremely rare.
        "require_fvg": {"type": "bool", "default": False},
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

        # Volatility-adaptive displacement threshold. Fall back to a small fraction
        # of price only when ATR is unavailable (warm-up bars).
        min_disp_atr = float(params.get("min_displacement_atr", 0.5))
        if atr_val > 0:
            min_disp = min_disp_atr * atr_val
        else:
            min_disp = 0.0005 * close  # 0.05% fallback for warm-up only

        fvg_window = int(params.get("fvg_lookback_bars", 10))
        require_fvg = bool(params.get("require_fvg", False))

        def _has_fvg(direction: str) -> bool:
            recent = history["fvg"].iloc[max(0, i - fvg_window) : i + 1].tolist()
            return direction in recent

        # Bullish: sweep prior low then reclaim it with displacement.
        if low < prior_low and close > prior_low and (close - low) > min_disp:
            has_fvg = _has_fvg("UP")
            if has_fvg or not require_fvg:
                reasons = ["sweep low+reclaim", f"displacement {(close-low):.1f}pts (>{min_disp:.1f})"]
                # Confluence with an UP-FVG strengthens the score.
                score = 75 if has_fvg else 62
                if has_fvg:
                    reasons.append("recent UP-FVG")
                return Signal(
                    direction="CE",
                    score=score,
                    reasons=reasons,
                    spot_target_pts=params["spot_target_pts"],
                    spot_stop_pts=params["spot_stop_pts"],
                )
        # Bearish: sweep prior high then reject it with displacement.
        if high > prior_high and close < prior_high and (high - close) > min_disp:
            has_fvg = _has_fvg("DOWN")
            if has_fvg or not require_fvg:
                reasons = ["sweep high+reject", f"displacement {(high-close):.1f}pts (>{min_disp:.1f})"]
                score = 75 if has_fvg else 62
                if has_fvg:
                    reasons.append("recent DOWN-FVG")
                return Signal(
                    direction="PE",
                    score=score,
                    reasons=reasons,
                    spot_target_pts=params["spot_target_pts"],
                    spot_stop_pts=params["spot_stop_pts"],
                )
        return Signal(direction="NONE")

