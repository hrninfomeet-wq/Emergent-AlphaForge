"""Forward-testing metrics for strategy deployments.

Metrics are intentionally gated by session completeness so intermittent local-PC
runtime does not make a deployment look better or worse than it really was.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from app.nse_calendar import trading_days_in_range


IST = timezone(timedelta(hours=5, minutes=30))
SESSION_START_IST = time(10, 0)
SESSION_END_IST = time(15, 0)
EXPECTED_SESSION_MINUTES = 300
COMPLETE_SESSION_RATIO = 0.70
THRESHOLD_MINUTES = int(EXPECTED_SESSION_MINUTES * COMPLETE_SESSION_RATIO)
MIN_COMPLETE_SESSIONS_FOR_LIBRARY = 10


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


async def _session_counts(
    db: Any,
    *,
    instrument: str,
    session_days: Iterable[str],
) -> Dict[str, int]:
    days = list(session_days)
    if not days:
        return {}
    start_ts = _ist_ms(days[0], SESSION_START_IST)
    end_ts = _ist_ms(days[-1], SESSION_END_IST)
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
        if not (SESSION_START_IST <= ist_dt.time() < SESSION_END_IST):
            continue
        minute_keys[day].add(ist_dt.hour * 60 + ist_dt.minute)
    return {day: len(values) for day, values in minute_keys.items()}


def _summarize_sessions(days: List[str], counts: Dict[str, int]) -> Dict[str, Any]:
    sessions: List[Dict[str, Any]] = []
    complete = 0
    partial = 0
    missing = 0
    for day in days:
        stored = int(counts.get(day) or 0)
        ratio = round(stored / EXPECTED_SESSION_MINUTES, 4)
        is_complete = stored >= THRESHOLD_MINUTES
        if is_complete:
            complete += 1
        elif stored > 0:
            partial += 1
        else:
            missing += 1
        sessions.append({
            "date": day,
            "stored_minutes": stored,
            "expected_minutes": EXPECTED_SESSION_MINUTES,
            "completeness": ratio,
            "status": "complete" if is_complete else ("partial" if stored > 0 else "missing"),
        })

    total_expected = len(days) * EXPECTED_SESSION_MINUTES
    total_stored = sum(int(counts.get(day) or 0) for day in days)
    return {
        "window_start_ist": SESSION_START_IST.strftime("%H:%M"),
        "window_end_ist": SESSION_END_IST.strftime("%H:%M"),
        "expected_minutes_per_session": EXPECTED_SESSION_MINUTES,
        "complete_threshold_ratio": COMPLETE_SESSION_RATIO,
        "threshold_minutes": THRESHOLD_MINUTES,
        "expected_session_count": len(days),
        "complete_session_count": complete,
        "partial_session_count": partial,
        "missing_session_count": missing,
        "overall_completeness": round(total_stored / total_expected, 4) if total_expected else 0.0,
        "recent_sessions": sessions[-20:],
    }


async def _closed_trades(db: Any, deployment_id: str) -> List[Dict[str, Any]]:
    cursor = db.paper_trades.find(
        {"deployment_id": deployment_id, "status": "CLOSED"},
        {"_id": 0},
    ).sort("closed_at", 1)
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
    complete_days = {
        item["date"]
        for item in session_summary["recent_sessions"]
        if item.get("status") == "complete"
    }
    if len(session_summary["recent_sessions"]) < len(days):
        complete_days = {day for day in days if int(counts.get(day) or 0) >= THRESHOLD_MINUTES}

    all_closed = await _closed_trades(db, deployment_id)
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
    return {
        "deployment_id": deployment_id,
        "deployment_name": deployment.get("name") or deployment_id,
        "strategy_id": deployment.get("strategy_id"),
        "instrument": instrument,
        "mode": deployment.get("mode"),
        "status": deployment.get("status"),
        "created_at": deployment.get("created_at"),
        "session_completeness": session_summary,
        **metrics,
        "closed_trade_count": len(all_closed),
        "excluded_incomplete_session_trade_count": excluded_incomplete,
        "excluded_no_pnl_trade_count": excluded_without_pnl,
        "library_gate": {
            "visible": visible,
            "min_complete_sessions": MIN_COMPLETE_SESSIONS_FOR_LIBRARY,
            "reason": "ok" if visible else "needs_10_complete_sessions",
        },
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
