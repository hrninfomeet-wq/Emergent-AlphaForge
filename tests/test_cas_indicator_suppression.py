"""Closing-auction bars must not poison indicators (SEBI CAS, 2026-08-03).

Reproduces the shape actually observed on NIFTY on day one: continuous trading
to 15:14, then 14 identical zero-range bars, then the auction close landing as a
single +200.95 point jump.

The load-bearing test here is `test_pre_cas_day_is_untouched` — roughly two years
of stored history and every backtest built on it must keep computing exactly as
they did before this change.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.indicators import (  # noqa: E402
    CAS_MASK_COL,
    _reset_on_gap,
    atr,
    cas_window_mask,
    ema,
    precompute_all_indicators,
)

FREEZE_MIN = 15 * 60 + 15
JUMP_MIN = 15 * 60 + 29
JUMP_POINTS = 200.95
BASE = 24573.35


def _session_frame(date_iso: str, *, frozen: bool = True) -> pd.DataFrame:
    """One 375-bar IST session. With `frozen`, the tail mimics a CAS day."""
    start = pd.Timestamp(f"{date_iso} 09:15", tz="Asia/Kolkata")
    index = pd.date_range(start, periods=375, freq="1min")
    rows = []
    for i, stamp in enumerate(index):
        minute = stamp.hour * 60 + stamp.minute
        if frozen and minute >= JUMP_MIN:
            o = h = low = c = BASE + JUMP_POINTS      # auction close jump bar
        elif frozen and minute >= FREEZE_MIN:
            o = h = low = c = BASE                    # frozen: zero range
        else:
            c = BASE + (i % 7) - 3.0                  # deterministic wiggle
            o, h, low = c - 1.0, c + 5.0, c - 5.0
        rows.append({
            "ts": int(stamp.timestamp() * 1000),
            "open": o, "high": h, "low": low, "close": c, "volume": 1000.0,
        })
    return pd.DataFrame(rows)


# --- the mask itself --------------------------------------------------------

def test_mask_flags_only_the_auction_window_on_and_after_the_effective_date():
    mask = cas_window_mask(_session_frame("2026-08-03"))
    ist = pd.to_datetime(_session_frame("2026-08-03")["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    minute = ist.dt.hour * 60 + ist.dt.minute
    assert mask[minute < FREEZE_MIN].sum() == 0
    assert bool(mask[minute >= FREEZE_MIN].all())
    assert int(mask.sum()) == 15          # 15:15..15:29 inclusive


def test_mask_never_fires_before_the_effective_date():
    """Same bar shape, one trading day earlier — must not be flagged."""
    assert int(cas_window_mask(_session_frame("2026-07-31")).sum()) == 0


def test_mask_is_bool_dtype():
    assert cas_window_mask(_session_frame("2026-08-03")).dtype == bool


def test_mask_handles_an_empty_frame():
    assert len(cas_window_mask(pd.DataFrame({"ts": []}))) == 0


# --- the regression guard ---------------------------------------------------

def test_pre_cas_day_is_untouched():
    """A pre-CAS frame must compute byte-identically to the old code path.

    Verified by comparing against `_reset_on_gap` on a frame with no auction
    column at all, which is exactly the pre-change behaviour.
    """
    df = _session_frame("2026-07-31")
    df["gap_before"] = False
    legacy = _reset_on_gap(df, lambda d: ema(d["close"], 9))

    df_with_mask = df.copy()
    df_with_mask[CAS_MASK_COL] = cas_window_mask(df_with_mask)
    current = _reset_on_gap(df_with_mask, lambda d: ema(d["close"], 9))

    pd.testing.assert_series_equal(legacy, current)


def test_pre_cas_jump_bar_still_moves_indicators():
    """Before the effective date a jump bar is real price action and must keep
    affecting ATR — masking it retroactively would rewrite history."""
    enriched = precompute_all_indicators(_session_frame("2026-07-31"))
    at_jump = enriched.iloc[-1]["atr"]
    before_jump = enriched.iloc[-16]["atr"]
    assert at_jump > before_jump


# --- suppression on a CAS day ----------------------------------------------

def test_auction_bars_do_not_inflate_atr():
    """The +201 jump bar must not reach ATR on a post-CAS day."""
    post = precompute_all_indicators(_session_frame("2026-08-03"))
    pre = precompute_all_indicators(_session_frame("2026-07-31"))
    assert post.iloc[-1]["atr"] < pre.iloc[-1]["atr"]


def test_indicators_hold_their_last_real_value_across_the_auction():
    enriched = precompute_all_indicators(_session_frame("2026-08-03"))
    ist = pd.to_datetime(enriched["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    minute = ist.dt.hour * 60 + ist.dt.minute
    last_live = enriched.loc[minute == FREEZE_MIN - 1].iloc[0]
    for col in ("ema9", "rsi", "atr"):
        held = enriched.loc[minute >= FREEZE_MIN, col]
        assert held.nunique() == 1, f"{col} moved during the auction"
        assert held.iloc[0] == pytest.approx(last_live[col])


def test_flat_bars_do_not_collapse_atr_toward_zero():
    """14 zero-range bars would drag a 14-period ATR to almost nothing."""
    enriched = precompute_all_indicators(_session_frame("2026-08-03"))
    assert enriched.iloc[-1]["atr"] > 1.0


def test_options_segment_is_never_suppressed():
    """Derivatives trade continuously through the cash auction, so their bars
    are real and must reach the indicators."""
    enriched = precompute_all_indicators(_session_frame("2026-08-03"), segment="options")
    assert not enriched[CAS_MASK_COL].any()
    assert enriched.iloc[-1]["atr"] > precompute_all_indicators(_session_frame("2026-08-03")).iloc[-1]["atr"]


def test_bool_indicator_columns_stay_bool_through_realignment():
    """`reindex` introduces NaN; bool columns must not decay to object or the
    downstream `&`/`|` signal masks break."""
    enriched = precompute_all_indicators(_session_frame("2026-08-03"))
    for col in ("is_swing_high", "is_swing_low", "nr7", "inside_bar"):
        assert enriched[col].dtype == bool, f"{col} lost bool dtype"


def test_event_markers_are_not_fabricated_across_the_auction():
    """Event columns must read "no signal" on suppressed bars. Forward-filling
    them would invent ~15 fresh swing points / fair-value gaps per day."""
    enriched = precompute_all_indicators(_session_frame("2026-08-03"))
    ist = pd.to_datetime(enriched["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    auction = (ist.dt.hour * 60 + ist.dt.minute) >= FREEZE_MIN

    for col in ("is_swing_high", "is_swing_low"):
        assert not enriched.loc[auction, col].any(), f"{col} fired during the auction"
    assert enriched.loc[auction, "fvg"].isna().all(), "fvg fired during the auction"


def test_enriched_frame_keeps_every_bar():
    """Suppression must not drop rows — the auction bars still belong to the
    candle series (the 15:29 bar carries the official close)."""
    assert len(precompute_all_indicators(_session_frame("2026-08-03"))) == 375


# --- the property that actually matters -------------------------------------

def test_artifact_does_not_bleed_into_the_next_session():
    """The auction window is untradeable, so distortion inside it would be
    harmless on its own. It is not confined there: `gap_before_mask` deliberately
    does not flag cross-date boundaries, so whole-frame EWM indicators carry the
    poisoned state straight into the next morning's tradeable hours.

    Measured on real 2026-08 data, unsuppressed ATR was off by +57%/-39% at 09:15
    and +34%/-15% at 09:25 — the minute the signal window opens.

    Excluding the auction bars must be exactly equivalent to their never having
    existed, so the next session computes identically either way.
    """
    day2 = _session_frame("2026-08-04", frozen=False)

    with_auction = pd.concat([_session_frame("2026-08-03"), day2], ignore_index=True)

    # Control: day one physically ends at 15:14, no auction bars at all.
    d1 = _session_frame("2026-08-03")
    ist = pd.to_datetime(d1["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    truncated = d1[(ist.dt.hour * 60 + ist.dt.minute) < FREEZE_MIN]
    without_auction = pd.concat([truncated, day2], ignore_index=True)

    a = precompute_all_indicators(with_auction).set_index("ts")
    b = precompute_all_indicators(without_auction).set_index("ts")

    next_day = day2["ts"].tolist()
    for col in ("atr", "ema9", "ema21", "rsi", "adx"):
        pd.testing.assert_series_equal(
            a.loc[next_day, col], b.loc[next_day, col], check_names=False,
        )


def test_without_suppression_the_next_session_would_differ():
    """Guards the test above from passing vacuously — the artifact really does
    propagate when the mask is off."""
    day2 = _session_frame("2026-08-04", frozen=False)
    d1 = _session_frame("2026-08-03")
    ist = pd.to_datetime(d1["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    truncated = d1[(ist.dt.hour * 60 + ist.dt.minute) < FREEZE_MIN]

    # segment="options" disables the mask == pre-change behaviour.
    a = precompute_all_indicators(pd.concat([d1, day2], ignore_index=True),
                                  segment="options").set_index("ts")
    b = precompute_all_indicators(pd.concat([truncated, day2], ignore_index=True),
                                  segment="options").set_index("ts")

    open_ts = day2["ts"].iloc[0]
    assert a.at[open_ts, "atr"] != pytest.approx(b.at[open_ts, "atr"])
