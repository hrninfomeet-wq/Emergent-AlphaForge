"""Journal routes: dashboard summary, signals ledger, paper trades.

Moved verbatim from backend/server.py (quality-hardening Slice C).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.db import get_db, serialize_doc
from app.strategies.base import get_registry
from app.paper_trading import close_trade, mark_trade_to_market, premium_sanity_error
from app.signal_lifecycle import SignalStateError, transition_signal
from app.paper_squareoff import square_off_open_paper_trades
from app.warehouse import get_coverage

from app.runtime import (
    _ENRICHED_CSV_COLUMNS,
    _ENRICHED_SORT_FIELDS,
    _TRADES_CSV_COLUMNS,
    _TRADES_SORT_FIELDS,
    _csv_response,
    _ist_day_bounds_ms_full,
    _ts_ms_to_ist_date_str,
    upstox_stream_manager,
)

from app.schemas import PaperCloseReq, PaperMarkReq, SignalsPurgeReq, TradesPurgeReq
from app.paper_open_positions import build_open_positions
from app import paper_analytics

api = APIRouter()


# ---------------------------------------------------------------------------
# Account config (starting capital)
# ---------------------------------------------------------------------------

_DEFAULT_STARTING_CAPITAL = 200_000.0


async def _get_starting_capital(db) -> float:
    doc = await db.app_settings.find_one({"key": "paper_account"}, {"_id": 0})
    if doc and doc.get("starting_capital") is not None:
        try:
            return float(doc["starting_capital"])
        except (TypeError, ValueError):
            pass
    return _DEFAULT_STARTING_CAPITAL


class AccountConfigReq(BaseModel):
    starting_capital: float
    # Account-wide capital ceiling (paper_capital): when enforce_capital is on,
    # the SUM of open premium across ALL paper deployments is gated against
    # starting_capital at entry time. Omitted fields keep their stored value.
    enforce_capital: Optional[bool] = None
    capital_basis: Optional[str] = None  # fixed | cumulative


@api.get("/paper/account-config")
async def get_paper_account_config():
    db = get_db()
    doc = await db.app_settings.find_one({"key": "paper_account"}, {"_id": 0}) or {}
    return {
        "starting_capital": await _get_starting_capital(db),
        "enforce_capital": bool(doc.get("enforce_capital")),
        "capital_basis": str(doc.get("capital_basis") or "fixed"),
    }


@api.put("/paper/account-config")
async def set_paper_account_config(req: AccountConfigReq):
    if req.starting_capital <= 0:
        raise HTTPException(400, "starting_capital must be > 0")
    if req.capital_basis is not None and str(req.capital_basis).lower() not in ("fixed", "cumulative"):
        raise HTTPException(400, "capital_basis must be 'fixed' or 'cumulative'")
    db = get_db()
    updates: Dict[str, Any] = {"key": "paper_account",
                               "starting_capital": float(req.starting_capital)}
    if req.enforce_capital is not None:
        updates["enforce_capital"] = bool(req.enforce_capital)
    if req.capital_basis is not None:
        updates["capital_basis"] = str(req.capital_basis).lower()
    await db.app_settings.update_one(
        {"key": "paper_account"}, {"$set": updates}, upsert=True,
    )
    doc = await db.app_settings.find_one({"key": "paper_account"}, {"_id": 0}) or {}
    return {
        "starting_capital": float(req.starting_capital),
        "enforce_capital": bool(doc.get("enforce_capital")),
        "capital_basis": str(doc.get("capital_basis") or "fixed"),
    }


# ---------------------------------------------------------------------------
# Account analytics + strategy stats
# ---------------------------------------------------------------------------

@api.get("/paper/analytics")
async def paper_account_analytics():
    db = get_db()
    starting = await _get_starting_capital(db)
    closed = await db.paper_trades.find(
        {"status": "CLOSED"},
        {"_id": 0, "realized_pnl": 1, "closed_at": 1, "updated_at": 1,
         "instrument": 1, "entry_price": 1, "quantity": 1, "status": 1},
    ).to_list(length=100000)
    open_rows = await db.paper_trades.find({"status": "OPEN"}, {"_id": 0, "events": 0}).to_list(length=500)
    from app.runtime import upstox_stream_manager
    live = build_open_positions(open_rows, latest_tick_lookup=upstox_stream_manager.latest_tick_map().get)
    live_by_id = {p["id"]: p for p in live["items"]}
    for r in open_rows:
        lp = live_by_id.get(r.get("id"))
        if lp is not None:
            r["unrealized_pnl"] = lp["unrealized_pnl"]
    out = paper_analytics.build_account_analytics(closed, open_rows, starting_capital=starting)
    return serialize_doc(paper_analytics.json_safe_floats(out))


@api.get("/paper/strategy-stats")
async def paper_strategy_stats():
    db = get_db()
    rows = await db.paper_trades.find(
        {}, {"_id": 0, "strategy_id": 1, "deployment_id": 1, "status": 1,
             "realized_pnl": 1, "unrealized_pnl": 1, "created_at": 1, "closed_at": 1,
             "exit_reason": 1, "risk_amount": 1, "total_charges": 1},
    ).to_list(length=100000)
    dep_ids = sorted({str(r.get("deployment_id")) for r in rows if r.get("deployment_id")})
    names = {}
    if dep_ids:
        for d in await db.strategy_deployments.find({"id": {"$in": dep_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(length=len(dep_ids)):
            names[str(d["id"])] = str(d.get("name") or "")
    stats = paper_analytics.per_strategy_stats(rows)
    for s in stats:
        s["deployment_name"] = names.get(str(s.get("deployment_id") or ""), "")

    # --- drift enrichment (lazy imports to avoid module-load ordering issues) ---
    from app.forward_metrics import compute_forward_metrics_for_deployment  # noqa: PLC0415
    from app.routers.deployments import _gather_deployment_evidence  # noqa: PLC0415

    for s in stats:
        dep_id = s.get("deployment_id")
        if not dep_id:
            s["drift"] = {"state": "no_baseline"}
            continue
        dep = await db.strategy_deployments.find_one({"id": dep_id}, {"_id": 0})
        if not dep:
            s["drift"] = {"state": "no_baseline"}
            continue
        try:
            fm = await compute_forward_metrics_for_deployment(db, dep)
            live = {"win_rate": fm.get("win_rate"), "avg": fm.get("avg_pnl"),
                    "visible": bool((fm.get("library_gate") or {}).get("visible"))}
            cfg = dep.get("config") or {}
            evidence = await _gather_deployment_evidence(
                db,
                strategy_id=cfg.get("strategy_id") or dep.get("strategy_id") or "",
                instrument=cfg.get("instrument") or dep.get("instrument") or "",
                params=cfg.get("params") or dep.get("params") or {},
                source_doc=dep,
            )
            oe = evidence.get("option_evidence") or {}
            paired = oe.get("paired_trade_count")
            base_avg = (oe.get("net_pnl_value") / paired) if paired else None
            baseline = {"win_rate": oe.get("win_rate"), "avg": base_avg,
                        "params_match": bool(oe.get("params_match"))}
            s["drift"] = paper_analytics.drift_compare(live, baseline)
        except Exception:
            s["drift"] = {"state": "no_baseline"}

    return serialize_doc(paper_analytics.json_safe_floats({"items": stats, "count": len(stats)}))


@api.get("/paper/deployment-stats")
async def paper_deployment_stats(deployment_id: str = Query(...)):
    """Per-deployment drill-down: day/week/month/year buckets of capital,
    P&L extremes, drawdown, and peak deployed capital (see
    paper_analytics.deployment_period_stats for the exact semantics)."""
    db = get_db()
    starting = await _get_starting_capital(db)
    rows = await db.paper_trades.find(
        {"deployment_id": deployment_id},
        {"_id": 0, "status": 1, "realized_pnl": 1, "created_at": 1,
         "closed_at": 1, "updated_at": 1, "entry_price": 1, "quantity": 1},
    ).to_list(length=100000)
    out = paper_analytics.deployment_period_stats(rows, starting_capital=starting)
    out["deployment_id"] = deployment_id
    return serialize_doc(paper_analytics.json_safe_floats(out))


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
# Live signal lifecycle + paper trading foundation
# ---------------------------------------------------------------------------

@api.get("/signals")
async def list_signals(state: Optional[str] = Query(None), limit: int = Query(50, le=200)):
    q: Dict[str, Any] = {}
    if state:
        q["state"] = state.upper()
    rows = await get_db().signals.find(q, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(length=limit)
    return {"items": serialize_doc(rows), "count": len(rows)}


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
                "risk_hints", "paper_trade_id", "paper_trade_error", "paper_trade_skip",
                "tracked_for_pnl",
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


@api.get("/paper/trades")
async def list_paper_trades(
    status: Optional[str] = Query(None),
    deployment_id: Optional[str] = Query(None),
    strategy_id: Optional[str] = Query(None),
    instrument: Optional[str] = Query(None),
    direction: Optional[str] = Query(None, description="CE or PE"),
    exit_reason: Optional[str] = Query(None, description="bucket: target|manual|eod|stop|other"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD (IST), on entry time"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD (IST)"),
    sort: str = Query("-updated_at"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=500),
    format: Optional[str] = Query(None, description="csv to download"),
    include_analytics: bool = Query(False),
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

    extra: list = []
    if direction:
        extra.append({"direction": direction.upper()})
    if exit_reason:
        cond = paper_analytics.exit_reason_query(exit_reason)
        if cond is not None:
            extra.append(cond)
    q = paper_analytics.merge_conditions(q, extra)

    field = sort.lstrip("-")
    sort_direction = -1 if sort.startswith("-") else 1  # distinct from the `direction` (CE/PE) filter param
    if field not in _TRADES_SORT_FIELDS:
        field, sort_direction = "updated_at", -1

    total = await db.paper_trades.count_documents(q)
    proj = {"_id": 0} if include_analytics else {"_id": 0, "events": 0}
    rows = await db.paper_trades.find(q, proj).sort(field, sort_direction).skip(skip).limit(limit).to_list(length=limit)

    dep_ids = sorted({str(r.get("deployment_id")) for r in rows if r.get("deployment_id")})
    dep_names: Dict[str, str] = {}
    if dep_ids:
        for d in await db.strategy_deployments.find({"id": {"$in": dep_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(length=len(dep_ids)):
            dep_names[str(d.get("id"))] = str(d.get("name") or "")
    for r in rows:
        r["deployment_name"] = dep_names.get(str(r.get("deployment_id") or ""), "")

    if include_analytics:
        for r in rows:
            r["analytics"] = paper_analytics.per_trade_analytics(r)
            r.pop("events", None)

    if (format or "").lower() == "csv":
        return _csv_response(rows, _TRADES_CSV_COLUMNS, "paper_trades.csv")
    return {"items": serialize_doc(rows), "count": len(rows), "total": total, "skip": skip, "limit": limit}


@api.get("/paper/open-positions")
async def paper_open_positions():
    """Live OPEN positions: unrealized P&L from the latest tick at request time.
    Lightweight (OPEN only) so the Paper page can poll it every ~2s."""
    db = get_db()
    rows = await db.paper_trades.find({"status": "OPEN"}, {"_id": 0, "events": 0}).to_list(length=500)
    dep_ids = sorted({str(r.get("deployment_id")) for r in rows if r.get("deployment_id")})
    if dep_ids:
        names = {str(d["id"]): str(d.get("name") or "") for d in
                 await db.strategy_deployments.find({"id": {"$in": dep_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(length=len(dep_ids))}
        for r in rows:
            r["deployment_name"] = names.get(str(r.get("deployment_id") or ""), "")
    from app.runtime import upstox_stream_manager  # lazy: avoid circular import at module load
    out = build_open_positions(rows, latest_tick_lookup=upstox_stream_manager.latest_tick_map().get)
    return serialize_doc(out)


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


def _require_open(trade: Dict[str, Any]) -> None:
    """Manual mark/close act on OPEN trades only — block a late write that would
    clobber an auto-close / square-off (the auto-marker is already OPEN-guarded;
    these routes were not)."""
    status = str(trade.get("status") or "").upper()
    if status != "OPEN":
        raise HTTPException(409, f"Trade is {status or 'unknown'}, not OPEN — refresh; only open trades can be marked or closed.")


def _check_premium(trade: Dict[str, Any], price: float, override: bool) -> None:
    """Reject an implausible option premium (e.g. a fat-fingered spot level)
    unless the operator explicitly overrode it."""
    if override:
        return
    err = premium_sanity_error(trade, price)
    if err:
        raise HTTPException(400, detail={
            "code": "implausible_premium",
            "message": err,
            "reference_price": trade.get("last_price") or trade.get("entry_price"),
        })


@api.post("/paper/trades/{trade_id}/mark")
async def mark_paper_trade(trade_id: str, req: PaperMarkReq):
    db = get_db()
    trade = await db.paper_trades.find_one({"id": trade_id}, {"_id": 0})
    if not trade:
        raise HTTPException(404, "Paper trade not found")
    _require_open(trade)
    _check_premium(trade, req.last_price, req.override_sanity)
    updated = mark_trade_to_market(trade, last_price=req.last_price, auto_close_on_risk=req.auto_close_on_risk)
    # Conditional on status=OPEN so a concurrent auto-close/square-off is never
    # clobbered (matches the auto-marker's guarded write).
    res = await db.paper_trades.replace_one({"id": trade_id, "status": "OPEN"}, updated, upsert=False)
    if int(getattr(res, "matched_count", 0) or 0) != 1:
        raise HTTPException(409, "Trade was closed concurrently — refresh and retry.")
    return serialize_doc(updated)


@api.post("/paper/trades/{trade_id}/close")
async def close_paper_trade(trade_id: str, req: PaperCloseReq):
    db = get_db()
    trade = await db.paper_trades.find_one({"id": trade_id}, {"_id": 0})
    if not trade:
        raise HTTPException(404, "Paper trade not found")
    _require_open(trade)
    _check_premium(trade, req.exit_price, req.override_sanity)
    updated = close_trade(trade, exit_price=req.exit_price, reason=req.reason)
    res = await db.paper_trades.replace_one({"id": trade_id, "status": "OPEN"}, updated, upsert=False)
    if int(getattr(res, "matched_count", 0) or 0) != 1:
        raise HTTPException(409, "Trade was closed concurrently — refresh and retry.")
    if updated.get("signal_id"):
        signal = await db.signals.find_one({"id": updated["signal_id"]}, {"_id": 0})
        if signal and str(signal.get("state") or "").upper() == "ACTIVE":
            try:
                exited = transition_signal(signal, "EXITED", reason="paper_trade_closed", snapshot={"trade_id": trade_id, "realized_pnl": updated.get("realized_pnl")})
                await db.signals.replace_one({"id": signal["id"]}, exited, upsert=False)
            except SignalStateError:
                pass
    return serialize_doc(updated)
