"""Freeze slicing emits whole lots, from ONE freeze table (audit 2026-10-07 #4).

NSE/FAOP/76693 (2026-10-01) revised the index quantity-freeze limits from
2026-10-05 — the maximum quantity of a single order: NIFTY 1,800 -> 3,510
(54 lots of 65), BANKNIFTY 600 -> 1,440 (48 lots of 30).

The repo still carried 1,800 / 600 in TWO places (flattrade_symbol.EXCHANGE_RULES
and a hard-coded copy in kill_switch used by the emergency-flatten slicer), and
the slicer cut children at exactly freeze_qty. 1,800 is not a multiple of the
NIFTY lot (65), so any NIFTY order or emergency flatten of >= 28 lots produced
a 1,800-unit child the exchange rejects (F&O quantity must be whole lots) — the
kill switch failing on exactly the large position it exists for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live import flattrade_symbol  # noqa: E402
from app.live.flattrade_symbol import EXCHANGE_RULES  # noqa: E402
from app.live.kill_switch import _build_flatten_legs  # noqa: E402
from app.live.order_builder import slice_to_freeze, validate_and_build  # noqa: E402
from tests.test_live_validate_and_build import _ticket  # noqa: E402


def _row(tsym, netqty, *, ls=None):
    r = {"tsym": tsym, "exch": "NFO", "prd": "M", "netqty": str(netqty), "lp": "100.0"}
    if ls is not None:
        r["ls"] = str(ls)
    return r


def _leg_qtys(rows):
    return [l["qty"] for l in _build_flatten_legs(rows, "lp")]


# --- the freeze table --------------------------------------------------------

def test_freeze_limits_follow_nse_faop_76693():
    assert EXCHANGE_RULES["NIFTY"]["freeze_qty"] == 3510
    assert EXCHANGE_RULES["BANKNIFTY"]["freeze_qty"] == 1440


# --- the slicer ---------------------------------------------------------------

def test_children_are_whole_lots_when_freeze_is_not_a_lot_multiple():
    # The old NIFTY figure: 1800 = 27.69 lots of 65. 28 lots must split 27 + 1.
    assert slice_to_freeze(28 * 65, 1800, lot_size=65) == [27 * 65, 65]


@pytest.mark.parametrize("underlying", sorted(EXCHANGE_RULES))
def test_every_child_is_a_whole_lot_within_the_freeze(underlying):
    lot = EXCHANGE_RULES[underlying]["lot_size"]
    freeze = EXCHANGE_RULES[underlying]["freeze_qty"]
    for lots in range(1, 400):
        qty = lots * lot
        kids = slice_to_freeze(qty, freeze, lot_size=lot, cap=False)
        assert sum(kids) == qty
        assert all(0 < k <= freeze and k % lot == 0 for k in kids), (underlying, lots, kids)


def test_a_freeze_below_one_lot_is_refused():
    with pytest.raises(ValueError):
        slice_to_freeze(130, 60, lot_size=65)


def test_uncapped_slicing_for_a_flatten():
    big = 11 * 3510
    with pytest.raises(ValueError):
        slice_to_freeze(big, 3510, lot_size=65)            # entry fat-finger cap
    assert sum(slice_to_freeze(big, 3510, lot_size=65, cap=False)) == big


# --- the emergency flatten --------------------------------------------------

def test_flatten_slices_nifty_at_the_new_freeze_in_whole_lots():
    assert _leg_qtys([_row("NIFTY26OCT26C25000", 60 * 65)]) == [3510, 390]


def test_flatten_banknifty_at_the_new_freeze():
    assert _leg_qtys([_row("BANKNIFTY26OCT26C55000", 50 * 30)]) == [1440, 60]


def test_flatten_prefers_the_rows_own_lot_size():
    """A contract listed before a lot revision keeps its old lot; the broker row's
    `ls` is authoritative for it."""
    kids = _leg_qtys([_row("NIFTY26OCT26C25000", 60 * 75, ls=75)])
    assert kids == [3450, 1050]                              # 46 + 14 lots of 75
    assert all(k % 75 == 0 for k in kids)


def test_flatten_reads_the_one_freeze_table(monkeypatch):
    """No second hard-coded copy: a change to EXCHANGE_RULES reaches the slicer."""
    monkeypatch.setitem(flattrade_symbol.EXCHANGE_RULES["NIFTY"], "freeze_qty", 1300)
    assert _leg_qtys([_row("NIFTY26OCT26C25000", 40 * 65)]) == [1300, 1300]


def test_a_nifty_prefixed_other_index_is_not_sliced_as_nifty():
    """NIFTYNXT50 starts with "NIFTY" but has its own lot and freeze — guessing
    NIFTY's would mis-slice it. Unknown → one leg, any oversize reject surfaced."""
    assert _leg_qtys([_row("NIFTYNXT5026OCT26C70000", 5000)]) == [5000]


# --- the order choke-point ------------------------------------------------

def test_choke_point_children_are_whole_lots():
    children, verdicts = validate_and_build(_ticket(lots=60, fat_finger_cap=100))
    assert children is not None, verdicts
    assert [c.qty for c in children] == [3510, 390]
    assert all(c.qty % 65 == 0 for c in children)
