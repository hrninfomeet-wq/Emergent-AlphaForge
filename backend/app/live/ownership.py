"""Which open broker positions can AlphaForge PROVE it opened?

The software guard adopts only proven-owned positions and FAILS CLOSED
(``live_position_guard.rehydrate_from_broker``). This resolution is therefore
safety-critical in both directions:

* **Too permissive** and AlphaForge takes over a position it never opened. On
  2026-08-04 the guard adopted a position the operator had bought by hand on the
  Flattrade mobile app, applied an invented 50% catastrophe stop, folded it into
  the overall basket, and squared it for real money 24 seconds later.
* **Too strict** and AlphaForge's OWN position goes unguarded — no software
  stop, no target, no EOD square, and no operator-visible reason.

The primary source is the **intent store**. ``record_intent`` persists the whole
``OrderIntent``, and ``intent.tsym`` IS the Noren symbol that was POSTed. That
gives ownership three properties the broker order book cannot:

1. it survives overnight, so a carry-forward (NRML) position stays owned even
   though its entry order sits in *yesterday's* order book;
2. it needs no broker read, so a failed/slow order-book call cannot silently
   un-own a live position;
3. it carries no symbol-space ambiguity — ``intent.tsym`` is exactly what was
   sent, so nothing here compares Upstox-space symbols against a Noren-keyed
   position book (the long-standing invariant that has caused real bugs).

The order-book join is kept as a secondary path for orders that reached the
broker without a surviving intent doc. It matches on ``norenordno`` OR on
``remarks``, because the order builder sets ``remarks == client_order_id`` and
``record_intent`` writes the cid BEFORE the POST — so an order that crashed
between the POST and ``mark_submitted`` is still provably ours.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Set

#: Intent states that can correspond to a real broker position.
#: ``INTENT`` is deliberately excluded: it was never POSTed, so no position can
#: exist from it. Counting it would re-open the adoption hole on a narrower path
#: — a stale intent for a strike would let AlphaForge adopt the operator's
#: hand-placed position on that same strike.
POSITION_BEARING_STATES = ("SUBMITTING", "SUBMITTED")


def _s(value: Any) -> str:
    return str(value) if value is not None else ""


def _expired(intent_doc: Dict[str, Any], today_iso: str) -> bool:
    """True only when the intent's contract PROVABLY expired before ``today_iso``.

    Unparseable → False: the calendar cannot prove anything, so the intent keeps
    whatever ownership the other rules give it.
    """
    from app.live_marks import expired_before

    intent = intent_doc.get("intent")
    return isinstance(intent, dict) and expired_before(intent.get("tsym"), today_iso)


def resolve_owned_tsyms(
    *,
    live_trades: Iterable[Optional[Dict[str, Any]]],
    live_orders: Iterable[Optional[Dict[str, Any]]],
    order_book: Iterable[Optional[Dict[str, Any]]],
    today_iso: Optional[str] = None,
) -> Set[str]:
    """Return the set of Noren tsyms AlphaForge can prove it opened.

    Pure: no DB, no network, no clock. The caller supplies already-read documents
    (and, optionally, today's IST date), which is what makes the whole ownership
    boundary host-testable.

    ``live_trades`` rows that are CLOSED are ignored — they are flat, and the
    contract may since have been reused by a different position.

    The SAME holds for the intent store, and it used to be missed there. An intent
    never leaves ``SUBMITTED`` — nothing writes a terminal state — so every
    contract AlphaForge had ever traded stayed "owned" forever (all 21 intents,
    back to June, read SUBMITTED on 2026-09-29). A position the operator later
    bought BY HAND on one of those contracts would have been adopted after a
    restart: invented stop, EOD square — the 2026-08-04 incident on a new path.
    An intent is therefore skipped when:

      * its entry's journal row is CLOSED (every close path closes only on a
        confirmed-flat book, so the entry's position is gone). The exclusion is by
        ORDER NUMBER, not tsym: a newer AlphaForge entry on the same strike keeps
        its own SUBMITTED intent and OPEN row, and stays owned;
      * ``today_iso`` is given and the contract's expiry date is BEFORE it — an
        expired option cannot be held. Expiring today still counts.
    """
    owned: Set[str] = set()
    ours_ordnos: Set[str] = set()
    ours_cids: Set[str] = set()
    closed_ordnos: Set[str] = {
        _s(doc.get("norenordno")) for doc in (live_trades or ())
        if isinstance(doc, dict) and doc.get("norenordno")
        and _s(doc.get("status")).upper() == "CLOSED"
    }

    # --- primary: the intent store knows the Noren tsym directly -----------
    for doc in live_orders or ():
        if not isinstance(doc, dict):
            continue
        if _s(doc.get("state")) not in POSITION_BEARING_STATES:
            continue
        if doc.get("norenordno") and _s(doc["norenordno"]) in closed_ordnos:
            continue
        if today_iso and _expired(doc, today_iso):
            continue
        intent = doc.get("intent")
        if isinstance(intent, dict):
            tsym = _s(intent.get("tsym")).strip()
            if tsym:
                owned.add(tsym)
        if doc.get("norenordno"):
            ours_ordnos.add(_s(doc["norenordno"]))
        if doc.get("client_order_id"):
            ours_cids.add(_s(doc["client_order_id"]))

    # --- secondary: journal rows, resolved through the order book ----------
    for doc in live_trades or ():
        if not isinstance(doc, dict):
            continue
        if _s(doc.get("status")).upper() == "CLOSED":
            continue
        if doc.get("norenordno"):
            ours_ordnos.add(_s(doc["norenordno"]))
        if doc.get("cid"):
            ours_cids.add(_s(doc["cid"]))

    for row in order_book or ():
        if not isinstance(row, dict):
            continue
        tsym = _s(row.get("tsym")).strip()
        if not tsym:
            continue
        if (_s(row.get("norenordno")) in ours_ordnos
                or (_s(row.get("remarks")) and _s(row.get("remarks")) in ours_cids)):
            owned.add(tsym)

    return owned
