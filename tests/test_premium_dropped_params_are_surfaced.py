"""A config field the backtest cannot honour must be SAID, not silently dropped.

HISTORY — this file was written on 2026-07-29 when the Backtest Lab really did
discard `leg_mode`, `entry_cutoff`, `exit_time`, the percent trails and the whole
lazy block, and it asserted that those drops were DISCLOSED. Later the same day
the underlying gap was closed: `premium_momentum_backtest` already implemented
every one of them (`ENGINE_PARAM_KEYS`, 34 keys), and `dispatch_full_backtest` was
simply filtering params through `PremiumTriggerConfig`'s 14. `build_engine_params`
now forwards the full surface, so nothing is dropped any more.

The tests were therefore INVERTED rather than deleted: the disclosure mechanism
still has to work, because it is the only thing standing between a user and a
silently different strategy. What changed is what it should find — for a
fully-supported config, nothing.

The one case that must still fire is a lazy block that cannot run: the sim raises
`ValueError('lazy_enabled requires lazy_momentum_pct or lazy_momentum_pts')`, and
`dispatch_full_backtest` promises never to raise, so it is pre-empted — which
means it MUST be reported instead of quietly running single-leg.
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


# --------------------------------------------------------------------------- #
# nothing is dropped any more — the whole point of the widening
# --------------------------------------------------------------------------- #

def test_the_lazy_leg_fields_are_no_longer_dropped():
    """Inverted from the original assertion. The lazy contingency IS simulated."""
    dropped = set(premium_dropped_params(AUTHORED))
    for name in ("lazy_enabled", "lazy_momentum_pct", "lazy_stop_pct",
                 "lazy_target_pct", "lazy_moneyness"):
        assert name not in dropped, f"{name} should now reach the engine"


def test_leg_mode_is_no_longer_dropped():
    assert "leg_mode" not in premium_dropped_params(AUTHORED)


def test_the_session_time_fields_are_no_longer_dropped():
    dropped = set(premium_dropped_params(AUTHORED))
    assert "entry_cutoff" not in dropped
    assert "exit_time" not in dropped


def test_the_percent_trails_are_no_longer_dropped():
    dropped = set(premium_dropped_params(AUTHORED))
    assert "trail_x_pct" not in dropped and "trail_y_pct" not in dropped


def test_a_fully_supported_config_produces_no_warning_at_all():
    """The twelve-item 'NOT simulated' banner the user saw must be gone."""
    assert premium_dropped_params(AUTHORED) == []


# --------------------------------------------------------------------------- #
# the mechanism must still fire when something really cannot run
# --------------------------------------------------------------------------- #

def test_an_unusable_lazy_block_is_still_reported():
    """lazy_enabled with no lazy momentum trigger cannot run. Disabling it
    silently is exactly the failure this mechanism exists to prevent."""
    bad = dict(AUTHORED, lazy_momentum_pct=None, lazy_momentum_pts=None)
    reasons = premium_dropped_params(bad)
    assert any("lazy_enabled" in r for r in reasons)
    assert any("lazy_momentum_pct" in r for r in reasons), (
        "the message must name what is missing, not just that something is wrong")


def test_registry_bookkeeping_is_never_reported():
    noisy = dict(AUTHORED, id="x", name="X", description="d", version="1.0.0")
    dropped = set(premium_dropped_params(noisy))
    assert not (dropped & {"id", "name", "description", "version"})


def test_none_valued_fields_are_not_reported():
    """A field the user left unset was never promised anything."""
    cfg = dict(AUTHORED, lazy_enabled=None, lazy_momentum_pct=None)
    assert premium_dropped_params(cfg) == []


# --------------------------------------------------------------------------- #
# end to end
# --------------------------------------------------------------------------- #

def test_the_envelope_reports_an_empty_warning_for_a_supported_config():
    from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario

    spot, opt, contracts = _simple_ce_wins_scenario()
    params = {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
              "momentum_pct": 15.0, "lots": 1, "lazy_enabled": True,
              "lazy_momentum_pct": 10.0, "leg_mode": "both", "exit_time": "15:13"}
    out = dispatch_full_backtest(
        strategy_id="probe", merged_params=params, spot_df=spot.copy(),
        option_candles=opt.copy(), contracts=list(contracts), instrument="NIFTY")
    assert out is not None
    assert out.get("dropped_params") == []
    # and the engine really did receive them
    assert out["engine_params"].get("leg_mode") == "both"
    assert out["engine_params"].get("lazy_enabled") is True


def test_a_clean_run_reports_an_empty_list_not_a_missing_key():
    from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario

    spot, opt, contracts = _simple_ce_wins_scenario()
    out = dispatch_full_backtest(
        strategy_id="probe",
        merged_params={"reference_time": "09:31", "moneyness": "itm1",
                       "side": "ce", "momentum_pct": 15.0, "lots": 1},
        spot_df=spot.copy(), option_candles=opt.copy(),
        contracts=list(contracts), instrument="NIFTY")
    assert out.get("dropped_params") == []
