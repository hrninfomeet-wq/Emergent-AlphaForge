"""What the BROKER's position book says — the ground the Greeks card stands on.

The Greeks route used to read only the software guard's registry. An empty registry
means "the guard is watching nothing" — NOT "the account is flat": after a restart
with an expired token guard rehydration cannot run, so the registry is empty while a
carried or unguarded broker position still exists. The card then said "No open live
positions." in the very state the account was most exposed.

Three different things have to stay distinct, and this module keeps them so:

* **flat**    — the broker book was READ, and no row has a non-zero net quantity.
                The only state in which "no positions" is a fact.
* **open**    — the broker book was read and something is open. ``unguarded`` lists
                the open rows the software guard is NOT watching.
* **unknown** — the book could not be read (no client, session expired, broker down)
                or is only a stale last-good. Nothing at all can be said.

PURE — no I/O, no clock. The route gathers the rows and the registry.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

FLAT = "flat"
OPEN = "open"
UNKNOWN = "unknown"


def _netqty(row: Dict[str, Any]) -> Optional[float]:
    """The signed net quantity of a Noren position row; None when unparseable."""
    try:
        n = float(row.get("netqty"))
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # NaN -> None


def open_rows(rows: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Rows with a non-zero net quantity. A row whose quantity cannot be parsed is
    treated as OPEN, not flat: an unreadable quantity is not evidence of zero."""
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        n = _netqty(row)
        if n is None or n != 0:
            out.append(row)
    return out


def classify_broker_book(
    rows: Optional[Iterable[Dict[str, Any]]],
    guarded_tsyms: Iterable[str],
    *,
    error: Optional[str] = None,
    stale: bool = False,
) -> Dict[str, Any]:
    """Return ``{state, open_count, guarded_count, unguarded, error, stale}``.

    ``rows`` is None when the book could not be read at all. ``stale`` means the
    rows are a last-good snapshot served while the source is failing: a stale
    book cannot confirm "flat" (or "open") NOW, so it is UNKNOWN.
    """
    if rows is None or stale:
        why = error or ("broker book is a stale last-good reading" if stale
                        else "broker book unreadable")
        return {"state": UNKNOWN, "open_count": None, "guarded_count": None,
                "unguarded": [], "error": str(why)[:240], "stale": bool(stale)}
    opened = open_rows(rows)
    if not opened:
        return {"state": FLAT, "open_count": 0, "guarded_count": 0,
                "unguarded": [], "error": None, "stale": False}
    guarded = {str(t) for t in guarded_tsyms if t}
    unguarded = [str(r.get("tsym") or "?") for r in opened
                 if str(r.get("tsym") or "") not in guarded]
    return {"state": OPEN, "open_count": len(opened),
            "guarded_count": len(opened) - len(unguarded),
            "unguarded": unguarded, "error": None, "stale": False}
