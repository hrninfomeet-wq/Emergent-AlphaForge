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
from app.signal_lifecycle import create_signal_doc, transition_signal
from app.strategies.base import StrategyBase, get_registry

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
    """Check whether the chosen option contract has at least one candle in the last N minutes.

    Used to flag signals where the contract has no live tradable data, so they get journaled
    but not tracked for P&L (per user's decision on 2026-05-27).
    """
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(minutes=int(max_age_minutes))).timestamp() * 1000)
    doc = await db.options_1m.find_one(
        {"instrument_key": instrument_key, "ts": {"$gte": cutoff_ms}},
        {"_id": 0, "ts": 1},
    )
    return bool(doc)


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

    # Strategy evaluate
    try:
        sig = strategy.evaluate(last_bar, prev_bar, merged_params, {"history_df": df_enriched, "i": last_idx})
    except Exception as exc:
        log.exception("strategy %s evaluate() failed for deployment %s", strategy_id, deployment_id)
        await _mark_deployment_evaluated(db, deployment_id, candle_ts)
        return {"deployment_id": deployment_id, "outcome": "error", "reason": f"strategy_evaluate_exception: {exc}"}

    direction = str(getattr(sig, "direction", "NONE") or "NONE").upper()
    if direction not in ("CE", "PE"):
        await _mark_deployment_evaluated(db, deployment_id, candle_ts)
        return {"deployment_id": deployment_id, "outcome": "no_setup", "candle_ts": candle_ts}

    # Resolve pretrade profile at signal time, snapshot for audit
    profile_name = str(deployment.get("pretrade_profile") or "Balanced")
    pretrade_settings = await _resolve_pretrade_filters(db, profile_name)
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
        "score": int(getattr(sig, "score", 0) or 0),
        "tracked_for_pnl": bool(contract and not no_recent_option_data and not all_blockers),
        # Trustworthy timing audit per user spec (2026-05-27).
        "bar_ts": candle_ts,                                         # the candle minute the strategy evaluated
        "decision_ts": datetime.now(timezone.utc).isoformat(),       # wall-clock when the evaluator decided
        "next_expiry_iso": next_expiry_iso,                          # source of truth for expiry-day cutoff
    }
    if no_recent_option_data:
        audit_context["option_no_data"] = True
        all_blockers.append("option_no_data (no candle in last 5 minutes; signal recorded but not tracked for P&L)")

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
    signal_doc["deployment_id"] = deployment_id
    signal_doc["candle_ts"] = candle_ts
    signal_doc["blockers"] = all_blockers
    signal_doc["blocked"] = bool(all_blockers)

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

    await db.signals.insert_one(signal_doc)
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


async def evaluate_active_deployments(db: Any) -> List[Dict[str, Any]]:
    """Evaluate all ACTIVE deployments. If multiple deployments fire on the same instrument
    at the same minute, the highest-scoring clean signal is kept and lower-scoring ones
    are journaled as blocked with reason `concurrency_lower_score`.
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

    # Concurrency rule (option b): per (instrument, candle_ts), keep highest-scoring clean signal,
    # demote others to blocked with reason `concurrency_lower_score`.
    await _apply_concurrency_rule(db, results)
    return results


async def _apply_concurrency_rule(db: Any, results: List[Dict[str, Any]]) -> None:
    by_bar: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for r in results:
        if r.get("outcome") != "clean":
            continue
        signal_id = r.get("signal_id")
        candle_ts = r.get("candle_ts")
        if not signal_id or not candle_ts:
            continue
        # Need instrument from the signal record
        sig = await db.signals.find_one({"id": signal_id}, {"_id": 0, "instrument": 1, "confidence": 1})
        if not sig:
            continue
        key = (str(sig["instrument"]), int(candle_ts))
        by_bar.setdefault(key, []).append({"signal_id": signal_id, "score": float(sig.get("confidence") or 0), "result": r})

    for (instrument, ts), entries in by_bar.items():
        if len(entries) <= 1:
            continue
        entries.sort(key=lambda e: e["score"], reverse=True)
        winner = entries[0]
        losers = entries[1:]
        for loser in losers:
            blocker = f"concurrency_lower_score (kept signal_id={winner['signal_id']} score={winner['score']})"
            await db.signals.update_one(
                {"id": loser["signal_id"]},
                {"$set": {"state": "AUDITED", "blocked": True, "blockers": [blocker], "concurrency_demoted": True}},
            )
            loser["result"]["outcome"] = "blocked"
            loser["result"].setdefault("blockers", []).append(blocker)
