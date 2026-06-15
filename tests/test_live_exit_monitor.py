import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_exit_monitor import LiveExitMonitor
from tests.test_deployment_evaluator import FakeDB  # reuse the in-memory async db
from app.paper_auto import mark_open_deployment_trades


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
    assert st["open_trades_checked"] == 2
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


# ---------------------------------------------------------------------------
# Integration: mark_open_deployment_trades against the real in-memory FakeDB
# ---------------------------------------------------------------------------

def _open_trade(stop=170.0, target=260.0):
    return {
        "id": "trd1", "status": "OPEN", "instrument_key": "NSE_FO|50614",
        "direction": "PE", "lots": 1, "quantity": 75,
        "entry_price": 200.0, "last_price": 200.0,
        "risk": {
            "stop_price": stop,
            "target_price": target,
            "auto_close_on_risk": True,
        },
        "signal_id": None,
    }


def test_mark_closes_on_stop_at_live_premium():
    db = FakeDB()
    db.paper_trades.rows.append(_open_trade())
    tick = {"NSE_FO|50614": {"last_price": 168.0, "ts": None}}
    summaries = asyncio.run(mark_open_deployment_trades(db, latest_tick_lookup=tick.get))
    assert summaries and summaries[0]["closed"] is True
    closed = [t for t in db.paper_trades.rows if t["id"] == "trd1"][0]
    assert str(closed["status"]).upper() == "CLOSED"


def test_mark_leaves_trade_open_when_no_fresh_tick():
    db = FakeDB()
    db.paper_trades.rows.append(_open_trade())
    summaries = asyncio.run(mark_open_deployment_trades(db, latest_tick_lookup=lambda k: None))
    assert summaries == []
    assert str(db.paper_trades.rows[0]["status"]).upper() == "OPEN"


import datetime as _dt


def test_mark_closes_on_time_stop():
    db = FakeDB()
    # created_at is the entry-time field on the real trade doc (paper_trade_from_signal)
    created_at = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=20)).isoformat()
    tr = _open_trade()
    tr["created_at"] = created_at
    tr["risk_hints"] = {"time_stop_minutes": 10}
    db.paper_trades.rows.append(tr)
    tick = {"NSE_FO|50614": {"last_price": 205.0, "ts": None}}   # between stop & target
    summaries = asyncio.run(mark_open_deployment_trades(db, latest_tick_lookup=tick.get))
    assert summaries and summaries[0]["closed"] is True
    closed = [t for t in db.paper_trades.rows if t["id"] == "trd1"][0]
    assert "time_stop" in str(closed.get("exit_reason") or "")
