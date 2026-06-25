"""AlphaForge Trading Lab — FastAPI server.

All routes prefixed with /api. CORS enabled. MongoDB via Motor.

Slice C (2026-06-13): this module is now only the app factory — request
models live in app/schemas.py, shared helpers/singletons in app/runtime.py,
and the routes in app/routers/*. Route registration order is preserved.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import asyncio
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import ensure_indexes, get_db
from app.strategies.base import get_registry
from app.option_coverage_cache import get_option_coverage_cached
from app.upstox_stream import DEFAULT_STREAM_MODE
from app.warehouse_autoupdate import daily_autoupdate_loop
from app import upstox_client

from app.runtime import (
    DEFAULT_PROFILES,
    _autoupdate_compute_plan,
    _autoupdate_connection_status,
    _autoupdate_execute_plan,
    _default_stream_instrument_keys,
    _deployment_evaluator_loop,
    _topup_vix,
    _trigger_autoupdate,
    live_candle_roller,
    live_exit_monitor,
    live_position_guard,
    live_startup_recovery,
    upstox_stream_manager,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("alphaforge")

app = FastAPI(title="AlphaForge Trading Lab API")
api = APIRouter(prefix="/api")


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
                await live_exit_monitor.start()
        else:
            log.info("Upstox not connected at startup; skipping WS auto-start")
    except Exception as exc:
        log.warning(f"Upstox WS auto-start skipped: {exc}")

    # Live software exit guard — starts unconditionally (reads the BROKER position
    # book, not the Upstox stream). It no-ops when the guard registry is empty /
    # outside market hours, and only TRANSMITS a square when LIVE_GUARD_ARMED=1.
    try:
        await live_position_guard.start()
        log.info("Live position guard started (offline-first; armed=%s)",
                 __import__("os").environ.get("LIVE_GUARD_ARMED", "0"))
    except Exception as exc:
        log.warning(f"Live position guard start skipped: {exc}")

    # One-shot live recovery (non-blocking, best-effort): adopt orphaned orders
    # (resume_pending) + re-attach the software guard to open broker positions left
    # unwatched by a restart. Skips when the broker isn't connected.
    asyncio.create_task(live_startup_recovery(), name="live-startup-recovery")

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
            pre_run_fn=_topup_vix,  # keep India VIX current on the daily timer too
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
    try:
        await live_exit_monitor.stop()
    except Exception as exc:
        log.warning("live_exit_monitor.stop() failed: %s", exc)
    try:
        await live_position_guard.stop()
    except Exception as exc:
        log.warning("live_position_guard.stop() failed: %s", exc)
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
# Mount + CORS — group routers in first-appearance order of the original
# server.py. No cross-group literal-vs-param conflicts exist (verified by
# the Slice C first-match probe), so matching behavior is unchanged.
# ---------------------------------------------------------------------------

from app.routers import broker, deployments, journals, live_broker, research, strategies_admin, warehouse  # noqa: E402

api.include_router(research.api)
api.include_router(strategies_admin.api)
api.include_router(warehouse.api)
api.include_router(journals.api)
api.include_router(deployments.api)
api.include_router(broker.api)
api.include_router(live_broker.api)

app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
