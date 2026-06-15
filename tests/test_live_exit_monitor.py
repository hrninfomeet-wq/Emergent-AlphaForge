import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_exit_monitor import LiveExitMonitor


def test_cycle_delegates_to_mark_and_records_stats():
    calls = {"n": 0}

    async def fake_mark(db, *, latest_tick_lookup):
        calls["n"] += 1
        return [{"id": "t1", "closed": True, "exit_reason": "option_target"},
                {"id": "t2", "closed": False}]

    mon = LiveExitMonitor(
        db_factory=lambda: object(),
        tick_lookup_factory=lambda: (lambda k: None),
        mark_fn=fake_mark,
    )
    summaries = asyncio.run(mon._cycle())
    assert calls["n"] == 1
    assert len(summaries) == 2
    st = mon.status()
    assert st["cycles"] == 1
    assert st["auto_closes"] == 1
    assert st["last_error"] is None


def test_cycle_records_error_without_raising():
    async def boom(db, *, latest_tick_lookup):
        raise RuntimeError("kaboom")

    mon = LiveExitMonitor(
        db_factory=lambda: object(),
        tick_lookup_factory=lambda: (lambda k: None),
        mark_fn=boom,
    )
    summaries = asyncio.run(mon._cycle())
    assert summaries == []
    assert "kaboom" in (mon.status()["last_error"] or "")
