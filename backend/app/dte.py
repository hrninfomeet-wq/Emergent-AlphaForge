"""Days-to-expiry (DTE) classification for option backtests.

DTE answers "how many trading days before the relevant weekly expiry did this
session occur?" It is computed from stored option-contract expiry metadata and
the NSE/BSE trading calendar — never from a hard-coded weekday rule, because the
NIFTY/BANKNIFTY/SENSEX expiry weekdays have rotated over time and shift on
holidays.

Convention:
  - DTE 0  = the session IS the expiry day (same-day / 0DTE).
  - DTE 1  = one trading day before expiry (e.g. Monday for a Tuesday expiry).
  - DTE n  = n trading days before expiry.

A session maps to the NEAREST upcoming expiry (the first expiry_date >= the
session date). Sessions after the last known expiry return None (unknown DTE).

This module is pure: it takes a list of expiry ISO strings and a date and
returns an int. The DB lookup of expiries lives in the caller.
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from app.nse_calendar import trading_days_in_range


def compute_dte(trade_date_iso: str, expiry_dates_sorted: List[str]) -> Optional[int]:
    """Trading-day distance from a session to its nearest upcoming expiry.

    `expiry_dates_sorted` must be ascending ISO date strings. Returns None when
    no expiry on or after the session is known.
    """
    expiry = next((e for e in expiry_dates_sorted if e >= trade_date_iso), None)
    if expiry is None:
        return None
    # trading_days_in_range is inclusive of both endpoints, so [D..E] has
    # (DTE + 1) entries; subtract 1 to get the day count to expiry.
    span = trading_days_in_range(trade_date_iso, expiry)
    if not span:
        return None
    return max(0, len(span) - 1)


def _normalize_single_dte(value) -> Optional[int]:
    """Parse one DTE token into an int (None for "all"/unset/garbage)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        iv = int(value)
        return iv if iv >= 0 else None
    s = str(value).strip().lower()
    if s in ("", "all"):
        return None
    if s.startswith("dte"):
        s = s[3:]
    try:
        iv = int(s)
        return iv if iv >= 0 else None
    except ValueError:
        return None


def normalize_dte_filter(value) -> Optional[frozenset]:
    """Parse a DTE filter selection into a set of ints, or None for "all"/unset.

    Accepts a single token (None, "all", "ALL", "dte2", "DTE2", "2", 2) or a
    list/tuple/set of tokens ([0, 1, 2], ["dte0", "dte1"]) for multi-DTE
    selection. Invalid entries inside a list are ignored; an empty list or a
    list with no valid entries means "all" (None), matching the single-token
    behavior for "all"/garbage.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple, set, frozenset)):
        out = {d for d in (_normalize_single_dte(v) for v in value) if d is not None}
        return frozenset(out) if out else None
    single = _normalize_single_dte(value)
    return frozenset({single}) if single is not None else None


def dte_matches(dte: Optional[int], selected) -> bool:
    """True if a session's DTE matches the selected filter ("all" matches any)."""
    target = normalize_dte_filter(selected)
    if target is None:
        return True  # "all"
    return dte is not None and dte in target


def sessions_matching_dte(
    session_dates: Iterable[str],
    expiry_dates_sorted: List[str],
    selected,
) -> List[str]:
    """Filter session ISO dates to those whose DTE matches the selection."""
    target = normalize_dte_filter(selected)
    if target is None:
        return list(session_dates)
    out: List[str] = []
    for d in session_dates:
        if compute_dte(d, expiry_dates_sorted) in target:
            out.append(d)
    return out
