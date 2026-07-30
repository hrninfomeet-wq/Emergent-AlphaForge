"""Two HIGH defects from the result-persistence-display audit (both CONFIRMED).

**Trust scorecard reads the SPOT metrics stub.** `evaluate_source_quality`
resolves `trade_count` and `sharpe` from `source_doc["metrics"]` — which is a
zero-filled stub for a premium-native run, because its `evaluate()` is an inert
stub and `compute_metrics([])` returns
`{"trade_count": 0, "win_rate": 0.0, "profit_factor": None, "sharpe": None, ...}`.
It already reads the option envelope one line later for the coverage/ruin checks.

Consequences, all displayed on the run and all gating deployment:
 1. fires "Trade count not available" — which is factually wrong;
 2. the weak-Sharpe check never runs (`if sharpe_val is not None`);
 3. the large-drawdown check never runs (`if total_pnl > 0 and max_dd > 0`);
 4. the selection-bias / deflated-Sharpe check never runs (needs trade_count > 0).
So a premium run showing "Trades 253 / Sharpe -0.40" sits above an amber panel
claiming no trade count, with the three checks that matter silently skipped.

**A backtest interrupted by a restart stays `status: running` forever.**
`run_backtest_job` is a fire-and-forget asyncio task, so a container rebuild
orphans the doc. `optimization_jobs` already gets a boot reconcile in
`server.py`; `backtest_runs` never did — the row polls forever and renders as a
blank results page.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.deployment_quality import evaluate_source_quality  # noqa: E402

_SPOT_STUB = {"trade_count": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
              "profit_factor": None, "total_pnl_pts": 0.0, "max_dd_pts": 0.0,
              "sharpe": None}


def _premium_run(*, paired=253, sharpe=-0.40, net=-5000.0, dd=-60000.0):
    return {
        "id": "r1", "status": "done", "metrics": dict(_SPOT_STUB),
        "option_backtest": {
            "enabled": True, "dispatch": "premium_trigger_config",
            "metrics": {"paired_trade_count": paired, "win_rate": 44.0,
                        "total_option_pnl_value": net},
            "portfolio": {"starting_capital": 200000.0, "sharpe_daily": sharpe,
                          "net_pnl_value": net, "max_drawdown_value": dd},
            "coverage": {"spot_trade_count": paired, "paired_trade_count": paired},
            "trades": [],
        },
    }


def _ordinary_run():
    return {
        "id": "r2", "status": "done",
        "metrics": {"trade_count": 180, "win_rate": 52.0, "profit_factor": 1.4,
                    "total_pnl_pts": 900.0, "max_dd_pts": -220.0, "sharpe": 1.1},
        "option_backtest": {"enabled": True, "metrics": {"paired_trade_count": 175},
                            "portfolio": {"sharpe_daily": 0.9}, "coverage": {},
                            "trades": []},
    }


def _codes(q):
    """Warning identifiers. The dicts use `id` (not `code`) — reading the wrong
    key silently returns an all-None set and every assertion passes/fails for the
    wrong reason."""
    return {str(w.get("id")) for w in (q or {}).get("warnings", [])}


# --------------------------------------------------------------------------- #
# trust scorecard: family-aware
# --------------------------------------------------------------------------- #

def test_a_premium_run_no_longer_claims_the_trade_count_is_missing():
    """THE symptom: 253 paired trades, panel says 'Trade count not available'."""
    q = evaluate_source_quality(_premium_run())
    assert not any("trade_count" in c and "not_available" in c for c in _codes(q)), _codes(q)


def test_the_weak_sharpe_check_actually_runs_for_a_premium_run():
    """It reads metrics.sharpe (None for premium), so -0.40 was never judged."""
    q = evaluate_source_quality(_premium_run(sharpe=-0.40))
    assert any("sharpe" in c for c in _codes(q)), (
        f"a daily Sharpe of -0.40 produced no Sharpe warning: {_codes(q)}")


def test_a_healthy_premium_sharpe_raises_no_sharpe_warning():
    """Guard against the fix simply always warning."""
    q = evaluate_source_quality(_premium_run(sharpe=1.8))
    assert not any("sharpe" in c for c in _codes(q)), _codes(q)


def test_a_tiny_premium_sample_is_still_flagged():
    """The sample-size check must now see the REAL count and act on it."""
    q = evaluate_source_quality(_premium_run(paired=4))
    assert _codes(q), "a 4-trade sample produced no warning at all"


def test_ordinary_runs_are_completely_unchanged():
    """Regression guard — existing verdicts must not move."""
    before = evaluate_source_quality(_ordinary_run())
    assert isinstance(before, dict) and "warnings" in before
    assert not any("not_available" in c for c in _codes(before))


def test_a_source_with_no_option_envelope_still_works():
    doc = {"id": "r3", "status": "done",
           "metrics": {"trade_count": 50, "win_rate": 50.0, "profit_factor": 1.1,
                       "total_pnl_pts": 100.0, "max_dd_pts": -20.0, "sharpe": 0.8}}
    assert isinstance(evaluate_source_quality(doc), dict)


def test_a_premium_run_with_zero_paired_trades_is_still_honest():
    """No trades really did happen — the missing-count warning is correct here."""
    q = evaluate_source_quality(_premium_run(paired=0))
    assert _codes(q)


def test_the_resolver_is_routed_by_dispatch_not_by_guesswork():
    """Same predicate research.py uses, so the two cannot disagree."""
    src = inspect.getsource(evaluate_source_quality)
    from app import deployment_quality
    whole = inspect.getsource(deployment_quality)
    assert "premium_trigger_config" in whole


# --------------------------------------------------------------------------- #
# stuck backtest runs
# --------------------------------------------------------------------------- #

def test_the_server_reconciles_orphaned_backtest_runs_on_boot():
    """optimization_jobs already had this; backtest_runs did not, so a rebuild
    left rows polling forever."""
    import pathlib
    src = pathlib.Path("backend/server.py").read_text(encoding="utf-8")
    i = src.index("backtest_runs")
    block = src[max(0, i - 800):i + 800]
    assert "update_many" in block
    assert "running" in block and "queued" in block


def test_the_reconcile_marks_them_failed_not_resumable():
    """A backtest has no resume mechanism (unlike an optimization job), so the
    honest terminal state is failed-with-a-reason, not `interrupted`."""
    import pathlib
    src = pathlib.Path("backend/server.py").read_text(encoding="utf-8")
    i = src.index("backtest_runs")
    block = src[max(0, i - 800):i + 900]
    assert '"failed"' in block
    assert "restart" in block.lower()


def test_the_reconcile_does_not_touch_finished_runs():
    import pathlib
    src = pathlib.Path("backend/server.py").read_text(encoding="utf-8")
    i = src.index("backtest_runs")
    block = src[max(0, i - 800):i + 900]
    assert '"$in": ["queued", "running"]' in block or "'$in': ['queued', 'running']" in block
