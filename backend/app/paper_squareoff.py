"""End-of-day square-off for open paper trades.

Per user decision (2026-05-27): all open paper trades must be force-closed at 15:00 IST,
regardless of expiry date. Signals continue to be journaled for the rest of the session;
only paper-trade exits are forced. This is a research-tool safety rule, not a market rule.

Configurable per deployment in a future slice via deployment.risk.square_off_time_ist.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.paper_trading import close_trade

log = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)
DEFAULT_SQUARE_OFF_IST = time(15, 0)


def _ist_now() -> datetime:
    return datetime.now(timezone.utc) + IST_OFFSET


def _is_market_day(ist_dt: datetime) -> bool:
    return ist_dt.weekday() < 5


def is_square_off_due(now_ist: Optional[datetime] = None, *, cutoff: time = DEFAULT_SQUARE_OFF_IST) -> bool:
    """Return True when current IST time is at-or-after the square-off cutoff on a market day."""
    ist = now_ist or _ist_now()
    if not _is_market_day(ist):
        return False
    return ist.time() >= cutoff


async def _resolve_exit_price(
    db: Any,
    trade: Dict[str, Any],
    *,
    latest_tick_lookup: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> float:
    """Pick a sensible exit price for square-off.

    Order:
      1. Latest WS tick for the trade's instrument_key (if provided)
      2. Last known mark on the trade itself (`last_price`)
      3. Original entry price (zero-PnL fallback so the close never crashes)
    """
    instrument_key = str(trade.get("instrument_key") or "")
    if instrument_key and latest_tick_lookup:
        tick = latest_tick_lookup(instrument_key)
        if tick and tick.get("last_price") not in (None, ""):
            try:
                return float(tick["last_price"])
            except (TypeError, ValueError):
                pass
    last_price = trade.get("last_price")
    if last_price not in (None, ""):
        try:
            return float(last_price)
        except (TypeError, ValueError):
            pass
    try:
        return float(trade.get("entry_price") or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def square_off_open_paper_trades(
    db: Any,
    *,
    latest_tick_lookup: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    reason: str = "auto_square_off_15_00_IST",
    now_ist: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Force-close all OPEN paper trades. Idempotent: closed trades are skipped.

    Trades belonging to deployments where `risk.allow_overnight` is True are skipped,
    so users who explicitly opted into overnight positions keep them open.

    Returns a list of summaries with id, exit_price, realized_pnl per closed trade.
    Safe to call multiple times - only OPEN trades are touched.
    """
    cursor = db.paper_trades.find({"status": "OPEN"}, {"_id": 0})
    open_trades = await cursor.to_list(length=None)
    if not open_trades:
        return []

    # Pre-resolve allow_overnight per deployment to avoid one query per trade.
    deployment_ids = {t.get("deployment_id") for t in open_trades if t.get("deployment_id")}
    overnight_allowed: Dict[str, bool] = {}
    if deployment_ids:
        deps_cursor = db.strategy_deployments.find(
            {"id": {"$in": list(deployment_ids)}},
            {"_id": 0, "id": 1, "risk": 1},
        )
        for dep in await deps_cursor.to_list(length=None):
            overnight_allowed[dep.get("id")] = bool((dep.get("risk") or {}).get("allow_overnight"))

    closed_at = (now_ist or _ist_now()).strftime("%Y-%m-%dT%H:%M:%S+05:30")
    summaries: List[Dict[str, Any]] = []
    for trade in open_trades:
        if overnight_allowed.get(trade.get("deployment_id"), False):
            summaries.append({"id": trade.get("id"), "skipped": "allow_overnight"})
            continue
        try:
            exit_price = await _resolve_exit_price(db, trade, latest_tick_lookup=latest_tick_lookup)
            updated = close_trade(trade, exit_price=exit_price, reason=reason, at=closed_at)
            await db.paper_trades.replace_one({"id": trade["id"]}, updated, upsert=False)
            summaries.append({
                "id": trade["id"],
                "instrument_key": trade.get("instrument_key"),
                "exit_price": exit_price,
                "realized_pnl": updated.get("realized_pnl"),
                "reason": reason,
            })
        except Exception as exc:
            log.exception("square-off failed for trade %s: %s", trade.get("id"), exc)
            summaries.append({"id": trade.get("id"), "error": str(exc)})
    return summaries
