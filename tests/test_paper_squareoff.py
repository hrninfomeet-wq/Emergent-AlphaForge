"""Tests for paper-trade auto-square-off at 15:00 IST."""
from __future__ import annotations

import os
import sys
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.paper_squareoff import (  # noqa: E402
    DEFAULT_SQUARE_OFF_IST,
    is_square_off_due,
    square_off_open_paper_trades,
)
from app.paper_trading import paper_trade_from_signal  # noqa: E402


IST = timezone(timedelta(hours=5, minutes=30))


def ist(year, month, day, hh, mm) -> datetime:
    return datetime(year, month, day, hh, mm, tzinfo=IST)


def test_is_square_off_due_before_cutoff_returns_false():
    assert not is_square_off_due(ist(2026, 5, 27, 14, 59))


def test_is_square_off_due_at_cutoff_returns_true():
    assert is_square_off_due(ist(2026, 5, 27, 15, 0))


def test_is_square_off_due_after_cutoff_returns_true():
    assert is_square_off_due(ist(2026, 5, 27, 15, 25))


def test_is_square_off_due_on_weekend_returns_false():
    # 2026-05-30 is Saturday
    assert not is_square_off_due(ist(2026, 5, 30, 15, 30))


# --- async helpers --------------------------------------------------------


class FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)

    async def to_list(self, length: Optional[int] = None):
        return list(self._rows if length is None else self._rows[: int(length)])


class FakePaperCollection:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def find(self, query=None, projection=None):
        q = query or {}
        if "$in" in str(q):
            # Handle $in queries used for deployment lookups
            result = []
            for r in self.rows:
                ok = True
                for k, v in q.items():
                    if isinstance(v, dict) and "$in" in v:
                        if r.get(k) not in v["$in"]:
                            ok = False
                            break
                    elif r.get(k) != v:
                        ok = False
                        break
                if ok:
                    result.append(r)
            return FakeCursor(result)
        rows = [r for r in self.rows if (q).get("status", r.get("status")) == r.get("status")]
        return FakeCursor(rows)

    async def replace_one(self, query, doc, upsert=False):
        for i, r in enumerate(self.rows):
            if r.get("id") == query.get("id"):
                self.rows[i] = doc
                return MagicMock()
        if upsert:
            self.rows.append(doc)
        return MagicMock()


class FakeDB:
    def __init__(self):
        self.paper_trades = FakePaperCollection()
        self.strategy_deployments = FakePaperCollection()


def make_open_trade(*, instrument_key: str, entry: float, last: float = None) -> Dict[str, Any]:
    sig = {
        "id": "sig-1",
        "instrument": "NIFTY",
        "direction": "CE",
        "strategy_id": "test",
        "entry_price": entry,
        "option_contract": {"trading_symbol": "NIFTY26JUN23900CE", "lot_size": 50, "instrument_key": instrument_key},
    }
    trade = paper_trade_from_signal(sig, lots=1, entry_price=entry)
    trade["instrument_key"] = instrument_key
    if last is not None:
        trade["last_price"] = last
    return trade


@pytest.mark.asyncio
async def test_square_off_uses_latest_ws_tick_when_available():
    db = FakeDB()
    trade = make_open_trade(instrument_key="NSE_FO|TEST|CE", entry=100.0, last=110.0)
    db.paper_trades.rows.append(trade)

    def latest_lookup(key: str):
        if key == "NSE_FO|TEST|CE":
            return {"last_price": 125.0}
        return None

    summaries = await square_off_open_paper_trades(db, latest_tick_lookup=latest_lookup)

    assert len(summaries) == 1
    assert summaries[0]["exit_price"] == 125.0  # WS tick wins over last mark
    closed = db.paper_trades.rows[0]
    assert closed["status"] == "CLOSED"
    assert closed["exit_reason"] == "auto_square_off_15_00_IST"
    assert closed["realized_pnl"] == round((125.0 - 100.0) * 50, 2)


@pytest.mark.asyncio
async def test_square_off_falls_back_to_last_price_when_no_tick():
    db = FakeDB()
    trade = make_open_trade(instrument_key="NSE_FO|TEST|CE", entry=100.0, last=115.0)
    db.paper_trades.rows.append(trade)

    summaries = await square_off_open_paper_trades(db)

    assert summaries[0]["exit_price"] == 115.0


@pytest.mark.asyncio
async def test_square_off_falls_back_to_entry_price_when_no_data():
    db = FakeDB()
    trade = make_open_trade(instrument_key="NSE_FO|TEST|CE", entry=100.0, last=None)
    trade["last_price"] = None
    db.paper_trades.rows.append(trade)

    summaries = await square_off_open_paper_trades(db)

    assert summaries[0]["exit_price"] == 100.0  # entry fallback -> zero PnL


@pytest.mark.asyncio
async def test_square_off_is_idempotent_for_already_closed_trades():
    db = FakeDB()
    open_trade = make_open_trade(instrument_key="NSE_FO|A|CE", entry=100.0, last=120.0)
    db.paper_trades.rows.append(open_trade)
    closed_trade = make_open_trade(instrument_key="NSE_FO|B|CE", entry=200.0, last=180.0)
    closed_trade["status"] = "CLOSED"
    db.paper_trades.rows.append(closed_trade)

    summaries = await square_off_open_paper_trades(db)

    assert len(summaries) == 1
    assert summaries[0]["instrument_key"] == "NSE_FO|A|CE"
    # Already-closed trade was not touched
    assert db.paper_trades.rows[1]["realized_pnl"] is None or db.paper_trades.rows[1].get("realized_pnl") is None


@pytest.mark.asyncio
async def test_square_off_skips_trades_from_allow_overnight_deployments():
    """User opted into overnight on a deployment -> its open trades survive square-off."""
    db = FakeDB()
    intraday = make_open_trade(instrument_key="NSE_FO|A|CE", entry=100.0, last=120.0)
    intraday["deployment_id"] = "intraday-dep"
    overnight = make_open_trade(instrument_key="NSE_FO|B|CE", entry=200.0, last=215.0)
    overnight["deployment_id"] = "overnight-dep"
    db.paper_trades.rows.extend([intraday, overnight])
    db.strategy_deployments.rows.extend([
        {"id": "intraday-dep", "risk": {"allow_overnight": False}},
        {"id": "overnight-dep", "risk": {"allow_overnight": True}},
    ])

    summaries = await square_off_open_paper_trades(db)

    # Both trades should appear in summaries: one closed, one skipped
    closed = [s for s in summaries if "exit_price" in s]
    skipped = [s for s in summaries if s.get("skipped") == "allow_overnight"]
    assert len(closed) == 1
    assert closed[0]["instrument_key"] == "NSE_FO|A|CE"
    assert len(skipped) == 1
    # Confirm the overnight trade is still OPEN in the DB
    overnight_after = next(t for t in db.paper_trades.rows if t.get("id") == overnight["id"])
    assert overnight_after["status"] == "OPEN"
