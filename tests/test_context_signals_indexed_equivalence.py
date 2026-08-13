"""The indexed pivot lookups must be INDISTINGUISHABLE from the DataFrame ones.

`recent_sr_levels` / `rsi_divergence` / `macd_divergence` re-slice a trailing
window on every bar. That was ~28s of a single explosive_reversal backtest
(833,700 DataFrame.__getitem__ calls) and it multiplied across every optimizer
trial, re-rank candidate and survival fold. The `*_indexed` twins answer the same
questions from a precomputed sparse pivot index instead.

Speed is worthless if it moves a signal, so these compare the two at EVERY bar
rather than on a sample, and cover the edge cases that a naive index gets wrong:
window truncation at the start of the frame, NaN indicator readings, frames with
no pivots at all, and the deliberate NaN-guard asymmetry between the RSI and MACD
originals.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.context_signals import (                      # noqa: E402
    build_pivot_index,
    recent_sr_levels, recent_sr_levels_indexed,
    rsi_divergence, rsi_divergence_indexed,
    macd_divergence, macd_divergence_indexed,
)


def _frame(n=420, seed=11, with_nans=True):
    """A frame with the shape the real one has: sparse swing pivots, an ATR that
    varies (so the S/R clustering tolerance varies), and NaN warmup."""
    rng = np.random.default_rng(seed)
    close = 24000 + np.cumsum(rng.normal(0, 12, n))
    high = close + rng.uniform(2, 25, n)
    low = close - rng.uniform(2, 25, n)
    df = pd.DataFrame({
        "open": close, "high": high, "low": low, "close": close,
        "atr": rng.uniform(5, 60, n),
        "rsi": rng.uniform(20, 80, n),
        "macd_hist": rng.normal(0, 4, n),
    })
    # sparse, irregularly spaced pivots — as in the real is_swing_* columns
    hi = np.zeros(n, dtype=bool); lo = np.zeros(n, dtype=bool)
    hi[rng.choice(n, size=n // 18, replace=False)] = True
    lo[rng.choice(n, size=n // 18, replace=False)] = True
    df["is_swing_high"] = hi
    df["is_swing_low"] = lo
    if with_nans:
        df.loc[:14, "rsi"] = np.nan          # indicator warmup
        df.loc[:9, "atr"] = np.nan
        df.loc[20:24, "macd_hist"] = np.nan
    return df


@pytest.mark.parametrize("lookback", [5, 40, 60, 500])
def test_divergences_match_at_every_bar(lookback):
    df = _frame()
    px = build_pivot_index(df)
    assert px is not None
    for i in range(len(df)):
        assert rsi_divergence_indexed(px, i, lookback=lookback) == \
               rsi_divergence(df, i, lookback=lookback), f"rsi bar {i}"
        assert macd_divergence_indexed(px, i, lookback=lookback) == \
               macd_divergence(df, i, lookback=lookback), f"macd bar {i}"


@pytest.mark.parametrize("lookback,mult", [(60, 0.4), (20, 0.1), (200, 1.5)])
def test_sr_levels_match_at_every_bar(lookback, mult):
    df = _frame()
    px = build_pivot_index(df)
    for i in range(len(df)):
        assert recent_sr_levels_indexed(px, i, lookback=lookback, cluster_atr_mult=mult) == \
               recent_sr_levels(df, i, lookback=lookback, cluster_atr_mult=mult), f"sr bar {i}"


def test_matches_when_the_frame_has_no_pivots_at_all():
    df = _frame(n=120)
    df["is_swing_high"] = False
    df["is_swing_low"] = False
    px = build_pivot_index(df)
    for i in range(len(df)):
        assert recent_sr_levels_indexed(px, i) == recent_sr_levels(df, i)
        assert rsi_divergence_indexed(px, i) == rsi_divergence(df, i)
        assert macd_divergence_indexed(px, i) == macd_divergence(df, i)


def test_nan_pivot_flags_do_not_invent_pivots():
    """A NaN in an object-dtype swing column is False under `== True`. A bare
    astype(bool) would make it True and fabricate a pivot the original never saw."""
    df = _frame(n=150)
    df["is_swing_high"] = df["is_swing_high"].astype(object)
    df.loc[30:40, "is_swing_high"] = np.nan
    px = build_pivot_index(df)
    assert not any(30 <= int(p) <= 40 for p in px["hi_idx"])
    for i in range(len(df)):
        assert rsi_divergence_indexed(px, i) == rsi_divergence(df, i), f"bar {i}"
        assert recent_sr_levels_indexed(px, i) == recent_sr_levels(df, i), f"bar {i}"


def test_build_pivot_index_degrades_to_none_not_to_a_wrong_answer():
    """Without the swing columns there is nothing to index. Returning None makes
    the strategy fall back to the DataFrame helpers; returning an empty index
    would silently answer 'no S/R, no divergence' for every bar."""
    assert build_pivot_index(pd.DataFrame()) is None
    assert build_pivot_index(None) is None
    df = _frame(n=60).drop(columns=["is_swing_low"])
    assert build_pivot_index(df) is None


def test_missing_indicator_column_returns_none_like_the_original():
    df = _frame(n=120).drop(columns=["rsi"])
    px = build_pivot_index(df)
    for i in range(0, len(df), 7):
        assert rsi_divergence_indexed(px, i) is None
        assert rsi_divergence(df, i) is None
