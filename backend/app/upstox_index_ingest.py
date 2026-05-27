"""Background Upstox index candle ingest jobs."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from pymongo import UpdateOne

from app import upstox_client
from app.db import get_db
from app.warehouse import _hash_day, _ist_day_bounds_ms


async def persist_index_candles_bulk(instrument: str, df: pd.DataFrame, db: Optional[Any] = None) -> Dict[str, Any]:
    """Persist index candles with one Mongo bulk write per broker chunk."""
    if df.empty:
        return {"upserted": 0, "modified": 0, "matched": 0, "dates": []}

    instrument = instrument.upper()
    db = db if db is not None else get_db()
    coll = db.candles_1m
    normalized = (
        df.copy()
        .drop_duplicates(subset=["instrument", "ts"])
        .sort_values("ts")
        .reset_index(drop=True)
    )
    normalized["instrument"] = instrument

    ops = []
    for row in normalized.to_dict(orient="records"):
        ops.append(
            UpdateOne(
                {"instrument": instrument, "ts": int(row["ts"])},
                {"$set": {
                    "instrument": instrument,
                    "ts": int(row["ts"]),
                    "datetime": str(row.get("datetime", "")),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0) or 0),
                }},
                upsert=True,
            )
        )

    result = await coll.bulk_write(ops, ordered=False) if ops else None
    normalized["date_str"] = pd.to_datetime(
        normalized["ts"], unit="ms", utc=True
    ).dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d")
    dates = sorted(str(item) for item in normalized["date_str"].unique())

    for date_str in dates:
        start_ms, end_ms = _ist_day_bounds_ms(date_str)
        rows = await coll.find(
            {"instrument": instrument, "ts": {"$gte": start_ms, "$lte": end_ms}},
            {"_id": 0},
        ).sort("ts", 1).to_list(length=2000)
        day_df = pd.DataFrame(rows)
        await db.integrity_hashes.update_one(
            {"instrument": instrument, "date": date_str},
            {"$set": {
                "instrument": instrument,
                "date": date_str,
                "hash": _hash_day(day_df),
                "candle_count": int(len(day_df)),
                "computed_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )

    return {
        "upserted": int(result.upserted_count if result else 0),
        "modified": int(result.modified_count if result else 0),
        "matched": int(result.matched_count if result else 0),
        "dates": dates,
    }


def _chunk_ranges(from_date: str, to_date: str, chunk_days: int) -> List[Dict[str, str]]:
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    if start > end:
        raise ValueError("from_date must be before or equal to to_date")
    step = max(1, int(chunk_days or 1))
    ranges: List[Dict[str, str]] = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=step - 1), end)
        ranges.append({"from_date": cur.isoformat(), "to_date": chunk_end.isoformat()})
        cur = chunk_end + timedelta(days=1)
    return ranges


async def run_upstox_index_ingest_job(
    run_id: str,
    instrument: str,
    from_date: str,
    to_date: str,
    chunk_days: int,
) -> None:
    """Run a long index ingest as a resumable warehouse run document."""
    db = get_db()
    instrument = instrument.upper()
    chunks = _chunk_ranges(from_date, to_date, chunk_days)
    total_chunks = len(chunks)
    totals = {
        "total_fetched": 0,
        "candles_added": 0,
        "candles_updated": 0,
        "matched_existing": 0,
        "completed_chunks": 0,
        "empty_chunks": 0,
    }
    failed_chunks: List[Dict[str, str]] = []
    observed_dates: set[str] = set()

    await db.warehouse_runs.update_one(
        {"id": run_id},
        {"$set": {
            "status": "running",
            "started_fetch_at": datetime.now(timezone.utc).isoformat(),
            "total_chunks": total_chunks,
            "completed_chunks": 0,
            "progress_pct": 0,
            "failed_chunks": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )

    for chunk in chunks:
        try:
            df = await upstox_client.fetch_historical_1m(
                instrument,
                chunk["from_date"],
                chunk["to_date"],
            )
            totals["total_fetched"] += int(len(df))
            if df.empty:
                totals["empty_chunks"] += 1
            else:
                saved = await persist_index_candles_bulk(instrument, df, db=db)
                totals["candles_added"] += saved["upserted"]
                totals["candles_updated"] += saved["modified"]
                totals["matched_existing"] += saved["matched"]
                observed_dates.update(saved["dates"])
        except Exception as exc:
            failed_chunks.append({
                "from_date": chunk["from_date"],
                "to_date": chunk["to_date"],
                "error": str(exc)[:300],
            })

        totals["completed_chunks"] += 1
        progress_pct = round((totals["completed_chunks"] / max(1, total_chunks)) * 100, 2)
        await db.warehouse_runs.update_one(
            {"id": run_id},
            {"$set": {
                **totals,
                "progress_pct": progress_pct,
                "observed_trading_days": len(observed_dates),
                "failed_chunks": failed_chunks,
                "last_chunk": chunk,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        await asyncio.sleep(0.2)

    if failed_chunks:
        status = "partial"
    elif totals["total_fetched"] <= 0:
        status = "empty"
    else:
        status = "ok"

    await db.warehouse_runs.update_one(
        {"id": run_id},
        {"$set": {
            **totals,
            "status": status,
            "progress_pct": 100,
            "observed_trading_days": len(observed_dates),
            "failed_chunks": failed_chunks,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
