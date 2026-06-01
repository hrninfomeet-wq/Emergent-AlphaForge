"""Server-side OHLC resampling for the warehouse candlestick chart.

Reads stored 1-minute candles and aggregates them into higher timeframes
(5m / 15m / 1h / 1d) so the frontend can render a TradingView-style chart
without shipping hundreds of thousands of 1m bars. Aggregation is done in
pandas on IST-localized timestamps so daily buckets align to trading days.

Gaps (missing minutes inside a trading session) are surfaced separately so the
chart and an audit view can show exactly what is missing rather than only a
coverage percentage.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from app.nse_calendar import is_trading_day

# Supported chart timeframes -> pandas resample rule (on IST-localized index).
TIMEFRAME_RULES: Dict[str, str] = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "1h": "60min",
    "1d": "1D",
}

EXPECTED_1M_PER_SESSION = 375  # NSE 09:15-15:30 inclusive
SESSION_START_MINUTE = 9 * 60 + 15
SESSION_END_MINUTE_EXCLUSIVE = 15 * 60 + 30


def _regular_session_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Return only regular-market rows on calendar-approved trading sessions.

    Stored data can contain occasional weekend/off-session rows from live or
    retry paths. The chart is a trust surface, so it should render only the
    same regular 09:15-15:30 IST sessions that the Data Trust Audit expects.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    work = df.copy()
    work["dt"] = pd.to_datetime(work["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    work["date"] = work["dt"].dt.strftime("%Y-%m-%d")
    minute = work["dt"].dt.hour * 60 + work["dt"].dt.minute
    session_mask = (minute >= SESSION_START_MINUTE) & (minute < SESSION_END_MINUTE_EXCLUSIVE)
    trading_mask = work["date"].map(is_trading_day)
    return work.loc[session_mask & trading_mask].copy()


def resample_ohlc(df: pd.DataFrame, timeframe: str) -> List[Dict[str, Any]]:
    """Resample a 1m candle DataFrame (with a `ts` ms column) to `timeframe`.

    Returns a list of bars sorted ascending, each:
      {ts, time (unix sec), open, high, low, close, volume}
    `ts` is the UTC epoch-ms of the bucket start (matches the 1m convention).
    """
    rule = TIMEFRAME_RULES.get(timeframe)
    if rule is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if df is None or df.empty:
        return []

    work = _regular_session_rows(df)
    if work.empty:
        return []
    work = work.set_index("dt").sort_index()

    if timeframe == "1m":
        # No aggregation needed; just normalize the rows.
        bars = work.reset_index()
    else:
        resample_kwargs = {"label": "left", "closed": "left"}
        if timeframe != "1d":
            # Intraday buckets must align to the 09:15 IST session open.
            # Otherwise 1h candles start at 09:00 because pandas anchors hourly
            # resampling at midnight by default.
            resample_kwargs.update({
                "origin": "start_day",
                "offset": pd.Timedelta(minutes=SESSION_START_MINUTE),
            })
        agg = work.resample(rule, **resample_kwargs).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
        # Drop empty buckets (weekends/holidays/overnight produce all-NaN rows).
        agg = agg.dropna(subset=["open", "high", "low", "close"])
        bars = agg.reset_index()

    out: List[Dict[str, Any]] = []
    for row in bars.to_dict(orient="records"):
        dt = row["dt"]
        ts_ms = int(pd.Timestamp(dt).tz_convert("UTC").value // 10**6)
        out.append({
            "ts": ts_ms,
            "time": ts_ms // 1000,  # unix seconds for lightweight-charts
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume") or 0),
        })
    out.sort(key=lambda b: b["ts"])
    return out


def _now_ist(now: Optional[pd.Timestamp] = None) -> pd.Timestamp:
    if now is None:
        return pd.Timestamp.now(tz="Asia/Kolkata")
    ts = pd.Timestamp(now)
    if ts.tzinfo is None:
        return ts.tz_localize("Asia/Kolkata")
    return ts.tz_convert("Asia/Kolkata")


def find_intraday_gaps(
    df: pd.DataFrame,
    max_report: int = 50,
    now: Optional[pd.Timestamp] = None,
) -> List[Dict[str, Any]]:
    """Identify trading days whose stored 1m count is below the expected 375.

    Returns up to `max_report` day summaries with the stored count and a small
    sample of missing minute timestamps (HH:MM IST), so the chart/audit can show
    exactly what is missing rather than just a percentage.
    """
    if df is None or df.empty:
        return []

    work = _regular_session_rows(df)
    if work.empty:
        return []

    gaps: List[Dict[str, Any]] = []
    current = _now_ist(now)
    current_date = current.strftime("%Y-%m-%d")
    current_minute = current.hour * 60 + current.minute
    for date_str, grp in work.groupby("date"):
        if date_str == current_date and current_minute < SESSION_END_MINUTE_EXCLUSIVE:
            continue
        stored = int(len(grp))
        if stored >= EXPECTED_1M_PER_SESSION:
            continue
        # Build the set of expected session minutes (09:15 .. 15:29 inclusive = 375).
        day = pd.Timestamp(f"{date_str} 09:15", tz="Asia/Kolkata")
        expected_index = pd.date_range(day, periods=EXPECTED_1M_PER_SESSION, freq="1min")
        present = set(grp["dt"].dt.floor("min"))
        missing = [t for t in expected_index if t not in present]
        gaps.append({
            "date": date_str,
            "stored": stored,
            "expected": EXPECTED_1M_PER_SESSION,
            "missing_count": len(missing),
            "missing_sample": [t.strftime("%H:%M") for t in missing[:20]],
        })
    gaps.sort(key=lambda g: g["date"])
    return gaps[:max_report]


async def build_ohlc_response(
    db_loader: Any,
    *,
    instrument: str,
    start_ts: Optional[int],
    end_ts: Optional[int],
    timeframe: str,
    include_gaps: bool = True,
) -> Dict[str, Any]:
    """Load 1m candles via `db_loader` and return resampled bars + gap report.

    `db_loader` is an async callable (instrument, start_ts, end_ts) -> DataFrame
    (injected so this module stays free of the DB import and is unit-testable).
    """
    if timeframe not in TIMEFRAME_RULES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    df = await db_loader(instrument, start_ts, end_ts)
    bars = resample_ohlc(df, timeframe)
    resp: Dict[str, Any] = {
        "instrument": instrument.upper(),
        "timeframe": timeframe,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "bar_count": len(bars),
        "bars": bars,
    }
    if include_gaps:
        resp["gaps"] = find_intraday_gaps(df)
        resp["gap_day_count"] = len(resp["gaps"])
    return resp
