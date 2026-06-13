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
    "2026-01-15",  # Maharashtra municipal corporation/BMC civic elections (sudden, declared)
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

# Exchange-driven expiry-day shifts: when the original expiry day fell on a holiday,
# the exchange moved that week's expiry to the prior trading day. Stored here for
# traceability; the planner already reads expiry_date from option_contracts so it
# does not need to consult this set.
SHIFTED_EXPIRY_DAYS: Set[str] = {
    # 2026-01-15 (Thu) holiday -> SENSEX weekly moved to 2026-01-14 (Wed)
    "2026-01-14",
}

# Budget Day Saturdays - NSE conducts a special Saturday trading session for the Union Budget.
# These dates are weekend by calendar but full trading days by exchange.
SPECIAL_SATURDAY_SESSIONS: Set[str] = {
    "2025-02-01",  # Union Budget 2025-26
    "2026-02-01",  # Union Budget 2026-27 (declared Saturday session)
}

# Standard NSE/BSE regular session is 09:15-15:30 IST = 375 one-minute candles.
REGULAR_SESSION_CANDLES = 375

# Muhurat (Diwali) trading is a special ~1-hour evening session. These dates ARE
# trading days but have a reduced expected candle count, so a complete short
# session is not flagged red by the coverage audit/heatmap.
MUHURAT_SESSIONS: dict = {
    "2025-10-21": 60,   # Diwali Muhurat 2025: ~1h evening session, ~60 candles observed
}

ALL_HOLIDAYS: Set[str] = _HOLIDAYS_2024 | _HOLIDAYS_2025 | _HOLIDAYS_2026

# Human-readable labels for each holiday, keyed by ISO date. Used by the UI
# holiday-calendar modal. Kept alongside the holiday sets so they stay in sync.
HOLIDAY_LABELS: dict = {
    "2024-01-22": "Ram Mandir consecration",
    "2024-01-26": "Republic Day",
    "2024-03-08": "Mahashivratri",
    "2024-03-25": "Holi",
    "2024-03-29": "Good Friday",
    "2024-04-11": "Eid-ul-Fitr (Ramzan Id)",
    "2024-04-17": "Ram Navami",
    "2024-05-01": "Maharashtra Day",
    "2024-05-20": "Mumbai general elections",
    "2024-06-17": "Bakri Id",
    "2024-07-17": "Muharram",
    "2024-08-15": "Independence Day",
    "2024-10-02": "Mahatma Gandhi Jayanti",
    "2024-11-01": "Diwali Laxmi Pujan",
    "2024-11-15": "Gurunanak Jayanti",
    "2024-12-25": "Christmas",
    "2025-02-26": "Mahashivratri",
    "2025-03-14": "Holi",
    "2025-03-31": "Eid-ul-Fitr",
    "2025-04-10": "Mahavir Jayanti",
    "2025-04-14": "Dr. Ambedkar Jayanti",
    "2025-04-18": "Good Friday",
    "2025-05-01": "Maharashtra Day",
    "2025-08-15": "Independence Day",
    "2025-08-27": "Ganesh Chaturthi",
    "2025-10-02": "Mahatma Gandhi Jayanti / Dussehra",
    "2025-10-22": "Diwali Laxmi Pujan",
    "2025-11-05": "Gurunanak Jayanti",
    "2025-12-25": "Christmas",
    "2026-01-15": "Maharashtra civic elections",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Eid-ul-Fitr",
    "2026-03-31": "Mahavir Jayanti / Annual close",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Eid-ul-Adha (Bakri Id)",
    "2026-08-15": "Independence Day",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-11-08": "Diwali Laxmi Pujan",
    "2026-12-25": "Christmas",
}

# Human-readable labels for the special Saturday trading sessions.
SPECIAL_SATURDAY_LABELS: dict = {
    "2025-02-01": "Union Budget 2025-26 (special session)",
    "2026-02-01": "Union Budget 2026-27 (special session)",
}


def calendar_for_year(year: int) -> dict:
    """Return the market-calendar exceptions for a given year for the UI modal.

    Output:
      {
        "year": 2026,
        "verified_through": 2026,
        "holidays": [{"date": "2026-01-26", "label": "Republic Day", "weekday": "Monday"}, ...],
        "special_sessions": [{"date": "2026-02-01", "label": "...", "weekday": "Sunday"}, ...],
        "holiday_count": N,
      }
    """
    prefix = f"{int(year):04d}-"
    holidays = []
    for iso in sorted(h for h in ALL_HOLIDAYS if h.startswith(prefix)):
        d = date.fromisoformat(iso)
        holidays.append({
            "date": iso,
            "label": HOLIDAY_LABELS.get(iso, "Market holiday"),
            "weekday": d.strftime("%A"),
        })
    sessions = []
    for iso in sorted(s for s in SPECIAL_SATURDAY_SESSIONS if s.startswith(prefix)):
        d = date.fromisoformat(iso)
        sessions.append({
            "date": iso,
            "label": SPECIAL_SATURDAY_LABELS.get(iso, "Special trading session"),
            "weekday": d.strftime("%A"),
        })
    return {
        "year": int(year),
        "verified_through": YEAR_LAST_VERIFIED,
        "holidays": holidays,
        "special_sessions": sessions,
        "holiday_count": len(holidays),
    }


def available_calendar_years() -> List[int]:
    """Years for which we have a curated holiday list, ascending."""
    years = {int(h[:4]) for h in ALL_HOLIDAYS}
    return sorted(years)


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


# Regular session bounds in IST minutes-of-day (09:15 .. 15:30).
SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_CLOSE_MIN = 15 * 60 + 30


def market_status(now_ist) -> dict:
    """Regular-session market status for a given IST datetime — the single,
    holiday-aware source of "is the market open right now?" so the UI never has
    to guess. `now_ist` is a naive/aware datetime already shifted to IST.

    Phases: weekend | holiday | pre_open | open | closed. Muhurat evening
    sessions are intentionally not modeled (rare; is_trading_day still True, so
    a Muhurat-only day reads as 'closed' during regular hours — acceptable).
    """
    iso = now_ist.strftime("%Y-%m-%d")
    minutes = now_ist.hour * 60 + now_ist.minute
    trading = is_trading_day(iso)
    if not trading:
        phase = "weekend" if now_ist.weekday() >= 5 else "holiday"
    elif minutes < SESSION_OPEN_MIN:
        phase = "pre_open"
    elif minutes < SESSION_CLOSE_MIN:
        phase = "open"
    else:
        phase = "closed"
    return {
        "is_open": phase == "open",
        "phase": phase,
        "is_trading_day": trading,
        "session_open_ist": "09:15",
        "session_close_ist": "15:30",
        "now_ist": now_ist.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
    }


def expected_candle_count(iso_date: str) -> int:
    """Expected 1-minute candle count for a date.

    Returns 0 for non-trading days (weekend/holiday, unless a special session),
    a reduced count for known short sessions (Muhurat), and the full regular
    session count otherwise. Used by the coverage audit/heatmap so weekends and
    holidays are not flagged red and short sessions are not penalized.
    """
    if iso_date in MUHURAT_SESSIONS:
        return int(MUHURAT_SESSIONS[iso_date])
    if not is_trading_day(iso_date):
        return 0
    return REGULAR_SESSION_CANDLES


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
