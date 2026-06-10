"""Tests for the DTE (days-to-expiry) classification module.

Uses a deliberately holiday-free span (2025-06-09 .. 2025-06-24) so trading-day
math is deterministic. Trading days that fortnight:
  Mon09 Tue10 Wed11 Thu12 Fri13  Mon16 Tue17  Wed18 Thu19 Fri20  Mon23 Tue24
Weekly expiries are Tuesdays: 2025-06-17 and 2025-06-24.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.dte import (  # noqa: E402
    compute_dte,
    normalize_dte_filter,
    dte_matches,
    sessions_matching_dte,
)

EXPIRIES = ["2025-06-17", "2025-06-24"]


def test_compute_dte_expiry_day_is_zero():
    assert compute_dte("2025-06-17", EXPIRIES) == 0


def test_compute_dte_one_day_before():
    # Mon 2025-06-16 -> 1 trading day before Tue 17 expiry.
    assert compute_dte("2025-06-16", EXPIRIES) == 1


def test_compute_dte_skips_weekend():
    # Fri 2025-06-13 -> trading days Fri13,Mon16,Tue17 = DTE 2 (weekend skipped).
    assert compute_dte("2025-06-13", EXPIRIES) == 2


def test_compute_dte_picks_nearest_upcoming_expiry():
    # Wed 2025-06-18 is after the 17th, so nearest upcoming is Tue 24.
    # Trading days 18,19,20,23,24 = 5 inclusive -> DTE 4.
    assert compute_dte("2025-06-18", EXPIRIES) == 4


def test_compute_dte_none_after_last_expiry():
    assert compute_dte("2025-06-25", EXPIRIES) is None


def test_normalize_dte_filter_variants():
    assert normalize_dte_filter(None) is None
    assert normalize_dte_filter("all") is None
    assert normalize_dte_filter("ALL") is None
    assert normalize_dte_filter("") is None
    assert normalize_dte_filter("dte0") == 0
    assert normalize_dte_filter("DTE3") == 3
    assert normalize_dte_filter("2") == 2
    assert normalize_dte_filter(1) == 1
    assert normalize_dte_filter("garbage") is None


def test_dte_matches_all_matches_any():
    assert dte_matches(0, "all") is True
    assert dte_matches(5, None) is True
    assert dte_matches(None, "all") is True


def test_dte_matches_specific():
    assert dte_matches(0, "dte0") is True
    assert dte_matches(1, "dte0") is False
    assert dte_matches(None, "dte0") is False


def test_sessions_matching_dte_filters():
    sessions = ["2025-06-13", "2025-06-16", "2025-06-17"]  # DTE 2, 1, 0
    assert sessions_matching_dte(sessions, EXPIRIES, "dte0") == ["2025-06-17"]
    assert sessions_matching_dte(sessions, EXPIRIES, "dte1") == ["2025-06-16"]
    assert sessions_matching_dte(sessions, EXPIRIES, "dte2") == ["2025-06-13"]
    assert sessions_matching_dte(sessions, EXPIRIES, "all") == sessions
