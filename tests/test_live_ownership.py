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


# --- the intent store never records a terminal state ------------------------
#
# An intent never leaves SUBMITTED. On 2026-09-29 all 21 intents — every order
# AlphaForge had ever placed, back to June — read SUBMITTED, so every contract it
# had ever traded stayed "owned" forever. The CLOSED rule above guarded the
# journal path only; the intent path walked straight past it.

SENSEX_PE = "SENSEX2691774300PE"   # 17 SEP 26 — the real 09-16 round trip


def test_a_closed_trades_intent_no_longer_confers_ownership():
    """The 2026-08-04 incident on a new path: AlphaForge closes a contract, the
    operator later buys the SAME contract by hand, the backend restarts, and the
    guard adopts the hand-placed position because the old intent says SUBMITTED."""
    owned = resolve_owned_tsyms(
        live_trades=[{"norenordno": "26091600102830", "cid": "c1", "status": "CLOSED"}],
        live_orders=[_intent(SENSEX_PE, "SUBMITTED", "26091600102830", cid="c1")],
        order_book=[])
    assert SENSEX_PE not in owned, "adopted a hand-placed position via a stale intent"


def test_a_newer_entry_on_the_same_strike_stays_owned():
    """Exclusion is by ORDER NUMBER, not by symbol: an earlier closed round trip
    must not un-own AlphaForge's own later position on the same contract."""
    owned = resolve_owned_tsyms(
        live_trades=[{"norenordno": "OLD", "cid": "c1", "status": "CLOSED"},
                     {"norenordno": "NEW", "cid": "c2", "status": "OPEN"}],
        live_orders=[_intent(SENSEX_PE, "SUBMITTED", "OLD", cid="c1"),
                     _intent(SENSEX_PE, "SUBMITTED", "NEW", cid="c2")],
        order_book=[])
    assert owned == {SENSEX_PE}, "AlphaForge's own open position went unguarded"


def test_an_expired_contracts_intent_confers_nothing():
    """An expired option cannot be held — whatever the intent store says."""
    owned = resolve_owned_tsyms(
        live_trades=[], live_orders=[_intent(SENSEX_PE, "SUBMITTED", "N1")],
        order_book=[], today_iso="2026-09-29")
    assert owned == set()


def test_a_contract_expiring_TODAY_is_still_owned():
    owned = resolve_owned_tsyms(
        live_trades=[], live_orders=[_intent(SENSEX_PE, "SUBMITTED", "N1")],
        order_book=[], today_iso="2026-09-17")
    assert owned == {SENSEX_PE}


def test_an_unparseable_symbol_is_not_expired_by_guesswork():
    """The calendar proves nothing about a symbol it cannot parse."""
    owned = resolve_owned_tsyms(
        live_trades=[], live_orders=[_intent("WEIRD-SYM", "SUBMITTED", "N1")],
        order_book=[], today_iso="2099-01-01")
    assert owned == {"WEIRD-SYM"}


def test_without_a_date_the_calendar_rule_is_off():
    """Pure function, no hidden clock: the caller must pass today_iso."""
    owned = resolve_owned_tsyms(
        live_trades=[], live_orders=[_intent(SENSEX_PE, "SUBMITTED", "N1")],
        order_book=[])
    assert owned == {SENSEX_PE}


def test_the_real_intent_store_owns_only_the_stale_open_row():
    """All 21 real intents (2026-09-29), 12 CLOSED journal rows + the one stale
    OPEN row. Before the fix every one of the 17 distinct contracts was owned."""
    closed = ["26080400148728", "26081400076294", "26081700029434", "26081700030634",
              "26090100424627", "26090100427552", "26090300039944", "26090300040956",
              "26090300161622", "26090900215605", "26091600102830", "26091600157346"]
    live_trades = ([{"norenordno": n, "status": "CLOSED"} for n in closed]
                   + [{"norenordno": "26091600196209", "status": "OPEN"}])
    intents = [_intent(t, "SUBMITTED", n, cid=n) for t, n in [
        ("NIFTY23JUN26C24100", "26062200296882"), ("NIFTY23JUN26C24100", "26062300076493"),
        ("NIFTY23JUN26C24100", "26062300101658"), ("NIFTY30JUN26C23900", "26062300599868"),
        ("SENSEX26JUN76500CE", "26062400020653"), ("SENSEX26JUN76600PE", "26062400128428"),
        ("NIFTY30JUN26C24000", "26063000101633"), ("NIFTY30JUN26C24000", "26063000109330"),
        ("NIFTY04AUG26P24550", "26080400148728"), ("NIFTY18AUG26P24300", "26081400076294"),
        ("NIFTY18AUG26P24300", "26081700029434"), ("NIFTY18AUG26P24300", "26081700030634"),
        ("NIFTY01SEP26C23950", "26090100424627"), ("NIFTY01SEP26C24000", "26090100427552"),
        ("SENSEX2690376800PE", "26090300039944"), ("SENSEX2690376800PE", "26090300040956"),
        ("SENSEX2690376600CE", "26090300161622"), ("SENSEX2691075200PE", "26090900215605"),
        ("SENSEX2691774300PE", "26091600102830"), ("SENSEX2691774400CE", "26091600157346"),
        ("SENSEX2691774400PE", "26091600196209")]]
    # Journal rule alone: only the stale OPEN row's contract, plus the June test
    # orders that have no journal row (placed from the manual order page).
    by_journal = resolve_owned_tsyms(live_trades=live_trades, live_orders=intents,
                                     order_book=[])
    assert "SENSEX2691774300PE" not in by_journal
    assert "SENSEX2691774400PE" in by_journal
    # Both rules: every contract is expired, so nothing at all is owned.
    assert resolve_owned_tsyms(live_trades=live_trades, live_orders=intents,
                               order_book=[], today_iso="2026-09-29") == set()


# --- the runtime must hand the resolver the CLOSED rows ----------------------

def test_startup_recovery_does_not_adopt_a_hand_placed_position_via_a_stale_intent(monkeypatch):
    """End to end through live_startup_recovery. The runtime used to read only
    non-CLOSED journal rows, so the resolver never saw the CLOSED row that proves
    the old intent is spent. A far-future contract keeps the calendar rule from
    masking the journal rule."""
    import asyncio

    import app.runtime as rt
    from tests.test_premium_momentum_recovery import _DB, _Locks, _Reg, _wire

    class _Coll:
        """Honours equality AND $ne — the runtime's filter is what is under test."""
        def __init__(self, docs):
            self.docs = docs

        def find(self, q=None, proj=None):
            def _ok(d):
                for k, v in (q or {}).items():
                    if isinstance(v, dict) and "$ne" in v:
                        if d.get(k) == v["$ne"]:
                            return False
                    elif d.get(k) != v:
                        return False
                return True

            async def _cursor():
                for d in self.docs:
                    if _ok(d):
                        yield dict(d)
            return _cursor()

    manual = "NIFTY29DEC99C24000"          # 2099 — never calendar-expired
    db = _DB(_Locks(), [])
    db.live_trades = _Coll([{"norenordno": "OLD", "cid": "c1", "status": "CLOSED"}])
    db.live_orders = _Coll([{"norenordno": "OLD", "client_order_id": "c1",
                             "state": "SUBMITTED", "intent": {"tsym": manual}}])
    events = []
    _wire(monkeypatch, book=[{"tsym": manual, "netqty": "65", "exch": "NFO"}],
          db=db, reg=_Reg(events=events), events=events)
    asyncio.run(rt.live_startup_recovery())
    adopted = [e[1] for e in events if e[0] == "generic_rehydrate"]
    assert adopted, "generic rehydrate never ran"
    assert manual not in adopted[0], "adopted the operator's hand-placed position"
