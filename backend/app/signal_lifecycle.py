"""Auditable live-signal lifecycle helpers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional


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
