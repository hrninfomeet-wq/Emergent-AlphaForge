"""Vectorized indicators ported from reference repo's JS. Pure functions on pd.Series/DataFrame."""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Tuple


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast=12, slow=26, signal=9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line.fillna(0), signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    return pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()


def session_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].fillna(0)
    has_volume = (vol > 0).any()
    if has_volume:
        return (typical * vol).cumsum() / vol.cumsum().replace(0, np.nan)
    return typical.expanding().mean()


def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(df)
    atr_s = tr.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / length, min_periods=length, adjust=False).mean() / atr_s
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / length, min_periods=length, adjust=False).mean() / atr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()


def choppiness_index(df: pd.DataFrame, length: int = 14) -> pd.Series:
    h, l = df["high"], df["low"]
    tr = true_range(df)
    sum_tr = tr.rolling(length).sum()
    high_n = h.rolling(length).max()
    low_n = l.rolling(length).min()
    chop = 100 * np.log10(sum_tr / (high_n - low_n).replace(0, np.nan)) / np.log10(length)
    return chop


def fibonacci_levels(swing_high: float, swing_low: float) -> dict:
    """Standard Fibonacci retracement levels."""
    diff = swing_high - swing_low
    return {
        "0.0": swing_low,
        "0.236": swing_low + 0.236 * diff,
        "0.382": swing_low + 0.382 * diff,
        "0.5": swing_low + 0.5 * diff,
        "0.618": swing_low + 0.618 * diff,
        "0.786": swing_low + 0.786 * diff,
        "1.0": swing_high,
        "1.272": swing_low + 1.272 * diff,
        "1.618": swing_low + 1.618 * diff,
    }


def detect_fvg(df: pd.DataFrame) -> pd.Series:
    """Fair Value Gap detection — 3-candle imbalance.
    Returns 'UP', 'DOWN', or None per row (FVG location is at index i; gap is between i-2 and i)."""
    out = pd.Series([None] * len(df), index=df.index, dtype=object)
    for i in range(2, len(df)):
        h_prev2 = df.iloc[i - 2]["high"]
        l_prev2 = df.iloc[i - 2]["low"]
        h_cur = df.iloc[i]["high"]
        l_cur = df.iloc[i]["low"]
        if l_cur > h_prev2:
            out.iloc[i] = "UP"
        elif h_cur < l_prev2:
            out.iloc[i] = "DOWN"
    return out


def detect_swing_points(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """Mark swing highs/lows using rolling N-bar window."""
    out = df.copy()
    out["is_swing_high"] = (df["high"] == df["high"].rolling(2 * lookback + 1, center=True).max())
    out["is_swing_low"] = (df["low"] == df["low"].rolling(2 * lookback + 1, center=True).min())
    return out


def precompute_all_indicators(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Compute all indicators needed by built-in strategies. Returns enriched df."""
    p = params or {}
    df = df.copy()
    df["ema9"] = ema(df["close"], int(p.get("ema_fast", 9)))
    df["ema21"] = ema(df["close"], int(p.get("ema_slow", 21)))
    df["ema50"] = ema(df["close"], 50)
    df["rsi"] = rsi(df["close"], int(p.get("rsi_length", 14)))
    macd_line, signal_line, hist = macd(
        df["close"],
        int(p.get("macd_fast", 12)),
        int(p.get("macd_slow", 26)),
        int(p.get("macd_signal", 9)),
    )
    df["macd_line"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_hist"] = hist
    df["atr"] = atr(df, int(p.get("atr_length", 14)))
    df["adx"] = adx(df, int(p.get("adx_length", 14)))
    df["chop"] = choppiness_index(df, int(p.get("chop_length", 14)))
    # Session VWAP per day (anchored)
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    df["session_date"] = df["dt"].dt.strftime("%Y-%m-%d")
    vwap = pd.Series(index=df.index, dtype="float64")
    for _, group in df.groupby("session_date", sort=False):
        vwap.loc[group.index] = session_vwap(group)
    df["vwap"] = vwap
    df["ist_time"] = df["dt"].dt.strftime("%H:%M")
    df["atr_avg"] = df["atr"].rolling(100, min_periods=20).mean()
    df["fvg"] = detect_fvg(df)
    df = detect_swing_points(df, lookback=int(p.get("swing_lookback", 5)))
    return df
