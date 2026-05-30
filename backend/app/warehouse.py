"""Data Warehouse v2 — cache-first MongoDB candles with integrity hashes, coverage map, dedup, sync state."""
from __future__ import annotations
import hashlib
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import pandas as pd

from app.db import get_db
from app.yfinance_source import fetch_1m
from app.nse_calendar import trading_days_in_range

log = logging.getLogger(__name__)

EXPECTED_1M_CANDLES_PER_DAY = 375


def _hash_day(rows: pd.DataFrame) -> str:
    if rows.empty:
        return ""
    ordered = rows.sort_values("ts")
    payload = ordered[["ts", "open", "high", "low", "close", "volume"]].to_json(orient="values").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _ms_to_ist_date(ms: int) -> date:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").tz_convert("Asia/Kolkata").date()


def _ist_day_bounds_ms(date_str: str) -> tuple[int, int]:
    start = pd.Timestamp(date_str, tz="Asia/Kolkata")
    end = start + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    return int(start.tz_convert("UTC").value // 10**6), int(end.tz_convert("UTC").value // 10**6)


def summarize_audit_days(
    instrument: str,
    expected_dates: List[str],
    stored_counts: Dict[str, int],
    stored_hashes: Dict[str, str],
    computed_hashes: Dict[str, str],
    expected_per_day: int = EXPECTED_1M_CANDLES_PER_DAY,
) -> Dict[str, Any]:
    days: List[Dict[str, Any]] = []
    for date_str in expected_dates:
        stored_count = int(stored_counts.get(date_str, 0) or 0)
        stored_hash = stored_hashes.get(date_str)
        computed_hash = computed_hashes.get(date_str)
        hash_ok = bool(stored_hash and computed_hash and stored_hash == computed_hash)
        coverage_pct = round((stored_count / expected_per_day) * 100, 2) if expected_per_day else 0

        if stored_count <= 0:
            status = "missing"
        elif stored_count < expected_per_day:
            status = "incomplete"
        elif stored_hash and computed_hash and stored_hash != computed_hash:
            status = "hash_mismatch"
        elif not stored_hash or not computed_hash:
            status = "unverified"
        else:
            status = "ok"

        days.append({
            "date": date_str,
            "expected_candles": expected_per_day,
            "stored_candles": stored_count,
            "coverage_pct": min(100, coverage_pct),
            "stored_hash": stored_hash,
            "computed_hash": computed_hash,
            "hash_ok": hash_ok,
            "status": status,
        })

    summary = {
        "instrument": instrument.upper(),
        "expected_days": len(expected_dates),
        "complete_days": sum(1 for d in days if d["status"] == "ok"),
        "missing_days": sum(1 for d in days if d["status"] == "missing"),
        "incomplete_days": sum(1 for d in days if d["status"] == "incomplete"),
        "hash_mismatch_days": sum(1 for d in days if d["status"] == "hash_mismatch"),
        "unverified_days": sum(1 for d in days if d["status"] == "unverified"),
        "stored_candles": sum(int(d["stored_candles"]) for d in days),
        "expected_candles": len(expected_dates) * expected_per_day,
        "expected_per_day": expected_per_day,
        "calendar_assumption": "nse_trading_calendar",
    }
    summary["complete"] = (
        summary["expected_days"] > 0
        and summary["complete_days"] == summary["expected_days"]
        and summary["missing_days"] == 0
        and summary["incomplete_days"] == 0
        and summary["hash_mismatch_days"] == 0
        and summary["unverified_days"] == 0
    )
    if expected_dates:
        summary["start_date"] = expected_dates[0]
        summary["end_date"] = expected_dates[-1]
    else:
        summary["start_date"] = None
        summary["end_date"] = None
    return {"summary": summary, "days": days}


async def persist_candles_df(instrument: str, df: pd.DataFrame) -> Dict[str, int]:
    if df.empty:
        return {"candles_added": 0, "candles_updated": 0, "total_fetched": 0}

    instrument = instrument.upper()
    db = get_db()
    coll = db.candles_1m
    inserted = 0
    updated = 0
    normalized = df.copy()
    normalized["instrument"] = instrument

    for d in normalized.to_dict(orient="records"):
        result = await coll.update_one(
            {"instrument": instrument, "ts": int(d["ts"])},
            {"$set": {
                "instrument": instrument,
                "ts": int(d["ts"]),
                "datetime": str(d.get("datetime", "")),
                "open": float(d["open"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
                "close": float(d["close"]),
                "volume": float(d.get("volume", 0) or 0),
            }},
            upsert=True,
        )
        if result.upserted_id is not None:
            inserted += 1
        elif result.modified_count > 0:
            updated += 1

    normalized["date_str"] = pd.to_datetime(normalized["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d")
    for date_str in sorted(normalized["date_str"].unique()):
        start_ms, end_ms = _ist_day_bounds_ms(str(date_str))
        day_rows = await coll.find(
            {"instrument": instrument, "ts": {"$gte": start_ms, "$lte": end_ms}},
            {"_id": 0},
        ).sort("ts", 1).to_list(length=2000)
        day_df = pd.DataFrame(day_rows)
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

    return {"candles_added": inserted, "candles_updated": updated, "total_fetched": int(len(normalized))}


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

    saved = await persist_candles_df(instrument.upper(), df)

    finished_at = datetime.now(timezone.utc).isoformat()
    await db.warehouse_runs.update_one(
        {"id": run_id},
        {"$set": {
            "status": "ok", "finished_at": finished_at,
            "candles_added": saved["candles_added"],
            "candles_updated": saved["candles_updated"],
            "total_fetched": saved["total_fetched"],
        }},
    )
    return {"run_id": run_id, "status": "ok",
            "candles_added": saved["candles_added"],
            "candles_updated": saved["candles_updated"],
            "total_fetched": saved["total_fetched"]}


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


async def audit_integrity(
    instrument: str,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
) -> Dict[str, Any]:
    """Recompute per-day hashes and coverage for a requested instrument/window."""
    instrument = instrument.upper()
    db = get_db()
    q: Dict[str, Any] = {"instrument": instrument}
    if start_ts is not None or end_ts is not None:
        rng: Dict[str, Any] = {}
        if start_ts is not None:
            rng["$gte"] = int(start_ts)
        if end_ts is not None:
            rng["$lte"] = int(end_ts)
        q["ts"] = rng

    rows = await db.candles_1m.find(q, {"_id": 0}).sort("ts", 1).to_list(length=1000000)
    stored_counts: Dict[str, int] = {}
    computed_hashes: Dict[str, str] = {}
    observed_dates: set[str] = set()
    if rows:
        df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
        df["date_str"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d")
        stored_counts = {str(k): int(v) for k, v in df.groupby("date_str").size().to_dict().items()}
        computed_hashes = {str(date_str): _hash_day(grp) for date_str, grp in df.groupby("date_str")}
        observed_dates.update(stored_counts.keys())

    hash_query: Dict[str, Any] = {"instrument": instrument}
    start_date = _ms_to_ist_date(start_ts).isoformat() if start_ts is not None else None
    end_date = _ms_to_ist_date(end_ts).isoformat() if end_ts is not None else None
    if start_date or end_date:
        date_rng: Dict[str, str] = {}
        if start_date:
            date_rng["$gte"] = start_date
        if end_date:
            date_rng["$lte"] = end_date
        hash_query["date"] = date_rng

    hash_docs = await db.integrity_hashes.find(hash_query, {"_id": 0}).sort("date", 1).to_list(length=5000)
    stored_hashes = {str(d["date"]): d.get("hash") for d in hash_docs}
    observed_dates.update(stored_hashes.keys())

    if start_date and end_date:
        # Holiday-aware expected trading days: skip NSE/BSE holidays and weekends,
        # include gazetted special Saturday sessions. The previous weekday-only
        # generator counted every holiday as a "missing" day and under-reported
        # true coverage.
        expected_dates = trading_days_in_range(start_date, end_date)
    else:
        expected_dates = sorted(observed_dates)

    return summarize_audit_days(
        instrument=instrument,
        expected_dates=expected_dates,
        stored_counts=stored_counts,
        stored_hashes=stored_hashes,
        computed_hashes=computed_hashes,
    )


async def clear_warehouse_data(instrument: Optional[str] = None) -> Dict[str, int]:
    db = get_db()
    q: Dict[str, Any] = {}
    if instrument:
        q["instrument"] = instrument.upper()
    candles = await db.candles_1m.delete_many(q)
    hashes = await db.integrity_hashes.delete_many(q)
    runs = await db.warehouse_runs.delete_many(q)
    return {
        "candles_deleted": int(candles.deleted_count),
        "hashes_deleted": int(hashes.deleted_count),
        "runs_deleted": int(runs.deleted_count),
    }


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
