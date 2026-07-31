"""Dashboard summary must be small and preserve the true KPI envelope.

The dashboard needs only scalar presentation fields.  A premium-native run's
real result is under ``option_backtest``; excluding only root-level spot arrays
silently sent its much larger nested trade and equity arrays over every page
load.  These tests emulate Mongo's inclusion/exclusion projection so the route
contract, not just its source text, is exercised.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import sys
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def _get_path(doc: dict[str, Any], path: str) -> tuple[bool, Any]:
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, deepcopy(node)


def _put_path(doc: dict[str, Any], path: str, value: Any) -> None:
    node = doc
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _mongo_projection(doc: dict[str, Any], projection: dict[str, int]) -> dict[str, Any]:
    """Enough Mongo projection semantics to make this route contract behavioural."""
    includes = [path for path, enabled in projection.items() if path != "_id" and enabled]
    if includes:
        out: dict[str, Any] = {}
        for path in includes:
            found, value = _get_path(doc, path)
            if found:
                _put_path(out, path, value)
        return out
    out = deepcopy(doc)
    for path, enabled in projection.items():
        if path == "_id" or enabled:
            continue
        parts = path.split(".")
        node: Any = out
        for part in parts[:-1]:
            node = node.get(part, {}) if isinstance(node, dict) else {}
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    return out


class _Runs:
    def __init__(self, latest: dict[str, Any]):
        self.latest = latest
        self.projection: dict[str, int] | None = None

    async def count_documents(self, _query: dict[str, Any]) -> int:
        return 1

    async def find_one(self, _query: dict[str, Any], projection: dict[str, int], **_kwargs: Any) -> dict[str, Any]:
        self.projection = projection
        return _mongo_projection(self.latest, projection)


class _Db:
    def __init__(self, latest: dict[str, Any]):
        self.backtest_runs = _Runs(latest)


class _Registry:
    def list_all(self) -> list[dict[str, bool]]:
        return [{"is_loaded": True}]


def _premium_run() -> dict[str, Any]:
    return {
        "id": "premium-latest",
        "created_at": "2026-08-01T00:00:00+00:00",
        "name": "premium native",
        "instrument": "NIFTY",
        "strategy_id": "premium_momentum",
        "status": "done",
        "config": {"mode": "SCALP", "params": {"momentum_pct": 20}},
        "metrics": {"trade_count": 0, "total_pnl_pts": 0},
        "significance": {"badge": "SIGNIFICANT"},
        "regime_distribution": {"TREND": 2},
        "trades": [{"large": "spot-trade"}],
        "equity_curve": [{"large": "spot-equity"}],
        "walkforward": {"folds": [{"large": "walk-forward"}]},
        "option_backtest": {
            "enabled": True,
            "dispatch": "premium_trigger_config",
            "metrics": {"paired_trade_count": 2, "win_rate": 50.0,
                        "profit_factor": 1.25, "total_option_pnl_value": 2500.0},
            "trades": [{"large": "premium-trade"}],
            "equity_curve": [{"large": "premium-equity"}],
            "portfolio": {
                "starting_capital": 100000.0,
                "ending_equity": 102500.0,
                "net_pnl_value": 2500.0,
                "max_drawdown_value": -750.0,
                "sharpe_daily": 1.2,
                "total_return_pct": 2.5,
                "curve": [{"large": "portfolio-curve"}],
            },
        },
    }


def test_dashboard_summary_is_bounded_and_keeps_premium_kpi_scalars(monkeypatch):
    from app.routers import journals

    db = _Db(_premium_run())
    monkeypatch.setattr(journals, "get_db", lambda: db)
    monkeypatch.setattr(journals, "get_coverage", _coverage)
    monkeypatch.setattr(journals, "get_registry", lambda: _Registry())

    result = asyncio.run(journals.dashboard_summary())
    latest = result["latest_backtest"]
    option = latest["option_backtest"]

    assert latest["config"] == {"mode": "SCALP"}
    assert option["dispatch"] == "premium_trigger_config"
    assert option["metrics"]["profit_factor"] == 1.25
    assert option["portfolio"]["net_pnl_value"] == 2500.0
    assert option["portfolio"]["sharpe_daily"] == 1.2
    for forbidden in ("trades", "equity_curve", "walkforward"):
        assert forbidden not in latest
        assert forbidden not in option
    assert "curve" not in option["portfolio"]
    assert "config.params" not in db.backtest_runs.projection
    assert all(value in (0, 1) for value in db.backtest_runs.projection.values())
    assert db.backtest_runs.projection["option_backtest.portfolio.net_pnl_value"] == 1


async def _coverage() -> dict[str, dict[str, int]]:
    return {"NIFTY": {"candle_count": 375}}
