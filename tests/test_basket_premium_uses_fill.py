"""`premium_pct` on the Overall controls must mean a % of what was actually PAID.

The basket notional that resolves every `premium_pct` threshold to rupees is built
in `_recompute_overall`:

    basket_premium += float(entry["entry_price"]) * int(entry["qty"])

`entry_price` is seeded from `ref_ltp` — the pre-trade REFERENCE — because Noren's
PlaceOrder response carries no `avgprc`, so the fill is not knowable at
registration. b3f7df3 re-anchored the TRAILING basis (`state["entry"]`) to the
observed fill but deliberately left `entry_price` alone as the record of what was
seeded. The basket notional was never moved across, so an operator asking for
"SL at 20% of premium" got 20% of the reference.

Bounded — 2026-08-14 was ref 61.35 vs fill 61.70, 0.57% — but wrong in a control
whose whole contract is "a percentage of premium", and the guard already knows the
right number: it stamps `entry_fill_price` on the same registry entry.

Direction matters and is the reason this is safe: a fill ABOVE the reference makes
the basket notional LARGER, so a % stop resolves to MORE rupees. That is not a
loosened stop, it is the honest one — 20% of a bigger premium IS more rupees.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live import live_position_guard as G  # noqa: E402


def _entry(**kw):
    e = {"tsym": "NIFTY18AUG26P24300", "qty": 65, "entry_price": 61.35,
         "source": "auto_live", "id": "26081400076294"}
    e.update(kw)
    return e


def _notional(entry):
    """Reproduce the basket-premium term for one entry, via the real helper."""
    return G._entry_basket_premium(entry)


def test_the_observed_fill_wins_over_the_reference():
    e = _entry(entry_fill_price=61.70)
    assert _notional(e) == 61.70 * 65


def test_the_reference_is_used_until_a_fill_is_observed():
    """Before the first mark there is nothing better — the reference must be used,
    not zero. Dropping the entry would silently shrink the basket and TIGHTEN
    every premium_pct threshold on a position that is genuinely open."""
    assert _notional(_entry()) == 61.35 * 65


def test_the_2026_08_14_gap_is_closed():
    ref, fill, qty = 61.35, 61.70, 65
    stale = ref * qty
    true = _notional(_entry(entry_fill_price=fill))
    assert true > stale
    # A 20% premium_pct SL resolves off the real premium, not the reference.
    assert round(0.20 * true, 2) == round(0.20 * fill * qty, 2)


def test_a_fill_below_the_reference_is_also_honoured():
    """Not a ratchet: this is a notional, not a live stop level. A cheaper fill
    means a smaller premium and correspondingly fewer rupees of risk budget."""
    e = _entry(entry_fill_price=60.00)
    assert _notional(e) == 60.00 * 65


def test_a_junk_fill_falls_back_rather_than_poisoning_the_basket():
    for bad in (None, 0, -1, "x", float("nan"), float("inf")):
        assert _notional(_entry(entry_fill_price=bad)) == 61.35 * 65, bad


def test_a_junk_reference_contributes_nothing_rather_than_raising():
    assert _notional({"qty": 65, "entry_price": None}) == 0.0
    assert _notional({"qty": "x", "entry_price": 61.35}) == 0.0
