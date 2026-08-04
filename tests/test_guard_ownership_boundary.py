"""The software guard must only ever act on positions AlphaForge itself opened.

2026-08-04, live market. The operator bought 5 lots of NIFTY 24600 CE by hand on
the Flattrade MOBILE app. Eight seconds later AlphaForge's recovery pass adopted it:

    guard rehydrate: re-attached NIFTY04AUG26C24600 (netqty=325) at default 50% stop
    - original levels lost on restart

There were no "original levels" — AlphaForge never opened it. The adopted position
was then summed into the OVERALL BASKET controls, whose target was ₹180 MTM. On a
₹11,099 basket that is a 1.6% move, so it breached almost immediately and the guard
transmitted a real square. Realised: -₹178.75, on a trade the operator considered
manual and unmanaged.

Three defects stack here; this file pins all three:
  1. `rehydrate_from_broker` had NO ownership check — it adopted every non-flat row
     in the broker position book.
  2. It adopted SHORTS too, with `build_monitor_state`'s LONG-only semantics, so the
     stop sits BELOW a short's entry: it fires on profit and never on loss.
  3. `_evaluate_overall_basket` aggregated adopted positions into basket MTM/premium
     and squared them on an overall breach.

Ownership must fail CLOSED: `owned_tsyms=None` adopts NOTHING.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from tests.test_live_position_guard import (  # noqa: E402
    _FakeClient, _Recorder, _guard, _pos, _TSYM, run,
)
from app.live.live_position_guard import LiveMonitorRegistry  # noqa: E402

OWNED = _TSYM
HAND = "NIFTY04AUG26C24600"          # the operator's mobile-placed position


# --- 1. ownership ----------------------------------------------------------

def test_adopts_only_positions_alphaforge_owns():
    reg = LiveMonitorRegistry()
    client = _FakeClient([_pos(netqty=20, tsym=OWNED), _pos(netqty=325, tsym=HAND)])
    n = run(_guard(reg, client, _Recorder()).rehydrate_from_broker(owned_tsyms={OWNED}))
    assert n == 1, "must adopt exactly the owned position"
    tsyms = {e["tsym"] for e in reg.snapshot()}
    assert OWNED in tsyms
    assert HAND not in tsyms, "adopted a hand-placed position it does not own"


def test_ownership_fails_closed_when_unknown():
    """`owned_tsyms=None` must adopt NOTHING — not everything."""
    reg = LiveMonitorRegistry()
    client = _FakeClient([_pos(netqty=20, tsym=OWNED), _pos(netqty=325, tsym=HAND)])
    n = run(_guard(reg, client, _Recorder()).rehydrate_from_broker(owned_tsyms=None))
    assert n == 0, "unknown ownership must adopt nothing, not the whole book"
    assert len(reg) == 0


def test_empty_ownership_set_adopts_nothing():
    reg = LiveMonitorRegistry()
    client = _FakeClient([_pos(netqty=325, tsym=HAND)])
    assert run(_guard(reg, client, _Recorder()).rehydrate_from_broker(owned_tsyms=set())) == 0
    assert len(reg) == 0


# --- 2. shorts -------------------------------------------------------------

def test_refuses_to_adopt_a_short_position():
    """The monitor's stop/target logic is LONG-only; claiming to guard a short is a lie."""
    reg = LiveMonitorRegistry()
    client = _FakeClient([_pos(netqty=-20, lp=250.0, tsym=OWNED)])
    n = run(_guard(reg, client, _Recorder()).rehydrate_from_broker(owned_tsyms={OWNED}))
    assert n == 0, "adopted a SHORT under long-only stop semantics"
    assert len(reg) == 0


def test_long_still_adopted_after_the_short_guard():
    """No over-firing: the short check must not block ordinary longs."""
    reg = LiveMonitorRegistry()
    client = _FakeClient([_pos(netqty=20, lp=250.0, tsym=OWNED)])
    assert run(_guard(reg, client, _Recorder()).rehydrate_from_broker(owned_tsyms={OWNED})) == 1
    assert reg.snapshot()[0]["source"] == "rehydrated"


# --- 3. basket contamination ----------------------------------------------

def test_recovered_positions_STAY_in_the_overall_basket():
    """Regression on my own ce82ba6 fix — it excluded the wrong population.

    `_basket_members` dropped every `source == "rehydrated"` entry, justified as
    excluding "a position the strategy never opened". The ownership gate added in
    the SAME commit made that premise unreachable: adoption now requires proven
    ownership, and every adopted row is written `source="rehydrated"`. So the
    filter removed EXACTLY AlphaForge's own restart-recovered positions.

    Only premium-momentum re-registers as `auto_live` on recovery (runtime.py);
    every other deployment's legs come back "rehydrated". After a restart the
    basket was therefore empty, `_evaluate_overall_basket` returned early, and the
    operator's account-level overall SL/target NEVER evaluated — while the UI
    still presented it as in force.

    `basket_mtm` is summed from the broker's own `urmtom`, which is correct for a
    recovered position; only `basket_premium` (built from a reconstructed entry
    mark) is approximate, and that is consulted ONLY for `premium_pct` thresholds.
    A ₹-mtm account stop must never be silently disabled.
    """
    from app.live.live_position_guard import _basket_members
    entries = [
        {"tsym": "A", "source": "auto_live", "entry_price": 100.0, "qty": 50},
        {"tsym": "B", "source": "rehydrated", "entry_price": 200.0, "qty": 50},
    ]
    members = _basket_members(entries)
    assert {e["tsym"] for e in members} == {"A", "B"}, (
        "a restart-recovered position AlphaForge owns must still be covered by the "
        "account-level overall stop")


def test_recovered_entry_price_is_flagged_as_a_mark():
    """The genuine half of the original concern, kept: a recovered entry_price is
    a recovery-time MARK, so percentage-scaled basket thresholds are approximate.
    Flag it rather than silently dropping the position's protection."""
    from app.live.live_position_guard import LiveMonitorRegistry
    from app.live.live_sl_monitor import build_monitor_state
    reg = LiveMonitorRegistry()
    client = _FakeClient([_pos(netqty=20, lp=250.0, tsym=OWNED)])
    run(_guard(reg, client, _Recorder()).rehydrate_from_broker(owned_tsyms={OWNED}))
    item = reg.snapshot()[0]
    assert item.get("entry_price_is_mark") is True, (
        "a reconstructed entry price must be marked so premium_pct scaling can "
        "declare itself approximate instead of pretending to be exact")


def test_account_stop_fires_for_a_restart_recovered_basket():
    """End-to-end proof of the regression: the overall SL must actually square.

    Reproduces the verifier's scenario — a restart-recovered basket, an mtm-mode
    account stop, and broker MTM through it. Before the fix this produced ZERO
    squares and left `_overall_state` None.
    """
    from app.live.live_position_guard import LivePositionGuard, LiveMonitorRegistry
    from app.live.live_sl_monitor import build_monitor_state
    from tests.test_live_position_guard import _NOW

    reg = LiveMonitorRegistry()
    for key, tsym in (("R1", "AAA26JUN100CE"), ("R2", "BBB26JUN100CE")):
        reg.register(key=key, tsym=tsym, exch="NFO", qty=65, prd="I",
                     entry_price=100.0,
                     state=build_monitor_state(100.0, stop_pct=50),
                     source="rehydrated")

    book = [{"tsym": "AAA26JUN100CE", "exch": "NFO", "netqty": "65",
             "lp": "60", "urmtom": "-9000"},
            {"tsym": "BBB26JUN100CE", "exch": "NFO", "netqty": "65",
             "lp": "60", "urmtom": "-9000"}]

    async def _client():
        class _C:
            async def position_book(self):
                return list(book)
        return _C()

    rec = _Recorder()

    async def overall_provider():
        return {"sl": {"enabled": True, "mode": "mtm", "value": 5000}}

    g = LivePositionGuard(registry=reg, client_factory=_client,
                          square_fn=rec.square_fn,
                          overall_provider=overall_provider,
                          now_fn=lambda: _NOW)
    run(g._cycle())
    assert rec.squared, (
        "an account-level overall SL must fire for restart-recovered positions — "
        "the basket was silently empty before this fix")
    assert len(rec.squared) == 2, "an overall breach squares the WHOLE basket"
