"""Tests for date- and segment-aware session bounds (SEBI CAS, 2026-08-03).

The load-bearing property here is the boundary: 2026-07-31 must resolve to the
pre-CAS shape (375 bars, 15:30 close, no auction) so stored history and every
backtest built on it keep computing as before, while 2026-08-03 onward picks up
the split session.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.nse_calendar import market_status  # noqa: E402
from app.session_spec import (  # noqa: E402
    CAS_EFFECTIVE_ISO,
    LEGACY_SESSION_CANDLES,
    OPTIONS,
    OPTIONS_SESSION_CANDLES_CAS,
    SPOT,
    cas_in_effect,
    expected_candle_count,
    session_spec,
)

# 2026-07-31 Friday and 2026-08-03 Monday: consecutive trading days straddling
# the change. Neither is a holiday in nse_calendar.
BEFORE = "2026-07-31"
AFTER = "2026-08-03"

M_1515 = 15 * 60 + 15
M_1529 = 15 * 60 + 29
M_1530 = 15 * 60 + 30
M_1539 = 15 * 60 + 39


def test_cas_effective_date_is_the_sebi_start_date():
    assert CAS_EFFECTIVE_ISO == "2026-08-03"
    assert not cas_in_effect(BEFORE)
    assert cas_in_effect(AFTER)
    assert cas_in_effect("2026-12-31")


# --- the day before: nothing may change ------------------------------------

def test_pre_cas_spot_is_the_legacy_session():
    spec = session_spec(BEFORE, SPOT)
    assert spec.close_min == M_1530
    assert spec.expected_candles == LEGACY_SESSION_CANDLES == 375
    assert not spec.has_cas


def test_pre_cas_options_match_spot_exactly():
    """Before the change both segments shared one shape — that is what all
    stored option history was audited against."""
    assert session_spec(BEFORE, OPTIONS) == session_spec(BEFORE, SPOT)


def test_pre_cas_afternoon_minutes_are_never_flagged_as_auction():
    spec = session_spec(BEFORE, SPOT)
    for minute in (M_1515, 15 * 60 + 20, M_1529):
        assert not spec.in_cas(minute)


# --- from the effective date ------------------------------------------------

def test_post_cas_spot_keeps_375_bars_but_gains_an_auction_window():
    """The index feed still publishes to 15:30 — the bars just stop moving."""
    spec = session_spec(AFTER, SPOT)
    assert spec.close_min == M_1530
    assert spec.expected_candles == 375
    assert spec.has_cas
    assert spec.cas_start_min == M_1515
    assert spec.cas_end_min == M_1530


def test_post_cas_options_trade_ten_minutes_longer():
    spec = session_spec(AFTER, OPTIONS)
    assert spec.close_min == 15 * 60 + 40
    assert spec.expected_candles == OPTIONS_SESSION_CANDLES_CAS == 385
    assert spec.contains(M_1530)
    assert spec.contains(M_1539)
    assert not spec.contains(15 * 60 + 40)


def test_post_cas_options_have_no_auction():
    """No auction applies to the derivatives segment — it trades continuously
    straight through the cash auction."""
    spec = session_spec(AFTER, OPTIONS)
    assert not spec.has_cas
    for minute in (M_1515, M_1529, M_1539):
        assert not spec.in_cas(minute)


def test_post_cas_spot_auction_window_covers_the_frozen_and_jump_bars():
    """15:15-15:28 are frozen and 15:29 carries the auction close jump; all of
    them must be inside the masked window."""
    spec = session_spec(AFTER, SPOT)
    assert spec.in_cas(M_1515)
    assert spec.in_cas(15 * 60 + 28)
    assert spec.in_cas(M_1529)
    assert not spec.in_cas(15 * 60 + 14)
    assert not spec.in_cas(M_1530)


def test_the_two_segments_now_disagree_on_day_length():
    """The reason this module exists: spot and options are no longer the same
    number of bars, so a shared constant cannot describe both."""
    assert session_spec(AFTER, SPOT).expected_candles == 375
    assert session_spec(AFTER, OPTIONS).expected_candles == 385


# --- calendar interaction ---------------------------------------------------

def test_non_trading_days_expect_zero_in_both_segments():
    for iso in ("2026-08-08", "2026-08-09", "2026-10-02"):  # Sat, Sun, holiday
        assert session_spec(iso, SPOT).expected_candles == 0
        assert session_spec(iso, OPTIONS).expected_candles == 0


def test_muhurat_short_session_is_not_widened_by_the_options_rule():
    assert expected_candle_count("2025-10-21", OPTIONS) == 60


def test_unknown_segment_falls_back_to_the_narrower_spot_bound():
    """Guessing wrong should never widen a caller's window."""
    assert session_spec(AFTER, "futures").close_min == M_1530


def test_expected_candle_count_helper_matches_the_spec():
    assert expected_candle_count(AFTER, SPOT) == 375
    assert expected_candle_count(AFTER, OPTIONS) == 385
    assert expected_candle_count(BEFORE, OPTIONS) == 375


# --- market_status phases ---------------------------------------------------

def _status(iso: str, hh: int, mm: int) -> dict:
    return market_status(datetime.fromisoformat(f"{iso}T{hh:02d}:{mm:02d}:00"))


def test_pre_cas_phases_are_unchanged():
    assert _status(BEFORE, 9, 0)["phase"] == "pre_open"
    assert _status(BEFORE, 12, 0)["phase"] == "open"
    assert _status(BEFORE, 15, 20)["phase"] == "open"     # no auction existed yet
    assert _status(BEFORE, 15, 30)["phase"] == "closed"


def test_post_cas_day_reports_the_auction_and_derivatives_phases():
    assert _status(AFTER, 15, 14)["phase"] == "open"
    assert _status(AFTER, 15, 15)["phase"] == "cas"
    assert _status(AFTER, 15, 29)["phase"] == "cas"
    assert _status(AFTER, 15, 30)["phase"] == "derivatives_only"
    assert _status(AFTER, 15, 39)["phase"] == "derivatives_only"
    assert _status(AFTER, 15, 40)["phase"] == "closed"


def test_is_open_stays_true_while_options_are_tradeable():
    """The app trades options; they remain tradeable through both new phases."""
    for hh, mm in ((15, 20), (15, 35)):
        assert _status(AFTER, hh, mm)["is_open"] is True
    assert _status(AFTER, 15, 40)["is_open"] is False


def test_status_publishes_both_segment_closes():
    after = _status(AFTER, 12, 0)
    assert after["session_close_ist"] == "15:30"
    assert after["derivatives_close_ist"] == "15:40"
    assert after["cas_start_ist"] == "15:15"

    before = _status(BEFORE, 12, 0)
    assert before["derivatives_close_ist"] == "15:30"
    assert before["cas_start_ist"] is None


def test_holidays_and_weekends_still_win_over_the_new_phases():
    assert _status("2026-08-08", 15, 20)["phase"] == "weekend"
    assert _status("2026-10-02", 15, 20)["phase"] == "holiday"
