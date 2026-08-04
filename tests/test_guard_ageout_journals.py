"""An entry order that never fills must not consume a max_concurrent slot forever.

`auto_live` writes the live_trades row with status "OPEN" on broker ACCEPTANCE —
a Noren norenordno is an acceptance, not a fill, and the entry is a marketable
LMT that the market can simply walk away from.

When such an entry never appears in the position book, the guard ages it out
(`_max_pending_misses` cycles), best-effort cancels the order and its OCO, and
`self._registry.remove(...)`s it. It never journalled the row terminal, and
`close_live_trade` is only reached from `_finalize_flat` (a CONFIRMED-flat exit,
which never happens for a position that never existed) or from reboot reconcile
(boot only).

`live_deploy_governor` counts `status == "OPEN"` rows with NO date filter, so the
stale row counts forever. With max_concurrent 1-2 — the norm — one unfilled limit
order silently ends autonomous trading for that deployment, that day and every day
after, with no operator-visible reason and no self-recovery short of a restart.

The honest journal entry is `never_filled` with NO realized_pnl: nothing was
squared, and `close_live_trade` deliberately leaves the number untouched rather
than fabricating one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from tests.test_live_position_guard import (  # noqa: E402
    _FakeClient, _Recorder, _TSYM, run,
)
from app.live.live_position_guard import (  # noqa: E402
    LiveMonitorRegistry, LivePositionGuard,
)
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402


class _CancelClient(_FakeClient):
    def __init__(self, positions):
        super().__init__(positions)
        self.cancelled = []

    async def cancel_order(self, ordno):
        self.cancelled.append(ordno)
        return {"stat": "Ok"}


def _guard_with_expiry(reg, client, rec, expired, *, misses=2):
    async def on_expire(entry, reason):
        expired.append((entry.get("id"), reason))

    return LivePositionGuard(
        registry=reg,
        client_factory=lambda: _aw(client),
        square_fn=rec.square_fn,
        max_pending_misses=misses,
        on_expire=on_expire,
        now_fn=lambda: _NOW,
    )


async def _aw(v):
    return v


from tests.test_live_position_guard import _NOW  # noqa: E402,E402


def _other_position():
    """An unrelated live position, so the book is KNOWN (non-empty) while our
    pending entry is absent from it."""
    return {"tsym": "SOMEOTHER26JUN100CE", "exch": "BFO", "netqty": "50",
            "lp": "10", "urmtom": "0"}


def _register_pending(reg):
    reg.register(key="N-PENDING", tsym=_TSYM, exch="BFO", qty=65, prd="I",
                 entry_price=200.0,
                 state=build_monitor_state(200.0, stop_pct=30),
                 source="auto_live")


def test_never_filled_entry_fires_the_expiry_hook():
    """The row must be journalled terminal, not silently dropped."""
    reg = LiveMonitorRegistry()
    _register_pending(reg)
    # An AUTHENTICATED, NON-EMPTY book that simply does not contain our tsym.
    # An EMPTY book is deliberately UNKNOWN (never read as flat), so it would not
    # advance the age-out counter at all — modelling that would test nothing.
    client = _CancelClient([_other_position()])
    expired = []
    g = _guard_with_expiry(reg, client, _Recorder(), expired, misses=2)
    for _ in range(3):
        run(g._cycle())
    assert expired, (
        "aged-out entry was dropped from the registry without journalling the "
        "live_trades row — max_concurrent stays consumed forever")
    assert expired[0][0] == "N-PENDING"
    assert expired[0][1] == "never_filled"


def test_expiry_fires_exactly_once():
    """No double-journalling if extra cycles run after removal."""
    reg = LiveMonitorRegistry()
    _register_pending(reg)
    client = _CancelClient([_other_position()])
    expired = []
    g = _guard_with_expiry(reg, client, _Recorder(), expired, misses=2)
    for _ in range(6):
        run(g._cycle())
    assert len(expired) == 1


def test_entry_is_still_removed_and_cancelled():
    """No behaviour regression: the age-out still cancels and de-registers."""
    reg = LiveMonitorRegistry()
    _register_pending(reg)
    client = _CancelClient([_other_position()])
    g = _guard_with_expiry(reg, client, _Recorder(), [], misses=2)
    for _ in range(3):
        run(g._cycle())
    assert len(reg) == 0
    assert "N-PENDING" in client.cancelled


def test_a_filled_entry_never_expires():
    """No over-firing: an entry present in the book must not be journalled."""
    reg = LiveMonitorRegistry()
    _register_pending(reg)
    client = _CancelClient([{"tsym": _TSYM, "exch": "BFO", "netqty": "65",
                             "lp": "205", "urmtom": "0"}])
    expired = []
    g = _guard_with_expiry(reg, client, _Recorder(), expired, misses=2)
    for _ in range(4):
        run(g._cycle())
    assert expired == []
    assert len(reg) == 1


def test_expiry_hook_failure_never_breaks_the_age_out():
    """A journal failure must not strand the registry entry."""
    reg = LiveMonitorRegistry()
    _register_pending(reg)

    async def boom(entry, reason):
        raise RuntimeError("mongo down")

    client = _CancelClient([_other_position()])
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=_Recorder().square_fn, max_pending_misses=2,
                          on_expire=boom, now_fn=lambda: _NOW)
    for _ in range(3):
        run(g._cycle())
    assert len(reg) == 0, "a failing on_expire must not leave the entry registered"


# --- the hook must be MOUNTED in production, not merely defined -------------

def test_production_guard_is_constructed_with_the_expiry_hook():
    """A hook with no caller is dead code.

    The suite passed with `on_expire` defined but never passed to the module-level
    guard: every behavioural test builds its own guard. This asserts the PRODUCTION
    wiring, matching the project convention that contract tests prove a component is
    mounted rather than merely imported.
    """
    src = (Path(__file__).resolve().parents[1]
           / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")
    assert "async def _live_guard_on_expire" in src
    assert "on_expire=_live_guard_on_expire" in src, (
        "the aged-out entry hook is defined but never wired into the live guard — "
        "live_trades rows would still leak max_concurrent slots in production")
    # It must sit in the same constructor call as the close hook.
    ctor = src[src.index("on_close=_live_guard_on_close"):]
    assert "on_expire=_live_guard_on_expire" in ctor[:200]
