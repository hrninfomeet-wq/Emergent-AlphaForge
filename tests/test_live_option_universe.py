from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))


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

    async def distinct(self, key: str, query: Optional[Dict[str, Any]] = None):
        seen = []
        for row in self.rows:
            if not _matches(row, query or {}):
                continue
            value = row.get(key)
            if value is not None and value not in seen:
                seen.append(value)
        return seen


class FakeDb:
    def __init__(self):
        self.option_contracts = FakeCollection()
        self.candles_1m = FakeCollection()


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                return False
            continue
        if actual != expected:
            return False
    return True


def _contracts(expiry: str, atm: int = 24000) -> List[Dict[str, Any]]:
    rows = []
    for strike in (atm - 100, atm - 50, atm, atm + 50, atm + 100):
        for side in ("CE", "PE"):
            rows.append({
                "underlying": "NIFTY",
                "expiry_date": expiry,
                "strike": strike,
                "side": side,
                "instrument_key": f"NSE_FO|NIFTY|{expiry}|{strike}|{side}",
                "trading_symbol": f"NIFTY {strike} {side} {expiry}",
            })
    return rows


def test_build_live_option_universe_uses_stream_spot_and_next_expiry():
    from app.live_option_universe import build_live_option_universe

    db = FakeDb()
    db.option_contracts.rows = _contracts("2026-06-04") + _contracts("2026-06-11")

    result = asyncio.run(build_live_option_universe(
        db,
        latest_ticks={"NSE_INDEX|Nifty 50": {"last_price": 24012.5, "received_ts": 1780271100000}},
        underlyings=["NIFTY"],
        radius=1,
        max_option_keys=20,
        today="2026-06-01",
    ))

    assert result["option_key_count"] == 6
    assert result["underlyings"][0]["spot_source"] == "stream_tick"
    assert result["underlyings"][0]["expiry_date"] == "2026-06-04"
    assert all("2026-06-04" in key for key in result["instrument_keys"])
    assert "NSE_FO|NIFTY|2026-06-04|24000|CE" in result["instrument_keys"]


def test_normalize_underlyings_does_not_expand_invalid_explicit_input():
    from app.live_option_universe import DEFAULT_LIVE_OPTION_UNDERLYINGS, normalize_underlyings

    assert normalize_underlyings(None) == DEFAULT_LIVE_OPTION_UNDERLYINGS
    assert normalize_underlyings(["NIFTY", "nifty", "FOO"]) == ["NIFTY"]
    assert normalize_underlyings(["FOO"]) == []


def test_build_live_option_universe_falls_back_to_latest_spot_candle():
    from app.live_option_universe import build_live_option_universe

    db = FakeDb()
    db.option_contracts.rows = _contracts("2026-06-04")
    db.candles_1m.rows = [
        {"instrument": "NIFTY", "ts": 1780271040000, "close": 23911.2},
        {"instrument": "NIFTY", "ts": 1780271100000, "close": 23962.4},
    ]

    result = asyncio.run(build_live_option_universe(
        db,
        latest_ticks={},
        underlyings=["NIFTY"],
        radius=0,
        max_option_keys=20,
        today="2026-06-01",
    ))

    assert result["option_key_count"] == 2
    assert result["underlyings"][0]["spot_source"] == "candles_1m"
    assert result["underlyings"][0]["spot_price"] == 23962.4
    assert result["underlyings"][0]["atm"] == 23950


def test_backend_exposes_live_option_stream_routes():
    server = backend_api_text()
    api = open(os.path.join(ROOT, "frontend", "src", "lib", "api.js"), encoding="utf-8").read()

    assert '@api.get("/upstox/stream/options/universe")' in server
    assert '@api.post("/upstox/stream/options/restart")' in server
    assert "upstoxOptionStreamUniverse" in api
    assert "restartUpstoxOptionStream" in api


# ---------------------------------------------------------------------------
# radius_for_deployments — live universe auto-follows deployment moneyness
# ---------------------------------------------------------------------------

from app.live_option_universe import radius_for_deployments  # noqa: E402
from tests.contract_corpus import backend_api_text


def test_radius_default_is_one_with_no_deployments():
    assert radius_for_deployments([]) == 1
    assert radius_for_deployments(None) == 1


def test_radius_covers_widest_moneyness_plus_drift_headroom():
    deps = [
        {"option_policy": {"moneyness": ["atm"]}},
        {"option_policy": {"moneyness": ["otm1"]}},
    ]
    assert radius_for_deployments(deps) == 2  # otm1 offset 1 + 1 headroom
    deps.append({"option_policy": {"moneyness": ["itm2", "atm"]}})
    assert radius_for_deployments(deps) == 3  # itm2 offset 2 + 1


def test_radius_clamps_to_five_and_ignores_unknown():
    deps = [{"option_policy": {"moneyness": ["otm3", "weird9"]}}] * 3
    assert radius_for_deployments(deps) == 4  # otm3 offset 3 + 1
    deps.append({"option_policy": {"moneyness": ["otm3", "itm3"]}})
    assert radius_for_deployments(deps) == 4
    # unknown-only policies fall back to the minimum useful radius
    assert radius_for_deployments([{"option_policy": {"moneyness": ["weird"]}}]) == 1


# ---------------------------------------------------------------------------
# Market-hours baseline: the evaluator loop keeps an ATM±3 option universe
# subscribed automatically (no manual stream restart), even with no deployments.
# server.py can't be imported on the host (motor); assert on its source text.
# ---------------------------------------------------------------------------


def test_market_hours_loop_wires_baseline_option_stream_follow():
    server = backend_api_text()
    # A baseline radius constant exists and covers ATM±3 for the chain snapshot.
    assert "OPTION_CHAIN_BASELINE_RADIUS = 3" in server
    # The market-hours loop calls auto-follow with that baseline floor.
    assert "_auto_follow_option_stream(min_radius=OPTION_CHAIN_BASELINE_RADIUS)" in server
    # Auto-follow takes a min_radius floor and is idempotent (skips redundant restarts).
    assert "async def _auto_follow_option_stream(min_radius: int = 0)" in server
    assert "already_covered" in server
