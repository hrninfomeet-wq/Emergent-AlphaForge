"""Behavioral regressions for the optimizer MED integrity closure.

Findings #14 and #23 were already closed by the broader HIGH #18/#28 fixes and
remain pinned in ``test_optimizer_verified_high_regressions.py``.  This module
covers the six MED findings that still require implementation.
"""
from __future__ import annotations

import inspect
import os
import sys
from types import SimpleNamespace

import pandas as pd
import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import optimizer, wfo  # noqa: E402


def _candidate(period: int) -> dict:
    return {
        "params": {"period": period},
        "objective_value": float(period),
        "metrics": {"trade_count": 20, "sharpe": 1.0},
    }


@pytest.mark.asyncio
async def test_17_premium_rerank_honours_stop_and_keeps_partial_result(monkeypatch):
    """A pause/cancel after finalist one must not evaluate finalist two."""
    from app import premium_trigger_dispatch
    from app.routers import premium_momentum_routes

    calls = []

    async def fake_load_window(*_args, **_kwargs):
        return pd.DataFrame({"ts": [1, 2]}), pd.DataFrame(), []

    def fake_dispatch(**kwargs):
        calls.append(dict(kwargs["merged_params"]))
        return {
            "metrics": {
                "total_option_pnl_value": 100.0,
                "total_option_pnl_pts": 10.0,
                "win_rate": 50.0,
                "paired_trade_count": 10,
            },
            "coverage": {"paired_trade_count": 10},
        }

    async def should_stop():
        return len(calls) >= 1

    monkeypatch.setattr(premium_momentum_routes, "_load_window", fake_load_window)
    monkeypatch.setattr(premium_trigger_dispatch, "dispatch_full_backtest", fake_dispatch)

    strategy = SimpleNamespace(
        id="premium_test",
        merged_params=lambda params: dict(params),
    )
    ranked, _contracts, _candles, budget_hit, stopped = (
        await optimizer._option_rerank_premium_trigger(
            [_candidate(1), _candidate(2)],
            lambda _params: pd.DataFrame({"ts": [1, 2]}),
            strategy,
            "NIFTY",
            min_trades=0,
            should_stop=should_stop,
        )
    )

    assert [row["params"] for row in ranked] == [{"period": 1}]
    assert calls == [{"period": 1}]
    assert budget_hit is False
    assert stopped is True


def test_17_ordinary_rerank_checks_stop_in_both_long_loops():
    src = inspect.getsource(optimizer._option_rerank)
    assert "should_stop" in src
    assert src.count("await should_stop()") >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control", "expected"),
    [((False, False), None), ((True, False), "cancelled"), ((False, True), "paused")],
)
async def test_20_wfo_analysis_control_reports_pause_and_cancel(monkeypatch, control, expected):
    async def fake_control(_job_id):
        return control

    monkeypatch.setattr(wfo, "_job_control", fake_control)
    assert await wfo._analysis_stop_reason("job-1") == expected


def test_20_wfo_final_analysis_rechecks_control_around_expensive_steps():
    src = inspect.getsource(wfo.run_wfo)
    assert src.count("_analysis_stop_reason(job_id)") >= 3
    assert '"analysis_stopped_by": analysis_stopped_by' in src
    assert 'final_status = analysis_stopped_by or' in src


def test_30_trial_evidence_separates_actual_count_from_ceiling():
    out = optimizer.optimizer_trial_evidence(
        completed=50, ceiling=150, early_stopped=True
    )
    assert out == {
        "n_trials": 50,
        "trials_ceiling": 150,
        "early_stopped": True,
    }


def test_30_runner_saves_and_scores_actual_trials_and_ui_explains_stop():
    runner = inspect.getsource(optimizer.run_optimization)
    assert "n_trials_completed=completed" in runner
    assert "trials_ceiling=n_trials" in runner
    assert '"n_trials": completed' in runner

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(
        os.path.join(root, "frontend", "src", "pages", "Optimizer.jsx"),
        encoding="utf-8",
    ) as handle:
        ui = handle.read()
    assert "effectiveTrialTotal" in ui
    assert "Auto-stopped at" in ui


def test_25_noop_or_duplicate_perturbations_do_not_inflate_robustness():
    def evaluate(params):
        return {"trade_count": 10, "objective": 100.0}, params

    out = optimizer._robustness_score(
        evaluate,
        lambda metrics: metrics["objective"],
        {"x": 1},
        {"x": {"type": "int", "min": 1, "max": 10}},
    )

    assert out["tested_count"] == 1
    assert out["skipped_count"] == 3
    assert len(out["perturbations"]) == 1
    assert out["perturbations"][0]["value"] == 2
    assert out["score"] == 100.0


def test_26_negative_objective_uses_symmetric_degradation_tolerance():
    def evaluate(params):
        value = -100.0 if float(params["x"]) == 10.0 else -90.0
        return {"trade_count": 10, "objective": value}, params

    out = optimizer._robustness_score(
        evaluate,
        lambda metrics: metrics["objective"],
        {"x": 10.0},
        {"x": {"type": "float", "min": 1.0, "max": 20.0}},
    )

    assert out["tested_count"] == 4
    assert all(row["ok"] for row in out["perturbations"])
    assert out["score"] == 100.0
