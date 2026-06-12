"""Tests for the Upstox intraday backfill that closes today's morning gap."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from tests.contract_corpus import backend_api_text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

ROOT = Path(__file__).resolve().parents[1]


def test_intraday_fetch_and_backfill_route_are_wired():
    """The intraday client fn and the backfill route must exist and be reachable."""
    client = (ROOT / "backend" / "app" / "upstox_client.py").read_text(encoding="utf-8")
    server = backend_api_text()

    # Client uses the Upstox INTRADAY endpoint (serves the current day, unlike
    # the historical endpoint which is empty for today).
    assert "async def fetch_intraday_1m" in client
    assert "/v3/historical-candle/intraday/" in client

    # Route persists via the same warehouse path (candles + integrity hashes).
    assert '@api.post("/warehouse/intraday-backfill/{instrument}")' in server
    assert "fetch_intraday_1m" in server
    assert "persist_candles_df" in server
