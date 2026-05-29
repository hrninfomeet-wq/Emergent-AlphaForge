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

log = logging.getLogger(__name__)

DEFAULT_START_DATE = "2024-11-27"
DEFAULT_INSTRUMENTS = ("NIFTY", "BANKNIFTY", "SENSEX")
DEFAULT_MONEYNESS: tuple = ("atm",)
DEFAULT_LEGS: tuple = ("CE", "PE")
DEFAULT_SAMPLE_INTERVAL_MIN = 1
IST_OFFSET = timedelta(hours=5, minutes=30)


def _today_ist_iso() -> str:
    return (datetime.now(timezone.utc) + IST_OFFSET).strftime("%Y-%m-%d")


def _expected_weekday_count(start_iso: str, end_iso: str) -> int:
    cur = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    n = 0
    while cur <= end:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


async def _spot_coverage(db: Any, instrument: str, start_iso: str, end_iso: str) -> Dict[str, Any]:
    """Count distinct trading days with candles in the window for an instrument.

    Avoids relying on session_date (some rows lack it) by deriving the date
    from each candle's IST-localized timestamp.
    """
    instrument = instrument.upper()
    cursor = db.candles_1m.find(
        {"instrument": instrument},
        {"_id": 0, "ts": 1, "session_date": 1},
    )
    rows = await cursor.to_list(length=None)
    distinct_dates: set[str] = set()
    for r in rows:
        d = r.get("session_date")
        if d:
            distinct_dates.add(str(d))
            continue
        ts = r.get("ts")
        if ts is None:
            continue
        try:
            d = (datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc) + IST_OFFSET).strftime("%Y-%m-%d")
            distinct_dates.add(d)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
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
    """Summarize option-candle coverage by joining options_1m -> option_contracts."""
    instrument = instrument.upper()
    pipeline = [
        {"$lookup": {
            "from": "option_contracts",
            "localField": "instrument_key",
            "foreignField": "instrument_key",
            "as": "contract",
        }},
        {"$unwind": "$contract"},
        {"$match": {
            "contract.underlying": instrument,
            "contract.expiry_date": {"$gte": start_iso, "$lte": end_iso},
        }},
        {"$group": {
            "_id": "$contract.expiry_date",
            "candles": {"$sum": 1},
            "contracts": {"$addToSet": "$instrument_key"},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = await db.options_1m.aggregate(pipeline).to_list(length=None)
    total_candles = sum(int(r.get("candles") or 0) for r in rows)
    total_contracts = 0
    expiries_with_data: List[str] = []
    for r in rows:
        contracts = r.get("contracts") or []
        total_contracts += len(contracts)
        if r.get("_id"):
            expiries_with_data.append(str(r["_id"]))
    return {
        "instrument": instrument,
        "total_candles": total_candles,
        "total_contracts_with_data": total_contracts,
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
