"""Squeeze Expansion Breakout (SEB) — variance-timing edge.

Buys the direction of a Bollinger-in-Keltner squeeze RELEASE (volatility
compression -> expansion) with an acceleration + VWAP confirm. Long-gamma
ignition: you owned cheap optionality through the coil and ride the expansion.
Built on AdaptiveStrategyBase (time-gate + speed-confirm + ATR exits).
"""
from __future__ import annotations
import pandas as pd
from app.strategies.adaptive_base import AdaptiveStrategyBase


class SqueezeExpansionBreakout(AdaptiveStrategyBase):
    id = "squeeze_expansion_breakout"
    name = "Squeeze Expansion Breakout"
    version = "1.0.0"
    description = ("Long-gamma ignition: buy the direction of a Bollinger-in-Keltner "
                   "squeeze release with acceleration + VWAP confirm. Variance-timing edge.")
    extra_params = {
        "min_coil_bars": {"type": "int", "min": 2, "max": 20, "default": 6},
        "bb_len": {"type": "int", "min": 10, "max": 30, "default": 20},
        "bb_mult": {"type": "float", "min": 1.5, "max": 2.5, "default": 2.0},
        "kc_len": {"type": "int", "min": 10, "max": 30, "default": 20},
        "kc_atr_mult": {"type": "float", "min": 1.0, "max": 2.0, "default": 1.5},
        "sqz_mom_len": {"type": "int", "min": 10, "max": 30, "default": 20},
    }

    def _core_signal(self, row, prev, params, ctx):
        for k in ("squeeze_on", "squeeze_fire", "sqz_mom", "vwap", "close"):
            if pd.isna(row.get(k)):
                return ("NONE", 0, [], ["warming up"], "momentum")
        coil = self._coil_age(ctx)
        fired = bool(row["squeeze_fire"]) or (
            coil >= int(params["min_coil_bars"]) and not bool(row["squeeze_on"]))
        if not fired:
            return ("NONE", 0, [], ["no squeeze fire"], "momentum")
        mom = float(row["sqz_mom"])
        mom_prev = float(prev.get("sqz_mom")) if (prev is not None and not pd.isna(prev.get("sqz_mom"))) else 0.0
        close, vwap = float(row["close"]), float(row["vwap"])
        score = 55
        reasons = ["squeeze fired"]
        if coil >= int(params["min_coil_bars"]):
            score += min(15, coil)
            reasons.append(f"coil={coil}")
        if bool(row.get("nr7")):
            score += 8
            reasons.append("NR7 prior day")
        if mom > 0 and close > vwap and mom >= mom_prev:
            return ("CE", min(100, score + 10), reasons + ["momentum up"], [], "momentum")
        if mom < 0 and close < vwap and mom <= mom_prev:
            return ("PE", min(100, score + 10), reasons + ["momentum down"], [], "momentum")
        return ("NONE", 0, reasons, ["fire without aligned direction"], "momentum")

    @staticmethod
    def _coil_age(ctx) -> int:
        hist = ctx.get("history_df") if ctx else None
        i = ctx.get("i") if ctx else None
        if hist is None or i is None or "squeeze_on" not in getattr(hist, "columns", []):
            return 0
        col = hist["squeeze_on"]
        coil, j = 0, int(i) - 1
        while j >= 0 and bool(col.iloc[j]):
            coil += 1
            j -= 1
        return coil
