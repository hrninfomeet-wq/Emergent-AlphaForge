"""Option warehouse coverage summaries."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


EXPECTED_1M_CANDLES_PER_CONTRACT_DAY = 375


def summarize_option_coverage(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize per-day option candle storage by underlying."""
    by_underlying: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        underlying = str(row.get("underlying") or "").upper()
        date = str(row.get("date") or "")
        if not underlying or not date:
            continue
        candles = int(row.get("candles", 0) or 0)
        contracts = int(row.get("contracts", 0) or 0)
        complete_contracts = int(row.get("complete_contracts", 0) or 0)
        incomplete_contracts = max(0, contracts - complete_contracts)
        expected = contracts * EXPECTED_1M_CANDLES_PER_CONTRACT_DAY
        coverage_pct = round(min(100.0, (candles / expected) * 100), 2) if expected else 0.0
        bucket = by_underlying.setdefault(underlying, {
            "underlying": underlying,
            "total_candles": 0,
            "contract_keys": set(),
            "days": [],
        })
        bucket["total_candles"] += candles
        for key in row.get("instrument_keys", []) or []:
            if key:
                bucket["contract_keys"].add(str(key))
        bucket["days"].append({
            "date": date,
            "candles": candles,
            "contracts": contracts,
            "complete_contracts": complete_contracts,
            "incomplete_contracts": incomplete_contracts,
            "expected_candles_for_stored_contracts": expected,
            "coverage_pct": coverage_pct,
        })

    result: Dict[str, Any] = {}
    for underlying, bucket in by_underlying.items():
        days = sorted(bucket["days"], key=lambda item: item["date"])
        result[underlying] = {
            "underlying": underlying,
            "total_candles": int(bucket["total_candles"]),
            "contract_count": len(bucket["contract_keys"]),
            "first_date": days[0]["date"] if days else None,
            "last_date": days[-1]["date"] if days else None,
            "days": days,
        }
    return result


async def get_option_coverage(db: Any, underlying: str | None = None) -> Dict[str, Any]:
    match: Dict[str, Any] = {}
    if underlying:
        match["underlying"] = str(underlying).upper()
    pipeline = [
        {"$match": match},
        {"$project": {
            "underlying": 1,
            "instrument_key": 1,
            "date": {
                "$dateToString": {
                    "format": "%Y-%m-%d",
                    "timezone": "Asia/Kolkata",
                    "date": {"$toDate": "$ts"},
                }
            },
        }},
        {"$group": {
            "_id": {
                "underlying": "$underlying",
                "date": "$date",
                "instrument_key": "$instrument_key",
            },
            "candles": {"$sum": 1},
        }},
        {"$group": {
            "_id": {
                "underlying": "$_id.underlying",
                "date": "$_id.date",
            },
            "candles": {"$sum": "$candles"},
            "contracts": {"$sum": 1},
            "complete_contracts": {
                "$sum": {"$cond": [{"$gte": ["$candles", EXPECTED_1M_CANDLES_PER_CONTRACT_DAY]}, 1, 0]}
            },
            "instrument_keys": {"$addToSet": "$_id.instrument_key"},
        }},
        {"$sort": {"_id.underlying": 1, "_id.date": 1}},
    ]
    rows: List[Dict[str, Any]] = []
    async for doc in db.options_1m.aggregate(pipeline):
        rows.append({
            "underlying": doc.get("_id", {}).get("underlying"),
            "date": doc.get("_id", {}).get("date"),
            "candles": doc.get("candles", 0),
            "contracts": doc.get("contracts", 0),
            "complete_contracts": doc.get("complete_contracts", 0),
            "instrument_keys": doc.get("instrument_keys", []),
        })
    return summarize_option_coverage(rows)
