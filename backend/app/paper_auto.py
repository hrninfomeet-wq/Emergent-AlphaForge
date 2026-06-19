"""Automatic paper trading on confirmed deployment signals.

User requirement (2026-06-10): a deployment generating live signals should also
paper-trade each confirmed signal automatically (configurable lots, default 1)
WITHOUT manual approval, so the signal's real outcome is auditable. The manual
Pending-Approval flow stays for deployments that don't opt in.

Honored only for deployments with mode == "paper" AND risk.auto_paper truthy.
Existing deployments (created before this slice) have no auto_paper field and
keep the approve-to-trade behavior unchanged.

Entry-price correctness: a paper trade's instrument_key is the OPTION contract,
and all later marks / square-off use option premium ticks — so the entry price
MUST be option premium, never the spot index level. (The original approval flow
filled entry from signal.entry_price, which is the spot close — that bug is
fixed by routing both paths through resolve_option_entry_price.) Resolution
order: live WS tick for the contract → latest options_1m candle close within
the freshness window → refuse to create the trade and journal
`paper_trade_error` on the signal. A refused trade is honest; a spot-priced
option trade poisons every forward metric downstream.

Exit levels — two complementary mechanisms, mirroring the backtest engines:

1. PREMIUM levels on trade.risk (LONG-premium semantics: target above entry,
   stop below). Sources, in order: the strategy's `risk_hints.target_pct` /
   `stop_pct` (% of entry premium — note that NO builtin strategy currently
   sets these), then the deployment-level `risk.auto_paper_target_pct` /
   `auto_paper_stop_pct` fallback. None set → no premium levels.
2. SPOT-MIRROR levels on trade.spot_exit — the builtin strategies define their
   exits as SPOT INDEX POINTS (`risk_hints.spot_target_pts` / `spot_stop_pts`),
   which is exactly what the backtest's `spot_exit` mode simulates: the option
   position closes when the UNDERLYING hits the spot level. The marker watches
   the live spot tick and closes the option at its current premium when a spot
   level is hit (direction-aware: CE targets above entry spot, PE below).

Either mechanism may fire first; the 15:00 IST square-off remains the backstop
when neither is configured.

mark_open_deployment_trades() is the minute-cadence marker: it marks every OPEN
paper trade against the latest live option tick (premium stop/target via the
existing mark_trade_to_market), then evaluates spot-mirror levels against the
live spot tick, transitioning the linked signal to EXITED on close. It runs
for ALL open trades regardless of the parent deployment's status — an open
position stays risk-managed even when its deployment is paused, matching the
square-off's existing behavior. Without the marker, exits would only ever fire
on manual marks.

Concurrency: trade creation is guarded by an atomic claim on the signal
(claim_signal_for_paper_trade) so the evaluator hook and the manual approve
route can never both open a trade for the same signal; the marker's writes are
conditional on status=OPEN so a concurrent manual close is never clobbered.
A claim with no resulting trade (process crash in the tiny window between
claim and insert) blocks later auto/approve trade creation for that signal —
visible as `paper_trade_claim` on the signal doc for audit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.instruments import INSTRUMENT_KEYS
from app.paper_trading import close_trade, mark_trade_to_market, paper_trade_from_signal, _iso_to_ms
from app.signal_lifecycle import SignalStateError, transition_signal

log = logging.getLogger(__name__)

# How fresh an options_1m candle must be to serve as an entry-price fallback.
ENTRY_CANDLE_MAX_AGE_MINUTES = 5
# A live tick older than this is not booked as a fill by the minute marker — a
# minutes-old premium shouldn't auto-close a trade at a price that isn't trading.
MARK_TICK_MAX_AGE_SECONDS = 120

TickLookup = Callable[[str], Optional[Dict[str, Any]]]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def resolve_option_entry_price(
    db: Any,
    instrument_key: str,
    *,
    latest_tick_lookup: Optional[TickLookup] = None,
    max_candle_age_minutes: int = ENTRY_CANDLE_MAX_AGE_MINUTES,
    now_utc: Optional[datetime] = None,
) -> Optional[float]:
    """Best available option PREMIUM for the contract, or None when nothing
    trustworthy exists (never falls back to spot)."""
    if not instrument_key:
        return None
    if latest_tick_lookup is not None:
        tick = latest_tick_lookup(instrument_key)
        if tick and tick.get("last_price") not in (None, ""):
            try:
                price = float(tick["last_price"])
                if price > 0:
                    return price
            except (TypeError, ValueError):
                pass
    now = now_utc or _now_utc()
    cutoff_ms = int((now - timedelta(minutes=int(max_candle_age_minutes))).timestamp() * 1000)
    cursor = (
        db.options_1m
        .find({"instrument_key": instrument_key, "ts": {"$gte": cutoff_ms}}, {"_id": 0, "ts": 1, "close": 1})
        .sort("ts", -1)
        .limit(1)
    )
    rows = await cursor.to_list(length=1)
    if rows and rows[0].get("close") not in (None, ""):
        try:
            price = float(rows[0]["close"])
            if price > 0:
                return price
        except (TypeError, ValueError):
            pass
    return None


def compute_auto_risk_levels(
    entry_price: float,
    risk_hints: Optional[Dict[str, Any]],
    deployment_risk: Optional[Dict[str, Any]],
) -> Tuple[Optional[float], Optional[float]]:
    """(stop_price, target_price) for a LONG option position.

    The strategy's own hints (signal.risk_hints.target_pct / stop_pct, % of
    entry premium) win over the deployment-level fallbacks — the
    shared-decision-engine rule: the strategy that fired the signal defines its
    exit. Deployment fallbacks support premium POINTS (auto_paper_target_pts /
    auto_paper_stop_pts) and percent (auto_paper_target_pct / auto_paper_stop_pct);
    points take precedence over percent, matching the backtest's
    `_resolve_option_levels` rule. Missing on all sides → None (the spot-mirror
    exits and the 15:00 IST square-off remain the backstops).
    """
    from app.execution_policy import resolve_premium_levels

    hints = risk_hints or {}
    dep = deployment_risk or {}

    def _num(value: Any) -> Optional[float]:
        try:
            if value in (None, ""):
                return None
            p = float(value)
            return p if p > 0 else None
        except (TypeError, ValueError):
            return None

    # Source precedence per leg: strategy hint (pct) > deployment pts >
    # deployment pct. The chosen source's value is then resolved through the
    # SAME level math as the backtest (execution_policy), with the live
    # floor (Rs 0.05 exchange tick) and 2dp storage rounding.
    target_kwargs: Dict[str, Any] = {}
    if _num(hints.get("target_pct")) is not None:
        target_kwargs["target_pct"] = hints.get("target_pct")
    elif _num(dep.get("auto_paper_target_pts")) is not None:
        target_kwargs["target_pts"] = dep.get("auto_paper_target_pts")
    elif _num(dep.get("auto_paper_target_pct")) is not None:
        target_kwargs["target_pct"] = dep.get("auto_paper_target_pct")

    stop_kwargs: Dict[str, Any] = {}
    if _num(hints.get("stop_pct")) is not None:
        stop_kwargs["stop_pct"] = hints.get("stop_pct")
    elif _num(dep.get("auto_paper_stop_pts")) is not None:
        stop_kwargs["stop_pts"] = dep.get("auto_paper_stop_pts")
    elif _num(dep.get("auto_paper_stop_pct")) is not None:
        stop_kwargs["stop_pct"] = dep.get("auto_paper_stop_pct")

    _stop_unused, target = resolve_premium_levels(
        entry_price, **target_kwargs, stop_floor=0.05, ndigits=2,
    ) if target_kwargs else (None, None)
    stop, _target_unused = resolve_premium_levels(
        entry_price, **stop_kwargs, stop_floor=0.05, ndigits=2,
    ) if stop_kwargs else (None, None)

    return stop, target


def compute_spot_exit_levels(signal_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Spot-mirror exit levels from the strategy's spot-point hints — the live
    equivalent of the backtest's `spot_exit` mode. Direction-aware for a LONG
    option: a CE profits when spot RISES (target above entry spot, stop below);
    a PE profits when spot FALLS (target below, stop above). Returns None when
    the strategy provided no spot hints or the spot tick key is unknown."""
    hints = signal_doc.get("risk_hints") or {}

    def _pts(value: Any) -> Optional[float]:
        try:
            if value in (None, ""):
                return None
            p = float(value)
            return p if p > 0 else None
        except (TypeError, ValueError):
            return None

    target_pts = _pts(hints.get("spot_target_pts"))
    stop_pts = _pts(hints.get("spot_stop_pts"))
    if target_pts is None and stop_pts is None:
        return None
    try:
        entry_spot = float(signal_doc.get("entry_price") or 0.0)
    except (TypeError, ValueError):
        return None
    if entry_spot <= 0:
        return None
    direction = str(signal_doc.get("direction") or "").upper()
    if direction not in ("CE", "PE"):
        return None
    instrument = str(signal_doc.get("instrument") or "").upper()
    spot_key = INSTRUMENT_KEYS.get(instrument)
    if not spot_key:
        return None
    from app.execution_policy import spot_mirror_levels
    levels = spot_mirror_levels(direction, entry_spot, target_pts=target_pts, stop_pts=stop_pts)
    return {
        "instrument": instrument,
        "instrument_key": spot_key,
        "direction": direction,
        "entry_spot": round(entry_spot, 2),
        "spot_target": levels["spot_target"],
        "spot_stop": levels["spot_stop"],
    }


def spot_exit_reason(spot_exit: Dict[str, Any], spot_price: float) -> Optional[str]:
    """Has the underlying hit a spot-mirror level? Direction-aware.

    Delegates to the shared execution policy, which routes through the SAME
    `intrabar_exit` the backtest uses (tick = degenerate bar) — including the
    pessimistic stop-first rule the old inline check got backwards."""
    from app.execution_policy import spot_mirror_exit_reason
    return spot_mirror_exit_reason(
        str(spot_exit.get("direction") or ""),
        spot_price,
        spot_target=spot_exit.get("spot_target"),
        spot_stop=spot_exit.get("spot_stop"),
    )


def auto_paper_enabled(deployment: Dict[str, Any]) -> bool:
    """Auto paper trading applies only to paper-mode deployments that opted in."""
    if str(deployment.get("mode") or "").lower() != "paper":
        return False
    return bool((deployment.get("risk") or {}).get("auto_paper"))


async def claim_signal_for_paper_trade(db: Any, signal_id: str, source: str) -> bool:
    """Atomically claim the right to create THE paper trade for a signal.

    Both writers (the evaluator's auto-paper hook and the manual approve route)
    must win this claim before inserting a trade, which closes the TOCTOU race
    where each passes its own in-memory paper_trade_id check before the other's
    write lands. The filter requires the signal to still be CONFIRMED with no
    trade and no prior claim; Mongo's single-document update is atomic.
    """
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


async def release_paper_trade_claim(db: Any, signal_id: str) -> None:
    """Release a claim after a journaled (non-crash) failure so the signal can
    be retried by either path."""
    await db.signals.update_one({"id": signal_id}, {"$unset": {"paper_trade_claim": ""}})


def resolve_deployment_lots(
    risk_cfg: Dict[str, Any],
    fill_entry: float,
    contract: Dict[str, Any],
    stop_price: Optional[float],
) -> Tuple[int, Dict[str, Any]]:
    """Lots for a live auto-paper trade, replaying the source run's pinned sizing
    policy (deployment.risk.sizing). Falls back to deployment.risk.default_lots
    when no policy was pinned (legacy deployments). Returns (lots, audit) where
    audit carries sizing_mode and, for premium_at_risk, the per-unit risk fields.

    lot_size always comes from the live contract; only the lot COUNT is sized — so
    SENSEX/BANKNIFTY (different lot_size) adapt automatically while rupee risk is
    held constant, exactly as the backtest does.
    """
    from app.portfolio import SizingConfig, size_position

    lot_size = max(1, int((contract or {}).get("lot_size") or 1))
    pin = (risk_cfg or {}).get("sizing") or {}
    sizing_config = pin.get("sizing_config")
    if isinstance(sizing_config, dict):
        cfg = SizingConfig.from_dict(sizing_config)
        if cfg.enabled:
            sized = size_position(
                entry_premium=float(fill_entry), lot_size=lot_size,
                stop_level=stop_price, cfg=cfg,
            )
            return int(sized["lots"]), {
                "sizing_mode": sized.get("sizing_mode"),
                "risk_per_unit": sized.get("risk_per_unit"),
                "risk_amount": sized.get("risk_amount"),
                "risk_exceeded": sized.get("risk_exceeded"),
            }
        return max(1, int(pin.get("lots") or 1)), {"sizing_mode": "fixed_lots"}
    if pin:
        # Pin present but sizing_config malformed/absent (only possible via DB
        # corruption — the deriver always co-writes both). Honour the pin's own
        # lot count rather than silently dropping to the deployment default.
        return max(1, int(pin.get("lots") or 1)), {"sizing_mode": "fixed_lots"}
    return max(1, int((risk_cfg or {}).get("default_lots") or 1)), {"sizing_mode": "fixed_lots_legacy"}


def build_auto_trade(
    signal_doc: Dict[str, Any],
    deployment: Dict[str, Any],
    entry_price: float,
) -> Dict[str, Any]:
    """Construct the paper-trade doc shared by the auto and approve paths:
    premium entry, lots from deployment risk, premium stop/target from hints →
    deployment pct fallback, and spot-mirror levels from the strategy's spot
    hints. The caller stamps deployment_id / source before insert."""
    risk_cfg = deployment.get("risk") or {}

    # Live execution-realism (app.live_friction): when the deployment opted in
    # (risk.friction.enabled), slip the ENTRY (BUY) BEFORE levels are computed —
    # exactly as the backtest does — so the forward fill, and the stop/target
    # measured off it, match the simulation. The moneyness + expiry travel with
    # the trade so the exit fill uses the same slippage bucket / expiry-tail rule.
    # Disabled (the default) → the raw premium is the fill, unchanged behavior.
    from app.live_friction import FrictionConfig, apply_entry_friction
    friction = FrictionConfig.from_dict(risk_cfg.get("friction"))
    policy = deployment.get("option_policy") or {}
    mlist = policy.get("moneyness") or ["atm"]
    friction.moneyness = str(mlist[0] if isinstance(mlist, list) and mlist else "atm").lower()
    contract = signal_doc.get("option_contract") or {}
    friction.expiry_iso = str(contract.get("expiry_date") or "") or None

    raw_entry = float(entry_price)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    entry_fill = apply_entry_friction(raw_entry, friction, ts_ms=now_ms)
    fill_entry = entry_fill["price"]

    stop_price, target_price = compute_auto_risk_levels(
        fill_entry, signal_doc.get("risk_hints"), risk_cfg,
    )
    lots, sizing_audit = resolve_deployment_lots(risk_cfg, fill_entry, contract, stop_price)
    trade = paper_trade_from_signal(
        signal_doc,
        lots=lots,
        entry_price=fill_entry,
        raw_entry_price=raw_entry,
        stop_price=stop_price,
        target_price=target_price,
        friction=friction.to_dict() if friction.enabled else None,
    )
    if friction.enabled:
        trade["entry_slippage_pts"] = entry_fill["slippage_pts"]
        trade["entry_spread_pts"] = round(entry_fill["spread_pts"], 4)
    for _k, _v in sizing_audit.items():
        if _v is not None:
            trade[_k] = _v
    spot_exit = compute_spot_exit_levels(signal_doc)
    if spot_exit:
        trade["spot_exit"] = spot_exit
    # Store risk_hints on the trade so the live marker can honour the strategy's
    # time_stop_minutes (and any future strategy-defined exits) without querying
    # the signal doc at mark time.
    if signal_doc.get("risk_hints"):
        trade["risk_hints"] = signal_doc["risk_hints"]
    # Seed the running-max at entry so the live trail ratchet has a starting peak.
    # Carry the exit_controls overlay so the live marker can ratchet the stop
    # without querying the deployment doc at mark time.
    trade["running_max_premium"] = float(fill_entry)
    ec = (deployment.get("risk") or {}).get("exit_controls")
    if ec:
        trade["exit_controls"] = ec
    return trade


async def auto_paper_trade_for_signal(
    db: Any,
    deployment: Dict[str, Any],
    signal_doc: Dict[str, Any],
    *,
    latest_tick_lookup: Optional[TickLookup] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Create a paper trade for a clean CONFIRMED signal without manual approval.

    Side effects on success: inserts into paper_trades, advances the signal
    CONFIRMED → TRIGGERED → ACTIVE with an auto_paper audit snapshot, stamps
    signal.paper_trade_id, and replaces the signal doc. On a journaled failure
    (no premium available) only signal.paper_trade_error is set.

    Returns {created, trade_id?, entry_price?, error?, reason?}.
    """
    signal_id = str(signal_doc.get("id") or "")
    if not auto_paper_enabled(deployment):
        return {"created": False, "reason": "auto_paper_disabled"}
    if str(signal_doc.get("state") or "").upper() != "CONFIRMED":
        return {"created": False, "reason": f"signal_not_confirmed ({signal_doc.get('state')})"}
    if signal_doc.get("blocked"):
        return {"created": False, "reason": "signal_blocked"}
    if signal_doc.get("paper_trade_id"):
        return {"created": False, "reason": "paper_trade_already_exists",
                "trade_id": signal_doc.get("paper_trade_id")}

    from app.deployment_kill_switch import check_soft_daily_governor
    gov = await check_soft_daily_governor(db, deployment)
    if gov.get("halt"):
        return {"created": False, "reason": f"daily_cap:{gov.get('reason')}"}

    contract = signal_doc.get("option_contract") or {}
    instrument_key = str(contract.get("instrument_key") or "")
    if not instrument_key:
        return {"created": False, "reason": "no_option_contract"}

    # Atomic claim BEFORE any trade-creating work — closes the race with the
    # manual approve route (and a concurrent skip/mark-blocked, whose state
    # change makes this claim fail).
    if not await claim_signal_for_paper_trade(db, signal_id, "auto_paper"):
        return {"created": False, "reason": "signal_claimed_elsewhere"}

    entry_price = await resolve_option_entry_price(
        db, instrument_key,
        latest_tick_lookup=latest_tick_lookup,
        now_utc=now_utc,
    )
    if entry_price is None:
        error = ("option_entry_price_unavailable "
                 f"(no live tick or options_1m candle within {ENTRY_CANDLE_MAX_AGE_MINUTES}m for {instrument_key})")
        await release_paper_trade_claim(db, signal_id)
        await db.signals.update_one(
            {"id": signal_id},
            {"$set": {"paper_trade_error": error,
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        log.warning("auto-paper skipped for signal %s: %s", signal_id, error)
        return {"created": False, "error": error}

    trade = build_auto_trade(signal_doc, deployment, entry_price)
    trade["deployment_id"] = str(deployment.get("id") or "")
    trade["source"] = "paper_auto_on_signal"
    trade["auto_created"] = True
    await db.paper_trades.insert_one(trade)

    # The booked entry is the friction-adjusted fill when the deployment opted
    # into live realism, else the raw resolved premium (identical to entry_price).
    booked_entry = float(trade.get("entry_price") or entry_price)

    snapshot = {"auto_paper": {
        "trade_id": trade["id"],
        "entry_price": booked_entry,
        "raw_entry_price": float(trade.get("raw_entry_price") or entry_price),
        "lots": trade.get("lots"),
        "stop_price": (trade.get("risk") or {}).get("stop_price"),
        "target_price": (trade.get("risk") or {}).get("target_price"),
        "spot_exit": trade.get("spot_exit"),
        "friction_enabled": bool(trade.get("friction")),
        "at": datetime.now(timezone.utc).isoformat(),
    }}
    try:
        updated = transition_signal(signal_doc, "TRIGGERED", reason="auto_paper_on_signal", snapshot=snapshot)
        updated = transition_signal(updated, "ACTIVE", reason="auto_paper_trade_open", snapshot=snapshot)
    except SignalStateError as exc:
        # The trade exists; keep the signal as-is but record the link + anomaly.
        log.warning("auto-paper state transition failed for signal %s: %s", signal_id, exc)
        updated = dict(signal_doc)
        updated["paper_trade_state_error"] = str(exc)[:240]
    updated["paper_trade_id"] = trade["id"]
    updated["auto_paper"] = snapshot["auto_paper"]
    # The in-memory doc predates the claim write — mirror it so the full-doc
    # replace doesn't silently drop the audit marker.
    updated["paper_trade_claim"] = {"source": "auto_paper",
                                    "at": snapshot["auto_paper"]["at"]}
    await db.signals.replace_one({"id": signal_id}, updated, upsert=False)

    log.info("auto-paper trade %s opened for signal %s (%s @ %.2f x %s lots)",
             trade["id"], signal_id, trade.get("trading_symbol") or instrument_key,
             booked_entry, trade.get("lots"))
    return {"created": True, "trade_id": trade["id"], "entry_price": booked_entry,
            "raw_entry_price": float(trade.get("raw_entry_price") or entry_price),
            "friction_enabled": bool(trade.get("friction")),
            "stop_price": (trade.get("risk") or {}).get("stop_price"),
            "target_price": (trade.get("risk") or {}).get("target_price"),
            "spot_exit": trade.get("spot_exit")}


async def mark_open_deployment_trades(
    db: Any,
    *,
    latest_tick_lookup: Optional[TickLookup] = None,
    at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Mark every OPEN paper trade to the latest live option tick and let
    mark_trade_to_market auto-close on stop/target. Trades without a live tick
    are left untouched (no stale-price closes). Linked signals transition
    ACTIVE → EXITED when a trade auto-closes. Returns per-trade summaries."""
    if latest_tick_lookup is None:
        return []

    now_ms = int(_now_utc().timestamp() * 1000)

    def _tick_price(key: str) -> Optional[float]:
        """Latest tick price for a key, but ONLY when fresh — a tick older than
        MARK_TICK_MAX_AGE_SECONDS is treated as absent so the marker never books a
        fill (or fires a stop/target) on a minutes-old premium. Ticks with no
        timestamp (tests / legacy) are treated as current."""
        if not key:
            return None
        tick = latest_tick_lookup(key)
        if not tick or tick.get("last_price") in (None, ""):
            return None
        try:
            price = float(tick["last_price"])
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        age_ref = tick.get("received_ts") or tick.get("ts")
        if age_ref is not None:
            try:
                if now_ms - int(age_ref) > MARK_TICK_MAX_AGE_SECONDS * 1000:
                    return None
            except (TypeError, ValueError):
                pass
        return price

    async def _exit_linked_signal(trade_doc: Dict[str, Any]) -> None:
        if not trade_doc.get("signal_id"):
            return
        sig = await db.signals.find_one({"id": trade_doc["signal_id"]}, {"_id": 0})
        if sig and str(sig.get("state") or "").upper() == "ACTIVE":
            try:
                exited = transition_signal(
                    sig, "EXITED",
                    reason=f"paper_trade_auto_closed ({trade_doc.get('exit_reason')})",
                    snapshot={"trade_id": trade_doc["id"],
                              "realized_pnl": trade_doc.get("realized_pnl")},
                )
                await db.signals.replace_one({"id": sig["id"]}, exited, upsert=False)
            except SignalStateError:
                pass

    cursor = db.paper_trades.find({"status": "OPEN"}, {"_id": 0})
    open_trades = await cursor.to_list(length=None)
    summaries: List[Dict[str, Any]] = []
    for trade in open_trades:
        try:
            option_price = _tick_price(str(trade.get("instrument_key") or ""))
            updated = trade
            wrote = False

            # 0. Trail/breakeven ratchet: raise the stored stop from the PRIOR running-max
            #    premium (look-ahead parity with the sim), then advance the max after (below).
            ec_raw = trade.get("exit_controls")
            if ec_raw and option_price is not None:
                from app.exit_controls import ExitControlsConfig, effective_premium_stop
                ec_cfg = ExitControlsConfig.from_dict(ec_raw)
                if ec_cfg.enabled:
                    rmax_prev = float(trade.get("running_max_premium") or trade.get("entry_price") or 0.0)
                    base_stop = (trade.get("risk") or {}).get("stop_price")
                    eff = effective_premium_stop(entry=float(trade.get("entry_price") or 0.0),
                                                 running_max=rmax_prev, base_stop=base_stop, cfg=ec_cfg)
                    if eff is not None and (base_stop is None or eff > float(base_stop)):
                        trade.setdefault("risk", {})["stop_price"] = round(float(eff), 2)
                        # trade["risk"]["stop_price"] is mutated in place; mark_trade_to_market below reads it off the same object.
                        wrote = True

            # 1. Premium mark + premium stop/target via the existing machinery.
            #    option_price is None when there is no FRESH option tick, so a
            #    stop/target can only fire on a current premium (staleness bound).
            if option_price is not None:
                updated = mark_trade_to_market(
                    trade, last_price=option_price, at=at, auto_close_on_risk=True)
                wrote = True
                if str(updated.get("status") or "").upper() == "CLOSED":
                    updated["exit_price_source"] = "live_tick"
                    updated["exit_price_stale"] = False

            # 2. Tick-level time-stop: close at the live premium when the
            #    strategy's time_stop_minutes has elapsed (parity with the
            #    backtest's time exit).  Uses created_at as the entry timestamp
            #    because that is the field paper_trade_from_signal stamps.
            if str(updated.get("status") or "").upper() == "OPEN":
                tsm = (updated.get("risk_hints") or {}).get("time_stop_minutes")
                created_at = updated.get("created_at")
                if tsm and created_at and option_price is not None:
                    entry_ts_ms = _iso_to_ms(created_at)
                    elapsed_min = (now_ms - entry_ts_ms) / 60000.0
                    if elapsed_min >= float(tsm):
                        updated = close_trade(updated, exit_price=option_price,
                                              reason="time_stop", at=at)
                        updated["exit_price_source"] = "live_tick"
                        updated["exit_price_stale"] = False
                        wrote = True

            # 3. Spot-mirror exits (the backtest's spot_exit mode, live): close
            #    the option at its current premium when the UNDERLYING hits the
            #    strategy's spot level. When no FRESH option tick exists the fill
            #    is the last known premium — booked (the spot level was hit) but
            #    flagged stale so the journal shows it is an estimate, not a fill.
            spot_exit = updated.get("spot_exit") or {}
            if str(updated.get("status") or "").upper() == "OPEN" and spot_exit:
                spot_price = _tick_price(str(spot_exit.get("instrument_key") or ""))
                if spot_price is not None:
                    reason = spot_exit_reason(spot_exit, spot_price)
                    if reason:
                        had_fresh_option = option_price is not None
                        exit_premium = option_price
                        if exit_premium is None:
                            try:
                                exit_premium = float(updated.get("last_price")
                                                     or updated.get("entry_price") or 0.0)
                            except (TypeError, ValueError):
                                exit_premium = 0.0
                        updated = close_trade(updated, exit_price=exit_premium,
                                              reason=reason, at=at)
                        updated["spot_exit"] = {**spot_exit, "hit_spot_price": spot_price}
                        updated["exit_price_source"] = "live_tick" if had_fresh_option else "last_mark"
                        updated["exit_price_stale"] = not had_fresh_option
                        wrote = True

            # Advance the running-max in the doc that will be written (after the mark
            # has already checked the eff-stop against the PRIOR peak — look-ahead safe).
            if str(updated.get("status") or "").upper() == "OPEN" and option_price is not None:
                prev = float(updated.get("running_max_premium") or updated.get("entry_price") or 0.0)
                updated["running_max_premium"] = max(prev, float(option_price))
                wrote = True

            if not wrote:
                continue  # no tick on either leg — leave untouched (no stale marks)

            # Conditional on status=OPEN so a concurrent manual close is never
            # clobbered by this stale-read replacement.
            res = await db.paper_trades.replace_one(
                {"id": trade["id"], "status": "OPEN"}, updated, upsert=False)
            if int(getattr(res, "matched_count", 0) or 0) != 1:
                continue  # someone else closed it first — their write wins

            closed = str(updated.get("status") or "").upper() == "CLOSED"
            summaries.append({
                "id": trade["id"],
                "last_price": updated.get("last_price"),
                "closed": closed, "exit_reason": updated.get("exit_reason"),
                "realized_pnl": updated.get("realized_pnl") if closed else None,
                "exit_price_stale": updated.get("exit_price_stale") if closed else None,
            })
            if closed:
                await _exit_linked_signal(updated)
        except Exception as exc:
            log.exception("mark failed for paper trade %s: %s", trade.get("id"), exc)
            summaries.append({"id": trade.get("id"), "error": str(exc)})
    return summaries
