"""Adaptive Regime Scalper (ARS) — direction-timing edge (flagship).

Soft-blends a trend module and a fade module by the Variance-Ratio regime score,
biased by the CPR day-type. Trend-ride when VR>1 (Supertrend + VWAP/CPR reclaim),
fade VWAP-2sigma/CPR edges when VR<1, stand aside near VR~1. Built on
AdaptiveStrategyBase.
"""
from __future__ import annotations
import pandas as pd
from app.strategies.adaptive_base import AdaptiveStrategyBase


class AdaptiveRegimeScalper(AdaptiveStrategyBase):
    id = "adaptive_regime_scalper"
    name = "Adaptive Regime Scalper"
    version = "1.0.0"
    description = ("Variance-Ratio soft-blend regime switch: trend-ride (Supertrend + "
                   "VWAP/CPR reclaim) when VR>1, fade VWAP-2sigma/CPR edges when VR<1, "
                   "biased by CPR day-type. Direction-timing edge.")
    extra_params = {
        "dead_band": {"type": "float", "min": 0.05, "max": 0.4, "default": 0.15},
        # vr_*/st_* tune the precomputed regime_score/supertrend columns via the optimizer's
        # INDICATOR_PARAM_KEYS recompute (consumed by precompute_all_indicators, not read here).
        "vr_q": {"type": "int", "min": 2, "max": 10, "default": 4},
        "vr_lookback": {"type": "int", "min": 40, "max": 150, "default": 90},
        "st_period": {"type": "int", "min": 5, "max": 20, "default": 10},
        "st_mult": {"type": "float", "min": 1.5, "max": 4.0, "default": 3.0},
    }

    def _core_signal(self, row, prev, params, ctx):
        for k in ("regime_score", "st_dir", "vwap", "vwap_l2", "vwap_u2", "close", "cpr_tc", "cpr_bc"):
            if pd.isna(row.get(k)):
                return ("NONE", 0, [], ["warming up"], "momentum")
        rs = float(row["regime_score"])
        dt = str(row.get("day_type", "NEUTRAL"))
        if abs(rs) < float(params["dead_band"]) and dt == "NEUTRAL":
            return ("NONE", 0, [], ["random walk / stand aside"], "momentum")
        bias = 1.2 if dt == "TREND" else (0.8 if dt == "RANGE" else 1.0)
        w_trend = max(0.0, rs) * bias
        w_fade = max(0.0, -rs) / bias
        close, vwap = float(row["close"]), float(row["vwap"])
        st = int(row["st_dir"])
        cands = []  # (weighted_score, direction, mode, kind)
        if st > 0 and close > vwap and close > float(row["cpr_tc"]):
            cands.append((w_trend * 60.0, "CE", "momentum", "trend"))
        elif st < 0 and close < vwap and close < float(row["cpr_bc"]):
            cands.append((w_trend * 60.0, "PE", "momentum", "trend"))
        if close <= float(row["vwap_l2"]):
            cands.append((w_fade * 60.0, "CE", "reversion", "fade"))
        elif close >= float(row["vwap_u2"]):
            cands.append((w_fade * 60.0, "PE", "reversion", "fade"))
        cands = [c for c in cands if c[0] > 0]
        if not cands:
            return ("NONE", 0, [], ["no weighted setup"], "momentum")
        cands.sort(key=lambda c: c[0], reverse=True)
        wscore, direction, mode, kind = cands[0]
        score = int(min(100, 50 + wscore))
        return (direction, score, [f"{kind} rs={rs:.2f} day={dt}"], [], mode)
