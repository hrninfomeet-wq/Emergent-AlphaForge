"""Option candle integrity audit helpers."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

EXPECTED_1M_CANDLES_PER_DAY = 375


def _ts_to_ist_date(ts_ms: int) -> str:
    return pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").tz_convert("Asia/Kolkata").date().isoformat()


def _weekday_counts(start_date: str, end_date: str, expected_per_day: int = EXPECTED_1M_CANDLES_PER_DAY) -> Dict[str, int]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        return {}
    counts: Dict[str, int] = {}
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            counts[cur.isoformat()] = expected_per_day
        cur += timedelta(days=1)
    return counts


def summarize_option_audit(
    *,
    underlying: str,
    contracts: Iterable[Dict[str, Any]],
    expected_date_counts: Dict[str, int],
    option_counts: Dict[Tuple[str, str], int],
    max_days_per_contract: int = 20,
) -> Dict[str, Any]:
    """Summarize option candle coverage by contract and trading date."""
    expected_dates = sorted(date_str for date_str, count in expected_date_counts.items() if int(count or 0) > 0)
    items: List[Dict[str, Any]] = []

    for contract in contracts or []:
        instrument_key = str(contract.get("instrument_key") or "")
        if not instrument_key:
            continue
        expiry_date = str(contract.get("expiry_date") or "")
        usable_dates = [date_str for date_str in expected_dates if not expiry_date or date_str <= expiry_date]
        expected_total = sum(int(expected_date_counts.get(date_str, 0) or 0) for date_str in usable_dates)

        days: List[Dict[str, Any]] = []
        complete_days = missing_days = incomplete_days = 0
        stored_total = 0
        for date_str in usable_dates:
            expected = int(expected_date_counts.get(date_str, 0) or 0)
            stored = int(option_counts.get((instrument_key, date_str), 0) or 0)
            stored_total += stored
            if stored <= 0:
                status = "missing"
                missing_days += 1
            elif stored < expected:
                status = "incomplete"
                incomplete_days += 1
            else:
                status = "ok"
                complete_days += 1
            days.append({
                "date": date_str,
                "expected_candles": expected,
                "stored_candles": stored,
                "coverage_pct": round(min(100.0, (stored / expected) * 100), 2) if expected else 0.0,
                "status": status,
            })

        if expected_total <= 0:
            status = "no_expected_sessions"
        elif missing_days == len(usable_dates):
            status = "missing"
        elif missing_days > 0:
            status = "missing"
        elif incomplete_days > 0:
            status = "incomplete"
        else:
            status = "ok"

        items.append({
            "instrument_key": instrument_key,
            "underlying": str(contract.get("underlying") or underlying).upper(),
            "expiry_date": expiry_date,
            "strike": contract.get("strike"),
            "side": str(contract.get("side") or "").upper(),
            "trading_symbol": contract.get("trading_symbol") or "",
            "status": status,
            "expected_days": len(usable_dates),
            "complete_days": complete_days,
            "missing_days": missing_days,
            "incomplete_days": incomplete_days,
            "stored_candles": stored_total,
            "expected_candles": expected_total,
            "coverage_pct": round(min(100.0, (stored_total / expected_total) * 100), 2) if expected_total else 0.0,
            "days": days[:max_days_per_contract],
        })

    items.sort(key=lambda item: (
        {"missing": 0, "incomplete": 1, "no_expected_sessions": 2, "ok": 3}.get(item["status"], 9),
        str(item.get("expiry_date") or ""),
        float(item.get("strike") or 0),
        str(item.get("side") or ""),
        str(item.get("instrument_key") or ""),
    ))

    summary = {
        "underlying": underlying.upper(),
        "contracts_checked": len(items),
        "expected_days": len(expected_dates),
        "complete_contracts": sum(1 for item in items if item["status"] == "ok"),
        "contracts_with_missing_days": sum(1 for item in items if item["missing_days"] > 0),
        "contracts_with_incomplete_days": sum(1 for item in items if item["missing_days"] == 0 and item["incomplete_days"] > 0),
        "stored_candles": sum(int(item["stored_candles"]) for item in items),
        "expected_candles": sum(int(item["expected_candles"]) for item in items),
        "calendar_assumption": "index_candle_sessions" if expected_dates else "none",
        "complete": False,
    }
    summary["complete"] = (
        summary["contracts_checked"] > 0
        and summary["complete_contracts"] == summary["contracts_checked"]
        and summary["contracts_with_missing_days"] == 0
        and summary["contracts_with_incomplete_days"] == 0
    )
    summary["coverage_pct"] = (
        round(min(100.0, (summary["stored_candles"] / summary["expected_candles"]) * 100), 2)
        if summary["expected_candles"]
        else 0.0
    )
    if expected_dates:
        summary["start_date"] = expected_dates[0]
        summary["end_date"] = expected_dates[-1]
    else:
        summary["start_date"] = None
        summary["end_date"] = None

    return {"summary": summary, "items": items}


async def _index_expected_date_counts(db: Any, underlying: str, start_ts: int, end_ts: int) -> Dict[str, int]:
    rows = await db.candles_1m.find(
        {"instrument": underlying.upper(), "ts": {"$gte": int(start_ts), "$lte": int(end_ts)}},
        {"_id": 0, "ts": 1},
    ).sort("ts", 1).to_list(length=1000000)
    counts: Dict[str, int] = {}
    for row in rows:
        date_str = _ts_to_ist_date(int(row["ts"]))
        counts[date_str] = counts.get(date_str, 0) + 1
    return counts


async def _option_counts(db: Any, instrument_keys: List[str], start_ts: int, end_ts: int) -> Dict[Tuple[str, str], int]:
    if not instrument_keys:
        return {}
    counts: Dict[Tuple[str, str], int] = {}
    pipeline = [
        {"$match": {"instrument_key": {"$in": instrument_keys}, "ts": {"$gte": int(start_ts), "$lte": int(end_ts)}}},
        {"$project": {
            "instrument_key": 1,
            "date": {
                "$dateToString": {
                    "format": "%Y-%m-%d",
                    "timezone": "Asia/Kolkata",
                    "date": {"$toDate": "$ts"},
                }
            },
        }},
        {"$group": {"_id": {"key": "$instrument_key", "date": "$date"}, "count": {"$sum": 1}}},
    ]
    async for doc in db.options_1m.aggregate(pipeline):
        key = str(doc.get("_id", {}).get("key") or "")
        date_str = str(doc.get("_id", {}).get("date") or "")
        if not key or not date_str:
            continue
        counts[(key, date_str)] = int(doc.get("count", 0) or 0)
    return counts


async def audit_option_data(
    db: Any,
    *,
    underlying: str,
    start_ts: int,
    end_ts: int,
    expiry: Optional[str] = None,
    side: Optional[str] = None,
    limit_contracts: int = 500,
) -> Dict[str, Any]:
    underlying = underlying.upper()
    contract_query: Dict[str, Any] = {"underlying": underlying}
    if expiry:
        contract_query["expiry_date"] = expiry
    if side:
        contract_query["side"] = side.upper()

    contracts = await db.option_contracts.find(contract_query, {"_id": 0}).sort([
        ("expiry_date", 1),
        ("strike", 1),
        ("side", 1),
        ("instrument_key", 1),
    ]).to_list(length=max(1, int(limit_contracts)))

    start_date = _ts_to_ist_date(start_ts)
    end_date = _ts_to_ist_date(end_ts)
    expected_counts = await _index_expected_date_counts(db, underlying, start_ts, end_ts)
    if not expected_counts:
        expected_counts = _weekday_counts(start_date, end_date)

    instrument_keys = [str(contract.get("instrument_key")) for contract in contracts if contract.get("instrument_key")]
    counts = await _option_counts(db, instrument_keys, start_ts, end_ts)
    result = summarize_option_audit(
        underlying=underlying,
        contracts=contracts,
        expected_date_counts=expected_counts,
        option_counts=counts,
    )
    result["filters"] = {
        "underlying": underlying,
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "expiry": expiry,
        "side": side.upper() if side else None,
        "limit_contracts": int(limit_contracts),
    }
    return result


async def clear_option_data(db: Any, *, underlying: Optional[str] = None) -> Dict[str, int]:
    query: Dict[str, Any] = {}
    if underlying and underlying.upper() != "ALL":
        query["underlying"] = underlying.upper()
    result = await db.options_1m.delete_many(query)
    return {"option_candles_deleted": int(result.deleted_count)}
