"""Option coverage must notice the ten minutes the derivatives session gained.

From 2026-08-03 a complete contract-day is 385 one-minute bars, not 375. Auditing
a post-CAS day against a flat 375 would let a day that is missing the entire
15:30-15:40 tape still report 100% coverage — a silent hole in a trust surface.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.option_coverage import summarize_option_coverage  # noqa: E402
from app.option_data_audit import _weekday_counts  # noqa: E402

BEFORE = "2026-07-31"
AFTER = "2026-08-03"


def _row(date_str: str, candles: int, contracts: int = 1):
    return {
        "underlying": "NIFTY",
        "date": date_str,
        "candles": candles,
        "contracts": contracts,
        "complete_contracts": contracts,
        "instrument_keys": [f"NSE_FO|{i}" for i in range(contracts)],
    }


def _day(rows):
    return summarize_option_coverage(rows)["NIFTY"]["days"][0]


def test_pre_cas_full_day_is_still_375_bars():
    day = _day([_row(BEFORE, candles=375)])
    assert day["expected_candles_for_stored_contracts"] == 375
    assert day["coverage_pct"] == 100.0


def test_post_cas_full_day_needs_385_bars():
    day = _day([_row(AFTER, candles=385)])
    assert day["expected_candles_for_stored_contracts"] == 385
    assert day["coverage_pct"] == 100.0


def test_post_cas_day_truncated_at_1530_is_flagged_not_silently_complete():
    """The regression this guards: 375 stored bars on a post-CAS day means the
    whole extended window is missing, and it must not read as full coverage."""
    day = _day([_row(AFTER, candles=375)])
    assert day["expected_candles_for_stored_contracts"] == 385
    assert day["coverage_pct"] < 100.0


def test_expected_scales_with_contract_count():
    day = _day([_row(AFTER, candles=385 * 4, contracts=4)])
    assert day["expected_candles_for_stored_contracts"] == 385 * 4
    assert day["coverage_pct"] == 100.0


def test_non_trading_day_does_not_crash_or_divide_by_zero():
    """A stray row on a holiday expects zero; fall back rather than blow up."""
    day = _day([_row("2026-10-02", candles=10)])
    assert day["expected_candles_for_stored_contracts"] > 0


def test_weekday_counts_step_up_on_the_effective_date():
    counts = _weekday_counts(BEFORE, AFTER)
    assert counts[BEFORE] == 375
    assert counts[AFTER] == 385


def test_weekday_counts_honour_an_explicit_override():
    assert _weekday_counts(AFTER, AFTER, expected_per_day=375)[AFTER] == 375


def test_weekday_counts_no_longer_expect_candles_on_a_holiday():
    """2026-10-02 (Gandhi Jayanti) is a weekday but not a trading day; the audit
    previously expected a full 375 bars on it."""
    assert _weekday_counts("2026-10-02", "2026-10-02")["2026-10-02"] == 0
