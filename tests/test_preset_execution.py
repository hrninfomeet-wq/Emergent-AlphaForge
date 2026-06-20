"""Tests for the preset execution-policy derivation (app/preset_execution.py).

A preset must carry the option execution context its result was validated
under, so backtest -> optimize -> deploy is one artifact. These cover the
derivation from an optimizer/WFO option_config.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.preset_execution import execution_from_option_config  # noqa: E402


def test_none_when_no_option_config():
    assert execution_from_option_config(None) is None
    assert execution_from_option_config({}) is None


def test_defaults_fill_in():
    ex = execution_from_option_config({"moneyness": None})
    assert ex["moneyness"] == "atm"
    assert ex["exit_mode"] == "spot_exit"
    assert ex["lots"] == 1
    assert "option_target_pts" not in ex  # unset levels stay absent


def test_full_option_levels_config_carries_over():
    ex = execution_from_option_config({
        "moneyness": "otm1",
        "dte_filter": [0, 1, 2],
        "exit_mode": "option_levels",
        "lots": 2,
        "option_target_pts": 40,
        "option_stop_pts": 30,
        "cost_config": {"enabled": True, "brokerage_per_order": 20, "spread_pct_of_premium": 1.0},
    })
    assert ex["moneyness"] == "otm1"
    assert ex["dte_filter"] == [0, 1, 2]
    assert ex["exit_mode"] == "option_levels"
    assert ex["lots"] == 2
    assert ex["option_target_pts"] == 40.0
    assert ex["option_stop_pts"] == 30.0
    assert "option_target_pct" not in ex
    assert ex["cost_config"] == {
        "enabled": True, "brokerage_per_order": 20.0, "spread_pct_of_premium": 1.0,
    }


def test_pct_levels_and_disabled_costs():
    ex = execution_from_option_config({
        "exit_mode": "option_levels",
        "option_target_pct": 50,
        "option_stop_pct": 25,
        "cost_config": {"enabled": False, "brokerage_per_order": 20},
    })
    assert ex["option_target_pct"] == 50.0
    assert ex["option_stop_pct"] == 25.0
    assert "option_target_pts" not in ex
    assert "cost_config" not in ex  # disabled costs are not carried


def test_zero_and_garbage_levels_are_dropped():
    ex = execution_from_option_config({
        "option_target_pts": 0,
        "option_stop_pts": "",
        "option_target_pct": "garbage",
        "option_stop_pct": -5,
    })
    for key in ("option_target_pts", "option_stop_pts", "option_target_pct", "option_stop_pct"):
        assert key not in ex


def test_execution_carries_sizing_config_when_present():
    ex = execution_from_option_config({
        "moneyness": "atm", "exit_mode": "spot_exit", "lots": 1,
        "sizing_config": {"enabled": True, "mode": "premium_at_risk",
                          "capital": 200_000, "risk_per_trade_pct": 1.0, "max_lots": 10},
    })
    assert ex["sizing_config"]["enabled"] is True
    assert ex["sizing_config"]["mode"] == "premium_at_risk"
    assert ex["sizing_config"]["capital"] == 200_000
    assert ex["sizing_config"]["max_lots"] == 10
    assert ex["sizing_config"]["risk_per_trade_pct"] == 1.0
    assert "assumed_stop_pct_of_premium" in ex["sizing_config"]  # canonical shape: default-filled


def test_execution_omits_sizing_config_when_absent():
    ex = execution_from_option_config({"moneyness": "atm", "exit_mode": "spot_exit", "lots": 1})
    assert "sizing_config" not in ex
