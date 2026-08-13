"""SENSEX Explosive Reversal — the reversal-confluence edge, built for ONE index.

Purpose-built for SENSEX rather than generalized. `explosive_reversal` is tuned
for NIFTY and `explosive_reversal_atr` deliberately stays instrument-agnostic;
this one is allowed to assume SENSEX and exploit what the data says about it.

WHAT THE SENSEX DATA ACTUALLY SAYS (1 year, 246 sessions, 91,928 bars)
---------------------------------------------------------------------
Measured forward 45-bar excursion, causally (trailing windows only — a
session-wide percentile would be look-ahead, and the first cut of this analysis
made exactly that mistake):

    trailing-240 ATR rank   forward move (ATR)   forward move (points)
    low   0.00-0.33                 5.62                 128.8
    high  0.66-1.00                 4.33                 172.2

Read in ATR the low-volatility bucket looks far better; read in POINTS it is
clearly worse. The apparent "compression edge" is a denominator effect — low-ATR
bars do not travel further, they merely travel more ATR-multiples.

That distinction decides the design. An option buyer is paid for ABSOLUTE index
points (delta x move), not for ATR multiples, which argues for high volatility.
But high volatility also makes the premium more expensive up front, which argues
the other way. The net is genuinely an empirical question, so the volatility
regime is exposed as a SEARCHABLE band (`min_atr_rank` / `max_atr_rank`) instead
of being hardcoded to either belief. That is the honest use of the optimizer.

DESIGN DECISIONS, AND WHY
-------------------------
1. Confluence scoring is inherited unchanged from the proven detector (S/R
   reaction, RSI/MACD divergence, rejection candle, round level, displacement).
   Measured at NIFTY-equivalent geometry it already reaches a 45.1% win rate on
   SENSEX, so the entry logic was never the problem.

2. Exits are ATR multiples with SENSEX-sized ranges. The one config that passed
   the survival gate on SENSEX wanted a 2.8 ATR stop against a 23 ATR target —
   rare, high-conviction, let-it-run. Option buying is a convexity trade, so the
   target range reaches 30 ATR rather than stopping at the parent's cap.

3. `stop_basis_blend` sizes the stop between the CURRENT bar's ATR and a trailing
   BASELINE ATR. Entering during compression with a stop sized off the compressed
   bar reproduces the original failure in miniature — a stop inside the noise of
   the expansion that follows. Blending toward the baseline decouples "when to
   enter" from "how much room to give it".

4. `min_session_bars` gates how far into the session a signal may fire. Forward
   excursion rises through the day in the raw table, but part of that is the
   15:15-15:30 closing-auction freeze leaking into a 45-bar horizon, so it is a
   tunable, not a baked-in belief.

DELIBERATELY NOT INCLUDED
-------------------------
Days-to-expiry. The thesis behind this family is that near-expiry premiums
multiply, and SENSEX expiry sits mostly on Thursday (63) with a mid-window shift
to Tuesday (34) — so day-of-week is not a usable proxy either. But no per-bar DTE
column exists in the spot frame, and inventing one from the weekday would be a
knob that looks meaningful and is not (the same dead-knob class as the removed
`vix_boost_threshold`). It needs a load-time join first.
"""
from __future__ import annotations

import numpy as np
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

_PIVOT_CTX_KEY = "_sensex_er_pivots"
_GRID_CTX_KEY = "_sensex_er_grid"
_REGIME_CTX_KEY = "_sensex_er_regime"


class SensexExplosiveReversal(StrategyBase):
    id = "sensex_explosive_reversal"
    name = "SENSEX Explosive Reversal"
    version = "1.0.0"
    supported_instruments = ["SENSEX"]
    supported_modes = ["SCALP", "INTRADAY"]
    description = (
        "SENSEX-only reversal-confluence detector. Same proven S/R + divergence + "
        "rejection-candle scoring, with exits in ATR multiples sized against a "
        "trailing baseline, and a searchable volatility-regime band — because on "
        "SENSEX low-ATR bars travel more ATR-multiples but FEWER points, and an "
        "option buyer is paid in points."
    )
    parameter_schema = {
        # --- confluence core (inherited, unchanged) ---
        "sr_lookback": {"type": "int", "min": 20, "max": 120, "default": 60},
        "divergence_lookback": {"type": "int", "min": 20, "max": 80, "default": 45},
        "displacement_atr_mult": {"type": "float", "min": 0.3, "max": 3.0, "default": 1.2},
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 60},
        "cooldown_bars": {"type": "int", "min": 1, "max": 40, "default": 15},
        # --- exits: ATR-denominated, SENSEX-sized ---
        # The survivor wanted 2.8 stop / 23 target. Floor the stop at 1.0 ATR: below
        # that it sits inside one bar's noise, which is what sank the parent here.
        "spot_stop_atr": {"type": "float", "min": 1.0, "max": 8.0, "default": 2.8},
        "spot_target_atr": {"type": "float", "min": 4.0, "max": 30.0, "default": 14.0},
        # 0 = size the stop off THIS bar's ATR, 1 = off the trailing baseline.
        "stop_basis_blend": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.5},
        # --- volatility regime: searchable, not assumed ---
        "atr_rank_window": {"type": "int", "min": 60, "max": 300, "default": 240},
        "min_atr_rank": {"type": "float", "min": 0.0, "max": 0.6, "default": 0.0},
        "max_atr_rank": {"type": "float", "min": 0.4, "max": 1.0, "default": 1.0},
        # --- session structure ---
        "min_session_bars": {"type": "int", "min": 0, "max": 180, "default": 15},
        # 0.146% of ~78,500 is the 100-grid the SENSEX search actually chose.
        "round_step_pct": {"type": "float", "min": 0.05, "max": 0.80, "default": 0.15},
    }

    # ── precompute ───────────────────────────────────────────────────────────
    def session_precompute(self, df, params):
        """Everything here is CAUSAL and computed once per frame.

        `rolling(...).rank(pct=True)` ranks each ATR within its own TRAILING
        window, so no bar can see its future. A session-wide percentile would be
        look-ahead and would have manufactured the edge this strategy is built on
        — the first version of the supporting analysis did exactly that, and the
        effect reversed once it was corrected.
        """
        out = {}
        px = build_pivot_index(df)
        if px is not None:
            out[_PIVOT_CTX_KEY] = px
        try:
            ref = float(df["close"].median())
            if ref > 0:
                out[_GRID_CTX_KEY] = derive_round_grid(ref, float(params.get("round_step_pct") or 0.15))
        except Exception:
            pass
        try:
            w = int(params.get("atr_rank_window") or 240)
            atr = pd.to_numeric(df["atr"], errors="coerce")
            rank = atr.rolling(w, min_periods=w).rank(pct=True)
            base = atr.rolling(w, min_periods=w).median()
            # Bars since this session's open — no cross-session leakage.
            if "session_date" in df:
                bars_in = df.groupby("session_date").cumcount()
            else:
                bars_in = pd.Series(np.arange(len(df)), index=df.index)
            out[_REGIME_CTX_KEY] = {
                "rank": rank.to_numpy(dtype="float64"),
                "base": base.to_numpy(dtype="float64"),
                "bars_in": np.asarray(bars_in, dtype="int64"),
            }
        except Exception:
            pass
        return out

    # ── scoring (inherited logic, verbatim) ──────────────────────────────────
    def confluence(self, row, params, ctx):
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
        grid = ctx.get(_GRID_CTX_KEY) or derive_round_grid(close, float(params["round_step_pct"]))
        rnd = round_level_proximity_grid(close, grid[0], grid[1], atr_val)

        displacement = (float(row["high"]) - float(row["low"])) / atr_val if atr_val > 0 else 0.0
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
            if candle or rsi_div:
                (ce_reasons if (candle == "BULLISH" or rsi_div == "BULLISH") else pe_reasons).append(
                    f"near round level {rnd['nearest_level']}")

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

    # ── entry ────────────────────────────────────────────────────────────────
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
            return Signal(direction="NONE", blockers=["no ATR to size exits"])

        reg = ctx.get(_REGIME_CTX_KEY)
        atr_basis = atr_val
        if reg is not None and 0 <= i < len(reg["rank"]):
            if int(reg["bars_in"][i]) < int(params["min_session_bars"]):
                return Signal(direction="NONE", blockers=["too early in session"])
            rank = reg["rank"][i]
            if not np.isnan(rank):
                # Warmup bars (NaN rank) are NOT silently admitted as if they passed —
                # they simply carry no regime evidence, so the band cannot judge them.
                if not (float(params["min_atr_rank"]) <= rank <= float(params["max_atr_rank"])):
                    return Signal(direction="NONE", blockers=[f"atr_rank {rank:.2f} outside band"])
            base = reg["base"][i]
            if not np.isnan(base) and base > 0:
                blend = float(params["stop_basis_blend"])
                atr_basis = (1.0 - blend) * atr_val + blend * float(base)

        direction, score, reasons = self.confluence(row, params, ctx)
        if score < int(params["signal_threshold"]) or not reasons:
            return Signal(direction="NONE")

        return Signal(
            direction=direction,
            score=score,
            reasons=reasons,
            # Target rides the CURRENT bar's ATR (it is the move being predicted);
            # the stop rides the blended basis so a compressed entry bar cannot
            # produce a noise-width stop.
            spot_target_pts=atr_val * float(params["spot_target_atr"]),
            spot_stop_pts=atr_basis * float(params["spot_stop_atr"]),
        )
