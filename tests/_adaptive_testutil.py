"""Shared synthetic-OHLC builders for adaptive-toolkit tests (host-safe)."""
import numpy as np
import pandas as pd

IST = "Asia/Kolkata"


def make_ohlc(closes, *, start="2025-01-01 09:15", high_pad=0.5, low_pad=0.5, volume=0.0):
    """1m OHLC frame from a close path. ts is epoch-ms (UTC). One continuous run."""
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.date_range(start=start, periods=n, freq="1min", tz=IST)
    ts = (idx.asi8 // 1_000_000).astype("int64")  # UTC epoch-ms; asi8 avoids deprecated .view
    return pd.DataFrame({
        "ts": ts,
        "open": closes,
        "high": closes + high_pad,
        "low": closes - low_pad,
        "close": closes,
        "volume": np.full(n, float(volume)),
    })


def make_sessions(per_session_closes, *, start_date="2025-01-01"):
    """Stack multiple trading sessions (each a list of closes) into one frame
    with correct ist session_date boundaries. Returns the frame WITH a
    `session_date` column already set (skips the precompute step)."""
    frames = []
    day = pd.Timestamp(start_date)
    for closes in per_session_closes:
        f = make_ohlc(closes, start=f"{day.date()} 09:15")
        f["session_date"] = str(day.date())
        frames.append(f)
        day += pd.Timedelta(days=1)
    return pd.concat(frames, ignore_index=True)
