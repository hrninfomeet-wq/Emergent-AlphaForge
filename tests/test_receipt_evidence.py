"""Durable read integration: fake storage only, no application/broker startup."""
import asyncio
import copy
import json
from types import SimpleNamespace

from app.live.receipt_events import build_event
from app.live.receipt_evidence import attach_evidence, safe_events
from app.live.execution_receipts import receipt_page


ORDER = {"client_order_id": "cid-a", "norenordno": "entry-a", "mode": "live",
         "state": "SUBMITTED", "intent": {"tsym": "NIFTY-TEST", "exch": "NFO",
         "trantype": "B", "prd": "I", "qty": 100, "prctyp": "LMT", "prc": 101}}


def event(**kwargs):
    args = dict(kind="order_fill_snapshot", source="broker_order_book",
                recorded_at="2026-10-25T00:00:00Z", client_order_id="cid-a", norenordno="entry-a",
                instruction=ORDER["intent"], fill={"quantity": 40, "average_price": 100,
                "order_status": "OPEN", "event_time": "10:00:00"})
    args.update(kwargs)
    return build_event(**args)


def matches(row, query):
    if "$or" in query:
        return any(matches(row, q) for q in query["$or"])
    return all(row.get(k) in v["$in"] if isinstance(v, dict) else row.get(k) == v for k, v in query.items())


class Cursor:
    def __init__(self, rows): self.rows = copy.deepcopy(rows)
    def sort(self, key, direction):
        self.rows.sort(key=lambda r: r.get(key) or "", reverse=direction == -1)
        return self
    def skip(self, n): self.rows = self.rows[n:]; return self
    async def to_list(self, length): return self.rows[:length] if length else self.rows


class Collection:
    def __init__(self, rows): self.rows = rows
    def find(self, query, projection): return Cursor([r for r in self.rows if matches(r, query)])
    async def count_documents(self, query): return len([r for r in self.rows if matches(r, query)])


def database(events):
    return SimpleNamespace(live_orders=Collection([ORDER]), live_trades=Collection([]),
                           execution_receipt_events=Collection(events))


def test_restart_reads_persisted_approval_and_fill_without_rewriting_journal():
    approved = event(kind="approval_decided", source="operator_approved", approval_id="approval-a", fill=None)
    data = [event(), approved]
    first = asyncio.run(receipt_page(database(data), limit=50, skip=0, guards=[]))
    restarted = asyncio.run(receipt_page(database(copy.deepcopy(data)), limit=50, skip=0, guards=[]))
    assert first["items"] == restarted["items"]
    item = first["items"][0]
    assert item["durable_evidence"]["state"] == "available"
    assert len(item["durable_evidence"]["events"]) == 2
    assert item["fills"]["quantity"] is None and item["journal"] is None
    assert data[0]["fill"]["quantity"] == 40


def test_missing_history_is_empty_and_failed_read_is_unavailable():
    result = asyncio.run(receipt_page(database([]), limit=50, skip=0, guards=[]))
    assert result["items"][0]["durable_evidence"] == {"state": "available", "events": [], "truncated": False}
    db = database([])
    del db.execution_receipt_events
    result = asyncio.run(receipt_page(db, limit=50, skip=0, guards=[]))
    assert result["items"][0]["durable_evidence"]["state"] == "unavailable"


def test_foreign_orders_excluded_and_conflicting_client_identity_not_certified():
    foreign = event(client_order_id="foreign", norenordno="foreign")
    wrong_client = event(client_order_id="wrong")
    result = asyncio.run(receipt_page(database([foreign, wrong_client]), limit=50, skip=0, guards=[]))
    events = result["items"][0]["durable_evidence"]["events"]
    assert len(events) == 1 and events[0]["evidence_state"] == "conflicting"


def test_read_allowlist_and_corrupt_payload_refused():
    stored = event()
    stored.update(token="SECRET", raw={"password": "SECRET"})
    stored["instruction"]["uid"] = "SECRET"
    assert "SECRET" not in json.dumps(safe_events([stored], ORDER))
    stored["fill"]["quantity"] = 80
    result = asyncio.run(receipt_page(database([stored]), limit=50, skip=0, guards=[]))
    assert result["items"][0]["durable_evidence"]["state"] == "unavailable"


def test_truncation_is_explicit():
    rows = [event(recorded_at=f"2026-10-25T00:00:{n:02d}Z", fill={"quantity": n, "average_price": 100,
                  "order_status": "OPEN", "event_time": f"10:00:{n:02d}"}) for n in range(51)]
    result = asyncio.run(receipt_page(database(rows), limit=50, skip=0, guards=[]))
    evidence = result["items"][0]["durable_evidence"]
    assert evidence["truncated"] is True and len(evidence["events"]) == 50


def test_contradictory_same_timestamp_and_exit_fill_are_flagged_not_selected():
    different = event(fill={"quantity": 60, "average_price": 102, "order_status": "OPEN", "event_time": "10:00:00"})
    assert all(e["evidence_state"] == "conflicting" for e in safe_events([event(), different], ORDER))
    exit_fields = dict(entry_norenordno="entry-a", exit_norenordno="exit-a", fill_id="fill-a",
                       quantity=40, price=110, attribution="explicit_oco_entry_tag")
    exits = [event(kind="exit_fill_evidence", fill=None, exit_evidence=exit_fields),
             event(kind="exit_fill_evidence", fill=None, exit_evidence={**exit_fields, "price": 120})]
    assert all(e["evidence_state"] == "conflicting" for e in safe_events(exits, ORDER))


def test_out_of_order_cumulative_snapshots_remain_distinct():
    late = event(fill={"quantity": 100, "average_price": 102, "order_status": "COMPLETE", "event_time": "10:01:00"})
    events = safe_events([late, event()], ORDER)
    assert [e["fill"]["quantity"] for e in events] == [100, 40]
    assert all(e["evidence_state"] == "observed" for e in events)


def test_duplicate_symbol_different_entry_is_not_joined():
    other = event(norenordno="entry-b", client_order_id="cid-b")
    result = asyncio.run(receipt_page(database([other]), limit=50, skip=0, guards=[]))
    assert result["items"][0]["durable_evidence"]["events"] == []
