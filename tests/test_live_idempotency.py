"""TDD tests for backend/app/live/idempotency.py (Task L1.2).

Covers:
  new_client_order_id:
    - returns a non-empty string
    - successive calls produce unique values

  IntentStore (via FakeAsyncCollection):
    - record_intent writes a doc with state=INTENT, norenordno=None
    - record_intent is idempotent: second call with same cid returns existing doc
      and does NOT create a duplicate
    - is_already_submitted → False after record_intent (no norenordno yet)
    - mark_submitted sets norenordno + state=SUBMITTED
    - is_already_submitted → True after mark_submitted
    - mark_submitted a second time with a DIFFERENT norenordno raises
      AlreadySubmittedError (matched 0 conditional update)
    - mark_submitted a second time with the SAME norenordno raises too
      (matched 0 — doc already has norenordno set)
    - resume_unsubmitted returns only INTENT/no-norenordno docs
    - resume_unsubmitted excludes docs that have been submitted
    - RESTART SIMULATION: record_intent, then construct a NEW IntentStore over
      the SAME fake collection, call resume_unsubmitted → finds the un-sent intent
    - timestamps: now_iso is stored; injected value appears in the doc

  F1 — atomic insert (unique-index race safety):
    - Two record_intent calls for the same cid against a unique-enforcing fake →
      exactly ONE doc; second call returns the existing doc, no exception leaks.

  F2 — reject falsy norenordno:
    - mark_submitted(cid, None) → ValueError before any write
    - mark_submitted(cid, "   ") → ValueError before any write
    - mark_submitted(cid, "") → ValueError before any write

  F3 — distinguish not-found from already-submitted:
    - mark_submitted for a cid that never had record_intent → IntentNotFoundError
    - mark_submitted for an already-submitted cid (norenordno set) → AlreadySubmittedError
    - (existing test_mark_unknown_cid_raises updated to expect IntentNotFoundError)

  F4 — idempotent-retry contract:
    - Second mark_submitted(cid, SAME_X) → no-op success (no exception)
    - Second mark_submitted(cid, DIFFERENT_Y) → AlreadySubmittedError

  F5 — per-cid submit claim:
    - claim_for_submit(cid) on INTENT doc → True
    - second claim_for_submit(cid) on SUBMITTING doc → False
    - claim_for_submit(cid) on unknown cid → False
    - resume_unsubmitted includes SUBMITTING docs with norenordno=None
    - mark_submitted accepts SUBMITTING state (state-agnostic on norenordno gate)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.broker_protocol import OrderIntent
from app.live.idempotency import (
    AlreadySubmittedError,
    IntentNotFoundError,
    IntentStore,
    new_client_order_id,
)

# pymongo DuplicateKeyError — needed for the fake that simulates unique index
from pymongo.errors import DuplicateKeyError


# ---------------------------------------------------------------------------
# FakeAsyncCollection — in-memory Mongo stand-in
# ---------------------------------------------------------------------------

class _FakeCursor:
    """Minimal async iterable / to_list helper returned by FakeAsyncCollection.find."""

    def __init__(self, docs: List[dict]) -> None:
        self._docs = docs

    async def to_list(self, length: Optional[int] = None) -> List[dict]:
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class _UpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class FakeAsyncCollection:
    """In-memory async collection satisfying the IntentStore collection interface.

    Stores docs in self.docs (a plain list) so tests can inspect state directly.

    Call ensure_unique(field) to activate duplicate-key enforcement on that field;
    subsequent insert_one calls that duplicate a value for that field will raise
    pymongo.errors.DuplicateKeyError — matching production Mongo unique-index behaviour.
    """

    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []
        self._unique_fields: List[str] = []

    def ensure_unique(self, field: str) -> None:
        """Mark *field* as unique — insert_one will raise DuplicateKeyError on dup."""
        if field not in self._unique_fields:
            self._unique_fields.append(field)

    async def find_one(
        self,
        query: Dict[str, Any],
        projection: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        for doc in self.docs:
            if _matches(doc, query):
                if projection:
                    return _project(doc, projection)
                return dict(doc)
        return None

    async def insert_one(self, doc: Dict[str, Any]) -> Any:
        for field in self._unique_fields:
            if field in doc:
                for existing in self.docs:
                    if existing.get(field) == doc[field]:
                        raise DuplicateKeyError(
                            f"E11000 duplicate key error — field: {field}"
                        )
        self.docs.append(dict(doc))

    async def update_one(
        self,
        query: Dict[str, Any],
        update: Dict[str, Any],
        upsert: bool = False,
    ) -> _UpdateResult:
        for doc in self.docs:
            if _matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                return _UpdateResult(matched_count=1)
        return _UpdateResult(matched_count=0)

    def find(
        self,
        query: Dict[str, Any],
        projection: Optional[Dict[str, Any]] = None,
    ) -> _FakeCursor:
        results = [
            (dict(d) if not projection else _project(d, projection))
            for d in self.docs
            if _matches(d, query)
        ]
        return _FakeCursor(results)

    async def create_index(self, field: str, unique: bool = False) -> str:
        """Simulate index creation; activates unique enforcement when unique=True."""
        if unique:
            self.ensure_unique(field)
        return f"{field}_1"


# ---------------------------------------------------------------------------
# Helpers for FakeAsyncCollection
# ---------------------------------------------------------------------------

def _matches(doc: dict, query: dict) -> bool:
    """Evaluate a flat Mongo query dict (equality checks only; None matches None)."""
    for k, v in query.items():
        if doc.get(k) != v:
            return False
    return True


def _project(doc: dict, projection: dict) -> dict:
    """Apply a Mongo-style inclusion projection (1 = include)."""
    return {k: doc[k] for k in projection if k in doc}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_intent(cid: str = "cid-001") -> OrderIntent:
    return OrderIntent(
        client_order_id=cid,
        trantype="B",
        prctyp="LMT",
        exch="NFO",
        tsym="NIFTY25000CE",
        qty=65,
        prc=158.5,
    )


def _store() -> tuple[IntentStore, FakeAsyncCollection]:
    col = FakeAsyncCollection()
    return IntentStore(col), col


# ---------------------------------------------------------------------------
# new_client_order_id
# ---------------------------------------------------------------------------

class TestNewClientOrderId:
    def test_returns_non_empty_string(self):
        cid = new_client_order_id()
        assert isinstance(cid, str) and len(cid) > 0

    def test_unique_on_successive_calls(self):
        ids = {new_client_order_id() for _ in range(50)}
        assert len(ids) == 50


# ---------------------------------------------------------------------------
# record_intent
# ---------------------------------------------------------------------------

class TestRecordIntent:
    def test_record_creates_doc_with_intent_state(self):
        store, col = _store()
        intent = _make_intent("cid-r1")
        doc = asyncio.run(store.record_intent(intent, mode="mock"))

        assert doc["client_order_id"] == "cid-r1"
        assert doc["state"] == "INTENT"
        assert doc["norenordno"] is None
        assert len(col.docs) == 1

    def test_record_stores_intent_fields(self):
        store, col = _store()
        intent = _make_intent("cid-r2")
        doc = asyncio.run(store.record_intent(intent, mode="mock"))

        stored_intent = doc["intent"]
        assert stored_intent["tsym"] == "NIFTY25000CE"
        assert stored_intent["qty"] == 65
        assert stored_intent["prc"] == 158.5

    def test_record_stores_deployment_id_and_mode(self):
        store, col = _store()
        intent = _make_intent("cid-r3")
        doc = asyncio.run(
            store.record_intent(intent, deployment_id="dep-42", mode="live")
        )

        assert doc["deployment_id"] == "dep-42"
        assert doc["mode"] == "live"

    def test_record_stores_injected_timestamp(self):
        store, col = _store()
        intent = _make_intent("cid-r4")
        doc = asyncio.run(
            store.record_intent(intent, now_iso="2026-06-22T09:15:00+00:00")
        )

        assert doc["ts_intent"] == "2026-06-22T09:15:00+00:00"

    def test_record_is_idempotent_second_call_returns_existing(self):
        """Second record_intent with the same cid must NOT overwrite or duplicate.

        In production the unique index on client_order_id enforces this.  We
        enable ensure_unique here so the FakeAsyncCollection enforces the same
        DuplicateKeyError → fallback path that real Mongo would trigger.
        """
        col = FakeAsyncCollection()
        col.ensure_unique("client_order_id")  # simulate production unique index
        store = IntentStore(col)
        intent = _make_intent("cid-r5")
        doc1 = asyncio.run(
            store.record_intent(intent, mode="mock", now_iso="2026-06-22T09:00:00+00:00")
        )
        # Second call — different timestamp to detect any overwrite
        doc2 = asyncio.run(
            store.record_intent(intent, mode="live", now_iso="2026-06-22T10:00:00+00:00")
        )

        # Only one doc in the collection
        assert len(col.docs) == 1
        # Second call returns the original doc unchanged
        assert doc2["ts_intent"] == "2026-06-22T09:00:00+00:00"
        assert doc2["mode"] == "mock"


# ---------------------------------------------------------------------------
# is_already_submitted
# ---------------------------------------------------------------------------

class TestIsAlreadySubmitted:
    def test_false_after_record_only(self):
        """intent recorded but not submitted → is_already_submitted False."""
        store, _ = _store()
        intent = _make_intent("cid-sub1")
        asyncio.run(store.record_intent(intent))

        result = asyncio.run(store.is_already_submitted("cid-sub1"))
        assert result is False

    def test_false_for_unknown_cid(self):
        """No doc at all → not submitted."""
        store, _ = _store()
        assert asyncio.run(store.is_already_submitted("cid-ghost")) is False

    def test_true_after_mark_submitted(self):
        store, _ = _store()
        intent = _make_intent("cid-sub2")
        asyncio.run(store.record_intent(intent))
        asyncio.run(store.mark_submitted("cid-sub2", norenordno="NORD-001"))

        assert asyncio.run(store.is_already_submitted("cid-sub2")) is True


# ---------------------------------------------------------------------------
# mark_submitted
# ---------------------------------------------------------------------------

class TestMarkSubmitted:
    def test_mark_sets_norenordno_and_state(self):
        store, col = _store()
        intent = _make_intent("cid-m1")
        asyncio.run(store.record_intent(intent))
        asyncio.run(store.mark_submitted("cid-m1", norenordno="NORD-111"))

        doc = col.docs[0]
        assert doc["norenordno"] == "NORD-111"
        assert doc["state"] == "SUBMITTED"

    def test_mark_stores_ts_submitted(self):
        store, col = _store()
        intent = _make_intent("cid-m2")
        asyncio.run(store.record_intent(intent))
        asyncio.run(
            store.mark_submitted(
                "cid-m2", norenordno="NORD-222", now_iso="2026-06-22T09:20:00+00:00"
            )
        )

        doc = col.docs[0]
        assert doc["ts_submitted"] == "2026-06-22T09:20:00+00:00"

    def test_second_mark_same_norenordno_is_noop(self):
        """F4: Second mark_submitted with the SAME norenordno → idempotent no-op.
        The order was already confirmed delivered — re-confirming the same
        number is safe and must not raise."""
        store, _ = _store()
        intent = _make_intent("cid-m3")
        asyncio.run(store.record_intent(intent))
        asyncio.run(store.mark_submitted("cid-m3", norenordno="NORD-333"))

        # Must NOT raise — same norenordno = idempotent retry
        asyncio.run(store.mark_submitted("cid-m3", norenordno="NORD-333"))

    def test_second_mark_different_norenordno_raises(self):
        """A DIFFERENT norenordno on the second call is the critical hazard —
        it must be refused so we never overwrite a real broker order number."""
        store, _ = _store()
        intent = _make_intent("cid-m4")
        asyncio.run(store.record_intent(intent))
        asyncio.run(store.mark_submitted("cid-m4", norenordno="NORD-444-A"))

        with pytest.raises(AlreadySubmittedError):
            asyncio.run(store.mark_submitted("cid-m4", norenordno="NORD-444-B"))

    def test_mark_unknown_cid_raises_intent_not_found(self):
        """F3: mark_submitted without a prior record_intent → IntentNotFoundError
        (not AlreadySubmittedError — the doc doesn't exist at all)."""
        store, _ = _store()
        with pytest.raises(IntentNotFoundError):
            asyncio.run(store.mark_submitted("cid-ghost", norenordno="NORD-000"))

    def test_mark_already_submitted_different_norenordno_raises_already_submitted(self):
        """F3: doc EXISTS and already has a norenordno → AlreadySubmittedError."""
        store, _ = _store()
        intent = _make_intent("cid-m5")
        asyncio.run(store.record_intent(intent))
        asyncio.run(store.mark_submitted("cid-m5", norenordno="NORD-555-A"))

        with pytest.raises(AlreadySubmittedError):
            asyncio.run(store.mark_submitted("cid-m5", norenordno="NORD-555-B"))


# ---------------------------------------------------------------------------
# F2 — reject falsy norenordno
# ---------------------------------------------------------------------------

class TestFalsyNorenordno:
    """F2: mark_submitted must reject None/empty/whitespace-only norenordno
    BEFORE touching the database — the doc must remain in INTENT/None state."""

    def test_none_norenordno_raises_value_error(self):
        store, col = _store()
        intent = _make_intent("cid-f2a")
        asyncio.run(store.record_intent(intent))

        with pytest.raises(ValueError):
            asyncio.run(store.mark_submitted("cid-f2a", norenordno=None))  # type: ignore[arg-type]

        # Doc must be unchanged — still INTENT with norenordno=None
        doc = col.docs[0]
        assert doc["state"] == "INTENT"
        assert doc["norenordno"] is None

    def test_empty_string_norenordno_raises_value_error(self):
        store, col = _store()
        intent = _make_intent("cid-f2b")
        asyncio.run(store.record_intent(intent))

        with pytest.raises(ValueError):
            asyncio.run(store.mark_submitted("cid-f2b", norenordno=""))

        doc = col.docs[0]
        assert doc["state"] == "INTENT"
        assert doc["norenordno"] is None

    def test_whitespace_only_norenordno_raises_value_error(self):
        store, col = _store()
        intent = _make_intent("cid-f2c")
        asyncio.run(store.record_intent(intent))

        with pytest.raises(ValueError):
            asyncio.run(store.mark_submitted("cid-f2c", norenordno="   "))

        doc = col.docs[0]
        assert doc["state"] == "INTENT"
        assert doc["norenordno"] is None


# ---------------------------------------------------------------------------
# F1 — atomic insert: unique-index race safety
# ---------------------------------------------------------------------------

class TestAtomicInsert:
    """F1: record_intent must rely on insert_one + DuplicateKeyError fallback,
    not find_one-then-insert_one.  When unique enforcement is active on the
    collection, two concurrent record_intent calls for the same cid must result
    in exactly ONE doc; the second call returns the existing doc without raising."""

    def test_two_record_intents_same_cid_unique_mode_one_doc(self):
        col = FakeAsyncCollection()
        col.ensure_unique("client_order_id")  # simulate production unique index
        store = IntentStore(col)

        intent = _make_intent("cid-atomic-1")
        doc1 = asyncio.run(
            store.record_intent(intent, mode="mock", now_iso="2026-06-22T09:00:00+00:00")
        )
        # Second call — different timestamp; in a race this would interleave
        doc2 = asyncio.run(
            store.record_intent(intent, mode="live", now_iso="2026-06-22T10:00:00+00:00")
        )

        # Exactly one doc stored
        assert len(col.docs) == 1
        # Both return the original doc (first-writer wins)
        assert doc1["ts_intent"] == "2026-06-22T09:00:00+00:00"
        assert doc2["ts_intent"] == "2026-06-22T09:00:00+00:00"
        assert doc2["mode"] == "mock"

    def test_no_exception_leaks_on_duplicate_key(self):
        """DuplicateKeyError must be caught inside record_intent — never propagated."""
        col = FakeAsyncCollection()
        col.ensure_unique("client_order_id")
        store = IntentStore(col)

        intent = _make_intent("cid-atomic-2")
        asyncio.run(store.record_intent(intent))

        # Must not raise anything — not DuplicateKeyError, not anything else
        try:
            asyncio.run(store.record_intent(intent))
        except Exception as exc:
            pytest.fail(f"record_intent raised unexpectedly: {exc!r}")


# ---------------------------------------------------------------------------
# F5 — per-cid submit claim
# ---------------------------------------------------------------------------

class TestClaimForSubmit:
    """F5: claim_for_submit does an atomic INTENT→SUBMITTING transition.

    Contracts:
      - First claim on an INTENT doc → True; state becomes SUBMITTING
      - Second claim on the now-SUBMITTING doc → False (not INTENT any more)
      - Claim on unknown cid → False
      - resume_unsubmitted includes SUBMITTING docs with norenordno=None
        (crashed-claim must be resumable)
      - mark_submitted accepts SUBMITTING state (matches on norenordno=None,
        not on state) → sets SUBMITTED
    """

    def test_claim_intent_returns_true(self):
        store, col = _store()
        intent = _make_intent("cid-claim-1")
        asyncio.run(store.record_intent(intent))

        result = asyncio.run(store.claim_for_submit("cid-claim-1"))
        assert result is True
        assert col.docs[0]["state"] == "SUBMITTING"

    def test_second_claim_returns_false(self):
        store, col = _store()
        intent = _make_intent("cid-claim-2")
        asyncio.run(store.record_intent(intent))
        asyncio.run(store.claim_for_submit("cid-claim-2"))  # first → True

        result = asyncio.run(store.claim_for_submit("cid-claim-2"))
        assert result is False

    def test_claim_unknown_cid_returns_false(self):
        store, _ = _store()
        result = asyncio.run(store.claim_for_submit("cid-ghost-claim"))
        assert result is False

    def test_resume_unsubmitted_includes_submitting_no_norenordno(self):
        """A crashed claim (SUBMITTING + norenordno=None) must appear in resume."""
        store, col = _store()
        intent = _make_intent("cid-claim-3")
        asyncio.run(store.record_intent(intent))
        asyncio.run(store.claim_for_submit("cid-claim-3"))  # → SUBMITTING, norenordno=None

        pending = asyncio.run(store.resume_unsubmitted())
        cids = [d["client_order_id"] for d in pending]
        assert "cid-claim-3" in cids

    def test_resume_unsubmitted_excludes_submitted_submitting(self):
        """A SUBMITTING doc that got mark_submitted must NOT appear in resume."""
        store, _ = _store()
        intent = _make_intent("cid-claim-4")
        asyncio.run(store.record_intent(intent))
        asyncio.run(store.claim_for_submit("cid-claim-4"))
        asyncio.run(store.mark_submitted("cid-claim-4", norenordno="NORD-CL4"))

        pending = asyncio.run(store.resume_unsubmitted())
        assert not any(d["client_order_id"] == "cid-claim-4" for d in pending)

    def test_mark_submitted_accepts_submitting_state(self):
        """mark_submitted must succeed on a SUBMITTING (claimed) doc — the
        norenordno=None gate is what matters, not the state field."""
        store, col = _store()
        intent = _make_intent("cid-claim-5")
        asyncio.run(store.record_intent(intent))
        asyncio.run(store.claim_for_submit("cid-claim-5"))

        # Should not raise — SUBMITTING + norenordno=None → valid target
        asyncio.run(store.mark_submitted("cid-claim-5", norenordno="NORD-CL5"))

        doc = col.docs[0]
        assert doc["norenordno"] == "NORD-CL5"
        assert doc["state"] == "SUBMITTED"

    def test_claim_stores_ts_claim(self):
        """claim_for_submit should record ts_claim on the doc."""
        store, col = _store()
        intent = _make_intent("cid-claim-6")
        asyncio.run(store.record_intent(intent))
        asyncio.run(
            store.claim_for_submit("cid-claim-6", now_iso="2026-06-22T09:30:00+00:00")
        )

        doc = col.docs[0]
        assert doc.get("ts_claim") == "2026-06-22T09:30:00+00:00"


# ---------------------------------------------------------------------------
# resume_unsubmitted
# ---------------------------------------------------------------------------

class TestResumeUnsubmitted:
    def test_returns_intent_state_docs(self):
        store, _ = _store()
        intent = _make_intent("cid-res1")
        asyncio.run(store.record_intent(intent))

        pending = asyncio.run(store.resume_unsubmitted())
        assert len(pending) == 1
        assert pending[0]["client_order_id"] == "cid-res1"

    def test_excludes_submitted_docs(self):
        store, _ = _store()
        # Two intents: one submitted, one not
        asyncio.run(store.record_intent(_make_intent("cid-res2a")))
        asyncio.run(store.record_intent(_make_intent("cid-res2b")))
        asyncio.run(store.mark_submitted("cid-res2b", norenordno="NORD-SUB"))

        pending = asyncio.run(store.resume_unsubmitted())
        cids = [d["client_order_id"] for d in pending]
        assert "cid-res2a" in cids
        assert "cid-res2b" not in cids

    def test_empty_when_all_submitted(self):
        store, _ = _store()
        asyncio.run(store.record_intent(_make_intent("cid-res3")))
        asyncio.run(store.mark_submitted("cid-res3", norenordno="NORD-DONE"))

        pending = asyncio.run(store.resume_unsubmitted())
        assert pending == []

    def test_empty_collection_returns_empty_list(self):
        store, _ = _store()
        assert asyncio.run(store.resume_unsubmitted()) == []

    def test_multiple_pending_all_returned(self):
        store, _ = _store()
        for i in range(5):
            asyncio.run(store.record_intent(_make_intent(f"cid-multi-{i}")))

        pending = asyncio.run(store.resume_unsubmitted())
        assert len(pending) == 5


# ---------------------------------------------------------------------------
# RESTART SIMULATION
# ---------------------------------------------------------------------------

class TestRestartSimulation:
    """Simulate a process crash between record_intent and mark_submitted.

    Scenario:
      1. record_intent is called → doc written to the collection.
      2. Process "crashes" before the broker POST / mark_submitted.
      3. A NEW IntentStore is constructed over the SAME fake collection.
      4. resume_unsubmitted must surface the un-sent intent.
      5. The engine can then reconcile against the broker before re-sending.
    """

    def test_resume_finds_unsent_intent_after_restart(self):
        col = FakeAsyncCollection()  # shared collection (survives "restart")

        # --- before crash ---
        store_before = IntentStore(col)
        intent = _make_intent("cid-restart-1")
        asyncio.run(
            store_before.record_intent(
                intent, mode="mock", now_iso="2026-06-22T09:15:00+00:00"
            )
        )
        # Crash happens here — mark_submitted never called

        # --- after restart ---
        store_after = IntentStore(col)  # fresh instance, same underlying collection
        pending = asyncio.run(store_after.resume_unsubmitted())

        assert len(pending) == 1
        assert pending[0]["client_order_id"] == "cid-restart-1"
        assert pending[0]["state"] == "INTENT"
        assert pending[0]["norenordno"] is None

    def test_submitted_intent_not_replayed_after_restart(self):
        """If mark_submitted ran before the crash, resume_unsubmitted must
        NOT return that doc — it was already sent to the broker."""
        col = FakeAsyncCollection()

        store_before = IntentStore(col)
        intent = _make_intent("cid-restart-2")
        asyncio.run(store_before.record_intent(intent))
        asyncio.run(store_before.mark_submitted("cid-restart-2", norenordno="NORD-PRE"))

        # Restart
        store_after = IntentStore(col)
        pending = asyncio.run(store_after.resume_unsubmitted())
        assert pending == []

    def test_mixed_restart_returns_only_unsent(self):
        """Multiple intents: some submitted, some not — only unsent ones resurface."""
        col = FakeAsyncCollection()

        store_before = IntentStore(col)
        asyncio.run(store_before.record_intent(_make_intent("cid-sent")))
        asyncio.run(store_before.mark_submitted("cid-sent", norenordno="NORD-S"))
        asyncio.run(store_before.record_intent(_make_intent("cid-unsent")))

        # Restart
        store_after = IntentStore(col)
        pending = asyncio.run(store_after.resume_unsubmitted())

        cids = [d["client_order_id"] for d in pending]
        assert "cid-unsent" in cids
        assert "cid-sent" not in cids


# ---------------------------------------------------------------------------
# Guard helper: dedup rule integration
# ---------------------------------------------------------------------------

class TestDedupRule:
    """Document and exercise the caller-side dedup contract."""

    def test_guard_refuses_second_post_after_mark_submitted(self):
        """Simulate the place() flow: record → check → POST → mark_submitted.
        A retry loop that calls is_already_submitted before re-posting must
        see True and abort — never double-submit."""
        store, _ = _store()
        intent = _make_intent("cid-dedup-1")

        # First submission
        asyncio.run(store.record_intent(intent))
        assert not asyncio.run(store.is_already_submitted("cid-dedup-1"))
        # (broker POST happens here)
        asyncio.run(store.mark_submitted("cid-dedup-1", norenordno="NORD-D1"))

        # Retry attempt — the guard must block
        already = asyncio.run(store.is_already_submitted("cid-dedup-1"))
        assert already is True, "Guard must detect the already-submitted intent"

    def test_network_timeout_before_mark_submitted_not_a_duplicate(self):
        """Simulate a network timeout: record_intent succeeded, but mark_submitted
        was never called (POST may or may not have reached the broker).  The intent
        stays in INTENT state → resume_unsubmitted surfaces it for reconciliation.
        No duplicate: the engine checks the broker's order book before re-sending."""
        store, _ = _store()
        intent = _make_intent("cid-dedup-2")

        asyncio.run(store.record_intent(intent))
        # POST sent (maybe), timeout fires, mark_submitted never runs

        # is_already_submitted must return False (no norenordno recorded)
        assert asyncio.run(store.is_already_submitted("cid-dedup-2")) is False

        # resume_unsubmitted surfaces it for reconciliation
        pending = asyncio.run(store.resume_unsubmitted())
        assert any(d["client_order_id"] == "cid-dedup-2" for d in pending)
