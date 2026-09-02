"""SENSEX VWAP Mean Reversion — VWAP displacement, built in SENSEX-native units.

READ THIS BEFORE OPTIMIZING IT
------------------------------
This plugin is CAPABILITY, not a validated edge. It ships on the same basis as
`premium_momentum`: the edge gate was run first, it FAILED, and the operator
chose to have the instrument anyway. The numbers below are the reason, kept here
so no future session mistakes "it exists" for "it works".

Measured on 441 SENSEX sessions (2024-11-25 .. 2026-09-02): forward 45-bar
excursion, CAS-frozen bars excluded, entries 09:30-14:50, every rule compared
against the SAME-DTE baseline — does it beat doing nothing on that day type?

    fade (this strategy's namesake premise), lift over baseline:
        DTE0 PE  n=625  +0.319   but 1st half +0.617, 2nd half -0.040
        DTE1 PE  n=772  +0.229   but 1st half +0.505, 2nd half -0.063
        DTE1 CE  n=790  -0.268
        DTE2 PE  n=843  -0.186
        DTE3 PE  n=734  -0.208

The only positive cells are DTE0/DTE1 puts and their entire lift lives in the
first half of the window. SENSEX fell 4.7% across it, which manufactures a
put-favourable base rate. Raising the stretch threshold 1.5 -> 3.0 ATR does not
help. RSI confirmation makes it WORSE (0.898 against a 0.951 unconditional
baseline). Fading VWAP on SENSEX is anti-predictive.

The best conditioned variant found — stretch >2 ATR above VWAP plus ATM call
aggression (`ce_volume_z - pe_volume_z` > 2) -> CE — looked strong on a
two-way split and dissolved on a four-way one:

    Q1 2024-11-25..2025-05-07  n=389  lift +0.139
    Q2 2025-05-08..2025-10-13  n=172  lift -0.124
    Q3 2025-10-14..2026-03-23  n= 99  lift -0.419
    Q4 2026-03-24..2026-09-02  n=197  lift +1.594   <- the whole effect

It fires on 64 of 441 sessions, 51 of them DTE0. Its mirror is -0.213 while the
two triggers fire near-symmetrically (7,694 vs 7,592 bars) at near-identical
z-score means (+0.254 / +0.250) — so the asymmetry is noise, not structure.

NO VWAP-ANCHORED VARIANT SURVIVED A FOUR-QUARTER STABILITY TEST ON SENSEX.

WHAT THIS FIXES ANYWAY, AND WHY IT IS STILL WORTH HAVING
--------------------------------------------------------
1. UNITS. The parent bounds exits in absolute points (`spot_target_pts` 5-80,
   `spot_stop_pts` 3-60). SENSEX runs ~3.28x NIFTY's point scale at near
   identical RELATIVE volatility, so NIFTY's working geometry is not merely
   unfound there, it is OUTSIDE THE BOX (commit fde863a). Exits here are ATR
   multiples converted per bar, floored above 0.5 ATR because a sub-ATR stop
   sits inside a single bar's noise — the measured cause of the parent
   family's SENSEX losses.

2. DIRECTION IS SEARCHABLE, NOT ASSUMED. `fade_mode` picks mean-reversion or
   continuation. Hardcoding the namesake premise would bake in the one
   direction the data says is backwards. Both mode switches are BOOL, not str,
   because `optimizer._build_param_space` silently drops string params — a str
   knob here would be a dead knob of exactly the class this codebase has been
   burned by before (`vix_boost_threshold`).

3. STRETCH BASIS IS SEARCHABLE. `use_sigma_basis` measures displacement in
   session-VWAP sigma (the engine already computes `vwap_sigma` as a causal
   per-session expanding deviation) instead of ATR. Sigma is the native unit
   for a VWAP band; ATR is the parent's. Neither is assumed correct.

4. THE REGIME GATE IS HONEST. The parent admits CHOP/VOLATILE_CHOP/MIXED,
   which on this data is 82.7% of bars (MIXED alone is 75.1%) — a filter that
   barely filters. Here it is one explicit knob, and a MISSING `regime` column
   produces a NAMED BLOCKER instead of silently blocking every signal, which
   is what the parent does on any path that has not enriched it.

5. OPTION FLOW IS AVAILABLE, OFF BY DEFAULT. Nothing else in the library reads
   the ATM flow columns. Only the >=97%-coverage ones are declared;
   `ce_oi_delta_z` / `pe_oi_delta_z` are deliberately NOT used at 38% coverage,
   where a rule reading them is inert on most bars and looks like a filter
   while doing nothing.

DELIBERATELY NOT INCLUDED
-------------------------
Days-to-expiry. DTE buckets on SENSEX are close to a weekday relabel AND the
mapping changed mid-window (DTE2 was Friday in the first half, Tuesday in the
second; DTE3 Thursday then Monday). Per-DTE tuning on ~46 sessions per half
fits a weekday and then deploys onto a different one. DTE stays where it
belongs — `option_backtest.dte_filter`, execution policy — so per-DTE runs are
a config choice, not a hidden parameter in here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies.base import Signal, StrategyBase

_REGIME_CTX_KEY = "_sensex_vwap_mr_regime"

#: Regimes treated as trending for `block_trending`. Kept as a module constant so
#: the test can assert the gate against the same names `app.regime` emits.
TRENDING_REGIMES = ("TREND", "TREND_EXPANDING")

#: A stop below this many ATR sits inside one bar's noise. The parent family's
#: measured SENSEX failure was 87% of trades dying at a sub-ATR stop, so this is
#: a floor on the SCHEMA, not a runtime clamp that could be optimized around.
MIN_STOP_ATR = 0.6


class SensexVWAPMeanReversion(StrategyBase):
    id = "sensex_vwap_mean_reversion"
    name = "SENSEX VWAP Mean Reversion"
    version = "1.0.0"
    supported_instruments = ["SENSEX"]
    supported_modes = ["SCALP", "INTRADAY"]
    supported_timeframes = ["1m", "3m", "5m"]
    description = (
        "SENSEX-only VWAP displacement. Exits in ATR multiples rather than the "
        "parent's absolute points (SENSEX runs ~3.28x NIFTY's point scale), with "
        "direction, stretch basis and volatility band all searchable. NOT "
        "edge-validated: no VWAP variant survived a four-quarter stability test "
        "on SENSEX — see the module docstring for the numbers."
    )

    # Session VWAP is computed over ONLY the rows in the evaluator's rolling
    # window. At the 200-bar default the window stops reaching 09:15 after
    # 12:34, and this strategy's live VWAP then silently diverges from its
    # backtest for the rest of the day (measured on the parent: 2.12 ATR off at
    # 14:49). 335 bars reach 09:15 from the 14:50 cutoff; 400 also retains the
    # prior session.
    live_lookback_bars = 400

    #: Opt-in ATM option-flow columns. Only the >=97%-coverage ones — see the
    #: docstring on why the OI-delta z-scores are excluded. Declaring these is
    #: free when `flow_gate` is off: the columns are joined but unread.
    required_data = ["ce_volume_z", "pe_volume_z"]

    parameter_schema = {
        # --- entry geometry ---
        # BOOL, not str: the optimizer drops string params entirely.
        "fade_mode": {"type": "bool", "default": True},
        "use_sigma_basis": {"type": "bool", "default": False},
        "stretch_mult": {"type": "float", "min": 0.5, "max": 5.0, "default": 2.0},
        "use_rsi_filter": {"type": "bool", "default": True},
        "rsi_overbought": {"type": "float", "min": 55, "max": 90, "default": 72},
        "rsi_oversold": {"type": "float", "min": 10, "max": 45, "default": 28},
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 55},
        "cooldown_bars": {"type": "int", "min": 1, "max": 60, "default": 15},
        # --- exits: ATR-denominated, SENSEX-sized ---
        "spot_stop_atr": {"type": "float", "min": MIN_STOP_ATR, "max": 8.0, "default": 2.0},
        "spot_target_atr": {"type": "float", "min": 1.0, "max": 30.0, "default": 5.0},
        # 0 = size the stop off THIS bar's ATR, 1 = off the trailing baseline.
        # Entering during compression with a stop sized off the compressed bar
        # reproduces the sub-ATR failure in miniature.
        "stop_basis_blend": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.5},
        # --- volatility regime: searchable, not assumed ---
        "atr_rank_window": {"type": "int", "min": 60, "max": 300, "default": 240},
        "min_atr_rank": {"type": "float", "min": 0.0, "max": 0.6, "default": 0.0},
        "max_atr_rank": {"type": "float", "min": 0.4, "max": 1.0, "default": 1.0},
        # --- market regime ---
        "block_trending": {"type": "bool", "default": False},
        # --- session structure ---
        "min_session_bars": {"type": "int", "min": 0, "max": 180, "default": 15},
        # --- optional ATM option-flow conditioning ---
        "flow_gate": {"type": "bool", "default": False},
        # Require |ce_volume_z - pe_volume_z| >= this, AGREEING with the trade
        # direction. Measured lift did not survive; exposed, off, and honest.
        "flow_imbalance_min": {"type": "float", "min": 0.0, "max": 4.0, "default": 2.0},
    }

    # ── precompute ───────────────────────────────────────────────────────────
    def session_precompute(self, df, params):
        """Per-frame constants. Everything here is CAUSAL.

        `rolling(...).rank(pct=True)` ranks each ATR inside its own TRAILING
        window. A session-wide or frame-wide percentile would be look-ahead and
        would manufacture the volatility band's apparent selectivity.
        """
        out = {}
        try:
            window = int(params.get("atr_rank_window") or 240)
            atr_series = pd.to_numeric(df["atr"], errors="coerce")
            rank = atr_series.rolling(window, min_periods=window).rank(pct=True)
            base = atr_series.rolling(window, min_periods=window).median()
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

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _regime_slice(ctx, i):
        """(atr_rank, baseline_atr, bars_into_session) or (nan, nan, None)."""
        block = ctx.get(_REGIME_CTX_KEY)
        if not block or i is None or i < 0:
            return float("nan"), float("nan"), None
        try:
            return (float(block["rank"][i]), float(block["base"][i]),
                    int(block["bars_in"][i]))
        except (IndexError, KeyError, TypeError, ValueError):
            return float("nan"), float("nan"), None

    def _flow_blockers(self, row, params, direction):
        """Option-flow agreement gate. Returns a list of blockers (possibly empty).

        Absent columns are a NAMED blocker, never a silent pass: an all-NaN flow
        column is indistinguishable from a quiet tape, and passing it through
        would make `flow_gate` look like a filter while doing nothing.
        """
        if not params.get("flow_gate"):
            return []
        ce_z, pe_z = row.get("ce_volume_z"), row.get("pe_volume_z")
        if ce_z is None or pe_z is None or pd.isna(ce_z) or pd.isna(pe_z):
            return ["option flow unavailable"]
        imbalance = float(ce_z) - float(pe_z)
        threshold = float(params["flow_imbalance_min"])
        # Agreement means flow leans the way the TRADE leans: calls hot for a CE,
        # puts hot for a PE — regardless of whether the entry is a fade or a join.
        agreeing = imbalance if direction == "CE" else -imbalance
        if agreeing < threshold:
            return [f"flow imbalance {imbalance:+.2f} does not confirm {direction}"]
        return []

    # ── evaluate ─────────────────────────────────────────────────────────────
    def evaluate(self, row, prev, params, ctx) -> Signal:
        required = ["close", "vwap", "atr", "rsi"]
        if any(row.get(k) is None or pd.isna(row.get(k)) for k in required):
            return Signal(direction="NONE", blockers=["warming up"])

        close = float(row["close"])
        vwap = float(row["vwap"])
        atr_val = float(row["atr"])
        rsi_val = float(row["rsi"])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return Signal(direction="NONE", blockers=["no ATR"])

        # --- displacement, in the chosen unit -------------------------------
        if params.get("use_sigma_basis"):
            sigma = row.get("vwap_sigma")
            if sigma is None or pd.isna(sigma) or float(sigma) <= 0:
                return Signal(direction="NONE", blockers=["no VWAP sigma"])
            basis = float(sigma)
            basis_name = "sigma"
        else:
            basis = atr_val
            basis_name = "ATR"
        stretch = (close - vwap) / basis
        threshold = float(params["stretch_mult"])

        # --- direction ------------------------------------------------------
        # fade: stretched ABOVE vwap -> expect a drop -> buy PE.
        # continuation: stretched ABOVE vwap -> expect more up -> buy CE.
        fade = bool(params.get("fade_mode", True))
        if stretch >= threshold:
            direction = "PE" if fade else "CE"
            side = "above"
        elif stretch <= -threshold:
            direction = "CE" if fade else "PE"
            side = "below"
        else:
            return Signal(direction="NONE")

        reasons = [
            f"{stretch:+.2f} {basis_name} {side} VWAP (>= {threshold:.2f})",
            "fade" if fade else "continuation",
        ]
        blockers = []

        # --- RSI confirmation ------------------------------------------------
        # Only meaningful for a fade: an extreme reading is the exhaustion the
        # fade is betting on. On a continuation it would gate out precisely the
        # momentum being joined, so it is not applied there.
        if params.get("use_rsi_filter") and fade:
            if direction == "PE" and rsi_val <= float(params["rsi_overbought"]):
                blockers.append(f"RSI {rsi_val:.0f} not overbought")
            elif direction == "CE" and rsi_val >= float(params["rsi_oversold"]):
                blockers.append(f"RSI {rsi_val:.0f} not oversold")
            else:
                reasons.append(f"RSI {rsi_val:.0f}")

        # --- market regime ----------------------------------------------------
        if params.get("block_trending"):
            regime = row.get("regime")
            if regime is None or (isinstance(regime, float) and pd.isna(regime)):
                # NAMED, not silent. The parent's gate turns every signal into a
                # blocker on any path that has not enriched `regime`, which is
                # indistinguishable from "the filter is working".
                blockers.append("regime unavailable")
            elif str(regime) in TRENDING_REGIMES:
                blockers.append(f"regime {regime} is trending")

        # --- volatility band + session progress -------------------------------
        atr_rank, baseline_atr, bars_in = self._regime_slice(ctx, ctx.get("i", -1))
        if np.isfinite(atr_rank):
            lo = float(params["min_atr_rank"])
            hi = float(params["max_atr_rank"])
            if not (lo <= atr_rank <= hi):
                blockers.append(f"ATR rank {atr_rank:.2f} outside [{lo:.2f}, {hi:.2f}]")
        min_bars = int(params["min_session_bars"])
        if bars_in is not None and bars_in < min_bars:
            blockers.append(f"bar {bars_in} of session < {min_bars}")

        blockers.extend(self._flow_blockers(row, params, direction))

        # --- exits, in ATR ----------------------------------------------------
        # The stop is sized between THIS bar's ATR and the trailing baseline, so
        # "when to enter" is decoupled from "how much room to give it". Entering
        # in compression with a stop sized off the compressed bar is the sub-ATR
        # failure that sank this family on SENSEX.
        if np.isfinite(baseline_atr) and baseline_atr > 0:
            blend = float(params["stop_basis_blend"])
            atr_basis = (1.0 - blend) * atr_val + blend * baseline_atr
        else:
            atr_basis = atr_val

        return Signal(
            direction=direction,
            score=62,
            reasons=reasons,
            blockers=blockers,
            spot_target_pts=atr_val * float(params["spot_target_atr"]),
            spot_stop_pts=atr_basis * float(params["spot_stop_atr"]),
        )
