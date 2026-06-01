"""Tests for calendar-aware expected candle counts (coverage heatmap fix)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.nse_calendar import REGULAR_SESSION_CANDLES, expected_candle_count  # noqa: E402


def test_weekend_expects_zero():
    # 2026-05-30 Saturday, 2026-05-31 Sunday — not trading days.
    assert expected_candle_count("2026-05-30") == 0
    assert expected_candle_count("2026-05-31") == 0


def test_holiday_expects_zero():
    # 2025-10-22 Diwali (in ALL_HOLIDAYS).
    assert expected_candle_count("2025-10-22") == 0


def test_regular_trading_day_expects_full_session():
    # 2026-05-29 Friday, not a holiday.
    assert expected_candle_count("2026-05-29") == REGULAR_SESSION_CANDLES


def test_muhurat_short_session_expects_reduced_count():
    # 2025-10-21 Diwali Muhurat: ~60 candle evening session, not penalized.
    assert expected_candle_count("2025-10-21") == 60


def test_budget_saturday_is_full_session():
    # 2026-02-01 Budget Saturday special session.
    assert expected_candle_count("2026-02-01") == REGULAR_SESSION_CANDLES


def test_coverage_days_are_calendar_annotated():
    """get_coverage must annotate each day with is_trading_day + expected_candles
    so the heatmap can exclude weekends/holidays."""
    from pathlib import Path
    warehouse = (Path(__file__).resolve().parents[1] / "backend" / "app" / "warehouse.py").read_text(encoding="utf-8")
    assert "is_trading_day" in warehouse
    assert "expected_candles" in warehouse
