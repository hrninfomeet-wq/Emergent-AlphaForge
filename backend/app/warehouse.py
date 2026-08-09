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
from app.nse_calendar import trading_days_in_range, expected_candle_count, is_trading_day
from app.session_spec import SPOT, expected_candle_count as expected_candles_for, session_rows_mask

log = logging.getLogger(__name__)

# Pre-2026-08-03 full cash session. Kept as a fallback only — the audit resolves
# each date through the calendar instead, so a Muhurat evening session expects its
# real 60 bars and a non-trading day expects none.
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
    expected_per_day: Optional[int] = None,
    segment: str = SPOT,
) -> Dict[str, Any]:
    """Classify each date's stored candles against what that date should hold.

    `expected_per_day` pins every date to one count. Leaving it None — the default —
    resolves each date through the calendar, the same way `get_coverage` already
    does. That is what makes the audit correct for *any* window the caller asks
    for: a Muhurat evening session expects its real 60 bars rather than reading
    "incomplete" at 60/375, a market holiday expects none, and (with `segment`)
    a post-2026-08-03 option day expects 385.

    A date can legitimately expect zero — a holiday, or a stray weekend row that
    reached storage from a live/retry path. Those are reported honestly rather
    than folded into "missing": `not_a_session` when nothing is stored (correct,
    not a defect) and `unexpected_session` when data exists on a day the exchange
    was closed (a real anomaly worth surfacing).
    """
    days: List[Dict[str, Any]] = []
    for date_str in expected_dates:
        expected = (
            expected_per_day if expected_per_day is not None
            else expected_candles_for(date_str, segment)
        )
        stored_count = int(stored_counts.get(date_str, 0) or 0)
        stored_hash = stored_hashes.get(date_str)
        computed_hash = computed_hashes.get(date_str)
        hash_ok = bool(stored_hash and computed_hash and stored_hash == computed_hash)

        if expected > 0:
            coverage_pct = min(100.0, round((stored_count / expected) * 100, 2))
        else:
            # Nothing was due. Full coverage if nothing is stored; 0 if data
            # exists that should not, so it cannot inflate an average.
            coverage_pct = 100.0 if stored_count <= 0 else 0.0

        if expected <= 0:
            status = "unexpected_session" if stored_count > 0 else "not_a_session"
        elif stored_count <= 0:
            status = "missing"
        elif stored_count < expected:
            status = "incomplete"
        elif stored_count > expected:
            # More bars than the session can hold — stale-feed artifacts stamped
            # outside market hours. Flat-375 arithmetic filed these as "ok"
            # because the count merely cleared the bar; they are not ok.
            status = "surplus"
        elif stored_hash and computed_hash and stored_hash != computed_hash:
            status = "hash_mismatch"
        elif not stored_hash or not computed_hash:
            status = "unverified"
        else:
            status = "ok"

        days.append({
            "date": date_str,
            "expected_candles": expected,
            "stored_candles": stored_count,
            "coverage_pct": coverage_pct,
            "stored_hash": stored_hash,
            "computed_hash": computed_hash,
            "hash_ok": hash_ok,
            "status": status,
        })

    # Only days that expected candles are sessions; a holiday in the window is not
    # a shortfall, so it must not count against completeness either way.
    session_days = sum(1 for d in days if d["expected_candles"] > 0)
    distinct_expected = {d["expected_candles"] for d in days if d["expected_candles"] > 0}

    summary = {
        "instrument": instrument.upper(),
        "expected_days": len(expected_dates),
        "session_days": session_days,
        "complete_days": sum(1 for d in days if d["status"] == "ok"),
        "missing_days": sum(1 for d in days if d["status"] == "missing"),
        "incomplete_days": sum(1 for d in days if d["status"] == "incomplete"),
        "surplus_days": sum(1 for d in days if d["status"] == "surplus"),
        "hash_mismatch_days": sum(1 for d in days if d["status"] == "hash_mismatch"),
        "unverified_days": sum(1 for d in days if d["status"] == "unverified"),
        "unexpected_session_days": sum(1 for d in days if d["status"] == "unexpected_session"),
        "not_a_session_days": sum(1 for d in days if d["status"] == "not_a_session"),
        "stored_candles": sum(int(d["stored_candles"]) for d in days),
        # True sum of per-day expectations, not len(dates) x a flat number.
        "expected_candles": sum(int(d["expected_candles"]) for d in days),
        # Only meaningful when every session in the window expects the same count;
        # None means the window mixes session lengths — read days[].expected_candles.
        "expected_per_day": next(iter(distinct_expected)) if len(distinct_expected) == 1 else None,
        "calendar_assumption": "nse_trading_calendar",
    }
    summary["complete"] = (
        session_days > 0
        and summary["complete_days"] == session_days
        and summary["missing_days"] == 0
        and summary["incomplete_days"] == 0
        and summary["surplus_days"] == 0
        and summary["hash_mismatch_days"] == 0
        and summary["unverified_days"] == 0
        and summary["unexpected_session_days"] == 0
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
        # Annotate each day with calendar context so the heatmap can skip
        # non-trading days (weekends/holidays) and color short sessions (Muhurat)
        # against the correct expected count instead of a flat 375.
        for day in days:
            iso = str(day.get("date") or "")
            day["is_trading_day"] = is_trading_day(iso)
            day["expected_candles"] = expected_candle_count(iso)
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


async def load_candles_df(instrument: str, start_ts: Optional[int] = None, end_ts: Optional[int] = None,
                          *, segment: str = SPOT, include_off_session: bool = False) -> pd.DataFrame:
    """Load stored 1-minute candles for an instrument, session-filtered.

    Storage is written straight from the vendor with no session filter, so
    stale-feed artifacts sit in it — bars stamped 23:47 or 00:09. The chart has
    always dropped those (`warehouse_ohlc._regular_session_rows`); this seam did
    not, so the chart and every backtest disagreed about what data exists and
    `precompute_all_indicators` treated 23:47 as a market minute.

    Filtering here rather than at ingest keeps the raw vendor record intact: a
    wrong session rule is then a re-read away from fixed, not an irreversible
    delete. Pass `include_off_session=True` for repair/audit tooling that needs
    to *see* the artifacts.
    """
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
    if not include_off_session:
        df = df.loc[session_rows_mask(df, segment)]
    return df.sort_values("ts").reset_index(drop=True)


async def attach_required_data(df: pd.DataFrame, required: Any,
                               *, db: Any = None) -> tuple:
    """Join a strategy's declared `required_data` columns onto `df`, as-of each
    bar's own ts. Returns (df, coverage).

    THE seam that makes warehouse-backed series (India VIX today) readable by a
    strategy AT DECISION TIME rather than only as a post-hoc trade tag. Call it
    on the RAW frame, right after `load_candles_df` and BEFORE indicator
    enrichment: `precompute_all_indicators`, `enrich_with_cache` and
    `materialize_features` all copy-and-add, so an extra raw column flows
    through every path untouched and — importantly — is neutral to the
    optimizer's indicator-param cache key.

    No declaration => returns the frame unchanged, so every existing strategy
    stays byte-identical. The heavy lifting (and the causality guarantee) lives
    in the pure `app.data_columns`; this function only does the I/O.
    """
    from app.data_columns import attach_data_columns, resolve_data_columns

    specs = resolve_data_columns(list(required or ()))
    if not specs or df is None or df.empty or "ts" not in df.columns:
        return df, {}

    db = db if db is not None else get_db()
    lo = int(pd.to_numeric(df["ts"], errors="coerce").min())
    hi = int(pd.to_numeric(df["ts"], errors="coerce").max())

    sources: Dict[str, List[Dict[str, Any]]] = {}
    for spec in specs:
        rows = await db.candles_1m.find(
            {"instrument": spec.instrument,
             "ts": {"$gte": lo - int(spec.max_staleness_ms)}},
            {"_id": 0, "ts": 1, spec.source_field: 1},
        ).sort("ts", 1).to_list(length=500000)
        # Upper bound filtered in Python, not via $lte: the repo's in-memory test
        # fakes implement only $gte/$gt/$exists (same reason app.deployment_
        # evaluator._resolve_vix_asof does this). Real Mongo would accept $lte.
        # NOTE this trim is a MEMORY bound, not the causality guarantee. Every bar
        # is <= hi by construction, and asof_series only ever looks backward, so a
        # post-window print could never be selected even if it were left in. A
        # mutation run confirmed removing this line changes no observable result —
        # do not mistake it for the thing that prevents lookahead.
        sources[spec.name] = [r for r in rows if int(r.get("ts") or 0) <= hi]

    out, coverage = attach_data_columns(df, specs, sources)
    # Degrade LOUDLY. A partially-covered column is the exact failure mode that
    # made the old vix_boost_threshold knob worthless: a strategy scoring zero
    # on the missing bars looks identical to one scoring zero on real data.
    for col, info in coverage.items():
        if info.get("coverage_pct", 0.0) < 100.0:
            log.warning(
                "data column %r covers only %.2f%% of this window (%d/%d bars, "
                "%d session(s) missing) — any rule reading it is INERT on the rest",
                col, info.get("coverage_pct", 0.0), info.get("present", 0),
                info.get("bars", 0), info.get("sessions_missing_count", 0),
            )
    return out, coverage


async def candle_sample(instrument: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Return latest N candles for the price chart preview."""
    db = get_db()
    cursor = db.candles_1m.find({"instrument": instrument.upper()}, {"_id": 0}).sort("ts", -1).limit(limit)
    rows = await cursor.to_list(length=limit)
    rows.reverse()
    return rows
