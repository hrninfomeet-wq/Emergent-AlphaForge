"""Read-only receipts from existing records. No broker access or persistence.

Stored order events are evidence about that order, not a fresh broker-book read.
The journal's exit mark is never promoted to a fill, even when entry fills exist.
"""
from __future__ import annotations

import math
from typing import Any


def _number(value: Any, *, positive: bool = False, integer: bool = False):
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        return None
    if integer and not result.is_integer():
        return None
    return int(result) if integer else result


def _signed(value: Any):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _text(value: Any):
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def execution_receipt(order: dict, journals: list[dict], guards: list[dict] | None) -> dict:
    """Allowlisted view; never return account IDs, tokens or arbitrary payloads."""
    intent = order.get("intent") if isinstance(order.get("intent"), dict) else {}
    nord = _text(order.get("norenordno"))
    qty = _number(intent.get("qty"), positive=True, integer=True)
    fill_qty = _number(order.get("fillshares"), integer=True)
    avg = _number(order.get("avgprc"), positive=True)
    warnings = []
    flags = [key for key in ("reconcile_required", "overfill", "nord_mismatch", "post_terminal_fillshares")
             if order.get(key)]
    if flags:
        warnings.append("Stored order has reconciliation flags; fill evidence is not reliable.")
    if fill_qty is not None and qty is not None and fill_qty > qty:
        flags.append("fill_exceeds_instruction")
        warnings.append("Reported filled quantity exceeds the recorded instruction.")
    if fill_qty is not None and fill_qty > 0 and avg is None:
        flags.append("missing_average")
        warnings.append("Filled quantity is present without a valid average price.")
    fill_status = "needs_review" if flags else ("stored_order_event" if fill_qty is not None else "unavailable")
    if fill_status == "unavailable":
        warnings.append("No stored order-specific fill evidence. Submitted does not mean filled.")
    own_fills = not flags and fill_qty is not None and fill_qty > 0 and avg is not None and bool(nord)
    partial = fill_qty is not None and qty is not None and 0 < fill_qty < qty
    if partial:
        warnings.append("Recorded fills are partial; the unfilled remainder may still be working.")

    matches = [j for j in journals if nord and str(j.get("norenordno") or "") == nord]
    journal = None
    if len(matches) > 1:
        warnings.append("Multiple journal rows match this order; no P&L comparison was made.")
    elif matches:
        j = matches[0]
        # trading_symbol is an Upstox/display label, not a Noren contract key.
        identity_conflict = any(j.get(key) and j[key] != intent.get(other)
                                for key, other in (("noren_tsym", "tsym"), ("exch", "exch")))
        if identity_conflict:
            warnings.append("Journal contract differs from the stored instruction; no P&L estimate was made.")
        # Match the journal's own entry/quantity basis without treating it as proof.
        basis_qty = _number(j.get("filled_quantity"), integer=True)
        if basis_qty is None:
            basis_qty = _number(j.get("quantity"), integer=True)
        basis_px = (_number(j.get("entry_basis_price"), positive=True)
                    if j.get("entry_basis") == "own_fills" else None)
        if basis_px is None:
            basis_px = _number(j.get("entry_fill_price"), positive=True)
        if basis_px is None:
            basis_px = _number(j.get("entry_price"), positive=True)
        exit_px = _number(j.get("exit_price"), positive=True)
        gross = _signed(j.get("realized_pnl"))
        # Diagnostic only: not a realized result, not used in caps or statistics.
        at_mark = (round(fill_qty * (exit_px - avg), 2)
                   if own_fills and not identity_conflict and exit_px is not None and intent.get("trantype") == "B"
                   and j.get("status") == "CLOSED" else None)
        differences = []
        if own_fills and basis_qty is not None and basis_qty != fill_qty:
            differences.append("quantity")
        if own_fills and basis_px is not None and abs(basis_px - avg) > 0.0001:
            differences.append("entry_price")
        if differences:
            warnings.append("Journal basis differs from the stored order fills: " + ", ".join(differences) + ".")
        journal = {
            "status": _text(j.get("status")), "quantity_basis": basis_qty,
            "entry_basis_price": basis_px, "exit_price": exit_px,
            "gross_pnl": gross, "net_pnl": _signed(j.get("net_realized_pnl")),
            "exit_evidence": "not_verified_by_receipt", "differences": differences,
            "gross_at_journal_exit_using_entry_fills": at_mark,
            "gross_difference": round(at_mark - gross, 2) if at_mark is not None and gross is not None else None,
        }
        if j.get("status") == "CLOSED":
            warnings.append("Exit fill not independently verified here; journal P&L may use an estimated exit.")

    registered = [g for g in (guards or []) if nord and str(g.get("id") or "") == nord]
    protection = {"state": "unavailable" if guards is None else "not_registered",
                  "broker_exit_verified": False}
    if len(registered) == 1:
        g = registered[0]
        if g.get("tsym") == intent.get("tsym") and g.get("exch") == intent.get("exch"):
            state = g.get("state") if isinstance(g.get("state"), dict) else {}
            protection.update(state="registered", source=_text(g.get("source")),
                              stop_level=_number(state.get("stop_level"), positive=True),
                              target_level=_number(state.get("target_level"), positive=True))
        else:
            protection["state"] = "identity_mismatch"
    elif len(registered) > 1:
        protection["state"] = "ambiguous"

    return {
        "client_order_id": _text(order.get("client_order_id")), "norenordno": nord,
        "mode": _text(order.get("mode")), "state": _text(order.get("state")),
        "recorded_at": _text(order.get("ts_intent")),
        "submitted_at": _text(order.get("ts_submitted")),
        "instruction": {"symbol": _text(intent.get("tsym")), "exchange": _text(intent.get("exch")),
                        "side": _text(intent.get("trantype")), "product": _text(intent.get("prd")),
                        "order_type": _text(intent.get("prctyp")), "quantity": qty,
                        "limit_price": _number(intent.get("prc"), positive=True)},
        "approval_evidence": "not_retained_in_order_record",
        "fills": {"evidence": fill_status, "quantity": fill_qty, "average_price": avg,
                  "partial": partial, "flags": flags, "freshness": "not_verified"},
        "journal": journal, "protection": protection, "warnings": warnings,
    }


async def receipt_page(db: Any, *, limit: int, skip: int, guards: list[dict] | None) -> dict:
    """Bounded DB reads only; includes unsubmitted and mock intents with labels."""
    orders = await db.live_orders.find({}, {"_id": 0}).sort("ts_intent", -1).skip(skip).to_list(length=limit)
    ids = list({str(o["norenordno"]) for o in orders if o.get("norenordno")})
    journals = await db.live_trades.find({"norenordno": {"$in": ids}}, {"_id": 0}).to_list(length=None) if ids else []
    from app.live.receipt_evidence import attach_evidence
    from app.live.receipt_capture import capture_status
    items = [execution_receipt(o, journals, guards) for o in orders]
    await attach_evidence(db, orders, items)
    return {"items": items, "capture_status": capture_status(),
            "total": await db.live_orders.count_documents({}), "limit": limit, "skip": skip}
