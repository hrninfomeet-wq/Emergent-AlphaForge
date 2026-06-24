"""Shared runtime for the AlphaForge API: singletons, constants, route helpers.

Moved verbatim from backend/server.py (quality-hardening Slice C).
Import direction: routers -> runtime -> app business modules. This module
never imports server.py or the routers.
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import HTTPException

from app.db import get_db
from app.instruments import canonical_instrument_key
from app.chunking import chunk_guidance_for_index, chunk_guidance_for_options
from app.indicators import precompute_all_indicators
from app.regime import classify_regime_series
from app.strategies.base import get_registry
from app.backtest import run_backtest
from app.option_backtest import simulate_paired_option_trades
from app.dte import compute_dte, normalize_dte_filter
from app.vix import VIX_INSTRUMENT, vix_instrument_key, annotate_trades_with_vix
from app.option_data_planner import build_option_warehouse_plan
from app.option_warehouse_jobs import run_option_warehouse_fetch_job
from app.market_header import DEFAULT_ITEMS
from app.options_universe import select_contract_for_signal
from app.paper_auto import mark_open_deployment_trades
from app.upstox_index_ingest import persist_index_candles_bulk, run_upstox_index_ingest_job
from app.upstox_stream import DEFAULT_STREAM_MODE, UpstoxMarketStreamManager
from app.live_candle_roller import LiveCandleRoller
from app.live_exit_monitor import LiveExitMonitor
from app.live.live_position_guard import (
    LivePositionGuard,
    get_registry as get_live_monitor_registry,
)
from app.live_option_universe import build_live_option_universe, radius_for_deployments
from app.deployment_evaluator import evaluate_active_deployments
from app.data_hygiene import (
    DEFAULT_INSTRUMENTS as HYGIENE_DEFAULT_INSTRUMENTS,
    DEFAULT_LEGS as HYGIENE_DEFAULT_LEGS,
    DEFAULT_MONEYNESS as HYGIENE_DEFAULT_MONEYNESS,
    DEFAULT_SAMPLE_INTERVAL_MIN as HYGIENE_DEFAULT_SAMPLE,
    build_band_fetch_plan,
    compute_hygiene_plan,
    default_scope_start,
    execute_hygiene_plan,
    record_broker_empty_pairs,
)
from app.warehouse_autoupdate import run_autoupdate_once
from app.paper_squareoff import is_square_off_due, square_off_open_paper_trades
from app.warehouse import audit_integrity, load_candles_df, persist_candles_df
from app import upstox_client
from app.option_candles import persist_option_candles_df
from app.option_contract_store import upsert_option_contracts

from app.schemas import BacktestReq, OptionWarehousePlanReq, UpstoxOptionCandleIngestReq

log = logging.getLogger("alphaforge")


# India VIX backtest baseline start (user-chosen). Auto-update tops up VIX from
# the last stored date, falling back to this when the warehouse has no VIX yet.
VIX_BASELINE_START = "2025-12-29"


upstox_stream_manager = UpstoxMarketStreamManager()


live_candle_roller = LiveCandleRoller(
    stream_manager=upstox_stream_manager,
    db_factory=get_db,
    persister=persist_index_candles_bulk,
)

live_exit_monitor = LiveExitMonitor(
    db_factory=get_db,
    tick_lookup_factory=lambda: upstox_stream_manager.latest_tick_map().get,
    mark_fn=mark_open_deployment_trades,
)


# ---------------------------------------------------------------------------
# Live software exit guard (margin-free SL/TP/trailing) — replaces the always-
# margin-rejected resting broker SL. OFFLINE-FIRST: the guard runs + detects +
# LOGS breaches, but only TRANSMITS a real square when LIVE_GUARD_ARMED=1, so the
# operator can validate it tracks correctly before arming real auto-exits.
# ---------------------------------------------------------------------------

def _live_guard_armed() -> bool:
    import os
    return os.environ.get("LIVE_GUARD_ARMED", "0").strip().lower() in ("1", "true", "yes", "on")


async def _live_token_doc() -> Optional[Dict[str, Any]]:
    try:
        from app.live.flattrade_token import DEFAULT_USER_ID
        doc = await get_db().live_broker_tokens.find_one(
            {"user": DEFAULT_USER_ID, "broker": "flattrade"}
        )
        return doc or None
    except Exception:
        return None


async def _live_guard_client_factory():
    """Build a FlattradeClient from the stored token, or None if not connected."""
    doc = await _live_token_doc()
    if not doc or not doc.get("jKey"):
        return None
    try:
        from app.live.flattrade_client import FlattradeClient
        return FlattradeClient(jKey=doc["jKey"], uid=doc.get("uid", ""), actid=doc.get("actid", doc.get("uid", "")))
    except Exception as exc:  # pragma: no cover
        logging.getLogger(__name__).warning("guard client build failed: %s", exc)
        return None


async def _live_guard_square_fn(client, position, *, reason):
    """Margin-safe square via auto_square.square_position — GATED by LIVE_GUARD_ARMED.

    When NOT armed (default), it logs the intended square and returns a dry-run
    result WITHOUT transmitting, so the operator can confirm the guard would have
    squared the right position at the right level before enabling real auto-exits.
    """
    if not _live_guard_armed():
        logging.getLogger(__name__).warning(
            "LIVE GUARD (dry-run): WOULD square %s reason=%s netqty=%s lp=%s "
            "— set LIVE_GUARD_ARMED=1 to transmit",
            position.get("tsym"), reason, position.get("netqty"), position.get("lp"),
        )
        return {"squared": False, "dry_run": True, "reason": reason, "would_square": True}
    from app.live.auto_square import square_position
    doc = await _live_token_doc()
    uid = (doc or {}).get("uid", "")
    actid = (doc or {}).get("actid", uid)
    return await square_position(client, position, reason=reason, band_pct=1.0, uid=uid, actid=actid)


async def _live_guard_overall_provider():
    """Return the saved overall-controls config (basket SL/target/trailing) for the
    guard's basket evaluation, or None if unavailable."""
    try:
        from app.live.overall_settings_store import default_store
        return await default_store("overall").get_config()
    except Exception:
        return None


live_position_guard = LivePositionGuard(
    registry=get_live_monitor_registry(),
    client_factory=_live_guard_client_factory,
    square_fn=_live_guard_square_fn,
    overall_provider=_live_guard_overall_provider,
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
    # New-bar gate state: only (re)evaluate strategies when the live-candle roller
    # has flushed a NEW closed 1-min spot bar — entries then fire ~2-3s after close
    # (vs the old fixed minute+10s), and never on the still-forming bucket.
    last_bar_ts = 0
    EVAL_POLL_SECONDS = 2.0
    while True:
        try:
            await asyncio.sleep(EVAL_POLL_SECONDS)

            # Skip outside NSE market hours (Mon-Fri, 09:15-15:30 IST)
            ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            today_ist = ist_now.strftime("%Y-%m-%d")
            if ist_now.weekday() >= 5:
                continue
            t = ist_now.time()
            if t < _time(9, 15) or t >= _time(15, 30):
                continue

            # 15:00 IST square-off is TIME-based + safety-critical, so it runs on
            # EVERY cycle (~2s) — never gated behind a fresh bar, so positions are
            # flattened on time even if the candle feed stalls near the cutoff.
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

            # New-bar gate. Exits are owned by LiveExitMonitor (~1.5s) — this loop
            # only journals signals + auto-opens entries, once per fresh bar.
            latest = await db.candles_1m.find_one(
                {"instrument": "NIFTY"}, {"_id": 0, "ts": 1}, sort=[("ts", -1)])
            latest_ts = int((latest or {}).get("ts") or 0)
            if latest_ts <= last_bar_ts:
                continue
            last_bar_ts = latest_ts

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
                await _auto_follow_option_stream(min_radius=OPTION_CHAIN_BASELINE_RADIUS)

            # Keep the baseline ATM-band option universe subscribed so the option
            # chain + paper marks always have fresh premiums. Idempotent: restarts
            # only when the band drifts. Runs once per fresh bar (~once/min).
            stream_follow = await _auto_follow_option_stream(min_radius=OPTION_CHAIN_BASELINE_RADIUS)
            if stream_follow.get("restarted"):
                log.info("option stream auto-follow (market-hours baseline): %s", stream_follow)
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
    """Compute a hygiene plan over the default rolling 9-month scope."""
    return await compute_hygiene_plan(
        get_db(),
        start_date=None,  # rolling 9-month window (band-complete target)
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


async def _run_paired_option_backtest(req: BacktestReq, spot_trades: List[Dict[str, Any]], validate: bool = True) -> Optional[Dict[str, Any]]:
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
        # Candles are stored under the canonical 2-part key; selected contract
        # docs may carry dated 3-part keys (root cause #3) — query both forms.
        candle_query: Dict[str, Any] = {"instrument_key": {"$in": _both_key_forms(sorted(selected_keys))}}
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

        present_keys = {canonical_instrument_key(str(row.get("instrument_key"))) for row in candle_rows}
        missing_keys = sorted(k for k in selected_keys if canonical_instrument_key(str(k)) not in present_keys)
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

    if validate and (config.exit_controls or config.daily_caps):
        from app.exit_controls import validate_exit_risk_config
        errs = validate_exit_risk_config(
            config.exit_controls.model_dump() if config.exit_controls else None,
            config.daily_caps.model_dump() if config.daily_caps else None,
            costs_on=bool((config.cost_config or {}).get("enabled")),
            option_exec_on=(config.exit_mode == "option_levels"))
        if errs:
            raise HTTPException(400, "; ".join(errs))

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
        exit_controls=config.exit_controls.model_dump() if config.exit_controls else None,
        daily_caps=config.daily_caps.model_dump() if config.daily_caps else None,
    )
    _trades = result.get("trades") or []
    result["skipped_trades"] = [t for t in _trades if t.get("status") == "SKIPPED_DAILY_CAP"]
    result["trades"] = [t for t in _trades if t.get("status") != "SKIPPED_DAILY_CAP"]
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
            {"instrument_key": {"$in": _both_key_forms(list(needed.keys()))},
             "ts": {"$gte": min(all_ts) - entry_age_ms, "$lte": max(all_ts)}},
            {"_id": 0, "instrument_key": 1, "ts": 1},
        ).sort("ts", 1).to_list(length=2000000)
        for r in rows:
            key_ts_index.setdefault(canonical_instrument_key(str(r["instrument_key"])), []).append(int(r["ts"]))
    import bisect
    missing_keys_set = set()
    for pt in per_trade:
        if pt["status"] != "needs_candle":
            continue
        key = pt["key"]
        ts_list = key_ts_index.get(canonical_instrument_key(str(key)), [])
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
        # Always keep EVERY open paper trade's contract subscribed, even if its
        # strike has drifted out of the ATM band — else the exit monitor loses its
        # premium feed and a stop could blow past un-monitored.
        open_keys = await db.paper_trades.distinct("instrument_key", {"status": "OPEN"})
        option_keys = list(dict.fromkeys([*option_keys, *(str(k) for k in open_keys if k)]))
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


async def _set_deployment_status(deployment_id: str, status: str) -> Dict[str, Any]:
    db = get_db()
    doc = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Deployment not found")
    doc["status"] = status
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.strategy_deployments.replace_one({"id": deployment_id}, doc, upsert=False)
    return doc


# Manual research-signal creation, lifecycle transitions, the approval flow
# (approve / skip / mark-blocked), and manual deploy-to-paper were retired on
# 2026-06-12 (user decision): deployments journal and auto-trade their own
# signals; nothing requires manual approval. Old journaled signals remain
# readable through GET /signals and /signals/enriched.


_TRADES_SORT_FIELDS = {"updated_at", "created_at", "closed_at", "realized_pnl", "entry_price", "exit_price", "mfe_value", "mae_value"}


_TRADES_CSV_COLUMNS = [
    "created_at", "deployment_name", "strategy_id", "instrument", "trading_symbol",
    "direction", "lots", "quantity", "entry_price", "exit_price", "exit_reason",
    "closed_at", "realized_pnl", "unrealized_pnl", "status",
]


# In-memory OAuth state store (per-process). For multi-instance prod, switch to Redis.
_OAUTH_STATES: Dict[str, float] = {}


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


def _ist_market_bounds_ms(from_date: str, to_date: str) -> tuple[int, int]:
    start = pd.Timestamp(f"{from_date} 09:15", tz="Asia/Kolkata")
    end = pd.Timestamp(f"{to_date} 15:30", tz="Asia/Kolkata")
    if start > end:
        raise ValueError("from_date must be before or equal to to_date")
    return int(start.tz_convert("UTC").value // 10**6), int(end.tz_convert("UTC").value // 10**6)


def _option_chunk_guidance(req: OptionWarehousePlanReq, contract_count: int) -> Dict[str, Any]:
    return chunk_guidance_for_options(req.from_date, req.to_date, contract_count, req.chunk_days)


def _both_key_forms(instrument_keys: List[str]) -> List[str]:
    """Each key in its stored AND canonical form — candles are persisted under
    the canonical 2-part key, but planner items may carry dated 3-part keys
    from expired-sourced contract docs (instruments.canonical_instrument_key)."""
    return sorted({k for key in instrument_keys for k in (str(key), canonical_instrument_key(str(key)))})


async def _option_candle_counts(db: Any, instrument_keys: List[str], start_ts: int, end_ts: int) -> Dict[str, int]:
    if not instrument_keys:
        return {}
    pipeline = [
        {"$match": {"instrument_key": {"$in": _both_key_forms(instrument_keys)}, "ts": {"$gte": int(start_ts), "$lte": int(end_ts)}}},
        {"$group": {"_id": "$instrument_key", "count": {"$sum": 1}}},
    ]
    counts: Dict[str, int] = {}
    async for doc in db.options_1m.aggregate(pipeline):
        ck = canonical_instrument_key(str(doc["_id"]))
        counts[ck] = counts.get(ck, 0) + int(doc.get("count", 0) or 0)
    # Items look up by their own (possibly dated) key — mirror the canonical
    # totals onto every requested form.
    for key in instrument_keys:
        counts.setdefault(str(key), counts.get(canonical_instrument_key(str(key)), 0))
    return counts


async def _option_candle_date_counts(db: Any, instrument_keys: List[str], start_ts: int, end_ts: int) -> Dict[str, Dict[str, int]]:
    if not instrument_keys:
        return {}
    counts: Dict[str, Dict[str, int]] = {}
    pipeline = [
        {"$match": {"instrument_key": {"$in": _both_key_forms(instrument_keys)}, "ts": {"$gte": int(start_ts), "$lte": int(end_ts)}}},
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
        key = canonical_instrument_key(str(doc.get("_id", {}).get("key") or ""))
        date_str = str(doc.get("_id", {}).get("date") or "")
        if not key or not date_str:
            continue
        per_key = counts.setdefault(key, {})
        per_key[date_str] = per_key.get(date_str, 0) + int(doc.get("count", 0) or 0)
    for key in instrument_keys:
        skey = str(key)
        if skey not in counts:
            ck = canonical_instrument_key(skey)
            if ck in counts:
                counts[skey] = counts[ck]
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

    # Stage 3: band-exact option fill over the FULL rolling hygiene window (not
    # just the catch-up days). The fetch is driven by the same completeness
    # band the hygiene plan judges against (build_band_fetch_plan ->
    # missing_band_pairs), so one catch-up run is a complete self-heal: the new
    # sessions' band pairs AND any wick-edge gaps left in earlier days are
    # requested in the same pass, while broker-proven-empty pairs are excluded
    # by the option_known_empty ledger. (The old path here re-derived a
    # close-sampled ATM±moneyness preview, which silently skipped wick strikes
    # the band demands — gaps accumulated daily until a manual "Fill gaps".
    # `moneyness`/`sample_interval_minutes` are kept in the signature for API
    # compatibility but the band needs neither.)
    try:
        window_start = default_scope_start()
        plan = await build_band_fetch_plan(db, instrument, window_start, to_date, legs=legs)
        chunk_days = 5
        await db.warehouse_runs.update_one(
            {"id": options_run_id},
            {"$set": {"status": "running", "progress_pct": 5, "chunk_days": chunk_days,
                      "band": True, "from_date": window_start,
                      "missing_pairs": plan.get("missing_pairs", 0),
                      "to_fetch_count": len(plan.get("items") or []),
                      "unresolved_contracts": (plan.get("unresolved_contracts") or [])[:50],
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        await run_option_warehouse_fetch_job(
            options_run_id, plan, fetch_missing_only=True, chunk_days=chunk_days,
        )
        recorded = await record_broker_empty_pairs(db, instrument, plan, options_run_id)
        if recorded:
            log.info("catch-up %s: %d band pair(s) ledgered as broker-empty", instrument, recorded)
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
    """Compute the option warehouse plan for the action and submit the fetch job.

    The fetch is driven by the SAME completeness band the plan/UI reports
    against (`data_hygiene.build_band_fetch_plan` → `missing_band_pairs`), so
    every (day, expiry, side, strike) judged missing is requested exactly —
    closing the old gap where the per-day ATM±moneyness preview never fetched
    intraday-wick / band-edge strikes the band demanded (permanent "degraded").
    """
    inst = str(action["instrument"]).upper()
    if inst not in upstox_client.INSTRUMENT_KEYS:
        raise ValueError(f"Unsupported instrument: {inst}")
    db = get_db()
    plan = await build_band_fetch_plan(
        db,
        inst,
        action["from_date"],
        action["to_date"],
        legs=list(action.get("legs") or HYGIENE_DEFAULT_LEGS),
    )
    return await submit_band_fetch_run(inst, plan)


async def submit_band_fetch_run(instrument: str, plan: Dict[str, Any]) -> str:
    """Create the tracked warehouse_runs doc for a prebuilt band fetch plan and
    fire the fetch+reconcile in the background. Returns the run_id."""
    db = get_db()
    chunk_days = 5
    run_id = str(_uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    await db.warehouse_runs.insert_one({
        "id": run_id, "instrument": instrument.upper(),
        "source": "data_hygiene", "kind": "option_candles",
        "started_at": timestamp, "updated_at": timestamp, "status": "queued",
        "from_date": plan["from_date"], "to_date": plan["to_date"],
        "band": True,
        "to_fetch_count": len(plan.get("items") or []),
        "missing_pairs": plan.get("missing_pairs", 0),
        "unresolved_contracts": (plan.get("unresolved_contracts") or [])[:50],
        "chunk_days": chunk_days,
        "progress_pct": 0,
    })
    asyncio.create_task(_band_fill_with_reconcile(run_id, instrument.upper(), plan, chunk_days=chunk_days))
    return run_id


async def _band_fill_with_reconcile(run_id: str, instrument: str, plan: Dict[str, Any], *, chunk_days: int = 5) -> None:
    """Run a band fetch job, then ledger requested-but-still-absent pairs as
    broker-empty (only pairs whose fetch did not fail count as proven empty)."""
    db = get_db()
    await run_option_warehouse_fetch_job(run_id, plan, fetch_missing_only=True, chunk_days=chunk_days)
    try:
        recorded = await record_broker_empty_pairs(db, instrument, plan, run_id)
        if recorded:
            log.info("band fill %s: %d pair(s) ledgered as broker-empty", instrument, recorded)
            await db.warehouse_runs.update_one(
                {"id": run_id},
                {"$set": {"broker_empty_recorded": recorded,
                          "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
    except Exception:
        log.exception("broker-empty reconcile failed for %s run %s", instrument, run_id)


def _overlay_option_contract_metadata(local_contract: Optional[Dict[str, Any]], req: UpstoxOptionCandleIngestReq) -> Dict[str, Any]:
    contract = dict(local_contract or {})
    for field in ("underlying", "expiry_date", "strike", "side", "trading_symbol"):
        value = getattr(req, field)
        if value not in (None, ""):
            contract[field] = value
    if "side" in contract:
        contract["side"] = str(contract["side"]).upper()
    return contract
