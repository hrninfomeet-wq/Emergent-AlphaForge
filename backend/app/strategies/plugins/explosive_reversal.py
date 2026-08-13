"""Explosive Reversal — catch sharp reversals that can multiply OTM premiums.

Built around the user's observation: near expiry and in elevated volatility, when
price reverses hard off a support/resistance or round/psychological level — often
with RSI/MACD divergence and a rejection candle — OTM option premiums can run
2x-10x. This strategy scores that confluence and fires when it clears a
threshold.

Design philosophy (per user): FLEXIBLE, not a tight gate. Each factor ADDS to a
confluence score; a missing factor lowers the score but never vetoes the trade.
Keep it practical — a handful of well-understood factors, not a black box.

Confluence factors (each contributes points):
  - Rejection candle at the reversal direction        (+20)
  - Price reacting at a support/resistance level       (+18)
  - Price near a round/psychological level             (+12)
  - Regular RSI divergence in the reversal direction   (+18)
  - Regular MACD-hist divergence in same direction     (+12)
  - Displacement: strong move vs ATR                   (+12)

Direction: a BULLISH confluence (reversal up off support) -> CE; BEARISH -> PE.

NOT VIX-aware — and do NOT "restore" that. An earlier revision carried a
`vix_boost_threshold` parameter whose branch read a `vix` value off the bar. No
engine path has ever produced that column: neither `precompute_all_indicators`
(app/indicators.py) nor `indicator_groups` creates it, and the feature registry
(`app/features/`) is pure-pandas by contract so it cannot fetch VIX from the
warehouse either. The knob was therefore tunable, persisted into saved
presets and optimizer results, and changed nothing — burning search budget while
implying a volatility-awareness the strategy did not have (same dead-knob class
as `early_stop`, see docs/OPTIMIZER_VERDICT_2026-07.md). Knob and branch removed.

India VIX is real data — `candles_1m` under instrument `INDIAVIX`, with as-of
join helpers in `app/vix.py` — but it only becomes readable per bar once it is
joined into the frame at LOAD time, which is a separate capability task
(docs/AGENT_TODO.md item 5F). Note also that VIX history starts 2025-06-09 while
spot starts 2024-11-25, so a per-bar VIX column is structurally absent for the
first ~133 sessions of the warehouse; any future booster must degrade loudly
there rather than silently scoring zero. `tests/test_strategy_column_contract.py`
now fails any strategy that reads a column the engine does not build.
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
    build_pivot_index,
    recent_sr_levels_indexed,
    rsi_divergence_indexed,
    macd_divergence_indexed,
)

#: ctx key holding the prebuilt swing-pivot index (see session_precompute).
_PIVOT_CTX_KEY = "_explosive_reversal_pivots"


class ExplosiveReversal(StrategyBase):
    id = "explosive_reversal"
    name = "Explosive Reversal (S/R + Divergence)"
    version = "1.0.0"
    description = (
        "Scores reversal confluence at support/resistance and round levels with "
        "RSI/MACD divergence and rejection candles. Aimed at the near-expiry, "
        "high-volatility regime where OTM premiums expand — but scored from price "
        "structure alone (the engine builds no per-bar VIX column). Flexible "
        "confluence score, not a hard gate."
    )
    supported_modes = ["SCALP", "INTRADAY"]
    parameter_schema = {
        "sr_lookback": {"type": "int", "min": 20, "max": 120, "default": 60},
        "divergence_lookback": {"type": "int", "min": 20, "max": 80, "default": 40},
        "displacement_atr_mult": {"type": "float", "min": 0.3, "max": 3.0, "default": 0.8},
        # Confluence score needed to fire. Lower = more signals.
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 50},
        "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 6},
        "spot_target_pts": {"type": "float", "min": 5, "max": 200, "default": 40},
        "spot_stop_pts": {"type": "float", "min": 3, "max": 100, "default": 18},
    }

    def session_precompute(self, df, params):
        """Build the swing-pivot index ONCE per frame.

        The three confluence lookups below (S/R levels, RSI and MACD divergence)
        only ever consult swing pivots, but their DataFrame form re-slices a
        trailing window on every bar. That was ~28s of a single SENSEX backtest —
        833,700 DataFrame.__getitem__ calls — and it multiplied across every trial,
        every re-rank candidate and every survival fold. Precomputing the pivot
        positions turns each per-bar lookup into two binary searches.

        Returns {} when the frame cannot support it, in which case evaluate()
        falls back to the DataFrame helpers and behaviour is exactly as before."""
        px = build_pivot_index(df)
        return {_PIVOT_CTX_KEY: px} if px is not None else {}

    def evaluate(self, row, prev, params, ctx) -> Signal:
        history = ctx.get("history_df")
        i = ctx.get("i", -1)
        # Present when session_precompute ran (backtest + paper/live both call it).
        # Absent -> the DataFrame helpers below, i.e. the original path.
        px = ctx.get(_PIVOT_CTX_KEY)
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
        _dl = int(params["divergence_lookback"])
        if px is not None:
            sr_levels = recent_sr_levels_indexed(px, i, lookback=sr_lookback)
            rsi_div = rsi_divergence_indexed(px, i, lookback=_dl)
            macd_div = macd_divergence_indexed(px, i, lookback=_dl)
        else:
            sr_levels = recent_sr_levels(history, i, lookback=sr_lookback)
            rsi_div = rsi_divergence(history, i, lookback=_dl)
            macd_div = macd_divergence(history, i, lookback=_dl)
        sr = nearest_sr_proximity(close, sr_levels, atr_val)
        rnd = round_level_proximity(close, instrument, atr_val)

        # Displacement: how far this candle travelled vs ATR.
        candle_range = float(row["high"]) - float(row["low"])
        displacement = candle_range / atr_val if atr_val > 0 else 0.0
        disp_ok = displacement >= float(params["displacement_atr_mult"])

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

        # Displacement applies to the leading side only.
        lead = "CE" if ce_score >= pe_score else "PE"
        if disp_ok:
            if lead == "CE":
                ce_score += 12; ce_reasons.append(f"displacement {displacement:.1f}xATR")
            else:
                pe_score += 12; pe_reasons.append(f"displacement {displacement:.1f}xATR")

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
