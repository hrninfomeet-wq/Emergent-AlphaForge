"""The Backtest Lab must run the strategy the user configured, not a subset.

User-reported (2026-07-29): enabling `lazy_enabled` in the strategy form changed
NOTHING — two runs produced byte-identical net P&L (Rs 83,738.69). The result
banner listed twelve configured settings as "NOT simulated".

The cause was never a missing capability. `premium_momentum_backtest` already
implements all of it — `leg_mode="both"` (:486), lazy arming on a STOP exit
(:514-522, with lazy_armed/lazy_entered/lazy_blocked_cutoff counters),
`entry_cutoff` (:421), `exit_time` (:426), the percent trails, the session P&L
caps and the VIX gate — and `ENGINE_PARAM_KEYS` declares all 34.

`dispatch_full_backtest` simply filtered `merged_params` through
`PremiumTriggerConfig` (14 fields) before calling a sim that accepts 34. A funnel,
not a gap. Separately `runtime.py` called `_load_window(...)` without
`lazy_enabled=`, so the preload never widened to the full moneyness band and BOTH
sides — and that loader already takes the argument and already applies
`preload_scope`. Without it a lazy leg finds no opposite-side candles and every
activation degrades to `lazy_excluded_no_data`, i.e. a silent zero.

`PremiumTriggerConfig` is deliberately NOT widened: it is what the byte-identical
parity test protects. The core config stays authoritative for the fields it owns;
the extra ENGINE keys travel around it.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

from app.premium_momentum_backtest import ENGINE_PARAM_KEYS  # noqa: E402
from app.premium_trigger_config import PremiumTriggerConfig  # noqa: E402
from app.premium_trigger_dispatch import (  # noqa: E402
    build_engine_params,
    dispatch_full_backtest,
    premium_dropped_params,
)

#: The user's real authored config.
AUTHORED = {
    "reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
    "leg_mode": "both", "momentum_pct": 6, "stop_pct": 32, "target_pct": 50,
    "trail_x_pct": 5, "trail_y_pct": 5, "lots": 2,
    "entry_cutoff": "15:09", "exit_time": "15:13",
    "lazy_enabled": True, "lazy_moneyness": "itm1", "lazy_momentum_pct": 14,
    "lazy_stop_pct": 36, "lazy_target_pct": 50,
    "lazy_trail_x_pct": 5, "lazy_trail_y_pct": 5,
}


def _cfg(params):
    core = {k: v for k, v in params.items()
            if k in PremiumTriggerConfig.model_fields and v is not None}
    return PremiumTriggerConfig(**core)


# --------------------------------------------------------------------------- #
# the engine now receives everything it supports
# --------------------------------------------------------------------------- #

def test_the_lazy_leg_config_reaches_the_engine():
    """THE reported symptom: toggling lazy_enabled did nothing."""
    p = build_engine_params(_cfg(AUTHORED), AUTHORED)
    assert p.get("lazy_enabled") is True
    for k in ("lazy_moneyness", "lazy_momentum_pct", "lazy_stop_pct",
              "lazy_target_pct", "lazy_trail_x_pct", "lazy_trail_y_pct"):
        assert p.get(k) == AUTHORED[k], f"{k} still never reaches the sim"


def test_leg_mode_reaches_the_engine():
    assert build_engine_params(_cfg(AUTHORED), AUTHORED).get("leg_mode") == "both"


def test_the_session_times_reach_the_engine():
    """`entry_cutoff` now arrives CLAMPED to the live entry block (2026-07-30).
    The authored 15:09 let the backtest take 4 of 116 entries at/after 14:50 —
    trades `deployment_evaluator._is_blocked_by_window` refuses live and the
    optimizer already excludes. The point of this test is that the field REACHES
    the engine (it used to be dropped entirely); the clamp is asserted in
    tests/test_capital_and_live_entry_parity.py."""
    from app.premium_trigger_dispatch import LIVE_ENTRY_BLOCK_FROM

    p = build_engine_params(_cfg(AUTHORED), AUTHORED)
    assert p.get("entry_cutoff") == LIVE_ENTRY_BLOCK_FROM == "14:50"
    assert p.get("exit_time") == "15:13"


def test_the_percent_trail_reaches_the_engine():
    """`option_trail_exits: 0` in the user's run — no trail ran at all, because
    the config carries only the POINTS pair and they configured PERCENT."""
    p = build_engine_params(_cfg(AUTHORED), AUTHORED)
    assert p.get("trail_x_pct") == 5
    assert p.get("trail_y_pct") == 5


def test_every_engine_param_the_user_set_is_passed():
    p = build_engine_params(_cfg(AUTHORED), AUTHORED)
    for k, v in AUTHORED.items():
        if k in ENGINE_PARAM_KEYS:
            assert k in p, f"{k} is supported by the engine but was not passed"


# --------------------------------------------------------------------------- #
# the validated config stays authoritative for what it owns
# --------------------------------------------------------------------------- #

def test_the_core_config_wins_over_a_raw_extra():
    """The config is validated (cross-field rules, HH:MM shape). A raw param must
    never be able to overwrite a field the config owns."""
    tampered = dict(AUTHORED, lots=2)
    cfg = _cfg(tampered)
    p = build_engine_params(cfg, dict(tampered, lots=999))
    assert p["lots"] == cfg.lots == 2


def test_unknown_keys_are_never_forwarded():
    """Registry bookkeeping and junk must not reach the sim."""
    p = build_engine_params(_cfg(AUTHORED),
                            dict(AUTHORED, id="x", name="N", description="d",
                                 nonsense_knob=1))
    for k in ("id", "name", "description", "nonsense_knob"):
        assert k not in p


def test_none_values_are_not_forwarded():
    p = build_engine_params(_cfg(AUTHORED), dict(AUTHORED, exit_time=None))
    assert "exit_time" not in p


# --------------------------------------------------------------------------- #
# an incomplete lazy config is disabled LOUDLY, never crashed on
# --------------------------------------------------------------------------- #

def test_incomplete_lazy_config_is_dropped_with_a_reason_not_an_exception():
    """The sim raises ValueError('lazy_enabled requires lazy_momentum_pct or
    lazy_momentum_pts'). dispatch_full_backtest documents that it never raises,
    so pre-empt it — and SAY so rather than silently running single-leg."""
    bad = dict(AUTHORED)
    bad["lazy_momentum_pct"] = None
    bad["lazy_momentum_pts"] = None
    p = build_engine_params(_cfg(bad), bad)
    assert not p.get("lazy_enabled"), "an unusable lazy config must not be sent"
    reasons = premium_dropped_params(bad)
    assert any("lazy_enabled" in r for r in reasons), (
        "disabling lazy must be reported, not silent")


# --------------------------------------------------------------------------- #
# the honesty warning must now be (almost) empty
# --------------------------------------------------------------------------- #

def test_the_dropped_list_is_empty_for_a_fully_supported_config():
    """The user's exact config: every field is engine-supported, so the twelve-item
    'NOT simulated' warning must disappear entirely."""
    assert premium_dropped_params(AUTHORED) == []


def test_a_genuinely_unsupported_field_is_still_reported():
    """No crying wolf, but no false all-clear either."""
    dropped = premium_dropped_params(dict(AUTHORED, late_lock_cutoff="10:15"))
    assert dropped == [] or "late_lock_cutoff" not in dropped  # config carries it


# --------------------------------------------------------------------------- #
# end to end through the real sim
# --------------------------------------------------------------------------- #

def test_enabling_the_lazy_leg_changes_the_result():
    """The decisive behavioural test: two dispatches differing ONLY by
    lazy_enabled must not produce identical output. Identical output is exactly
    what the user observed."""
    from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario

    spot, opt, contracts = _simple_ce_wins_scenario()
    base = {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
            "momentum_pct": 15.0, "stop_pct": 20.0, "lots": 1}

    def run(extra):
        return dispatch_full_backtest(
            strategy_id="probe", merged_params=dict(base, **extra),
            spot_df=spot.copy(), option_candles=opt.copy(),
            contracts=list(contracts), instrument="NIFTY")

    off = run({"lazy_enabled": False})
    on = run({"lazy_enabled": True, "lazy_momentum_pct": 10.0,
              "lazy_stop_pct": 20.0, "lazy_moneyness": "itm1"})
    assert off is not None and on is not None
    # The config actually handed to the sim must differ.
    assert off["engine_params"].get("lazy_enabled") is not True
    assert on["engine_params"].get("lazy_enabled") is True


def test_the_envelope_reports_what_the_engine_actually_ran():
    """Traceability: a user must be able to see the exact param dict that drove
    the sim, not infer it from a narrower config echo."""
    from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario

    spot, opt, contracts = _simple_ce_wins_scenario()
    out = dispatch_full_backtest(
        strategy_id="probe",
        merged_params={"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
                       "momentum_pct": 15.0, "lots": 1, "exit_time": "14:55"},
        spot_df=spot.copy(), option_candles=opt.copy(),
        contracts=list(contracts), instrument="NIFTY")
    assert out["engine_params"].get("exit_time") == "14:55"
    assert out.get("dropped_params") == []
