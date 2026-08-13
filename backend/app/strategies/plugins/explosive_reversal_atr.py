"""Explosive Reversal (ATR-scaled) — the same confluence edge, expressed in units
that survive a change of instrument.

WHY THIS EXISTS
---------------
`explosive_reversal` is profitable on NIFTY and deeply negative on SENSEX, and
the measured reason is not the edge — it is the UNITS.

Both indices carry nearly identical relative volatility (median 1-minute range
0.0344% NIFTY vs 0.0347% SENSEX); SENSEX simply runs at ~3.2x the point scale
(78,500 vs 24,400). But the parent expresses exits in ABSOLUTE POINTS, bounded
`spot_stop_pts` <= 100 and `spot_target_pts` <= 200. Measured against each
index's own ATR:

    NIFTY winner    stop 62.1 pts = 6.49 ATR   target 141.6 pts = 14.8 ATR
    SENSEX winner   stop 18.4 pts = 0.59 ATR   target 173.9 pts =  5.6 ATR
    SENSEX ceiling  stop        <= 3.20 ATR    target        <=  6.4 ATR

So the geometry that works on NIFTY is not merely unfound on SENSEX — it is
UNREACHABLE, capped at roughly half the stop it needs. Forced into sub-ATR
stops, the search lands inside the noise: 0.59 ATR is smaller than one median
bar, 87% of trades die at STOP, win rate 13.2% against NIFTY's 45.0%, and 2,242
round trips of option spread turn a positive spot curve into -845,541 net rupees.

Re-measured at NIFTY-equivalent geometry the SENSEX edge is intact — 6.49 ATR
stop / 10 ATR target gives 468 trades at a 45.1% win rate. The edge transfers.
Only the units did not.

WHAT CHANGED (and only this)
----------------------------
1. Exits are ATR MULTIPLES, converted per bar. Self-scaling across instruments
   and across volatility regimes, so the same numbers mean the same thing on a
   24,000 index and a 78,500 one.
2. The round-number grid is derived from price as a PERCENT of it rather than
   read from a hardcoded per-instrument table. That table gives NIFTY 100/50 but
   SENSEX 500/500 — coarser in percentage terms and with both tiers identical, so
   SENSEX silently loses the two-grid comparison. `round_step_pct` makes the
   granularity a searchable parameter instead of a constant someone has to
   remember to add per instrument.

The confluence SCORING is deliberately unchanged, factor for factor and weight
for weight — otherwise this could not be compared against the parent honestly.
`tests/test_explosive_reversal_atr.py` pins the scoring against the parent's on a
real frame, so the copy cannot drift.

`explosive_reversal` itself is untouched: its NIFTY results stand.
"""
from __future__ import annotations

import pandas as pd

from app.strategies.base import StrategyBase, Signal
from app.context_signals import (
    recent_sr_levels,
    nearest_sr_proximity,
    rsi_divergence,
    macd_divergence,
    reversal_candle,
    derive_round_grid,
    round_level_proximity_grid,
    build_pivot_index,
    recent_sr_levels_indexed,
    rsi_divergence_indexed,
    macd_divergence_indexed,
)

#: ctx key holding the prebuilt swing-pivot index (see session_precompute).
_PIVOT_CTX_KEY = "_explosive_reversal_atr_pivots"
#: ctx key holding the (major, minor) round-number grid for the whole run.
_GRID_CTX_KEY = "_explosive_reversal_atr_grid"


class ExplosiveReversalATR(StrategyBase):
    id = "explosive_reversal_atr"
    name = "Explosive Reversal (ATR-scaled)"
    version = "1.0.0"
    description = (
        "Instrument-agnostic twin of explosive_reversal: identical reversal-"
        "confluence scoring, but exits sized in ATR multiples and round levels "
        "derived as a percent of price. Built because the parent's absolute-point "
        "exits are calibrated for a ~24,000 index and cannot express a workable "
        "stop on SENSEX at ~78,500."
    )
    supported_modes = ["SCALP", "INTRADAY"]
    parameter_schema = {
        # --- confluence: identical to the parent ---
        "sr_lookback": {"type": "int", "min": 20, "max": 120, "default": 60},
        "divergence_lookback": {"type": "int", "min": 20, "max": 80, "default": 40},
        "displacement_atr_mult": {"type": "float", "min": 0.3, "max": 3.0, "default": 0.8},
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 50},
        "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 6},
        # --- what actually changed ---
        # Ranges COVER the NIFTY winner (6.49 ATR stop / 14.8 ATR target), which the
        # parent's point bounds cannot reach on SENSEX. The floor stays above 0.5 ATR
        # because a sub-ATR stop is inside one bar's noise — the trap that produced
        # the 13.2% win rate.
        "spot_stop_atr": {"type": "float", "min": 0.5, "max": 12.0, "default": 3.0},
        "spot_target_atr": {"type": "float", "min": 1.0, "max": 25.0, "default": 8.0},
        # 0.41 reproduces NIFTY's existing 100/50 grid exactly (100/24,400), so the
        # default is a no-op there and the optimizer discovers the right granularity
        # elsewhere rather than inheriting a guess.
        "round_step_pct": {"type": "float", "min": 0.1, "max": 1.0, "default": 0.41},
    }

    def session_precompute(self, df, params):
        """Build the swing-pivot index and the round-number grid once per frame.

        The grid is derived HERE, from a reference price for the run, and not per
        bar. Per-bar derivation looks equivalent and is not: 0.41% of prices either
        side of 24,400 straddles 100, so the grid would flip between 100 and 50 as
        price drifts and the same level would score differently on different days.
        (Caught by test_scoring_matches_the_parent_bar_for_bar, which is exactly
        what that test is for.)"""
        out = {}
        px = build_pivot_index(df)
        if px is not None:
            out[_PIVOT_CTX_KEY] = px
        try:
            ref = float(df["close"].median())
            if ref > 0:
                out[_GRID_CTX_KEY] = derive_round_grid(ref, float(params.get("round_step_pct") or 0.41))
        except Exception:
            pass
        return out

    def confluence(self, row, params, ctx):
        """Score the reversal confluence. Returns (direction, score, reasons).

        Kept as its own method so the equivalence test can compare it against the
        parent's scoring directly, without the exit change getting in the way."""
        history = ctx.get("history_df")
        i = ctx.get("i", -1)
        px = ctx.get(_PIVOT_CTX_KEY)
        sr_lookback = int(params["sr_lookback"])
        close = float(row["close"])
        atr_val = float(row["atr"]) or 0.0

        candle = reversal_candle(row)
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
        # THE round-level difference: a price-derived grid, not a per-instrument
        # constant. Fixed for the run (session_precompute); the fallback derives it
        # from this bar only when precompute could not run, e.g. a single-bar path.
        grid = ctx.get(_GRID_CTX_KEY) or derive_round_grid(close, float(params["round_step_pct"]))
        rnd = round_level_proximity_grid(close, grid[0], grid[1], atr_val)

        candle_range = float(row["high"]) - float(row["low"])
        displacement = candle_range / atr_val if atr_val > 0 else 0.0
        disp_ok = displacement >= float(params["displacement_atr_mult"])

        ce_score, pe_score = 0, 0
        ce_reasons, pe_reasons = [], []

        if candle == "BULLISH":
            ce_score += 20; ce_reasons.append("bullish rejection candle")
        elif candle == "BEARISH":
            pe_score += 20; pe_reasons.append("bearish rejection candle")

        if sr["is_near"]:
            if sr["kind"] == "support":
                ce_score += 18; ce_reasons.append(f"at support {sr['level']}")
            elif sr["kind"] == "resistance":
                pe_score += 18; pe_reasons.append(f"at resistance {sr['level']}")

        if rnd["is_near"]:
            ce_score += 12 if (candle == "BULLISH" or rsi_div == "BULLISH") else 0
            pe_score += 12 if (candle == "BEARISH" or rsi_div == "BEARISH") else 0
            if rnd["is_near"] and (candle or rsi_div):
                (ce_reasons if (candle == "BULLISH" or rsi_div == "BULLISH") else pe_reasons).append(
                    f"near round level {rnd['nearest_level']}"
                )

        if rsi_div == "BULLISH":
            ce_score += 18; ce_reasons.append("RSI bullish divergence")
        elif rsi_div == "BEARISH":
            pe_score += 18; pe_reasons.append("RSI bearish divergence")
        if macd_div == "BULLISH":
            ce_score += 12; ce_reasons.append("MACD bullish divergence")
        elif macd_div == "BEARISH":
            pe_score += 12; pe_reasons.append("MACD bearish divergence")

        lead = "CE" if ce_score >= pe_score else "PE"
        if disp_ok:
            if lead == "CE":
                ce_score += 12; ce_reasons.append(f"displacement {displacement:.1f}xATR")
            else:
                pe_score += 12; pe_reasons.append(f"displacement {displacement:.1f}xATR")

        ce_score = max(0, min(100, ce_score))
        pe_score = max(0, min(100, pe_score))
        direction = "CE" if ce_score >= pe_score else "PE"
        return direction, max(ce_score, pe_score), (ce_reasons if direction == "CE" else pe_reasons)

    def evaluate(self, row, prev, params, ctx) -> Signal:
        history = ctx.get("history_df")
        i = ctx.get("i", -1)
        sr_lookback = int(params["sr_lookback"])
        if history is None or i < sr_lookback + 3:
            return Signal(direction="NONE")

        required = ["close", "high", "low", "open", "atr", "rsi", "macd_hist"]
        if any(pd.isna(row.get(k)) for k in required):
            return Signal(direction="NONE", blockers=["indicators warming up"])

        atr_val = float(row["atr"]) or 0.0
        if atr_val <= 0:
            # Exits are ATR-denominated, so a zero/absent ATR cannot size a trade.
            # Refuse rather than fall back to some fixed point value that would mean
            # something different on every instrument — the exact bug this exists to fix.
            return Signal(direction="NONE", blockers=["no ATR to size exits"])

        direction, score, reasons = self.confluence(row, params, ctx)
        threshold = int(params["signal_threshold"])
        if score < threshold or not reasons:
            return Signal(direction="NONE")

        return Signal(
            direction=direction,
            score=score,
            reasons=reasons,
            spot_target_pts=atr_val * float(params["spot_target_atr"]),
            spot_stop_pts=atr_val * float(params["spot_stop_atr"]),
        )
