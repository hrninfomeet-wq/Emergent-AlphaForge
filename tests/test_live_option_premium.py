"""Tests for app.live.option_premium (pure, no I/O, no DB).

Covers:
  - match_contract: exact match, wrong strike/side/expiry → None,
    string-vs-float strike tolerance, ambiguous (>1 match) → None.
  - resolve_premium: fresh tick, stale tick fallback, tick=None + candle,
    both absent, non-finite tick prices skipped, non-finite candle skipped,
    never raises on garbage tick dict.
  - Light route test with all dependencies monkeypatched.
"""
from __future__ import annotations

import importlib
import math
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure backend/ is on sys.path (same pattern as all other test_live_*.py files)
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.live.option_premium import match_contract, resolve_premium

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _contract(strike=25000.0, side="CE", expiry_date="2026-06-26", instrument_key="NSE_FO|123"):
    return {
        "strike": strike,
        "side": side,
        "expiry_date": expiry_date,
        "instrument_key": instrument_key,
        "trading_symbol": "NIFTY26JUN25000CE",
        "underlying": "NIFTY",
    }


NOW = 1_700_000_000.0  # arbitrary epoch seconds


# ===========================================================================
# match_contract
# ===========================================================================

class TestMatchContract:
    def _contracts(self):
        return [_contract()]

    # ---- happy path ----

    def test_exact_match_returns_row(self):
        row = match_contract(
            self._contracts(),
            strike=25000.0,
            side="CE",
            expiry_date="2026-06-26",
        )
        assert row is not None
        assert row["instrument_key"] == "NSE_FO|123"

    # ---- wrong attribute → None ----

    def test_wrong_strike_returns_none(self):
        assert match_contract(
            self._contracts(), strike=25100.0, side="CE", expiry_date="2026-06-26"
        ) is None

    def test_wrong_side_returns_none(self):
        assert match_contract(
            self._contracts(), strike=25000.0, side="PE", expiry_date="2026-06-26"
        ) is None

    def test_wrong_expiry_returns_none(self):
        assert match_contract(
            self._contracts(), strike=25000.0, side="CE", expiry_date="2026-06-19"
        ) is None

    # ---- float-tolerance ----

    def test_string_strike_matches_float(self):
        row = match_contract(
            self._contracts(), strike="25000", side="CE", expiry_date="2026-06-26"
        )
        assert row is not None

    def test_float_strike_stored_as_int_matches(self):
        contracts = [_contract(strike=25000)]  # stored as int
        row = match_contract(contracts, strike=25000.0, side="CE", expiry_date="2026-06-26")
        assert row is not None

    def test_side_case_insensitive(self):
        row = match_contract(
            self._contracts(), strike=25000.0, side="ce", expiry_date="2026-06-26"
        )
        assert row is not None

    # ---- ambiguous ----

    def test_two_identical_rows_returns_none(self):
        contracts = [_contract(), _contract()]  # duplicates
        assert match_contract(
            contracts, strike=25000.0, side="CE", expiry_date="2026-06-26"
        ) is None

    def test_two_different_rows_one_match_returns_it(self):
        contracts = [
            _contract(strike=25000.0, side="CE", instrument_key="K1"),
            _contract(strike=25000.0, side="PE", instrument_key="K2"),
        ]
        row = match_contract(contracts, strike=25000.0, side="CE", expiry_date="2026-06-26")
        assert row is not None
        assert row["instrument_key"] == "K1"

    # ---- empty list ----

    def test_empty_contracts_returns_none(self):
        assert match_contract([], strike=25000.0, side="CE", expiry_date="2026-06-26") is None

    # ---- bad strike arg ----

    def test_non_numeric_strike_arg_returns_none(self):
        assert match_contract(
            self._contracts(), strike="abc", side="CE", expiry_date="2026-06-26"
        ) is None

    # ---- malformed row skipped gracefully ----

    def test_row_with_bad_strike_skipped(self):
        contracts = [
            {"strike": "bad", "side": "CE", "expiry_date": "2026-06-26", "instrument_key": "K1"},
            _contract(),
        ]
        row = match_contract(contracts, strike=25000.0, side="CE", expiry_date="2026-06-26")
        assert row is not None
        assert row["instrument_key"] == "NSE_FO|123"


# ===========================================================================
# resolve_premium
# ===========================================================================

class TestResolvePremium:

    # ---- fresh live tick ----

    def test_fresh_tick_returns_live_tick(self):
        tick = {"last_price": 84.5, "ts": NOW}
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=50.0, now_ts=NOW, max_age_sec=120
        )
        assert result["source"] == "live_tick"
        assert result["premium"] == 84.5
        assert result["fresh"] is True
        assert result["ts"] == NOW

    def test_tick_ts_exactly_at_boundary_is_fresh(self):
        tick = {"last_price": 10.0, "ts": NOW - 120}
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=None, now_ts=NOW, max_age_sec=120
        )
        assert result["source"] == "live_tick"

    # ---- stale tick falls back to candle ----

    def test_stale_tick_falls_back_to_candle(self):
        tick = {"last_price": 84.5, "ts": NOW - 300}  # 300s > 120s max
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=77.0, now_ts=NOW, max_age_sec=120
        )
        assert result["source"] == "last_candle"
        assert result["premium"] == 77.0
        assert result["fresh"] is False

    def test_stale_tick_no_candle_returns_none_source(self):
        tick = {"last_price": 84.5, "ts": NOW - 300}
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=None, now_ts=NOW, max_age_sec=120
        )
        assert result["source"] == "none"
        assert result["premium"] is None

    # ---- tick=None + candle ----

    def test_tick_none_candle_available_returns_candle(self):
        result = resolve_premium(
            instrument_key="K", tick=None, candle_close=84.5, now_ts=NOW
        )
        assert result["source"] == "last_candle"
        assert result["premium"] == 84.5
        assert result["fresh"] is False

    # ---- both absent ----

    def test_both_absent_returns_none_source(self):
        result = resolve_premium(
            instrument_key="K", tick=None, candle_close=None, now_ts=NOW
        )
        assert result["source"] == "none"
        assert result["premium"] is None
        assert result["fresh"] is False

    # ---- non-finite tick last_price skipped ----

    def test_tick_last_price_nan_skips_to_candle(self):
        tick = {"last_price": math.nan, "ts": NOW}
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=55.0, now_ts=NOW
        )
        assert result["source"] == "last_candle"

    def test_tick_last_price_inf_skips_to_candle(self):
        tick = {"last_price": math.inf, "ts": NOW}
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=55.0, now_ts=NOW
        )
        assert result["source"] == "last_candle"

    def test_tick_last_price_zero_skips(self):
        tick = {"last_price": 0, "ts": NOW}
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=55.0, now_ts=NOW
        )
        assert result["source"] == "last_candle"

    def test_tick_last_price_negative_skips(self):
        tick = {"last_price": -5.0, "ts": NOW}
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=55.0, now_ts=NOW
        )
        assert result["source"] == "last_candle"

    def test_tick_last_price_none_skips(self):
        tick = {"last_price": None, "ts": NOW}
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=55.0, now_ts=NOW
        )
        assert result["source"] == "last_candle"

    # ---- non-finite candle skipped ----

    def test_candle_close_nan_skips_to_none(self):
        result = resolve_premium(
            instrument_key="K", tick=None, candle_close=math.nan, now_ts=NOW
        )
        assert result["source"] == "none"

    def test_candle_close_zero_skips(self):
        result = resolve_premium(
            instrument_key="K", tick=None, candle_close=0, now_ts=NOW
        )
        assert result["source"] == "none"

    def test_candle_close_negative_skips(self):
        result = resolve_premium(
            instrument_key="K", tick=None, candle_close=-1.0, now_ts=NOW
        )
        assert result["source"] == "none"

    # ---- garbage tick dict — never raises ----

    def test_empty_tick_dict_does_not_raise(self):
        result = resolve_premium(
            instrument_key="K", tick={}, candle_close=42.0, now_ts=NOW
        )
        # no last_price → skipped; candle wins
        assert result["source"] == "last_candle"

    def test_tick_with_string_last_price_accepted(self):
        tick = {"last_price": "88.5", "ts": NOW}
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=42.0, now_ts=NOW
        )
        assert result["source"] == "live_tick"
        assert result["premium"] == pytest.approx(88.5)

    def test_tick_ts_from_received_ts_used_as_fallback(self):
        """When 'ts' absent, 'received_ts' is used for freshness."""
        tick = {"last_price": 72.0, "received_ts": NOW - 10}
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=42.0, now_ts=NOW, max_age_sec=120
        )
        assert result["source"] == "live_tick"

    def test_tick_with_non_numeric_ts_falls_back_to_candle(self):
        """Garbage ts → tick age unknown → skipped."""
        tick = {"last_price": 72.0, "ts": "badtime"}
        result = resolve_premium(
            instrument_key="K", tick=tick, candle_close=42.0, now_ts=NOW
        )
        assert result["source"] == "last_candle"

    def test_completely_garbage_tick_does_not_raise(self):
        """A completely wrong-typed tick must not raise."""
        result = resolve_premium(
            instrument_key="K",
            tick={"last_price": "xyz", "ts": object()},
            candle_close=33.0,
            now_ts=NOW,
        )
        assert result["source"] == "last_candle"
        assert result["premium"] == 33.0


# ===========================================================================
# Light route test — POST /live-broker/option-premium
# ===========================================================================

@pytest.mark.asyncio
async def test_route_returns_premium_from_tick(monkeypatch):
    """Smoke-test the route end-to-end with all external dependencies patched."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # ---- patch upstox_stream import so live_broker doesn't fail on import ----
    fake_stream_mod = types.ModuleType("app.upstox_stream")
    fake_manager = MagicMock()
    fake_manager.latest_tick_map.return_value = {
        "NSE_FO|123": {"last_price": 91.0, "ts": NOW}
    }
    fake_stream_mod.upstox_stream_manager = fake_manager
    sys.modules.setdefault("app.upstox_stream", fake_stream_mod)

    import app.routers.live_broker as lb

    # ---- patch module-level getters ----
    fake_db = MagicMock()
    fake_db.option_contracts.find.return_value.to_list = AsyncMock(
        return_value=[_contract()]
    )
    fake_db.options_1m.find_one = AsyncMock(return_value={"close": 88.0})

    monkeypatch.setattr(lb, "_get_db_for_option_premium", lambda: fake_db)
    monkeypatch.setattr(lb, "_get_tick_map_for_option_premium", lambda: fake_manager.latest_tick_map())
    monkeypatch.setattr(lb, "_now_ts_for_option_premium", lambda: NOW)

    app_inst = FastAPI()
    app_inst.include_router(lb.api, prefix="")

    with TestClient(app_inst) as client:
        resp = client.post(
            "/live-broker/option-premium",
            json={
                "underlying": "NIFTY",
                "strike": 25000.0,
                "expiry_date": "2026-06-26",
                "side": "CE",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "live_tick"
    assert body["premium"] == pytest.approx(91.0)
    assert body["fresh"] is True
    assert body["instrument_key"] == "NSE_FO|123"


@pytest.mark.asyncio
async def test_route_contract_not_found_returns_gracefully(monkeypatch):
    """When no contract matches, the route returns premium=None without 500."""
    import app.routers.live_broker as lb

    fake_db = MagicMock()
    fake_db.option_contracts.find.return_value.to_list = AsyncMock(return_value=[])
    fake_db.options_1m.find_one = AsyncMock(return_value=None)

    monkeypatch.setattr(lb, "_get_db_for_option_premium", lambda: fake_db)
    monkeypatch.setattr(lb, "_get_tick_map_for_option_premium", lambda: {})
    monkeypatch.setattr(lb, "_now_ts_for_option_premium", lambda: NOW)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app_inst = FastAPI()
    app_inst.include_router(lb.api, prefix="")

    with TestClient(app_inst) as client:
        resp = client.post(
            "/live-broker/option-premium",
            json={
                "underlying": "NIFTY",
                "strike": 99999.0,
                "expiry_date": "2099-01-01",
                "side": "CE",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["premium"] is None
    assert body["source"] == "none"
    assert body.get("reason") == "contract_not_found"


@pytest.mark.asyncio
async def test_route_queries_active_expiries_without_cap(monkeypatch):
    """REGRESSION: the route MUST filter option_contracts to active expiries
    (expiry_date >= today) and NOT cap the result. option_contracts holds ~20k rows
    per underlying (mostly EXPIRED); the old unfiltered `.to_list(5000)` returned a
    natural-order slice with ZERO active contracts, so every real strike came back
    contract_not_found (the live Order-Ticket "Fetch ₹" button never resolved a price)."""
    import app.routers.live_broker as lb

    fake_db = MagicMock()
    fake_db.option_contracts.find.return_value.to_list = AsyncMock(return_value=[_contract()])
    fake_db.options_1m.find_one = AsyncMock(return_value={"close": 88.0})

    monkeypatch.setattr(lb, "_get_db_for_option_premium", lambda: fake_db)
    monkeypatch.setattr(lb, "_get_tick_map_for_option_premium", lambda: {})
    monkeypatch.setattr(lb, "_now_ts_for_option_premium", lambda: NOW)
    monkeypatch.setattr(lb, "_today_utc_iso", lambda: "2026-06-25")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app_inst = FastAPI()
    app_inst.include_router(lb.api, prefix="")
    with TestClient(app_inst) as client:
        resp = client.post(
            "/live-broker/option-premium",
            json={"underlying": "NIFTY", "strike": 25000.0, "expiry_date": "2026-06-26", "side": "CE"},
        )

    assert resp.status_code == 200
    # the contract query MUST scope to active expiries (>= today) ...
    qarg = fake_db.option_contracts.find.call_args.args[0]
    assert qarg.get("underlying") == "NIFTY"
    assert qarg.get("expiry_date") == {"$gte": "2026-06-25"}
    # ... and MUST NOT cap the result (load all active, like the evaluator).
    fake_db.option_contracts.find.return_value.to_list.assert_awaited_with(length=None)
