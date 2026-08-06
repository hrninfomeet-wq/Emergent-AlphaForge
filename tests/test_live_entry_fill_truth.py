"""A live trade's P&L must be measured from the price the account actually paid.

`auto_live` stores `entry_price = float(ref_ltp)` — the reference premium resolved
to BAND the limit order. That is an INTENT, not a fill. `close_loop` then computes
`realized_pnl = qty * (exit - entry_price)` against it, so entry slippage was
structurally unmeasurable and every live rupee was measured from a price the
account never paid.

The broker's true average IS available. Two candidate sources:

  * `live_orders.avgprc` — exact per order, but populated by `order_sm.apply_om`,
    whose only entrypoint (`engine.on_om`) has NO production caller. Nothing feeds
    Noren order updates into the app, so in production that field stays empty.
  * the POSITION BOOK's `daybuyavgprc` — the day's average BUY price for the
    contract. Long-only entries (the executor refuses sells), so the buy average
    IS the entry. The guard already reads this book every ~1.5s, so capturing it
    costs no additional broker call.

Caveat recorded deliberately: `daybuyavgprc` is the day's average across ALL buys
of that contract. With one position per contract per day — the normal case, and
the only case the caps permit at max_concurrent 1 — it is exact. A second entry on
the same contract the same day blends them, so the capture is one-shot: the FIRST
observation wins and is never overwritten.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from tests.test_live_position_guard import (  # noqa: E402
    _FakeClient, _Recorder, _NOW, _TSYM, run,
)
from app.live.live_position_guard import (  # noqa: E402
    LiveMonitorRegistry, LivePositionGuard,
)
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402


async def _aw(v):
    return v


def _guard(reg, client, marks):
    async def on_mark(payload):
        marks.append(payload)
    return LivePositionGuard(
        registry=reg, client_factory=lambda: _aw(client),
        square_fn=_Recorder().square_fn, on_mark=on_mark, now_fn=lambda: _NOW)


def _registered(reg, entry=34.15):
    reg.register(key="N1", tsym=_TSYM, exch="BFO", qty=65, prd="I",
                 entry_price=entry, state=build_monitor_state(entry, stop_pct=50),
                 source="auto_live")


def _pos(**over):
    p = {"tsym": _TSYM, "exch": "BFO", "netqty": "65", "lp": "33.00",
         "urmtom": "-74.75", "daybuyavgprc": "34.00", "daybuyqty": "65"}
    p.update(over)
    return p


def test_mark_carries_the_broker_true_entry_fill():
    reg = LiveMonitorRegistry(); _registered(reg); marks = []
    run(_guard(reg, _FakeClient([_pos()]), marks)._cycle())
    m = [x for b in marks for x in b][0]
    assert m["entry_fill_price"] == 34.00, (
        "the guard reads daybuyavgprc every cycle and must surface it — without "
        "it every live rupee is measured from a price never paid")


def test_absent_buy_average_yields_no_fill_claim():
    """Never fabricate a fill: absent means absent, not zero."""
    reg = LiveMonitorRegistry(); _registered(reg); marks = []
    p = _pos(); p.pop("daybuyavgprc")
    run(_guard(reg, _FakeClient([p]), marks)._cycle())
    m = [x for b in marks for x in b][0]
    assert m.get("entry_fill_price") is None


def test_zero_or_junk_buy_average_is_rejected():
    reg = LiveMonitorRegistry(); _registered(reg); marks = []
    run(_guard(reg, _FakeClient([_pos(daybuyavgprc="0")]), marks)._cycle())
    m = [x for b in marks for x in b][0]
    assert m.get("entry_fill_price") is None, "0 is not a real fill price"


def test_marking_still_works_without_a_buy_average():
    """The P&L mark must not depend on the fill capture."""
    reg = LiveMonitorRegistry(); _registered(reg); marks = []
    p = _pos(); p.pop("daybuyavgprc")
    run(_guard(reg, _FakeClient([p]), marks)._cycle())
    m = [x for b in marks for x in b][0]
    assert m["unrealized_pnl"] == -74.75


def test_the_persist_hook_never_overwrites_a_captured_fill():
    """One-shot: daybuyavgprc blends a same-day second entry on the contract, so
    the FIRST observation is authoritative."""
    src = (Path(__file__).resolve().parents[1]
           / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")
    hook = src[src.index("async def _live_guard_on_mark"):
               src.index("async def _live_guard_on_expire")]
    assert "entry_fill_price" in hook
    assert "$exists" in hook or "entry_fill_price\": None" in hook, (
        "the fill write must be guarded so a later blended average cannot "
        "overwrite the first true capture")


# --- the close must MEASURE from the fill -----------------------------------

def _close_db(doc):
    class _Col:
        def __init__(self): self.updated = {}
        async def find_one(self, flt, proj=None): return dict(doc)
        async def update_one(self, flt, upd):
            self.updated = upd.get("$set", {})
            class _R: modified_count = 1
            return _R()
    class _DB:
        def __init__(self): self.live_trades = _Col()
    return _DB()


def test_realized_pnl_is_measured_from_the_fill_not_the_reference():
    """Entry ref 34.15, TRUE fill 34.00, exit 33.45 on 325 qty.

    Against the reference: 325 * (33.45 - 34.15) = -227.50
    Against the true fill: 325 * (33.45 - 34.00) = -178.75  <- what actually happened
    """
    import asyncio
    from app.live.close_loop import close_live_trade
    db = _close_db({
        "norenordno": "N1", "status": "OPEN", "quantity": 325,
        "entry_price": 34.15, "entry_fill_price": 34.00,
    })
    asyncio.run(close_live_trade(db, norenordno="N1", exit_price=33.45,
                                 exit_reason="stop"))
    assert db.live_trades.updated["realized_pnl"] == -178.75


def test_realized_pnl_falls_back_to_entry_price_when_no_fill_captured():
    """No regression for a trade the guard never marked."""
    import asyncio
    from app.live.close_loop import close_live_trade
    db = _close_db({
        "norenordno": "N1", "status": "OPEN", "quantity": 325,
        "entry_price": 34.15,
    })
    asyncio.run(close_live_trade(db, norenordno="N1", exit_price=33.45,
                                 exit_reason="stop"))
    assert db.live_trades.updated["realized_pnl"] == round(325 * (33.45 - 34.15), 2)
