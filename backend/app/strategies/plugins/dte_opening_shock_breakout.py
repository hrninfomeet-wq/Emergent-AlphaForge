"""DTE Opening Shock Breakout — research-only underlying signal.

The plugin freezes the 09:15-09:44 opening range, uses the 09:44 close versus
the prior session close to choose one direction, and emits only the first
post-range close crossing in that direction.  DTE and option moneyness are
execution-policy concerns and deliberately do not appear in this strategy.

This is an unvalidated research hypothesis, not a profitability claim.  The
precompute fails closed unless it can see the exact 30 opening bars and a prior
session close.  That prevents a rolling live window from silently rebuilding a
different "opening" range after the true session open has rolled out.
"""
from __future__ import annotations

import math
from typing import Any, Dict

import numpy as np
import pandas as pd

from app.strategies.base import Signal, StrategyBase


_CTX_KEY = "_dte_opening_shock_breakout"
_OPENING_TIMES = tuple(
    f"09:{minute:02d}" for minute in range(15, 45)
)
_FIRST_ELIGIBLE_TIME = "09:45"
# Bars are labelled by interval start and become actionable one minute later.
# The 14:49 bar closes at the hard 14:50 entry cutoff, so 14:48 is the last
# label that can produce a decision strictly before the cutoff.
_LAST_ELIGIBLE_TIME = "14:48"
_FIXED_SCORE = 65
_REQUIRED_COLUMNS = ("open", "high", "low", "close", "session_date", "ist_time")


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _precompute_opening_shocks(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """Return causal, per-session opening-range constants and first crossing.

    Scanning the full frame is look-ahead safe because ``evaluate`` emits only
    when its current positional index equals the first observed crossing.  On a
    prefix ending before that crossing the field is simply ``None``; once the
    crossing arrives, the chosen index is identical to the full-history scan.
    """
    if df is None or df.empty or any(column not in df.columns for column in _REQUIRED_COLUMNS):
        return {}

    session_values = df["session_date"].astype(str).to_numpy()
    ordered_sessions = list(dict.fromkeys(session_values.tolist()))
    sessions: Dict[str, Dict[str, Any]] = {}
    prior_session_close = None

    for session_date in ordered_sessions:
        positions = np.flatnonzero(session_values == session_date)
        if positions.size == 0:
            continue

        session_rows = df.iloc[positions]
        current_prior_close = prior_session_close

        # Update the carry for the NEXT session even when this session cannot
        # form a valid opening setup. A non-finite close makes the next session's
        # shock unverifiable and therefore leaves the carry absent.
        last_close = session_rows.iloc[-1].get("close")
        prior_session_close = float(last_close) if _finite(last_close) else None

        if len(positions) < len(_OPENING_TIMES) or current_prior_close is None:
            continue

        opening = session_rows.iloc[:len(_OPENING_TIMES)]
        if tuple(opening["ist_time"].astype(str)) != _OPENING_TIMES:
            continue
        if not all(
            np.isfinite(pd.to_numeric(opening[column], errors="coerce").to_numpy(dtype=float)).all()
            for column in ("open", "high", "low", "close")
        ):
            continue

        range_high = float(pd.to_numeric(opening["high"]).max())
        range_low = float(pd.to_numeric(opening["low"]).min())
        range_close = float(opening.iloc[-1]["close"])
        if range_close > current_prior_close:
            direction = "CE"
        elif range_close < current_prior_close:
            direction = "PE"
        else:
            continue

        first_cross_i = None
        for session_offset in range(len(_OPENING_TIMES), len(positions)):
            pos = int(positions[session_offset])
            prev_pos = int(positions[session_offset - 1])
            ist_time = str(df.iloc[pos].get("ist_time") or "")
            if ist_time < _FIRST_ELIGIBLE_TIME:
                continue
            if ist_time > _LAST_ELIGIBLE_TIME:
                break
            current_close = df.iloc[pos].get("close")
            previous_close = df.iloc[prev_pos].get("close")
            if not (_finite(current_close) and _finite(previous_close)):
                continue
            current_close = float(current_close)
            previous_close = float(previous_close)
            crossed = (
                current_close > range_high and previous_close <= range_high
                if direction == "CE"
                else current_close < range_low and previous_close >= range_low
            )
            if crossed:
                first_cross_i = pos
                break

        sessions[session_date] = {
            "direction": direction,
            "prior_close": float(current_prior_close),
            "range_close": range_close,
            "range_high": range_high,
            "range_low": range_low,
            "first_cross_i": first_cross_i,
        }

    return sessions


class DTEOpeningShockBreakout(StrategyBase):
    id = "dte_opening_shock_breakout"
    name = "DTE Opening Shock Breakout"
    version = "1.0.0"
    description = (
        "Unvalidated research hypothesis: freeze the first 30 one-minute bars, "
        "use the opening shock versus the prior close for direction, and take only "
        "the first same-direction opening-range break. Signals stop at 14:48 so "
        "the last decision precedes the 14:50 entry cutoff; set research exit time "
        "to 15:00. DTE and moneyness stay in the deployment's option policy."
    )
    is_builtin = False
    supported_instruments = ["NIFTY", "SENSEX"]
    supported_modes = ["INTRADAY"]
    supported_timeframes = ["1m"]
    # At 14:48 there are 334 current-session bars. Four hundred bars retain
    # the prior session's final close as well as today's exact opening range.
    live_lookback_bars = 400
    parameter_schema = {
        "spot_target_pts": {"type": "float", "min": 10.0, "max": 200.0, "default": 40.0},
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 60},
    }

    def session_precompute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        return {_CTX_KEY: _precompute_opening_shocks(df)}

    def evaluate(self, row: pd.Series, prev: pd.Series, params: Dict[str, Any],
                 ctx: Dict[str, Any]) -> Signal:
        if any(name not in row or pd.isna(row.get(name)) for name in _REQUIRED_COLUMNS):
            return Signal(direction="NONE", blockers=["opening-shock inputs unavailable"])

        session_date = str(row.get("session_date") or "")
        info = ((ctx or {}).get(_CTX_KEY) or {}).get(session_date)
        if not info or info.get("first_cross_i") is None:
            return Signal(direction="NONE")
        try:
            current_i = int((ctx or {}).get("i"))
        except (TypeError, ValueError):
            return Signal(direction="NONE", blockers=["opening-shock index unavailable"])
        if current_i != int(info["first_cross_i"]):
            return Signal(direction="NONE")

        if _FIXED_SCORE < int(params.get("signal_threshold", 60)):
            return Signal(direction="NONE", blockers=["fixed score below signal threshold"])

        direction = str(info.get("direction") or "")
        entry_close = row.get("close")
        target = params.get("spot_target_pts")
        if direction not in ("CE", "PE") or not (_finite(entry_close) and _finite(target)):
            return Signal(direction="NONE", blockers=["opening-shock risk inputs unavailable"])
        entry_close = float(entry_close)
        target = float(target)
        if target <= 0:
            return Signal(direction="NONE", blockers=["opening-shock target must be positive"])

        boundary = float(info["range_high"] if direction == "CE" else info["range_low"])
        stop_pts = entry_close - boundary if direction == "CE" else boundary - entry_close
        if not math.isfinite(stop_pts) or stop_pts <= 0:
            return Signal(direction="NONE", blockers=["opening-range boundary stop invalid"])

        shock_relation = ">" if direction == "CE" else "<"
        boundary_name = "high" if direction == "CE" else "low"
        break_relation = ">" if direction == "CE" else "<"
        return Signal(
            direction=direction,
            score=_FIXED_SCORE,
            reasons=[
                (f"opening shock {direction}: 30th close {info['range_close']:.2f} "
                 f"{shock_relation} prior close {info['prior_close']:.2f}"),
                (f"close {entry_close:.2f} {break_relation} opening range {boundary_name} "
                 f"{boundary:.2f}"),
            ],
            spot_target_pts=target,
            spot_stop_pts=stop_pts,
        )
