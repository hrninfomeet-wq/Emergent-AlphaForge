"""Lot size must come from the CONTRACT DATA, never from a hardcoded map.

Background (2026-07-27): two independent lot-size sources existed and disagreed.

  - ``option_backtest.py:750`` uses the SELECTED CONTRACT's ``lot_size`` — the
    data-driven path, and the one the project's stated design mandates
    ("expiries/lots come from option_contracts/broker, NEVER hardcoded").
  - ``premium_momentum_backtest.py:342`` and ``premium_trigger_dispatch.py:194``
    used ``UNDERLYING_META[instrument]["lot_size"]`` — a hardcoded map.

For NIFTY (65) and SENSEX (20) the two agree, so the divergence was invisible.
For BANKNIFTY they do NOT: every stored 2026 expiry carries lot 30, while
``UNDERLYING_META`` says 35 — a 16.7% error in every quantity, rupee P&L and
cost figure produced by the premium-momentum path, silently.

These tests pin the rule: resolve from data, fall back to the map only when the
contracts carry no lot at all, and never silently pick one when expiries within
a run disagree.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.instruments import UNDERLYING_META, resolve_lot_size  # noqa: E402


def _c(expiry: str, lot, **extra):
    d = {"expiry_date": expiry, "lot_size": lot, "underlying": "BANKNIFTY"}
    d.update(extra)
    return d


def test_resolves_the_lot_from_contract_data_not_the_hardcoded_map():
    """THE regression: BANKNIFTY contracts say 30, UNDERLYING_META says 35."""
    contracts = [_c("2026-07-28", 30), _c("2026-08-25", 30)]
    lot, warnings = resolve_lot_size(contracts, "BANKNIFTY")
    assert lot == 30
    assert lot != UNDERLYING_META["BANKNIFTY"]["lot_size"], (
        "if these are equal the fixture no longer reproduces the divergence")
    assert warnings == []


def test_agreeing_instruments_are_unaffected():
    """NIFTY/SENSEX must be byte-identical to the old behaviour."""
    for inst, lot_val in (("NIFTY", 65), ("SENSEX", 20)):
        contracts = [{"expiry_date": "2026-07-28", "lot_size": lot_val}]
        lot, warnings = resolve_lot_size(contracts, inst)
        assert lot == lot_val == UNDERLYING_META[inst]["lot_size"]
        assert warnings == []


def test_falls_back_to_the_map_when_contracts_carry_no_lot():
    """Fallback must still work — and must SAY it fell back."""
    contracts = [{"expiry_date": "2026-07-28"}, {"expiry_date": "2026-08-25", "lot_size": None}]
    lot, warnings = resolve_lot_size(contracts, "BANKNIFTY")
    assert lot == UNDERLYING_META["BANKNIFTY"]["lot_size"]
    assert any("fallback" in w for w in warnings)


def test_no_contracts_at_all_falls_back():
    lot, warnings = resolve_lot_size([], "NIFTY")
    assert lot == 65
    assert any("fallback" in w for w in warnings)


def test_unknown_instrument_with_no_data_is_1_and_warns():
    """Never silently invent a lot for an instrument we know nothing about."""
    lot, warnings = resolve_lot_size([], "DOESNOTEXIST")
    assert lot == 1
    assert warnings, "an unknown instrument with no contract data must warn"


def test_a_lot_change_inside_the_window_is_SURFACED_not_silently_picked():
    """BANKNIFTY genuinely changed lot mid-history (35 in Jul-Dec 2025, 30 after).

    A run spanning the change must not silently average or arbitrarily pick —
    it must take the MOST RECENT expiry's lot (the live convention) and say so,
    because every rupee figure in that run is then only approximately right.
    """
    contracts = [_c("2025-09-30", 35), _c("2026-07-28", 30), _c("2025-11-25", 35)]
    lot, warnings = resolve_lot_size(contracts, "BANKNIFTY")
    assert lot == 30, "must take the most-recent expiry's lot"
    assert any("changed" in w for w in warnings), warnings


def test_malformed_lot_values_are_ignored_not_crashed_on():
    contracts = [_c("2026-07-28", "30"), _c("2026-08-25", "junk"), _c("2026-09-29", 0)]
    lot, warnings = resolve_lot_size(contracts, "BANKNIFTY")
    assert lot == 30, "a numeric string is usable; junk and 0 are not"


def test_premium_momentum_backtest_no_longer_hardcodes_the_lot():
    """Source contract: the whole point is that this path stops using the map."""
    import inspect
    from app import premium_momentum_backtest as pmb
    src = inspect.getsource(pmb.run_premium_momentum_backtest)
    assert 'UNDERLYING_META' not in src or 'resolve_lot_size' in src, (
        "run_premium_momentum_backtest must resolve the lot from contract data")
    assert "resolve_lot_size" in src


def test_premium_trigger_dispatch_no_longer_hardcodes_the_lot():
    import inspect
    from app import premium_trigger_dispatch as ptd
    src = inspect.getsource(ptd)
    assert "resolve_lot_size" in src, (
        "the dispatch path must resolve the lot from contract data too")
