"""Forward-testing metrics for strategy deployments.

Metrics are intentionally gated by session completeness so intermittent local-PC
runtime does not make a deployment look better or worse than it really was.
"""

from __future__ import annotations

import asyncio
import logging
import math
import weakref
from datetime import date, datetime, time, timedelta, timezone
# NOT `import time` — the name is already bound to datetime.time above.
from time import monotonic as _monotonic
from typing import Any, Dict, Iterable, List, Optional, Set

log = logging.getLogger(__name__)

from app.nse_calendar import trading_days_in_range


IST = timezone(timedelta(hours=5, minutes=30))
SESSION_START_IST = time(10, 0)
SESSION_END_IST = time(15, 0)
EXPECTED_SESSION_MINUTES = 300
COMPLETE_SESSION_RATIO = 0.70
THRESHOLD_MINUTES = int(EXPECTED_SESSION_MINUTES * COMPLETE_SESSION_RATIO)
MIN_COMPLETE_SESSIONS_FOR_LIBRARY = 10
PROMOTION_SESSION_START_IST = time(9, 15)
PROMOTION_SESSION_END_IST = time(15, 30)
PROMOTION_EXPECTED_SESSION_MINUTES = 375
PROMOTION_COMPLETE_SESSION_RATIO = 0.95
PROMOTION_THRESHOLD_MINUTES = math.ceil(
    PROMOTION_EXPECTED_SESSION_MINUTES * PROMOTION_COMPLETE_SESSION_RATIO)


def _qualifying_account_capital(config: Optional[Dict[str, Any]]) -> bool:
    """True only for the user's frozen ₹2L, non-compounding account gate."""
    if not config:
        return False
    try:
        amount = float(config.get("amount") or 0)
    except (TypeError, ValueError):
        return False
    return (
        abs(amount - 200_000.0) < 0.01
        and str(config.get("basis") or "").lower() == "fixed"
    )


def _policy_lots(configured_lots: int, observed_lots: Set[int]) -> int:
    """Return 1 only when both configuration and every observed trade are one lot."""
    if int(configured_lots or 0) == 1 and observed_lots.issubset({1}):
        return 1
    if int(configured_lots or 0) != 1:
        return int(configured_lots or 0)
    return max(observed_lots or {0})


def _trade_has_qualifying_account_capital(trade: Dict[str, Any]) -> bool:
    for check in trade.get("capital_gate_evidence") or []:
        if (
            check.get("allowed") is True
            and str(check.get("scope") or "") == "account"
            and _qualifying_account_capital({
                "amount": check.get("capital"), "basis": check.get("basis"),
            })
        ):
            return True
    return False


def _today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def _ist_date(value: Any) -> Optional[str]:
    dt = _parse_datetime(value)
    return dt.date().isoformat() if dt else None


def _ist_from_ts_ms(ts_ms: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).astimezone(IST)
    except (TypeError, ValueError, OSError):
        return None


def _ist_ms(day: str, at: time) -> int:
    ist_dt = datetime.combine(date.fromisoformat(day), at, tzinfo=IST)
    return int(ist_dt.astimezone(timezone.utc).timestamp() * 1000)


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _promotion_trade_pnl(trade: Dict[str, Any], *, is_live: bool = False) -> float:
    """Use executable-surface P&L; haircut uncovered winners to zero.

    The policy permits up to 5% missing surfaces for operational tolerance. It
    must not let those missing observations contribute optimistic legacy/LTP
    profits, while known losses still belong to the account record.
    """
    execution = _float_or_none(trade.get("execution_realized_pnl"))
    if execution is not None:
        return execution
    realized = float(_float_or_none(trade.get("realized_pnl")) or 0.0)
    if is_live:
        # A LIVE trade's realized_pnl is already broker-measured — entry from the
        # captured fill, exit from the fill — so it IS the executable surface.
        # `execution_realized_pnl` is written only by the paper path, so haircutting
        # on its absence would zero every live WINNER while counting every loser in
        # full, making a real cohort look catastrophic.
        return realized
    return min(realized, 0.0)


#: IST offset in milliseconds, for server-side date maths in the aggregation.
_IST_OFFSET_MS = 5 * 3600 * 1000 + 30 * 60 * 1000

#: Memo for `_session_counts`. The paper strategy-stats route computes forward
#: metrics for every deployment, and they overwhelmingly share an instrument and
#: an overlapping day range — measured 2026-09-16: 16 calls, 5 distinct keys,
#: 1102ms, of which ~758ms was pure duplication. Keyed on the exact query so a
#: hit is byte-identical to a miss.
#:
#: TTL is deliberately short. These are per-day COUNTS of distinct traded
#: minutes: every historical day is immutable, and only TODAY moves — by one
#: minute per minute, while the roller appends. A few seconds of lag on today's
#: count cannot change a completeness verdict, which is a whole-session question.
_SESSION_COUNTS_TTL_S = 20.0
_SESSION_COUNTS_CACHE: Dict[Any, tuple] = {}
#: SINGLE-FLIGHT. The strategy-stats route now fans its deployments out with
#: asyncio.gather, so without this every one of them would miss the empty cache
#: in the same tick and run the same aggregation concurrently — the memo would
#: only ever help across requests, never within one, which is the case that
#: actually hurts. One lock per key: the first caller computes, the rest wait
#: and read the value it stored.
_SESSION_COUNTS_LOCKS: Dict[Any, "asyncio.Lock"] = {}


def _session_counts_cache_clear() -> None:
    """Drop the memo. Tests use this so one case cannot leak into the next."""
    _SESSION_COUNTS_CACHE.clear()
    _SESSION_COUNTS_LOCKS.clear()


async def _session_counts(
    db: Any,
    *,
    instrument: str,
    session_days: Iterable[str],
    window_start: time = SESSION_START_IST,
    window_end: time = SESSION_END_IST,
) -> Dict[str, int]:
    days = list(session_days)
    if not days:
        return {}
    start_ts = _ist_ms(days[0], window_start)
    end_ts = _ist_ms(days[-1], window_end)

    # SCOPED TO THE DATABASE OBJECT. Production has exactly one, so this is inert
    # there — but the test suite builds a fresh stand-in DB per case, and without
    # this two cases sharing a query shape would read each other's counts. That is
    # not a test-only nicety: these counts gate the forward-validation completeness
    # verdict, so a cache that can answer for the wrong dataset is a correctness
    # bug wearing a performance hat. Found by the full suite, which reddened
    # test_forward_metrics_hides_strategy_library_until_ten_complete_sessions while
    # it passed in isolation.
    key = (id(db), str(instrument), start_ts, end_ts,
           window_start.hour * 60 + window_start.minute,
           window_end.hour * 60 + window_end.minute,
           tuple(days))
    # id() alone is not enough: CPython recycles ids, so a GC'd stand-in DB could
    # hand its id to a new one and serve it the old counts. Hold a WEAK reference
    # and verify identity on read — a dead referent reads as a miss, and a live one
    # proves the entry belongs to THIS database. Weak refs keep nothing alive.
    try:
        db_ref: Any = weakref.ref(db)
    except TypeError:
        db_ref = None  # unweakrefable stand-in -> simply do not cache

    def _fresh() -> Optional[Dict[str, int]]:
        hit = _SESSION_COUNTS_CACHE.get(key)
        if hit is None or (_monotonic() - hit[0]) >= _SESSION_COUNTS_TTL_S:
            return None
        ref = hit[2]
        if ref is not None and ref() is not db:
            return None  # recycled id, or a different database — treat as a miss
        return dict(hit[1])

    cached = _fresh()
    if cached is not None:
        return cached

    lock = _SESSION_COUNTS_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        # Re-check: a concurrent caller may have filled it while we queued.
        cached = _fresh()
        if cached is not None:
            return cached
        try:
            counts = await _session_counts_grouped(
                db, instrument=instrument, days=days, start_ts=start_ts, end_ts=end_ts,
                window_start=window_start, window_end=window_end,
            )
        except Exception as exc:  # aggregation unsupported / stand-in DB in tests
            log.debug("session_counts: aggregation unavailable (%s) — scanning", exc)
            counts = await _session_counts_scan(
                db, instrument=instrument, days=days, start_ts=start_ts, end_ts=end_ts,
                window_start=window_start, window_end=window_end,
            )
        if db_ref is not None:
            # Bound the dict so a long-lived process cannot accumulate keys
            # forever; entries are cheap and the TTL is short, so a crude clear is
            # fine and keeps this free of an LRU dependency.
            if len(_SESSION_COUNTS_CACHE) > 512:
                _SESSION_COUNTS_CACHE.clear()
                _SESSION_COUNTS_LOCKS.clear()
            _SESSION_COUNTS_CACHE[key] = (_monotonic(), dict(counts), db_ref)
        return counts


async def _session_counts_grouped(
    db: Any, *, instrument: str, days: List[str], start_ts: int, end_ts: int,
    window_start: time, window_end: time,
) -> Dict[str, int]:
    """Per-day distinct traded-minute counts, grouped SERVER-SIDE.

    The scan below pulled every 1m candle ts in range into Python purely to count
    distinct minutes per day — ~78 sessions x 375 minutes is ~29k documents per
    call, and the paper strategy-stats route made 16 such calls. This returns one
    row per day instead. Semantics are identical to `_session_counts_scan`, which
    remains as the fallback and as the oracle the equality test compares against.
    """
    start_min = window_start.hour * 60 + window_start.minute
    end_min = window_end.hour * 60 + window_end.minute
    pipeline = [
        {"$match": {"instrument": instrument, "ts": {"$gte": start_ts, "$lt": end_ts}}},
        # Shift to IST before extracting the day and minute-of-day, mirroring
        # `_ist_from_ts_ms`. Same +5:30 trick the warehouse day-aggregation uses.
        {"$project": {"_id": 0, "ist": {"$add": [{"$toDate": "$ts"}, _IST_OFFSET_MS]}}},
        {"$project": {
            "day": {"$dateToString": {"format": "%Y-%m-%d", "date": "$ist"}},
            "mod": {"$add": [{"$multiply": [{"$hour": "$ist"}, 60]}, {"$minute": "$ist"}]},
        }},
        # Half-open [start, end) — the scan uses `window_start <= t < window_end`.
        {"$match": {"mod": {"$gte": start_min, "$lt": end_min}}},
        {"$group": {"_id": "$day", "minutes": {"$addToSet": "$mod"}}},
        {"$project": {"_id": 1, "n": {"$size": "$minutes"}}},
    ]
    rows = await db.candles_1m.aggregate(pipeline).to_list(length=None)
    by_day = {str(r.get("_id")): int(r.get("n") or 0) for r in rows}
    # Every requested day is present, missing ones at 0 — the scan seeded its dict
    # from `days`, and callers index it directly.
    return {day: by_day.get(day, 0) for day in days}


async def _session_counts_scan(
    db: Any, *, instrument: str, days: List[str], start_ts: int, end_ts: int,
    window_start: time, window_end: time,
) -> Dict[str, int]:
    """The original client-side scan. Kept as the fallback and the test oracle."""
    cursor = db.candles_1m.find(
        {
            "instrument": instrument,
            "ts": {"$gte": start_ts, "$lt": end_ts},
        },
        {"_id": 0, "ts": 1},
    ).sort("ts", 1)
    rows = await cursor.to_list(length=None)

    day_set = set(days)
    minute_keys: Dict[str, Set[int]] = {day: set() for day in days}
    for row in rows:
        ist_dt = _ist_from_ts_ms(row.get("ts"))
        if not ist_dt:
            continue
        day = ist_dt.date().isoformat()
        if day not in day_set:
            continue
        if not (window_start <= ist_dt.time() < window_end):
            continue
        minute_keys[day].add(ist_dt.hour * 60 + ist_dt.minute)
    return {day: len(values) for day, values in minute_keys.items()}


def _summarize_sessions(
    days: List[str],
    counts: Dict[str, int],
    *,
    window_start: time = SESSION_START_IST,
    window_end: time = SESSION_END_IST,
    expected_minutes: int = EXPECTED_SESSION_MINUTES,
    threshold_ratio: float = COMPLETE_SESSION_RATIO,
) -> Dict[str, Any]:
    threshold_minutes = math.ceil(int(expected_minutes) * float(threshold_ratio))
    sessions: List[Dict[str, Any]] = []
    complete = 0
    partial = 0
    missing = 0
    for day in days:
        stored = int(counts.get(day) or 0)
        ratio = round(stored / expected_minutes, 4)
        is_complete = stored >= threshold_minutes
        if is_complete:
            complete += 1
        elif stored > 0:
            partial += 1
        else:
            missing += 1
        sessions.append({
            "date": day,
            "stored_minutes": stored,
            "expected_minutes": expected_minutes,
            "completeness": ratio,
            "status": "complete" if is_complete else ("partial" if stored > 0 else "missing"),
        })

    total_expected = len(days) * expected_minutes
    total_stored = sum(int(counts.get(day) or 0) for day in days)
    return {
        "window_start_ist": window_start.strftime("%H:%M"),
        "window_end_ist": window_end.strftime("%H:%M"),
        "expected_minutes_per_session": expected_minutes,
        "complete_threshold_ratio": threshold_ratio,
        "threshold_minutes": threshold_minutes,
        "expected_session_count": len(days),
        "complete_session_count": complete,
        "partial_session_count": partial,
        "missing_session_count": missing,
        "overall_completeness": round(total_stored / total_expected, 4) if total_expected else 0.0,
        "recent_sessions": sessions[-20:],
    }


def trades_collection_for_mode(db: Any, mode: Any):
    """The trade collection a deployment in *mode* actually writes to.

    Mirrors `deployment_evaluator`'s existing
    `col = db.live_trades if mode == "live" else db.paper_trades`. That branch
    existed for the daily-realized read but was never applied to the metrics
    aggregations, so a promoted deployment read as dead on every performance
    surface the moment it started mattering.
    """
    return db.live_trades if str(mode or "").lower() == "live" else db.paper_trades


async def _closed_trades(db: Any, deployment_id: str, *,
                         mode: Any = "paper") -> List[Dict[str, Any]]:
    cursor = trades_collection_for_mode(db, mode).find(
        {"deployment_id": deployment_id, "status": "CLOSED"},
        {"_id": 0},
    ).sort("closed_at", 1)
    return await cursor.to_list(length=None)


async def _open_trades(db: Any, deployment_id: str, *,
                       mode: Any = "paper") -> List[Dict[str, Any]]:
    cursor = trades_collection_for_mode(db, mode).find(
        {"deployment_id": deployment_id, "status": "OPEN"}, {"_id": 0},
    ).sort("created_at", 1)
    return await cursor.to_list(length=None)


def _trade_session_date(trade: Dict[str, Any]) -> Optional[str]:
    return (
        _ist_date(trade.get("created_at"))
        or _ist_date(trade.get("opened_at"))
        or _ist_date(trade.get("closed_at"))
        or _ist_date(trade.get("updated_at"))
    )


def _trade_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnls = [pnl for pnl in (_float_or_none(trade.get("realized_pnl")) for trade in trades) if pnl is not None]
    if not pnls:
        return {
            "trade_count": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "total_pnl": 0.0,
            "profit_factor": None,
        }

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakeven = [p for p in pnls if p == 0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    return {
        "trade_count": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": round(len(wins) / len(pnls) * 100, 2),
        "avg_pnl": round(sum(pnls) / len(pnls), 2),
        "total_pnl": round(sum(pnls), 2),
        "profit_factor": round(gross_profit / abs(gross_loss), 3) if gross_loss < 0 else None,
    }


def _count_option_entry_surface_misses(
    signals: Iterable[Dict[str, Any]], complete_days: Set[str]
) -> int:
    """Count attempted option entries that could not obtain any usable price."""
    count = 0
    for signal in signals:
        if signal.get("paper_trade_id"):
            continue
        reason = str(signal.get("paper_trade_error") or "")
        if not (
            reason.startswith("option_entry_price_unavailable")
            or reason.startswith("no_option_contract")
        ):
            continue
        day = _ist_date(signal.get("created_at")) or _ist_date(
            (signal.get("context") or {}).get("decision_ts"))
        if day in complete_days:
            count += 1
    return count


async def _option_entry_surface_miss_count(
    db: Any, deployment_id: str, complete_days: Set[str]
) -> int:
    collection = getattr(db, "signals", None)
    if collection is None or not complete_days:
        return 0
    cursor = collection.find(
        {"deployment_id": deployment_id},
        {"_id": 0, "created_at": 1, "context.decision_ts": 1,
         "paper_trade_id": 1, "paper_trade_error": 1},
    )
    rows = await cursor.to_list(length=None)
    return _count_option_entry_surface_misses(rows, complete_days)


async def compute_forward_metrics_for_deployment(
    db: Any,
    deployment: Dict[str, Any],
    *,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """Return session-gated paper-trade metrics for one deployment."""
    deployment_id = str(deployment.get("id") or "")
    instrument = str(deployment.get("instrument") or "").upper()
    today_iso = today or _today_ist()
    start_date = _ist_date(deployment.get("created_at")) or today_iso
    if start_date > today_iso:
        start_date = today_iso

    days = trading_days_in_range(start_date, today_iso)
    counts = await _session_counts(db, instrument=instrument, session_days=days)
    session_summary = _summarize_sessions(days, counts)
    promotion_counts = await _session_counts(
        db, instrument=instrument, session_days=days,
        window_start=PROMOTION_SESSION_START_IST,
        window_end=PROMOTION_SESSION_END_IST,
    )
    promotion_session_summary = _summarize_sessions(
        days, promotion_counts,
        window_start=PROMOTION_SESSION_START_IST,
        window_end=PROMOTION_SESSION_END_IST,
        expected_minutes=PROMOTION_EXPECTED_SESSION_MINUTES,
        threshold_ratio=PROMOTION_COMPLETE_SESSION_RATIO,
    )
    complete_days = {
        item["date"]
        for item in session_summary["recent_sessions"]
        if item.get("status") == "complete"
    }
    if len(session_summary["recent_sessions"]) < len(days):
        complete_days = {day for day in days if int(counts.get(day) or 0) >= THRESHOLD_MINUTES}
    promotion_complete_days = {
        day for day in days
        if int(promotion_counts.get(day) or 0) >= PROMOTION_THRESHOLD_MINUTES
    }

    _dep_mode = str(deployment.get("mode") or "").lower()
    all_closed = await _closed_trades(db, deployment_id, mode=_dep_mode)
    open_trades = await _open_trades(db, deployment_id, mode=_dep_mode)
    eligible: List[Dict[str, Any]] = []
    excluded_incomplete = 0
    excluded_without_pnl = 0
    for trade in all_closed:
        if _float_or_none(trade.get("realized_pnl")) is None:
            excluded_without_pnl += 1
            continue
        trade_day = _trade_session_date(trade)
        if trade_day in complete_days:
            eligible.append(trade)
        else:
            excluded_incomplete += 1

    metrics = _trade_metrics(eligible)
    complete_count = int(session_summary["complete_session_count"])
    visible = complete_count >= MIN_COMPLETE_SESSIONS_FOR_LIBRARY
    # Pre-registered promotion policy (separate from the permissive 10-session
    # visibility badge). Require >=95% of the 09:15-15:30 market window; include
    # zero-trade complete days so silence is not discarded from the record.
    promotion_eligible = [
        trade for trade in all_closed
        if _trade_session_date(trade) in promotion_complete_days
        and _float_or_none(trade.get("realized_pnl")) is not None
    ]
    daily_pnl = {day: 0.0 for day in sorted(promotion_complete_days)}
    valid_option_trades = 0
    execution_surface_total = 0.0
    for trade in promotion_eligible:
        day = _trade_session_date(trade)
        execution_pnl = _float_or_none(trade.get("execution_realized_pnl"))
        policy_pnl = _promotion_trade_pnl(trade, is_live=(_dep_mode == "live"))
        if day in daily_pnl:
            daily_pnl[day] += policy_pnl
        if bool((trade.get("execution_evidence") or {}).get(
                "point_in_time_surface_complete")) and execution_pnl is not None:
            valid_option_trades += 1
            execution_surface_total += execution_pnl
    option_entry_surface_misses = await _option_entry_surface_miss_count(
        db, deployment_id, promotion_complete_days)
    option_surface_decisions = len(promotion_eligible) + option_entry_surface_misses
    option_coverage = (
        valid_option_trades / option_surface_decisions
        if option_surface_decisions else 0.0
    )
    allow_overnight = bool((deployment.get("risk") or {}).get("allow_overnight"))
    eod_violations = 0 if allow_overnight else sum(
        1 for trade in open_trades
        if (_trade_session_date(trade) or today_iso) < today_iso
    )
    from app.paper_capital import load_account_capital_config
    # Promotion is an account contract, not merely a per-deployment budget.
    # Require the accepted fixed ₹2L account ceiling exactly; a larger account,
    # cumulative compounding, or a deployment-only limit changes the risk model
    # and therefore needs a different frozen cohort.
    account_capital = await load_account_capital_config(db)
    qualifying_capital_trades = sum(
        1 for trade in promotion_eligible
        if _trade_has_qualifying_account_capital(trade)
    )
    capital_evidence_coverage = (
        qualifying_capital_trades / len(promotion_eligible)
        if promotion_eligible else 0.0
    )
    capital_enforced = bool(
        _qualifying_account_capital(account_capital)
        and promotion_eligible
        and qualifying_capital_trades == len(promotion_eligible)
    )
    risk = deployment.get("risk") or {}
    configured_lots = int(
        ((risk.get("sizing") or {}).get("lots"))
        or risk.get("default_lots")
        or ((risk.get("live") or {}).get("lots"))
        or 1
    )
    observed_lots: Set[int] = set()
    for trade in promotion_eligible:
        try:
            observed_lots.add(int(trade.get("lots") or 0))
        except (TypeError, ValueError):
            observed_lots.add(0)
    lots = _policy_lots(configured_lots, observed_lots)
    from app.forward_validation import evaluate_forward_promotion
    from app.strategy_deployments import compute_forward_config_hash
    stored_forward_hash = str(deployment.get("forward_config_hash") or "")
    current_forward_hash = compute_forward_config_hash(deployment)
    frozen_forward_hash = (
        stored_forward_hash
        if stored_forward_hash and stored_forward_hash == current_forward_hash
        else ""
    )
    ordered_days = sorted(daily_pnl)
    promotion = evaluate_forward_promotion(
        daily_pnl=[daily_pnl[day] for day in ordered_days],
        complete_sessions=int(promotion_session_summary["complete_session_count"]),
        closed_trades=len(promotion_eligible),
        option_coverage=option_coverage,
        eod_violation_count=eod_violations,
        capital_enforced=capital_enforced,
        config_hash=frozen_forward_hash,
        lots=lots,
        session_dates=ordered_days,
    )
    return {
        "deployment_id": deployment_id,
        "deployment_name": deployment.get("name") or deployment_id,
        "strategy_id": deployment.get("strategy_id"),
        "instrument": instrument,
        "mode": deployment.get("mode"),
        "status": deployment.get("status"),
        "created_at": deployment.get("created_at"),
        "session_completeness": session_summary,
        "promotion_session_completeness": promotion_session_summary,
        **metrics,
        "closed_trade_count": len(all_closed),
        "excluded_incomplete_session_trade_count": excluded_incomplete,
        "excluded_no_pnl_trade_count": excluded_without_pnl,
        "open_trade_count": len(open_trades),
        "execution_surface_trade_count": valid_option_trades,
        "option_entry_surface_miss_count": option_entry_surface_misses,
        "option_surface_decision_count": option_surface_decisions,
        "promotion_closed_trade_count": len(promotion_eligible),
        "promotion_observed_lots": sorted(observed_lots),
        "capital_evidence_trade_count": qualifying_capital_trades,
        "capital_evidence_coverage": round(capital_evidence_coverage, 4),
        "execution_surface_total_pnl": round(execution_surface_total, 2),
        "forward_config_hash": stored_forward_hash or None,
        "forward_config_matches": bool(frozen_forward_hash),
        "forward_validation": promotion,
        "library_gate": {
            "visible": visible,
            "min_complete_sessions": MIN_COMPLETE_SESSIONS_FOR_LIBRARY,
            "reason": "ok" if visible else "needs_10_complete_sessions",
        },
    }


def build_arm_advisories(forward: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """NON-BLOCKING advisories for the live-arm dialog, derived from a deployment's
    forward (paper) metrics. The arm route has NO performance gate — arming consulted
    zero evidence (audit S19) — so surface the record and flag the cases that most
    warrant a second look: non-positive forward P&L, too few complete sessions, or no
    forward trades at all. Advisory only: arming still succeeds. Pure + host-testable."""
    if not forward:
        return [{"severity": "warning", "id": "no_forward_evidence",
                 "message": "No forward (paper) record for this deployment yet — "
                            "arming live with zero validated sessions."}]
    adv: List[Dict[str, str]] = []
    complete = int(((forward.get("session_completeness") or {}).get("complete_session_count")) or 0)
    min_sessions = int(((forward.get("library_gate") or {}).get("min_complete_sessions"))
                       or MIN_COMPLETE_SESSIONS_FOR_LIBRARY)
    trades = int(forward.get("trade_count") or 0)
    total_pnl = forward.get("total_pnl")
    wr = forward.get("win_rate")
    if total_pnl is not None and float(total_pnl) <= 0 and trades > 0:
        adv.append({"severity": "danger", "id": "nonpositive_forward_pnl",
                    "message": f"Forward P&L is ₹{float(total_pnl):,.0f} (≤ 0) over "
                               f"{trades} trade(s), win-rate {wr}% — the paper record "
                               f"is not profitable."})
    if complete < min_sessions:
        adv.append({"severity": "warning", "id": "thin_sessions",
                    "message": f"Only {complete} complete forward session(s) "
                               f"(< {min_sessions}) — the forward edge is not yet "
                               f"established."})
    if trades == 0:
        adv.append({"severity": "warning", "id": "no_forward_trades",
                    "message": "Zero closed forward trades — nothing has validated "
                               "the live path yet."})
    return adv


PREMIUM_EDGE_VERDICT_MESSAGE = (
    "This strategy family FAILED its pre-registered edge gate (validation-best lost "
    "-Rs153.8k on the untouched 2026 holdout at 1%/side friction, worse than the "
    "untuned baseline) - see docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md; multi-leg "
    "execution is a capability build, not a validated edge"
)


def premium_edge_verdict_advisory(
    strategy_id: Optional[str], merged_params: Optional[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """NON-BLOCKING advisory (Phase 5B B8) for premium_momentum deployments that have
    opted into multi-leg live/paper execution — `leg_mode == "both"` or
    `lazy_enabled` truthy. The strategy family's pre-registered edge hunt CLOSED with
    the gate FAILED (docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md): the validation-best
    config lost money on the untouched 2026 holdout, worse than the untuned baseline.
    Multi-leg execution is a capability build, not a validated edge — surface that
    honestly wherever the operator is deciding whether to arm real money. Advisory
    only: never blocks arming/creation. Pure + host-testable."""
    if strategy_id != "premium_momentum" or not merged_params:
        return None
    leg_mode = str(merged_params.get("leg_mode") or "first_to_trigger").lower()
    lazy_enabled = bool(merged_params.get("lazy_enabled"))
    if leg_mode != "both" and not lazy_enabled:
        return None
    return {
        "severity": "warning",
        "id": "premium_edge_verdict",
        "message": PREMIUM_EDGE_VERDICT_MESSAGE,
    }


async def compute_forward_metrics_for_deployments(
    db: Any,
    deployments: Iterable[Dict[str, Any]],
    *,
    today: Optional[str] = None,
) -> List[Dict[str, Any]]:
    items = []
    for deployment in deployments:
        items.append(await compute_forward_metrics_for_deployment(db, deployment, today=today))
    return items
