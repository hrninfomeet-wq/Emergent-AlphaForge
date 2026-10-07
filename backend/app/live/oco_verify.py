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

import math
import re
from typing import Any, Dict, FrozenSet, Iterable, List, Optional

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


# ---------------------------------------------------------------------------
# Lost-ACK resolution: which resting alert IS this one?
#
# A non-200 / timeout on PlaceGTTOrder / PlaceOCOOrder leaves the outcome
# UNKNOWN (the gateway can fail after the OMS created the alert), so there is no
# al_id to track it by. The GTT book is the only place to find it again. Rows
# come back in the documented mixed case (``Al_id``, ``Remarks``) and the broker
# DECORATES remarks ("LMT_BOS_O: oco:<n>: Ltp ... is above ..."), so matching
# is case-insensitive on keys and token-bounded on the tag.
# ---------------------------------------------------------------------------

def _ci(row: Dict[str, Any], name: str) -> Any:
    """``row[name]`` with a case-insensitive key lookup."""
    if name in row:
        return row[name]
    for key, value in row.items():
        if isinstance(key, str) and key.lower() == name:
            return value
    return None


def _book_al_id(row: Dict[str, Any]) -> str:
    value = _ci(row, "al_id")
    return str(value).strip() if value is not None else ""


def _price(value: Any) -> Optional[float]:
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    return round(p, 2) if math.isfinite(p) and p > 0 else None


def _trigger_sets(alert: Dict[str, Any]) -> List[FrozenSet[float]]:
    """Every trigger representation an alert carries: the ``oivariable`` ``d``
    values (OCO request; the wrapped read form of any stored alert) and a flat
    ``d`` (single-GTT request)."""
    out: List[FrozenSet[float]] = []
    oiv = _ci(alert, "oivariable")
    if isinstance(oiv, list):
        prices = {_price(v.get("d")) for v in oiv if isinstance(v, dict)}
        prices.discard(None)
        if prices:
            out.append(frozenset(prices))
    d = _price(_ci(alert, "d"))
    if d is not None:
        out.append(frozenset({d}))
    return out


def alert_triggers(alert: Dict[str, Any]) -> Optional[FrozenSet[float]]:
    """The trigger prices of a GTT/OCO request (``oivariable`` first, else ``d``)."""
    sets = _trigger_sets(alert) if isinstance(alert, dict) else []
    return sets[0] if sets else None


def _has_tag(remarks: Any, tag: str) -> bool:
    if not isinstance(remarks, str):
        return False
    pattern = r"(?<![0-9A-Za-z])" + re.escape(tag) + r"(?![0-9A-Za-z])"
    return re.search(pattern, remarks) is not None


def matching_alert_ids(
    gtt_book: Optional[Iterable[Any]],
    *,
    tsym: Any,
    remarks: Optional[str] = None,
    triggers: Optional[Iterable[float]] = None,
) -> List[str]:
    """al_ids of GTT-book rows that are provably the alert described.

    A row must be on the same ``tsym``. Then, when BOTH the request and the row
    carry remarks, the request's remarks must appear in the row's as a whole
    token; otherwise the row's trigger prices must equal ``triggers`` exactly.
    With neither usable there is no proof and nothing matches.

    Pure. The caller decides what a count means: lost-ack resolution adopts only
    an EXACTLY-one match, while an orphan sweep cancels every row carrying its
    tag.
    """
    want_tsym = str(tsym or "").strip()
    tag = str(remarks or "").strip()
    want = frozenset(_price(t) for t in triggers) if triggers is not None else frozenset()
    if None in want:
        want = frozenset()
    if not want_tsym:
        return []
    out: List[str] = []
    for row in gtt_book or ():
        if not isinstance(row, dict):
            continue
        al_id = _book_al_id(row)
        if not al_id or str(_ci(row, "tsym") or "").strip() != want_tsym:
            continue
        row_remarks = _ci(row, "remarks")
        if tag and isinstance(row_remarks, str) and row_remarks.strip():
            matched = _has_tag(row_remarks, tag)
        else:
            matched = bool(want) and want in _trigger_sets(row)
        if matched:
            out.append(al_id)
    return out
