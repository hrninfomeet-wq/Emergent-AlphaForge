"""Instrument metadata must have ONE definition, and explosive_reversal is NIFTY-only.

Two findings from the 2026-09-01 SENSEX investigation:

1. `optimizer._DEFAULT_LOT_SIZE` carried NIFTY 75 while `instruments.UNDERLYING_META`
   carried 65 — NSE revised the NIFTY lot from 75 to 65. A second copy of a table
   that is documented as going stale is how the BANKNIFTY 35-vs-30 error happened;
   the fix is to have one definition, not two that agree today.

2. `explosive_reversal` expresses its exits in ABSOLUTE INDEX POINTS with bounds
   sized for NIFTY. On SENSEX (~3.2x the point scale at identical relative
   volatility) the profitable geometry is unreachable inside those bounds and the
   optimizer returned -Rs 932,976 against NIFTY's +Rs 514,052. Research on SENSEX
   stays open by design (`supported_instruments` is enforced only at DEPLOYMENT),
   but the strategy must not be deployable there.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.instruments import UNDERLYING_META  # noqa: E402
from app.optimizer import _DEFAULT_LOT_SIZE  # noqa: E402
from app.strategies.base import get_registry  # noqa: E402


# --- 1. lot size ------------------------------------------------------------

def test_nifty_default_lot_size_is_65():
    """NSE revised the NIFTY F&O lot from 75 to 65."""
    assert _DEFAULT_LOT_SIZE["NIFTY"] == 65


def test_optimizer_lot_fallback_matches_the_single_source():
    """No second copy of the table. Any instrument, any value — one definition."""
    for inst, meta in UNDERLYING_META.items():
        assert _DEFAULT_LOT_SIZE[inst] == meta["lot_size"], (
            f"{inst}: optimizer fallback {_DEFAULT_LOT_SIZE.get(inst)} != "
            f"UNDERLYING_META {meta['lot_size']}"
        )


def test_no_instrument_is_missing_from_the_fallback():
    assert set(_DEFAULT_LOT_SIZE) == set(UNDERLYING_META)


# --- 2. explosive_reversal is NIFTY-only ------------------------------------

@pytest.fixture(scope="module")
def registry():
    reg = get_registry()
    reg.auto_discover()
    return reg


def test_explosive_reversal_declares_nifty_only(registry):
    strat = registry.get("explosive_reversal")
    assert strat is not None, "explosive_reversal must stay registered — not retired"
    assert [s.upper() for s in strat.supported_instruments] == ["NIFTY"]


def test_explosive_reversal_is_not_retired(registry):
    """Declaring the instrument must not remove the strategy: the NIFTY
    deployment and every past NIFTY result depend on it staying loadable."""
    assert "explosive_reversal" in {s.get("id") for s in registry.list_all()}


def test_point_denominated_bounds_are_still_the_nifty_ones(registry):
    """The declaration is the ONLY change. Bounds, defaults and units are
    untouched, so every existing NIFTY result stays reproducible."""
    schema = registry.get("explosive_reversal").parameter_schema
    assert schema["spot_target_pts"] == {"type": "float", "min": 5, "max": 200, "default": 40}
    assert schema["spot_stop_pts"] == {"type": "float", "min": 3, "max": 100, "default": 18}


def test_atr_scaled_siblings_stay_multi_instrument(registry):
    """The scale-free variants are the ones allowed to travel."""
    atr = registry.get("explosive_reversal_atr")
    assert atr is not None
    assert "SENSEX" in [s.upper() for s in atr.supported_instruments]
    assert "NIFTY" in [s.upper() for s in atr.supported_instruments]


def test_sensex_variant_stays_sensex_only(registry):
    sen = registry.get("sensex_explosive_reversal")
    assert sen is not None
    assert [s.upper() for s in sen.supported_instruments] == ["SENSEX"]
