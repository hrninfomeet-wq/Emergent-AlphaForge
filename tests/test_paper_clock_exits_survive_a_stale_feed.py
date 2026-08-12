"""A clock exit must fire on the clock, not on the feed.

Paper's two CLOCK-driven exits — `square_at_ist` and `time_stop_minutes` — were
gated on `option_price is not None`, i.e. on a FRESH option tick. The live guard's
equivalents are price-independent. So a feed hiccup at 14:30 meant paper rode to
the 15:00 EOD sweep on a strategy live squares at 14:30, and the operator
validating on paper measured a different strategy from the one they deploy.

The correct behaviour was already written three branches below, in the SPOT-MIRROR
exit, whose comment states the principle:

    "When no FRESH option tick exists the fill is the last known premium — booked
     (the spot level was hit) but flagged stale so the journal shows it is an
     estimate, not a fill."

A time condition being MET is exactly as definite as a spot level being HIT. The
price is only how you BOOK the exit, never whether it fired. The clock branches
simply never received that treatment.

Preserved deliberately:
  * an unparseable `square_at_ist` still fails OPEN — squaring a real position
    because a string could not be parsed is worse than ignoring it, and the EOD
    sweep still backstops it;
  * a fresh tick still books `live_tick` / not-stale, so nothing about the normal
    path changes;
  * no exit fires before its time, stale feed or not.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

from test_paper_exit_time_parity import _DB, IST  # noqa: E402  (reuse the harness)


def _fake_utc_at(now_ist_hhmm: str) -> datetime:
    """The UTC instant the marker will see once its clock is patched."""
    h, m = (int(x) for x in now_ist_hhmm.split(":"))
    return (datetime.now(IST).replace(hour=h, minute=m, second=0, microsecond=0)
            .astimezone(timezone.utc))


def _trade(*, square_at_ist: Optional[str] = None, time_stop_minutes=None,
           entry=100.0, last_price=None, created_min_ago=0,
           relative_to: Optional[str] = None):
    hints: Dict[str, Any] = {}
    if square_at_ist:
        hints["square_at_ist"] = square_at_ist
    if time_stop_minutes is not None:
        hints["time_stop_minutes"] = time_stop_minutes
    t = {
        "id": "t1", "deployment_id": "dep-1", "status": "OPEN",
        "instrument_key": "NSE_FO|K1", "entry_price": entry, "lots": 1,
        "lot_size": 75, "direction": "CE", "quantity": 75,
        # created_at MUST be relative to the clock the marker will run under.
        # Building it from the real wall clock while the marker sees a faked one
        # makes elapsed_min negative and no time-stop can ever fire — a fixture
        # bug that reads exactly like a broken feature.
        "created_at": ((_fake_utc_at(relative_to) if relative_to
                        else datetime.now(timezone.utc))
                       - timedelta(minutes=created_min_ago)).isoformat(),
        "risk_hints": hints,
    }
    if last_price is not None:
        t["last_price"] = last_price
    return t


def _run(db, *, now_ist_hhmm: str, stale: bool, price=120.0):
    """Drive the real marker. `stale=True` makes the tick ancient, so the
    marker's freshness gate yields option_price=None — the feed-hiccup case."""
    from app import paper_auto
    real = paper_auto._now_utc
    h, m = (int(x) for x in now_ist_hhmm.split(":"))
    fake_ist = datetime.now(IST).replace(hour=h, minute=m, second=0, microsecond=0)
    fake_utc = fake_ist.astimezone(timezone.utc)
    paper_auto._now_utc = lambda: fake_utc

    ts = int((fake_utc - timedelta(hours=6)).timestamp() * 1000) if stale \
        else int(fake_utc.timestamp() * 1000)

    def _lookup(_key):
        return {"last_price": price, "ts": ts}
    try:
        return asyncio.run(paper_auto.mark_open_deployment_trades(
            db, latest_tick_lookup=_lookup))
    finally:
        paper_auto._now_utc = real


def _row(db):
    return db.paper_trades.rows[0]


# --- the gap ---------------------------------------------------------------

def test_exit_time_fires_even_when_the_option_tick_is_stale():
    db = _DB([_trade(square_at_ist="14:30", last_price=118.0)])
    _run(db, now_ist_hhmm="14:31", stale=True)
    r = _row(db)
    assert str(r.get("status")).upper() == "CLOSED", (
        "a feed hiccup at 14:30 made paper ride to the 15:00 sweep while live "
        "squared on time — paper stopped being a rehearsal")
    assert "exit_time" in str(r.get("exit_reason") or "").lower()


def test_time_stop_fires_even_when_the_option_tick_is_stale():
    db = _DB([_trade(time_stop_minutes=5, created_min_ago=30,
                     last_price=118.0, relative_to="12:00")])
    _run(db, now_ist_hhmm="12:00", stale=True)
    r = _row(db)
    assert str(r.get("status")).upper() == "CLOSED"
    assert "time_stop" in str(r.get("exit_reason") or "").lower()


# --- the booking must be honest about being an estimate --------------------

def test_a_stale_clock_exit_is_flagged_as_an_estimate():
    db = _DB([_trade(square_at_ist="14:30", last_price=118.0)])
    _run(db, now_ist_hhmm="14:31", stale=True)
    r = _row(db)
    assert r.get("exit_price_source") == "last_mark", (
        "booked a stale mark as if it were a live fill")
    assert r.get("exit_price_stale") is True


def test_a_stale_clock_exit_books_the_last_known_premium():
    db = _DB([_trade(square_at_ist="14:30", last_price=118.0)])
    _run(db, now_ist_hhmm="14:31", stale=True)
    assert float(_row(db).get("exit_price")) == 118.0


def test_it_falls_back_to_entry_when_there_is_no_last_mark():
    db = _DB([_trade(square_at_ist="14:30", entry=100.0)])
    _run(db, now_ist_hhmm="14:31", stale=True)
    r = _row(db)
    assert str(r.get("status")).upper() == "CLOSED"
    assert float(r.get("exit_price")) == 100.0


def test_it_never_books_a_fabricated_zero():
    """No usable price at all -> leave it OPEN for the EOD sweep rather than
    record a total loss that never happened."""
    t = _trade(square_at_ist="14:30")
    t["entry_price"] = 0.0
    db = _DB([t])
    _run(db, now_ist_hhmm="14:31", stale=True)
    r = _row(db)
    assert float(r.get("exit_price") or 0.0) != 0.0 or \
        str(r.get("status")).upper() == "OPEN", (
        "booked an exit at 0.0 — a fabricated total loss")


# --- nothing about the normal path changes ---------------------------------

def test_a_fresh_tick_still_books_a_live_fill():
    db = _DB([_trade(square_at_ist="14:30", last_price=118.0)])
    _run(db, now_ist_hhmm="14:31", stale=False, price=120.0)
    r = _row(db)
    assert str(r.get("status")).upper() == "CLOSED"
    assert r.get("exit_price_source") == "live_tick"
    assert r.get("exit_price_stale") is False
    assert float(r.get("exit_price")) == 120.0


def test_nothing_fires_before_its_time_even_on_a_stale_feed():
    db = _DB([_trade(square_at_ist="14:30", last_price=118.0)])
    _run(db, now_ist_hhmm="14:29", stale=True)
    assert str(_row(db).get("status")).upper() == "OPEN"


def test_a_time_stop_that_has_not_elapsed_does_not_fire():
    db = _DB([_trade(time_stop_minutes=60, created_min_ago=5,
                     last_price=118.0, relative_to="12:00")])
    _run(db, now_ist_hhmm="12:00", stale=True)
    assert str(_row(db).get("status")).upper() == "OPEN"


def test_a_malformed_exit_time_still_fails_OPEN():
    """Preserved: squaring because a string could not be parsed is worse."""
    db = _DB([_trade(square_at_ist="not-a-time", last_price=118.0)])
    _run(db, now_ist_hhmm="14:31", stale=True)
    assert str(_row(db).get("status")).upper() == "OPEN"


def test_a_trade_with_no_clock_exit_is_untouched():
    db = _DB([_trade(last_price=118.0)])
    _run(db, now_ist_hhmm="14:31", stale=True)
    assert str(_row(db).get("status")).upper() == "OPEN"
