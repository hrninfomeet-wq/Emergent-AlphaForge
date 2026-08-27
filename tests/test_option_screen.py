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
    _contiguous_blocks,
    causal_session_stat,
    chronological_split,
    classify_cell,
    forward_excursions,
    mfe_mae_ratio,
    net_hold_return_pct,
    net_vertical_return_pct,
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
    assert split.counts() == {"train": 2, "validation": 2,
                              "consumed": 0, "holdout": 1}


def test_a_consumed_window_is_excluded_from_the_holdout():
    """The defect this parameter exists for.

    Against the real warehouse the split reported 158 "PROTECTED" holdout
    sessions while an earlier campaign had already read 2026-01-01 -> 2026-07-10.
    A spent window counted into the holdout does not merely inflate a number — it
    destroys the one property a holdout has, under the word PROTECTED.
    """
    sessions = ["2025-06-01", "2025-11-01",
                "2026-03-01", "2026-06-01",          # consumed by a prior campaign
                "2026-08-01", "2026-08-15"]          # genuinely untouched
    split = chronological_split(sessions, train_end="2025-08-31",
                                validation_end="2025-12-31",
                                consumed_until="2026-07-10")

    assert split.counts() == {"train": 1, "validation": 1,
                              "consumed": 2, "holdout": 2}
    assert split.consumed == ["2026-03-01", "2026-06-01"]
    assert split.unlock_holdout(reason="test") == ["2026-08-01", "2026-08-15"]


def test_omitting_the_consumed_window_keeps_the_old_wider_holdout():
    """Backwards compatible: no consumed_until means everything after validation."""
    sessions = ["2025-06-01", "2026-03-01", "2026-08-01"]
    split = chronological_split(sessions, train_end="2025-08-31",
                                validation_end="2025-12-31")
    assert split.counts()["consumed"] == 0
    assert split.counts()["holdout"] == 2


def test_a_consumed_boundary_at_or_before_validation_is_ignored():
    """It can only ever shrink the holdout, never reclassify validation."""
    sessions = ["2025-06-01", "2025-11-01", "2026-03-01"]
    split = chronological_split(sessions, train_end="2025-08-31",
                                validation_end="2025-12-31",
                                consumed_until="2025-10-01")
    assert split.counts() == {"train": 1, "validation": 1,
                              "consumed": 0, "holdout": 1}


def test_a_consumed_window_swallowing_everything_leaves_no_holdout():
    sessions = ["2026-03-01", "2026-06-01"]
    split = chronological_split(sessions, train_end="2025-08-31",
                                validation_end="2025-12-31",
                                consumed_until="2026-07-10")
    assert split.counts()["holdout"] == 0
    assert split.unlock_holdout(reason="test") == []


def test_the_cli_defaults_the_consumed_window_to_the_spent_campaign():
    """2026-07-10 is the premium-momentum campaign's holdout end. Defaulting to
    None here would reintroduce the 158-session mislabel silently."""
    source = (ROOT / "backend" / "scripts" / "screen_option_buying.py").read_text()
    assert '"--consumed-until", default="2026-07-10"' in source
    assert "consumed_until=args.consumed_until or None" in source


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


# ---------------------------------------------------------------------------
# Block boundaries — a forward window must never cross one
#
# These exist because a mutation that deleted the block loop entirely (reverting
# to the original whole-frame behaviour) passed every other test in this file.
# The only guard was an end-to-end test in the CLI's test module, which is the
# wrong place for an invariant this module owns.
# ---------------------------------------------------------------------------

def test_contiguous_blocks_splits_on_runs_not_groups():
    """Two separated stretches of the same key are two blocks, not one.

    A window may only look inside the bars it is physically contiguous with, so
    grouping (which would rejoin them) would be wrong.
    """
    keys = np.array(["a", "a", "b", "b", "a"], dtype=object)
    assert _contiguous_blocks(keys) == [(0, 2), (2, 4), (4, 5)]


def test_contiguous_blocks_handles_the_trivial_shapes():
    assert _contiguous_blocks(np.array([], dtype=object)) == []
    assert _contiguous_blocks(np.array(["x"], dtype=object)) == [(0, 1)]
    assert _contiguous_blocks(np.array(["x", "x", "x"], dtype=object)) == [(0, 3)]


def _two_block_frame():
    """Two 10-bar blocks. The second is priced far above the first, so a window
    that leaks across the boundary produces an obviously wrong excursion."""
    rows = []
    for date, price in (("2026-01-01", 100.0), ("2026-01-02", 500.0)):
        for i in range(10):
            rows.append({"session_date": date, "high": price + 1,
                         "low": price - 1, "close": price})
    return pd.DataFrame(rows)


def test_the_last_h_bars_of_every_block_are_ineligible():
    """The mutation-killer.

    With two 10-bar blocks and a 3-bar horizon, 3 bars at the end of EACH block
    cannot fit a full window: 20 - 6 = 14. Measuring over the whole frame
    instead would wrongly keep the first block's tail (20 - 3 = 17).
    """
    cells = screen_condition(_two_block_frame(), label="blocks", horizons=[3],
                             spread_pct_per_side=0.0, min_sessions=1)
    assert cells[0].n_bars == 14


def test_a_window_never_borrows_the_next_blocks_prices():
    """Block one sits at 100 and block two at 500. If the window crossed, the
    first block's tail would show a ~400-point favourable excursion."""
    frame = _two_block_frame()
    cells = screen_condition(frame, label="blocks", horizons=[3],
                             spread_pct_per_side=0.0, min_sessions=1)
    # Every eligible bar is flat within its own block, so MFE is ~1 point, and
    # the ratio is finite and near 1 — not the huge number a leak would give.
    assert cells[0].mfe_mae == pytest.approx(1.0)


def test_grouping_defaults_to_session_date():
    """The safe behaviour is the default — a caller must not have to remember."""
    frame = _two_block_frame()
    default = screen_condition(frame, label="d", horizons=[3],
                               spread_pct_per_side=0.0, min_sessions=1)
    explicit = screen_condition(frame, label="e", horizons=[3],
                                spread_pct_per_side=0.0, min_sessions=1,
                                group_by=frame["session_date"])
    assert default[0].n_bars == explicit[0].n_bars == 14


def test_a_finer_group_key_splits_blocks_further():
    """Stacking a CE and a PE leg inside one session needs a compound key."""
    frame = _two_block_frame()
    frame["side"] = ["CE"] * 5 + ["PE"] * 5 + ["CE"] * 5 + ["PE"] * 5
    blocks = frame["session_date"] + "|" + frame["side"]

    by_session = screen_condition(frame, label="s", horizons=[2],
                                  spread_pct_per_side=0.0, min_sessions=1)
    by_leg = screen_condition(frame, label="l", horizons=[2], group_by=blocks,
                              spread_pct_per_side=0.0, min_sessions=1)

    assert by_session[0].n_bars == 16      # 2 blocks  -> 20 - 2*2
    assert by_leg[0].n_bars == 12          # 4 blocks  -> 20 - 4*2


def test_a_mismatched_group_by_length_raises():
    with pytest.raises(ValueError, match="group_by length"):
        screen_condition(_two_block_frame(), label="bad", horizons=[2],
                         group_by=["only-one-key"])


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


# ---------------------------------------------------------------------------
# THE SHORT SIDE
#
# Every campaign in this repo has tested option BUYING, and the register's
# headline (MFE/MAE 0.86-0.95, below 1.0 at every horizon) is a measurement of
# what the buyer loses. The obvious reading is that the seller collects it. That
# reading is WRONG in one specific and decisive way, and these tests exist to
# stop the screen from making the error:
#
#   the spread is crossed twice by BOTH sides.
#
# A long buys the ask and sells the bid. A short sells the bid and buys back the
# ask. Neither escapes the friction, so a short's return is NOT the arithmetic
# negative of a long's — and a screen that models it as `-long` would manufacture
# an edge out of an accounting mistake. That is precisely the class of error §11
# of the campaign doc was written about.
# ---------------------------------------------------------------------------

def test_an_unchanged_premium_loses_money_for_BOTH_sides():
    """The friction floor. If this ever shows a profit for either side, the cost
    model has been broken."""
    kw = dict(entry_premium=100.0, exit_premium=100.0, spread_pct_per_side=1.0,
              charges_pct_round_trip=0.186)
    long_r = net_hold_return_pct(side="LONG", **kw)
    short_r = net_hold_return_pct(side="SHORT", **kw)
    assert long_r < 0
    assert short_r < 0
    assert long_r == pytest.approx(-2.166, abs=0.01)
    assert short_r == pytest.approx(-2.206, abs=0.01)


def test_a_short_is_not_the_arithmetic_negative_of_a_long():
    """Both pay the spread, so the two are not mirror images. A screen that
    assumed they were would hand the seller the buyer's losses as profit."""
    kw = dict(entry_premium=100.0, exit_premium=105.0, spread_pct_per_side=1.0,
              charges_pct_round_trip=0.186)
    long_r = net_hold_return_pct(side="LONG", **kw)
    short_r = net_hold_return_pct(side="SHORT", **kw)
    assert short_r != pytest.approx(-long_r, abs=0.05)
    assert long_r + short_r < 0, "the pair must lose the round-trip friction"


def test_the_seller_profits_only_when_decay_exceeds_the_friction():
    """A premium that falls by less than the round trip costs is still a loss."""
    def short_at(exit_premium):
        return net_hold_return_pct(entry_premium=100.0, exit_premium=exit_premium,
                                   spread_pct_per_side=1.0,
                                   charges_pct_round_trip=0.186, side="SHORT")
    assert short_at(99.0) < 0        # 1% decay does not cover ~2.2% friction
    assert short_at(95.0) > 0        # 5% decay does
    assert short_at(100.0) < short_at(98.0) < short_at(95.0)


def test_side_defaults_to_long_so_every_existing_caller_is_unchanged():
    kw = dict(entry_premium=100.0, exit_premium=110.0, spread_pct_per_side=1.0)
    assert net_hold_return_pct(**kw) == net_hold_return_pct(side="LONG", **kw)


def test_an_unknown_side_is_refused_rather_than_silently_treated_as_long():
    with pytest.raises(ValueError):
        net_hold_return_pct(entry_premium=100.0, exit_premium=100.0,
                            spread_pct_per_side=1.0, side="sideways")


def test_short_screen_swaps_the_excursions():
    """For a seller a FALLING premium is favourable, so MFE and MAE swap. The
    ratio of a short cell is therefore the reciprocal of the long cell's on the
    same series — the one place where the mirror really does hold, because
    excursions are gross and pay no friction."""
    frame = _series(n_sessions=30, bars=40, drift=0.05)
    long_cell = screen_condition(frame, label="L", horizons=[5], side="LONG")[0]
    short_cell = screen_condition(frame, label="S", horizons=[5], side="SHORT")[0]
    assert long_cell.mfe_mae is not None and short_cell.mfe_mae is not None
    assert short_cell.mfe_mae == pytest.approx(1.0 / long_cell.mfe_mae, rel=1e-6)


def test_short_screen_charges_the_seller_the_same_friction():
    """The net columns must NOT be mirror images — see the module comment."""
    frame = _series(n_sessions=30, bars=40, drift=0.05)
    long_cell = screen_condition(frame, label="L", horizons=[5], side="LONG",
                                 spread_pct_per_side=1.0,
                                 charges_pct_round_trip=0.186)[0]
    short_cell = screen_condition(frame, label="S", horizons=[5], side="SHORT",
                                  spread_pct_per_side=1.0,
                                  charges_pct_round_trip=0.186)[0]
    lm = long_cell.net_pct.median_of_session_medians
    sm = short_cell.net_pct.median_of_session_medians
    assert lm is not None and sm is not None
    assert lm + sm < 0, "both sides must be charged the round trip"


def test_screen_condition_side_defaults_to_long():
    frame = _series(n_sessions=30, bars=40, drift=0.05)
    default = screen_condition(frame, label="d", horizons=[5])[0]
    explicit = screen_condition(frame, label="e", horizons=[5], side="LONG")[0]
    assert default.mfe_mae == pytest.approx(explicit.mfe_mae)


def test_a_decaying_series_pays_the_seller_and_costs_the_buyer():
    """The economic property the whole short-side thesis rests on, and the one
    that discriminates a real short calculation from a mislabelled long one.

    Caught by mutation: dropping `side=side` from screen_condition's net call
    left every other test green, because asserting `long + short < 0` is
    satisfied by `2 x long` whenever the long is negative.
    """
    frame = _series(n_sessions=30, bars=40, drift=-0.5)
    kw = dict(horizons=[5], spread_pct_per_side=1.0, charges_pct_round_trip=0.186)
    long_net = screen_condition(frame, label="L", side="LONG", **kw)[0] \
        .net_pct.median_of_session_medians
    short_net = screen_condition(frame, label="S", side="SHORT", **kw)[0] \
        .net_pct.median_of_session_medians
    assert long_net is not None and short_net is not None
    assert long_net < 0 < short_net, (
        f"a decaying premium must cost the buyer and pay the seller "
        f"(long={long_net}, short={short_net})")


def test_the_two_sides_never_report_the_same_net():
    """A short computed as a long is the single most dangerous silent failure in
    this module — it would report the buyer's losses as the seller's profit."""
    frame = _series(n_sessions=30, bars=40, drift=0.05)
    kw = dict(horizons=[5, 10], spread_pct_per_side=1.0, charges_pct_round_trip=0.186)
    longs = screen_condition(frame, label="L", side="LONG", **kw)
    shorts = screen_condition(frame, label="S", side="SHORT", **kw)
    for lc, sc in zip(longs, shorts):
        assert sc.net_pct.median_of_session_medians != pytest.approx(
            lc.net_pct.median_of_session_medians, abs=1e-9)


# ---------------------------------------------------------------------------
# DEFINED-RISK VERTICALS — the kill test for the short-side thesis
#
# §13.3 measured the naked short's tail: the worst single entry lost 5.0x the
# premium collected. Naked selling is therefore off the table, and the thesis
# only matters if the edge SURVIVES paying for protection.
#
# A wing is expensive in a specific, non-obvious way. Friction scales with the
# SUM of the two leg premiums (each leg crosses its own spread, twice), while
# the credit scales with their DIFFERENCE. So a wing roughly doubles the cost
# while cutting the collected premium — which is exactly why this has to be
# measured rather than assumed either way.
#
# The denominator is MAX LOSS (width - credit), not premium. For a defined-risk
# vertical that is what the broker blocks as margin, so the number is a return
# on capital. It is NOT comparable to the naked screen's return-on-premium.
# ---------------------------------------------------------------------------

def _vert(short_entry, long_entry, short_exit, long_exit, *, width=150.0,
          spread=1.0, charges=0.0):
    return net_vertical_return_pct(
        short_entry=short_entry, long_entry=long_entry,
        short_exit=short_exit, long_exit=long_exit,
        width_points=width, spread_pct_per_side=spread,
        charges_pct_round_trip=charges)


def test_an_unchanged_spread_loses_the_friction():
    r = _vert(112.0, 40.0, 112.0, 40.0, charges=0.186)
    assert r is not None and r < 0


def test_friction_scales_with_the_SUM_of_the_legs_not_the_credit():
    """The reason a wing is expensive. Same credit, fatter legs -> more cost."""
    thin = _vert(112.0, 40.0, 112.0, 40.0)     # credit 72, legs sum 152
    fat = _vert(312.0, 240.0, 312.0, 240.0)    # credit 72, legs sum 552
    assert thin is not None and fat is not None
    assert fat < thin, "a wider-premium pair must pay more friction for one credit"


def test_max_profit_is_the_credit_over_the_capital_at_risk():
    """Both legs worthless at exit — the seller keeps the whole credit."""
    r = _vert(112.0, 40.0, 0.0, 0.0, width=150.0, spread=0.0, charges=0.0)
    credit = 72.0
    assert r == pytest.approx(100.0 * credit / (150.0 - credit), rel=1e-6)


def test_max_loss_is_minus_one_hundred_percent_of_capital_at_risk():
    """The spread widens to its full width — the defined-risk floor. This is the
    property that makes a vertical survivable where a naked short is not."""
    r = _vert(112.0, 40.0, 200.0, 50.0, width=150.0, spread=0.0, charges=0.0)
    assert r == pytest.approx(-100.0, abs=1e-6)


def test_the_loss_can_never_exceed_the_defined_floor():
    """However violently the short leg moves, a vertical cannot lose more than
    its width. §13.3's naked short lost 5.0x the premium collected; this is what
    buying the wing purchases."""
    for short_exit in (200.0, 500.0, 5000.0):
        long_exit = short_exit - 150.0   # legs 150 apart => spread at full width
        r = _vert(112.0, 40.0, short_exit, long_exit, width=150.0, spread=0.0)
        assert r == pytest.approx(-100.0, abs=1e-6)


def test_a_credit_wider_than_the_structure_is_refused_not_reported():
    """Credit >= width means no capital at risk, which is arbitrage or bad data.
    Reporting a return on a non-positive denominator would print a spectacular
    number from a division artefact."""
    assert _vert(160.0, 5.0, 100.0, 5.0, width=150.0, spread=0.0) is None


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf")])
def test_an_unusable_width_is_refused(bad):
    assert _vert(112.0, 40.0, 100.0, 35.0, width=bad) is None


def test_both_legs_pay_the_spread_in_the_punishing_direction():
    """Short sold at the bid and bought back at the ask; wing bought at the ask
    and sold at the bid. A model that gave either leg a favourable fill would
    manufacture edge."""
    frictionless = _vert(112.0, 40.0, 100.0, 36.0, spread=0.0)
    charged = _vert(112.0, 40.0, 100.0, 36.0, spread=1.0)
    assert charged < frictionless


def test_vertical_arithmetic_is_pinned_exactly():
    """Inequalities were not enough. A mutation sweep showed that giving the
    SHORT leg a favourable entry fill, or the wing a favourable one, still
    satisfied every `charged < frictionless` style assertion — the numbers moved
    but stayed on the correct side of the comparison. Only exact arithmetic pins
    which side of the book each leg is filled on.

        credit_in  = 112 x 0.99 - 40 x 1.01 = 70.48   (sell bid, buy ask)
        debit_out  = 100 x 1.01 - 36 x 0.99 = 65.36   (buy ask,  sell bid)
        max_loss   = 150 - 70.48            = 79.52
        charges    = 0.186% x (112 + 40)    =  0.28272
        profit     = 70.48 - 65.36 - 0.28272 = 4.83728
        return     = 100 x 4.83728 / 79.52   = 6.0831%
    """
    r = _vert(112.0, 40.0, 100.0, 36.0, width=150.0, spread=1.0, charges=0.186)
    assert r == pytest.approx(6.0831, abs=1e-4)


def test_charges_alone_scale_with_both_legs_turnover():
    """Isolates CHARGES from the bid-ask. Same credit, same width, same exits —
    only the leg premiums differ, so any difference is the charge base. With
    charges levied on the credit (or dropped) these two are identical, which is
    how both mutants survived the first sweep."""
    thin = _vert(112.0, 40.0, 112.0, 40.0, width=150.0, spread=0.0, charges=0.186)
    fat = _vert(312.0, 240.0, 312.0, 240.0, width=150.0, spread=0.0, charges=0.186)
    assert thin is not None and fat is not None
    assert fat < thin
    assert thin == pytest.approx(100.0 * -(0.00186 * 152.0) / 78.0, rel=1e-6)
    assert fat == pytest.approx(100.0 * -(0.00186 * 552.0) / 78.0, rel=1e-6)


def test_charges_strictly_reduce_the_return():
    free = _vert(112.0, 40.0, 100.0, 36.0, width=150.0, spread=0.0, charges=0.0)
    paid = _vert(112.0, 40.0, 100.0, 36.0, width=150.0, spread=0.0, charges=0.186)
    assert paid < free


def test_a_non_positive_short_premium_is_refused():
    """The `width > 0` guard is caught downstream by the max-loss check, but
    `short_entry > 0` is not: a zero short leg produces a NEGATIVE credit, which
    makes max_loss LARGER than the width and sails through. Selling something
    for nothing is not a structure."""
    assert _vert(0.0, 40.0, 100.0, 36.0, width=150.0) is None
    assert _vert(-5.0, 40.0, 100.0, 36.0, width=150.0) is None
