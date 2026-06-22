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

Engine-halt flags (L2.3 reads these to pause/alert instead of proceeding)
--------------------------------------------------------------------------
reconcile_required : bool
    Set True when a contradiction is detected that the state machine cannot
    safely resolve unilaterally:
    - A fill arrives on a CANCELED/REJECTED order with a higher cumulative
      fillshares than what was recorded (post_terminal_fillshares).
    - An over-fill (fillshares > qty) is clamped.
    - A COMPLETE fill event carries no valid avgprc (torn read).
    - norenordno in the om does not match norenordno in the doc (wrong-order
      guard fired; om not applied).

overfill : bool
    Set True when the incoming cumulative fillshares exceeds qty.  The stored
    fillshares is clamped to qty; the excess is a broker error.

post_terminal_fillshares : int
    Populated when a fill event arrives on a CANCELED or REJECTED doc and the
    incoming cumulative fillshares > the doc's current fillshares.  Surfaces
    the conflicting value for the reconcile path rather than silently landing
    a phantom position.
"""
from __future__ import annotations

import copy
import logging
import math
import re
from typing import Any, Dict

from app.live.broker_protocol import ORDER_STATES

log = logging.getLogger(__name__)

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
# Strategy: check TERMINAL markers FIRST (they win on mixed reasons).
# Only if NO terminal marker matched, check TRANSIENT markers.
# Default (none matched, None/empty/non-string) → "terminal".
#
# TERMINAL markers (word-boundary, priority):
#   margin / rms / insufficient / band / lot / symbol / disallowed /
#   not allowed / blocked / freeze / frozen / invalid / square off /
#   limit price
#
# TRANSIENT markers (word-boundary, only checked if no terminal match):
#   session / token / timeout / timed out / throttle / too many /
#   rate limit / connection / try again / temporarily / busy
#
# None / empty / non-string → "terminal" (fail-safe: never auto-retry an
# unknown reject; the human must inspect and decide).
# ---------------------------------------------------------------------------

# Each entry is a raw pattern; we compile with re.IGNORECASE.
# Multi-word patterns (spaces) are kept as literals — re handles word boundaries
# around punctuation correctly for single-word entries.
_TERMINAL_PATTERNS: tuple[str, ...] = (
    r"\bmargin\b",
    r"\brms\b",
    r"\binsufficient\b",
    r"\bband\b",
    r"\blot\b",
    r"\bsymbol\b",
    r"\bdisallowed\b",
    r"\bnot\s+allowed\b",
    r"\bblocked\b",
    r"\bfreeze\b",
    r"\bfrozen\b",
    # NOTE: bare \binvalid\b is intentionally omitted because "Invalid token"
    # and "Invalid session" are transient — the specific terminal sub-cases
    # (invalid lot, invalid symbol) are already covered by \blot\b / \bsymbol\b.
    r"\bsquare\s*off\b",
    r"\blimit\s+price\b",
)

_TRANSIENT_PATTERNS: tuple[str, ...] = (
    r"\bsession\b",
    r"\btoken\b",
    r"\btimeout\b",
    r"\btimed\s+out\b",
    r"\bthrottle\b",
    r"\btoo\s+many\b",
    r"\brate\b.*\blimit\b",   # "rate ... limit" (order matters in the string)
    r"\brate\s+limit\b",      # explicit phrase variant
    r"\bconnection\b",
    r"\btry\s+again\b",
    r"\btemporarily\b",
    r"\bbusy\b",
)

_COMPILED_TERMINAL = [re.compile(p, re.IGNORECASE) for p in _TERMINAL_PATTERNS]
_COMPILED_TRANSIENT = [re.compile(p, re.IGNORECASE) for p in _TRANSIENT_PATTERNS]


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

    Note: None / empty / non-string / unrecognised strings return "terminal"
    as a fail-safe to prevent blind retries on unknown error classes.
    Terminal markers are checked first; they win over transient on mixed reasons.
    Word-boundary matching prevents false positives (e.g. "rate" in "moderate").
    """
    if not isinstance(rejreason, str) or not rejreason:
        return "terminal"

    # Terminal markers win on any match — check first.
    for pattern in _COMPILED_TERMINAL:
        if pattern.search(rejreason):
            return "terminal"

    # Only if no terminal marker matched, check transient.
    for pattern in _COMPILED_TRANSIENT:
        if pattern.search(rejreason):
            return "transient"

    return "terminal"


# ---------------------------------------------------------------------------
# avgprc validation helper
# ---------------------------------------------------------------------------

def _valid_avgprc(value: Any) -> float | None:
    """Return float(value) if it is a finite positive number, else None.

    Accepts numeric strings; rejects NaN, inf, negative, zero, and anything
    that cannot be parsed as a float.
    """
    if value is None or value == "" or value == "0" or value == 0:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # bool is a subclass of int/float in Python — reject it
    if isinstance(value, bool):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


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
    # M1: coerce to str before .upper() so non-string status values never crash
    raw = str(om.get("status") or "").upper().strip()

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

    Engine-halt flags set on the returned doc
    ------------------------------------------
    reconcile_required  — see module docstring
    overfill            — see module docstring
    post_terminal_fillshares — see module docstring
    """
    # S6: deep-copy so nested mutables are not shared with the caller
    doc = copy.deepcopy(order_doc)

    current_state: str = doc.get("state", "INTENT")

    # --- C8. norenordno mismatch guard (second line behind the router) ------
    # If BOTH doc and om carry non-empty, non-matching norenordno values,
    # this event belongs to a different order.  Return early with only the
    # reconcile flag — do not apply ANY state/fill changes.
    doc_nord = doc.get("norenordno") or ""
    om_nord = om.get("norenordno") or ""
    if doc_nord and om_nord and doc_nord != om_nord:
        log.warning(
            "norenordno mismatch: doc=%r om=%r — om not applied, reconcile_required=True",
            doc_nord, om_nord,
        )
        doc["reconcile_required"] = True
        return doc

    # --- 1. Map raw status to an ORDER_STATES member ----------------------
    new_state = map_status(om, current_state=current_state)

    # --- 2. Terminal guard -------------------------------------------------
    # A terminal doc's STATE never changes.
    already_terminal = current_state in TERMINAL
    if already_terminal:
        new_state = current_state  # pin to current terminal

    # --- 3. No-regress (monotonic rank) ------------------------------------
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

    # M2: post-terminal fill injection guard
    # COMPLETE is special: monotonic-max fold is the normal/expected path.
    # CANCELED / REJECTED: a fill arriving here is a contradiction — surface it
    # via post_terminal_fillshares + reconcile_required rather than silently
    # merging a phantom position.
    if already_terminal and current_state in ("CANCELED", "REJECTED"):
        if incoming_fills > existing_fills:
            doc["post_terminal_fillshares"] = incoming_fills
            doc["reconcile_required"] = True
        # Do NOT update fillshares or avgprc — leave the doc as-is.
        # Still fall through to reject metadata + norenordno propagation below.
        # Return early so no fill logic runs.
        if doc.get("state") == "REJECTED":
            rr = om.get("rejreason")
            if rr is not None:
                doc["rejreason"] = rr
                doc["reject_class"] = classify_reject(rr)
        incoming_nord = om.get("norenordno")
        if incoming_nord and not doc.get("norenordno"):
            doc["norenordno"] = incoming_nord
        return doc

    # For COMPLETE (terminal) and non-terminal states: normal monotonic-max fold.
    new_fills = max(existing_fills, incoming_fills)

    # M4: over-fill guard — clamp to qty, flag for reconcile
    qty: int = 0
    try:
        qty = int(doc.get("qty") or om.get("qty") or 0)
    except (TypeError, ValueError):
        pass
    if qty > 0 and new_fills > qty:
        doc["overfill"] = True
        doc["reconcile_required"] = True
        new_fills = qty  # clamp

    doc["fillshares"] = new_fills

    # avgprc: take the om's value only when fillshares actually increased
    fills_increased = new_fills > existing_fills
    avgprc_stored = False
    if fills_increased:
        valid = _valid_avgprc(om.get("avgprc"))
        if valid is not None:
            doc["avgprc"] = valid
            avgprc_stored = True

    # S7: torn-read flag — COMPLETE fill with no valid avgprc is an inconsistency
    if fills_increased and not avgprc_stored and doc.get("state") == "COMPLETE":
        doc["reconcile_required"] = True

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
