"""Strategy read + lifecycle routes (list/get/retire/un-retire/delete/reload).

Host-importable: heavy deps (motor DB, square-off, deployment status) are behind
module-level seams that import lazily, so router tests can patch them without
importing motor. Mirrors the isolation pattern in app/routers/live_broker.py.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.strategies.base import get_registry

api = APIRouter()
log = logging.getLogger(__name__)


def _db():
    from app.db import get_db  # lazy: app.db imports motor at top
    return get_db()


def _delete_plugin_file(strategy_id: str) -> bool:
    """Remove the .py for a custom plugin. Returns True if a file was removed.
    Only deletes files physically under .../strategies/plugins/ as a safety net."""
    from app.strategy_source_hash import strategy_file_path
    s = get_registry().get(strategy_id)
    if s is None:
        return False
    path = strategy_file_path(s)
    plugins_marker = os.path.join("strategies", "plugins")
    if path and os.path.isfile(path) and plugins_marker in path:
        os.remove(path)
        return True
    return False


async def _square_off_strategy_deployments(strategy_id: str) -> List[Dict[str, Any]]:
    """Pause + scoped square-off every ACTIVE deployment of a strategy.
    Lazily imports the heavy deployment/paper modules."""
    from app.paper_squareoff import square_off_open_paper_trades
    from app.runtime import _set_deployment_status, upstox_stream_manager
    db = _db()
    active = await db.strategy_deployments.find(
        {"strategy_id": strategy_id, "status": "ACTIVE"}, {"_id": 0, "id": 1}
    ).to_list(length=None)
    summaries: List[Dict[str, Any]] = []
    for d in active:
        s = await square_off_open_paper_trades(
            db, deployment_id=d["id"],
            latest_tick_lookup=upstox_stream_manager.latest_tick_map().get,
            reason="manual_retire",
        )
        summaries.extend(s)
        await _set_deployment_status(d["id"], "PAUSED")
    return summaries


@api.get("/strategies")
async def list_strategies():
    items = get_registry().list_all()
    db = _db()
    rows = await db.strategy_lifecycle.find({"retired": True}, {"_id": 0}).to_list(length=None)
    retired = {r["strategy_id"] for r in rows}
    for it in items:
        it["is_retired"] = it["id"] in retired
    return {"items": items}


@api.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: str):
    s = get_registry().get(strategy_id)
    if not s:
        raise HTTPException(404, f"Strategy {strategy_id} not found")
    meta = s.meta()
    life = await _db().strategy_lifecycle.find_one({"strategy_id": strategy_id}, {"_id": 0})
    meta["is_retired"] = bool(life and life.get("retired"))
    return meta


def _exists(strategy_id: str) -> bool:
    reg = get_registry()
    return reg.get(strategy_id) is not None or reg.origin_of(strategy_id) is not None


@api.post("/strategies/{strategy_id}/retire")
async def retire_strategy(strategy_id: str):
    if not _exists(strategy_id):
        raise HTTPException(404, f"Strategy {strategy_id} not found")
    summaries = await _square_off_strategy_deployments(strategy_id)
    now = datetime.now(timezone.utc).isoformat()
    await _db().strategy_lifecycle.update_one(
        {"strategy_id": strategy_id},
        {"$set": {"strategy_id": strategy_id, "retired": True, "retired_at": now}},
        upsert=True,
    )
    return {"strategy_id": strategy_id, "retired": True,
            "squared_off": summaries, "squared_off_count": len(summaries)}


@api.post("/strategies/{strategy_id}/un-retire")
async def unretire_strategy(strategy_id: str):
    await _db().strategy_lifecycle.update_one(
        {"strategy_id": strategy_id},
        {"$set": {"strategy_id": strategy_id, "retired": False, "retired_at": None}},
        upsert=True,
    )
    return {"strategy_id": strategy_id, "retired": False}
