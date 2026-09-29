"""Auditable live-signal lifecycle helpers."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

log = logging.getLogger(__name__)


class SignalStateError(ValueError):
    pass


ALLOWED_TRANSITIONS = {
    "WATCHING": {"FORMING", "AUDITED"},
    "FORMING": {"CONFIRMED", "AUDITED"},
    "CONFIRMED": {"TRIGGERED", "AUDITED"},
    "TRIGGERED": {"ACTIVE", "SKIPPED", "AUDITED"},
    "ACTIVE": {"EXITED", "AUDITED"},
    "EXITED": {"AUDITED"},
    "SKIPPED": {"AUDITED"},
    "AUDITED": set(),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _event(from_state: Optional[str], to_state: str, reason: str = "", at: Optional[str] = None, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "from_state": from_state,
        "to_state": to_state,
        "reason": reason,
        "at": at or _now_iso(),
        "snapshot": snapshot or {},
    }


def create_signal_doc(
    *,
    instrument: str,
    direction: str,
    strategy_id: str,
    entry_price: Any,
    confidence: Any,
    reasons: Optional[Iterable[str]] = None,
    option_contract: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    state = "WATCHING"
    at = created_at or _now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "instrument": str(instrument or "").upper(),
        "direction": str(direction or "").upper(),
        "strategy_id": str(strategy_id or ""),
        "entry_price": _float_or_none(entry_price),
        "confidence": _float_or_none(confidence),
        "reasons": [str(reason) for reason in (reasons or [])],
        "option_contract": option_contract or {},
        "context": context or {},
        "state": state,
        "created_at": at,
        "updated_at": at,
        "events": [_event(None, state, reason="created", at=at)],
    }
    return doc


def transition_signal(
    signal: Dict[str, Any],
    to_state: str,
    *,
    reason: str = "",
    at: Optional[str] = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    current = str(signal.get("state") or "WATCHING").upper()
    target = str(to_state or "").upper()
    if target not in ALLOWED_TRANSITIONS:
        raise SignalStateError(f"Unknown signal state: {target}")
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise SignalStateError(f"Invalid signal transition: {current} -> {target}")

    updated = dict(signal)
    events = list(signal.get("events") or [])
    timestamp = at or _now_iso()
    events.append(_event(current, target, reason=reason, at=timestamp, snapshot=snapshot))
    updated["state"] = target
    updated["updated_at"] = timestamp
    updated["events"] = events
    if target in {"TRIGGERED", "ACTIVE"} and not updated.get("triggered_at"):
        updated["triggered_at"] = timestamp
    if target == "EXITED":
        updated["exited_at"] = timestamp
    if target == "AUDITED":
        updated["audited_at"] = timestamp
    if target == "SKIPPED":
        updated["skipped_at"] = timestamp
    return updated


async def exit_linked_signal(
    db: Any,
    signal_id: Any,
    *,
    reason: str,
    trade_id: Any = None,
    realized_pnl: Any = None,
    at: Optional[str] = None,
) -> bool:
    """Idempotently move the signal behind a CLOSED trade ACTIVE -> EXITED.

    The ONE place a trade close reaches back to its signal. Before this existed the
    only ACTIVE -> EXITED transitions were the paper marker's own stop/target
    auto-close and the manual single-trade close, so a signal whose trade was
    squared by ANY other route (the 15:00 sweep, the boot reconcile, Stop /
    Stop-ALL, the basket controls, retire) stayed ACTIVE for good — and EVERY live
    close (guard, reconcile, kill switch) never touched it, so every live signal
    was ACTIVE forever. The Signal Journal then showed a wall of "active" signals
    for positions that were long flat.

    Returns True iff THIS call moved it. Only an ACTIVE signal moves: EXITED /
    AUDITED / SKIPPED, a missing doc or a blank id is left exactly as found, so a
    repeat call (and a second close route racing this one) is a harmless no-op.
    The write is conditional on ``state == ACTIVE`` for the same reason.

    NEVER raises. It runs on the far side of a close that has already been
    persisted — a journal failure here must not turn a real, completed exit into an
    error or stop the rest of a sweep — so any failure is logged and swallowed.
    """
    if not signal_id or db is None:
        return False
    try:
        sig = await db.signals.find_one({"id": signal_id}, {"_id": 0})
        if not sig or str(sig.get("state") or "").upper() != "ACTIVE":
            return False
        exited = transition_signal(
            sig, "EXITED", reason=reason, at=at,
            snapshot={"trade_id": trade_id, "realized_pnl": realized_pnl},
        )
        res = await db.signals.replace_one(
            {"id": signal_id, "state": "ACTIVE"}, exited, upsert=False)
        matched = getattr(res, "matched_count", None)
        return True if matched is None else bool(matched)
    except Exception as exc:  # noqa: BLE001 — see docstring: the close already happened
        log.warning("exit_linked_signal: signal %s not moved to EXITED (%s: %s)",
                    signal_id, type(exc).__name__, str(exc)[:160])
        return False
