"""Container test for POST /premium-momentum/backtest (Task 1.4).

This is an I/O test: it mounts the real router and hits the real warehouse
(motor -> mongo) inside the container. The premium-momentum SIM math is unit-
tested in tests/test_premium_momentum_backtest.py; here we lock the route
WIRING — that it loads spot candles, resolves the weekly expiry, locks the
strikes, loads their full-day options_1m series, calls the sim, and returns
its {trades, coverage}.

Assertion is STRUCTURAL (has trades + coverage, sessions_total is an int) so
it holds whether or not the chosen range has premium coverage in this DB.

The whole request runs inside one asyncio.run loop via httpx + ASGITransport
(not Starlette's TestClient portal) so the module-global motor client binds to
the SAME loop it is awaited on — TestClient tears its loop down between calls,
which closes motor's loop out from under a real DB query.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict

import pandas as pd
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.db as _dbmod  # noqa: E402
import app.routers.premium_momentum_routes as _routes  # noqa: E402


def _ms(ist: str) -> int:
    return int(pd.Timestamp(ist, tz="Asia/Kolkata").value // 1_000_000)


def _post(body: Dict[str, Any]) -> Dict[str, Any]:
    async def _run() -> Dict[str, Any]:
        # Bind a fresh motor client to THIS event loop.
        _dbmod._client = None
        _dbmod._db = None
        app = FastAPI()
        app.include_router(_routes.api)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.post("/premium-momentum/backtest", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()

    return asyncio.run(_run())


def test_route_runs_a_real_warehouse_backtest():
    out = _post({
        "instrument": "NIFTY",
        # A small in-warehouse range: Mon + weekly-expiry Tue.
        "start_ts": _ms("2026-06-15 00:00"),
        "end_ts": _ms("2026-06-16 23:59"),
        "params": {
            "reference_time": "09:31", "moneyness": "itm1",
            "side": "first_to_trigger", "momentum_pct": 15.0,
            "target_pct": 50.0, "stop_pct": 20.0,
        },
    })

    # Structure: the sim's contract, surfaced by the route.
    assert isinstance(out.get("trades"), list)
    cov = out.get("coverage")
    assert isinstance(cov, dict)
    for key in ("sessions_total", "sessions_traded", "sessions_excluded",
                "sessions_no_signal", "exclude_reasons"):
        assert key in cov, f"coverage missing {key}"
    assert isinstance(cov["sessions_total"], int)
    # Spot candles exist for this range -> a real run saw >=1 session.
    assert cov["sessions_total"] >= 1
    # Coverage accounting is exhaustive (never a mis-fill: every session is
    # traded, excluded, or no-signal).
    assert cov["sessions_total"] == (
        cov["sessions_traded"] + cov["sessions_excluded"] + cov["sessions_no_signal"]
    )


def test_route_empty_range_returns_zeroed_coverage():
    """A range with no spot candles returns a well-formed zeroed report, not 500."""
    out = _post({
        "instrument": "NIFTY",
        "start_ts": _ms("2019-01-01 00:00"),
        "end_ts": _ms("2019-01-02 00:00"),
        "params": {"reference_time": "09:31", "moneyness": "itm1"},
    })
    assert out["trades"] == []
    assert out["coverage"]["sessions_total"] == 0
