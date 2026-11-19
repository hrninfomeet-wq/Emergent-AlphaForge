"""Allowlisted, append-only receipt evidence. No broker or database connection setup.

Fill quantities are cumulative snapshots, never increments. Evidence ordering and
accounting belong to readers; this store preserves distinct observed snapshots.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from pymongo.errors import DuplicateKeyError

KINDS = frozenset({"approval_decided", "instruction_submitted", "order_fill_snapshot", "exit_fill_evidence"})
STATES = frozenset({"observed", "conflicting", "incomplete"})
REASONS = frozenset({"missing_identity", "invalid_instruction", "missing_fill", "invalid_fill",
                     "identity_conflict", "overfill", "incomplete_status", "ambiguous_exit",
                     "missing_exit_id", "missing_fill_id"})
WRITE_TIMEOUT_SECONDS = 0.75


def _text(value: Any, limit: int, *, identifier: bool = False) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    if not identifier and not isinstance(value, str):
        return None
    value = str(value).strip()
    if not value or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return value


def _number(value: Any, *, quantity: bool = False, positive: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or (positive and number == 0):
            return None
        if quantity:
            # MongoDB integers are signed 64-bit; reject values it cannot store.
            if number != number.to_integral_value() or number > 9223372036854775807:
                return None
            return int(number)
        result = float(number)
        if not math.isfinite(result) or (positive and result == 0):
            return None
        return result
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _capture_time(value: Any) -> str:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError("invalid receipt capture time") from None


def _event_time(value: Any) -> str | None:
    if isinstance(value, datetime):
        # Preserve the supplied clock representation; do not assume a timezone.
        return value.isoformat()
    return _text(value, 80)


def build_event(*, kind, source, recorded_at, client_order_id=None, norenordno=None,
                approval_id=None, deployment_id=None, instruction=None, fill=None,
                exit_evidence=None, evidence_state="observed", reasons=None) -> dict:
    """Build safe evidence and a deterministic key, excluding capture time.

    Invalid envelope values raise ValueError. Invalid evidence is omitted and
    marked incomplete, so an invalid number never becomes a fabricated zero.
    Unknown keys (including secrets and raw payloads) are never copied.
    """
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("invalid receipt kind")
    if not isinstance(evidence_state, str) or evidence_state not in STATES:
        raise ValueError("invalid receipt state")
    safe_source = _text(source, 80)
    if safe_source is None:
        raise ValueError("invalid receipt source")
    safe_reasons = {r for r in reasons if isinstance(r, str) and r in REASONS} if isinstance(reasons, (list, tuple, set, frozenset)) else set()
    event = {"schema_version": 1, "kind": kind, "recorded_at": _capture_time(recorded_at), "source": safe_source}
    for key, value in (("client_order_id", client_order_id), ("norenordno", norenordno),
                       ("approval_id", approval_id), ("deployment_id", deployment_id)):
        event[key] = _text(value, 256, identifier=True)
        if value is not None and event[key] is None:
            safe_reasons.add("missing_identity")
    if not any(event[key] for key in ("client_order_id", "norenordno", "approval_id")):
        safe_reasons.add("missing_identity")

    safe_instruction = {}
    if instruction is not None:
        if not isinstance(instruction, dict):
            safe_reasons.add("invalid_instruction")
        else:
            for key in ("exch", "tsym", "trantype", "prd", "prctyp", "ret"):
                if key in instruction:
                    value = _text(instruction[key], 256 if key == "tsym" else 32)
                    if value is None:
                        safe_reasons.add("invalid_instruction")
                    else:
                        safe_instruction[key] = value
            for key in ("qty", "prc", "trgprc"):
                if key in instruction:
                    value = _number(instruction[key], quantity=key == "qty", positive=key == "qty")
                    if value is None:
                        safe_reasons.add("invalid_instruction")
                    else:
                        safe_instruction[key] = value
            if any(key not in safe_instruction for key in ("exch", "tsym", "trantype", "prd", "prctyp", "qty")):
                safe_reasons.add("invalid_instruction")
            if safe_instruction.get("trantype") not in {"B", "S"}:
                safe_reasons.add("invalid_instruction")
    elif kind in {"approval_decided", "instruction_submitted"}:
        safe_reasons.add("invalid_instruction")
    event["instruction"] = safe_instruction or None

    safe_fill = {}
    if fill is not None:
        if not isinstance(fill, dict):
            safe_reasons.add("invalid_fill")
        else:
            for key in ("quantity", "average_price"):
                if key in fill:
                    value = _number(fill[key], quantity=key == "quantity")
                    if value is None:
                        safe_reasons.add("invalid_fill")
                    else:
                        safe_fill[key] = value
            for key in ("order_status", "event_time"):
                if key in fill and fill[key] is not None:
                    value = _event_time(fill[key]) if key == "event_time" else _text(fill[key], 64)
                    if value is None:
                        safe_reasons.add("invalid_fill")
                    else:
                        safe_fill[key] = value
            if "quantity" not in safe_fill or (safe_fill["quantity"] > 0 and not safe_fill.get("average_price")):
                safe_reasons.add("invalid_fill")
            if not safe_fill.get("order_status"):
                safe_reasons.add("incomplete_status")
            if safe_fill.get("quantity", 0) > safe_instruction.get("qty", float("inf")):
                safe_reasons.add("overfill")
    if kind == "order_fill_snapshot" and not safe_fill:
        safe_reasons.add("missing_fill")
    event["fill"] = safe_fill or None

    safe_exit = {}
    if exit_evidence is not None:
        if not isinstance(exit_evidence, dict):
            safe_reasons.add("ambiguous_exit")
        else:
            for key in ("entry_norenordno", "exit_norenordno", "fill_id", "attribution"):
                if key in exit_evidence:
                    value = _text(exit_evidence[key], 256, identifier=key != "attribution")
                    if value is not None:
                        safe_exit[key] = value
                    else:
                        safe_reasons.add("ambiguous_exit")
            for key in ("quantity", "price"):
                if key in exit_evidence:
                    value = _number(exit_evidence[key], quantity=key == "quantity", positive=True)
                    if value is not None:
                        safe_exit[key] = value
                    else:
                        safe_reasons.add("invalid_fill")
            if exit_evidence.get("event_time") is not None:
                value = _event_time(exit_evidence["event_time"])
                if value is not None:
                    safe_exit["event_time"] = value
                else:
                    safe_reasons.add("invalid_fill")
    if kind == "exit_fill_evidence" or exit_evidence is not None:
        if not safe_exit.get("exit_norenordno"):
            safe_reasons.add("missing_exit_id")
        if not safe_exit.get("fill_id"):
            safe_reasons.add("missing_fill_id")
        if not safe_exit.get("entry_norenordno") or not safe_exit.get("attribution"):
            safe_reasons.add("ambiguous_exit")
        if "quantity" not in safe_exit or "price" not in safe_exit:
            safe_reasons.add("invalid_fill")
        if safe_exit.get("entry_norenordno") == safe_exit.get("exit_norenordno") and safe_exit.get("entry_norenordno"):
            safe_reasons.add("identity_conflict")
    event["exit_evidence"] = safe_exit or None
    if safe_reasons & {"identity_conflict", "overfill"}:
        evidence_state = "conflicting"
    elif safe_reasons and evidence_state == "observed":
        evidence_state = "incomplete"
    event["evidence_state"] = evidence_state
    event["reasons"] = sorted(safe_reasons)
    meaningful = {k: v for k, v in event.items() if k != "recorded_at"}
    event["event_key"] = hashlib.sha256(json.dumps(meaningful, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return event


async def ensure_indexes(collection: Any) -> bool:
    """Idempotent startup call; index failure never escapes into execution."""
    try:
        await asyncio.wait_for(collection.create_index("event_key", unique=True), WRITE_TIMEOUT_SECONDS)
        return True
    except Exception:
        return False


class ReceiptEventStore:
    def __init__(self, collection: Any):
        self._collection = collection

    async def append(self, event) -> dict:
        """Rebuild untrusted input; insert atomically without replacing captures."""
        try:
            if not isinstance(event, dict):
                return {"status": "unavailable"}
            fields = ("kind", "source", "recorded_at", "client_order_id", "norenordno", "approval_id",
                      "deployment_id", "instruction", "fill", "exit_evidence", "evidence_state", "reasons")
            safe = build_event(**{key: event[key] for key in fields if key in event})
            safe["_id"] = safe["event_key"]
            await asyncio.wait_for(self._collection.insert_one(safe), WRITE_TIMEOUT_SECONDS)
            return {"status": "recorded"}
        except DuplicateKeyError:
            return {"status": "duplicate"}
        except Exception:
            return {"status": "unavailable"}
