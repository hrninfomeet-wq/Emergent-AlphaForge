"""Deleting an optimizer job must not orphan the runs it produced (item #14).

Found by hitting it. A saved SENSEX backtest run recorded
`config.optimization_job_id = "cac0151c-..."`, and that job is not in
`optimization_jobs` — 30 jobs stored, none of them it. There is a
`DELETE /api/optimize/jobs/{job_id}` endpoint and a bulk-delete affordance in the
UI, so the overwhelmingly likely explanation is an ordinary user deletion rather
than a persistence bug.

The consequence is not ordinary. That run reports +₹87,721 on 480 trials, and
with its job gone there is no way to recover WHAT WAS SEARCHED — the param space,
the trial count, the parameter importance, the data-integrity blockers, the
quality warnings. A result whose search space is unknowable cannot be audited,
and an unauditable result is worth less than no result, because it still looks
like evidence.

The run already carries `optimization_job_id` and a trial count. Everything an
audit actually needs lives on the job. So the job's audit-critical fields are
copied onto every referencing run BEFORE the job is deleted: deletion stays
allowed and stops being destructive.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.optimizer_provenance import (  # noqa: E402
    PROVENANCE_FIELDS,
    provenance_snapshot,
)


def _job(**over):
    job = {
        "id": "job-1",
        "strategy_id": "expiry_regime_trend_continuation",
        "instrument": "SENSEX",
        "objective": "net_pnl_inr",
        "method": "bayesian",
        "evaluation_mode": "option_rerank",
        "lot_size": 20,
        "n_trials_completed": 480,
        "n_trials_total": 800,
        "param_space": {"stop_bps": {"min": 4.0, "max": 20.0}},
        "parameter_importance": [{"param": "range_mult", "importance": 0.73}],
        "research_eligibility": {"status": "research_only", "promotion_allowed": False},
        "best_quality": {"warnings": [{"id": "large_drawdown"}]},
        "trial_log": ["huge"] * 5000,      # must NOT be copied
        "heatmap": {"big": "payload"},     # must NOT be copied
    }
    job.update(over)
    return job


# ---------------------------------------------------------------------------
# The snapshot itself
# ---------------------------------------------------------------------------

def test_the_snapshot_keeps_everything_an_audit_needs():
    snap = provenance_snapshot(_job())
    for field in ("param_space", "objective", "parameter_importance",
                  "research_eligibility", "n_trials_completed"):
        assert field in snap, f"{field} is what an audit asks for first"
    assert snap["param_space"]["stop_bps"]["max"] == 20.0
    assert snap["research_eligibility"]["promotion_allowed"] is False


def test_the_snapshot_records_which_job_it_came_from():
    assert provenance_snapshot(_job())["optimization_job_id"] == "job-1"


def test_the_snapshot_leaves_the_bulk_payloads_behind():
    """A trial log and a heatmap are why the job document is large. Copying them
    onto every referencing run would multiply the collection for no audit value —
    the searched SPACE is the question, not every step taken through it."""
    snap = provenance_snapshot(_job())
    assert "trial_log" not in snap
    assert "heatmap" not in snap
    assert "best_so_far" not in snap


def test_absent_fields_are_simply_absent_not_null_filled():
    """A missing field must not become a null that reads as 'we looked and there
    was nothing' — the same missing-vs-zero distinction the chain recorder keeps."""
    snap = provenance_snapshot({"id": "job-2"})
    assert snap["optimization_job_id"] == "job-2"
    for field in PROVENANCE_FIELDS:
        assert field not in snap


@pytest.mark.parametrize("junk", [None, "nope", 42, [], object()])
def test_an_unusable_job_yields_an_empty_snapshot_rather_than_raising(junk):
    assert provenance_snapshot(junk) == {}


# ---------------------------------------------------------------------------
# The endpoint preserves before it deletes
# ---------------------------------------------------------------------------

class _Coll:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.updates = []

    async def find_one(self, q, proj=None):
        return next((r for r in self.rows if r.get("id") == q.get("id")), None)

    async def delete_one(self, q):
        before = len(self.rows)
        self.rows = [r for r in self.rows if r.get("id") != q.get("id")]
        class R:
            deleted_count = before - len(self.rows)
        return R()

    async def update_many(self, q, update):
        self.updates.append({"query": q, "update": update})
        n = 0
        for r in self.rows:
            if r.get("config", {}).get("optimization_job_id") == \
                    q.get("config.optimization_job_id"):
                r.update(update["$set"]); n += 1
        class R:
            modified_count = n
        return R()


class _Db:
    def __init__(self, jobs, runs):
        self.optimization_jobs = _Coll(jobs)
        self.backtest_runs = _Coll(runs)


def _delete(db, job_id):
    from app.routers import research
    return asyncio.run(research.delete_opt_job.__wrapped__(job_id)
                       if hasattr(research.delete_opt_job, "__wrapped__")
                       else research.delete_opt_job(job_id))


def test_the_referencing_run_keeps_its_provenance_after_the_job_is_gone(monkeypatch):
    run = {"id": "run-1", "config": {"optimization_job_id": "job-1"}}
    db = _Db([_job()], [run])
    from app.routers import research
    monkeypatch.setattr(research, "get_db", lambda: db)

    out = _delete(db, "job-1")

    assert out["deleted"] == 1
    assert db.optimization_jobs.rows == []
    prov = run.get("optimizer_provenance")
    assert prov, "the run must carry the job's audit trail once the job is gone"
    assert prov["param_space"]["stop_bps"]["max"] == 20.0
    assert prov["optimization_job_id"] == "job-1"
    assert out["runs_preserved"] == 1


def test_deleting_a_job_nothing_references_still_works(monkeypatch):
    db = _Db([_job()], [])
    from app.routers import research
    monkeypatch.setattr(research, "get_db", lambda: db)
    out = _delete(db, "job-1")
    assert out["deleted"] == 1 and out["runs_preserved"] == 0


def test_deleting_an_absent_job_is_not_an_error(monkeypatch):
    db = _Db([], [])
    from app.routers import research
    monkeypatch.setattr(research, "get_db", lambda: db)
    out = _delete(db, "ghost")
    assert out["deleted"] == 0 and out["runs_preserved"] == 0
