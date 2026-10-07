"""The KNOWN-book never-filled age-out must not drop on the cancel REQUEST.

An entry the position book never shows (the book is KNOWN — other scrips are
live) aged out after `max_pending_misses` cycles by sending a cancel for the
entry order and de-registering it IN THE SAME CYCLE, cancelling its OCO and
journalling `never_filled` on the way out. A cancel request is not a cancel: if
the order filled before the broker processed it, that fill was a real-money
position with no software guard and no OCO, journalled as never filled.

The flat-account path (`_resolve_pending_on_empty_book`, 3f34fdd) already had
the right contract; the known-book path now shares it:
  * still working → cancel it, but KEEP guarding it until the ORDER BOOK
    confirms it dead (a fill can race the cancel);
  * terminal with nothing filled (CANCELED / REJECTED, any spelling) → age out
    and journal never_filled exactly once — no cancel for an already-dead order;
  * any fill at all, an unreadable order book, or the order not visible → HOLD.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from tests.test_guard_flat_account_ageout import (  # noqa: E402
    _ORD, _Clock, _cycles_until, _row,
)
from tests.test_live_position_guard import _TSYM, _Recorder, run  # noqa: E402
from app.live.broker_protocol import BrokerReadError  # noqa: E402
from app.live.live_position_guard import (  # noqa: E402
    LiveMonitorRegistry, LivePositionGuard,
)
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402

_OTHER = {"tsym": "SOMEOTHER26JUN100CE", "exch": "BFO", "netqty": "50",
          "lp": "10", "urmtom": "0"}


class _KnownBookClient:
    """Position book is KNOWN (an unrelated live position) and does not show the
    pending entry; the order book is scripted. A cancel REQUEST only records it —
    the order's status moves when the test says the broker processed it."""

    def __init__(self, order_rows=None, *, order_book_error=None):
        self.positions = [dict(_OTHER)]
        self.order_rows = list(order_rows or [])
        self.order_book_error = order_book_error
        self.order_book_reads = 0
        self.cancelled = []

    async def position_book(self):
        return list(self.positions)

    async def order_book(self):
        self.order_book_reads += 1
        if self.order_book_error is not None:
            raise self.order_book_error
        return list(self.order_rows)

    async def cancel_order(self, ordno):
        self.cancelled.append(ordno)
        return {"stat": "Ok"}


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

    clock, rec = _Clock(), _Recorder()
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=rec.square_fn, on_expire=on_expire,
                          now_fn=clock)
    return reg, g, clock, expired, rec


def test_cancel_request_alone_never_drops_the_entry():
    client = _KnownBookClient([_row("OPEN")])
    reg, g, clock, expired, _ = _setup(client)
    _cycles_until(g, clock, 61)                  # past the 40-miss grace
    assert client.cancelled == [_ORD], "the working entry was never cancelled"
    assert len(reg) == 1, (
        "dropped on the cancel REQUEST — a fill racing the cancel would be an "
        "unguarded live position")
    assert expired == [], "journalled never_filled before the cancel was confirmed"


def test_a_fill_racing_the_cancel_is_still_guarded():
    client = _KnownBookClient([_row("OPEN")])
    reg, g, clock, expired, rec = _setup(client)
    _cycles_until(g, clock, 61)                  # cancel requested
    # The broker filled it before it processed the cancel; the position book
    # has not caught up yet — still not proof of anything.
    client.order_rows = [_row("COMPLETE", "65")]
    _cycles_until(g, clock, 10)
    assert len(reg) == 1 and expired == []
    # The position lands, already through the 140 stop: the guard must act on it.
    client.positions.append({"tsym": _TSYM, "exch": "BFO", "netqty": "65",
                             "lp": "100", "urmtom": "-6500"})
    run(g._cycle())
    assert reg.get(_ORD)["seen_filled"] is True
    assert rec.squared and rec.squared[0][0] == _TSYM, (
        "the racing fill is registered but its stop never fired")
    assert expired == []


def test_confirmed_cancel_ages_out_and_journals_never_filled_once():
    client = _KnownBookClient([_row("OPEN")])
    reg, g, clock, expired, _ = _setup(client)
    _cycles_until(g, clock, 61)
    client.order_rows = [_row("CANCELED")]       # the broker processed the cancel
    _cycles_until(g, clock, 30)
    assert expired == [(_ORD, "never_filled")]
    assert len(reg) == 0
    assert client.cancelled == [_ORD], "the cancel was re-sent"


def test_dead_entry_ages_out_without_spending_a_cancel():
    """A REJECTED / already-CANCELED order can never fill: age it out at once and
    do not spend the shared per-key ORDER budget cancelling it."""
    for status in ("REJECTED", "REJECT", "Rejected", "CANCELED", "CANCELLED"):
        client = _KnownBookClient([_row(status)])
        reg, g, clock, expired, _ = _setup(client)
        _cycles_until(g, clock, 61)
        assert expired == [(_ORD, "never_filled")], status
        assert len(reg) == 0, status
        assert client.cancelled == [], status


def test_any_fill_is_never_aged_out():
    for row in (_row("COMPLETE", "65"), _row("CANCELED", "20"), _row("OPEN", "10")):
        client = _KnownBookClient([row])
        reg, g, clock, expired, _ = _setup(client)
        _cycles_until(g, clock, 120)
        assert expired == [], row
        assert len(reg) == 1, row


def test_unreadable_order_book_holds():
    client = _KnownBookClient(order_book_error=BrokerReadError(
        "Session Expired : Invalid Session Key", route="OrderBook"))
    reg, g, clock, expired, _ = _setup(client)
    _cycles_until(g, clock, 120)
    assert client.order_book_reads >= 1
    assert expired == [] and client.cancelled == []
    assert len(reg) == 1


def test_order_not_visible_in_the_book_holds():
    client = _KnownBookClient([])
    reg, g, clock, expired, _ = _setup(client)
    _cycles_until(g, clock, 120)
    assert expired == [] and client.cancelled == []
    assert len(reg) == 1


def test_order_book_reads_are_rate_bounded():
    client = _KnownBookClient([_row("OPEN", "10")])   # filled → held forever
    reg, g, clock, expired, _ = _setup(client)
    _cycles_until(g, clock, 60 + 60)
    assert 1 <= client.order_book_reads <= 15, client.order_book_reads


def test_one_order_book_read_serves_every_pending_entry_in_a_cycle():
    second = "N-PENDING-2"
    client = _KnownBookClient([_row("OPEN"),
                               {**_row("OPEN"), "norenordno": second,
                                "tsym": "SENSEX26JUN77000CE"}])
    reg, g, clock, expired, _ = _setup(client)
    reg.register(key=second, tsym="SENSEX26JUN77000CE", exch="BFO", qty=65,
                 prd="I", entry_price=150.0,
                 state=build_monitor_state(150.0, stop_pct=30), source="auto_live")
    _cycles_until(g, clock, 61)
    assert sorted(client.cancelled) == sorted([_ORD, second])
    assert client.order_book_reads == 1, client.order_book_reads
