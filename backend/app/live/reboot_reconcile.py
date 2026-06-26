"""Transient-safe reboot reconciliation for the live execution path.

When the PC / backend is down and a resting OCO fires (or a position is closed
externally), the in-memory state is lost and two holes open up:

  1. the ``live_trades`` doc for that position stays ``status="OPEN"`` forever —
     so realized P&L is never journaled, ``max_concurrent`` over-counts, and the
     ``daily_loss_cap`` is blind to the realized loss;
  2. the resting OCO that fired (one leg) may leave its *other* leg dangling, or
     a still-resting OCO for an already-closed position is orphaned at the broker.

``reconcile_on_startup`` closes both, run once on boot.

CRITICAL SAFETY — the empty-book false-close hole
--------------------------------------------------
An EMPTY ``position_book`` (a broker ``Not_Ok`` / transport hiccup returns ``[]``)
must be treated as **UNKNOWN**, never "flat". If we treated empty as flat, EVERY
open position would look closed and get false-closed, and EVERY resting OCO would
get false-cancelled — catastrophically removing the broker-side backstop while the
position is in fact still live. So: a non-list / empty ``position_book`` short-
circuits the whole routine — no close, no cancel.

CRITICAL SAFETY — match the exit fill to THE entry
--------------------------------------------------
We never pick "the newest SELL by tsym": a same-strike re-entry would leave stale
fills that point at the wrong exit price. We match the OCO's ``remarks`` tag
(``oco:<entry_norenordno>``) first; only if there is NO remarks-tagged exit fill do
we fall back to a same-tsym SELL — and then ONLY when exactly one such SELL exists
(ambiguous → we close the doc but never fabricate a price).

The function NEVER raises — every phase is wrapped, and a failure in one phase
does not abort the other.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.live.close_loop import close_live_trade
from app.live.kill_switch import _parse_netqty

log = logging.getLogger(__name__)

_OCO_REMARKS_PREFIX = "oco:"


def _finite(v: Any) -> Optional[float]:
    """float(v) if finite, else None (guards None / '' / NaN / inf)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _open_tsyms(book: List[Dict[str, Any]]) -> Dict[str, bool]:
    """{ tsym: True } for every book row whose parsed netqty != 0 (still held).

    A row with netqty 0 / absent / unparseable is NOT counted as held; the caller
    treats a tsym missing from this map as flat — which is safe ONLY because the
    book was already confirmed to be a non-empty list before we got here.
    """
    held: Dict[str, bool] = {}
    for row in book:
        if not isinstance(row, dict):
            continue
        tsym = row.get("tsym")
        if not tsym:
            continue
        nq = _parse_netqty(row.get("netqty", 0))
        if nq:  # non-zero (and not None) → held
            held[str(tsym)] = True
    return held


def _parse_oco_norenordno(remarks: Any) -> Optional[str]:
    """Extract ``<norenordno>`` from a ``oco:<norenordno>`` remarks tag."""
    if not isinstance(remarks, str):
        return None
    s = remarks.strip()
    if not s.startswith(_OCO_REMARKS_PREFIX):
        return None
    tail = s[len(_OCO_REMARKS_PREFIX):].strip()
    return tail or None


def _match_exit_fill_price(
    trade_book: List[Dict[str, Any]], *, norenordno: str, tsym: str
) -> Optional[float]:
    """Find the exit fill price for THIS entry.

    1. PRIMARY — a SELL fill (``trantype == "S"``) tagged ``remarks == oco:<norenordno>``.
       This is unambiguous: the OCO was placed with exactly that remarks tag, so the
       fired leg carries it.
    2. FALLBACK — exactly ONE same-tsym SELL fill (no remarks link survived). More than
       one (or zero) → ambiguous → return None (caller closes without a price).
    """
    tag = f"{_OCO_REMARKS_PREFIX}{norenordno}"
    for row in trade_book:
        if not isinstance(row, dict):
            continue
        if row.get("trantype") == "S" and row.get("remarks") == tag:
            return _finite(row.get("flprc"))
    # Fallback: exactly one same-tsym SELL fill.
    sells = [
        row for row in trade_book
        if isinstance(row, dict)
        and row.get("trantype") == "S"
        and row.get("tsym") == tsym
    ]
    if len(sells) == 1:
        return _finite(sells[0].get("flprc"))
    return None


async def _reconcile_open_flat(
    db: Any, client: Any, open_tsyms: Dict[str, bool]
) -> Dict[str, int]:
    """Phase 2 — close OPEN docs whose position is flat at the broker."""
    summary = {"closed": 0, "skipped_held": 0, "skipped_no_norenordno": 0}
    try:
        trade_book = await client.trade_book()
        if not isinstance(trade_book, list):
            trade_book = []
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("reboot reconcile: trade_book fetch failed: %s", exc)
        trade_book = []
    try:
        cursor = db.live_trades.find({"status": {"$ne": "CLOSED"}})
        docs = await cursor.to_list(length=None)
    except Exception as exc:
        log.warning("reboot reconcile: open-doc query failed: %s", exc)
        return summary
    for doc in docs:
        try:
            norenordno = doc.get("norenordno")
            if not norenordno:
                # rehydrated / manual doc with no broker order id → cannot match.
                summary["skipped_no_norenordno"] += 1
                continue
            tsym = str(doc.get("trading_symbol") or "")
            if tsym in open_tsyms:
                # still held at the broker → leave OPEN.
                summary["skipped_held"] += 1
                continue
            # Flat at the broker → close. Match the exit fill to THIS entry.
            fill_price = _match_exit_fill_price(
                trade_book, norenordno=str(norenordno), tsym=tsym
            )
            closed = await close_live_trade(
                db,
                norenordno=str(norenordno),
                exit_price=None,
                fill_price=fill_price,
                exit_reason="reconciled_closed",
            )
            if closed:
                summary["closed"] += 1
        except Exception as exc:  # one bad doc never aborts the rest
            log.warning("reboot reconcile: close of %s failed: %s",
                        doc.get("norenordno"), exc)
    return summary


async def _sweep_orphan_ocos(
    db: Any, client: Any, open_tsyms: Dict[str, bool]
) -> Dict[str, int]:
    """Phase 3 — cancel resting OCOs whose entry is CONFIRMED gone.

    Conservative: cancel ONLY a confirmed orphan —
      * remarks-linked: the entry's ``live_trades`` doc is now CLOSED; OR
      * unlinked (no remarks / no doc): the row's tsym is flat in the NON-EMPTY book
        AND there is no OPEN ``live_trades`` doc for that tsym.
    Never cancel an OCO whose entry is still OPEN / position still held.
    """
    summary = {"cancelled": 0, "kept": 0}
    try:
        gtt_book = await client.gtt_book()
        if not isinstance(gtt_book, list):
            gtt_book = []
    except Exception as exc:
        log.warning("reboot reconcile: gtt_book fetch failed: %s", exc)
        return summary
    for row in gtt_book:
        try:
            if not isinstance(row, dict):
                continue
            al_id = row.get("al_id") or row.get("Al_id")
            if not al_id:
                continue
            entry_no = _parse_oco_norenordno(row.get("remarks"))
            should_cancel = False
            if entry_no:
                # remarks-linked: orphan iff its entry doc is CLOSED.
                doc = await db.live_trades.find_one({"norenordno": entry_no})
                if doc is not None and doc.get("status") == "CLOSED":
                    should_cancel = True
            else:
                # unlinked: orphan iff tsym is flat AND no OPEN live_trade for it.
                tsym = str(row.get("tsym") or "")
                if tsym and tsym not in open_tsyms:
                    open_doc = await db.live_trades.find_one(
                        {"trading_symbol": tsym, "status": {"$ne": "CLOSED"}}
                    )
                    if open_doc is None:
                        should_cancel = True
            if should_cancel:
                try:
                    await client.cancel_oco(al_id)
                    summary["cancelled"] += 1
                except Exception as exc:  # best-effort
                    log.warning("reboot reconcile: cancel_oco(%s) failed: %s",
                                al_id, exc)
            else:
                summary["kept"] += 1
        except Exception as exc:
            log.warning("reboot reconcile: OCO sweep row failed: %s", exc)
    return summary


async def reconcile_on_startup(db: Any, client: Any) -> Dict[str, Any]:
    """Transient-safe one-shot startup reconciliation. NEVER raises.

    Returns a small summary dict. See the module docstring for the two safety
    invariants (empty-book = UNKNOWN; match-the-entry fill).
    """
    summary: Dict[str, Any] = {"closed": 0, "cancelled": 0, "status": "ok"}
    try:
        book = await client.position_book()
    except Exception as exc:
        log.warning("reboot reconcile: position_book fetch failed (treating as "
                    "UNKNOWN — no action): %s", exc)
        summary["status"] = "unknown_position_book"
        return summary
    # EMPTY / non-list position_book == UNKNOWN. Do NOTHING (no close, no cancel).
    if not isinstance(book, list) or len(book) == 0:
        log.info("reboot reconcile: position_book empty/None — treating as UNKNOWN "
                 "(transient hiccup); no close, no cancel")
        summary["status"] = "unknown_position_book"
        return summary

    open_tsyms = _open_tsyms(book)

    # Phase 2 — close OPEN-but-flat docs.
    try:
        close_summary = await _reconcile_open_flat(db, client, open_tsyms)
        summary["closed"] = close_summary.get("closed", 0)
        summary["close_detail"] = close_summary
    except Exception as exc:  # pragma: no cover - defensive (inner already wraps)
        log.warning("reboot reconcile: open-flat phase failed: %s", exc)

    # Phase 3 — orphan-OCO sweep (independent of phase 2 outcome).
    try:
        sweep_summary = await _sweep_orphan_ocos(db, client, open_tsyms)
        summary["cancelled"] = sweep_summary.get("cancelled", 0)
        summary["sweep_detail"] = sweep_summary
    except Exception as exc:  # pragma: no cover - defensive (inner already wraps)
        log.warning("reboot reconcile: orphan-OCO sweep phase failed: %s", exc)

    return summary
