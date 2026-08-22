"""Tests for the shipped option-buying screen (`app.option_screen`).

The screen exists to stop a campaign from starting on a number that is not real.
These tests pin the three properties that make it worth trusting: excursions are
causal, statistics are per-session rather than per-bar, and the holdout cannot be
read by accident.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.option_screen import (  # noqa: E402
    BASE_RATE_MFE_MAE,
    HoldoutProtectionError,
    ScreenCell,
    SessionStat,
    causal_session_stat,
    chronological_split,
    classify_cell,
    forward_excursions,
    mfe_mae_ratio,
    net_hold_return_pct,
    screen_condition,
    session_level_stats,
    summarize_screen,
)


# ---------------------------------------------------------------------------
# forward_excursions
# ---------------------------------------------------------------------------

def test_excursion_window_excludes_the_entry_bar():
    """A bar cannot be entered at its close and also claim its own high."""
    high = np.array([100.0, 10.0, 10.0], dtype=float)
    low = np.array([1.0, 9.0, 9.0], dtype=float)
    close = np.array([10.0, 10.0, 10.0], dtype=float)

    mfe, mae, net = forward_excursions(high, low, close, horizon=2)

    # Entry at bar 0 (close 10). Window is bars 1..2, whose high is 10 and low 9.
    # The entry bar's own 100 high / 1 low must not leak in.
    assert mfe[0] == pytest.approx(0.0)
    assert mae[0] == pytest.approx(1.0)
    assert net[0] == pytest.approx(0.0)


def test_excursion_is_nan_where_the_horizon_does_not_fit():
    close = np.array([10.0, 11.0, 12.0], dtype=float)
    mfe, mae, net = forward_excursions(close.copy(), close.copy(), close, horizon=2)
    assert math.isfinite(mfe[0])
    for arr in (mfe, mae, net):
        assert math.isnan(arr[1])
        assert math.isnan(arr[2])


def test_excursions_are_clipped_at_zero():
    """A window that never traded above entry has MFE 0, not a negative number."""
    high = np.array([10.0, 8.0], dtype=float)
    low = np.array([10.0, 7.0], dtype=float)
    close = np.array([10.0, 8.0], dtype=float)
    mfe, mae, _ = forward_excursions(high, low, close, horizon=1)
    assert mfe[0] == pytest.approx(0.0)
    assert mae[0] == pytest.approx(3.0)


def test_non_positive_entry_premium_is_skipped():
    high = np.array([1.0, 5.0], dtype=float)
    low = np.array([0.0, 1.0], dtype=float)
    close = np.array([0.0, 5.0], dtype=float)
    mfe, _, _ = forward_excursions(high, low, close, horizon=1)
    assert math.isnan(mfe[0])


# ---------------------------------------------------------------------------
# mfe_mae_ratio
# ---------------------------------------------------------------------------

def test_ratio_is_of_medians_not_median_of_ratios():
    """Ratio-of-medians keeps zero-MAE bars in the sample instead of dropping them.

    Dropping them (as a per-bar ratio must) biases the statistic upward, which is
    exactly the direction that manufactures a false edge.
    """
    mfe = [1.0, 2.0, 3.0, 4.0]
    mae = [0.0, 0.0, 2.0, 4.0]          # two zero-MAE bars
    # medians: MFE 2.5, MAE 1.0 -> 2.5
    assert mfe_mae_ratio(mfe, mae) == pytest.approx(2.5)


def test_zero_mae_with_positive_mfe_is_unbounded_not_missing():
    """A cell that never traded against the entry is the BEST case, not a blank.

    Collapsing it to None would have the screen report NO_EDGE for the strongest
    series it could ever be handed.
    """
    assert mfe_mae_ratio([1.0, 2.0], [0.0, 0.0]) == math.inf
    assert classify_cell(mfe_mae=math.inf, stat=_stat()) == "CANDIDATE"


def test_ratio_none_when_nothing_moved_either_way():
    assert mfe_mae_ratio([0.0, 0.0], [0.0, 0.0]) is None


def test_ratio_none_on_empty_input():
    assert mfe_mae_ratio([], []) is None


# ---------------------------------------------------------------------------
# net_hold_return_pct
# ---------------------------------------------------------------------------

def test_spread_is_paid_on_both_sides():
    """A flat premium round trip loses roughly two half-spreads, never zero."""
    r = net_hold_return_pct(entry_premium=100.0, exit_premium=100.0,
                            spread_pct_per_side=1.0)
    # buy at 101, sell at 99 -> (99-101)/101
    assert r == pytest.approx((99.0 - 101.0) / 101.0 * 100.0)
    assert r < 0


def test_charges_reduce_the_net_return():
    base = net_hold_return_pct(entry_premium=100.0, exit_premium=110.0,
                               spread_pct_per_side=0.0)
    charged = net_hold_return_pct(entry_premium=100.0, exit_premium=110.0,
                                  spread_pct_per_side=0.0,
                                  charges_pct_round_trip=2.0)
    assert charged == pytest.approx(base - 2.0)


def test_net_return_none_on_unusable_entry():
    assert net_hold_return_pct(entry_premium=0.0, exit_premium=5.0,
                               spread_pct_per_side=1.0) is None
    assert net_hold_return_pct(entry_premium=float("nan"), exit_premium=5.0,
                               spread_pct_per_side=1.0) is None


# ---------------------------------------------------------------------------
# session_level_stats — the method lesson, in code
# ---------------------------------------------------------------------------

def test_one_lucky_session_cannot_carry_the_statistic():
    """The failure mode this function exists to prevent — hypothesis #5's shape.

    500 bars from one wildly positive session plus 30 mildly negative sessions.
    Pooled, the mean is strongly positive and the sample looks enormous. Collapsed
    to sessions it is 31 observations, 3% of them positive, with a t-stat nowhere
    near the gate — the same collapse the microstructure register recorded
    (pooled +1.78% -> per-session median -5.49%, 1.4% of sessions positive).

    Note what per-session medians do NOT fix: the outlier still drags the MEAN of
    session medians positive. Skew survives the collapse; only the t-stat and the
    positive-share expose it. That is why the verdict reads all three.
    """
    values = [50.0] * 500 + [-1.0] * 30
    sessions = ["2026-01-01"] * 500 + [f"2026-02-{d:02d}" for d in range(1, 31)]

    assert np.mean(values) > 0                      # pooled says yes, loudly

    stat = session_level_stats(values, sessions)
    assert stat.n_sessions == 31                    # 530 bars were 31 observations
    assert stat.median_of_session_medians == pytest.approx(-1.0)
    assert stat.share_sessions_positive < 0.05
    assert stat.t_stat is not None
    assert stat.t_stat < 2.0                        # nowhere near the gate

    # And the verdict refuses it even with a flattering ratio supplied.
    assert classify_cell(mfe_mae=1.50, stat=stat) == "WEAK"


def test_t_stat_matches_the_one_sample_definition():
    per_session = [1.0, 2.0, 3.0, 4.0, 5.0]
    sessions = [f"2026-01-{d:02d}" for d in range(1, 6)]
    stat = session_level_stats(per_session, sessions, min_sessions=5)

    arr = np.array(per_session)
    expected = float(np.mean(arr)) / (float(np.std(arr, ddof=1)) / math.sqrt(len(arr)))
    assert stat.t_stat == pytest.approx(expected)
    assert stat.share_sessions_positive == pytest.approx(1.0)


def test_thin_sample_is_marked_unpowered_not_silently_reported():
    stat = session_level_stats([1.0, 2.0], ["2026-01-01", "2026-01-02"])
    assert stat.n_sessions == 2
    assert stat.sufficient is False
    assert "unpowered" in stat.note


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        session_level_stats([1.0, 2.0], ["2026-01-01"])


# ---------------------------------------------------------------------------
# causal_session_stat
# ---------------------------------------------------------------------------

def test_causal_stat_never_includes_its_own_session():
    dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    values = {"2026-01-01": 10.0, "2026-01-02": 20.0,
              "2026-01-03": 30.0, "2026-01-04": 1000.0}

    out = causal_session_stat(dates, values, lookback_sessions=3, min_sessions=1)

    assert math.isnan(out.iloc[0])                       # nothing before day 1
    assert out.iloc[1] == pytest.approx(10.0)            # mean of {day1}
    assert out.iloc[2] == pytest.approx(15.0)            # mean of {day1, day2}
    # Day 4's own 1000 must not appear in its own threshold.
    assert out.iloc[3] == pytest.approx(20.0)            # mean of {day1..day3}


def test_causal_stat_respects_min_sessions():
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    values = {d: 1.0 for d in dates}
    out = causal_session_stat(dates, values, lookback_sessions=3, min_sessions=2)
    assert math.isnan(out.iloc[0])
    assert math.isnan(out.iloc[1])       # only 1 prior session, min is 2
    assert out.iloc[2] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# chronological_split + holdout protection
# ---------------------------------------------------------------------------

def test_split_boundaries_are_inclusive_and_ordered():
    sessions = ["2026-01-03", "2026-01-01", "2026-03-01", "2026-02-01", "2026-04-01"]
    split = chronological_split(sessions, train_end="2026-01-31",
                                validation_end="2026-03-01")
    assert split.train == ["2026-01-01", "2026-01-03"]
    assert split.validation == ["2026-02-01", "2026-03-01"]   # end is inclusive
    assert split.counts() == {"train": 2, "validation": 2, "holdout": 1}


def test_holdout_raises_until_explicitly_unlocked():
    split = chronological_split(["2026-01-01", "2026-09-01"],
                                train_end="2026-01-31", validation_end="2026-02-28")
    with pytest.raises(HoldoutProtectionError):
        _ = split.holdout

    unlocked = split.unlock_holdout(reason="finalists frozen 2026-08-22, single read")
    assert unlocked == ["2026-09-01"]
    assert split.holdout == ["2026-09-01"]       # readable once unlocked


def test_unlocking_without_a_reason_is_refused():
    split = chronological_split(["2026-09-01"], train_end="2026-01-01",
                                validation_end="2026-02-01")
    with pytest.raises(ValueError):
        split.unlock_holdout(reason="   ")


# ---------------------------------------------------------------------------
# classify_cell
# ---------------------------------------------------------------------------

def _stat(*, n=40, median=1.0, t=3.0, sufficient=True) -> SessionStat:
    return SessionStat(n, median, median, t, 0.6, sufficient)


def test_base_rate_ratio_is_not_an_edge():
    assert classify_cell(mfe_mae=BASE_RATE_MFE_MAE, stat=_stat()) == "NO_EDGE"
    assert classify_cell(mfe_mae=0.90, stat=_stat()) == "NO_EDGE"


def test_candidate_requires_both_ratio_and_significance():
    assert classify_cell(mfe_mae=1.30, stat=_stat(t=3.0, median=1.0)) == "CANDIDATE"
    # good ratio, no significance
    assert classify_cell(mfe_mae=1.30, stat=_stat(t=0.4)) == "WEAK"
    # good ratio, significant but NEGATIVE median
    assert classify_cell(mfe_mae=1.30, stat=_stat(t=3.0, median=-1.0)) == "WEAK"


def test_unpowered_dominates_every_other_verdict():
    assert classify_cell(mfe_mae=5.0, stat=_stat(sufficient=False)) == "UNPOWERED"


# ---------------------------------------------------------------------------
# screen_condition — end to end on a constructed series
# ---------------------------------------------------------------------------

def _series(n_sessions: int, bars: int, drift: float, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(n_sessions):
        date = f"2026-01-{s + 1:02d}"
        price = 100.0
        for _ in range(bars):
            price = max(1.0, price + drift + rng.normal(0, 0.20))
            rows.append({"session_date": date, "high": price + 0.30,
                         "low": price - 0.30, "close": price})
    return pd.DataFrame(rows)


def test_screen_flags_a_genuinely_drifting_series_as_candidate():
    """Drift modest enough that adverse excursion is real — the normal path.

    A larger drift drives median MAE to zero and the ratio to inf, which is
    covered separately; here the point is that a finite, honestly-measured ratio
    above the base rate plus session-level significance reads as CANDIDATE.
    """
    frame = _series(n_sessions=40, bars=40, drift=0.10)
    cells = screen_condition(frame, label="drift", horizons=[5, 10],
                             spread_pct_per_side=0.0)
    for c in cells:
        assert c.mfe_mae is not None
        assert math.isfinite(c.mfe_mae)
        assert c.mfe_mae > BASE_RATE_MFE_MAE
    assert summarize_screen(cells)["survives"] is True


def test_screen_rejects_a_random_walk():
    frame = _series(n_sessions=40, bars=40, drift=0.0)
    cells = screen_condition(frame, label="noise", horizons=[5, 10],
                             spread_pct_per_side=1.0)
    summary = summarize_screen(cells)
    assert summary["survives"] is False
    assert all(c.verdict in ("NO_EDGE", "WEAK") for c in cells)


def test_screen_honours_the_condition_mask():
    frame = _series(n_sessions=30, bars=30, drift=0.0)
    mask = pd.Series(np.zeros(len(frame), dtype=bool))
    mask.iloc[::10] = True
    cells = screen_condition(frame, label="masked", horizons=[5], condition=mask)
    unmasked = screen_condition(frame, label="all", horizons=[5])
    assert cells[0].n_bars < unmasked[0].n_bars


def test_screen_rejects_a_mask_of_the_wrong_length():
    frame = _series(n_sessions=2, bars=10, drift=0.0)
    with pytest.raises(ValueError):
        screen_condition(frame, label="bad", horizons=[2],
                         condition=pd.Series([True, False]))


def test_screen_requires_its_columns():
    with pytest.raises(ValueError, match="missing required columns"):
        screen_condition(pd.DataFrame({"close": [1.0]}), label="x", horizons=[1])


def test_summary_flags_a_single_surviving_horizon_as_fragile():
    good = _stat()
    cells = [
        ScreenCell("c", 5, 100, 1.4, good, "CANDIDATE"),
        ScreenCell("c", 10, 100, 0.9, good, "NO_EDGE"),
        ScreenCell("c", 15, 100, 0.9, good, "NO_EDGE"),
    ]
    summary = summarize_screen(cells)
    assert summary["survives"] is True
    assert summary["fragile_single_horizon"] is True
    assert summary["candidate_horizons"] == [5]
