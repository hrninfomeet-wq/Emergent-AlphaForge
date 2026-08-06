"""Live-trade close-loop — write realized P&L + CLOSED status back to the
``live_trades`` journal when a deployed live position is squared.

Until this module existed, ``live_trades`` docs were inserted ``status="OPEN"``
and never updated, so:
  * realized P&L was never journaled (the Signal Journal / blotter could only
    show the broker's live MTM for still-open positions);
  * ``max_concurrent`` (counts OPEN rows) over-counted forever and would
    eventually block every new entry;
  * ``daily_loss_cap`` (sums CLOSED ``realized_pnl`` for today) was blind to
    realized losses.

This closes the gap for **real** squares only — never on a dry-run
(``LIVE_GUARD_ARMED`` off) and never on a transient broker hiccup — and links by
the entry ``norenordno`` (unique per order), so a same-strike re-entry closes
exactly its own doc rather than an ambiguous tsym match.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


def should_journal_close(
    entry: Optional[Dict[str, Any]], result: Optional[Dict[str, Any]]
) -> bool:
    """True only when a REAL exit occurred for a JOURNALED (deployed) position.

    * ``source == "manual"`` → False: manual single-shot positions keep their
      state in the session store and have no ``live_trades`` doc to close.
    * the square ``result`` must be a real fill — ``squared`` truthy AND not a
      dry-run. A dry-run (``LIVE_GUARD_ARMED`` off) returns
      ``{"squared": False, "dry_run": True}`` and transmits nothing, so the
      broker position is STILL OPEN; journaling it CLOSED would be a lie. A
      failed square (``squared`` falsy) likewise leaves the position live.
    """
    if (entry or {}).get("source") == "manual":
        return False
    r = result or {}
    return bool(r.get("squared")) and not r.get("dry_run")


def _finite(v: Any) -> Optional[float]:
    """float(v) if finite, else None (guards None/''/NaN/inf)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


async def close_live_trade(
    db: Any,
    *,
    norenordno: Optional[str],
    exit_price: Optional[float],
    exit_reason: str,
    fill_price: Optional[float] = None,
    now_iso: Optional[str] = None,
) -> bool:
    """Idempotently mark the non-CLOSED ``live_trades`` doc for ``norenordno`` CLOSED.

    Returns True iff a doc was transitioned to CLOSED. Long-only option BUY, so
    ``realized_pnl = quantity * (exit_price - entry_price)`` (positive = gain).
    ``exit_price`` is the guard's last-seen broker last-price at the exit cycle —
    an exit *mark*, not a confirmed fill — so realized_pnl is an estimate.

    ``fill_price``, when finite, is the broker's **true** exit fill price (e.g.
    from the trade book during reboot reconciliation); it is used as THE exit
    price — for both the stored ``exit_price`` field and the realized_pnl
    computation — in preference to the ``exit_price`` estimate. When ``fill_price``
    is None, behaviour is exactly as before (the ``exit_price`` mark is used).

    If neither price is finite the doc is still CLOSED (with ``closed_at`` +
    ``exit_reason``) but ``realized_pnl`` is left untouched (``None``); we never
    fabricate a number. The ``status != "CLOSED"`` filter makes a repeat call a
    safe no-op.
    """
    if not norenordno or db is None:
        return False
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    flt = {"norenordno": norenordno, "status": {"$ne": "CLOSED"}}
    doc = await db.live_trades.find_one(flt)
    if not doc:
        return False
    set_fields: Dict[str, Any] = {
        "status": "CLOSED",
        "exit_reason": exit_reason,
        "closed_at": now_iso,
        "updated_at": now_iso,
    }
    # broker-true fill_price wins over the guard's exit-mark estimate.
    fp = _finite(fill_price)
    ep = fp if fp is not None else _finite(exit_price)
    if ep is not None:
        set_fields["exit_price"] = ep
        qty = _finite(doc.get("quantity"))
        # Measure from the price the account ACTUALLY PAID. `entry_price` is the
        # pre-trade REFERENCE premium used to band the limit order — an intent —
        # so P&L measured against it silently excluded all entry slippage.
        # `entry_fill_price` is the broker's own average, captured by the guard's
        # mark cycle from the position book. Falls back to the reference for any
        # trade the guard never marked, so no historical row changes meaning.
        entry_px = _finite(doc.get("entry_fill_price"))
        if entry_px is None:
            entry_px = _finite(doc.get("entry_price"))
        if qty is not None and entry_px is not None:
            # Round like every other money field here (close_economics,
            # option_backtest): an unrounded product yields -227.4999999999986.
            gross = round(qty * (ep - entry_px), 2)
            set_fields["realized_pnl"] = gross
            # What the exchange actually took. Paper and backtest both apply this
            # model; live did not, so the three were never measuring the same
            # quantity — and `routers/live_broker` already PROJECTS total_charges,
            # reading a field nothing wrote. Parity is by SHARED CODE: the same
            # `round_trip_charges` paper's close path uses, levied on the prices
            # actually transacted (STT is a turnover tax).
            #
            # `realized_pnl` deliberately stays GROSS — `close_economics` records
            # the project's decision that kill-switch / caps / analytics semantics
            # are unchanged. The day-stop sums it; making the loss cap
            # net-of-charges is an explicit operator policy call, not a side
            # effect of journalling charges.
            if int(qty) > 0:
                try:
                    from app.option_costs import CostConfig, round_trip_charges
                    _ch = round_trip_charges(
                        entry_premium=float(entry_px), exit_premium=float(ep),
                        quantity=int(qty), cfg=CostConfig(enabled=True))
                    _total = round(float(_ch["total_charges"]), 2)
                    set_fields["total_charges"] = _total
                    set_fields["charges"] = _ch
                    set_fields["net_realized_pnl"] = round(gross - _total, 2)
                except Exception as exc:      # never block the close on costing
                    log.warning("close_live_trade: charge computation failed for "
                                "%s (%s) — realized_pnl still journalled", norenordno, exc)
    res = await db.live_trades.update_one(flt, {"$set": set_fields})
    modified = getattr(res, "modified_count", None)
    if modified is None:  # FakeDB / drivers without modified_count
        modified = getattr(res, "matched_count", 0)
    return bool(modified)
