"""ATR Sigma Router — a scale-free entry-family search space.

WHAT THIS IS
------------
Not an edge claim. A deliberately small, honest search space over the three
entry families that intraday index research keeps returning to — momentum,
mean-reversion fade, and volatility-expansion ignition — with every threshold
expressed in ATR (or basis points of spot) so a single parameter set means the
same thing on NIFTY (~24,500) and SENSEX (~80,000). The family is an INT
parameter precisely so the Optimizer searches all three at once instead of the
author guessing which one works.

WHY ATR-NORMALISED (the trap this avoids)
-----------------------------------------
SENSEX trades at ~3.3x NIFTY's level, so an absolute-point threshold tuned on
one index is a completely different trade on the other — a point-bounded exit
that is loose on NIFTY is a scratch on SENSEX. Earlier strategies here carried
absolute `spot_target_pts`/`spot_stop_pts` and could not transfer. Every knob
below is a RATIO.

WHY THE STOP HAS A BASIS-POINT FLOOR
------------------------------------
Measured against 769k real NIFTY ticks (2026-05-27 → 2026-08-14): stored 1-min
bars reproduce the tick extremes exactly (0.0% misses), but a bar hides ~5x its
range in intrabar oscillation (p90 7.6x). Bars where a stop AND a target would
both sit inside the range — where the backtest must guess the order and live
would not — run 2.51% at +/-5 NIFTY points but only 0.16% at +/-10 and 0.02% at
+/-20. `min_stop_bps` keeps the stop above that ambiguity floor in scale-free
units (4 bps ~ 9.8 NIFTY pts ~ 32 SENSEX pts).

DEPLOYMENT NOTE — SET A PREMIUM STOP
-----------------------------------
This strategy emits SPOT exits only (spot_target_pts / spot_stop_pts /
time_stop_minutes). It sets no `stop_pct` / `target_pct`, so a paper or live
deployment with no `risk.auto_paper_stop_pct` falls back to the deep 50%-of-
premium catastrophe default and gets NO premium target at all. Set
`auto_paper_stop_pct` / `auto_paper_target_pct` on the deployment.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
No DTE gating. The expiry weekday ROTATED TWICE inside the warehouse window
(NIFTY Thu -> Tue during 2025Q3; SENSEX Fri -> Tue -> Thu), so any weekday-based
DTE rule silently targets the wrong DTE across 2024-2025. DTE selection belongs
to the run's `dte_filter` / the deployment's option policy, which read real
expiry metadata. `weekday_mask` below is a calendar research knob only — it is
NOT a DTE proxy.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict

import pandas as pd

from app.strategies.base import Signal, StrategyBase

MOMENTUM, FADE, EXPANSION = 0, 1, 2
_FAMILY_NAMES = {MOMENTUM: "momentum", FADE: "fade", EXPANSION: "vol_expansion"}
_REQUIRED = ("close", "vwap", "atr")
_BASE_SCORE = 60


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _weekday_of(session_date: Any) -> int:
    """Mon=0 .. Sun=6, or -1 when the session date is unusable."""
    try:
        return date.fromisoformat(str(session_date)[:10]).weekday()
    except (TypeError, ValueError):
        return -1


class ATRSigmaRouter(StrategyBase):
    id = "atr_sigma_router"
    name = "ATR Sigma Router"
    version = "1.0.0"
    description = (
        "Scale-free entry-family router: momentum / fade / volatility-expansion, "
        "every threshold in ATR units so one parameter set transfers across NIFTY, "
        "BANKNIFTY and SENSEX. Research capability, NOT a validated edge — pair it "
        "with the run's DTE filter for expiry targeting."
    )
    is_builtin = False
    supported_instruments = ["NIFTY", "BANKNIFTY", "SENSEX"]
    supported_modes = ["SCALP", "INTRADAY"]
    supported_timeframes = ["1m"]
    # MUST cover the whole session. `session_vwap` is grouped by session_date over
    # ONLY the rows present in the evaluator's rolling window, so the VWAP anchor
    # is the WINDOW start, not 09:15. At the default 200 the window stops reaching
    # 09:15 after 12:34, and by 14:49 the anchor error measured +17.02 pts = 2.12
    # ATR — larger than the entire default entry band (band_atr_mult=1.0), which
    # silently FLIPS momentum/fade signals for ~40% of the session. 400 keeps the
    # full current session (335 bars at 14:49) plus warm-up from the prior one.
    live_lookback_bars = 400

    parameter_schema = {
        # Entry family. INT, not a string: optimizer.py only searches
        # int/float/bool, so a string family would be silently dropped from the
        # search space and every trial would evaluate the same default.
        "entry_family":      {"type": "int",   "min": 0,    "max": 2,    "default": 0},
        # Distance from session VWAP that counts as "outside the noise", in ATR.
        "band_atr_mult":     {"type": "float", "min": 0.25, "max": 4.0,  "default": 1.0},
        # EXPANSION family only: atr / atr_avg required to call it an ignition.
        "expansion_ratio":   {"type": "float", "min": 0.8,  "max": 3.0,  "default": 1.3},
        "stop_atr_mult":     {"type": "float", "min": 0.5,  "max": 6.0,  "default": 2.0},
        "target_atr_mult":   {"type": "float", "min": 1.0,  "max": 12.0, "default": 4.0},
        # Scale-free stop floor; see the tick-fidelity note in the module docstring.
        "min_stop_bps":      {"type": "float", "min": 1.0,  "max": 25.0, "default": 4.0},
        "time_stop_minutes": {"type": "int",   "min": 5,    "max": 180,  "default": 45},
        # Calendar research knob. bit0=Mon .. bit4=Fri; 31 = every weekday.
        # NOT a DTE proxy — see the module docstring.
        "weekday_mask":      {"type": "int",   "min": 1,    "max": 31,   "default": 31},
        # Require the EMA stack to agree. Applied to the trend-following
        # families only; demanding it of FADE would be self-contradictory.
        "trend_filter":      {"type": "bool",  "default": True},
        "signal_threshold":  {"type": "int",   "min": 40,   "max": 90,   "default": 55},
        "cooldown_bars":     {"type": "int",   "min": 1,    "max": 60,   "default": 10},
    }

    # ---------------------------------------------------------------- entry --

    def evaluate(self, row, prev, params, ctx) -> Signal:
        if any(not _finite(row.get(k)) for k in _REQUIRED):
            return Signal(direction="NONE", blockers=["indicators warming up"])

        atr = float(row["atr"])
        if atr <= 0:
            # A zero/negative ATR means the window is still warming or the bars
            # are frozen (e.g. the closing-auction hold). Dividing by it would
            # manufacture an infinite ratio, which validate_signal rejects.
            return Signal(direction="NONE", blockers=["atr not yet meaningful"])

        mask = int(params.get("weekday_mask", 31))
        weekday = _weekday_of(row.get("session_date"))
        if weekday < 0 or weekday > 4 or not (mask >> weekday) & 1:
            return Signal(direction="NONE", blockers=["weekday excluded by mask"])

        close = float(row["close"])
        vwap = float(row["vwap"])
        band = float(params["band_atr_mult"]) * atr
        dev = close - vwap
        family = int(params.get("entry_family", MOMENTUM))

        direction, reasons = self._route(family, row, params, dev, band, atr)
        if direction == "NONE":
            return Signal(direction="NONE")

        if params.get("trend_filter", True) and family in (MOMENTUM, EXPANSION):
            blocked = self._trend_veto(row, direction)
            if blocked:
                return Signal(direction="NONE", blockers=[blocked])

        # Score from scale-free ratios only, via threshold comparisons rather
        # than arithmetic truncation, so two identically-shaped bars at
        # different index levels cannot round to different ints.
        stretch = abs(dev) / atr
        # NOT `atr_avg and atr/atr_avg or 1.0`: bool(nan) is True, so a NaN
        # atr_avg fell through to nan rather than the 1.0 fallback, and every
        # downstream `expansion >= threshold` then silently read False.
        atr_avg = row.get("atr_avg")
        expansion = (atr / float(atr_avg)
                     if _finite(atr_avg) and float(atr_avg) > 0 else 1.0)
        score = _BASE_SCORE
        for threshold in (1.5, 2.5, 4.0):
            if stretch >= threshold:
                score += 5
                reasons.append(f"stretch >= {threshold} ATR")
        for threshold in (1.5, 2.0):
            if expansion >= threshold:
                score += 5
                reasons.append(f"ATR {expansion:.1f}x its average")

        # The BACKTEST engine applies signal_threshold and cooldown_bars
        # generically (backtest.py:181-200); the LIVE evaluator applies NEITHER
        # (deployment_evaluator.py checks only direction). Enforcing the
        # threshold here is what makes a tuned value mean the same thing in both.
        score = min(100, score)
        if score < int(params.get("signal_threshold", 55)):
            return Signal(direction="NONE",
                          blockers=[f"score {score} below threshold"])

        # Edge-trigger. Backtest takes ONE trade then blocks on position-open +
        # cooldown, but `close` typically sits outside the band for many
        # consecutive bars, so a level-triggered rule emits on EVERY bar live —
        # unbounded paper exposure. Fire only on the bar the setup first appears.
        if self._route(family, prev, params,
                       self._dev(prev, params), band, atr)[0] == direction:
            return Signal(direction="NONE", blockers=["setup already active"])

        stop_pts, target_pts = self._exits(close, atr, params)
        return Signal(
            direction=direction,
            score=score,
            reasons=reasons,
            scenario=_FAMILY_NAMES.get(family, "unknown"),
            spot_stop_pts=stop_pts,
            spot_target_pts=target_pts,
            time_stop_minutes=int(params["time_stop_minutes"]),
        )

    @staticmethod
    def _dev(row, params):
        """close - vwap for a bar, or an out-of-band NaN-safe 0.0 when the bar
        cannot be read (an unreadable previous bar must not suppress a signal)."""
        close, vwap = row.get("close"), row.get("vwap")
        if not (_finite(close) and _finite(vwap)):
            return 0.0
        return float(close) - float(vwap)

    # ------------------------------------------------------------- families --

    def _route(self, family, row, params, dev, band, atr):
        """Return (direction, reasons) for the selected family."""
        if family == MOMENTUM:
            if dev > band:
                return "CE", [f"close {dev / atr:.1f} ATR above VWAP"]
            if dev < -band:
                return "PE", [f"close {abs(dev) / atr:.1f} ATR below VWAP"]
            return "NONE", []

        if family == FADE:
            # Same trigger, opposite side: the stretch is treated as
            # exhaustion rather than information.
            if dev > band:
                return "PE", [f"fading {dev / atr:.1f} ATR stretch above VWAP"]
            if dev < -band:
                return "CE", [f"fading {abs(dev) / atr:.1f} ATR stretch below VWAP"]
            return "NONE", []

        if family == EXPANSION:
            atr_avg = row.get("atr_avg")
            if not _finite(atr_avg) or float(atr_avg) <= 0:
                return "NONE", []
            ratio = atr / float(atr_avg)
            if ratio < float(params["expansion_ratio"]):
                return "NONE", []
            # Direction from where the bar CLOSED in its own range — an
            # ignition bar closes on its extreme.
            high, low = float(row["high"]), float(row["low"])
            span = high - low
            if span <= 0:
                return "NONE", []
            position = (float(row["close"]) - low) / span
            if position >= 2.0 / 3.0:
                return "CE", [f"expansion {ratio:.1f}x, close in top third"]
            if position <= 1.0 / 3.0:
                return "PE", [f"expansion {ratio:.1f}x, close in bottom third"]
            return "NONE", []

        return "NONE", []

    @staticmethod
    def _trend_veto(row, direction):
        ema9, ema21 = row.get("ema9"), row.get("ema21")
        if not (_finite(ema9) and _finite(ema21)):
            return ""          # filter cannot speak; do not invent a veto
        bullish = float(ema9) > float(ema21)
        if direction == "CE" and not bullish:
            return "EMA stack disagrees (bearish) with CE"
        if direction == "PE" and bullish:
            return "EMA stack disagrees (bullish) with PE"
        return ""

    # ---------------------------------------------------------------- exits --

    @staticmethod
    def _exits(close, atr, params):
        """ATR-scaled stop/target, floored for tick ambiguity, target > stop.

        Returned in index POINTS because that is what Signal/`run_backtest`
        consume — but both are derived purely from ratios, so they scale with
        the instrument automatically.
        """
        floor_pts = close * float(params["min_stop_bps"]) / 10_000.0
        stop_pts = max(float(params["stop_atr_mult"]) * atr, floor_pts)
        target_pts = float(params["target_atr_mult"]) * atr
        # A target inside the stop would make every trade a guaranteed loser
        # under the engine's stop-first intrabar rule.
        target_pts = max(target_pts, stop_pts * 1.2)
        return round(stop_pts, 4), round(target_pts, 4)
