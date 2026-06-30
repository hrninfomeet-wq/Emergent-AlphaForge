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
    default_scope_start,
    _expected_weekday_count,
)
from app.completeness import resolve_expiry_for_day, strike_band  # noqa: E402


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
        self.option_known_empty = FakeCollection()  # broker-empty ledger (empty by default)


# ---- helpers ----------------------------------------------------------------


def seed_full_spot_coverage(
    db: FakeDB, instrument: str, start_iso: str, end_iso: str,
    *, low: float = 24000.0, high: float = 24000.0, minutes: int = 375,
):
    """Add per-day spot aggregation rows for every weekday in the range.

    The plan's single candles_1m aggregation now returns
    {_id: date, count, low, high} per day — count drives spot coverage and
    low/high drive the option band-completeness check.
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
            agg_rows.append({"_id": date_str, "count": minutes, "low": low, "high": high})
        cur += timedelta(days=1)
    db.candles_1m._agg_result = list(agg_rows)


def weekdays_between(start_iso: str, end_iso: str):
    cur = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso)
    out = []
    while cur.date() <= end.date():
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def seed_full_band_coverage(
    db: FakeDB, start_iso: str, end_iso: str, expiries,
    *, low: float = 24000.0, high: float = 24000.0, step: int = 50,
    sides=("CE", "PE"), skip_pairs=(),
):
    """Seed the options_1m aggregation with the FULL daily ATM band — one
    distinct (date, expiry, side, strike) row per pair — minus `skip_pairs`
    ((date, side, strike) tuples) to simulate partially-covered days."""
    rows = []
    expiries_sorted = sorted(expiries)
    for day in weekdays_between(start_iso, end_iso):
        expiry = resolve_expiry_for_day(day, expiries_sorted)
        if not expiry:
            continue
        for strike in strike_band(low, high, step, pad_steps=1):
            for side in sides:
                if (day, side, strike) in skip_pairs:
                    continue
                rows.append({
                    "_id": {"date": day, "expiry": expiry, "side": side, "strike": float(strike)},
                    "candles": 375,
                })
    db.options_1m._agg_result = rows
    return rows


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
    """Pre-seed the raw aggregation result for options_1m -> _option_pairs_by_day."""
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
    seed_full_band_coverage(db, "2024-11-27", "2024-12-06", ["2024-11-28", "2024-12-05"])
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


# ---- compute_hygiene_plan: auto-update repairs under-captured days -----------


@pytest.mark.asyncio
async def test_plan_repairs_under_captured_recent_day(monkeypatch):
    """The daily AUTO-UPDATE runs compute_hygiene_plan. A recent CLOSED day that
    has SOME bars (a 'found date', so spot coverage stays verified) but is
    materially short must still get a spot repair action — the partial-session
    class (live roller died mid-day). Previously only the manual catch-up did."""
    import app.data_hygiene as dh
    monkeypatch.setattr(dh, "most_recent_closed_session", lambda *a, **k: "2026-06-29")
    db = FakeDB()
    seed_full_spot_coverage(db, "NIFTY", "2026-06-08", "2026-06-29")
    # one recent closed trading day captured only partially (5/375)
    for r in db.candles_1m._agg_result:
        if r["_id"] == "2026-06-22":
            r["count"] = 5
    plan = await compute_hygiene_plan(
        db, start_date="2026-06-08", end_date="2026-06-29", instruments=["NIFTY"],
    )
    nifty = plan["instruments"][0]
    spot_actions = [a for a in nifty["actions"] if a["kind"] == "spot"]
    assert spot_actions, "auto-update plan must repair the under-captured day"
    assert spot_actions[0]["from_date"] == "2026-06-22"
    assert "under-captured" in spot_actions[0]["reason"]
    assert nifty["spot"]["status"] != "verified"  # repair surfaced in the status


# ---- band completeness: the May-2026 class of gap ---------------------------


@pytest.mark.asyncio
async def test_plan_detects_partial_day_band_gap():
    """A day with candles for SOME band strikes but not all must produce an
    option_candles action (the old per-expiry heuristic reported verified)."""
    db = FakeDB()
    seed_full_spot_coverage(db, "NIFTY", "2024-11-27", "2024-12-06")
    seed_contracts(db, "NIFTY", ["2024-11-28", "2024-12-05"])
    # Full band everywhere EXCEPT one strike-day (the 2026-05-20 23550CE case,
    # distilled): 24050 CE missing on 2024-12-02.
    seed_full_band_coverage(
        db, "2024-11-27", "2024-12-06", ["2024-11-28", "2024-12-05"],
        skip_pairs={("2024-12-02", "CE", 24050)},
    )
    plan = await compute_hygiene_plan(
        db, start_date="2024-11-27", end_date="2024-12-06", instruments=["NIFTY"],
    )
    nifty = plan["instruments"][0]
    opt = nifty["option_candles"]
    assert opt["missing_pairs"] == 1
    assert opt["status"] != "verified"
    assert opt["missing_sample"][0] == {
        "date": "2024-12-02", "expiry": "2024-12-05", "side": "CE", "strike": 24050,
    }
    actions = [a for a in nifty["actions"] if a["kind"] == "option_candles"]
    assert len(actions) == 1
    assert "strike-day" in actions[0]["reason"]


# ---- rolling 9-month default scope -------------------------------------------


def test_default_scope_start_rolls_nine_months():
    assert default_scope_start("2026-06-12") == "2025-09-12"


def test_default_scope_start_floors_at_baseline():
    # 9 months before 2025-05-01 is 2024-08-01, which is before the project
    # baseline -> floored.
    assert default_scope_start("2025-05-01") == "2024-11-27"


def test_default_scope_start_caps_day_of_month():
    # 31st -> capped to 28 to avoid month-length issues.
    assert default_scope_start("2026-05-31") == "2025-08-28"


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


# ---- compute_catch_up_plan: incremental spot + option window ----------------

from app.data_hygiene import compute_catch_up_plan, most_recent_closed_session  # noqa: E402


def _ms(iso: str) -> int:
    # IST 15:30 close for the given date, expressed as epoch ms (UTC).
    dt = datetime.fromisoformat(iso).replace(tzinfo=IST, hour=15, minute=30)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


@pytest.mark.asyncio
async def test_catch_up_plan_proposes_window_from_last_stored_date():
    db = FakeDB()
    # NIFTY last stored 2026-06-02 (Tue). Target end Fri 2026-06-05 (after close).
    db.candles_1m._agg_result = [{"_id": None, "max_ts": _ms("2026-06-02")}]
    now = datetime(2026, 6, 5, 16, 0, tzinfo=IST)
    plan = await compute_catch_up_plan(db, instruments=["NIFTY"], now_ist=now)
    nifty = plan["instruments"][0]
    assert nifty["last_spot_date"] == "2026-06-02"
    assert nifty["from_date"] == "2026-06-03"   # day after last stored
    assert nifty["to_date"] == "2026-06-05"
    assert nifty["up_to_date"] is False
    kinds = sorted(a["kind"] for a in nifty["actions"])
    assert kinds == ["contracts", "option_candles", "spot"]


@pytest.mark.asyncio
async def test_catch_up_plan_up_to_date_yields_no_actions():
    db = FakeDB()
    # Last stored is the most recent closed session already.
    db.candles_1m._agg_result = [{"_id": None, "max_ts": _ms("2026-06-05")}]
    now = datetime(2026, 6, 5, 16, 0, tzinfo=IST)
    plan = await compute_catch_up_plan(db, instruments=["NIFTY"], now_ist=now)
    nifty = plan["instruments"][0]
    assert nifty["up_to_date"] is True
    assert nifty["actions"] == []
    assert plan["summary"]["total_actions"] == 0


@pytest.mark.asyncio
async def test_catch_up_plan_empty_warehouse_uses_fallback_start():
    db = FakeDB()
    db.candles_1m._agg_result = []  # no stored candles
    now = datetime(2026, 6, 5, 16, 0, tzinfo=IST)
    plan = await compute_catch_up_plan(
        db, instruments=["NIFTY"], now_ist=now, fallback_start_date="2024-11-27",
    )
    nifty = plan["instruments"][0]
    assert nifty["last_spot_date"] is None
    assert nifty["from_date"] == "2024-11-27"
    assert nifty["up_to_date"] is False


def test_most_recent_closed_session_intraday_targets_yesterday():
    # Friday 10:00 IST: market still open, so most recent closed session is Thu.
    now = datetime(2026, 6, 5, 10, 0, tzinfo=IST)
    assert most_recent_closed_session(now) == "2026-06-04"


def test_most_recent_closed_session_weekend_targets_friday():
    now = datetime(2026, 6, 7, 12, 0, tzinfo=IST)  # Sunday
    assert most_recent_closed_session(now) == "2026-06-05"


def test_fetch_items_from_missing_pairs_groups_and_resolves():
    """The band-driven fetch must request EXACTLY the missing (day, expiry,
    side, strike) pairs, grouped per contract with the missing days as
    fetch_dates. Regression for the permanent-degraded bug: the per-day ATM
    moneyness preview never fetched intraday-wick / band-edge strikes the
    completeness band demanded, even though the broker had the candles."""
    from app.data_hygiene import fetch_items_from_missing_pairs

    # Two missing days for the 25200 CE (the wick/pad strike) + one day for a
    # PE; one pair has no resolvable contract.
    missing = [
        ("2025-09-15", "2025-09-16", "CE", 25200),
        ("2025-09-12", "2025-09-16", "CE", 25200),
        ("2025-09-15", "2025-09-16", "PE", 25000),
        ("2025-09-15", "2025-09-16", "CE", 99999),  # no contract -> unresolved
    ]
    contract_map = {
        ("2025-09-16", "CE", 25200): {"instrument_key": "NSE_FO|44730|16-09-2025", "trading_symbol": "NIFTY 25200 CE", "lot_size": 25},
        ("2025-09-16", "PE", 25000): {"instrument_key": "NSE_FO|44900|16-09-2025", "trading_symbol": "NIFTY 25000 PE", "lot_size": 25},
    }

    out = fetch_items_from_missing_pairs(missing, contract_map, underlying="NIFTY")
    items = {(i["side"], i["strike"]): i for i in out["items"]}

    assert len(out["items"]) == 2
    ce = items[("CE", 25200)]
    assert ce["instrument_key"] == "NSE_FO|44730|16-09-2025"
    assert ce["needs_fetch"] is True
    # Both missing days requested, sorted+deduped — 09-15 is no longer dropped.
    assert ce["fetch_dates"] == ["2025-09-12", "2025-09-15"]
    assert ce["expiry_date"] == "2025-09-16"

    pe = items[("PE", 25000)]
    assert pe["fetch_dates"] == ["2025-09-15"]

    # The strike with no stored contract is surfaced, not silently dropped.
    assert len(out["unresolved_contracts"]) == 1
    assert out["unresolved_contracts"][0]["strike"] == 99999


def test_fetch_items_from_missing_pairs_empty():
    from app.data_hygiene import fetch_items_from_missing_pairs

    out = fetch_items_from_missing_pairs([], {}, underlying="NIFTY")
    assert out["items"] == []
    assert out["unresolved_contracts"] == []
