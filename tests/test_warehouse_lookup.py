"""Tests for the point-in-time warehouse lookup (slice 6)."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.warehouse_lookup import ist_datetime_to_ms, lookup_market_snapshot  # noqa: E402


# ---- minimal Mongo-style fakes ---------------------------------------------


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, key, direction=1):
        self._rows.sort(key=lambda r: r.get(key), reverse=(direction == -1))
        return self

    def limit(self, n):
        self._rows = self._rows[: int(n)]
        return self

    async def to_list(self, length=None):
        return list(self._rows)


class FakeCollection:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self.rows = list(rows or [])

    async def find_one(self, query, projection=None, sort=None):
        rows = [r for r in self.rows if _matches(r, query)]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda r: r.get(key), reverse=(direction == -1))
        return dict(rows[0]) if rows else None

    def find(self, query, projection=None):
        return FakeCursor([r for r in self.rows if _matches(r, query)])


def _matches(row, query):
    for k, v in (query or {}).items():
        if isinstance(v, dict):
            rv = row.get(k)
            if "$gte" in v and (rv is None or rv < v["$gte"]):
                return False
            if "$lt" in v and (rv is None or rv >= v["$lt"]):
                return False
        elif row.get(k) != v:
            return False
    return True


class FakeDB:
    def __init__(self):
        self.candles_1m = FakeCollection()
        self.options_1m = FakeCollection()
        self.option_contracts = FakeCollection()


# ---- timestamp conversion ---------------------------------------------------


def test_ist_datetime_to_ms_is_minute_truncated_utc():
    # 2026-05-20 09:15 IST == 03:45 UTC == 1779248700000 ms
    ms = ist_datetime_to_ms("2026-05-20", "09:15")
    assert ms == 1779248700000
    # Round-trips back to the same IST minute.
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    back = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(ist)
    assert back.strftime("%Y-%m-%d %H:%M") == "2026-05-20 09:15"


def test_ist_datetime_defaults_to_open_when_time_blank():
    assert ist_datetime_to_ms("2026-05-20", "") == ist_datetime_to_ms("2026-05-20", "09:15")


# ---- full snapshot ----------------------------------------------------------


def _seed_full(db: FakeDB, ts: int):
    db.candles_1m.rows.append({
        "instrument": "NIFTY", "ts": ts,
        "open": 24000, "high": 24050, "low": 23980, "close": 24023, "volume": 100,
    })
    # ATM for close 24023, step 50 -> 24000
    for side, key, price in (("CE", "NSE_FO|CE24000", 120.5), ("PE", "NSE_FO|PE24000", 95.25)):
        db.option_contracts.rows.append({
            "underlying": "NIFTY", "expiry_date": "2026-05-26", "strike": 24000.0,
            "side": side, "instrument_key": key, "trading_symbol": f"NIFTY{side}", "lot_size": 65,
        })
        db.options_1m.rows.append({
            "instrument_key": key, "ts": ts,
            "open": price, "high": price + 5, "low": price - 5, "close": price, "volume": 10, "oi": 500,
        })


@pytest.mark.asyncio
async def test_lookup_returns_spot_atm_and_both_legs():
    db = FakeDB()
    ts = ist_datetime_to_ms("2026-05-20", "10:00")
    _seed_full(db, ts)

    snap = await lookup_market_snapshot(db, underlying="NIFTY", date_str="2026-05-20", time_str="10:00")

    assert snap["spot_exact"] is True
    assert snap["spot"]["close"] == 24023
    assert snap["atm_strike"] == 24000
    assert snap["expiry"] == "2026-05-26"
    assert snap["legs"]["CE"]["available"] is True
    assert snap["legs"]["CE"]["candle"]["close"] == 120.5
    assert snap["legs"]["CE"]["candle"]["oi"] == 500
    assert snap["legs"]["PE"]["available"] is True
    assert snap["legs"]["PE"]["candle"]["close"] == 95.25


@pytest.mark.asyncio
async def test_lookup_reports_missing_spot():
    db = FakeDB()
    snap = await lookup_market_snapshot(db, underlying="NIFTY", date_str="2026-05-20", time_str="10:00")
    assert snap["spot"] is None
    assert any("No spot candle" in n for n in snap["notes"])


@pytest.mark.asyncio
async def test_lookup_falls_back_to_recent_bar_within_window():
    db = FakeDB()
    target = ist_datetime_to_ms("2026-05-20", "10:00")
    # Seed a spot bar 2 minutes earlier only.
    earlier = target - 2 * 60 * 1000
    db.candles_1m.rows.append({
        "instrument": "NIFTY", "ts": earlier,
        "open": 24000, "high": 24050, "low": 23980, "close": 24010, "volume": 100,
    })
    snap = await lookup_market_snapshot(db, underlying="NIFTY", date_str="2026-05-20", time_str="10:00")
    assert snap["spot"] is not None
    assert snap["spot_exact"] is False
    assert any("Exact minute missing for spot" in n for n in snap["notes"])


@pytest.mark.asyncio
async def test_lookup_reports_missing_option_contract():
    db = FakeDB()
    ts = ist_datetime_to_ms("2026-05-20", "10:00")
    db.candles_1m.rows.append({
        "instrument": "NIFTY", "ts": ts,
        "open": 24000, "high": 24050, "low": 23980, "close": 24023, "volume": 100,
    })
    # Expiry metadata exists but no contracts at the ATM strike.
    db.option_contracts.rows.append({
        "underlying": "NIFTY", "expiry_date": "2026-05-26", "strike": 99999.0,
        "side": "CE", "instrument_key": "x",
    })
    snap = await lookup_market_snapshot(db, underlying="NIFTY", date_str="2026-05-20", time_str="10:00")
    assert snap["atm_strike"] == 24000
    assert snap["expiry"] == "2026-05-26"
    assert snap["legs"]["CE"]["available"] is False
    assert snap["legs"]["CE"]["reason"] == "contract_metadata_missing"


@pytest.mark.asyncio
async def test_lookup_picks_nearest_expiry_on_or_after_date():
    db = FakeDB()
    ts = ist_datetime_to_ms("2026-05-20", "10:00")
    db.candles_1m.rows.append({
        "instrument": "NIFTY", "ts": ts,
        "open": 24000, "high": 24050, "low": 23980, "close": 24023, "volume": 100,
    })
    # Two expiries: one before the date (should be ignored), one after.
    for exp in ("2026-05-12", "2026-05-26", "2026-06-02"):
        db.option_contracts.rows.append({
            "underlying": "NIFTY", "expiry_date": exp, "strike": 24000.0,
            "side": "CE", "instrument_key": f"k{exp}",
        })
    snap = await lookup_market_snapshot(db, underlying="NIFTY", date_str="2026-05-20", time_str="10:00")
    assert snap["expiry"] == "2026-05-26"
