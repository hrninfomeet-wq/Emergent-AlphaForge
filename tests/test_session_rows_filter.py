"""Off-session rows must not reach anything that reads raw candles.

Storage is written straight from the vendor with no session filter, so stale-feed
artifacts land in it — bars stamped 23:47, 00:09, or 15:30 on a pre-CAS day. The
chart has always dropped them; `load_candles_df` did not, so a backtest treated
23:47 as a market minute while the chart showed a clean session.

The subtle requirement is that "off-session" is **date- and segment-dependent**:
15:30 is an artifact before 2026-08-03 and a legitimate option bar after it.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.session_spec import OPTIONS, SPOT, session_rows_mask  # noqa: E402

PRE_CAS = "2026-07-31"     # Friday
POST_CAS = "2026-08-03"    # Monday, first CAS day
SATURDAY = "2026-08-08"
HOLIDAY = "2026-10-02"
MUHURAT = "2025-10-21"


def _frame(*stamps):
    """Frame from ("YYYY-MM-DD", "HH:MM") pairs."""
    ts = [
        int(pd.Timestamp(f"{d} {t}", tz="Asia/Kolkata").timestamp() * 1000)
        for d, t in stamps
    ]
    return pd.DataFrame({"ts": ts, "close": [100.0] * len(ts)})


def _kept(frame, segment=SPOT):
    mask = session_rows_mask(frame, segment)
    ist = pd.to_datetime(frame.loc[mask, "ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    return set(ist.dt.strftime("%Y-%m-%d %H:%M"))


# --- ordinary session bars survive ------------------------------------------

def test_keeps_every_bar_inside_the_session():
    f = _frame((PRE_CAS, "09:15"), (PRE_CAS, "12:00"), (PRE_CAS, "15:29"))
    assert len(_kept(f)) == 3


def test_drops_bars_before_the_open():
    f = _frame((PRE_CAS, "09:14"), (PRE_CAS, "09:15"))
    assert _kept(f) == {f"{PRE_CAS} 09:15"}


# --- the actual artifacts found in storage ----------------------------------

def test_drops_the_late_night_artifacts():
    """2026-05-29 carried bars out to 23:47; 2026-06-01 one at 00:09."""
    f = _frame(("2026-05-29", "15:29"), ("2026-05-29", "16:30"), ("2026-05-29", "23:47"),
               ("2026-06-01", "00:09"), ("2026-06-01", "09:15"))
    assert _kept(f) == {"2026-05-29 15:29", "2026-06-01 09:15"}


def test_drops_the_1539_and_1559_artifacts_on_pre_cas_days():
    f = _frame(("2025-04-25", "15:29"), ("2025-04-25", "15:39"), ("2025-04-25", "15:59"))
    assert _kept(f) == {"2025-04-25 15:29"}


# --- date-awareness: the same clock time flips meaning ----------------------

def test_1530_is_an_artifact_before_cas_and_legitimate_after_for_options():
    """The 16 stray option rows sit at 15:30 on 2025-11-03 — off-session then,
    in-session from 2026-08-03."""
    before = _frame(("2025-11-03", "15:30"))
    after = _frame((POST_CAS, "15:30"))
    assert _kept(before, OPTIONS) == set()
    assert _kept(after, OPTIONS) == {f"{POST_CAS} 15:30"}


def test_options_keep_the_extended_window_but_spot_does_not():
    f = _frame((POST_CAS, "15:35"), (POST_CAS, "15:39"))
    assert len(_kept(f, OPTIONS)) == 2
    assert _kept(f, SPOT) == set()


def test_options_close_is_still_exclusive_at_1540():
    assert _kept(_frame((POST_CAS, "15:40")), OPTIONS) == set()


# --- non-trading days -------------------------------------------------------

def test_drops_weekend_and_holiday_rows_entirely():
    f = _frame((SATURDAY, "11:00"), (HOLIDAY, "11:00"), (PRE_CAS, "11:00"))
    assert _kept(f) == {f"{PRE_CAS} 11:00"}


# --- the exemption that prevents destroying a session -----------------------

def test_short_sessions_keep_every_bar_whatever_the_clock():
    """Muhurat bounds are not modeled — the exchange has scheduled it in the
    afternoon and in the evening in different years. Filtering it on an assumed
    window would silently delete a whole session."""
    f = _frame((MUHURAT, "13:45"), (MUHURAT, "14:44"), (MUHURAT, "18:30"), (MUHURAT, "19:15"))
    assert len(_kept(f)) == 4


# --- shape / robustness -----------------------------------------------------

def test_empty_frame_returns_an_empty_mask():
    assert len(session_rows_mask(pd.DataFrame({"ts": []}))) == 0
    assert len(session_rows_mask(pd.DataFrame())) == 0
    assert len(session_rows_mask(None)) == 0


def test_mask_is_bool_and_aligned_to_the_frame():
    f = _frame((PRE_CAS, "09:15"), (PRE_CAS, "23:47"))
    mask = session_rows_mask(f)
    assert mask.dtype == bool
    assert list(mask.index) == list(f.index)
    assert list(mask) == [True, False]
