"""Idempotency + restart-survivable order-intent store (L1.2).

Design contract
---------------
The INTENT STORE is the single source of truth for whether a client_order_id
has been (or is about to be) submitted to the broker.

Dedup rule (callers MUST honour):
  1. Call `new_client_order_id()` to mint a fresh cid.
  2. Call `record_intent(intent, ...)` BEFORE any broker POST — this persists
     the intent with state="INTENT" and norenordno=None.
  3. Call `is_already_submitted(cid)` and REFUSE to POST if it returns True.
  4. On a successful broker POST call `mark_submitted(cid, norenordno)`.
  5. On startup / reconnect call `resume_unsubmitted()` and reconcile those
     intents against the broker before deciding to re-send.

The class is DB-AGNOSTIC: the constructor takes any "collection" object that
exposes the async interface below (motor's AsyncIOMotorCollection satisfies it;
tests pass a FakeAsyncCollection).

Collection async interface expected:
  find_one(query, projection=None) -> dict | None
  insert_one(doc) -> Any
  update_one(query, update, upsert=False) -> UpdateResult-like (has .matched_count)
  find(query, projection=None) -> async-iterable with .to_list(length)

The module-level `default_store()` helper wires production code to Mongo
without importing the DB anywhere else in this file.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

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
# Intent store
# ---------------------------------------------------------------------------

class AlreadySubmittedError(RuntimeError):
    """Raised by mark_submitted when the doc already has a norenordno."""


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

        # Idempotent-insert: only write if the cid doesn't exist yet.
        existing = await self._col.find_one(
            {"client_order_id": intent.client_order_id}
        )
        if existing is not None:
            return existing

        doc: Dict[str, Any] = {
            "client_order_id": intent.client_order_id,
            "deployment_id": deployment_id,
            "mode": mode,
            "intent": _intent_to_dict(intent),
            "state": "INTENT",
            "norenordno": None,
            "ts_intent": ts,
        }
        await self._col.insert_one(doc)
        return doc

    # ------------------------------------------------------------------
    # is_already_submitted — pre-POST guard
    # ------------------------------------------------------------------

    async def is_already_submitted(self, client_order_id: str) -> bool:
        """Return True iff a doc exists for this cid AND norenordno is set.

        A doc that exists but still has norenordno=None is in INTENT state —
        it has been recorded but not yet sent to the broker.
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

        Conditional update: only applies to a doc where norenordno IS None.
        If the doc already has a norenordno (i.e. a concurrent/duplicate submit
        already set it) this raises AlreadySubmittedError — the caller must NOT
        overwrite a broker order number with a different one.

        Parameters
        ----------
        client_order_id: The cid of the intent to update.
        norenordno:      The broker order number returned by PlaceOrder.
        now_iso:         Injected timestamp; defaults to UTC now.

        Raises
        ------
        AlreadySubmittedError: if the doc already has a norenordno (matched 0).
        """
        ts = now_iso or _utcnow_iso()

        result = await self._col.update_one(
            # Only match a doc that has NOT been submitted yet
            {"client_order_id": client_order_id, "norenordno": None},
            {
                "$set": {
                    "norenordno": norenordno,
                    "state": "SUBMITTED",
                    "ts_submitted": ts,
                }
            },
        )
        if result.matched_count == 0:
            raise AlreadySubmittedError(
                f"client_order_id={client_order_id!r} already has a norenordno "
                f"(or does not exist). Refusing to overwrite."
            )

    # ------------------------------------------------------------------
    # resume_unsubmitted — startup reconciliation
    # ------------------------------------------------------------------

    async def resume_unsubmitted(self) -> List[dict]:
        """Return all docs still in state="INTENT" with norenordno=None.

        These are intents that were recorded before a crash/timeout but whose
        broker POST never completed (or whose result was never observed).  On
        startup the engine reconciles these against the broker's order book
        before deciding whether to re-send.
        """
        cursor = self._col.find({"state": "INTENT", "norenordno": None})
        return await cursor.to_list(length=None)


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
