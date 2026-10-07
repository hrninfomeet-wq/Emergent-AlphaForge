"""2026 trading holidays match NSE's official F&O circular (audit 2026-10-07 #5).

Source: NSE/FAOP/71777, "Trading holidays for the calendar year 2026", dated
December 12, 2025 (F&O segment). The curated `_HOLIDAYS_2026` was missing four
of its fifteen weekday holidays — 2026-09-14, 2026-10-20, 2026-11-10 and
2026-11-24, the last three NIFTY weekly-expiry TUESDAYS. On those days DTE
(`app.dte`, which counts trading days) and the live session clock
(`live/session_clock.py`) treated a closed market as open.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.dte import compute_dte  # noqa: E402
from app.live.session_clock import describe_session  # noqa: E402
from app.nse_calendar import (  # noqa: E402
    HOLIDAY_LABELS, calendar_for_year, is_trading_day, trading_days_in_range,
)

#: NSE/FAOP/71777 — the 15 weekday trading holidays of 2026, verbatim order.
NSE_FAOP_71777_WEEKDAY_HOLIDAYS = {
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}


@pytest.mark.parametrize("iso", sorted(NSE_FAOP_71777_WEEKDAY_HOLIDAYS))
def test_every_official_2026_holiday_is_closed(iso):
    assert is_trading_day(iso) is False, f"{iso} ({NSE_FAOP_71777_WEEKDAY_HOLIDAYS[iso]})"


@pytest.mark.parametrize("iso", sorted(NSE_FAOP_71777_WEEKDAY_HOLIDAYS))
def test_every_official_2026_holiday_carries_its_name(iso):
    """The UI calendar shows HOLIDAY_LABELS — a missing label renders a bare
    'Market holiday', and two existing labels contradicted the circular."""
    last_word = NSE_FAOP_71777_WEEKDAY_HOLIDAYS[iso].split()[-1].lower()
    assert last_word in HOLIDAY_LABELS.get(iso, "").lower(), (iso, HOLIDAY_LABELS.get(iso))


def test_the_2026_ui_calendar_lists_all_fifteen():
    listed = {h["date"] for h in calendar_for_year(2026)["holidays"]}
    assert set(NSE_FAOP_71777_WEEKDAY_HOLIDAYS) <= listed


def test_dte_skips_the_ganesh_chaturthi_monday():
    """Fri 2026-09-11 → Tue 2026-09-15 NIFTY expiry: Mon 09-14 is closed, so the
    Friday session is ONE trading day before expiry, not two."""
    assert compute_dte("2026-09-11", ["2026-09-15"]) == 1


def test_trading_day_count_around_the_expiry_tuesday_holidays():
    # Oct 19-23: Tue 20 closed → 4 sessions. Nov 9-13: Tue 10 closed → 4.
    # Nov 23-27: Tue 24 closed → 4.
    assert len(trading_days_in_range("2026-10-19", "2026-10-23")) == 4
    assert len(trading_days_in_range("2026-11-09", "2026-11-13")) == 4
    assert len(trading_days_in_range("2026-11-23", "2026-11-27")) == 4


@pytest.mark.parametrize("iso", ["2026-09-14", "2026-10-20", "2026-11-10", "2026-11-24"])
def test_live_session_clock_reports_the_holiday_closed(iso):
    y, m, d = map(int, iso.split("-"))
    now = datetime(y, m, d, 5, 0, tzinfo=timezone.utc)          # 10:30 IST
    out = describe_session(now, eod_square_ist=None)
    assert out["phase"] == "closed_day", out
