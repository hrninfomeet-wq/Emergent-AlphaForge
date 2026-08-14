"""An accepted OCO is not a resting OCO.

`PlaceOCOOrder` returning ``ok`` with an ``al_id`` means the broker ACCEPTED the
alert. It does not mean a protective order survives. On 2026-08-14 a real 1-lot
live trade got al_id ``26081400000991`` — and the resting leg was then rejected
asynchronously:

    RED:Margin Shortfall:INR 101830.33 Available:INR 86932.68

The place-time code was correct: it only records ``oco_al_id`` when the response
is ok. The gap is that ACCEPTANCE and SURVIVAL are different facts, and nothing
checked the second. The journal claimed a broker backstop while the GTT book was
empty.

On this account that is structural rather than a tuning problem. A **resting**
NRML sell is margined as a potential naked short — the broker cannot know the long
will still exist when the trigger fires — so protecting a Rs 4,010 option position
demanded Rs 1,01,830 of span margin. The software guard is the real protection and
the operator-facing surfaces must say so.

Those surfaces already exist: ``auto_live`` writes
``oco_error="no_broker_backstop"`` whenever ``oco_al_id`` is falsy, and the Live
cockpit's alert rail renders "N live positions have no broker backstop
(software-guard-only)". They stayed dark only because the al_id was set. So the
job here is to tell the truth about the al_id, not to build a new surface.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

#: Keys a GTT/OCO row may carry its alert id under. GetPendingGTTOrder returns a
#: stored GTT in a WRAPPED representation that differs from the write contract, so
#: matching on a single key name risks missing a live backstop and wrongly
#: declaring a guarded position unprotected.
_AL_ID_KEYS = ("al_id", "alert_id", "id")


def _row_al_id(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    for key in _AL_ID_KEYS:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def unbacked_norenordnos(
    live_trades: Iterable[Optional[Dict[str, Any]]],
    gtt_book: Optional[Iterable[Optional[Dict[str, Any]]]],
) -> List[str]:
    """Open live trades whose recorded ``oco_al_id`` is NOT in the GTT book.

    Pure: no DB, no network. Returns the ``norenordno`` of each trade whose claimed
    backstop no longer exists, so the caller can clear the stale id and journal
    ``no_broker_backstop``.

    ``gtt_book=None`` means the read FAILED and returns nothing: an unreadable book
    is UNKNOWN, not "no backstop". Clearing a real al_id on a transient failure
    would also destroy the handle needed to CANCEL that OCO later — the same
    empty-read-is-not-flat rule the position guard applies.
    """
    if gtt_book is None:
        return []
    resting = {_row_al_id(r) for r in gtt_book if _row_al_id(r)}
    out: List[str] = []
    for trade in live_trades or ():
        if not isinstance(trade, dict):
            continue
        if str(trade.get("status") or "").upper() == "CLOSED":
            continue
        al_id = trade.get("oco_al_id")
        if al_id is None or not str(al_id).strip():
            continue          # already journalled no_broker_backstop — no churn
        if str(al_id).strip() not in resting:
            ordno = str(trade.get("norenordno") or "").strip()
            if ordno:
                out.append(ordno)
    return out
