"""Completeness is not freshness — the check that was missing.

`live_feed_health.py:20` measures only `FRESH_THRESHOLD_SEC = 120`: the AGE of the
newest bar. A backend booting mid-session starts the tick roller, gets a bar within
two minutes, and reports LIVE — while the entire morning is absent.

That is exactly what happened on 2026-08-14. The backend booted ~09:49 and NIFTY
stored 340 bars beginning at 09:50, a clean 35-bar leading hole with no interior
gap. Nothing detected it during the session; the warehouse was only repaired at
09:13 IST the FOLLOWING day, by which time every trading decision had been made.

This module answers "which minutes of today are missing", from `ts` alone. It is
pure: no DB, no clock, no network — the caller supplies `now_ms` — so the session
arithmetic is directly testable, including the cases that actually bite (holidays,
the in-progress minute, and the 2026-08-03 CAS split that made spot 375 bars and
options 385).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.candle_gap import (  # noqa: E402
    assess_completeness,
    expected_minute_ts,
    missing_ranges,
)
from app.session_spec import OPTIONS, SPOT  # noqa: E402

MIN_MS = 60_000
# 2026-08-14 09:15:00 IST in epoch ms (a Friday, a trading day). Verified against
# the warehouse: the first NIFTY bar that day was ts=1786681200000 = 09:50 IST,
# exactly 35 minutes later — the hole this module exists to find.
OPEN_0814 = 1786679100000


def _ist(hh, mm, base=OPEN_0814):
    """Epoch ms for hh:mm IST on 2026-08-14."""
    return base + ((hh * 60 + mm) - (9 * 60 + 15)) * MIN_MS


# --------------------------------------------------------------------------
# The expected grid
# --------------------------------------------------------------------------

def test_a_full_spot_session_is_375_minutes():
    ts = expected_minute_ts("2026-08-14", segment=SPOT)
    assert len(ts) == 375
    assert ts[0] == OPEN_0814                      # 09:15
    assert ts[-1] == _ist(15, 29)                  # 15:29 inclusive


def test_a_full_options_session_is_385_minutes_after_the_cas_split():
    ts = expected_minute_ts("2026-08-14", segment=OPTIONS)
    assert len(ts) == 385
    assert ts[-1] == _ist(15, 39)


def test_the_grid_is_strictly_one_minute_apart_and_ascending():
    ts = expected_minute_ts("2026-08-14", segment=SPOT)
    assert all(b - a == MIN_MS for a, b in zip(ts, ts[1:]))


def test_a_holiday_expects_no_candles_at_all():
    """Reporting a gap every holiday would train the operator to ignore the signal."""
    assert expected_minute_ts("2026-01-26", segment=SPOT) == []   # Republic Day


def test_a_sunday_expects_no_candles():
    assert expected_minute_ts("2026-08-16", segment=SPOT) == []


# --------------------------------------------------------------------------
# The in-progress minute must never be expected
# --------------------------------------------------------------------------

def test_mid_session_expects_only_minutes_that_have_CLOSED():
    """At 10:31:30 the 10:31 bar is still forming. Demanding it would make every
    healthy live system look permanently incomplete."""
    ts = expected_minute_ts("2026-08-14", segment=SPOT, now_ms=_ist(10, 31) + 30_000)
    assert ts[-1] == _ist(10, 30)


def test_exactly_on_a_boundary_the_just_closed_minute_is_expected():
    ts = expected_minute_ts("2026-08-14", segment=SPOT, now_ms=_ist(10, 31))
    assert ts[-1] == _ist(10, 30)


def test_before_the_open_expects_nothing():
    assert expected_minute_ts("2026-08-14", segment=SPOT, now_ms=_ist(9, 0)) == []


def test_after_the_close_expects_the_whole_session():
    ts = expected_minute_ts("2026-08-14", segment=SPOT, now_ms=_ist(16, 0))
    assert len(ts) == 375


# --------------------------------------------------------------------------
# Gap ranges
# --------------------------------------------------------------------------

def test_the_2026_08_14_leading_hole_is_found():
    """The real case: 340 bars starting 09:50, no interior gap."""
    have = {_ist(9, 50) + i * MIN_MS for i in range(340)}
    ranges = missing_ranges(have, expected_minute_ts("2026-08-14", segment=SPOT))
    assert len(ranges) == 1
    start, end, count = ranges[0]
    assert start == _ist(9, 15) and end == _ist(9, 49) and count == 35


def test_interior_gaps_are_reported_separately():
    exp = expected_minute_ts("2026-08-14", segment=SPOT)
    have = set(exp) - {_ist(10, 0), _ist(10, 1), _ist(12, 30)}
    ranges = missing_ranges(have, exp)
    assert [(r[0], r[2]) for r in ranges] == [(_ist(10, 0), 2), (_ist(12, 30), 1)]


def test_a_complete_day_has_no_ranges():
    exp = expected_minute_ts("2026-08-14", segment=SPOT)
    assert missing_ranges(set(exp), exp) == []


def test_extra_bars_outside_the_session_are_ignored_not_errors():
    """The roller drops out-of-session ticks, but a manual ingest might not."""
    exp = expected_minute_ts("2026-08-14", segment=SPOT)
    have = set(exp) | {_ist(8, 0), _ist(16, 0)}
    assert missing_ranges(have, exp) == []


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------

def test_the_verdict_on_the_real_2026_08_14_hole():
    have = {_ist(9, 50) + i * MIN_MS for i in range(340)}
    v = assess_completeness("2026-08-14", have, segment=SPOT)
    assert v["complete"] is False
    assert v["expected"] == 375 and v["present"] == 340 and v["missing"] == 35
    assert v["coverage_pct"] == 90.67
    assert v["first_missing"] == _ist(9, 15)
    assert len(v["ranges"]) == 1


def test_a_complete_day_is_complete():
    exp = expected_minute_ts("2026-08-14", segment=SPOT)
    v = assess_completeness("2026-08-14", set(exp), segment=SPOT)
    assert v["complete"] is True
    assert v["missing"] == 0 and v["coverage_pct"] == 100.0
    assert v["ranges"] == [] and v["first_missing"] is None


def test_a_holiday_is_complete_not_broken():
    v = assess_completeness("2026-01-26", set(), segment=SPOT)
    assert v["complete"] is True
    assert v["expected"] == 0 and v["reason"] == "not_a_trading_day"


def test_pre_open_is_complete_not_broken():
    v = assess_completeness("2026-08-14", set(), segment=SPOT, now_ms=_ist(9, 0))
    assert v["complete"] is True
    assert v["expected"] == 0 and v["reason"] == "before_open"


def test_a_live_session_with_every_closed_minute_present_is_complete():
    """The healthy mid-session state must NOT read as a gap."""
    now = _ist(10, 31) + 30_000
    have = set(expected_minute_ts("2026-08-14", segment=SPOT, now_ms=now))
    v = assess_completeness("2026-08-14", have, segment=SPOT, now_ms=now)
    assert v["complete"] is True and v["missing"] == 0


def test_ranges_are_capped_so_a_pathological_day_cannot_flood_the_report():
    exp = expected_minute_ts("2026-08-14", segment=SPOT)
    have = {t for i, t in enumerate(exp) if i % 2 == 0}      # 188 separate holes
    v = assess_completeness("2026-08-14", have, segment=SPOT)
    assert v["missing"] == 187
    assert len(v["ranges"]) <= 20
    assert v["ranges_truncated"] is True


def test_the_verdict_never_raises_on_junk_input():
    for have in (set(), {None}, {"x"}, {1.5}, {-1}):
        v = assess_completeness("2026-08-14", have, segment=SPOT)
        assert isinstance(v["complete"], bool)
