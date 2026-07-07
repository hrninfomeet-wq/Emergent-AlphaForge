"""Explosive Reversal — catch sharp reversals that can multiply OTM premiums.

Built around the user's observation: near expiry and in elevated volatility, when
price reverses hard off a support/resistance or round/psychological level — often
with RSI/MACD divergence and a rejection candle — OTM option premiums can run
2x-10x. This strategy scores that confluence and fires when it clears a
threshold.

Design philosophy (per user): FLEXIBLE, not a tight gate. Each factor ADDS to a
confluence score; a missing factor lowers the score but never vetoes the trade.
VIX and near-expiry are boosters, not hard filters. Keep it practical — a handful
of well-understood factors, not a black box.

Confluence factors (each contributes points):
  - Rejection candle at the reversal direction        (+20)
  - Price reacting at a support/resistance level       (+18)
  - Price near a round/psychological level             (+12)
  - Regular RSI divergence in the reversal direction   (+18)
  - Regular MACD-hist divergence in same direction     (+12)
  - Displacement: strong move vs ATR                   (+12)
  - VIX booster (row 'vix' >= threshold, if available) (+ up to 8)

Direction: a BULLISH confluence (reversal up off support) -> CE; BEARISH -> PE.
"""
from __future__ import annotations

import pandas as pd

from app.strategies.base import StrategyBase, Signal
from app.context_signals import (
    round_level_proximity,
    recent_sr_levels,
    nearest_sr_proximity,
    rsi_divergence,
    macd_divergence,
    reversal_candle,
)


class ExplosiveReversal(StrategyBase):
    id = "explosive_reversal"
    name = "Explosive Reversal (S/R + Divergence)"
    version = "1.0.0"
    description = (
        "Scores reversal confluence at support/resistance and round levels with "
        "RSI/MACD divergence and rejection candles. Designed for near-expiry, "
        "elevated-VIX OTM premium expansion. Flexible confluence score, not a hard gate."
    )
    supported_modes = ["SCALP", "INTRADAY"]
    parameter_schema = {
        "sr_lookback": {"type": "int", "min": 20, "max": 120, "default": 60},
        "divergence_lookback": {"type": "int", "min": 20, "max": 80, "default": 40},
        "displacement_atr_mult": {"type": "float", "min": 0.3, "max": 3.0, "default": 0.8},
        # VIX booster: when the bar carries a 'vix' value >= this, add points.
        # Does NOT block when VIX is low/absent (flexible, per spec).
        "vix_boost_threshold": {"type": "float", "min": 8.0, "max": 30.0, "default": 15.0},
        # Confluence score needed to fire. Lower = more signals.
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 50},
        "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 6},
        "spot_target_pts": {"type": "float", "min": 5, "max": 200, "default": 40},
        "spot_stop_pts": {"type": "float", "min": 3, "max": 100, "default": 18},
    }

    def evaluate(self, row, prev, params, ctx) -> Signal:
        history = ctx.get("history_df")
        i = ctx.get("i", -1)
        sr_lookback = int(params["sr_lookback"])
        if history is None or i < sr_lookback + 3:
            return Signal(direction="NONE")

        required = ["close", "high", "low", "open", "atr", "rsi", "macd_hist"]
        if any(pd.isna(row.get(k)) for k in required):
            return Signal(direction="NONE", blockers=["indicators warming up"])

        instrument = str(ctx.get("instrument") or "NIFTY").upper()
        close = float(row["close"])
        atr_val = float(row["atr"]) or 0.0

        # --- gather confluence factors ---
        candle = reversal_candle(row)                       # BULLISH | BEARISH | None
        sr_levels = recent_sr_levels(history, i, lookback=sr_lookback)
        sr = nearest_sr_proximity(close, sr_levels, atr_val)
        rnd = round_level_proximity(close, instrument, atr_val)
        rsi_div = rsi_divergence(history, i, lookback=int(params["divergence_lookback"]))
        macd_div = macd_divergence(history, i, lookback=int(params["divergence_lookback"]))

        # Displacement: how far this candle travelled vs ATR.
        candle_range = float(row["high"]) - float(row["low"])
        displacement = candle_range / atr_val if atr_val > 0 else 0.0
        disp_ok = displacement >= float(params["displacement_atr_mult"])

        # VIX booster (optional; row may carry 'vix' if joined upstream).
        vix_val = row.get("vix")
        vix_boost = 0
        try:
            if vix_val is not None and float(vix_val) >= float(params["vix_boost_threshold"]):
                # Scale 0..8 as VIX rises from threshold to threshold+10.
                over = min(10.0, float(vix_val) - float(params["vix_boost_threshold"]))
                vix_boost = int(round(8 * over / 10.0)) + 2  # min +2 once over threshold
        except (TypeError, ValueError):
            vix_boost = 0

        # --- score each direction ---
        ce_score, pe_score = 0, 0
        ce_reasons, pe_reasons = [], []

        # Reversal candle.
        if candle == "BULLISH":
            ce_score += 20; ce_reasons.append("bullish rejection candle")
        elif candle == "BEARISH":
            pe_score += 20; pe_reasons.append("bearish rejection candle")

        # S/R reaction: support favors CE (bounce), resistance favors PE (reject).
        if sr["is_near"]:
            if sr["kind"] == "support":
                ce_score += 18; ce_reasons.append(f"at support {sr['level']}")
            elif sr["kind"] == "resistance":
                pe_score += 18; pe_reasons.append(f"at resistance {sr['level']}")

        # Round-level proximity adds to whichever direction the candle/divergence implies.
        if rnd["is_near"]:
            ce_score += 12 if (candle == "BULLISH" or rsi_div == "BULLISH") else 0
            pe_score += 12 if (candle == "BEARISH" or rsi_div == "BEARISH") else 0
            if rnd["is_near"] and (candle or rsi_div):
                (ce_reasons if (candle == "BULLISH" or rsi_div == "BULLISH") else pe_reasons).append(
                    f"near round level {rnd['nearest_level']}"
                )

        # Divergence.
        if rsi_div == "BULLISH":
            ce_score += 18; ce_reasons.append("RSI bullish divergence")
        elif rsi_div == "BEARISH":
            pe_score += 18; pe_reasons.append("RSI bearish divergence")
        if macd_div == "BULLISH":
            ce_score += 12; ce_reasons.append("MACD bullish divergence")
        elif macd_div == "BEARISH":
            pe_score += 12; pe_reasons.append("MACD bearish divergence")

        # Displacement + VIX boost apply to the leading side only.
        lead = "CE" if ce_score >= pe_score else "PE"
        if disp_ok:
            if lead == "CE":
                ce_score += 12; ce_reasons.append(f"displacement {displacement:.1f}xATR")
            else:
                pe_score += 12; pe_reasons.append(f"displacement {displacement:.1f}xATR")
        if vix_boost > 0:
            if lead == "CE":
                ce_score += vix_boost; ce_reasons.append(f"VIX boost +{vix_boost}")
            else:
                pe_score += vix_boost; pe_reasons.append(f"VIX boost +{vix_boost}")

        ce_score = max(0, min(100, ce_score))
        pe_score = max(0, min(100, pe_score))
        direction = "CE" if ce_score >= pe_score else "PE"
        score = max(ce_score, pe_score)
        reasons = ce_reasons if direction == "CE" else pe_reasons

        threshold = int(params["signal_threshold"])
        if score < threshold or not reasons:
            return Signal(direction="NONE")

        return Signal(
            direction=direction,
            score=score,
            reasons=reasons,
            spot_target_pts=params["spot_target_pts"],
            spot_stop_pts=params["spot_stop_pts"],
        )
