"""Tests for the NSE/BSE holiday calendar (slice 6 polish).

These dates are confirmed against the actual gaps observed in the spot
warehouse on 2026-05-29. Re-running these tests is an early warning if the
calendar ever drifts from reality.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.nse_calendar import (  # noqa: E402
    expected_trading_days,
    holidays_in_range,
    is_market_holiday,
    is_trading_day,
    is_weekend,
    trading_days_in_range,
)


def test_known_holidays_are_recognised():
    assert is_market_holiday("2024-12-25")  # Christmas 2024
    assert is_market_holiday("2025-03-14")  # Holi 2025
    assert is_market_holiday("2025-08-15")  # Independence Day 2025
    assert is_market_holiday("2026-01-26")  # Republic Day 2026


def test_regular_weekday_is_not_holiday():
    assert not is_market_holiday("2025-04-15")  # Tuesday after Holi
    assert not is_market_holiday("2026-05-26")  # Tuesday before Eid


def test_is_trading_day_excludes_weekend_and_holidays():
    assert is_trading_day("2025-04-15")          # Tuesday, not holiday
    assert not is_trading_day("2025-04-19")      # Saturday
    assert not is_trading_day("2025-04-20")      # Sunday
    assert not is_trading_day("2025-04-18")      # Good Friday
    assert not is_trading_day("not-a-date")      # malformed input is safe


def test_expected_trading_days_excludes_holidays_and_weekends():
    """A 1-week window with one mid-week holiday should count 4 trading days."""
    # 2025-04-14 Mon (Ambedkar Jayanti) - holiday; 18 Good Friday holiday
    # Mon-Fri 14-18 -> normally 5 weekdays, minus 2 holidays = 3 trading days
    assert expected_trading_days("2025-04-14", "2025-04-18") == 3


def test_expected_trading_days_skips_weekend_only_window():
    assert expected_trading_days("2025-04-19", "2025-04-20") == 0


def test_long_window_against_real_observation():
    """Window 2024-11-27 -> 2026-05-26 (the actual warehouse range observed
    on 2026-05-29) should have 370 trading days, matching what the spot
    warehouse contains for each index after backfill (368 weekdays minus holidays
    + 2 Budget Saturday sessions on 2025-02-01 and 2026-02-01)."""
    n = expected_trading_days("2024-11-27", "2026-05-26")
    assert n == 370, f"expected 370 trading days, got {n}"


def test_budget_saturdays_count_as_trading_days():
    assert is_trading_day("2025-02-01")  # Budget Saturday 2025
    assert is_trading_day("2026-02-01")  # Budget Saturday 2026
    # Regular Saturday is not a trading day
    assert not is_trading_day("2025-02-08")


def test_holidays_in_range_returns_sorted_subset():
    holidays = holidays_in_range("2025-03-01", "2025-04-30")
    # These are the 2025 holidays that fall in this range
    expected = ["2025-03-14", "2025-03-31", "2025-04-10", "2025-04-14", "2025-04-18"]
    assert holidays == expected


def test_trading_days_in_range_excludes_holidays_and_weekends():
    days = trading_days_in_range("2025-04-14", "2025-04-18")
    assert days == ["2025-04-15", "2025-04-16", "2025-04-17"]
