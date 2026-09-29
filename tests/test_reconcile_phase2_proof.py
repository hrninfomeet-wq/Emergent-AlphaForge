"""Phase 2 of the reboot reconcile — closing OPEN docs on a readable, non-empty book.

Findings of the 2026-09-29 adversarial review, each pinned here:

  * a doc entered TODAY with no fill yet is not flat — its entry order may still
    be working (the doc is written on broker ACCEPTANCE) and can fill a second
    later. Closing it freed a max_concurrent slot and left a real position with
    no journal row and no guard;
  * a position CARRIED FORWARD on the contract is invisible to the day-scoped
    trade book, so an untagged SELL cannot be attributed without the position
    book's proof of zero carry;
  * a prior-day doc closed today has no known exit day, and must not count toward
    TODAY's realized P&L;
  * the orphan-OCO sweep looked up open docs by the UPSTOX symbol, which never
    equals the Noren symbol it had in hand.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.reboot_reconcile import reconcile_on_startup  # noqa: E402
from tests.test_reboot_reconcile import FakeClient, FakeDB, _open_doc  # noqa: E402

TSYM = "NIFTY24X25000CE"
NOW = datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc)     # 10:30 IST
TODAY_ENTRY = "2026-09-29T04:30:00+00:00"                  # 10:00 IST today
PRIOR_ENTRY = "2026-09-25T04:30:00+00:00"


@pytest.fixture(autouse=True)
def _no_confirm_delay(monkeypatch):
    monkeypatch.setattr("app.live.reboot_reconcile._CONFIRM_READ_DELAY_S", 0)


class _Client(FakeClient):
    def __init__(self, *, orders=None, **kw):
        super().__init__(**kw)
        self._orders = orders

    async def order_book(self):
        if isinstance(self._orders, Exception):
            raise self._orders
        return list(self._orders or [])


def _other_book(*extra):
    """A non-empty book that does not hold TSYM."""
    return [{"tsym": "BANKNIFTY24X50000CE", "netqty": "30"}, *extra]


def _flat_row(**kw):
    r = {"tsym": TSYM, "netqty": "0", "cfbuyqty": "0", "cfsellqty": "0"}
    r.update(kw)
    return r


def _run(db, client):
    return asyncio.run(reconcile_on_startup(db, client, now_utc=NOW))


# --------------------------------------------------------------------------- #
# A same-day entry with no fill yet
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("status", ["OPEN", "PENDING", "TRIGGER_PENDING", None])
def test_a_working_entry_order_is_left_open(status):
    db = FakeDB()
    db.live_trades.rows.append(_open_doc(created_at=TODAY_ENTRY))
    orders = [{"norenordno": "N1", "status": status}] if status else []
    out = _run(db, _Client(position_book=_other_book(), orders=orders))
    assert db.live_trades.rows[0]["status"] == "OPEN"
    assert out["close_detail"]["skipped_working"] == 1


@pytest.mark.parametrize("status", ["REJECTED", "CANCELED", "CANCELLED", "REJECT"])
def test_an_entry_that_ended_unfilled_closes_as_never_filled(status):
    db = FakeDB()
    db.live_trades.rows.append(_open_doc(created_at=TODAY_ENTRY))
    _run(db, _Client(position_book=_other_book(),
                     orders=[{"norenordno": "N1", "status": status}]))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED" and doc["exit_reason"] == "never_filled"
    assert doc["realized_pnl"] is None


def test_an_unreadable_order_book_leaves_it_open():
    db = FakeDB()
    db.live_trades.rows.append(_open_doc(created_at=TODAY_ENTRY))
    _run(db, _Client(position_book=_other_book(), orders=RuntimeError("401")))
    assert db.live_trades.rows[0]["status"] == "OPEN"


def test_the_order_book_is_read_once_per_run():
    reads = []

    class _Counting(_Client):
        async def order_book(self):
            reads.append(1)
            return []
    db = FakeDB()
    db.live_trades.rows.extend([_open_doc(norenordno="N1", created_at=TODAY_ENTRY),
                                _open_doc(norenordno="N2", created_at=TODAY_ENTRY)])
    _run(db, _Counting(position_book=_other_book()))
    assert len(reads) == 1


def test_a_same_day_entry_WITH_fills_still_closes_normally():
    """The working-order hold applies only when no fill exists; a filled-and-exited
    entry is closed and priced as before."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc(created_at=TODAY_ENTRY))
    book = [
        {"tsym": TSYM, "trantype": "B", "flprc": "100", "flqty": "65",
         "fltm": "29-09-2026 10:00:00", "norenordno": "N1"},
        {"tsym": TSYM, "trantype": "S", "flprc": "130", "flqty": "65",
         "fltm": "29-09-2026 10:20:00", "norenordno": "X1"},
    ]
    _run(db, _Client(position_book=_other_book(_flat_row()), trade_book=book))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED" and doc["realized_pnl"] == 65 * 30.0
    assert not doc.get("exit_day_unknown")


# --------------------------------------------------------------------------- #
# Carry-forward
# --------------------------------------------------------------------------- #

def test_a_carried_forward_position_blocks_the_price_but_not_the_close():
    """Yesterday's NRML position on the contract: today's book cannot see it, and
    its exit would be read as ours. The position book shows the carry, so the doc
    closes (it IS flat) — without a price."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc(created_at=TODAY_ENTRY))
    book = [
        {"tsym": TSYM, "trantype": "B", "flprc": "100", "flqty": "65",
         "fltm": "29-09-2026 10:00:00", "norenordno": "N1"},
        {"tsym": TSYM, "trantype": "S", "flprc": "70", "flqty": "65",
         "fltm": "29-09-2026 10:10:00", "norenordno": "X9"},   # the carried exit
        {"tsym": TSYM, "trantype": "S", "flprc": "130", "flqty": "65",
         "fltm": "29-09-2026 10:20:00", "norenordno": "X1"},
    ]
    _run(db, _Client(position_book=_other_book(_flat_row(cfbuyqty="65")),
                     trade_book=book))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED" and doc["realized_pnl"] is None


# --------------------------------------------------------------------------- #
# The exit day
# --------------------------------------------------------------------------- #

def test_a_prior_day_doc_closed_today_has_no_known_exit_day():
    db = FakeDB()
    db.live_trades.rows.append(_open_doc(created_at=PRIOR_ENTRY))
    _run(db, _Client(position_book=_other_book()))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED" and doc["exit_day_unknown"] is True


def test_a_same_day_close_without_a_price_keeps_a_known_exit_day():
    """Entered and exited today: the exit day IS today, even if the price could
    not be proven."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc(created_at=TODAY_ENTRY))
    book = [{"tsym": TSYM, "trantype": "B", "flprc": "100", "flqty": "65",
             "fltm": "29-09-2026 10:00:00", "norenordno": "N1"}]   # exit not in book
    _run(db, _Client(position_book=_other_book(_flat_row()), trade_book=book))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED" and not doc.get("exit_day_unknown")


# --------------------------------------------------------------------------- #
# Orphan-OCO sweep — Noren symbol space
# --------------------------------------------------------------------------- #

def test_an_unlinked_oco_is_kept_while_an_open_doc_holds_its_contract():
    """A same-day working entry stays OPEN; an unlinked resting OCO on its contract
    must survive the sweep. The sweep looked the doc up by the UPSTOX symbol, found
    nothing, and cancelled it."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc(created_at=TODAY_ENTRY))
    client = _Client(position_book=_other_book(),
                     orders=[{"norenordno": "N1", "status": "OPEN"}],
                     gtt_book=[{"al_id": "AL9", "tsym": TSYM, "remarks": ""}])
    _run(db, client)
    assert db.live_trades.rows[0]["status"] == "OPEN"
    assert client.cancelled == [], "cancelled the OCO of a still-open entry"
