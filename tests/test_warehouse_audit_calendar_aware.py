"""The spot audit must expect what each date actually holds, for any window.

`summarize_audit_days` used a flat 375 for every date while `get_coverage` already
resolved per-day counts. That made the audit lie about any window containing a short
or non-trading session — the 2025-10-21 Muhurat evening session reported "incomplete"
at 60/375 when 60 is exactly right.

These pin the per-day resolution so an arbitrary user-chosen window reports honestly.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import warehouse  # noqa: E402
from app.session_spec import OPTIONS  # noqa: E402

MUHURAT = "2025-10-21"        # ~60-bar evening session
DIWALI = "2025-10-22"         # holiday, no session
SATURDAY = "2026-08-08"
REGULAR = "2026-05-18"        # ordinary Monday
POST_CAS = "2026-08-03"


def _audit(dates, stored, *, hashes=True, **kw):
    h = {d: "h" for d in dates} if hashes else {}
    return warehouse.summarize_audit_days(
        instrument="NIFTY",
        expected_dates=list(dates),
        stored_counts=stored,
        stored_hashes=dict(h),
        computed_hashes=dict(h),
        **kw,
    )


def _by_date(result):
    return {d["date"]: d for d in result["days"]}


# --- the bug this fixes -----------------------------------------------------

def test_muhurat_session_is_complete_at_its_real_length():
    """60 stored on a 60-bar session is complete, not 'incomplete at 60/375'."""
    day = _by_date(_audit([MUHURAT], {MUHURAT: 60}))[MUHURAT]
    assert day["expected_candles"] == 60
    assert day["coverage_pct"] == 100.0
    assert day["status"] == "ok"


def test_muhurat_session_short_of_its_own_length_is_still_incomplete():
    day = _by_date(_audit([MUHURAT], {MUHURAT: 40}))[MUHURAT]
    assert day["status"] == "incomplete"
    assert day["coverage_pct"] < 100.0


def test_a_window_containing_a_muhurat_day_can_read_complete():
    res = _audit([REGULAR, MUHURAT], {REGULAR: 375, MUHURAT: 60})
    assert res["summary"]["complete"] is True
    assert res["summary"]["expected_candles"] == 435       # 375 + 60, not 750


# --- non-trading days inside a user-chosen window ---------------------------

def test_holiday_in_the_window_is_not_a_shortfall():
    res = _audit([REGULAR, DIWALI], {REGULAR: 375})
    days = _by_date(res)
    assert days[DIWALI]["expected_candles"] == 0
    assert days[DIWALI]["status"] == "not_a_session"
    assert res["summary"]["missing_days"] == 0
    assert res["summary"]["complete"] is True
    assert res["summary"]["session_days"] == 1


def test_data_stored_on_a_closed_day_is_surfaced_as_an_anomaly():
    """Stray weekend rows reach storage from live/retry paths. Nothing was due,
    so this is not 'incomplete' — it is data that should not exist."""
    res = _audit([REGULAR, SATURDAY], {REGULAR: 375, SATURDAY: 12})
    days = _by_date(res)
    assert days[SATURDAY]["status"] == "unexpected_session"
    assert days[SATURDAY]["coverage_pct"] == 0.0
    assert res["summary"]["unexpected_session_days"] == 1
    assert res["summary"]["complete"] is False


def test_a_window_of_only_closed_days_is_never_reported_complete():
    res = _audit([DIWALI, SATURDAY], {})
    assert res["summary"]["session_days"] == 0
    assert res["summary"]["complete"] is False


# --- summary honesty --------------------------------------------------------

def test_expected_candles_is_the_true_sum_not_a_flat_multiple():
    res = _audit([REGULAR, MUHURAT, DIWALI], {})
    assert res["summary"]["expected_candles"] == 375 + 60 + 0


def test_expected_per_day_is_none_when_the_window_mixes_session_lengths():
    assert _audit([REGULAR, MUHURAT], {})["summary"]["expected_per_day"] is None
    assert _audit([REGULAR], {})["summary"]["expected_per_day"] == 375


def test_an_explicit_expected_per_day_still_pins_every_date():
    """Backward compatibility for callers that deliberately want a flat basis."""
    res = _audit([MUHURAT], {MUHURAT: 60}, expected_per_day=375)
    assert _by_date(res)[MUHURAT]["expected_candles"] == 375
    assert _by_date(res)[MUHURAT]["status"] == "incomplete"


def test_segment_lets_the_same_helper_audit_option_days():
    res = _audit([POST_CAS], {POST_CAS: 385}, segment=OPTIONS)
    day = _by_date(res)[POST_CAS]
    assert day["expected_candles"] == 385
    assert day["status"] == "ok"


def test_spot_is_unaffected_by_the_cas_change():
    day = _by_date(_audit([POST_CAS], {POST_CAS: 375}))[POST_CAS]
    assert day["expected_candles"] == 375
    assert day["status"] == "ok"


# --- unchanged behaviour on ordinary windows --------------------------------

def test_ordinary_trading_days_classify_exactly_as_before():
    res = warehouse.summarize_audit_days(
        instrument="NIFTY",
        expected_dates=["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21"],
        stored_counts={"2026-05-18": 375, "2026-05-19": 200, "2026-05-20": 375},
        stored_hashes={"2026-05-18": "ok", "2026-05-19": "short", "2026-05-20": "stored"},
        computed_hashes={"2026-05-18": "ok", "2026-05-19": "short", "2026-05-20": "computed"},
    )
    days = _by_date(res)
    assert days["2026-05-18"]["status"] == "ok"
    assert days["2026-05-19"]["status"] == "incomplete"
    assert days["2026-05-20"]["status"] == "hash_mismatch"
    assert days["2026-05-21"]["status"] == "missing"
    assert res["summary"]["complete"] is False
