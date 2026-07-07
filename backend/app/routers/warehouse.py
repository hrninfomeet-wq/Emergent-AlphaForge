"""Warehouse routes: spot/option ingest, hygiene, coverage, audits, contracts.

Moved verbatim from backend/server.py (quality-hardening Slice C).
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.db import get_db, serialize_doc
from app.chunking import chunk_guidance_for_index
from app.vix import VIX_INSTRUMENT, vix_instrument_key
from app.option_data_audit import audit_option_data, clear_option_data
from app.option_plan_response import compact_option_plan_for_response
from app.option_coverage_cache import get_option_coverage_cached, refresh_option_coverage_cache
from app.nse_calendar import available_calendar_years, calendar_for_year
from app.warehouse_lookup import lookup_market_snapshot
from app.warehouse_ohlc import TIMEFRAME_RULES, build_ohlc_response
from app.option_warehouse_jobs import option_fetch_tasks_from_plan, run_option_warehouse_fetch_job
from app.expired_contract_backfill import backfill_expired_option_contracts
from app.upstox_index_ingest import persist_index_candles_bulk, run_upstox_index_ingest_job
from app.volatility import VolatilityConfig, annotate_volatility, summarize_spikes
from app.data_hygiene import (
    DEFAULT_LEGS as HYGIENE_DEFAULT_LEGS,
    DEFAULT_MONEYNESS as HYGIENE_DEFAULT_MONEYNESS,
    DEFAULT_SAMPLE_INTERVAL_MIN as HYGIENE_DEFAULT_SAMPLE,
    build_band_fetch_plan,
    compute_catch_up_plan,
    compute_hygiene_plan,
    compute_range_ingest_plan,
    default_scope_start,
    execute_hygiene_plan,
    hygiene_status,
)
from app.warehouse_autoupdate import STATE as AUTOUPDATE_STATE
from app.warehouse import (
    audit_integrity,
    candle_sample,
    clear_warehouse_data,
    get_coverage,
    ingest_yfinance,
    list_runs,
    load_candles_df,
    persist_candles_df,
)
from app import upstox_client
from app.option_candles import persist_option_candles_df
from app.option_contract_store import upsert_option_contracts

from app.runtime import (
    VIX_BASELINE_START,
    _build_option_warehouse_preview,
    _hygiene_submit_contracts,
    _hygiene_submit_option_candles,
    _hygiene_submit_spot,
    _ist_market_bounds_ms,
    _overlay_option_contract_metadata,
    _start_catch_up_chain,
    _start_historical_range_chain,
    _topup_vix,
    _trigger_autoupdate,
    log,
    submit_band_fetch_run,
)

from app.schemas import (
    AutoUpdateToggleReq,
    DataHygieneCatchUpReq,
    DataHygieneExecuteReq,
    DataHygieneScopeReq,
    ExpiredOptionContractBackfillReq,
    IngestReq,
    OptionWarehousePlanReq,
    UpstoxIngestReq,
    UpstoxOptionCandleIngestReq,
    VixIngestReq,
    VolatilityAuditReq,
)

api = APIRouter()


@api.post("/warehouse/ingest")
async def warehouse_ingest(req: IngestReq):
    if req.instrument.upper() not in ("NIFTY", "BANKNIFTY", "SENSEX"):
        raise HTTPException(400, f"Unsupported instrument: {req.instrument}")
    if not (1 <= req.days <= 30):
        raise HTTPException(400, "days must be between 1 and 30 for yfinance 1m")
    result = await ingest_yfinance(req.instrument.upper(), days=req.days)
    return result


@api.get("/warehouse/coverage")
async def warehouse_coverage():
    cov = await get_coverage()
    return {"instruments": cov}


@api.get("/warehouse/runs")
async def warehouse_runs(limit: int = Query(50, le=200)):
    runs = await list_runs(limit=limit)
    return {"items": runs}


@api.post("/warehouse/intraday-backfill/{instrument}")
async def warehouse_intraday_backfill(instrument: str):
    """Backfill the CURRENT trading day's 1m candles from Upstox intraday.

    The historical endpoint returns empty for today, so today's bars normally
    come from the live tick->1m roller. If the roller was not running at market
    open, the morning is missing. This route pulls today's candles (from 09:15
    IST) via the Upstox intraday endpoint and upserts them into candles_1m,
    closing that gap. `instrument=ALL` does all three indices.
    """
    inst = instrument.upper()
    targets = list(upstox_client.INSTRUMENT_KEYS.keys()) if inst == "ALL" else [inst]
    for t in targets:
        if t not in upstox_client.INSTRUMENT_KEYS:
            raise HTTPException(400, f"Unsupported instrument: {t}")
    status = await upstox_client.get_connection_status()
    if not status.get("connected") or status.get("expired"):
        raise HTTPException(400, "Upstox not connected (or token expired). Connect first.")

    results = []
    for t in targets:
        try:
            df = await upstox_client.fetch_intraday_1m(t)
            if df.empty:
                results.append({"instrument": t, "status": "empty", "candles_added": 0})
                continue
            saved = await persist_candles_df(t, df)
            results.append({
                "instrument": t,
                "status": "ok",
                "fetched": int(saved.get("total_fetched") or 0),
                "candles_added": int(saved.get("candles_added") or 0),
                "candles_updated": int(saved.get("candles_updated") or 0),
            })
        except Exception as exc:
            log.exception("intraday backfill failed for %s", t)
            results.append({"instrument": t, "status": "error", "error": str(exc)[:300]})
    return {"results": results}


@api.get("/warehouse/candles/{instrument}")
async def warehouse_candles(instrument: str, limit: int = Query(500, le=5000)):
    rows = await candle_sample(instrument.upper(), limit=limit)
    return {"items": rows, "count": len(rows)}


@api.get("/warehouse/lookup")
async def warehouse_point_lookup(
    instrument: str = Query(...),
    date: str = Query(...),
    time: str = Query("09:15"),
):
    """Point-in-time warehouse lookup: spot + ATM CE/PE at a date/time (IST).

    Reads only local warehouse data (candles_1m, options_1m, option_contracts)
    so the result can be cross-checked against a broker terminal. ATM is the
    nearest strike to the stored spot close; expiry is the nearest stored expiry
    on/after the date.
    """
    inst = instrument.upper()
    if inst not in upstox_client.INSTRUMENT_KEYS:
        raise HTTPException(400, f"Unsupported instrument: {instrument}")
    try:
        snapshot = await lookup_market_snapshot(get_db(), underlying=inst, date_str=date, time_str=time)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"Invalid date/time: {str(exc)[:200]}")
    return serialize_doc(snapshot)


@api.get("/warehouse/ohlc/{instrument}")
async def warehouse_ohlc(
    instrument: str,
    timeframe: str = Query("1d"),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
    include_gaps: bool = Query(True),
):
    """Resampled OHLC bars (1m/5m/15m/1h/1d) from stored 1m candles, with a gap
    report of trading days that have fewer than 375 stored minutes."""
    inst = instrument.upper()
    if inst not in upstox_client.INSTRUMENT_KEYS:
        raise HTTPException(400, f"Unsupported instrument: {instrument}")
    if timeframe not in TIMEFRAME_RULES:
        raise HTTPException(400, f"Unsupported timeframe: {timeframe}. Use one of {list(TIMEFRAME_RULES)}")
    resp = await build_ohlc_response(
        load_candles_df,
        instrument=inst,
        start_ts=start_ts,
        end_ts=end_ts,
        timeframe=timeframe,
        include_gaps=include_gaps,
    )
    return serialize_doc(resp)


@api.get("/warehouse/audit/{instrument}")
async def warehouse_audit(
    instrument: str,
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
):
    if instrument.upper() not in ("NIFTY", "BANKNIFTY", "SENSEX"):
        raise HTTPException(400, f"Unsupported instrument: {instrument}")
    return await audit_integrity(instrument.upper(), start_ts=start_ts, end_ts=end_ts)


@api.delete("/warehouse/data/{instrument}")
async def warehouse_clear_data(instrument: str, confirm: str = Query("")):
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "BANKNIFTY", "SENSEX", "ALL"):
        raise HTTPException(400, f"Unsupported instrument: {instrument}")
    if confirm != "CLEAR":
        raise HTTPException(400, "Clear requires confirm=CLEAR")
    result = await clear_warehouse_data(None if instrument == "ALL" else instrument)
    return {"ok": True, "instrument": instrument, **result}


@api.post("/volatility/audit")
async def volatility_audit(req: VolatilityAuditReq):
    """Annotate spot 1m bars in the window with realized vs 30-day baseline ratios.

    Returns a summary plus a sample of spike minutes. Useful for filtering
    backtest trades that fired during chaotic conditions, or for reviewing
    whether a strategy's edge depends disproportionately on high-vol bars.
    """
    if req.instrument.upper() not in upstox_client.INSTRUMENT_KEYS:
        raise HTTPException(400, f"Unsupported instrument: {req.instrument}")
    start_ts, end_ts = _ist_market_bounds_ms(req.from_date, req.to_date)
    df = await load_candles_df(req.instrument.upper(), start_ts=start_ts, end_ts=end_ts)
    if df.empty:
        return serialize_doc({
            "instrument": req.instrument.upper(),
            "from_date": req.from_date,
            "to_date": req.to_date,
            "summary": {"total_bars": 0, "spike_bars": 0, "spike_pct": 0.0, "max_ratio": None},
            "spikes": [],
            "config": VolatilityConfig().to_dict(),
        })
    cfg = VolatilityConfig.from_dict({
        "spike_threshold": req.spike_threshold,
        "realized_window": req.realized_window,
        "baseline_lookback_bars": req.baseline_lookback_bars,
    })
    enriched = annotate_volatility(df, cfg)
    summary = summarize_spikes(enriched)
    # Surface up to 20 spike rows so the UI has a tangible list, not just a count
    spike_rows = (
        enriched[enriched["volatility_spike"]]
        .sort_values("vol_ratio", ascending=False)
        .head(20)
        [["ts", "datetime", "close", "realized_vol_5m", "vol_baseline_30d", "vol_ratio"]]
        .to_dict(orient="records")
    )
    return serialize_doc({
        "instrument": req.instrument.upper(),
        "from_date": req.from_date,
        "to_date": req.to_date,
        "summary": summary,
        "spikes": spike_rows,
        "config": cfg.to_dict(),
    })


@api.post("/upstox/warehouse/ingest")
async def upstox_warehouse_ingest(req: UpstoxIngestReq):
    """Fetch 1m candles from Upstox V3 and persist into the SAME warehouse used by yfinance ingest."""
    if req.instrument.upper() not in upstox_client.INSTRUMENT_KEYS:
        raise HTTPException(400, f"Unsupported instrument: {req.instrument}")

    guidance = chunk_guidance_for_index(req.from_date, req.to_date, req.chunk_days)
    chunk_days = int(guidance["chunk_days"])
    db = get_db()
    run_id = str(_uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    await db.warehouse_runs.insert_one({
        "id": run_id,
        "instrument": req.instrument.upper(),
        "source": "upstox",
        "started_at": started_at,
        "status": "running",
        "from_date": req.from_date,
        "to_date": req.to_date,
        "days": guidance["calendar_days"],
        "chunk_days": chunk_days,
        "chunk_mode": guidance["mode"],
    })
    try:
        df = await upstox_client.fetch_historical_1m_chunked(
            req.instrument.upper(), req.from_date, req.to_date, max_days_per_call=chunk_days,
        )
    except Exception as e:
        log.exception("upstox ingest failed")
        await db.warehouse_runs.update_one(
            {"id": run_id},
            {"$set": {"status": "failed", "finished_at": datetime.now(timezone.utc).isoformat(), "error": str(e)[:500]}},
        )
        return {"run_id": run_id, "status": "failed", "error": str(e)[:500], "chunk_guidance": guidance}

    if df.empty:
        await db.warehouse_runs.update_one(
            {"id": run_id},
            {"$set": {"status": "empty", "finished_at": datetime.now(timezone.utc).isoformat(), "candles_added": 0}},
        )
        return {"run_id": run_id, "status": "empty", "candles_added": 0, "chunk_guidance": guidance}

    # Upsert into candles_1m + integrity hashes (same logic as warehouse.ingest_yfinance)
    import hashlib
    coll = db.candles_1m
    inserted = updated = 0
    for d in df.to_dict(orient="records"):
        result = await coll.update_one(
            {"instrument": d["instrument"], "ts": int(d["ts"])},
            {"$set": {
                "instrument": d["instrument"], "ts": int(d["ts"]),
                "datetime": str(d["datetime"]),
                "open": float(d["open"]), "high": float(d["high"]),
                "low": float(d["low"]), "close": float(d["close"]),
                "volume": float(d.get("volume", 0) or 0),
            }},
            upsert=True,
        )
        if result.upserted_id is not None:
            inserted += 1
        elif result.modified_count > 0:
            updated += 1

    df["date_str"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d")
    for date_str, grp in df.groupby("date_str"):
        payload = grp[["ts", "open", "high", "low", "close", "volume"]].to_json(orient="values").encode("utf-8")
        h = hashlib.sha256(payload).hexdigest()[:16]
        await db.integrity_hashes.update_one(
            {"instrument": req.instrument.upper(), "date": date_str},
            {"$set": {
                "instrument": req.instrument.upper(),
                "date": date_str,
                "hash": h,
                "candle_count": int(len(grp)),
                "computed_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )

    await db.warehouse_runs.update_one(
        {"id": run_id},
        {"$set": {
            "status": "ok",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "candles_added": inserted,
            "candles_updated": updated,
            "total_fetched": len(df),
        }},
    )
    return {
        "run_id": run_id,
        "status": "ok",
        "candles_added": inserted,
        "candles_updated": updated,
        "total_fetched": len(df),
        "chunk_guidance": guidance,
    }


@api.post("/upstox/warehouse/ingest/jobs")
async def start_upstox_warehouse_ingest_job(req: UpstoxIngestReq, background_tasks: BackgroundTasks):
    """Start a long Upstox index ingest in the background and return immediately."""
    instrument = req.instrument.upper()
    if instrument not in upstox_client.INSTRUMENT_KEYS:
        raise HTTPException(400, f"Unsupported instrument: {req.instrument}")

    guidance = chunk_guidance_for_index(req.from_date, req.to_date, req.chunk_days)
    chunk_days = int(guidance["chunk_days"])
    run_id = str(_uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": run_id,
        "instrument": instrument,
        "source": "upstox_background",
        "started_at": timestamp,
        "updated_at": timestamp,
        "status": "queued",
        "from_date": req.from_date,
        "to_date": req.to_date,
        "days": guidance["calendar_days"],
        "chunk_days": chunk_days,
        "chunk_mode": guidance["mode"],
        "total_chunks": guidance["estimated_api_calls"],
        "completed_chunks": 0,
        "progress_pct": 0,
        "total_fetched": 0,
        "candles_added": 0,
        "candles_updated": 0,
        "matched_existing": 0,
        "failed_chunks": [],
    }
    await get_db().warehouse_runs.insert_one(doc)
    background_tasks.add_task(
        run_upstox_index_ingest_job,
        run_id,
        instrument,
        req.from_date,
        req.to_date,
        chunk_days,
    )
    return serialize_doc({**doc, "chunk_guidance": guidance})


@api.get("/upstox/warehouse/ingest/jobs/{run_id}")
async def get_upstox_warehouse_ingest_job(run_id: str):
    doc = await get_db().warehouse_runs.find_one({"id": run_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Ingest job not found")
    return serialize_doc(doc)


@api.post("/upstox/options/warehouse/preview")
async def upstox_option_warehouse_preview(req: OptionWarehousePlanReq):
    """Preview which option contracts/candles are needed before broker downloads."""
    plan = await _build_option_warehouse_preview(req)
    return serialize_doc(compact_option_plan_for_response(plan))


@api.post("/upstox/options/warehouse/fetch")
async def upstox_option_warehouse_fetch(req: OptionWarehousePlanReq):
    """Fetch the planned missing option candles into the local options_1m warehouse."""
    preview = await _build_option_warehouse_preview(req)
    items = preview.get("items", [])
    to_fetch = [item for item in items if item.get("needs_fetch")] if req.fetch_missing_only else list(items)
    max_contracts = max(1, int(req.max_contracts or 1))
    if len(to_fetch) > max_contracts and not req.confirm_large_fetch:
        raise HTTPException(
            400,
            f"Fetch would request {len(to_fetch)} option contracts, above max_contracts={max_contracts}. Narrow the request or set confirm_large_fetch=true.",
        )

    status = await upstox_client.get_connection_status()
    if not status.get("connected"):
        raise HTTPException(400, "Upstox is not connected. Complete OAuth before fetching option candles.")
    if status.get("expired"):
        raise HTTPException(400, "Upstox token expired. Reconnect Upstox before fetching option candles.")

    db = get_db()
    guidance = preview.get("chunk_guidance", {})
    chunk_days = int(guidance.get("chunk_days") or req.chunk_days or 7)
    run_id = str(_uuid.uuid4())
    await db.warehouse_runs.insert_one({
        "id": run_id,
        "instrument": req.underlying.upper(),
        "source": "upstox_options_warehouse",
        "collection": "options_1m",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "from_date": req.from_date,
        "to_date": req.to_date,
        "planned_contracts": len(items),
        "fetch_contracts": len(to_fetch),
        "chunk_days": chunk_days,
    })

    fetched: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    total_fetched = candles_added = candles_updated = 0
    for item in to_fetch:
        contract = {
            "underlying": item.get("underlying"),
            "expiry_date": item.get("expiry_date"),
            "strike": item.get("strike"),
            "side": item.get("side"),
            "trading_symbol": item.get("trading_symbol", ""),
            "lot_size": item.get("lot_size"),
        }
        instrument_key = str(item["instrument_key"])
        try:
            result = await upstox_client.fetch_historical_1m_for_key_chunked(
                instrument_key,
                req.from_date,
                req.to_date,
                max_days_per_call=chunk_days,
                contract=contract,
            )
            df = result["df"]
            persist_result = await persist_option_candles_df(db, df)
            fetched_count = int(len(df))
            total_fetched += fetched_count
            candles_added += int(persist_result["candles_added"])
            candles_updated += int(persist_result["candles_updated"])
            fetched.append({
                "instrument_key": instrument_key,
                "trading_symbol": item.get("trading_symbol", ""),
                "fetched": fetched_count,
                **persist_result,
                "failed_chunks": result.get("failed_chunks", []),
            })
            for chunk in result.get("failed_chunks", []):
                failed.append({"instrument_key": instrument_key, **chunk})
        except Exception as e:
            log.warning("option warehouse fetch failed for %s: %s", instrument_key, e)
            failed.append({"instrument_key": instrument_key, "error": str(e)[:300]})

    if failed and fetched:
        final_status = "partial"
    elif failed:
        final_status = "failed"
    elif not fetched:
        final_status = "empty"
    else:
        final_status = "ok"

    await db.warehouse_runs.update_one(
        {"id": run_id},
        {"$set": {
            "status": final_status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "total_fetched": total_fetched,
            "candles_added": candles_added,
            "candles_updated": candles_updated,
            "failed": failed[:100],
        }},
    )
    return {
        "run_id": run_id,
        "status": final_status,
        "planned_contracts": len(items),
        "fetch_contracts": len(to_fetch),
        "chunk_guidance": guidance,
        "total_fetched": total_fetched,
        "candles_added": candles_added,
        "candles_updated": candles_updated,
        "fetched": fetched[:200],
        "failed": failed[:100],
    }


@api.post("/upstox/options/warehouse/fetch/jobs")
async def start_upstox_option_warehouse_fetch_job(req: OptionWarehousePlanReq, background_tasks: BackgroundTasks):
    """Start a long option warehouse fetch in the background and return immediately."""
    preview = await _build_option_warehouse_preview(req)
    items = preview.get("items", [])
    to_fetch = [item for item in items if item.get("needs_fetch")] if req.fetch_missing_only else list(items)
    max_contracts = max(1, int(req.max_contracts or 1))
    if len(to_fetch) > max_contracts and not req.confirm_large_fetch:
        raise HTTPException(
            400,
            f"Fetch would request {len(to_fetch)} option contracts, above max_contracts={max_contracts}. Narrow the request or set confirm_large_fetch=true.",
        )

    status = await upstox_client.get_connection_status()
    if not status.get("connected"):
        raise HTTPException(400, "Upstox is not connected. Complete OAuth before fetching option candles.")
    if status.get("expired"):
        raise HTTPException(400, "Upstox token expired. Reconnect Upstox before fetching option candles.")

    guidance = preview.get("chunk_guidance", {})
    chunk_days = int(guidance.get("chunk_days") or req.chunk_days or 7)
    tasks = option_fetch_tasks_from_plan(preview, fetch_missing_only=req.fetch_missing_only)
    run_id = str(_uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": run_id,
        "instrument": req.underlying.upper(),
        "source": "upstox_options_background",
        "collection": "options_1m",
        "started_at": timestamp,
        "updated_at": timestamp,
        "status": "queued",
        "from_date": req.from_date,
        "to_date": req.to_date,
        "planned_contracts": len(items),
        "fetch_contracts": len(to_fetch),
        "total_tasks": len(tasks),
        "completed_tasks": 0,
        "progress_pct": 0,
        "chunk_days": chunk_days,
        "chunk_guidance": guidance,
        "total_fetched": 0,
        "candles_added": 0,
        "candles_updated": 0,
        "matched_existing": 0,
        "failed": [],
    }
    await get_db().warehouse_runs.insert_one(doc)
    background_tasks.add_task(
        run_option_warehouse_fetch_job,
        run_id,
        preview,
        fetch_missing_only=req.fetch_missing_only,
        chunk_days=chunk_days,
    )
    return serialize_doc(doc)


@api.get("/upstox/options/warehouse/fetch/jobs/{run_id}")
async def get_upstox_option_warehouse_fetch_job(run_id: str):
    doc = await get_db().warehouse_runs.find_one({"id": run_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Option fetch job not found")
    return serialize_doc(doc)


@api.post("/data-hygiene/plan")
async def data_hygiene_plan_route(req: DataHygieneScopeReq):
    """Compute the hygiene plan against the current warehouse. Pure read of the
    warehouse - never fetches. The result is persisted as the single
    `data_hygiene_latest` doc so the page can show the last-known state
    instantly on load instead of forcing a 5-15s check first."""
    db = get_db()
    plan = await compute_hygiene_plan(
        db,
        start_date=req.start_date,
        end_date=req.end_date,
        instruments=req.instruments,
        moneyness=req.moneyness,
        legs=req.legs,
        sample_interval_minutes=req.sample_interval_minutes,
    )
    try:
        # `id` must stay "latest" — the plan dict carries its own uuid `id`,
        # so it is kept under `plan_id` and must not win the key collision.
        await db.data_hygiene_latest.replace_one(
            {"id": "latest"}, {**plan, "id": "latest", "plan_id": plan.get("id")}, upsert=True,
        )
    except Exception:
        log.exception("failed to persist latest hygiene plan (non-fatal)")
    return serialize_doc(plan)


@api.get("/data-hygiene/latest")
async def data_hygiene_latest_route():
    """Last persisted hygiene plan (instant — no aggregation). Null until the
    first check has run."""
    doc = await get_db().data_hygiene_latest.find_one({"id": "latest"}, {"_id": 0})
    return serialize_doc({"plan": doc})


@api.post("/data-hygiene/catch-up")
async def data_hygiene_catch_up_route(req: DataHygieneCatchUpReq):
    """Incrementally bring spot + option data up to the last closed session.

    For each instrument this computes the gap from the last stored spot date to
    the most recent closed trading session, then runs a SEQUENTIAL per-instrument
    chain: spot ingest -> current contract sync -> band-exact option-candle fill
    over the full rolling window. The sequencing matters because the option
    band reads the freshly ingested spot candles + contracts to resolve the
    day's strike band; running them in parallel (as the full hygiene execute
    does) fails for brand-new days because the spot candles are not persisted
    yet. Instruments whose SPOT is already current still get a band sweep —
    wick-edge strikes can be missing with no new session to ingest. `dry_run=
    true` returns the plan without fetching. `include_options=false` updates
    spot only.

    Requires a connected, non-expired Upstox token (unless dry_run).
    """
    return await _run_warehouse_sync(req)


@api.post("/warehouse/sync")
async def warehouse_sync_route(req: DataHygieneCatchUpReq):
    """One-button warehouse sync — alias of /data-hygiene/catch-up.

    Catch-up to the last closed session, then band-exact gap fill, with
    broker-proven-empty pairs excluded by the option_known_empty ledger.
    """
    return await _run_warehouse_sync(req)


async def _run_warehouse_sync(req: DataHygieneCatchUpReq):
    # Historical range mode: BOTH dates present routes to the range planner +
    # range chain (dry-run-first is enforced there via the confirm gate).
    if req.from_date or req.to_date:
        return await _run_historical_range_ingest(req)
    db = get_db()
    plan = await compute_catch_up_plan(
        db,
        instruments=req.instruments,
        moneyness=req.moneyness,
        legs=req.legs,
        sample_interval_minutes=req.sample_interval_minutes,
    )

    # Spot-only mode: strip contracts/option_candles actions before executing.
    if not req.include_options:
        for inst in plan.get("instruments", []):
            inst["actions"] = [a for a in inst.get("actions", []) if a.get("kind") == "spot"]
        plan["summary"]["total_actions"] = sum(
            len(inst.get("actions", [])) for inst in plan.get("instruments", [])
        )

    total_actions = int(plan.get("summary", {}).get("total_actions") or 0)
    if req.dry_run:
        return serialize_doc({"plan": plan, "submitted_count": 0, "dry_run": True})

    # India VIX is part of "in sync" too — refresh it on every real sync, even
    # when spot/options are already current (the volatility-context layer should
    # not need a separate manual click). Self-guards on the Upstox connection.
    vix_result = await _topup_vix()

    # Band sweep for instruments whose spot is already current: the chain's
    # option stage only runs for gapped instruments, but wick-edge band pairs
    # can be missing even when there is no new session to ingest (e.g. the
    # nightly update ran, yesterday was volatile, and the band demands strikes
    # beyond what the live stream captured). Build the exact plans up front so
    # an all-current warehouse with zero band needs short-circuits to
    # up_to_date without touching the broker.
    target_end = str(plan.get("summary", {}).get("target_end") or "")
    band_sweeps: List[tuple] = []
    if req.include_options and target_end:
        for inst_report in plan.get("instruments", []):
            if not inst_report.get("up_to_date"):
                continue  # the catch-up chain band-fills these in stage 3
            instrument = str(inst_report["instrument"]).upper()
            if instrument not in upstox_client.INSTRUMENT_KEYS:
                continue
            band_plan = await build_band_fetch_plan(
                db, instrument, default_scope_start(), target_end,
                legs=list(req.legs or HYGIENE_DEFAULT_LEGS),
            )
            if band_plan.get("items"):
                band_sweeps.append((instrument, band_plan))

    if total_actions == 0 and not band_sweeps:
        return serialize_doc({"plan": plan, "submitted": [], "submitted_count": 0,
                              "up_to_date": True, "vix": vix_result})

    status = await upstox_client.get_connection_status()
    if not status.get("connected"):
        raise HTTPException(400, "Upstox is not connected. Connect it before running catch-up.")
    if status.get("expired"):
        raise HTTPException(400, "Upstox token expired. Reconnect before running catch-up.")

    # One sequential background chain per instrument that has a gap. Each chain
    # creates its own tracked `data_hygiene` warehouse_runs doc (one per stage),
    # so the existing Data Hygiene progress UI and job tracker pick it up.
    submitted: List[Dict[str, Any]] = []
    for inst_report in plan.get("instruments", []):
        if inst_report.get("up_to_date"):
            continue
        instrument = str(inst_report["instrument"]).upper()
        if instrument not in upstox_client.INSTRUMENT_KEYS:
            continue
        from_date = inst_report["from_date"]
        to_date = inst_report["to_date"]
        run_ids = await _start_catch_up_chain(
            instrument=instrument,
            from_date=from_date,
            to_date=to_date,
            include_options=bool(req.include_options),
            moneyness=list(req.moneyness or HYGIENE_DEFAULT_MONEYNESS),
            legs=list(req.legs or HYGIENE_DEFAULT_LEGS),
            sample_interval_minutes=int(req.sample_interval_minutes or HYGIENE_DEFAULT_SAMPLE),
            chunk_days_spot=int(req.chunk_days_spot or 30),
        )
        for kind, run_id in run_ids:
            submitted.append({
                "action_id": f"{kind}_{instrument}",
                "kind": kind,
                "instrument": instrument,
                "run_id": run_id,
            })

    for instrument, band_plan in band_sweeps:
        run_id = await submit_band_fetch_run(instrument, band_plan)
        submitted.append({
            "action_id": f"options_{instrument}",
            "kind": "option_candles",
            "instrument": instrument,
            "run_id": run_id,
        })

    return serialize_doc({
        "plan": plan,
        "submitted": submitted,
        "submitted_count": len(submitted),
        "band_sweeps": [
            {"instrument": i, "missing_pairs": p.get("missing_pairs", 0)} for i, p in band_sweeps
        ],
        "vix": vix_result,
    })


async def _run_historical_range_ingest(req: DataHygieneCatchUpReq):
    """Historical range ingestion: dry-run-first plan → typed confirm → the
    sequential range chain (spot → expired+current contracts → band options).

    The plan is NEVER persisted to data_hygiene_latest (a historical range must
    not overwrite the rolling-window health strip the page loads instantly).
    Upsert-only end to end — this path can never delete or rewrite candles
    beyond broker-value corrections on re-fetched days.
    """
    if not (req.from_date and req.to_date):
        raise HTTPException(
            400, "historical range ingestion needs BOTH from_date and to_date")
    db = get_db()
    try:
        plan = await compute_range_ingest_plan(
            db,
            from_date=str(req.from_date),
            to_date=str(req.to_date),
            instruments=req.instruments,
            include_options=bool(req.include_options),
            legs=req.legs,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if req.dry_run:
        return serialize_doc({"plan": plan, "submitted_count": 0, "dry_run": True})
    if not req.confirm:
        raise HTTPException(
            400,
            "historical range ingestion always plans first: call with "
            "dry_run=true, review the plan, then re-post with confirm=true")

    total_actions = int(plan.get("summary", {}).get("total_actions") or 0)
    if total_actions == 0:
        return serialize_doc({"plan": plan, "submitted": [], "submitted_count": 0,
                              "up_to_date": True})

    status = await upstox_client.get_connection_status()
    if not status.get("connected"):
        raise HTTPException(400, "Upstox is not connected. Connect it before running a historical ingest.")
    if status.get("expired"):
        raise HTTPException(400, "Upstox token expired. Reconnect before running a historical ingest.")

    submitted: List[Dict[str, Any]] = []
    for inst_report in plan.get("instruments", []):
        if inst_report.get("up_to_date"):
            continue
        instrument = str(inst_report["instrument"]).upper()
        if instrument not in upstox_client.INSTRUMENT_KEYS:
            continue
        spot_action = next(
            (a for a in inst_report.get("actions", []) if a.get("kind") == "spot"), None)
        run_ids = await _start_historical_range_chain(
            instrument=instrument,
            from_date=inst_report["from_date"],
            to_date=inst_report["to_date"],
            spot_from=(spot_action or {}).get("from_date"),
            spot_to=(spot_action or {}).get("to_date"),
            include_options=bool(req.include_options),
            legs=list(req.legs or HYGIENE_DEFAULT_LEGS),
            chunk_days_spot=int(req.chunk_days_spot or 30),
        )
        for kind, run_id in run_ids:
            submitted.append({
                "action_id": f"{kind}_{instrument}",
                "kind": kind,
                "instrument": instrument,
                "run_id": run_id,
            })

    return serialize_doc({
        "plan": plan,
        "submitted": submitted,
        "submitted_count": len(submitted),
    })


@api.post("/data-hygiene/execute")
async def data_hygiene_execute_route(req: DataHygieneExecuteReq):
    """Submit the suggested fetches in dependency order: spot -> contracts -> option_candles.

    Re-running the same plan is safe: each diff is recomputed on submit.
    """
    if not req.plan:
        raise HTTPException(400, "plan body required (use /data-hygiene/plan first)")
    result = await execute_hygiene_plan(
        get_db(),
        req.plan,
        submit_spot=_hygiene_submit_spot,
        submit_contracts=_hygiene_submit_contracts,
        submit_option_candles=_hygiene_submit_option_candles,
        chunk_days_spot=int(req.chunk_days_spot or 30),
    )
    return serialize_doc(result)


@api.get("/data-hygiene/status")
async def data_hygiene_status_route(plan_id: Optional[str] = Query(None)):
    """Return the most recent data-hygiene run docs and their progress."""
    return serialize_doc(await hygiene_status(get_db(), plan_id=plan_id))


@api.get("/warehouse/auto-update/status")
async def warehouse_autoupdate_status():
    """Return the warehouse auto-update worker status (last run, schedule, etc.)."""
    return AUTOUPDATE_STATE.snapshot()


@api.post("/warehouse/auto-update/toggle")
async def warehouse_autoupdate_toggle(req: AutoUpdateToggleReq):
    """Enable or disable automatic warehouse catch-up (startup / OAuth / daily)."""
    AUTOUPDATE_STATE.enabled = bool(req.enabled)
    return AUTOUPDATE_STATE.snapshot()


@api.post("/warehouse/auto-update/run")
async def warehouse_autoupdate_run_now():
    """Trigger a warehouse auto-update catch-up immediately (manual)."""
    summary = await _trigger_autoupdate("manual")
    return {"summary": summary, "state": AUTOUPDATE_STATE.snapshot()}


@api.get("/warehouse/vix/coverage")
async def warehouse_vix_coverage():
    """Report India VIX coverage in the warehouse (count + date range)."""
    db = get_db()
    pipeline = [
        {"$match": {"instrument": VIX_INSTRUMENT}},
        {"$group": {"_id": None, "count": {"$sum": 1}, "min_ts": {"$min": "$ts"}, "max_ts": {"$max": "$ts"}}},
    ]
    rows = await db.candles_1m.aggregate(pipeline).to_list(length=1)
    if not rows:
        return {"instrument": VIX_INSTRUMENT, "count": 0, "min_ts": None, "max_ts": None,
                "baseline_start": VIX_BASELINE_START}
    r = rows[0]
    return {"instrument": VIX_INSTRUMENT, "count": int(r.get("count") or 0),
            "min_ts": r.get("min_ts"), "max_ts": r.get("max_ts"),
            "baseline_start": VIX_BASELINE_START}


@api.post("/warehouse/vix/ingest")
async def warehouse_vix_ingest(req: VixIngestReq):
    """Fetch India VIX 1m candles from Upstox and persist into candles_1m as INDIAVIX.

    VIX powers the volatility-context layer (vix_bucket on every trade). Requires
    a connected, non-expired Upstox token.
    """
    status = await upstox_client.get_connection_status()
    if not status.get("connected"):
        raise HTTPException(400, "Upstox is not connected.")
    if status.get("expired"):
        raise HTTPException(400, "Upstox token expired. Reconnect first.")
    try:
        result = await upstox_client.fetch_historical_1m_for_key_chunked(
            vix_instrument_key(), req.from_date, req.to_date, max_days_per_call=int(req.chunk_days or 7),
        )
        df = result["df"]
        if df.empty:
            return {"status": "empty", "fetched": 0, "candles_added": 0,
                    "failed_chunks": result.get("failed_chunks", [])}
        # The by-key fetch returns an `instrument_key` column; persist_index_candles_bulk
        # dedups/sets on `instrument`. Stamp it as the AUX VIX instrument name.
        df = df.copy()
        df["instrument"] = VIX_INSTRUMENT
        saved = await persist_index_candles_bulk(VIX_INSTRUMENT, df)
        return {
            "status": "ok",
            "fetched": int(len(df)),
            "candles_added": saved["upserted"],
            "candles_updated": saved["modified"],
            "dates": len(saved["dates"]),
            "failed_chunks": result.get("failed_chunks", []),
        }
    except Exception as e:
        raise HTTPException(400, str(e)[:300])


@api.post("/upstox/options/candles/ingest")
async def upstox_option_candles_ingest(req: UpstoxOptionCandleIngestReq):
    """Fetch 1m option candles from Upstox and persist them into options_1m."""
    db = get_db()
    local_contract = await db.option_contracts.find_one({"instrument_key": req.instrument_key}, {"_id": 0})
    contract = _overlay_option_contract_metadata(local_contract, req)
    required = ("underlying", "expiry_date", "strike", "side")
    missing = [field for field in required if contract.get(field) in (None, "")]
    if missing:
        raise HTTPException(400, f"Missing option metadata: {', '.join(missing)}. Sync option contracts first or pass metadata.")

    run_id = str(_uuid.uuid4())
    await db.warehouse_runs.insert_one({
        "id": run_id,
        "instrument_key": req.instrument_key,
        "underlying": str(contract["underlying"]).upper(),
        "source": "upstox_options",
        "collection": "options_1m",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "from_date": req.from_date,
        "to_date": req.to_date,
    })
    try:
        result = await upstox_client.fetch_historical_1m_for_key_chunked(
            req.instrument_key,
            req.from_date,
            req.to_date,
            max_days_per_call=req.chunk_days,
            contract=contract,
        )
        df = result["df"]
        failed_chunks = result["failed_chunks"]
        persist_result = await persist_option_candles_df(db, df)
    except Exception as e:
        log.exception("upstox option candle ingest failed")
        await db.warehouse_runs.update_one(
            {"id": run_id},
            {"$set": {"status": "failed", "finished_at": datetime.now(timezone.utc).isoformat(), "error": str(e)[:500]}},
        )
        raise HTTPException(400, str(e)[:300])

    status = "ok"
    if failed_chunks and not df.empty:
        status = "partial"
    elif failed_chunks:
        status = "failed"
    elif df.empty:
        status = "empty"

    await db.warehouse_runs.update_one(
        {"id": run_id},
        {"$set": {
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "candles_added": persist_result["candles_added"],
            "candles_updated": persist_result["candles_updated"],
            "total_fetched": int(len(df)),
            "failed_chunks": failed_chunks,
        }},
    )
    return {
        "run_id": run_id,
        "status": status,
        "instrument_key": req.instrument_key,
        "contract": serialize_doc(contract),
        "total_fetched": int(len(df)),
        "failed_chunks": failed_chunks,
        **persist_result,
    }


@api.get("/options/candles")
async def local_option_candles(
    instrument_key: Optional[str] = Query(None),
    underlying: Optional[str] = Query(None),
    expiry: Optional[str] = Query(None),
    strike: Optional[float] = Query(None),
    side: Optional[str] = Query(None),
    start_ts: Optional[int] = Query(None),
    end_ts: Optional[int] = Query(None),
    limit: int = Query(500, le=10000),
):
    """Read locally persisted option candles for strategy testing."""
    q: Dict[str, Any] = {}
    if instrument_key:
        q["instrument_key"] = instrument_key
    if underlying:
        q["underlying"] = underlying.upper()
    if expiry:
        q["expiry_date"] = expiry
    if strike is not None:
        q["strike"] = float(strike)
    if side:
        q["side"] = side.upper()
    if start_ts is not None or end_ts is not None:
        rng: Dict[str, int] = {}
        if start_ts is not None:
            rng["$gte"] = int(start_ts)
        if end_ts is not None:
            rng["$lte"] = int(end_ts)
        q["ts"] = rng

    db = get_db()
    cursor = db.options_1m.find(q, {"_id": 0}).sort("ts", 1)
    items = await cursor.to_list(length=limit)
    return {"items": serialize_doc(items), "count": len(items), "source": "local_option_candles"}


@api.get("/options/coverage")
async def local_option_coverage(
    underlying: Optional[str] = Query(None),
    refresh: bool = Query(False),
):
    """Summarize stored option candles by date for heatmap visibility.

    Served from the precomputed `option_coverage_cache` so the Data Warehouse
    page loads fast even with 5M+ option candles. Pass `refresh=1` to force a
    recompute (e.g. after a manual data change).
    """
    if underlying and underlying.upper() not in upstox_client.INSTRUMENT_KEYS:
        raise HTTPException(400, f"Unsupported instrument: {underlying}")
    instruments = await get_option_coverage_cached(get_db(), underlying=underlying, force_refresh=refresh)
    return {
        "instruments": serialize_doc(instruments),
        "source": "option_coverage_cache" if not refresh else "option_coverage_cache_refreshed",
    }


@api.get("/calendar/holidays")
async def market_calendar_holidays(year: Optional[int] = Query(None)):
    """Return the NSE/BSE market-holiday calendar for a year (UI modal).

    Without a year, returns the list of available curated years plus the current
    year's calendar. With ?year=YYYY, returns that year's holidays + special
    sessions with human-readable labels.
    """
    years = available_calendar_years()
    if year is None:
        from datetime import date as _date
        year = _date.today().year
        if year not in years and years:
            year = years[-1]
    return {
        "available_years": years,
        "calendar": calendar_for_year(year),
    }


@api.get("/options/audit/{instrument}")
async def local_option_data_audit(
    instrument: str,
    start_ts: int = Query(...),
    end_ts: int = Query(...),
    expiry: Optional[str] = Query(None),
    side: Optional[str] = Query(None),
    limit_contracts: int = Query(500, le=5000),
):
    """Audit locally stored option candle completeness by contract and date."""
    instrument = instrument.upper()
    if instrument not in upstox_client.INSTRUMENT_KEYS:
        raise HTTPException(400, f"Unsupported instrument: {instrument}")
    if side and side.upper() not in ("CE", "PE"):
        raise HTTPException(400, "side must be CE or PE")
    try:
        return await audit_option_data(
            get_db(),
            underlying=instrument,
            start_ts=start_ts,
            end_ts=end_ts,
            expiry=expiry,
            side=side,
            limit_contracts=limit_contracts,
        )
    except Exception as e:
        raise HTTPException(400, str(e)[:300])


@api.delete("/options/data/{instrument}")
async def local_option_data_clear(instrument: str, confirm: str = Query("")):
    """Clear locally stored option candles separately from index candles."""
    instrument = instrument.upper()
    if instrument not in (*upstox_client.INSTRUMENT_KEYS.keys(), "ALL"):
        raise HTTPException(400, f"Unsupported instrument: {instrument}")
    if confirm != "CLEAR":
        raise HTTPException(400, "Clear requires confirm=CLEAR")
    result = await clear_option_data(get_db(), underlying=instrument)
    # Coverage changed: refresh the cache so the heatmap reflects the cleared state.
    try:
        await refresh_option_coverage_cache(get_db(), underlying=None if instrument == "ALL" else instrument)
    except Exception as exc:
        log.warning(f"Option coverage cache refresh after clear failed: {exc}")
    return {"ok": True, "instrument": instrument, **result}


@api.get("/upstox/expiries/{instrument}")
async def upstox_expiries(instrument: str):
    """Phase 4c prep: list expiry dates for an underlying (Upstox Plus required)."""
    try:
        items = await upstox_client.fetch_expiries(instrument)
        return {"items": items}
    except Exception as e:
        raise HTTPException(400, str(e)[:300])


@api.get("/upstox/options/contracts/{instrument}")
async def upstox_option_contracts(instrument: str, expiry: Optional[str] = Query(None)):
    """Read-only current option contract lookup for live universe selection."""
    try:
        items = await upstox_client.fetch_option_contracts(instrument, expiry=expiry)
        return {"items": items, "count": len(items), "source": "current_option_contract"}
    except Exception as e:
        raise HTTPException(400, str(e)[:300])


@api.post("/upstox/options/contracts/{instrument}/sync")
async def upstox_option_contracts_sync(instrument: str, expiry: Optional[str] = Query(None)):
    """Fetch current option contracts from Upstox and persist metadata locally."""
    try:
        items = await upstox_client.fetch_option_contracts(instrument, expiry=expiry)
        result = await upsert_option_contracts(get_db(), items)
        return {
            "status": "ok",
            "source": "current_option_contract",
            "fetched": len(items),
            **result,
        }
    except Exception as e:
        raise HTTPException(400, str(e)[:300])


@api.get("/options/contracts/{instrument}")
async def local_option_contracts(
    instrument: str,
    expiry: Optional[str] = Query(None),
    side: Optional[str] = Query(None),
    limit: int = Query(5000, le=10000),
):
    """Read locally persisted option contracts for backtests and live selection."""
    q: Dict[str, Any] = {"underlying": instrument.upper()}
    if expiry:
        q["expiry_date"] = expiry
    if side:
        q["side"] = side.upper()
    cursor = get_db().option_contracts.find(q, {"_id": 0}).sort([
        ("expiry_date", 1),
        ("strike", 1),
        ("side", 1),
        ("instrument_key", 1),
    ])
    items = await cursor.to_list(length=limit)
    return {"items": serialize_doc(items), "count": len(items), "source": "local_option_contracts"}


@api.get("/upstox/expired-options/contracts/{instrument}")
async def upstox_expired_option_contracts(instrument: str, expiry: str = Query(...)):
    """Read-only expired option contract lookup for options backtest preparation."""
    try:
        items = await upstox_client.fetch_expired_option_contracts(instrument, expiry)
        return {"items": items, "count": len(items), "source": "expired_option_contract"}
    except Exception as e:
        raise HTTPException(400, str(e)[:300])


@api.post("/upstox/expired-options/contracts/{instrument}/sync")
async def upstox_expired_option_contracts_sync(instrument: str, req: ExpiredOptionContractBackfillReq):
    """Fetch expired option contracts across a date range and persist metadata locally."""
    try:
        return await backfill_expired_option_contracts(
            get_db(),
            instrument,
            from_date=req.from_date,
            to_date=req.to_date,
            max_expiries=req.max_expiries,
            confirm_large_fetch=req.confirm_large_fetch,
        )
    except Exception as e:
        raise HTTPException(400, str(e)[:300])
