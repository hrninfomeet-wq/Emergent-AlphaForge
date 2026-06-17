"""Tests for the costs_disabled advisory warning (Task 10).

The option backtest persists its cost model under
source_doc["option_backtest"]["cost_config"] (CostConfig.to_dict(), which always
emits an "enabled" boolean). When costs were OFF the reported option P&L is gross
(no brokerage/STT/charges/spread) -> optimistic. We surface that as an ADVISORY
warning. It must NEVER gate: presence only appends to `warnings` (and flips the
existing `acknowledgment_required`, which any warning already does).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.deployment_quality import (  # noqa: E402
    SEVERITY_WARNING,
    evaluate_source_quality,
)


def _ids(res):
    return {w["id"] for w in res["warnings"]}


def _opt_source(net, *, costs_enabled, omit_cost_config=False, paired=100, spot=100):
    """A backtest_run-shaped source doc with a paired-option result.

    `costs_enabled` controls option_backtest.cost_config.enabled (the field the
    real CostConfig.to_dict() always emits). `omit_cost_config` drops the whole
    cost_config block to exercise the absent-flag path.
    """
    om = {
        "portfolio": {"net_pnl_value": net, "total_return_pct": net / 2000.0,
                      "ending_equity": 200000 + net, "max_drawdown_pct": -20.0,
                      "curve": [{"equity_value": 200000 + net}]},
        "coverage": {"spot_trade_count": spot, "paired_trade_count": paired,
                     "skipped_by_cap": 0, "missing_contract": 0, "missing_entry_candle": 0},
    }
    if not omit_cost_config:
        om["cost_config"] = {"enabled": costs_enabled, "brokerage_per_order": 20.0}
    return {
        "metrics": {"trade_count": 120, "sharpe": 1.0, "win_rate": 50.0,
                    "max_dd_pts": 50.0, "total_pnl_pts": 100.0},
        "option_backtest": om,
    }


# (1) option P&L + costs OFF -> costs_disabled warning present
def test_costs_disabled_fires_when_costs_off_on_option_source():
    res = evaluate_source_quality(_opt_source(50000, costs_enabled=False))
    assert "costs_disabled" in _ids(res)


# (1b) costs flag absent entirely (no cost_config block) -> still fires
def test_costs_disabled_fires_when_cost_config_absent():
    res = evaluate_source_quality(_opt_source(50000, costs_enabled=False, omit_cost_config=True))
    assert "costs_disabled" in _ids(res)


# (2) option P&L + costs ON -> no costs_disabled warning
def test_costs_disabled_absent_when_costs_on():
    res = evaluate_source_quality(_opt_source(50000, costs_enabled=True))
    assert "costs_disabled" not in _ids(res)


# (3) spot-only doc (no option P&L) -> no costs_disabled warning (option-specific)
def test_costs_disabled_absent_on_spot_only_source():
    src = {"metrics": {"trade_count": 120, "sharpe": 1.0, "win_rate": 50.0,
                       "max_dd_pts": 50.0, "total_pnl_pts": 100.0},
           "walkforward": {"is_vs_oos": {"avg_is_win_rate": 60.0, "avg_oos_win_rate": 55.0,
                                         "divergence_warning": False}}}
    res = evaluate_source_quality(src)
    assert "costs_disabled" not in _ids(res)


# (4) severity is advisory (warning), snapshot key present, and it only appends
def test_costs_disabled_is_advisory_warning_and_appends_only():
    res = evaluate_source_quality(_opt_source(50000, costs_enabled=False))
    w = next(w for w in res["warnings"] if w["id"] == "costs_disabled")
    assert w["severity"] == SEVERITY_WARNING
    # Advisory mechanism: any warning sets acknowledgment_required; no separate gate.
    assert res["acknowledgment_required"] is True
    # Return shape unchanged beyond the appended warning + snapshot echo.
    assert res["metrics_snapshot"]["option_costs_enabled"] is False
    assert set(res.keys()) == {
        "acknowledgment_required", "warnings", "metrics_snapshot", "thresholds", "computed_at"}


# (4b) costs ON: snapshot reflects True; spot-only: snapshot is None
def test_option_costs_enabled_snapshot_reflects_state():
    on = evaluate_source_quality(_opt_source(50000, costs_enabled=True))
    assert on["metrics_snapshot"]["option_costs_enabled"] is True
    spot = evaluate_source_quality({"metrics": {"trade_count": 50, "sharpe": 1.0}})
    assert spot["metrics_snapshot"]["option_costs_enabled"] is None
