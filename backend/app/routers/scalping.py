"""Scalper lab routes (paper / replay only — no route here can reach a broker).

  GET  /scalping/status                 runner + engine state, tape archive status
  GET  /scalping/config                 the pre-registered specs with units and provenance
  GET  /scalping/paper/trades?date=     closed paper round trips for an IST date
  GET  /scalping/events?date=&kind=     engine events (latency source), newest first, capped
  POST /scalping/paper/kill             kill switch for the PAPER engines (halts, exits, no auto-resume)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from app.db import get_db
from app.scalping.config import PRESETS

api = APIRouter()

_runner = None   # set by server startup (app.scalping.paper_runner.ScalpPaperRunner)


def set_runner(runner) -> None:
    global _runner
    _runner = runner


@api.get("/scalping/status")
async def scalping_status() -> Dict[str, Any]:
    from app.scalping.paper_runner import paper_enabled
    from app.scalping.recorder import ARCHIVE_COLLECTION, archive_enabled
    db = get_db()
    last = await db[ARCHIVE_COLLECTION].find_one({"stored_at_ms": {"$ne": None}}, {"stored_at_ms": 1},
                                                 sort=[("stored_at_ms", -1)])
    return {
        "real_money": "not implemented — paper/replay only",
        "paper": _runner.status() if _runner else {"enabled": paper_enabled(), "running": False},
        "tape_archive": {"enabled": archive_enabled(),
                         "docs": await db[ARCHIVE_COLLECTION].estimated_document_count(),
                         "latest_stored_at_ms": (last or {}).get("stored_at_ms")},
    }


@api.get("/scalping/config")
async def scalping_config() -> Dict[str, Any]:
    return {sid: cfg.to_dict() for sid, cfg in PRESETS.items()}


@api.get("/scalping/paper/trades")
async def scalping_trades(date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
                          strategy_id: Optional[str] = None) -> Dict[str, Any]:
    q: Dict[str, Any] = {"session_date": date}
    if strategy_id:
        q["strategy_id"] = strategy_id
    rows = [r async for r in get_db().scalp_paper_trades.find(q, {"_id": 0}).sort("closed_ms", 1)]
    net = round(sum(float(r.get("net_inr") or 0) for r in rows), 2)
    return {"date": date, "count": len(rows), "net_inr": net, "trades": rows}


@api.get("/scalping/events")
async def scalping_events(date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"), kind: Optional[str] = None,
                          limit: int = Query(500, ge=1, le=5000)) -> Dict[str, Any]:
    q: Dict[str, Any] = {"session_date": date}
    if kind:
        q["kind"] = kind
    rows = [r async for r in get_db().scalp_events.find(q, {"_id": 0}).sort("ts_ms", -1).limit(limit)]
    return {"date": date, "count": len(rows), "events": rows}


@api.post("/scalping/paper/kill")
async def scalping_kill(reason: str = "operator_kill") -> Dict[str, Any]:
    if _runner is None:
        return {"killed": 0, "note": "paper runner not running"}
    return {"killed": _runner.kill(reason[:80])}
