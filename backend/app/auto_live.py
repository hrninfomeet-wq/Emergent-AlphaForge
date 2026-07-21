"""Continuous LIVE sink — turn an armed deployment's confirmed signal into a
REAL order.

This module is a structural clone of ``app.paper_auto`` (read it alongside this
file). The ONE substantive difference: the success path places a real order
through the executor choke-point (``executor.place_deployed_order``, injected as
``place_fn``) instead of inserting a paper trade. Everything else — the atomic
claim, the guard-clause prologue, the signal lifecycle transitions, the trade
journal fields — mirrors paper_auto so journaling/exit semantics stay aligned.

Authorization is the per-deployment ``risk.live`` arm (``is_deployment_live_allowed``);
the ``LIVE_AUTOPLACE_ARMED`` env master gate is the executor's transmit-boundary
concern, NOT checked here (an armed-but-env-unset deployment dry-runs through the
executor, returning ``dry_run=True``).

Paper↔live mutual exclusion: claim/release reuse the SAME ``paper_trade_claim``
field as paper_auto, so a single signal can be claimed by paper OR live, never
both — there is exactly one trade per signal regardless of which sink fires.

Entry-price correctness (stricter than paper): the live ENTRY ref_ltp must be the
OPTION premium from a FRESH live tick (``resolve_premium``'s ``fresh is True``).
A stale tick / last-candle / absent tick is REFUSED — a stale ref_ltp would
mis-band the live LMT order. Never spot, never a stale candle.

Protection floor: a deployed position is NEVER unprotected. If no premium stop
and no spot stop is configured, ``resolve_live_exit_plan`` seeds the deep-default
50% catastrophe premium stop (mirrors ``live_broker._GUARD_DEFAULT_STOP_PCT``).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from app.live.mode import is_deployment_live_allowed
from app.paper_auto import compute_auto_risk_levels, compute_spot_exit_levels
from app.live.option_premium import resolve_premium
from app.signal_lifecycle import SignalStateError, transition_signal

log = logging.getLogger(__name__)

# Deep-default catastrophe premium stop (% of entry premium). Mirrors the
# manual-path constant in routers/live_broker.py (_GUARD_DEFAULT_STOP_PCT = 50.0)
# so a deployed position is NEVER unprotected; duplicated as a literal rather than
# imported so this host-testable module never pulls in the router.
_GUARD_DEFAULT_STOP_PCT = 50.0

TickLookup = Callable[[str], Optional[Dict[str, Any]]]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. enable predicate
# ---------------------------------------------------------------------------

def auto_live_enabled(deployment: Dict[str, Any], now_utc: datetime, *, connected: bool) -> bool:
    """True iff the deployment is armed, within the arm window, and connected.

    Delegates to ``is_deployment_live_allowed``. Does NOT check the
    ``LIVE_AUTOPLACE_ARMED`` env master gate — that is the executor's
    transmit-boundary concern (an enabled deployment may still dry-run)."""
    return is_deployment_live_allowed(deployment, now_utc, connected=connected)[0]


# ---------------------------------------------------------------------------
# 2. atomic claim / release (SAME paper_trade_claim field → paper↔live exclusion)
# ---------------------------------------------------------------------------

async def claim_signal_for_live_trade(db: Any, signal_id: str, source: str) -> bool:
    """Atomically claim the right to create THE trade for a signal.

    Copied verbatim from ``paper_auto.claim_signal_for_paper_trade`` and uses the
    SAME ``paper_trade_claim`` field name. This is intentional: it gives paper↔live
    mutual exclusion — a signal can be claimed by paper OR live, never both. The
    filter requires the signal to still be CONFIRMED with no trade and no prior
    claim; Mongo's single-document update is atomic."""
    res = await db.signals.update_one(
        {
            "id": signal_id,
            "state": "CONFIRMED",
            "paper_trade_id": {"$exists": False},
            "paper_trade_claim": {"$exists": False},
        },
        {"$set": {"paper_trade_claim": {
            "source": source,
            "at": datetime.now(timezone.utc).isoformat(),
        }}},
    )
    return int(getattr(res, "matched_count", 0) or 0) == 1


async def release_live_trade_claim(db: Any, signal_id: str) -> None:
    """Release a claim after a journaled (non-crash) failure so the signal can be
    retried by either path. Mirrors ``paper_auto.release_paper_trade_claim``."""
    await db.signals.update_one({"id": signal_id}, {"$unset": {"paper_trade_claim": ""}})


# ---------------------------------------------------------------------------
# 3. fresh-premium entry ref_ltp (stricter than paper: fresh tick ONLY)
# ---------------------------------------------------------------------------

def resolve_live_entry_ref_ltp(
    db: Any,
    instrument_key: str,
    *,
    latest_tick_lookup: Optional[TickLookup] = None,
    now_ts: Optional[float] = None,
) -> Optional[float]:
    """Resolve the option premium for the contract from a FRESH live tick.

    Built on ``option_premium.resolve_premium`` but returns the premium ONLY when
    ``fresh is True`` (a live tick inside the freshness window). A stale tick, a
    last-candle (``fresh=False``), or an absent tick → ``None`` (REFUSE). Never
    spot, never a stale candle: a stale ref_ltp would mis-band the live LMT."""
    if not instrument_key:
        return None
    tick = latest_tick_lookup(instrument_key) if latest_tick_lookup is not None else None
    ts = now_ts if now_ts is not None else _now_utc().timestamp()
    res = resolve_premium(
        instrument_key=instrument_key,
        tick=tick,
        candle_close=None,   # NEVER a last candle for a live entry band
        now_ts=ts,
    )
    if res.get("fresh") is True and res.get("premium") is not None:
        return float(res["premium"])
    return None


# ---------------------------------------------------------------------------
# 4. capped lots (user's fixed lots, NOT sizing replay)
# ---------------------------------------------------------------------------

def resolve_capped_lots(deployment: Dict[str, Any], account_max: int) -> int:
    """User's fixed ``risk.live.lots`` clamped to the account ceiling.

    ``max(1, min(int(lots), int(account_max)))``; non-numeric / missing / zero
    lots → 1. NOT the sizing-replay path (live uses the user's fixed lot count)."""
    lots = ((deployment.get("risk") or {}).get("live") or {}).get("lots")
    try:
        lots = int(lots)
    except (TypeError, ValueError):
        lots = 1
    try:
        cap = int(account_max)
    except (TypeError, ValueError):
        cap = 1
    return max(1, min(lots, cap))


# ---------------------------------------------------------------------------
# 5. exit plan for the guard + the live order builder
# ---------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        p = float(value)
        return p if p > 0 else None
    except (TypeError, ValueError):
        return None


def resolve_live_exit_plan(signal_doc: Dict[str, Any], deployment: Dict[str, Any]) -> Dict[str, Any]:
    """Build the exit plan carried to the live order builder + the software guard.

    Returns::

        {"levels": {"stop_pct","target_pct","stop_pts","target_pts","trail"},
         "spot_exit": <dict|None>, "time_stop_minutes": <num|None>}

    Premium SL/TP source precedence mirrors paper's ``compute_auto_risk_levels``:
    strategy hints (``signal.risk_hints.stop_pct/target_pct``) win, else deployment
    ``risk.auto_paper_stop_pct/target_pct`` (and ``_pts`` variants if present),
    with pts taking precedence over pct on each leg. The chosen pct/pts is passed
    through on ``levels`` (the guard/order-builder derive the absolute premium
    levels from the live ref_ltp at arm time).

    ``trail`` passes through ``deployment.risk.exit_controls`` (or None). ``spot_exit``
    reuses paper's ``compute_spot_exit_levels``. ``time_stop_minutes`` is the
    strategy hint.

    DEEP-DEFAULT FLOOR: if there is NO premium stop (stop_pct and stop_pts both
    None), set ``levels["stop_pct"] = 50.0`` — independent of any spot stop. The
    premium stop is what lets the software guard register the position
    (``build_monitor_state`` requires a premium input); the spot-mirror/time-stop
    exits are additive. A deployed position is NEVER left unprotected."""
    risk = deployment.get("risk") or {}
    hints = signal_doc.get("risk_hints") or {}

    # --- premium target: hint pct > deployment pts > deployment pct ---
    target_pct: Optional[float] = None
    target_pts: Optional[float] = None
    if _num(hints.get("target_pct")) is not None:
        target_pct = float(hints.get("target_pct"))
    elif _num(risk.get("auto_paper_target_pts")) is not None:
        target_pts = float(risk.get("auto_paper_target_pts"))
    elif _num(risk.get("auto_paper_target_pct")) is not None:
        target_pct = float(risk.get("auto_paper_target_pct"))

    # --- premium stop: hint pct > deployment pts > deployment pct ---
    stop_pct: Optional[float] = None
    stop_pts: Optional[float] = None
    if _num(hints.get("stop_pct")) is not None:
        stop_pct = float(hints.get("stop_pct"))
    elif _num(risk.get("auto_paper_stop_pts")) is not None:
        stop_pts = float(risk.get("auto_paper_stop_pts"))
    elif _num(risk.get("auto_paper_stop_pct")) is not None:
        stop_pct = float(risk.get("auto_paper_stop_pct"))

    ec = risk.get("exit_controls")
    trail = ec if ec else None

    levels: Dict[str, Any] = {
        "stop_pct": stop_pct,
        "target_pct": target_pct,
        "stop_pts": stop_pts,
        "target_pts": target_pts,
        "trail": trail,
    }

    spot_exit = compute_spot_exit_levels(signal_doc)

    plan: Dict[str, Any] = {
        "levels": levels,
        "spot_exit": spot_exit,
        "time_stop_minutes": hints.get("time_stop_minutes"),
    }

    # DEEP-DEFAULT FLOOR — never leave a deployed position unprotected. A premium
    # stop is ALWAYS seeded when none is configured, INDEPENDENT of any spot stop:
    # build_monitor_state needs a premium input to register the position at all, and
    # every live position must carry a premium catastrophe stop as the ultimate
    # downside net. The spot-mirror + time-stop exits remain additive on top.
    has_premium_stop = levels["stop_pct"] is not None or levels["stop_pts"] is not None
    if not has_premium_stop:
        levels["stop_pct"] = _GUARD_DEFAULT_STOP_PCT

    return plan


# ---------------------------------------------------------------------------
# 6. orchestrator
# ---------------------------------------------------------------------------

async def auto_live_trade_for_signal(
    db: Any,
    deployment: Dict[str, Any],
    signal_doc: Dict[str, Any],
    *,
    latest_tick_lookup: Optional[TickLookup] = None,
    now_utc: Optional[datetime] = None,
    place_fn: Optional[Callable] = None,
    arm_for: Optional[Callable] = None,
    account_max: int = 20,
    throttle: Any = None,
    allow_fn: Optional[Callable] = None,
    client: Any = None,
    intent_store: Any = None,
    engine: Any = None,
    search_fn: Optional[Callable] = None,
    connected: bool = True,
    band_pct: float = 5.0,
    uid: str = "",
    actid: str = "",
) -> Dict[str, Any]:
    """Place a REAL order for a clean CONFIRMED signal on an armed deployment.

    Structural clone of ``paper_auto.auto_paper_trade_for_signal`` whose success
    path inserts a ``live_trades`` doc + advances the signal CONFIRMED → TRIGGERED
    → ACTIVE, but whose creation side-effect is a real order through ``place_fn``
    (defaults to ``executor.place_deployed_order``).

    Returns ``{created, trade_id?, norenordno?, entry_price?, lots?, reason?,
    error?, dry_run?, paused?}``.
    """
    now = now_utc or _now_utc()
    signal_id = str(signal_doc.get("id") or "")

    # (a) authorization
    if not auto_live_enabled(deployment, now, connected=connected):
        return {"created": False, "reason": "auto_live_disabled"}

    # (b) signal must be a clean CONFIRMED with no existing live trade
    if str(signal_doc.get("state") or "").upper() != "CONFIRMED":
        return {"created": False, "reason": f"signal_not_confirmed ({signal_doc.get('state')})"}
    if signal_doc.get("blocked"):
        return {"created": False, "reason": "signal_blocked"}
    if signal_doc.get("live_trade_id"):
        return {"created": False, "reason": "live_trade_already_exists",
                "trade_id": signal_doc.get("live_trade_id")}

    # (c) per-deployment caps governor
    from app.live_deploy_governor import check_live_caps
    capped = resolve_capped_lots(deployment, account_max)
    gov = await check_live_caps(db, deployment, capped_lots=capped, now_utc=now)
    if not gov.get("allow"):
        if gov.get("pause"):
            dep_id = str(deployment.get("id") or "")
            # Read-modify-write the whole `risk` sub-object so the nested
            # risk.live disarm lands the same way under real Mongo and the
            # in-memory test FakeDB (which doesn't interpret dotted $set keys).
            stored = await db.strategy_deployments.find_one({"id": dep_id})
            risk = dict((stored or deployment).get("risk") or {})
            live = dict(risk.get("live") or {})
            # `status: "PAUSED"` in the same write below is what actually stops
            # re-entry — evaluate_all only iterates {"status": "ACTIVE"}. The old
            # `armed = False` write was the belt to that braces; with the arm gone,
            # this field is audit-only and must never be read as authorization.
            live["last_block_reason"] = "daily_loss"
            live["disabled_at"] = datetime.now(timezone.utc).isoformat()
            risk["live"] = live
            # DEMOTE to paper in the same write: `mode` alone authorizes real
            # orders, and /resume restores ACTIVE without inspecting why the
            # deployment paused. Without the demotion an ordinary Resume after a
            # daily-loss breach would re-authorize real money with no fresh
            # /live/enable consent (release-audit blocker C4). Going live again
            # after a breach must be an explicit /live/enable decision.
            await db.strategy_deployments.update_one(
                {"id": dep_id},
                {"$set": {
                    "status": "PAUSED",
                    "mode": "paper",
                    "risk": risk,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
            log.warning("auto-live PAUSED deployment %s on %s", dep_id, gov.get("reason"))
            return {"created": False, "reason": gov.get("reason"), "paused": True}
        return {"created": False, "reason": gov.get("reason")}

    # (d) option contract present
    contract_doc = signal_doc.get("option_contract") or {}
    instrument_key = str(contract_doc.get("instrument_key") or "")
    if not instrument_key:
        return {"created": False, "reason": "no_option_contract"}

    # (e) ATOMIC CLAIM before any place work — paper↔live mutual exclusion.
    if not await claim_signal_for_live_trade(db, signal_id, "auto_live"):
        return {"created": False, "reason": "signal_claimed_elsewhere"}

    # (f) fresh option premium for the entry band (stale/absent → refuse).
    now_ts = now.timestamp()
    ref_ltp = resolve_live_entry_ref_ltp(
        db, instrument_key, latest_tick_lookup=latest_tick_lookup, now_ts=now_ts)
    if ref_ltp is None:
        await release_live_trade_claim(db, signal_id)
        await db.signals.update_one(
            {"id": signal_id},
            {"$set": {"live_trade_error": "live_entry_premium_unavailable_or_stale",
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        log.warning("auto-live skipped for signal %s: no fresh premium for %s",
                    signal_id, instrument_key)
        return {"created": False, "error": "live_entry_premium_unavailable_or_stale"}

    # (f2) Track B last-line re-check: the momentum trigger was decided on the
    # bar close; the premium may have collapsed in the seconds before placement.
    # Re-verify against the FRESH entry tick (ref_ltp IS premium_now). On
    # failure: journal a distinct refusal, release the claim AND the session
    # latch so a later bar may re-trigger. Marginally more conservative than
    # the backtest's trigger-bar-close fill — intentional (spec §5.4).
    pm = signal_doc.get("premium_momentum") or {}
    if pm.get("ref_premium") is not None:
        from app.premium_momentum import momentum_triggered
        dep_params = dict(deployment.get("params") or {})
        mom_pct = dep_params.get("momentum_pct")
        mom_pts = dep_params.get("momentum_pts")
        if mom_pts is not None:
            # Explicit precedence: a user-set momentum_pts wins over momentum_pct
            # (the registration schema DEFAULTS pct=15.0, so both-set is the
            # normal shape of a pts deployment — momentum_triggered raises on
            # both-set). Same rule as evaluate_premium_momentum_bar.
            mom_pct = None
        if not momentum_triggered(premium_now=float(ref_ltp),
                                  ref_premium=float(pm["ref_premium"]),
                                  pct=mom_pct, pts=mom_pts):
            await release_live_trade_claim(db, signal_id)
            await db.signals.update_one(
                {"id": signal_id},
                {"$set": {"live_trade_error": "premium_trigger_not_met",
                          "live_intended": {"ref_premium": pm["ref_premium"],
                                            "premium_at_entry": float(ref_ltp)},
                          "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
            from datetime import timedelta as _td
            _sess = (datetime.now(timezone.utc) + _td(hours=5, minutes=30)).strftime("%Y-%m-%d")
            _leg = str(pm.get("leg") or "")
            if str(dep_params.get("leg_mode") or "first_to_trigger").lower() == "both" and _leg:
                # 5B A4: release ONLY this leg's latch — a global unlatch here
                # would silently release the OTHER leg's completed latch too
                # (recon anchor #2 flagged exactly this two-signal seam).
                from app.premium_lock_store import unlatch_trigger_leg
                await unlatch_trigger_leg(db.premium_locks,
                                          deployment_id=str(deployment.get("id") or ""),
                                          session_date=_sess, leg=_leg)
            else:
                from app.premium_lock_store import unlatch_trigger
                await unlatch_trigger(db.premium_locks,
                                      deployment_id=str(deployment.get("id") or ""),
                                      session_date=_sess)
            log.warning("auto-live refused for signal %s: premium %.2f fell back "
                        "below the trigger (ref %.2f)",
                        signal_id, float(ref_ltp), float(pm["ref_premium"]))
            return {"created": False, "reason": "premium_trigger_not_met"}

    # (g) capped lots + exit plan
    plan = resolve_live_exit_plan(signal_doc, deployment)

    # (h) authorization callable + arm callable for the executor.
    if allow_fn is None:
        allow_fn = lambda: is_deployment_live_allowed(deployment, now, connected=connected)  # noqa: E731
    # The per-deployment catastrophe band reaches arm_for via THIS per-signal call —
    # the live context (build_live_deploy_context) is deployment-agnostic and binds
    # only client/uid/actid. Forward risk.live.catastrophe_stop_pct/target_pct so the
    # resting broker OCO uses the deployment's configured band (None → band defaults).
    risk_live = (deployment.get("risk") or {}).get("live") or {}
    arm = arm_for(
        plan, signal_doc, ref_ltp,
        catastrophe_stop_pct=risk_live.get("catastrophe_stop_pct"),
        catastrophe_target_pct=risk_live.get("catastrophe_target_pct"),
    ) if arm_for is not None else None

    # (i) build the option-leg contract the way the manual place route does, then
    #     place through the executor choke-point (side="B" ALWAYS — long-only; the
    #     CE/PE direction selects which leg we buy).
    dep_id = str(deployment.get("id") or "")
    contract = {
        "underlying": signal_doc.get("instrument"),
        "strike": contract_doc.get("strike"),
        "side": signal_doc.get("direction"),       # CE / PE — the leg, not buy/sell
        "expiry_date": contract_doc.get("expiry_date"),
    }
    if place_fn is None:
        from app.live import executor as _executor_mod
        place_fn = _executor_mod.place_deployed_order

    result = await place_fn(
        contract,
        side="B",
        ref_ltp=ref_ltp,
        band_pct=band_pct,
        levels=plan["levels"],
        capped_lots=capped,
        client=client,
        intent_store=intent_store,
        engine=engine,
        search_fn=search_fn,
        arm=arm,
        allow_fn=allow_fn,
        throttle=throttle,
        account_max_lots=account_max,
        deployment_id=dep_id,
        uid=uid,
        actid=actid,
    )

    # (j) branch on the executor result.
    # --- dry-run (offline-first; env master gate unset): no trade, audit + release.
    if result.get("dry_run"):
        await release_live_trade_claim(db, signal_id)
        await db.signals.update_one(
            {"id": signal_id},
            {"$set": {"live_intended": {
                "would_send": result.get("would_send"),
                "ref_ltp": ref_ltp,
                "lots": capped,
                "at": datetime.now(timezone.utc).isoformat(),
            },
                "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        return {"created": False, "dry_run": True}

    # --- not placed (throttle / reject / gate block): no trade, journal + release.
    if not result.get("placed"):
        reason = result.get("reason")
        await release_live_trade_claim(db, signal_id)
        await db.signals.update_one(
            {"id": signal_id},
            {"$set": {"live_trade_error": reason,
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        log.warning("auto-live not placed for signal %s: %s", signal_id, reason)
        return {"created": False, "reason": reason}

    # --- placed & protected → journal a live_trades doc + advance the signal.
    iso_now = datetime.now(timezone.utc).isoformat()

    # Derive absolute premium stop/target from the entry ref_ltp where possible
    # (same level math as paper). When neither resolves, fall back to the pct.
    stop_price, target_price = compute_auto_risk_levels(
        float(ref_ltp), signal_doc.get("risk_hints"), deployment.get("risk"),
    )
    levels = plan["levels"]
    risk_field: Dict[str, Any] = {}
    if stop_price is not None:
        risk_field["stop_price"] = stop_price
    elif levels.get("stop_pct") is not None:
        risk_field["stop_pct"] = levels.get("stop_pct")
    elif levels.get("stop_pts") is not None:
        risk_field["stop_pts"] = levels.get("stop_pts")
    if target_price is not None:
        risk_field["target_price"] = target_price
    elif levels.get("target_pct") is not None:
        risk_field["target_pct"] = levels.get("target_pct")
    elif levels.get("target_pts") is not None:
        risk_field["target_pts"] = levels.get("target_pts")

    lot_size = max(1, int(contract_doc.get("lot_size") or 1))
    import uuid as _uuid
    trade: Dict[str, Any] = {
        "id": str(_uuid.uuid4()),
        "signal_id": signal_doc.get("id"),
        "instrument": signal_doc.get("instrument"),
        "direction": signal_doc.get("direction"),
        "strategy_id": signal_doc.get("strategy_id"),
        "instrument_key": instrument_key,
        "trading_symbol": contract_doc.get("trading_symbol") or "",
        "lots": capped,
        "lot_size": lot_size,
        "quantity": capped * lot_size,
        "entry_price": float(ref_ltp),
        "norenordno": result.get("norenordno"),
        "cid": result.get("cid"),
        # Resting broker OCO backstop (PC-down catastrophe net). When the executor's
        # best-effort OCO place succeeded the result carries its al_id; otherwise the
        # position has NO broker-side backstop (only the software guard while alive).
        "oco_al_id": result.get("oco_al_id"),
        "oco_error": None if result.get("oco_al_id") else "no_broker_backstop",
        "deployment_id": dep_id,
        "source": "auto_live_on_signal",
        "risk": risk_field,
        "spot_exit": plan.get("spot_exit"),
        "time_stop_minutes": plan.get("time_stop_minutes"),
        "verdicts": result.get("verdicts"),
        "status": "OPEN",
        "unrealized_pnl": 0.0,
        "realized_pnl": None,
        "created_at": iso_now,
        "updated_at": iso_now,
    }
    if signal_doc.get("risk_hints"):
        trade["risk_hints"] = signal_doc["risk_hints"]
    await db.live_trades.insert_one(trade)

    if pm.get("ref_premium") is not None:
        # Track B: adopt the placed order into today's session lock so recovery
        # rehydrates with the PERSISTED entry premium (mark_entered no-ops when
        # the lock is gone). A lock-write failure must never fail a PLACED order.
        try:
            from datetime import timedelta as _td
            _sess = (datetime.now(timezone.utc) + _td(hours=5, minutes=30)).strftime("%Y-%m-%d")
            _leg = str(pm.get("leg") or "")
            _both = str((deployment.get("params") or {}).get("leg_mode")
                        or "first_to_trigger").lower() == "both"
            if _both and _leg:
                # 5B A4: per-leg entry adoption — the leg name keys recovery's
                # per-leg rehydration (B7). The legacy session-global fields
                # stay untouched in both-mode (they'd be ambiguous with two
                # concurrent entries).
                from app.premium_lock_store import mark_entered_leg
                await mark_entered_leg(db.premium_locks, deployment_id=dep_id,
                                       session_date=_sess, leg=_leg,
                                       norenordno=str(result.get("norenordno") or ""),
                                       entry_premium=float(ref_ltp))
            else:
                from app.premium_lock_store import mark_entered
                await mark_entered(db.premium_locks, deployment_id=dep_id,
                                   session_date=_sess,
                                   norenordno=str(result.get("norenordno") or ""),
                                   entry_premium=float(ref_ltp))
        except Exception:
            log.exception("premium-momentum mark_entered failed for signal %s", signal_id)

    snapshot = {"live": {
        "trade_id": trade["id"],
        "norenordno": result.get("norenordno"),
        "cid": result.get("cid"),
        "entry_price": float(ref_ltp),
        "lots": capped,
        "stop": risk_field.get("stop_price") or risk_field.get("stop_pct") or risk_field.get("stop_pts"),
        "target": risk_field.get("target_price") or risk_field.get("target_pct") or risk_field.get("target_pts"),
        "spot_exit": plan.get("spot_exit"),
        "at": iso_now,
    }}
    try:
        updated = transition_signal(signal_doc, "TRIGGERED", reason="auto_live_on_signal", snapshot=snapshot)
        updated = transition_signal(updated, "ACTIVE", reason="auto_live_trade_open", snapshot=snapshot)
    except SignalStateError as exc:
        # The order is live; keep the signal as-is but record the link + anomaly.
        log.warning("auto-live state transition failed for signal %s: %s", signal_id, exc)
        updated = dict(signal_doc)
        updated["live_trade_state_error"] = str(exc)[:240]
    updated["live_trade_id"] = trade["id"]
    updated["live"] = snapshot["live"]
    # The in-memory doc predates the claim write — mirror it so the full-doc
    # replace doesn't silently drop the audit marker (as paper does).
    updated["paper_trade_claim"] = {"source": "auto_live", "at": iso_now}
    await db.signals.replace_one({"id": signal_id}, updated, upsert=False)

    log.info("auto-live trade %s opened for signal %s (%s @ %.2f x %s lots, norenordno=%s)",
             trade["id"], signal_id, trade.get("trading_symbol") or instrument_key,
             float(ref_ltp), capped, result.get("norenordno"))
    return {"created": True, "trade_id": trade["id"], "norenordno": result.get("norenordno"),
            "entry_price": float(ref_ltp), "lots": capped}
