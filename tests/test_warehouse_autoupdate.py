"""Tests for the warehouse auto-update worker (slice 5)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.warehouse_autoupdate import (  # noqa: E402
    AutoUpdateState,
    run_autoupdate_once,
    seconds_until_next_daily_run,
    should_run_autoupdate,
)

IST = timezone(timedelta(hours=5, minutes=30))


# ---- should_run_autoupdate (pure guard) ------------------------------------


def test_guard_runs_when_connected_and_idle():
    run, reason = should_run_autoupdate(
        enabled=True,
        connection_status={"connected": True, "expired": False},
        in_progress=False,
    )
    assert run is True
    assert reason == "ok"


def test_guard_skips_when_disabled():
    run, reason = should_run_autoupdate(
        enabled=False,
        connection_status={"connected": True, "expired": False},
        in_progress=False,
    )
    assert run is False
    assert reason == "disabled"


def test_guard_skips_when_already_running():
    run, reason = should_run_autoupdate(
        enabled=True,
        connection_status={"connected": True, "expired": False},
        in_progress=True,
    )
    assert run is False
    assert reason == "already_running"


def test_guard_skips_when_not_connected():
    run, reason = should_run_autoupdate(
        enabled=True,
        connection_status={"connected": False},
        in_progress=False,
    )
    assert run is False
    assert reason == "upstox_not_connected"


def test_guard_skips_when_token_expired():
    run, reason = should_run_autoupdate(
        enabled=True,
        connection_status={"connected": True, "expired": True},
        in_progress=False,
    )
    assert run is False
    assert reason == "upstox_token_expired"


# ---- seconds_until_next_daily_run ------------------------------------------


def test_next_daily_run_is_later_today_when_before_target():
    # 10:00 IST -> next 18:00 IST is 8 hours away.
    now_utc = datetime(2026, 5, 20, 10, 0, tzinfo=IST).astimezone(timezone.utc)
    secs = seconds_until_next_daily_run(now_utc, hour_ist=18, minute_ist=0)
    assert abs(secs - 8 * 3600) < 5


def test_next_daily_run_rolls_to_tomorrow_when_after_target():
    # 20:00 IST -> next 18:00 IST is 22 hours away (tomorrow).
    now_utc = datetime(2026, 5, 20, 20, 0, tzinfo=IST).astimezone(timezone.utc)
    secs = seconds_until_next_daily_run(now_utc, hour_ist=18, minute_ist=0)
    assert abs(secs - 22 * 3600) < 5


# ---- run_autoupdate_once (orchestration) -----------------------------------


@pytest.mark.asyncio
async def test_run_skips_when_not_connected_and_does_not_plan():
    planned = {"called": False}

    async def conn():
        return {"connected": False}

    async def compute():
        planned["called"] = True
        return {"summary": {"total_actions": 0}}

    async def execute(plan):
        raise AssertionError("execute must not be called when skipped")

    state = AutoUpdateState()
    summary = await run_autoupdate_once(
        reason="startup",
        connection_status_fn=conn,
        compute_plan_fn=compute,
        execute_plan_fn=execute,
        state=state,
    )
    assert summary["status"] == "skipped"
    assert summary["reason"] == "upstox_not_connected"
    assert planned["called"] is False
    assert state.in_progress is False


@pytest.mark.asyncio
async def test_run_plans_but_does_not_execute_when_no_actions():
    executed = {"called": False}

    async def conn():
        return {"connected": True, "expired": False}

    async def compute():
        return {"summary": {"total_actions": 0, "overall_status": "verified"}}

    async def execute(plan):
        executed["called"] = True
        return {"submitted_count": 0}

    state = AutoUpdateState()
    summary = await run_autoupdate_once(
        reason="daily_timer",
        connection_status_fn=conn,
        compute_plan_fn=compute,
        execute_plan_fn=execute,
        state=state,
    )
    assert summary["status"] == "ok"
    assert summary["actions_planned"] == 0
    assert summary["submitted_count"] == 0
    assert executed["called"] is False
    assert state.runs_count == 1


@pytest.mark.asyncio
async def test_run_executes_when_actions_present():
    async def conn():
        return {"connected": True, "expired": False}

    async def compute():
        return {"summary": {"total_actions": 3, "overall_status": "degraded"}}

    async def execute(plan):
        return {"submitted_count": 3}

    state = AutoUpdateState()
    summary = await run_autoupdate_once(
        reason="oauth_connect",
        connection_status_fn=conn,
        compute_plan_fn=compute,
        execute_plan_fn=execute,
        state=state,
    )
    assert summary["status"] == "ok"
    assert summary["actions_planned"] == 3
    assert summary["submitted_count"] == 3
    assert state.last_status == "ok"
    assert state.last_submitted_count == 3
    assert len(state.history) == 1


@pytest.mark.asyncio
async def test_run_captures_errors_without_raising():
    async def conn():
        return {"connected": True, "expired": False}

    async def compute():
        raise RuntimeError("mongo unavailable")

    async def execute(plan):
        return {"submitted_count": 0}

    state = AutoUpdateState()
    summary = await run_autoupdate_once(
        reason="manual",
        connection_status_fn=conn,
        compute_plan_fn=compute,
        execute_plan_fn=execute,
        state=state,
    )
    assert summary["status"] == "error"
    assert "mongo unavailable" in summary["error"]
    assert state.in_progress is False
    assert state.last_status == "error"


@pytest.mark.asyncio
async def test_disabled_state_blocks_run():
    async def conn():
        return {"connected": True, "expired": False}

    async def compute():
        raise AssertionError("must not plan when disabled")

    async def execute(plan):
        raise AssertionError("must not execute when disabled")

    state = AutoUpdateState(enabled=False)
    summary = await run_autoupdate_once(
        reason="startup",
        connection_status_fn=conn,
        compute_plan_fn=compute,
        execute_plan_fn=execute,
        state=state,
    )
    assert summary["status"] == "skipped"
    assert summary["reason"] == "disabled"
