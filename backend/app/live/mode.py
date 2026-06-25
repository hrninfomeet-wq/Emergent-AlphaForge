"""Mode gate (L3.1) — master safety switch for real orders.

Design contract
---------------
Real (non-mock) orders are reachable ONLY when:
  1.  The current mode is "LIVE_TEST", AND
  2.  single_shot_consumed is False.

Any other combination — PAPER, LIVE_OFFLINE, LIVE_ARMED, None, malformed doc,
absent doc — resolves to False from `is_live_order_allowed`.

Modes (in deployment order):
  PAPER        — paper-trade only; no broker connection needed
  LIVE_OFFLINE — broker connected but orders suppressed (dry-run with real feed)
  LIVE_TEST    — single real order allowed, then self-locks (single_shot_consumed)
  LIVE_ARMED   — full live trading (L4, rejected in L3)

`ModeStore` is DB-agnostic: the constructor takes any collection object that
exposes the same async interface used by IntentStore (see idempotency.py).
Tests pass a FakeAsyncCollection; production code uses `default_store()`.

The module-level `default_store()` helper wires production code to Mongo
without importing the DB anywhere else in this file.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODES = ("PAPER", "LIVE_OFFLINE", "LIVE_TEST", "LIVE_ARMED")
DEFAULT_MODE = "PAPER"

# LIVE_ARMED is an L4 concept — explicitly rejected in the L3 gate.
_L3_ALLOWED_TARGETS = ("PAPER", "LIVE_OFFLINE", "LIVE_TEST")

_SINGLETON_ID = "singleton"

_DEFAULT_DOC: Dict[str, Any] = {
    "mode": DEFAULT_MODE,
    "single_shot_consumed": False,
    "test_session_id": None,
}


# ---------------------------------------------------------------------------
# Pure predicate — no I/O
# ---------------------------------------------------------------------------

def is_live_order_allowed(mode_doc: Optional[Dict[str, Any]]) -> bool:
    """Return True ONLY in LIVE_TEST with an unconsumed single-shot.

    Fail-safe: None / non-dict / missing keys / unknown mode / consumed → False.
    This function must never raise; every unexpected shape must produce False.

    F3 — explicit-False check: ``single_shot_consumed`` must be the literal
    boolean False.  A missing key (returns None), 0, empty string, or any other
    falsy non-False value all produce False so a partial/tampered doc fails closed.
    """
    if not isinstance(mode_doc, dict):
        return False
    return (
        mode_doc.get("mode") == "LIVE_TEST"
        and mode_doc.get("single_shot_consumed") is False
    )


# ---------------------------------------------------------------------------
# ModeStore
# ---------------------------------------------------------------------------

class ModeStore:
    """Async mode-state store backed by an injectable collection.

    Never imports app.db — the caller passes the collection at construction
    time.  Production code uses `default_store()`; tests pass a
    FakeAsyncCollection.

    The store holds exactly ONE document identified by `{_id: "singleton"}`.
    """

    def __init__(self, collection: Any) -> None:
        self._c = collection

    # ------------------------------------------------------------------
    # get — read the singleton, defaulting when absent
    # ------------------------------------------------------------------

    async def get(self) -> Dict[str, Any]:
        """Return the singleton doc.

        When the collection is empty (first boot) returns a default doc with
        mode=PAPER, single_shot_consumed=False, test_session_id=None without
        writing anything.  The returned dict always contains at least the keys
        ``mode`` and ``single_shot_consumed``.
        """
        doc = await self._c.find_one({"_id": _SINGLETON_ID})
        if doc is None:
            return dict(_DEFAULT_DOC)
        # Ensure mandatory keys are present even on a partial/legacy doc.
        result = dict(_DEFAULT_DOC)
        result.update(doc)
        return result

    # ------------------------------------------------------------------
    # set_mode — guarded mode transition
    # ------------------------------------------------------------------

    async def set_mode(
        self,
        target: str,
        *,
        confirm: bool = False,
        can_trade: bool = True,
        connected: bool = True,
        now_iso: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transition the stored mode to *target*.

        Guards
        ------
        - ``target not in _L3_ALLOWED_TARGETS`` → ValueError (LIVE_ARMED rejected)
        - Entering LIVE_TEST requires ALL of:
              ``confirm is True`` AND ``connected`` AND ``can_trade``
          Any missing precondition → ValueError with a descriptive message.

        Side effects
        ------------
        - Writes ``{mode, since, single_shot_consumed: False}`` via upsert on
          ``{_id: "singleton"}``.
        - Returns the new doc (as would be returned by ``get()``).

        Parameters
        ----------
        target:    One of PAPER / LIVE_OFFLINE / LIVE_TEST.
        confirm:   Caller must explicitly pass True to enter LIVE_TEST.
        can_trade: Engine's can_trade flag (from broker health check).
        connected: Whether the broker websocket is currently connected.
        now_iso:   Injected ISO-8601 timestamp; defaults to UTC now.
        """
        if target not in _L3_ALLOWED_TARGETS:
            raise ValueError(
                f"mode {target!r} is not allowed in L3. "
                f"Allowed targets: {_L3_ALLOWED_TARGETS}. "
                "LIVE_ARMED is an L4 feature — upgrade the gate first."
            )

        if target == "LIVE_TEST":
            # F1 — strict confirm: only the literal boolean True is accepted.
            # Truthy non-True values (1, "yes", [1], …) are rejected so that
            # callers cannot accidentally coerce a non-boolean into the gate.
            if confirm is not True:
                raise ValueError(
                    "Entering LIVE_TEST requires confirm=True (literal boolean). "
                    "Pass confirm=True to acknowledge this is a real-order session."
                )
            # F2 — strict connected / can_trade: same rationale as F1.
            if connected is not True:
                raise ValueError(
                    "Entering LIVE_TEST requires connected=True (literal boolean). "
                    "A truthy non-True value is not accepted (connected=False or non-bool)."
                )
            if can_trade is not True:
                raise ValueError(
                    "Entering LIVE_TEST requires can_trade=True (literal boolean). "
                    "The broker engine is currently halted or in a non-trading state."
                )

        ts = now_iso or _utcnow_iso()

        new_doc: Dict[str, Any] = {
            "mode": target,
            "since": ts,
            "single_shot_consumed": False,
            "test_session_id": None,
        }

        await self._c.update_one(
            {"_id": _SINGLETON_ID},
            {"$set": new_doc},
            upsert=True,
        )
        result = dict(_DEFAULT_DOC)
        result.update(new_doc)
        result["_id"] = _SINGLETON_ID
        return result

    # ------------------------------------------------------------------
    # consume_single_shot — lock the single-shot after a real order
    # ------------------------------------------------------------------

    async def consume_single_shot(self) -> None:
        """Mark single_shot_consumed=True, preventing further real orders.

        Idempotent: calling it a second time is a no-op (it's already True).
        """
        await self._c.update_one(
            {"_id": _SINGLETON_ID},
            {"$set": {"single_shot_consumed": True}},
            upsert=False,
        )

    # ------------------------------------------------------------------
    # revert_to_offline — safe rollback after a LIVE_TEST session
    # ------------------------------------------------------------------

    async def revert_to_offline(
        self,
        *,
        now_iso: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Revert to LIVE_OFFLINE and clear the single-shot flag.

        Clears: mode → LIVE_OFFLINE, single_shot_consumed → False,
                test_session_id → None.

        Returns the updated doc (as would be returned by ``get()``).
        """
        ts = now_iso or _utcnow_iso()

        new_doc: Dict[str, Any] = {
            "mode": "LIVE_OFFLINE",
            "since": ts,
            "single_shot_consumed": False,
            "test_session_id": None,
        }

        await self._c.update_one(
            {"_id": _SINGLETON_ID},
            {"$set": new_doc},
            upsert=True,
        )
        result = dict(_DEFAULT_DOC)
        result.update(new_doc)
        result["_id"] = _SINGLETON_ID
        return result


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Per-deployment live-arm predicate (L4 / strategy-deploy-to-live)
# ---------------------------------------------------------------------------

def armed_until_today_ist(now_utc: "datetime") -> str:
    """ISO-UTC timestamp for 15:00 IST on now_utc's IST date (the EOD square cutoff)."""
    from datetime import timezone, timedelta
    ist = now_utc.astimezone(timezone.utc) + timedelta(hours=5, minutes=30)
    cutoff_ist = ist.replace(hour=15, minute=0, second=0, microsecond=0)
    return (cutoff_ist - timedelta(hours=5, minutes=30)).replace(tzinfo=timezone.utc).isoformat()


def is_deployment_live_allowed(deployment: "Dict[str, Any]", now_utc: "datetime", *, connected: bool) -> "Tuple[bool, str]":
    """(ok, reason) — True iff risk.live armed, now_utc < armed_until, and connected.
    Fail-closed: any missing/malformed field or expired arm -> (False, reason)."""
    from datetime import datetime, timezone
    if not isinstance(deployment, dict):
        return False, "no_deployment"
    risk = deployment.get("risk")
    live = risk.get("live") if isinstance(risk, dict) else None
    if not isinstance(live, dict):
        return False, "not_armed"
    if live.get("armed") is not True:
        return False, "not_armed"
    raw = live.get("armed_until")
    if not raw:
        return False, "arm_expired"
    try:
        until = datetime.fromisoformat(str(raw))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False, "arm_expired"
    if now_utc >= until:
        return False, "arm_expired"
    if connected is not True:
        return False, "not_connected"
    return True, "ok"


# ---------------------------------------------------------------------------
# Production helper — NOT imported by the class; only routes use this
# ---------------------------------------------------------------------------

def default_store() -> "ModeStore":
    """Return a ModeStore backed by the production Mongo live_mode collection.

    Import is deferred to this function so that ModeStore itself never pulls
    in app.db (keeping it host-testable without a running Mongo).
    """
    from app.db import get_db  # type: ignore[import]

    return ModeStore(get_db().live_mode)
