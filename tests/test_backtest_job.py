"""Pin the Phase-1 backtest-as-job conversion (async fire-and-forget + poll).

The synchronous /backtest/run blocked the event loop and could double-run on an
HTTP retry. /backtest/start inserts the run doc up front (crash-visible), offloads
the heavy compute via asyncio.to_thread, returns a run_id instantly, and the UI
polls the existing GET /backtest/runs/{id}. The legacy /backtest/run is kept.
"""
from __future__ import annotations

from pathlib import Path

from tests.contract_corpus import backend_api_text

FE = Path(__file__).resolve().parents[1] / "frontend" / "src"


def _read(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def test_backend_start_endpoint_and_worker():
    src = backend_api_text()
    assert '"/backtest/start"' in src               # new fire-and-forget endpoint
    assert "async def run_backtest_job" in src       # background worker
    assert "asyncio.to_thread" in src                # heavy compute off the event loop
    assert "asyncio.create_task(run_backtest_job" in src
    assert '"status": "running"' in src              # doc inserted up front (crash-visible)
    assert '"status": "failed"' in src               # worker marks failures
    assert '"/backtest/run"' in src                  # legacy synchronous path kept


def test_frontend_starts_then_polls():
    apijs = _read("lib/api.js")
    assert "startBacktest" in apijs
    assert "/backtest/start" in apijs
    bl = _read("pages/BacktestLab.jsx")
    assert "api.startBacktest" in bl
    assert "getBacktestRun(run_id)" in bl
