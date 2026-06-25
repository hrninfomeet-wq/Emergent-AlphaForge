"""Per-deployment caps for LIVE (real-order) deployments — Section C.

A live deployment carries optional caps under `deployment.risk.live_caps`:

  - max_concurrent     -> BLOCK a new entry when this many live_trades are
                          already OPEN for the deployment. Soft (self-clears as
                          positions close); never pauses.
  - max_lots_per_day   -> BLOCK a new entry when today's (IST, by ENTRY date)
                          cumulative lots — already-placed PLUS the lots about to
                          be placed (`capped_lots`) — would exceed the limit.
                          Soft; never pauses.
  - daily_loss_cap     -> PAUSE the deployment when today's (IST) net P&L
                          (realized of trades closed today + unrealized of trades
                          open today) drops to/below the negative ₹ cap.

Unlike the paper kill switches (% of capital), the live daily loss cap is an
ABSOLUTE rupee figure, and it counts OPEN mark-to-market P&L too, so a single
running-away open position can trip the breaker before it is even closed.

`check_live_caps` is a pure, synchronously-testable decision over a DB handle
exposing a `live_trades` collection with a synchronous `find(query) -> rows`
(the caller adapts the async Mongo cursor; the FakeDB in the tests is sync).
P&L math is NOT duplicated here — the realized side reuses
`deployment_kill_switch.daily_realized_summary` and every field read reuses the
shared `_float` / `_ist_date` coercers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.deployment_kill_switch import (
    IST,
    _float,
    _float_or_none,
    _int_or_none,
    _ist_date,
    daily_realized_summary,
)


def _open_today_unrealized(rows: List[Dict[str, Any]], today_ist: str) -> float:
    """Sum mark-to-market `unrealized_pnl` of trades OPEN and entered today (IST).

    Reuses the shared `_ist_date` / `_float` coercers — no new pnl math. A trade's
    entry date is its `created_at` (the field the trade builder stamps)."""
    total = 0.0
    for trade in rows:
        if str(trade.get("status") or "").upper() != "OPEN":
            continue
        if _ist_date(trade.get("created_at")) != today_ist:
            continue
        total += _float(trade.get("unrealized_pnl"))
    return round(total, 2)


def _lots_today(rows: List[Dict[str, Any]], today_ist: str) -> int:
    """Sum lots of trades entered today (IST), OPEN or CLOSED."""
    total = 0
    for trade in rows:
        if _ist_date(trade.get("created_at")) != today_ist:
            continue
        lots = _int_or_none(trade.get("lots"))
        if lots and lots > 0:
            total += lots
    return total


def check_live_caps(
    db: Any,
    deployment: Dict[str, Any],
    *,
    capped_lots: int,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Decide whether a new live entry of `capped_lots` lots is allowed.

    Returns {"allow": bool, "reason": str | None, "pause": bool}:
      - allow=False, pause=False  -> a soft cap blocks THIS entry (max_concurrent
                                     or max_lots_per_day); the deployment keeps
                                     running and may enter again once it clears.
      - allow=False, pause=True   -> the daily_loss_cap tripped; the caller must
                                     PAUSE the deployment (hard circuit-breaker).
      - allow=True               -> every configured cap passed.

    Caps are evaluated cheapest-first; the daily_loss_cap (pause) takes headline
    precedence over the soft blocks when several would trip.
    """
    risk = dict(deployment.get("risk") or {})
    caps = dict(risk.get("live_caps") or {})
    dep_id = str(deployment.get("id") or "")
    today = _ist_date(_now_iso(now_utc)) or datetime.now(IST).date().isoformat()

    add_lots = max(0, int(capped_lots or 0))

    max_concurrent = _int_or_none(caps.get("max_concurrent"))
    max_lots_per_day = _int_or_none(caps.get("max_lots_per_day"))
    daily_loss_cap = _float_or_none(caps.get("daily_loss_cap"))

    # Nothing configured -> allow without touching the DB.
    if not (
        (max_concurrent and max_concurrent > 0)
        or (max_lots_per_day and max_lots_per_day > 0)
        or (daily_loss_cap is not None and daily_loss_cap < 0)
    ):
        return {"allow": True, "reason": None, "pause": False}

    rows = list(db.live_trades.find({"deployment_id": dep_id}))

    # --- daily_loss_cap (PAUSE) — evaluated first so it wins the headline -----
    if daily_loss_cap is not None and daily_loss_cap < 0:
        realized = daily_realized_summary(rows, today)["net"]
        unrealized = _open_today_unrealized(rows, today)
        day_pnl = round(realized + unrealized, 2)
        if day_pnl <= daily_loss_cap:
            return {
                "allow": False,
                "pause": True,
                "reason": (
                    f"live_cap:daily_loss_cap (today ₹{day_pnl} <= ₹{daily_loss_cap}; "
                    f"realized ₹{realized}, open ₹{unrealized})"
                ),
            }

    # --- max_concurrent (soft BLOCK) ------------------------------------------
    if max_concurrent and max_concurrent > 0:
        open_count = sum(1 for r in rows if str(r.get("status") or "").upper() == "OPEN")
        if open_count >= max_concurrent:
            return {
                "allow": False,
                "pause": False,
                "reason": f"live_cap:max_concurrent ({open_count} open >= {max_concurrent})",
            }

    # --- max_lots_per_day (soft BLOCK) ----------------------------------------
    if max_lots_per_day and max_lots_per_day > 0:
        used = _lots_today(rows, today)
        if used + add_lots > max_lots_per_day:
            return {
                "allow": False,
                "pause": False,
                "reason": (
                    f"live_cap:max_lots_per_day ({used} today + {add_lots} new "
                    f"> {max_lots_per_day})"
                ),
            }

    return {"allow": True, "reason": None, "pause": False}


def _now_iso(now_utc: Optional[datetime]) -> Optional[str]:
    if now_utc is None:
        return None
    return now_utc.isoformat()
