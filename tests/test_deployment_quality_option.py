"""Tests for the Fix-B option-rupee trust checks (Piece 3)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.deployment_quality import (  # noqa: E402
    QualityThresholds,
    evaluate_source_quality,
)


def _ids(res):
    return {w["id"] for w in res["warnings"]}


def _opt_source(net, *, paired=100, spot=100, skipped=0, curve=None,
                ending=None, max_dd=-20.0, missing_contract=0, missing_entry=0,
                metrics=None, walkforward="omit"):
    """A backtest_run-shaped source doc with a paired-option result."""
    if curve is None:
        curve = [{"equity_value": 200000 + net}]
    if ending is None:
        ending = 200000 + net
    src = {
        "metrics": metrics or {"trade_count": 120, "sharpe": 1.0, "win_rate": 50.0,
                               "max_dd_pts": 50.0, "total_pnl_pts": 100.0},
        "option_backtest": {
            "portfolio": {"net_pnl_value": net, "total_return_pct": net / 2000.0,
                          "ending_equity": ending, "max_drawdown_pct": max_dd, "curve": curve},
            "coverage": {"spot_trade_count": spot, "paired_trade_count": paired,
                         "skipped_by_cap": skipped, "missing_contract": missing_contract,
                         "missing_entry_candle": missing_entry},
        },
    }
    if walkforward != "omit":
        src["walkforward"] = walkforward
    return src


# (a) full-window negative with paired>0 -> option_full_window_negative
def test_full_window_negative_fires():
    res = evaluate_source_quality(_opt_source(-24451))
    assert "option_full_window_negative" in _ids(res)
    assert res["acknowledgment_required"] is True


# (a2) escalation: OOS positive but full-window negative -> "fragile" label
def test_fragile_escalation_when_oos_positive():
    res = evaluate_source_quality(_opt_source(-24451), evidence={"oos_return_pct": 9.66})
    w = next(w for w in res["warnings"] if w["id"] == "option_full_window_negative")
    assert "ragile" in w["label"]
    assert w["value"]["oos_signal"] == 9.66


# (b) ruin: negative ending equity / DD>=100 -> ruin_floor_breach
def test_ruin_floor_breach_fires_on_negative_equity():
    res = evaluate_source_quality(
        _opt_source(-412306, curve=[{"equity_value": -212306}], ending=-212306, max_dd=-211.5))
    assert "ruin_floor_breach" in _ids(res)


# (b2) zero-pair run: curve [], net 0.0 -> NO crash, NO fragility, NO ruin
def test_zero_pair_run_no_crash_no_false_negative():
    res = evaluate_source_quality(_opt_source(0, paired=0, curve=[], ending=200000, max_dd=0.0))
    assert "option_full_window_negative" not in _ids(res)
    assert "ruin_floor_breach" not in _ids(res)


# (c) low DATA coverage -> coverage_attrition
def test_coverage_attrition_fires_on_low_data_coverage():
    res = evaluate_source_quality(_opt_source(100, spot=100, paired=40, skipped=0, missing_contract=60))
    assert "coverage_attrition" in _ids(res)


# (c2) intentional caps must NOT trigger coverage_attrition
def test_intentional_cap_skips_not_flagged_as_attrition():
    res = evaluate_source_quality(_opt_source(100, spot=100, paired=40, skipped=60))
    assert "coverage_attrition" not in _ids(res)


# (d) clean positive option run -> none of the three
def test_clean_positive_option_run_no_option_warnings():
    res = evaluate_source_quality(_opt_source(50000, paired=100, spot=100))
    assert _ids(res).isdisjoint({"option_full_window_negative", "ruin_floor_breach", "coverage_attrition"})


# (e) spot-only doc + evidence=None -> unchanged (no option warnings, no crash)
def test_spot_only_source_byte_identical_warnings():
    src = {"metrics": {"trade_count": 120, "sharpe": 1.0, "win_rate": 50.0,
                       "max_dd_pts": 50.0, "total_pnl_pts": 100.0},
           "walkforward": {"is_vs_oos": {"avg_is_win_rate": 60.0, "avg_oos_win_rate": 55.0,
                                         "divergence_warning": False}}}
    res = evaluate_source_quality(src)
    assert _ids(res).isdisjoint({"option_full_window_negative", "ruin_floor_breach", "coverage_attrition"})


# (f) dedup: source WITH option_backtest AND evidence -> legacy option_oos suppressed
def test_dedup_suppresses_legacy_option_oos_when_option_backtest_present():
    res = evaluate_source_quality(_opt_source(-24451), evidence={"oos_return_pct": 5.0, "n_trials": 50})
    ids = _ids(res)
    assert "option_full_window_negative" in ids
    assert "option_oos_negative" not in ids
    assert "missing_option_oos" not in ids


# (g) option doc + evidence=None (results-page call) -> no crash, escalation falls back
def test_option_doc_with_evidence_none_does_not_crash():
    res = evaluate_source_quality(_opt_source(-24451, walkforward={"is_vs_oos": {"divergence_warning": False}}))
    assert "option_full_window_negative" in _ids(res)  # did not raise


# (g2) option doc with walkforward None -> no crash in the wf fallback
def test_option_doc_with_walkforward_none_does_not_crash():
    res = evaluate_source_quality(_opt_source(-24451, walkforward=None))
    assert "option_full_window_negative" in _ids(res)


# thresholds: new knobs in returned dict + from_overrides
def test_new_thresholds_present_and_overridable():
    res = evaluate_source_quality(_opt_source(50000))
    assert "ruin_floor" in res["thresholds"]
    assert "min_coverage_ratio" in res["thresholds"]
    th = QualityThresholds.from_overrides(ruin_floor=-5000.0, min_coverage_ratio=0.5)
    assert th.ruin_floor == -5000.0 and th.min_coverage_ratio == 0.5
