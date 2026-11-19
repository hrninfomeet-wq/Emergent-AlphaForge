"""Capture hooks tested with in-memory collections and an injected fake app.db."""
import asyncio
import copy
import json
import sys
import time
from types import ModuleType, SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

from app.live import receipt_capture as capture
from app.live import receipt_events


CAPTURE = "2026-10-25T10:00:00Z"
INTENT = {"exch": "NFO", "tsym": "NIFTY-TEST", "trantype": "B", "prd": "I",
          "prctyp": "LMT", "qty": 100, "prc": 102, "trgprc": 0, "ret": "DAY"}


def order(**changes):
    return {"client_order_id": "client-one", "norenordno": "entry-one", "mode": "live",
            "deployment_id": "deployment-one", "intent": copy.deepcopy(INTENT), **changes}


def row(**changes):
    return {**INTENT, "norenordno": "entry-one", "fillshares": "60", "avgprc": "101",
            "status": "PARTIAL", "norentm": "25-10-2026 15:29:59", **changes}


def trade(**changes):
    return {**INTENT, "trantype": "S", "norenordno": "exit-one", "remarks": "oco:entry-one",
            "flid": "fill-one", "flqty": "60", "flprc": "110", "fltm": "15:30:00", **changes}


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length):
        return copy.deepcopy(self.rows[:length])


class Orders:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def find(self, query, projection):
        self.queries.append((copy.deepcopy(query), copy.deepcopy(projection)))
        return Cursor([r for r in self.rows if r.get("norenordno") in query["norenordno"]["$in"]
                       and r.get("mode") == query["mode"]])

    async def find_one(self, query):
        return copy.deepcopy(next((r for r in self.rows if r.get("client_order_id") == query["client_order_id"]), None))


class Receipts:
    def __init__(self):
        self.rows = {}

    async def insert_one(self, event):
        if event["_id"] in self.rows:
            raise DuplicateKeyError("private database details")
        self.rows[event["_id"]] = copy.deepcopy(event)


def database(*orders):
    return SimpleNamespace(live_orders=Orders(list(orders)), execution_receipt_events=Receipts())


@pytest.fixture(autouse=True)
def isolated_capture(monkeypatch):
    monkeypatch.setattr(capture, "_tasks", set())
    monkeypatch.setattr(capture, "_failed", 0)
    monkeypatch.setattr(capture, "_last_failure", None)
    monkeypatch.setattr(capture, "_observed", False)
    monkeypatch.setattr(capture, "now", lambda: CAPTURE)


def inject_db(monkeypatch, db):
    fake = ModuleType("app.db")
    fake.get_db = lambda: db
    monkeypatch.setitem(sys.modules, "app.db", fake)


async def drain():
    await asyncio.gather(*tuple(capture._tasks))
    await asyncio.sleep(0)  # run task-discard callbacks


def test_schedule_returns_immediately_while_store_blocked():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        class Blocked(Receipts):
            async def insert_one(self, event):
                started.set()
                await release.wait()
                await super().insert_one(event)
        db = database(order())
        db.execution_receipt_events = Blocked()
        before = time.monotonic()
        capture.submitted(db.live_orders, db.execution_receipt_events, "client-one")
        assert time.monotonic() - before < 0.1
        assert capture.capture_status()["pending_count"] == 1
        await asyncio.wait_for(started.wait(), 0.2)
        assert db.execution_receipt_events.rows == {}
        release.set()
        await drain()
        assert capture.capture_status()["state"] == "ok"
    asyncio.run(scenario())


def test_store_failure_and_timeout_are_bounded_without_exceptions(monkeypatch):
    monkeypatch.setattr(receipt_events, "WRITE_TIMEOUT_SECONDS", 0.02)
    async def scenario():
        class Broken:
            async def insert_one(self, event):
                raise RuntimeError("token-account-secret")
        class Slow:
            async def insert_one(self, event):
                await asyncio.sleep(60)
        for writer in (Broken(), Slow()):
            capture.submitted(Orders([order()]), writer, "client-one")
            await asyncio.wait_for(drain(), 0.3)
        status = capture.capture_status()
        assert status["state"] == "degraded"
        assert status["failed_writes"] == 2
        assert status["pending_count"] == 0
        assert "secret" not in json.dumps(status)
    asyncio.run(scenario())


def test_scheduler_factory_failure_no_loop_and_queue_limit(monkeypatch):
    called = []
    capture.schedule(lambda: called.append(True))
    assert called == []
    assert capture.capture_status()["failed_writes"] == 1
    async def scenario():
        monkeypatch.setattr(capture, "MAX_PENDING", 0)
        capture.schedule(lambda: called.append(True))
        assert called == []
        monkeypatch.setattr(capture, "MAX_PENDING", 64)
        def broken():
            raise RuntimeError("secret")
        capture.schedule(broken)
        await drain()
        assert capture.capture_status()["failed_writes"] == 3
    asyncio.run(scenario())


def test_approval_links_actual_result_and_drops_tokens_before_scheduling(monkeypatch):
    db = database()
    inject_db(monkeypatch, db)
    async def scenario():
        instruction = {**INTENT, "approval_token": "approval-secret", "uid": "account-secret"}
        result = {"cid": "actual-cid", "norenordno": "actual-order", "token": "result-secret"}
        capture.approval_decided("approval-one", instruction, result)
        # Caller mutation after scheduling must not alter captured instruction.
        instruction["qty"] = 999
        result["cid"] = "other-cid"
        await drain()
        saved = next(iter(db.execution_receipt_events.rows.values()))
        assert saved["approval_id"] == "approval-one"
        assert saved["client_order_id"] == "actual-cid"
        assert saved["norenordno"] == "actual-order"
        assert saved["instruction"]["qty"] == 100
        assert "secret" not in json.dumps(saved)
    asyncio.run(scenario())


def test_approval_without_result_keeps_missing_order_identity_unknown(monkeypatch):
    db = database()
    inject_db(monkeypatch, db)
    async def scenario():
        capture.approval_decided("approval-one", INTENT)
        await drain()
        saved = next(iter(db.execution_receipt_events.rows.values()))
        assert saved["approval_id"] == "approval-one"
        assert saved["client_order_id"] is saved["norenordno"] is None
    asyncio.run(scenario())


def test_book_excludes_foreign_mock_and_requires_exact_tracked_identity():
    db = database(order(), order(norenordno="mock-one", mode="mock"))
    asyncio.run(capture.capture_book(db, [row(), row(norenordno="foreign"), row(norenordno="mock-one")], captured_at=CAPTURE))
    assert len(db.execution_receipt_events.rows) == 1
    saved = next(iter(db.execution_receipt_events.rows.values()))
    assert saved["norenordno"] == "entry-one"
    assert db.live_orders.queries[0][0]["mode"] == "live"


@pytest.mark.parametrize("changes", [{"exch": "BFO"}, {"tsym": "OTHER"}, {"prd": "M"},
                                     {"trantype": "S"}, {"norenordno": "foreign"}])
def test_identity_conflicts_are_preserved_as_conflicts(changes):
    event = capture.order_snapshot(order(), row(**changes), CAPTURE)
    assert event["evidence_state"] == "conflicting"
    assert "identity_conflict" in event["reasons"]


def test_missing_identity_is_incomplete_and_duplicate_tracked_orders_refused():
    event = capture.order_snapshot(order(), row(tsym=None), CAPTURE)
    assert event["evidence_state"] == "incomplete"
    assert "missing_identity" in event["reasons"]
    db = database(order(), order(client_order_id="second-cid"))
    asyncio.run(capture.capture_book(db, [row()], captured_at=CAPTURE))
    assert db.execution_receipt_events.rows == {}
    assert capture.capture_status()["state"] == "degraded"


def test_book_duplicates_cumulative_out_of_order_partial_and_canceled():
    async def scenario():
        db = database(order())
        await capture.capture_book(db, [row(fillshares=100, avgprc=102, status="COMPLETE"), row()], captured_at=CAPTURE)
        await capture.capture_book(db, [row()], captured_at="2026-10-25T11:00:00Z")
        await capture.capture_book(db, [row(status="CANCELED")], captured_at=CAPTURE)
        saved = list(db.execution_receipt_events.rows.values())
        assert sorted(e["fill"]["quantity"] for e in saved) == [60, 60, 100]
        assert len(saved) == 3
        assert all(e["recorded_at"] == CAPTURE for e in saved)
        assert {e["fill"]["order_status"] for e in saved} == {"PARTIAL", "COMPLETE", "CANCELED"}
    asyncio.run(scenario())


def test_exit_tag_links_only_explicit_entry_and_refuses_symbol_reentry_guess():
    async def scenario():
        db = database(order(), order(norenordno="entry-two", client_order_id="reentry"))
        await capture.capture_book(db, [trade(), trade(remarks="", flid="unattributed"),
                                       trade(remarks="oco:entry-one oco:entry-two", flid="ambiguous")],
                                   trades=True, captured_at=CAPTURE)
        saved = list(db.execution_receipt_events.rows.values())
        assert len(saved) == 1
        assert saved[0]["norenordno"] == "entry-one"
        assert saved[0]["exit_evidence"]["entry_norenordno"] == "entry-one"
        assert saved[0]["exit_evidence"]["fill_id"] == "fill-one"
        assert saved[0]["evidence_state"] == "observed"
    asyncio.run(scenario())


@pytest.mark.parametrize("remarks", ["oco:entry-one", "LMT_BOS_O: oco:entry-one: Ltp condition",
                                     "oco:entry-one oco:entry-one"])
def test_exit_tag_supports_broker_decoration_and_repeated_same_tag(remarks):
    assert capture.exit_entry_id(trade(remarks=remarks)) == "entry-one"


def test_exit_missing_fill_id_is_incomplete_and_conflicting_contract_is_not_verified():
    event = capture.exit_snapshot(order(), trade(flid=None), CAPTURE)
    assert event["evidence_state"] == "incomplete"
    assert "missing_fill_id" in event["reasons"]
    assert "fill_id" not in event["exit_evidence"]
    foreign = capture.exit_snapshot(order(), trade(tsym="OTHER"), CAPTURE)
    assert foreign["evidence_state"] == "conflicting"
    ambiguous = capture.exit_snapshot(order(), trade(remarks="oco:entry-two"), CAPTURE)
    assert ambiguous["evidence_state"] == "incomplete"
    assert "ambiguous_exit" in ambiguous["reasons"]


def test_observe_book_captures_allowlisted_snapshot_and_reduces_exit_remarks(monkeypatch):
    db = database(order())
    inject_db(monkeypatch, db)
    async def scenario():
        raw = trade(token="session-secret", uid="account-secret", raw={"password": "raw-secret"},
                    remarks="LMT: oco:entry-one: personal-secret")
        capture.observe_book([raw], trades=True)
        raw["flqty"] = 999
        await drain()
        saved = next(iter(db.execution_receipt_events.rows.values()))
        assert saved["exit_evidence"]["quantity"] == 60
        assert "secret" not in json.dumps(saved)
    asyncio.run(scenario())


def test_observe_invalid_book_and_submitted_without_ack_are_visible_failures():
    capture.observe_book({"token": "secret"})
    async def scenario():
        capture.submitted(Orders([order(norenordno=None)]), Receipts(), "client-one")
        await drain()
        assert capture.capture_status()["failed_writes"] == 2
    asyncio.run(scenario())


def test_book_row_limit_marks_capture_degraded(monkeypatch):
    monkeypatch.setattr(capture, "MAX_ROWS", 1)
    db = database(order())
    asyncio.run(capture.capture_book(db, [row(), row(status="CANCELED")], captured_at=CAPTURE))
    assert len(db.execution_receipt_events.rows) == 1
    assert capture.capture_status()["state"] == "degraded"


@pytest.mark.parametrize("quantity, expected", [(99, "observed"), (100, "observed"), (101, "conflicting")])
def test_exit_quantity_boundary_against_approved_entry(quantity, expected):
    event = capture.exit_snapshot(order(), trade(flqty=quantity), CAPTURE)
    assert event["evidence_state"] == expected
    assert ("overfill" in event["reasons"]) is (quantity > 100)


@pytest.mark.parametrize("status", ["account-secret", "COMPLETE account-secret", None, {}, []])
def test_unknown_or_structured_status_is_omitted_without_raw_payload(status):
    event = capture.order_snapshot(order(), row(status=status), CAPTURE)
    assert "order_status" not in event["fill"]
    assert "incomplete_status" in event["reasons"]
    assert event["evidence_state"] == "incomplete"
    assert "account-secret" not in json.dumps(event)


@pytest.mark.parametrize("quantity, average, status, expected", [
    (99, 100, "COMPLETE", "conflicting"),
    (100, 100, "COMPLETE", "observed"),
    (101, 100, "COMPLETE", "conflicting"),
    (0, 100, "OPEN", "conflicting"),
    (0, 0, "OPEN", "observed"),
    (60, 100, "CANCELED", "observed"),
])
def test_terminal_and_zero_quantity_contradiction_boundaries(quantity, average, status, expected):
    event = capture.order_snapshot(order(), row(fillshares=quantity, avgprc=average, status=status), CAPTURE)
    assert event["evidence_state"] == expected
    if expected == "conflicting":
        assert "invalid_fill" in event["reasons"]
