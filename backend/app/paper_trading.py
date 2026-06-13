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


def _iso_to_ms(value: Any) -> int:
    """Best-effort ISO-timestamp → epoch ms (for the friction model's
    expiry-tail check). Falls back to 'now' when the value is missing/unparseable."""
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            pass
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# Manual mark/close sanity bounds: catch a fat-fingered SPOT/index level typed
# where an option PREMIUM is expected (e.g. 23950 instead of ~150). Premiums move
# sharply (theta/gamma), so the guard trips only on the HIGH side and only when
# BOTH a large multiple AND a large absolute gap vs the last known premium are
# exceeded — small premiums (a 0.5→8 move) are never over-guarded, while a spot
# value against any realistic premium is caught. The operator can still override.
PREMIUM_SANITY_MAX_MULTIPLE = 20.0
PREMIUM_SANITY_ABS_BAND = 500.0


def premium_sanity_error(trade: Dict[str, Any], price: Any) -> Optional[str]:
    """Human message when `price` is an implausible option premium for this trade,
    else None. Pure + tested; the manual mark/close routes call it and let the
    operator override deliberately (the conscious-choice rule)."""
    p = _float(price, -1.0)
    if p <= 0:
        return "Price must be a positive option premium (₹ > 0)."
    reference = _float(trade.get("last_price")) or _float(trade.get("entry_price"))
    if reference and reference > 0:
        if p > reference * PREMIUM_SANITY_MAX_MULTIPLE and p > reference + PREMIUM_SANITY_ABS_BAND:
            return (
                f"₹{p:g} looks like a spot/index level, not an option premium "
                f"(last known ≈ ₹{reference:g}). Re-enter the premium, or override to book it anyway."
            )
    return None


def paper_trade_from_signal(
    signal: Dict[str, Any],
    *,
    lots: int = 1,
    entry_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
    at: Optional[str] = None,
    raw_entry_price: Optional[float] = None,
    friction: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a paper-trade doc. `entry_price` is the booked fill (already
    friction-adjusted by the caller when applicable); `raw_entry_price` is the
    unslipped premium kept for gross-vs-net analysis. `friction`, when provided,
    is stored on the trade so `close_trade` applies the SAME exit slippage +
    charges the entry was opened with (so forward P&L mirrors the backtest)."""
    option_contract = signal.get("option_contract") or {}
    lot_size = max(1, _int(option_contract.get("lot_size"), 1))
    lots = max(1, _int(lots, 1))
    quantity = lots * lot_size
    fill_price = _float(entry_price, _float(signal.get("entry_price"), 0.0))
    raw_entry = _float(raw_entry_price, fill_price) if raw_entry_price not in (None, "") else fill_price
    stop = _float(stop_price) if stop_price not in (None, "") else None
    target = _float(target_price) if target_price not in (None, "") else None
    timestamp = at or _now_iso()
    trade = {
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
        "raw_entry_price": round(raw_entry, 4),
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
    if friction:
        trade["friction"] = friction
    return trade


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
    raw_exit = _float(exit_price)            # the tick/mark that triggered the close
    quantity = _int(updated.get("quantity"))
    entry = _float(updated.get("entry_price"))
    raw_entry = _float(updated.get("raw_entry_price"), entry)
    timestamp = at or _now_iso()

    # Friction parity (app.live_friction): when the trade was opened with a
    # friction config, the exit is slipped (SELL) and round-trip charges are
    # subtracted so realized P&L mirrors the backtest. Absent the block, the
    # close stays gross — realized = (raw_exit - entry) * quantity — exactly as
    # before, so deployments/tests without friction are unchanged.
    from app.live_friction import FrictionConfig, close_economics
    friction = FrictionConfig.from_dict(updated.get("friction"))
    econ = close_economics(
        raw_exit_premium=raw_exit,
        entry_price=entry,
        raw_entry_price=raw_entry,
        quantity=quantity,
        friction=friction,
        ts_ms=_iso_to_ms(timestamp),
    )
    fill_price = _float(econ["exit_fill_price"], raw_exit)
    realized = econ["realized_pnl"]
    updated.update({
        "status": "CLOSED",
        "exit_price": fill_price,
        "raw_exit_price": raw_exit,
        "exit_value": round(fill_price * quantity, 2),
        "exit_reason": reason,
        "realized_pnl": realized,
        "gross_realized_pnl": econ["gross_realized_pnl"],
        "friction_cost": econ["friction_cost"],
        "total_charges": econ["total_charges"],
        "exit_slippage_pts": econ["exit_slippage_pts"],
        "exit_spread_pts": econ["exit_spread_pts"],
        "unrealized_pnl": 0.0,
        "last_price": raw_exit,
        "closed_at": timestamp,
        "updated_at": timestamp,
    })
    if econ.get("charges"):
        updated["charges"] = econ["charges"]
    events = list(updated.get("events") or [])
    events.append({"type": "CLOSE", "at": timestamp, "price": fill_price,
                   "realized_pnl": realized, "reason": reason})
    updated["events"] = events
    return updated
