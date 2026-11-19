"""Best-effort evidence capture; never a trading dependency or a broker client.

Hooks schedule bounded work and return immediately. Only acknowledged inserts
are durable. A crash can lose pending work; health is process-local, not proof
of complete history. Existing books are observed, never polled by this module.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from app.live.receipt_events import ReceiptEventStore, build_event, ensure_indexes

_tasks: set = set()
_failed = 0
_last_failure = None
_observed = False
MAX_PENDING = 64
MAX_ROWS = 500
FIELDS = ("exch", "tsym", "trantype", "prd", "prctyp", "qty", "prc", "trgprc", "ret")


def now():
    return datetime.now(timezone.utc).isoformat()


def failure():
    global _failed, _last_failure
    _failed += 1
    _last_failure = now()


def capture_status():
    return {"state": "degraded" if _failed else "ok" if _observed else "not_observed",
            "failed_writes": _failed, "last_failure_at": _last_failure,
            "pending_count": len(_tasks), "scope": "current_process"}


def schedule(factory):
    """Do not create an unawaited coroutine when the queue is full/no loop."""
    try:
        loop = asyncio.get_running_loop()
        if len(_tasks) >= MAX_PENDING:
            failure()
            return

        async def run():
            try:
                await asyncio.wait_for(factory(), timeout=3.0)
            except asyncio.CancelledError:
                failure()
                raise
            except Exception:
                failure()  # No raw errors/payloads, and no trading side effects.

        task = loop.create_task(run(), name="receipt-evidence")
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:
        failure()


async def initialize(collection):
    if not await ensure_indexes(collection):
        failure()


async def append(collection, event):
    global _observed
    result = await ReceiptEventStore(collection).append(event)
    if result["status"] == "unavailable":
        failure()
    else:
        _observed = True


def submitted(orders, receipts, client_order_id):
    """Run after the existing ACK write, without delaying protection setup."""
    async def capture():
        order = await orders.find_one({"client_order_id": client_order_id})
        if not order or not order.get("norenordno"):
            failure()
            return
        await append(receipts, build_event(
            kind="instruction_submitted", source="intent_ack_" + str(order.get("mode", "unknown")),
            recorded_at=now(), client_order_id=client_order_id,
            norenordno=order["norenordno"], deployment_id=order.get("deployment_id"),
            instruction=order.get("intent")))
    schedule(capture)


def approval_decided(approval_id, instruction, result=None):
    """Snapshot only allowlisted fields before deferring; never retain a token."""
    try:
        result = result if isinstance(result, dict) else {}
        event = build_event(kind="approval_decided", source="operator_approved",
                            recorded_at=now(), approval_id=approval_id,
                            client_order_id=result.get("cid"), norenordno=result.get("norenordno"),
                            instruction={k: instruction[k] for k in FIELDS if k in instruction})
        async def capture():
            from app.db import get_db
            await append(get_db().execution_receipt_events, event)
        schedule(capture)
    except Exception:
        failure()


def _identity_reasons(order, row, *, exit_row=False):
    intent = order.get("intent") or {}
    reasons = []
    for key in ("exch", "tsym", "prd", "trantype"):
        expected = "S" if exit_row and key == "trantype" else intent.get(key)
        if not row.get(key) or not expected:
            reasons.append("missing_identity")
        elif str(row[key]) != str(expected):
            reasons.append("identity_conflict")
    if exit_row and intent.get("trantype") != "B":
        reasons.append("identity_conflict")
    return reasons


def order_snapshot(order, row, captured_at):
    """No position averages, coercion to zero, or sum of cumulative fills."""
    reasons = _identity_reasons(order, row)
    if str(order.get("norenordno")) != str(row.get("norenordno")):
        reasons.append("identity_conflict")
    status = row.get("status")
    if not isinstance(status, str) or status not in {"OPEN", "COMPLETE", "CANCELED", "CANCELLED", "REJECTED", "REJECT", "PENDING", "TRIGGER_PENDING", "PARTIAL"}:
        status = None
        reasons.append("incomplete_status")
    fill = {"quantity": row.get("fillshares"), "average_price": row.get("avgprc"),
            "order_status": status, "event_time": row.get("norentm")}
    event = build_event(kind="order_fill_snapshot", source="broker_order_book",
                       recorded_at=captured_at, client_order_id=order.get("client_order_id"),
                       norenordno=order.get("norenordno"), deployment_id=order.get("deployment_id"),
                       instruction=order.get("intent"), fill=fill, reasons=reasons)
    reported = event.get("fill") or {}
    qty = (event.get("instruction") or {}).get("qty")
    contradictory = (status == "COMPLETE" and reported.get("quantity") != qty
                     or reported.get("quantity") == 0 and reported.get("average_price", 0) > 0)
    if contradictory:
        event["evidence_state"] = "conflicting"
        event["reasons"] = sorted(set(event["reasons"]) | {"invalid_fill"})
        event = build_event(**{k: v for k, v in event.items() if k not in {"event_key", "schema_version"}})
    return event


def exit_entry_id(row):
    """Only an explicit, unambiguous OCO entry tag; never a same-symbol guess."""
    tags = set(re.findall(r"(?:^|[\s:])oco:([A-Za-z0-9_-]+)(?=$|[\s:,])", str(row.get("remarks") or "")))
    return next(iter(tags)) if len(tags) == 1 else None


def exit_snapshot(order, row, captured_at):
    reasons = _identity_reasons(order, row, exit_row=True)
    if exit_entry_id(row) != str(order.get("norenordno")):
        reasons.append("ambiguous_exit")
    event = build_event(kind="exit_fill_evidence", source="broker_trade_book",
                       recorded_at=captured_at, client_order_id=order.get("client_order_id"),
                       norenordno=order.get("norenordno"), deployment_id=order.get("deployment_id"),
                       instruction=order.get("intent"), reasons=reasons,
                       exit_evidence={"entry_norenordno": order.get("norenordno"),
                                      "exit_norenordno": row.get("norenordno"), "fill_id": row.get("flid"),
                                      "quantity": row.get("flqty"), "price": row.get("flprc"),
                                      "event_time": row.get("fltm"), "attribution": "explicit_oco_entry_tag"})
    exit_qty = (event.get("exit_evidence") or {}).get("quantity")
    entry_qty = (event.get("instruction") or {}).get("qty")
    if exit_qty is not None and entry_qty is not None and exit_qty > entry_qty:
        event["reasons"] = sorted(set(event["reasons"]) | {"overfill"})
        event = build_event(**{k: v for k, v in event.items() if k not in {"event_key", "schema_version"}})
    return event


async def capture_book(db, rows, *, trades=False, captured_at=None):
    """Only tracked LIVE identities; observed historical books are dated now."""
    if not isinstance(rows, list):
        failure()
        return
    if len(rows) > MAX_ROWS:
        failure()  # visible bounded capture; no silent completeness claim
    rows = [r for r in rows[:MAX_ROWS] if isinstance(r, dict)]
    pairs = [(exit_entry_id(r) if trades else str(r.get("norenordno") or ""), r) for r in rows]
    ids = list({key for key, _ in pairs if key})
    if not ids:
        return
    orders = await db.live_orders.find({"norenordno": {"$in": ids}, "mode": "live"}, {"_id": 0}).to_list(length=MAX_ROWS + 1)
    if len(orders) > MAX_ROWS:
        failure()
        return
    captured_at = captured_at or now()
    for key, row in pairs:
        matches = [o for o in orders if str(o.get("norenordno")) == key and o.get("mode") == "live"]
        if len(matches) != 1:
            if matches:
                failure()
            continue
        event = (exit_snapshot if trades else order_snapshot)(matches[0], row, captured_at)
        await append(db.execution_receipt_events, event)


def observe_book(rows, *, trades=False):
    """Hook into an existing successful book read; no broker calls are added."""
    try:
        # Keep no account/session/raw payload fields while waiting in the queue.
        keys = set(FIELDS) | {"norenordno", "fillshares", "avgprc", "status", "norentm", "flid", "flqty", "flprc", "fltm"}
        if not isinstance(rows, list):
            failure()
            return
        if len(rows) > MAX_ROWS:
            failure()
        safe_rows = []
        for row in rows[:MAX_ROWS]:
            if not isinstance(row, dict):
                continue
            safe = {k: row[k] for k in keys if k in row}
            if trades:
                entry = exit_entry_id(row)
                safe["remarks"] = "oco:" + entry if entry else ""
            safe_rows.append(safe)
        captured_at = now()
        async def capture():
            from app.db import get_db
            await capture_book(get_db(), safe_rows, trades=trades, captured_at=captured_at)
        schedule(capture)
    except Exception:
        failure()
