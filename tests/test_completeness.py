"""Tests for the daily ATM-band completeness definition (app/completeness.py).

This module is the fix for the 2026-06-12 root cause: hygiene judged option
coverage per-day/per-expiry while spot sweeps several strikes intraday, so
partially-covered days reported "verified" and backtests hit
MISSING_ENTRY_CANDLE on volatile sessions (e.g. NIFTY 2026-05-20: spot range
23397-23691, but only one strike per day was ever fetched).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.completeness import (  # noqa: E402
    band_completeness,
    expected_pairs_for_day,
    missing_band_pairs,
    resolve_expiry_for_day,
    strike_band,
)


# ---- strike_band -------------------------------------------------------------

def test_band_covers_swept_strikes_with_pad():
    # The real 2026-05-20 NIFTY day: low 23397.3, high 23690.9, step 50.
    band = strike_band(23397.3, 23690.9, 50, pad_steps=1)
    assert band[0] == 23350   # round(23397.3)=23400, minus 1 pad step
    assert band[-1] == 23750  # round(23690.9)=23700, plus 1 pad step
    assert 23550 in band      # the strike the backtest needed at 09:51
    assert band == sorted(band) and len(set(band)) == len(band)


def test_band_uses_nearest_rounding_like_the_fetch_path():
    # low=23377 rounds to 23400 (NEAREST, not floor) — the per-minute ATM
    # selection in the planner can never pick 23300, so completeness must not
    # demand it either (it would be permanently "missing").
    band = strike_band(23377, 23377, 50, pad_steps=1)
    assert band == [23350, 23400, 23450]


def test_band_flat_day_is_three_strikes():
    assert strike_band(24000, 24000, 50, pad_steps=1) == [23950, 24000, 24050]


def test_band_handles_inverted_and_invalid_inputs():
    assert strike_band(24100, 24000, 50) == strike_band(24000, 24100, 50)
    assert strike_band(None, 24000, 50) == []
    assert strike_band(24000, 24100, 0) == []


# ---- expiry resolution ---------------------------------------------------------

def test_resolve_expiry_next_available():
    exps = ["2026-05-19", "2026-05-26", "2026-06-02"]
    assert resolve_expiry_for_day("2026-05-20", exps) == "2026-05-26"
    assert resolve_expiry_for_day("2026-05-26", exps) == "2026-05-26"
    assert resolve_expiry_for_day("2026-06-03", exps) is None


# ---- expected pairs ------------------------------------------------------------

def test_expected_pairs_for_day_both_legs():
    pairs = expected_pairs_for_day(
        "2026-05-20", 23397.3, 23690.9, step=50,
        expiries_sorted=["2026-05-26"], legs=("CE", "PE"), pad_steps=1,
    )
    assert ("2026-05-20", "2026-05-26", "CE", 23550) in pairs
    assert ("2026-05-20", "2026-05-26", "PE", 23550) in pairs
    strikes = {k for (_, _, _, k) in pairs}
    assert len(pairs) == len(strikes) * 2


def test_expected_pairs_empty_without_expiry():
    assert expected_pairs_for_day("2026-06-03", 24000, 24100, step=50,
                                  expiries_sorted=["2026-06-02"]) == set()


# ---- band_completeness / missing_band_pairs ------------------------------------

DAY_ROWS = [
    {"date": "2026-05-20", "count": 375, "low": 23397.3, "high": 23690.9},
    {"date": "2026-05-21", "count": 375, "low": 23596.6, "high": 23859.9},
]
EXPIRIES = ["2026-05-26"]


def _full_pairs(day_rows):
    pairs = set()
    for r in day_rows:
        pairs |= expected_pairs_for_day(
            r["date"], r["low"], r["high"], step=50,
            expiries_sorted=EXPIRIES, pad_steps=1,
        )
    return pairs


def test_completeness_verified_when_band_fully_stored():
    stored = _full_pairs(DAY_ROWS)
    out = band_completeness(DAY_ROWS, expiries_sorted=EXPIRIES, stored_pairs=stored, step=50)
    assert out["missing_pairs"] == 0
    assert out["coverage_pct"] == 100.0
    assert out["judged_days"] == 2


def test_completeness_finds_the_real_missing_strikes():
    # Remove exactly the three pairs the confluence-10 run needed.
    stored = _full_pairs(DAY_ROWS)
    stored -= {
        ("2026-05-20", "2026-05-26", "CE", 23550),
        ("2026-05-21", "2026-05-26", "CE", 23800),
        ("2026-05-21", "2026-05-26", "PE", 23700),
    }
    out = band_completeness(DAY_ROWS, expiries_sorted=EXPIRIES, stored_pairs=stored, step=50)
    assert out["missing_pairs"] == 3
    assert out["missing_by_month"] == {"2026-05": 3}
    missing = missing_band_pairs(DAY_ROWS, expiries_sorted=EXPIRIES, stored_pairs=stored, step=50)
    assert ("2026-05-20", "2026-05-26", "CE", 23550) in missing
    assert len(missing) == 3


def test_completeness_skips_in_progress_and_thin_days():
    rows = DAY_ROWS + [
        {"date": "2026-05-22", "count": 40, "low": 23700, "high": 23800},   # thin spot day
        {"date": "2026-05-25", "count": 375, "low": 23700, "high": 23800},  # after judge_until
    ]
    out = band_completeness(
        rows, expiries_sorted=EXPIRIES, stored_pairs=_full_pairs(DAY_ROWS),
        step=50, judge_until="2026-05-21",
    )
    assert out["judged_days"] == 2
    assert out["missing_pairs"] == 0


def test_completeness_empty_inputs():
    out = band_completeness([], expiries_sorted=[], stored_pairs=set(), step=50)
    assert out["planned_pairs"] == 0
    assert out["coverage_pct"] == 100.0
