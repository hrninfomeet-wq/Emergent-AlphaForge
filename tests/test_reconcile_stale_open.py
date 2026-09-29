"""A position the broker closed while AlphaForge was down must not stay OPEN forever.

2026-09-16, real money: the PC went down at 13:21 IST holding a 3-lot SENSEX 17 SEP
74400 PE (norenordno 26091600196209); the operator squared it the same day. Eleven
days later the `live_trades` doc was still OPEN — counting toward `max_concurrent`
on its deployment and reporting a position that did not exist.

Every restart since had met either an expired daily token (unreadable book →
UNKNOWN, correctly) or a FLAT account, whose position book Noren answers with "no
data" → []. `reconcile_on_startup` treated an empty book as UNKNOWN too, so the doc
could never close, and recovery reported INCOMPLETE and re-ran every supervisor
tick, all day, on the shared broker rate budget.

The fix closes only on PROOF:
  * a CONFIRMED-empty book (two consecutive empty reads — the guard's own
    `flat_confirm_reads = 2`) closes docs entered on an EARLIER IST day;
  * an unreadable book falls back to CALENDAR proof: an expired contract;
  * no price is ever fabricated — the exit fill has left the day-scoped book.
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
from tests.test_reboot_reconcile import FakeClient, FakeDB  # noqa: E402

# 2026-09-28 04:00 UTC = Monday 09:30 IST — the next session after the stale doc.
MONDAY = datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)

# A contract that has NOT expired by MONDAY (08 OCT 26), so a test of the
# prior-day / same-day rules is not decided by the calendar rule instead.
LIVE_TSYM = "SENSEX26O0876000PE"


@pytest.fixture(autouse=True)
def _no_confirm_delay(monkeypatch):
    monkeypatch.setattr("app.live.reboot_reconcile._CONFIRM_READ_DELAY_S", 0)


def _stale_doc(**kw):
    """The real stale doc's identifying fields, verbatim from Mongo."""
    d = {
        "id": "f5fafab2-0e4b-4e61-9e84-01d54120d8fc",
        "norenordno": "26091600196209",
        "noren_tsym": "SENSEX2691774400PE",
        "trading_symbol": "SENSEX 74400 PE 17 SEP 26",
        "exch": "BFO",
        "quantity": 60,
        "entry_price": 367.95,
        "entry_fill_price": 367.22,
        "status": "OPEN",
        "realized_pnl": None,
        "unrealized_pnl": -853,
        "created_at": "2026-09-16T06:45:03.570662+00:00",
        "source": "auto_live_on_signal",
    }
    d.update(kw)
    return d


class _SeqClient(FakeClient):
    """Position book answers from a SEQUENCE — each read takes the next entry; an
    Exception entry is raised. Models an empty read followed by a different one."""

    def __init__(self, books, **kw):
        super().__init__(**kw)
        self._books = list(books)
        self.position_reads = 0

    async def position_book(self):
        self.position_reads += 1
        nxt = self._books.pop(0) if self._books else []
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class _Unreadable(FakeClient):
    async def position_book(self):
        raise RuntimeError('Flattrade PositionBook HTTP 401: {"stat":"Not_Ok",'
                           '"emsg":"Session Expired :  Invalid Session Key"}')


def _run(db, client, now=MONDAY):
    return asyncio.run(reconcile_on_startup(db, client, now_utc=now))


# --------------------------------------------------------------------------- #
# Confirmed-empty book
# --------------------------------------------------------------------------- #

def test_the_real_stale_doc_closes_on_a_confirmed_flat_account():
    """THE regression: stayed OPEN from 2026-09-16 to 09-27. Its contract expired
    on 09-17, so on a flat book the calendar proof closes it first."""
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc())
    out = _run(db, _SeqClient([[], []]))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED"
    assert doc["exit_reason"] == "reconciled_expired"
    assert out["status"] == "flat_confirmed"
    assert out["closed"] == 1


def test_a_prior_day_doc_on_a_LIVE_contract_closes_on_a_confirmed_flat_account():
    """The same situation on a contract that has not expired: only the
    confirmed-empty book can prove it gone."""
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc(noren_tsym=LIVE_TSYM))
    out = _run(db, _SeqClient([[], []]))
    doc = db.live_trades.rows[0]
    assert doc["exit_reason"] == "reconciled_flat_prior_day"
    assert out["status"] == "flat_confirmed"


def test_a_stale_close_is_kept_out_of_TODAYS_realized_pnl():
    """Its closed_at is the moment it was NOTICED. Counted as today's P&L - or a
    price backfilled onto it later - it would trip today's day-stop for a
    deployment that traded nothing today."""
    from app.deployment_kill_switch import daily_realized_summary

    db = FakeDB()
    db.live_trades.rows.extend([_stale_doc(norenordno="A"),
                                _stale_doc(norenordno="B", noren_tsym=LIVE_TSYM)])
    _run(db, _SeqClient([[], []]))
    assert all(r["exit_day_unknown"] is True for r in db.live_trades.rows)
    rows = [dict(r, realized_pnl=-5000.0, closed_at=MONDAY.isoformat())
            for r in db.live_trades.rows]
    assert daily_realized_summary(rows, "2026-09-28")["net"] == 0.0


def test_no_price_is_fabricated_for_it():
    """The exit fill is gone from the day-scoped trade book; the last persisted
    MARK (unrealized_pnl -853) is not a fill and must not become one."""
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc())
    _run(db, _SeqClient([[], []]))
    doc = db.live_trades.rows[0]
    assert doc["realized_pnl"] is None
    assert "exit_price" not in doc


def test_a_flat_account_is_a_COMPLETE_answer_not_unknown():
    """It used to report unknown_position_book, which the runtime reads as
    INCOMPLETE and retries every tick."""
    db = FakeDB()
    assert _run(db, _SeqClient([[], []]))["status"] == "flat_confirmed"


def test_one_empty_read_is_not_enough():
    """A second read that is NOT empty means the first was not the truth — and it
    is the NEWER read, so it becomes the book. Here it says HELD, so neither the
    empty-book rule nor the calendar may close the doc."""
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc())
    held = [{"tsym": "SENSEX2691774400PE", "netqty": "60"}]
    out = _run(db, _SeqClient([[], held]))
    assert db.live_trades.rows[0]["status"] == "OPEN"
    assert out["status"] == "ok"


def test_a_non_empty_confirming_read_runs_the_normal_close():
    """…and when the newer read shows the contract flat, the ordinary phase-2
    close applies (exit_reason reconciled_closed, not a stale-doc reason)."""
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc())
    other = [{"tsym": "NIFTY29SEP26P24300", "netqty": "65"}]
    _run(db, _SeqClient([[], other]))
    assert db.live_trades.rows[0]["exit_reason"] == "reconciled_closed"


def test_a_failed_confirming_read_is_unknown():
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc(noren_tsym=LIVE_TSYM))
    out = _run(db, _SeqClient([[], RuntimeError("401 Session Expired")]))
    assert db.live_trades.rows[0]["status"] == "OPEN"
    assert out["status"] == "unknown_position_book"


def test_it_really_reads_the_book_twice():
    client = _SeqClient([[], []])
    _run(FakeDB(), client)
    assert client.position_reads == 2


def test_a_doc_entered_TODAY_is_never_closed_by_an_empty_book():
    """Its entry order may still be WORKING (the doc is written on broker
    acceptance) and can fill a second later - an empty book beside a same-day doc
    is a contradiction, so it stays OPEN, and recovery must NOT report complete:
    a latched recovery would never re-attach the guard once the order fills."""
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc(noren_tsym=LIVE_TSYM,
                                          created_at="2026-09-28T03:50:00+00:00"))
    out = _run(db, _SeqClient([[], []]))
    assert db.live_trades.rows[0]["status"] == "OPEN"
    assert out["status"] == "unknown_position_book"


def test_the_ist_day_boundary_is_used_not_utc():
    """2026-09-27T20:00Z is already 01:30 IST on the 28th — the SAME IST day as
    MONDAY. Comparing UTC dates would call it a prior day and close it."""
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc(noren_tsym=LIVE_TSYM,
                                          created_at="2026-09-27T20:00:00+00:00"))
    _run(db, _SeqClient([[], []]))
    assert db.live_trades.rows[0]["status"] == "OPEN"


def test_an_undatable_doc_is_left_open_and_recovery_stays_incomplete():
    db = FakeDB()
    db.live_trades.rows.extend([
        _stale_doc(norenordno="A", noren_tsym=LIVE_TSYM, created_at=None),
        _stale_doc(norenordno="B", noren_tsym=LIVE_TSYM, created_at="not a date"),
        # a naive string states no zone - refused rather than guessed
        _stale_doc(norenordno="C", noren_tsym=LIVE_TSYM,
                   created_at="2026-09-16T06:45:03"),
    ])
    out = _run(db, _SeqClient([[], []]))
    assert [r["status"] for r in db.live_trades.rows] == ["OPEN", "OPEN", "OPEN"]
    assert out["status"] == "unknown_position_book"


def test_an_undatable_doc_on_an_EXPIRED_contract_still_closes_on_a_flat_book():
    """Calendar proof holds whatever the book says. It used to run only on an
    UNREADABLE book, so the stronger evidence (a confirmed-flat book) closed less."""
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc(created_at="2026-09-16T06:45:03"))
    out = _run(db, _SeqClient([[], []]))
    assert db.live_trades.rows[0]["exit_reason"] == "reconciled_expired"
    assert out["status"] == "flat_confirmed"


def test_an_empty_book_never_cancels_a_resting_oco():
    """Closing a journal row is reversible bookkeeping; cancelling a broker order
    is not. The OCO phases stay skipped on an empty book."""
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc())
    client = _SeqClient([[], []], gtt_book=[
        {"al_id": "ALx", "tsym": "SENSEX2691774400PE",
         "remarks": "oco:26091600196209"}])
    _run(db, client)
    assert client.cancelled == []


def test_a_closed_doc_is_untouched():
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc(status="CLOSED", realized_pnl=822.0,
                                          exit_reason="reconciled_closed"))
    _run(db, _SeqClient([[], []]))
    assert db.live_trades.rows[0]["realized_pnl"] == 822.0
    assert db.live_trades.rows[0]["exit_reason"] == "reconciled_closed"


# --------------------------------------------------------------------------- #
# Unreadable book → calendar proof only
# --------------------------------------------------------------------------- #

def test_an_expired_contract_closes_even_while_the_broker_is_unreadable():
    """The token expired on 09-16 and every restart since was a 401. An option past
    its expiry date cannot be held, and that needs no broker to establish."""
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc())  # SENSEX 17 SEP 26
    out = _run(db, _Unreadable())
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED"
    assert doc["exit_reason"] == "reconciled_expired"
    assert doc["realized_pnl"] is None and "exit_price" not in doc
    assert out["status"] == "unknown_position_book", "recovery is still incomplete"


@pytest.mark.parametrize("tsym,expect", [
    ("SENSEX2692874400PE", "OPEN"),     # expires TODAY (28 SEP) — still trades
    ("SENSEX26O0176000CE", "OPEN"),     # 01 OCT — not expired
    ("NIFTY29SEP26P24300", "OPEN"),     # NFO, 29 SEP — not expired
    ("NIFTY18AUG26P24300", "CLOSED"),   # NFO, 18 AUG — expired
    ("GARBAGE", "OPEN"),                # unparseable — never guessed
    ("", "OPEN"),                       # legacy doc without noren_tsym
])
def test_only_a_provably_expired_contract_is_closed(tsym, expect):
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc(noren_tsym=tsym))
    _run(db, _Unreadable())
    assert db.live_trades.rows[0]["status"] == expect


def test_an_unconfirmed_empty_book_still_applies_the_calendar():
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc())
    _run(db, _SeqClient([[], RuntimeError("hiccup")]))
    assert db.live_trades.rows[0]["exit_reason"] == "reconciled_expired"


def test_a_held_position_is_never_closed_by_the_calendar():
    """A readable, non-empty book is the authority: if the broker says HELD, the
    doc stays OPEN whatever the symbol parser thinks of the expiry."""
    db = FakeDB()
    db.live_trades.rows.append(_stale_doc())
    client = FakeClient(position_book=[{"tsym": "SENSEX2691774400PE", "netqty": "60"}])
    _run(db, client)
    assert db.live_trades.rows[0]["status"] == "OPEN"


# --------------------------------------------------------------------------- #
# The runtime must treat flat_confirmed as COMPLETE
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("status,complete", [
    ("flat_confirmed", True),
    ("ok", True),
    ("unknown_position_book", False),
])
def test_recovery_completeness_follows_the_reconcile_status(monkeypatch, status, complete):
    from tests.test_premium_momentum_recovery import _DB, _Locks, _Reg, _wire
    import app.runtime as rt

    events = []
    _wire(monkeypatch, book=[], db=_DB(_Locks(), []), reg=_Reg(events=events),
          events=events)

    async def _reconcile(_db, _client):
        return {"status": status}

    monkeypatch.setattr("app.live.reboot_reconcile.reconcile_on_startup", _reconcile)
    assert asyncio.run(rt.live_startup_recovery()) is complete


def test_a_same_day_REJECTED_entry_closes_on_a_flat_book_and_recovery_completes():
    """Its entry order ended unfilled, so nothing can fill later: close it
    never_filled instead of letting it hold a concurrency slot all session."""
    class _Orders(_SeqClient):
        async def order_book(self):
            return [{"norenordno": "26092800000001", "status": "REJECTED"}]

    db = FakeDB()
    db.live_trades.rows.append(_stale_doc(norenordno="26092800000001",
                                          noren_tsym=LIVE_TSYM,
                                          created_at="2026-09-28T03:50:00+00:00"))
    out = _run(db, _Orders([[], []]))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED" and doc["exit_reason"] == "never_filled"
    assert out["status"] == "flat_confirmed"


def test_a_same_day_WORKING_entry_stays_open_and_recovery_stays_incomplete():
    class _Orders(_SeqClient):
        async def order_book(self):
            return [{"norenordno": "26092800000001", "status": "OPEN"}]

    db = FakeDB()
    db.live_trades.rows.append(_stale_doc(norenordno="26092800000001",
                                          noren_tsym=LIVE_TSYM,
                                          created_at="2026-09-28T03:50:00+00:00"))
    out = _run(db, _Orders([[], []]))
    assert db.live_trades.rows[0]["status"] == "OPEN"
    assert out["status"] == "unknown_position_book"
