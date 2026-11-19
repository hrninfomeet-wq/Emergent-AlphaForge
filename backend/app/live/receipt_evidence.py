"""Bounded, sanitized durable receipt reads; no writes or broker access."""
import asyncio

from app.live.receipt_events import build_event

EVENT_LIMIT = 50
EVENT_FIELDS = ("kind", "source", "recorded_at", "client_order_id", "norenordno", "approval_id",
                "deployment_id", "instruction", "fill", "exit_evidence", "evidence_state", "reasons")


def safe_events(rows, order):
    result = []
    for row in rows:
        event = build_event(**{k: row[k] for k in EVENT_FIELDS if k in row})
        if event["event_key"] != row.get("event_key"):
            raise ValueError("receipt integrity unavailable")
        intent = order.get("intent") or {}
        conflict = any(event.get(k) and order.get(k) and str(event[k]) != str(order[k])
                       for k in ("client_order_id", "norenordno", "deployment_id"))
        instruction = event.get("instruction") or {}
        conflict |= any(instruction.get(k) and intent.get(k) and str(instruction[k]) != str(intent[k])
                        for k in ("exch", "tsym", "trantype", "prd"))
        if conflict:
            event["evidence_state"] = "conflicting"
            event["reasons"] = sorted(set(event["reasons"]) | {"identity_conflict"})
        result.append(event)
    # Contradictory observations of the SAME broker fill or event timestamp stay
    # visible; arrival order cannot select the truth. Never sum snapshots.
    groups = {}
    for event in result:
        fill, exit_fill = event.get("fill") or {}, event.get("exit_evidence") or {}
        key = None
        if exit_fill.get("fill_id"):
            key = ("exit", exit_fill.get("exit_norenordno"), exit_fill["fill_id"])
            fact = (exit_fill.get("quantity"), exit_fill.get("price"))
        elif fill.get("event_time"):
            key = ("entry", event.get("norenordno"), fill["event_time"])
            fact = (fill.get("quantity"), fill.get("average_price"), fill.get("order_status"))
        if key:
            groups.setdefault(key, []).append((fact, event))
    for members in groups.values():
        if len({fact for fact, _ in members}) > 1:
            for _, event in members:
                event["evidence_state"] = "conflicting"
                event["reasons"] = sorted(set(event["reasons"]) | {"invalid_fill"})
    return result


async def attach_evidence(db, orders, items):
    gate = asyncio.Semaphore(8)

    async def attach(order, item):
        unavailable = {"state": "unavailable", "events": [], "truncated": False}
        item["durable_evidence"] = unavailable
        query = [{k: str(order[k])} for k in ("norenordno", "client_order_id") if order.get(k)]
        if not query:
            return
        async with gate:
            try:
                cursor = db.execution_receipt_events.find({"$or": query}, {"_id": 0}).sort("recorded_at", -1)
                rows = await asyncio.wait_for(cursor.to_list(length=EVENT_LIMIT + 1), timeout=1.5)
                item["durable_evidence"] = {"state": "available",
                                            "events": safe_events(rows[:EVENT_LIMIT], order),
                                            "truncated": len(rows) > EVENT_LIMIT}
            except Exception:
                pass  # No raw exception/record leakage; unknown stays explicit.
    await asyncio.gather(*(attach(order, item) for order, item in zip(orders, items)))
