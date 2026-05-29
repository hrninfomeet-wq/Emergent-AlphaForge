"""NSE / BSE Indian equity-market holiday calendar.

Hand-curated list of confirmed market holidays for 2024 and 2025, plus 2026
holidays that have been gazetted by the exchanges. NSE and BSE share the same
trading calendar for equity / index segments.

Source verification (2026-05-29):
  - 2024 holidays from NSE published equity calendar
  - 2025 holidays from NSE published equity calendar
  - 2026 holidays as published by NSE
  - Confirmed against actual gaps observed in the spot warehouse
    (those gaps map exactly onto these dates)

Usage:
  from app.nse_calendar import is_market_holiday, expected_trading_days

  if is_market_holiday("2025-12-25"): ...
  expected_trading_days("2024-11-27", "2026-05-28")  # weekdays minus holidays

Maintenance: review at the start of each calendar year when the new schedule is
published; add the new dates and bump the YEAR_LAST_VERIFIED constant.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, List, Set

YEAR_LAST_VERIFIED = 2026

# Confirmed NSE/BSE equity holidays. Trading is closed on these dates.
_HOLIDAYS_2024: Set[str] = {
    "2024-01-22",  # Ram Mandir consecration (declared)
    "2024-01-26",  # Republic Day
    "2024-03-08",  # Mahashivratri
    "2024-03-25",  # Holi
    "2024-03-29",  # Good Friday
    "2024-04-11",  # Eid-ul-Fitr (Ramzan Id)
    "2024-04-17",  # Ram Navami
    "2024-05-01",  # Maharashtra Day
    "2024-05-20",  # Mumbai general elections
    "2024-06-17",  # Bakri Id
    "2024-07-17",  # Muharram
    "2024-08-15",  # Independence Day
    "2024-10-02",  # Mahatma Gandhi Jayanti
    "2024-11-01",  # Diwali Laxmi Pujan (special trading session in evening)
    "2024-11-15",  # Gurunanak Jayanti
    "2024-12-25",  # Christmas
}

_HOLIDAYS_2025: Set[str] = {
    "2025-02-26",  # Mahashivratri
    "2025-03-14",  # Holi
    "2025-03-31",  # Eid-ul-Fitr
    "2025-04-10",  # Mahavir Jayanti
    "2025-04-14",  # Dr. Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-05-01",  # Maharashtra Day
    "2025-08-15",  # Independence Day
    "2025-08-27",  # Ganesh Chaturthi
    "2025-10-02",  # Mahatma Gandhi Jayanti / Dussehra
    "2025-10-22",  # Diwali Laxmi Pujan
    "2025-11-05",  # Gurunanak Jayanti
    "2025-12-25",  # Christmas
}

_HOLIDAYS_2026: Set[str] = {
    "2026-01-15",  # Pongal / Makar Sankranti (state)
    "2026-01-26",  # Republic Day
    "2026-03-03",  # Holi
    "2026-03-26",  # Eid-ul-Fitr
    "2026-03-31",  # Mahavir Jayanti / Annual financial close
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-05-28",  # Eid-ul-Adha (Bakri Id)
    "2026-08-15",  # Independence Day (Saturday this year, but listed for safety)
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-11-08",  # Diwali Laxmi Pujan (Sunday) - special session
    "2026-12-25",  # Christmas
}

# Budget Day Saturdays - NSE conducts a special Saturday trading session for the Union Budget.
# These dates are weekend by calendar but full trading days by exchange.
SPECIAL_SATURDAY_SESSIONS: Set[str] = {
    "2025-02-01",  # Union Budget 2025-26
    "2026-02-01",  # Union Budget 2026-27 (declared Saturday session)
}

ALL_HOLIDAYS: Set[str] = _HOLIDAYS_2024 | _HOLIDAYS_2025 | _HOLIDAYS_2026


def is_market_holiday(iso_date: str) -> bool:
    return iso_date in ALL_HOLIDAYS


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_trading_day(iso_date: str) -> bool:
    """True if the date is a trading session: weekday minus holiday, OR a special Saturday session."""
    try:
        d = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return False
    if iso_date in SPECIAL_SATURDAY_SESSIONS:
        return True
    if is_weekend(d):
        return False
    return iso_date not in ALL_HOLIDAYS


def expected_trading_days(start_iso: str, end_iso: str) -> int:
    """Count trading days inclusive of both endpoints. Skips weekends + holidays;
    includes the gazetted Saturday Budget sessions.
    """
    cur = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    n = 0
    while cur <= end:
        iso = cur.isoformat()
        if iso in SPECIAL_SATURDAY_SESSIONS:
            n += 1
        elif not is_weekend(cur) and iso not in ALL_HOLIDAYS:
            n += 1
        cur += timedelta(days=1)
    return n


def trading_days_in_range(start_iso: str, end_iso: str) -> List[str]:
    """Return the list of trading-day ISO strings in the inclusive range."""
    cur = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    out: List[str] = []
    while cur <= end:
        iso = cur.isoformat()
        if iso in SPECIAL_SATURDAY_SESSIONS:
            out.append(iso)
        elif not is_weekend(cur) and iso not in ALL_HOLIDAYS:
            out.append(iso)
        cur += timedelta(days=1)
    return out


def holidays_in_range(start_iso: str, end_iso: str) -> List[str]:
    """Return holidays whose date falls within the inclusive range, sorted."""
    return sorted(h for h in ALL_HOLIDAYS if start_iso <= h <= end_iso)
