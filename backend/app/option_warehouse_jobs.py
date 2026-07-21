"""Background jobs for option candle warehouse fetches."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Sequence

import pandas as pd


def compact_date_ranges(dates: Sequence[str]) -> List[Dict[str, str]]:
    """Group sorted YYYY-MM-DD dates into contiguous calendar ranges."""
    cleaned = sorted({str(item) for item in dates or [] if item})
    if not cleaned:
        return []

    ranges: List[Dict[str, str]] = []
    start = prev = date.fromisoformat(cleaned[0])
    for item in cleaned[1:]:
        current = date.fromisoformat(item)
        if current == prev + timedelta(days=1):
            prev = current
            continue
        ranges.append({"from_date": start.isoformat(), "to_date": prev.isoformat()})
        start = prev = current
    ranges.append({"from_date": start.isoformat(), "to_date": prev.isoformat()})
    return ranges


def _contract_from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "underlying": item.get("underlying"),
        "expiry_date": item.get("expiry_date"),
        "strike": item.get("strike"),
        "side": item.get("side"),
        "trading_symbol": item.get("trading_symbol", ""),
        "lot_size": item.get("lot_size"),
    }


def option_fetch_tasks_from_plan(plan: Dict[str, Any], fetch_missing_only: bool = True) -> List[Dict[str, Any]]:
    """Build exact contract/date fetch tasks from a preview plan."""
    tasks: List[Dict[str, Any]] = []
    for item in plan.get("items", []) or []:
        if fetch_missing_only and not item.get("needs_fetch"):
            continue
        dates = item.get("fetch_dates") if fetch_missing_only else item.get("selected_dates")
        for range_item in compact_date_ranges(dates or []):
            tasks.append({
                "instrument_key": str(item.get("instrument_key")),
                "from_date": range_item["from_date"],
                "to_date": range_item["to_date"],
                "contract": _contract_from_item(item),
            })
    return [task for task in tasks if task.get("instrument_key")]


async def persist_option_candles_bulk(
    db: Any, df: pd.DataFrame, *, retrieval_run_id: str | None = None
) -> Dict[str, int]:
    """Bulk upsert candles by immutable contract identity and timestamp."""
    from pymongo import UpdateOne

    if df is None or df.empty:
        return {"candles_added": 0, "candles_updated": 0, "matched_existing": 0}

    from app.instruments import canonical_instrument_key, contract_identity_key

    ops: List[UpdateOne] = []
    refreshed_at = datetime.now(timezone.utc)
    for row in df.to_dict(orient="records"):
        expiry = row.get("expiry_date", "")
        doc = {
            "instrument_key": canonical_instrument_key(row["instrument_key"]),
            "contract_key": row.get("contract_key") or contract_identity_key(
                row["instrument_key"], expiry),
            "underlying": row.get("underlying", ""),
            "expiry_date": row.get("expiry_date", ""),
            "strike": float(row.get("strike") or 0),
            "side": str(row.get("side", "")).upper(),
            "trading_symbol": row.get("trading_symbol", ""),
            "ts": int(row["ts"]),
            "bar_end_ts": int(row.get("bar_end_ts") or int(row["ts"]) + 60_000),
            "datetime": str(row.get("datetime", "")),
            "open": float(row.get("open") or 0),
            "high": float(row.get("high") or 0),
            "low": float(row.get("low") or 0),
            "close": float(row.get("close") or 0),
            "volume": float(row.get("volume") or 0),
            "oi": float(row.get("oi") or 0),
            "source": row.get("source", "upstox"),
            "source_endpoint": row.get("source_endpoint", "historical-candle-v3"),
            "last_retrieved_at": refreshed_at,
            "retrieval_run_id": retrieval_run_id or row.get("retrieval_run_id"),
        }
        ops.append(
            UpdateOne(
                {"instrument_key": doc["instrument_key"],
                 "expiry_date": doc["expiry_date"], "ts": doc["ts"]},
                {"$set": doc, "$setOnInsert": {"first_ingested_at": refreshed_at}},
                upsert=True,
            )
        )
    result = await db.options_1m.bulk_write(ops, ordered=False) if ops else None
    return {
        "candles_added": int(result.upserted_count if result else 0),
        "candles_updated": int(result.modified_count if result else 0),
        "matched_existing": int(result.matched_count if result else 0),
    }


async def run_option_warehouse_fetch_job(
    run_id: str,
    plan: Dict[str, Any],
    *,
    fetch_missing_only: bool,
    chunk_days: int,
) -> None:
    """Fetch option candles in background using exact selected-date windows."""
    from app import upstox_client
    from app.db import get_db

    db = get_db()
    tasks = option_fetch_tasks_from_plan(plan, fetch_missing_only=fetch_missing_only)
    totals = {
        "total_fetched": 0,
        "candles_added": 0,
        "candles_updated": 0,
        "matched_existing": 0,
        "completed_tasks": 0,
        "empty_tasks": 0,
    }
    failed: List[Dict[str, Any]] = []
    fetched: List[Dict[str, Any]] = []

    await db.warehouse_runs.update_one(
        {"id": run_id},
        {"$set": {
            "status": "running",
            "total_tasks": len(tasks),
            "completed_tasks": 0,
            "progress_pct": 0,
            "started_fetch_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )

    for task in tasks:
        try:
            result = await upstox_client.fetch_historical_1m_for_key_chunked(
                task["instrument_key"],
                task["from_date"],
                task["to_date"],
                max_days_per_call=chunk_days,
                contract=task["contract"],
            )
            df = result["df"]
            persist_result = await persist_option_candles_bulk(
                db, df, retrieval_run_id=run_id)
            fetched_count = int(len(df))
            totals["total_fetched"] += fetched_count
            if fetched_count <= 0:
                totals["empty_tasks"] += 1
            totals["candles_added"] += int(persist_result["candles_added"])
            totals["candles_updated"] += int(persist_result["candles_updated"])
            totals["matched_existing"] += int(persist_result["matched_existing"])
            fetched.append({
                "instrument_key": task["instrument_key"],
                "from_date": task["from_date"],
                "to_date": task["to_date"],
                "fetched": fetched_count,
                **persist_result,
            })
            for chunk in result.get("failed_chunks", []):
                failed.append({"instrument_key": task["instrument_key"], **chunk})
        except Exception as exc:
            failed.append({
                "instrument_key": task["instrument_key"],
                "from_date": task["from_date"],
                "to_date": task["to_date"],
                "error": str(exc)[:300],
            })

        totals["completed_tasks"] += 1
        progress_pct = round((totals["completed_tasks"] / max(1, len(tasks))) * 100, 2)
        await db.warehouse_runs.update_one(
            {"id": run_id},
            {"$set": {
                **totals,
                "progress_pct": progress_pct,
                "failed": failed[:200],
                "fetched": fetched[-200:],
                "last_task": task,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        await asyncio.sleep(0.15)

    if failed and totals["total_fetched"] > 0:
        status = "partial"
    elif failed:
        status = "failed"
    elif not tasks or totals["total_fetched"] <= 0:
        status = "empty"
    else:
        status = "ok"

    await db.warehouse_runs.update_one(
        {"id": run_id},
        {"$set": {
            **totals,
            "status": status,
            "progress_pct": 100,
            "failed": failed[:200],
            "fetched": fetched[-200:],
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )

    # Coverage changed: refresh the precomputed cache so the Data Warehouse
    # heatmap reflects the newly stored candles without a slow live aggregation.
    if totals.get("candles_added") or totals.get("candles_updated"):
        try:
            from app.option_coverage_cache import refresh_option_coverage_cache

            await refresh_option_coverage_cache(db, underlying=None)
        except Exception:
            logging.getLogger(__name__).warning("option coverage cache refresh after fetch failed", exc_info=True)
