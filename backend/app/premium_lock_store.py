# backend/app/premium_lock_store.py
"""Per-(deployment, session) state for premium-momentum execution (Track B).

One doc per deployment per IST session, unique on (deployment_id, session_date)
— create-once crash-safety via duplicate-key ADOPT (a racer reads the winner's
doc; the same pattern as the signals dedupe index). The doc is simultaneously:
the strike lock (never re-resolve from drifting spot), the ref-premium record,
the first-to-trigger latch, the subscription-pin source, and the recovery source.

Side fields are FLAT (ce_ref_premium, not ce.ref_premium) so filtered atomic
updates stay top-level-equality only. The store takes ANY async collection.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_or_create_lock(col: Any, *, deployment_id: str, session_date: str,
                             payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create the session lock once; a concurrent/second caller ADOPTS the
    existing doc (its payload is discarded). Never overwrites."""
    doc = {
        "deployment_id": str(deployment_id),
        "session_date": str(session_date),
        "locked_at": _now_iso(),
        "triggered_side": None,
        "entered_norenordno": None,
        "entry_premium": None,
        "done_for_day": False,
        "done_reason": None,
        **(payload or {}),
    }
    try:
        await col.insert_one(doc)
        doc.pop("_id", None)
        return doc
    except Exception as exc:  # duplicate key → adopt the existing winner
        if "duplicate" not in str(exc).lower() and "e11000" not in str(exc).lower():
            raise
        existing = await col.find_one(
            {"deployment_id": str(deployment_id), "session_date": str(session_date)},
            {"_id": 0})
        return existing or doc


async def get_lock(col: Any, *, deployment_id: str, session_date: str) -> Optional[Dict[str, Any]]:
    return await col.find_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date)}, {"_id": 0})


async def capture_ref(col: Any, *, deployment_id: str, session_date: str,
                      side: str, ref_premium: float, ref_ts: int) -> bool:
    """Persist one side's reference premium ONCE (filtered on the field being
    absent — a second capture is a no-op, the first tick wins)."""
    s = str(side).lower()
    res = await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         f"{s}_ref_premium": {"$exists": False}},
        {"$set": {f"{s}_ref_premium": float(ref_premium),
                  f"{s}_ref_ts": int(ref_ts),
                  f"{s}_ref_captured_at": _now_iso()}},
    )
    return int(getattr(res, "matched_count", 0) or 0) == 1


async def latch_trigger(col: Any, *, deployment_id: str, session_date: str, side: str) -> bool:
    """Atomically latch the first side to trigger. Filter requires the latch to
    still be None — Mongo's single-doc update makes first-wins race-safe."""
    res = await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         "triggered_side": None, "done_for_day": False},
        {"$set": {"triggered_side": str(side).upper(), "triggered_at": _now_iso()}},
    )
    return int(getattr(res, "matched_count", 0) or 0) == 1


async def unlatch_trigger(col: Any, *, deployment_id: str, session_date: str) -> None:
    """Release the latch after a journaled entry FAILURE (refusal/error) so a
    later bar may re-trigger. Mirrors release_live_trade_claim's philosophy."""
    await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         "entered_norenordno": None},
        {"$set": {"triggered_side": None}},
    )


async def mark_entered(col: Any, *, deployment_id: str, session_date: str,
                       norenordno: str, entry_premium: Optional[float]) -> None:
    await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date)},
        {"$set": {"entered_norenordno": str(norenordno),
                  "entry_premium": (float(entry_premium) if entry_premium is not None else None),
                  "entered_at": _now_iso()}},
    )


async def mark_done(col: Any, *, deployment_id: str, session_date: str, reason: str) -> None:
    await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date)},
        {"$set": {"done_for_day": True, "done_reason": str(reason), "done_at": _now_iso()}},
    )


async def today_locked_keys(col: Any, *, session_date: str) -> List[str]:
    """Distinct instrument keys locked for THIS session (both sides, all
    deployments) — the subscription-pin source."""
    cur = col.find({"session_date": str(session_date)}, {"_id": 0})
    keys: List[str] = []
    for doc in await cur.to_list(length=None):
        for s in ("ce", "pe"):
            k = ((doc.get(s) or {}).get("instrument_key")) if isinstance(doc.get(s), dict) else None
            if k and k not in keys:
                keys.append(str(k))
    return keys
