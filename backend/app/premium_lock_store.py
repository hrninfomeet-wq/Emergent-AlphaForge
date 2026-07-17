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
    deployments) — the subscription-pin source.

    Phase 5B: also scans the lazy legs (``lce``/``lpe``, flat
    ``{leg}_instrument_key`` naming — they have no nested contract sub-doc
    like ``ce``/``pe`` do) so a mid-session lazy pin actually subscribes."""
    cur = col.find({"session_date": str(session_date)}, {"_id": 0})
    keys: List[str] = []
    for doc in await cur.to_list(length=None):
        for s in ("ce", "pe"):
            k = ((doc.get(s) or {}).get("instrument_key")) if isinstance(doc.get(s), dict) else None
            if k and k not in keys:
                keys.append(str(k))
        for s in ("lce", "lpe"):
            k = doc.get(f"{s}_instrument_key")
            if k and k not in keys:
                keys.append(str(k))
    return keys


# ---------------------------------------------------------------------------
# Phase 5B — per-leg primitives (ADDITIVE ONLY; nothing above this line was
# touched — see tests/test_premium_lock_store_multileg.py's string-pin test).
#
# Legs: "pce"/"ppe" are the PRIMARY CE/PE legs. They alias the EXISTING
# ce/pe storage (the nested {"ce": {...contract...}} sub-doc written by
# premium_momentum_live.py + the flat ce_ref_premium/ce_ref_ts fields written
# by capture_ref above) — no duplicate contract/ref storage. "lce"/"lpe" are
# the LAZY reversal legs; they have no pre-existing storage, so they get pure
# flat naming (lce_instrument_key, lce_ref_premium, ...). All FOUR legs share
# new flat trigger/entry/exit fields that did not exist before this section:
# {prefix}_triggered, {prefix}_entered_norenordno, {prefix}_entry_premium,
# {prefix}_exited — additive, and distinct from the whole-doc
# triggered_side/entered_norenordno fields that first_to_trigger mode keeps
# using untouched.
# ---------------------------------------------------------------------------

_LEG_PREFIX = {"pce": "ce", "ppe": "pe", "lce": "lce", "lpe": "lpe"}


def _leg_prefix(leg: str) -> str:
    key = str(leg).lower()
    if key not in _LEG_PREFIX:
        raise ValueError(f"unknown leg {leg!r}; expected one of {sorted(_LEG_PREFIX)}")
    return _LEG_PREFIX[key]


async def latch_trigger_leg(col: Any, *, deployment_id: str, session_date: str,
                           leg: str) -> bool:
    """Atomically latch ONE leg's trigger. Filtered on that leg's trigger
    field being genuinely ABSENT (not merely None) so a released latch
    (unlatch_trigger_leg uses $unset) can re-fire — and on done_for_day being
    False. Never touches any other leg's fields."""
    prefix = _leg_prefix(leg)
    res = await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         f"{prefix}_triggered": {"$exists": False}, "done_for_day": False},
        {"$set": {f"{prefix}_triggered": True, f"{prefix}_triggered_at": _now_iso()}},
    )
    return int(getattr(res, "matched_count", 0) or 0) == 1


async def unlatch_trigger_leg(col: Any, *, deployment_id: str, session_date: str,
                              leg: str) -> None:
    """Release ONE leg's latch after a journaled entry FAILURE. Filtered on
    that leg never having a completed entry — never releases a completed
    entry, never touches other legs. Uses $unset (not $set-to-None) so the
    field is genuinely absent again for latch_trigger_leg's filter."""
    prefix = _leg_prefix(leg)
    await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         f"{prefix}_entered_norenordno": None},
        {"$unset": {f"{prefix}_triggered": ""}},
    )


async def mark_entered_leg(col: Any, *, deployment_id: str, session_date: str,
                          leg: str, norenordno: str,
                          entry_premium: Optional[float]) -> None:
    prefix = _leg_prefix(leg)
    await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date)},
        {"$set": {f"{prefix}_entered_norenordno": str(norenordno),
                  f"{prefix}_entry_premium": (float(entry_premium) if entry_premium is not None else None),
                  f"{prefix}_entered_at": _now_iso()}},
    )


async def mark_leg_exited(col: Any, *, deployment_id: str, session_date: str,
                         leg: str) -> None:
    prefix = _leg_prefix(leg)
    await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date)},
        {"$set": {f"{prefix}_exited": True, f"{prefix}_exited_at": _now_iso()}},
    )


async def set_lazy_armed(col: Any, *, deployment_id: str, session_date: str,
                        side: str, parent_reason: str) -> bool:
    """Idempotent one-shot: arm the lazy leg for ``side`` ("ce"/"pe"). Filtered
    on the flag being absent so a second STOP on the same side (shouldn't
    happen — one shot per side per session) never re-arms."""
    s = str(side).lower()
    res = await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         f"lazy_armed_{s}": {"$exists": False}},
        {"$set": {f"lazy_armed_{s}": True,
                  f"lazy_armed_{s}_reason": str(parent_reason),
                  f"lazy_armed_{s}_at": _now_iso()}},
    )
    return int(getattr(res, "matched_count", 0) or 0) == 1


async def capture_ref_leg(col: Any, *, deployment_id: str, session_date: str,
                         leg: str, ref_premium: float, ref_ts: int) -> bool:
    """Persist ONE leg's reference premium ONCE (filtered on the field being
    absent). Generalizes capture_ref by leg identity rather than side — used
    by the lazy legs (lce/lpe) which have no pre-existing ref storage."""
    prefix = _leg_prefix(leg)
    res = await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         f"{prefix}_ref_premium": {"$exists": False}},
        {"$set": {f"{prefix}_ref_premium": float(ref_premium),
                  f"{prefix}_ref_ts": int(ref_ts),
                  f"{prefix}_ref_captured_at": _now_iso()}},
    )
    return int(getattr(res, "matched_count", 0) or 0) == 1


async def mark_day_stop(col: Any, *, deployment_id: str, session_date: str, reason: str) -> bool:
    """Idempotent one-shot: record that this session's realized-only day-stop
    gate (Phase 5B Task A4, ``deployment_evaluator.py``) breached. Filtered on
    the flag being absent so a breach re-detected on a later bar (already
    recorded) never re-marks -- the evaluator uses this same True/False to
    fire its one-time-per-session deployment square exactly once."""
    res = await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         "day_stop_reason": {"$exists": False}},
        {"$set": {"day_stop_reason": str(reason), "day_stop_at": _now_iso()}},
    )
    return int(getattr(res, "matched_count", 0) or 0) == 1


def legs_unresolved(lock: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
    """PURE helper (no I/O): which legs are armed (triggered) or entered but
    not yet exited on this lock doc — drives the whole-doc done decision in
    leg_mode="both"/lazy sessions. A leg that never triggered/entered isn't
    "unresolved", it's simply not in play. lce/lpe are only ever considered
    when params["lazy_enabled"] is truthy (they cannot become active
    otherwise); pce/ppe are always considered since both primaries are
    locked at session start regardless of mode."""
    lazy_enabled = bool((params or {}).get("lazy_enabled", False))
    candidate_legs = ["pce", "ppe"] + (["lce", "lpe"] if lazy_enabled else [])
    unresolved: List[str] = []
    for leg in candidate_legs:
        prefix = _LEG_PREFIX[leg]
        triggered = lock.get(f"{prefix}_triggered")
        entered = lock.get(f"{prefix}_entered_norenordno")
        exited = lock.get(f"{prefix}_exited")
        active = bool(triggered) or bool(entered)
        if active and not exited:
            unresolved.append(leg)
    return unresolved
