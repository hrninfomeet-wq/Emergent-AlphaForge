from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from tests.contract_corpus import backend_api_text


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))


IST = timezone(timedelta(hours=5, minutes=30))


class FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)

    def sort(self, key, direction: int = 1):
        if isinstance(key, list):
            for field, order in reversed(key):
                self._rows.sort(key=lambda row: row.get(field), reverse=(order == -1))
        else:
            self._rows.sort(key=lambda row: row.get(key), reverse=(direction == -1))
        return self

    def limit(self, n: int):
        self._rows = self._rows[: int(n)]
        return self

    async def to_list(self, length: Optional[int] = None):
        return list(self._rows if length is None else self._rows[: int(length)])


class FakeCollection:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self.rows = list(rows or [])

    def find(self, query: Optional[Dict[str, Any]] = None, projection: Optional[Dict[str, Any]] = None):
        return FakeCursor([dict(row) for row in self.rows if _matches(row, query or {})])

    async def find_one(self, query: Dict[str, Any], projection: Optional[Dict[str, Any]] = None):
        for row in self.rows:
            if _matches(row, query):
                return dict(row)
        return None


class FakeDb:
    def __init__(self):
        self.candles_1m = FakeCollection()
        self.paper_trades = FakeCollection()


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$lt" in expected and (actual is None or actual >= expected["$lt"]):
                return False
            continue
        if actual != expected:
            return False
    return True


def _ist_ms(day: str, hh: int, mm: int) -> int:
    dt = datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00+05:30")
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _session_candles(day: str, count: int, *, instrument: str = "NIFTY") -> List[Dict[str, Any]]:
    rows = []
    start = datetime.fromisoformat(f"{day}T10:00:00+05:30")
    for i in range(count):
        ist_dt = start + timedelta(minutes=i)
        rows.append({
            "instrument": instrument,
            "ts": int(ist_dt.astimezone(timezone.utc).timestamp() * 1000),
            "close": 24000 + i,
        })
    return rows


def _closed_trade(deployment_id: str, day: str, pnl: float, trade_id: str) -> Dict[str, Any]:
    return {
        "id": trade_id,
        "deployment_id": deployment_id,
        "status": "CLOSED",
        "realized_pnl": pnl,
        "created_at": f"{day}T10:30:00+05:30",
        "closed_at": f"{day}T11:00:00+05:30",
    }


def test_forward_metrics_uses_complete_sessions_for_trade_stats():
    from app.forward_metrics import compute_forward_metrics_for_deployment

    db = FakeDb()
    deployment = {
        "id": "dep-1",
        "name": "NIFTY paper",
        "strategy_id": "confluence_scalper",
        "instrument": "NIFTY",
        "mode": "paper",
        "created_at": "2026-05-15T09:00:00+05:30",
    }
    complete_days = [
        "2026-05-15",
        "2026-05-18",
        "2026-05-19",
        "2026-05-20",
        "2026-05-21",
        "2026-05-22",
        "2026-05-25",
        "2026-05-26",
        "2026-05-27",
        "2026-05-29",
    ]
    for day in complete_days:
        db.candles_1m.rows.extend(_session_candles(day, 210))
    db.candles_1m.rows.extend(_session_candles("2026-06-01", 120))
    db.paper_trades.rows.extend([
        _closed_trade("dep-1", "2026-05-15", 1000, "t1"),
        _closed_trade("dep-1", "2026-05-18", -500, "t2"),
        _closed_trade("dep-1", "2026-05-19", 1500, "t3"),
        _closed_trade("dep-1", "2026-06-01", 999, "t4"),
    ])

    result = asyncio.run(compute_forward_metrics_for_deployment(
        db,
        deployment,
        today="2026-06-01",
    ))

    assert result["deployment_id"] == "dep-1"
    assert result["session_completeness"]["complete_session_count"] == 10
    assert result["session_completeness"]["partial_session_count"] == 1
    assert result["session_completeness"]["threshold_minutes"] == 210
    assert result["trade_count"] == 3
    assert result["excluded_incomplete_session_trade_count"] == 1
    assert result["win_rate"] == 66.67
    assert result["avg_pnl"] == 666.67
    assert result["profit_factor"] == 5.0
    assert result["library_gate"]["visible"] is True


def test_forward_metrics_hides_strategy_library_until_ten_complete_sessions():
    from app.forward_metrics import compute_forward_metrics_for_deployment

    db = FakeDb()
    deployment = {
        "id": "dep-2",
        "name": "Collecting sessions",
        "strategy_id": "vwap_pullback_scalp",
        "instrument": "NIFTY",
        "mode": "paper",
        "created_at": "2026-05-15T09:00:00+05:30",
    }
    for day in [
        "2026-05-15",
        "2026-05-18",
        "2026-05-19",
        "2026-05-20",
        "2026-05-21",
        "2026-05-22",
        "2026-05-25",
        "2026-05-26",
        "2026-05-27",
    ]:
        db.candles_1m.rows.extend(_session_candles(day, 300))
    db.paper_trades.rows.append(_closed_trade("dep-2", "2026-05-15", 750, "t1"))

    result = asyncio.run(compute_forward_metrics_for_deployment(
        db,
        deployment,
        today="2026-06-01",
    ))

    assert result["session_completeness"]["complete_session_count"] == 9
    assert result["trade_count"] == 1
    assert result["library_gate"] == {
        "visible": False,
        "min_complete_sessions": 10,
        "reason": "needs_10_complete_sessions",
    }


def test_backend_and_frontend_expose_forward_metrics():
    server = backend_api_text()
    api = open(os.path.join(ROOT, "frontend", "src", "lib", "api.js"), encoding="utf-8").read()
    library = open(os.path.join(ROOT, "frontend", "src", "pages", "StrategyLibrary.jsx"), encoding="utf-8").read()

    assert '@api.get("/deployments/metrics")' in server
    assert '@api.get("/deployments/{deployment_id}/metrics")' in server
    assert "listDeploymentMetrics" in api
    assert "forward-metrics-block" in library
