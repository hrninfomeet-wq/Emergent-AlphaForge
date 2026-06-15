"""Per-deployment kill switches (Phase 4b Slice 12).

Risk circuit-breakers governing a PAPER deployment, configured under
`deployment.risk`:

  - max_consecutive_losses  -> PAUSE the deployment when the trailing run of
                               losing closed paper trades reaches the limit.
  - daily_loss_cutoff_pct   -> PAUSE the deployment when today's net realized
                               paper P&L, as a percentage of capital deployed
                               today, drops to/below the (negative) cutoff.
  - max_open_paper_trades   -> BLOCK new signals while this many paper trades
                               are already OPEN. Self-clears when trades close;
                               does NOT pause the deployment.

Pause switches are hard circuit-breakers: the deployment stops generating
signals until the user manually resumes it. The block switch is soft.

Only paper deployments are governed (shadow/recommendation create no paper
trades, so there is no realized P&L to act on).

This module keeps the decision logic pure and unit-testable; an async wrapper
loads the trade data from Mongo.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

IST = timezone(timedelta(hours=5, minutes=30))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _ist_date(value: Any) -> Optional[str]:
    """IST date (YYYY-MM-DD) from an ISO timestamp string."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).date().isoformat()


def trailing_consecutive_losses(closed_trades: List[Dict[str, Any]]) -> int:
    """Count the trailing run of losing closed trades (most recent first).

    `closed_trades` must be sorted ascending by `closed_at`. A trade is a loss
    when `realized_pnl < 0`. A breakeven (0) or winning trade breaks the streak.
    """
    streak = 0
    for trade in reversed(closed_trades):
        pnl = _float(trade.get("realized_pnl"))
        if pnl < 0:
            streak += 1
        else:
            break
    return streak


def daily_realized_summary(closed_trades: List[Dict[str, Any]], today_ist: str) -> Dict[str, float]:
    """Net realized P&L and gross entry capital for trades closed today (IST)."""
    net = 0.0
    capital = 0.0
    count = 0
    for trade in closed_trades:
        if _ist_date(trade.get("closed_at")) != today_ist:
            continue
        net += _float(trade.get("realized_pnl"))
        capital += abs(_float(trade.get("entry_value")))
        count += 1
    pct = round((net / capital) * 100, 4) if capital else 0.0
    return {"net": round(net, 2), "capital": round(capital, 2), "pct": pct, "count": count}


def evaluate_kill_switches(
    *,
    risk: Dict[str, Any],
    consecutive_losses: int,
    daily_pct: float,
    daily_net: float,
    open_trade_count: int,
) -> Dict[str, Any]:
    """Pure decision from precomputed inputs.

    Returns:
      {
        "pause": bool,
        "pause_reason": str | None,
        "pause_switch": str | None,
        "block_reason": str | None,     # max_open_paper_trades (soft block)
        "triggered": [ {switch, action, detail}, ... ],
      }
    """
    risk = risk or {}
    triggered: List[Dict[str, Any]] = []
    pause_reason: Optional[str] = None
    pause_switch: Optional[str] = None
    block_reason: Optional[str] = None

    max_losses = _int_or_none(risk.get("max_consecutive_losses"))
    if max_losses and max_losses > 0 and consecutive_losses >= max_losses:
        pause_switch = "max_consecutive_losses"
        pause_reason = (
            f"kill_switch:max_consecutive_losses ({consecutive_losses} >= {max_losses})"
        )
        triggered.append({"switch": pause_switch, "action": "pause", "detail": pause_reason})

    cutoff = _float_or_none(risk.get("daily_loss_cutoff_pct"))
    # Cutoff is a negative percent (e.g. -3.0). Only meaningful when negative.
    if cutoff is not None and cutoff < 0 and daily_pct <= cutoff:
        reason = (
            f"kill_switch:daily_loss_cutoff_pct (today {daily_pct}% <= {cutoff}%, "
            f"net {daily_net})"
        )
        triggered.append({"switch": "daily_loss_cutoff_pct", "action": "pause", "detail": reason})
        # First pause switch wins as the headline; record both in triggered[].
        if pause_switch is None:
            pause_switch = "daily_loss_cutoff_pct"
            pause_reason = reason

    max_open = _int_or_none(risk.get("max_open_paper_trades"))
    if max_open and max_open > 0 and open_trade_count >= max_open:
        block_reason = (
            f"kill_switch:max_open_paper_trades ({open_trade_count} open >= {max_open})"
        )
        triggered.append({"switch": "max_open_paper_trades", "action": "block", "detail": block_reason})

    return {
        "pause": pause_switch is not None,
        "pause_reason": pause_reason,
        "pause_switch": pause_switch,
        "block_reason": block_reason,
        "triggered": triggered,
    }


def kill_switches_configured(risk: Dict[str, Any]) -> bool:
    """True if any kill switch is set, so callers can skip DB work when none are."""
    risk = risk or {}
    return bool(
        (_int_or_none(risk.get("max_consecutive_losses")) or 0) > 0
        or ((_float_or_none(risk.get("daily_loss_cutoff_pct")) or 0) < 0)
        or (_int_or_none(risk.get("max_open_paper_trades")) or 0) > 0
    )


async def check_deployment_kill_switches(
    db: Any,
    deployment: Dict[str, Any],
    *,
    today_ist: Optional[str] = None,
) -> Dict[str, Any]:
    """Async wrapper: load this deployment's paper trades and evaluate the switches.

    Returns the same shape as `evaluate_kill_switches`, plus `"inputs"` for audit.
    Returns an all-clear decision (no DB work) when no switch is configured or the
    deployment is not in paper mode.
    """
    risk = dict(deployment.get("risk") or {})
    clear = {"pause": False, "pause_reason": None, "pause_switch": None, "block_reason": None, "triggered": []}

    if str(deployment.get("mode") or "").lower() != "paper":
        return clear
    if not kill_switches_configured(risk):
        return clear

    deployment_id = str(deployment.get("id") or "")
    today = today_ist or datetime.now(IST).date().isoformat()

    closed = await (
        db.paper_trades
        .find({"deployment_id": deployment_id, "status": "CLOSED"}, {"_id": 0})
        .sort("closed_at", 1)
        .to_list(length=None)
    )
    open_trade_count = await db.paper_trades.count_documents(
        {"deployment_id": deployment_id, "status": "OPEN"}
    )

    consecutive = trailing_consecutive_losses(closed)
    daily = daily_realized_summary(closed, today)

    decision = evaluate_kill_switches(
        risk=risk,
        consecutive_losses=consecutive,
        daily_pct=daily["pct"],
        daily_net=daily["net"],
        open_trade_count=int(open_trade_count),
    )
    decision["inputs"] = {
        "consecutive_losses": consecutive,
        "daily_pct": daily["pct"],
        "daily_net": daily["net"],
        "daily_trade_count": daily["count"],
        "open_trade_count": int(open_trade_count),
    }
    return decision


async def check_soft_daily_governor(db, deployment, *, today_ist=None):
    """Entry-session soft governor: halt NEW entries when today's (by ENTRY date)
    realized cum-extremum trips loss/target or the entry count reaches max_trades.
    Counts OPEN+CLOSED trades entered today; accumulates realized of closed-entered-today
    trades in CLOSED_AT order (sticky extremum). Stateless (auto-resets next session).
    Blocks entries only; never pauses. Paper deployments only."""
    from app.exit_controls import DailyCapsConfig, daily_governor_decision
    risk = dict(deployment.get("risk") or {})
    caps = DailyCapsConfig.from_dict(risk.get("daily_caps"))
    clear = {"halt": False, "reason": None}
    if str(deployment.get("mode") or "").lower() != "paper" or not caps.active:
        return clear
    dep_id = str(deployment.get("id") or "")
    today = today_ist or datetime.now(IST).date().isoformat()
    rows = await (
        db.paper_trades.find({"deployment_id": dep_id}, {"_id": 0}).sort("created_at", 1).to_list(length=None)
    )
    entered_today = [t for t in rows if _ist_date(t.get("created_at")) == today]
    entry_count = len(entered_today)
    closed_today = sorted(
        [t for t in entered_today if str(t.get("status") or "").upper() == "CLOSED"],
        key=lambda t: str(t.get("closed_at") or ""))
    cum = cmin = cmax = 0.0
    for t in closed_today:
        cum += _float(t.get("realized_pnl"))
        cmin = min(cmin, cum)
        cmax = max(cmax, cum)
    return daily_governor_decision(realized_cum_min=cmin, realized_cum_max=cmax,
                                   entry_count=entry_count, cfg=caps)
