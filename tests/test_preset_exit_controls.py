import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.preset_execution import execution_from_option_config


def test_execution_carries_exit_controls_and_daily_caps():
    oc = {"exit_mode": "option_levels", "cost_config": {"enabled": True},
          "exit_controls": {"enabled": True, "unit": "pct", "trailing": {"activation": 0.4, "distance": 0.25}},
          "daily_caps": {"loss": 15000, "max_trades": 6}}
    ex = execution_from_option_config(oc)
    assert ex.get("exit_controls") == oc["exit_controls"]
    assert ex.get("daily_caps") == oc["daily_caps"]


def test_execution_without_overlay_unchanged():
    ex = execution_from_option_config({"exit_mode": "spot_exit"})
    assert ex.get("exit_controls") is None
    assert ex.get("daily_caps") is None
