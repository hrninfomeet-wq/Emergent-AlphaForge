"""Tests for per-deployment LIVE caps (Section C — live_deploy_governor)."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_deploy_governor import check_live_caps  # noqa: E402


# --- FakeDB: a synchronous live_trades collection ---------------------------


class _FakeColl:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows

    def find(self, query: Dict[str, Any]):
        return [r for r in self.rows if all(r.get(k) == v for k, v in query.items())]


class _FakeDB:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.live_trades = _FakeColl(rows)


def _open(unrealized: float = 0.0, created_at: str = "2026-06-15T05:00:00+00:00",
          lots: int = 1, dep: str = "d1") -> Dict[str, Any]:
    return {"deployment_id": dep, "status": "OPEN", "unrealized_pnl": unrealized,
            "created_at": created_at, "lots": lots}


def _closed(pnl: float, created_at: str = "2026-06-15T04:00:00+00:00",
            closed_at: str = "2026-06-15T05:00:00+00:00", lots: int = 1,
            entry_value: float = 10000.0, dep: str = "d1") -> Dict[str, Any]:
    return {"deployment_id": dep, "status": "CLOSED", "realized_pnl": pnl,
            "created_at": created_at, "closed_at": closed_at, "lots": lots,
            "entry_value": entry_value}


def _dep(live_caps: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": "d1", "mode": "live", "risk": {"live_caps": live_caps}}


NOW = "2026-06-15T06:00:00+00:00"  # IST date 2026-06-15

import datetime as _dt  # noqa: E402

NOW_UTC = _dt.datetime(2026, 6, 15, 6, 0, tzinfo=_dt.timezone.utc)


# --- no caps / all-pass ------------------------------------------------------


def test_no_caps_configured_allows_without_db():
    # A DB whose find() would explode proves no query is issued when no cap is set.
    class _Boom:
        @property
        def live_trades(self):
            raise AssertionError("DB must not be queried when no cap configured")

    d = check_live_caps(_Boom(), {"id": "d1", "risk": {}}, capped_lots=5, now_utc=NOW_UTC)
    assert d == {"allow": True, "reason": None, "pause": False}


def test_all_caps_pass():
    db = _FakeDB([_open(unrealized=-100.0), _closed(-200.0)])
    dep = _dep({"max_concurrent": 5, "max_lots_per_day": 10, "daily_loss_cap": -50000})
    d = check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC)
    assert d == {"allow": True, "reason": None, "pause": False}


# --- max_concurrent (soft block) --------------------------------------------


def test_max_concurrent_blocks_at_limit():
    db = _FakeDB([_open(), _open()])
    d = check_live_caps(db, _dep({"max_concurrent": 2}), capped_lots=1, now_utc=NOW_UTC)
    assert d["allow"] is False
    assert d["pause"] is False
    assert "max_concurrent" in d["reason"]


def test_max_concurrent_allows_below_limit():
    db = _FakeDB([_open()])
    d = check_live_caps(db, _dep({"max_concurrent": 2}), capped_lots=1, now_utc=NOW_UTC)
    assert d["allow"] is True


def test_max_concurrent_ignores_closed_and_other_deployments():
    db = _FakeDB([_open(), _closed(-5.0), _open(dep="d2")])
    # only one OPEN trade for d1 -> below a limit of 2
    d = check_live_caps(db, _dep({"max_concurrent": 2}), capped_lots=1, now_utc=NOW_UTC)
    assert d["allow"] is True


# --- max_lots_per_day (soft block) ------------------------------------------


def test_max_lots_per_day_blocks_when_new_lots_exceed():
    db = _FakeDB([_closed(-5.0, lots=8), _open(lots=1)])  # 9 lots used today
    d = check_live_caps(db, _dep({"max_lots_per_day": 10}), capped_lots=2, now_utc=NOW_UTC)
    assert d["allow"] is False
    assert d["pause"] is False
    assert "max_lots_per_day" in d["reason"]


def test_max_lots_per_day_allows_exactly_at_limit():
    db = _FakeDB([_closed(-5.0, lots=8)])
    d = check_live_caps(db, _dep({"max_lots_per_day": 10}), capped_lots=2, now_utc=NOW_UTC)
    assert d["allow"] is True  # 8 + 2 == 10, not > 10


def test_max_lots_per_day_ignores_other_days():
    db = _FakeDB([_closed(-5.0, lots=9, created_at="2026-06-14T05:00:00+00:00",
                          closed_at="2026-06-14T06:00:00+00:00")])
    d = check_live_caps(db, _dep({"max_lots_per_day": 5}), capped_lots=3, now_utc=NOW_UTC)
    assert d["allow"] is True  # yesterday's 9 lots don't count


# --- daily_loss_cap (pause) -------------------------------------------------


def test_daily_loss_cap_pauses_on_realized_breach():
    db = _FakeDB([_closed(-16000.0)])
    d = check_live_caps(db, _dep({"daily_loss_cap": -15000}), capped_lots=1, now_utc=NOW_UTC)
    assert d["allow"] is False
    assert d["pause"] is True
    assert "daily_loss_cap" in d["reason"]


def test_daily_loss_cap_counts_open_unrealized():
    # realized -8000 + open -8000 = -16000 <= -15000 -> pause even before close
    db = _FakeDB([_closed(-8000.0), _open(unrealized=-8000.0)])
    d = check_live_caps(db, _dep({"daily_loss_cap": -15000}), capped_lots=1, now_utc=NOW_UTC)
    assert d["allow"] is False
    assert d["pause"] is True


def test_daily_loss_cap_not_breached():
    db = _FakeDB([_closed(-5000.0), _open(unrealized=-4000.0)])
    d = check_live_caps(db, _dep({"daily_loss_cap": -15000}), capped_lots=1, now_utc=NOW_UTC)
    assert d["allow"] is True


def test_daily_loss_cap_takes_headline_over_soft_blocks():
    # both max_concurrent AND the loss cap would trip; pause must win.
    db = _FakeDB([_open(unrealized=-20000.0), _open(unrealized=0.0)])
    dep = _dep({"max_concurrent": 2, "daily_loss_cap": -15000})
    d = check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC)
    assert d["allow"] is False
    assert d["pause"] is True
    assert "daily_loss_cap" in d["reason"]


def test_daily_loss_cap_open_pnl_only_counts_today():
    # an open trade entered yesterday must not feed today's loss cap
    db = _FakeDB([_open(unrealized=-99999.0, created_at="2026-06-14T05:00:00+00:00")])
    d = check_live_caps(db, _dep({"daily_loss_cap": -15000}), capped_lots=1, now_utc=NOW_UTC)
    assert d["allow"] is True
