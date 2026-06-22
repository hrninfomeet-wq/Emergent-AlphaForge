"""Test-session store for L3 live-test order sessions (L3.6).

Holds the armed state for the ONE active live-test position:
  - entry_norenordno
  - sl_norenordno          (protective SL backstop; None if not placed)
  - deadline               (ISO UTC string — fill_time + 10 min)
  - status                 "armed" | "squared" | "kill_switch"
  - heartbeat_ts           last GET /test-session access (ISO UTC)

There is at most ONE session document at a time (singleton).  A new arm()
call overwrites the previous session (the old position must be closed first —
this is not enforced here; the executor's mode gate guarantees it via
consume_single_shot).

DB-agnostic: constructor takes any async collection that exposes find_one /
update_one (upsert=True) / find.  Tests pass FakeAsyncCollection; production
code uses ``default_store()``.

remaining_secs(now_iso) helper computes seconds until deadline from an injected
now — no wall-clock inside.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

_SINGLETON_ID = "session_singleton"

_EMPTY: Dict[str, Any] = {
    "entry_norenordno": None,
    "sl_norenordno": None,
    "deadline": None,
    "status": "none",
    "heartbeat_ts": None,
    "reject_reason": None,
}


def _remaining(deadline_iso: Optional[str], now_iso: str) -> Optional[float]:
    """Return seconds until ``deadline_iso`` from ``now_iso``, or None on parse error."""
    if not deadline_iso:
        return None
    try:
        def _to_utc(s: str) -> datetime:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt

        return max(0.0, (_to_utc(deadline_iso) - _to_utc(now_iso)).total_seconds())
    except Exception:
        return None


class SessionStore:
    """Singleton session store for the active live-test position."""

    def __init__(self, collection: Any) -> None:
        self._c = collection

    async def get(self) -> Dict[str, Any]:
        """Return the current session doc; returns _EMPTY if none exists."""
        doc = await self._c.find_one({"_id": _SINGLETON_ID})
        if doc is None:
            return dict(_EMPTY)
        result = dict(_EMPTY)
        result.update(doc)
        return result

    async def arm(
        self,
        *,
        entry_norenordno: str,
        deadline: str,
        sl_norenordno: Optional[str] = None,
        now_iso: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write the armed session doc (overwrites any previous session)."""
        ts = now_iso or _utcnow_iso()
        doc = {
            "entry_norenordno": entry_norenordno,
            "sl_norenordno": sl_norenordno,
            "deadline": deadline,
            "status": "armed",
            "heartbeat_ts": ts,
        }
        await self._c.update_one(
            {"_id": _SINGLETON_ID},
            {"$set": doc},
            upsert=True,
        )
        return dict(doc)

    async def update_status(self, status: str, reject_reason: Optional[str] = None) -> None:
        """Update the session status (e.g. 'squared', 'kill_switch', 'rejected').

        Parameters
        ----------
        status : str
            New status string.
        reject_reason : str or None
            Optional broker reject reason string, stored when status='rejected'.
        """
        fields: Dict[str, Any] = {"status": status}
        if reject_reason is not None:
            fields["reject_reason"] = reject_reason
        await self._c.update_one(
            {"_id": _SINGLETON_ID},
            {"$set": fields},
            upsert=False,
        )

    async def bump_heartbeat(self, now_iso: Optional[str] = None) -> None:
        """Update the heartbeat timestamp to now."""
        ts = now_iso or _utcnow_iso()
        await self._c.update_one(
            {"_id": _SINGLETON_ID},
            {"$set": {"heartbeat_ts": ts}},
            upsert=False,
        )

    async def clear(self) -> None:
        """Reset to the empty state (call after a successful square)."""
        await self._c.update_one(
            {"_id": _SINGLETON_ID},
            {"$set": dict(_EMPTY)},
            upsert=True,
        )

    def remaining_secs(self, deadline_iso: Optional[str], now_iso: str) -> Optional[float]:
        """Pure helper — seconds until deadline from now_iso (no wall-clock)."""
        return _remaining(deadline_iso, now_iso)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_store() -> "SessionStore":
    """Return a SessionStore backed by the production Mongo collection."""
    from app.db import get_db  # type: ignore[import]

    return SessionStore(get_db().live_test_sessions)
