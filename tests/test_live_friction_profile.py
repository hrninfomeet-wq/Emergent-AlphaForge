"""TDD suite for the zero-brokerage statutory friction profile.

Tests verify:
- brokerage is always 0
- STT is sell-side only
- stamp duty is buy-side only
- exchange_txn + sebi_fee apply to both sides
- GST = 18% of (exchange_txn + sebi_fee) since brokerage is 0
- total == explicit sum of all named components (no hidden charge)
- NFO vs BFO use different exchange_txn rates
- zero turnover → all components 0
- all values are rounded to 2 decimal places (paise)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.live_friction_profile import (  # noqa: E402
    EXCH_TXN_BFO,
    EXCH_TXN_NFO,
    GST_RATE,
    SEBI_FEE,
    STAMP_OPTIONS,
    STT_OPTIONS_SELL,
    live_charges,
)

# Canonical round-trip turnover used by the parametric tests.
# buy_turnover = sell_turnover = 7800 (e.g. 60 × ₹130 premium)
BUY_TO = 7800.0
SELL_TO = 7800.0


# ---------------------------------------------------------------------------
# Brokerage invariant
# ---------------------------------------------------------------------------

def test_brokerage_always_zero():
    result = live_charges(BUY_TO, SELL_TO)
    assert result["brokerage"] == 0.0


def test_brokerage_zero_with_bfo_segment():
    result = live_charges(BUY_TO, SELL_TO, segment="BFO")
    assert result["brokerage"] == 0.0


# ---------------------------------------------------------------------------
# STT — sell-side only
# ---------------------------------------------------------------------------

def test_stt_on_sell_side_only():
    """STT must use sell_turnover, not buy_turnover."""
    result = live_charges(BUY_TO, SELL_TO)
    expected_stt = round(SELL_TO * STT_OPTIONS_SELL, 2)
    assert result["stt"] == expected_stt


def test_stt_zero_when_no_sell():
    """With sell_turnover=0 STT must be 0 regardless of buy."""
    result = live_charges(BUY_TO, 0.0)
    assert result["stt"] == 0.0


def test_stt_nonzero_when_no_buy():
    """With buy_turnover=0 STT still applies on sell."""
    result = live_charges(0.0, SELL_TO)
    assert result["stt"] == round(SELL_TO * STT_OPTIONS_SELL, 2)


# ---------------------------------------------------------------------------
# Stamp duty — buy-side only
# ---------------------------------------------------------------------------

def test_stamp_duty_on_buy_side_only():
    """Stamp duty must use buy_turnover, not sell_turnover."""
    result = live_charges(BUY_TO, SELL_TO)
    expected_stamp = round(BUY_TO * STAMP_OPTIONS, 2)
    assert result["stamp_duty"] == expected_stamp


def test_stamp_duty_zero_when_no_buy():
    """With buy_turnover=0 stamp must be 0 regardless of sell."""
    result = live_charges(0.0, SELL_TO)
    assert result["stamp_duty"] == 0.0


def test_stamp_duty_nonzero_when_no_sell():
    """With sell_turnover=0 stamp still applies on buy."""
    result = live_charges(BUY_TO, 0.0)
    assert result["stamp_duty"] == round(BUY_TO * STAMP_OPTIONS, 2)


# ---------------------------------------------------------------------------
# Exchange txn + SEBI — both sides
# ---------------------------------------------------------------------------

def test_exchange_txn_uses_total_turnover():
    result = live_charges(BUY_TO, SELL_TO)
    expected = round((BUY_TO + SELL_TO) * EXCH_TXN_NFO, 2)
    assert result["exchange_txn"] == expected


def test_sebi_fee_uses_total_turnover():
    result = live_charges(BUY_TO, SELL_TO)
    expected = round((BUY_TO + SELL_TO) * SEBI_FEE, 2)
    assert result["sebi_fee"] == expected


# ---------------------------------------------------------------------------
# GST — on (exchange_txn + sebi_fee), brokerage = 0
# ---------------------------------------------------------------------------

def test_gst_is_18pct_of_exchange_and_sebi_only():
    """GST = 18% of (exchange_txn + sebi_fee) when brokerage is 0."""
    result = live_charges(BUY_TO, SELL_TO)
    total_turnover = BUY_TO + SELL_TO
    exch = total_turnover * EXCH_TXN_NFO
    sebi = total_turnover * SEBI_FEE
    expected_gst = round((exch + sebi) * GST_RATE, 2)
    assert result["gst"] == expected_gst


# ---------------------------------------------------------------------------
# Total = explicit sum of named components (no hidden charge)
# ---------------------------------------------------------------------------

def test_total_equals_sum_of_components():
    result = live_charges(BUY_TO, SELL_TO)
    explicit_sum = round(
        result["brokerage"]
        + result["stt"]
        + result["exchange_txn"]
        + result["sebi_fee"]
        + result["gst"]
        + result["stamp_duty"],
        2,
    )
    assert result["total"] == explicit_sum


def test_total_equals_sum_bfo():
    result = live_charges(BUY_TO, SELL_TO, segment="BFO")
    explicit_sum = round(
        result["brokerage"]
        + result["stt"]
        + result["exchange_txn"]
        + result["sebi_fee"]
        + result["gst"]
        + result["stamp_duty"],
        2,
    )
    assert result["total"] == explicit_sum


# ---------------------------------------------------------------------------
# NFO vs BFO — different exchange transaction rates
# ---------------------------------------------------------------------------

def test_nfo_and_bfo_differ_on_exchange_txn():
    """NFO and BFO must yield different exchange_txn values (different rates)."""
    nfo = live_charges(BUY_TO, SELL_TO, segment="NFO")
    bfo = live_charges(BUY_TO, SELL_TO, segment="BFO")
    assert nfo["exchange_txn"] != bfo["exchange_txn"]
    # NFO rate (~0.03503%) is slightly higher than BFO (~0.0325%)
    assert nfo["exchange_txn"] > bfo["exchange_txn"]


def test_nfo_and_bfo_same_stt_and_stamp():
    """STT and stamp duty rates are the same for both segments."""
    nfo = live_charges(BUY_TO, SELL_TO, segment="NFO")
    bfo = live_charges(BUY_TO, SELL_TO, segment="BFO")
    assert nfo["stt"] == bfo["stt"]
    assert nfo["stamp_duty"] == bfo["stamp_duty"]


def test_nfo_is_default_segment():
    """Calling without segment= should default to NFO rates."""
    default = live_charges(BUY_TO, SELL_TO)
    nfo = live_charges(BUY_TO, SELL_TO, segment="NFO")
    assert default == nfo


# ---------------------------------------------------------------------------
# Zero turnover → all components 0
# ---------------------------------------------------------------------------

def test_zero_turnover_all_zero():
    result = live_charges(0.0, 0.0)
    assert result["brokerage"] == 0.0
    assert result["stt"] == 0.0
    assert result["exchange_txn"] == 0.0
    assert result["sebi_fee"] == 0.0
    assert result["gst"] == 0.0
    assert result["stamp_duty"] == 0.0
    assert result["total"] == 0.0


def test_zero_turnover_bfo_all_zero():
    result = live_charges(0.0, 0.0, segment="BFO")
    assert result["total"] == 0.0


# ---------------------------------------------------------------------------
# All values rounded to 2 decimal places (paise)
# ---------------------------------------------------------------------------

def test_all_values_are_2dp():
    """Every component and total must be rounded to exactly 2 decimal places."""
    result = live_charges(BUY_TO, SELL_TO)
    for key, val in result.items():
        # Check the value has at most 2 decimal places
        assert round(val, 2) == val, f"{key}={val!r} is not 2dp"


# ---------------------------------------------------------------------------
# Negative / out-of-range inputs are clamped to zero (defensive)
# ---------------------------------------------------------------------------

def test_negative_turnover_treated_as_zero():
    result = live_charges(-1000.0, -500.0)
    assert result["total"] == 0.0


# ---------------------------------------------------------------------------
# Sanity: concrete round-trip amounts for a real-world trade
# ---------------------------------------------------------------------------

def test_concrete_round_trip_nfo():
    """Manual calculation for a concrete trade to guard against regressions.

    Setup: 1 lot NIFTY (65 units), entry ₹120, exit ₹120 (flat move).
    buy_turnover = sell_turnover = 120 × 65 = 7800.
    """
    buy_to = 120.0 * 65    # 7800
    sell_to = 120.0 * 65   # 7800
    total_to = buy_to + sell_to  # 15600

    expected_brokerage = 0.0
    expected_stt = round(sell_to * STT_OPTIONS_SELL, 2)
    expected_exch = round(total_to * EXCH_TXN_NFO, 2)
    expected_sebi = round(total_to * SEBI_FEE, 2)
    expected_gst = round((expected_exch + expected_sebi) * GST_RATE, 2)
    expected_stamp = round(buy_to * STAMP_OPTIONS, 2)
    expected_total = round(
        expected_brokerage + expected_stt + expected_exch + expected_sebi
        + expected_gst + expected_stamp,
        2,
    )

    result = live_charges(buy_to, sell_to, segment="NFO")
    assert result["brokerage"] == expected_brokerage
    assert result["stt"] == expected_stt
    assert result["exchange_txn"] == expected_exch
    assert result["sebi_fee"] == expected_sebi
    assert result["gst"] == expected_gst
    assert result["stamp_duty"] == expected_stamp
    assert result["total"] == expected_total
