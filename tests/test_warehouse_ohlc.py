"""Tests for warehouse OHLC resampling + gap detection (slice 7)."""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.warehouse_ohlc import (  # noqa: E402
    build_ohlc_response,
    find_intraday_gaps,
    resample_ohlc,
)


def _ts(date_str: str, hh: int, mm: int) -> int:
    """UTC epoch ms for an IST date + HH:MM."""
    t = pd.Timestamp(f"{date_str} {hh:02d}:{mm:02d}", tz="Asia/Kolkata")
    return int(t.tz_convert("UTC").value // 10**6)


def _one_minute_df(date_str: str, n: int, start_hh: int = 9, start_mm: int = 15):
    """Build n consecutive 1m candles starting at the given IST time."""
    rows = []
    base = pd.Timestamp(f"{date_str} {start_hh:02d}:{start_mm:02d}", tz="Asia/Kolkata")
    for i in range(n):
        t = base + pd.Timedelta(minutes=i)
        ms = int(t.tz_convert("UTC").value // 10**6)
        price = 100 + i
        rows.append({
            "instrument": "NIFTY", "ts": ms,
            "open": price, "high": price + 2, "low": price - 2, "close": price + 1,
            "volume": 10,
        })
    return pd.DataFrame(rows)


def _candle_row(date_str: str, hh: int, mm: int, price: int = 100):
    return {
        "instrument": "NIFTY",
        "ts": _ts(date_str, hh, mm),
        "open": price,
        "high": price + 2,
        "low": price - 2,
        "close": price + 1,
        "volume": 10,
    }


# ---- resample_ohlc ----------------------------------------------------------


def test_resample_1m_passthrough():
    df = _one_minute_df("2026-05-20", 5)
    bars = resample_ohlc(df, "1m")
    assert len(bars) == 5
    assert bars[0]["open"] == 100
    assert bars[0]["time"] == bars[0]["ts"] // 1000


def test_resample_5m_aggregates_ohlc():
    df = _one_minute_df("2026-05-20", 10)  # two 5m buckets
    bars = resample_ohlc(df, "5m")
    assert len(bars) == 2
    # First bucket covers minutes 0-4: open=100, close of 5th minute = 104+1=105
    first = bars[0]
    assert first["open"] == 100
    assert first["close"] == 105
    assert first["high"] == 106  # minute 4 high = 104+2
    assert first["low"] == 98    # minute 0 low = 100-2
    assert first["volume"] == 50


def test_resample_daily_one_bar_per_session():
    df = pd.concat([_one_minute_df("2026-05-20", 375), _one_minute_df("2026-05-21", 375)], ignore_index=True)
    bars = resample_ohlc(df, "1d")
    assert len(bars) == 2
    assert bars[0]["open"] == 100


def test_resample_empty_returns_empty():
    assert resample_ohlc(pd.DataFrame(), "5m") == []


def test_resample_rejects_unknown_timeframe():
    with pytest.raises(ValueError):
        resample_ohlc(_one_minute_df("2026-05-20", 5), "7m")


def test_resample_bars_are_sorted_ascending():
    df = _one_minute_df("2026-05-20", 30)
    bars = resample_ohlc(df, "15m")
    times = [b["ts"] for b in bars]
    assert times == sorted(times)


def test_resample_filters_non_trading_dates_and_off_session_rows():
    df = pd.DataFrame([
        _candle_row("2026-05-27", 9, 15, 100),   # valid trading session
        _candle_row("2026-05-28", 9, 15, 200),   # Eid holiday in nse_calendar
        _candle_row("2026-05-30", 9, 15, 300),   # Saturday, not a special session
        _candle_row("2026-05-31", 21, 15, 400),  # Sunday/off-session junk
        _candle_row("2026-05-27", 16, 0, 500),   # valid date, outside regular market hours
    ])

    bars = resample_ohlc(df, "1m")

    assert len(bars) == 1
    assert bars[0]["ts"] == _ts("2026-05-27", 9, 15)
    assert bars[0]["open"] == 100


# ---- find_intraday_gaps -----------------------------------------------------


def test_full_session_has_no_gaps():
    df = _one_minute_df("2026-05-20", 375)
    assert find_intraday_gaps(df) == []


def test_partial_session_reports_missing_minutes():
    df = _one_minute_df("2026-05-20", 370)  # 5 minutes short
    gaps = find_intraday_gaps(df)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["date"] == "2026-05-20"
    assert g["stored"] == 370
    assert g["expected"] == 375
    assert g["missing_count"] == 5
    # The missing minutes are the last 5 of the session (15:25..15:29).
    assert "15:25" in g["missing_sample"]


def test_gap_detection_ignores_non_trading_dates_and_off_session_rows():
    df = pd.DataFrame([
        _candle_row("2026-05-28", 9, 15, 200),   # Eid holiday in nse_calendar
        _candle_row("2026-05-30", 9, 15, 300),   # Saturday, not a special session
        _candle_row("2026-05-31", 21, 15, 400),  # Sunday/off-session junk
        _candle_row("2026-05-27", 16, 0, 500),   # valid date, outside regular market hours
    ])

    assert find_intraday_gaps(df) == []


def test_empty_df_has_no_gaps():
    assert find_intraday_gaps(pd.DataFrame()) == []


# ---- build_ohlc_response ----------------------------------------------------


@pytest.mark.asyncio
async def test_build_response_injects_loader_and_includes_gaps():
    df = _one_minute_df("2026-05-20", 370)

    async def loader(instrument, start_ts, end_ts):
        assert instrument == "NIFTY"
        return df

    resp = await build_ohlc_response(
        loader, instrument="NIFTY", start_ts=None, end_ts=None, timeframe="15m",
    )
    assert resp["instrument"] == "NIFTY"
    assert resp["timeframe"] == "15m"
    assert resp["bar_count"] == len(resp["bars"])
    assert resp["gap_day_count"] == 1


@pytest.mark.asyncio
async def test_build_response_can_skip_gaps():
    async def loader(instrument, start_ts, end_ts):
        return _one_minute_df("2026-05-20", 100)

    resp = await build_ohlc_response(
        loader, instrument="NIFTY", start_ts=None, end_ts=None, timeframe="5m", include_gaps=False,
    )
    assert "gaps" not in resp
