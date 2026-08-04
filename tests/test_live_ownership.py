"""Which broker positions can AlphaForge PROVE it opened?

The guard adopts only proven-owned positions and fails closed (2026-08-04). That
makes this resolution safety-critical in BOTH directions:

  * too permissive -> AlphaForge adopts a hand-placed position and squares real
    money it never opened (the 2026-08-04 incident);
  * too strict     -> AlphaForge's OWN position goes unguarded: no software stop,
    no target, no EOD square.

The authoritative source is the intent store: `record_intent` persists the full
OrderIntent, whose `tsym` IS the Noren symbol that was POSTed. That survives
overnight and needs no order-book read — which matters because a carry-forward
(NRML) position's entry order is in YESTERDAY's order book, not today's.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.ownership import resolve_owned_tsyms  # noqa: E402

OURS = "NIFTY04AUG26C24600"
THEIRS = "NIFTY04AUG26P24550"


def _intent(tsym, state, ordno=None, cid="cid-1"):
    return {"client_order_id": cid, "state": state, "norenordno": ordno,
            "intent": {"tsym": tsym, "client_order_id": cid}}


# --- the intent store is the primary, order-book-independent source --------

def test_submitted_intent_confers_ownership():
    owned = resolve_owned_tsyms(
        live_trades=[], live_orders=[_intent(OURS, "SUBMITTED", "N1")], order_book=[])
    assert owned == {OURS}


def test_carry_forward_resolves_without_todays_order_book():
    """The regression this function exists to prevent.

    An overnight NRML position's entry order is NOT in today's order book. Resolving
    ownership only through that book left it unowned -> unguarded after a restart.
    """
    owned = resolve_owned_tsyms(
        live_trades=[{"norenordno": "N1", "cid": "cid-1", "status": "OPEN"}],
        live_orders=[_intent(OURS, "SUBMITTED", "N1")],
        order_book=[],                      # today's book: yesterday's order absent
    )
    assert OURS in owned, "a carry-forward AlphaForge position must stay guarded"


def test_submitting_intent_confers_ownership():
    """The lost-ACK orphan: claimed, POST in flight, broker state unknown."""
    owned = resolve_owned_tsyms(
        live_trades=[], live_orders=[_intent(OURS, "SUBMITTING", None)], order_book=[])
    assert owned == {OURS}, "a lost-ACK orphan may well be a real position"


def test_bare_intent_never_confers_ownership():
    """state=INTENT was never POSTed, so no position can exist from it.

    Counting it would re-open the original hole on a narrower path: a stale intent
    for NIFTY24600CE would let AlphaForge adopt the operator's hand-bought
    NIFTY24600CE.
    """
    owned = resolve_owned_tsyms(
        live_trades=[], live_orders=[_intent(OURS, "INTENT", None)], order_book=[])
    assert owned == set()


# --- the order book stays a secondary path --------------------------------

def test_order_book_remarks_join_still_works():
    """remarks == client_order_id, written BEFORE the POST."""
    owned = resolve_owned_tsyms(
        live_trades=[{"norenordno": None, "cid": "cid-9", "status": "OPEN"}],
        live_orders=[],
        order_book=[{"norenordno": "N9", "tsym": OURS, "remarks": "cid-9"}])
    assert owned == {OURS}


def test_order_book_norenordno_join_still_works():
    owned = resolve_owned_tsyms(
        live_trades=[{"norenordno": "N1", "status": "OPEN"}],
        live_orders=[],
        order_book=[{"norenordno": "N1", "tsym": OURS, "remarks": "x"}])
    assert owned == {OURS}


# --- it must never over-claim ---------------------------------------------

def test_unrelated_broker_orders_are_not_owned():
    owned = resolve_owned_tsyms(
        live_trades=[{"norenordno": "N1", "cid": "cid-1", "status": "OPEN"}],
        live_orders=[_intent(OURS, "SUBMITTED", "N1")],
        order_book=[{"norenordno": "N1", "tsym": OURS, "remarks": "cid-1"},
                    {"norenordno": "ZZ", "tsym": THEIRS, "remarks": ""}])
    assert owned == {OURS}
    assert THEIRS not in owned, "adopted a position opened outside AlphaForge"


def test_closed_trades_do_not_confer_ownership():
    """A CLOSED trade is flat — nothing to guard, and its tsym may be reused."""
    owned = resolve_owned_tsyms(
        live_trades=[{"norenordno": "N1", "cid": "cid-1", "status": "CLOSED"}],
        live_orders=[],
        order_book=[{"norenordno": "N1", "tsym": OURS, "remarks": "cid-1"}])
    assert owned == set()


def test_empty_everything_is_empty_not_everything():
    """Fail closed: no evidence means own nothing."""
    assert resolve_owned_tsyms(live_trades=[], live_orders=[], order_book=[]) == set()


def test_malformed_docs_are_skipped_not_fatal():
    owned = resolve_owned_tsyms(
        live_trades=[{}, None, {"norenordno": "N1", "status": "OPEN"}],
        live_orders=[{}, None, {"state": "SUBMITTED", "intent": None},
                     _intent(OURS, "SUBMITTED", "N1")],
        order_book=[None, {}, {"tsym": ""}])
    assert owned == {OURS}
