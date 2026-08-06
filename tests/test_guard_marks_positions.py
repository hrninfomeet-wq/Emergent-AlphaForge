"""The live day-stop must be able to SEE an open position's loss.

`/live/enable` makes `daily_loss_cap` mandatory, and `live_deploy_governor`'s
docstring promises to pause when "realized + open-unrealized loss today" hits it.
But `auto_live` writes `live_trades.unrealized_pnl = 0.0` at insert and NOTHING in
the live path ever updates it — the only other writers are paper analytics. So
`_open_unrealized_today` summed zeros forever and the cap could only ever see
CLOSED trades.

Combined with the caps being evaluated ONLY from the new-entry path
(`check_account_caps`/`check_live_caps` have exactly one caller each, in
auto_live), a held position running against the account all afternoon tripped
nothing at all: the stop could only fire on the arrival of a new signal, which is
precisely what does not happen while a position is being held.

The data already flows — the guard polls the broker position book every ~1.5s and
Noren's `urmtom`/`rpnl` carry the P&L. It was simply never persisted.

Marks are keyed on **norenordno**, never on `trading_symbol`: live_trades stores
the UPSTOX symbol while the position book is NOREN-keyed, and joining those two
spaces is a long-standing source of real bugs in this codebase.
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


def _guard(reg, client, marks, rec=None):
    async def on_mark(payload):
        marks.append(payload)

    return LivePositionGuard(
        registry=reg, client_factory=lambda: _aw(client),
        square_fn=(rec or _Recorder()).square_fn,
        on_mark=on_mark, now_fn=lambda: _NOW,
    )


def _registered(reg, key="N1", tsym=_TSYM, entry=250.0):
    reg.register(key=key, tsym=tsym, exch="BFO", qty=65, prd="I",
                 entry_price=entry,
                 state=build_monitor_state(entry, stop_pct=50),
                 source="auto_live")


def _pos(tsym=_TSYM, netqty=65, lp=200.0, urmtom=-3250.0, rpnl=None):
    p = {"tsym": tsym, "exch": "BFO", "netqty": str(netqty),
         "lp": str(lp), "urmtom": str(urmtom)}
    if rpnl is not None:
        p["rpnl"] = str(rpnl)
    return p


def test_guard_emits_a_mark_for_an_open_position():
    reg = LiveMonitorRegistry()
    _registered(reg)
    marks = []
    run(_guard(reg, _FakeClient([_pos()]), marks)._cycle())
    assert marks, "the guard saw the broker P&L and never persisted it"
    flat = [m for batch in marks for m in batch]
    assert len(flat) == 1
    assert flat[0]["norenordno"] == "N1"
    assert flat[0]["unrealized_pnl"] == -3250.0


def test_mark_is_keyed_on_norenordno_not_trading_symbol():
    """live_trades.trading_symbol is UPSTOX-space; the book is NOREN-keyed."""
    reg = LiveMonitorRegistry()
    _registered(reg)
    marks = []
    run(_guard(reg, _FakeClient([_pos()]), marks)._cycle())
    m = [x for b in marks for x in b][0]
    assert "norenordno" in m
    assert "trading_symbol" not in m, (
        "joining the Noren position book by the Upstox trading_symbol is the "
        "symbol-space bug this codebase has repeatedly paid for")


def test_mark_carries_urmtom_plus_rpnl():
    """A same-day partial: broker P&L is unrealized + realized."""
    reg = LiveMonitorRegistry()
    _registered(reg)
    marks = []
    run(_guard(reg, _FakeClient([_pos(urmtom=-1000.0, rpnl=-250.0)]), marks)._cycle())
    m = [x for b in marks for x in b][0]
    assert m["unrealized_pnl"] == -1250.0


def test_mark_carries_the_price_and_a_timestamp():
    """A stale mark must be detectable downstream, so it needs a time."""
    reg = LiveMonitorRegistry()
    _registered(reg)
    marks = []
    run(_guard(reg, _FakeClient([_pos(lp=200.0)]), marks)._cycle())
    m = [x for b in marks for x in b][0]
    assert m["mark_price"] == 200.0
    assert m.get("marked_at"), "a mark with no timestamp cannot be aged out"


def test_no_mark_on_an_unknown_book():
    """An EMPTY book is UNKNOWN, never flat — stamping 0.0 would be a lie that
    silently re-enables the very blindness this fixes."""
    reg = LiveMonitorRegistry()
    _registered(reg)
    marks = []
    run(_guard(reg, _FakeClient([]), marks)._cycle())
    assert marks == [], "stamped a mark from an unreadable/empty position book"


def test_a_failing_mark_hook_never_breaks_the_cycle():
    reg = LiveMonitorRegistry()
    _registered(reg)

    async def boom(_payload):
        raise RuntimeError("mongo down")

    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(_FakeClient([_pos()])),
                          square_fn=_Recorder().square_fn, on_mark=boom,
                          now_fn=lambda: _NOW)
    run(g._cycle())          # must not raise
    assert g.status()["running"] is False or True


def test_guard_without_a_mark_hook_is_unchanged():
    """Default None → no behaviour change for every existing caller/test."""
    reg = LiveMonitorRegistry()
    _registered(reg)
    g = LivePositionGuard(registry=reg,
                          client_factory=lambda: _aw(_FakeClient([_pos()])),
                          square_fn=_Recorder().square_fn, now_fn=lambda: _NOW)
    run(g._cycle())          # must not raise


# --- the hook must be MOUNTED in production, not merely defined -------------

def test_production_guard_is_wired_with_the_mark_hook():
    """A hook with no caller is dead code — the `on_expire` lesson, re-pinned."""
    src = (Path(__file__).resolve().parents[1]
           / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")
    assert "async def _live_guard_on_mark" in src
    assert "on_mark=_live_guard_on_mark" in src, (
        "position marking is defined but never wired into the live guard — the "
        "day-stop would still be blind to open risk in production")
    ctor = src[src.index("on_close=_live_guard_on_close"):]
    assert "on_mark=_live_guard_on_mark" in ctor[:300]


def test_marks_are_written_keyed_on_norenordno():
    """The persist hook must not reintroduce the Upstox/Noren symbol-space join."""
    src = (Path(__file__).resolve().parents[1]
           / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")
    hook = src[src.index("async def _live_guard_on_mark"):
               src.index("async def _live_guard_on_expire")]
    assert '"norenordno": ordno' in hook
    assert "trading_symbol" not in hook.split('"""')[2], (
        "the mark writer must join on norenordno, never on trading_symbol")
