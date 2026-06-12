"""Offline-safe paper trading helpers for signal replay and forward testing."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def paper_trade_from_signal(
    signal: Dict[str, Any],
    *,
    lots: int = 1,
    entry_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
    at: Optional[str] = None,
) -> Dict[str, Any]:
    option_contract = signal.get("option_contract") or {}
    lot_size = max(1, _int(option_contract.get("lot_size"), 1))
    lots = max(1, _int(lots, 1))
    quantity = lots * lot_size
    fill_price = _float(entry_price, _float(signal.get("entry_price"), 0.0))
    stop = _float(stop_price) if stop_price not in (None, "") else None
    target = _float(target_price) if target_price not in (None, "") else None
    timestamp = at or _now_iso()
    return {
        "id": str(uuid.uuid4()),
        "signal_id": signal.get("id"),
        "instrument": signal.get("instrument"),
        "direction": signal.get("direction"),
        "strategy_id": signal.get("strategy_id"),
        "instrument_key": option_contract.get("instrument_key") or "",
        "trading_symbol": option_contract.get("trading_symbol") or "",
        "lots": lots,
        "lot_size": lot_size,
        "quantity": quantity,
        "entry_price": fill_price,
        "entry_value": round(fill_price * quantity, 2),
        "last_price": fill_price,
        "risk": {
            "stop_price": stop,
            "target_price": target,
            "auto_close_on_risk": True,
        },
        "unrealized_pnl": 0.0,
        "realized_pnl": None,
        "status": "OPEN",
        "created_at": timestamp,
        "updated_at": timestamp,
        "events": [{
            "type": "OPEN",
            "at": timestamp,
            "price": fill_price,
            "reason": "deployed_from_signal",
        }],
        "source": "paper",
    }


def _mark_open_trade(trade: Dict[str, Any], *, last_price: Any, at: Optional[str] = None) -> Dict[str, Any]:
    if str(trade.get("status") or "").upper() != "OPEN":
        return dict(trade)
    updated = dict(trade)
    price = _float(last_price)
    quantity = _int(updated.get("quantity"))
    entry = _float(updated.get("entry_price"))
    timestamp = at or _now_iso()
    updated["last_price"] = price
    updated["unrealized_pnl"] = round((price - entry) * quantity, 2)
    updated["updated_at"] = timestamp
    events = list(updated.get("events") or [])
    events.append({"type": "MARK", "at": timestamp, "price": price, "unrealized_pnl": updated["unrealized_pnl"]})
    updated["events"] = events
    return updated


def risk_exit_reason(trade: Dict[str, Any], last_price: Any) -> Optional[str]:
    """Premium stop/target decision for a live mark.

    Delegates to the shared execution policy (a tick is a degenerate bar
    through the backtest's `intrabar_exit`), making the live marker provably
    consistent with the sim — including STOP-FIRST when both levels are
    satisfied (the old inline check tested the target first and booked the
    lucky fill in degenerate configurations)."""
    from app.execution_policy import tick_exit_reason
    risk = trade.get("risk") or {}
    return tick_exit_reason(
        _float(last_price),
        stop=risk.get("stop_price"),
        target=risk.get("target_price"),
        is_long=True,
    )


def mark_trade_to_market(
    trade: Dict[str, Any],
    *,
    last_price: Any,
    at: Optional[str] = None,
    auto_close_on_risk: bool = False,
) -> Dict[str, Any]:
    if str(trade.get("status") or "").upper() != "OPEN":
        return dict(trade)
    updated = _mark_open_trade(trade, last_price=last_price, at=at)
    if auto_close_on_risk or (updated.get("risk") or {}).get("auto_close_on_risk"):
        reason = risk_exit_reason(updated, last_price)
        if reason:
            return close_trade(updated, exit_price=last_price, reason=reason, at=at)
    return updated


def close_trade(
    trade: Dict[str, Any],
    *,
    exit_price: Any,
    reason: str = "",
    at: Optional[str] = None,
) -> Dict[str, Any]:
    updated = dict(trade)
    price = _float(exit_price)
    quantity = _int(updated.get("quantity"))
    entry = _float(updated.get("entry_price"))
    timestamp = at or _now_iso()
    realized = round((price - entry) * quantity, 2)
    updated.update({
        "status": "CLOSED",
        "exit_price": price,
        "exit_value": round(price * quantity, 2),
        "exit_reason": reason,
        "realized_pnl": realized,
        "unrealized_pnl": 0.0,
        "last_price": price,
        "closed_at": timestamp,
        "updated_at": timestamp,
    })
    events = list(updated.get("events") or [])
    events.append({"type": "CLOSE", "at": timestamp, "price": price, "realized_pnl": realized, "reason": reason})
    updated["events"] = events
    return updated
