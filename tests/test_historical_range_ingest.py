"""Tests for historical date-range ingestion (item C).

Warehouse-safety invariants:
- the plan is a pure read (dry-run) and the route REFUSES to execute without
  confirm=true — plan → confirm → execute is enforced server-side, not just UX;
- explicit ranges may reach back only to the verified-calendar floor
  (2024-01-22) and are clamped to the most recent closed session — with honest
  warnings for pre-baseline ranges and expired-options broker dependence;
- the execute chain composes ONLY upsert-only primitives (spot ingest job,
  expired-contract backfill, band option fetch) — no delete path exists.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.data_hygiene import (  # noqa: E402
    CALENDAR_VERIFIED_FLOOR,
    DEFAULT_START_DATE,
    compute_range_ingest_plan,
)
from app.nse_calendar import expected_candle_count, trading_days_in_range  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Fixed clock: Friday 2026-06-05 16:00 IST — market closed, so 2026-06-05 is
# the most recent closed session.
NOW_IST = datetime(2026, 6, 5, 16, 0)
RANGE_FROM, RANGE_TO = "2026-05-04", "2026-05-08"
RANGE_DAYS = trading_days_in_range(RANGE_FROM, RANGE_TO)


# ---- FakeDB (test_data_hygiene idiom) -----------------------------------------

class FakeAggCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def to_list(self, length=None):
        return list(self._rows)


class FakeCollection:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []
        self._agg_result: List[Dict[str, Any]] = []

    def aggregate(self, pipeline):
        return FakeAggCursor(self._agg_result)

    async def count_documents(self, query):
        return len(self.rows)


class FakeDB:
    def __init__(self):
        self.candles_1m = FakeCollection()
        self.option_contracts = FakeCollection()
        self.options_1m = FakeCollection()
        self.warehouse_runs = FakeCollection()
        self.option_known_empty = FakeCollection()


def seed_days(db: FakeDB, days: List[str], count: int = 375):
    db.candles_1m._agg_result += [
        {"_id": d, "count": count, "low": 24000.0, "high": 24010.0} for d in days
    ]


def _run(coro):
    return asyncio.run(coro)


# ---- planner ---------------------------------------------------------------------

def test_range_plan_empty_warehouse_lists_every_trading_day_missing():
    db = FakeDB()
    plan = _run(compute_range_ingest_plan(
        db, from_date=RANGE_FROM, to_date=RANGE_TO,
        instruments=["NIFTY"], now_ist=NOW_IST))
    inst = plan["instruments"][0]
    assert plan["mode"] == "historical_range"
    assert inst["trading_days"] == len(RANGE_DAYS)
    assert inst["missing_trading_days"] == len(RANGE_DAYS)
    assert inst["stored_days"] == 0
    kinds = [a["kind"] for a in inst["actions"]]
    assert kinds == ["spot", "contracts", "option_candles"]
    spot = inst["actions"][0]
    assert spot["from_date"] == RANGE_DAYS[0] and spot["to_date"] == RANGE_DAYS[-1]
    expected = sum(expected_candle_count(d) for d in RANGE_DAYS)
    assert inst["expected_new_spot_candles"] == expected
    assert plan["summary"]["expected_new_spot_candles"] == expected
    assert plan["summary"]["total_actions"] == 3


def test_range_plan_partial_coverage_narrows_the_spot_action():
    db = FakeDB()
    seed_days(db, RANGE_DAYS[:2])  # first two days complete
    plan = _run(compute_range_ingest_plan(
        db, from_date=RANGE_FROM, to_date=RANGE_TO,
        instruments=["NIFTY"], now_ist=NOW_IST))
    inst = plan["instruments"][0]
    assert inst["missing_trading_days"] == len(RANGE_DAYS) - 2
    assert inst["stored_days"] == 2
    spot = next(a for a in inst["actions"] if a["kind"] == "spot")
    assert spot["from_date"] == RANGE_DAYS[2]


def test_range_plan_reports_under_captured_days():
    db = FakeDB()
    seed_days(db, RANGE_DAYS[:-1])
    seed_days(db, RANGE_DAYS[-1:], count=200)  # last day under-captured
    plan = _run(compute_range_ingest_plan(
        db, from_date=RANGE_FROM, to_date=RANGE_TO,
        instruments=["NIFTY"], now_ist=NOW_IST))
    inst = plan["instruments"][0]
    assert inst["missing_trading_days"] == 0
    assert inst["incomplete_days"] == [
        {"date": RANGE_DAYS[-1], "count": 200,
         "expected": expected_candle_count(RANGE_DAYS[-1])}]
    spot = next(a for a in inst["actions"] if a["kind"] == "spot")
    assert spot["from_date"] == spot["to_date"] == RANGE_DAYS[-1]


def test_range_plan_spot_only_complete_range_is_up_to_date():
    db = FakeDB()
    seed_days(db, RANGE_DAYS)
    plan = _run(compute_range_ingest_plan(
        db, from_date=RANGE_FROM, to_date=RANGE_TO,
        instruments=["NIFTY"], include_options=False, now_ist=NOW_IST))
    inst = plan["instruments"][0]
    assert inst["up_to_date"] is True
    assert inst["actions"] == []
    assert plan["summary"]["total_actions"] == 0


def test_range_plan_with_options_always_proposes_band_fill():
    """Option band completeness is only knowable after spot+contracts land, so
    a spot-complete range still gets contracts + option_candles actions — the
    plan says so honestly instead of pretending to know the candle count."""
    db = FakeDB()
    seed_days(db, RANGE_DAYS)
    plan = _run(compute_range_ingest_plan(
        db, from_date=RANGE_FROM, to_date=RANGE_TO,
        instruments=["NIFTY"], now_ist=NOW_IST))
    inst = plan["instruments"][0]
    kinds = [a["kind"] for a in inst["actions"]]
    assert kinds == ["contracts", "option_candles"]
    opt = inst["actions"][-1]
    assert "resolved AFTER spot + contracts" in opt["reason"]


def test_range_plan_validation_errors():
    db = FakeDB()
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        _run(compute_range_ingest_plan(
            db, from_date="junk", to_date=RANGE_TO, now_ist=NOW_IST))
    with pytest.raises(ValueError, match="on or before"):
        _run(compute_range_ingest_plan(
            db, from_date=RANGE_TO, to_date=RANGE_FROM, now_ist=NOW_IST))
    with pytest.raises(ValueError, match=CALENDAR_VERIFIED_FLOOR):
        _run(compute_range_ingest_plan(
            db, from_date="2024-01-01", to_date="2024-02-01", now_ist=NOW_IST))
    with pytest.raises(ValueError, match="no closed trading session"):
        _run(compute_range_ingest_plan(
            db, from_date="2026-06-08", to_date="2026-06-09", now_ist=NOW_IST))


def test_range_plan_clamps_future_end_and_warns():
    db = FakeDB()
    plan = _run(compute_range_ingest_plan(
        db, from_date="2026-06-01", to_date="2026-12-31",
        instruments=["NIFTY"], now_ist=NOW_IST))
    assert plan["window"]["end"] == "2026-06-05"
    assert plan["summary"]["target_end"] == "2026-06-05"
    assert any("clamped" in w for w in plan["warnings"])


def test_range_plan_pre_baseline_warning_is_honest():
    db = FakeDB()
    plan = _run(compute_range_ingest_plan(
        db, from_date="2024-06-03", to_date="2024-06-07",
        instruments=["NIFTY"], now_ist=NOW_IST))
    assert any(DEFAULT_START_DATE in w for w in plan["warnings"])
    assert any("never faked" in w for w in plan["warnings"])
    assert any("upsert" in w.lower() for w in plan["warnings"])


# ---- route state machine (plan → confirm → execute) -------------------------------

def _route_req(**kw):
    from app.schemas import DataHygieneCatchUpReq
    base = {"instruments": ["NIFTY"], "from_date": RANGE_FROM, "to_date": RANGE_TO}
    return DataHygieneCatchUpReq(**{**base, **kw})


def _patched_route(monkeypatch, *, connected=True, expired=False):
    import app.routers.warehouse as wh

    db = FakeDB()
    monkeypatch.setattr(wh, "get_db", lambda: db)

    async def fake_status():
        return {"connected": connected, "expired": expired}
    monkeypatch.setattr(wh.upstox_client, "get_connection_status", fake_status)

    chain_calls: List[Dict[str, Any]] = []

    async def fake_chain(**kwargs):
        chain_calls.append(kwargs)
        return [("spot", "r-spot"), ("contracts", "r-con"), ("option_candles", "r-opt")]
    monkeypatch.setattr(wh, "_start_historical_range_chain", fake_chain)
    return wh, db, chain_calls


def test_route_dry_run_returns_plan_and_never_starts_the_chain(monkeypatch):
    wh, _db, chain_calls = _patched_route(monkeypatch)
    out = _run(wh._run_warehouse_sync(_route_req(dry_run=True)))
    assert out["dry_run"] is True
    assert out["submitted_count"] == 0
    assert out["plan"]["mode"] == "historical_range"
    assert chain_calls == []


def test_route_refuses_execute_without_confirm(monkeypatch):
    from fastapi import HTTPException
    wh, _db, chain_calls = _patched_route(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _run(wh._run_warehouse_sync(_route_req(dry_run=False)))
    assert exc.value.status_code == 400
    assert "confirm=true" in str(exc.value.detail)
    assert chain_calls == []


def test_route_requires_both_dates(monkeypatch):
    from fastapi import HTTPException
    wh, _db, _calls = _patched_route(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _run(wh._run_warehouse_sync(_route_req(to_date=None, dry_run=True)))
    assert "BOTH from_date and to_date" in str(exc.value.detail)


def test_route_requires_token_only_for_execute(monkeypatch):
    from fastapi import HTTPException
    wh, _db, chain_calls = _patched_route(monkeypatch, connected=False)
    # dry-run works without a token
    out = _run(wh._run_warehouse_sync(_route_req(dry_run=True)))
    assert out["dry_run"] is True
    # execute does not
    with pytest.raises(HTTPException) as exc:
        _run(wh._run_warehouse_sync(_route_req(confirm=True)))
    assert "not connected" in str(exc.value.detail)
    assert chain_calls == []


def test_route_confirmed_execute_starts_the_chain_with_the_planned_range(monkeypatch):
    wh, _db, chain_calls = _patched_route(monkeypatch)
    out = _run(wh._run_warehouse_sync(_route_req(confirm=True)))
    assert out["submitted_count"] == 3
    assert [s["kind"] for s in out["submitted"]] == ["spot", "contracts", "option_candles"]
    assert len(chain_calls) == 1
    call = chain_calls[0]
    assert call["instrument"] == "NIFTY"
    assert call["from_date"] == RANGE_FROM and call["to_date"] == RANGE_TO
    # Empty warehouse → the spot stage covers the first..last missing day.
    assert call["spot_from"] == RANGE_DAYS[0] and call["spot_to"] == RANGE_DAYS[-1]
    assert call["include_options"] is True


def test_route_validation_error_maps_to_400(monkeypatch):
    from fastapi import HTTPException
    wh, _db, _calls = _patched_route(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _run(wh._run_warehouse_sync(
            _route_req(from_date="2023-01-01", dry_run=True)))
    assert exc.value.status_code == 400
    assert CALENDAR_VERIFIED_FLOOR in str(exc.value.detail)


# ---- executor chain + wiring pins --------------------------------------------------

def _runtime_chain_slice() -> str:
    src = (ROOT / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")
    start = src.index("async def _run_historical_range_chain")
    end = src.index("\nasync def _hygiene_submit_spot")
    return src[start:end]


def test_range_chain_composes_upsert_only_primitives():
    chain = _runtime_chain_slice()
    # The three upsert-only engines, in dependency order.
    assert "run_upstox_index_ingest_job" in chain
    assert "backfill_expired_option_contracts" in chain
    assert "confirm_large_fetch=True" in chain
    # Expiry discovery must look past to_date: each day's band resolves to the
    # NEXT expiry on/after it (verified live: a Mon-Tue range needs Thursday's
    # expiry, which is outside [from,to]).
    assert "EXPIRY_LOOKAHEAD_DAYS" in chain
    assert "build_band_fetch_plan(db, instrument, from_date, to_date" in chain
    assert "run_option_warehouse_fetch_job" in chain
    assert "record_broker_empty_pairs" in chain
    # No destructive Mongo operation anywhere in the chain.
    assert "delete_many" not in chain
    assert "delete_one" not in chain
    assert ".drop(" not in chain
    # Options proceed off what the warehouse HOLDS (re-run friendly), not what
    # this run fetched.
    assert "no_spot_candles_in_range" in chain


def test_backend_exposes_range_ingest_surface():
    from tests.contract_corpus import backend_api_text
    text = backend_api_text()
    assert "compute_range_ingest_plan" in text
    assert "_start_historical_range_chain" in text
    assert "re-post with confirm=true" in text
    assert "Historical range ingestion (2026-07)" in text  # schema fields doc


def test_frontend_historical_panel_is_wired():
    from tests.contract_corpus import warehouse_page_text
    text = warehouse_page_text()
    for pin in (
        "historical-ingest-panel",
        "historical-plan-button",
        "historical-execute-button",
        "historical-verify-button",
        "historical-degraded-banner",
        "dataHygieneCatchUp",         # executes through the range-aware endpoint
        "auditWarehouse",             # before/after per-day count+hash snapshot
        "confirm: true",              # the confirm gate is actually sent
    ):
        assert pin in text, f"missing frontend pin: {pin}"
    # The execute button must be plan-gated (state machine, not just copy).
    panel = (ROOT / "frontend" / "src" / "components" / "warehouse" /
             "HistoricalIngestPanel.jsx").read_text(encoding="utf-8")
    assert "disabled={!plan" in panel
    assert "window.confirm" in panel
