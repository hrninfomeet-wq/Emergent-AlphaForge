"""Exercise actual route bodies with explicit fakes, without importing the app."""
import ast
import asyncio
import copy
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest

from app.live import receipt_capture
from app.live.idempotency import IntentStore
from tests.test_receipt_capture import Receipts, Orders, order


ROOT = Path(__file__).resolve().parents[1]


def route(name, scope):
    tree = ast.parse((ROOT / "backend/app/routers/live_broker.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    fn.decorator_list = []
    mod = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), fn], type_ignores=[])
    exec(compile(ast.fix_missing_locations(mod), "isolated_route", "exec"), scope)
    return scope[name]


@pytest.fixture
def fake_capture(monkeypatch):
    db = SimpleNamespace(live_orders=Orders([]), execution_receipt_events=Receipts())
    module = ModuleType("app.db")
    module.get_db = lambda: db
    monkeypatch.setitem(sys.modules, "app.db", module)
    for key, value in (("_tasks", set()), ("_failed", 0), ("_last_failure", None), ("_observed", False)):
        monkeypatch.setattr(receipt_capture, key, value)
    return db


async def drain():
    await asyncio.gather(*tuple(receipt_capture._tasks))
    await asyncio.sleep(0)


def test_approve_captures_decision_and_links_actual_returned_order(fake_capture):
    instruction = order()["intent"]
    ticket = {"side": "B", "lots": 1, "order_type": "LIMIT", "product": "MIS", "underlying": "NIFTY",
              "strike": 25000, "option_type": "CE", "ref_ltp": 100, "buffer_pct": 0.5}
    store = SimpleNamespace(approve=lambda *a: {"ok": True, "payload": {"ticket": ticket, "would_send": [instruction]}},
                            mark_consumed=lambda *a: None)
    called = []
    async def place(body, *, approved_entry):
        called.append(copy.deepcopy(approved_entry))
        return {"placed": True, "cid": "actual-cid", "norenordno": "actual-order"}
    fn = route("live_order_approve", {"_approval_store": lambda: store, "_utcnow_iso": receipt_capture.now,
               "_PlaceBody": SimpleNamespace, "_place_manual_order": place, "receipt_capture": receipt_capture,
               "HTTPException": RuntimeError})
    async def scenario():
        result = await fn("approved-id", SimpleNamespace(token="DO_NOT_STORE"))
        await drain()
        return result
    result = asyncio.run(scenario())
    assert result["placed"] is True and called == [instruction]
    events = list(fake_capture.execution_receipt_events.rows.values())
    assert len(events) == 2
    linked = next(e for e in events if e["norenordno"])
    assert linked["approval_id"] == "approved-id" and linked["client_order_id"] == "actual-cid"
    assert "DO_NOT_STORE" not in repr(events)


def test_order_book_hook_uses_one_existing_read_and_keeps_result_on_storage_failure(fake_capture):
    rows = [{**order()["intent"], "norenordno": "entry-one", "fillshares": 40, "avgprc": 100, "status": "OPEN"}]
    fake_capture.live_orders = Orders([order()])
    class Broken:
        async def insert_one(self, doc): raise RuntimeError("private")
    fake_capture.execution_receipt_events = Broken()
    reads = []
    class Client:
        async def order_book(self): reads.append(1); return rows
    async def get_client(): return Client()
    fn = route("live_broker_orders", {"_get_client": get_client, "receipt_capture": receipt_capture,
                                      "HTTPException": RuntimeError, "BrokerReadError": RuntimeError})
    async def scenario():
        result = await fn()
        await drain()
        return result
    assert asyncio.run(scenario()) == {"orders": rows, "count": 1}
    assert reads == [1] and receipt_capture.capture_status()["state"] == "degraded"


def test_intent_ack_stays_submitted_when_receipt_write_fails(fake_capture):
    doc = order(norenordno=None, state="SUBMITTING")
    class Instructions:
        async def update_one(self, query, update):
            doc.update(update["$set"])
            return SimpleNamespace(matched_count=1)
        async def find_one(self, query): return doc
    class Broken:
        async def insert_one(self, doc): raise RuntimeError("private")
    store = IntentStore(Instructions(), Broken())
    async def scenario():
        await store.mark_submitted("client-one", "entry-one")
        assert doc["state"] == "SUBMITTED" and doc["norenordno"] == "entry-one"
        await drain()
    asyncio.run(scenario())
    assert doc["state"] == "SUBMITTED" and receipt_capture.capture_status()["state"] == "degraded"
