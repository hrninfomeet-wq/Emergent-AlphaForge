"""Data Hygiene workflow.

Higher-level orchestration that drives the existing background ingest jobs
to bring the local warehouse to a known-good state across multiple instruments
in the right order. Pure data layer - no broker calls of its own; it composes
the existing ingest helpers.

Order of operations enforced by execute_hygiene_plan:
  1. Spot candles for all requested instruments (small, fast, parallel-safe)
  2. Option contract metadata sync + expired-contract backfill
  3. Option candles (largest, sequential per instrument so token rate is bounded)

Re-running the same plan only fetches what is still missing - the diff against
the warehouse is recomputed each time, so partial failures resume cleanly.

Per user spec (2026-05-27):
  - Default scope: 2024-11-27 to today, NIFTY+BANKNIFTY+SENSEX, ATM CE+PE only
  - Add OTM1/ITM1 only when the strategy needs it (out of v1 scope)
  - Source of truth is option_contracts.expiry_date, never a hardcoded weekday
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.nse_calendar import (
    expected_trading_days as _calendar_expected_trading_days,
    holidays_in_range,
    is_trading_day,
    trading_days_in_range,
)

log = logging.getLogger(__name__)

DEFAULT_START_DATE = "2024-11-27"
DEFAULT_INSTRUMENTS = ("NIFTY", "BANKNIFTY", "SENSEX")
DEFAULT_MONEYNESS: tuple = ("atm",)
DEFAULT_LEGS: tuple = ("CE", "PE")
DEFAULT_SAMPLE_INTERVAL_MIN = 1
IST_OFFSET = timedelta(hours=5, minutes=30)


def _today_ist_iso() -> str:
    return (datetime.now(timezone.utc) + IST_OFFSET).strftime("%Y-%m-%d")


def _now_ist() -> datetime:
    return datetime.now(timezone.utc) + IST_OFFSET


def most_recent_closed_session(now_ist: Optional[datetime] = None) -> Optional[str]:
    """Return the ISO date of the most recent *closed* trading session.

    A session counts as closed once it is in the past, or it is today and the
    wall clock is at/after 15:30 IST. Upstox historical returns empty for the
    in-progress day, so an incremental catch-up should target this date as its
    upper bound. Returns None if no trading day is found in the lookback.
    """
    now = now_ist or _now_ist()
    today_iso = now.strftime("%Y-%m-%d")
    market_closed_today = (now.hour, now.minute) >= (15, 30)
    # Walk backwards from today up to ~10 calendar days to find a trading day.
    cur = date.fromisoformat(today_iso)
    for _ in range(12):
        iso = cur.isoformat()
        if is_trading_day(iso):
            if iso < today_iso or (iso == today_iso and market_closed_today):
                return iso
        cur = cur - timedelta(days=1)
    return None


def _expected_weekday_count(start_iso: str, end_iso: str) -> int:
    """Count trading days (weekdays minus published NSE holidays) inclusive."""
    return _calendar_expected_trading_days(start_iso, end_iso)


async def _spot_coverage(db: Any, instrument: str, start_iso: str, end_iso: str) -> Dict[str, Any]:
    """Count distinct trading days with candles in the window for an instrument.

    Uses a Mongo aggregation that derives the IST date from each candle's
    timestamp server-side and groups by it, so we never pull hundreds of
    thousands of rows into Python. The (instrument, ts) index supports the match.
    """
    instrument = instrument.upper()
    pipeline = [
        {"$match": {"instrument": instrument}},
        {"$project": {
            "date": {
                "$dateToString": {
                    "format": "%Y-%m-%d",
                    "timezone": "Asia/Kolkata",
                    "date": {"$toDate": "$ts"},
                }
            },
        }},
        {"$group": {"_id": "$date"}},
    ]
    distinct_dates: set[str] = set()
    rows = await db.candles_1m.aggregate(pipeline).to_list(length=None)
    for doc in rows:
        d = doc.get("_id")
        if d:
            distinct_dates.add(str(d))
    in_window = {d for d in distinct_dates if start_iso <= d <= end_iso}
    expected = _expected_weekday_count(start_iso, end_iso)
    return {
        "instrument": instrument,
        "expected_weekdays": expected,
        "found_dates": len(in_window),
        "coverage_pct": round((len(in_window) / max(1, expected)) * 100, 1),
        "first_date": min(in_window) if in_window else None,
        "last_date": max(in_window) if in_window else None,
    }


async def _option_contracts_summary(db: Any, instrument: str, start_iso: str, end_iso: str) -> Dict[str, Any]:
    """Summarize option contract metadata coverage per instrument."""
    instrument = instrument.upper()
    expiries = await db.option_contracts.distinct(
        "expiry_date",
        {"underlying": instrument},
    )
    expiries = sorted(e for e in expiries if e)
    in_window = [e for e in expiries if start_iso <= e <= end_iso]
    today = _today_ist_iso()
    upcoming = [e for e in expiries if e >= today]
    return {
        "instrument": instrument,
        "total_expiries": len(expiries),
        "expiries_in_window": len(in_window),
        "first_expiry_in_window": in_window[0] if in_window else None,
        "last_expiry_in_window": in_window[-1] if in_window else None,
        "upcoming_count": len(upcoming),
    }


async def _option_candles_summary(db: Any, instrument: str, start_iso: str, end_iso: str) -> Dict[str, Any]:
    """Summarize option-candle coverage per instrument.

    `options_1m` already stores `underlying` and `expiry_date` on each candle
    (set at fetch time), so we can group directly on those fields. The
    (underlying, expiry_date, strike, side, ts) index supports the match,
    avoiding the previous full-collection `$lookup` join over 5M+ docs.
    """
    instrument = instrument.upper()
    pipeline = [
        {"$match": {
            "underlying": instrument,
            "expiry_date": {"$gte": start_iso, "$lte": end_iso},
        }},
        {"$group": {
            "_id": "$expiry_date",
            "candles": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = await db.options_1m.aggregate(pipeline).to_list(length=None)
    total_candles = sum(int(r.get("candles") or 0) for r in rows)
    expiries_with_data: List[str] = [str(r["_id"]) for r in rows if r.get("_id")]
    return {
        "instrument": instrument,
        "total_candles": total_candles,
        "expiries_with_data": len(expiries_with_data),
        "first_expiry_with_data": expiries_with_data[0] if expiries_with_data else None,
        "last_expiry_with_data": expiries_with_data[-1] if expiries_with_data else None,
    }


def _coverage_status(pct: float, *, ok: float = 95.0, warn: float = 75.0) -> str:
    if pct >= ok:
        return "verified"
    if pct >= warn:
        return "warning"
    return "degraded"


async def compute_hygiene_plan(
    db: Any,
    *,
    start_date: str = DEFAULT_START_DATE,
    end_date: Optional[str] = None,
    instruments: Optional[List[str]] = None,
    moneyness: Optional[List[str]] = None,
    legs: Optional[List[str]] = None,
    sample_interval_minutes: int = DEFAULT_SAMPLE_INTERVAL_MIN,
) -> Dict[str, Any]:
    """Compute the data-hygiene plan for the given date window.

    Returns a structured report:
      {
        "id": str,
        "computed_at": iso,
        "window": {"start": ..., "end": ...},
        "instruments": [
          {
            "instrument": "NIFTY",
            "spot": {expected_weekdays, found_dates, coverage_pct, status, gap_days, ...},
            "contracts": {total_expiries, expiries_in_window, status, ...},
            "option_candles": {total_candles, expiries_with_data, status, ...},
            "actions": [...]   # ordered list of suggested fetches with eta hints
          },
          ...
        ],
        "summary": {overall_status, total_actions, ...}
      }

    Pure read - never mutates the warehouse.
    """
    end_date = end_date or _today_ist_iso()
    insts = [str(i).upper() for i in (instruments or DEFAULT_INSTRUMENTS) if i]
    money = list(moneyness or DEFAULT_MONEYNESS)
    legs_list = list(legs or DEFAULT_LEGS)

    inst_reports: List[Dict[str, Any]] = []
    total_actions = 0
    worst_status = "verified"

    for inst in insts:
        spot = await _spot_coverage(db, inst, start_date, end_date)
        contracts = await _option_contracts_summary(db, inst, start_date, end_date)
        opt = await _option_candles_summary(db, inst, start_date, end_date)

        spot_status = _coverage_status(spot["coverage_pct"])
        contract_status = "verified" if contracts["expiries_in_window"] > 0 else "degraded"
        # Heuristic: option candle coverage in window is "verified" if at least one
        # expiry has data AND first/last expiries with data span at least 60% of window
        opt_status = "verified"
        if opt["expiries_with_data"] == 0:
            opt_status = "degraded"
        elif contracts["expiries_in_window"] > 0:
            ratio = opt["expiries_with_data"] / max(1, contracts["expiries_in_window"])
            if ratio < 0.6:
                opt_status = "warning"

        actions: List[Dict[str, Any]] = []
        # Spot fetch action
        if spot_status != "verified":
            actions.append({
                "id": f"spot_{inst}",
                "kind": "spot",
                "instrument": inst,
                "from_date": start_date,
                "to_date": end_date,
                "reason": f"Spot coverage {spot['coverage_pct']}% (expected {spot['expected_weekdays']} weekdays, found {spot['found_dates']})",
                "eta_minutes": max(2, spot["expected_weekdays"] // 30),  # rough heuristic
            })
        # Contracts sync action
        if contract_status != "verified":
            actions.append({
                "id": f"contracts_{inst}",
                "kind": "contracts",
                "instrument": inst,
                "from_date": start_date,
                "to_date": end_date,
                "reason": "No option_contracts in window. Run expired-contract backfill.",
                "eta_minutes": 5,
            })
        # Option candles fetch action - only if contracts exist
        if contract_status == "verified" and opt_status != "verified":
            actions.append({
                "id": f"options_{inst}",
                "kind": "option_candles",
                "instrument": inst,
                "from_date": start_date,
                "to_date": end_date,
                "moneyness": money,
                "legs": legs_list,
                "sample_interval_minutes": sample_interval_minutes,
                "reason": (
                    f"Option candle coverage low ({opt['expiries_with_data']} of "
                    f"{contracts['expiries_in_window']} window expiries have data)"
                ),
                "eta_minutes": max(15, contracts["expiries_in_window"] * 5),
            })

        total_actions += len(actions)
        for s in (spot_status, contract_status, opt_status):
            if s == "degraded" and worst_status != "degraded":
                worst_status = "degraded"
            elif s == "warning" and worst_status == "verified":
                worst_status = "warning"

        inst_reports.append({
            "instrument": inst,
            "spot": {**spot, "status": spot_status},
            "contracts": {**contracts, "status": contract_status},
            "option_candles": {**opt, "status": opt_status},
            "actions": actions,
        })

    return {
        "id": str(uuid.uuid4()),
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start_date, "end": end_date},
        "scope": {"moneyness": money, "legs": legs_list, "sample_interval_minutes": sample_interval_minutes},
        "instruments": inst_reports,
        "summary": {
            "overall_status": worst_status,
            "total_actions": total_actions,
            "instruments_count": len(insts),
        },
    }


async def _last_spot_date(db: Any, instrument: str) -> Optional[str]:
    """Return the most recent IST date that has any stored spot candle, or None."""
    instrument = instrument.upper()
    pipeline = [
        {"$match": {"instrument": instrument}},
        {"$group": {"_id": None, "max_ts": {"$max": "$ts"}}},
    ]
    rows = await db.candles_1m.aggregate(pipeline).to_list(length=1)
    if not rows or rows[0].get("max_ts") is None:
        return None
    max_ts = int(rows[0]["max_ts"])
    dt = datetime.fromtimestamp(max_ts / 1000, timezone.utc) + IST_OFFSET
    return dt.strftime("%Y-%m-%d")


async def compute_catch_up_plan(
    db: Any,
    *,
    instruments: Optional[List[str]] = None,
    moneyness: Optional[List[str]] = None,
    legs: Optional[List[str]] = None,
    sample_interval_minutes: int = DEFAULT_SAMPLE_INTERVAL_MIN,
    fallback_start_date: str = DEFAULT_START_DATE,
    now_ist: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build an *incremental* catch-up plan per instrument.

    Unlike `compute_hygiene_plan`, which always diffs against the full fixed
    scope (2024-11-27 -> today) and is expensive to re-run daily, this targets
    only the gap between each instrument's last stored spot date and the most
    recent closed trading session. For each instrument with a real gap it emits
    spot + contracts + option_candles actions over that small window so both
    spot and the corresponding option data are refreshed together.

    The upper bound is the most recently *closed* session (Upstox historical is
    empty for the in-progress day; today's bars arrive via the live roller), so
    the plan never chases data the broker cannot yet return.
    """
    insts = [str(i).upper() for i in (instruments or DEFAULT_INSTRUMENTS) if i]
    money = list(moneyness or DEFAULT_MONEYNESS)
    legs_list = list(legs or DEFAULT_LEGS)
    target_end = most_recent_closed_session(now_ist)

    inst_reports: List[Dict[str, Any]] = []
    total_actions = 0

    for inst in insts:
        last_date = await _last_spot_date(db, inst)
        # Start the day after the last stored session; if the warehouse is empty
        # for this instrument, fall back to the configured baseline start date.
        if last_date:
            start_dt = date.fromisoformat(last_date) + timedelta(days=1)
            from_date = start_dt.isoformat()
        else:
            from_date = fallback_start_date

        if not target_end or from_date > target_end:
            inst_reports.append({
                "instrument": inst,
                "last_spot_date": last_date,
                "from_date": from_date,
                "to_date": target_end,
                "up_to_date": True,
                "missing_trading_days": 0,
                "actions": [],
            })
            continue

        missing_days = trading_days_in_range(from_date, target_end)
        if not missing_days:
            inst_reports.append({
                "instrument": inst,
                "last_spot_date": last_date,
                "from_date": from_date,
                "to_date": target_end,
                "up_to_date": True,
                "missing_trading_days": 0,
                "actions": [],
            })
            continue

        actions: List[Dict[str, Any]] = [
            {
                "id": f"spot_{inst}",
                "kind": "spot",
                "instrument": inst,
                "from_date": from_date,
                "to_date": target_end,
                "reason": f"{len(missing_days)} trading day(s) missing since {last_date or 'inception'}",
                "eta_minutes": 2,
            },
            {
                "id": f"contracts_{inst}",
                "kind": "contracts",
                "instrument": inst,
                "from_date": from_date,
                "to_date": target_end,
                "reason": "Sync option contracts covering the catch-up window",
                "eta_minutes": 3,
            },
            {
                "id": f"options_{inst}",
                "kind": "option_candles",
                "instrument": inst,
                "from_date": from_date,
                "to_date": target_end,
                "moneyness": money,
                "legs": legs_list,
                "sample_interval_minutes": sample_interval_minutes,
                "reason": f"Fetch ATM option candles for {len(missing_days)} new session(s)",
                "eta_minutes": max(5, len(missing_days) * 2),
            },
        ]
        total_actions += len(actions)
        inst_reports.append({
            "instrument": inst,
            "last_spot_date": last_date,
            "from_date": from_date,
            "to_date": target_end,
            "up_to_date": False,
            "missing_trading_days": len(missing_days),
            "actions": actions,
        })

    return {
        "id": str(uuid.uuid4()),
        "mode": "catch_up",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start": None, "end": target_end},
        "scope": {"moneyness": money, "legs": legs_list, "sample_interval_minutes": sample_interval_minutes},
        "instruments": inst_reports,
        "summary": {
            "overall_status": "verified" if total_actions == 0 else "warning",
            "total_actions": total_actions,
            "instruments_count": len(insts),
            "target_end": target_end,
        },
    }


async def _ensure_run_doc(db: Any, run_id: str, plan_action: Dict[str, Any], now: str) -> None:
    """Insert the warehouse_runs doc that the existing job loops will update."""
    base = {
        "id": run_id,
        "instrument": plan_action.get("instrument"),
        "source": "data_hygiene",
        "started_at": now,
        "updated_at": now,
        "status": "queued",
        "from_date": plan_action.get("from_date"),
        "to_date": plan_action.get("to_date"),
        "kind": plan_action.get("kind"),
        "progress_pct": 0,
        "data_hygiene_action_id": plan_action.get("id"),
    }
    await db.warehouse_runs.insert_one(base)


async def execute_hygiene_plan(
    db: Any,
    plan: Dict[str, Any],
    *,
    submit_spot: Any,                 # async fn (instrument, from_date, to_date, chunk_days) -> run_id
    submit_contracts: Any,            # async fn (instrument, from_date, to_date) -> run_id
    submit_option_candles: Any,       # async fn (action_dict) -> run_id  (handles its own preview)
    chunk_days_spot: int = 30,
) -> Dict[str, Any]:
    """Submit background jobs in dependency order: spot -> contracts -> option_candles.

    Each `submit_*` callable is injected so this module stays pure and testable.
    The real implementations live in server.py (they call upstox_client, etc).

    Returns:
      {
        "plan_id": ...,
        "submitted": [
          {"action_id": ..., "kind": "spot|contracts|option_candles",
           "instrument": ..., "run_id": ...}
        ],
        "submitted_count": N
      }
    """
    submitted: List[Dict[str, Any]] = []
    by_kind: Dict[str, List[Dict[str, Any]]] = {"spot": [], "contracts": [], "option_candles": []}
    for inst_report in plan.get("instruments", []) or []:
        for action in inst_report.get("actions", []) or []:
            kind = str(action.get("kind") or "")
            if kind in by_kind:
                by_kind[kind].append(action)

    # 1. Spot fetches first (independent and small)
    for action in by_kind["spot"]:
        try:
            run_id = await submit_spot(
                action["instrument"], action["from_date"], action["to_date"], chunk_days_spot,
            )
            submitted.append({
                "action_id": action["id"], "kind": "spot",
                "instrument": action["instrument"], "run_id": str(run_id),
            })
        except Exception as exc:
            log.exception("data hygiene: spot submit failed for %s", action.get("instrument"))
            submitted.append({
                "action_id": action.get("id"), "kind": "spot",
                "instrument": action.get("instrument"), "error": str(exc)[:240],
            })

    # 2. Option contract metadata sync
    for action in by_kind["contracts"]:
        try:
            run_id = await submit_contracts(
                action["instrument"], action["from_date"], action["to_date"],
            )
            submitted.append({
                "action_id": action["id"], "kind": "contracts",
                "instrument": action["instrument"], "run_id": str(run_id),
            })
        except Exception as exc:
            log.exception("data hygiene: contracts submit failed for %s", action.get("instrument"))
            submitted.append({
                "action_id": action.get("id"), "kind": "contracts",
                "instrument": action.get("instrument"), "error": str(exc)[:240],
            })

    # 3. Option candles (largest; sequential per call so broker rate is bounded)
    for action in by_kind["option_candles"]:
        try:
            run_id = await submit_option_candles(action)
            submitted.append({
                "action_id": action["id"], "kind": "option_candles",
                "instrument": action["instrument"], "run_id": str(run_id),
            })
        except Exception as exc:
            log.exception("data hygiene: option_candles submit failed for %s", action.get("instrument"))
            submitted.append({
                "action_id": action.get("id"), "kind": "option_candles",
                "instrument": action.get("instrument"), "error": str(exc)[:240],
            })

    return {
        "plan_id": plan.get("id"),
        "submitted": submitted,
        "submitted_count": sum(1 for s in submitted if s.get("run_id")),
        "errors": [s for s in submitted if s.get("error")],
    }


async def hygiene_status(db: Any, plan_id: Optional[str] = None) -> Dict[str, Any]:
    """List recent data-hygiene runs and their progress.

    If plan_id is given, returns only runs that were submitted as part of that plan
    (by linking via warehouse_runs.data_hygiene_action_id; but since we cannot
    re-derive plan->action mapping after the fact, we surface all data_hygiene runs
    sorted by recency and let the caller filter).
    """
    cursor = db.warehouse_runs.find(
        {"source": "data_hygiene"},
        {"_id": 0},
    ).sort("updated_at", -1).limit(50)
    rows = await cursor.to_list(length=50)
    return {
        "plan_id": plan_id,
        "items": rows,
        "count": len(rows),
    }
