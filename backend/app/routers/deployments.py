"""Deployment routes: create/manage deployments, readiness, metrics, evaluation.

Moved verbatim from backend/server.py (quality-hardening Slice C).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, StrictBool

from app.db import get_db, serialize_doc
from app.strategies.base import get_registry
from app.strategy_deployments import build_deployment_doc
from app.live_friction import FrictionConfig
from app.strategy_source_hash import hash_strategy_source, build_repin_update
from app.deployment_quality import evaluate_source_quality, QualityThresholds
from app.forward_metrics import (
    build_arm_advisories,
    compute_forward_metrics_for_deployment,
    compute_forward_metrics_for_deployments,
    premium_edge_verdict_advisory,
)
from app.deployment_evaluator import evaluate_active_deployments, evaluate_deployment_on_close
from app.deployment_preflight import compute_data_realism
from app.nse_calendar import market_status
from app.paper_squareoff import square_off_open_paper_trades

from app.runtime import (
    _auto_follow_option_stream,
    _ist_day_bounds_ms_full,
    _load_deployment_source,
    _set_deployment_status,
    upstox_stream_manager,
)

from app.schemas import DeploymentCreateReq

api = APIRouter()


# ---------------------------------------------------------------------------
# Live deployment control surface (strategy-deploy-to-live)
#
# arm/disarm/stop/status for a deployment's REAL-money auto-placing. These are
# the only routes that flip a deployment into the armed (live-allowed) state.
#
# The seams below are module-level so tests can monkeypatch them with fakes
# (no real broker / Mongo / engine touched host-side), matching the pattern in
# routers/live_broker.py. Production wires them lazily to the real singletons.
# ---------------------------------------------------------------------------

async def _live_get_token_doc():
    """Return the stored Flattrade token doc (raises HTTPException 400 if absent).

    Imported lazily from live_broker so this router never hard-depends on it at
    import time; tests monkeypatch this seam directly."""
    from app.routers.live_broker import _get_token_doc
    return await _get_token_doc()


def _live_l3_engine():
    """Return the LiveEngine singleton (real or fail-closed). Tests patch this."""
    from app.routers.live_broker import _l3_engine
    return _l3_engine()


def _live_registry():
    """Return the process-wide LiveMonitorRegistry. Tests patch this."""
    from app.live.live_position_guard import get_registry
    return get_registry()


async def _live_square_position(client, position, *, reason, **kw):
    """Square one live position via the SAME margin-safe exit path used by the
    manual square / kill switch (auto_square.square_position). Tests patch this."""
    from app.live.auto_square import square_position
    return await square_position(client, position, reason=reason, **kw)


def _live_autoplace_armed() -> bool:
    """True iff LIVE_AUTOPLACE_ARMED is set affirmative (the executor transmit gate)."""
    return os.environ.get("LIVE_AUTOPLACE_ARMED", "0").strip().lower() in ("1", "true", "yes", "on")


def _live_guard_armed() -> bool:
    """The software exit guard ALWAYS transmits — the LIVE_GUARD_ARMED env gate was
    removed by explicit user decision. Kept as a named constant-function so the
    deployment live-status payload keeps a stable `guard_armed` field."""
    return True


async def _broker_connected() -> bool:
    """True iff a Flattrade token is stored (the broker is connected)."""
    try:
        await _live_get_token_doc()
        return True
    except HTTPException:
        return False
    except Exception:
        return False


def _is_drift_paused(deployment: Dict[str, Any]) -> bool:
    """True iff the deployment carries the source-drift pause marker."""
    return str(deployment.get("drift_reason") or "") == "strategy_source_drift"


def _premium_edge_verdict_advisory_for(deployment: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Phase 5B B8: informational-only advisory for premium_momentum deployments that
    opted into multi-leg execution (leg_mode=="both" or lazy_enabled). Resolves the
    deployment's merged params via the strategy registry (same pattern as
    deployment_evaluator.py) so a deployment created pre-5B (no leg_mode stored) still
    reads the plugin-schema default honestly. NEVER read by the arm gating path
    (arm/disarm decisions are made above this line in both call sites) — advisory
    only, per S19's existing non-blocking convention."""
    strategy_id = str(deployment.get("strategy_id") or "")
    if strategy_id != "premium_momentum":
        return None
    strategy_obj = get_registry().get(strategy_id)
    if strategy_obj is None:
        return None
    merged_params = strategy_obj.merged_params(deployment.get("params") or {})
    return premium_edge_verdict_advisory(strategy_id, merged_params)


def _utcnow() -> datetime:
    """Current UTC time. A seam so tests can pin 'now' for the arm-window check."""
    return datetime.now(timezone.utc)


async def _square_live_positions_for_deployment(
    deployment_id: str, *, reason: str
) -> List[str]:
    """Flatten THIS deployment's registered live positions via the margin-safe
    square path, removing each from the guard registry first (so a slow square is
    never re-issued). Returns the list of squared tsyms. Best-effort: a per-
    position square failure is swallowed (the registry entry is still removed)."""
    reg = _live_registry()
    targets = [e for e in reg.snapshot() if str(e.get("deployment_id") or "") == str(deployment_id)]
    if not targets:
        return []
    # Resolve a broker client + uid/actid (best-effort). This is a USER-INITIATED
    # flatten over the SAME margin-safe exit path as the manual square / kill switch,
    # so it transmits the exit directly — it is NOT auto-place-env-gated (the user is
    # explicitly squaring their own positions, exactly like the manual square button).
    # In offline-first / dry-run mode nothing was ever auto-placed, so the registry is
    # empty and there is nothing to square. A per-position square failure is swallowed
    # but the registry entry is still removed.
    client = None
    uid = actid = ""
    try:
        from app.routers.live_broker import _get_client
        client = await _get_client()
        token = await _live_get_token_doc()
        uid = token.get("uid", "")
        actid = token.get("actid", uid)
    except Exception:
        pass  # square_position tolerates a None/limited client
    from app.live.close_loop import should_journal_close, close_live_trade
    squared: List[str] = []
    for entry in targets:
        position = dict(entry.get("position") or {})
        position.setdefault("tsym", entry.get("tsym"))
        result: Dict[str, Any] = {"squared": False}
        try:
            result = await _live_square_position(
                client, position, reason=reason, uid=uid, actid=actid) or {"squared": False}
        except Exception:
            pass
        squared.append(entry.get("tsym"))
        # Drop the guard entry + cancel the resting OCO ONLY after a CONFIRMED square.
        # square_position now serializes per-tsym exits and returns squared=False when
        # another path is already flattening this scrip (reason=exit_in_flight_elsewhere)
        # — in that case we must NOT strip the OCO or stop watching, or a competing
        # square that then fails would leave the position naked and unwatched. On
        # squared=False the entry + OCO stay intact (the other path / the guard handles
        # it; the broker OCO remains the PC-down backstop).
        if result.get("squared") and not result.get("dry_run"):
            reg.remove(entry["id"])
            if entry.get("oco_al_id") and client is not None and hasattr(client, "cancel_oco"):
                try:
                    await client.cancel_oco(entry["oco_al_id"])
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(
                        "deployment-stop cancel_oco failed for %s (al_id=%s)",
                        entry.get("tsym"), entry.get("oco_al_id"))
        # Close-loop: journal realized P&L for this deployment position, but ONLY
        # on a real fill (should_journal_close skips a failed/dry-run square and
        # manual-source entries). Linked by the entry norenordno; never raises.
        try:
            if should_journal_close(entry, result):
                await close_live_trade(
                    get_db(), norenordno=entry.get("id"),
                    exit_price=position.get("lp"), exit_reason=reason)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "deployment-stop close-loop failed for %s", entry.get("tsym"))
    return squared


def _live_today_counters(rows: List[Dict[str, Any]], now_utc: datetime) -> Dict[str, Any]:
    """Today-IST order count, lots, and realized P&L for a deployment's live_trades."""
    from app.deployment_kill_switch import IST, _float, _ist_date, daily_realized_summary
    today = now_utc.astimezone(IST).date().isoformat()
    todays = [r for r in rows if _ist_date(r.get("created_at")) == today]
    orders = len(todays)
    lots = sum(int(_float(r.get("lots"))) for r in todays)
    realized = daily_realized_summary(rows, today)["net"]
    return {"orders": orders, "lots": lots, "realized_pnl": realized}


class _LiveEnableBody(BaseModel):
    """Deploy-time live config. This body is the SOLE writer of the live risk caps
    and the PC-down OCO catastrophe band — the fields the governor and the resting
    OCO read on every entry. lots / max_lots_per_day / max_concurrent are REQUIRED:
    a live deployment without caps would sail past `_live_caps_configured`'s
    allow-all fast path and trade unbounded.

    `confirm` survives the arm-ceremony removal deliberately. It is not a per-session
    gate — enabling live is a ONE-TIME act per deployment that persists across
    sessions — it only stops a stray API call from flipping a deployment to real
    money by accident.
    """
    lots: int
    max_lots_per_day: int
    max_concurrent: int
    daily_loss_cap: Optional[float] = None
    catastrophe_stop_pct: Optional[float] = None
    catastrophe_target_pct: Optional[float] = None
    confirm: StrictBool = False


#: Back-compat alias — some tests/importers reference the old name.
_LiveArmBody = _LiveEnableBody


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
    if strategy_id:
        # local import avoids a circular dependency between the two routers
        from app.routers.strategies_admin import is_retired
        if await is_retired(strategy_id):
            raise HTTPException(409, f"Strategy {strategy_id} is retired — un-retire it before deploying")
    # Merge explicit kill-switch fields into the risk dict (only when provided).
    kill_switch_cfg = {
        k: v for k, v in {
            "max_consecutive_losses": req.max_consecutive_losses,
            "daily_loss_cutoff_pct": req.daily_loss_cutoff_pct,
            "max_open_paper_trades": req.max_open_paper_trades,
        }.items() if v is not None
    }
    # Paper account realism: per-deployment capital constraint + wizard-time
    # lots override (same field the Paper caps editor writes post-deploy).
    if req.lots_override is not None and not (1 <= int(req.lots_override) <= 100):
        raise HTTPException(400, "lots_override must be 1..100")
    capital_cfg = None
    if req.capital_amount is not None:
        try:
            _amt = float(req.capital_amount)
        except (TypeError, ValueError):
            raise HTTPException(400, "capital_amount must be a number")
        if _amt <= 0:
            raise HTTPException(400, "capital_amount must be > 0")
        _basis = str(req.capital_basis or "fixed").lower()
        if _basis not in ("fixed", "cumulative"):
            raise HTTPException(400, "capital_basis must be 'fixed' or 'cumulative'")
        capital_cfg = {"amount": _amt, "basis": _basis}
    elif req.capital_basis is not None:
        raise HTTPException(400, "capital_basis requires capital_amount")
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
                **({"capital": capital_cfg} if capital_cfg is not None else {}),
                **({"lots_override": int(req.lots_override)}
                   if req.lots_override is not None else {}),
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
    """Return session-gated forward metrics for one deployment, plus the NON-blocking
    live-arm advisories the ARM dialog renders (S19)."""
    db = get_db()
    deployment = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not deployment:
        raise HTTPException(404, "Deployment not found")
    fwd = await compute_forward_metrics_for_deployment(db, deployment)
    out = serialize_doc(fwd)
    out["arm_advisories"] = build_arm_advisories(fwd)
    _pm_advisory = _premium_edge_verdict_advisory_for(deployment)
    if _pm_advisory:
        out["arm_advisories"].append(_pm_advisory)
    return out


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


class _PaperCapsBody(BaseModel):
    """Paper-deployment risk caps (Live-deploy parity). Every field optional;
    null clears the cap. daily_caps mirrors exit_controls.DailyCapsConfig.
    capital is the honest capital constraint {"amount": float, "basis":
    "fixed"|"cumulative"} enforced at paper-trade entry (paper_capital)."""
    lots_override: Optional[int] = None
    max_concurrent: Optional[int] = None
    daily_caps: Optional[Dict[str, Any]] = None
    capital: Optional[Dict[str, Any]] = None


@api.put("/deployments/{deployment_id}/paper-caps")
async def set_paper_caps(deployment_id: str, body: _PaperCapsBody):
    """Set per-deployment paper trading caps: fixed lots per signal (overrides
    the pinned sizing replay), max concurrent open positions, and the soft
    daily governor config (max loss / target / max trades per day)."""
    db = get_db()
    dep = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not dep:
        raise HTTPException(404, "Deployment not found")
    if str(dep.get("mode") or "").lower() != "paper":
        raise HTTPException(400, "paper-caps apply to paper deployments only")
    if body.lots_override is not None and not (1 <= int(body.lots_override) <= 100):
        raise HTTPException(400, "lots_override must be 1..100")
    if body.max_concurrent is not None and not (1 <= int(body.max_concurrent) <= 50):
        raise HTTPException(400, "max_concurrent must be 1..50")
    if body.daily_caps is not None:
        from app.exit_controls import DailyCapsConfig
        try:
            DailyCapsConfig.from_dict(body.daily_caps)  # validate shape
        except Exception as exc:
            raise HTTPException(400, f"invalid daily_caps: {exc}") from exc
    if body.capital is not None:
        from app.paper_capital import parse_capital_config
        if parse_capital_config(body.capital) is None:
            raise HTTPException(
                400, "invalid capital: expected {amount > 0, basis: fixed|cumulative}")
    risk = dict(dep.get("risk") or {})
    for key, val in (("lots_override", body.lots_override),
                     ("max_concurrent", body.max_concurrent),
                     ("daily_caps", body.daily_caps),
                     ("capital", body.capital)):
        if val is None:
            risk.pop(key, None)
        else:
            risk[key] = val
    await db.strategy_deployments.update_one(
        {"id": deployment_id},
        {"$set": {"risk": risk, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    doc = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    return serialize_doc(doc)


@api.post("/deployments/{deployment_id}/pause")
async def pause_deployment(deployment_id: str):
    return serialize_doc(await _set_deployment_status(deployment_id, "PAUSED"))


@api.post("/deployments/{deployment_id}/resume")
async def resume_deployment(deployment_id: str):
    db = get_db()
    deployment = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not deployment:
        raise HTTPException(404, "Deployment not found")
    sid = str(deployment.get("strategy_id") or "")
    # local import avoids a circular dependency between the two routers
    from app.routers.strategies_admin import is_retired
    if sid and await is_retired(sid):
        raise HTTPException(409, f"Strategy {sid} is retired — un-retire it before resuming")
    doc = await _set_deployment_status(deployment_id, "ACTIVE")
    stream = await _auto_follow_option_stream()
    return serialize_doc({**doc, "option_stream": stream})


@api.post("/deployments/stop-all")
async def stop_all_deployments():
    """Stop ALL trading: square off every open paper position, pause every ACTIVE
    deployment, AND disarm + flatten every armed live deployment. Open positions
    close at the live tick price when the market is open, else at a flagged
    estimate."""
    db = get_db()
    summaries = await square_off_open_paper_trades(
        db,
        latest_tick_lookup=upstox_stream_manager.latest_tick_map().get,
        reason="manual_stop_all",
    )
    # --- Flatten + de-live every LIVE deployment ---
    # Selector is `mode == "live"`, NOT the old {"risk.live.armed": True}. That field
    # no longer exists, and a stale selector here would silently match zero documents
    # and quietly turn Stop-ALL into a no-op that flattens nothing.
    live_deps = await db.strategy_deployments.find(
        {"mode": "live"}, {"_id": 0}
    ).to_list(length=None)
    disarmed_live_ids: list = []
    for d in live_deps:
        dep_id = d["id"]
        await _square_live_positions_for_deployment(dep_id, reason="manual_stop_all")
        risk = dict(d.get("risk") or {})
        live = dict(risk.get("live") or {})
        live["disabled_at"] = datetime.now(timezone.utc).isoformat()
        live["last_block_reason"] = "manual_stop_all"
        risk["live"] = live
        await db.strategy_deployments.update_one(
            {"id": dep_id},
            {"$set": {"mode": "paper", "risk": risk,
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        disarmed_live_ids.append(dep_id)

    active = await db.strategy_deployments.find(
        {"status": "ACTIVE"}, {"_id": 0, "id": 1}
    ).to_list(length=None)
    paused_ids = [d["id"] for d in active]
    if paused_ids:
        await db.strategy_deployments.update_many(
            {"id": {"$in": paused_ids}},
            {"$set": {"status": "PAUSED", "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    return serialize_doc({
        "squared_off": summaries,
        "squared_off_count": len(summaries),
        "paused_deployment_ids": paused_ids,
        "disarmed_live_deployment_ids": disarmed_live_ids,
    })


@api.post("/deployments/{deployment_id}/stop")
async def stop_deployment(deployment_id: str):
    """Stop a paper deployment: square off ITS open positions, then pause it
    (no new entries until resume). Open positions close at the live tick price
    when the market is open, else at a flagged estimate."""
    db = get_db()
    deployment = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not deployment:
        raise HTTPException(404, "Deployment not found")
    summaries = await square_off_open_paper_trades(
        db,
        deployment_id=deployment_id,
        latest_tick_lookup=upstox_stream_manager.latest_tick_map().get,
        reason="manual_stop_square_off",
    )
    doc = await _set_deployment_status(deployment_id, "PAUSED")
    return serialize_doc({
        **doc,
        "squared_off": summaries,
        "squared_off_count": len(summaries),
    })


@api.post("/deployments/{deployment_id}/live/enable")
async def enable_deployment_live(deployment_id: str, body: _LiveEnableBody):
    """Switch this deployment to LIVE mode — real-money execution driven by the
    strategy's own entry/exit/SL/TP/trailing logic, with the resting broker OCO as
    the PC-down backstop.

    This REPLACES the old per-session arm ceremony (removed by explicit user
    decision). Enabling is a one-time act that persists across sessions: from here
    on, every confirmed signal from this deployment routes to the live sink while
    the broker is connected and it is before the 15:00 IST entry cutoff.

    The preflight chain below is the SAME chain the arm route used to run. It must
    stay here: the auto-live path re-checks none of these, so anything dropped from
    this list stops being enforced anywhere.
      - deployment exists (404);
      - status == "ACTIVE";
      - strategy not retired;
      - not source-drift-paused;
      - broker connected (a Flattrade token is stored);
      - the LiveEngine.can_trade() is True (not halted / latched);
      - confirm is the literal boolean True (StrictBool — truthy-non-True rejected).

    On pass, sets mode="live" and writes risk.live = {lots, max_lots_per_day,
    max_concurrent, daily_loss_cap, catastrophe_stop_pct/target_pct, enabled_at,
    enabled_by}. The user's lots value is stored verbatim — the executor clamps it
    to the account ceiling at place time. Response carries `autoplace_armed` (the
    env transmit gate, the one remaining master switch) plus a human `note` when it
    is False (backend dry-run-logs).

    NOTE there is deliberately NO "cannot enable after 15:00" check: unlike an arm,
    a live deployment is not scoped to the current session, so enabling in the
    evening simply means it goes live at the next session's open.
    """
    if body.confirm is not True:
        raise HTTPException(400, "Enabling live execution requires confirm=True (literal boolean).")
    if int(body.lots) < 1 or int(body.max_lots_per_day) < 1 or int(body.max_concurrent) < 1:
        raise HTTPException(
            400,
            "lots, max_lots_per_day and max_concurrent must all be >= 1 — a live "
            "deployment without caps would trade unbounded.",
        )
    db = get_db()
    deployment = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not deployment:
        raise HTTPException(404, "Deployment not found")
    if str(deployment.get("status") or "").upper() != "ACTIVE":
        raise HTTPException(400, "Deployment must be ACTIVE to enable live execution.")
    sid = str(deployment.get("strategy_id") or "")
    # local import avoids a circular dependency between the two routers
    from app.routers.strategies_admin import is_retired
    if sid and await is_retired(sid):
        raise HTTPException(409, f"Strategy {sid} is retired — un-retire it before going live.")
    if _is_drift_paused(deployment):
        raise HTTPException(400, "Deployment is paused for strategy source drift — re-pin it before going live.")
    if not await _broker_connected():
        raise HTTPException(400, "Flattrade not connected — complete OAuth before going live.")
    try:
        ok, reason = await _live_l3_engine().can_trade()
    except Exception:
        ok, reason = False, "engine_unavailable"
    if ok is not True:
        raise HTTPException(400, f"Live engine cannot trade ({reason}) — clear the halt/latch before going live.")

    now = _utcnow()
    live = {
        "lots": int(body.lots),
        "max_lots_per_day": int(body.max_lots_per_day),
        "max_concurrent": int(body.max_concurrent),
        "daily_loss_cap": (float(body.daily_loss_cap) if body.daily_loss_cap is not None else None),
        "catastrophe_stop_pct": body.catastrophe_stop_pct,
        "catastrophe_target_pct": body.catastrophe_target_pct,
        "enabled_at": now.isoformat(),
        "enabled_by": "user",
        "last_block_reason": None,
    }
    # Read-modify-write of the whole risk dict: set risk.live without clobbering
    # risk.sizing / risk.exit_controls / etc. (the FakeDB test harness applies a
    # single-level $set; production Mongo handles a full-risk $set the same way).
    # risk.live survives the arm removal as a pure CONFIG sub-doc — it no longer
    # carries any authorization field; `mode` alone authorizes.
    risk = dict(deployment.get("risk") or {})
    risk["live"] = live
    await db.strategy_deployments.update_one(
        {"id": deployment_id},
        {"$set": {"mode": "live", "risk": risk, "updated_at": now.isoformat()}},
    )
    autoplace = _live_autoplace_armed()
    out = {**live, "autoplace_armed": autoplace}
    if not autoplace:
        out["note"] = "backend will dry-run-log, not transmit real orders (LIVE_AUTOPLACE_ARMED is off)"
    # S19: arming has NO performance gate — echo the forward evidence + non-blocking
    # advisories so the operator sees whether the paper record actually supports this.
    try:
        fwd = await compute_forward_metrics_for_deployment(db, deployment)
    except Exception:
        fwd = None
    out["arm_advisories"] = build_arm_advisories(fwd)
    _pm_advisory = _premium_edge_verdict_advisory_for(deployment)
    if _pm_advisory:
        out["arm_advisories"].append(_pm_advisory)
    return serialize_doc(out)


@api.post("/deployments/{deployment_id}/live/disable")
async def disable_deployment_live(deployment_id: str):
    """Take a deployment OUT of live mode (does NOT flatten open positions).

    This is the "stop placing new real orders but leave what's open alone" action —
    the successor to disarm. The deployment reverts to `paper`, so its signals go
    back to the paper sink exactly as an unarmed live deployment used to. Open live
    positions stay registered with the guard and keep their stop/target/trail and
    the resting OCO; use /live/stop to flatten them.

    The live CONFIG (risk.live caps + catastrophe band) is retained so re-enabling
    doesn't require re-entering it.
    """
    db = get_db()
    deployment = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not deployment:
        raise HTTPException(404, "Deployment not found")
    now_iso = datetime.now(timezone.utc).isoformat()
    risk = dict(deployment.get("risk") or {})
    live = dict(risk.get("live") or {})
    live["disabled_at"] = now_iso
    live["last_block_reason"] = "manual_disable"
    risk["live"] = live
    await db.strategy_deployments.update_one(
        {"id": deployment_id},
        {"$set": {"mode": "paper", "risk": risk, "updated_at": now_iso}},
    )
    return serialize_doc({"deployment_id": deployment_id, "mode": "paper", "live": live})


@api.post("/deployments/{deployment_id}/live/stop")
async def stop_deployment_live(deployment_id: str):
    """Flatten THIS deployment's open live positions, then disarm.

    The flatten reuses the existing margin-safe exit machinery
    (auto_square.square_position) scoped to this deployment's guard-registry
    entries — it does NOT open a new place_order path. As a USER-INITIATED exit it
    transmits directly (like the manual square / kill switch); it is not gated by the
    auto-place env flag. In dry-run mode nothing was auto-placed, so there is nothing
    to square."""
    db = get_db()
    deployment = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not deployment:
        raise HTTPException(404, "Deployment not found")
    squared = await _square_live_positions_for_deployment(deployment_id, reason="manual_stop")
    now_iso = datetime.now(timezone.utc).isoformat()
    risk = dict(deployment.get("risk") or {})
    live = dict(risk.get("live") or {})
    live["disabled_at"] = now_iso
    live["last_block_reason"] = "manual_stop"
    risk["live"] = live
    # Flatten AND take it out of live AND pause it. Dropping out of live mode alone
    # would not be enough: an ACTIVE live deployment whose positions were just
    # squared would re-enter on the very next confirmed signal. status=PAUSED is the
    # authoritative stop — evaluate_all only iterates {"status": "ACTIVE"}.
    await db.strategy_deployments.update_one(
        {"id": deployment_id},
        {"$set": {"mode": "paper", "status": "PAUSED", "risk": risk, "updated_at": now_iso}},
    )
    return serialize_doc({
        "deployment_id": deployment_id,
        "squared_tsyms": squared,
        "squared_count": len(squared),
        "disabled": True,
        "paused": True,
        "live": live,
    })


async def _live_status_payload(db: Any, deployment_id: str) -> Optional[Dict[str, Any]]:
    """Build one deployment's live-status dict, or None if the deployment is absent.

    Shared by the per-id route and the batched ?ids= route so both emit the
    identical shape. Returns the UN-serialized dict; callers ``serialize_doc`` it.
    """
    deployment = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not deployment:
        return None
    live = dict((deployment.get("risk") or {}).get("live") or {})
    rows = await db.live_trades.find({"deployment_id": deployment_id}).to_list(length=None)
    today = _live_today_counters(rows, datetime.now(timezone.utc))
    reg = _live_registry()
    open_positions = []
    for e in reg.snapshot():
        if str(e.get("deployment_id") or "") != str(deployment_id):
            continue
        st = e.get("state") or {}
        open_positions.append({
            "id": e.get("id"),
            "tsym": e.get("tsym"),
            "qty": e.get("qty"),
            "entry_price": e.get("entry_price"),
            "stop_level": st.get("stop_level"),
            "target_level": st.get("target_level"),
            "seen_filled": e.get("seen_filled"),
        })

    # Most recent live-entry OUTCOME for this deployment. auto_live writes
    # signals.live_trade_error (a refused/blocked entry — e.g. stale premium,
    # throttle, broker reject) and signals.live_intended (the offline-first
    # dry-run audit), but nothing ever read them — so an armed deployment that
    # silently never places had no on-screen explanation. Surface the latest so
    # the Live strip can show WHY. Only populated when the latest signal actually
    # carries one of those fields (a paper-only latest signal → None, no chip).
    last_entry: Optional[Dict[str, Any]] = None
    try:
        # Sort by candle_ts (the bar timestamp) desc — the latest BAR's signal
        # carries the current live outcome. This rides the existing
        # (deployment_id, candle_ts) unique index for an INDEXED sort (no
        # in-memory sort / collection scan on the 10s batch poll), and is more
        # robust than updated_at (which a later unrelated write could bump).
        sig = await db.signals.find_one(
            {"deployment_id": deployment_id},
            sort=[("candle_ts", -1)],
        )
        if sig and (sig.get("live_trade_error") is not None
                    or sig.get("live_intended") is not None):
            last_entry = {
                "signal_id": sig.get("id"),
                "error": sig.get("live_trade_error"),
                "intended": sig.get("live_intended"),
                "at": sig.get("updated_at") or sig.get("created_at"),
            }
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "live_status: last-entry lookup failed for %s: %s", deployment_id, exc)

    return {
        "armed": bool(live.get("armed")),
        "armed_until": live.get("armed_until"),
        "caps": {
            "lots": live.get("lots"),
            "max_lots_per_day": live.get("max_lots_per_day"),
            "max_concurrent": live.get("max_concurrent"),
            "daily_loss_cap": live.get("daily_loss_cap"),
        },
        "today": today,
        "open_positions": open_positions,
        "last_entry": last_entry,
        "autoplace_armed": _live_autoplace_armed(),
        "guard_armed": _live_guard_armed(),
    }


# NOTE: this static 3-segment path is declared BEFORE the parametrised
# /deployments/{deployment_id}/live/status (4-seg) route. They have different
# segment counts so they cannot collide, but keeping the static one first is the
# defensive convention.
@api.get("/deployments/live/status")
async def deployments_live_status_batch(
    ids: str = Query(..., description="comma-separated deployment ids"),
):
    """Batched live status: returns ``{deployment_id: <per-id payload>}`` for many
    deployments in ONE request, so the Live Deployment strip makes a single call
    per cycle instead of one per deployment. Unknown ids are OMITTED (never 404
    the whole batch); the caller treats a missing key the same as null."""
    db = get_db()
    seen: set = set()
    id_list: List[str] = []
    for raw in ids.split(","):
        i = raw.strip()
        if i and i not in seen:
            seen.add(i)
            id_list.append(i)
    id_list = id_list[:200]  # bound the batch size
    out: Dict[str, Any] = {}
    for i in id_list:
        payload = await _live_status_payload(db, i)
        if payload is not None:
            out[i] = serialize_doc(payload)
    return out


@api.get("/deployments/{deployment_id}/live/status")
async def deployment_live_status(deployment_id: str):
    """Report a deployment's live arm state, caps, today's counters, open live
    positions (filtered to this deployment), and the two transmit gates."""
    payload = await _live_status_payload(get_db(), deployment_id)
    if payload is None:
        raise HTTPException(404, "Deployment not found")
    return serialize_doc(payload)


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
    # local import avoids a circular dependency between the two routers
    from app.routers.strategies_admin import is_retired
    if await is_retired(strategy_id):
        raise HTTPException(409, f"Strategy {strategy_id} is retired — un-retire it before re-pinning/resuming its deployment")
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
