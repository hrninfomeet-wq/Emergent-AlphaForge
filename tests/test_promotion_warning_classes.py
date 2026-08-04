"""A measured zero is not a missing measurement.

Operator-directed promotion (2026-07-31) made research qualification advisory: a
finite candidate may be promoted with an acknowledgment. That widened what can
reach paper and, by the operator's separate live-enable act, real money.

Two of those cases are NOT the same risk and must not share one warning:

  * "this configuration ran and produced ZERO trades" — a definite, measured
    result. There is no executed behaviour to evaluate at all. `_score_trial`
    returns the `_DISQUALIFY` sentinel for exactly this (optimizer.py, `tc == 0`).
  * "the source does not report a trade count" — genuinely unknown.

Before this split, `trade_count == 0` emitted `missing_trade_count` / "does not
report a trade count. Cannot assess sample-size reliability", i.e. it reported a
measured zero as an absent field. It also shared `optimizer_guardrail_failed`
with the merely-weak-evidence cases (too few trades, one-sided direction).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.deployment_quality import evaluate_source_quality  # noqa: E402


def _wf():
    return {"is_vs_oos": {"avg_is_win_rate": 60.0, "avg_oos_win_rate": 55.0,
                          "divergence_warning": False}}


def _metrics(**over):
    m = {"trade_count": 120, "win_rate": 58.0, "profit_factor": 1.45,
         "sharpe": 1.2, "max_dd_pts": 80.0, "total_pnl_pts": 1500.0}
    m.update(over)
    return m


def _ids(source):
    return [w["id"] for w in evaluate_source_quality(source)["warnings"]]


def _by_id(source, wid):
    for w in evaluate_source_quality(source)["warnings"]:
        if w["id"] == wid:
            return w
    return None


def test_measured_zero_trades_is_its_own_class():
    """A run that fired nothing gets `zero_trade_result`, not `missing_trade_count`."""
    src = {"metrics": _metrics(trade_count=0), "walkforward": _wf()}
    ids = _ids(src)
    assert "zero_trade_result" in ids, (
        "a measured zero must be reported as a definite result, not an absent field")
    assert "missing_trade_count" not in ids, (
        "a measured zero must not be reported as 'trade count not available'")


def test_absent_trade_count_stays_unknown():
    """Genuinely absent stays `missing_trade_count` — the two must not collapse."""
    m = _metrics()
    m.pop("trade_count")
    src = {"metrics": m, "walkforward": _wf()}
    ids = _ids(src)
    assert "missing_trade_count" in ids
    assert "zero_trade_result" not in ids


def test_zero_trade_warning_names_the_deployment_risk():
    """The operator reads this before authorizing capital: it must be explicit."""
    src = {"metrics": _metrics(trade_count=0), "walkforward": _wf()}
    w = _by_id(src, "zero_trade_result")
    assert w is not None
    detail = w["detail"].lower()
    assert "no trades" in detail or "zero trades" in detail
    assert w["value"] == {"trade_count": 0}


def test_premium_native_zero_pairs_uses_the_same_class():
    """Premium runs keep their truth in option_backtest; the split must route there too."""
    src = {
        "metrics": _metrics(trade_count=0),
        "walkforward": _wf(),
        "option_backtest": {
            "dispatch": "premium_trigger_config",
            "metrics": {"paired_trade_count": 0, "win_rate": 0.0},
            "portfolio": {"sharpe_daily": None, "net_pnl_value": 0.0,
                          "max_drawdown_value": 0.0},
        },
    }
    ids = _ids(src)
    assert "zero_trade_result" in ids
    assert "missing_trade_count" not in ids


def test_guardrail_warning_separates_no_trades_from_weak_evidence():
    """`optimizer_guardrail_failed` must not imply a sample existed when none did."""
    src = {
        "metrics": _metrics(trade_count=0),
        "walkforward": _wf(),
        "config": {"validation": {"guardrail_qualified": False}},
    }
    w = _by_id(src, "optimizer_guardrail_failed")
    assert w is not None
    assert w["value"].get("trade_count") == 0, (
        "the guardrail warning must carry the trade count so the UI can tell a "
        "zero-trade disqualification from a weak-but-real sample")


def test_weak_but_real_sample_is_not_escalated():
    """A small non-zero sample keeps the existing, softer class. No over-firing."""
    src = {"metrics": _metrics(trade_count=12), "walkforward": _wf()}
    ids = _ids(src)
    assert "low_trade_count" in ids
    assert "zero_trade_result" not in ids
