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

def test_rehydrated_positions_are_excluded_from_the_overall_basket():
    """A position whose entry_price is a reconstructed MARK must not move the basket.

    This is the exact 2026-08-04 mechanism: an adopted position's own MTM breached a
    basket target that belonged to the deployed strategy's basket.
    """
    from app.live.live_position_guard import _basket_members
    entries = [
        {"tsym": "A", "source": "auto_live", "entry_price": 100.0, "qty": 50},
        {"tsym": "B", "source": "rehydrated", "entry_price": 200.0, "qty": 50},
    ]
    members = _basket_members(entries)
    assert [e["tsym"] for e in members] == ["A"], (
        "a rehydrated position must not contribute to basket MTM/premium, and must "
        "not be squared by an overall breach")
