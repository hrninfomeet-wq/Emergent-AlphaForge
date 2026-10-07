"""Mongo persistence for the scalper: decision/order events, closed paper trades, engine snapshots.

Collections (no TTL, no BSON date fields — timestamps are epoch-ms integers):
  scalp_events          every engine event (signal_filtered, place, ack, fill, cancel, state, exit_decision,
                        trade_closed, halt, pause, reconcile_*) with mode + session_date; the latency source
  scalp_paper_trades    one row per closed paper round trip (entry/exit avg, charges, net, timings, orders)
  scalp_engine_state    one snapshot per (strategy_id, session_date) for restart recovery
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


async def ensure_indexes(db) -> None:
    await db.scalp_events.create_index([("strategy_id", 1), ("session_date", 1), ("ts_ms", 1)])
    await db.scalp_events.create_index([("kind", 1), ("ts_ms", -1)])
    await db.scalp_paper_trades.create_index([("strategy_id", 1), ("session_date", 1), ("closed_ms", 1)])
    await db.scalp_engine_state.create_index([("strategy_id", 1), ("session_date", 1)], unique=True)


def _clean(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in doc.items():
        if isinstance(v, float) and v != v:      # NaN -> None (BSON-safe, never a fake number)
            v = None
        out[k] = v
    return out


async def write_events(db, events: Iterable[Dict[str, Any]], *, mode: str, session_date: str) -> int:
    docs = [_clean({**e, "mode": mode, "session_date": session_date}) for e in events]
    if docs:
        await db.scalp_events.insert_many(docs, ordered=False)
    return len(docs)


async def write_trades(db, trades: List[Dict[str, Any]], *, mode: str, session_date: str) -> int:
    docs = [_clean({**t, "mode": mode, "session_date": session_date}) for t in trades]
    if docs:
        await db.scalp_paper_trades.insert_many(docs, ordered=False)
    return len(docs)


async def save_snapshot(db, strategy_id: str, session_date: str, snap: Dict[str, Any], now_ms: int) -> None:
    await db.scalp_engine_state.update_one(
        {"strategy_id": strategy_id, "session_date": session_date},
        {"$set": {"snapshot": snap, "saved_ms": int(now_ms)}}, upsert=True)


async def load_snapshot(db, strategy_id: str, session_date: str):
    doc = await db.scalp_engine_state.find_one({"strategy_id": strategy_id, "session_date": session_date})
    return (doc or {}).get("snapshot")
