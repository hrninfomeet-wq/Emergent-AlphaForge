"""The /deployments/overview aggregation, executed by a REAL Mongo.

No other test runs this pipeline — the rest pin it by AST — and an aggregation's
meaning lives in the server that evaluates it. Runs against a THROWAWAY database
on 127.0.0.1:27017 when one is reachable (the dev box always has it), and skips
cleanly otherwise. It never touches the `alphaforge` database.

Pins two truths the command-centre cards used to get wrong:
  * a LIVE open row with a missing or stale guard mark (> MARK_STALE_AFTER_SECONDS)
    is not today's MTM — its frozen P&L is excluded and counted as unverified;
  * a close whose exit day is unknown (a stale doc reconciled days later) is not
    today's realized P&L.
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _mongo_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 27017), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _mongo_up(), reason="no local Mongo on 127.0.0.1:27017")

SCRATCH = "alphaforge_scratch_overview_test"


def test_the_overview_counts_only_verified_live_mtm(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient

    import app.routers.deployments as dep

    async def run():
        client = AsyncIOMotorClient("mongodb://127.0.0.1:27017",
                                    serverSelectionTimeoutMS=2000)
        db = client[SCRATCH]
        assert db.name != "alphaforge"
        await client.drop_database(SCRATCH)
        try:
            now = datetime.now(timezone.utc)
            fresh = (now - timedelta(seconds=10)).isoformat()
            stale = (now - timedelta(minutes=30)).isoformat()
            await db.strategy_deployments.insert_many([
                {"id": "L1", "name": "live", "mode": "live", "status": "ACTIVE",
                 "created_at": now.isoformat()},
                {"id": "P1", "name": "paper", "mode": "paper", "status": "ACTIVE",
                 "created_at": now.isoformat()},
            ])
            await db.live_trades.insert_many([
                {"deployment_id": "L1", "status": "OPEN", "unrealized_pnl": -100.0,
                 "marked_at": fresh},
                {"deployment_id": "L1", "status": "OPEN", "unrealized_pnl": -853.0,
                 "marked_at": stale},
                {"deployment_id": "L1", "status": "OPEN", "unrealized_pnl": -50.0},
                {"deployment_id": "L1", "status": "CLOSED", "realized_pnl": 500.0,
                 "closed_at": now.isoformat()},
                {"deployment_id": "L1", "status": "CLOSED", "realized_pnl": -9999.0,
                 "closed_at": now.isoformat(), "exit_day_unknown": True},
            ])
            # Paper trades carry no guard marks: their open P&L is unchanged.
            await db.paper_trades.insert_one(
                {"deployment_id": "P1", "status": "OPEN", "unrealized_pnl": -25.0})
            monkeypatch.setattr(dep, "get_db", lambda: db)
            return await dep.deployments_overview()
        finally:
            await client.drop_database(SCRATCH)

    out = asyncio.run(run())
    by_id = {i["deployment"]["id"]: i["today"] for i in out["items"]}
    live, paper = by_id["L1"], by_id["P1"]
    assert live["open_trades"] == 3
    assert live["open_unrealized"] == -100.0       # was -1003: stale marks summed
    assert live["open_unverified"] == 2
    assert live["realized_pnl"] == 500.0           # was -9499: exit day unknown
    assert paper["open_unrealized"] == -25.0 and paper["open_unverified"] == 0
