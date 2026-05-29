"""Tests for the deployment-quality / acknowledgment check (slice 9)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.deployment_quality import (  # noqa: E402
    MAX_DRAWDOWN_RATIO,
    MIN_SHARPE,
    MIN_TRADE_COUNT,
    SEVERITY_WARNING,
    WALK_FORWARD_RATIO_THRESHOLD,
    evaluate_source_quality,
)


def _good_metrics():
    return {
        "trade_count": 120,
        "win_rate": 58.0,
        "profit_factor": 1.45,
        "sharpe": 1.2,
        "max_dd_pts": 80.0,
        "total_pnl_pts": 1500.0,
    }


def _good_walkforward():
    return {
        "is_vs_oos": {
            "avg_is_win_rate": 60.0,
            "avg_oos_win_rate": 55.0,
            "divergence_warning": False,
            "fold_count": 3,
        },
    }


# ---- happy path ------------------------------------------------------------


def test_clean_source_yields_no_warnings_and_no_ack_required():
    source = {"metrics": _good_metrics(), "walkforward": _good_walkforward()}
    res = evaluate_source_quality(source)
    assert res["acknowledgment_required"] is False
    assert res["warnings"] == []
    assert res["metrics_snapshot"]["has_walkforward"] is True


# ---- missing walkforward ---------------------------------------------------


def test_missing_walkforward_triggers_warning():
    source = {"metrics": _good_metrics()}  # no walkforward
    res = evaluate_source_quality(source)
    assert res["acknowledgment_required"] is True
    ids = [w["id"] for w in res["warnings"]]
    assert "missing_walk_forward" in ids


# ---- walk-forward divergence ----------------------------------------------


def test_walk_forward_divergence_below_threshold_triggers_warning():
    """OOS win rate dropping >30% vs IS triggers an overfit warning."""
    wf = {
        "is_vs_oos": {
            "avg_is_win_rate": 60.0,
            "avg_oos_win_rate": 30.0,  # ratio = 0.5, below 0.7
            "divergence_warning": False,
            "fold_count": 3,
        },
    }
    source = {"metrics": _good_metrics(), "walkforward": wf}
    res = evaluate_source_quality(source)
    ids = [w["id"] for w in res["warnings"]]
    assert "walk_forward_divergence" in ids
    detail_w = next(w for w in res["warnings"] if w["id"] == "walk_forward_divergence")
    assert detail_w["value"]["ratio"] == 0.5


def test_explicit_divergence_flag_triggers_warning():
    """Even if the ratio is OK, an explicit flag from walk-forward should warn."""
    wf = {
        "is_vs_oos": {
            "avg_is_win_rate": 55.0,
            "avg_oos_win_rate": 50.0,  # ratio 0.91, above 0.7
            "divergence_warning": True,  # but the flag is set
            "fold_count": 3,
        },
    }
    source = {"metrics": _good_metrics(), "walkforward": wf}
    res = evaluate_source_quality(source)
    ids = [w["id"] for w in res["warnings"]]
    assert "walk_forward_divergence" in ids


# ---- low trade count -------------------------------------------------------


def test_low_trade_count_triggers_warning():
    metrics = _good_metrics()
    metrics["trade_count"] = 12  # < 30
    source = {"metrics": metrics, "walkforward": _good_walkforward()}
    res = evaluate_source_quality(source)
    ids = [w["id"] for w in res["warnings"]]
    assert "low_trade_count" in ids


def test_zero_trade_count_triggers_missing_warning():
    metrics = _good_metrics()
    metrics["trade_count"] = 0
    source = {"metrics": metrics, "walkforward": _good_walkforward()}
    res = evaluate_source_quality(source)
    ids = [w["id"] for w in res["warnings"]]
    assert "missing_trade_count" in ids


# ---- weak Sharpe -----------------------------------------------------------


def test_low_sharpe_triggers_warning():
    metrics = _good_metrics()
    metrics["sharpe"] = 0.3  # < 0.5
    source = {"metrics": metrics, "walkforward": _good_walkforward()}
    res = evaluate_source_quality(source)
    ids = [w["id"] for w in res["warnings"]]
    assert "weak_sharpe" in ids


def test_negative_sharpe_triggers_warning():
    metrics = _good_metrics()
    metrics["sharpe"] = -0.2
    source = {"metrics": metrics, "walkforward": _good_walkforward()}
    res = evaluate_source_quality(source)
    ids = [w["id"] for w in res["warnings"]]
    assert "weak_sharpe" in ids


def test_missing_sharpe_does_not_trigger_warning():
    """When Sharpe is None (not computable), don't warn - just don't count it."""
    metrics = _good_metrics()
    metrics["sharpe"] = None
    source = {"metrics": metrics, "walkforward": _good_walkforward()}
    res = evaluate_source_quality(source)
    ids = [w["id"] for w in res["warnings"]]
    assert "weak_sharpe" not in ids


# ---- large drawdown --------------------------------------------------------


def test_large_drawdown_triggers_warning():
    metrics = _good_metrics()
    metrics["max_dd_pts"] = -400.0   # 400 / 1500 = 26.7% > 15%
    metrics["total_pnl_pts"] = 1500.0
    source = {"metrics": metrics, "walkforward": _good_walkforward()}
    res = evaluate_source_quality(source)
    ids = [w["id"] for w in res["warnings"]]
    assert "large_drawdown" in ids


def test_drawdown_with_zero_pnl_does_not_divide_by_zero():
    metrics = _good_metrics()
    metrics["max_dd_pts"] = -500.0
    metrics["total_pnl_pts"] = 0.0
    source = {"metrics": metrics, "walkforward": _good_walkforward()}
    res = evaluate_source_quality(source)  # Must not raise
    ids = [w["id"] for w in res["warnings"]]
    assert "large_drawdown" not in ids


# ---- preset config wrapper -------------------------------------------------


def test_metrics_resolved_from_config_when_top_level_missing():
    """Some sources nest metrics under config (older preset format)."""
    source = {"config": {"metrics": _good_metrics()}, "walkforward": _good_walkforward()}
    res = evaluate_source_quality(source)
    assert res["metrics_snapshot"]["trade_count"] == 120


# ---- multiple warnings together --------------------------------------------


def test_multiple_warnings_aggregate():
    """A truly bad source should surface multiple distinct warnings."""
    metrics = {
        "trade_count": 5,        # too few
        "sharpe": 0.1,           # weak
        "max_dd_pts": -300.0,    # large vs total
        "total_pnl_pts": 100.0,
    }
    source = {"metrics": metrics}  # also no walkforward
    res = evaluate_source_quality(source)
    ids = sorted(w["id"] for w in res["warnings"])
    assert "missing_walk_forward" in ids
    assert "low_trade_count" in ids
    assert "weak_sharpe" in ids
    assert "large_drawdown" in ids


# ---- threshold constants are sane -----------------------------------------


def test_thresholds_match_user_spec():
    """If these change, downstream UI copy may need updating."""
    assert MIN_TRADE_COUNT == 30
    assert MIN_SHARPE == 0.5
    assert WALK_FORWARD_RATIO_THRESHOLD == 0.7
    assert MAX_DRAWDOWN_RATIO == 0.15


# ---- snapshot contents -----------------------------------------------------


def test_metrics_snapshot_includes_key_fields():
    source = {"metrics": _good_metrics(), "walkforward": _good_walkforward()}
    res = evaluate_source_quality(source)
    snap = res["metrics_snapshot"]
    for key in ("trade_count", "win_rate", "profit_factor", "sharpe", "max_dd_pts", "total_pnl_pts", "has_walkforward"):
        assert key in snap
