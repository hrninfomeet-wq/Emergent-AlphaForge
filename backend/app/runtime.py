"""Shared runtime for the AlphaForge API: singletons, constants, route helpers.

Moved verbatim from backend/server.py (quality-hardening Slice C).
Import direction: routers -> runtime -> app business modules. This module
never imports server.py or the routers.
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from datetime import datetime, time as dtime, timedelta, timezone
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
from app.option_backtest import simulate_paired_option_trades, preflight_trade_pairs
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

# Hard row cap for a single option-candle load in a paired-option backtest.
# Candles are loaded oldest-first, so a load that HITS this cap silently drops
# the NEWEST candles — every trade in the most recent period would then report
# missing_entry_candle (0% pairing) with no other signal. The cap exists only as
# a memory backstop; it is set well above any realistic multi-strike multi-year
# range, and a cap-hit is always warned + surfaced (never silent). Mirrors the
# 4,000,000-row cap in optimizer._option_rerank and wfo.
OPTION_CANDLE_LOAD_CAP = 4_000_000


upstox_stream_manager = UpstoxMarketStreamManager()


live_candle_roller = LiveCandleRoller(
    stream_manager=upstox_stream_manager,
    db_factory=get_db,
    persister=persist_index_candles_bulk,
)

from app.paper_overall_controls import check_paper_overall_controls

live_exit_monitor = LiveExitMonitor(
    db_factory=get_db,
    tick_lookup_factory=lambda: upstox_stream_manager.latest_tick_map().get,
    mark_fn=mark_open_deployment_trades,
    # Basket-level overall controls (Paper page parity with Live): evaluated
    # every cycle after per-leg marking; supervisor-reconciled with the monitor.
    overall_fn=check_paper_overall_controls,
)

from app.live_feed_health import supervise_once as _supervise_once, decide_exit_monitor_action, SUPERVISE_POLL_SEC as _SUPERVISE_POLL_SEC

# Auto-reconcile supervisor state (exposed to /live-feed/health). `suppressed` is
# set True by a manual stop endpoint so the loop won't fight a deliberate Stop.
_feed_supervisor: Dict[str, Any] = {
    "suppressed": False, "backoff_active": False, "last_error": None,
    "last_actions": [], "last_tick_at": None,
}


def feed_supervisor_state() -> Dict[str, Any]:
    return dict(_feed_supervisor)


# ---------------------------------------------------------------------------
# Live software exit guard (margin-free SL/TP/trailing) — replaces the always-
# margin-rejected resting broker SL. The guard ALWAYS TRANSMITS: a deployed
# strategy's stop/target/trailing exits are part of the strategy, not an optional
# extra, and the old LIVE_GUARD_ARMED env gate created a genuinely dangerous split
# where real entries opened but automated exits only logged. Removed by explicit
# user decision (see DEVELOPER_GUIDE §E); the resting broker OCO remains the
# PC-down backstop underneath.
# ---------------------------------------------------------------------------


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
    """Margin-safe square via auto_square.square_position. ALWAYS TRANSMITS.

    No env gate: an automated exit that only logs would leave a real position open
    past its stop. Failures surface through square_position's own result contract
    (and the guard's retry/escalation path), never as a silent no-op.
    """
    from app.live.auto_square import square_position
    doc = await _live_token_doc()
    uid = (doc or {}).get("uid", "")
    actid = (doc or {}).get("actid", uid)
    return await square_position(client, position, reason=reason, band_pct=1.0, uid=uid, actid=actid)


async def _live_guard_reprice_fn(client, position, *, band_pct, prev_ordno, prev_qty, reason):
    """Layer 2: over-sell-safe widening re-price of a resting-unfilled guard exit via
    auto_square.reprice_exit_leg. ALWAYS TRANSMITS — un-gated alongside the square.

    Un-gating these two together is load-bearing: leaving the escalation gated while
    the square transmits would let a resting unfilled exit sit un-widened forever.

    Distinct from _live_guard_square_fn: this cancels the TRACKED prior exit, re-reads
    its fillshares, and places ONLY the confirmed remaining qty at a wider bid-anchored,
    circuit-clamped price — never over-sells."""
    from app.live.auto_square import reprice_exit_leg
    return await reprice_exit_leg(
        client, position, band_pct=band_pct,
        prev_ordno=prev_ordno, prev_qty=prev_qty, reason=reason)


async def _live_guard_overall_provider():
    """Return the saved overall-controls config (basket SL/target/trailing) for the
    guard's basket evaluation, or None if unavailable."""
    try:
        from app.live.overall_settings_store import default_store
        return await default_store("overall").get_config()
    except Exception:
        return None


def _live_guard_spot_tick_fn() -> dict:
    """Return the latest Upstox spot-tick map for the guard's spot-mirror exits.

    Wraps upstox_stream_manager.latest_tick_map() in try/except so a stream
    outage degrades gracefully to "no spot data" rather than killing the guard
    cycle — mirrors the same pattern in live_broker._get_tick_map_for_option_premium.
    """
    try:
        return upstox_stream_manager.latest_tick_map()
    except Exception:
        return {}


async def _live_guard_on_close(entry, exit_price, reason, result) -> None:
    """Close-loop: journal realized P&L back to the live_trades doc when the guard's
    square is CONFIRMED FLAT by the broker. Fired from the guard's ``_finalize_flat``
    (the sole confirmed-flat finalizer) with a synthesized
    ``{"squared": True, "via": "confirmed_flat"}`` result — NOT on place-acceptance,
    and only for an entry that had a guard square pending (`squaring`), across every
    exit path (premium stop/target/trail, spot-mirror, time-stop, EOD, overall-basket).

    Safety (both adversarially verified): only journals a CONFIRMED close —
    ``should_journal_close`` skips a dry-run / non-squared result and ``source==
    "manual"`` single-shots (no live_trades doc). Links by the entry norenordno
    (== entry["id"] for an auto_live entry; a rehydrated entry is keyed by tsym and
    has no doc, so the update no-ops). exit_price is the last broker mark (an
    estimate; reboot reconcile back-fills the true fill price). Idempotent
    (status != CLOSED). NEVER raises (the guard wraps this call)."""
    from app.live.close_loop import should_journal_close, close_live_trade
    if not should_journal_close(entry, result):
        return
    await close_live_trade(
        get_db(),
        norenordno=(entry or {}).get("id"),
        exit_price=exit_price,
        exit_reason=reason,
    )
    # Track B / Phase 5B B6: a premium-momentum deployment's confirmed-flat
    # close finalizes the session lock PER LEG. The closed leg is identified by
    # matching the entry norenordno against the lock's <leg>_entered_norenordno
    # fields (recon anchor 3: the guard entry has no side/direction of its own).
    # The whole-doc done fires ONLY when no leg remains unresolved (recon
    # correction 3: the old unconditional mark_done would silently kill a still
    # -open sibling leg's session mid-day). A STOP-class primary close in
    # both-mode ADDITIONALLY arms the opposite-side lazy leg (one shot,
    # blueprint §4) — never on target/EOD/exit-time/time-stop/overall-basket
    # squares (overall_* squares EVERYTHING for the deployment; arming a
    # reversal into the operator's own basket stop would fight it), and never
    # when the deployment has no lazy trigger configured (a silently
    # never-triggering lazy leg pins subscriptions for nothing). NEVER raises.
    try:
        _dep_id = str((entry or {}).get("deployment_id") or "")
        if _dep_id:
            _db = get_db()
            _dep = await _db.strategy_deployments.find_one({"id": _dep_id})
            if str((_dep or {}).get("strategy_id") or "") == "premium_momentum":
                from app.premium_lock_store import (
                    get_lock, legs_unresolved, mark_done, mark_leg_exited,
                    set_lazy_armed,
                )
                from app.strategies.base import get_registry as _get_strat_registry
                _today = (datetime.now(timezone.utc)
                          + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
                _strat = _get_strat_registry().get("premium_momentum")
                _params = (_strat.merged_params(dict(_dep.get("params") or {}))
                           if _strat else dict(_dep.get("params") or {}))
                _both = str(_params.get("leg_mode") or "first_to_trigger").lower() == "both"
                _lock = await get_lock(_db.premium_locks, deployment_id=_dep_id,
                                       session_date=_today)
                if not _both or _lock is None:
                    # first_to_trigger / no lock: today's observable behavior,
                    # byte-identical (single leg resolved => session done).
                    await mark_done(_db.premium_locks, deployment_id=_dep_id,
                                    session_date=_today, reason="exited")
                else:
                    _ordno = str((entry or {}).get("id") or "")
                    _leg = None
                    for _cand, _prefix in (("pce", "ce"), ("ppe", "pe"),
                                           ("lce", "lce"), ("lpe", "lpe")):
                        if str(_lock.get(f"{_prefix}_entered_norenordno") or "") == _ordno and _ordno:
                            _leg = _cand
                            break
                    if _leg:
                        await mark_leg_exited(_db.premium_locks, deployment_id=_dep_id,
                                              session_date=_today, leg=_leg)
                        # Realized-only day-stop accumulator (best-effort: the
                        # close-loop journals the authoritative realized figure
                        # onto live_trades; the evaluator's day-stop gate reads
                        # THAT, not this field — this is observability only).
                        try:
                            _qty = abs(int((entry or {}).get("qty") or 0))
                            _ep = float((entry or {}).get("entry_price") or 0.0)
                            _xp = float(exit_price) if exit_price is not None else _ep
                            await _db.premium_locks.update_one(
                                {"deployment_id": _dep_id, "session_date": _today},
                                {"$set": {f"{_leg}_realized_estimate":
                                          round((_xp - _ep) * _qty, 2)}})
                        except Exception:
                            pass
                        # Lazy arming: STOP-class PRIMARY closes only.
                        _STOP_CLASS = {"stop", "breakeven_stop", "trailing_stop",
                                       "spot_stop_hit"}
                        _lazy_trigger_set = (_params.get("lazy_momentum_pct") is not None
                                             or _params.get("lazy_momentum_pts") is not None)
                        _cutoff = None
                        try:
                            from app.premium_momentum import normalize_hhmm
                            _cutoff = normalize_hhmm(_params.get("entry_cutoff"))
                        except ValueError:
                            _cutoff = None
                        _now_hhmm = ((datetime.now(timezone.utc)
                                      + timedelta(hours=5, minutes=30)).strftime("%H:%M"))
                        if (_leg in ("pce", "ppe")
                                and str(reason or "").lower() in _STOP_CLASS
                                and bool(_params.get("lazy_enabled"))
                                and _lazy_trigger_set
                                and (_cutoff is None or _now_hhmm < _cutoff)):
                            # The ARMED side is the OPPOSITE of the failed
                            # primary (blueprint: a stopped CALL arms the lazy
                            # PUT and vice versa). lazy_armed_<side> names the
                            # side of the LAZY leg itself — the engine's pickup
                            # maps lazy_armed_ce -> leg lce (a CE lazy leg).
                            _lazy_side = "pe" if _leg == "pce" else "ce"
                            armed = await set_lazy_armed(
                                _db.premium_locks, deployment_id=_dep_id,
                                session_date=_today, side=_lazy_side,
                                parent_reason=str(reason or ""))
                            if armed:
                                log.info("premium 5B: lazy %s armed for deployment %s "
                                         "(primary %s closed: %s)",
                                         _lazy_side, _dep_id, _leg, reason)
                    else:
                        log.warning("premium 5B: confirmed-flat entry %s matched no "
                                    "leg on the session lock (deployment %s)",
                                    _ordno, _dep_id)
                    # Whole-doc done ONLY when (a) no armed/entered leg remains
                    # unresolved AND (b) both primaries actually traded and
                    # exited. A never-triggered sibling primary keeps the
                    # session MONITORING (it is still eligible to enter) — done
                    # here would silently amputate it; an idle session simply
                    # ages out at EOD with no done marker, which is safe.
                    _lock = await get_lock(_db.premium_locks, deployment_id=_dep_id,
                                           session_date=_today)
                    if _lock is not None and not legs_unresolved(_lock, _params) \
                            and bool(_lock.get("ce_exited")) and bool(_lock.get("pe_exited")):
                        await mark_done(_db.premium_locks, deployment_id=_dep_id,
                                        session_date=_today, reason="exited")
    except Exception:
        log.exception("premium-momentum per-leg close finalize failed for entry %s",
                      (entry or {}).get("id"))


live_position_guard = LivePositionGuard(
    registry=get_live_monitor_registry(),
    client_factory=_live_guard_client_factory,
    square_fn=_live_guard_square_fn,
    reprice_fn=_live_guard_reprice_fn,
    overall_provider=_live_guard_overall_provider,
    spot_tick_fn=_live_guard_spot_tick_fn,
    eod_square_ist=dtime(15, 0),
    on_close=_live_guard_on_close,
)


async def rehydrate_premium_momentum(db, registry, broker_positions_by_tsym,
                                     *, noren_tsym_by_ordno=None) -> Dict[str, Any]:
    """Track B recovery: re-attach the guard to premium-momentum positions using
    the PERSISTED lock state (entry premium, deployment, exit plan) instead of
    the generic 50%-catastrophe rehydrate. Locks whose position is gone are
    closed out honestly (done_for_day='exited_while_down').

    RE-RUN SAFE: recovery re-runs are routine (the supervisor retries on
    complete=False; every daily Flattrade OAuth forces maybe_run_live_recovery).
    A lock whose entry norenordno OR tsym is already in the registry is SKIPPED
    (counted in ``skipped``) — re-registering would either double-watch one
    position under two keys (two independent stop evaluations → two full-qty
    square orders on a fast gap through both levels) or REPLACE a mid-square
    entry (``register()`` overwrites, resetting ``squaring``/``square_ordno``
    and re-arming a second exit while the first still rests at the broker).
    Mirrors the generic ``rehydrate_from_broker`` watched_tsyms guard.
    Never raises."""
    from app.live.kill_switch import _parse_netqty
    from app.live.live_sl_monitor import build_monitor_state
    from app.premium_lock_store import legs_unresolved, mark_done, mark_leg_exited
    out = {"reattached": 0, "closed": 0, "skipped": 0, "errors": 0}
    try:
        today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        # 5B B7: both-mode entries live in PER-LEG fields and deliberately never
        # set the legacy entered_norenordno (review C2's flagged blind spot: the
        # old $ne:None query would skip them and an open position restarted with
        # NO stop monitor). Query broadly, branch per doc shape below.
        locks = await db.premium_locks.find(
            {"session_date": today, "done_for_day": False},
            {"_id": 0}).to_list(length=None)
        locks = [l for l in locks
                 if l.get("entered_norenordno")
                 or any(l.get(f"{p}_entered_norenordno") for p in ("ce", "pe", "lce", "lpe"))]
        # Already-watched set: registry entries are keyed by entry norenordno
        # (auto_live / this function) OR by tsym (generic rehydrate), and the
        # guard matches broker positions to entries BY TSYM — collect both ids
        # and tsyms so no re-run path can double-watch or clobber a live entry.
        watched: set = set()
        for e in registry.snapshot():
            watched.add(str(e.get("id") or ""))
            watched.add(str(e.get("tsym") or ""))
        watched.discard("")
        for lock in locks:
            try:
                dep = await db.strategy_deployments.find_one(
                    {"id": lock["deployment_id"]}, {"_id": 0})
                params = dict((dep or {}).get("params") or {})
                risk = dict((dep or {}).get("risk") or {})

                # ---- 5B B7: build the leg worklist for this lock doc. Legacy
                # single-leg (first_to_trigger) docs keep their EXACT prior
                # treatment incl. the whole-doc exited_while_down close; per-leg
                # (both-mode) docs are handled leg-by-leg with per-leg exit
                # marks and NO premature whole-doc done (recon correction 3).
                # 5B review Finding 1 (CONFIRMED): the persisted contract's
                # trading_symbol is the UPSTOX symbol; the broker book is keyed
                # by the NOREN tsym — matching those directly declared every
                # genuinely open leg "gone" on a real restart and falsely
                # finalized the session. The ONLY trusted symbol source here is
                # the broker's own order book (norenordno -> Noren tsym),
                # passed in by the caller. A leg whose ordno cannot be resolved
                # is SKIPPED (left to the generic rehydrate) — never guessed at
                # and NEVER marked exited on an unresolvable symbol.
                _ord_map = dict(noren_tsym_by_ordno or {})
                legs: list = []
                if lock.get("entered_norenordno"):
                    legs.append({
                        "legacy": True, "leg": None,
                        "ordno": str(lock["entered_norenordno"]),
                        "exch": "NFO",
                        "entry": float(lock.get("entry_premium") or 0) or None,
                        "stop_pct": params.get("stop_pct") or 50.0,
                        "target_pct": params.get("target_pct"),
                    })
                for leg, prefix in (("pce", "ce"), ("ppe", "pe"),
                                    ("lce", "lce"), ("lpe", "lpe")):
                    _ord = lock.get(f"{prefix}_entered_norenordno")
                    if not _ord or lock.get(f"{prefix}_exited"):
                        continue
                    if leg in ("pce", "ppe"):
                        _stop = params.get("stop_pct") or 50.0
                        _tgt = params.get("target_pct")
                    else:
                        _stop = (params.get("lazy_stop_pct")
                                 if params.get("lazy_stop_pct") is not None
                                 else (params.get("stop_pct") or 50.0))
                        _tgt = params.get("lazy_target_pct")
                    legs.append({
                        "legacy": False, "leg": leg, "ordno": str(_ord),
                        "exch": "NFO",
                        "entry": float(lock.get(f"{prefix}_entry_premium") or 0) or None,
                        "stop_pct": _stop, "target_pct": _tgt,
                    })
                for item in legs:
                    item["tsym"] = str(_ord_map.get(item["ordno"]) or "")

                # exit_time -> per-entry square time; register() re-clamps.
                _sq_at = None
                try:
                    from app.premium_momentum import normalize_hhmm as _nh
                    _sq_at = _nh(params.get("exit_time"))
                except ValueError:
                    _sq_at = None

                for item in legs:
                    ordno, tsym = item["ordno"], item["tsym"]
                    if ordno in watched or (tsym and tsym in watched):
                        out["skipped"] += 1
                        continue  # already guarded (re-run / fresh arm) — never clobber
                    if not tsym:
                        continue  # no symbol persisted — leave to the generic rehydrate
                    pos = broker_positions_by_tsym.get(tsym)
                    if not pos:
                        if item["legacy"]:
                            # single-leg: whole session done, exactly as before.
                            await mark_done(db.premium_locks,
                                            deployment_id=lock["deployment_id"],
                                            session_date=today,
                                            reason="exited_while_down")
                        else:
                            # per-leg: mark ONLY this leg exited; the whole doc
                            # finalizes below only when nothing is unresolved.
                            await mark_leg_exited(db.premium_locks,
                                                  deployment_id=lock["deployment_id"],
                                                  session_date=today, leg=item["leg"])
                        out["closed"] += 1
                        continue
                    qty = _parse_netqty(pos.get("netqty"))
                    if not qty:
                        continue  # flat/unparseable netqty — generic rehydrate's job
                    if item["entry"] is None:
                        continue  # no persisted entry -> generic rehydrate's job
                    state = build_monitor_state(
                        item["entry"], stop_pct=item["stop_pct"],
                        target_pct=item["target_pct"],
                        trail=risk.get("exit_controls"))
                    registry.register(
                        key=ordno, tsym=tsym, exch=item["exch"],
                        qty=abs(qty), prd="I",
                        entry_price=item["entry"], state=state, source="auto_live",
                        deployment_id=str(lock["deployment_id"]),
                        square_at_ist=_sq_at)
                    watched.add(ordno)
                    watched.add(tsym)  # guard against duplicate locks on one tsym
                    out["reattached"] += 1

                # per-leg docs: finalize the whole session ONLY when no leg is
                # unresolved AND both primaries exited (same rule as the B6
                # close hook — a never-triggered sibling keeps monitoring).
                if not lock.get("entered_norenordno"):
                    fresh = await db.premium_locks.find_one(
                        {"deployment_id": lock["deployment_id"], "session_date": today},
                        {"_id": 0})
                    if fresh and not fresh.get("done_for_day") \
                            and not legs_unresolved(fresh, params) \
                            and bool(fresh.get("ce_exited")) and bool(fresh.get("pe_exited")):
                        await mark_done(db.premium_locks,
                                        deployment_id=lock["deployment_id"],
                                        session_date=today, reason="exited_while_down")
            except Exception:
                out["errors"] += 1
                log.exception("premium-momentum rehydrate failed for lock %s", lock.get("deployment_id"))
    except Exception:
        out["errors"] += 1
        log.exception("premium-momentum rehydrate scan failed")
    return out


async def live_startup_recovery() -> bool:
    """One-shot startup recovery for the live execution path (best-effort, non-blocking).

    Closes the two restart-orphan holes the in-memory live state otherwise leaves:
      1. resume_pending — adopt any orphaned SUBMITTING-but-unACKed order by matching
         the broker order book on remarks==client_order_id (the order builder pins
         remarks=cid), closing the crash-between-POST-and-ACK duplicate-order gap.
      2. guard rehydrate — re-attach the software exit guard to open broker positions
         (the guard registry is empty on boot), so a position opened before the
         restart is never left unwatched (no software stop/target/EOD square).
         Premium-momentum positions are re-attached FIRST from their persisted
         lock state (real entry premium + exit plan, ``rehydrate_premium_momentum``);
         the generic rehydrate then covers the rest at the default catastrophe stop.

    Skips silently when the broker isn't connected. Never raises out.

    Returns True only when the run was COMPLETE: a broker client existed, no step
    raised, and the reboot reconcile could actually READ the position book. The
    per-token recovery latch (``maybe_run_live_recovery``) records success only on
    True — a run whose every step failed (network down at boot, broker outage)
    returns False so the supervisor keeps retrying, instead of latching a green
    "recovered" over an unguarded position.
    """
    _log = logging.getLogger(__name__)
    complete = True
    client = await _live_guard_client_factory()
    if client is None:
        _log.info("live startup recovery: broker not connected — skipping resume_pending + guard rehydrate")
        return False
    # 1. resume_pending — adopt orphaned orders (build a real-client engine; the
    #    live_broker singleton is built with a None client, so use a local one).
    try:
        from app.live.engine import LiveEngine
        from app.live.idempotency import default_store as _intent_store
        from app.live.kill_switch import default_store as _safety_store
        eng = LiveEngine(
            client=client,
            orders_collection=get_db().live_orders,
            intent_store=_intent_store(),
            config_store=_safety_store(),
        )
        res = await eng.resume_pending()
        _log.info("live startup recovery: resume_pending adopted=%s needs_submit=%s",
                  res.get("adopted"), res.get("needs_submit"))
    except Exception as exc:
        complete = False
        _log.warning("live startup recovery: resume_pending failed: %s", exc)
    # 2. Track B premium-momentum rehydrate — re-attach entered premium positions
    #    from their PERSISTED lock state (entry premium + deployment exit plan).
    #    MUST run BEFORE the generic guard rehydrate (step 3): both steps skip
    #    already-watched tsyms, so premium-first means the premium position gets
    #    its persisted plan and the generic step skips it; the reverse order
    #    would leave it on the generic 50% catastrophe default. NOTE: the
    #    generic step reads the position book INTERNALLY (no book variable is in
    #    scope here), so this step performs its own read via the same client. An
    #    empty/non-list book == UNKNOWN (transient) -> skip entirely (no
    #    mark_done, no register); the reboot reconcile (step 4) surfaces an
    #    unreadable book as incomplete so the supervisor retries.
    try:
        _pm_book = await client.position_book()
    except Exception as exc:
        _pm_book = None
        complete = False
        _log.warning("live startup recovery: premium rehydrate position-book read failed: %s", exc)
    if isinstance(_pm_book, list) and _pm_book:
        from app.live.kill_switch import _parse_netqty as _pm_parse_netqty
        _held_by_tsym: Dict[str, Any] = {}
        for _pos in _pm_book:
            try:
                _nq = _pm_parse_netqty(_pos.get("netqty"))
                _ts = str(_pos.get("tsym") or "")
            except Exception:
                continue
            if _nq and _ts:
                _held_by_tsym[_ts] = _pos
        # 5B review Finding 1: the lock's persisted contract carries the UPSTOX
        # trading_symbol, but the position book above is keyed by the NOREN
        # tsym — matching those directly reads every open leg as "gone" and
        # falsely finalizes a session with real money open. The durable Noren
        # symbol for an entered leg lives in the broker's own ORDER book row
        # for its norenordno — build that join map here (same-day restart ⇒
        # today's entries are in today's book). An unreadable order book ⇒
        # empty map ⇒ the rehydrate SKIPS legs it cannot resolve (leaving them
        # to the generic rehydrate) instead of guessing.
        _ordno_tsym: Dict[str, str] = {}
        try:
            _pm_orders = await client.order_book()
            if isinstance(_pm_orders, list):
                for _row in _pm_orders:
                    _no = str(_row.get("norenordno") or "")
                    _rt = str(_row.get("tsym") or "")
                    if _no and _rt:
                        _ordno_tsym[_no] = _rt
        except Exception as exc:
            _log.warning("live startup recovery: premium rehydrate order-book "
                         "read failed (%s) — unresolved legs left to the "
                         "generic rehydrate", exc)
        pm_res = await rehydrate_premium_momentum(
            get_db(), get_live_monitor_registry(), _held_by_tsym,
            noren_tsym_by_ordno=_ordno_tsym)
        _log.info("live startup recovery: premium-momentum rehydrate reattached=%s "
                  "closed=%s skipped=%s errors=%s", pm_res.get("reattached"),
                  pm_res.get("closed"), pm_res.get("skipped"), pm_res.get("errors"))
        # A per-lock error leaves that position guarded only at the generic 50%
        # catastrophe stop (persisted plan stop/trail silently lost) — do NOT
        # latch a green "recovered" over it. Retrying is safe: the already-
        # watched guard in rehydrate_premium_momentum makes a re-run skip every
        # lock that DID attach (no double-watch, no clobber).
        if pm_res.get("errors"):
            complete = False
    elif _pm_book is not None:
        _log.info("live startup recovery: premium rehydrate skipped — position book "
                  "%s (UNKNOWN); no lock closed",
                  "empty" if isinstance(_pm_book, list)
                  else "unreadable (non-list payload)")
    # 3. guard rehydrate — re-attach to the remaining open positions (default stop).
    try:
        n = await live_position_guard.rehydrate_from_broker()
        if n:
            _log.warning("live startup recovery: guard re-attached to %s open position(s) "
                         "at the default catastrophe stop (original levels lost on restart)", n)
        else:
            _log.info("live startup recovery: no open broker positions to rehydrate")
    except Exception as exc:
        complete = False
        _log.warning("live startup recovery: guard rehydrate failed: %s", exc)
    # 4. transient-safe reboot reconciliation — journal any OCO that fired (or any
    #    position closed externally) while the PC was down + sweep orphan OCOs.
    #    Empty position_book == UNKNOWN (no close, no cancel); never raises.
    try:
        from app.live.reboot_reconcile import reconcile_on_startup
        res = await reconcile_on_startup(get_db(), client)
        _log.info("live startup recovery: reboot reconcile closed=%s cancelled=%s "
                  "relinked=%s no_backstop=%s status=%s",
                  res.get("closed"), res.get("cancelled"),
                  res.get("relinked"), res.get("no_backstop"), res.get("status"))
        # reconcile reads the position book directly and reports an unreadable/
        # empty read as "unknown_position_book". Both rehydrate (steps 2–3) and
        # reconcile swallow read failures internally, so this status is the honest
        # broker-readability signal for the whole run: unreadable ⇒ the rehydrate
        # almost certainly saw nothing either ⇒ recovery is NOT complete.
        if res.get("status") == "unknown_position_book":
            complete = False
    except Exception as exc:
        complete = False
        _log.warning("live startup recovery: reboot reconcile failed: %s", exc)
    # (The old step 4 — re-arm the 10-min manual auto-square timer — is gone with
    # the timer itself: a recovered manual position is protected by the rehydrated
    # software guard stop + the 15:00 IST EOD square, like any other position.)
    return complete


# ---------------------------------------------------------------------------
# Re-runnable live recovery — the boot-time run is SKIPPED when the PC boots
# before the daily Flattrade OAuth, so recovery must also fire when a token first
# appears (OAuth callback) and be retried by the supervisor. A per-token latch
# runs it exactly once per token so overnight positions come back guarded +
# reconciled regardless of boot order.
# ---------------------------------------------------------------------------

_live_recovery_state: Dict[str, Any] = {
    "succeeded": False,
    "token_fingerprint": None,
    "last_attempt_at": None,
    "last_result": None,
}


async def maybe_run_live_recovery(*, force: bool = False) -> Dict[str, Any]:
    """Run ``live_startup_recovery`` IFF a Flattrade token is present and recovery
    has not already succeeded for THIS token (fingerprinted by jKey). Idempotent
    and safe to call from boot, the OAuth callback, and the supervisor. Never
    raises."""
    _log = logging.getLogger(__name__)
    try:
        doc = await _live_token_doc()
    except Exception:
        doc = None
    jkey = (doc or {}).get("jKey")
    if not jkey:
        _live_recovery_state["last_result"] = "no_token"
        return {"ran": False, "reason": "no_token"}
    fp = str(jkey)[:12]  # short fingerprint — never store the whole session key
    if (not force and _live_recovery_state["succeeded"]
            and _live_recovery_state["token_fingerprint"] == fp):
        return {"ran": False, "reason": "already_recovered"}
    _live_recovery_state["last_attempt_at"] = datetime.now(timezone.utc).isoformat()
    try:
        complete = await live_startup_recovery()
        if not complete:
            # A run happened but could NOT do its job (broker unreachable, a step
            # failed, or the position book was unreadable). Do NOT latch success —
            # the supervisor keeps retrying every tick until a COMPLETE run, which
            # is the entire point of the retry loop (an incomplete run latched as
            # "ok" would leave an overnight position unguarded all day behind a
            # green recovery-status strip).
            _live_recovery_state["last_result"] = "incomplete — will retry"
            _log.warning("live recovery: ran but INCOMPLETE (broker unreachable or a "
                         "step failed) — will retry")
            return {"ran": True, "reason": "incomplete"}
        _live_recovery_state["succeeded"] = True
        _live_recovery_state["token_fingerprint"] = fp
        _live_recovery_state["last_result"] = "ok"
        _log.info("live recovery: completed for token %s…", fp)
        return {"ran": True, "reason": "ok"}
    except Exception as exc:   # noqa: BLE001 — recovery must never take down the caller
        _live_recovery_state["last_result"] = f"error: {str(exc)[:120]}"
        _log.warning("live recovery: failed: %s", exc)
        return {"ran": True, "reason": "error"}


def live_recovery_status() -> Dict[str, Any]:
    """Snapshot of the live-recovery latch (for /live-broker/recovery-status)."""
    return dict(_live_recovery_state)


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


async def _live_feed_supervisor_loop() -> None:
    """Keep the Upstox stream + candle roller running during market hours whenever
    the token is valid. Fixes the 'app started before the daily OAuth' gap and
    self-heals mid-session drops. Never touches credentials — when the token is
    missing/expired it does nothing (health surfaces NEEDS_LOGIN)."""
    from datetime import time as _time
    from app.nse_calendar import is_trading_day
    log.info("Live-feed supervisor loop initialized")
    while True:
        try:
            await asyncio.sleep(_SUPERVISE_POLL_SEC)
            ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            today_iso = ist_now.strftime("%Y-%m-%d")
            t = ist_now.time()
            market_open = (
                is_trading_day(today_iso)
                and _time(9, 15) <= t < _time(15, 30)
            )
            token = await upstox_client.get_connection_status()
            token_ok = bool(token.get("connected") and not token.get("expired"))
            keys = _default_stream_instrument_keys()
            actions = await _supervise_once(
                market_open=market_open, token_ok=token_ok,
                stream_manager=upstox_stream_manager, roller=live_candle_roller,
                instrument_keys=keys, mode=DEFAULT_STREAM_MODE, state=_feed_supervisor,
            )
            # Reconcile the paper tick-exit / mark-to-market monitor in PARITY with the
            # roller. Without this, a boot-before-OAuth gap (or any restart while the
            # token is disconnected) leaves the monitor dead even after the supervisor
            # revives the stream+roller — so OPEN paper trades are never marked-to-market
            # (blotter Net/Max/Min P&L + P&L%/curve stuck at 0) nor tick-exited on SL/TP.
            try:
                em_action = decide_exit_monitor_action(
                    market_open=market_open, token_ok=token_ok,
                    suppressed=bool(_feed_supervisor.get("suppressed")),
                    running=bool(live_exit_monitor.status().get("running")),
                )
                if em_action == "start_exit_monitor":
                    await live_exit_monitor.start()
                    actions = list(actions) + ["start_exit_monitor"]
                elif em_action == "stop_exit_monitor":
                    await live_exit_monitor.stop()
                    actions = list(actions) + ["stop_exit_monitor"]
            except Exception as exc:   # noqa: BLE001 - never kill the supervisor loop
                log.warning("exit-monitor reconcile failed: %s", exc)
            _feed_supervisor["last_actions"] = actions
            _feed_supervisor["last_tick_at"] = datetime.now(timezone.utc).isoformat()
            # Retry live recovery until it succeeds for the current Flattrade token.
            # This is what makes the boot-before-OAuth case recover: the boot run is
            # skipped (no token yet), the OAuth callback fires it, and this backstops
            # both. The per-token latch makes it a cheap no-op once done.
            try:
                await maybe_run_live_recovery()
            except Exception as exc:   # noqa: BLE001 - never kill the supervisor
                log.warning("live recovery (supervisor) failed: %s", exc)
        except Exception as exc:   # noqa: BLE001 - never kill the loop
            log.exception("live-feed supervisor tick failed: %s", exc)


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
        # Forward-append alone can't fill a mid-window HOLE (a VIX day missed
        # while later days were fetched). Pull the start back to the earliest
        # missing/short VIX trading day in the recent repair window.
        try:
            from app.data_hygiene import (
                most_recent_closed_session, _spot_day_rows, vix_topup_from_date,
            )
            judge = most_recent_closed_session()
            vix_rows = await _spot_day_rows(db, VIX_INSTRUMENT)
            from_date = vix_topup_from_date(vix_rows, forward_from=from_date, judge_until=judge)
        except Exception as exc:
            log.debug("VIX hole scan skipped: %s", exc)
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

    # Phase 4 engine dispatch: premium_momentum's evaluate() is a deliberate stub
    # (the real logic lives only in deployment_evaluator.py's dedicated branch), so
    # `spot_trades` (built from the generic run_backtest -> strategy.evaluate() path)
    # is always empty for this strategy — the caller never has real spot trades to
    # pass in here. Dispatch to the option-native sim directly instead of pairing an
    # empty list. Every other strategy_id is unaffected (dispatch_full_backtest
    # returns None immediately with zero side effects for any other id).
    if req.strategy_id == "premium_momentum" and req.start_ts is not None and req.end_ts is not None:
        from app.premium_momentum_backtest import _sides_for
        from app.premium_trigger_dispatch import dispatch_full_backtest
        from app.routers.premium_momentum_routes import _load_window

        # Apply the plugin's schema defaults (merged_params) so a raw/partial
        # API request behaves like the UI's filled params panel — raw
        # req.params with no momentum trigger would otherwise fail
        # PremiumTriggerConfig validation and silently fall through to the
        # generic (always-empty) spot path. merged_params is a strict
        # allow-list, so `lots`/`cost_config` (not in the plugin schema) are
        # re-applied AFTER the merge: raw-params value wins, else the Backtest
        # Lab option form's value — the form is the only UI surface for them.
        # moneyness is NOT taken from the option form: the strategy param
        # governs the strike lock; the form's pairing-moneyness has no meaning
        # for a premium-native run and is deliberately ignored.
        _strategy = get_registry().get(req.strategy_id)
        pm_params = _strategy.merged_params(req.params) if _strategy else dict(req.params)
        pm_params["lots"] = int(req.params.get("lots") or config.lots or 1)
        if req.params.get("cost_config") is not None:
            pm_params["cost_config"] = req.params["cost_config"]
        elif config.cost_config is not None:
            pm_params["cost_config"] = config.cost_config

        # NOTE: no lazy_enabled here — the general Backtest Lab path stays
        # single-leg by design until Phase 5B (PremiumTriggerConfig deliberately
        # has no lazy fields; dispatch would drop them anyway). The bespoke
        # /premium-momentum page is the two-leg/lazy surface.
        loaded = await _load_window(
            underlying, req.start_ts, req.end_ts,
            ref_time=str(pm_params.get("reference_time") or "09:31"),
            moneynesses=[str(pm_params.get("moneyness") or "itm1")],
            sides=_sides_for(pm_params.get("side")),
        )
        if loaded is None:
            return None
        spot_df, option_candles, contracts = loaded
        pm_result = dispatch_full_backtest(
            strategy_id=req.strategy_id, merged_params=pm_params,
            spot_df=spot_df, option_candles=option_candles, contracts=contracts,
            instrument=underlying,
        )
        if pm_result is not None:
            pm_result["skipped_trades"] = []
            pm_result["request"] = config.model_dump()
            pm_result["data"] = {
                "expiry_date": config.expiry_date, "expiry_mode": "premium_trigger_config",
                "resolved_expiries": [], "trades_without_expiry": 0,
                "contracts_loaded": len(contracts), "instrument_keys_needed": 0,
                "candles_loaded": int(len(option_candles)), "candles_capped": False,
                "source": "premium_trigger_dispatch", "auto_fetch": False, "dte_filter": None,
            }
            return pm_result
        # cfg didn't validate (e.g. no momentum trigger set) -> fall through to the
        # normal spot_trades path below, which will correctly produce an empty/
        # honest result since spot_trades is empty for this strategy anyway.

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
    # expiry_date values; "all"/None keeps every trade. The sim enumerates the
    # FILTERED list, but the caller's response/UI joins option legs to the FULL
    # spot trade list by index (index_trade_id) — so record each kept trade's
    # original position and remap the legs after the sim. Without the remap,
    # every leg after the first dropped trade renders on the wrong spot row
    # (2026-07-18 root cause: a CE row showing another trade's PE leg).
    dte_target = normalize_dte_filter(config.dte_filter)
    dte_stats = {"filter": config.dte_filter, "input_trades": len(spot_trades)}
    index_remap: Optional[List[int]] = None
    if dte_target is not None:
        expiry_dates_sorted = sorted({
            str(c.get("expiry_date")) for c in contracts if c.get("expiry_date")
        })
        kept: List[Dict[str, Any]] = []
        index_remap = []
        for orig_pos, trade in enumerate(spot_trades):
            entry_ts = trade.get("entry_ts")
            if entry_ts is None:
                continue
            trade_date = _ts_ms_to_ist_date_str(int(entry_ts))
            if compute_dte(trade_date, expiry_dates_sorted) in dte_target:
                kept.append(trade)
                index_remap.append(orig_pos)
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
    candles_capped = False
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
        candle_rows = await db.options_1m.find(candle_query, {"_id": 0}).sort("ts", 1).to_list(length=OPTION_CANDLE_LOAD_CAP)
        if len(candle_rows) >= OPTION_CANDLE_LOAD_CAP:
            # Oldest-first sort means a capped load drops the NEWEST candles, so
            # the most recent trades silently fail to pair. Never let this pass
            # unnoticed: warn and surface candles_capped in the response.
            candles_capped = True
            log.warning(
                "paired-option backtest candle load hit the %d-row cap (%d strike keys); "
                "newest candles were dropped (oldest-first) and recent trades will not pair",
                OPTION_CANDLE_LOAD_CAP, len(selected_keys),
            )

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
    if index_remap is not None:
        # Translate filtered-list positions back to full-list positions so
        # index_trade_id always refers to the caller's spot trade list (the one
        # the UI displays and joins against). Trades the DTE filter dropped
        # simply have no leg — which is the honest rendering.
        for t in _trades:
            fi = t.get("index_trade_id")
            if isinstance(fi, int) and 0 <= fi < len(index_remap):
                t["index_trade_id"] = index_remap[fi]
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
        "candles_capped": candles_capped,
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

    # premium_momentum: the generic path below derives needed contracts from
    # SPOT trades, but this strategy's evaluate() is a deliberate stub — zero
    # spot trades made the preflight report a misleading 0% forever. Coverage
    # for a premium-native run means "sessions whose locked strikes have
    # premium candles", which is exactly what the option-native sim's own
    # coverage gate measures — run it and report honestly (the sim is the same
    # work the real run does; a preflight for this strategy costs one run).
    if req.strategy_id == "premium_momentum" and req.start_ts and req.end_ts:
        from app.premium_momentum_backtest import _sides_for, run_premium_momentum_backtest
        from app.routers.premium_momentum_routes import _load_window

        pm_params = strategy.merged_params(req.params)
        if "lots" not in req.params and config.lots:
            pm_params["lots"] = int(config.lots)
        loaded = await _load_window(
            req.instrument.upper(), req.start_ts, req.end_ts,
            ref_time=str(pm_params.get("reference_time") or "09:31"),
            moneynesses=[str(pm_params.get("moneyness") or "itm1")],
            sides=_sides_for(pm_params.get("side")),
        )
        if loaded is None:
            raise HTTPException(400, f"Insufficient spot candles for {req.instrument.upper()} in the window.")
        spot_df, option_candles, contracts = loaded
        pm = await asyncio.to_thread(
            run_premium_momentum_backtest,
            spot_df=spot_df, option_candles=option_candles, contracts=contracts,
            instrument=req.instrument.upper(), params=pm_params,
        )
        cov = pm.get("coverage") or {}
        total = int(cov.get("sessions_total", 0) or 0)
        excluded = int(cov.get("sessions_excluded", 0) or 0)
        covered = max(0, total - excluded)
        pct = round(covered / total * 100.0, 1) if total else 0.0
        return {
            "enabled": True,
            "dispatch": "premium_trigger_config",
            "instrument": req.instrument.upper(),
            # Panel-compatible fields — for a premium-native run these are
            # SESSIONS, not per-signal pairings (one lock decision per session).
            "total_spot_trades": total,
            "would_pair": covered,
            "coverage_pct": pct,
            "missing_candle": excluded,
            "missing_contract": 0,
            "exclude_reasons": cov.get("exclude_reasons") or {},
            "note": ("premium-native strategy: coverage = sessions whose "
                     "reference-time locked strikes have premium candles "
                     f"({covered}/{total} sessions)."),
        }

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

    # Resolve the needed contract per trade. NOTE: per_trade "idx" values are
    # positions in the DTE-FILTERED spot_trades list; they are consumed only
    # inside this function (self-consistent) and only aggregates are returned.
    # Never surface them joined to the caller's full trade list without a
    # remap like _run_paired_option_backtest's (0.55.1 misalignment fix).
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
    exit_age_ms = max(0, int(config.exit_max_age_sec or 0)) * 1000
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
        exit_ts = int(spot_trades[pt["idx"]].get("exit_ts") or entry_ts)
        # A trade only pairs if BOTH the entry AND the exit candle exist within
        # their max-age windows — mirroring the real sim's two _candle_at_or_before
        # gates (option_backtest.py). Checking only the entry side overstated
        # coverage: illiquid strikes with an entry print but an exit-side gap were
        # counted as would_pair, then silently dropped as MISSING_EXIT_CANDLE.
        if preflight_trade_pairs(ts_list, entry_ts, exit_ts, entry_age_ms, exit_age_ms):
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


async def _load_deployment_source(
    db: Any,
    source_type: str,
    source_id: str,
    *,
    strategy_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source_type = str(source_type or "").lower()
    if source_type == "preset":
        doc = await db.presets.find_one({"name": source_id}, {"_id": 0})
    elif source_type == "backtest_run":
        doc = await db.backtest_runs.find_one({"id": source_id}, {"_id": 0, "trades": 0, "equity_curve": 0})
    elif source_type == "strategy":
        # A library strategy is converted to the same immutable source shape as
        # a preset.  This is deliberately a snapshot, not a pointer to mutable
        # class defaults.  build_deployment_doc additionally pins the source SHA.
        from app.strategies.base import get_registry

        strategy = get_registry().get(str(source_id or ""))
        if strategy is None:
            raise HTTPException(404, f"Strategy {source_id} not found")
        cfg = dict(strategy_config or {})
        supported_instruments = [str(v).upper() for v in strategy.supported_instruments]
        supported_timeframes = [str(v) for v in strategy.supported_timeframes]
        instrument = str(cfg.get("instrument") or (supported_instruments[0] if supported_instruments else "")).upper()
        # The deployment evaluator consumes closed candles_1m directly and does
        # not resample. A library strategy is live-compatible only when it
        # explicitly supports 1m; otherwise the snapshot would be misleading.
        if "1m" not in supported_timeframes:
            raise HTTPException(
                400,
                f"Strategy {strategy.id} is not deployment-compatible: live signals currently require 1m support",
            )
        timeframe = str(cfg.get("timeframe") or "1m")
        if instrument not in supported_instruments:
            raise HTTPException(
                400,
                f"Strategy {strategy.id} does not support {instrument}; allowed: {supported_instruments}",
            )
        if timeframe not in supported_timeframes:
            raise HTTPException(
                400,
                f"Strategy {strategy.id} does not support {timeframe}; allowed: {supported_timeframes}",
            )
        if timeframe != "1m":
            raise HTTPException(
                400,
                "Strategy deployments currently require timeframe=1m; 3m/5m live resampling is not implemented",
            )
        requested_params = dict(cfg.get("params") or {})
        unknown_params = sorted(set(requested_params) - set(strategy.parameter_schema or {}))
        if unknown_params:
            raise HTTPException(
                400,
                f"Strategy {strategy.id} received unknown parameter(s): {unknown_params}",
            )
        params = strategy.merged_params(requested_params)
        for param_name, spec in (strategy.parameter_schema or {}).items():
            value = params.get(param_name)
            value_type = str((spec or {}).get("type") or "")
            if value_type == "bool":
                if not isinstance(value, bool):
                    raise HTTPException(400, f"Strategy parameter {param_name} must be boolean")
            elif value_type == "int":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
                    raise HTTPException(400, f"Strategy parameter {param_name} must be an integer")
                value = int(value)
            elif value_type == "float":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise HTTPException(400, f"Strategy parameter {param_name} must be numeric")
                value = float(value)
            elif value_type == "str":
                if not isinstance(value, str) or not value.strip():
                    raise HTTPException(400, f"Strategy parameter {param_name} must be non-empty text")
            if value_type in ("int", "float"):
                if spec.get("min") is not None and value < spec["min"]:
                    raise HTTPException(400, f"Strategy parameter {param_name} must be >= {spec['min']}")
                if spec.get("max") is not None and value > spec["max"]:
                    raise HTTPException(400, f"Strategy parameter {param_name} must be <= {spec['max']}")
                params[param_name] = value
        doc = {
            "id": strategy.id,
            "name": strategy.name,
            "strategy_id": strategy.id,
            "strategy_version": strategy.version,
            "instrument": instrument,
            "timeframe": timeframe,
            "params": params,
            "config": {
                "strategy_id": strategy.id,
                "strategy_version": strategy.version,
                "instrument": instrument,
                "timeframe": timeframe,
                "params": params,
            },
            "source_kind": "strategy_library_snapshot",
        }
    else:
        raise HTTPException(400, "Deployment source_type must be strategy, preset or backtest_run")
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
        # Track B: pin today's premium-momentum LOCKED strikes (cap-exempt, like
        # open paper keys) so ATM drift / rebuilds never drop a locked feed.
        from app.premium_pin import premium_pin_keys
        pin_keys = await premium_pin_keys(db.premium_locks)
        option_keys = list(dict.fromkeys([
            *option_keys,
            *(str(k) for k in open_keys if k),
            *(str(k) for k in pin_keys if k),
        ]))
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
    # SAFETY INVARIANT (v0.56.0): `mode == "live"` is the real-money authorization,
    # and ONLY POST /live/enable (with its preflight + caps + confirm) may produce it.
    # Any transition OUT of ACTIVE must therefore demote a live deployment back to
    # paper — otherwise resume/re-pin/un-retire (which set status back to ACTIVE and
    # inspect nothing else) would silently re-authorize real trading against a
    # deployment the operator paused, retired, or whose code drifted. Going live again
    # requires an explicit /live/enable. This is the one place that guarantee is
    # enforced for every current and future pause/archive caller.
    if status != "ACTIVE" and str(doc.get("mode") or "").lower() == "live":
        doc["mode"] = "paper"
        _live = dict(doc.get("risk", {}).get("live") or {})
        _live["disabled_at"] = datetime.now(timezone.utc).isoformat()
        _live["last_block_reason"] = f"status_{status.lower()}"
        doc.setdefault("risk", {})["live"] = _live
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


# Historical-range contracts stage: how far past to_date to discover expiries —
# every day in the range needs its NEXT expiry, which can be up to a month out
# (BANKNIFTY is monthly-expiry-only).
EXPIRY_LOOKAHEAD_DAYS = 35


async def _start_historical_range_chain(
    *,
    instrument: str,
    from_date: str,
    to_date: str,
    spot_from: Optional[str],
    spot_to: Optional[str],
    include_options: bool,
    legs: List[str],
    chunk_days_spot: int,
) -> List[tuple]:
    """Historical-range twin of _start_catch_up_chain: create tracked run docs
    and launch ONE sequential background task over an explicit [from_date,
    to_date] window. spot_from/spot_to narrow stage 1 to the missing/under-
    captured days (None = spot already complete, stage 1 is skipped); the
    contracts stage backfills EXPIRED contracts for the range (the current-
    contract sync knows nothing about old expiries); the option stage band-fills
    the requested range. Upsert-only end to end — no stage can delete."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    created: List[tuple] = []

    spot_run_id = None
    eff_chunk_days = int(chunk_days_spot or 30)
    if spot_from and spot_to:
        spot_run_id = str(_uuid.uuid4())
        guidance = chunk_guidance_for_index(spot_from, spot_to, chunk_days_spot)
        eff_chunk_days = int(guidance["chunk_days"])
        await db.warehouse_runs.insert_one({
            "id": spot_run_id, "instrument": instrument,
            "source": "data_hygiene", "kind": "spot", "range_ingest": True,
            "started_at": now, "updated_at": now, "status": "queued",
            "from_date": spot_from, "to_date": spot_to,
            "days": guidance["calendar_days"], "chunk_days": eff_chunk_days,
            "chunk_mode": guidance["mode"], "total_chunks": guidance["estimated_api_calls"],
            "completed_chunks": 0, "progress_pct": 0,
            "total_fetched": 0, "candles_added": 0, "candles_updated": 0,
            "matched_existing": 0, "failed_chunks": [],
        })
        created.append(("spot", spot_run_id))

    contracts_run_id = None
    options_run_id = None
    if include_options:
        contracts_run_id = str(_uuid.uuid4())
        await db.warehouse_runs.insert_one({
            "id": contracts_run_id, "instrument": instrument,
            "source": "data_hygiene", "kind": "contracts", "range_ingest": True,
            "started_at": now, "updated_at": now, "status": "queued",
            "from_date": from_date, "to_date": to_date, "progress_pct": 0,
        })
        created.append(("contracts", contracts_run_id))

        options_run_id = str(_uuid.uuid4())
        await db.warehouse_runs.insert_one({
            "id": options_run_id, "instrument": instrument,
            "source": "data_hygiene", "kind": "option_candles", "range_ingest": True,
            "started_at": now, "updated_at": now, "status": "queued",
            "from_date": from_date, "to_date": to_date,
            "legs": legs, "progress_pct": 0,
        })
        created.append(("option_candles", options_run_id))

    asyncio.create_task(_run_historical_range_chain(
        instrument=instrument,
        from_date=from_date,
        to_date=to_date,
        spot_from=spot_from,
        spot_to=spot_to,
        eff_chunk_days=eff_chunk_days,
        spot_run_id=spot_run_id,
        contracts_run_id=contracts_run_id,
        options_run_id=options_run_id,
        legs=legs,
    ), name=f"range-ingest-{instrument}")

    return created


async def _run_historical_range_chain(
    *,
    instrument: str,
    from_date: str,
    to_date: str,
    spot_from: Optional[str],
    spot_to: Optional[str],
    eff_chunk_days: int,
    spot_run_id: Optional[str],
    contracts_run_id: Optional[str],
    options_run_id: Optional[str],
    legs: List[str],
) -> None:
    """Sequential historical-range worker: spot → expired+current contracts →
    band-exact option candles over the requested range. Never deletes; every
    write is an upsert. Each stage updates its own warehouse_runs doc, and a
    failed earlier stage skips the rest with a recorded reason."""
    db = get_db()

    # Stage 1: spot ingest for the missing/under-captured days (skipped when
    # the range's spot is already complete — an options-only repair).
    if spot_run_id and spot_from and spot_to:
        try:
            await run_upstox_index_ingest_job(spot_run_id, instrument, spot_from, spot_to, eff_chunk_days)
        except Exception as exc:
            log.exception("range-ingest spot failed for %s", instrument)
            await db.warehouse_runs.update_one(
                {"id": spot_run_id},
                {"$set": {"status": "failed",
                          "error": (str(exc) or type(exc).__name__)[:300],
                          "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
            await _fail_remaining_catch_up(db, contracts_run_id, options_run_id, "spot_ingest_failed")
            return

    if contracts_run_id is None:
        return  # spot-only mode

    # The option band needs spot candles for the range (the day's low/high set
    # the strike band). Unlike the catch-up chain we judge by what the
    # warehouse HOLDS, not what this run fetched — a re-run over an already-
    # ingested range must still be able to fill options.
    range_lo_ms = int((datetime.fromisoformat(from_date) - timedelta(hours=5, minutes=30))
                      .replace(tzinfo=timezone.utc).timestamp() * 1000)
    range_hi_ms = int((datetime.fromisoformat(to_date) + timedelta(days=1) - timedelta(hours=5, minutes=30))
                      .replace(tzinfo=timezone.utc).timestamp() * 1000)
    spot_in_range = await db.candles_1m.count_documents(
        {"instrument": instrument, "ts": {"$gte": range_lo_ms, "$lt": range_hi_ms}})
    if int(spot_in_range or 0) <= 0:
        await _fail_remaining_catch_up(
            db, contracts_run_id, options_run_id, "no_spot_candles_in_range")
        return

    # Stage 2: EXPIRED option contracts for the range (broker-discovered) +
    # current contract sync (covers a range that reaches into live expiries).
    # The band resolves each trading day to the NEXT expiry ON/AFTER it, which
    # usually falls AFTER to_date (e.g. a Mon-Tue range whose weekly expiry is
    # Thursday) — so the expiry discovery must look ahead past the range end
    # or the band plan reports every day's contracts as unresolved.
    from app.expired_contract_backfill import backfill_expired_option_contracts
    contracts_to = (datetime.fromisoformat(to_date)
                    + timedelta(days=EXPIRY_LOOKAHEAD_DAYS)).strftime("%Y-%m-%d")
    await db.warehouse_runs.update_one(
        {"id": contracts_run_id},
        {"$set": {"status": "running", "progress_pct": 10,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    try:
        backfill_result = await backfill_expired_option_contracts(
            db, instrument,
            from_date=from_date, to_date=contracts_to,
            max_expiries=200,
            confirm_large_fetch=True,
        )
        current_upserted = 0
        try:
            items = await upstox_client.fetch_option_contracts(instrument)
            contract_result = await upsert_option_contracts(db, items)
            current_upserted = int(contract_result.get("upserted")
                                   or contract_result.get("inserted") or 0)
        except Exception:
            # Best-effort: an old range only needs the expired backfill.
            log.warning("range-ingest current-contract sync failed for %s (non-fatal)",
                        instrument)
        await db.warehouse_runs.update_one(
            {"id": contracts_run_id},
            {"$set": {
                "status": str(backfill_result.get("status") or "ok"),
                "progress_pct": 100,
                "expiry_count": int(backfill_result.get("expiry_count") or 0),
                "fetched_contracts": int(backfill_result.get("fetched_contracts") or 0),
                "upserted": int(backfill_result.get("upserted") or 0) + current_upserted,
                "linked_helper_run_id": backfill_result.get("run_id"),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        if str(backfill_result.get("status")) == "failed":
            await _fail_remaining_catch_up(db, None, options_run_id, "contract_backfill_failed")
            return
    except Exception as exc:
        log.exception("range-ingest contract backfill failed for %s", instrument)
        await db.warehouse_runs.update_one(
            {"id": contracts_run_id},
            {"$set": {"status": "failed",
                      "error": (str(exc) or type(exc).__name__)[:300],
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        await _fail_remaining_catch_up(db, None, options_run_id, "contract_backfill_failed")
        return

    # Stage 3: band-exact option fill over the REQUESTED range (the catch-up
    # chain uses the rolling window here; a historical ingest targets exactly
    # what the user asked for).
    try:
        plan = await build_band_fetch_plan(db, instrument, from_date, to_date, legs=legs)
        chunk_days = 5
        await db.warehouse_runs.update_one(
            {"id": options_run_id},
            {"$set": {"status": "running", "progress_pct": 5, "chunk_days": chunk_days,
                      "band": True, "from_date": from_date,
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
            log.info("range-ingest %s: %d band pair(s) ledgered as broker-empty",
                     instrument, recorded)
    except HTTPException as exc:
        await db.warehouse_runs.update_one(
            {"id": options_run_id},
            {"$set": {"status": "failed", "error": str(exc.detail)[:300],
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception as exc:
        log.exception("range-ingest option fetch failed for %s", instrument)
        await db.warehouse_runs.update_one(
            {"id": options_run_id},
            {"$set": {"status": "failed",
                      "error": (str(exc) or type(exc).__name__)[:300],
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
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
