"""OPEN-position accounting for the deployments command-centre cards.

The card's "today" block used to count every ``status == OPEN`` row of the deployment's
CURRENT-mode collection, whatever day it was entered, and sum their ``unrealized_pnl``:

* an OPEN paper trade stranded from an earlier day (a PC that was off through the
  square-off) inflated "Open trades" and "Today MTM" with a frozen number;
* a live deployment demoted to paper by a pause / kill switch stopped reading
  ``live_trades`` altogether, so a still-OPEN REAL-MONEY row vanished from the card.

This module is the one place that decides, PURE (no DB, no clock — the caller passes
``today_ist`` and the freshness cut):

* a row's ENTRY day (IST) and whether it is CARRIED (entered on an earlier IST day);
* which OPEN rows contribute to MTM — never counted twice, and never a frozen number:
  a live row needs a fresh guard mark (``marked_at``); a paper row entered on an earlier
  day needs a fresh marker stamp (``updated_at``); a paper row entered today keeps the
  old behaviour (the marker is running that session);
* the OTHER book — OPEN rows sitting in the collection that does not match the
  deployment's current mode. They are reported SEPARATELY, never added to the primary
  figures: paper and real money must not be summed into one number.

A row whose entry day cannot be read is NOT called carried (unknown is not "earlier")
and is not hidden either — it stays in ``open_trades`` exactly as before.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app.deployment_kill_switch import _ist_date
from app.trade_time import parse_instant


def _finite_float(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def entry_day_ist(row: Dict[str, Any]) -> Optional[str]:
    """IST calendar date (YYYY-MM-DD) the trade was entered, or None if unreadable."""
    return _ist_date(row.get("created_at"))


def is_carried(row: Dict[str, Any], today_ist: str) -> bool:
    """Entered on an IST day BEFORE today. Unknown / future entry days are not carried."""
    day = entry_day_ist(row)
    return day is not None and day < today_ist


def _fresh(stamp: Any, fresh_cut: datetime) -> bool:
    dt = parse_instant(stamp)
    return dt is not None and dt >= fresh_cut


def summarize_open_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    book: str,
    today_ist: str,
    fresh_cut: datetime,
) -> Dict[str, Any]:
    """Figures for ONE deployment's OPEN rows in ONE book (``"live"`` or ``"paper"``).

    ``open_trades``      every OPEN row (unchanged meaning: open right now)
    ``open_unrealized``  MTM of the rows whose mark is trustworthy (see module doc)
    ``open_unverified``  OPEN rows whose P&L is NOT in ``open_unrealized``
    ``open_carried``     OPEN rows entered on an earlier IST day
    ``open_carried_oldest``  the earliest such entry day, or None
    """
    opened: List[Dict[str, Any]] = [r for r in rows if isinstance(r, dict)]
    total = 0.0
    unverified = 0
    carried_days: List[str] = []
    for row in opened:
        carried = is_carried(row, today_ist)
        if carried:
            carried_days.append(entry_day_ist(row))  # type: ignore[arg-type]
        if book == "live":
            trusted = _fresh(row.get("marked_at"), fresh_cut)
        else:
            # Paper: today's rows are marked by the running marker (as before); a row
            # from an earlier day is trusted only while the marker is still stamping it.
            trusted = (not carried) or _fresh(row.get("updated_at"), fresh_cut)
        if trusted:
            total += _finite_float(row.get("unrealized_pnl"))
        else:
            unverified += 1
    return {
        "open_trades": len(opened),
        "open_unrealized": round(total, 2),
        "open_unverified": unverified,
        "open_carried": len(carried_days),
        "open_carried_oldest": min(carried_days) if carried_days else None,
    }


def annotate_live_open_row(
    row: Dict[str, Any],
    *,
    today_ist: str,
    fresh_cut: datetime,
    now: datetime,
) -> Dict[str, Any]:
    """The verification facts for ONE live_trades row, for a table that lists the raw
    journal (the Live Trade Statistics tab).

    An OPEN row's ``status`` is the journal's word; nothing at the broker was asked.
    What the app CAN say without a broker call is whether its own guard is still
    marking the row: the guard stamps ``marked_at`` every ~1.5s while it watches a
    position, so a stale or missing stamp means nobody is vouching for it (a restart, an
    expired token, or a doc the close never reached). Returns, for an OPEN row:

    ``open_state``     "verified" (fresh guard mark) | "unverified" (stale / never)
    ``mark_age_s``     seconds since the last mark, or None when never / unreadable
    ``carried``        entered on an IST day before today
    ``entry_day_ist``  the entry day, or None when unreadable

    and ``{}`` for any other status — a CLOSED row needs no verification.
    """
    if str(row.get("status") or "").upper() != "OPEN":
        return {}
    marked = parse_instant(row.get("marked_at"))
    age = None if marked is None else max(0, int((now - marked).total_seconds()))
    return {
        "open_state": "verified" if _fresh(row.get("marked_at"), fresh_cut) else "unverified",
        "mark_age_s": age,
        "carried": is_carried(row, today_ist),
        "entry_day_ist": entry_day_ist(row),
    }


def summarize_other_book(
    rows: Iterable[Dict[str, Any]],
    *,
    book: str,
    today_ist: str,
    realized_today: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """The OPEN rows (and today's realized P&L) in the book that does NOT match the
    deployment's current mode. None when there is nothing there — the card then says
    nothing about it.

    ``book`` is the book these rows live in: a deployment demoted live -> paper has
    ``book == "live"`` here, and those are REAL-MONEY rows the card must not drop.
    """
    opened = [r for r in rows if isinstance(r, dict)]
    realized = round(float(realized_today or 0.0), 2)
    if not opened and not realized:
        return None
    days = [d for d in (entry_day_ist(r) for r in opened) if d is not None]
    return {
        "book": book,
        "open_trades": len(opened),
        "open_carried": sum(1 for r in opened if is_carried(r, today_ist)),
        "oldest_open_entry_ist": min(days) if days else None,
        "realized_today": realized,
    }
