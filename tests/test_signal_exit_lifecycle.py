"""A signal follows its trade out of ACTIVE — on EVERY close route.

The audit (Task B, 2026-09-29) found signals never leave ACTIVE: the only
ACTIVE -> EXITED writers were the paper marker's own stop/target close and the
manual single-trade close. The 15:00 sweep, the boot reconcile, Stop / Stop-ALL, the
basket controls, retire (all `square_off_open_paper_trades`) and EVERY live close
(`close_live_trade`: guard, reconcile, kill switch) left the signal ACTIVE for good.

These tests EXECUTE the real close paths against in-memory Mongo stand-ins and pin
the contract of the one shared helper: it moves only ACTIVE signals, is idempotent,
never clobbers a racing writer, and NEVER raises into the close it follows.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.close_loop import close_live_trade  # noqa: E402
from app.paper_squareoff import square_off_open_paper_trades  # noqa: E402
from app.paper_trading import paper_trade_from_signal  # noqa: E402
from app.signal_lifecycle import (  # noqa: E402
    create_signal_doc, exit_linked_signal, transition_signal)

IST = timezone(timedelta(hours=5, minutes=30))


# --------------------------------------------------------------------------- #
# In-memory Mongo stand-in: equality / $in / $ne / $gte, replace_one that HONOURS
# every key of its filter (so a `state` guard really guards), and failure /
# race injection.
# --------------------------------------------------------------------------- #

def _match(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in (query or {}).items():
        rv = row.get(k)
        if isinstance(v, dict) and "$in" in v:
            if rv not in v["$in"]:
                return False
        elif isinstance(v, dict) and "$ne" in v:
            if rv == v["$ne"]:
                return False
        elif isinstance(v, dict) and "$gte" in v:
            if rv is None or not str(rv) >= str(v["$gte"]):
                return False
        elif rv != v:
            return False
    return True


class Cur:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def sort(self, key, direction=1):
        # A STRING sort, exactly what Mongo does with ISO timestamps.
        self._rows = sorted(self._rows, key=lambda r: str(r.get(key) or ""),
                            reverse=(direction == -1))
        return self

    async def to_list(self, length=None):
        rows = self._rows if length is None else self._rows[: int(length)]
        return [dict(r) for r in rows]


class Res:
    def __init__(self, n: int):
        self.matched_count = n
        self.modified_count = n


class Coll:
    def __init__(self, rows=None):
        self.rows: List[Dict[str, Any]] = [dict(r) for r in (rows or [])]
        self.fail: set = set()                       # op names that raise
        self.after_find_one: Optional[Callable[[Dict[str, Any]], None]] = None
        self.writes = 0

    def find(self, query=None, projection=None):
        return Cur([r for r in self.rows if _match(r, query or {})])

    def aggregate(self, pipeline):
        return Cur([])                               # pipelines are not emulated

    async def count_documents(self, query):
        return len([r for r in self.rows if _match(r, query or {})])

    async def find_one(self, query, projection=None):
        if "find_one" in self.fail:
            raise RuntimeError("find_one boom")
        for r in self.rows:
            if _match(r, query):
                found = dict(r)
                if self.after_find_one:
                    self.after_find_one(r)           # a racing writer lands here
                return found
        return None

    async def replace_one(self, query, doc, upsert=False):
        if "replace_one" in self.fail:
            raise RuntimeError("replace_one boom")
        for i, r in enumerate(self.rows):
            if _match(r, query):
                self.rows[i] = dict(doc)
                self.writes += 1
                return Res(1)
        return Res(0)

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if _match(r, query):
                r.update(update.get("$set", {}))
                self.writes += 1
                return Res(1)
        return Res(0)


class FakeDB:
    def __init__(self):
        self.signals = Coll()
        self.paper_trades = Coll()
        self.live_trades = Coll()
        self.strategy_deployments = Coll()


def make_signal(state: str = "ACTIVE", **extra) -> Dict[str, Any]:
    sig = create_signal_doc(instrument="NIFTY", direction="CE", strategy_id="s1",
                            entry_price=100.0, confidence=0.7)
    path = ["FORMING", "CONFIRMED", "TRIGGERED", "ACTIVE"]
    for st in path:
        sig = transition_signal(sig, st, reason="test")
        if st == state:
            break
    if state == "EXITED":
        sig = transition_signal(sig, "EXITED", reason="already")
    if state == "AUDITED":
        sig = transition_signal(sig, "AUDITED", reason="already")
    sig.update(extra)
    return sig


def make_paper_trade(signal: Dict[str, Any], *, last: float = 110.0,
                     deployment_id: Optional[str] = None) -> Dict[str, Any]:
    sig = {**signal, "option_contract": {"trading_symbol": "NIFTY26SEP24000CE",
                                         "lot_size": 50,
                                         "instrument_key": "NSE_FO|1|CE"}}
    trade = paper_trade_from_signal(sig, lots=1, entry_price=100.0)
    trade["instrument_key"] = "NSE_FO|1|CE"
    trade["last_price"] = last
    if deployment_id:
        trade["deployment_id"] = deployment_id
    return trade


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# The shared helper
# --------------------------------------------------------------------------- #

def test_an_active_signal_moves_to_exited_with_an_audit_event():
    db = FakeDB()
    sig = make_signal("ACTIVE")
    db.signals.rows.append(sig)
    moved = run(exit_linked_signal(db, sig["id"], reason="why", trade_id="T1",
                                   realized_pnl=-250.0, at="2026-09-28T09:30:00+00:00"))
    assert moved is True
    row = db.signals.rows[0]
    assert row["state"] == "EXITED"
    assert row["exited_at"] == "2026-09-28T09:30:00+00:00"
    last = row["events"][-1]
    assert (last["from_state"], last["to_state"], last["reason"]) == ("ACTIVE", "EXITED", "why")
    assert last["snapshot"] == {"trade_id": "T1", "realized_pnl": -250.0}


@pytest.mark.parametrize("state", ["WATCHING", "FORMING", "CONFIRMED", "TRIGGERED",
                                   "EXITED", "AUDITED"])
def test_only_an_active_signal_is_touched(state):
    db = FakeDB()
    if state == "WATCHING":
        sig = create_signal_doc(instrument="NIFTY", direction="CE", strategy_id="s",
                                entry_price=1, confidence=1)
    else:
        sig = make_signal(state)
    db.signals.rows.append(sig)
    before = dict(db.signals.rows[0])
    assert run(exit_linked_signal(db, sig["id"], reason="x")) is False
    assert db.signals.rows[0] == before
    assert db.signals.writes == 0


def test_a_repeat_call_is_a_no_op_not_a_second_event():
    db = FakeDB()
    sig = make_signal("ACTIVE")
    db.signals.rows.append(sig)
    assert run(exit_linked_signal(db, sig["id"], reason="first")) is True
    events = len(db.signals.rows[0]["events"])
    assert run(exit_linked_signal(db, sig["id"], reason="second")) is False
    assert len(db.signals.rows[0]["events"]) == events


@pytest.mark.parametrize("signal_id", [None, "", "no-such-signal"])
def test_a_blank_or_unknown_signal_id_is_a_quiet_no_op(signal_id):
    db = FakeDB()
    db.signals.rows.append(make_signal("ACTIVE"))
    assert run(exit_linked_signal(db, signal_id, reason="x")) is False
    assert db.signals.rows[0]["state"] == "ACTIVE"


def test_a_missing_db_or_missing_signals_collection_never_raises():
    class NoSignals:
        pass
    assert run(exit_linked_signal(None, "s", reason="x")) is False
    assert run(exit_linked_signal(NoSignals(), "s", reason="x")) is False


@pytest.mark.parametrize("op", ["find_one", "replace_one"])
def test_a_failing_signal_read_or_write_never_raises(op):
    db = FakeDB()
    sig = make_signal("ACTIVE")
    db.signals.rows.append(sig)
    db.signals.fail.add(op)
    assert run(exit_linked_signal(db, sig["id"], reason="x")) is False
    assert db.signals.rows[0]["state"] == "ACTIVE"


def test_a_racing_writer_is_never_clobbered():
    """Between our read and our write another route moves the signal on (here to
    AUDITED). The write is conditional on state == ACTIVE, so it must lose — and
    report that it did NOT move the signal."""
    db = FakeDB()
    sig = make_signal("ACTIVE")
    db.signals.rows.append(sig)

    def other_route(row):
        row["state"] = "AUDITED"
        row["audited_by_other"] = True

    db.signals.after_find_one = other_route
    assert run(exit_linked_signal(db, sig["id"], reason="x")) is False
    assert db.signals.rows[0]["state"] == "AUDITED"
    assert db.signals.rows[0].get("audited_by_other") is True


# --------------------------------------------------------------------------- #
# Paper: the shared square-off sweep (7 callers)
# --------------------------------------------------------------------------- #

def _paper_setup(state="ACTIVE", **trade_kw):
    db = FakeDB()
    sig = make_signal(state)
    db.signals.rows.append(sig)
    trade = make_paper_trade(sig, **trade_kw)
    db.paper_trades.rows.append(trade)
    return db, sig, trade


def test_the_square_off_sweep_moves_the_linked_signal_to_exited():
    db, sig, trade = _paper_setup()
    out = run(square_off_open_paper_trades(db, reason="auto_square_off_15_00_IST"))
    assert out[0]["id"] == trade["id"] and "error" not in out[0]
    assert db.paper_trades.rows[0]["status"] == "CLOSED"
    row = db.signals.rows[0]
    assert row["state"] == "EXITED"
    ev = row["events"][-1]
    assert ev["reason"] == "paper_trade_squared_off (auto_square_off_15_00_IST)"
    assert ev["snapshot"]["trade_id"] == trade["id"]
    assert ev["snapshot"]["realized_pnl"] == db.paper_trades.rows[0]["realized_pnl"]


def test_a_scoped_stop_also_moves_the_signal():
    """Stop / Stop-ALL / basket / manual / retire all reach the same function —
    here through the per-deployment scope the Stop button uses."""
    db, sig, trade = _paper_setup(deployment_id="dep-A")
    run(square_off_open_paper_trades(db, deployment_id="dep-A", reason="manual_stop"))
    assert db.signals.rows[0]["state"] == "EXITED"


def test_a_failing_signal_write_does_not_stop_the_close_or_the_sweep():
    """The trade is already persisted CLOSED when the signal is touched; a journal
    failure must neither report the exit as an error nor abort the rest of the sweep."""
    db = FakeDB()
    sigs = [make_signal("ACTIVE"), make_signal("ACTIVE")]
    for s in sigs:
        db.signals.rows.append(s)
        db.paper_trades.rows.append(make_paper_trade(s))
    db.signals.fail.add("replace_one")
    out = run(square_off_open_paper_trades(db))
    assert len(out) == 2 and all("error" not in s for s in out)
    assert [t["status"] for t in db.paper_trades.rows] == ["CLOSED", "CLOSED"]
    assert [s["state"] for s in db.signals.rows] == ["ACTIVE", "ACTIVE"]  # unchanged, not corrupted


def test_a_deployment_with_no_signals_collection_still_squares_off():
    class DBNoSignals:
        def __init__(self, inner):
            self.paper_trades = inner.paper_trades
            self.strategy_deployments = inner.strategy_deployments

    inner = FakeDB()
    sig = make_signal("ACTIVE")
    inner.paper_trades.rows.append(make_paper_trade(sig))
    out = run(square_off_open_paper_trades(DBNoSignals(inner)))
    assert "error" not in out[0] and inner.paper_trades.rows[0]["status"] == "CLOSED"


def test_a_signal_already_exited_by_the_marker_is_not_touched_again():
    db, sig, trade = _paper_setup(state="EXITED")
    before = dict(db.signals.rows[0])
    run(square_off_open_paper_trades(db))
    assert db.signals.rows[0] == before


def test_a_trade_with_no_signal_link_is_squared_without_error():
    db, sig, trade = _paper_setup()
    db.paper_trades.rows[0].pop("signal_id", None)
    out = run(square_off_open_paper_trades(db))
    assert "error" not in out[0]
    assert db.signals.rows[0]["state"] == "ACTIVE"      # nothing to link, nothing moved


# --------------------------------------------------------------------------- #
# Live: the single choke point every live close goes through
# --------------------------------------------------------------------------- #

def _live_setup(state="ACTIVE"):
    db = FakeDB()
    sig = make_signal(state, live_trade_id="LT1")
    db.signals.rows.append(sig)
    db.live_trades.rows.append({
        "id": "LT1", "norenordno": "2609290001", "signal_id": sig["id"],
        "status": "OPEN", "quantity": 100, "entry_price": 100.0, "exch": "NFO"})
    return db, sig


def test_a_live_close_moves_the_signal_to_exited():
    db, sig = _live_setup()
    ok = run(close_live_trade(db, norenordno="2609290001", exit_price=90.0,
                              exit_reason="guard_stop", now_iso="2026-09-29T05:00:00+00:00"))
    assert ok is True
    assert db.live_trades.rows[0]["status"] == "CLOSED"
    row = db.signals.rows[0]
    assert row["state"] == "EXITED"
    assert row["exited_at"] == "2026-09-29T05:00:00+00:00"
    ev = row["events"][-1]
    assert ev["reason"] == "live_trade_closed (guard_stop)"
    assert ev["snapshot"]["trade_id"] == "LT1"
    assert ev["snapshot"]["realized_pnl"] == db.live_trades.rows[0]["realized_pnl"] == -1000.0


def test_a_failing_signal_write_does_not_stop_a_live_close():
    db, sig = _live_setup()
    db.signals.fail.add("replace_one")
    ok = run(close_live_trade(db, norenordno="2609290001", exit_price=90.0,
                              exit_reason="guard_stop"))
    assert ok is True                                    # the close is what matters
    assert db.live_trades.rows[0]["status"] == "CLOSED"
    assert db.signals.rows[0]["state"] == "ACTIVE"


def test_a_live_close_with_no_signals_collection_still_closes():
    db, sig = _live_setup()
    del db.signals                                       # a db with no `signals` at all
    assert run(close_live_trade(db, norenordno="2609290001", exit_price=90.0,
                                exit_reason="x")) is True
    assert db.live_trades.rows[0]["status"] == "CLOSED"


def test_a_close_that_did_not_land_leaves_the_signal_alone():
    """A repeat / raced close (the doc is no longer non-CLOSED) transitions nothing,
    so it must not touch the signal either — the first close already did."""
    db, sig = _live_setup()
    db.live_trades.rows[0]["status"] = "CLOSED"
    assert run(close_live_trade(db, norenordno="2609290001", exit_price=90.0,
                                exit_reason="x")) is False
    assert db.signals.rows[0]["state"] == "ACTIVE"


def test_a_lost_race_on_the_trade_write_leaves_the_signal_alone():
    """The trade was still non-CLOSED when read, but the conditional write matched
    nothing (another closer won): the signal must not move on OUR say-so."""
    db, sig = _live_setup()

    async def lost(query, update, upsert=False):
        return Res(0)

    db.live_trades.update_one = lost
    assert run(close_live_trade(db, norenordno="2609290001", exit_price=90.0,
                                exit_reason="x")) is False
    assert db.signals.rows[0]["state"] == "ACTIVE"


def test_a_never_filled_close_is_journalled_on_the_signal_with_its_reason():
    db, sig = _live_setup()
    assert run(close_live_trade(db, norenordno="2609290001", exit_price=None,
                                exit_reason="never_filled")) is True
    ev = db.signals.rows[0]["events"][-1]
    assert db.signals.rows[0]["state"] == "EXITED"
    assert ev["reason"] == "live_trade_closed (never_filled)"
    assert ev["snapshot"]["realized_pnl"] is None       # never a fabricated number


# --------------------------------------------------------------------------- #
# The backfill script
# --------------------------------------------------------------------------- #

_spec = importlib.util.spec_from_file_location(
    "backfill_signal_exits_script", ROOT / "backend" / "scripts" / "backfill_signal_exits.py")
backfill_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill_script)


def _bf(db, apply=False):
    return run(backfill_script.backfill(db, apply=apply))


def _seed_backfill():
    db = FakeDB()
    # 1. paper: ACTIVE signal, its paper trade CLOSED (closed via a +05:30 stamp)
    s1 = make_signal("ACTIVE", paper_trade_id="PT1")
    db.signals.rows.append(s1)
    db.paper_trades.rows.append({"id": "PT1", "status": "CLOSED", "realized_pnl": -120.0,
                                 "closed_at": "2026-09-28T15:00:00+05:30"})
    # 2. live via live_trade_id
    s2 = make_signal("ACTIVE", live_trade_id="LT2")
    db.signals.rows.append(s2)
    db.live_trades.rows.append({"id": "LT2", "status": "CLOSED", "realized_pnl": 300.0,
                                "closed_at": "2026-09-28T09:45:00+00:00"})
    # 3. live found only through live_trades.signal_id (signal has no live_trade_id)
    s3 = make_signal("ACTIVE")
    db.signals.rows.append(s3)
    db.live_trades.rows.append({"id": "LT3", "status": "CLOSED", "signal_id": s3["id"],
                                "closed_at": "2026-09-28T10:00:00+00:00"})
    # 4. trade still OPEN -> untouched
    s4 = make_signal("ACTIVE", paper_trade_id="PT4")
    db.signals.rows.append(s4)
    db.paper_trades.rows.append({"id": "PT4", "status": "OPEN"})
    # 5. no linked trade at all -> untouched
    s5 = make_signal("ACTIVE")
    db.signals.rows.append(s5)
    # 6. link points at a trade doc that no longer exists -> untouched
    s6 = make_signal("ACTIVE", paper_trade_id="GONE")
    db.signals.rows.append(s6)
    # 7. not ACTIVE -> not even scanned
    s7 = make_signal("EXITED", paper_trade_id="PT1")
    db.signals.rows.append(s7)
    return db, {"s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5, "s6": s6, "s7": s7}


def _state(db, sig):
    return next(r for r in db.signals.rows if r["id"] == sig["id"])["state"]


def test_backfill_dry_run_counts_and_writes_nothing():
    db, s = _seed_backfill()
    before = [dict(r) for r in db.signals.rows]
    out = _bf(db, apply=False)
    assert out == {"applied": False, "active_signals": 6, "would_move": 3, "moved": 0,
                   "trade_still_open": 1, "no_linked_trade": 2, "raced": 0, "errors": 0}
    assert db.signals.rows == before and db.signals.writes == 0


def test_backfill_apply_moves_exactly_the_closed_trade_signals():
    db, s = _seed_backfill()
    out = _bf(db, apply=True)
    assert out["moved"] == 3 and out["would_move"] == 3 and out["errors"] == 0
    for k in ("s1", "s2", "s3"):
        assert _state(db, s[k]) == "EXITED", k
    for k in ("s4", "s5", "s6"):
        assert _state(db, s[k]) == "ACTIVE", k


def test_backfill_stamps_the_reason_and_the_trades_own_close_time_in_utc():
    db, s = _seed_backfill()
    _bf(db, apply=True)
    row = next(r for r in db.signals.rows if r["id"] == s["s1"]["id"])
    assert row["events"][-1]["reason"] == "backfill_trade_closed"
    # the +05:30 stamp is normalised: 15:00 IST == 09:30 UTC — when the position
    # ended, not when the script ran, and never a second timestamp format.
    assert row["exited_at"] == "2026-09-28T09:30:00+00:00"
    assert row["events"][-1]["snapshot"] == {"trade_id": "PT1", "realized_pnl": -120.0}


def test_backfill_is_idempotent():
    db, s = _seed_backfill()
    _bf(db, apply=True)
    snapshot = [dict(r) for r in db.signals.rows]
    again = _bf(db, apply=True)
    assert again["moved"] == 0 and again["would_move"] == 0
    assert again["active_signals"] == 3                   # the three it left alone
    assert db.signals.rows == snapshot


def test_backfill_needs_every_linked_trade_closed():
    """A signal linked to BOTH a paper and a live trade moves only when both are
    CLOSED — an OPEN one anywhere means the signal is still live."""
    db = FakeDB()
    sig = make_signal("ACTIVE", paper_trade_id="PT", live_trade_id="LT")
    db.signals.rows.append(sig)
    db.paper_trades.rows.append({"id": "PT", "status": "CLOSED", "closed_at": "2026-09-28T09:30:00+00:00"})
    db.live_trades.rows.append({"id": "LT", "status": "OPEN"})
    out = _bf(db, apply=True)
    assert out["trade_still_open"] == 1 and out["moved"] == 0
    assert db.signals.rows[0]["state"] == "ACTIVE"


def test_backfill_a_trade_with_no_close_time_still_moves_the_signal():
    db = FakeDB()
    sig = make_signal("ACTIVE", paper_trade_id="PT")
    db.signals.rows.append(sig)
    db.paper_trades.rows.append({"id": "PT", "status": "CLOSED"})
    assert _bf(db, apply=True)["moved"] == 1
    assert db.signals.rows[0]["state"] == "EXITED"


def test_backfill_a_lost_race_is_counted_not_forced():
    db, s = _seed_backfill()

    def other_route(row):
        if row["id"] == s["s1"]["id"]:
            row["state"] = "AUDITED"

    db.signals.after_find_one = other_route
    out = _bf(db, apply=True)
    assert out["raced"] == 1 and out["moved"] == 2
    assert _state(db, s["s1"]) == "AUDITED"


def test_backfill_one_bad_signal_does_not_abort_the_rest():
    db, s = _seed_backfill()
    real = db.paper_trades.find_one

    async def flaky(query, projection=None):
        if query.get("id") == "PT1":
            raise RuntimeError("boom")
        return await real(query, projection)

    db.paper_trades.find_one = flaky
    out = _bf(db, apply=True)
    assert out["errors"] == 1 and out["moved"] == 2
    assert _state(db, s["s1"]) == "ACTIVE"


def test_backfill_follows_the_trades_back_reference_past_a_dangling_signal_id():
    """The real database (2026-09-29): 697 of 705 ACTIVE signals carried a
    paper_trade_id, only 108 paper trades existed, and 49 ACTIVE signals were
    referenced by a CLOSED paper trade's own signal_id. Following only the
    signal-side id moved none of them."""
    db = FakeDB()
    sig = make_signal("ACTIVE", paper_trade_id="PURGED-ID")
    db.signals.rows.append(sig)
    db.paper_trades.rows.append({"id": "PT9", "signal_id": sig["id"], "status": "CLOSED",
                                 "closed_at": "2026-09-28T15:00:00+05:30"})
    out = _bf(db, apply=True)
    assert out["moved"] == 1 and _state(db, sig) == "EXITED"


def test_a_back_referencing_trade_still_open_keeps_the_signal_active():
    db = FakeDB()
    sig = make_signal("ACTIVE", paper_trade_id="PURGED-ID")
    db.signals.rows.append(sig)
    db.paper_trades.rows.append({"id": "PT9", "signal_id": sig["id"], "status": "OPEN"})
    out = _bf(db, apply=True)
    assert out["moved"] == 0 and _state(db, sig) == "ACTIVE"
