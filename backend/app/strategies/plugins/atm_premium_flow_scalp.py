"""ATM Premium-Flow Scalp — Candidate A, built AFTER its screen rejected it.

**READ THIS BEFORE DEPLOYING ANYTHING.** This strategy's own pre-registered
screen REJECTED the hypothesis it implements, on both indices, at every
pre-registered parameter value. `docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md`
§16 has the measurement; the short version:

    conditioned MFE/MAE @10min   NIFTY 0.856   SENSEX 0.703   needed > 1.15
    session t-stat on net%       NIFTY -2.81   SENSEX -2.24   needed > +2.0

In 11 of 12 conditioned cells the ratio came in BELOW the unconditioned base
rate on the same series — the trigger selected bars that did *worse* than
average, not better. All three pre-registered `flow_z_threshold` values failed,
and loosening the threshold made it worse. A sign-inversion falsification also
failed, so it is not a wiring error: the signal was not predictive in either
direction. It was built anyway, by explicit operator decision, to be optimized
by hand and retired if it does not come good. Nothing here is evidence of an
edge, and the plugin makes no such claim.

The hypothesis (§4.1): directional conviction expresses in leveraged option flow
before it shows in the underlying, so when ATM call volume and OI build
accelerate relative to ATM put flow, the next ten minutes of that side's premium
beats the 0.90–0.95 unconditioned base rate.

Four design choices are load-bearing.

1. **The 20-session baseline is NOT computed here.** `flow_imbalance` is built
   from z-scores this strategy READS; it never derives them. `deployment_evaluator`
   clamps the live window to 1,000 bars — under three sessions — so a
   `session_precompute` deriving a 20-session distribution would see full history
   in a backtest and under three sessions live, computing a different number in
   each path while both looked healthy. That is the
   `live-window-anchors-session-indicators` failure and it is invisible to a
   backtest by construction. The z-scores arrive as `required_data` columns from
   `app.option_flow`, whose query window is chosen independently of this frame.

2. **`live_lookback_bars = 400`.** The VWAP confirmation is session-anchored, and
   session anchors are computed over ONLY the rows handed to
   `precompute_all_indicators`. At the 200-bar default the live window stops
   reaching 09:15 after 12:34; a measured VWAP anchor error of 2.12 ATR at 14:49
   silently inverted nine shipped strategies (`fc424a1`). 400 holds a full
   375-bar session, which also makes the session trade budget below identical in
   both paths.

3. **Everything fails closed.** A NaN z-score, a missing ATM bar, a NaN
   indicator or an unparseable timestamp produces no signal and says why. NaN
   here is genuinely common rather than exotic: on SENSEX **60.8% of same-minute
   OI-delta baselines are flat**, so `flow_imbalance` is unavailable on ~61% of
   SENSEX bars (§15.4b). Scoring those as 0 would read as "perfectly typical
   flow" and is the single most likely way this plugin could look alive while
   being dead.

4. **Distances are in basis points of spot, never index points.** SENSEX is
   ~3.2× NIFTY's point scale at the same relative volatility, so a point-bounded
   stop makes one index's geometry unreachable on the other.

DTE (1–3, 0DTE excluded) and moneyness are NOT here. They are execution policy —
the run's `dte_filter` and the deployment's option policy, which read real expiry
metadata via `app.dte`. Expiry weekdays rotated twice in 2024–2025, so a
weekday-derived DTE rule reproduces the real expiry on barely half of sessions.

§4.1 also specifies a trailing rule (breakeven at +1.0× stop, then trail 1.0×
stop) and a ₹4,000 daily loss cap. Neither is expressible in `Signal`; both are
exit-control / risk settings on the deployment and must be configured there. The
plugin emits the stop, the target and the time stop only, rather than pretending
to own an exit it cannot enforce.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.strategies.base import Signal, StrategyBase

_CTX_KEY = "_atm_premium_flow_scalp"

#: Entry window, IST. Bars are labelled by interval start and act one minute
#: later, so 14:47 is the last label whose decision lands before the 14:48 cap
#: §4.1 sets; 14:48 itself is already out.
_FIRST_ELIGIBLE_MIN = 9 * 60 + 25        # 09:25
_LAST_ELIGIBLE_MIN = 14 * 60 + 48        # 14:48 — exclusive

#: Bars between two entries in the SAME direction. §4.1 fixes this at 30 and the
#: frozen parameter budget is six dimensions — this is not one of them, so it is
#: a constant rather than a searchable knob.
_COOLDOWN_BARS = 30

#: `stop = max(stop_bps of spot, _STOP_ATR_MULT * atr)`. The 0.6 is fixed by
#: §4.1 and is likewise outside the frozen budget.
_STOP_ATR_MULT = 0.6

#: ATM bar volume must reach this fraction of the causal 20-session median.
_LIQUIDITY_MULT = 0.5

#: Operational ranking value, not a win probability. The score is constant, so
#: every `signal_threshold` at or below it behaves identically and every value
#: above it suppresses everything — see the pinned schema entry.
_FIXED_SCORE = 65

_REQUIRED_COLUMNS = (
    "close", "session_date", "ist_time", "vwap", "atr", "adx",
    "ce_volume_z", "pe_volume_z", "ce_oi_delta_z", "pe_oi_delta_z",
    "ce_volume", "pe_volume", "atm_volume_median_20d",
)


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


def flow_imbalance(row: Any) -> Optional[float]:
    """§4.1's `(ce_vol_z − pe_vol_z) + (ce_oi_delta_z − pe_oi_delta_z)`.

    None when ANY term is missing or non-finite. Deliberately not a partial sum:
    a value built from two of the four terms is a different quantity wearing the
    same name, and on SENSEX — where the OI half is unavailable on ~61% of bars —
    it would be the quantity most of the time.
    """
    terms = []
    for name in ("ce_volume_z", "pe_volume_z", "ce_oi_delta_z", "pe_oi_delta_z"):
        try:
            value = row[name]
        except (KeyError, IndexError, TypeError):
            return None
        if not _finite(value):
            return None
        terms.append(float(value))
    ce_vz, pe_vz, ce_dz, pe_dz = terms
    return (ce_vz - pe_vz) + (ce_dz - pe_dz)


def _admitted_entries(df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Dict[int, str]]:
    """Per session, the bar positions admitted as entries and their direction.

    Look-ahead safe by the same argument `expiry_regime_trend_continuation` uses:
    every condition tested at bar ``i`` reads only data at or before ``i``, and
    entry *k* depends only on bar *k* plus the positions of entries 1..k-1, which
    are strictly earlier. On a prefix ending at any admitted bar the admitted set
    is exactly the full-history set truncated there — nothing later is invented
    and nothing earlier changes. Pinned by
    ``test_precompute_is_look_ahead_safe_on_every_prefix``.
    """
    if df is None or df.empty:
        return {}
    if any(col not in df.columns for col in _REQUIRED_COLUMNS):
        return {}

    threshold = abs(float(params.get("flow_z_threshold", 1.5) or 0.0))
    adx_min = float(params.get("adx_min", 20.0) or 0.0)
    max_trades = max(1, int(params.get("max_trades", 2) or 1))

    sessions = df["session_date"].astype(str).to_numpy()
    out: Dict[str, Dict[int, str]] = {}

    for session in dict.fromkeys(sessions.tolist()):
        positions = np.flatnonzero(sessions == session)
        admitted: Dict[int, str] = {}
        last_fire: Dict[str, int] = {}
        taken = 0
        for pos in positions:
            if taken >= max_trades:
                break
            row = df.iloc[pos]
            direction = _direction_for(row, threshold=threshold, adx_min=adx_min)
            if direction is None:
                continue
            previous = last_fire.get(direction)
            if previous is not None and (int(pos) - previous) < _COOLDOWN_BARS:
                continue
            admitted[int(pos)] = direction
            last_fire[direction] = int(pos)
            taken += 1
        if admitted:
            out[session] = admitted
    return out


def _direction_for(row: Any, *, threshold: float, adx_min: float) -> Optional[str]:
    """"CE", "PE" or None for one bar, applying every §4.1 gate except the
    cooldown and the session budget (which need cross-bar state)."""
    for name in _REQUIRED_COLUMNS:
        try:
            value = row[name]
        except (KeyError, IndexError, TypeError):
            return None
        if name in ("session_date", "ist_time"):
            continue
        if not _finite(value):
            return None

    minutes = _minutes_of(row["ist_time"])
    if minutes is None or not (_FIRST_ELIGIBLE_MIN <= minutes < _LAST_ELIGIBLE_MIN):
        return None

    if float(row["adx"]) < adx_min:
        return None

    # Liquidity floor: ATM straddle bar volume against the causal 20-session
    # median. Both legs must have printed — a straddle volume missing its put
    # leg is unknown, not the call leg's volume.
    atm_volume = float(row["ce_volume"]) + float(row["pe_volume"])
    if atm_volume < float(row["atm_volume_median_20d"]) * _LIQUIDITY_MULT:
        return None

    flow = flow_imbalance(row)
    if flow is None:
        return None

    close = float(row["close"])
    vwap = float(row["vwap"])
    if flow >= threshold and close > vwap:
        return "CE"
    if flow <= -threshold and close < vwap:
        return "PE"
    return None


class AtmPremiumFlowScalp(StrategyBase):
    id = "atm_premium_flow_scalp"
    name = "ATM Premium-Flow Scalp"
    version = "1.0.0"
    description = (
        "Candidate A. Buys the ATM call or put when option-side flow imbalance — "
        "(ce_vol_z - pe_vol_z) + (ce_oi_delta_z - pe_oi_delta_z), z-scored against a "
        "CAUSAL 20-session same-minute distribution computed in the data layer — clears "
        "a threshold AND the underlying agrees with that side relative to session VWAP, "
        "with adx above a floor and ATM volume above half its 20-session causal median. "
        "WARNING: this hypothesis FAILED its own pre-registered screen on both indices "
        "(deliverable §16: MFE/MAE 0.856 NIFTY / 0.703 SENSEX at 10 min against a 1.15 "
        "gate, session t-stat -2.81 / -2.24 against +2.0), and the conditioned bars did "
        "WORSE than unconditioned ones. Built for hand-optimization by operator decision; "
        "no edge is claimed or implied. DTE 1-3 and moneyness live in the run's option "
        "policy, not here."
    )
    is_builtin = False
    supported_instruments = ["NIFTY", "SENSEX"]
    supported_modes = ["SCALP", "INTRADAY"]
    supported_timeframes = ["1m"]

    # Session-anchored VWAP + a whole-session trade budget. See docstring, note 2.
    live_lookback_bars = 400

    # The four z-scores and the liquidity inputs are warehouse-backed columns
    # joined AS-OF each bar at load time. Declaring them is what makes the engine
    # join them at all; an undeclared read would be a silent all-NaN column.
    required_data = [
        "ce_volume_z", "pe_volume_z", "ce_oi_delta_z", "pe_oi_delta_z",
        "ce_volume", "pe_volume", "atm_volume_median_20d",
    ]

    # §4.1 freezes the search at SIX dimensions / 324 combinations per index.
    # Everything else that shapes a trade is fixed by the spec and lives as a
    # module constant, so the search space cannot quietly grow. Indicator periods
    # are pinned at defaults: they are not part of the hypothesis, and 10 of the
    # 14 dimensions in a prior campaign were indicator periods that could not
    # move the objective.
    parameter_schema = {
        "flow_z_threshold": {"type": "float", "min": 0.5, "max": 4.0, "default": 1.5},
        "hold_minutes": {"type": "int", "min": 5, "max": 60, "default": 10},
        # Basis points of SPOT, never index points — see docstring, note 4. The
        # 4 bps minimum is the intrabar stop/target ambiguity floor measured in
        # OPTION_BUYING_MICROSTRUCTURE_2026-08.md §4; the schema sits on it so a
        # search cannot tune below the level at which backtest and live agree.
        "stop_bps": {"type": "float", "min": 4.0, "max": 20.0, "default": 4.0},
        "target_mult": {"type": "float", "min": 1.0, "max": 5.0, "default": 2.0},
        "adx_min": {"type": "float", "min": 10.0, "max": 40.0, "default": 20.0},
        "max_trades": {"type": "int", "min": 1, "max": 5, "default": 2},
        # Pinned. The score is the constant 65, so every value at or below it is
        # behaviourally identical and every value above suppresses EVERY signal —
        # an on/off switch wearing a threshold's name. Left searchable it taught
        # one real 426-trial job where the off switch was, taking 80.2% of
        # parameter importance (register item #4). `fixed` keeps it visible and
        # settable while removing it from the search; a per-run override still
        # works. The range stays wide because narrowing one has broken saved
        # presets before (`56bc3a9`).
        "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 60,
                             "fixed": 60},
    }

    def session_precompute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        return {_CTX_KEY: _admitted_entries(df, params or {})}

    def evaluate(self, row: pd.Series, prev: pd.Series, params: Dict[str, Any],
                 ctx: Dict[str, Any]) -> Signal:
        blockers: List[str] = []

        for name in _REQUIRED_COLUMNS:
            try:
                value = row[name]
            except (KeyError, IndexError, TypeError):
                return Signal(direction="NONE",
                              blockers=[f"{name} unavailable"])
            if name in ("session_date", "ist_time"):
                continue
            if not _finite(value):
                # Named individually on purpose: "flow unavailable" and "atr is
                # NaN" send an operator to different places, and on SENSEX the
                # first is the ordinary case rather than a fault.
                blockers.append(f"{name} unavailable")
        if blockers:
            return Signal(direction="NONE", blockers=blockers)

        session = str(row.get("session_date") or "")
        admitted = ((ctx or {}).get(_CTX_KEY) or {}).get(session) or {}
        i = int((ctx or {}).get("i", -1))
        direction = admitted.get(i)
        if direction is None:
            return Signal(direction="NONE")

        close = float(row["close"])
        atr = float(row["atr"])
        stop_bps = float(params.get("stop_bps", 4.0) or 0.0)
        target_mult = float(params.get("target_mult", 2.0) or 0.0)
        hold_minutes = int(params.get("hold_minutes", 10) or 0)

        # max(bps of spot, 0.6 x ATR). The bps term is what makes the geometry
        # transfer between NIFTY and SENSEX; the ATR term stops a quiet session
        # from setting a stop inside the noise.
        stop_pts = max(close * stop_bps / 10_000.0, _STOP_ATR_MULT * atr)
        if not (math.isfinite(stop_pts) and stop_pts > 0):
            return Signal(direction="NONE", blockers=["stop distance is not resolvable"])
        target_pts = target_mult * stop_pts
        if not (math.isfinite(target_pts) and target_pts > 0):
            return Signal(direction="NONE", blockers=["target distance is not resolvable"])

        flow = flow_imbalance(row)
        reasons = [
            f"flow_imbalance {flow:+.2f} clears "
            f"{'+' if direction == 'CE' else '-'}"
            f"{abs(float(params.get('flow_z_threshold', 1.5) or 0.0)):.2f}",
            f"close {close:.2f} agrees with {direction} vs session vwap {float(row['vwap']):.2f}",
            f"adx {float(row['adx']):.1f} at or above {float(params.get('adx_min', 20.0) or 0.0):.1f}",
        ]
        return Signal(
            direction=direction,
            score=_FIXED_SCORE,
            reasons=reasons,
            spot_stop_pts=round(stop_pts, 4),
            spot_target_pts=round(target_pts, 4),
            time_stop_minutes=hold_minutes,
        )
