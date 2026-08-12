"""Boot must clean up what the PC-off pattern orphans.

This machine is rarely on during market hours, so a missed 15:00 square-off is the
NORMAL case, not an edge case. Two collections were left stranded by it, and both
are stranded RIGHT NOW in the live database: 19 OPEN paper trades entered
2026-08-04 across 2 deployments, and 9 warehouse_runs stuck in "running".

1. OPEN paper trades. `square_off_open_paper_trades` runs only inside
   `_deployment_evaluator_loop`, which `continue`s outside 09:15-15:30 IST and
   latches on a per-PROCESS in-memory date. Miss the window and the trade stays
   OPEN forever: it permanently consumes a `max_concurrent` slot and committed
   capital, gets marked against a later session's ticks for a possibly-expired
   contract, and counts as an EOD violation that fails the promotion policy.

2. `warehouse_runs`. Inserted "queued" and driven by fire-and-forget tasks, so one
   restart mid-sync leaves a row "running" forever. The frontend's hygiene batch
   is persisted to localStorage and polls until every tracked run leaves
   `TERMINAL_EXCLUDED = ["queued","running"]` — which can never happen — so
   `isHygieneActive()` stays true and disables BOTH "Fill gaps" and "Sync now"
   permanently. The only escape today is clearing browser localStorage.

The square-off must be STALE-ONLY. A mid-session restart at 11:00 must not flatten
positions the strategy legitimately opened at 09:30 — that would turn a restart
into an unintended exit.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.paper_squareoff import square_off_open_paper_trades  # noqa: E402


class _Coll:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]
        self.replaced = []

    def find(self, q=None, proj=None):
        docs = self.docs
        if q:
            docs = [d for d in docs
                    if all(d.get(k) == v for k, v in q.items()
                           if not isinstance(v, dict))]
        class _C:
            async def to_list(_self, length=None):
                return [dict(d) for d in docs]
        return _C()

    async def replace_one(self, flt, doc, upsert=False):
        self.replaced.append(doc)
        class _R: modified_count = 1
        return _R()


class _DB:
    def __init__(self, trades, deps=()):
        self.paper_trades = _Coll(trades)
        self.strategy_deployments = _Coll(deps)


def _trade(tid, created_at, dep="dep1"):
    return {"id": tid, "status": "OPEN", "deployment_id": dep,
            "instrument_key": "NSE_FO|1", "created_at": created_at,
            "entry_price": 100.0, "quantity": 65, "lots": 1}


def _run(db, **kw):
    return asyncio.run(square_off_open_paper_trades(db, **kw))


def test_a_stale_trade_is_squared():
    db = _DB([_trade("t1", "2026-08-04T07:04:02+00:00")])
    out = _run(db, entered_before_ist_date="2026-08-07",
               reason="boot_reconcile_missed_squareoff")
    assert len(db.paper_trades.replaced) == 1
    assert db.paper_trades.replaced[0]["status"] == "CLOSED"
    assert any(s.get("id") == "t1" for s in out)


def test_todays_trade_is_NOT_squared():
    """A mid-session restart must not flatten a legitimately open position."""
    db = _DB([_trade("t1", "2026-08-07T04:00:00+00:00")])   # 09:30 IST today
    _run(db, entered_before_ist_date="2026-08-07")
    assert db.paper_trades.replaced == [], (
        "squared a position opened today — a restart would become an exit")


def test_the_ist_boundary_is_respected():
    """20:00 UTC on the 6th is 01:30 IST on the 7th — i.e. TODAY, not stale."""
    db = _DB([_trade("t1", "2026-08-06T20:00:00+00:00")])
    _run(db, entered_before_ist_date="2026-08-07")
    assert db.paper_trades.replaced == [], "compared UTC dates, not IST"


def test_without_the_filter_behaviour_is_unchanged():
    """Default None keeps the existing global square-off semantics."""
    db = _DB([_trade("t1", "2026-08-07T04:00:00+00:00")])
    _run(db)
    assert len(db.paper_trades.replaced) == 1


def test_allow_overnight_still_wins_over_the_boot_sweep():
    """An operator who opted into overnight positions keeps them.

    `honour_allow_overnight=True` mirrors the production call: the boot sweep IS
    the missed 15:00 EOD sweep, so it is one of only two callers that may carry the
    exemption. Every other caller — the basket stop, Stop-ALL, manual Stop, retire
    — squares regardless. See tests/test_overnight_scope.py.
    """
    db = _DB([_trade("t1", "2026-08-04T07:04:02+00:00", dep="depN")],
             deps=[{"id": "depN", "risk": {"allow_overnight": True}}])
    out = _run(db, entered_before_ist_date="2026-08-07", honour_allow_overnight=True)
    assert db.paper_trades.replaced == []
    assert out and out[0].get("skipped") == "allow_overnight"


def test_a_missing_created_at_is_not_treated_as_stale():
    """Never square on an unknown date — absent is not old."""
    t = _trade("t1", "2026-08-04T07:04:02+00:00")
    t.pop("created_at")
    db = _DB([t])
    _run(db, entered_before_ist_date="2026-08-07")
    assert db.paper_trades.replaced == []


# --- the boot wiring itself -------------------------------------------------

def _server_src():
    from pathlib import Path
    return (Path(__file__).resolve().parents[1]
            / "backend" / "server.py").read_text(encoding="utf-8")


def test_boot_reconciles_stuck_warehouse_runs():
    src = _server_src()
    assert "warehouse_runs" in src, (
        "a warehouse_run stuck at queued/running disables Sync AND Fill-gaps "
        "permanently — the frontend batch can never reach allDone")
    assert 'update_many' in src


def test_boot_squares_only_stale_paper_trades():
    src = _server_src()
    assert "square_off_open_paper_trades" in src
    assert "entered_before_ist_date" in src, (
        "the boot sweep must be stale-only, or a mid-session restart flattens "
        "today's legitimately open positions")
