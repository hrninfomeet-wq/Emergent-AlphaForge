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
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.nse_calendar import (
    expected_trading_days as _calendar_expected_trading_days,
    holidays_in_range,
    is_trading_day,
    trading_days_in_range,
)
from app.completeness import band_completeness, missing_band_pairs
from app.options_universe import strike_step_for

log = logging.getLogger(__name__)

DEFAULT_START_DATE = "2024-11-27"  # hard floor — never audit earlier than this
# Rolling completeness window (user decision 2026-06-12): guarantee a complete
# warehouse over the last N calendar months; older data is kept but no longer
# audited/fetched by default.
ROLLING_SCOPE_MONTHS = 9
DEFAULT_INSTRUMENTS = ("NIFTY", "BANKNIFTY", "SENSEX")
# atm + otm1 + itm1 ensures the per-minute ATM fetch path covers the daily
# strike band with +/-1 step of drift headroom (matches completeness.strike_band
# with pad_steps=1).
DEFAULT_MONEYNESS: tuple = ("atm", "otm1", "itm1")
DEFAULT_LEGS: tuple = ("CE", "PE")
DEFAULT_SAMPLE_INTERVAL_MIN = 1
BAND_PAD_STEPS = 1
MIN_SPOT_MINUTES_TO_JUDGE = 60


def default_scope_start(today_iso: Optional[str] = None) -> str:
    """Start of the rolling 9-month completeness window, floored at the project
    baseline. Day-of-month is capped at 28 to avoid month-length edge cases."""
    if today_iso:
        today = date.fromisoformat(today_iso)
    else:
        today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()
    year, month = today.year, today.month - ROLLING_SCOPE_MONTHS
    while month <= 0:
        month += 12
        year -= 1
    start = date(year, month, min(today.day, 28)).isoformat()
    return max(start, DEFAULT_START_DATE)
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


async def _spot_day_rows(db: Any, instrument: str) -> List[Dict[str, Any]]:
    """Per-IST-day spot summary rows: [{date, count, low, high}].

    ONE aggregation per instrument serves both spot coverage and the option
    band-completeness check (the day's low/high defines the strike band the
    warehouse must hold). The (instrument, ts) index supports the match; the
    grouping happens server-side so we never pull raw rows into Python.
    """
    instrument = instrument.upper()
    pipeline = [
        {"$match": {"instrument": instrument}},
        {"$project": {
            "low": 1,
            "high": 1,
            "date": {
                "$dateToString": {
                    "format": "%Y-%m-%d",
                    "timezone": "Asia/Kolkata",
                    "date": {"$toDate": "$ts"},
                }
            },
        }},
        {"$group": {
            "_id": "$date",
            "count": {"$sum": 1},
            "low": {"$min": "$low"},
            "high": {"$max": "$high"},
        }},
    ]
    rows = await db.candles_1m.aggregate(pipeline).to_list(length=None)
    out: List[Dict[str, Any]] = []
    for doc in rows:
        d = doc.get("_id")
        if d:
            out.append({
                "date": str(d),
                "count": int(doc.get("count") or 0),
                "low": doc.get("low"),
                "high": doc.get("high"),
            })
    return sorted(out, key=lambda r: r["date"])


def _spot_coverage_from_rows(
    day_rows: List[Dict[str, Any]], instrument: str, start_iso: str, end_iso: str,
) -> Dict[str, Any]:
    """Spot coverage summary computed from the shared per-day rows (pure)."""
    in_window = {r["date"] for r in day_rows if start_iso <= r["date"] <= end_iso}
    expected = _expected_weekday_count(start_iso, end_iso)
    return {
        "instrument": instrument.upper(),
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
        # For band completeness: every known expiry on/after the window start,
        # so days near the window edge resolve their next upcoming expiry.
        "expiries_sorted_from_start": [e for e in expiries if e >= start_iso],
    }


# Expiries up to this many days after the window end still satisfy days near
# the window edge that resolve to the next upcoming expiry.
_EXPIRY_LOOKAHEAD_DAYS = 45


async def _option_pairs_by_day(
    db: Any, instrument: str, start_iso: str, end_iso: str,
) -> Dict[str, Any]:
    """Stored option coverage as exact (day, expiry, side, strike) pairs.

    This is what the band-completeness diff consumes. `options_1m` embeds
    `underlying`/`expiry_date`/`strike`/`side` on every candle (set at fetch
    time), so ONE server-side group on the (underlying, expiry_date, strike,
    side, ts) index produces the distinct pairs — no `$lookup`, no raw rows
    in Python. (The old per-expiry candle-count heuristic lived here; it could
    not see partially-covered days and reported verified-but-incomplete.)
    """
    instrument = instrument.upper()
    lookahead = (date.fromisoformat(end_iso) + timedelta(days=_EXPIRY_LOOKAHEAD_DAYS)).isoformat()
    pipeline = [
        {"$match": {
            "underlying": instrument,
            "expiry_date": {"$gte": start_iso, "$lte": lookahead},
        }},
        {"$group": {
            "_id": {
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "timezone": "Asia/Kolkata",
                        "date": {"$toDate": "$ts"},
                    }
                },
                "expiry": "$expiry_date",
                "side": "$side",
                "strike": "$strike",
            },
            "candles": {"$sum": 1},
        }},
    ]
    rows = await db.options_1m.aggregate(pipeline).to_list(length=None)
    stored_pairs: set = set()
    total_candles = 0
    expiries_with_data: set = set()
    for doc in rows:
        key = doc.get("_id") or {}
        day = str(key.get("date") or "")
        expiry = str(key.get("expiry") or "")
        side = str(key.get("side") or "").upper()
        try:
            strike = int(float(key.get("strike")))
        except (TypeError, ValueError):
            continue
        if not day or not expiry or not side:
            continue
        stored_pairs.add((day, expiry, side, strike))
        total_candles += int(doc.get("candles") or 0)
        expiries_with_data.add(expiry)
    return {
        "stored_pairs": stored_pairs,
        "total_candles": total_candles,
        "expiries_with_data": len(expiries_with_data),
    }


def _coverage_status(pct: float, *, ok: float = 95.0, warn: float = 75.0) -> str:
    if pct >= ok:
        return "verified"
    if pct >= warn:
        return "warning"
    return "degraded"


def fetch_items_from_missing_pairs(
    missing: Sequence[Tuple[str, str, str, int]],
    contract_map: Dict[Tuple[str, str, int], Dict[str, Any]],
    *,
    underlying: str,
) -> Dict[str, Any]:
    """Group missing (day, expiry, side, strike) band pairs into per-contract
    fetch items and resolve each to a stored option contract.

    Pure (no I/O) so it is unit-testable. Each item carries `needs_fetch=True`
    and the exact `fetch_dates` the completeness band reports missing for that
    contract — `option_fetch_tasks_from_plan` turns these into contract/date
    fetch tasks. Pairs whose contract is not in `contract_map` (or has no
    instrument_key) are reported under `unresolved_contracts` instead of being
    silently dropped (those are a contracts-sync gap, not a candle gap).
    """
    by_contract: Dict[Tuple[str, str, int], List[str]] = {}
    for day, expiry, side, strike in missing:
        by_contract.setdefault((str(expiry), str(side).upper(), int(strike)), []).append(str(day))

    items: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    for (expiry, side, strike), days in sorted(by_contract.items()):
        contract = contract_map.get((expiry, side, strike))
        if not contract or not contract.get("instrument_key"):
            unresolved.append({"expiry": expiry, "side": side, "strike": strike, "days": len(set(days))})
            continue
        items.append({
            "instrument_key": contract["instrument_key"],
            "underlying": underlying,
            "expiry_date": expiry,
            "strike": strike,
            "side": side,
            "trading_symbol": contract.get("trading_symbol", ""),
            "lot_size": contract.get("lot_size"),
            "needs_fetch": True,
            "fetch_dates": sorted(set(days)),
        })
    return {"items": items, "unresolved_contracts": unresolved}


# ---------------------------------------------------------------------------
# Broker-empty ledger (option_known_empty)
#
# Some band pairs are unfixable: the contract existed, the fetch succeeded,
# and Upstox returned zero candles (thin strikes the exchange never traded /
# the broker never archived). Without a ledger those pairs generate "Fill
# gaps" actions forever and pin the hygiene status at amber even though there
# is nothing anyone can do. After every band-driven fetch we record the pairs
# that were cleanly requested yet still have no candles; the plan and the
# fetch builder exclude them from then on and report them as
# `broker_empty_pairs` instead of `missing_pairs`.
# ---------------------------------------------------------------------------

KNOWN_EMPTY_COLLECTION = "option_known_empty"


async def load_known_empty_pairs(
    db: Any, instrument: str, start_iso: str, end_iso: str
) -> Set[Tuple[str, str, str, int]]:
    """Load the broker-empty ledger for one instrument/window as band tuples."""
    rows = await db.option_known_empty.find(
        {"underlying": instrument.upper(), "date": {"$gte": start_iso, "$lte": end_iso}},
        {"_id": 0, "date": 1, "expiry": 1, "side": 1, "strike": 1},
    ).to_list(length=None)
    out: Set[Tuple[str, str, str, int]] = set()
    for r in rows:
        try:
            out.add((str(r["date"]), str(r["expiry"]), str(r["side"]).upper(), int(float(r["strike"]))))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def pairs_from_band_plan_items(items: Sequence[Dict[str, Any]]) -> Dict[Tuple[str, str, str, int], str]:
    """(day, expiry, side, strike) -> instrument_key for every pair a band
    fetch plan actually requested. Pure (unit-testable)."""
    out: Dict[Tuple[str, str, str, int], str] = {}
    for item in items:
        try:
            expiry = str(item["expiry_date"])
            side = str(item["side"]).upper()
            strike = int(float(item["strike"]))
            key = str(item.get("instrument_key") or "")
        except (KeyError, TypeError, ValueError):
            continue
        for day in item.get("fetch_dates") or []:
            out[(str(day), expiry, side, strike)] = key
    return out


def broker_empty_candidates(
    requested: Dict[Tuple[str, str, str, int], str],
    stored_pairs: Set[Tuple[str, str, str, int]],
    failed_entries: Sequence[Dict[str, Any]],
    *,
    grace_from: Optional[str] = None,
) -> List[Tuple[str, str, str, int]]:
    """Pairs that were cleanly requested but still have no stored candles.

    A pair is NOT a broker-empty candidate when:
    - its task/chunk FAILED (error, rate limit, ...) — only a successful fetch
      that returned nothing proves emptiness; or
    - its date is >= `grace_from` (normally the most recent closed session).
      Upstox publishes historical F&O bars with a lag after the close, so a
      same-night fetch of yesterday's band legitimately returns empty even for
      ATM strikes that traded all day. Without the grace rule one early sync
      would permanently mis-ledger the whole previous session (observed live:
      2026-06-12's full 20/28/28-pair bands came back empty at 00:45 IST).
      Such pairs stay actionable and are simply retried on the next sync.

    Pure (unit-testable)."""
    failed_ranges: List[Tuple[str, str, str]] = []
    for f in failed_entries or []:
        key = str(f.get("instrument_key") or "")
        lo = str(f.get("from_date") or f.get("from") or "")
        hi = str(f.get("to_date") or f.get("to") or "9999-12-31")
        if key:
            failed_ranges.append((key, lo, hi))

    out: List[Tuple[str, str, str, int]] = []
    for pair, key in requested.items():
        if pair in stored_pairs:
            continue
        day = pair[0]
        if grace_from and day >= str(grace_from):
            continue
        if any(k == key and lo <= day <= hi for (k, lo, hi) in failed_ranges):
            continue
        out.append(pair)
    return sorted(out)


async def record_broker_empty_pairs(
    db: Any, instrument: str, plan: Dict[str, Any], run_id: str
) -> int:
    """After a band-driven fetch run: ledger every requested-but-still-absent
    pair whose fetch did not fail. Returns the number of pairs recorded."""
    instrument = instrument.upper()
    requested = pairs_from_band_plan_items(plan.get("items") or [])
    if not requested:
        return 0
    pairs = await _option_pairs_by_day(db, instrument, plan["from_date"], plan["to_date"])
    run = await db.warehouse_runs.find_one({"id": run_id}, {"_id": 0, "failed": 1}) or {}
    candidates = broker_empty_candidates(
        requested, pairs["stored_pairs"], run.get("failed") or [],
        grace_from=most_recent_closed_session(),
    )
    if not candidates:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    for day, expiry, side, strike in candidates:
        await db.option_known_empty.update_one(
            {"underlying": instrument, "date": day, "expiry": expiry, "side": side, "strike": strike},
            {"$setOnInsert": {
                "underlying": instrument, "date": day, "expiry": expiry,
                "side": side, "strike": strike,
                "instrument_key": requested.get((day, expiry, side, strike), ""),
                "recorded_at": now, "run_id": run_id,
            }},
            upsert=True,
        )
    return len(candidates)


async def build_band_fetch_plan(
    db: Any,
    instrument: str,
    start_iso: str,
    end_iso: str,
    *,
    legs: Optional[List[str]] = None,
    pad_steps: int = BAND_PAD_STEPS,
    retest_known_empty: bool = False,
) -> Dict[str, Any]:
    """Build an exact option-candle fetch plan from the completeness band.

    The hygiene fetch must request EXACTLY the (day, expiry, side, strike)
    pairs band-completeness reports missing. The older path re-derived a
    SEPARATE per-day ATM ± moneyness selection via the option-warehouse
    preview, which does not cover the padded spot-range band — so intraday-wick
    and band-edge strikes (e.g. NIFTY 25200 on 2025-09-15, where the day's high
    rounded to 25150 and the +1 pad demanded 25200) were judged "missing"
    forever yet never fetched, even though the broker had the candles. Driving
    the fetch from `missing_band_pairs` closes that loop. Returns a plan dict
    consumable by `run_option_warehouse_fetch_job`.
    """
    instrument = instrument.upper()
    legs_list = list(legs or DEFAULT_LEGS)
    day_rows = await _spot_day_rows(db, instrument)
    window_rows = [r for r in day_rows if start_iso <= r["date"] <= end_iso]
    contracts = await _option_contracts_summary(db, instrument, start_iso, end_iso)
    pairs = await _option_pairs_by_day(db, instrument, start_iso, end_iso)
    known_empty = (
        set() if retest_known_empty
        else await load_known_empty_pairs(db, instrument, start_iso, end_iso)
    )

    missing = missing_band_pairs(
        window_rows,
        expiries_sorted=contracts["expiries_sorted_from_start"],
        stored_pairs=pairs["stored_pairs"],
        step=strike_step_for(instrument),
        legs=legs_list,
        pad_steps=pad_steps,
        judge_until=most_recent_closed_session(),
        min_spot_minutes=MIN_SPOT_MINUTES_TO_JUDGE,
        known_empty=known_empty,
    )

    needed_expiries = sorted({expiry for (_d, expiry, _s, _k) in missing})
    contract_docs = (
        await db.option_contracts.find({
            "underlying": instrument,
            "expiry_date": {"$in": needed_expiries},
        }).to_list(length=None)
        if needed_expiries else []
    )
    contract_map: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for contract in contract_docs:
        try:
            key = (str(contract.get("expiry_date")), str(contract.get("side")).upper(), int(float(contract.get("strike"))))
        except (TypeError, ValueError):
            continue
        contract_map.setdefault(key, contract)

    grouped = fetch_items_from_missing_pairs(missing, contract_map, underlying=instrument)
    return {
        "items": grouped["items"],
        "instrument": instrument,
        "from_date": start_iso,
        "to_date": end_iso,
        "missing_pairs": len(missing),
        "fetch_contracts": len(grouped["items"]),
        "unresolved_contracts": grouped["unresolved_contracts"],
    }


async def compute_hygiene_plan(
    db: Any,
    *,
    start_date: Optional[str] = None,
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
    start_date = start_date or default_scope_start(end_date)
    insts = [str(i).upper() for i in (instruments or DEFAULT_INSTRUMENTS) if i]
    money = list(moneyness or DEFAULT_MONEYNESS)
    legs_list = list(legs or DEFAULT_LEGS)
    judge_until = most_recent_closed_session()

    inst_reports: List[Dict[str, Any]] = []
    total_actions = 0
    worst_status = "verified"

    for inst in insts:
        day_rows = await _spot_day_rows(db, inst)
        window_rows = [r for r in day_rows if start_date <= r["date"] <= end_date]
        spot = _spot_coverage_from_rows(window_rows, inst, start_date, end_date)
        contracts = await _option_contracts_summary(db, inst, start_date, end_date)
        pairs = await _option_pairs_by_day(db, inst, start_date, end_date)

        spot_status = _coverage_status(spot["coverage_pct"])
        contract_status = "verified" if contracts["expiries_in_window"] > 0 else "degraded"

        # Band completeness: every strike the day's spot range touched (+/- pad)
        # must have candles for both legs at the day's resolved expiry. This is
        # the exact-need check — the old per-expiry heuristic reported
        # verified-but-incomplete and silently starved backtests of strikes.
        known_empty = await load_known_empty_pairs(db, inst, start_date, end_date)
        band = band_completeness(
            window_rows,
            expiries_sorted=contracts["expiries_sorted_from_start"],
            stored_pairs=pairs["stored_pairs"],
            step=strike_step_for(inst),
            legs=legs_list,
            pad_steps=BAND_PAD_STEPS,
            judge_until=judge_until,
            min_spot_minutes=MIN_SPOT_MINUTES_TO_JUDGE,
            known_empty=known_empty,
        )
        opt = {
            **band,
            "total_candles": pairs["total_candles"],
            "expiries_with_data": pairs["expiries_with_data"],
        }
        if band["missing_pairs"] == 0:
            opt_status = "verified"
        elif band["coverage_pct"] >= 98.0:
            opt_status = "warning"
        else:
            opt_status = "degraded"

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
        # Option candles fetch action — whenever ANY ACTIONABLE band pair is
        # missing (broker-proven-empty pairs are excluded by the ledger). The
        # submit path re-derives exact per-contract per-date needs via
        # build_band_fetch_plan, so this action is cheap to emit and
        # idempotent to execute.
        if contract_status == "verified" and band["missing_pairs"] > 0:
            months = ", ".join(f"{m}: {n}" for m, n in list(band["missing_by_month"].items())[:6])
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
                    f"{band['missing_pairs']} strike-day(s) missing from the daily ATM band "
                    f"({band['coverage_pct']}% band coverage; by month: {months})"
                ),
                "eta_minutes": max(5, band["missing_pairs"] // 10),
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
    fallback_start_date: Optional[str] = None,
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
    fallback_start_date = fallback_start_date or default_scope_start()
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
