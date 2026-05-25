"""Confluence Scalper — ported from reference repo's indexOptionConfluenceScalper.
Multi-factor scoring using EMA stack + VWAP + RSI + MACD + ADX + pullback bonus.
"""
from __future__ import annotations
import pandas as pd
from app.strategies.base import StrategyBase, Signal


class ConfluenceScalper(StrategyBase):
    id = "confluence_scalper"
    name = "Confluence Scalper"
    version = "1.0.0"
    description = "Multi-factor confluence: EMA stack + VWAP + RSI + MACD + ADX + pullback. Best in trending+expanding regimes."
    supported_modes = ["SCALP", "INTRADAY"]
    parameter_schema = {
        "ema_fast": {"type": "int", "min": 5, "max": 30, "default": 9},
        "ema_slow": {"type": "int", "min": 10, "max": 60, "default": 21},
        "rsi_bull_thr": {"type": "float", "min": 50, "max": 70, "default": 52},
        "rsi_bear_thr": {"type": "float", "min": 30, "max": 50, "default": 48},
        "signal_threshold": {"type": "int", "min": 40, "max": 95, "default": 62},
        "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 5},
        "spot_target_pts": {"type": "float", "min": 5, "max": 200, "default": 35},
        "spot_stop_pts": {"type": "float", "min": 3, "max": 100, "default": 18},
        "only_in_trend_regime": {"type": "bool", "default": True},
        "use_vwap_inhibit": {"type": "bool", "default": True},
        "vwap_inhibit_pts": {"type": "float", "min": 20, "max": 500, "default": 100},
    }

    def evaluate(self, row: pd.Series, prev: pd.Series, params, ctx) -> Signal:
        required = ["close", "ema9", "ema21", "rsi", "macd_hist", "vwap", "atr", "adx", "chop"]
        if any(pd.isna(row.get(k)) for k in required):
            return Signal(direction="NONE", blockers=["indicators warming up"])

        close, ema_f, ema_s = float(row["close"]), float(row["ema9"]), float(row["ema21"])
        rsi_val, macd_h = float(row["rsi"]), float(row["macd_hist"])
        macd_h_prev = float(prev.get("macd_hist", 0) or 0)
        vwap = float(row["vwap"])
        adx_val = float(row["adx"])

        ce_score = pe_score = 0
        ce_reasons, pe_reasons = [], []

        if close > ema_f and ema_f >= ema_s:
            ce_score += 22
            ce_reasons.append("trend bull (close>EMAf>=EMAs)")
        if close < ema_f and ema_f <= ema_s:
            pe_score += 22
            pe_reasons.append("trend bear (close<EMAf<=EMAs)")
        if close > vwap:
            ce_score += 12
            ce_reasons.append("above VWAP")
        if close < vwap:
            pe_score += 12
            pe_reasons.append("below VWAP")
        if rsi_val > params["rsi_bull_thr"]:
            ce_score += 14
            ce_reasons.append(f"RSI bull {rsi_val:.0f}")
        if rsi_val < params["rsi_bear_thr"]:
            pe_score += 14
            pe_reasons.append(f"RSI bear {rsi_val:.0f}")
        if macd_h > macd_h_prev and macd_h > 0:
            ce_score += 12
            ce_reasons.append("MACD hist rising")
        if macd_h < macd_h_prev and macd_h < 0:
            pe_score += 12
            pe_reasons.append("MACD hist falling")
        if adx_val >= 22:
            ce_score += 10
            pe_score += 10
        # Pullback bonus
        if row["low"] <= ema_f <= row["high"]:
            if close > row["open"]:
                ce_score += 10
                ce_reasons.append("pullback bounce EMAf")
            elif close < row["open"]:
                pe_score += 10
                pe_reasons.append("rejection EMAf")

        ce_score = max(0, min(100, ce_score))
        pe_score = max(0, min(100, pe_score))
        direction = "CE" if ce_score >= pe_score else "PE"
        score = max(ce_score, pe_score)
        reasons = ce_reasons if direction == "CE" else pe_reasons
        blockers = []

        if params["use_vwap_inhibit"]:
            if direction == "CE" and (close - vwap) >= params["vwap_inhibit_pts"]:
                blockers.append(f"VWAP stretch +{close-vwap:.0f}pts")
            if direction == "PE" and (vwap - close) >= params["vwap_inhibit_pts"]:
                blockers.append(f"VWAP stretch -{vwap-close:.0f}pts")
        if params["only_in_trend_regime"]:
            regime = row.get("regime", "UNKNOWN")
            if regime not in ("TREND", "TREND_EXPANDING"):
                blockers.append(f"regime {regime}")

        return Signal(
            direction=direction,
            score=score,
            reasons=reasons,
            blockers=blockers,
            spot_target_pts=params["spot_target_pts"],
            spot_stop_pts=params["spot_stop_pts"],
        )
