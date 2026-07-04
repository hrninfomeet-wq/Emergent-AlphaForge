"""Research routes: strategies, profiles, backtests, presets, optimizer/WFO.

Moved verbatim from backend/server.py (quality-hardening Slice C).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
import uuid as _uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.db import get_db, serialize_doc
from app.indicators import precompute_all_indicators
from app.regime import classify_regime_series
from app.strategies.base import get_registry
from app.backtest import run_backtest, stat_significance
from app.option_warehouse_jobs import run_option_warehouse_fetch_job
from app.preset_execution import execution_from_option_config
from app.walkforward import walk_forward
from app.warehouse import load_candles_df
from app.optimizer import (
    create_job as optimizer_create_job,
    resume_optimization as optimizer_resume_job,
)
from app.wfo import create_wfo_job, resume_wfo_job
from app import upstox_client

from app.runtime import (
    _audit_and_fill_backtest_data,
    _build_option_warehouse_preview,
    _option_preflight_report,
    _run_paired_option_backtest,
    _ts_ms_to_ist_date_str,
)
from app.option_contract_store import upsert_option_contracts
from app.expired_contract_backfill import backfill_expired_option_contracts

from app.schemas import (
    BacktestReq,
    OptimizerStartReq,
    OptionWarehousePlanReq,
    PresetSaveBody,
    ProfileSave,
    WfoStartReq,
)

api = APIRouter()
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pre-trade profiles
# ---------------------------------------------------------------------------

@api.get("/profiles")
async def list_profiles():
    db = get_db()
    cur = db.pretrade_profiles.find({}, {"_id": 0}).sort("name", 1)
    rows = await cur.to_list(length=100)
    return {"items": rows}


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


async def _run_preflight_ingest_chain(
    run_id: str,
    plan_req: OptionWarehousePlanReq,
    *,
    sync_contracts: bool,
) -> None:
    """Background chain for the backtest preflight ingest.

    Mirrors the data-hygiene catch-up stages: (optionally) sync option-contract
    metadata first — current contracts AND expired-in-window weeklies — THEN
    build the missing-only fetch plan, so contracts that were unknown a moment
    ago produce fetch tasks instead of surviving as missing_contract gaps.
    Any stage failure is recorded on the warehouse_runs row (the panel polls it).
    """
    db = get_db()

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    try:
        if sync_contracts:
            await db.warehouse_runs.update_one(
                {"id": run_id},
                {"$set": {"status": "running", "stage": "contracts",
                          "progress_pct": 5, "updated_at": _now()}},
            )
            items = await upstox_client.fetch_option_contracts(plan_req.underlying)
            await upsert_option_contracts(db, items)
            await backfill_expired_option_contracts(
                db, plan_req.underlying,
                from_date=plan_req.from_date, to_date=plan_req.to_date,
                max_expiries=200, confirm_large_fetch=True,
            )
        preview = await _build_option_warehouse_preview(plan_req)
        chunk_days = int(preview.get("chunk_guidance", {}).get("chunk_days") or 5)
        await db.warehouse_runs.update_one(
            {"id": run_id},
            {"$set": {"stage": "option_candles", "chunk_days": chunk_days,
                      "updated_at": _now()}},
        )
        await run_option_warehouse_fetch_job(
            run_id, preview, fetch_missing_only=True, chunk_days=chunk_days,
        )
    except Exception as exc:
        log.exception("backtest preflight ingest chain failed (run %s)", run_id)
        await db.warehouse_runs.update_one(
            {"id": run_id},
            {"$set": {"status": "failed", "error": str(exc)[:300],
                      "updated_at": _now()}},
        )


@api.post("/backtest/option-preflight")
async def backtest_option_preflight(req: BacktestReq, ingest_missing: bool = Query(False)):
    """Pre-run check: does the option warehouse cover the signals this config
    would generate? Returns a would-pair coverage report. With ingest_missing=1
    and Upstox connected, submits a background chain that (when contracts are
    missing) syncs contract metadata first, then fetches the missing candles
    for ATM/ITM1/OTM1 plus the configured moneyness. The panel polls the
    returned run_id and re-checks automatically when the run finishes."""
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
            # Band always covers the default ATM±1 plus whatever moneyness this
            # config actually trades — otherwise OTM2/OTM3/ITM2 setups ingest
            # contracts the backtest never uses and coverage cannot converge.
            band = ["atm", "itm1", "otm1"]
            cfg_m = str(getattr(req.option_backtest, "moneyness", "") or "").lower()
            if cfg_m and cfg_m not in band:
                band.append(cfg_m)
            plan_req = OptionWarehousePlanReq(
                underlying=report["instrument"],
                from_date=_ts_ms_to_ist_date_str(req.start_ts),
                to_date=_ts_ms_to_ist_date_str(req.end_ts),
                expiry_policy="next_available",
                moneyness=band,
                legs=["CE", "PE"],
                sample_interval_minutes=1,
                max_contracts=2000,
                fetch_missing_only=True,
            )
            sync_contracts = report["missing_contract"] > 0
            run_id = str(_uuid.uuid4())
            ts = datetime.now(timezone.utc).isoformat()
            await get_db().warehouse_runs.insert_one({
                "id": run_id, "instrument": report["instrument"],
                "source": "backtest_preflight", "kind": "option_candles",
                "started_at": ts, "updated_at": ts, "status": "queued",
                "from_date": plan_req.from_date, "to_date": plan_req.to_date,
                "moneyness": band, "legs": plan_req.legs,
                "sync_contracts": sync_contracts, "progress_pct": 0,
            })
            asyncio.create_task(_run_preflight_ingest_chain(
                run_id, plan_req, sync_contracts=sync_contracts,
            ))
            report["ingest"] = {"status": "started", "run_id": run_id,
                                "moneyness": band, "contracts_stage": sync_contracts}
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


async def run_backtest_job(run_id: str, req: BacktestReq) -> None:
    """Background worker for POST /backtest/start. Mirrors backtest_run, but runs
    the CPU-heavy compute OFF the event loop via asyncio.to_thread (the Phase-1
    fix — a long backtest no longer freezes every other request, matching the
    optimizer which already offloads). Writes status + result onto the run doc
    that /backtest/start inserted up front, so a failure leaves a visible record
    instead of nothing."""
    db = get_db()
    try:
        strategy = get_registry().get(req.strategy_id)
        if not strategy:
            await db.backtest_runs.update_one({"id": run_id}, {"$set": {
                "status": "failed", "error": f"Strategy {req.strategy_id} not found"}})
            return
        data_audit = await _audit_and_fill_backtest_data(req)
        df = await load_candles_df(req.instrument.upper(), req.start_ts, req.end_ts)
        if df.empty or len(df) < 50:
            await db.backtest_runs.update_one({"id": run_id}, {"$set": {
                "status": "failed",
                "error": f"Insufficient candles for {req.instrument}. Ingest data first via /api/warehouse/ingest."}})
            return
        params = strategy.merged_params(req.params)

        # All CPU-bound work (indicators + spot backtest + optional walk-forward)
        # runs inside ONE worker thread so the event loop stays responsive.
        def _compute():
            de = precompute_all_indicators(df, params)
            de["regime"] = classify_regime_series(de)
            r = run_backtest(
                de, strategy, params,
                instrument=req.instrument.upper(), costs_enabled=req.costs_enabled,
                pretrade_filters=req.pretrade_filters,
                trade_window_start=req.trade_window_start, trade_window_end=req.trade_window_end,
            )
            w = None
            if req.walkforward and len(de) >= 200:
                w = walk_forward(
                    de, strategy, params,
                    instrument=req.instrument.upper(), costs_enabled=req.costs_enabled,
                    pretrade_filters=req.pretrade_filters, train_pct=req.train_pct, n_folds=req.n_folds,
                    trade_window_start=req.trade_window_start, trade_window_end=req.trade_window_end,
                )
            rd = {str(k): int(v) for k, v in de["regime"].value_counts().to_dict().items()}
            return r, w, rd, int(len(de))

        res, wf, regime_dist, candle_count = await asyncio.to_thread(_compute)
        metrics = res["metrics"]
        option_result = await _run_paired_option_backtest(req, res["trades"])
        sig = stat_significance(metrics["trade_count"], metrics["win_rate"], metrics.get("profit_factor"))

        await db.backtest_runs.update_one({"id": run_id}, {"$set": {
            "params_applied": params,
            "metrics": metrics,
            "trades": res["trades"],
            "equity_curve": res["equity_curve"],
            "walkforward": wf,
            "significance": sig,
            "candle_count": candle_count,
            "regime_distribution": regime_dist,
            "signal_funnel": res["signal_funnel"],
            "data_audit": data_audit,
            "option_backtest": option_result,
            "status": "done",
            "progress": 100,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }})
    except Exception as exc:  # mark the doc failed so the client sees a result, not a hang
        log.exception("backtest job %s failed: %s", run_id, exc)
        try:
            await db.backtest_runs.update_one({"id": run_id}, {"$set": {
                "status": "failed", "error": str(exc)[:500]}})
        except Exception:
            pass


@api.post("/backtest/start")
async def backtest_start(req: BacktestReq):
    """Fire-and-forget backtest. Inserts the run doc immediately with status
    'running', launches the worker, and returns {run_id, status} instantly so the
    client polls GET /backtest/runs/{id} instead of holding one long request
    (which used to hit the 60s client timeout and could double-run on retry).
    The legacy synchronous POST /backtest/run is kept for scripts."""
    strategy = get_registry().get(req.strategy_id)
    if not strategy:
        raise HTTPException(404, f"Strategy {req.strategy_id} not found")
    # Submit-time overlay validation. The async path runs the engine (and its
    # in-worker validate) in a background task, so without this a bad overlay
    # becomes a failed run instead of a clean 400. Mirror the in-worker backstop
    # (runtime.py): option_backtest.exit_controls/daily_caps are pydantic models ->
    # .model_dump() before the dict-based validator; gate on enabled so an inert
    # overlay under enabled=False stays byte-identical (the worker returns early
    # before validating when disabled).
    ob = req.option_backtest
    if ob.enabled and (ob.exit_controls or ob.daily_caps):
        from app.exit_controls import validate_exit_risk_config
        errs = validate_exit_risk_config(
            ob.exit_controls.model_dump() if ob.exit_controls else None,
            ob.daily_caps.model_dump() if ob.daily_caps else None,
            costs_on=bool((ob.cost_config or {}).get("enabled")),
            option_exec_on=(ob.exit_mode == "option_levels"),
        )
        if errs:
            raise HTTPException(400, "; ".join(errs))
    run_id = str(uuid.uuid4())
    db = get_db()
    await db.backtest_runs.insert_one({
        "id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "name": req.name,
        "config": req.model_dump(),
        "instrument": req.instrument.upper(),
        "strategy_id": req.strategy_id,
        "status": "running",
        "progress": 0,
    })
    asyncio.create_task(run_backtest_job(run_id, req))
    return {"run_id": run_id, "status": "queued"}


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
    # Fix-C: attach the trust verdict (compute-on-read; self-contained, no evidence
    # needed). Never break the read — omit the scorecard if it can't compute.
    try:
        from app.deployment_quality import evaluate_source_quality
        _nt = doc.get("n_trials")
        evidence = {"n_trials": _nt} if _nt else None
        doc["quality"] = evaluate_source_quality(doc, evidence=evidence)
    except Exception:
        pass
    return serialize_doc(doc)


@api.delete("/backtest/runs/{run_id}")
async def delete_backtest_run(run_id: str):
    db = get_db()
    res = await db.backtest_runs.delete_one({"id": run_id})
    return {"deleted": res.deleted_count}


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
    # Keep deployment references intact: deployments resolve their source by
    # preset NAME (readiness/quality lookups would 404 after a rename).
    ref = await db.strategy_deployments.update_many(
        {"source_type": "preset", "source_id": name},
        {"$set": {"source_id": target, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"ok": True, "name": target, "deployments_updated": int(ref.modified_count)}


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
    sc = req.survival_config
    if sc and sc.enabled:
        from app.survival_validate import validate_survival_request
        cap = float(((req.option_config or {}).get("sizing_config") or {}).get("capital", 200_000) or 200_000)
        err = validate_survival_request(
            enabled=True, evaluation_mode=req.evaluation_mode,
            costs_enabled=req.costs_enabled, capital=cap, ruin_floor=sc.ruin_floor,
            max_drawdown_pct=sc.max_drawdown_pct, max_ror_pct=sc.max_ror_pct,
        )
        if err:
            raise HTTPException(400, err)
    oc = req.option_config or {}
    if oc.get("exit_controls") or oc.get("daily_caps"):
        from app.exit_controls import validate_exit_risk_config
        errs = validate_exit_risk_config(
            oc.get("exit_controls"), oc.get("daily_caps"),
            costs_on=bool(req.costs_enabled),
            option_exec_on=(req.evaluation_mode == "option_rerank"))
        if errs:
            raise HTTPException(400, "; ".join(errs))
    job_id = await optimizer_create_job(req.model_dump())
    return {"job_id": job_id, "status": "queued"}


@api.get("/optimize/jobs")
async def list_opt_jobs(limit: int = Query(50, le=1000)):
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
    # Project `id` (always present) alongside `kind`: a regular (non-WFO) job has no
    # `kind` field, so {"_id":0,"kind":1} alone returns an EMPTY dict for a job that
    # DOES exist. Check `is None` (genuine miss), not falsiness, or a real job 404s.
    doc = await db.optimization_jobs.find_one({"id": job_id}, {"_id": 0, "id": 1, "kind": 1})
    if doc is None:
        raise HTTPException(404, "Job not found")
    if doc.get("kind") == "wfo":
        ok = await resume_wfo_job(job_id)
    else:
        ok = await optimizer_resume_job(job_id)
    if not ok:
        raise HTTPException(400, "Job cannot be resumed (not paused/interrupted/failed, or missing config)")
    return {"ok": True, "status": "running"}


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
        "source": "optimizer",  # explicit origin tag for the Saved Presets page
        "source_optimization_job": job_id,
        "source_job_kind": job.get("kind") or "single",
        "optimization_method": job["method"],
        "objective": job["objective"],
    }
    # Carry the execution policy the result was validated under (option re-rank
    # or option-aware WFO), so the preset is the full deployable artifact:
    # Backtest Lab re-applies it on load and the deployment form prefills from it.
    # Overlay the survival-chosen exit_controls/daily_caps so a strategy whose
    # survival DEPENDED on the overlay deploys WITH it (not bare).
    _oc = dict(((job.get("config") or {}).get("option_config")) or {})
    if job.get("best_exit_controls") is not None:
        _oc["exit_controls"] = job.get("best_exit_controls")
    if job.get("best_daily_caps") is not None:
        _oc["daily_caps"] = job.get("best_daily_caps")
    execution = execution_from_option_config(_oc)
    if execution:
        config["execution"] = execution
    await db.presets.update_one(
        {"name": name},
        {"$set": {"name": name, "config": config, "saved_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return {"ok": True, "preset_name": name}
