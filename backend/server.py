"""AlphaForge Trading Lab — FastAPI server.

All routes prefixed with /api. CORS enabled. MongoDB via Motor.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
import pandas as pd
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from app.db import ensure_indexes, get_db, serialize_doc
from app.chunking import chunk_guidance_for_index, chunk_guidance_for_options
from app.indicators import precompute_all_indicators
from app.regime import classify_regime_series
from app.strategies.base import get_registry
from app.backtest import run_backtest, stat_significance
from app.option_backtest import simulate_paired_option_trades
from app.dte import compute_dte, normalize_dte_filter
from app.vix import VIX_INSTRUMENT, vix_instrument_key, annotate_trades_with_vix

# India VIX backtest baseline start (user-chosen). Auto-update tops up VIX from
# the last stored date, falling back to this when the warehouse has no VIX yet.
VIX_BASELINE_START = "2025-12-29"
from app.option_data_audit import audit_option_data, clear_option_data
from app.option_data_planner import DEFAULT_LEGS, build_option_warehouse_plan
from app.option_plan_response import compact_option_plan_for_response
from app.option_coverage import get_option_coverage
from app.option_coverage_cache import get_option_coverage_cached, refresh_option_coverage_cache
from app.nse_calendar import available_calendar_years, calendar_for_year
from app.warehouse_lookup import lookup_market_snapshot
from app.warehouse_ohlc import TIMEFRAME_RULES, build_ohlc_response
from app.option_warehouse_jobs import option_fetch_tasks_from_plan, run_option_warehouse_fetch_job
from app.expired_contract_backfill import backfill_expired_option_contracts
from app.market_header import DEFAULT_ITEMS, build_market_header_snapshot
from app.options_universe import select_contract_for_signal
from app.paper_trading import close_trade, mark_trade_to_market
from app.paper_auto import mark_open_deployment_trades
from app.signal_lifecycle import SignalStateError, transition_signal
from app.strategy_deployments import build_deployment_doc
from app.strategy_source_hash import detect_drift, hash_strategy_source, build_repin_update
from app.deployment_quality import evaluate_source_quality
from app.preset_execution import execution_from_option_config
from app.forward_metrics import compute_forward_metrics_for_deployment, compute_forward_metrics_for_deployments
from app.upstox_index_ingest import persist_index_candles_bulk, run_upstox_index_ingest_job
from app.upstox_stream import DEFAULT_STREAM_MODE, UpstoxMarketStreamManager
from app.live_candle_roller import LiveCandleRoller
from app.live_option_universe import build_live_option_universe, radius_for_deployments
from app.deployment_evaluator import (
    evaluate_active_deployments,
    evaluate_deployment_on_close,
)
from app.deployment_preflight import compute_data_realism
from app.volatility import VolatilityConfig, annotate_volatility, summarize_spikes
from app.data_hygiene import (
    DEFAULT_INSTRUMENTS as HYGIENE_DEFAULT_INSTRUMENTS,
    DEFAULT_LEGS as HYGIENE_DEFAULT_LEGS,
    DEFAULT_MONEYNESS as HYGIENE_DEFAULT_MONEYNESS,
    DEFAULT_SAMPLE_INTERVAL_MIN as HYGIENE_DEFAULT_SAMPLE,
    DEFAULT_START_DATE as HYGIENE_DEFAULT_START,
    compute_catch_up_plan,
    compute_hygiene_plan,
    execute_hygiene_plan,
    hygiene_status,
)
from app.warehouse_autoupdate import (
    STATE as AUTOUPDATE_STATE,
    daily_autoupdate_loop,
    run_autoupdate_once,
)
from app.paper_squareoff import (
    DEFAULT_SQUARE_OFF_IST,
    is_square_off_due,
    square_off_open_paper_trades,
)
from app.walkforward import walk_forward
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
from app.optimizer import create_job as optimizer_create_job
from app.optimizer import resume_optimization as optimizer_resume_job
from app.wfo import create_wfo_job, resume_wfo_job
from app import upstox_client
from app.option_candles import persist_option_candles_df
from app.option_contract_store import upsert_option_contracts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("alphaforge")

app = FastAPI(title="AlphaForge Trading Lab API")
api = APIRouter(prefix="/api")
upstox_stream_manager = UpstoxMarketStreamManager()
live_candle_roller = LiveCandleRoller(
    stream_manager=upstox_stream_manager,
    db_factory=get_db,
    persister=persist_index_candles_bulk,
)

# ---------------------------------------------------------------------------
# Startup: discover plugins + ensure indexes + seed pretrade profiles
# ---------------------------------------------------------------------------

DEFAULT_PROFILES = {
    "Conservative": {
        "min_confidence_score": 70,
        "max_vix": 28,
        "min_vix": 10,
        "allowed_regimes": ["TREND", "TREND_EXPANDING"],
        "news_block_before_min": 45,
        "news_block_after_min": 30,
        "max_spread_pct": 3.0,
        "cooldown_sec": 180,
        "max_trades_per_day": 3,
        "daily_loss_cutoff_pct": -1.5,
        "trade_window_start": "09:30",
        "trade_window_end": "14:30",
        "bar_close_confirmation": "5m",
        "min_confluence_reasons": 4,
    },
    "Balanced": {
        "min_confidence_score": 60,
        "max_vix": 35,
        "min_vix": 9,
        "allowed_regimes": ["TREND", "TREND_EXPANDING", "MIXED"],
        "news_block_before_min": 30,
        "news_block_after_min": 15,
        "max_spread_pct": 5.0,
        "cooldown_sec": 60,
        "max_trades_per_day": 6,
        "daily_loss_cutoff_pct": -2.0,
        "trade_window_start": "09:25",
        "trade_window_end": "14:50",
        "bar_close_confirmation": "1m",
        "min_confluence_reasons": 3,
    },
    "Aggressive": {
        "min_confidence_score": 50,
        "max_vix": 50,
        "min_vix": 7,
        "allowed_regimes": ["TREND", "TREND_EXPANDING", "MIXED", "CHOP", "VOLATILE_CHOP"],
        "news_block_before_min": 15,
        "news_block_after_min": 5,
        "max_spread_pct": 8.0,
        "cooldown_sec": 30,
        "max_trades_per_day": 12,
        "daily_loss_cutoff_pct": -3.0,
        "trade_window_start": "09:20",
        "trade_window_end": "15:10",
        "bar_close_confirmation": "off",
        "min_confluence_reasons": 2,
    },
}


# Strike radius the live option stream keeps subscribed during market hours so
# the ATM±3 option-chain snapshot and paper marks always have fresh premiums,
# even with no active deployments.
OPTION_CHAIN_BASELINE_RADIUS = 3


async def _deployment_evaluator_loop() -> None:
    """Wake up ~10s after each minute boundary and evaluate ACTIVE deployments.

    Sleeps quietly outside Indian market hours (NSE 09:15 - 15:30 IST, weekdays only).
    Uses the existing 1-minute candle warehouse, so the loop is independent of the
    WebSocket connection state - if the stream is down, it simply finds no fresh bar.

    Also runs a once-per-day paper-trade auto-square-off at 15:00 IST.
    """
    from datetime import time as _time
    log.info("Deployment evaluator loop initialized")
    db = get_db()
    last_squareoff_ist_date: Optional[str] = None
    while True:
        try:
            # Sleep until 10 seconds past the next minute boundary
            now = datetime.now(timezone.utc)
            next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            wake_at = next_minute + timedelta(seconds=10)
            sleep_s = max(1.0, (wake_at - datetime.now(timezone.utc)).total_seconds())
            await asyncio.sleep(sleep_s)

            # Skip evaluation outside NSE market hours (Mon-Fri, 09:15-15:30 IST)
            ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            today_ist = ist_now.strftime("%Y-%m-%d")
            if ist_now.weekday() >= 5:
                continue
            t = ist_now.time()
            if t < _time(9, 15) or t >= _time(15, 30):
                continue

            tick_lookup = upstox_stream_manager.latest_tick_map().get
            results = await evaluate_active_deployments(db, latest_tick_lookup=tick_lookup)
            interesting = [r for r in results if r.get("outcome") in ("clean", "blocked")]
            if interesting:
                log.info(
                    "deployment_evaluator: %d evaluated, %d journaled (%s)",
                    len(results), len(interesting),
                    ", ".join(f"{r['outcome']}/{str(r.get('deployment_id') or '')[:8]}" for r in interesting[:5]),
                )
            auto_opened = [r for r in results if (r.get("auto_paper") or {}).get("created")]
            if auto_opened:
                log.info("auto-paper opened %d trade(s) this bar", len(auto_opened))

            # Mark all OPEN paper trades against the latest live option ticks so
            # stop/target exits actually fire intraday (minute granularity).
            marked = await mark_open_deployment_trades(db, latest_tick_lookup=tick_lookup)
            auto_closed = [m for m in marked if m.get("closed")]
            if auto_closed:
                log.info(
                    "paper marker auto-closed %d trade(s): %s",
                    len(auto_closed),
                    ", ".join(f"{m['id'][:8]}/{m.get('exit_reason')}" for m in auto_closed[:5]),
                )

            # Keep a baseline ATM±3 option universe subscribed during market hours
            # so the live option-chain snapshot (and paper marks) always have fresh
            # premiums, with no manual stream restart needed. Idempotent: only
            # restarts when the ATM band drifts out of the current subscription.
            stream_follow = await _auto_follow_option_stream(min_radius=OPTION_CHAIN_BASELINE_RADIUS)
            if stream_follow.get("restarted"):
                log.info("option stream auto-follow (market-hours baseline): %s", stream_follow)

            # Once per IST date, force-close any open paper trades when we cross the cutoff.
            if last_squareoff_ist_date != today_ist and is_square_off_due(ist_now):
                summaries = await square_off_open_paper_trades(
                    db,
                    latest_tick_lookup=upstox_stream_manager.latest_tick_map().get,
                    reason="auto_square_off_15_00_IST",
                    now_ist=ist_now,
                )
                if summaries:
                    log.info("paper square-off at 15:00 IST closed %d open trades", len(summaries))
                last_squareoff_ist_date = today_ist
        except asyncio.CancelledError:
            log.info("Deployment evaluator loop cancelled")
            return
        except Exception as exc:
            log.exception("Deployment evaluator loop error: %s", exc)
            await asyncio.sleep(15.0)


# ---------------------------------------------------------------------------
# Warehouse auto-update wiring (Slice 5)
# ---------------------------------------------------------------------------

async def _autoupdate_connection_status() -> Dict[str, Any]:
    """Connection probe for the auto-update guard."""
    return await upstox_client.get_connection_status()


async def _autoupdate_compute_plan() -> Dict[str, Any]:
    """Compute a hygiene plan over the default scope (2024-11-27 -> today)."""
    return await compute_hygiene_plan(
        get_db(),
        start_date=HYGIENE_DEFAULT_START,
        end_date=None,
        instruments=list(HYGIENE_DEFAULT_INSTRUMENTS),
        moneyness=list(HYGIENE_DEFAULT_MONEYNESS),
        legs=list(HYGIENE_DEFAULT_LEGS),
        sample_interval_minutes=HYGIENE_DEFAULT_SAMPLE,
    )


async def _autoupdate_execute_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a hygiene plan using the existing dependency-ordered submitters."""
    return await execute_hygiene_plan(
        get_db(),
        plan,
        submit_spot=_hygiene_submit_spot,
        submit_contracts=_hygiene_submit_contracts,
        submit_option_candles=_hygiene_submit_option_candles,
        chunk_days_spot=30,
    )


async def _topup_vix() -> Dict[str, Any]:
    """Best-effort: fetch India VIX 1m candles from the last stored VIX date (or
    the configured baseline 2025-12-29) up to today, into candles_1m as INDIAVIX.

    Runs as part of the warehouse auto-update so the volatility-context layer
    stays current without manual work. Never raises.
    """
    db = get_db()
    try:
        status = await upstox_client.get_connection_status()
        if not status.get("connected") or status.get("expired"):
            return {"status": "skipped", "reason": "upstox_unavailable"}
        # Last stored VIX date, else baseline.
        rows = await db.candles_1m.aggregate([
            {"$match": {"instrument": VIX_INSTRUMENT}},
            {"$group": {"_id": None, "max_ts": {"$max": "$ts"}}},
        ]).to_list(length=1)
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        ist = _td(hours=5, minutes=30)
        if rows and rows[0].get("max_ts"):
            last = (_dt.fromtimestamp(int(rows[0]["max_ts"]) / 1000, tz=_tz.utc) + ist)
            from_date = (last + _td(days=1)).strftime("%Y-%m-%d")
        else:
            from_date = VIX_BASELINE_START
        to_date = (_dt.now(_tz.utc) + ist).strftime("%Y-%m-%d")
        if from_date > to_date:
            return {"status": "ok", "reason": "up_to_date"}
        result = await upstox_client.fetch_historical_1m_for_key_chunked(
            vix_instrument_key(), from_date, to_date, max_days_per_call=7,
        )
        df = result["df"]
        if df.empty:
            return {"status": "empty", "from_date": from_date, "to_date": to_date}
        df = df.copy()
        df["instrument"] = VIX_INSTRUMENT
        saved = await persist_index_candles_bulk(VIX_INSTRUMENT, df)
        return {"status": "ok", "candles_added": saved["upserted"], "from_date": from_date, "to_date": to_date}
    except Exception as exc:
        log.warning("VIX top-up failed: %s", exc)
        return {"status": "error", "error": str(exc)[:200]}


async def _trigger_autoupdate(reason: str) -> Dict[str, Any]:
    """Run one auto-update catch-up with the standard injected callables."""
    # Keep India VIX current alongside spot/option catch-up (best-effort).
    await _topup_vix()
    return await run_autoupdate_once(
        reason=reason,
        connection_status_fn=_autoupdate_connection_status,
        compute_plan_fn=_autoupdate_compute_plan,
        execute_plan_fn=_autoupdate_execute_plan,
    )


@app.on_event("startup")
async def startup() -> None:
    await ensure_indexes()
    registry = get_registry()
    registry.auto_discover()
    log.info(f"Discovered {len(registry.list_all())} strategy plugins")
    # Seed default profiles
    db = get_db()
    for name, settings in DEFAULT_PROFILES.items():
        await db.pretrade_profiles.update_one(
            {"name": name},
            {"$set": {"name": name, "settings": settings, "is_default": True}},
            upsert=True,
        )
    log.info("Pre-trade profiles seeded")

    # Reconcile orphaned optimization jobs. Optimization workers are in-process
    # fire-and-forget asyncio tasks, so any job left "queued"/"running"/
    # "analyzing" in the DB belongs to a previous process (e.g. a container
    # rebuild). Mark them "interrupted" — a resumable state — so the UI stops
    # polling, the Auto-Optimize button re-enables, and the user can Resume the
    # job from its last persisted trial instead of starting over.
    try:
        reconciled = await db.optimization_jobs.update_many(
            {"status": {"$in": ["queued", "running", "analyzing"]}},
            {"$set": {
                "status": "interrupted",
                "paused": False,
                "error": "Interrupted by a server restart — resume to continue.",
                "interrupted_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        if reconciled.modified_count:
            log.info("Reconciled %d interrupted optimization job(s) (resumable)", reconciled.modified_count)
    except Exception as exc:
        log.warning("Optimization job reconciliation failed: %s", exc)

    # Warm the option-coverage cache in the background so the first Data Warehouse
    # page load is fast. The persisted cache from the previous run is still valid
    # at boot (the backend is the only writer to options_1m and refreshes the
    # cache on every write), so this only pays the slow aggregation when the
    # cache is genuinely empty (first ever boot / after a clear).
    async def _warm_option_coverage_cache() -> None:
        try:
            await get_option_coverage_cached(get_db(), underlying=None)
            log.info("Option coverage cache warmed")
        except Exception as exc:
            log.warning(f"Option coverage cache warm-up failed: {exc}")

    asyncio.create_task(_warm_option_coverage_cache(), name="option-coverage-cache-warm")
    # Best-effort: auto-start Upstox WS stream if token is connected and not expired.
    # Header tiles configured with source=upstox will then serve live ticks instead of REST.
    try:
        token_status = await upstox_client.get_connection_status()
        if token_status.get("connected") and not token_status.get("expired"):
            keys = _default_stream_instrument_keys()
            if keys:
                await upstox_stream_manager.start(
                    instrument_keys=keys,
                    mode=DEFAULT_STREAM_MODE,
                    persist=True,
                )
                log.info(f"Upstox WS stream auto-started with {len(keys)} instruments")
                # Also start the live tick -> 1m bar roller so candles_1m gets today's bars.
                # This is what makes the deployment evaluator able to fire on intraday data.
                await live_candle_roller.start()
        else:
            log.info("Upstox not connected at startup; skipping WS auto-start")
    except Exception as exc:
        log.warning(f"Upstox WS auto-start skipped: {exc}")

    # Background scheduler: evaluate ACTIVE deployments ~10s after each 1-minute bar closes.
    asyncio.create_task(_deployment_evaluator_loop(), name="deployment-evaluator")
    log.info("Deployment evaluator scheduler started")

    # Warehouse auto-update: catch up missing data to yesterday's close.
    # Runs once at startup (best-effort, only if Upstox is connected) and then
    # daily at ~18:00 IST. Today's intraday bars come from the live roller.
    async def _startup_autoupdate() -> None:
        try:
            await _trigger_autoupdate("startup")
        except Exception as exc:
            log.warning(f"Startup warehouse auto-update skipped: {exc}")

    asyncio.create_task(_startup_autoupdate(), name="warehouse-autoupdate-startup")
    asyncio.create_task(
        daily_autoupdate_loop(
            connection_status_fn=_autoupdate_connection_status,
            compute_plan_fn=_autoupdate_compute_plan,
            execute_plan_fn=_autoupdate_execute_plan,
        ),
        name="warehouse-autoupdate-daily",
    )
    log.info("Warehouse auto-update scheduler started")


@app.on_event("shutdown")
async def shutdown() -> None:
    try:
        await live_candle_roller.stop()
    except Exception as exc:
        log.warning("live_candle_roller.stop() failed: %s", exc)
    from app.db import get_client
    get_client().close()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@api.get("/")
async def root():
    return {"app": "AlphaForge Trading Lab", "status": "ok", "version": "1.0.0"}


@api.get("/health")
async def health():
    db = get_db()
    try:
        await db.command("ping")
        return {"db": "ok"}
    except Exception as e:
        raise HTTPException(503, str(e))


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@api.get("/strategies")
async def list_strategies():
    return {"items": get_registry().list_all()}


@api.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: str):
    s = get_registry().get(strategy_id)
    if not s:
        raise HTTPException(404, f"Strategy {strategy_id} not found")
    return s.meta()


# ---------------------------------------------------------------------------
# Data Warehouse
# ---------------------------------------------------------------------------

class IngestReq(BaseModel):
    instrument: str
    days: int = 7


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


# ---------------------------------------------------------------------------
# Pre-trade profiles
# ---------------------------------------------------------------------------

@api.get("/profiles")
async def list_profiles():
    db = get_db()
    cur = db.pretrade_profiles.find({}, {"_id": 0}).sort("name", 1)
    rows = await cur.to_list(length=100)
    return {"items": rows}


class ProfileSave(BaseModel):
    name: str
    settings: Dict[str, Any]


@api.put("/profiles/{name}")
async def save_profile(name: str, body: ProfileSave):
    db = get_db()
    await db.pretrade_profiles.update_one(
        {"name": name},
        {"$set": {"name": name, "settings": body.settings, "is_default": False}},
        upsert=True,
    )
    doc = await db.pretrade_profiles.find_one({"name": name}, {"_id": 0})
    return doc


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

class OptionBacktestReq(BaseModel):
    enabled: bool = False
    expiry_date: Optional[str] = None
    # ATM is the default: it matches the warehouse's auto-maintained data scope
    # (Data Hygiene keeps ATM CE/PE current) and the deployment default.
    moneyness: str = "atm"
    lots: int = 1
    entry_max_age_sec: int = 120
    exit_max_age_sec: int = 180
    auto_fetch: bool = True
    max_auto_fetch_contracts: int = 12
    slippage_config: Optional[Dict[str, Any]] = None
    # Option exit mode: "spot_exit" (option mirrors the spot trade's exit) or
    # "option_levels" (exit on the option's own premium target/stop).
    exit_mode: str = "spot_exit"
    option_target_pts: Optional[float] = None
    option_stop_pts: Optional[float] = None
    option_target_pct: Optional[float] = None
    option_stop_pct: Optional[float] = None
    # DTE filter: None/"all" = every weekly expiry; a single token ("dte0".."dte6"
    # or 0..6) or a list of tokens ([0, 1, 2]) = only sessions that many trading
    # days before the nearest expiry. Lets the user test a strategy on, e.g.,
    # expiry-day only (0) or the 0-2 DTE buying window ([0, 1, 2]).
    dte_filter: Optional[Union[str, int, List[Union[str, int]]]] = None
    # Rupee cost model (brokerage + STT + charges + % bid-ask spread). Opt-in;
    # when omitted/disabled the backtest reports gross premium P&L as before.
    cost_config: Optional[Dict[str, Any]] = None
    # Position sizing + capital (premium-at-risk or fixed lots). Opt-in; off keeps
    # the fixed `lots` count. Lot SIZE always comes from the contract metadata.
    sizing_config: Optional[Dict[str, Any]] = None


class BacktestReq(BaseModel):
    instrument: str = "NIFTY"
    mode: str = "SCALP"
    strategy_id: str
    timeframe: str = "1m"
    params: Dict[str, Any] = Field(default_factory=dict)
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    costs_enabled: bool = True
    walkforward: bool = True
    train_pct: float = 0.6
    n_folds: int = 3
    pretrade_filters: Dict[str, Any] = Field(default_factory=dict)
    option_backtest: OptionBacktestReq = Field(default_factory=OptionBacktestReq)
    # Intraday trade window (IST HH:MM). Default 09:25-15:00 implements the
    # user's discipline rule: no entries in the first 10 min (09:15-09:25) or the
    # last 30 min (15:00-15:30). Configurable per run.
    trade_window_start: str = "09:25"
    trade_window_end: str = "15:00"
    name: str = "Untitled Run"


def _ts_ms_to_ist_date_str(ts_ms: int) -> str:
    return pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").tz_convert("Asia/Kolkata").date().isoformat()


async def _audit_and_fill_backtest_data(req: BacktestReq) -> Optional[Dict[str, Any]]:
    if req.start_ts is None or req.end_ts is None:
        return None

    instrument = req.instrument.upper()
    before = await audit_integrity(instrument, start_ts=req.start_ts, end_ts=req.end_ts)
    before_summary = before.get("summary", {})
    fill: Dict[str, Any] = {
        "attempted": False,
        "status": "skipped",
        "reason": "coverage_complete",
    }

    if before_summary.get("expected_days", 0) == 0:
        fill["reason"] = "no_weekday_sessions"
    elif not before_summary.get("complete"):
        status = await upstox_client.get_connection_status()
        if not status.get("connected"):
            fill["reason"] = "upstox_not_connected"
        elif status.get("expired"):
            fill["reason"] = "upstox_token_expired"
        elif instrument not in upstox_client.INSTRUMENT_KEYS:
            fill["reason"] = "instrument_not_supported_by_upstox"
        else:
            from_date = _ts_ms_to_ist_date_str(req.start_ts)
            to_date = _ts_ms_to_ist_date_str(req.end_ts)
            fill = {
                "attempted": True,
                "source": "upstox",
                "status": "running",
                "from_date": from_date,
                "to_date": to_date,
            }
            try:
                df = await upstox_client.fetch_historical_1m_chunked(
                    instrument,
                    from_date,
                    to_date,
                    max_days_per_call=7,
                )
                saved = await persist_candles_df(instrument, df)
                fill.update({
                    "status": "ok" if saved["total_fetched"] else "empty",
                    "fetched": saved["total_fetched"],
                    "candles_added": saved["candles_added"],
                    "candles_updated": saved["candles_updated"],
                })
            except Exception as e:
                log.warning("Backtest Upstox gap-fill failed: %s", e)
                fill.update({"status": "failed", "error": str(e)[:300]})

    after = await audit_integrity(instrument, start_ts=req.start_ts, end_ts=req.end_ts)
    return {
        "before": before_summary,
        "after": after.get("summary", {}),
        "fill": fill,
        "days": after.get("days", []),
    }


def _resolve_option_expiry_by_trade(
    spot_trades: List[Dict[str, Any]],
    contracts: List[Dict[str, Any]],
    fixed_expiry_date: Optional[str] = None,
) -> Dict[int, str]:
    """Resolve the actual option expiry to use for each spot trade.

    When the user supplies an expiry, it is an explicit override. Otherwise use
    the first available contract expiry on or after the trade's IST entry date.
    This lets holiday-adjusted expiries work from contract metadata instead of
    hard-coded weekday assumptions.
    """
    if fixed_expiry_date:
        return {idx: fixed_expiry_date for idx, _ in enumerate(spot_trades)}

    expiries = sorted({
        str(contract.get("expiry_date"))
        for contract in contracts
        if contract.get("expiry_date")
    })
    expiry_by_trade: Dict[int, str] = {}
    for idx, trade in enumerate(spot_trades):
        entry_ts = trade.get("entry_ts")
        if entry_ts is None:
            continue
        trade_date = _ts_ms_to_ist_date_str(int(entry_ts))
        resolved = next((expiry for expiry in expiries if expiry >= trade_date), None)
        if resolved:
            expiry_by_trade[idx] = resolved
    return expiry_by_trade


async def _run_paired_option_backtest(req: BacktestReq, spot_trades: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    config = req.option_backtest
    if not config.enabled:
        return None

    db = get_db()
    underlying = req.instrument.upper()

    # Volatility context: annotate spot trades with India VIX (as-of join) so the
    # context breakdown can show edge by VIX regime. Best-effort — if VIX has not
    # been ingested, trades simply carry vix=None.
    if spot_trades:
        trade_ts = [int(t["entry_ts"]) for t in spot_trades if t.get("entry_ts") is not None]
        if trade_ts:
            vix_rows = await db.candles_1m.find(
                {"instrument": VIX_INSTRUMENT,
                 "ts": {"$gte": min(trade_ts) - 7 * 24 * 3600 * 1000, "$lte": max(trade_ts)}},
                {"_id": 0, "ts": 1, "close": 1},
            ).sort("ts", 1).to_list(length=1000000)
            if vix_rows:
                annotate_trades_with_vix(spot_trades, vix_rows)

    contract_query: Dict[str, Any] = {"underlying": underlying}
    fixed_expiry_date = config.expiry_date
    if fixed_expiry_date:
        contract_query["expiry_date"] = fixed_expiry_date
    elif spot_trades:
        # Load only contracts whose expiry is relevant to the backtest window.
        # This keeps the working set small AND fixes a real bug: an unbounded
        # contract universe sorted ascending would, under a row cap, truncate to
        # the OLDEST expiries and leave recent trades unable to resolve their
        # nearest expiry. We bound by the trades' date span plus a forward margin
        # to cover the last trades' next weekly expiry.
        _ts = [int(t["entry_ts"]) for t in spot_trades if t.get("entry_ts") is not None]
        _xt = [int(t["exit_ts"]) for t in spot_trades if t.get("exit_ts") is not None]
        if _ts:
            win_start = _ts_ms_to_ist_date_str(min(_ts))
            last_ms = max(_xt) if _xt else max(_ts)
            # +21 days forward margin so a trade late in the window still finds
            # its nearest upcoming weekly/monthly expiry.
            win_end = _ts_ms_to_ist_date_str(last_ms + 21 * 24 * 3600 * 1000)
            contract_query["expiry_date"] = {"$gte": win_start, "$lte": win_end}

    contracts = await db.option_contracts.find(contract_query, {"_id": 0}).sort([
        ("expiry_date", 1),
        ("strike", 1),
        ("side", 1),
    ]).to_list(length=None)

    # DTE filter: keep only spot trades whose entry session is the selected number
    # of trading days before the nearest expiry. Metadata-driven via stored
    # expiry_date values; "all"/None keeps every trade. Applied before pairing so
    # downstream indices, expiry resolution and option fetch all stay consistent.
    dte_target = normalize_dte_filter(config.dte_filter)
    dte_stats = {"filter": config.dte_filter, "input_trades": len(spot_trades)}
    if dte_target is not None:
        expiry_dates_sorted = sorted({
            str(c.get("expiry_date")) for c in contracts if c.get("expiry_date")
        })
        kept: List[Dict[str, Any]] = []
        for trade in spot_trades:
            entry_ts = trade.get("entry_ts")
            if entry_ts is None:
                continue
            trade_date = _ts_ms_to_ist_date_str(int(entry_ts))
            if compute_dte(trade_date, expiry_dates_sorted) in dte_target:
                kept.append(trade)
        spot_trades = kept
        dte_stats["matched_trades"] = len(spot_trades)

    expiry_by_trade = _resolve_option_expiry_by_trade(spot_trades, contracts, fixed_expiry_date=fixed_expiry_date)

    selected_keys: set[str] = set()
    for idx, trade in enumerate(spot_trades):
        resolved_expiry = fixed_expiry_date or expiry_by_trade.get(idx)
        if not resolved_expiry:
            # No upcoming expiry resolved for this trade — do NOT fall back to the
            # whole contract universe (that silently picks the oldest expiry). Skip;
            # simulate() will mark it MISSING_CONTRACT with a clear reason.
            continue
        eligible_contracts = [
            contract
            for contract in contracts
            if str(contract.get("expiry_date", "")) == str(resolved_expiry)
        ]
        try:
            selected = select_contract_for_signal(
                contracts=eligible_contracts,
                underlying=underlying,
                spot_price=float(trade.get("entry_price", 0.0)),
                direction=str(trade.get("direction", "")).upper(),
                moneyness=config.moneyness,
            )
        except Exception:
            selected = None
        if selected and selected.get("instrument_key"):
            selected_keys.add(str(selected["instrument_key"]))

    candle_rows: List[Dict[str, Any]] = []
    auto_fetch: Dict[str, Any] = {
        "attempted": False,
        "status": "skipped",
        "reason": "disabled" if not config.auto_fetch else "local_data_available",
        "keys_fetched": 0,
        "candles_added": 0,
        "candles_updated": 0,
        "failed": [],
    }
    if selected_keys:
        candle_query: Dict[str, Any] = {"instrument_key": {"$in": sorted(selected_keys)}}
        trade_ts = [
            int(ts)
            for trade in spot_trades
            for ts in (trade.get("entry_ts"), trade.get("exit_ts"))
            if ts is not None
        ]
        if trade_ts:
            candle_query["ts"] = {
                "$gte": min(trade_ts) - max(0, int(config.entry_max_age_sec)) * 1000,
                "$lte": max(trade_ts),
            }
        candle_rows = await db.options_1m.find(candle_query, {"_id": 0}).sort("ts", 1).to_list(length=1000000)

        present_keys = {str(row.get("instrument_key")) for row in candle_rows}
        missing_keys = sorted(selected_keys - present_keys)
        if config.auto_fetch and missing_keys:
            if len(missing_keys) > max(0, int(config.max_auto_fetch_contracts)):
                auto_fetch.update({
                    "attempted": False,
                    "status": "skipped",
                    "reason": "too_many_contracts",
                    "missing_keys": len(missing_keys),
                })
            elif req.start_ts is not None and req.end_ts is not None:
                auto_fetch.update({"attempted": True, "status": "running", "reason": "missing_local_candles"})
                from_date = _ts_ms_to_ist_date_str(req.start_ts)
                to_date = _ts_ms_to_ist_date_str(req.end_ts)
                contract_by_key = {str(c.get("instrument_key")): c for c in contracts}
                fetched_rows: List[Dict[str, Any]] = []
                for key in missing_keys:
                    try:
                        fetch_result = await upstox_client.fetch_historical_1m_for_key_chunked(
                            key,
                            from_date,
                            to_date,
                            max_days_per_call=7,
                            contract=contract_by_key.get(key, {}),
                        )
                        df = fetch_result["df"]
                        saved = await persist_option_candles_df(db, df)
                        auto_fetch["keys_fetched"] += 1
                        auto_fetch["candles_added"] += saved["candles_added"]
                        auto_fetch["candles_updated"] += saved["candles_updated"]
                        if not df.empty:
                            fetched_rows.extend(df.to_dict(orient="records"))
                        if fetch_result["failed_chunks"]:
                            auto_fetch["failed"].append({"instrument_key": key, "chunks": fetch_result["failed_chunks"]})
                    except Exception as e:
                        auto_fetch["failed"].append({"instrument_key": key, "error": str(e)[:300]})
                candle_rows.extend(fetched_rows)
                auto_fetch["status"] = "partial" if auto_fetch["failed"] else "ok"
            else:
                auto_fetch.update({"status": "skipped", "reason": "missing_backtest_window"})

    result = simulate_paired_option_trades(
        spot_trades=spot_trades,
        contracts=contracts,
        option_candles=pd.DataFrame(candle_rows),
        underlying=underlying,
        moneyness=config.moneyness,
        lots=config.lots,
        entry_max_age_sec=config.entry_max_age_sec,
        exit_max_age_sec=config.exit_max_age_sec,
        expiry_by_trade=expiry_by_trade,
        fixed_expiry_date=fixed_expiry_date,
        slippage_config=config.slippage_config,
        exit_mode=config.exit_mode,
        option_target_pts=config.option_target_pts,
        option_stop_pts=config.option_stop_pts,
        option_target_pct=config.option_target_pct,
        option_stop_pct=config.option_stop_pct,
        cost_config=config.cost_config,
        sizing_config=config.sizing_config,
    )
    result["request"] = config.model_dump()
    resolved_expiries = sorted(set(expiry_by_trade.values()))
    result["data"] = {
        "expiry_date": fixed_expiry_date,
        "expiry_mode": "fixed" if fixed_expiry_date else "per_trade_next_available",
        "resolved_expiries": resolved_expiries,
        "trades_without_expiry": max(0, len(spot_trades) - len(expiry_by_trade)),
        "contracts_loaded": len(contracts),
        "instrument_keys_needed": len(selected_keys),
        "candles_loaded": len(candle_rows),
        "source": "options_1m",
        "auto_fetch": auto_fetch,
        "dte_filter": dte_stats,
    }
    return result


async def _option_preflight_report(req: BacktestReq) -> Dict[str, Any]:
    """Run the strategy + resolve the option contracts it would need, then report
    how many trades WOULD pair against stored option data — without running the
    full option simulation or persisting anything.

    This is the pre-run data-availability check: it tells the user, before they
    trust a backtest, whether the option warehouse actually covers the signals.
    """
    registry = get_registry()
    strategy = registry.get(req.strategy_id)
    if not strategy:
        raise HTTPException(404, f"Strategy {req.strategy_id} not found")
    config = req.option_backtest
    if not config.enabled:
        return {"enabled": False, "note": "Option execution is off — nothing to check."}

    underlying = req.instrument.upper()
    df = await load_candles_df(underlying, req.start_ts, req.end_ts)
    if df.empty or len(df) < 50:
        raise HTTPException(400, f"Insufficient spot candles for {underlying} in the window.")
    params = strategy.merged_params(req.params)
    df_enriched = precompute_all_indicators(df, params)
    df_enriched["regime"] = classify_regime_series(df_enriched)
    res = run_backtest(
        df_enriched, strategy, params, instrument=underlying,
        costs_enabled=req.costs_enabled, pretrade_filters=req.pretrade_filters,
        trade_window_start=req.trade_window_start, trade_window_end=req.trade_window_end,
    )
    spot_trades = res["trades"]
    db = get_db()

    # Windowed contract load (same correctness rules as the real run).
    contract_query: Dict[str, Any] = {"underlying": underlying}
    fixed_expiry_date = config.expiry_date
    if fixed_expiry_date:
        contract_query["expiry_date"] = fixed_expiry_date
    elif spot_trades:
        _ts = [int(t["entry_ts"]) for t in spot_trades if t.get("entry_ts") is not None]
        _xt = [int(t["exit_ts"]) for t in spot_trades if t.get("exit_ts") is not None]
        if _ts:
            win_start = _ts_ms_to_ist_date_str(min(_ts))
            last_ms = max(_xt) if _xt else max(_ts)
            win_end = _ts_ms_to_ist_date_str(last_ms + 21 * 24 * 3600 * 1000)
            contract_query["expiry_date"] = {"$gte": win_start, "$lte": win_end}
    contracts = await db.option_contracts.find(contract_query, {"_id": 0}).sort([
        ("expiry_date", 1), ("strike", 1), ("side", 1),
    ]).to_list(length=None)

    # DTE filter (same as the run).
    dte_target = normalize_dte_filter(config.dte_filter)
    if dte_target is not None:
        exp_sorted = sorted({str(c.get("expiry_date")) for c in contracts if c.get("expiry_date")})
        spot_trades = [t for t in spot_trades if t.get("entry_ts") is not None
                       and compute_dte(_ts_ms_to_ist_date_str(int(t["entry_ts"])), exp_sorted) in dte_target]

    expiry_by_trade = _resolve_option_expiry_by_trade(spot_trades, contracts, fixed_expiry_date=fixed_expiry_date)

    # Resolve the needed contract per trade.
    needed: Dict[str, Dict[str, Any]] = {}
    no_contract = 0
    per_trade = []
    for idx, trade in enumerate(spot_trades):
        rexp = fixed_expiry_date or expiry_by_trade.get(idx)
        if not rexp:
            no_contract += 1
            per_trade.append({"idx": idx, "status": "no_expiry"})
            continue
        elig = [c for c in contracts if str(c.get("expiry_date", "")) == str(rexp)]
        try:
            sel = select_contract_for_signal(
                contracts=elig, underlying=underlying,
                spot_price=float(trade.get("entry_price", 0.0)),
                direction=str(trade.get("direction", "")).upper(), moneyness=config.moneyness,
            )
        except Exception:
            sel = None
        if not sel or not sel.get("instrument_key"):
            no_contract += 1
            per_trade.append({"idx": idx, "status": "no_contract", "expiry": rexp})
            continue
        key = str(sel["instrument_key"])
        needed.setdefault(key, {"key": key, "ts": []})
        needed[key]["ts"].append((int(trade.get("entry_ts", 0)), int(trade.get("exit_ts") or trade.get("entry_ts") or 0)))
        per_trade.append({"idx": idx, "status": "needs_candle", "key": key, "strike": sel.get("strike"), "side": sel.get("side"), "expiry": rexp})

    # Check candle presence for the needed keys.
    would_pair = 0
    missing_candle = 0
    entry_age_ms = max(0, int(config.entry_max_age_sec or 0)) * 1000
    key_ts_index: Dict[str, list] = {}
    if needed:
        all_ts = [t for v in needed.values() for pair in v["ts"] for t in pair]
        rows = await db.options_1m.find(
            {"instrument_key": {"$in": list(needed.keys())},
             "ts": {"$gte": min(all_ts) - entry_age_ms, "$lte": max(all_ts)}},
            {"_id": 0, "instrument_key": 1, "ts": 1},
        ).sort("ts", 1).to_list(length=2000000)
        for r in rows:
            key_ts_index.setdefault(str(r["instrument_key"]), []).append(int(r["ts"]))
    import bisect
    missing_keys_set = set()
    for pt in per_trade:
        if pt["status"] != "needs_candle":
            continue
        key = pt["key"]
        ts_list = key_ts_index.get(key, [])
        entry_ts = int(spot_trades[pt["idx"]].get("entry_ts", 0))
        ok = False
        if ts_list:
            pos = bisect.bisect_right(ts_list, entry_ts) - 1
            if pos >= 0 and (entry_ts - ts_list[pos]) <= entry_age_ms:
                ok = True
        if ok:
            would_pair += 1
        else:
            missing_candle += 1
            missing_keys_set.add(key)

    total = len(spot_trades)
    pct = round(would_pair / total * 100, 1) if total else 0.0
    return {
        "enabled": True,
        "instrument": underlying,
        "total_spot_trades": total,
        "would_pair": would_pair,
        "missing_contract": no_contract,
        "missing_candle": missing_candle,
        "coverage_pct": pct,
        "missing_contract_keys": sorted(missing_keys_set)[:50],
        "expiry_mode": "fixed" if fixed_expiry_date else "per_trade_nearest",
        "window": {
            "from": _ts_ms_to_ist_date_str(req.start_ts) if req.start_ts else None,
            "to": _ts_ms_to_ist_date_str(req.end_ts) if req.end_ts else None,
        },
        "moneyness": config.moneyness,
        "dte_filter": config.dte_filter,
    }


@api.post("/backtest/option-preflight")
async def backtest_option_preflight(req: BacktestReq, ingest_missing: bool = Query(False)):
    """Pre-run check: does the option warehouse cover the signals this config
    would generate? Returns a would-pair coverage report. With ingest_missing=1
    and Upstox connected, also submits a background fetch of the option data for
    the window (ATM/ITM1/OTM1) so the gaps are filled before the real run."""
    report = await _option_preflight_report(req)
    if not report.get("enabled"):
        return serialize_doc(report)

    if ingest_missing and (report["missing_candle"] > 0 or report["missing_contract"] > 0):
        status = await upstox_client.get_connection_status()
        if not status.get("connected") or status.get("expired"):
            report["ingest"] = {"status": "skipped", "reason": "upstox_not_connected"}
        elif not (req.start_ts and req.end_ts):
            report["ingest"] = {"status": "skipped", "reason": "no_window"}
        else:
            # Fetch ATM + ITM1 + OTM1 both legs over the window via the standard
            # option warehouse fetch job (missing-only, so re-running is cheap).
            plan_req = OptionWarehousePlanReq(
                underlying=report["instrument"],
                from_date=_ts_ms_to_ist_date_str(req.start_ts),
                to_date=_ts_ms_to_ist_date_str(req.end_ts),
                expiry_policy="next_available",
                moneyness=["atm", "itm1", "otm1"],
                legs=["CE", "PE"],
                sample_interval_minutes=1,
                max_contracts=2000,
                fetch_missing_only=True,
            )
            try:
                preview = await _build_option_warehouse_preview(plan_req)
                chunk_days = int(preview.get("chunk_guidance", {}).get("chunk_days") or 5)
                run_id = str(_uuid.uuid4())
                ts = datetime.now(timezone.utc).isoformat()
                await get_db().warehouse_runs.insert_one({
                    "id": run_id, "instrument": report["instrument"],
                    "source": "backtest_preflight", "kind": "option_candles",
                    "started_at": ts, "updated_at": ts, "status": "queued",
                    "from_date": plan_req.from_date, "to_date": plan_req.to_date,
                    "moneyness": plan_req.moneyness, "legs": plan_req.legs,
                    "chunk_days": chunk_days, "progress_pct": 0,
                })
                asyncio.create_task(run_option_warehouse_fetch_job(
                    run_id, preview, fetch_missing_only=True, chunk_days=chunk_days,
                ))
                report["ingest"] = {"status": "started", "run_id": run_id,
                                    "to_fetch": int(preview.get("summary", {}).get("missing_data_contracts", 0))}
            except Exception as e:
                report["ingest"] = {"status": "error", "error": str(e)[:300]}
    return serialize_doc(report)


@api.post("/backtest/run")
async def backtest_run(req: BacktestReq):
    registry = get_registry()
    strategy = registry.get(req.strategy_id)
    if not strategy:
        raise HTTPException(404, f"Strategy {req.strategy_id} not found")

    data_audit = await _audit_and_fill_backtest_data(req)
    df = await load_candles_df(req.instrument.upper(), req.start_ts, req.end_ts)
    if df.empty or len(df) < 50:
        audit_msg = ""
        if data_audit:
            after = data_audit.get("after", {})
            fill = data_audit.get("fill", {})
            audit_msg = (
                f" Audit: {after.get('complete_days', 0)}/{after.get('expected_days', 0)} complete days; "
                f"fill {fill.get('status')} ({fill.get('reason') or fill.get('source', 'unknown')})."
            )
        raise HTTPException(
            400,
            f"Insufficient candles for {req.instrument}. Ingest data first via /api/warehouse/ingest.{audit_msg}"
        )

    # Merge default + user params (strict allow-list)
    params = strategy.merged_params(req.params)

    # Compute indicators + regime
    df_enriched = precompute_all_indicators(df, params)
    df_enriched["regime"] = classify_regime_series(df_enriched)

    # Backtest
    res = run_backtest(
        df_enriched,
        strategy,
        params,
        instrument=req.instrument.upper(),
        costs_enabled=req.costs_enabled,
        pretrade_filters=req.pretrade_filters,
        trade_window_start=req.trade_window_start,
        trade_window_end=req.trade_window_end,
    )
    metrics = res["metrics"]
    option_result = await _run_paired_option_backtest(req, res["trades"])

    wf = None
    if req.walkforward and len(df_enriched) >= 200:
        wf = walk_forward(
            df_enriched,
            strategy,
            params,
            instrument=req.instrument.upper(),
            costs_enabled=req.costs_enabled,
            pretrade_filters=req.pretrade_filters,
            train_pct=req.train_pct,
            n_folds=req.n_folds,
            trade_window_start=req.trade_window_start,
            trade_window_end=req.trade_window_end,
        )

    sig = stat_significance(metrics["trade_count"], metrics["win_rate"], metrics.get("profit_factor"))
    regime_dist = df_enriched["regime"].value_counts().to_dict()
    regime_dist = {str(k): int(v) for k, v in regime_dist.items()}

    result_doc = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "name": req.name,
        "config": req.model_dump(),
        "params_applied": params,
        "metrics": metrics,
        "trades": res["trades"],
        "equity_curve": res["equity_curve"],
        "walkforward": wf,
        "significance": sig,
        "candle_count": int(len(df_enriched)),
        "regime_distribution": regime_dist,
        "signal_funnel": res["signal_funnel"],
        "instrument": req.instrument.upper(),
        "strategy_id": req.strategy_id,
        "data_audit": data_audit,
        "option_backtest": option_result,
    }
    db = get_db()
    await db.backtest_runs.insert_one(result_doc)
    return serialize_doc(result_doc)


@api.get("/backtest/runs")
async def list_backtest_runs(limit: int = Query(50, le=200)):
    db = get_db()
    cur = db.backtest_runs.find({}, {"_id": 0, "trades": 0, "equity_curve": 0, "walkforward": 0}).sort("created_at", -1).limit(limit)
    rows = await cur.to_list(length=limit)
    return {"items": rows}


@api.get("/backtest/runs/{run_id}")
async def get_backtest_run(run_id: str):
    db = get_db()
    doc = await db.backtest_runs.find_one({"id": run_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Run not found")
    return serialize_doc(doc)


@api.delete("/backtest/runs/{run_id}")
async def delete_backtest_run(run_id: str):
    db = get_db()
    res = await db.backtest_runs.delete_one({"id": run_id})
    return {"deleted": res.deleted_count}


# ---------------------------------------------------------------------------
# Presets (named backtest configs)
# ---------------------------------------------------------------------------

class PresetSaveBody(BaseModel):
    name: str
    config: Dict[str, Any]


@api.get("/presets")
async def list_presets():
    db = get_db()
    cur = db.presets.find({}, {"_id": 0}).sort("name", 1)
    rows = await cur.to_list(length=200)
    return {"items": rows}


@api.put("/presets/{name}")
async def save_preset(name: str, body: PresetSaveBody):
    db = get_db()
    await db.presets.update_one(
        {"name": name},
        {"$set": {
            "name": name,
            "config": body.config,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    doc = await db.presets.find_one({"name": name}, {"_id": 0})
    return doc


@api.delete("/presets/{name}")
async def delete_preset(name: str):
    db = get_db()
    res = await db.presets.delete_one({"name": name})
    return {"deleted": res.deleted_count}


@api.post("/presets/{name}/rename")
async def rename_preset(name: str, new_name: str = Query(...)):
    """Rename a saved preset, preserving its config (params + execution policy).
    Rejects an empty or already-taken target name."""
    target = (new_name or "").strip()
    if not target:
        raise HTTPException(400, "new_name is required")
    if target == name:
        return {"ok": True, "name": name}
    db = get_db()
    doc = await db.presets.find_one({"name": name}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Preset '{name}' not found")
    if await db.presets.find_one({"name": target}, {"_id": 0}):
        raise HTTPException(409, f"A preset named '{target}' already exists")
    doc["name"] = target
    doc["saved_at"] = datetime.now(timezone.utc).isoformat()
    await db.presets.insert_one(doc)
    await db.presets.delete_one({"name": name})
    return {"ok": True, "name": target}


# ---------------------------------------------------------------------------
# Dashboard summary
# ---------------------------------------------------------------------------

@api.get("/dashboard/summary")
async def dashboard_summary():
    db = get_db()
    cov = await get_coverage()
    instrument_count = len(cov)
    candle_total = sum(c["candle_count"] for c in cov.values())
    bt_count = await db.backtest_runs.count_documents({})
    strategies = get_registry().list_all()
    # Latest backtest summary
    latest = await db.backtest_runs.find_one({}, {"_id": 0, "trades": 0, "equity_curve": 0, "walkforward": 0}, sort=[("created_at", -1)])
    return {
        "warehouse": {
            "instruments_tracked": instrument_count,
            "total_candles": candle_total,
            "by_instrument": {k: v["candle_count"] for k, v in cov.items()},
        },
        "strategies_loaded": len([s for s in strategies if s.get("is_loaded", True)]),
        "strategies_failed": len([s for s in strategies if not s.get("is_loaded", True)]),
        "backtest_runs": bt_count,
        "latest_backtest": serialize_doc(latest) if latest else None,
    }


@api.get("/market/header")
async def market_header_snapshot():
    """Read the persistent terminal header quote snapshot."""
    return await build_market_header_snapshot(latest_ticks=upstox_stream_manager.latest_tick_map())


class VolatilityAuditReq(BaseModel):
    instrument: str = "NIFTY"
    from_date: str
    to_date: str
    spike_threshold: float = 2.5
    realized_window: int = 5
    baseline_lookback_bars: int = 11250


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


@api.get("/market/header/stream")
async def market_header_sse(request: Request):
    """Server-Sent Events feed of market header snapshots.

    Pushes a snapshot:
      - Immediately on connect
      - Whenever any subscribed Upstox WS tick arrives (debounced to ~10/s max per client)
      - As a heartbeat every 15s if no tick fires (so proxies do not close the connection)
    Falls back to client-side polling if SSE is unsupported or the WS stream is offline.
    """

    async def event_source():
        queue = upstox_stream_manager.subscribe(max_queue=128)
        try:
            # Initial snapshot so the UI paints immediately.
            snapshot = await build_market_header_snapshot(
                latest_ticks=upstox_stream_manager.latest_tick_map()
            )
            yield f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"

            # Debounce: at most one snapshot per `min_interval_s` to avoid hammering the client
            # when 10+ instruments tick simultaneously.
            min_interval_s = 0.1
            last_emit = asyncio.get_event_loop().time()

            while True:
                if await request.is_disconnected():
                    break
                # Wait for a tick or heartbeat timeout (15s).
                try:
                    await asyncio.wait_for(queue.get(), timeout=15.0)
                    # Drain any other ticks queued during the same instant.
                    drained = 0
                    while drained < 32:
                        try:
                            queue.get_nowait()
                            drained += 1
                        except asyncio.QueueEmpty:
                            break
                except asyncio.TimeoutError:
                    # Heartbeat keeps proxies/load-balancers from closing the connection.
                    yield ": heartbeat\n\n"
                    continue

                now = asyncio.get_event_loop().time()
                wait = (last_emit + min_interval_s) - now
                if wait > 0:
                    await asyncio.sleep(wait)
                snapshot = await build_market_header_snapshot(
                    latest_ticks=upstox_stream_manager.latest_tick_map()
                )
                yield f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"
                last_emit = asyncio.get_event_loop().time()
        finally:
            upstox_stream_manager.unsubscribe(queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering for instant push
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Live signal lifecycle + paper trading foundation
# ---------------------------------------------------------------------------

@api.get("/signals")
async def list_signals(state: Optional[str] = Query(None), limit: int = Query(50, le=200)):
    q: Dict[str, Any] = {}
    if state:
        q["state"] = state.upper()
    rows = await get_db().signals.find(q, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(length=limit)
    return {"items": serialize_doc(rows), "count": len(rows)}



def _ist_day_bounds_ms_full(date_from: Optional[str], date_to: Optional[str]) -> tuple:
    """Full-day IST bounds in epoch-ms for inclusive YYYY-MM-DD date filters."""
    ist = timezone(timedelta(hours=5, minutes=30))
    start_ms = end_ms = None
    if date_from:
        start_ms = int(datetime.fromisoformat(f"{date_from}T00:00:00").replace(tzinfo=ist).timestamp() * 1000)
    if date_to:
        end_ms = int((datetime.fromisoformat(f"{date_to}T00:00:00").replace(tzinfo=ist) + timedelta(days=1)).timestamp() * 1000)
    return start_ms, end_ms


def _csv_response(rows: List[Dict[str, Any]], columns: List[str], filename: str):
    import csv as _csv
    import io as _io
    from fastapi.responses import Response
    buf = _io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        writer.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


_ENRICHED_SORT_FIELDS = {"bar_ts", "updated_at", "confidence", "instrument", "state"}

_ENRICHED_CSV_COLUMNS = [
    "bar_ist", "deployment_name", "strategy_id", "instrument", "direction", "state",
    "score", "contract", "spot_entry", "entry_premium", "exit_premium", "exit_reason",
    "pnl_value", "pnl_premium_pts", "lots", "quantity", "reasons", "blockers",
]


@api.get("/signals/enriched")
async def list_signals_enriched(
    deployment_id: Optional[str] = Query(None),
    strategy_id: Optional[str] = Query(None),
    instrument: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    clean: Optional[bool] = Query(None, description="true = clean only, false = blocked only"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD (IST)"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD (IST)"),
    sort: str = Query("-bar_ts", description="bar_ts | updated_at | confidence | instrument | state, prefix - for desc"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=500),
    format: Optional[str] = Query(None, description="csv to download"),
):
    """The trade-recommendation ledger: deployment signals JOINED with their
    paper trades (entry premium, exit price/reason, P&L in rupees and premium
    points) plus the strategy's entry-trigger reasons. Server-side filter /
    sort / pagination / CSV. Manual research signals (no deployment_id) are
    excluded by design."""
    db = get_db()
    q: Dict[str, Any] = {"deployment_id": {"$exists": True, "$type": "string"}}
    if deployment_id:
        q["deployment_id"] = deployment_id
    if strategy_id:
        q["strategy_id"] = strategy_id
    if instrument:
        q["instrument"] = instrument.upper()
    if state:
        q["state"] = state.upper()
    if clean is True:
        q["blocked"] = {"$ne": True}
    elif clean is False:
        q["blocked"] = True
    start_ms, end_ms = _ist_day_bounds_ms_full(date_from, date_to)
    if start_ms is not None or end_ms is not None:
        rng: Dict[str, Any] = {}
        if start_ms is not None:
            rng["$gte"] = start_ms
        if end_ms is not None:
            rng["$lt"] = end_ms
        q["bar_ts"] = rng

    field = sort.lstrip("-")
    direction = -1 if sort.startswith("-") else 1
    if field not in _ENRICHED_SORT_FIELDS:
        field, direction = "bar_ts", -1

    total = await db.signals.count_documents(q)
    rows = await db.signals.find(q, {"_id": 0}).sort(field, direction).skip(skip).limit(limit).to_list(length=limit)

    trade_ids = [str(r.get("paper_trade_id")) for r in rows if r.get("paper_trade_id")]
    trades_by_id: Dict[str, Dict[str, Any]] = {}
    if trade_ids:
        for t in await db.paper_trades.find({"id": {"$in": trade_ids}}, {"_id": 0, "events": 0}).to_list(length=len(trade_ids)):
            trades_by_id[str(t.get("id"))] = t
    dep_ids = sorted({str(r.get("deployment_id")) for r in rows if r.get("deployment_id")})
    dep_names: Dict[str, str] = {}
    if dep_ids:
        for d in await db.strategy_deployments.find({"id": {"$in": dep_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(length=len(dep_ids)):
            dep_names[str(d.get("id"))] = str(d.get("name") or "")

    items: List[Dict[str, Any]] = []
    for s in rows:
        t = trades_by_id.get(str(s.get("paper_trade_id") or ""))
        contract = s.get("option_contract") or {}
        closed = bool(t and t.get("status") == "CLOSED")
        qty = int((t or {}).get("quantity") or 0)
        entry_premium = (t or {}).get("entry_price")
        exit_premium = (t or {}).get("exit_price") if closed else None
        pnl_value = (t or {}).get("realized_pnl") if closed else ((t or {}).get("unrealized_pnl") if t else None)
        pnl_pts = round(float(pnl_value) / qty, 2) if (pnl_value is not None and qty) else None
        items.append({
            **{k: s.get(k) for k in (
                "id", "deployment_id", "strategy_id", "instrument", "direction", "state",
                "bar_ts", "decision_ts", "updated_at", "blocked", "blockers", "reasons",
                "risk_hints", "paper_trade_id", "paper_trade_error", "tracked_for_pnl",
            )},
            "score": s.get("confidence"),
            "spot_entry": s.get("entry_price"),
            "bar_ist": ((s.get("context") or {}).get("candle") or {}).get("ist_time") or _ts_ms_to_ist_date_str(int(s.get("bar_ts") or 0)),
            "deployment_name": dep_names.get(str(s.get("deployment_id") or ""), ""),
            "contract": (str(contract.get("strike") or "") + " " + str(contract.get("side") or "")).strip(),
            "contract_expiry": contract.get("expiry_date"),
            "trade_status": (t or {}).get("status"),
            "entry_premium": entry_premium,
            "exit_premium": exit_premium,
            "exit_reason": (t or {}).get("exit_reason"),
            "closed_at": (t or {}).get("closed_at"),
            "lots": (t or {}).get("lots"),
            "quantity": qty or None,
            "pnl_value": pnl_value,
            "pnl_premium_pts": pnl_pts,
        })

    if (format or "").lower() == "csv":
        flat = [{**i, "reasons": "; ".join(i.get("reasons") or []), "blockers": "; ".join(i.get("blockers") or [])} for i in items]
        return _csv_response(flat, _ENRICHED_CSV_COLUMNS, "signals_enriched.csv")
    return {"items": serialize_doc(items), "count": len(items), "total": total, "skip": skip, "limit": limit}


class SignalsPurgeReq(BaseModel):
    ids: Optional[List[str]] = None
    deployment_id: Optional[str] = None
    older_than_days: Optional[int] = None
    states: Optional[List[str]] = None


@api.post("/signals/purge")
async def purge_signals(req: SignalsPurgeReq):
    """Delete journaled signals. Requires at least one criterion (ids,
    deployment_id, or older_than_days); criteria AND together. Returns the
    deleted count. Paper trades are never touched by this route."""
    if not (req.ids or req.deployment_id or req.older_than_days):
        raise HTTPException(400, "Provide ids, deployment_id, or older_than_days")
    q: Dict[str, Any] = {}
    if req.ids:
        q["id"] = {"$in": [str(i) for i in req.ids]}
    if req.deployment_id:
        q["deployment_id"] = req.deployment_id
    if req.older_than_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(req.older_than_days))).isoformat()
        q["updated_at"] = {"$lt": cutoff}
    if req.states:
        q["state"] = {"$in": [str(s).upper() for s in req.states]}
    res = await get_db().signals.delete_many(q)
    return {"deleted": int(res.deleted_count)}


@api.get("/deployments")
async def list_deployments(status: Optional[str] = Query(None), limit: int = Query(50, le=200)):
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status.upper()
    rows = await get_db().strategy_deployments.find(q, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(length=limit)
    return {"items": serialize_doc(rows), "count": len(rows)}


async def _load_deployment_source(db: Any, source_type: str, source_id: str) -> Dict[str, Any]:
    source_type = str(source_type or "").lower()
    if source_type == "preset":
        doc = await db.presets.find_one({"name": source_id}, {"_id": 0})
    elif source_type == "backtest_run":
        doc = await db.backtest_runs.find_one({"id": source_id}, {"_id": 0, "trades": 0, "equity_curve": 0})
    else:
        raise HTTPException(400, "Deployment source_type must be preset or backtest_run")
    if not doc:
        raise HTTPException(404, f"Deployment source not found: {source_type} {source_id}")
    return doc


@api.post("/deployments")
async def create_deployment(req: DeploymentCreateReq):
    db = get_db()
    source = await _load_deployment_source(db, req.source_type, req.source_id)
    # Quality gate (slice 9): warn but never silently allow problematic backtests.
    # If any warning is present, the user must acknowledge by setting
    # acknowledged_warnings=true in the create request.
    quality = evaluate_source_quality(source)
    if quality["acknowledgment_required"] and not req.acknowledged_warnings:
        warning_summary = "; ".join(w["label"] for w in quality["warnings"])
        raise HTTPException(
            400,
            detail={
                "code": "acknowledgment_required",
                "message": (
                    f"Deployment source has {len(quality['warnings'])} quality warning(s): "
                    f"{warning_summary}. Re-submit with acknowledged_warnings=true to proceed."
                ),
                "quality": quality,
            },
        )
    # Pin the strategy source-file SHA at creation time so the evaluator can
    # later detect drift if the user edits the .py file without re-deploying.
    strategy_id = str(source.get("strategy_id") or (source.get("config") or {}).get("strategy_id") or "")
    strategy_obj = get_registry().get(strategy_id) if strategy_id else None
    pinned_source_sha = hash_strategy_source(strategy_obj) if strategy_obj else None
    # Merge explicit kill-switch fields into the risk dict (only when provided).
    kill_switch_cfg = {
        k: v for k, v in {
            "max_consecutive_losses": req.max_consecutive_losses,
            "daily_loss_cutoff_pct": req.daily_loss_cutoff_pct,
            "max_open_paper_trades": req.max_open_paper_trades,
        }.items() if v is not None
    }
    try:
        doc = build_deployment_doc(
            source_type=req.source_type,
            source_doc=source,
            name=req.name,
            mode=req.mode,
            confirmation_mode=req.confirmation_mode,
            option_moneyness=req.option_moneyness,
            pretrade_profile=req.pretrade_profile,
            risk={
                **(req.risk or {}),
                **kill_switch_cfg,
                "default_lots": int(req.default_lots or 1),
                "auto_paper": bool(req.auto_paper),
                **({"auto_paper_target_pts": float(req.auto_paper_target_pts)}
                   if req.auto_paper_target_pts is not None else {}),
                **({"auto_paper_stop_pts": float(req.auto_paper_stop_pts)}
                   if req.auto_paper_stop_pts is not None else {}),
                **({"auto_paper_target_pct": float(req.auto_paper_target_pct)}
                   if req.auto_paper_target_pct is not None else {}),
                **({"auto_paper_stop_pct": float(req.auto_paper_stop_pct)}
                   if req.auto_paper_stop_pct is not None else {}),
            },
            dte_filter=req.dte_filter,
            allow_overnight=req.allow_overnight,
            strategy_source_sha=pinned_source_sha,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Record the quality snapshot + acknowledgment on the deployment for full audit
    doc["quality_at_creation"] = quality
    doc["acknowledged_warnings"] = bool(req.acknowledged_warnings) if quality["acknowledgment_required"] else None
    await db.strategy_deployments.insert_one(doc)
    # Best-effort: re-align the live option subscription with the now-ACTIVE
    # deployments so paper trades have premiums to fill/mark against.
    stream = await _auto_follow_option_stream()
    return serialize_doc({**doc, "option_stream": stream})


async def _auto_follow_option_stream(min_radius: int = 0) -> Dict[str, Any]:
    """Align the live option subscription with ACTIVE paper deployments — and,
    when `min_radius` > 0, keep a baseline ATM-centered universe subscribed even
    with no deployments (so the option-chain snapshot and paper marks always have
    fresh premiums during market hours).

    Derives the needed strike radius from the deployments' moneyness policies
    (`radius_for_deployments`), floored at `min_radius`, and restarts the
    read-only stream with the refreshed universe. Upstox captures subscriptions
    at connect time, so a restart is the only way to widen coverage.

    Idempotent: when the live subscription already covers every desired option
    key, it does NOT restart (a restart briefly drops the WS). Best-effort by
    design: only acts when Upstox is connected and the stream is already running;
    any failure is reported, never raised — a deployment must never fail to
    create because the stream couldn't restart.
    """
    try:
        status = await upstox_client.get_connection_status()
        if not status.get("connected") or status.get("expired"):
            return {"restarted": False, "reason": "upstox_not_connected"}
        if not upstox_stream_manager.status().get("running"):
            return {"restarted": False, "reason": "stream_not_running"}
        db = get_db()
        deployments = await db.strategy_deployments.find(
            {"status": "ACTIVE", "mode": "paper"}, {"_id": 0, "option_policy": 1}
        ).to_list(length=None)
        radius = max(
            radius_for_deployments(deployments) if deployments else 0,
            int(min_radius or 0),
        )
        if radius <= 0:
            return {"restarted": False, "reason": "no_active_paper_deployments"}
        universe = await build_live_option_universe(
            db,
            latest_ticks=upstox_stream_manager.latest_tick_map(),
            radius=radius,
        )
        option_keys = universe.get("instrument_keys") or []
        if not option_keys:
            return {"restarted": False, "reason": "no_option_keys", "radius": radius}
        # Idempotent: skip the (disruptive) restart when every desired option key
        # is already subscribed. As the ATM band drifts intraday, missing keys
        # trigger a re-center restart automatically.
        current_keys = set(upstox_stream_manager.status().get("instrument_keys") or [])
        if set(option_keys).issubset(current_keys):
            return {"restarted": False, "reason": "already_covered", "radius": radius, "option_keys": len(option_keys)}
        stream_keys = list(dict.fromkeys([*_default_stream_instrument_keys(), *option_keys]))
        await upstox_stream_manager.stop()
        await upstox_stream_manager.start(
            instrument_keys=stream_keys, mode=DEFAULT_STREAM_MODE, persist=True,
        )
        log.info("option stream auto-follow: restarted with %d keys (radius=%d)", len(stream_keys), radius)
        return {"restarted": True, "radius": radius, "option_keys": len(option_keys)}
    except Exception as exc:  # never block deployment lifecycle on stream issues
        log.exception("option stream auto-follow failed")
        return {"restarted": False, "reason": f"error: {exc}"}


@api.get("/deployments/preflight")
async def deployment_preflight_route(
    instrument: str = Query(..., description="Instrument key e.g. NIFTY, BANKNIFTY, SENSEX"),
    lookback_days: int = Query(30, ge=1, le=365),
    lookahead_expiries: int = Query(4, ge=1, le=20),
):
    """Data-realism pre-flight report for a deployment. Informational only - never blocks creation."""
    report = await compute_data_realism(
        get_db(),
        instrument,
        lookback_days=lookback_days,
        lookahead_expiries=lookahead_expiries,
    )
    return serialize_doc(report)


@api.get("/deployments/quality")
async def deployment_quality_route(
    source_type: str = Query(..., description="preset or backtest_run"),
    source_id: str = Query(..., description="preset name or backtest run id"),
):
    """Quality / acknowledgment check for a deployment source.

    Returns warnings (overfit, low trade count, weak Sharpe, missing walkforward,
    large drawdown). Never blocks creation by itself - the user must pass
    `acknowledged_warnings=true` on the create request when warnings are present.
    """
    source = await _load_deployment_source(get_db(), source_type, source_id)
    return serialize_doc(evaluate_source_quality(source))


@api.get("/deployments/readiness")
async def deployment_readiness(
    source_type: str = Query("preset", description="preset or backtest_run"),
    source_id: str = Query(..., description="preset name or backtest run id"),
):
    """Deployment-readiness evidence for a source — the canonical pipeline check.

    Complements /deployments/quality (which gates on the source's own backtest):
    this surfaces whether the HONEST validation steps were done for the same
    strategy/params — a completed walk-forward optimization (does the edge
    survive out of sample?) and option-rupee evidence (does it survive premium,
    spread, and costs?). Informational only; never blocks creation.
    """
    db = get_db()
    source = await _load_deployment_source(db, source_type, source_id)
    cfg = source.get("config") or {}
    strategy_id = cfg.get("strategy_id") or source.get("strategy_id")
    instrument = (cfg.get("instrument") or source.get("instrument") or "").upper()
    params = cfg.get("params") or source.get("params") or {}

    # --- Honest-WFO evidence: latest completed walk-forward for this strategy ---
    wfo_job = await db.optimization_jobs.find_one(
        {"kind": "wfo", "strategy_id": strategy_id, "instrument": instrument, "status": "done"},
        {"_id": 0, "id": 1, "finished_at": 1, "best_params": 1,
         "wfo.efficiency": 1, "wfo.consistency": 1, "wfo.option_oos.net_pnl_value": 1},
        sort=[("finished_at", -1)],
    )
    wfo_evidence = None
    if wfo_job:
        w = wfo_job.get("wfo") or {}
        consistency = w.get("consistency") or {}
        option_oos = w.get("option_oos") or {}
        wfo_evidence = {
            "job_id": wfo_job.get("id"),
            "finished_at": wfo_job.get("finished_at"),
            "efficiency": w.get("efficiency"),
            "windows": consistency.get("windows"),
            "positive_windows": consistency.get("positive_windows"),
            "consistency_pct": consistency.get("consistency_pct"),
            "option_oos_net": option_oos.get("net_pnl_value"),
            "params_match": (wfo_job.get("best_params") or {}) == params,
        }

    # --- Option-rupee evidence: exact-params re-rank job or option backtest ---
    option_evidence = None
    rerank_jobs = await db.optimization_jobs.find(
        {"kind": {"$ne": "wfo"}, "strategy_id": strategy_id, "instrument": instrument,
         "status": "done", "evaluation_mode": "option_rerank"},
        {"_id": 0, "id": 1, "finished_at": 1, "best_params": 1, "rerank.ranked": {"$slice": 1}},
    ).sort("finished_at", -1).limit(25).to_list(length=25)
    for job in rerank_jobs:
        # rerank = {top_k, evaluated, option_config, ranked: [...]}; the ranked
        # list is sorted by option net rupee, so row 0 is the option-best.
        top = ((job.get("rerank") or {}).get("ranked") or [{}])[0]
        match = (job.get("best_params") or {}) == params
        if option_evidence is None or (match and not option_evidence.get("params_match")):
            option_evidence = {
                "kind": "rerank",
                "id": job.get("id"),
                "at": job.get("finished_at"),
                "net_pnl_value": top.get("option_pnl_value"),
                "win_rate": top.get("option_win_rate"),
                "paired_trade_count": top.get("paired_trade_count"),
                "params_match": match,
            }
        if option_evidence.get("params_match"):
            break
    if option_evidence is None or not option_evidence.get("params_match"):
        runs = await db.backtest_runs.find(
            {"config.strategy_id": strategy_id, "config.instrument": instrument,
             "config.option_backtest.enabled": True, "option_backtest.metrics": {"$ne": None}},
            {"_id": 0, "id": 1, "created_at": 1, "config.params": 1, "option_backtest.metrics": 1},
        ).sort("created_at", -1).limit(25).to_list(length=25)
        for run in runs:
            metrics = (run.get("option_backtest") or {}).get("metrics") or {}
            match = (run.get("config") or {}).get("params") == params
            candidate = {
                "kind": "backtest_run",
                "id": run.get("id"),
                "at": run.get("created_at"),
                "net_pnl_value": metrics.get("total_option_pnl_value"),
                "win_rate": metrics.get("win_rate"),
                "paired_trade_count": metrics.get("paired_trade_count"),
                "params_match": match,
            }
            if option_evidence is None or (match and not option_evidence.get("params_match")):
                option_evidence = candidate
            if match:
                break

    return serialize_doc({
        "source": {
            "type": source_type,
            "id": source_id,
            "strategy_id": strategy_id,
            "instrument": instrument,
            "has_execution": bool(cfg.get("execution")),
        },
        "wfo": wfo_evidence,
        "option_evidence": option_evidence,
    })


@api.get("/deployments/metrics")
async def list_deployment_metrics(
    strategy_id: Optional[str] = Query(None),
    include_ineligible: bool = Query(False),
    limit: int = Query(100, le=300),
):
    """Return session-gated forward metrics for deployments.

    By default this returns only deployments that have met the Strategy Library
    visibility gate (>=10 complete forward sessions). Pass include_ineligible=1
    for audit/debug views that need to see collecting deployments too.
    """
    db = get_db()
    query: Dict[str, Any] = {}
    if strategy_id:
        query["strategy_id"] = strategy_id
    deployments = await db.strategy_deployments.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(length=limit)
    items = await compute_forward_metrics_for_deployments(db, deployments)
    if not include_ineligible:
        items = [item for item in items if (item.get("library_gate") or {}).get("visible")]
    return serialize_doc({"items": items, "count": len(items)})


@api.get("/deployments/{deployment_id}/metrics")
async def get_deployment_metrics(deployment_id: str):
    """Return session-gated forward metrics for one deployment."""
    db = get_db()
    deployment = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not deployment:
        raise HTTPException(404, "Deployment not found")
    return serialize_doc(await compute_forward_metrics_for_deployment(db, deployment))


@api.get("/deployments/overview")
async def deployments_overview():
    """Command-center summary: one row per non-archived deployment with today's
    activity (signals, open trades, realized + open P&L) and lifetime paper
    results. Powers the Deployments page cards in a single call."""
    db = get_db()
    deployments = await db.strategy_deployments.find(
        {"status": {"$ne": "ARCHIVED"}}, {"_id": 0}
    ).sort("created_at", -1).to_list(length=200)
    dep_ids = [str(d.get("id")) for d in deployments]
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    today_iso = ist_now.strftime("%Y-%m-%d")
    start_ms, end_ms = _ist_day_bounds_ms_full(today_iso, today_iso)
    utc_day_start_iso = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat()

    sig_stats: Dict[str, Dict[str, int]] = {}
    if dep_ids:
        rows = await db.signals.aggregate([
            {"$match": {"deployment_id": {"$in": dep_ids}, "bar_ts": {"$gte": start_ms, "$lt": end_ms}}},
            {"$group": {"_id": {"dep": "$deployment_id", "blocked": {"$eq": ["$blocked", True]}}, "n": {"$sum": 1}}},
        ]).to_list(length=None)
        for r in rows:
            dep = str((r.get("_id") or {}).get("dep") or "")
            entry = sig_stats.setdefault(dep, {"clean": 0, "blocked": 0})
            entry["blocked" if (r.get("_id") or {}).get("blocked") else "clean"] += int(r.get("n") or 0)

    trade_stats: Dict[str, Dict[str, Any]] = {}
    if dep_ids:
        rows = await db.paper_trades.aggregate([
            {"$match": {"deployment_id": {"$in": dep_ids}}},
            {"$group": {
                "_id": "$deployment_id",
                "open_count": {"$sum": {"$cond": [{"$eq": ["$status", "OPEN"]}, 1, 0]}},
                "open_unrealized": {"$sum": {"$cond": [{"$eq": ["$status", "OPEN"]}, {"$ifNull": ["$unrealized_pnl", 0]}, 0]}},
                "closed_count": {"$sum": {"$cond": [{"$eq": ["$status", "CLOSED"]}, 1, 0]}},
                "realized_total": {"$sum": {"$cond": [{"$eq": ["$status", "CLOSED"]}, {"$ifNull": ["$realized_pnl", 0]}, 0]}},
                "wins": {"$sum": {"$cond": [{"$and": [{"$eq": ["$status", "CLOSED"]}, {"$gt": [{"$ifNull": ["$realized_pnl", 0]}, 0]}]}, 1, 0]}},
                "realized_today": {"$sum": {"$cond": [{"$and": [{"$eq": ["$status", "CLOSED"]}, {"$gte": [{"$ifNull": ["$closed_at", ""]}, utc_day_start_iso]}]}, {"$ifNull": ["$realized_pnl", 0]}, 0]}},
            }},
        ]).to_list(length=None)
        for r in rows:
            trade_stats[str(r.get("_id") or "")] = r

    items = []
    totals = {"open_trades": 0, "open_unrealized": 0.0, "realized_today": 0.0, "signals_today": 0}
    for d in deployments:
        dep_id = str(d.get("id"))
        sig = sig_stats.get(dep_id, {"clean": 0, "blocked": 0})
        tr = trade_stats.get(dep_id, {})
        closed = int(tr.get("closed_count") or 0)
        wins = int(tr.get("wins") or 0)
        item = {
            "deployment": {k: d.get(k) for k in (
                "id", "name", "mode", "status", "instrument", "strategy_id", "source_type", "source_id",
                "option_policy", "risk", "pretrade_profile", "created_at", "kill_switch_reason", "drift_reason",
            )},
            "today": {
                "clean_signals": sig["clean"],
                "blocked_signals": sig["blocked"],
                "realized_pnl": round(float(tr.get("realized_today") or 0.0), 2),
                "open_trades": int(tr.get("open_count") or 0),
                "open_unrealized": round(float(tr.get("open_unrealized") or 0.0), 2),
            },
            "lifetime": {
                "closed_trades": closed,
                "realized_pnl": round(float(tr.get("realized_total") or 0.0), 2),
                "win_rate": round(wins / closed * 100, 1) if closed else None,
            },
        }
        items.append(item)
        totals["open_trades"] += item["today"]["open_trades"]
        totals["open_unrealized"] = round(totals["open_unrealized"] + item["today"]["open_unrealized"], 2)
        totals["realized_today"] = round(totals["realized_today"] + item["today"]["realized_pnl"], 2)
        totals["signals_today"] += sig["clean"] + sig["blocked"]
    return serialize_doc({"items": items, "totals": totals, "as_of_ist": ist_now.isoformat()})


@api.get("/deployments/{deployment_id}")
async def get_deployment(deployment_id: str):
    doc = await get_db().strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Deployment not found")
    return serialize_doc(doc)


async def _set_deployment_status(deployment_id: str, status: str) -> Dict[str, Any]:
    db = get_db()
    doc = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Deployment not found")
    doc["status"] = status
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.strategy_deployments.replace_one({"id": deployment_id}, doc, upsert=False)
    return doc


@api.post("/deployments/{deployment_id}/pause")
async def pause_deployment(deployment_id: str):
    return serialize_doc(await _set_deployment_status(deployment_id, "PAUSED"))


@api.post("/deployments/{deployment_id}/resume")
async def resume_deployment(deployment_id: str):
    doc = await _set_deployment_status(deployment_id, "ACTIVE")
    stream = await _auto_follow_option_stream()
    return serialize_doc({**doc, "option_stream": stream})


@api.post("/deployments/{deployment_id}/repin-source")
async def repin_deployment_source(deployment_id: str):
    """Re-pin a deployment to its strategy's CURRENT source after a drift pause.

    Recomputes the plugin's source SHA, updates `strategy_source_sha`, clears the
    drift audit fields, appends a `repin_history` entry, and (only if the
    deployment was auto-paused for `strategy_source_drift`) resumes it. Use this
    when the plugin edit was intentional and you accept the new code as the
    pinned baseline."""
    db = get_db()
    deployment = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not deployment:
        raise HTTPException(404, "Deployment not found")
    strategy_id = str(deployment.get("strategy_id") or "")
    strategy_obj = get_registry().get(strategy_id) if strategy_id else None
    if strategy_obj is None:
        raise HTTPException(409, f"Strategy '{strategy_id}' is not loaded — cannot re-pin its source.")
    current_sha = hash_strategy_source(strategy_obj)
    if not current_sha:
        raise HTTPException(409, "Could not resolve the strategy's source file to re-pin.")

    upd = build_repin_update(deployment, current_sha)
    mongo_update: Dict[str, Any] = {
        "$set": upd["set"],
        "$unset": upd["unset"],
        "$push": {"repin_history": upd["audit"]},
    }
    await db.strategy_deployments.update_one({"id": deployment_id}, mongo_update)
    doc = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    stream = await _auto_follow_option_stream() if upd["resumed"] else {}
    return serialize_doc({**doc, "repinned_to": current_sha, "resumed": upd["resumed"],
                          **({"option_stream": stream} if upd["resumed"] else {})})


@api.post("/deployments/{deployment_id}/archive")
async def archive_deployment(deployment_id: str, purge: int = Query(0, description="1 = also delete this deployment's signals and CLOSED trades")):
    """Undeploy: stops signal generation and paper trading for this strategy.
    With purge=1 its journaled signals and CLOSED trades are deleted too
    (OPEN trades are kept so the marker / square-off can finish them)."""
    doc = await _set_deployment_status(deployment_id, "ARCHIVED")
    purged: Dict[str, Any] = {}
    if purge:
        db = get_db()
        sig_res = await db.signals.delete_many({"deployment_id": deployment_id})
        trade_res = await db.paper_trades.delete_many({"deployment_id": deployment_id, "status": "CLOSED"})
        open_left = await db.paper_trades.count_documents({"deployment_id": deployment_id, "status": "OPEN"})
        purged = {"signals_deleted": int(sig_res.deleted_count),
                  "closed_trades_deleted": int(trade_res.deleted_count),
                  "open_trades_kept": int(open_left)}
    return serialize_doc({**doc, **({"purged": purged} if purge else {})})


@api.get("/deployments/{deployment_id}/signals")
async def list_deployment_signals(deployment_id: str, limit: int = Query(100, le=500)):
    rows = await get_db().signals.find({"deployment_id": deployment_id}, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(length=limit)
    return {"items": serialize_doc(rows), "count": len(rows)}


@api.post("/deployments/{deployment_id}/evaluate-on-close")
async def evaluate_deployment_now(deployment_id: str):
    """Run the 1-minute close evaluator against this deployment once.

    Shadow-mode only in this slice: a clean signal is journaled as CONFIRMED awaiting manual
    approval; blocked signals are journaled as AUDITED with a blockers list. No paper trade
    is created.
    """
    db = get_db()
    deployment = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not deployment:
        raise HTTPException(404, "Deployment not found")
    result = await evaluate_deployment_on_close(db, deployment)
    return serialize_doc(result)


@api.post("/deployments/evaluate-active")
async def evaluate_active_deployments_route():
    """Run the evaluator across every ACTIVE deployment. Used by the scheduler and on-demand."""
    db = get_db()
    results = await evaluate_active_deployments(db)
    return serialize_doc({"items": results, "count": len(results)})


@api.post("/paper/square-off")
async def manual_paper_square_off():
    """Force-close all OPEN paper trades immediately. Idempotent (closed trades are skipped).

    Used for manual end-of-day cleanup or testing. The scheduled auto-square-off runs at
    15:00 IST during the market session loop.
    """
    db = get_db()
    summaries = await square_off_open_paper_trades(
        db,
        latest_tick_lookup=upstox_stream_manager.latest_tick_map().get,
        reason="manual_square_off",
    )
    return serialize_doc({"items": summaries, "count": len(summaries)})


@api.get("/live-candles/status")
async def live_candle_roller_status():
    """Return the live tick-to-OHLC roller status: tick counts, active buckets, last error."""
    return serialize_doc(live_candle_roller.status())


@api.post("/live-candles/start")
async def live_candle_roller_start():
    """Manually start the live tick-to-OHLC roller. No-op if already running."""
    await live_candle_roller.start()
    return serialize_doc(live_candle_roller.status())


@api.post("/live-candles/stop")
async def live_candle_roller_stop():
    """Stop the roller and flush any in-progress buckets."""
    await live_candle_roller.stop()
    return serialize_doc(live_candle_roller.status())


# Manual research-signal creation, lifecycle transitions, the approval flow
# (approve / skip / mark-blocked), and manual deploy-to-paper were retired on
# 2026-06-12 (user decision): deployments journal and auto-trade their own
# signals; nothing requires manual approval. Old journaled signals remain
# readable through GET /signals and /signals/enriched.


_TRADES_SORT_FIELDS = {"updated_at", "created_at", "closed_at", "realized_pnl", "entry_price"}

_TRADES_CSV_COLUMNS = [
    "created_at", "deployment_name", "strategy_id", "instrument", "trading_symbol",
    "direction", "lots", "quantity", "entry_price", "exit_price", "exit_reason",
    "closed_at", "realized_pnl", "unrealized_pnl", "status",
]


@api.get("/paper/trades")
async def list_paper_trades(
    status: Optional[str] = Query(None),
    deployment_id: Optional[str] = Query(None),
    strategy_id: Optional[str] = Query(None),
    instrument: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD (IST), on entry time"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD (IST)"),
    sort: str = Query("-updated_at"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=500),
    format: Optional[str] = Query(None, description="csv to download"),
):
    """Paper-trade journal with server-side filter / sort / pagination / CSV.
    Each row carries the deployment name so the journal reads by strategy."""
    db = get_db()
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status.upper()
    if deployment_id:
        q["deployment_id"] = deployment_id
    if strategy_id:
        q["strategy_id"] = strategy_id
    if instrument:
        q["instrument"] = instrument.upper()
    start_ms, end_ms = _ist_day_bounds_ms_full(date_from, date_to)
    if start_ms is not None or end_ms is not None:
        rng: Dict[str, Any] = {}
        if start_ms is not None:
            rng["$gte"] = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat()
        if end_ms is not None:
            rng["$lt"] = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).isoformat()
        q["created_at"] = rng

    field = sort.lstrip("-")
    direction = -1 if sort.startswith("-") else 1
    if field not in _TRADES_SORT_FIELDS:
        field, direction = "updated_at", -1

    total = await db.paper_trades.count_documents(q)
    rows = await db.paper_trades.find(q, {"_id": 0, "events": 0}).sort(field, direction).skip(skip).limit(limit).to_list(length=limit)

    dep_ids = sorted({str(r.get("deployment_id")) for r in rows if r.get("deployment_id")})
    dep_names: Dict[str, str] = {}
    if dep_ids:
        for d in await db.strategy_deployments.find({"id": {"$in": dep_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(length=len(dep_ids)):
            dep_names[str(d.get("id"))] = str(d.get("name") or "")
    for r in rows:
        r["deployment_name"] = dep_names.get(str(r.get("deployment_id") or ""), "")

    if (format or "").lower() == "csv":
        return _csv_response(rows, _TRADES_CSV_COLUMNS, "paper_trades.csv")
    return {"items": serialize_doc(rows), "count": len(rows), "total": total, "skip": skip, "limit": limit}


class TradesPurgeReq(BaseModel):
    ids: Optional[List[str]] = None
    deployment_id: Optional[str] = None
    older_than_days: Optional[int] = None


@api.post("/paper/trades/purge")
async def purge_paper_trades(req: TradesPurgeReq):
    """Delete CLOSED paper trades (OPEN trades are never deletable). Requires
    at least one criterion; criteria AND together."""
    if not (req.ids or req.deployment_id or req.older_than_days):
        raise HTTPException(400, "Provide ids, deployment_id, or older_than_days")
    q: Dict[str, Any] = {"status": "CLOSED"}
    if req.ids:
        q["id"] = {"$in": [str(i) for i in req.ids]}
    if req.deployment_id:
        q["deployment_id"] = req.deployment_id
    if req.older_than_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(req.older_than_days))).isoformat()
        q["updated_at"] = {"$lt": cutoff}
    res = await get_db().paper_trades.delete_many(q)
    return {"deleted": int(res.deleted_count)}


@api.post("/paper/trades/{trade_id}/mark")
async def mark_paper_trade(trade_id: str, req: PaperMarkReq):
    db = get_db()
    trade = await db.paper_trades.find_one({"id": trade_id}, {"_id": 0})
    if not trade:
        raise HTTPException(404, "Paper trade not found")
    updated = mark_trade_to_market(trade, last_price=req.last_price, auto_close_on_risk=req.auto_close_on_risk)
    await db.paper_trades.replace_one({"id": trade_id}, updated, upsert=False)
    return serialize_doc(updated)


@api.post("/paper/trades/{trade_id}/close")
async def close_paper_trade(trade_id: str, req: PaperCloseReq):
    db = get_db()
    trade = await db.paper_trades.find_one({"id": trade_id}, {"_id": 0})
    if not trade:
        raise HTTPException(404, "Paper trade not found")
    updated = close_trade(trade, exit_price=req.exit_price, reason=req.reason)
    await db.paper_trades.replace_one({"id": trade_id}, updated, upsert=False)
    if updated.get("signal_id"):
        signal = await db.signals.find_one({"id": updated["signal_id"]}, {"_id": 0})
        if signal and str(signal.get("state") or "").upper() == "ACTIVE":
            try:
                exited = transition_signal(signal, "EXITED", reason="paper_trade_closed", snapshot={"trade_id": trade_id, "realized_pnl": updated.get("realized_pnl")})
                await db.signals.replace_one({"id": signal["id"]}, exited, upsert=False)
            except SignalStateError:
                pass
    return serialize_doc(updated)


# ---------------------------------------------------------------------------
# Auto-Optimizer (Phase 3)
# ---------------------------------------------------------------------------

class OptimizerStartReq(BaseModel):
    instrument: str = "NIFTY"
    mode: str = "SCALP"
    strategy_id: str
    method: str = "bayesian"  # bayesian | grid | genetic
    objective: str = "risk_adjusted"  # sharpe | profit_factor | total_pnl_pts | net_pnl_inr | win_rate | neg_max_dd | risk_adjusted
    n_trials: int = 200
    costs_enabled: bool = True
    pretrade_filters: Dict[str, Any] = Field(default_factory=dict)
    pretrade_profile: Optional[str] = None  # stored for lossless clone/display; engine uses pretrade_filters
    param_overrides: Dict[str, Any] = Field(default_factory=dict)
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    name: str = "Optimization run"
    # Guard rails against degenerate solutions (1-trade / all-PE etc.)
    min_trades: int = 10
    min_direction_share: float = 0.0  # 0 disables one-sided guard
    optimize_indicator_periods: bool = False
    # Evaluation mode: "spot" (default, original — score the index backtest) or
    # "option_rerank" (two-stage: spot search, then re-rank the top-K candidates
    # by REAL paired-option net rupee P&L). option_config mirrors OptionBacktestReq.
    evaluation_mode: str = "spot"
    rerank_top_k: int = 50
    option_config: Optional[Dict[str, Any]] = None


@api.post("/optimize/start")
async def optimize_start(req: OptimizerStartReq):
    if req.method not in ("bayesian", "grid", "genetic"):
        raise HTTPException(400, f"Unknown method {req.method}")
    if not get_registry().get(req.strategy_id):
        raise HTTPException(404, f"Strategy {req.strategy_id} not found")
    if not (10 <= req.n_trials <= 5000):
        raise HTTPException(400, "n_trials must be 10–5000")
    if req.evaluation_mode not in ("spot", "option_rerank"):
        raise HTTPException(400, f"Unknown evaluation_mode {req.evaluation_mode}")
    if req.evaluation_mode == "option_rerank" and not (1 <= req.rerank_top_k <= 500):
        raise HTTPException(400, "rerank_top_k must be 1–500")
    job_id = await optimizer_create_job(req.model_dump())
    return {"job_id": job_id, "status": "queued"}


@api.get("/optimize/jobs")
async def list_opt_jobs(limit: int = Query(50, le=200)):
    db = get_db()
    cur = db.optimization_jobs.find(
        {},
        {"_id": 0, "param_space": 0, "top_n_alternatives": 0, "heatmap": 0, "robustness": 0, "rerank": 0, "trial_log": 0, "wfo": 0, "wfo_windows": 0, "wfo_oos_trades": 0},
    ).sort("created_at", -1).limit(limit)
    rows = await cur.to_list(length=limit)
    return {"items": rows}


@api.get("/optimize/jobs/{job_id}")
async def get_opt_job(job_id: str):
    db = get_db()
    doc = await db.optimization_jobs.find_one({"id": job_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Job not found")
    return serialize_doc(doc)


@api.delete("/optimize/jobs/{job_id}")
async def delete_opt_job(job_id: str):
    db = get_db()
    res = await db.optimization_jobs.delete_one({"id": job_id})
    return {"deleted": res.deleted_count}


@api.post("/optimize/jobs/{job_id}/cancel")
async def cancel_opt_job(job_id: str):
    db = get_db()
    doc = await db.optimization_jobs.find_one({"id": job_id}, {"_id": 0, "status": 1})
    if not doc:
        raise HTTPException(404, "Job not found")
    if doc.get("status") in ("done", "failed", "cancelled"):
        return {"already_finished": True, "status": doc.get("status")}
    await db.optimization_jobs.update_one({"id": job_id}, {"$set": {"cancelled": True}})
    return {"ok": True}


@api.post("/optimize/jobs/{job_id}/pause")
async def pause_opt_job(job_id: str):
    db = get_db()
    doc = await db.optimization_jobs.find_one({"id": job_id}, {"_id": 0, "status": 1})
    if not doc:
        raise HTTPException(404, "Job not found")
    if doc.get("status") not in ("running", "queued", "analyzing"):
        return {"ok": False, "status": doc.get("status"), "reason": "not_running"}
    await db.optimization_jobs.update_one({"id": job_id}, {"$set": {"paused": True}})
    return {"ok": True}


@api.post("/optimize/jobs/{job_id}/resume")
async def resume_opt_job(job_id: str):
    db = get_db()
    doc = await db.optimization_jobs.find_one({"id": job_id}, {"_id": 0, "kind": 1})
    if not doc:
        raise HTTPException(404, "Job not found")
    if doc.get("kind") == "wfo":
        ok = await resume_wfo_job(job_id)
    else:
        ok = await optimizer_resume_job(job_id)
    if not ok:
        raise HTTPException(400, "Job cannot be resumed (not paused/interrupted/failed, or missing config)")
    return {"ok": True, "status": "running"}


class WfoStartReq(BaseModel):
    """Walk-forward optimization: re-optimize on each train window, evaluate on
    the unseen test window, stitch OOS. Window sizes are in TRADING DAYS present
    in the data (holiday-aware by construction)."""
    instrument: str = "NIFTY"
    mode: str = "SCALP"
    strategy_id: str
    method: str = "bayesian"  # bayesian | genetic (grid is not supported per-window)
    objective: str = "risk_adjusted"
    costs_enabled: bool = True
    pretrade_filters: Dict[str, Any] = Field(default_factory=dict)
    pretrade_profile: Optional[str] = None
    param_overrides: Dict[str, Any] = Field(default_factory=dict)
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    name: str = "Walk-forward optimization"
    min_trades: int = 10
    min_direction_share: float = 0.0
    optimize_indicator_periods: bool = False
    # Window configuration
    train_days: int = 60
    test_days: int = 20
    step_days: Optional[int] = None  # default = test_days (contiguous OOS)
    wf_mode: str = "rolling"  # rolling | anchored
    n_trials_per_window: int = 40
    max_windows: int = 12
    # Option-aware OOS (WFO v2): after stitching, pair the OOS spot trades with
    # real option candles ONCE and report net rupee + per-window rupee
    # consistency alongside the spot stitch. option_config mirrors the
    # optimizer re-rank's option_config shape.
    option_aware: bool = False
    option_config: Optional[Dict[str, Any]] = None


@api.post("/optimize/wfo")
async def optimize_wfo_start(req: WfoStartReq):
    if req.method not in ("bayesian", "genetic"):
        raise HTTPException(400, f"Unknown method {req.method} (wfo supports bayesian | genetic)")
    if not get_registry().get(req.strategy_id):
        raise HTTPException(404, f"Strategy {req.strategy_id} not found")
    if req.wf_mode not in ("rolling", "anchored"):
        raise HTTPException(400, f"Unknown wf_mode {req.wf_mode}")
    if not (20 <= req.train_days <= 250):
        raise HTTPException(400, "train_days must be 20–250 trading days")
    if not (5 <= req.test_days <= 60):
        raise HTTPException(400, "test_days must be 5–60 trading days")
    if req.step_days is not None and not (1 <= req.step_days <= 60):
        raise HTTPException(400, "step_days must be 1–60 trading days")
    if not (10 <= req.n_trials_per_window <= 500):
        raise HTTPException(400, "n_trials_per_window must be 10–500")
    if not (2 <= req.max_windows <= 36):
        raise HTTPException(400, "max_windows must be 2–36")
    job_id = await create_wfo_job(req.model_dump())
    return {"job_id": job_id, "status": "queued", "kind": "wfo"}


@api.post("/optimize/apply-as-preset/{job_id}")
async def apply_opt_as_preset(job_id: str, name: str = Query(...)):
    """Save the best params from an optimization as a Preset for reuse in Backtest Lab."""
    db = get_db()
    job = await db.optimization_jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("status") not in ("done", "cancelled", "paused", "interrupted", "failed"):
        raise HTTPException(400, "Job has no finished result yet")
    best_params = job.get("best_params") or (job.get("best_so_far") or {}).get("params")
    if not best_params:
        raise HTTPException(400, "Job has no best parameters to save (no qualifying trial yet)")
    config = {
        "instrument": job["instrument"],
        "mode": job.get("config", {}).get("mode", "SCALP"),
        "strategy_id": job["strategy_id"],
        "params": best_params,
        "source_optimization_job": job_id,
        "source_job_kind": job.get("kind") or "single",
        "optimization_method": job["method"],
        "objective": job["objective"],
    }
    # Carry the execution policy the result was validated under (option re-rank
    # or option-aware WFO), so the preset is the full deployable artifact:
    # Backtest Lab re-applies it on load and the deployment form prefills from it.
    execution = execution_from_option_config((job.get("config") or {}).get("option_config"))
    if execution:
        config["execution"] = execution
    await db.presets.update_one(
        {"name": name},
        {"$set": {"name": name, "config": config, "saved_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return {"ok": True, "preset_name": name}


# ---------------------------------------------------------------------------
# Upstox V3 (Phase 4a) \u2014 OAuth + historical candle ingestion
# ---------------------------------------------------------------------------

import secrets
import uuid as _uuid
from fastapi.responses import RedirectResponse

# In-memory OAuth state store (per-process). For multi-instance prod, switch to Redis.
_OAUTH_STATES: Dict[str, float] = {}


@api.get("/upstox/status")
async def upstox_status():
    return await upstox_client.get_connection_status()


@api.get("/upstox/auth/start")
async def upstox_auth_start():
    if not upstox_client.is_configured():
        raise HTTPException(500, "Upstox credentials not configured. Set UPSTOX_CLIENT_ID / UPSTOX_CLIENT_SECRET / UPSTOX_REDIRECT_URI in backend/.env")
    state = secrets.token_urlsafe(24)
    _OAUTH_STATES[state] = datetime.now(timezone.utc).timestamp()
    # Prune old states (>15 min)
    now = datetime.now(timezone.utc).timestamp()
    for s, t in list(_OAUTH_STATES.items()):
        if now - t > 900:
            _OAUTH_STATES.pop(s, None)
    url = upstox_client.build_login_url(state)
    return {"login_url": url, "state": state}


@api.get("/upstox/auth/callback")
async def upstox_auth_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Browser is redirected here by Upstox after login. Exchange code for token, then redirect to frontend."""
    frontend_url = os.environ.get("FRONTEND_POST_AUTH_URL", "/warehouse")
    if error:
        return RedirectResponse(f"{frontend_url}?upstox_error={error}")
    if not code or not state:
        return RedirectResponse(f"{frontend_url}?upstox_error=missing_code_or_state")
    if state not in _OAUTH_STATES:
        return RedirectResponse(f"{frontend_url}?upstox_error=invalid_state")
    _OAUTH_STATES.pop(state, None)
    try:
        payload = await upstox_client.exchange_code_for_token(code)
        await upstox_client.save_token(upstox_client.DEFAULT_USER_ID, payload)
        # Fresh token: kick off a warehouse catch-up in the background so the
        # redirect is not delayed. Best-effort; failures are captured in state.
        asyncio.create_task(_trigger_autoupdate("oauth_connect"), name="warehouse-autoupdate-oauth")
        return RedirectResponse(f"{frontend_url}?upstox_connected=1")
    except Exception as e:
        log.exception("upstox token exchange failed")
        return RedirectResponse(f"{frontend_url}?upstox_error={str(e)[:200]}")


@api.post("/upstox/disconnect")
async def upstox_disconnect():
    deleted = await upstox_client.disconnect()
    return {"disconnected": deleted}


@api.get("/upstox/market-quote/{instrument}")
async def upstox_market_quote(instrument: str):
    """Read a live Upstox market quote snapshot for a supported index."""
    try:
        return await upstox_client.fetch_market_quote(instrument)
    except Exception as e:
        raise HTTPException(400, str(e)[:300])


class UpstoxStreamStartReq(BaseModel):
    instrument_keys: Optional[List[str]] = None
    mode: str = DEFAULT_STREAM_MODE
    persist_ticks: bool = True


class UpstoxOptionStreamRestartReq(BaseModel):
    underlyings: Optional[List[str]] = None
    radius: int = Field(1, ge=0, le=5)
    max_option_keys: int = Field(60, ge=2, le=200)
    mode: str = DEFAULT_STREAM_MODE
    persist_ticks: bool = True


def _default_stream_instrument_keys() -> List[str]:
    keys: List[str] = []
    for item in DEFAULT_ITEMS:
        if item.get("source") == "upstox" and item.get("instrument_key"):
            keys.append(str(item["instrument_key"]))
    return list(dict.fromkeys(keys))


def _parse_underlyings_query(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


@api.post("/upstox/stream/start")
async def upstox_stream_start(req: UpstoxStreamStartReq):
    """Start the read-only Upstox V3 market-data WebSocket stream."""
    status = await upstox_client.get_connection_status()
    if not status.get("connected"):
        raise HTTPException(400, "Upstox is not connected. Complete OAuth before starting the stream.")
    if status.get("expired"):
        raise HTTPException(400, "Upstox token expired. Reconnect Upstox before starting the stream.")
    instrument_keys = req.instrument_keys or _default_stream_instrument_keys()
    if not instrument_keys:
        raise HTTPException(400, "No stream instrument keys configured")
    try:
        return serialize_doc(await upstox_stream_manager.start(
            instrument_keys=instrument_keys,
            mode=req.mode,
            persist=req.persist_ticks,
        ))
    except ValueError as e:
        raise HTTPException(400, str(e))


@api.post("/upstox/stream/stop")
async def upstox_stream_stop():
    """Stop the local read-only Upstox WebSocket stream."""
    return serialize_doc(await upstox_stream_manager.stop())


@api.get("/upstox/stream/status")
async def upstox_stream_status():
    """Return sanitized local WebSocket stream status."""
    return serialize_doc(upstox_stream_manager.status())


@api.get("/upstox/stream/options/universe")
async def upstox_stream_options_universe(
    underlyings: Optional[str] = Query(None, description="Comma-separated index underlyings, e.g. NIFTY,BANKNIFTY"),
    radius: int = Query(1, ge=0, le=5),
    max_option_keys: int = Query(60, ge=2, le=200),
):
    """Preview the nearest-expiry ATM option keys suitable for the live WS stream."""
    result = await build_live_option_universe(
        get_db(),
        latest_ticks=upstox_stream_manager.latest_tick_map(),
        underlyings=_parse_underlyings_query(underlyings),
        radius=radius,
        max_option_keys=max_option_keys,
    )
    return serialize_doc(result)


@api.post("/upstox/stream/options/restart")
async def upstox_stream_options_restart(req: UpstoxOptionStreamRestartReq):
    """Restart the read-only stream with market-header keys plus live ATM option keys."""
    status = await upstox_client.get_connection_status()
    if not status.get("connected"):
        raise HTTPException(400, "Upstox is not connected. Complete OAuth before starting the stream.")
    if status.get("expired"):
        raise HTTPException(400, "Upstox token expired. Reconnect Upstox before starting the stream.")

    universe = await build_live_option_universe(
        get_db(),
        latest_ticks=upstox_stream_manager.latest_tick_map(),
        underlyings=req.underlyings,
        radius=req.radius,
        max_option_keys=req.max_option_keys,
    )
    option_keys = universe.get("instrument_keys") or []
    if not option_keys:
        raise HTTPException(400, "No live option keys available. Sync current option contracts and ensure spot data exists.")

    stream_keys = list(dict.fromkeys([*_default_stream_instrument_keys(), *option_keys]))
    await upstox_stream_manager.stop()
    stream_status = await upstox_stream_manager.start(
        instrument_keys=stream_keys,
        mode=req.mode,
        persist=req.persist_ticks,
    )
    return serialize_doc({
        "status": "ok",
        "stream": stream_status,
        "universe": universe,
        "stream_instrument_count": len(stream_keys),
    })


@api.get("/upstox/stream/ticks/latest")
async def upstox_stream_latest_ticks(limit: int = Query(50, le=500)):
    """Return latest sanitized ticks from memory, falling back to stored Mongo ticks."""
    items = upstox_stream_manager.latest_ticks(limit=limit)
    if len(items) < limit:
        seen = {(item.get("instrument_key"), item.get("ts")) for item in items}
        rows = await get_db().ticks.find({}, {"_id": 0}).sort("received_ts", -1).limit(limit).to_list(length=limit)
        for row in rows:
            key = (row.get("instrument_key"), row.get("ts"))
            if key in seen:
                continue
            items.append(row)
            seen.add(key)
            if len(items) >= limit:
                break
    return {"items": serialize_doc(items[:limit]), "count": len(items[:limit]), "source": "upstox_ws_v3"}


class UpstoxIngestReq(BaseModel):
    instrument: str  # NIFTY / BANKNIFTY / SENSEX
    from_date: str   # YYYY-MM-DD (IST)
    to_date: str     # YYYY-MM-DD (IST)
    chunk_days: Optional[int] = None


class UpstoxOptionCandleIngestReq(BaseModel):
    instrument_key: str
    from_date: str
    to_date: str
    underlying: Optional[str] = None
    expiry_date: Optional[str] = None
    strike: Optional[float] = None
    side: Optional[str] = None
    trading_symbol: Optional[str] = None
    chunk_days: int = 7


class OptionWarehousePlanReq(BaseModel):
    underlying: str = "NIFTY"
    from_date: str
    to_date: str
    moneyness: List[str] = Field(default_factory=lambda: ["atm"])
    legs: List[str] = Field(default_factory=lambda: list(DEFAULT_LEGS))
    expiry_policy: str = "next_available"
    fixed_expiry_date: Optional[str] = None
    sample_interval_minutes: int = 15
    chunk_days: Optional[int] = None
    fetch_missing_only: bool = True
    max_contracts: int = 250
    confirm_large_fetch: bool = False


class ExpiredOptionContractBackfillReq(BaseModel):
    from_date: str
    to_date: str
    max_expiries: int = 12
    confirm_large_fetch: bool = False


class PaperMarkReq(BaseModel):
    last_price: float
    auto_close_on_risk: bool = True


class PaperCloseReq(BaseModel):
    exit_price: float
    reason: str = "manual"


class DeploymentCreateReq(BaseModel):
    name: str
    source_type: str
    source_id: str
    mode: str = "signal_only"  # signal_only | paper (legacy shadow/recommendation map to signal_only)
    confirmation_mode: str = "1m_close"
    option_moneyness: List[str] = Field(default_factory=lambda: ["atm"])
    pretrade_profile: str = "Balanced"
    risk: Dict[str, Any] = Field(default_factory=dict)
    dte_filter: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    allow_overnight: bool = False
    default_lots: int = 1
    # Auto paper trading (2026-06-10): paper mode only. When true, every clean
    # CONFIRMED signal opens a paper trade immediately (no manual approval) so
    # the signal's outcome is auditable. Default ON for new deployments.
    auto_paper: bool = True
    # Optional deployment-level premium exits (long options): points (₹ of
    # premium) or % of entry premium. Points take precedence over percent,
    # matching the backtest's option_levels rule. The strategy's own risk
    # hints on the signal take precedence over both.
    auto_paper_target_pts: Optional[float] = None
    auto_paper_stop_pts: Optional[float] = None
    auto_paper_target_pct: Optional[float] = None
    auto_paper_stop_pct: Optional[float] = None
    # Per-deployment kill switches (Slice 12). Paper mode only. Omit/0/None to disable.
    max_consecutive_losses: Optional[int] = None
    daily_loss_cutoff_pct: Optional[float] = None
    max_open_paper_trades: Optional[int] = None
    acknowledged_warnings: bool = False


def _ist_market_bounds_ms(from_date: str, to_date: str) -> tuple[int, int]:
    start = pd.Timestamp(f"{from_date} 09:15", tz="Asia/Kolkata")
    end = pd.Timestamp(f"{to_date} 15:30", tz="Asia/Kolkata")
    if start > end:
        raise ValueError("from_date must be before or equal to to_date")
    return int(start.tz_convert("UTC").value // 10**6), int(end.tz_convert("UTC").value // 10**6)


def _option_chunk_guidance(req: OptionWarehousePlanReq, contract_count: int) -> Dict[str, Any]:
    return chunk_guidance_for_options(req.from_date, req.to_date, contract_count, req.chunk_days)


async def _option_candle_counts(db: Any, instrument_keys: List[str], start_ts: int, end_ts: int) -> Dict[str, int]:
    if not instrument_keys:
        return {}
    pipeline = [
        {"$match": {"instrument_key": {"$in": instrument_keys}, "ts": {"$gte": int(start_ts), "$lte": int(end_ts)}}},
        {"$group": {"_id": "$instrument_key", "count": {"$sum": 1}}},
    ]
    counts: Dict[str, int] = {}
    async for doc in db.options_1m.aggregate(pipeline):
        counts[str(doc["_id"])] = int(doc.get("count", 0) or 0)
    return counts


async def _option_candle_date_counts(db: Any, instrument_keys: List[str], start_ts: int, end_ts: int) -> Dict[str, Dict[str, int]]:
    if not instrument_keys:
        return {}
    counts: Dict[str, Dict[str, int]] = {}
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
        per_key = counts.setdefault(key, {})
        per_key[date_str] = int(doc.get("count", 0) or 0)
    return counts


def _spot_date_counts(spot_df: pd.DataFrame) -> Dict[str, int]:
    if spot_df is None or spot_df.empty:
        return {}
    dates = pd.to_datetime(spot_df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d")
    return {str(k): int(v) for k, v in dates.value_counts().to_dict().items()}


def _apply_option_storage_counts(
    plan: Dict[str, Any],
    counts: Dict[str, int],
    spot_df: pd.DataFrame,
    date_counts_by_key: Optional[Dict[str, Dict[str, int]]] = None,
) -> Dict[str, Any]:
    date_counts = _spot_date_counts(spot_df)
    date_counts_by_key = date_counts_by_key or {}
    for item in plan.get("items", []):
        instrument_key = str(item.get("instrument_key"))
        stored = int(counts.get(instrument_key, 0) or 0)
        selected_dates = item.get("selected_dates") or []
        expected_by_date = {str(date_str): int(date_counts.get(str(date_str), 0) or 0) for date_str in selected_dates}
        stored_by_date = date_counts_by_key.get(instrument_key, {})
        fetch_dates = [
            str(date_str)
            for date_str, expected_count in expected_by_date.items()
            if int(stored_by_date.get(str(date_str), 0) or 0) < int(expected_count or 0)
        ]
        expected = sum(expected_by_date.values())
        stored_selected_dates = sum(min(int(stored_by_date.get(date_str, 0) or 0), int(expected_count or 0)) for date_str, expected_count in expected_by_date.items())
        if expected <= 0:
            expected = len(spot_df) if spot_df is not None else 0
            stored_selected_dates = stored
        item["stored_candles"] = stored
        item["stored_selected_date_candles"] = int(stored_selected_dates)
        item["expected_candles"] = int(expected)
        item["selected_date_counts"] = {
            date_str: {
                "expected": int(expected_count),
                "stored": int(stored_by_date.get(date_str, 0) or 0),
            }
            for date_str, expected_count in expected_by_date.items()
        }
        item["fetch_dates"] = sorted(fetch_dates)
        item["coverage_pct"] = round(min(100.0, (stored_selected_dates / expected) * 100), 2) if expected else 0.0
        item["needs_fetch"] = bool(fetch_dates) if expected_by_date else stored < expected

    summary = plan.setdefault("summary", {})
    summary["stored_contracts"] = sum(1 for item in plan.get("items", []) if not item.get("needs_fetch"))
    summary["missing_data_contracts"] = sum(1 for item in plan.get("items", []) if item.get("needs_fetch"))
    summary["expected_candles_per_selected_dates"] = sum(int(item.get("expected_candles", 0) or 0) for item in plan.get("items", []))
    summary["stored_option_candles"] = sum(int(item.get("stored_candles", 0) or 0) for item in plan.get("items", []))
    return plan


async def _build_option_warehouse_preview(req: OptionWarehousePlanReq) -> Dict[str, Any]:
    underlying = req.underlying.upper()
    if underlying not in upstox_client.INSTRUMENT_KEYS:
        raise HTTPException(400, f"Unsupported instrument: {req.underlying}")
    if req.sample_interval_minutes < 1 or req.sample_interval_minutes > 375:
        raise HTTPException(400, "sample_interval_minutes must be between 1 and 375")
    if req.expiry_policy == "fixed" and not req.fixed_expiry_date:
        raise HTTPException(400, "Fixed expiry date is required when expiry policy is fixed.")

    start_ts, end_ts = _ist_market_bounds_ms(req.from_date, req.to_date)
    db = get_db()
    spot_df = await load_candles_df(underlying, start_ts=start_ts, end_ts=end_ts)
    if spot_df.empty:
        raise HTTPException(400, f"Index candles missing for {underlying} {req.from_date} to {req.to_date}. Ingest the index first.")

    fixed_expiry = req.fixed_expiry_date if req.expiry_policy == "fixed" else None
    contract_query: Dict[str, Any] = {"underlying": underlying}
    if fixed_expiry:
        contract_query["expiry_date"] = fixed_expiry
    contracts = await db.option_contracts.find(contract_query, {"_id": 0}).sort([
        ("expiry_date", 1),
        ("strike", 1),
        ("side", 1),
    ]).to_list(length=50000)
    if not contracts:
        raise HTTPException(400, f"Option contracts missing for {underlying}. Sync contracts first.")

    plan = build_option_warehouse_plan(
        spot_candles=spot_df,
        contracts=contracts,
        underlying=underlying,
        moneyness=req.moneyness,
        legs=req.legs,
        sample_interval_minutes=req.sample_interval_minutes,
        fixed_expiry_date=fixed_expiry,
    )
    keys = [str(item["instrument_key"]) for item in plan.get("items", []) if item.get("instrument_key")]
    counts = await _option_candle_counts(db, keys, start_ts, end_ts)
    date_counts_by_key = await _option_candle_date_counts(db, keys, start_ts, end_ts)
    _apply_option_storage_counts(plan, counts, spot_df, date_counts_by_key=date_counts_by_key)
    missing_items = [item for item in plan.get("items", []) if item.get("needs_fetch")]
    plan["chunk_guidance"] = _option_chunk_guidance(req, len(missing_items) if req.fetch_missing_only else len(keys))
    plan["from_date"] = req.from_date
    plan["to_date"] = req.to_date
    plan["start_ts"] = start_ts
    plan["end_ts"] = end_ts
    plan["status"] = "ok"
    plan["fetch_ready"] = bool(plan.get("items")) and plan["summary"].get("missing_contract_count", 0) == 0
    if len(missing_items) > max(1, int(req.max_contracts or 1)):
        plan["warning"] = f"Preview has {len(missing_items)} missing-data contracts, above max_contracts={req.max_contracts}. Raise max_contracts or narrow the request to fetch."
    return plan


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


# ---------------------------------------------------------------------------
# Data Hygiene workflow (slice 6)
# ---------------------------------------------------------------------------


class DataHygieneScopeReq(BaseModel):
    start_date: str = HYGIENE_DEFAULT_START
    end_date: Optional[str] = None
    instruments: Optional[List[str]] = None
    moneyness: Optional[List[str]] = None
    legs: Optional[List[str]] = None
    sample_interval_minutes: int = HYGIENE_DEFAULT_SAMPLE


class DataHygieneExecuteReq(BaseModel):
    plan: Dict[str, Any]
    chunk_days_spot: int = 30
    max_contracts_per_action: int = 2000


@api.post("/data-hygiene/plan")
async def data_hygiene_plan_route(req: DataHygieneScopeReq):
    """Compute the hygiene plan against the current warehouse. Pure read - never fetches."""
    plan = await compute_hygiene_plan(
        get_db(),
        start_date=req.start_date,
        end_date=req.end_date,
        instruments=req.instruments,
        moneyness=req.moneyness,
        legs=req.legs,
        sample_interval_minutes=req.sample_interval_minutes,
    )
    return serialize_doc(plan)


class DataHygieneCatchUpReq(BaseModel):
    instruments: Optional[List[str]] = None
    moneyness: Optional[List[str]] = None
    legs: Optional[List[str]] = None
    sample_interval_minutes: int = HYGIENE_DEFAULT_SAMPLE
    include_options: bool = True
    dry_run: bool = False
    chunk_days_spot: int = 30


@api.post("/data-hygiene/catch-up")
async def data_hygiene_catch_up_route(req: DataHygieneCatchUpReq):
    """Incrementally bring spot + option data up to the last closed session.

    For each instrument this computes the gap from the last stored spot date to
    the most recent closed trading session, then runs a SEQUENTIAL per-instrument
    chain: spot ingest -> current contract sync -> option-candle fetch. The
    sequencing matters because the option-candle plan reads the freshly ingested
    spot candles + contracts to resolve ATM strikes; running them in parallel
    (as the full hygiene execute does) fails for brand-new days because the spot
    candles are not persisted yet. `dry_run=true` returns the plan without
    fetching. `include_options=false` updates spot only.

    Requires a connected, non-expired Upstox token (unless dry_run).
    """
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

    if total_actions == 0:
        return serialize_doc({"plan": plan, "submitted": [], "submitted_count": 0, "up_to_date": True})

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

    return serialize_doc({
        "plan": plan,
        "submitted": submitted,
        "submitted_count": len(submitted),
    })


async def _start_catch_up_chain(
    *,
    instrument: str,
    from_date: str,
    to_date: str,
    include_options: bool,
    moneyness: List[str],
    legs: List[str],
    sample_interval_minutes: int,
    chunk_days_spot: int,
) -> List[tuple]:
    """Create tracked run docs and launch ONE sequential task for an instrument.

    Returns [(kind, run_id), ...] for the stages that were created so the caller
    can report them and the job tracker can poll them. The actual work runs in a
    single background task that awaits each stage before starting the next.
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    spot_run_id = str(_uuid.uuid4())
    guidance = chunk_guidance_for_index(from_date, to_date, chunk_days_spot)
    eff_chunk_days = int(guidance["chunk_days"])
    await db.warehouse_runs.insert_one({
        "id": spot_run_id, "instrument": instrument,
        "source": "data_hygiene", "kind": "spot",
        "started_at": now, "updated_at": now, "status": "queued",
        "from_date": from_date, "to_date": to_date,
        "days": guidance["calendar_days"], "chunk_days": eff_chunk_days,
        "chunk_mode": guidance["mode"], "total_chunks": guidance["estimated_api_calls"],
        "completed_chunks": 0, "progress_pct": 0,
        "total_fetched": 0, "candles_added": 0, "candles_updated": 0,
        "matched_existing": 0, "failed_chunks": [],
    })
    created: List[tuple] = [("spot", spot_run_id)]

    contracts_run_id = None
    options_run_id = None
    if include_options:
        contracts_run_id = str(_uuid.uuid4())
        await db.warehouse_runs.insert_one({
            "id": contracts_run_id, "instrument": instrument,
            "source": "data_hygiene", "kind": "contracts",
            "started_at": now, "updated_at": now, "status": "queued",
            "from_date": from_date, "to_date": to_date, "progress_pct": 0,
        })
        created.append(("contracts", contracts_run_id))

        options_run_id = str(_uuid.uuid4())
        await db.warehouse_runs.insert_one({
            "id": options_run_id, "instrument": instrument,
            "source": "data_hygiene", "kind": "option_candles",
            "started_at": now, "updated_at": now, "status": "queued",
            "from_date": from_date, "to_date": to_date,
            "moneyness": moneyness, "legs": legs, "progress_pct": 0,
        })
        created.append(("option_candles", options_run_id))

    asyncio.create_task(_run_catch_up_chain(
        instrument=instrument,
        from_date=from_date,
        to_date=to_date,
        eff_chunk_days=eff_chunk_days,
        spot_run_id=spot_run_id,
        contracts_run_id=contracts_run_id,
        options_run_id=options_run_id,
        moneyness=moneyness,
        legs=legs,
        sample_interval_minutes=sample_interval_minutes,
    ), name=f"catch-up-{instrument}")

    return created


async def _run_catch_up_chain(
    *,
    instrument: str,
    from_date: str,
    to_date: str,
    eff_chunk_days: int,
    spot_run_id: str,
    contracts_run_id: Optional[str],
    options_run_id: Optional[str],
    moneyness: List[str],
    legs: List[str],
    sample_interval_minutes: int,
) -> None:
    """Sequential catch-up worker: spot -> contracts -> option candles.

    Each stage updates its own warehouse_runs doc. A failed/empty earlier stage
    short-circuits the option stage with a recorded reason so it never raises the
    misleading "Index candles missing" error from a race.
    """
    db = get_db()

    # Stage 1: spot ingest (await to completion).
    try:
        await run_upstox_index_ingest_job(spot_run_id, instrument, from_date, to_date, eff_chunk_days)
    except Exception as exc:
        log.exception("catch-up spot ingest failed for %s", instrument)
        await db.warehouse_runs.update_one(
            {"id": spot_run_id},
            {"$set": {"status": "failed", "error": str(exc)[:300],
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        await _fail_remaining_catch_up(db, contracts_run_id, options_run_id, "spot_ingest_failed")
        return

    if contracts_run_id is None:
        return  # spot-only mode

    # Confirm spot candles actually landed before continuing; Upstox returns
    # empty for the in-progress day, so a 0-candle result is a legitimate skip.
    spot_doc = await db.warehouse_runs.find_one({"id": spot_run_id}, {"_id": 0, "candles_added": 1, "total_fetched": 1})
    if not spot_doc or int(spot_doc.get("total_fetched") or 0) <= 0:
        await _fail_remaining_catch_up(db, contracts_run_id, options_run_id, "no_spot_candles_fetched")
        return

    # Stage 2: sync current option contracts (covers current + upcoming expiry).
    await db.warehouse_runs.update_one(
        {"id": contracts_run_id},
        {"$set": {"status": "running", "progress_pct": 10, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    try:
        items = await upstox_client.fetch_option_contracts(instrument)
        contract_result = await upsert_option_contracts(db, items)
        await db.warehouse_runs.update_one(
            {"id": contracts_run_id},
            {"$set": {
                "status": "ok", "progress_pct": 100,
                "fetched_contracts": len(items),
                "upserted": int(contract_result.get("upserted") or contract_result.get("inserted") or 0),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
    except Exception as exc:
        log.exception("catch-up contract sync failed for %s", instrument)
        await db.warehouse_runs.update_one(
            {"id": contracts_run_id},
            {"$set": {"status": "failed", "error": str(exc)[:300],
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        await _fail_remaining_catch_up(db, None, options_run_id, "contract_sync_failed")
        return

    # Stage 3: build the option plan (now that spot + contracts exist) and fetch.
    try:
        opt_req = OptionWarehousePlanReq(
            underlying=instrument,
            from_date=from_date,
            to_date=to_date,
            expiry_policy="next_available",
            moneyness=moneyness,
            legs=legs,
            sample_interval_minutes=sample_interval_minutes,
            max_contracts=2000,
            fetch_missing_only=True,
        )
        preview = await _build_option_warehouse_preview(opt_req)
        chunk_days = int(preview.get("chunk_guidance", {}).get("chunk_days") or 5)
        await db.warehouse_runs.update_one(
            {"id": options_run_id},
            {"$set": {"status": "running", "progress_pct": 5, "chunk_days": chunk_days,
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        await run_option_warehouse_fetch_job(
            options_run_id, preview, fetch_missing_only=True, chunk_days=chunk_days,
        )
    except HTTPException as exc:
        await db.warehouse_runs.update_one(
            {"id": options_run_id},
            {"$set": {"status": "failed", "error": str(exc.detail)[:300],
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception as exc:
        log.exception("catch-up option fetch failed for %s", instrument)
        await db.warehouse_runs.update_one(
            {"id": options_run_id},
            {"$set": {"status": "failed", "error": str(exc)[:300],
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )


async def _fail_remaining_catch_up(db, contracts_run_id, options_run_id, reason: str) -> None:
    """Mark not-yet-run catch-up stages as skipped with a reason."""
    now = datetime.now(timezone.utc).isoformat()
    for run_id in (contracts_run_id, options_run_id):
        if run_id:
            await db.warehouse_runs.update_one(
                {"id": run_id},
                {"$set": {"status": "skipped", "reason": reason, "progress_pct": 100, "updated_at": now}},
            )


async def _hygiene_submit_spot(instrument: str, from_date: str, to_date: str, chunk_days: int) -> str:
    """Submit a spot ingest as a background task and return the run_id."""
    db = get_db()
    if instrument.upper() not in upstox_client.INSTRUMENT_KEYS:
        raise ValueError(f"Unsupported instrument: {instrument}")
    guidance = chunk_guidance_for_index(from_date, to_date, chunk_days)
    eff_chunk_days = int(guidance["chunk_days"])
    run_id = str(_uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": run_id, "instrument": instrument.upper(),
        "source": "data_hygiene", "kind": "spot",
        "started_at": timestamp, "updated_at": timestamp, "status": "queued",
        "from_date": from_date, "to_date": to_date,
        "days": guidance["calendar_days"], "chunk_days": eff_chunk_days,
        "chunk_mode": guidance["mode"],
        "total_chunks": guidance["estimated_api_calls"],
        "completed_chunks": 0, "progress_pct": 0,
        "total_fetched": 0, "candles_added": 0, "candles_updated": 0,
        "matched_existing": 0, "failed_chunks": [],
    }
    await db.warehouse_runs.insert_one(doc)
    asyncio.create_task(run_upstox_index_ingest_job(run_id, instrument.upper(), from_date, to_date, eff_chunk_days))
    return run_id


async def _hygiene_submit_contracts(instrument: str, from_date: str, to_date: str) -> str:
    """Submit an expired-contract backfill as a background task and return a run_id.

    The backfill helper creates its own warehouse_runs row. We pre-stamp it with
    source='data_hygiene' for filterability by writing a placeholder linked-by-instrument,
    then let the helper insert its real row separately. Both rows show up in
    warehouse_runs and the user can correlate by instrument + start time.
    """
    from app.expired_contract_backfill import backfill_expired_option_contracts
    inst = instrument.upper()
    if inst not in upstox_client.INSTRUMENT_KEYS:
        raise ValueError(f"Unsupported instrument: {instrument}")
    db = get_db()

    run_id = str(_uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    await db.warehouse_runs.insert_one({
        "id": run_id, "instrument": inst,
        "source": "data_hygiene", "kind": "contracts",
        "started_at": timestamp, "updated_at": timestamp, "status": "queued",
        "from_date": from_date, "to_date": to_date,
        "progress_pct": 0,
        "note": "Tracker row. The helper will create its own detailed row at the same time.",
    })

    async def _run():
        try:
            result = await backfill_expired_option_contracts(
                db, inst,
                from_date=from_date, to_date=to_date,
                max_expiries=200,             # large window-friendly cap
                confirm_large_fetch=True,     # data hygiene scope is opt-in already
            )
            await db.warehouse_runs.update_one(
                {"id": run_id},
                {"$set": {
                    "status": str(result.get("status") or "ok"),
                    "progress_pct": 100,
                    "fetched_contracts": int(result.get("fetched_contracts") or 0),
                    "upserted": int(result.get("upserted") or 0),
                    "skipped": int(result.get("skipped") or 0),
                    "linked_helper_run_id": result.get("run_id"),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
        except Exception as exc:
            log.exception("data_hygiene contracts backfill failed for %s", inst)
            await db.warehouse_runs.update_one(
                {"id": run_id},
                {"$set": {
                    "status": "failed",
                    "error": str(exc)[:300],
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
            )

    asyncio.create_task(_run())
    return run_id


async def _hygiene_submit_option_candles(action: Dict[str, Any]) -> str:
    """Compute the option warehouse plan for the action and submit the fetch job."""
    inst = str(action["instrument"]).upper()
    if inst not in upstox_client.INSTRUMENT_KEYS:
        raise ValueError(f"Unsupported instrument: {inst}")
    # Build a synthetic OptionWarehousePlanReq so we can reuse the existing planner path
    req = OptionWarehousePlanReq(
        underlying=inst,
        from_date=action["from_date"],
        to_date=action["to_date"],
        expiry_policy="next_available",
        moneyness=list(action.get("moneyness") or HYGIENE_DEFAULT_MONEYNESS),
        legs=list(action.get("legs") or HYGIENE_DEFAULT_LEGS),
        sample_interval_minutes=int(action.get("sample_interval_minutes") or HYGIENE_DEFAULT_SAMPLE),
        max_contracts=2000,
        fetch_missing_only=True,
    )
    preview = await _build_option_warehouse_preview(req)
    items = preview.get("items", [])
    to_fetch = [i for i in items if i.get("needs_fetch")]
    chunk_days = int(preview.get("chunk_guidance", {}).get("chunk_days") or 5)
    db = get_db()
    run_id = str(_uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    await db.warehouse_runs.insert_one({
        "id": run_id, "instrument": inst,
        "source": "data_hygiene", "kind": "option_candles",
        "started_at": timestamp, "updated_at": timestamp, "status": "queued",
        "from_date": req.from_date, "to_date": req.to_date,
        "moneyness": req.moneyness, "legs": req.legs,
        "to_fetch_count": len(to_fetch),
        "chunk_days": chunk_days,
        "progress_pct": 0,
    })
    asyncio.create_task(run_option_warehouse_fetch_job(
        run_id, preview,
        fetch_missing_only=True,
        chunk_days=chunk_days,
    ))
    return run_id


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


class AutoUpdateToggleReq(BaseModel):
    enabled: bool


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


class VixIngestReq(BaseModel):
    from_date: str
    to_date: str
    chunk_days: int = 7


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
        return {"instrument": VIX_INSTRUMENT, "count": 0, "min_ts": None, "max_ts": None}
    r = rows[0]
    return {"instrument": VIX_INSTRUMENT, "count": int(r.get("count") or 0),
            "min_ts": r.get("min_ts"), "max_ts": r.get("max_ts")}


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


def _overlay_option_contract_metadata(local_contract: Optional[Dict[str, Any]], req: UpstoxOptionCandleIngestReq) -> Dict[str, Any]:
    contract = dict(local_contract or {})
    for field in ("underlying", "expiry_date", "strike", "side", "trading_symbol"):
        value = getattr(req, field)
        if value not in (None, ""):
            contract[field] = value
    if "side" in contract:
        contract["side"] = str(contract["side"]).upper()
    return contract


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


# ---------------------------------------------------------------------------
# Mount + CORS
# ---------------------------------------------------------------------------

app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
