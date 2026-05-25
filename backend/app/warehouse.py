"""Data Warehouse v2 — cache-first MongoDB candles with integrity hashes, coverage map, dedup, sync state."""
from __future__ import annotations
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import pandas as pd

from app.db import get_db
from app.yfinance_source import fetch_1m

log = logging.getLogger(__name__)


def _hash_day(rows: pd.DataFrame) -> str:
    if rows.empty:
        return ""
    payload = rows[["ts", "open", "high", "low", "close", "volume"]].to_json(orient="values").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


async def ingest_yfinance(instrument: str, days: int = 7) -> Dict[str, Any]:
    db = get_db()
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    await db.warehouse_runs.insert_one({
        "id": run_id, "instrument": instrument.upper(), "source": "yfinance",
        "started_at": started_at, "status": "running", "days": days,
    })
    try:
        df = fetch_1m(instrument, days=days)
    except Exception as e:
        log.exception("fetch failed")
        await db.warehouse_runs.update_one(
            {"id": run_id},
            {"$set": {"status": "failed", "finished_at": datetime.now(timezone.utc).isoformat(), "error": str(e)}},
        )
        return {"run_id": run_id, "status": "failed", "error": str(e)}

    if df.empty:
        await db.warehouse_runs.update_one(
            {"id": run_id},
            {"$set": {"status": "empty", "finished_at": datetime.now(timezone.utc).isoformat(), "candles_added": 0}},
        )
        return {"run_id": run_id, "status": "empty", "candles_added": 0}

    coll = db.candles_1m
    inserted = 0
    updated = 0
    docs = df.to_dict(orient="records")
    bulk_ops = []
    for d in docs:
        bulk_ops.append({
            "filter": {"instrument": d["instrument"], "ts": int(d["ts"])},
            "update": {"$set": {
                "instrument": d["instrument"], "ts": int(d["ts"]),
                "datetime": str(d["datetime"]),
                "open": float(d["open"]), "high": float(d["high"]),
                "low": float(d["low"]), "close": float(d["close"]),
                "volume": float(d.get("volume", 0) or 0),
            }},
        })
    for op in bulk_ops:
        result = await coll.update_one(op["filter"], op["update"], upsert=True)
        if result.upserted_id is not None:
            inserted += 1
        elif result.modified_count > 0:
            updated += 1

    # Per-day integrity hashes
    df["date_str"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d")
    for date_str, grp in df.groupby("date_str"):
        h = _hash_day(grp)
        await db.integrity_hashes.update_one(
            {"instrument": instrument.upper(), "date": date_str},
            {"$set": {
                "instrument": instrument.upper(),
                "date": date_str,
                "hash": h,
                "candle_count": int(len(grp)),
                "computed_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )

    finished_at = datetime.now(timezone.utc).isoformat()
    await db.warehouse_runs.update_one(
        {"id": run_id},
        {"$set": {
            "status": "ok", "finished_at": finished_at,
            "candles_added": inserted, "candles_updated": updated, "total_fetched": len(df),
        }},
    )
    return {"run_id": run_id, "status": "ok",
            "candles_added": inserted, "candles_updated": updated, "total_fetched": len(df)}


async def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    db = get_db()
    cursor = db.warehouse_runs.find({}, {"_id": 0}).sort("started_at", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def get_coverage() -> Dict[str, Any]:
    """Return per-instrument candle counts, date ranges, day-by-day breakdown."""
    db = get_db()
    out = {}
    pipeline = [
        {"$group": {
            "_id": "$instrument",
            "count": {"$sum": 1},
            "min_ts": {"$min": "$ts"},
            "max_ts": {"$max": "$ts"},
        }},
    ]
    async for doc in db.candles_1m.aggregate(pipeline):
        instrument = doc["_id"]
        if not instrument:
            continue
        # Per-day counts from integrity_hashes
        days_cur = db.integrity_hashes.find({"instrument": instrument}, {"_id": 0, "date": 1, "candle_count": 1, "hash": 1}).sort("date", 1)
        days = await days_cur.to_list(length=2000)
        out[instrument] = {
            "candle_count": doc["count"],
            "min_ts": doc["min_ts"],
            "max_ts": doc["max_ts"],
            "min_datetime": _ms_to_ist(doc["min_ts"]),
            "max_datetime": _ms_to_ist(doc["max_ts"]),
            "days": days,
        }
    return out


def _ms_to_ist(ms: int) -> str:
    if not ms:
        return ""
    return pd.Timestamp(ms, unit="ms", tz="UTC").tz_convert("Asia/Kolkata").strftime("%Y-%m-%d %H:%M")


async def load_candles_df(instrument: str, start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> pd.DataFrame:
    db = get_db()
    q: Dict[str, Any] = {"instrument": instrument.upper()}
    if start_ts is not None or end_ts is not None:
        rng: Dict[str, Any] = {}
        if start_ts is not None:
            rng["$gte"] = int(start_ts)
        if end_ts is not None:
            rng["$lte"] = int(end_ts)
        q["ts"] = rng
    cursor = db.candles_1m.find(q, {"_id": 0}).sort("ts", 1)
    rows = await cursor.to_list(length=200000)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values("ts").reset_index(drop=True)


async def candle_sample(instrument: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Return latest N candles for the price chart preview."""
    db = get_db()
    cursor = db.candles_1m.find({"instrument": instrument.upper()}, {"_id": 0}).sort("ts", -1).limit(limit)
    rows = await cursor.to_list(length=limit)
    rows.reverse()
    return rows
