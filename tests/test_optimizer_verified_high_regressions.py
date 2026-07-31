"""Regression coverage for the four verified HIGH optimizer defects.

These behaviors sit inside ``run_optimization``, whose complete driver needs Mongo,
warehouse frames, strategy plugins, Optuna and (on Linux) a fork pool.  The pure
promotion/survival helpers below exercise the data contracts directly; focused
source-contract checks pin that all three execution branches use those helpers and
that pool ownership is respected.  Existing parallel, optimizer-result and frontend
contract suites provide the surrounding integration coverage.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app import optimizer  # noqa: E402
from app.rerank_select import DISQUALIFY  # noqa: E402


def _runner_source() -> str:
    return inspect.getsource(optimizer.run_optimization)


def _parallel_block() -> str:
    src = _runner_source()
    start = src.index("# PARALLEL (workers>1)")
    end = src.index("# Final analyses", start)
    return src[start:end]


# #11 -----------------------------------------------------------------------

def test_all_trial_paths_use_the_finite_score_promotion_gate():
    """Every path preserves finite results and refuses only invalid scores."""
    # One call rebuilds resumed state from logged evidence; the other three are
    # grid, sequential Optuna and parallel Optuna promotion sites.
    assert _runner_source().count("_promote_best_if_finite(") == 4


def test_resume_rebuilds_best_only_from_logged_finite_trials():
    src = _runner_source()
    resume = src[src.index("if resume:"):src.index("if method == \"grid\":")]
    assert "for trial_num, record in enumerate(trial_history)" in resume
    assert "_promote_best_if_finite(" in resume
    assert 'bsf.get("params")' not in resume


def test_finite_guardrail_disqualification_remains_available_for_user_promotion():
    promote = getattr(optimizer, "_promote_best_if_finite")
    seed = {"value": -float("inf"), "params": {}, "metrics": {}, "trial_num": -1}

    out = promote(
        seed,
        value=DISQUALIFY,
        params={"signal_threshold": 99},
        metrics={"trade_count": 0},
        trial_num=0,
    )
    assert out["params"] == {"signal_threshold": 99}
    assert out["metrics"] == {"trade_count": 0}
    assert out["guardrail_qualified"] is False


def test_nonfinite_scores_never_populate_a_best_result_or_rankings():
    promote = getattr(optimizer, "_promote_best_if_finite")
    seed = {"value": -float("inf"), "params": {}, "metrics": {}, "trial_num": -1}

    for value in (float("nan"), float("inf"), -float("inf")):
        out = promote(
            seed,
            value=value,
            params={"signal_threshold": 99},
            metrics={"trade_count": 0},
            trial_num=0,
        )
        assert out is seed
        assert out["params"] == {} and out["metrics"] == {}

    ranked = optimizer.scored_trials([
        {"objective_value": DISQUALIFY, "params": {"finite": True}},
        {"objective_value": float("inf"), "params": {"finite": False}},
        {"objective_value": float("nan"), "params": {"finite": False}},
    ])
    assert [row["params"] for row in ranked] == [{"finite": True}]


# #18 -----------------------------------------------------------------------

def test_survival_summary_separates_evaluated_from_unevaluated_finalists():
    summarize = getattr(optimizer, "_survival_progress_summary")
    ranked = [
        {"survival": {"survived": False, "reason": "drawdown"}},
        {"survival": {"survived": False, "reason": "ror"}},
        {},
        {},
    ]

    out = summarize(ranked, evaluated=2)

    assert out == {
        "evaluated": 2,
        "finalists": 4,
        "not_evaluated": 2,
        "reason_counts": {"drawdown": 1, "ror": 1},
    }
    assert "unknown" not in out["reason_counts"]


def test_survival_stop_reason_and_truthful_counts_reach_the_finished_job():
    src = _runner_source()
    assert '"analyze_stopped_by": analyze_stopped_by' in src
    assert "_survival_progress_summary(" in src
    assert "evaluated=surv_evaluated" in src
    assert 'survival_summary["evaluated"]' in src


def test_frontend_discloses_incomplete_survival_evaluation():
    src = (ROOT / "frontend" / "src" / "pages" / "Optimizer.jsx").read_text(
        encoding="utf-8"
    )
    assert "analyze_stopped_by" in src
    assert "not_evaluated" in src
    assert "opt-analyze-stopped" in src


# #22 -----------------------------------------------------------------------

def test_parallel_fallback_does_not_shutdown_another_jobs_pool():
    block = _parallel_block()
    assert "use_parallel = pool is not None" in block
    guard = block.index("if use_parallel:")
    shutdown = block.index("shutdown_pool()")
    assert guard < shutdown


# #28 -----------------------------------------------------------------------

def test_parallel_winner_keeps_its_full_params_and_own_metrics():
    promote = getattr(optimizer, "_promote_best_if_finite")
    previous = {
        "value": 1.0,
        "params": {"alpha": 1, "cooldown_bars": 3},
        "metrics": {"winner": "previous"},
        "trial_num": 0,
    }
    new_params = {"alpha": 2, "cooldown_bars": 3}  # includes pinned dimension
    new_metrics = {"winner": "new", "trade_count": 42}

    out = promote(
        previous,
        value=2.0,
        params=new_params,
        metrics=new_metrics,
        trial_num=1,
    )

    assert out["params"] == new_params
    assert out["metrics"] is new_metrics
    assert out["metrics"]["winner"] == "new"


def test_parallel_path_promotes_at_tell_time_not_by_partial_param_lookup():
    block = _parallel_block()
    loop = block[block.index("for batch_offset"):]
    assert "_promote_best_if_finite(" in loop
    assert "study_best_params" not in loop
    assert "t[\"params\"] == study_best_params" not in loop


def test_zero_survivor_stage_keeps_the_best_finite_rerank_candidate():
    choose = getattr(optimizer, "_best_finite_rerank_candidate")
    ranked = [
        {"option_pnl_value": 1250.0, "params": {"period": 8},
         "spot_metrics": {"trade_count": 12},
         "paired_trade_count": 10, "option_pnl_pts": 20.0,
         "option_win_rate": 40.0,
         "survival": {"survived": False, "reason": "drawdown"}},
        {"option_pnl_value": float("inf"), "params": {"period": 9}},
    ]
    out = choose(ranked)
    assert out["params"] == {"period": 8}
    assert out["survival"]["survived"] is False


def test_survival_with_no_stage2_finalists_preserves_finite_stage1_candidate():
    src = _runner_source()
    start = src.index("elif survival.enabled:")
    block = src[start:start + 900]
    assert 'best_so_far = {**best_so_far, "survival_qualified": False}' in block
    assert '"survivors": 0' in block
    assert '"finalists": 0' in block


def test_wfo_preserves_finite_unqualified_windows_for_oos_and_promotion():
    from app import wfo
    src = inspect.getsource(wfo.run_wfo)
    assert "_promote_best_if_finite" in src
    assert '"guardrail_qualified": window_best.get("guardrail_qualified", False)' in src
    assert "promotion_windows" in src


def test_optimizer_preset_route_uses_finite_candidate_not_terminal_status_as_gate():
    from app.routers import research
    src = inspect.getsource(research.apply_opt_as_preset)
    assert "finite_candidate_available" in src
    assert "Job has no finished result yet" not in src
    assert '"guardrail_qualified"' in src


def test_frontend_presents_evidence_failures_as_advisory_promotion_warnings():
    src = (ROOT / "frontend" / "src" / "pages" / "Optimizer.jsx").read_text(
        encoding="utf-8"
    )
    assert "Research only — promotion blocked" not in src
    assert "Save Research Preset" not in src
    assert "finite candidate" in src
    assert "user-authorized paper or live" in src


def test_deploy_wizard_derives_execution_policy_from_backtest_sources_too():
    src = (ROOT / "frontend" / "src" / "pages" / "LiveSignals.jsx").read_text(
        encoding="utf-8"
    )
    assert 'const backtestRun = form.source_type === "backtest_run"' in src
    assert "const selectedSourceConfig = preset?.config || backtestRun?.config" in src
    assert 'form.source_type !== "strategy"' in src
