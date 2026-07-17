"""1-minute close evaluator for Strategy Deployments.

Pulls the latest closed 1-minute candles for a deployment's instrument,
runs the strategy's evaluate() on the freshest closed bar, applies the
pretrade filter, picks an option contract from stored metadata, and
journals one of:

  - clean signal:  state=CONFIRMED, awaiting manual approval
  - blocked:       state=SKIPPED, with blockers list (pretrade filter, time-of-day,
                   missing data, contract metadata gap, etc.)
  - no setup:      no journal entry (the strategy returned direction='NONE')
  - duplicate:     skipped silently if last_evaluated_ts >= candle ts

Deterministic, pure on top of the existing strategy plugin contract.
First-version mode: shadow only. No paper trade creation. No broker calls.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.indicators import precompute_all_indicators
from app.instruments import UNDERLYING_META
from app.options_universe import select_contract_for_signal
from app.regime import classify_regime_series
from app.features import materialize_features
from app.signal_lifecycle import create_signal_doc, transition_signal
from app.strategies.base import StrategyBase, get_registry, build_live_eval_ctx
from app.deployment_kill_switch import check_deployment_kill_switches

log = logging.getLogger(__name__)

# IST is UTC+5:30. We compute IST-time-of-day from candle ts to apply window guards.
IST_OFFSET = timedelta(hours=5, minutes=30)

# Time windows (IST). Block opening 10 min and last 30 min by user decision (2026-05-27).
BLOCK_OPEN_UNTIL = time(9, 25)        # block 09:15 -> 09:25 (first 10 min)
BLOCK_CLOSE_FROM = time(14, 50)       # block 14:50 -> 15:30 (last 30 min)
SQUARE_OFF_AT = time(15, 0)           # paper-trade square-off cutoff (used elsewhere)

# Minimum bars needed for indicators/strategy lookback.
MIN_BARS_FOR_EVALUATION = 50


def _ist_time_of_ts(ts_ms: int) -> time:
    """Convert an epoch-ms candle timestamp to IST time-of-day."""
    dt_utc = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
    return (dt_utc + IST_OFFSET).time()


def _ist_session_date_of_ts(ts_ms: int) -> str:
    dt_utc = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
    return (dt_utc + IST_OFFSET).strftime("%Y-%m-%d")


def _is_blocked_by_window(ts_ms: int) -> Optional[str]:
    """Return blocker reason if the given bar timestamp falls in a blocked window."""
    t = _ist_time_of_ts(ts_ms)
    if t < BLOCK_OPEN_UNTIL:
        return f"window_open_block (IST {t.strftime('%H:%M')} < 09:25)"
    if t >= BLOCK_CLOSE_FROM:
        return f"window_close_block (IST {t.strftime('%H:%M')} >= 14:50)"
    return None


def compute_strategy_hash(strategy_id: str, version: str, params: Dict[str, Any]) -> str:
    """Stable hash over (strategy_id, version, params). Identifies an audit-frozen run.

    Two deployments produce the same hash iff strategy id, version, and params match exactly.
    """
    payload = json.dumps(
        {"id": strategy_id, "version": version, "params": params or {}},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _apply_pretrade_filter(score: int, regime: Optional[str], filters: Dict[str, Any]) -> List[str]:
    """Return list of pretrade-blocker reasons, empty list if signal passes."""
    blockers: List[str] = []
    if not filters:
        return blockers
    min_score = int(filters.get("min_confidence_score") or 0)
    if min_score and int(score or 0) < min_score:
        blockers.append(f"pretrade_min_score (signal={score} < required={min_score})")
    allowed_regimes = filters.get("allowed_regimes")
    if allowed_regimes and regime not in allowed_regimes:
        blockers.append(f"pretrade_regime ({regime} not in allowed)")
    return blockers


async def _load_recent_candles(db: Any, instrument: str, *, lookback: int = 200) -> pd.DataFrame:
    """Load the latest `lookback` 1-minute candles for the instrument."""
    cursor = (
        db.candles_1m
        .find({"instrument": instrument.upper()}, {"_id": 0})
        .sort("ts", -1)
        .limit(int(lookback))
    )
    rows = await cursor.to_list(length=int(lookback))
    if not rows:
        return pd.DataFrame()
    rows.reverse()  # oldest first for indicator computation
    df = pd.DataFrame(rows)
    if "ts" not in df.columns:
        return pd.DataFrame()
    df["ts"] = df["ts"].astype("int64")
    return df


async def _resolve_pretrade_filters(db: Any, profile_name: str) -> Dict[str, Any]:
    """Resolve pretrade profile name -> settings at signal time. Snapshot stays on the signal."""
    if not profile_name:
        return {}
    doc = await db.pretrade_profiles.find_one({"name": profile_name}, {"_id": 0, "settings": 1})
    return (doc or {}).get("settings") or {}


async def _resolve_option_contract(
    db: Any,
    *,
    instrument: str,
    spot_price: float,
    direction: str,
    moneyness: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Pick an option contract per moneyness using stored metadata.

    Returns (contract_dict, blocker_reason). Either contract is set or blocker is set.
    Blockers:
      - "option_contract_metadata_missing": no contracts in option_contracts for instrument
      - "option_contract_no_active_expiry": all stored contracts are past their expiry
      - "option_contract_not_found":        no exact strike/side match for the requested moneyness
    """
    if instrument.upper() not in UNDERLYING_META:
        return None, f"option_unsupported_underlying ({instrument})"

    today_iso = (datetime.now(timezone.utc) + IST_OFFSET).strftime("%Y-%m-%d")

    # Query for ACTIVE contracts only (expiry_date >= today). This prevents the bug we
    # observed on 2026-05-28 where the picker resolved a live signal to a Nov-2024
    # expired contract because the warehouse mixes current + expired contracts.
    contracts = await db.option_contracts.find(
        {"underlying": instrument.upper(), "expiry_date": {"$gte": today_iso}},
        {"_id": 0},
    ).to_list(length=None)

    if not contracts:
        # Distinguish "no metadata at all" vs "only expired metadata" so the audit is clear
        any_contract = await db.option_contracts.find_one(
            {"underlying": instrument.upper()},
            {"_id": 0, "expiry_date": 1},
        )
        if any_contract:
            return None, "option_contract_no_active_expiry (all stored contracts are past expiry; sync current contracts)"
        return None, "option_contract_metadata_missing"

    contract = select_contract_for_signal(
        contracts=contracts,
        underlying=instrument,
        spot_price=float(spot_price),
        direction=direction,
        moneyness=str(moneyness or "atm").lower(),
    )
    if not contract:
        return None, f"option_contract_not_found (moneyness={moneyness}, spot={spot_price}, side={direction})"
    return contract, None


async def next_expiry_for(db: Any, instrument: str, *, today_iso: Optional[str] = None) -> Optional[str]:
    """Return the next expiry_date >= today for this instrument from option_contracts.

    Uses metadata only; does not assume any weekday rule. Returns YYYY-MM-DD or None.
    """
    today = today_iso or (datetime.now(timezone.utc) + IST_OFFSET).strftime("%Y-%m-%d")
    expiries = await db.option_contracts.distinct(
        "expiry_date",
        {"underlying": instrument.upper(), "expiry_date": {"$gte": today}},
    )
    if not expiries:
        return None
    return min(expiries)


def _is_blocked_by_expiry_day_cutoff(ist_dt: datetime, next_expiry_iso: Optional[str], cutoff: time = time(15, 0)) -> Optional[str]:
    """Return blocker reason if today is the expiry day for this instrument and IST time >= cutoff.

    Implements the user rule: no trades after 15:00 IST on the expiry day for the
    deployment's instrument. Uses stored option_contracts.expiry_date as the source of truth,
    not a hard-coded weekday rule.
    """
    if not next_expiry_iso:
        return None
    today_iso = ist_dt.strftime("%Y-%m-%d")
    if today_iso != next_expiry_iso:
        return None
    if ist_dt.time() >= cutoff:
        return f"expiry_day_cutoff (IST {ist_dt.strftime('%H:%M')} >= {cutoff.strftime('%H:%M')} on expiry {next_expiry_iso})"
    return None


async def _has_recent_option_data(db: Any, instrument_key: str, *, max_age_minutes: int = 5) -> bool:
    """Whether the chosen option contract has LIVE TRADABLE data in the last N minutes.

    During a live session option premiums arrive as LTPC ticks in `ticks`; `options_1m`
    is filled only by the historical/warehouse fetch and lags the session (it has no
    today candles). So we check the live TICK first — mirroring
    paper_auto.resolve_option_entry_price's tick→options_1m order — then fall back to a
    recent warehouse candle. Checking options_1m alone falsely flags every live signal
    `option_no_data`. Signals with neither are journaled but not tracked for P&L
    (per user's decision on 2026-05-27).
    """
    if not instrument_key:
        return False
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(minutes=int(max_age_minutes))).timestamp() * 1000)
    tick = await db.ticks.find_one(
        {"instrument_key": instrument_key, "ts": {"$gte": cutoff_ms}, "last_price": {"$gt": 0}},
        {"_id": 0, "ts": 1},
    )
    if tick:
        return True
    doc = await db.options_1m.find_one(
        {"instrument_key": instrument_key, "ts": {"$gte": cutoff_ms}},
        {"_id": 0, "ts": 1},
    )
    return bool(doc)


# ---------------------------------------------------------------------------
# Phase 5B Task A4 — VIX asof resolution + realized-only day-stop accumulator.
# Both are evaluator-side concerns (the premium engine stays a pure function of
# an already-resolved vix value; the day-stop gate has no engine involvement at
# all — a breach short-circuits BEFORE the engine is ever called this bar, so
# no strike lock / ref capture / trigger can advance while it holds).
# ---------------------------------------------------------------------------

async def _resolve_vix_asof(db: Any, candle_ts: int) -> Optional[float]:
    """Stored INDIAVIX close as-of ``candle_ts`` (5-day staleness), or None if
    unverifiable. Reuses ``app.vix``'s asof helpers over the SAME candles_1m
    warehouse VIX is stored in (an AUX instrument there) — same honesty
    contract as the backtest's ``vix_by_session_map`` (recon anchor #5).
    Callers must only invoke this when a VIX gate is actually configured
    (vix_min/vix_max not None) so an ungated deployment never pays this query."""
    from app.vix import build_asof_index, vix_asof, VIX_INSTRUMENT
    lookback_ms = 7 * 24 * 60 * 60 * 1000
    rows = await db.candles_1m.find(
        {"instrument": VIX_INSTRUMENT, "ts": {"$gte": candle_ts - lookback_ms}},
        {"_id": 0, "ts": 1, "close": 1},
    ).sort("ts", -1).limit(2000).to_list(length=2000)
    # Filter the upper bound in Python (not a $lte query) so this stays
    # compatible with the repo's in-memory test fakes, which only implement
    # $gte/$gt/$exists — real Mongo would accept $lte equally well here.
    rows = [r for r in rows if int(r.get("ts") or 0) <= candle_ts]
    if not rows:
        return None
    index = build_asof_index(rows)
    return vix_asof(index, candle_ts, max_staleness_ms=5 * 24 * 60 * 60 * 1000)


async def _resolve_realized_today_rupees(db: Any, deployment: Dict[str, Any], *, today_ist: str) -> float:
    """Realized-ONLY session P&L for THIS deployment's own trades (Phase 5B
    Task A4 day-stop accumulator). Recon correction #4: the existing
    ``live_deploy_governor`` daily_loss_cap is mark-to-market (realized +
    open-unrealized) and cannot be reused for a realized-only gate — this
    reuses ONLY ``daily_realized_summary`` (the same net-P&L math the paper
    kill switches already use), over live_trades for live-mode deployments and
    paper_trades for every other mode (paper/shadow) — mirrors
    ``check_deployment_kill_switches``'s query shape."""
    from app.deployment_kill_switch import daily_realized_summary
    dep_id = str(deployment.get("id") or "")
    mode = str(deployment.get("mode") or "").lower()
    col = db.live_trades if mode == "live" else db.paper_trades
    rows = await col.find({"deployment_id": dep_id}, {"_id": 0}).to_list(length=None)
    return float(daily_realized_summary(rows, today_ist)["net"])


async def _premium_day_stop_fire_once(db: Any, deployment: Dict[str, Any], *,
                                      session_date: str, realized: float) -> bool:
    """Idempotent day-stop finalizer (5B A4). Atomically claims the lock doc's
    ``day_stop_fired`` flag ($exists filter — one winner ever, evaluator
    restarts included since the flag persists on the doc); the winner marks
    the session done (reason "day_stop", which also terminates the session
    engine on every later bar) and, for LIVE deployments only, squares this
    deployment's open premium positions through the EXISTING deployment-stop
    path (routers.deployments._square_live_positions_for_deployment — the
    guard/auto_square machinery; never a new placement path). Paper/shadow:
    block-only, per the plan's parity table. Returns True only for the winner."""
    from app.premium_lock_store import get_or_create_lock, mark_done
    dep_id = str(deployment.get("id") or "")
    await get_or_create_lock(db.premium_locks, deployment_id=dep_id, session_date=session_date)
    res = await db.premium_locks.update_one(
        {"deployment_id": dep_id, "session_date": session_date,
         "day_stop_fired": {"$exists": False}},
        {"$set": {"day_stop_fired": True,
                  "day_stop_realized": float(realized),
                  "day_stop_at": datetime.now(timezone.utc).isoformat()}},
    )
    if int(getattr(res, "matched_count", 0) or 0) != 1:
        return False
    await mark_done(db.premium_locks, deployment_id=dep_id,
                    session_date=session_date, reason="day_stop")
    if str(deployment.get("mode") or "").lower() == "live":
        try:
            from app.routers.deployments import _square_live_positions_for_deployment
            await _square_live_positions_for_deployment(dep_id, reason="premium_day_stop")
        except Exception:
            # The square is best-effort here: the guard's own exits (stop/
            # trail/EOD) still govern any open leg. The BLOCK on new entries
            # (done_for_day) is already latched above and never depends on
            # this square succeeding.
            log.exception("premium day-stop square failed for deployment %s", dep_id)
    return True


async def evaluate_deployment_on_close(
    db: Any,
    deployment: Dict[str, Any],
    *,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Evaluate a single deployment against the latest closed 1-minute candle.

    Returns a result dict describing the outcome:
      {
        "deployment_id": ...,
        "outcome": "clean" | "blocked" | "no_setup" | "skipped" | "error",
        "signal_id": <uuid if a signal was journaled>,
        "candle_ts": <epoch ms of evaluated bar>,
        "reasons": [...],
        "blockers": [...],
        "details": {...}
      }

    First version is shadow only. No paper trade is created.
    Side effects:
      - inserts at most one signal into `signals`
      - updates `strategy_deployments.{id}.last_evaluated_ts`
    """
    deployment_id = str(deployment.get("id") or "")
    strategy_id = str(deployment.get("strategy_id") or "")
    instrument = str(deployment.get("instrument") or "").upper()
    params = dict(deployment.get("params") or {})

    if deployment.get("status") != "ACTIVE":
        return {"deployment_id": deployment_id, "outcome": "skipped", "reason": "deployment_not_active"}

    if deployment.get("confirmation_mode", "1m_close") != "1m_close":
        return {"deployment_id": deployment_id, "outcome": "skipped", "reason": "non_1m_close_mode_not_supported"}

    strategy = get_registry().get(strategy_id)
    if strategy is None:
        return {"deployment_id": deployment_id, "outcome": "error", "reason": f"strategy_not_loaded: {strategy_id}"}

    # Drift check: if a strategy_source_sha was pinned at deployment creation time
    # and the current file no longer matches, auto-pause this deployment and journal
    # the drift event. Conservative: missing/None on either side = no drift.
    # Drift check: if a strategy_source_sha was pinned at deployment creation time
    # and the current file no longer matches, auto-pause this deployment and journal
    # the drift event. Conservative: missing/None on either side = no drift.
    pinned_sha = str(deployment.get("strategy_source_sha") or "")
    if pinned_sha:
        from app.strategy_source_hash import detect_drift, hash_strategy_source
        current_sha = hash_strategy_source(strategy)
        if detect_drift(pinned=pinned_sha, current=current_sha):
            await db.strategy_deployments.update_one(
                {"id": deployment_id},
                {"$set": {
                    "status": "PAUSED",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "drift_detected_at": datetime.now(timezone.utc).isoformat(),
                    "drift_pinned_sha": pinned_sha,
                    "drift_current_sha": current_sha,
                    "drift_reason": "strategy_source_drift",
                }},
            )
            log.warning(
                "strategy_source_drift on deployment %s: pinned=%s current=%s -> auto-paused",
                deployment_id, pinned_sha, current_sha,
            )
            return {
                "deployment_id": deployment_id,
                "outcome": "skipped",
                "reason": f"strategy_source_drift (pinned={pinned_sha}, current={current_sha}); deployment auto-paused",
                "drift_pinned_sha": pinned_sha,
                "drift_current_sha": current_sha,
            }

    # Kill switches (Slice 12): paper deployments only. A pause switch
    # (max_consecutive_losses / daily_loss_cutoff_pct) is a hard circuit-breaker
    # that auto-pauses the deployment, like drift. The block switch
    # (max_open_paper_trades) is soft: it adds a blocker to this bar's signal but
    # leaves the deployment ACTIVE so it self-clears as trades close.
    kill = await check_deployment_kill_switches(db, deployment)
    if kill.get("pause"):
        await db.strategy_deployments.update_one(
            {"id": deployment_id},
            {"$set": {
                "status": "PAUSED",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "kill_switch_paused_at": datetime.now(timezone.utc).isoformat(),
                "kill_switch_reason": kill.get("pause_reason"),
                "kill_switch": kill.get("pause_switch"),
                "kill_switch_inputs": kill.get("inputs"),
            }},
        )
        log.warning(
            "kill_switch on deployment %s: %s -> auto-paused",
            deployment_id, kill.get("pause_reason"),
        )
        return {
            "deployment_id": deployment_id,
            "outcome": "skipped",
            "reason": f"{kill.get('pause_reason')}; deployment auto-paused",
            "kill_switch": kill.get("pause_switch"),
            "kill_switch_inputs": kill.get("inputs"),
        }
    kill_block_reason = kill.get("block_reason")

    df = await _load_recent_candles(db, instrument, lookback=200)
    if df.empty or len(df) < MIN_BARS_FOR_EVALUATION:
        return {
            "deployment_id": deployment_id,
            "outcome": "skipped",
            "reason": f"insufficient_candles ({len(df)} < {MIN_BARS_FOR_EVALUATION})",
        }

    # Strategy needs indicator-enriched + regime-classified frame.
    merged_params = strategy.merged_params(params)
    df_enriched = precompute_all_indicators(df, merged_params)
    df_enriched["regime"] = classify_regime_series(df_enriched)
    if strategy.required_features:
        df_enriched = materialize_features(df_enriched, merged_params, strategy.required_features, {})

    last_idx = len(df_enriched) - 1
    last_bar = df_enriched.iloc[last_idx]
    prev_bar = df_enriched.iloc[last_idx - 1]
    candle_ts = int(last_bar["ts"])

    # Idempotency guard — never re-evaluate the same closed bar twice.
    if int(deployment.get("last_evaluated_ts") or 0) >= candle_ts:
        return {
            "deployment_id": deployment_id,
            "outcome": "skipped",
            "reason": "already_evaluated_this_bar",
            "candle_ts": candle_ts,
        }

    # Pre-flight: time-of-day window guard. Still record the bar as evaluated to advance pointer.
    window_blocker = _is_blocked_by_window(candle_ts)

    # Pre-flight: expiry-day cutoff (15:00 IST on the day the deployment's instrument expires).
    next_expiry_iso = await next_expiry_for(db, instrument)
    bar_ist_dt = datetime.fromtimestamp(candle_ts / 1000, tz=timezone.utc) + IST_OFFSET
    expiry_blocker = _is_blocked_by_expiry_day_cutoff(bar_ist_dt, next_expiry_iso)

    # ---- Track B: premium-momentum deployments use the premium session engine
    # instead of the generic spot evaluate + per-bar contract re-resolution. The
    # branch REJOINS the shared signal pipeline below (audit/lifecycle/dedupe all
    # apply). See docs/superpowers/specs/2026-07-10-premium-momentum-track-b-*.md
    pm_result = None
    if strategy_id == "premium_momentum":
        from app.premium_momentum_live import evaluate_premium_momentum_bar
        from app.runtime import upstox_stream_manager

        # ---- 5B A4: realized-only session day-stop gate. Checked BEFORE the
        # session engine so a breached day stops immediately (no new triggers,
        # no lazy armings). Realized-only by design (plan parity table): an
        # open leg's unrealized bleed never trips this — that would be the
        # deferred mark-to-market variant, not this rule.
        _pm_sess = _ist_session_date_of_ts(candle_ts)
        _max_loss = merged_params.get("session_max_loss_rupees")
        _max_profit = merged_params.get("session_max_profit_rupees")
        if _max_loss is not None or _max_profit is not None:
            realized = await _resolve_realized_today_rupees(db, deployment, today_ist=_pm_sess)
            breached = ((_max_loss is not None and realized <= -abs(float(_max_loss))) or
                        (_max_profit is not None and realized >= abs(float(_max_profit))))
            if breached:
                fired = await _premium_day_stop_fire_once(
                    db, deployment, session_date=_pm_sess, realized=realized)
                await _mark_deployment_evaluated(db, deployment_id, candle_ts)
                return {"deployment_id": deployment_id, "outcome": "day_stop",
                        "reason": f"session realized {realized:.2f} breached the day-stop cap",
                        "day_stop_squared": fired, "candle_ts": candle_ts}

        # ---- 5B A4: VIX gate value resolution. Engine stays pure — it receives
        # the value; unverifiable-with-a-configured-gate is decided IN the engine
        # (honest blocked outcome, never a silent pass). Only queried when the
        # gate is configured: zero overhead for every pre-5B deployment.
        _pm_vix = None
        if merged_params.get("vix_min") is not None or merged_params.get("vix_max") is not None:
            _pm_vix = await _resolve_vix_asof(db, candle_ts)

        pm_contracts = await db.option_contracts.find(
            {"underlying": instrument,
             "expiry_date": {"$gte": _ist_session_date_of_ts(candle_ts)}},
            {"_id": 0},
        ).sort([("expiry_date", 1), ("strike", 1), ("side", 1)]).to_list(length=None)
        # per-session weekly: keep only the nearest expiry >= session (blueprint
        # "current weekly"); mirrors the backtest's expiry_for_session.
        _expiries = sorted({str(c.get("expiry_date")) for c in pm_contracts if c.get("expiry_date")})
        if _expiries:
            pm_contracts = [c for c in pm_contracts if str(c.get("expiry_date")) == _expiries[0]]
        pm_result = await evaluate_premium_momentum_bar(
            locks_col=db.premium_locks, deployment=deployment, instrument=instrument,
            candle_ts=candle_ts, spot_close=float(last_bar["close"]),
            contracts=pm_contracts,
            latest_tick_map=upstox_stream_manager.latest_tick_map,
            now_ts=datetime.now(timezone.utc).timestamp(),
            vix=_pm_vix,
        )
        if pm_result.get("outcome") != "triggered":
            await _mark_deployment_evaluated(db, deployment_id, candle_ts)
            return {"deployment_id": deployment_id, "outcome": "no_setup",
                    "reason": f"premium_{pm_result.get('outcome')}",
                    "pm": {k: pm_result.get(k) for k in ("outcome", "reason", "blockers")},
                    "candle_ts": candle_ts}

    # Strategy evaluate (skipped entirely for the Track B branch: the plugin's
    # evaluate is deliberately inert — pm_result already carries the decision)
    sig = None
    if pm_result is None:
        try:
            eval_ctx = build_live_eval_ctx(strategy, df_enriched, last_idx, instrument, merged_params)
            sig = strategy.evaluate(last_bar, prev_bar, merged_params, eval_ctx)
        except Exception as exc:
            log.exception("strategy %s evaluate() failed for deployment %s", strategy_id, deployment_id)
            await _mark_deployment_evaluated(db, deployment_id, candle_ts)
            return {"deployment_id": deployment_id, "outcome": "error", "reason": f"strategy_evaluate_exception: {exc}"}

    if pm_result is None:
        direction = str(getattr(sig, "direction", "NONE") or "NONE").upper()
    else:
        direction = str(pm_result["direction"]).upper()
    if direction not in ("CE", "PE"):
        await _mark_deployment_evaluated(db, deployment_id, candle_ts)
        return {"deployment_id": deployment_id, "outcome": "no_setup", "candle_ts": candle_ts}

    # Resolve pretrade profile at signal time, snapshot for audit
    profile_name = str(deployment.get("pretrade_profile") or "Balanced")
    pretrade_settings = await _resolve_pretrade_filters(db, profile_name)
    if pm_result is not None:
        # Track B: the premium engine IS the entry gate (ref capture, momentum
        # threshold, late-lock cutoff, tick-freshness HOLDs). The score/regime
        # pretrade filter is a spot-signal concept the mechanical blueprint never
        # had (and the backtest never simulated): sig is None on this branch, so
        # feeding score=0 into any seeded profile (Balanced min=60) would journal
        # 'blocked' every bar and the strategy could NEVER trade. Bypass it —
        # window/expiry/kill blockers below still apply unchanged.
        pretrade_blockers: List[str] = []
    else:
        pretrade_blockers = _apply_pretrade_filter(
            score=int(getattr(sig, "score", 0) or 0),
            regime=last_bar.get("regime"),
            filters=pretrade_settings,
        )

    # Strategy-level blockers from the Signal dataclass
    strategy_blockers: List[str] = list(getattr(sig, "blockers", []) or [])

    # Option contract resolution (use first moneyness from policy; ATM by default)
    option_policy = dict(deployment.get("option_policy") or {})
    moneyness_list = option_policy.get("moneyness") or ["atm"]
    moneyness = str(moneyness_list[0] if isinstance(moneyness_list, list) and moneyness_list else "atm").lower()
    spot_price = float(last_bar["close"])

    if pm_result is not None:
        # Track B: the contract comes from the SESSION LOCK — never re-resolved
        # from the current bar's drifting spot (the audit's L-bypass site).
        contract, contract_blocker = dict(pm_result["contract"]), None
    else:
        contract, contract_blocker = await _resolve_option_contract(
            db,
            instrument=instrument,
            spot_price=spot_price,
            direction=direction,
            moneyness=moneyness,
        )

    # No-data flag: contract exists but has no recent option candle
    no_recent_option_data = False
    if contract:
        no_recent_option_data = not await _has_recent_option_data(
            db, str(contract.get("instrument_key") or ""), max_age_minutes=5
        )

    # Aggregate blockers; window blocker dominates so the audit is clear
    all_blockers: List[str] = []
    if window_blocker:
        all_blockers.append(window_blocker)
    if expiry_blocker:
        all_blockers.append(expiry_blocker)
    if kill_block_reason:
        all_blockers.append(kill_block_reason)
    all_blockers.extend(pretrade_blockers)
    all_blockers.extend(strategy_blockers)
    if contract_blocker:
        all_blockers.append(contract_blocker)

    # Build the signal document with full audit context. Reasons + score + strategy hash
    # are captured immutably so future review can be trusted.
    strategy_hash = compute_strategy_hash(strategy_id, getattr(strategy, "version", "") or "", merged_params)
    audit_context = {
        "deployment_id": deployment_id,
        "deployment_name": deployment.get("name"),
        "deployment_mode": deployment.get("mode"),
        "source_type": deployment.get("source_type"),
        "source_id": deployment.get("source_id"),
        "strategy_version": getattr(strategy, "version", "") or "",
        "strategy_hash": strategy_hash,
        "strategy_source_sha": pinned_sha if pinned_sha else None,
        "params": merged_params,
        "pretrade_profile_name": profile_name,
        "pretrade_settings_snapshot": pretrade_settings,
        "regime": last_bar.get("regime"),
        "candle": {
            "ts": candle_ts,
            "open": float(last_bar.get("open", 0)),
            "high": float(last_bar.get("high", 0)),
            "low": float(last_bar.get("low", 0)),
            "close": spot_price,
            "volume": float(last_bar.get("volume", 0) or 0),
            "ist_time": _ist_time_of_ts(candle_ts).strftime("%H:%M"),
        },
        # pm branch: sig is None — audit score must match the journaled
        # confidence (100), never a contradictory 0 from the inert Signal.
        "score": 100 if pm_result is not None else int(getattr(sig, "score", 0) or 0),
        "tracked_for_pnl": bool(contract and not no_recent_option_data and not all_blockers),
        # Trustworthy timing audit per user spec (2026-05-27).
        "bar_ts": candle_ts,                                         # the candle minute the strategy evaluated
        "decision_ts": datetime.now(timezone.utc).isoformat(),       # wall-clock when the evaluator decided
        "next_expiry_iso": next_expiry_iso,                          # source of truth for expiry-day cutoff
    }
    if pm_result is not None:
        # Honest audit: the settings snapshot above is what the profile WOULD
        # have applied — record explicitly that the branch did not apply it.
        audit_context["pretrade_bypassed"] = (
            "premium_momentum (mechanical engine gates entry; score/regime filter not applicable)"
        )
    if no_recent_option_data:
        audit_context["option_no_data"] = True
        all_blockers.append("option_no_data (no candle in last 5 minutes; signal recorded but not tracked for P&L)")

    if pm_result is None:
        signal_doc = create_signal_doc(
            instrument=instrument,
            direction=direction,
            strategy_id=strategy_id,
            entry_price=spot_price,
            confidence=getattr(sig, "score", None),
            reasons=getattr(sig, "reasons", []) or [],
            option_contract=contract or {},
            context=audit_context,
        )
    else:
        _pm_ref = float(pm_result["ref_premium"])
        _pm_now = float(pm_result["premium_now"])
        _pm_pct = ((_pm_now - _pm_ref) / _pm_ref * 100.0) if _pm_ref else 0.0
        signal_doc = create_signal_doc(
            instrument=instrument,
            direction=direction,
            strategy_id=strategy_id,
            entry_price=spot_price,
            confidence=100,
            reasons=[f"premium +{_pm_pct:.1f}% over ref"],
            option_contract=contract or {},
            context=audit_context,
        )
    signal_doc["deployment_id"] = deployment_id
    signal_doc["candle_ts"] = candle_ts
    signal_doc["blockers"] = all_blockers
    signal_doc["blocked"] = bool(all_blockers)
    # The strategy's own exit definition, captured at signal time so paper trades
    # (auto or approved) can honor the SAME exits the backtest simulated.
    signal_doc["risk_hints"] = {
        "target_pct": getattr(sig, "target_pct", None),
        "stop_pct": getattr(sig, "stop_pct", None),
        "spot_target_pts": getattr(sig, "spot_target_pts", None),
        "spot_stop_pts": getattr(sig, "spot_stop_pts", None),
        "time_stop_minutes": getattr(sig, "time_stop_minutes", None),
    }
    if pm_result is not None:
        # merged_params, NOT raw deployment params: a deployment whose params
        # omit stop_pct must journal the plugin-schema default (20.0), or the
        # live exit plan silently falls through to the 50% deep-default floor.
        # 5B A4: lazy legs (lce/lpe) exit on their OWN params (blueprint §4);
        # lazy_stop_pct falls back to the primary stop (a stop must always
        # exist), lazy_target_pct does NOT fall back (None = ride to EOD,
        # mirroring the schema's own default semantics).
        _pm_leg = str(pm_result.get("leg") or "")
        _pm_is_lazy = _pm_leg in ("lce", "lpe")
        _pm_stop = merged_params.get("stop_pct")
        _pm_target = merged_params.get("target_pct")
        if _pm_is_lazy:
            if merged_params.get("lazy_stop_pct") is not None:
                _pm_stop = merged_params.get("lazy_stop_pct")
            _pm_target = merged_params.get("lazy_target_pct")
        signal_doc["risk_hints"] = {
            "target_pct": _pm_target,
            "stop_pct": _pm_stop,
            "spot_target_pts": None, "spot_stop_pts": None,
            "time_stop_minutes": None,
        }
        # 5B A4: per-deployment hard square time as a risk hint, clamped
        # STRICTLY BEFORE the system 15:00 EOD square — the EOD backstop
        # always wins (plan parity table: EXP2's 15:13 is backtest-only). The
        # guard-side honoring lands in Task B5; until then this hint is
        # journaled but inert.
        _pm_exit_t = merged_params.get("exit_time")
        if _pm_exit_t and str(_pm_exit_t) < "15:00":
            signal_doc["risk_hints"]["square_at_ist"] = str(_pm_exit_t)
        signal_doc["premium_momentum"] = {
            "ref_premium": pm_result["ref_premium"],
            "premium_now": pm_result["premium_now"],
        }
        # leg identity travels ONLY in both-mode: first_to_trigger signal docs
        # stay byte-identical to Track B's shipped shape (an existing test pins
        # the sub-dict with exact equality — that pin is the guarantee).
        if str(merged_params.get("leg_mode") or "first_to_trigger").lower() == "both":
            signal_doc["premium_momentum"]["leg"] = _pm_leg or None

    if all_blockers:
        # Record as SKIPPED via lifecycle: WATCHING -> AUDITED is allowed but we also need
        # to capture the SKIPPED reason. The state machine allows TRIGGERED -> SKIPPED, so we
        # mark blocked signals as AUDITED with an explicit blocker list to keep the lifecycle clean.
        signal_doc = transition_signal(
            signal_doc,
            "AUDITED",
            reason=f"blocked: {'; '.join(all_blockers)[:200]}",
            snapshot={"blockers": all_blockers, "context": audit_context},
        )
        outcome = "blocked"
    else:
        # Clean signal: WATCHING -> FORMING -> CONFIRMED. Awaiting manual approval next slice.
        signal_doc = transition_signal(signal_doc, "FORMING", reason="strategy direction set")
        signal_doc = transition_signal(
            signal_doc,
            "CONFIRMED",
            reason="passed pretrade filter and contract resolution",
            snapshot={"contract": contract, "context": audit_context},
        )
        outcome = "clean"

    try:
        await db.signals.insert_one(signal_doc)
    except Exception as exc:
        # The unique partial index on (deployment_id, candle_ts) raises a duplicate-key
        # error if a signal for this bar already exists. Treat as idempotent skip rather
        # than crashing - the bar was already journaled (likely from a crash recovery
        # or an out-of-order scheduler tick).
        msg = str(exc).lower()
        if "duplicate" in msg or "e11000" in msg:
            log.info(
                "signals.insert_one duplicate for deployment=%s bar=%s; treating as already_journaled",
                deployment_id, candle_ts,
            )
            await _mark_deployment_evaluated(db, deployment_id, candle_ts)
            return {
                "deployment_id": deployment_id,
                "outcome": "skipped",
                "reason": "already_journaled (duplicate key on insert)",
                "candle_ts": candle_ts,
            }
        raise
    if pm_result is not None and outcome == "clean":
        _latch_leg = str(pm_result.get("leg") or "")
        if str(merged_params.get("leg_mode") or "first_to_trigger").lower() == "both" and _latch_leg:
            # 5B A4: both-mode latches PER LEG (pce/ppe/lce/lpe) — one leg's
            # latch never blocks the other's monitoring. first_to_trigger
            # keeps the legacy session-global latch below, byte-identically.
            from app.premium_lock_store import latch_trigger_leg
            latched = await latch_trigger_leg(db.premium_locks,
                                              deployment_id=deployment_id,
                                              session_date=_ist_session_date_of_ts(candle_ts),
                                              leg=_latch_leg)
        else:
            from app.premium_lock_store import latch_trigger
            latched = await latch_trigger(db.premium_locks,
                                          deployment_id=deployment_id,
                                          session_date=_ist_session_date_of_ts(candle_ts),
                                          side=direction)
        if not latched:
            # The lock store refused the latch (concurrent first-to-trigger win,
            # or done_for_day flipped mid-bar, e.g. the EOD backstop). Fail SAFE
            # on trades: downgrade the outcome so the sink tee (outcome=="clean")
            # never routes this signal; the CONFIRMED journal row remains as the
            # visible audit trail of the refused pass.
            log.warning(
                "premium latch refused for deployment=%s bar=%s side=%s — "
                "signal journaled but NOT routed to a trade sink",
                deployment_id, candle_ts, direction,
            )
            outcome = "latch_refused"
    await _mark_deployment_evaluated(db, deployment_id, candle_ts)

    return {
        "deployment_id": deployment_id,
        "outcome": outcome,
        "signal_id": signal_doc.get("id"),
        "candle_ts": candle_ts,
        "direction": direction,
        "score": signal_doc.get("confidence"),
        "blockers": all_blockers,
        "reasons": signal_doc.get("reasons", []),
        "tracked_for_pnl": audit_context.get("tracked_for_pnl"),
    }


async def _mark_deployment_evaluated(db: Any, deployment_id: str, candle_ts: int) -> None:
    """Persist the latest evaluated bar timestamp on the deployment for idempotency."""
    await db.strategy_deployments.update_one(
        {"id": deployment_id},
        {"$set": {"last_evaluated_ts": int(candle_ts), "updated_at": datetime.now(timezone.utc).isoformat()}},
    )


async def evaluate_active_deployments(
    db: Any,
    *,
    latest_tick_lookup: Optional[Any] = None,
    live_ctx: Optional[Dict[str, Any]] = None,
    now_utc: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Evaluate all ACTIVE deployments — each one independently.

    Deployments are intentionally independent (user decision 2026-06-12): when
    two strategies fire on the same instrument at the same minute, BOTH journal
    and BOTH may paper-trade, enabling honest head-to-head comparison. (The old
    highest-score-wins concurrency rule that demoted lower-score signals with
    `concurrency_lower_score` was removed; per-deployment exposure is governed
    by the `max_open_paper_trades` kill switch.)

    Sink routing per clean signal (the continuous tee):
      - An ARMED deployment (risk.live armed within its window, broker connected)
        routes its confirmed signal to ``auto_live`` (a REAL order) AND SUPPRESSES
        the paper path for that signal (if/elif — never both; the shared
        ``paper_trade_claim`` also enforces one trade per signal).
      - Otherwise a paper deployment with risk.auto_paper opens a paper trade for
        the signal automatically (unchanged behavior).

    ``live_ctx`` injects the live collaborators for tests; when None (production)
    it is lazily built via ``build_live_deploy_context(db)``. If that returns None
    (broker not connected / not configured) live is treated as DISABLED and the
    flow falls through to auto_paper. Backward compatible: existing callers that
    pass only ``db`` + ``latest_tick_lookup`` keep working (live_ctx defaults None
    and the lazy builder never raises when the broker is unconfigured).
    """
    cursor = db.strategy_deployments.find({"status": "ACTIVE"}, {"_id": 0})
    deployments = await cursor.to_list(length=None)
    results: List[Dict[str, Any]] = []
    for deployment in deployments:
        try:
            res = await evaluate_deployment_on_close(db, deployment)
            results.append(res)
        except Exception as exc:
            log.exception("evaluator failed for deployment %s", deployment.get("id"))
            results.append({"deployment_id": deployment.get("id"), "outcome": "error", "reason": str(exc)})

    # Resolve the live-deploy context once for this pass. Tests inject it; in
    # production it is lazily built (None when the broker is not connected). The
    # builder NEVER raises when the broker is unconfigured. We only bother building
    # it when there is at least one clean signal to route.
    if live_ctx is None and any(r.get("outcome") == "clean" and r.get("signal_id") for r in results):
        try:
            from app.live_deploy_context import build_live_deploy_context
            live_ctx = await build_live_deploy_context(db)
        except Exception as exc:
            log.warning("build_live_deploy_context failed (%s) — live disabled this pass", exc)
            live_ctx = None
    live_connected = bool(live_ctx and live_ctx.get("connected"))
    live_kwargs: Dict[str, Any] = {}
    if live_ctx:
        for k in ("place_fn", "arm_for", "client", "intent_store", "engine",
                  "search_fn", "throttle", "account_max", "connected",
                  "band_pct", "uid", "actid", "allow_fn"):
            if k in live_ctx:
                live_kwargs[k] = live_ctx[k]
    now = now_utc or datetime.now(timezone.utc)

    # Sink routing for clean signals.
    from app.paper_auto import auto_paper_enabled, auto_paper_trade_for_signal
    from app.auto_live import auto_live_enabled, auto_live_trade_for_signal
    dep_by_id = {str(d.get("id") or ""): d for d in deployments}
    for r in results:
        if r.get("outcome") != "clean" or not r.get("signal_id"):
            continue
        deployment = dep_by_id.get(str(r.get("deployment_id") or ""))
        if not deployment:
            continue
        try:
            # Re-read the signal state: it must still be a clean CONFIRMED
            # signal (guards against concurrent manual mutations). This race
            # guard precedes BOTH sinks.
            sig = await db.signals.find_one({"id": r["signal_id"]}, {"_id": 0})
            if not sig or str(sig.get("state") or "").upper() != "CONFIRMED" or sig.get("blocked"):
                continue
            if auto_live_enabled(deployment, now, connected=live_connected):
                # ARMED → real order; PAPER is suppressed for this signal (if/elif).
                r["auto_live"] = await auto_live_trade_for_signal(
                    db, deployment, sig, latest_tick_lookup=latest_tick_lookup,
                    now_utc=now, **live_kwargs,
                )
            elif auto_paper_enabled(deployment):
                r["auto_paper"] = await auto_paper_trade_for_signal(
                    db, deployment, sig, latest_tick_lookup=latest_tick_lookup,
                )
        except Exception as exc:
            log.exception("sink hook failed for signal %s", r.get("signal_id"))
            # Attribute the error to whichever sink was selected for this signal.
            if auto_live_enabled(deployment, now, connected=live_connected):
                r["auto_live"] = {"created": False, "error": str(exc)}
            else:
                r["auto_paper"] = {"created": False, "error": str(exc)}
    return results
