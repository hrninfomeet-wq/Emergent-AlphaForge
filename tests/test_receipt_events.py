"""Offline-only receipt ledger contract; collections are in-memory fakes."""
import asyncio
import copy
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from pymongo.errors import DuplicateKeyError

from app.live.receipt_events import ReceiptEventStore, build_event, ensure_indexes


CAPTURE = "2026-10-24T10:00:00Z"
INSTRUCTION = {"exch": "NFO", "tsym": "NIFTY-TEST&26", "trantype": "B", "prd": "I",
               "prctyp": "LMT", "qty": "100", "prc": "102.50", "trgprc": "0", "ret": "DAY"}


def snapshot(**changes):
    fields = {"kind": "order_fill_snapshot", "source": "order_event", "recorded_at": CAPTURE,
              "client_order_id": "app:order/with+punctuation", "norenordno": 241024000001,
              "instruction": INSTRUCTION,
              "fill": {"quantity": "60", "average_price": "101.25", "order_status": "PARTIAL"}}
    fields.update(changes)
    return build_event(**fields)


class FakeCollection:
    def __init__(self):
        self.rows = {}
        self.indexes = {}

    async def insert_one(self, doc):
        if doc["_id"] in self.rows:
            raise DuplicateKeyError("private details must never escape")
        self.rows[doc["_id"]] = copy.deepcopy(doc)

    async def create_index(self, field, unique=False):
        self.indexes[field] = unique
        return field


def run(coro):
    return asyncio.run(coro)


def test_dedup_normalizes_numbers_and_excludes_capture_time():
    first = snapshot()
    second = snapshot(recorded_at="2026-10-25T10:00:00+00:00", fill={
        "quantity": 60.0, "average_price": 101.25, "order_status": "PARTIAL"})
    assert first["event_key"] == second["event_key"]
    assert first["fill"] == {"quantity": 60, "average_price": 101.25, "order_status": "PARTIAL"}
    assert "event_time" not in first["fill"]
    assert first["norenordno"] == "241024000001"


def test_cumulative_out_of_order_restart_and_concurrent_duplicates():
    async def scenario():
        collection = FakeCollection()
        store = ReceiptEventStore(collection)
        later = snapshot(fill={"quantity": 100, "average_price": 102, "order_status": "COMPLETE"})
        earlier = snapshot()
        results = await asyncio.gather(store.append(later), store.append(earlier), store.append(earlier))
        assert results == [{"status": "recorded"}, {"status": "recorded"}, {"status": "duplicate"}]
        restarted = ReceiptEventStore(collection)
        assert await restarted.append(snapshot(recorded_at="2026-10-25T11:00:00Z")) == {"status": "duplicate"}
        assert sorted(row["fill"]["quantity"] for row in collection.rows.values()) == [60, 100]
        assert all(row["recorded_at"] == CAPTURE for row in collection.rows.values())
        assert all(row["_id"] == row["event_key"] for row in collection.rows.values())
    run(scenario())


def test_allowlist_at_every_level_and_no_input_mutation():
    instruction = {**INSTRUCTION, "susertoken": "secret", "uid": "account", "raw": {"password": "secret"}}
    fill = {"quantity": 5, "average_price": 100, "order_status": "CANCELED", "actid": "secret"}
    before = copy.deepcopy((instruction, fill))
    event = snapshot(instruction=instruction, fill=fill, reasons=["missing_fill", "secret", {"token": "secret"}])
    event.update({"event_key": "forged", "_id": "forged", "uid": "secret", "token": "secret"})
    collection = FakeCollection()
    assert run(ReceiptEventStore(collection).append(event)) == {"status": "recorded"}
    row = next(iter(collection.rows.values()))
    serialized = json.dumps(row, allow_nan=False)
    assert "secret" not in serialized
    assert "forged" not in serialized
    assert "raw" not in serialized
    assert (instruction, fill) == before
    assert row["reasons"] == ["missing_fill"]


@pytest.mark.parametrize("value", [True, False, "NaN", float("inf"), -1, 1.5, {}, [], 2**63])
def test_invalid_quantities_omit_numeric_evidence(value):
    event = snapshot(fill={"quantity": value, "average_price": 100, "order_status": "PARTIAL"})
    assert "quantity" not in event["fill"]
    assert event["evidence_state"] == "incomplete"
    assert "invalid_fill" in event["reasons"]
    json.dumps(event, allow_nan=False)


@pytest.mark.parametrize("value", [True, "NaN", float("inf"), -1, {}, [], None])
def test_invalid_prices_and_instruction(value):
    event = snapshot(instruction={**INSTRUCTION, "qty": value}, fill={
        "quantity": 5, "average_price": value, "order_status": "COMPLETE"})
    assert "qty" not in event["instruction"]
    assert "average_price" not in event["fill"]
    assert event["reasons"] == ["invalid_fill", "invalid_instruction"]


def test_zero_fill_canceled_partial_and_overfill_states():
    assert snapshot(fill={"quantity": 0, "average_price": 0, "order_status": "CANCELED"})["evidence_state"] == "observed"
    assert snapshot(fill={"quantity": 60, "average_price": 100, "order_status": "CANCELED"})["fill"]["quantity"] == 60
    event = snapshot(fill={"quantity": 101, "average_price": 100})
    assert event["evidence_state"] == "conflicting"
    assert event["reasons"] == ["incomplete_status", "overfill"]


def test_explicit_exit_evidence_missing_identifiers_and_self_identity_conflict():
    fields = {"entry_norenordno": "entry", "exit_norenordno": "exit", "fill_id": "trade/1",
              "quantity": "5", "price": "110.5", "event_time": "10:01:00 24-10-2026",
              "attribution": "explicit_order_link", "token": "secret"}
    event = snapshot(kind="exit_fill_evidence", exit_evidence=fields)
    assert event["evidence_state"] == "observed"
    assert event["exit_evidence"]["event_time"] == fields["event_time"]
    assert "token" not in event["exit_evidence"]
    missing = snapshot(kind="exit_fill_evidence", exit_evidence={"quantity": 5, "price": 100})
    assert missing["reasons"] == ["ambiguous_exit", "missing_exit_id", "missing_fill_id"]
    conflict = snapshot(kind="exit_fill_evidence", exit_evidence={**fields, "exit_norenordno": "entry"})
    assert conflict["evidence_state"] == "conflicting"


@pytest.mark.parametrize("changes", [{"kind": "unknown"}, {"source": {}}, {"source": "x" * 81},
                                      {"evidence_state": "trusted"}, {"recorded_at": "yesterday"},
                                      {"recorded_at": datetime(2026, 10, 24)}])
def test_invalid_envelopes_rejected(changes):
    with pytest.raises(ValueError):
        snapshot(**changes)


def test_identity_rejects_structures_and_control_text_but_preserves_scalar_ids():
    event = snapshot(client_order_id={"token": "secret"}, norenordno="\n")
    assert event["client_order_id"] is None
    assert event["norenordno"] is None
    assert event["reasons"] == ["missing_identity"]
    assert snapshot(norenordno="NOREN:123/456-abc+def")["norenordno"] == "NOREN:123/456-abc+def"


def test_capture_time_utc_and_supplied_event_time_preserved():
    event = snapshot(recorded_at=datetime(2026, 10, 24, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
                     fill={"quantity": 0, "order_status": "OPEN", "event_time": "24-10-2026 15:29:59"})
    assert event["recorded_at"] == CAPTURE
    assert event["fill"]["event_time"] == "24-10-2026 15:29:59"


def test_malformed_events_cannot_bypass_rebuild_and_bad_evidence_is_stable():
    collection = FakeCollection()
    store = ReceiptEventStore(collection)
    for bad in (None, [], {}, {"kind": "order_fill_snapshot", "source": "order_event", "recorded_at": "secret"}):
        assert run(store.append(bad)) == {"status": "unavailable"}
    event = snapshot(fill=True)
    assert run(store.append(event)) == {"status": "recorded"}
    assert run(store.append(event)) == {"status": "duplicate"}
    assert next(iter(collection.rows.values()))["event_key"] == event["event_key"]


def test_writer_and_index_failures_are_bounded_and_secret_free():
    class Broken:
        async def insert_one(self, doc):
            raise RuntimeError("api-token secret")

        async def create_index(self, *args, **kwargs):
            raise RuntimeError("account secret")

    class Slow(Broken):
        async def insert_one(self, doc):
            await asyncio.sleep(60)

    assert run(ReceiptEventStore(Broken()).append(snapshot())) == {"status": "unavailable"}
    assert run(ensure_indexes(Broken())) is False
    started = time.monotonic()
    assert run(ReceiptEventStore(Slow()).append(snapshot())) == {"status": "unavailable"}
    assert time.monotonic() - started < 1.0


def test_index_startup_is_repeatable_and_unique():
    collection = FakeCollection()
    assert run(ensure_indexes(collection)) is True
    assert run(ensure_indexes(collection)) is True
    assert collection.indexes == {"event_key": True}


def test_approval_instruction_and_snapshot_have_distinct_keys():
    approval = snapshot(kind="approval_decided", approval_id="approval:1", fill=None)
    submitted = snapshot(kind="instruction_submitted", fill=None)
    assert approval["evidence_state"] == submitted["evidence_state"] == "observed"
    assert len({approval["event_key"], submitted["event_key"], snapshot()["event_key"]}) == 3


@pytest.mark.parametrize("evidence", [None, {}, True, {"quantity": True}, {"quantity": 0},
                                      {"quantity": 4, "average_price": "Infinity"}])
def test_rebuilding_incomplete_evidence_keeps_its_key(evidence):
    event = snapshot(fill=evidence)
    rebuilt = build_event(**{key: value for key, value in event.items()
                             if key not in {"schema_version", "event_key"}})
    assert rebuilt == event


def test_positive_exit_price_that_underflows_is_invalid():
    event = snapshot(kind="exit_fill_evidence", exit_evidence={
        "entry_norenordno": "entry", "exit_norenordno": "exit", "fill_id": "trade",
        "quantity": 1, "price": "1e-9999", "attribution": "explicit_order_link"})
    assert "price" not in event["exit_evidence"]
    assert event["evidence_state"] == "incomplete"
