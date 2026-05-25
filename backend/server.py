"""AlphaForge Trading Lab — FastAPI server.

All routes prefixed with /api. CORS enabled. MongoDB via Motor.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from app.db import ensure_indexes, get_db, serialize_doc
from app.indicators import precompute_all_indicators
from app.regime import classify_regime_series
from app.strategies.base import get_registry
from app.backtest import run_backtest, stat_significance
from app.walkforward import walk_forward
from app.warehouse import (
    candle_sample,
    get_coverage,
    ingest_yfinance,
    list_runs,
    load_candles_df,
)
from app.optimizer import create_job as optimizer_create_job
from app import upstox_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("alphaforge")

app = FastAPI(title="AlphaForge Trading Lab API")
api = APIRouter(prefix="/api")

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


@app.on_event("shutdown")
async def shutdown() -> None:
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


@api.get("/warehouse/candles/{instrument}")
async def warehouse_candles(instrument: str, limit: int = Query(500, le=5000)):
    rows = await candle_sample(instrument.upper(), limit=limit)
    return {"items": rows, "count": len(rows)}


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
    name: str = "Untitled Run"


@api.post("/backtest/run")
async def backtest_run(req: BacktestReq):
    registry = get_registry()
    strategy = registry.get(req.strategy_id)
    if not strategy:
        raise HTTPException(404, f"Strategy {req.strategy_id} not found")

    df = await load_candles_df(req.instrument.upper(), req.start_ts, req.end_ts)
    if df.empty or len(df) < 50:
        raise HTTPException(
            400,
            f"Insufficient candles for {req.instrument}. Ingest data first via /api/warehouse/ingest"
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
    )
    metrics = res["metrics"]

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


# ---------------------------------------------------------------------------
# Auto-Optimizer (Phase 3)
# ---------------------------------------------------------------------------

class OptimizerStartReq(BaseModel):
    instrument: str = "NIFTY"
    mode: str = "SCALP"
    strategy_id: str
    method: str = "bayesian"  # bayesian | grid | genetic
    objective: str = "risk_adjusted"  # sharpe | profit_factor | total_pnl_pts | win_rate | neg_max_dd | risk_adjusted
    n_trials: int = 200
    costs_enabled: bool = True
    pretrade_filters: Dict[str, Any] = Field(default_factory=dict)
    param_overrides: Dict[str, Any] = Field(default_factory=dict)
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    name: str = "Optimization run"


@api.post("/optimize/start")
async def optimize_start(req: OptimizerStartReq):
    if req.method not in ("bayesian", "grid", "genetic"):
        raise HTTPException(400, f"Unknown method {req.method}")
    if not get_registry().get(req.strategy_id):
        raise HTTPException(404, f"Strategy {req.strategy_id} not found")
    if not (10 <= req.n_trials <= 5000):
        raise HTTPException(400, "n_trials must be 10–5000")
    job_id = await optimizer_create_job(req.model_dump())
    return {"job_id": job_id, "status": "queued"}


@api.get("/optimize/jobs")
async def list_opt_jobs(limit: int = Query(50, le=200)):
    db = get_db()
    cur = db.optimization_jobs.find(
        {},
        {"_id": 0, "param_space": 0, "top_n_alternatives": 0, "heatmap": 0, "robustness": 0},
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


@api.post("/optimize/apply-as-preset/{job_id}")
async def apply_opt_as_preset(job_id: str, name: str = Query(...)):
    """Save the best params from an optimization as a Preset for reuse in Backtest Lab."""
    db = get_db()
    job = await db.optimization_jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("status") != "done":
        raise HTTPException(400, "Job not finished")
    config = {
        "instrument": job["instrument"],
        "mode": job.get("config", {}).get("mode", "SCALP"),
        "strategy_id": job["strategy_id"],
        "params": job.get("best_params", {}),
        "source_optimization_job": job_id,
        "optimization_method": job["method"],
        "objective": job["objective"],
    }
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
        return RedirectResponse(f"{frontend_url}?upstox_connected=1")
    except Exception as e:
        log.exception("upstox token exchange failed")
        return RedirectResponse(f"{frontend_url}?upstox_error={str(e)[:200]}")


@api.post("/upstox/disconnect")
async def upstox_disconnect():
    deleted = await upstox_client.disconnect()
    return {"disconnected": deleted}


class UpstoxIngestReq(BaseModel):
    instrument: str  # NIFTY / BANKNIFTY / SENSEX
    from_date: str   # YYYY-MM-DD (IST)
    to_date: str     # YYYY-MM-DD (IST)
    chunk_days: int = 7


@api.post("/upstox/warehouse/ingest")
async def upstox_warehouse_ingest(req: UpstoxIngestReq):
    """Fetch 1m candles from Upstox V3 and persist into the SAME warehouse used by yfinance ingest."""
    if req.instrument.upper() not in upstox_client.INSTRUMENT_KEYS:
        raise HTTPException(400, f"Unsupported instrument: {req.instrument}")

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
    })
    try:
        df = await upstox_client.fetch_historical_1m_chunked(
            req.instrument.upper(), req.from_date, req.to_date, max_days_per_call=req.chunk_days,
        )
    except Exception as e:
        log.exception("upstox ingest failed")
        await db.warehouse_runs.update_one(
            {"id": run_id},
            {"$set": {"status": "failed", "finished_at": datetime.now(timezone.utc).isoformat(), "error": str(e)[:500]}},
        )
        return {"run_id": run_id, "status": "failed", "error": str(e)[:500]}

    if df.empty:
        await db.warehouse_runs.update_one(
            {"id": run_id},
            {"$set": {"status": "empty", "finished_at": datetime.now(timezone.utc).isoformat(), "candles_added": 0}},
        )
        return {"run_id": run_id, "status": "empty", "candles_added": 0}

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
    return {"run_id": run_id, "status": "ok", "candles_added": inserted, "candles_updated": updated, "total_fetched": len(df)}


@api.get("/upstox/expiries/{instrument}")
async def upstox_expiries(instrument: str):
    \"\"\"Phase 4c prep: list expiry dates for an underlying (Upstox Plus required).\"\"\"
    try:
        items = await upstox_client.fetch_expiries(instrument)
        return {"items": items}
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
