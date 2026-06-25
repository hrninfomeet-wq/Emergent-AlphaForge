"""Tests for GET /live-broker/blotter route wiring (slice 2, item 3).

Verifies the route fetches live_trades (recent), joins the live broker position
book for P&L, resolves deployment names, and degrades gracefully when the broker
is unreachable. The join math is covered in test_live_blotter.py; here we lock
the route assembling its inputs correctly.

RULES: never instantiate a real FlattradeClient; patch the module getters.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch, AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.live_broker as _routes  # noqa: E402


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self._docs = list(docs)

    def sort(self, *_args, **_kw) -> "_Cursor":
        # Production sorts by created_at desc in Mongo; the pure builder re-sorts
        # newest-first regardless, so the fake can be order-agnostic.
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self._docs) if length is None else list(self._docs[:length])


class _Collection:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self._docs = docs

    def find(self, query=None, projection=None) -> _Cursor:  # noqa: ARG002
        query = query or {}
        # support {"id": {"$in": [...]}} for the deployment name lookup
        idspec = query.get("id")
        if isinstance(idspec, dict) and "$in" in idspec:
            wanted = set(idspec["$in"])
            return _Cursor([d for d in self._docs if d.get("id") in wanted])
        return _Cursor(self._docs)


class _DB:
    def __init__(self, *, trades, deployments) -> None:
        self.live_trades = _Collection(trades)
        self.strategy_deployments = _Collection(deployments)


class _FakeClient:
    def __init__(self, positions: List[Dict[str, Any]]) -> None:
        self._positions = positions

    async def position_book(self) -> List[Dict[str, Any]]:
        return list(self._positions)


def _blotter(*, trades, positions, deployments, connected=True, limit=None):
    app = FastAPI()
    app.include_router(_routes.api)
    db = _DB(trades=trades, deployments=deployments)
    # _get_client lives on the router; the route does a deferred `from app.db
    # import get_db`, so patch get_db at its source module.
    client_patch = patch.object(
        _routes, "_get_client",
        AsyncMock(return_value=_FakeClient(positions)) if connected
        else AsyncMock(side_effect=Exception("not connected")),
    )
    import app.db as _dbmod
    db_patch = patch.object(_dbmod, "get_db", lambda: db)
    client_patch.start()
    db_patch.start()
    try:
        tc = TestClient(app, raise_server_exceptions=True)
        url = "/live-broker/blotter" + (f"?limit={limit}" if limit else "")
        resp = tc.get(url)
        assert resp.status_code == 200, resp.text
        return resp.json()
    finally:
        client_patch.stop()
        db_patch.stop()


_TRADE = dict(
    id="t1", created_at="2026-06-25T04:00:00+00:00", deployment_id="dep1",
    strategy_id="orb", instrument="NIFTY", trading_symbol="NIFTY24JUN24000CE",
    direction="LONG", lots=2, quantity=150, entry_price=120.0, norenordno="N1",
)
_DEP = {"id": "dep1", "name": "ORB · NIFTY", "strategy_id": "orb", "instrument": "NIFTY"}
_POS = {"tsym": "NIFTY24JUN24000CE", "netqty": "150", "lp": "135", "urmtom": "2250", "rpnl": "0"}


def test_route_joins_broker_pnl_and_deployment_name():
    body = _blotter(trades=[dict(_TRADE)], positions=[dict(_POS)], deployments=[dict(_DEP)])
    assert body["count"] == 1
    r = body["rows"][0]
    assert r["status"] == "LIVE"
    assert r["pnl"] == 2250.0
    assert r["deployment_name"] == "ORB · NIFTY"


def test_route_degrades_when_broker_disconnected():
    body = _blotter(trades=[dict(_TRADE)], positions=[], deployments=[dict(_DEP)], connected=False)
    assert body["count"] == 1
    r = body["rows"][0]
    assert r["status"] == "FLAT"          # no broker book → not held
    assert r["pnl"] is None               # no fabricated P&L
    assert r["deployment_name"] == "ORB · NIFTY"  # attribution still present


def test_route_empty_when_no_trades():
    body = _blotter(trades=[], positions=[dict(_POS)], deployments=[dict(_DEP)])
    assert body == {"rows": [], "count": 0}
