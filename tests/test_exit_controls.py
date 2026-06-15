# tests/test_exit_controls.py
# NEW test files live at repo-root tests/ and must bootstrap backend onto sys.path
# (mirrors tests/test_option_backtest.py / test_deployment_kill_switch.py). Run from REPO ROOT.
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.exit_controls import ExitControlsConfig, effective_premium_stop


def test_disabled_returns_base_stop_only():
    cfg = ExitControlsConfig.from_dict({"enabled": False})
    assert effective_premium_stop(entry=100.0, running_max=200.0, base_stop=80.0, cfg=cfg) == 80.0
    assert effective_premium_stop(entry=100.0, running_max=200.0, base_stop=None, cfg=cfg) is None


def test_breakeven_pct_raises_to_entry_once_triggered():
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "breakeven": {"trigger": 0.30, "lock": 0.0}})
    # not yet up 30% -> base stop only
    assert effective_premium_stop(entry=100.0, running_max=120.0, base_stop=80.0, cfg=cfg) == 80.0
    # up 30% -> stop ratchets to entry (100)
    assert effective_premium_stop(entry=100.0, running_max=130.0, base_stop=80.0, cfg=cfg) == 100.0


def test_trailing_pct_trails_running_max():
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "trailing": {"activation": 0.40, "distance": 0.25}})
    # not activated (needs +40%)
    assert effective_premium_stop(entry=100.0, running_max=130.0, base_stop=80.0, cfg=cfg) == 80.0
    # activated at +50% -> trail = 150*(1-0.25)=112.5
    assert effective_premium_stop(entry=100.0, running_max=150.0, base_stop=80.0, cfg=cfg) == 112.5


def test_pts_unit_uses_additive_levels():
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pts",
                                        "breakeven": {"trigger": 20.0, "lock": 5.0},
                                        "trailing": {"activation": 30.0, "distance": 10.0}})
    # up 35 pts (rm=135): breakeven lock = 105; trail = 135-10 = 125 -> max = 125
    assert effective_premium_stop(entry=100.0, running_max=135.0, base_stop=80.0, cfg=cfg) == 125.0


def test_monotonic_never_below_base():
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "trailing": {"activation": 0.10, "distance": 0.90}})
    # huge distance would put trail below base; effective must not drop below base 80
    assert effective_premium_stop(entry=100.0, running_max=120.0, base_stop=80.0, cfg=cfg) == 80.0


from app.exit_controls import stop_fill_price


def test_stop_fill_gaps_to_open_below_level():
    assert stop_fill_price(150.0, "STOP", bar_open=130.0) == 130.0       # gap-down fills at open


def test_stop_fill_no_gap_uses_level():
    assert stop_fill_price(150.0, "STOP", bar_open=151.0) == 150.0       # open above stop -> level
    assert stop_fill_price(150.0, "STOP", bar_open=150.0) == 150.0       # boundary -> level


def test_stop_fill_non_stop_reason_uses_level():
    assert stop_fill_price(150.0, "TARGET", bar_open=130.0) == 150.0     # only STOP is gap-clamped


def test_stop_fill_none_open_uses_level():
    assert stop_fill_price(150.0, "STOP", bar_open=None) == 150.0


def test_from_dict_ignores_garbage_values():
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pts",
                                        "breakeven": {"trigger": "bad", "lock": None},
                                        "trailing": {"activation": "", "distance": 10.0}})
    assert cfg.enabled is True and cfg.unit == "pts"
    assert cfg.be_trigger == 0.0 and cfg.be_lock == 0.0     # garbage left at defaults
    assert cfg.trail_distance == 10.0
