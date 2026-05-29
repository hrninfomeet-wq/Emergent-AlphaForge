"""Tests for the data-realism pre-flight report (slice 5)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.deployment_preflight import (  # noqa: E402
    STATUS_DEGRADED,
    STATUS_VERIFIED,
    STATUS_WARNING,
    compute_data_realism,
)


IST = timezone(timedelta(hours=5, minutes=30))


def today_ist() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def days_ago_ist(n: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30) - timedelta(days=n)).strftime("%Y-%m-%d")


def days_ahead_ist(n: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30) + timedelta(days=n)).strftime("%Y-%m-%d")


# ---- minimal async-mock collection ------------------------------------------


class FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)

    async def to_list(self, length: Optional[int] = None):
        return list(self._rows if length is None else self._rows[: int(length)])


class FakeCollection:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self.rows = list(rows or [])

    def find(self, query: Optional[Dict[str, Any]] = None, projection=None):
        rows = [r for r in self.rows if _matches(r, query or {})]
        return FakeCursor(rows)

    async def find_one(self, query: Dict[str, Any], projection=None):
        for r in self.rows:
            if _matches(r, query):
                return dict(r)
        return None

    async def distinct(self, key: str, query: Optional[Dict[str, Any]] = None):
        rows = [r for r in self.rows if _matches(r, query or {})]
        seen: List[Any] = []
        for r in rows:
            v = r.get(key)
            if v is not None and v not in seen:
                seen.append(v)
        return seen


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        if isinstance(v, dict) and "$gte" in v:
            row_val = row.get(k)
            if row_val is None:
                return False
            try:
                if row_val < v["$gte"]:
                    return False
            except TypeError:
                return False
        elif row.get(k) != v:
            return False
    return True


class FakeDB:
    def __init__(self):
        self.candles_1m = FakeCollection()
        self.option_contracts = FakeCollection()
        self.upstox_tokens = FakeCollection()


def _seed_full_coverage_spot(db: FakeDB, instrument: str = "NIFTY", days: int = 30) -> None:
    """Seed candles for every weekday in the last `days`."""
    cur = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30) - timedelta(days=days)
    end = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    while cur.date() <= end.date():
        if cur.weekday() < 5:
            db.candles_1m.rows.append({
                "instrument": instrument.upper(),
                "session_date": cur.strftime("%Y-%m-%d"),
                "ts": int(cur.timestamp() * 1000),
            })
        cur += timedelta(days=1)


def _seed_active_token(db: FakeDB) -> None:
    db.upstox_tokens.rows.append({
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        "expired": False,
    })


def _seed_expired_token(db: FakeDB) -> None:
    db.upstox_tokens.rows.append({
        "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "expired": True,
    })


def _seed_active_contracts(db: FakeDB, *, expiries: List[str], instrument: str = "NIFTY") -> None:
    for exp in expiries:
        for side in ("CE", "PE"):
            db.option_contracts.rows.append({
                "underlying": instrument.upper(),
                "expiry_date": exp,
                "side": side,
                "strike": 24000.0,
                "instrument_key": f"NSE_FO|{exp}|{side}",
            })


# ---- happy path -------------------------------------------------------------


@pytest.mark.asyncio
async def test_verified_status_when_everything_is_healthy():
    db = FakeDB()
    _seed_full_coverage_spot(db, "NIFTY")
    _seed_active_contracts(db, expiries=[days_ahead_ist(d) for d in (5, 12, 19, 26)])
    _seed_active_token(db)

    report = await compute_data_realism(db, "NIFTY")

    assert report["instrument"] == "NIFTY"
    assert report["status"] == STATUS_VERIFIED
    ids = [c["id"] for c in report["checks"]]
    assert {"spot_coverage", "option_contracts_upcoming", "active_contracts_present", "upstox_token"} <= set(ids)


# ---- spot coverage scenarios ------------------------------------------------


@pytest.mark.asyncio
async def test_degraded_status_when_no_spot_candles():
    db = FakeDB()
    _seed_active_contracts(db, expiries=[days_ahead_ist(5)])
    _seed_active_token(db)

    report = await compute_data_realism(db, "NIFTY")

    spot_check = next(c for c in report["checks"] if c["id"] == "spot_coverage")
    assert spot_check["status"] == STATUS_DEGRADED
    assert report["status"] == STATUS_DEGRADED


# ---- option contract scenarios ----------------------------------------------


@pytest.mark.asyncio
async def test_degraded_when_only_expired_contracts_present():
    """The exact bug we hit on 2026-05-28: contracts exist, but all in the past."""
    db = FakeDB()
    _seed_full_coverage_spot(db, "NIFTY")
    _seed_active_contracts(db, expiries=[days_ago_ist(d) for d in (10, 50, 90)])
    _seed_active_token(db)

    report = await compute_data_realism(db, "NIFTY")

    active_check = next(c for c in report["checks"] if c["id"] == "active_contracts_present")
    assert active_check["status"] == STATUS_DEGRADED
    assert active_check["value"]["active_count"] == 0
    assert active_check["value"]["expired_count"] >= 1


@pytest.mark.asyncio
async def test_warning_when_too_few_upcoming_expiries():
    db = FakeDB()
    _seed_full_coverage_spot(db, "NIFTY")
    _seed_active_contracts(db, expiries=[days_ahead_ist(5)])  # only 1 future expiry
    _seed_active_token(db)

    report = await compute_data_realism(db, "NIFTY", lookahead_expiries=4)

    upcoming = next(c for c in report["checks"] if c["id"] == "option_contracts_upcoming")
    assert upcoming["status"] == STATUS_WARNING


# ---- token scenarios --------------------------------------------------------


@pytest.mark.asyncio
async def test_token_expired_yields_warning():
    db = FakeDB()
    _seed_full_coverage_spot(db, "NIFTY")
    _seed_active_contracts(db, expiries=[days_ahead_ist(d) for d in (5, 12, 19, 26)])
    _seed_expired_token(db)

    report = await compute_data_realism(db, "NIFTY")

    token_check = next(c for c in report["checks"] if c["id"] == "upstox_token")
    assert token_check["status"] == STATUS_WARNING
    assert report["status"] in (STATUS_WARNING, STATUS_DEGRADED)


# ---- structural break scenarios --------------------------------------------


@pytest.mark.asyncio
async def test_banknifty_yields_weekly_discontinued_warning():
    db = FakeDB()
    _seed_full_coverage_spot(db, "BANKNIFTY")
    _seed_active_contracts(db, expiries=[days_ahead_ist(d) for d in (5, 12, 19, 26)], instrument="BANKNIFTY")
    _seed_active_token(db)

    report = await compute_data_realism(db, "BANKNIFTY")

    breaks = report["structural_breaks"]
    assert any(b["id"] == "banknifty_weekly_discontinued" for b in breaks)
    # Aggregate should be at least WARNING because of the break
    assert report["status"] in (STATUS_WARNING, STATUS_DEGRADED)


@pytest.mark.asyncio
async def test_unsupported_instrument_yields_degraded_with_clear_reason():
    db = FakeDB()
    report = await compute_data_realism(db, "TCS")
    assert report["status"] == STATUS_DEGRADED
    assert report["checks"][0]["id"] == "supported_instrument"
