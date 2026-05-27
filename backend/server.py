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
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query
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
from app.option_data_audit import audit_option_data, clear_option_data
from app.option_data_planner import DEFAULT_LEGS, build_option_warehouse_plan
from app.option_plan_response import compact_option_plan_for_response
from app.option_coverage import get_option_coverage
from app.option_warehouse_jobs import option_fetch_tasks_from_plan, run_option_warehouse_fetch_job
from app.expired_contract_backfill import backfill_expired_option_contracts
from app.market_header import DEFAULT_ITEMS, build_market_header_snapshot
from app.options_universe import select_contract_for_signal
from app.paper_trading import close_trade, mark_trade_to_market, paper_trade_from_signal
from app.signal_lifecycle import SignalStateError, create_signal_doc, transition_signal
from app.strategy_deployments import build_deployment_doc
from app.upstox_index_ingest import run_upstox_index_ingest_job
from app.upstox_stream import DEFAULT_STREAM_MODE, UpstoxMarketStreamManager
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
    moneyness: str = "otm1"
    lots: int = 1
    entry_max_age_sec: int = 120
    exit_max_age_sec: int = 180
    auto_fetch: bool = True
    max_auto_fetch_contracts: int = 12


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
    contract_query: Dict[str, Any] = {"underlying": underlying}
    fixed_expiry_date = config.expiry_date
    if fixed_expiry_date:
        contract_query["expiry_date"] = fixed_expiry_date

    contracts = await db.option_contracts.find(contract_query, {"_id": 0}).sort([
        ("expiry_date", 1),
        ("strike", 1),
        ("side", 1),
    ]).to_list(length=10000)
    expiry_by_trade = _resolve_option_expiry_by_trade(spot_trades, contracts, fixed_expiry_date=fixed_expiry_date)

    selected_keys: set[str] = set()
    for idx, trade in enumerate(spot_trades):
        resolved_expiry = fixed_expiry_date or expiry_by_trade.get(idx)
        eligible_contracts = [
            contract
            for contract in contracts
            if not resolved_expiry or str(contract.get("expiry_date", "")) == str(resolved_expiry)
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
    }
    return result


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
    try:
        doc = build_deployment_doc(
            source_type=req.source_type,
            source_doc=source,
            name=req.name,
            mode=req.mode,
            confirmation_mode=req.confirmation_mode,
            option_moneyness=req.option_moneyness,
            pretrade_profile=req.pretrade_profile,
            risk=req.risk,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.strategy_deployments.insert_one(doc)
    return serialize_doc(doc)


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
    return serialize_doc(await _set_deployment_status(deployment_id, "ACTIVE"))


@api.post("/deployments/{deployment_id}/archive")
async def archive_deployment(deployment_id: str):
    return serialize_doc(await _set_deployment_status(deployment_id, "ARCHIVED"))


@api.get("/deployments/{deployment_id}/signals")
async def list_deployment_signals(deployment_id: str, limit: int = Query(100, le=500)):
    rows = await get_db().signals.find({"deployment_id": deployment_id}, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(length=limit)
    return {"items": serialize_doc(rows), "count": len(rows)}


@api.post("/signals")
async def create_signal(req: SignalCreateReq):
    doc = create_signal_doc(
        instrument=req.instrument,
        direction=req.direction,
        strategy_id=req.strategy_id,
        entry_price=req.entry_price,
        confidence=req.confidence,
        reasons=req.reasons,
        option_contract=req.option_contract,
        context=req.context,
    )
    await get_db().signals.insert_one(doc)
    return serialize_doc(doc)


@api.post("/signals/{signal_id}/transition")
async def transition_signal_route(signal_id: str, req: SignalTransitionReq):
    db = get_db()
    doc = await db.signals.find_one({"id": signal_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Signal not found")
    try:
        updated = transition_signal(doc, req.to_state, reason=req.reason, snapshot=req.snapshot)
    except SignalStateError as e:
        raise HTTPException(400, str(e))
    await db.signals.replace_one({"id": signal_id}, updated, upsert=False)
    return serialize_doc(updated)


def _advance_signal_for_paper(signal: Dict[str, Any]) -> Dict[str, Any]:
    state = str(signal.get("state") or "WATCHING").upper()
    if state in ("AUDITED", "EXITED", "SKIPPED"):
        raise SignalStateError(f"Cannot deploy signal in state {state}")
    updated = dict(signal)
    for target in ("FORMING", "CONFIRMED", "TRIGGERED", "ACTIVE"):
        if str(updated.get("state") or "").upper() == target:
            continue
        if target in ("FORMING", "CONFIRMED", "TRIGGERED", "ACTIVE"):
            try:
                updated = transition_signal(updated, target, reason="paper_deploy_auto_transition")
            except SignalStateError:
                continue
        if str(updated.get("state") or "").upper() == "ACTIVE":
            break
    if str(updated.get("state") or "").upper() != "ACTIVE":
        raise SignalStateError(f"Could not deploy signal from state {state}")
    return updated


@api.post("/signals/{signal_id}/paper")
async def deploy_signal_to_paper(signal_id: str, req: PaperDeployReq):
    db = get_db()
    signal = await db.signals.find_one({"id": signal_id}, {"_id": 0})
    if not signal:
        raise HTTPException(404, "Signal not found")
    try:
        active_signal = _advance_signal_for_paper(signal)
        trade = paper_trade_from_signal(
            active_signal,
            lots=req.lots,
            entry_price=req.entry_price,
            stop_price=req.stop_price,
            target_price=req.target_price,
        )
    except (SignalStateError, ValueError) as e:
        raise HTTPException(400, str(e))

    await db.paper_trades.insert_one(trade)
    active_signal["paper_trade_id"] = trade["id"]
    active_signal["updated_at"] = trade["updated_at"]
    await db.signals.replace_one({"id": signal_id}, active_signal, upsert=False)
    return {"signal": serialize_doc(active_signal), "trade": serialize_doc(trade)}


@api.get("/paper/trades")
async def list_paper_trades(status: Optional[str] = Query(None), limit: int = Query(50, le=200)):
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status.upper()
    rows = await get_db().paper_trades.find(q, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(length=limit)
    return {"items": serialize_doc(rows), "count": len(rows)}


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


def _default_stream_instrument_keys() -> List[str]:
    keys: List[str] = []
    for item in DEFAULT_ITEMS:
        if item.get("source") == "upstox" and item.get("instrument_key"):
            keys.append(str(item["instrument_key"]))
    return list(dict.fromkeys(keys))


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


class SignalCreateReq(BaseModel):
    instrument: str = "NIFTY"
    direction: str = "LONG"
    strategy_id: str = "manual_research"
    entry_price: float
    confidence: float = 50
    reasons: List[str] = Field(default_factory=list)
    option_contract: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)


class SignalTransitionReq(BaseModel):
    to_state: str
    reason: str = ""
    snapshot: Dict[str, Any] = Field(default_factory=dict)


class PaperDeployReq(BaseModel):
    lots: int = 1
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None


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
    mode: str = "shadow"
    confirmation_mode: str = "1m_close"
    option_moneyness: List[str] = Field(default_factory=lambda: ["atm"])
    pretrade_profile: str = "Balanced"
    risk: Dict[str, Any] = Field(default_factory=dict)


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
async def local_option_coverage(underlying: Optional[str] = Query(None)):
    """Summarize stored option candles by date for heatmap visibility."""
    if underlying and underlying.upper() not in upstox_client.INSTRUMENT_KEYS:
        raise HTTPException(400, f"Unsupported instrument: {underlying}")
    instruments = await get_option_coverage(get_db(), underlying=underlying)
    return {"instruments": serialize_doc(instruments), "source": "local_option_coverage"}


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
