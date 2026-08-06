"""Live guards must watch until the derivatives close, not the cash close.

From 2026-08-03 the F&O segment trades until 15:40. A guard bounded at 15:30
would stand down while the option position it protects is still tradeable for
another ten minutes — the position would be unwatched precisely when the auction
result is repricing it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.live_position_guard import _in_market_hours as guard_hours  # noqa: E402
from app.live.live_sl_monitor import _in_market_hours as sl_hours  # noqa: E402
from app.live_exit_monitor import _in_market_hours as exit_hours  # noqa: E402

_IST = timedelta(hours=5, minutes=30)

ALL_GUARDS = pytest.mark.parametrize(
    "in_market_hours",
    [guard_hours, sl_hours, exit_hours],
    ids=["position_guard", "sl_monitor", "exit_monitor"],
)


def _utc(date_iso: str, hh: int, mm: int) -> datetime:
    """UTC instant for an IST wall-clock time."""
    naive_ist = datetime.fromisoformat(f"{date_iso}T{hh:02d}:{mm:02d}:00")
    return (naive_ist - _IST).replace(tzinfo=timezone.utc)


BEFORE = "2026-07-31"   # Friday, pre-CAS
AFTER = "2026-08-03"    # Monday, first CAS day


@ALL_GUARDS
def test_pre_cas_still_stands_down_at_1530(in_market_hours):
    assert in_market_hours(_utc(BEFORE, 15, 29))
    assert not in_market_hours(_utc(BEFORE, 15, 30))
    assert not in_market_hours(_utc(BEFORE, 15, 35))


@ALL_GUARDS
def test_post_cas_keeps_watching_through_the_extended_session(in_market_hours):
    """The regression this guards: an open option position between 15:30 and
    15:40 must not be left unwatched."""
    for hh, mm in ((15, 30), (15, 35), (15, 39)):
        assert in_market_hours(_utc(AFTER, hh, mm)), f"unguarded at {hh}:{mm}"


@ALL_GUARDS
def test_post_cas_stands_down_once_derivatives_close(in_market_hours):
    assert not in_market_hours(_utc(AFTER, 15, 40))
    assert not in_market_hours(_utc(AFTER, 16, 0))


@ALL_GUARDS
def test_session_open_bound_is_unchanged(in_market_hours):
    for iso in (BEFORE, AFTER):
        assert not in_market_hours(_utc(iso, 9, 14))
        assert in_market_hours(_utc(iso, 9, 15))


@ALL_GUARDS
def test_weekends_are_still_closed(in_market_hours):
    assert not in_market_hours(_utc("2026-08-08", 12, 0))   # Saturday
    assert not in_market_hours(_utc("2026-08-09", 12, 0))   # Sunday
