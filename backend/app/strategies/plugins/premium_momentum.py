# backend/app/strategies/plugins/premium_momentum.py
"""Premium-momentum contingency breakout (Track B execution vehicle).

Registration-only plugin: it carries the id/params so deployments, the UI, and
the arm chain treat this like any strategy. The ACTUAL per-bar logic (strike
lock at the reference time, ref-premium capture from ticks, first-to-trigger
momentum entry) runs in the deployment evaluator's Track B branch
(app.premium_momentum_live) using the SAME pure helpers as the backtest —
evaluate() here is deliberately inert so the generic spot path can never fire.
Exits: premium stop/target from these params via signal risk_hints; the stepped
X-Y trail comes ONLY from deployment.risk.exit_controls (mode 'stepped_xy')."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from app.strategies.base import Signal, StrategyBase


class PremiumMomentum(StrategyBase):
    id = "premium_momentum"
    name = "Premium Momentum (AlgoTest-style)"
    version = "1.0.0"
    description = (
        "Locks the chosen-moneyness CE+PE strikes from spot at a reference time, "
        "then buys the FIRST side whose option premium rises by the momentum "
        "threshold. Exits on premium stop/target (+ stepped trail via deployment "
        "exit_controls) — evaluated by the Track B premium branch, not this class."
    )
    supported_instruments = ["NIFTY"]          # v1: NIFTY-only (spec §1)
    supported_modes = ["INTRADAY"]
    supported_timeframes = ["1m"]
    parameter_schema: Dict[str, Any] = {
        "reference_time": {"type": "str", "default": "09:31",
                           "description": "IST HH:MM bar whose close locks the strikes + refs"},
        "moneyness": {"type": "str", "default": "itm1",
                      "description": "atm | itm1 | itm2 | otm1 | otm2 (must be warehouse/stream covered)"},
        "side": {"type": "str", "default": "first_to_trigger",
                 "description": "first_to_trigger | ce | pe"},
        "momentum_pct": {"type": "float", "min": 5.0, "max": 50.0, "default": 15.0,
                         "description": "enter when premium rises this % over its ref (None if using pts)"},
        # "fixed": None keeps this OUT of the Optimizer's numeric search space (any
        # float type gets a search dimension by default — see _build_param_space in
        # optimizer.py). momentum_pct/momentum_pts are mutually exclusive
        # (PremiumTriggerConfig._entry_trigger_present raises if both are set), so
        # the general Optimizer searches the pct variant only; the dedicated tuner's
        # own grid logic (premium_momentum_tuner.py) is the place to sweep pts.
        "momentum_pts": {"type": "float", "fixed": None, "default": None,
                         "description": "absolute premium-points trigger (exactly one of pct/pts)"},
        "stop_pct": {"type": "float", "min": 10.0, "max": 40.0, "default": 20.0,
                     "description": "premium stop % below entry (guard-enforced)"},
        # Fixed at None (matches the shipped blueprint default: no target, ride the
        # trail to EOD) — not searched by the general Optimizer in this first pass.
        "target_pct": {"type": "float", "fixed": None, "default": None,
                       "description": "premium target % above entry (None = ride to EOD)"},
        "late_lock_cutoff": {"type": "str", "default": "10:15",
                             "description": "no lock after this IST time -> session done (no_lock)"},
        # --- Phase 5B: live/paper multi-leg execution (additive; every field
        # below defaults to the single-leg pre-5B shape, so a stored params
        # dict with only the original 8 keys above merges to these defaults
        # with zero migration — see merged_params, base.py:88-102). Multi-leg
        # exposure is deliberately kept OUT of the general Optimizer's search
        # space (see docs/superpowers/specs/2026-07-15-premium-momentum-
        # phase5b-live-multileg-design.md §1 OUT-of-scope): "fixed" pins a
        # float/bool value; str-typed params are excluded from that space
        # unconditionally by optimizer.py's _build_param_space.
        "leg_mode": {"type": "str", "default": "first_to_trigger",
                     "description": "first_to_trigger | both — both runs CE+PE as independent primary legs (5B)"},
        "lazy_enabled": {"type": "bool", "default": False, "fixed": False,
                         "description": "one-shot lazy reversal leg armed after a primary STOP-class exit (5B); "
                                        "fixed False keeps it OFF the general Optimizer's search space"},
        "lazy_momentum_pct": {"type": "float", "min": 5.0, "max": 50.0, "default": None,
                              "description": "lazy leg's own momentum trigger % off its fresh ref (only meaningful when lazy_enabled)"},
        "lazy_stop_pct": {"type": "float", "min": 10.0, "max": 40.0, "default": None,
                          "description": "lazy leg's own premium stop % (only meaningful when lazy_enabled)"},
        "lazy_target_pct": {"type": "float", "fixed": None, "default": None,
                            "description": "lazy leg premium target % (None = ride to EOD, mirrors target_pct's default)"},
        "lazy_moneyness": {"type": "str", "default": "itm1",
                           "description": "moneyness for the fresh lazy-leg strike lock (independent of the primary's moneyness)"},
        "entry_cutoff": {"type": "str", "default": None,
                         "description": "IST HH:MM — no NEW triggers or lazy armings at/after this time (None = no extra cutoff)"},
        "exit_time": {"type": "str", "default": None,
                     "description": "IST HH:MM per-deployment square time, clamped to no later than the system EOD square (None = EOD only)"},
        "session_max_loss_rupees": {"type": "float", "fixed": None, "default": None,
                                    "description": "realized-only session day-stop (rupees) — a risk control, not a search dimension"},
        "session_max_profit_rupees": {"type": "float", "fixed": None, "default": None,
                                      "description": "realized-only session day-stop (rupees) — a risk control, not a search dimension"},
        "vix_min": {"type": "float", "fixed": None, "default": None,
                   "description": "INDIAVIX gate lower bound (asof session start); unverifiable VIX with a configured gate = no_setup, never a silent pass"},
        "vix_max": {"type": "float", "fixed": None, "default": None,
                   "description": "INDIAVIX gate upper bound (asof session start)"},
    }
    is_builtin = False

    def evaluate(self, row: pd.Series, prev: pd.Series, params: Dict[str, Any],
                 ctx: Dict[str, Any]) -> Signal:
        return Signal(direction="NONE", reasons=["premium_momentum runs via the Track B evaluator branch"])
