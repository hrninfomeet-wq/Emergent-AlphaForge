"""Expiry-Regime Trend Continuation — research-only underlying signal.

Candidate B of `docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md`. It exists to
test one falsifiable claim, and the claim is about a DIFFERENCE between two arms
rather than about a single positive number:

    A trend-day continuation trade held 30-60 minutes has materially better net
    expectancy on 1DTE than the identical trade on 0DTE.

The 0DTE arm is pre-registered as EXPECTED TO FAIL. `OPTION_BUYING_MICROSTRUCTURE_2026-08.md`
§3 measured the net cost of a 5-minute ATM hold at -4.43% (NIFTY) / -2.01% (SENSEX)
on 0DTE against -1.48% / -0.61% on 1DTE. If the 0DTE arm wins, that register is
wrong and the finding is worth more than the strategy.

**This is an unvalidated research hypothesis, not a profitability claim.** No
screen, backtest or paper cohort has been run against it. Do not deploy it for
real money.

Three design choices are load-bearing, each one paid for by a prior defect:

1. **DTE and moneyness never appear here.** They are execution-policy settings
   (the run's ``dte_filter`` / the deployment's option policy), which read real
   expiry metadata via ``app.dte``. Expiry weekdays rotated twice in 2024-2025 —
   NIFTY Thu -> Tue, SENSEX Fri -> Tue -> Thu — so a weekday-derived DTE rule
   reproduces the real expiry on only 233/424 NIFTY and 238/426 SENSEX sessions.
   The two arms are two RUNS of this one strategy, not two strategies.

2. **`live_lookback_bars = 400`.** The opening range, the session VWAP and the
   prior session's close are all session-anchored. At the 200-bar default the
   live window stops reaching 09:15 after 12:34, and a measured VWAP anchor error
   of 2.12 ATR at 14:49 silently inverted nine shipped strategies (`fc424a1`).
   At the 13:30 entry cutoff there are 255 current-session bars; 400 also retains
   the prior session's close.

3. **Everything fails closed.** A missing opening bar, a missing prior close, a
   NaN indicator or a zero-range bar produces no signal. Every "cannot verify"
   path returns the DENY answer — the lesson of `fa2b65d`, where `detect_drift`
   was documented as conservative and did the opposite.

Entry requires THREE-WAY agreement plus a bar-quality confirmation, which is what
separates it from `dte_opening_shock_breakout` (opening shock vs prior close, then
the first range break) and from `opening_range_breakout` (the break alone).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from app.strategies.base import Signal, StrategyBase


_CTX_KEY = "_expiry_regime_trend_continuation"

#: The opening range is the exact 30 bars 09:15-09:44. Requiring the exact labels
#: (not "the first 30 rows") stops a rolling live window from rebuilding a
#: different "opening" range once the true session open has scrolled out.
_OPENING_TIMES = tuple(f"09:{minute:02d}" for minute in range(15, 45))

_SESSION_OPEN_MIN = 9 * 60 + 15          # 09:15
_FIRST_ELIGIBLE_MIN = 9 * 60 + 45        # 09:45 — the bar after the range closes

#: Bars are labelled by interval start and become actionable one minute later, so
#: the 14:49 bar's decision lands exactly on the hard 14:50 live entry cutoff
#: (`deployment_evaluator.BLOCK_CLOSE_FROM`). 14:48 is the last usable label. This
#: caps the tunable cutoff — a parameter can shorten the window, never extend it
#: past what live will actually accept.
_LAST_ELIGIBLE_MIN = 14 * 60 + 48        # 14:48

#: Fraction of its own range the bar must close within, on the signal side. 0.65
#: means "close in the top (CE) or bottom (PE) 35% of the bar". Fixed, not tuned:
#: the frozen parameter budget is five dimensions and this is not one of them.
_CLOSE_LOCATION_MIN = 0.65

#: Operational ranking value, not a win-probability estimate. Values at or below
#: it are behaviourally identical, so optimizing `signal_threshold` is meaningless
#: here; pin it. The schema keeps a wide range only because narrowing one has
#: broken saved presets before (`56bc3a9`).
_FIXED_SCORE = 65

_REQUIRED_COLUMNS = ("open", "high", "low", "close", "session_date", "ist_time",
                     "vwap", "atr")


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _minutes_of(ist_time: Any) -> Optional[int]:
    """'HH:MM' -> minutes since midnight IST, or None if unparseable."""
    text = str(ist_time or "")
    if len(text) < 5 or text[2] != ":":
        return None
    try:
        return int(text[:2]) * 60 + int(text[3:5])
    except ValueError:
        return None


def _close_location(high: float, low: float, close: float, direction: str) -> Optional[float]:
    """Where the close sits in the bar's own range, from the signal's side.

    1.0 means the bar closed at its extreme in the signal's favour, 0.0 at the
    opposite extreme. Returns None for a zero-range bar, where the question has
    no answer — CAS-frozen index bars are exactly that shape.
    """
    span = high - low
    if not math.isfinite(span) or span <= 0:
        return None
    return (close - low) / span if direction == "CE" else (high - close) / span


def _precompute_sessions(df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per-session opening range, prior close, and the first qualifying bar.

    Scanning the whole frame is look-ahead safe for the same reason it is in
    `dte_opening_shock_breakout`: every condition tested at bar ``i`` reads only
    data at or before ``i``, the scan stops at the FIRST match, and ``evaluate``
    fires only when its own positional index equals that match. On a prefix
    ending before the match the field is simply ``None``; once the bar arrives,
    the chosen index is identical to the full-history scan.
    """
    if df is None or df.empty or any(c not in df.columns for c in _REQUIRED_COLUMNS):
        return {}

    range_mult = float(params.get("range_mult", 1.2) or 0.0)
    cutoff_offset = int(params.get("entry_cutoff_minutes_after_open", 255) or 0)
    # A parameter may shorten the entry window; it may never push past 14:48.
    cutoff_min = min(_SESSION_OPEN_MIN + max(0, cutoff_offset), _LAST_ELIGIBLE_MIN)

    session_values = df["session_date"].astype(str).to_numpy()
    ordered_sessions = list(dict.fromkeys(session_values.tolist()))
    sessions: Dict[str, Dict[str, Any]] = {}
    prior_session_close: Optional[float] = None

    for session_date in ordered_sessions:
        positions = np.flatnonzero(session_values == session_date)
        if positions.size == 0:
            continue

        session_rows = df.iloc[positions]
        current_prior_close = prior_session_close

        # Advance the carry for the NEXT session even when THIS session cannot
        # form a setup. A non-finite close leaves the carry absent, which makes
        # the next session fail closed rather than compare against a stale price.
        last_close = session_rows.iloc[-1].get("close")
        prior_session_close = float(last_close) if _finite(last_close) else None

        if len(positions) < len(_OPENING_TIMES) or current_prior_close is None:
            continue

        opening = session_rows.iloc[:len(_OPENING_TIMES)]
        if tuple(opening["ist_time"].astype(str)) != _OPENING_TIMES:
            continue
        if not all(
            np.isfinite(pd.to_numeric(opening[col], errors="coerce").to_numpy(dtype=float)).all()
            for col in ("open", "high", "low", "close")
        ):
            continue

        range_high = float(pd.to_numeric(opening["high"]).max())
        range_low = float(pd.to_numeric(opening["low"]).min())

        first_i: Optional[int] = None
        direction = ""
        for offset in range(len(_OPENING_TIMES), len(positions)):
            pos = int(positions[offset])
            minutes = _minutes_of(df.iloc[pos].get("ist_time"))
            if minutes is None or minutes < _FIRST_ELIGIBLE_MIN:
                continue
            if minutes > cutoff_min:
                break

            bar = df.iloc[pos]
            close = bar.get("close")
            high = bar.get("high")
            low = bar.get("low")
            vwap = bar.get("vwap")
            atr = bar.get("atr")
            if not all(_finite(v) for v in (close, high, low, vwap, atr)):
                continue
            close, high, low = float(close), float(high), float(low)
            vwap, atr = float(vwap), float(atr)
            if atr <= 0:
                continue

            # 1. Opening-range break picks the candidate side.
            if close > range_high:
                side = "CE"
            elif close < range_low:
                side = "PE"
            else:
                continue

            # 2. Three-way agreement: the break, session VWAP, and the prior close
            #    must all point the same way. Any one of them alone is a weaker
            #    condition already covered by an existing plugin.
            if side == "CE" and not (close > vwap and close > current_prior_close):
                continue
            if side == "PE" and not (close < vwap and close < current_prior_close):
                continue

            # 3. Bar quality: range expansion plus a decisive close. A break on a
            #    narrow indecisive bar is the shape that fails on retest.
            if (high - low) < range_mult * atr:
                continue
            location = _close_location(high, low, close, side)
            if location is None or location < _CLOSE_LOCATION_MIN:
                continue

            first_i, direction = pos, side
            break

        sessions[session_date] = {
            "direction": direction,
            "prior_close": float(current_prior_close),
            "range_high": range_high,
            "range_low": range_low,
            "first_signal_i": first_i,
        }

    return sessions


class ExpiryRegimeTrendContinuation(StrategyBase):
    id = "expiry_regime_trend_continuation"
    name = "Expiry-Regime Trend Continuation"
    version = "1.0.0"
    description = (
        "Unvalidated research hypothesis (candidate B): on a trend day — opening-range "
        "break agreeing with session VWAP and the prior session close, on an expanded "
        "bar with a decisive close — a 30-60 minute option-buying hold has better net "
        "expectancy at 1DTE than at 0DTE. One signal per session, entries stop at the "
        "tunable cutoff and never past 14:48. DTE and moneyness stay in the deployment's "
        "option policy: run 1DTE and 0DTE as two separate cohorts and compare them. "
        "The 0DTE arm is pre-registered as expected to FAIL."
    )
    is_builtin = False
    supported_instruments = ["NIFTY", "SENSEX"]
    supported_modes = ["INTRADAY"]
    supported_timeframes = ["1m"]
    # Opening range, session VWAP and the prior session close are all session
    # anchored. See the module docstring, point 2.
    live_lookback_bars = 400

    parameter_schema = {
        # Distance parameters are in BASIS POINTS OF SPOT, never index points:
        # NIFTY ~24,500 and SENSEX ~81,000 do not share a point scale, and the
        # brief forbids reusing a fixed threshold across the two indices.
        # 4 bps is the intrabar stop/target ambiguity floor measured in
        # OPTION_BUYING_MICROSTRUCTURE_2026-08.md §4 (~10 NIFTY / ~32 SENSEX pts,
        # where backtest and live agree to within 0.16% of bars); the schema
        # minimum sits at it so a search cannot tune below it.
        "stop_bps": {"type": "float", "min": 4.0, "max": 20.0, "default": 5.0},
        "stop_atr_mult": {"type": "float", "min": 0.0, "max": 3.0, "default": 0.8},
        "target_mult": {"type": "float", "min": 1.0, "max": 5.0, "default": 2.5},
        "range_mult": {"type": "float", "min": 0.5, "max": 3.0, "default": 1.2},
        # Minutes after the 09:15 open. 30 = 09:45 (earliest), 255 = 13:30
        # (default), 333 = 14:48 (the hard live cap).
        "entry_cutoff_minutes_after_open": {"type": "int", "min": 30, "max": 333,
                                            "default": 255},
        "hold_max_minutes": {"type": "int", "min": 15, "max": 120, "default": 60},
        # Pinned. The score is fixed, so any value <= 65 behaves identically and
        # any value above it suppresses every signal.
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 60},
    }

    def session_precompute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        return {_CTX_KEY: _precompute_sessions(df, params or {})}

    def evaluate(self, row: pd.Series, prev: pd.Series, params: Dict[str, Any],
                 ctx: Dict[str, Any]) -> Signal:
        if any(name not in row or pd.isna(row.get(name)) for name in _REQUIRED_COLUMNS):
            return Signal(direction="NONE", blockers=["trend-continuation inputs unavailable"])

        session_date = str(row.get("session_date") or "")
        info = ((ctx or {}).get(_CTX_KEY) or {}).get(session_date)
        if not info or info.get("first_signal_i") is None:
            return Signal(direction="NONE")

        try:
            current_i = int((ctx or {}).get("i"))
        except (TypeError, ValueError):
            return Signal(direction="NONE", blockers=["trend-continuation index unavailable"])
        if current_i != int(info["first_signal_i"]):
            return Signal(direction="NONE")

        if _FIXED_SCORE < int(params.get("signal_threshold", 60)):
            return Signal(direction="NONE", blockers=["fixed score below signal threshold"])

        direction = str(info.get("direction") or "")
        if direction not in ("CE", "PE"):
            return Signal(direction="NONE", blockers=["trend-continuation direction unresolved"])

        close = row.get("close")
        atr = row.get("atr")
        if not (_finite(close) and _finite(atr)):
            return Signal(direction="NONE", blockers=["trend-continuation risk inputs unavailable"])
        close, atr = float(close), float(atr)

        stop_bps = float(params.get("stop_bps", 5.0) or 0.0)
        stop_atr_mult = float(params.get("stop_atr_mult", 0.8) or 0.0)
        target_mult = float(params.get("target_mult", 2.5) or 0.0)
        hold_minutes = int(params.get("hold_max_minutes", 60) or 0)

        # The stop is the LARGER of the bps floor and the volatility term, so a
        # quiet session cannot shrink it under the intrabar-ambiguity floor where
        # backtest and live stop agreeing about which level filled first.
        stop_pts = max(close * stop_bps / 10_000.0, atr * stop_atr_mult)
        if not math.isfinite(stop_pts) or stop_pts <= 0:
            return Signal(direction="NONE", blockers=["trend-continuation stop invalid"])

        target_pts = stop_pts * target_mult
        if not math.isfinite(target_pts) or target_pts <= 0:
            return Signal(direction="NONE", blockers=["trend-continuation target invalid"])
        if hold_minutes <= 0:
            return Signal(direction="NONE", blockers=["trend-continuation hold must be positive"])

        boundary = float(info["range_high"] if direction == "CE" else info["range_low"])
        rel = ">" if direction == "CE" else "<"
        boundary_name = "high" if direction == "CE" else "low"
        return Signal(
            direction=direction,
            score=_FIXED_SCORE,
            reasons=[
                f"close {close:.2f} {rel} opening-range {boundary_name} {boundary:.2f}",
                f"close {rel} session VWAP {float(row['vwap']):.2f}",
                f"close {rel} prior session close {float(info['prior_close']):.2f}",
                f"expanded bar (>= {float(params.get('range_mult', 1.2)):.2f} x ATR) "
                f"closing in the decisive {int((1 - _CLOSE_LOCATION_MIN) * 100)}%",
            ],
            spot_target_pts=target_pts,
            spot_stop_pts=stop_pts,
            time_stop_minutes=hold_minutes,
        )
