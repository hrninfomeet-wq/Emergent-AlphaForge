"""An unfilled entry must age out on a FLAT account too (audit 2026-10-07 #2).

The never-filled age-out advances `misses` only on a KNOWN (non-empty) position
book. Before the day's first fill the account is flat, `position_book()` is `[]`,
and `[]` is deliberately read as UNKNOWN (never as flat) — so `misses` never
moved, the ~60 s never_filled cancel never fired, and a DAY BUY LMT the market
walked away from rested at the broker indefinitely: a stale signal that could
fill hours later, at a price nobody chose.

The position book cannot answer "did it fill?" here, so the ORDER BOOK must,
on a wall-clock deadline:
  * still working past the deadline → cancel it, but KEEP guarding it until the
    book confirms it dead (a fill can race the cancel);
  * terminal with nothing filled (CANCELED / REJECTED, any spelling) → age out
    and journal never_filled;
  * any fill at all, an unreadable book, or the order not visible → HOLD.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from tests.test_live_position_guard import _NOW, _TSYM, _Recorder, run  # noqa: E402
from app.live.broker_protocol import BrokerReadError  # noqa: E402
from app.live.live_position_guard import (  # noqa: E402
    LiveMonitorRegistry, LivePositionGuard,
)
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402

_ORD = "N-PENDING"


class _FlatAccountClient:
    """Position book is EMPTY (flat account); the order book is scripted."""

    def __init__(self, order_rows=None, *, order_book_error=None):
        self.order_rows = list(order_rows or [])
        self.order_book_error = order_book_error
        self.order_book_reads = 0
        self.cancelled = []

    async def position_book(self):
        return []

    async def order_book(self):
        self.order_book_reads += 1
        if self.order_book_error is not None:
            raise self.order_book_error
        return list(self.order_rows)

    async def cancel_order(self, ordno):
        self.cancelled.append(ordno)
        return {"stat": "Ok"}


def _row(status, fillshares="0"):
    return {"norenordno": _ORD, "tsym": _TSYM, "status": status,
            "fillshares": fillshares, "qty": "65"}


class _Clock:
    def __init__(self):
        self.now = _NOW

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)

    def __call__(self):
        return self.now


async def _aw(v):
    return v


def _setup(client):
    reg = LiveMonitorRegistry()
    reg.register(key=_ORD, tsym=_TSYM, exch="BFO", qty=65, prd="I",
                 entry_price=200.0, state=build_monitor_state(200.0, stop_pct=30),
                 source="auto_live")
    expired = []

    async def on_expire(entry, reason):
        expired.append((entry.get("id"), reason))

    clock = _Clock()
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=_Recorder().square_fn, on_expire=on_expire,
                          now_fn=clock)
    return reg, g, clock, expired


def _cycles_until(g, clock, seconds, step=1.5):
    t = 0.0
    while t < seconds:
        run(g._cycle())
        clock.advance(step)
        t += step
    run(g._cycle())


def test_working_entry_on_flat_account_is_cancelled_after_the_deadline():
    client = _FlatAccountClient([_row("OPEN")])
    reg, g, clock, expired = _setup(client)
    _cycles_until(g, clock, 61)
    assert client.cancelled == [_ORD], (
        "a resting entry on a flat account was never cancelled — the DAY LMT "
        "rests at the broker indefinitely")
    assert len(reg) == 1, (
        "dropped on the cancel REQUEST — a fill racing the cancel would be "
        "an unguarded live position")
    assert expired == []


def test_nothing_happens_inside_the_grace_window():
    client = _FlatAccountClient([_row("OPEN")])
    reg, g, clock, expired = _setup(client)
    _cycles_until(g, clock, 45)
    assert client.cancelled == []
    assert client.order_book_reads == 0, "order book polled before the deadline"
    assert len(reg) == 1 and expired == []


def test_confirmed_cancel_ages_out_and_journals_never_filled():
    client = _FlatAccountClient([_row("OPEN")])
    reg, g, clock, expired = _setup(client)
    _cycles_until(g, clock, 61)
    assert client.cancelled == [_ORD]
    client.order_rows = [_row("CANCELED")]
    _cycles_until(g, clock, 10)
    assert expired == [(_ORD, "never_filled")]
    assert len(reg) == 0


def test_rejected_entry_ages_out_whatever_the_spelling():
    for status in ("REJECTED", "REJECT", "Rejected", "CANCELLED"):
        client = _FlatAccountClient([_row(status)])
        reg, g, clock, expired = _setup(client)
        _cycles_until(g, clock, 61)
        assert expired == [(_ORD, "never_filled")], status
        assert len(reg) == 0, status


def test_any_fill_is_never_aged_out():
    """Partial-then-cancelled and COMPLETE both mean a position exists — the
    empty position book is the anomaly, not proof of nothing."""
    for row in (_row("COMPLETE", "65"), _row("CANCELED", "20"), _row("OPEN", "10")):
        client = _FlatAccountClient([row])
        reg, g, clock, expired = _setup(client)
        _cycles_until(g, clock, 120)
        assert expired == [], row
        assert len(reg) == 1, row


def test_unreadable_order_book_holds():
    client = _FlatAccountClient(order_book_error=BrokerReadError(
        "Session Expired : Invalid Session Key", route="OrderBook"))
    reg, g, clock, expired = _setup(client)
    _cycles_until(g, clock, 120)
    assert client.order_book_reads >= 1
    assert expired == [] and client.cancelled == []
    assert len(reg) == 1


def test_order_not_visible_in_the_book_holds():
    client = _FlatAccountClient([])
    reg, g, clock, expired = _setup(client)
    _cycles_until(g, clock, 120)
    assert expired == [] and client.cancelled == []
    assert len(reg) == 1


def test_order_book_reads_are_rate_bounded():
    """Past the deadline the order book is re-checked periodically, not on
    every ~1.5 s guard cycle (the read budget is shared per API key)."""
    client = _FlatAccountClient([_row("OPEN", "10")])   # filled → held forever
    reg, g, clock, expired = _setup(client)
    _cycles_until(g, clock, 60 + 60)
    assert 1 <= client.order_book_reads <= 15, client.order_book_reads
