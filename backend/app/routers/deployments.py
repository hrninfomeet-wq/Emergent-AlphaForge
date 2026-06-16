"""Deployment routes: create/manage deployments, readiness, metrics, evaluation.

Moved verbatim from backend/server.py (quality-hardening Slice C).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from app.db import get_db, serialize_doc
from app.strategies.base import get_registry
from app.strategy_deployments import build_deployment_doc
from app.live_friction import FrictionConfig
from app.strategy_source_hash import hash_strategy_source, build_repin_update
from app.deployment_quality import evaluate_source_quality, QualityThresholds
from app.forward_metrics import (
    compute_forward_metrics_for_deployment,
    compute_forward_metrics_for_deployments,
)
from app.deployment_evaluator import evaluate_active_deployments, evaluate_deployment_on_close
from app.deployment_preflight import compute_data_realism
from app.nse_calendar import market_status

from app.runtime import (
    _auto_follow_option_stream,
    _ist_day_bounds_ms_full,
    _load_deployment_source,
    _set_deployment_status,
)

from app.schemas import DeploymentCreateReq

api = APIRouter()


async def _gather_deployment_evidence(
    db: Any,
    *,
    strategy_id: str,
    instrument: str,
    params: Dict[str, Any],
    source_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Gather out-of-sample + selection-bias evidence for a deployment source.

    Shared by /deployments/readiness (display) and the quality gate (warnings)
    so the two never drift: the latest honest walk-forward (efficiency,
    consistency, option-rupee OOS net), the exact-params option-rupee evidence
    (re-rank job or option backtest), and the optimizer trial count behind the
    chosen params (the selection-bias signal for the deflated Sharpe).
    """
    strategy_id = strategy_id or ""
    instrument = (instrument or "").upper()
    params = params or {}

    # Honest-WFO evidence: latest completed walk-forward for this strategy.
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

    # Option-rupee evidence: exact-params re-rank job, else option backtest run.
    option_evidence = None
    rerank_jobs = await db.optimization_jobs.find(
        {"kind": {"$ne": "wfo"}, "strategy_id": strategy_id, "instrument": instrument,
         "status": "done", "evaluation_mode": "option_rerank"},
        {"_id": 0, "id": 1, "finished_at": 1, "best_params": 1,
         "best_option_pnl_value": 1, "rerank.ranked": {"$slice": 1}},
    ).sort("finished_at", -1).limit(25).to_list(length=25)
    for job in rerank_jobs:
        top = ((job.get("rerank") or {}).get("ranked") or [{}])[0]
        match = (job.get("best_params") or {}) == params
        if option_evidence is None or (match and not option_evidence.get("params_match")):
            # Fix-D: prefer the PROMOTED survivor's full-window with-overlay net
            # (Fix-A persists it on the job) over ranked[0] (base-config, not the survivor).
            net = job.get("best_option_pnl_value")
            if net is None:
                net = top.get("option_pnl_value")
            option_evidence = {
                "kind": "rerank", "id": job.get("id"), "at": job.get("finished_at"),
                "net_pnl_value": net,
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
                "kind": "backtest_run", "id": run.get("id"), "at": run.get("created_at"),
                "net_pnl_value": metrics.get("total_option_pnl_value"),
                "win_rate": metrics.get("win_rate"),
                "paired_trade_count": metrics.get("paired_trade_count"),
                "params_match": match,
            }
            if option_evidence is None or (match and not option_evidence.get("params_match")):
                option_evidence = candidate
            if match:
                break

    # Optimizer trial count behind the chosen params (selection-bias signal).
    n_trials = None
    job_id = None
    if source_doc:
        job_id = source_doc.get("optimization_job_id") or (source_doc.get("config") or {}).get("optimization_job_id")
    if job_id:
        job = await db.optimization_jobs.find_one(
            {"id": job_id}, {"_id": 0, "n_trials_completed": 1, "n_trials_total": 1})
        if job:
            n_trials = job.get("n_trials_completed") or job.get("n_trials_total")
    if not n_trials:
        cand = await db.optimization_jobs.find(
            {"kind": {"$ne": "wfo"}, "strategy_id": strategy_id, "instrument": instrument, "status": "done"},
            {"_id": 0, "best_params": 1, "n_trials_completed": 1, "n_trials_total": 1},
        ).sort("finished_at", -1).limit(25).to_list(length=25)
        for job in cand:
            if (job.get("best_params") or {}) == params:
                n_trials = job.get("n_trials_completed") or job.get("n_trials_total")
                break

    return {
        "strategy_id": strategy_id,
        "instrument": instrument,
        "params": params,
        "wfo": wfo_evidence,
        "option_evidence": option_evidence,
        "n_trials": int(n_trials) if n_trials else None,
    }


@api.get("/deployments")
async def list_deployments(status: Optional[str] = Query(None), limit: int = Query(50, le=200)):
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status.upper()
    rows = await get_db().strategy_deployments.find(q, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(length=limit)
    return {"items": serialize_doc(rows), "count": len(rows)}


@api.post("/deployments")
async def create_deployment(req: DeploymentCreateReq):
    db = get_db()
    source = await _load_deployment_source(db, req.source_type, req.source_id)
    # Quality gate (slice 9 + gate-rigor pass): warn but never silently allow
    # problematic backtests. The gate now also consumes out-of-sample evidence
    # (selection-bias-adjusted Sharpe over the optimizer search + option-rupee
    # OOS) so an overfit / premium-bleeding strategy is flagged, not just
    # in-sample spot stability. If any warning is present, the user must
    # acknowledge by setting acknowledged_warnings=true in the create request.
    _cfg = source.get("config") or {}
    evidence = await _gather_deployment_evidence(
        db,
        strategy_id=_cfg.get("strategy_id") or source.get("strategy_id") or "",
        instrument=_cfg.get("instrument") or source.get("instrument") or "",
        params=_cfg.get("params") or source.get("params") or {},
        source_doc=source,
    )
    quality = evaluate_source_quality(source, evidence=evidence)
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
    _risk = req.risk or {}
    if _risk.get("exit_controls") or _risk.get("daily_caps"):
        from app.exit_controls import validate_exit_risk_config
        _costs_on = bool(((req.friction or {}).get("costs") or {}).get("enabled"))
        errs = validate_exit_risk_config(
            _risk.get("exit_controls"), _risk.get("daily_caps"),
            costs_on=_costs_on, option_exec_on=True)  # deployments always pair options live
        if errs:
            raise HTTPException(400, "; ".join(errs))
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
                # Live execution-realism config, normalized to the canonical
                # FrictionConfig shape so the live close path can apply the same
                # slippage + charges the backtest used.
                **({"friction": FrictionConfig.from_dict(req.friction).to_dict()}
                   if req.friction is not None else {}),
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
    min_sharpe: Optional[float] = Query(None, description="override weak-Sharpe threshold"),
    min_trade_count: Optional[int] = Query(None, description="override low-trade-count threshold"),
    selection_bias_min_trials: Optional[int] = Query(None, description="trials before selection-bias is assessed"),
    min_deflated_sharpe: Optional[float] = Query(None, description="deflated-Sharpe warn threshold"),
):
    """Quality / acknowledgment check for a deployment source.

    Returns warnings (overfit, low trade count, weak Sharpe, missing walk-forward,
    large drawdown, plus selection-bias and option-rupee-OOS checks from the
    optimizer evidence). Never blocks creation by itself - the user must pass
    `acknowledged_warnings=true` on the create request when warnings are present.
    Thresholds are tunable via the optional query params so the user can preview
    the gate at stricter/looser settings.
    """
    db = get_db()
    source = await _load_deployment_source(db, source_type, source_id)
    cfg = source.get("config") or {}
    evidence = await _gather_deployment_evidence(
        db,
        strategy_id=cfg.get("strategy_id") or source.get("strategy_id") or "",
        instrument=cfg.get("instrument") or source.get("instrument") or "",
        params=cfg.get("params") or source.get("params") or {},
        source_doc=source,
    )
    thresholds = QualityThresholds.from_overrides(
        min_sharpe=min_sharpe,
        min_trade_count=min_trade_count,
        selection_bias_min_trials=selection_bias_min_trials,
        min_deflated_sharpe=min_deflated_sharpe,
    )
    return serialize_doc(evaluate_source_quality(source, evidence=evidence, thresholds=thresholds))


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

    evidence = await _gather_deployment_evidence(
        db, strategy_id=strategy_id, instrument=instrument, params=params, source_doc=source,
    )
    return serialize_doc({
        "source": {
            "type": source_type,
            "id": source_id,
            "strategy_id": strategy_id,
            "instrument": instrument,
            "has_execution": bool(cfg.get("execution")),
        },
        "wfo": evidence["wfo"],
        "option_evidence": evidence["option_evidence"],
        "n_trials": evidence["n_trials"],
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
            # When the evaluator last ran a 1m-close for this deployment (epoch ms,
            # the candle minute). Lets the UI show "last evaluated HH:MM" so an
            # operator can tell a live deployment from a stalled one.
            "last_evaluated_ts": d.get("last_evaluated_ts"),
        }
        items.append(item)
        totals["open_trades"] += item["today"]["open_trades"]
        totals["open_unrealized"] = round(totals["open_unrealized"] + item["today"]["open_unrealized"], 2)
        totals["realized_today"] = round(totals["realized_today"] + item["today"]["realized_pnl"], 2)
        totals["signals_today"] += sig["clean"] + sig["blocked"]
    return serialize_doc({
        "items": items,
        "totals": totals,
        "as_of_ist": ist_now.isoformat(),
        # Holiday-aware "is the market open right now?" so the page is an honest
        # live cockpit instead of leaving the operator to guess off-hours.
        "market_status": market_status(ist_now),
    })


@api.get("/deployments/{deployment_id}")
async def get_deployment(deployment_id: str):
    doc = await get_db().strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Deployment not found")
    return serialize_doc(doc)


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
