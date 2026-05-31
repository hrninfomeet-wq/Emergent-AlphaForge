"""Tests for the data hygiene plan/diff logic (slice 6)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.data_hygiene import (  # noqa: E402
    compute_hygiene_plan,
    execute_hygiene_plan,
    _expected_weekday_count,
)


IST = timezone(timedelta(hours=5, minutes=30))


def today_ist_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


# ---- minimal Mongo-style fakes ---------------------------------------------


class FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        self._rows = self._rows[:int(n)]
        return self

    async def to_list(self, length: Optional[int] = None):
        return list(self._rows if length is None else self._rows[: int(length)])


class FakeAggCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def to_list(self, length: Optional[int] = None):
        return list(self._rows)


class FakeCollection:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None, agg_result=None):
        self.rows = list(rows or [])
        self._agg_result = agg_result or []

    def find(self, query=None, projection=None):
        rows = [r for r in self.rows if _matches(r, query or {})]
        return FakeCursor(rows)

    async def find_one(self, query, projection=None):
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

    def aggregate(self, pipeline):
        return FakeAggCursor(self._agg_result)


def _matches(row, query):
    for k, v in (query or {}).items():
        if isinstance(v, dict):
            row_val = row.get(k)
            if "$gte" in v and (row_val is None or row_val < v["$gte"]):
                return False
            if "$lte" in v and (row_val is None or row_val > v["$lte"]):
                return False
        elif row.get(k) != v:
            return False
    return True


class FakeDB:
    def __init__(self):
        self.candles_1m = FakeCollection()
        self.option_contracts = FakeCollection()
        self.options_1m = FakeCollection()
        self.warehouse_runs = FakeCollection()


# ---- helpers ----------------------------------------------------------------


def seed_full_spot_coverage(db: FakeDB, instrument: str, start_iso: str, end_iso: str):
    """Add candles for every weekday in the range so coverage_pct == 100.

    Spot coverage now runs a Mongo aggregation that groups by IST date, so we
    seed both the raw rows (for any find-based callers) and the aggregation
    result (distinct {_id: date} docs) that _spot_coverage consumes.
    """
    cur = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso)
    agg_rows = []
    while cur.date() <= end.date():
        if cur.weekday() < 5:
            ts = int(cur.replace(tzinfo=timezone.utc).timestamp() * 1000)
            date_str = cur.strftime("%Y-%m-%d")
            db.candles_1m.rows.append({
                "instrument": instrument.upper(),
                "ts": ts,
                "session_date": date_str,
            })
            agg_rows.append({"_id": date_str})
        cur += timedelta(days=1)
    db.candles_1m._agg_result = list(agg_rows)


def seed_contracts(db: FakeDB, instrument: str, expiries: List[str]):
    for e in expiries:
        for side in ("CE", "PE"):
            db.option_contracts.rows.append({
                "underlying": instrument.upper(),
                "expiry_date": e,
                "side": side,
                "strike": 24000.0,
                "instrument_key": f"NSE_FO|{instrument}|{e}|{side}",
            })


def seed_option_candle_aggregate(db: FakeDB, rows: List[Dict[str, Any]]):
    """Pre-seed the aggregation result for options_1m -> _option_candles_summary."""
    db.options_1m._agg_result = list(rows)


# ---- _expected_weekday_count -----------------------------------------------


def test_expected_weekday_count_inclusive():
    # 2024-11-25 (Mon) to 2024-11-29 (Fri) = 5 weekdays
    assert _expected_weekday_count("2024-11-25", "2024-11-29") == 5


def test_expected_weekday_count_skips_weekend():
    # 2024-11-30 (Sat) to 2024-12-01 (Sun) = 0 weekdays
    assert _expected_weekday_count("2024-11-30", "2024-12-01") == 0


# ---- compute_hygiene_plan: degraded -> all actions present ------------------


@pytest.mark.asyncio
async def test_plan_empty_warehouse_yields_three_actions_per_instrument():
    db = FakeDB()
    plan = await compute_hygiene_plan(
        db, start_date="2024-11-27", end_date="2024-12-06",
        instruments=["NIFTY", "BANKNIFTY"],
    )
    assert plan["summary"]["overall_status"] == "degraded"
    # 2 instruments * (spot + contracts only - because option_candles only fires after contracts exist)
    actions = []
    for inst in plan["instruments"]:
        actions.extend(inst["actions"])
    spots = [a for a in actions if a["kind"] == "spot"]
    contracts = [a for a in actions if a["kind"] == "contracts"]
    options = [a for a in actions if a["kind"] == "option_candles"]
    assert len(spots) == 2
    assert len(contracts) == 2
    # No options actions because contracts are missing - they get added in the next pass
    assert len(options) == 0


# ---- compute_hygiene_plan: spot complete, contracts missing ----------------


@pytest.mark.asyncio
async def test_plan_with_spot_only_still_blocks_options_action():
    db = FakeDB()
    seed_full_spot_coverage(db, "NIFTY", "2024-11-27", "2024-12-06")
    plan = await compute_hygiene_plan(
        db, start_date="2024-11-27", end_date="2024-12-06",
        instruments=["NIFTY"],
    )
    nifty = plan["instruments"][0]
    assert nifty["spot"]["status"] == "verified"
    # No contracts seeded, so contracts is degraded and options action does NOT fire
    assert nifty["contracts"]["status"] == "degraded"
    assert all(a["kind"] != "option_candles" for a in nifty["actions"])


# ---- compute_hygiene_plan: full chain ------------------------------------


@pytest.mark.asyncio
async def test_plan_with_spot_and_contracts_proposes_option_candles_action():
    db = FakeDB()
    seed_full_spot_coverage(db, "NIFTY", "2024-11-27", "2024-12-06")
    seed_contracts(db, "NIFTY", ["2024-11-28", "2024-12-05"])
    # option_candles aggregate empty -> options status = degraded -> action fires
    plan = await compute_hygiene_plan(
        db, start_date="2024-11-27", end_date="2024-12-06",
        instruments=["NIFTY"],
    )
    nifty = plan["instruments"][0]
    assert nifty["contracts"]["status"] == "verified"
    assert nifty["option_candles"]["status"] == "degraded"
    options_actions = [a for a in nifty["actions"] if a["kind"] == "option_candles"]
    assert len(options_actions) == 1
    assert options_actions[0]["from_date"] == "2024-11-27"
    assert options_actions[0]["to_date"] == "2024-12-06"


# ---- compute_hygiene_plan: everything verified ------------------------------


@pytest.mark.asyncio
async def test_plan_fully_covered_yields_no_actions():
    db = FakeDB()
    seed_full_spot_coverage(db, "NIFTY", "2024-11-27", "2024-12-06")
    seed_contracts(db, "NIFTY", ["2024-11-28", "2024-12-05"])
    seed_option_candle_aggregate(db, [
        {"_id": "2024-11-28", "candles": 22000, "contracts": ["k1", "k2"]},
        {"_id": "2024-12-05", "candles": 24000, "contracts": ["k3", "k4"]},
    ])
    plan = await compute_hygiene_plan(
        db, start_date="2024-11-27", end_date="2024-12-06",
        instruments=["NIFTY"],
    )
    nifty = plan["instruments"][0]
    assert nifty["spot"]["status"] == "verified"
    assert nifty["contracts"]["status"] == "verified"
    assert nifty["option_candles"]["status"] == "verified"
    assert nifty["actions"] == []
    assert plan["summary"]["total_actions"] == 0
    assert plan["summary"]["overall_status"] == "verified"


# ---- execute order ----------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_calls_submitters_in_dependency_order():
    """Submitters must be called in order: spot -> contracts -> option_candles."""
    db = FakeDB()
    plan = {
        "id": "test-plan",
        "instruments": [
            {
                "instrument": "NIFTY",
                "actions": [
                    {"id": "spot_NIFTY", "kind": "spot", "instrument": "NIFTY",
                     "from_date": "2024-11-27", "to_date": "2024-12-06"},
                    {"id": "contracts_NIFTY", "kind": "contracts", "instrument": "NIFTY",
                     "from_date": "2024-11-27", "to_date": "2024-12-06"},
                    {"id": "options_NIFTY", "kind": "option_candles", "instrument": "NIFTY",
                     "from_date": "2024-11-27", "to_date": "2024-12-06",
                     "moneyness": ["atm"], "legs": ["CE", "PE"]},
                ],
            },
        ],
    }
    call_log: List[str] = []
    async def submit_spot(inst, fd, td, cd):
        call_log.append(f"spot:{inst}")
        return f"runs_spot_{inst}"
    async def submit_contracts(inst, fd, td):
        call_log.append(f"contracts:{inst}")
        return f"runs_contracts_{inst}"
    async def submit_option_candles(action):
        call_log.append(f"options:{action['instrument']}")
        return f"runs_options_{action['instrument']}"

    result = await execute_hygiene_plan(
        db, plan,
        submit_spot=submit_spot,
        submit_contracts=submit_contracts,
        submit_option_candles=submit_option_candles,
    )

    assert call_log == ["spot:NIFTY", "contracts:NIFTY", "options:NIFTY"]
    assert result["submitted_count"] == 3
    assert all(s.get("run_id") for s in result["submitted"])


@pytest.mark.asyncio
async def test_execute_handles_submitter_errors_without_aborting():
    """One submitter failing must not stop the others. Failures are recorded."""
    plan = {
        "id": "p1",
        "instruments": [
            {
                "instrument": "NIFTY",
                "actions": [
                    {"id": "spot_NIFTY", "kind": "spot", "instrument": "NIFTY",
                     "from_date": "2024-11-27", "to_date": "2024-12-06"},
                    {"id": "contracts_NIFTY", "kind": "contracts", "instrument": "NIFTY",
                     "from_date": "2024-11-27", "to_date": "2024-12-06"},
                ],
            },
        ],
    }
    async def submit_spot(*args, **kwargs):
        raise RuntimeError("simulated upstream failure")
    async def submit_contracts(*args, **kwargs):
        return "runs_contracts_NIFTY"
    async def submit_option_candles(*args, **kwargs):
        return "n/a"

    result = await execute_hygiene_plan(
        FakeDB(), plan,
        submit_spot=submit_spot,
        submit_contracts=submit_contracts,
        submit_option_candles=submit_option_candles,
    )
    assert result["submitted_count"] == 1
    assert len(result["errors"]) == 1
    assert "simulated" in result["errors"][0]["error"]
