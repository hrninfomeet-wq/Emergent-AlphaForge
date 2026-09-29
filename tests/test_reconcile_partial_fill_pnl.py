"""A reconcile-closed trade must journal its realized P&L, including partial fills.

2026-09-16, real money: a 5-lot SENSEX 74300 PE was bought at 10:19:03 and sold at
10:20:46/47 as TWO partial fills, 60 @ 361.00 and 40 @ 361.00. `_match_exit_fill_price`
required *exactly one* same-tsym SELL, found two, and returned None — so the trade
closed with `realized_pnl = null` and the Live Deployments pane showed ₹0 for a
round trip the broker had booked at ₹822.

Partial fills are ordinary, not an edge case: any order that sweeps more than one
price level produces them. Six of twelve live trades were already CLOSED with a
null realized_pnl, and this module's own docstring says that is exactly what it
exists to prevent — an unjournalled close leaves `daily_loss_cap` and the day-stop
blind to a realized loss.

The bar for a price is PROOF, not plausibility. The broker nets a contract's fills
into one position, so a SELL never says which BUY it closed; it can be attributed
only when nothing else could own it. And the proof yields the ENTRY basis too —
the doc's `entry_fill_price` blends every same-day entry on the contract.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.close_loop import realized_fields  # noqa: E402
from app.live.reboot_reconcile import (  # noqa: E402
    _carry_free,
    _fill_time,
    _match_close,
    _match_exit_fill_price,
)

ENTRY = "26091600102830"
OTHER = "26091600099999"
TSYM = "SENSEX2691774300PE"


def _row(trantype, qty, prc, tm, *, tsym=TSYM, remarks=None, ordno=None, prd=None):
    r = {"trantype": trantype, "tsym": tsym, "flqty": qty, "flprc": prc,
         "fltm": tm, "remarks": remarks, "norenordno": ordno}
    if prd is not None:
        r["prd"] = prd
    return r


def _close(book, carry_proven=True, **kw):
    return _match_close(book, norenordno=ENTRY, tsym=TSYM, carry_proven=carry_proven,
                        **kw)


def _exit(book, carry_proven=True, **kw):
    return _match_exit_fill_price(book, norenordno=ENTRY, tsym=TSYM,
                                  carry_proven=carry_proven, **kw)


# The real book, verbatim from /live-broker/trades on 2026-09-16.
REAL_BOOK = [
    _row("S", 40, 361.00, "16-09-2026 10:20:47"),
    _row("S", 60, 361.00, "16-09-2026 10:20:46"),
    _row("B", 80, 352.80, "16-09-2026 10:19:03", remarks="1623d483ba2b4e8c9b5e0bfdfade93b6", ordno=ENTRY),
    _row("B", 20, 352.70, "16-09-2026 10:19:03", remarks="1623d483ba2b4e8c9b5e0bfdfade93b6", ordno=ENTRY),
    # An earlier, unrelated round trip on a DIFFERENT contract the same day.
    _row("S", 80, 337.00, "16-09-2026 09:34:29", tsym="SENSEX2691774300CE"),
    _row("B", 60, 327.00, "16-09-2026 09:28:35", tsym="SENSEX2691774300CE"),
]


# --------------------------------------------------------------------------- #
# The regression
# --------------------------------------------------------------------------- #

def test_the_real_partial_fill_exit_now_resolves():
    """THE regression. Returned None before the aggregate path existed."""
    assert _exit(REAL_BOOK, quantity=100) == 361.00


def test_it_reproduces_the_brokers_own_realized_pnl_exactly_through_the_journal():
    """The broker booked rpnl 822.00. Routed through the SAME helper every close
    uses — not recomputed in the test — the journal must land on that number."""
    m = _close(REAL_BOOK)
    doc = {"quantity": 100, "entry_fill_price": 352.78, "exch": "BFO"}
    f = realized_fields(doc, m["exit_px"], entry_px=m["entry_px"], quantity=m["qty"])
    assert f["realized_pnl"] == 822.00


def test_the_doc_quantity_is_not_needed_any_more():
    """The entry's OWN fills give the position size (100 = 80 + 20). The old rule
    needed the doc's quantity; the proof does not."""
    m = _close(REAL_BOOK)
    assert (m["qty"], round(m["entry_px"], 4), m["exit_px"]) == (100, 352.78, 361.00)


def test_fills_are_weighted_by_quantity_not_averaged_naively():
    """A naive mean of 300 and 360 is 330; weighted by 90/10 it is 306. Getting
    this wrong misstates P&L on every unevenly-split exit."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("S", 90, 300.00, "16-09-2026 10:05:00"),
        _row("S", 10, 360.00, "16-09-2026 10:05:01"),
    ]
    assert _exit(book) == 306.00


def test_the_price_is_not_rounded_before_it_is_multiplied():
    """30 @ 361.05 + 30 @ 361.10 + 40 @ 361.15 averages 361.105. Rounding that to
    361.11 first and multiplying by 100 books ₹0.50 the broker never paid."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("S", 30, 361.05, "16-09-2026 10:05:00"),
        _row("S", 30, 361.10, "16-09-2026 10:05:01"),
        _row("S", 40, 361.15, "16-09-2026 10:05:02"),
    ]
    m = _close(book)
    f = realized_fields({"quantity": 100, "exch": "BFO"}, m["exit_px"],
                        entry_px=m["entry_px"], quantity=m["qty"])
    assert f["realized_pnl"] == 16110.50


def test_a_single_exact_sell_resolves():
    """What the old lone-SELL fallback handled, now with proof: the entry's own
    fill precedes it and nothing else touched the contract."""
    book = [
        _row("B", 65, 60.00, "14-08-2026 09:30:00", ordno=ENTRY),
        _row("S", 65, 75.00, "14-08-2026 10:44:00"),
    ]
    assert _exit(book) == 75.00


# --------------------------------------------------------------------------- #
# Attribution proof
# --------------------------------------------------------------------------- #

def test_a_sell_before_this_entry_is_never_borrowed():
    """A same-strike re-entry must not pull the EARLIER round trip's exit price
    into the later trade. Here the earlier SELL leaves the account short on the
    contract when we enter, so nothing is provable."""
    book = [
        _row("S", 100, 500.00, "16-09-2026 09:30:00"),   # earlier trade's exit
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
    ]
    assert _close(book) is None


def test_an_over_or_under_sized_pool_is_refused():
    """Short means the exit is incomplete or partly someone else's; over means the
    pool spans more than this trade. Neither is provably this trade's exit."""
    short = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("S", 60, 300.00, "16-09-2026 10:05:00"),
    ]
    assert _close(short) is None
    odd = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("S", 70, 300.00, "16-09-2026 10:05:00"),
        _row("S", 70, 310.00, "16-09-2026 10:05:01"),   # 140 != 100
    ]
    assert _close(odd) is None


def test_a_sell_tagged_for_a_different_entry_refuses_the_whole_answer():
    """An oco tag is positive proof of a SECOND position. Even when untagged SELLs
    after it sum exactly to our size, nothing here is provably ours."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("S", 100, 300.00, "16-09-2026 10:05:00", remarks="oco:9999999999"),
        _row("S", 100, 310.00, "16-09-2026 10:06:00"),
    ]
    assert _close(book) is None


def test_an_entry_with_no_fills_in_the_book_cannot_be_priced():
    """A never-filled entry must stay null — nothing was bought, so no SELL can be
    its exit. This is also what stops a PRIOR-DAY doc borrowing today's fills."""
    assert _close([_row("S", 100, 300.00, "16-09-2026 10:05:00")]) is None


def test_the_oco_tagged_leg_still_wins_when_present():
    """Rule (1): the tag is proof of ownership and takes precedence."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("S", 100, 999.00, "16-09-2026 10:05:00",
             remarks=f"LMT_BOS_O: oco:{ENTRY}: Ltp 61.95 is above 21.45"),
    ]
    assert _exit(book) == 999.00


def test_a_tagged_oco_leg_filled_in_parts_is_weighted():
    """The tagged path used to return whichever part it met first (the book is
    newest-first: 300, not the true 270)."""
    tag = f"oco:{ENTRY}"
    book = [
        _row("S", 40, 300.00, "16-09-2026 10:05:01", remarks=tag),
        _row("S", 60, 250.00, "16-09-2026 10:05:00", remarks=tag),
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
    ]
    assert _exit(book) == 270.00


def test_an_overlapping_position_on_the_same_strike_is_refused():
    """X is open when we enter; X exits first. Time + quantity alone would hand
    X's exit price to us (100 sold after our entry == our 100). The account was not
    flat on this contract when we entered, so no SELL is provably ours."""
    book = [
        _row("B", 100, 150.00, "16-09-2026 10:00:00", ordno=OTHER),   # X enters
        _row("B", 100, 200.00, "16-09-2026 10:05:00", ordno=ENTRY),   # we enter
        _row("S", 100, 170.00, "16-09-2026 10:10:00"),                # X's exit
        _row("S", 100, 260.00, "16-09-2026 10:20:00"),                # ours
    ]
    assert _close(book) is None


def test_an_earlier_COMPLETE_round_trip_does_not_block_the_proof():
    """Flat again before we entered → the earlier trade cannot own a later SELL."""
    book = [
        _row("B", 100, 150.00, "16-09-2026 09:30:00", ordno=OTHER),
        _row("S", 100, 170.00, "16-09-2026 09:40:00"),
        _row("B", 100, 200.00, "16-09-2026 10:05:00", ordno=ENTRY),
        _row("S", 100, 260.00, "16-09-2026 10:20:00"),
    ]
    assert _exit(book) == 260.00


def test_a_same_day_re_entry_is_measured_from_ITS_OWN_entry_not_the_blend():
    """E1 100 @150 → 170, then E2 (ours) 100 @200 → 260. The doc's
    entry_fill_price is the broker's daybuyavgprc — (150+200)/2 = 175 — so the old
    journal booked 100 × (260 − 175) = ₹8,500 for a ₹6,000 trade."""
    book = [
        _row("B", 100, 150.00, "16-09-2026 09:30:00", ordno=OTHER),
        _row("S", 100, 170.00, "16-09-2026 09:40:00"),
        _row("B", 100, 200.00, "16-09-2026 10:05:00", ordno=ENTRY),
        _row("S", 100, 260.00, "16-09-2026 10:20:00"),
    ]
    m = _close(book)
    doc = {"quantity": 100, "entry_fill_price": 175.0, "exch": "BFO"}
    f = realized_fields(doc, m["exit_px"], entry_px=m["entry_px"], quantity=m["qty"])
    assert f["realized_pnl"] == 6000.00
    assert f["entry_basis"] == "own_fills" and f["entry_basis_price"] == 200.0


def test_a_partially_filled_entry_is_priced_at_what_filled():
    """Ordered 100, filled 60, exited 60. The target is the FILLED size; the old
    target (the ordered 100) never matched, so the P&L stayed null forever."""
    book = [
        _row("B", 60, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("S", 60, 250.00, "16-09-2026 10:05:00"),
    ]
    m = _close(book, quantity=100)
    f = realized_fields({"quantity": 100, "exch": "BFO"}, m["exit_px"],
                        entry_px=m["entry_px"], quantity=m["qty"])
    assert f["realized_pnl"] == 3000.00 and f["filled_quantity"] == 60


def test_a_newcomer_stops_the_pool_before_it_can_complete_the_quantity():
    """We sell 60, a DIFFERENT order buys 40, then 40 is sold. 60 + 40 == 100, so a
    time+quantity rule would accept a price that is partly the newcomer's exit."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("S", 60, 250.00, "16-09-2026 10:05:00"),
        _row("B", 40, 240.00, "16-09-2026 10:06:00", ordno=OTHER),
        _row("S", 40, 300.00, "16-09-2026 10:07:00"),
    ]
    assert _close(book) is None


def test_another_order_filling_DURING_our_entry_is_refused():
    """Our 100 fills as 50 + 50 two minutes apart; another order buys in between.
    Nothing before our first fill, nothing after our last — only the middle shows
    the second position, and it can own the SELLs that follow."""
    book = [
        _row("B", 50, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("B", 100, 205.00, "16-09-2026 10:01:00", ordno=OTHER),
        _row("B", 50, 210.00, "16-09-2026 10:02:00", ordno=ENTRY),
        _row("S", 100, 260.00, "16-09-2026 10:20:00"),
    ]
    assert _close(book) is None


def test_a_same_second_newcomer_is_read_conservatively():
    """A tie between a foreign BUY and a SELL cannot be ordered; the BUY is taken
    as first, so the SELL is not claimed."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("S", 100, 250.00, "16-09-2026 10:05:00"),
        _row("B", 100, 240.00, "16-09-2026 10:05:00", ordno=OTHER),
    ]
    assert _close(book) is None


def test_an_unpriced_partial_refuses_rather_than_skipping_ahead():
    """60 @ (no price) + 40 @ 361, then an unrelated 60 later. Skipping the unpriced
    row would reach 100 with SOMEONE ELSE's 60."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("S", 60, None, "16-09-2026 10:05:00"),
        _row("S", 40, 361.00, "16-09-2026 10:05:01"),
        _row("S", 60, 380.00, "16-09-2026 10:30:00"),
    ]
    assert _close(book) is None


def test_an_unorderable_fill_on_the_contract_refuses_the_proof():
    """Everything above is an argument about ORDER; one row with no usable time
    on this contract makes the argument impossible."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        _row("S", 100, 250.00, "16-09-2026 10:05:00"),
        _row("S", 100, 250.00, ""),
    ]
    assert _close(book) is None


def test_a_row_with_no_FILLED_quantity_is_unorderable():
    """`qty` is the ORDER quantity. A 100-lot SELL that filled 60 + 40 would, read
    by `qty`, complete the exit on its first part — at the wrong price."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY),
        {"trantype": "S", "tsym": TSYM, "qty": "100", "flprc": 250.0,
         "fltm": "16-09-2026 10:05:00"},
        {"trantype": "S", "tsym": TSYM, "qty": "100", "flprc": 300.0,
         "fltm": "16-09-2026 10:05:01"},
    ]
    assert _close(book) is None


def test_mixed_products_on_one_contract_refuse_the_proof():
    """An MIS long and an NRML short on one symbol net to zero while BOTH are open
    (the OCO leg that fired at placement did exactly that)."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:00:00", ordno=ENTRY, prd="I"),
        _row("S", 100, 180.00, "16-09-2026 10:00:02", prd="M"),
        _row("S", 100, 260.00, "16-09-2026 10:20:00", prd="I"),
    ]
    assert _close(book) is None


def test_other_contracts_do_not_disturb_the_proof():
    """Unorderable or overlapping rows on a DIFFERENT symbol are irrelevant."""
    book = REAL_BOOK + [_row("B", 5, 1.0, "", tsym="NIFTY29SEP26P24300")]
    assert _exit(book) == 361.00


# --------------------------------------------------------------------------- #
# Carry-forward: the one position the day-scoped trade book cannot see
# --------------------------------------------------------------------------- #

def test_a_carried_position_makes_an_untagged_exit_unattributable():
    """Yesterday's NRML position on the contract is sold at 10:10 @170; ours at
    10:20 @260. Today's book cannot see yesterday's buy, so the 170 SELL looks like
    ours: -3,000 booked for a +6,000 trade. Only a carry-free position book (or an
    attestation) lets untagged SELLs be attributed."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:05:00", ordno=ENTRY),
        _row("S", 100, 170.00, "16-09-2026 10:10:00"),   # the CARRIED position's exit
        _row("S", 100, 260.00, "16-09-2026 10:20:00"),
    ]
    assert _close(book, carry_proven=False) is None


def test_a_tagged_exit_needs_no_carry_proof():
    """The tag proves ownership whatever else was open on the contract."""
    book = [
        _row("B", 100, 200.00, "16-09-2026 10:05:00", ordno=ENTRY),
        _row("S", 100, 260.00, "16-09-2026 10:20:00", remarks=f"oco:{ENTRY}"),
    ]
    assert _exit(book, carry_proven=False) == 260.00


def test_carry_free_needs_a_row_proving_zero_carry():
    assert _carry_free([{"tsym": TSYM, "cfbuyqty": "0", "cfsellqty": "0"}], TSYM)
    assert not _carry_free([{"tsym": TSYM, "cfbuyqty": "100", "cfsellqty": "0"}], TSYM)
    assert not _carry_free([{"tsym": TSYM, "cfbuyqty": "0", "cfsellqty": "0"},
                            {"tsym": TSYM, "cfbuyqty": "0", "cfsellqty": "20"}], TSYM)
    assert not _carry_free([{"tsym": TSYM}], TSYM), "absent fields prove nothing"
    assert not _carry_free([{"tsym": "OTHER", "cfbuyqty": "0", "cfsellqty": "0"}], TSYM)
    assert not _carry_free([], TSYM) and not _carry_free(None, TSYM)


# --------------------------------------------------------------------------- #
# Fill timestamps. The vendor's own TradeBook sample carries a 1980 placeholder in
# `fltm`, and TIME-first `exch_tm`; the WebSocket/postback carry it DATE-first.
# --------------------------------------------------------------------------- #

def test_the_1980_placeholder_is_not_a_time():
    """It parses cleanly and sorts before every real fill — an exit would appear to
    precede its own entry. Treated as absent; the next field is used."""
    row = {"fltm": "01-01-1980 00:00:00", "exch_tm": "10:20:47 16-09-2026"}
    assert _fill_time(row) == datetime(2026, 9, 16, 10, 20, 47)
    assert _fill_time({"fltm": "01-01-1980 00:00:00",
                       "exch_tm": "00:00:00 01-01-1980"}) is None


def test_both_exch_tm_layouts_parse():
    assert _fill_time({"exch_tm": "10:20:47 16-09-2026"}) == datetime(2026, 9, 16, 10, 20, 47)
    assert _fill_time({"exch_tm": "16-09-2026 10:20:47"}) == datetime(2026, 9, 16, 10, 20, 47)


def test_the_order_entry_time_is_not_a_fill_time():
    """`norentm` is when the ORDER was entered: every partial shares it, and a
    resting SELL would sort before a BUY it actually filled after."""
    assert _fill_time({"norentm": "19:59:32 13-12-2020"}) is None
    assert _fill_time({"fltm": "16-09-2026 10:20:47",
                       "norentm": "10:20:40 16-09-2026"}) == datetime(2026, 9, 16, 10, 20, 47)


# --------------------------------------------------------------------------- #
# Phase 2b — backfilling a close that never journalled its P&L
#
# Fixing the matcher only helps trades closed from now on. Six of twelve live
# trades were ALREADY closed with realized_pnl = null. The repair only ever adds
# a number that was missing; it must never overwrite one that exists.
# --------------------------------------------------------------------------- #

import asyncio  # noqa: E402

from app.live.reboot_reconcile import _repair_missing_realized  # noqa: E402

FLAT_BOOK = [{"tsym": TSYM, "netqty": "0", "cfbuyqty": "0", "cfsellqty": "0"}]


class _Coll:
    def __init__(self, rows): self.rows = rows; self.updates = []; self.finds = []

    def find(self, flt):
        self.finds.append(flt)
        sel = [r for r in self.rows
               if all(r.get(k) == v for k, v in flt.items())]

        class _C:
            async def to_list(_s, length=None): return list(sel)
        return _C()

    async def update_one(self, flt, upd):
        for r in self.rows:
            if all(r.get(k) == v for k, v in flt.items()):
                r.update(upd.get("$set") or {})
                self.updates.append((flt, upd))
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        self.updates.append((flt, None))
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


class _DB:
    def __init__(self, rows): self.live_trades = _Coll(rows)


class _Client:
    def __init__(self, book): self._book = book
    async def trade_book(self): return list(self._book)


def _doc(**kw):
    base = {"norenordno": ENTRY, "noren_tsym": TSYM, "quantity": 100,
            "entry_fill_price": 352.78, "status": "CLOSED", "realized_pnl": None,
            "exch": "BFO"}
    base.update(kw)
    return base


def _repair(db, book=REAL_BOOK, position_book=FLAT_BOOK, **kw):
    return asyncio.run(_repair_missing_realized(db, _Client(book),
                                                position_book=position_book, **kw))


def test_the_repair_backfills_the_real_trade():
    db = _DB([_doc()])
    out = _repair(db)
    assert out["repaired"] == 1
    row = db.live_trades.rows[0]
    assert row["realized_pnl"] == 822.00, row.get("realized_pnl")
    assert row["exit_price"] == 361.00
    assert row["realized_pnl_backfilled"] is True


def test_the_repair_also_journals_charges_and_net():
    db = _DB([_doc()])
    _repair(db)
    row = db.live_trades.rows[0]
    assert row.get("total_charges") is not None
    assert row["net_realized_pnl"] == round(822.00 - row["total_charges"], 2)
    assert row["net_realized_pnl"] < row["realized_pnl"], "charges must reduce net"


def test_the_repair_never_overwrites_an_existing_number():
    """A correctly-journalled trade must be untouchable — the query filters on
    realized_pnl null, and the WRITE re-asserts it against a concurrent close."""
    db = _DB([_doc(realized_pnl=-500.0)])
    out = _repair(db)
    assert out["repaired"] == 0
    assert db.live_trades.rows[0]["realized_pnl"] == -500.0
    assert db.live_trades.updates == [], "it issued a write against a journalled trade"


def test_the_repairs_write_itself_re_asserts_the_null():
    """The read-then-write race: the filter ON THE WRITE is what lets a concurrent
    close's real number win. Pinned directly, not via a stub that ignores it."""
    db = _DB([_doc()])
    _repair(db)
    flt, _ = db.live_trades.updates[0]
    assert flt.get("realized_pnl", "absent") is None and flt.get("status") == "CLOSED"


def test_the_repair_reads_only_closed_rows():
    """An OPEN row with a null P&L is simply unclosed — the repair must not price
    a position that may still be held."""
    db = _DB([_doc(status="OPEN")])
    out = _repair(db)
    assert out["repaired"] == 0 and db.live_trades.rows[0]["realized_pnl"] is None
    assert db.live_trades.finds[0].get("status") == "CLOSED"


def test_a_never_filled_trade_stays_null():
    """Nothing was bought, so no SELL can be its exit."""
    book = [_row("S", 100, 300.00, "16-09-2026 10:05:00")]  # no matching entry fills
    db = _DB([_doc(norenordno="26090100427552")])
    out = _repair(db, book=book)
    assert out["repaired"] == 0
    assert db.live_trades.rows[0]["realized_pnl"] is None


def test_an_unreachable_older_trade_is_left_alone_not_guessed():
    """The broker trade book is DAY-SCOPED, so an August trade has no recoverable
    fills. It must stay null rather than borrow today's prices."""
    db = _DB([_doc(norenordno="26081400076294", noren_tsym="NIFTY18AUG26P24300",
                   quantity=65, entry_fill_price=61.7)])
    out = _repair(db)
    assert out["repaired"] == 0 and out["unresolved"] == 1
    assert db.live_trades.rows[0]["realized_pnl"] is None


def test_a_carried_forward_position_blocks_the_repair():
    """An NRML position bought YESTERDAY on this contract is invisible to today's
    trade book, and a SELL that closed it would be attributed to today's entry.
    The position book shows the carry; the repair must refuse."""
    carried = [{"tsym": TSYM, "netqty": "0", "cfbuyqty": "100", "cfsellqty": "0"}]
    db = _DB([_doc()])
    out = _repair(db, position_book=carried)
    assert out["repaired"] == 0 and db.live_trades.rows[0]["realized_pnl"] is None


def test_without_a_position_book_nothing_is_provable():
    db = _DB([_doc()])
    assert _repair(db, position_book=None)["repaired"] == 0


def test_a_write_that_lands_nowhere_is_not_counted_as_repaired():
    """A concurrent close journalled a real number between the read and the write:
    the null-filtered write matches nothing, and theirs stands."""
    db = _DB([_doc()])

    async def _raced(flt, upd):
        return type("R", (), {"modified_count": 0, "matched_count": 0})()
    db.live_trades.update_one = _raced
    out = _repair(db)
    assert out["repaired"] == 0 and out["raced"] == 1


def test_the_repair_reuses_a_trade_book_it_is_handed():
    """reconcile_on_startup reads the book once and shares it."""
    class _NoFetch:
        async def trade_book(self):
            raise AssertionError("re-fetched the trade book")
    db = _DB([_doc()])
    out = asyncio.run(_repair_missing_realized(db, _NoFetch(), trade_book=REAL_BOOK,
                                               position_book=FLAT_BOOK))
    assert out["repaired"] == 1


def test_the_repair_and_a_live_close_journal_IDENTICAL_fields():
    """One formula, not two. The repair used to carry its own copy of
    close_live_trade's P&L + costing; a divergence would mean the same trade
    journals different money depending on which path closed it."""
    from app.live.close_loop import close_live_trade
    from tests.test_reboot_reconcile import FakeDB  # supports $ne + find_one

    repaired = _DB([_doc()])
    _repair(repaired)

    m = _close(REAL_BOOK)
    closed = FakeDB()
    closed.live_trades.rows.append(_doc(status="OPEN"))
    assert asyncio.run(close_live_trade(closed, norenordno=ENTRY, exit_price=None,
                                        fill_price=m["exit_px"], entry_px=m["entry_px"],
                                        quantity=m["qty"], exit_reason="t"))

    a, b = repaired.live_trades.rows[0], closed.live_trades.rows[0]
    for k in ("exit_price", "realized_pnl", "total_charges", "net_realized_pnl",
              "charges", "entry_basis_price"):
        assert a[k] == b[k], k


def test_an_empty_or_failed_trade_book_is_a_no_op():
    db = _DB([_doc()])
    assert _repair(db, book=[])["repaired"] == 0

    class _Boom:
        async def trade_book(self): raise RuntimeError("broker down")
    assert asyncio.run(_repair_missing_realized(
        db, _Boom(), position_book=FLAT_BOOK))["repaired"] == 0
    assert db.live_trades.rows[0]["realized_pnl"] is None
