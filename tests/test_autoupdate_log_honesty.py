"""The auto-update log must not report green for a day it never ran.

Three ways it lied, all verified in source:

1. A SKIPPED run returned before the `try/finally` that appends history, so the
   run was never recorded at all. A week of `upstox_not_connected` skips left an
   EMPTY history — indistinguishable from a week of healthy silence. "Auto-update
   skipped because Upstox was never connected" IS the not-ready state, and it is
   exactly what an unattended operator needs to see.

2. `last_status = "ok"` was set on successful SUBMISSION of jobs that then run
   asynchronously. N jobs that all fail read "ok (N jobs)". Submission is not
   success; the honest word is "submitted".

3. The Dashboard banner counted a skip as healthy
   (`autoOk = last_status === "ok" || last_status === "skipped"`), so the one
   state that means "nothing happened" rendered green.

Not fixed here, and named so it is not mistaken for done: `STATE` is an in-memory
module global, so every restart still erases the history. Persisting it needs a db
handle threaded into a currently pure module.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.warehouse_autoupdate import AutoUpdateState, run_autoupdate_once  # noqa: E402


def _run(**kw):
    st = kw.pop("state")
    async def _conn():
        return kw.pop("_conn_result", {}) if False else conn
    conn = kw.pop("connection", {"connected": True})
    async def conn_fn():
        return conn
    async def plan_fn():
        return kw.pop("_p", None) or {"summary": {"total_actions": 1}}
    async def exec_fn(_plan):
        return {"submitted_count": 2}
    return asyncio.run(run_autoupdate_once(
        reason=kw.pop("reason", "test"),
        connection_status_fn=conn_fn,
        compute_plan_fn=plan_fn,
        execute_plan_fn=exec_fn,
        state=st,
    ))


def test_a_skipped_run_is_recorded_in_history():
    st = AutoUpdateState()
    out = _run(state=st, connection={"connected": False}, reason="startup")
    assert out["status"] == "skipped"
    assert st.history, (
        "a skipped run left NO trace — a week of skips is indistinguishable from "
        "a week of healthy silence")
    assert st.history[-1]["status"] == "skipped"


def test_the_skip_reason_is_preserved_in_history():
    st = AutoUpdateState()
    _run(state=st, connection={"connected": False}, reason="daily_timer")
    entry = st.history[-1]
    assert entry.get("reason"), "the skip reason is the whole diagnostic value"
    assert "daily_timer" in str(entry.get("trigger"))


def test_a_skipped_run_counts_as_a_run():
    st = AutoUpdateState()
    _run(state=st, connection={"connected": False})
    assert st.runs_count == 1, "a skip is a run that happened and decided nothing"


def test_submission_is_reported_as_submitted_not_ok():
    """Jobs run async after submission; calling that 'ok' asserts an outcome
    nobody has observed."""
    st = AutoUpdateState()
    out = _run(state=st)
    assert out["status"] == "submitted", (
        "reported 'ok' when it had only SUBMITTED async jobs that may all fail")
    assert st.last_status == "submitted"


def test_a_no_action_run_is_still_ok():
    """Nothing to do IS a real, completed outcome — that one is honestly ok."""
    st = AutoUpdateState()

    async def conn_fn():
        return {"connected": True}
    async def plan_fn():
        return {"summary": {"total_actions": 0}}
    async def exec_fn(_p):
        raise AssertionError("must not execute with zero actions")

    out = asyncio.run(run_autoupdate_once(
        reason="t", connection_status_fn=conn_fn, compute_plan_fn=plan_fn,
        execute_plan_fn=exec_fn, state=st))
    assert out["status"] == "ok"
    assert st.history[-1]["status"] == "ok"


def test_an_error_is_still_an_error():
    st = AutoUpdateState()

    async def conn_fn():
        return {"connected": True}
    async def plan_fn():
        raise RuntimeError("planner exploded")
    async def exec_fn(_p):
        return {}

    out = asyncio.run(run_autoupdate_once(
        reason="t", connection_status_fn=conn_fn, compute_plan_fn=plan_fn,
        execute_plan_fn=exec_fn, state=st))
    assert out["status"] == "error"
    assert st.history[-1]["status"] == "error"


def test_the_dashboard_does_not_render_a_skip_as_healthy():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "frontend" / "src"
           / "components" / "WarehouseHealthBanner.jsx").read_text(encoding="utf-8")
    assert 'last_status === "skipped"' not in src.split("autoOk")[1][:160], (
        "a skipped auto-update renders GREEN — that is the one status meaning "
        "nothing happened")
