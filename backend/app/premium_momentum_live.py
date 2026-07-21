# backend/app/premium_momentum_live.py
"""Track B per-bar session engine for premium-momentum deployments.

Called from the deployment evaluator's Track B branch once per closed bar. Owns
the session state machine over the premium_locks store:

    pre_reference -> (lock strikes at the ref bar's close, capture refs from
    FRESH ticks) -> monitoring -> triggered (first side to cross) -> the
    EVALUATOR journals the signal + latches; entry/exit/done transitions are
    driven by auto_live + the guard's confirmed-flat hook, never here.

Uses the SAME pure helpers as the backtest (lock_reference_strike,
momentum_triggered) and the SAME live price contract as entries
(option_premium.resolve_premium, fresh-only). Stale/absent ticks HOLD — this
module never invents a price. It does NOT latch the trigger (spec: latch only
after the signal journals clean) and never touches order placement.

Phase 5B (docs/superpowers/plans/2026-07-15-premium-momentum-phase5b-
execution.md Task A3) adds four things, all default-OFF and additive:
  - ``leg_mode="both"``: CE and PE primaries resolve INDEPENDENTLY instead of
    one whole-session winner. ``leg_mode="first_to_trigger"`` (the default)
    is BYTE-IDENTICAL to the pre-5B engine — see the terminal check below,
    pinned verbatim.
  - One-shot lazy reversal legs (``lce``/``lpe``). They are ARMED by the
    guard-close hook (Task B6, out of this module's scope — this module only
    reads the ``lazy_armed_<side>`` flag it sets); this module performs the
    fresh strike pickup + ref capture once armed, and monitors for the lazy
    leg's own trigger.
  - An ``entry_cutoff`` gate (IST "HH:MM"): no NEW primary triggers and no new
    lazy strike locks at/after it.
  - A VIX gate via an injected, already-resolved value: this module stays a
    pure function of its inputs and never fetches VIX itself; the EVALUATOR
    resolves the asof value and passes it in as ``vix``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.live.option_premium import resolve_premium
from app.premium_lock_store import (
    capture_ref, capture_ref_leg, get_lock, get_or_create_lock, mark_done,
)
from app.premium_momentum import normalize_hhmm, lock_reference_strike, momentum_triggered

log = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)

#: Contract fields captured for a LAZY leg. lce/lpe have no nested contract
#: sub-doc like the ce/pe primaries do (premium_lock_store.py's Phase-5B
#: header comment) -- these persist flat, prefixed ``{leg}_<field>``.
_LAZY_CONTRACT_FIELDS = ("instrument_key", "strike", "side", "moneyness",
                        "expiry_date", "lot_size", "tsym")


def _ist_hhmm(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc) + IST_OFFSET
    return dt.strftime("%H:%M")


def _ist_session_date(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc) + IST_OFFSET
    return dt.strftime("%Y-%m-%d")


def _fresh_premium(latest_tick_map: Callable[[], Dict[str, Any]],
                   instrument_key: str, now_ts: float) -> Optional[Dict[str, Any]]:
    """FRESH tick premium via the canonical resolver, else None (HOLD).

    The returned ``ts`` is the tick timestamp in epoch MILLISECONDS — the unit
    persisted as ``{side}_ref_ts`` in the lock doc (matching ``candle_ts`` and
    the raw tick feed). ``resolve_premium`` normalizes its ``ts`` to SECONDS,
    so it is converted back to ms here."""
    try:
        tick = (latest_tick_map() or {}).get(instrument_key)
    except Exception:
        log.warning("premium_momentum_live: latest_tick_map failed for %s — "
                    "treating as no tick (HOLD)", instrument_key, exc_info=True)
        tick = None
    res = resolve_premium(instrument_key=instrument_key, tick=tick,
                          candle_close=None, now_ts=now_ts)
    if res.get("fresh") is True and res.get("premium") is not None:
        return {"premium": float(res["premium"]),
                "ts": int(round(float(res.get("ts") or 0) * 1000.0))}
    return None


def _sides(params: Dict[str, Any]) -> List[str]:
    p = str(params.get("side") or "first_to_trigger").lower()
    if p == "ce":
        return ["CE"]
    if p == "pe":
        return ["PE"]
    return ["CE", "PE"]


def _leg_mode(params: Dict[str, Any]) -> str:
    return str(params.get("leg_mode") or "first_to_trigger").lower()


def _primary_leg(side: str) -> str:
    return "pce" if str(side).upper() == "CE" else "ppe"


def _leg_resolved(lock: Dict[str, Any], prefix: str) -> bool:
    """True once this leg has already latched a trigger or completed an entry
    -- no more per-bar processing needed for it. Drives both-mode's per-leg
    skip AND the lazy legs' one-shot guard. ``prefix`` is the FIELD prefix
    (``ce``/``pe`` for primaries — pce/ppe alias that storage — or
    ``lce``/``lpe`` for lazy legs), not the leg name the evaluator uses."""
    return bool(lock.get(f"{prefix}_triggered")) or bool(lock.get(f"{prefix}_entered_norenordno"))


#: Live-guard / SL-monitor stop-class exit reasons (runtime._live_guard_on_close).
LIVE_STOP_CLASS_REASONS = frozenset(
    {"stop", "breakeven_stop", "trailing_stop", "spot_stop_hit"})
#: Paper exit-marker stop-class reasons. The premium stop (incl. a ratcheted
#: breakeven/trailing stop, which all close via risk_exit_reason) resolves to
#: ``stop_hit`` (execution_policy.tick_exit_reason). target_hit / time_stop / EOD
#: are deliberately NOT here — the blueprint arms the lazy leg ONLY on an SL hit.
PAPER_STOP_CLASS_REASONS = frozenset({"stop_hit"})


def lazy_arm_side(
    closed_leg: str,
    *,
    is_stop_class: bool,
    params: Dict[str, Any],
    now_hhmm: str,
) -> Optional[str]:
    """Decide whether a closing PRIMARY leg arms the opposite-side lazy leg, and
    which side. PURE (no I/O) so it is the single source of truth for the arming
    GATE across both rails: the live guard-close hook (runtime._live_guard_on_close)
    and the paper exit marker (paper_auto.mark_open_deployment_trades) both call it.

    Each rail classifies its OWN stop reasons — the live guard and the paper marker
    emit different reason strings (see LIVE_/PAPER_STOP_CLASS_REASONS) — and passes
    the result as ``is_stop_class``; keeping the reason sets per-rail while sharing
    the gate is what prevents the paper/live arming decision from drifting.

    Returns the LAZY leg's side ("ce"/"pe" = OPPOSITE the stopped primary), or
    None when this close must not arm anything (blueprint §4: STOP-class primary
    only, both-mode + lazy configured, before the entry cutoff)."""
    if closed_leg not in ("pce", "ppe"):
        return None
    if not is_stop_class:
        return None
    if not bool(params.get("lazy_enabled")):
        return None
    # A silently never-triggering lazy leg would pin subscriptions for nothing.
    if params.get("lazy_momentum_pct") is None and params.get("lazy_momentum_pts") is None:
        return None
    try:
        cutoff = normalize_hhmm(params.get("entry_cutoff"))
    except ValueError:
        cutoff = None
    if cutoff and str(now_hhmm) >= cutoff:
        return None
    # A stopped CALL (pce) arms the lazy PUT and vice versa.
    return "pe" if closed_leg == "pce" else "ce"


def _lazy_pending(lock: Dict[str, Any], params: Dict[str, Any]) -> bool:
    """True when a lazy leg has been armed (by the guard-close hook, B6) but
    hasn't yet been picked up/resolved. The both-mode terminal check must NOT
    report holding_position while this is true, or the pickup below would
    never get a chance to run."""
    if not bool(params.get("lazy_enabled") or False):
        return False
    for s in ("ce", "pe"):
        if lock.get(f"lazy_armed_{s}") and not _leg_resolved(lock, f"l{s}"):
            return True
    return False


def _leg_contract(lock: Dict[str, Any], leg: str) -> Dict[str, Any]:
    """Reconstruct a lazy leg's contract dict from its flat fields (mirrors
    the nested ``lock.get(s)`` sub-doc the primaries use for their outcome)."""
    return {k: lock.get(f"{leg}_{k}") for k in _LAZY_CONTRACT_FIELDS
            if lock.get(f"{leg}_{k}") is not None}


async def _capture_lazy_contract(locks_col: Any, *, deployment_id: str, session_date: str,
                                 leg: str, contract: Dict[str, Any]) -> bool:
    """Persist a lazy leg's FRESH strike ONCE (filtered on the leg's
    instrument_key being absent — first successful lock wins, mirroring
    capture_ref's philosophy). lce/lpe have no nested contract sub-doc like
    ce/pe, hence the flat ``{leg}_<field>`` naming. Lives here (not in
    premium_lock_store.py) because it is engine-side pickup logic (Task A3),
    not a generic store primitive (Task A1)."""
    fields = {f"{leg}_{k}": contract.get(k) for k in _LAZY_CONTRACT_FIELDS if k in contract}
    if not fields:
        return False
    res = await locks_col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         f"{leg}_instrument_key": {"$exists": False}},
        {"$set": fields},
    )
    return int(getattr(res, "matched_count", 0) or 0) == 1


async def evaluate_premium_momentum_bar(
    *, locks_col: Any, deployment: Dict[str, Any], instrument: str,
    candle_ts: int, spot_close: float, contracts: List[Dict[str, Any]],
    latest_tick_map: Callable[[], Dict[str, Any]], now_ts: float,
    vix: Optional[float] = None,
) -> Dict[str, Any]:
    """One bar of the premium-momentum session machine. Returns
    {"outcome": pre_reference|awaiting_ref|monitoring|triggered|holding_position|done,
     and on triggered: direction, leg, contract, ref_premium, premium_now, blockers[]}.

    ``vix`` (Phase 5B): the EVALUATOR's already-resolved INDIAVIX asof value
    (or None if unverifiable). This module never fetches VIX itself — it only
    gates on the value it is given, staying a pure function of its inputs."""
    dep_id = str(deployment.get("id") or "")
    params = dict(deployment.get("params") or {})
    # normalize-or-die on every HH:MM risk gate (review C1: unpadded valid
    # times made lexicographic comparisons silently fail-OPEN - a cutoff
    # that never fires). A ValueError here surfaces as an evaluator error,
    # loud and visible, never a silent no-op gate.
    ref_time = normalize_hhmm(params.get("reference_time")) or "09:31"
    cutoff = normalize_hhmm(params.get("late_lock_cutoff")) or "10:15"
    moneyness = str(params.get("moneyness") or "itm1")
    sides = _sides(params)
    both_mode = _leg_mode(params) == "both"

    # (The Cluster-A interim guard that blocked both-mode LIVE deployments was
    # removed here once B6 — per-leg exit finalize + lazy arming in the guard
    # close hook — and B7 — per-leg recovery rehydration — both landed; the
    # full both-mode lifecycle now exists end to end.)
    lazy_enabled = bool(params.get("lazy_enabled") or False)
    lazy_moneyness = str(params.get("lazy_moneyness") or moneyness)
    lazy_mom_pct = params.get("lazy_momentum_pct")
    entry_cutoff = normalize_hhmm(params.get("entry_cutoff"))
    bar_hhmm = _ist_hhmm(candle_ts)
    session = _ist_session_date(candle_ts)

    # Phase 5B entry_cutoff (design decision: "no NEW triggers, no lazy locks
    # at/after it" -- distinct from late_lock_cutoff below, which gates only
    # the INITIAL reference-time strike lock). None (the pre-5B default) ->
    # always False -> zero behavior change for every existing configuration.
    cutoff_reached = bool(entry_cutoff) and bar_hhmm >= entry_cutoff

    if bar_hhmm < ref_time:
        return {"outcome": "pre_reference"}

    lock = await get_lock(locks_col, deployment_id=dep_id, session_date=session)

    # --- session terminal states first ---
    if lock and lock.get("done_for_day"):
        return {"outcome": "done", "reason": lock.get("done_reason")}

    # --- Phase 5B: mode-aware terminal check (the "line-100 seam") ----------
    if not both_mode:
        # first_to_trigger: the EXACT pre-5B check, byte-identical (PINNED).
        # Deliberate, plan-directed consequence: once ANY side latches, the
        # WHOLE session is "holding_position" forever, so a lazy leg armed
        # while in this mode is structurally UNREACHABLE below. lazy_enabled
        # is intended to pair with leg_mode="both" (see the plugin schema and
        # the Phase 5A edge-hunt finding that "both" carries the lazy edge) --
        # see the task report for this explicit, tested limitation.
        if lock and (lock.get("triggered_side") or lock.get("entered_norenordno")):
            return {"outcome": "holding_position"}
        eligible_sides = list(sides)
    else:
        # both mode: skip only INDIVIDUALLY resolved legs so an unresolved
        # sibling leg keeps being evaluated on later bars. This is how a
        # same-bar double-cross resolves live (parity-divergence table): CE
        # wins THIS bar (the CE-first loop order below reports one trigger
        # per bar); PE's still-true momentum condition re-fires on the NEXT
        # bar via its own still-unlatched leg -- live is later/fewer than the
        # backtest's same-bar double-entry, a conservative divergence.
        eligible_sides = [s for s in sides if lock is None or not _leg_resolved(lock, s.lower())]
        if lock is not None and not eligible_sides and not _lazy_pending(lock, params):
            return {"outcome": "holding_position"}

    # --- create the lock at/after the reference bar (strikes from THIS close) ---
    if lock is None:
        vix_min = params.get("vix_min")
        vix_max = params.get("vix_max")
        if vix_min is not None or vix_max is not None:
            # Phase 5B VIX gate: session-start check, BEFORE the strike lock
            # (mirrors the backtest's ordering -- a gated/unverifiable session
            # costs nothing further). Configured-but-unverifiable is its OWN
            # reason, never a silent pass (mirrors the backtest's
            # sessions_excluded_vix_missing counter -- trading an
            # unverifiable gate would be dishonest).
            vix_reason = None
            if vix is None:
                vix_reason = "vix_unverifiable"
            else:
                vix_f = float(vix)
                if (vix_min is not None and vix_f < float(vix_min)) or \
                   (vix_max is not None and vix_f > float(vix_max)):
                    vix_reason = "vix_gate"
            if vix_reason:
                await get_or_create_lock(locks_col, deployment_id=dep_id,
                                         session_date=session, payload={})
                await mark_done(locks_col, deployment_id=dep_id, session_date=session,
                                reason=vix_reason)
                return {"outcome": "done", "reason": vix_reason}

        if bar_hhmm > cutoff:
            # never locked and past the cutoff: the session is honestly dead.
            await get_or_create_lock(locks_col, deployment_id=dep_id,
                                     session_date=session, payload={})
            await mark_done(locks_col, deployment_id=dep_id, session_date=session,
                            reason="no_lock")
            return {"outcome": "done", "reason": "no_lock"}
        payload: Dict[str, Any] = {"spot_at_ref": float(spot_close),
                                   "reference_bar_ts": int(candle_ts)}
        for side in sides:
            locked = lock_reference_strike(contracts=contracts, underlying=instrument,
                                           spot_at_ref=float(spot_close), side=side,
                                           moneyness=moneyness)
            if not locked:
                await get_or_create_lock(locks_col, deployment_id=dep_id,
                                         session_date=session, payload=payload)
                await mark_done(locks_col, deployment_id=dep_id, session_date=session,
                                reason="strike_lock_failed")
                return {"outcome": "done", "reason": "strike_lock_failed",
                        "blockers": [f"strike_lock_failed ({side} {moneyness})"]}
            # persist the FULL contract doc for the side (entry + audit need it)
            full = next((c for c in contracts
                         if str(c.get("instrument_key")) == locked["instrument_key"]), {})
            payload[side.lower()] = {**full, **locked}
        lock = await get_or_create_lock(locks_col, deployment_id=dep_id,
                                        session_date=session, payload=payload)

    # --- capture refs from FRESH ticks (first fresh tick wins; stale = HOLD) ---
    missing_ref = False
    for side in sides:
        s = side.lower()
        if lock.get(f"{s}_ref_premium") is not None:
            continue
        key = str(((lock.get(s) or {}).get("instrument_key")) or "")
        fp = _fresh_premium(latest_tick_map, key, now_ts) if key else None
        if fp is None:
            missing_ref = True
            continue
        if await capture_ref(locks_col, deployment_id=dep_id, session_date=session,
                             side=s, ref_premium=fp["premium"], ref_ts=fp["ts"]):
            lock[f"{s}_ref_premium"] = fp["premium"]
        else:
            # lost the store's first-wins race — adopt the PERSISTED winner's
            # value, never this process's losing premium.
            lock = (await get_lock(locks_col, deployment_id=dep_id,
                                   session_date=session)) or lock
        if lock.get(f"{s}_ref_premium") is None:
            missing_ref = True
    if missing_ref:
        if bar_hhmm > cutoff:
            await mark_done(locks_col, deployment_id=dep_id, session_date=session,
                            reason="no_lock")
            return {"outcome": "done", "reason": "no_lock"}
        return {"outcome": "awaiting_ref",
                "blockers": ["ref_premium_unavailable (stale/absent tick — holding)"]}

    # --- monitor: first side to cross wins (CE first on a same-bar tie) ---
    mom_pct = params.get("momentum_pct")
    mom_pts = params.get("momentum_pts")
    if mom_pts is not None:
        # Explicit precedence: a user-set momentum_pts wins over momentum_pct
        # (the registration schema DEFAULTS pct=15.0, so both-set is the normal
        # shape of a pts deployment — momentum_triggered raises on both-set,
        # which would silently dead-bar the session as outcome=error every bar).
        mom_pct = None
    if not cutoff_reached:
        for side in eligible_sides:
            s = side.lower()
            key = str(((lock.get(s) or {}).get("instrument_key")) or "")
            fp = _fresh_premium(latest_tick_map, key, now_ts) if key else None
            if fp is None:
                continue   # this side's feed is stale THIS bar — hold it, try the other
            ref = float(lock[f"{s}_ref_premium"])
            if momentum_triggered(premium_now=fp["premium"], ref_premium=ref,
                                  pct=mom_pct, pts=mom_pts):
                return {"outcome": "triggered", "direction": side, "leg": _primary_leg(side),
                        "contract": dict(lock.get(s) or {}),
                        "ref_premium": ref, "premium_now": fp["premium"], "blockers": []}
    # else: entry_cutoff reached (Phase 5B) -- no NEW primary triggers past
    # this clock; an already-open position's exit is the guard's job, not
    # this engine's, so this bar simply stops looking for new entries.

    # --- Phase 5B: lazy reversal leg pickup + monitor ------------------------
    # Armed by the guard-close hook (Task B6, out of this module's scope) on
    # a confirmed-flat STOP exit. Live semantic (parity-divergence table): the
    # backtest arms + locks at the SAME stop-out bar (that bar's close as
    # spot_at_ref); live arms on guard-confirmed-flat, then locks HERE on the
    # NEXT evaluator bar from THIS bar's live spot -- later by flat-confirm +
    # up to one bar than the backtest's bar-close approximation, a
    # conservative divergence, documented not hidden.
    if lazy_enabled and not cutoff_reached:
        for s in ("ce", "pe"):
            if not lock.get(f"lazy_armed_{s}"):
                continue
            leg = f"l{s}"                                   # lce / lpe
            if _leg_resolved(lock, leg) or lock.get(f"{leg}_exited"):
                continue   # one-shot: already triggered/entered/exited

            if lock.get(f"{leg}_instrument_key") is None:
                # Fresh strike from THIS bar's live spot (never the stale
                # reference-bar spot) -- see the divergence note above.
                locked = lock_reference_strike(contracts=contracts, underlying=instrument,
                                               spot_at_ref=float(spot_close),
                                               side=s.upper(), moneyness=lazy_moneyness)
                if not locked:
                    continue   # coverage gap THIS bar -- retry next bar, never invent a strike
                full = next((c for c in contracts
                             if str(c.get("instrument_key")) == locked["instrument_key"]), {})
                merged = {**full, **locked}
                if await _capture_lazy_contract(locks_col, deployment_id=dep_id,
                                                session_date=session, leg=leg, contract=merged):
                    for k in _LAZY_CONTRACT_FIELDS:
                        if merged.get(k) is not None:
                            lock[f"{leg}_{k}"] = merged[k]
                else:
                    # lost a same-bar race (or another process already locked
                    # it) -- reread and let a later bar continue from there.
                    lock = (await get_lock(locks_col, deployment_id=dep_id,
                                           session_date=session)) or lock
                    if lock.get(f"{leg}_instrument_key") is None:
                        continue

            leg_key = str(lock.get(f"{leg}_instrument_key") or "")
            if lock.get(f"{leg}_ref_premium") is None:
                fp = _fresh_premium(latest_tick_map, leg_key, now_ts) if leg_key else None
                if fp is None:
                    continue   # stale/absent tick — HOLD, never invent a ref
                if await capture_ref_leg(locks_col, deployment_id=dep_id, session_date=session,
                                         leg=leg, ref_premium=fp["premium"], ref_ts=fp["ts"]):
                    lock[f"{leg}_ref_premium"] = fp["premium"]
                else:
                    lock = (await get_lock(locks_col, deployment_id=dep_id,
                                           session_date=session)) or lock
                if lock.get(f"{leg}_ref_premium") is None:
                    continue

            fp = _fresh_premium(latest_tick_map, leg_key, now_ts) if leg_key else None
            if fp is None:
                continue
            ref = float(lock[f"{leg}_ref_premium"])
            if momentum_triggered(premium_now=fp["premium"], ref_premium=ref,
                                  pct=lazy_mom_pct, pts=None):
                return {"outcome": "triggered", "direction": s.upper(), "leg": leg,
                        "contract": _leg_contract(lock, leg),
                        "ref_premium": ref, "premium_now": fp["premium"], "blockers": []}
    return {"outcome": "monitoring"}
