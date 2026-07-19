"""Per-deployment caps governor for live trading.

Governs new live-trade entries for a deployment by checking three caps
configured under ``deployment["risk"]["live"]``:

  - ``max_concurrent``    — hard cap on open live trades at any moment
  - ``max_lots_per_day``  — rolling daily lots limit (IST calendar day)
  - ``daily_loss_cap``    — positive ₹ magnitude; pause when realized +
                             open-unrealized loss today hits the threshold

Returns ``{"allow": bool, "reason": str, "pause": bool}``.

Precedence (first match wins):
  1. daily_loss_cap  → allow=False, pause=True
  2. max_lots_per_day → allow=False, pause=False
  3. max_concurrent  → allow=False, pause=False
  4. else            → allow=True,  reason="ok", pause=False

If none of the three caps is configured, the DB is never queried.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.deployment_kill_switch import (
    IST,
    _float,
    _float_or_none,
    _int_or_none,
    _ist_date,
    daily_realized_summary,
)


def _live_caps_configured(live: Dict[str, Any]) -> bool:
    """Return True if at least one live cap is set to an actionable value."""
    if not live:
        return False
    return bool(
        (_int_or_none(live.get("max_concurrent")) or 0) > 0
        or (_int_or_none(live.get("max_lots_per_day")) or 0) > 0
        or (_float_or_none(live.get("daily_loss_cap")) or 0.0) > 0.0
    )


def _entered_today(row: Dict[str, Any], today: str) -> bool:
    """True when the row's created_at falls on *today* (IST)."""
    return _ist_date(row.get("created_at")) == today


def _open_unrealized_today(rows: List[Dict[str, Any]], today: str) -> float:
    """Sum of unrealized_pnl for OPEN trades entered today (IST)."""
    total = 0.0
    for row in rows:
        if str(row.get("status") or "").upper() == "OPEN" and _entered_today(row, today):
            total += _float(row.get("unrealized_pnl"))
    return total


async def check_live_caps(
    db: Any,
    deployment: Dict[str, Any],
    *,
    capped_lots: int,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Check live caps for *deployment* before opening a new trade of *capped_lots*.

    Args:
        db:          Mongo-like DB object; must expose ``db.live_trades.find(...).to_list(length=None)``.
        deployment:  Deployment document; caps are read from ``deployment["risk"]["live"]``.
        capped_lots: Lots the incoming signal wants to trade.
        now_utc:     UTC datetime for the IST date (defaults to ``datetime.now(timezone.utc)``).

    Returns:
        ``{"allow": bool, "reason": str, "pause": bool}``
    """
    _allow = {"allow": True, "reason": "ok", "pause": False}
    risk = dict(deployment.get("risk") or {})
    live = dict(risk.get("live") or {})

    # Fast path: no caps configured → skip DB entirely.
    #
    # FAIL-CLOSED FOR LIVE MODE. The allow-all fast path is only safe for a
    # deployment that cannot reach the real-order path. Once `mode == "live"` is
    # itself the authorization (the arm ceremony that used to guarantee caps were
    # written is gone), a live deployment with no caps would otherwise trade
    # UNBOUNDED — no lot ceiling, no concurrency ceiling, no daily loss stop.
    # /live/enable requires the caps, so reaching here means the doc was crafted or
    # migrated around that route: refuse rather than trade without limits.
    if not _live_caps_configured(live):
        if str(deployment.get("mode") or "").strip().lower() == "live":
            return {"allow": False, "reason": "live_caps_missing", "pause": True}
        return _allow

    # Resolve IST today
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    today: str = now_utc.astimezone(IST).date().isoformat()

    deployment_id = str(deployment.get("id") or "")
    rows: List[Dict[str, Any]] = await db.live_trades.find(
        {"deployment_id": deployment_id}
    ).to_list(length=None)

    # ------------------------------------------------------------------
    # 1. daily_loss_cap (pause=True on breach)
    # ------------------------------------------------------------------
    loss_cap = _float_or_none(live.get("daily_loss_cap"))
    if loss_cap is not None and loss_cap > 0:
        # daily_realized_summary filters rows by closed_at date; it treats all
        # rows as "closed trades" — rows without a closed_at are simply skipped.
        realized_today = daily_realized_summary(rows, today)["net"]
        open_unrealized_today = _open_unrealized_today(rows, today)
        if realized_today + open_unrealized_today <= -abs(loss_cap):
            return {"allow": False, "reason": "daily_loss_cap", "pause": True}

    # ------------------------------------------------------------------
    # 2. max_lots_per_day
    # ------------------------------------------------------------------
    max_lots = _int_or_none(live.get("max_lots_per_day"))
    if max_lots is not None and max_lots > 0:
        lots_today = sum(
            int(_float(row.get("lots")))
            for row in rows
            if _entered_today(row, today)
        )
        if lots_today + capped_lots > max_lots:
            return {"allow": False, "reason": "max_lots_per_day", "pause": False}

    # ------------------------------------------------------------------
    # 3. max_concurrent
    # ------------------------------------------------------------------
    max_conc = _int_or_none(live.get("max_concurrent"))
    if max_conc is not None and max_conc > 0:
        open_n = sum(
            1 for row in rows
            if str(row.get("status") or "").upper() == "OPEN"
        )
        if open_n >= max_conc:
            return {"allow": False, "reason": "max_concurrent", "pause": False}

    return _allow
