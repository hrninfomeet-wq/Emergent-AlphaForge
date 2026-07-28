"""Step 1 of the config-block generalization: route on CONFIG PRESENCE.

`premium_trigger_dispatch.py`'s own docstring states the design intent —
"route on CONFIG PRESENCE, not on `strategy_id == 'premium_momentum'`" — and then
the code does the opposite at two caller-side sites:

  * `premium_trigger_dispatch.dispatch_full_backtest`  (`if strategy_id != "premium_momentum": return None`)
  * `runtime._run_paired_option_backtest`              (`if req.strategy_id == "premium_momentum" and ...`)

`dispatch_backtest` is ALREADY strategy-agnostic and is what the parity test
protects, so nothing here touches it, `to_backtest_params`, or
`PremiumTriggerConfig`'s field semantics.

Two defects are fixed alongside the routing, because both would hit every
authored strategy the moment config blocks exist:

1. **Silent field loss.** `StrategyBase.merged_params` is a strict allow-list
   keyed on the plugin's `parameter_schema`, and `premium_momentum`'s schema is
   missing 6 of the config's 14 fields. `runtime` rescues only `lots` and
   `cost_config`; `stop_pts`, `target_pts`, `trail_x`, `trail_y` are dropped, so a
   point-based stop/target or an X-Y trail is silently ignored while the run
   reports numbers as though it had applied. Same class as the
   `vix_boost_threshold` dead knob.

2. **Absent vs invalid conflated.** Today any `ValidationError` is swallowed and
   becomes `None`, i.e. indistinguishable from "this strategy has no config at
   all". A typo'd config must not silently degrade into a different execution
   path that reports plausible numbers.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
# the parity test's fixture is the single source of truth for the scenario shape
sys.path.insert(0, os.path.dirname(__file__))

from app.premium_trigger_config import PremiumTriggerConfig  # noqa: E402
from app.premium_trigger_dispatch import (  # noqa: E402
    _CONFIG_FIELDS,
    dispatch_full_backtest,
    extract_premium_trigger_config,
)

from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario  # noqa: E402


_VALID = {
    "reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
    "momentum_pct": 15.0, "target_pct": 50.0, "stop_pct": 20.0, "lots": 2,
}


# --------------------------------------------------------------------------- #
# _CONFIG_FIELDS must not be able to drift from the model
# --------------------------------------------------------------------------- #

def test_config_fields_are_derived_from_the_model_not_hand_maintained():
    """A hand-copied literal list silently stops extracting any field added to
    the model later — the extraction would just quietly ignore it."""
    assert set(_CONFIG_FIELDS) == set(PremiumTriggerConfig.model_fields), (
        "_CONFIG_FIELDS must be derived from PremiumTriggerConfig.model_fields")


# --------------------------------------------------------------------------- #
# absent vs invalid are DIFFERENT answers
# --------------------------------------------------------------------------- #

def test_no_config_fields_at_all_reports_absent():
    cfg, reason = extract_premium_trigger_config({"ema_fast": 9, "rsi_bull_thr": 52})
    assert cfg is None
    assert reason == "absent"


def test_a_valid_config_is_extracted():
    cfg, reason = extract_premium_trigger_config(dict(_VALID))
    assert isinstance(cfg, PremiumTriggerConfig)
    assert reason is None
    assert cfg.momentum_pct == 15.0
    assert cfg.lots == 2


def test_present_but_INVALID_is_not_reported_as_absent():
    """THE distinction. A typo'd config that silently becomes 'absent' would fall
    through to a different execution path and report plausible numbers for a
    strategy the user never configured."""
    broken = dict(_VALID)
    broken["momentum_pts"] = 10.0          # both momentum_pct AND momentum_pts => invalid
    cfg, reason = extract_premium_trigger_config(broken)
    assert cfg is None
    assert reason is not None and reason != "absent"
    assert "invalid" in reason


def test_a_lone_trail_leg_is_invalid_not_absent():
    broken = dict(_VALID)
    broken["trail_x"] = 5.0                # trail_y missing
    cfg, reason = extract_premium_trigger_config(broken)
    assert cfg is None
    assert reason is not None and reason != "absent"


# --------------------------------------------------------------------------- #
# the silent-drop bug
# --------------------------------------------------------------------------- #

def test_point_based_stops_and_the_xy_trail_are_NOT_dropped():
    """These 6 fields are absent from the plugin's parameter_schema, so anything
    routed through merged_params loses them. Extraction must read the RAW params."""
    raw = dict(_VALID)
    raw.pop("target_pct"); raw.pop("stop_pct")
    raw.update({"stop_pts": 7.5, "target_pts": 20.0, "trail_x": 5.0,
                "trail_y": 5.0, "cost_config": {"enabled": True}})
    cfg, reason = extract_premium_trigger_config(raw)
    assert reason is None and cfg is not None
    assert cfg.stop_pts == 7.5, "stop_pts was dropped"
    assert cfg.target_pts == 20.0, "target_pts was dropped"
    assert cfg.trail_x == 5.0 and cfg.trail_y == 5.0, "the X-Y trail was dropped"
    assert cfg.cost_config == {"enabled": True}
    assert cfg.lots == 2


def test_extraction_ignores_unrelated_strategy_params():
    """A real strategy's params carry indicator knobs too; they must not make the
    config invalid via extra='forbid'."""
    raw = dict(_VALID)
    raw.update({"ema_fast": 9, "signal_threshold": 62, "cooldown_bars": 5})
    cfg, reason = extract_premium_trigger_config(raw)
    assert reason is None and cfg is not None


# --------------------------------------------------------------------------- #
# routing: presence, not identity
# --------------------------------------------------------------------------- #

def test_ANY_strategy_id_with_a_valid_config_now_dispatches():
    """THE generalization. An authored strategy carrying a premium-trigger config
    must run it — that is the promise `classify_rule` already makes to users."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    out = dispatch_full_backtest(
        strategy_id="some_ai_authored_strategy",
        merged_params=dict(_VALID),
        spot_df=spot.copy(), option_candles=opt.copy(), contracts=list(contracts),
        instrument="NIFTY",
    )
    assert out is not None, "a valid config must dispatch regardless of strategy_id"
    assert out.get("trades"), "the dispatched run should produce trades"


def test_premium_momentum_itself_still_dispatches_unchanged():
    spot, opt, contracts = _simple_ce_wins_scenario()
    out = dispatch_full_backtest(
        strategy_id="premium_momentum", merged_params=dict(_VALID),
        spot_df=spot.copy(), option_candles=opt.copy(), contracts=list(contracts),
        instrument="NIFTY",
    )
    assert out is not None and out.get("trades")


def test_a_strategy_with_NO_config_is_untouched():
    """Byte-identical no-op for every ordinary strategy: no config, no dispatch,
    zero side effects — exactly today's behaviour for non-premium ids."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    out = dispatch_full_backtest(
        strategy_id="confluence_scalper",
        merged_params={"ema_fast": 9, "ema_slow": 21, "signal_threshold": 62},
        spot_df=spot.copy(), option_candles=opt.copy(), contracts=list(contracts),
        instrument="NIFTY",
    )
    assert out is None


def test_an_INVALID_config_still_refuses_rather_than_running_something_else():
    """Fail closed: a broken config must not fall through to a path that produces
    numbers the user's config never described."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    broken = dict(_VALID); broken["momentum_pts"] = 10.0
    out = dispatch_full_backtest(
        strategy_id="some_ai_authored_strategy", merged_params=broken,
        spot_df=spot.copy(), option_candles=opt.copy(), contracts=list(contracts),
        instrument="NIFTY",
    )
    assert out is None


def test_runtime_routes_on_config_presence_not_on_strategy_id():
    """Source contract for the SECOND guard. runtime._run_paired_option_backtest
    carried its own duplicate `req.strategy_id == "premium_momentum"` check; a
    generalization that fixed only the dispatch-side guard would still be blocked
    there for every authored strategy."""
    import inspect
    from app import runtime as rt
    src = inspect.getsource(rt._run_paired_option_backtest)
    assert 'req.strategy_id == "premium_momentum"' not in src, (
        "runtime must route on config presence, not on a hardcoded strategy id")
    assert "extract_premium_trigger_config" in src or "premium_trigger_cfg" in src
