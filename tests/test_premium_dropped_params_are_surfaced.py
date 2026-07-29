"""A config field the backtest cannot honour must be SAID, not silently dropped.

The worst finding of the 2026-07-29 audit. The user authored a two-leg strategy
with a lazy contingency — `leg_mode='both'`, `lazy_enabled=True` and six `lazy_*`
knobs — and the authoring wizard told them that capability is `BUILDABLE_NOW` and
live-feasible (it is: Phase 5B shipped 2026-07-17, and the deployment path really
does honour those fields).

But `dispatch_full_backtest` builds its run config from `PremiumTriggerConfig`
only, and that model deliberately carries none of them. `runtime.py` even says so
in a comment — "the general Backtest Lab path stays single-leg by design …
dispatch would drop them anyway". So the backtest silently measured a DIFFERENT
strategy from the one configured and from the one that would be deployed, and
reported its numbers with no caveat at all.

Whether to SIMULATE the lazy leg in backtest is a separate, larger question. What
is not in question is that the user must be told, because they are otherwise
making a deployment decision on numbers that do not describe their strategy —
made worse by the optimizer having spent trials tuning `lazy_*` knobs that could
not move the objective.

Entry-strict / exit-permissive does not apply here: this is a REPORTING honesty
rule, and the safe direction is always to over-disclose.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

from app.premium_trigger_dispatch import (  # noqa: E402
    dispatch_full_backtest,
    premium_dropped_params,
)

#: The user's real authored config.
AUTHORED = {
    "reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
    "leg_mode": "both", "momentum_pct": 15, "stop_pct": 20, "target_pct": 50,
    "trail_x_pct": 5, "trail_y_pct": 5, "lots": 2,
    "entry_cutoff": "15:09", "exit_time": "15:13",
    "lazy_enabled": True, "lazy_moneyness": "itm1", "lazy_momentum_pct": 10,
    "lazy_stop_pct": 10, "lazy_target_pct": 50,
    "lazy_trail_x_pct": 5, "lazy_trail_y_pct": 5,
}


def test_the_lazy_leg_fields_are_reported_as_dropped():
    dropped = set(premium_dropped_params(AUTHORED))
    for name in ("lazy_enabled", "lazy_momentum_pct", "lazy_stop_pct",
                 "lazy_target_pct", "lazy_moneyness"):
        assert name in dropped, f"{name} is silently ignored by the backtest"


def test_leg_mode_is_reported_as_dropped():
    """'both' vs 'first_to_trigger' is a different strategy, not a nuance."""
    assert "leg_mode" in premium_dropped_params(AUTHORED)


def test_the_session_time_fields_are_reported_as_dropped():
    dropped = set(premium_dropped_params(AUTHORED))
    assert "entry_cutoff" in dropped
    assert "exit_time" in dropped


def test_fields_the_config_DOES_carry_are_not_reported():
    """No crying wolf — a warning that lists honoured fields teaches the user to
    ignore it."""
    dropped = set(premium_dropped_params(AUTHORED))
    for name in ("reference_time", "moneyness", "side", "momentum_pct",
                 "stop_pct", "target_pct", "lots"):
        assert name not in dropped, f"{name} IS honoured; do not warn about it"


def test_an_ordinary_premium_config_drops_nothing():
    """A config that uses only supported fields must produce a clean, silent run."""
    clean = {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
             "momentum_pct": 15.0, "stop_pct": 20.0, "lots": 1}
    assert premium_dropped_params(clean) == []


def test_registry_bookkeeping_is_never_reported_as_a_dropped_param():
    """merged_params carries id/name/description; those are not user config."""
    noisy = dict(clean_extra=None, **{"reference_time": "09:31", "moneyness": "itm1",
                                      "side": "ce", "momentum_pct": 15.0})
    noisy.update({"id": "x", "name": "X", "description": "d", "version": "1.0.0"})
    dropped = set(premium_dropped_params(noisy))
    assert not (dropped & {"id", "name", "description", "version", "clean_extra"})


def test_none_valued_fields_are_not_reported():
    """A field the user left unset was never promised anything."""
    cfg = dict(AUTHORED)
    cfg["lazy_enabled"] = None
    cfg["lazy_momentum_pct"] = None
    dropped = set(premium_dropped_params(cfg))
    assert "lazy_enabled" not in dropped
    assert "lazy_momentum_pct" not in dropped


def test_the_result_envelope_carries_the_warning():
    """End-to-end: the frontend needs it on the result, not just in a log line."""
    from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario

    spot, opt, contracts = _simple_ce_wins_scenario()
    params = {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
              "momentum_pct": 15.0, "lots": 1, "lazy_enabled": True,
              "leg_mode": "both", "exit_time": "15:13"}
    out = dispatch_full_backtest(
        strategy_id="probe", merged_params=params, spot_df=spot.copy(),
        option_candles=opt.copy(), contracts=list(contracts), instrument="NIFTY")
    assert out is not None
    surfaced = set(out.get("dropped_params") or [])
    assert {"lazy_enabled", "leg_mode", "exit_time"} <= surfaced


def test_a_clean_run_reports_an_empty_list_not_a_missing_key():
    """A missing key would make the frontend's check ambiguous."""
    from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario

    spot, opt, contracts = _simple_ce_wins_scenario()
    out = dispatch_full_backtest(
        strategy_id="probe",
        merged_params={"reference_time": "09:31", "moneyness": "itm1",
                       "side": "ce", "momentum_pct": 15.0, "lots": 1},
        spot_df=spot.copy(), option_candles=opt.copy(),
        contracts=list(contracts), instrument="NIFTY")
    assert out.get("dropped_params") == []
