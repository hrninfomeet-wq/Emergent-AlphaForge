"""Per-trade approval queue with one-shot tokens (P1.6).

Every live order is HELD pending explicit operator approval — "my approval is
necessary for taking the trade." Each pending approval carries:

- a **one-shot token** (an unguessable uuid4 returned ONLY at creation) so an
  approval can be redeemed EXACTLY once — a double-click, a network retry, or a
  replayed request can never place the order twice; and
- a **TTL** so a stale approval cannot fire against a stale price minutes later.

The store is pure, in-memory, and INJECTED-TIME (no wall-clock reads inside the
logic — callers pass ISO ``now`` strings), so it is fully deterministic under
test, mirroring auto_square / kill_switch.

Autonomy later: making the system autonomous = the route auto-approving on create
instead of waiting for the operator — the one-shot/TTL guarantees still hold.

State machine (a record only ever moves forward):
    pending ──approve(token)──► approved ──mark_consumed──► consumed
       │
       ├──reject──► rejected
       └──(age ≥ ttl)──► expired   (lazily, on any read/decision)

Only a ``pending`` record can be approved/rejected; only an ``approved`` record
can be consumed. Every other transition returns ok=False with a reason.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.live.idempotency import new_client_order_id

DEFAULT_TTL_SEC = 120

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
STATUS_CONSUMED = "consumed"

# Fields exposed to the UI (NEVER the token, except in the create() response).
_PUBLIC_FIELDS = (
    "approval_id", "status", "summary", "created_at", "decided_at", "reason",
)


def _to_utc(iso: str) -> datetime:
    """Parse an ISO 8601 string to a UTC-aware datetime (naive assumed UTC)."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class ApprovalStore:
    """In-memory, injected-time approval queue. One instance per process (the
    route layer owns the singleton)."""

    def __init__(
        self,
        *,
        ttl_sec: int = DEFAULT_TTL_SEC,
        id_factory: Callable[[], str] = new_client_order_id,
    ) -> None:
        self._q: Dict[str, Dict[str, Any]] = {}
        self._ttl = int(ttl_sec)
        self._id = id_factory

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def create(self, *, payload: Any, summary: Dict[str, Any], now_iso: str) -> Dict[str, Any]:
        """Queue a new pending approval. Returns the public record PLUS the
        one-shot token (the ONLY time the token is ever exposed).

        ``payload`` is whatever the executor needs to place the order — for the
        live order page this is the list of validated child jdata dicts from the
        choke-point. ``summary`` is a human-readable description for the UI.
        """
        approval_id = self._id()
        token = self._id()
        rec = {
            "approval_id": approval_id,
            "token": token,
            "status": STATUS_PENDING,
            "summary": dict(summary or {}),
            "payload": payload,
            "created_at": now_iso,
            "decided_at": None,
            "reason": None,
        }
        self._q[approval_id] = rec
        out = self._public(rec)
        out["token"] = token  # surfaced once, here only
        return out

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get(self, approval_id: str, *, now_iso: Optional[str] = None) -> Optional[Dict[str, Any]]:
        rec = self._q.get(approval_id)
        if rec is None:
            return None
        if now_iso is not None:
            self._refresh(rec, now_iso)
        return self._public(rec)

    def list_pending(self, now_iso: str) -> List[Dict[str, Any]]:
        """Return all currently-pending approvals (lazily expiring stale ones)."""
        out: List[Dict[str, Any]] = []
        for rec in self._q.values():
            self._refresh(rec, now_iso)
            if rec["status"] == STATUS_PENDING:
                out.append(self._public(rec))
        return out

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------
    def approve(self, approval_id: str, token: str, now_iso: str) -> Dict[str, Any]:
        """Redeem the one-shot token. On success transitions pending→approved,
        SPENDS the token, and returns the payload for execution — exactly once.

        A wrong token leaves the record pending (so the legit operator can still
        approve). An expired/already-decided record cannot be approved.
        """
        rec = self._q.get(approval_id)
        if rec is None:
            return {"ok": False, "reason": "not_found"}
        self._refresh(rec, now_iso)
        if rec["status"] != STATUS_PENDING:
            return {"ok": False, "reason": f"not pending ({rec['status']})"}
        # Constant-ish comparison is unnecessary here (uuid4 token, local single
        # operator); a wrong token must NOT consume or change state.
        if not token or token != rec["token"]:
            return {"ok": False, "reason": "bad_token"}
        rec["status"] = STATUS_APPROVED
        rec["decided_at"] = now_iso
        # One-shot is enforced by the STATUS gate (a replay sees status != pending),
        # NOT by destroying the token — so revert_to_pending() can return a stranded
        # approval to the queue with its original token still valid for one retry.
        return {"ok": True, "approval_id": approval_id, "payload": rec["payload"]}

    def revert_to_pending(self, approval_id: str, now_iso: str) -> Dict[str, Any]:
        """Return an APPROVED-but-not-placed approval to pending so it stays in the
        queue and can be retried (or rejected) with its original token.

        Called by the route when a redeemed approval is NOT actually placed (mode
        not armed, BUY-only, broker reject, …). Without this the record would be
        stuck 'approved' — un-placeable (approve() needs pending) AND un-rejectable
        (reject() needs pending) — and would silently vanish from list_pending.
        Only an 'approved' record can be reverted; a 'consumed' (actually-placed)
        one cannot (so a placed order can never be re-placed).
        """
        rec = self._q.get(approval_id)
        if rec is None:
            return {"ok": False, "reason": "not_found"}
        if rec["status"] != STATUS_APPROVED:
            return {"ok": False, "reason": f"not approved ({rec['status']})"}
        rec["status"] = STATUS_PENDING
        rec["decided_at"] = None
        rec["reason"] = None
        return {"ok": True, "approval_id": approval_id}

    def mark_consumed(self, approval_id: str, now_iso: str) -> Dict[str, Any]:
        """Record that an APPROVED order was actually placed. Only an approved
        record can be consumed; this makes a second execution attempt impossible
        to mistake for a fresh one."""
        rec = self._q.get(approval_id)
        if rec is None:
            return {"ok": False, "reason": "not_found"}
        if rec["status"] != STATUS_APPROVED:
            return {"ok": False, "reason": f"not approved ({rec['status']})"}
        rec["status"] = STATUS_CONSUMED
        rec["decided_at"] = now_iso
        return {"ok": True, "approval_id": approval_id}

    def reject(self, approval_id: str, now_iso: str, *, reason: str = "operator_rejected") -> Dict[str, Any]:
        """Operator declines a pending approval. Only a pending record can be
        rejected (an approved/consumed order is already past the gate)."""
        rec = self._q.get(approval_id)
        if rec is None:
            return {"ok": False, "reason": "not_found"}
        self._refresh(rec, now_iso)
        if rec["status"] != STATUS_PENDING:
            return {"ok": False, "reason": f"not pending ({rec['status']})"}
        rec["status"] = STATUS_REJECTED
        rec["decided_at"] = now_iso
        rec["reason"] = reason
        return {"ok": True, "approval_id": approval_id}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _refresh(self, rec: Dict[str, Any], now_iso: str) -> None:
        """Lazily flip a stale pending record to expired."""
        if rec["status"] != STATUS_PENDING:
            return
        try:
            age = (_to_utc(now_iso) - _to_utc(rec["created_at"])).total_seconds()
        except Exception:
            return  # unparseable time → leave pending; an explicit decision still works
        if age >= self._ttl:
            rec["status"] = STATUS_EXPIRED
            rec["decided_at"] = now_iso
            rec["reason"] = "expired"
            rec["token"] = None  # an expired approval's token is dead

    def _public(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        d = {k: rec.get(k) for k in _PUBLIC_FIELDS}
        d["summary"] = dict(rec.get("summary") or {})
        d["payload"] = rec.get("payload")
        return d
