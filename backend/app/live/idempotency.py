"""Idempotency + restart-survivable order-intent store (L1.2).

Design contract
---------------
The INTENT STORE is the single source of truth for whether a client_order_id
has been (or is about to be) submitted to the broker.

Dedup rule (callers MUST honour):
  1. Call `new_client_order_id()` to mint a fresh cid.
  2. Call `record_intent(intent, ...)` BEFORE any broker POST — this persists
     the intent with state="INTENT" and norenordno=None.
  3. Call `claim_for_submit(cid)` for an atomic INTENT→SUBMITTING transition;
     only proceed with the broker POST if it returns True.
  4. On a successful broker POST call `mark_submitted(cid, norenordno)`.
  5. On startup / reconnect call `resume_unsubmitted()` and reconcile those
     intents against the broker before deciding to re-send.
  6. Call `is_already_submitted(cid)` as an additional guard if needed.

ENGINE-LEVEL requirements this store CANNOT enforce (for L1.3/L2.3):
  (1) The order builder MUST set remarks == client_order_id on every order
      payload before it is posted to the broker.
  (2) On resume, the engine MUST search the broker's live order book for an
      order whose remarks field matches the cid BEFORE re-POSTing any doc
      returned by resume_unsubmitted() — this reconciliation step is the
      last line of defence against double-submission.
  (3) The place() flow MUST be serialised per cid:
        claim_for_submit(cid) → POST → mark_submitted(cid, norenordno)
      No two coroutines should concurrently attempt to submit the same cid.
  (4) On any retry the engine MUST reuse the SAME cid minted for the original
      attempt — it must NOT mint a new cid for a retry of the same order.

The class is DB-AGNOSTIC: the constructor takes any "collection" object that
exposes the async interface below (motor's AsyncIOMotorCollection satisfies it;
tests pass a FakeAsyncCollection).

Collection async interface expected:
  find_one(query, projection=None) -> dict | None
  insert_one(doc) -> Any            # raises DuplicateKeyError on unique index dup
  update_one(query, update, upsert=False) -> UpdateResult-like (has .matched_count)
  find(query, projection=None) -> async-iterable with .to_list(length)
  create_index(field, unique=False) -> str  # called by ensure_indexes()

The module-level `default_store()` helper wires production code to Mongo
without importing the DB anywhere else in this file.

Unique-index guard (F1)
-----------------------
`ensure_indexes(collection)` creates a unique index on client_order_id.  That
unique index is the REAL race guard: even if two concurrent coroutines both pass
the find_one check before either inserts, exactly one insert_one will succeed
and the other will raise DuplicateKeyError.  `record_intent` catches that error
and falls back to find_one — so the caller always gets the winning doc and never
sees the exception.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from pymongo.errors import DuplicateKeyError

from app.live.broker_protocol import OrderIntent


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def new_client_order_id() -> str:
    """Return a fresh UUID4 hex string suitable for use as a Flattrade
    client_order_id (remarks field).  Guaranteed globally unique, collision
    probability negligible."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class AlreadySubmittedError(RuntimeError):
    """Raised by mark_submitted when the doc already has a norenordno (and the
    provided norenordno is different from the stored one)."""


class IntentNotFoundError(RuntimeError):
    """Raised by mark_submitted when no intent doc exists for the given cid.

    Callers should NOT attempt to submit an order without first calling
    record_intent — this error indicates a programming error or a very
    unexpected store inconsistency.
    """


# ---------------------------------------------------------------------------
# Index setup — call once at startup (production only)
# ---------------------------------------------------------------------------

async def ensure_indexes(collection: Any) -> None:
    """Create the unique index on client_order_id that backs the atomic-insert
    race guard (F1).

    Must be called at application startup before any orders are processed.
    The unique index is what makes `record_intent`'s DuplicateKeyError fallback
    correct — without it, the fallback is still safe but the race window exists.

    Safe to call repeatedly (create_index is idempotent in MongoDB).
    """
    await collection.create_index("client_order_id", unique=True)


# ---------------------------------------------------------------------------
# Intent store
# ---------------------------------------------------------------------------

class IntentStore:
    """Async intent/idempotency store backed by an injectable collection.

    Never imports app.db — the caller passes the collection at construction
    time.  Production code uses `default_store()`; tests pass a
    `FakeAsyncCollection`.
    """

    def __init__(self, collection: Any) -> None:
        self._col = collection

    # ------------------------------------------------------------------
    # record_intent — write BEFORE any broker POST
    # ------------------------------------------------------------------

    async def record_intent(
        self,
        intent: OrderIntent,
        *,
        deployment_id: Optional[str] = None,
        mode: str = "mock",
        now_iso: Optional[str] = None,
    ) -> dict:
        """Persist an intent doc with state="INTENT" before any broker POST.

        Idempotent: if a doc with the same client_order_id already exists the
        existing doc is returned unchanged (no overwrite).

        F1 — Atomic insert:
          Attempts insert_one FIRST.  If the unique index rejects it with
          DuplicateKeyError (i.e. a concurrent writer already inserted this cid)
          we fall back to find_one and return the existing doc.  This eliminates
          the classic find-then-insert race window: the unique index is the real
          guard, not an application-level check.

        Parameters
        ----------
        intent:        The OrderIntent to record.
        deployment_id: Optional deployment context (for audit / grouping).
        mode:          "mock" | "live" — which broker path will be used.
        now_iso:       Injected timestamp (ISO 8601); defaults to UTC now.
                       Pass this in tests for deterministic assertions.

        Returns
        -------
        The doc that is now stored (either newly inserted or the pre-existing one).
        """
        ts = now_iso or _utcnow_iso()

        doc: Dict[str, Any] = {
            "client_order_id": intent.client_order_id,
            "deployment_id": deployment_id,
            "mode": mode,
            "intent": _intent_to_dict(intent),
            "state": "INTENT",
            "norenordno": None,
            "ts_intent": ts,
        }

        # F1: attempt insert first; catch DuplicateKeyError for idempotency.
        # The unique index on client_order_id is the authoritative race guard.
        try:
            await self._col.insert_one(doc)
            return doc
        except DuplicateKeyError:
            # Another writer won the race — return the existing doc.
            existing = await self._col.find_one(
                {"client_order_id": intent.client_order_id}
            )
            # existing should never be None here (the dup key proves it exists),
            # but guard defensively just in case.
            if existing is not None:
                return existing
            # If somehow it vanished between the dup error and the find
            # (extremely unlikely), re-raise to surface the inconsistency.
            raise

    # ------------------------------------------------------------------
    # claim_for_submit — F5: atomic INTENT→SUBMITTING transition
    # ------------------------------------------------------------------

    async def claim_for_submit(
        self,
        client_order_id: str,
        *,
        now_iso: Optional[str] = None,
    ) -> bool:
        """Atomically transition a doc from state="INTENT" to state="SUBMITTING".

        Only one caller can claim a given cid — the update_one is conditional on
        state="INTENT", so a second concurrent claim will find matched_count==0
        and return False.

        Parameters
        ----------
        client_order_id: The cid to claim.
        now_iso:         Injected timestamp; defaults to UTC now.

        Returns
        -------
        True  — this caller holds the claim; proceed with the broker POST.
        False — another caller already claimed (or cid unknown / already submitted).
        """
        ts = now_iso or _utcnow_iso()
        result = await self._col.update_one(
            {"client_order_id": client_order_id, "state": "INTENT"},
            {"$set": {"state": "SUBMITTING", "ts_claim": ts}},
        )
        return result.matched_count == 1

    # ------------------------------------------------------------------
    # is_already_submitted — pre-POST guard
    # ------------------------------------------------------------------

    async def is_already_submitted(self, client_order_id: str) -> bool:
        """Return True iff a doc exists for this cid AND norenordno is set.

        A doc that exists but still has norenordno=None is in INTENT or
        SUBMITTING state — it has been recorded but not yet confirmed by
        the broker.
        """
        doc = await self._col.find_one(
            {"client_order_id": client_order_id},
            projection={"norenordno": 1},
        )
        if doc is None:
            return False
        return doc.get("norenordno") is not None

    # ------------------------------------------------------------------
    # mark_submitted — call AFTER a successful broker POST
    # ------------------------------------------------------------------

    async def mark_submitted(
        self,
        client_order_id: str,
        norenordno: str,
        *,
        now_iso: Optional[str] = None,
    ) -> None:
        """Record that the broker accepted the order and assigned norenordno.

        F2 — Rejects falsy norenordno:
          Raises ValueError if norenordno is None, empty, or whitespace-only,
          BEFORE touching the database.  This prevents a silent corruption where
          a failed POST (that returned None/empty) would stomp the stored state.

        F3 — Distinguishes not-found from already-submitted:
          On matched_count==0 (no doc with norenordno=None) the method queries
          the collection:
            • No doc at all → IntentNotFoundError
            • Doc exists with a norenordno → AlreadySubmittedError

        F4 — Idempotent same-norenordno retry:
          If matched_count==0 but the stored doc's norenordno matches the
          argument exactly, this is a safe duplicate call (e.g. a retry after
          a network timeout where the ACK was lost).  Returns silently.

        State-agnostic norenordno gate (F5):
          The conditional update matches on norenordno=None regardless of the
          state field, so it accepts both INTENT and SUBMITTING docs.  The
          claim_for_submit step is what gates concurrent submission; this method
          only cares that the slot is empty.

        Parameters
        ----------
        client_order_id: The cid of the intent to update.
        norenordno:      The broker order number returned by PlaceOrder.
        now_iso:         Injected timestamp; defaults to UTC now.

        Raises
        ------
        ValueError:           norenordno is falsy (None / empty / whitespace).
        IntentNotFoundError:  No doc exists for client_order_id at all.
        AlreadySubmittedError: The doc already has a DIFFERENT norenordno.
        """
        # F2 — reject falsy norenordno before any DB interaction
        if not norenordno or not str(norenordno).strip():
            raise ValueError(
                f"norenordno must be a non-empty string; got {norenordno!r}. "
                "Refusing to write a falsy broker order number — the POST "
                "likely failed or returned an empty/None order ID."
            )

        ts = now_iso or _utcnow_iso()

        # F5 — match on norenordno=None (state-agnostic); accepts INTENT or SUBMITTING
        result = await self._col.update_one(
            {"client_order_id": client_order_id, "norenordno": None},
            {
                "$set": {
                    "norenordno": norenordno,
                    "state": "SUBMITTED",
                    "ts_submitted": ts,
                }
            },
        )

        if result.matched_count == 1:
            return  # success

        # matched_count == 0 — no doc with norenordno=None matched.
        # Determine why: not-found vs already-submitted (F3) vs same retry (F4).
        existing = await self._col.find_one({"client_order_id": client_order_id})

        if existing is None:
            # F3: the cid was never recorded
            raise IntentNotFoundError(
                f"No intent doc found for client_order_id={client_order_id!r}. "
                "record_intent must be called before mark_submitted."
            )

        existing_nord = existing.get("norenordno")

        # F4: idempotent retry — same norenordno means the call already succeeded
        if existing_nord == norenordno:
            return  # no-op success

        # F3: doc exists but has a DIFFERENT norenordno → refuse to overwrite
        raise AlreadySubmittedError(
            f"client_order_id={client_order_id!r} already has "
            f"norenordno={existing_nord!r}. "
            f"Refusing to overwrite with {norenordno!r}."
        )

    # ------------------------------------------------------------------
    # resume_unsubmitted — startup reconciliation
    # ------------------------------------------------------------------

    async def resume_unsubmitted(self) -> List[dict]:
        """Return all docs with norenordno=None that have not been submitted.

        Includes both state="INTENT" (never claimed) and state="SUBMITTING"
        (claimed but crashed before mark_submitted completed).  Both categories
        represent orders whose broker delivery is uncertain and must be
        reconciled against the broker's live order book before re-sending.

        F5: SUBMITTING docs with norenordno=None are included because a crash
        between claim_for_submit and mark_submitted leaves the intent in an
        ambiguous state — the POST may or may not have reached the broker.
        The engine MUST check the broker's order book (matching on remarks==cid)
        before deciding to re-submit.
        """
        cursor = self._col.find({"norenordno": None})
        docs = await cursor.to_list(length=None)
        # Exclude any docs that have somehow been SUBMITTED (belt-and-suspenders):
        # a SUBMITTED doc should always have a norenordno, but filter defensively.
        return [d for d in docs if d.get("state") != "SUBMITTED"]


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _intent_to_dict(intent: OrderIntent) -> Dict[str, Any]:
    """Convert an OrderIntent to a plain dict for Mongo storage."""
    return {
        "client_order_id": intent.client_order_id,
        "trantype": intent.trantype,
        "prctyp": intent.prctyp,
        "exch": intent.exch,
        "tsym": intent.tsym,
        "qty": intent.qty,
        "prc": intent.prc,
        "prd": intent.prd,
        "ret": intent.ret,
        "trgprc": intent.trgprc,
        "remarks": intent.remarks,
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Production helper — NOT imported by the class; only routes use this
# ---------------------------------------------------------------------------

def default_store() -> "IntentStore":
    """Return an IntentStore backed by the production Mongo live_orders collection.

    Import is deferred to this function so that IntentStore itself never pulls
    in app.db (keeping it host-testable without a running Mongo).
    """
    from app.db import get_db  # type: ignore[import]

    return IntentStore(get_db().live_orders)
