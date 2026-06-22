"""Order state machine for Noren (Flattrade) WS om events — L2.1.

Design invariants
-----------------
1. PURE / IMMUTABLE — `apply_om` returns a NEW dict; it never mutates the input.
2. STATE NEVER REGRESSES — state rank is monotonically non-decreasing; a late
   duplicate or out-of-order lower-ranked event is silently discarded.
3. TERMINAL IS STICKY — once COMPLETE / REJECTED / CANCELED, the state field
   cannot change.  (Special case: COMPLETE still accepts a higher cumulative
   fillshares from a replayed om, but does NOT leave COMPLETE.)
4. FILLS ARE CUMULATIVE, NOT ADDITIVE — Noren reports a running total in
   `fillshares`, not a per-event delta.  We take max(existing, incoming) so
   duplicate replays never double-count.
5. UNKNOWN STATUS → NO-OP — an unrecognised or missing status field preserves
   the current state rather than corrupting it.
6. NEVER CRASH — non-numeric fillshares / avgprc are silently ignored; the doc
   is returned with whatever numeric values were already stored.
"""
from __future__ import annotations

from typing import Any, Dict

from app.live.broker_protocol import ORDER_STATES

# ---------------------------------------------------------------------------
# State rank — higher = further along the lifecycle
# OPEN and TRIGGER_PENDING share rank 3 (order is live but not yet filled)
# COMPLETE / REJECTED / CANCELED all share rank 5 (terminal)
# ---------------------------------------------------------------------------
STATE_RANK: Dict[str, int] = {
    "INTENT": 0,
    "SUBMITTED": 1,
    "ACKED": 2,
    "OPEN": 3,
    "TRIGGER_PENDING": 3,
    "PARTIAL": 4,
    "COMPLETE": 5,
    "REJECTED": 5,
    "CANCELED": 5,
}

TERMINAL: frozenset[str] = frozenset({"COMPLETE", "REJECTED", "CANCELED"})

# ---------------------------------------------------------------------------
# Reject classification
#
# TRANSIENT keywords — these failures are recoverable with a retry:
#   session / token / timeout / throttle / "too many" / "rate" /
#   connection / "try again"
#
# TERMINAL (default) — everything else must NOT be auto-retried:
#   disallowed order type, wrong lot/qty/symbol, RMS/margin/insufficient,
#   price band, etc.
#
# None / empty rejreason → "terminal" (fail-safe: never auto-retry an
# unknown reject; the human must inspect and decide).
# ---------------------------------------------------------------------------
_TRANSIENT_SUBSTRINGS: tuple[str, ...] = (
    "session",
    "token",
    "timeout",
    "throttle",
    "too many",
    "rate",
    "connection",
    "try again",
)


def classify_reject(rejreason: Any) -> str:
    """Return "transient" or "terminal" for a Noren reject reason string.

    Parameters
    ----------
    rejreason : str or None
        The raw rejreason string from the om event.

    Returns
    -------
    "transient"
        Session expired, token issues, throttle, rate-limit, timeout, or
        connection errors — safe to retry automatically.
    "terminal"
        Everything else (disallowed order type, wrong lot/qty/symbol,
        RMS/margin/insufficient funds, price band, etc.) — must NOT be
        auto-retried; requires human inspection.

    Note: None / empty / unrecognised strings return "terminal" as a
    fail-safe to prevent blind retries on unknown error classes.
    """
    if not rejreason:
        return "terminal"
    lower = str(rejreason).lower()
    for keyword in _TRANSIENT_SUBSTRINGS:
        if keyword in lower:
            return "transient"
    return "terminal"


# ---------------------------------------------------------------------------
# Status mapping — raw Noren om status → ORDER_STATES member
# ---------------------------------------------------------------------------

def map_status(om: Dict[str, Any], current_state: str = "INTENT") -> str:
    """Map a raw Noren om event status to an ORDER_STATES member.

    Parameters
    ----------
    om : dict
        The raw om event dict from the Noren WebSocket.
    current_state : str
        The current state in the order_doc.  Used as a fallback when the
        om status is unknown/missing — preserves the current state rather
        than inventing one.

    Noren status → ORDER_STATES mapping
    ------------------------------------
    PENDING           → SUBMITTED
    NEW               → ACKED
    OPEN              → OPEN  (but OPEN + 0 < fillshares < qty → PARTIAL)
    TRIGGER_PENDING   → TRIGGER_PENDING
    COMPLETE          → COMPLETE
    REJECTED          → REJECTED
    CANCELED          → CANCELED
    PARTIALLY_FILLED  → PARTIAL
    PARTIAL           → PARTIAL  (Noren sometimes uses this directly)
    <unknown/missing> → current_state unchanged  (never invent a state)
    """
    raw = (om.get("status") or "").upper().strip()

    if raw == "PENDING":
        return "SUBMITTED"
    if raw == "NEW":
        return "ACKED"
    if raw == "TRIGGER_PENDING":
        return "TRIGGER_PENDING"
    if raw == "COMPLETE":
        return "COMPLETE"
    if raw == "REJECTED":
        return "REJECTED"
    if raw == "CANCELED":
        return "CANCELED"
    if raw in ("PARTIALLY_FILLED", "PARTIAL"):
        return "PARTIAL"
    if raw == "OPEN":
        # An OPEN event with a non-zero partial fill is really PARTIAL
        try:
            fs = int(om.get("fillshares") or 0)
            qty = int(om.get("qty") or 0)
            if fs > 0 and qty > 0 and fs < qty:
                return "PARTIAL"
        except (TypeError, ValueError):
            pass
        return "OPEN"

    # Unknown / missing status → preserve current state (never invent)
    return current_state


# ---------------------------------------------------------------------------
# Core state machine
# ---------------------------------------------------------------------------

def apply_om(order_doc: Dict[str, Any], om: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a Noren om event to an order_doc, returning a NEW dict.

    Idempotent under duplicate and out-of-order events:
    - State rank is monotonically non-decreasing.
    - Terminal states are sticky.
    - fillshares is the running max (cumulative, never additive).

    Parameters
    ----------
    order_doc : dict
        The current order document from the idempotency store (live_orders).
        Must contain at least: state, qty.
        Optional: fillshares (defaults 0), avgprc (defaults None).
    om : dict
        A Noren WebSocket om event dict.  Expected keys:
        status, fillshares (cumulative), avgprc, norenordno, qty, rejreason.

    Returns
    -------
    dict
        A NEW order_doc with updated fields.  The input is NOT mutated.
    """
    # Always start from a shallow copy — PURE function guarantee
    doc = dict(order_doc)

    current_state: str = doc.get("state", "INTENT")

    # --- 1. Map raw status to an ORDER_STATES member ----------------------
    new_state = map_status(om, current_state=current_state)

    # --- 2. Terminal guard -------------------------------------------------
    # A terminal doc's STATE never changes.  (But we still update fillshares
    # if COMPLETE receives a higher cumulative total — see point 4 below.)
    already_terminal = current_state in TERMINAL
    if already_terminal:
        new_state = current_state  # pin to current terminal

    # --- 3. No-regress (monotonic rank) ------------------------------------
    # If the incoming event has equal or lower rank, keep the current state.
    if not already_terminal:
        if STATE_RANK.get(new_state, 0) > STATE_RANK.get(current_state, 0):
            doc["state"] = new_state
        # equal rank: keep current state unchanged (no-op; covers ACK replays etc.)
        # lower rank: discard (out-of-order late event)

    # --- 4. Cumulative fill dedup (MONOTONIC MAX) --------------------------
    existing_fills: int = int(doc.get("fillshares") or 0)
    try:
        incoming_fills: int = int(om.get("fillshares") or 0)
    except (TypeError, ValueError):
        incoming_fills = existing_fills  # guard non-numeric — keep existing

    new_fills = max(existing_fills, incoming_fills)
    doc["fillshares"] = new_fills

    # avgprc: take the om's value only when fillshares actually increased
    if new_fills > existing_fills:
        try:
            raw_avgprc = om.get("avgprc")
            if raw_avgprc not in (None, "", "0", 0):
                doc["avgprc"] = float(raw_avgprc)
        except (TypeError, ValueError):
            pass  # non-numeric avgprc — keep whatever was there

    # --- 5. Reject metadata -----------------------------------------------
    if doc.get("state") == "REJECTED":
        rr = om.get("rejreason")
        doc["rejreason"] = rr
        doc["reject_class"] = classify_reject(rr)

    # --- 6. norenordno — propagate if not already set ---------------------
    incoming_nord = om.get("norenordno")
    if incoming_nord and not doc.get("norenordno"):
        doc["norenordno"] = incoming_nord

    return doc
