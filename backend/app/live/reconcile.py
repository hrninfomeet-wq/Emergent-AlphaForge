"""Broker reconciliation diff — pure function, no DB/network.

Compares internal order/position state against the broker's live order book and
position book and returns a structured diff report.  Fails toward mismatch: when
in doubt, flag it (a false halt is safer than trading blind).

Broker field contract (consumed here — must match L2.3 wiring):
---------------------------------------------------------------
Broker order row fields read:
    "norenordno"  : str  — broker order number (same key used in internal docs)
    "status"      : str  — e.g. "OPEN", "TRIGGER_PENDING", "PARTIAL", "COMPLETE",
                          "CANCELED", "REJECTED".  Absent → treated as working
                          (fail-toward-mismatch).

Broker position row fields read:
    "tsym"        : str  — trading symbol, same namespace as internal positions
    "netqty"      : str  — net qty as a string (Noren convention); absent → "0"

Internal order doc fields read:
    "state"       : str  — one of ORDER_STATES from broker_protocol.py
    "norenordno"  : str | None  — present once SUBMITTED; absent in INTENT state

Internal position doc fields read:
    "tsym"        : str
    "qty"         : int  — net qty (signed; 0 = flat)
"""
from __future__ import annotations

from typing import Any, Dict, List

# Broker statuses that mean "this order is no longer working at the broker".
# An internal *working* order whose broker status is one of these (or is
# absent from the broker book entirely) is a mismatch.
_BROKER_TERMINAL_STATUSES = frozenset({"COMPLETE", "CANCELED", "REJECTED"})

# Internal states considered "working" — these orders must exist at the
# broker as a non-terminal entry.
_INTERNAL_WORKING_STATES = frozenset({"OPEN", "TRIGGER_PENDING", "SUBMITTED", "PARTIAL"})


def reconcile(
    internal_orders: List[Dict[str, Any]],
    internal_positions: List[Dict[str, Any]],
    broker_orders: List[Dict[str, Any]],
    broker_positions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare internal state against broker books and return a diff report.

    Parameters
    ----------
    internal_orders
        Docs from the ``live_orders`` store.  Each should have ``state`` and,
        once submitted, ``norenordno``.
    internal_positions
        Internal position tracking docs with ``tsym`` and ``qty`` (int, signed).
    broker_orders
        Raw rows from ``BrokerClient.order_book()``.  Each has ``norenordno``
        and ``status``.
    broker_positions
        Raw rows from ``BrokerClient.position_book()``.  Each has ``tsym`` and
        ``netqty`` (str).

    Returns
    -------
    dict
        ``{"ok": bool, "mismatches": [{"type": str, "detail": dict}, ...]}``
    """
    mismatches: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Build broker-side lookup structures
    # ------------------------------------------------------------------
    # norenordno -> broker order dict
    broker_order_map: Dict[str, Dict[str, Any]] = {
        row["norenordno"]: row
        for row in broker_orders
        if row.get("norenordno")
    }

    # norenordno -> status (absent key → treat as working/unknown → fail-toward-mismatch)
    def _broker_status(norenordno: str) -> str:
        row = broker_order_map.get(norenordno)
        if row is None:
            return ""  # not in book at all
        return row.get("status", "")  # absent status → ""

    # ------------------------------------------------------------------
    # 1. Order reconciliation
    # ------------------------------------------------------------------

    # Track which broker norenordnos were "claimed" by internal orders
    # so we can flag leftover broker working orders.
    claimed_broker_orders: set = set()

    for doc in internal_orders:
        state = doc.get("state", "")
        norenordno = doc.get("norenordno")

        if state not in _INTERNAL_WORKING_STATES:
            # Terminal or pre-submission — no broker-side expectation.
            continue

        # Working internal order: must exist at broker as a non-terminal entry.
        if not norenordno:
            # Working state but no norenordno yet — shouldn't happen after
            # SUBMITTED, but treat as mismatch to be safe.
            mismatches.append({
                "type": "internal_order_not_at_broker",
                "detail": {
                    "state": state,
                    "norenordno": None,
                    "reason": "working state but norenordno absent",
                },
            })
            continue

        broker_status = _broker_status(norenordno)

        if broker_status == "" or broker_status in _BROKER_TERMINAL_STATUSES:
            # Broker doesn't know about it, or shows it as terminal while we
            # think it's still working.
            mismatches.append({
                "type": "internal_order_not_at_broker",
                "detail": {
                    "norenordno": norenordno,
                    "internal_state": state,
                    "broker_status": broker_status or "NOT_FOUND",
                },
            })
        else:
            claimed_broker_orders.add(norenordno)

    # Flag broker working orders with no internal match.
    for norenordno, row in broker_order_map.items():
        broker_status = row.get("status", "")
        # Absent status → fail-toward-mismatch → treat as working/unknown.
        if broker_status in _BROKER_TERMINAL_STATUSES:
            continue  # terminal broker order with no internal record is fine
        if norenordno not in claimed_broker_orders:
            mismatches.append({
                "type": "unknown_broker_order",
                "detail": {
                    "norenordno": norenordno,
                    "broker_status": broker_status or "UNKNOWN",
                },
            })

    # ------------------------------------------------------------------
    # 2. Position reconciliation
    # ------------------------------------------------------------------

    # Build internal position map: tsym -> qty
    internal_pos_map: Dict[str, int] = {}
    for pos in internal_positions:
        tsym = pos.get("tsym")
        if tsym:
            qty = pos.get("qty", 0)
            try:
                internal_pos_map[tsym] = int(qty)
            except (TypeError, ValueError):
                internal_pos_map[tsym] = 0

    # Walk broker positions
    seen_broker_tsyms: set = set()
    for row in broker_positions:
        tsym = row.get("tsym")
        if not tsym:
            continue

        raw_netqty = row.get("netqty", "0")
        try:
            broker_qty = int(float(raw_netqty))
        except (TypeError, ValueError):
            broker_qty = 0

        seen_broker_tsyms.add(tsym)

        if tsym in internal_pos_map:
            internal_qty = internal_pos_map[tsym]
            if internal_qty != broker_qty:
                mismatches.append({
                    "type": "position_qty_mismatch",
                    "detail": {
                        "tsym": tsym,
                        "internal_qty": internal_qty,
                        "broker_qty": broker_qty,
                    },
                })
        else:
            # Broker has a position we have no internal record for.
            # Only flag if net qty != 0 (a zero position is effectively flat).
            if broker_qty != 0:
                mismatches.append({
                    "type": "unknown_broker_position",
                    "detail": {
                        "tsym": tsym,
                        "broker_qty": broker_qty,
                    },
                })

    # Note: internal positions NOT seen in the broker book are *not* flagged
    # here — a position that exists internally but is flat at broker (qty 0 on
    # both sides) is reconciled cleanly via the qty-match check above once the
    # broker returns the symbol with netqty "0".  Positions the broker never
    # returns (e.g. a stale internal record) are handled by the engine layer
    # which can compare against the full position universe; this pure diff only
    # works with what's given.

    return {
        "ok": len(mismatches) == 0,
        "mismatches": mismatches,
    }
