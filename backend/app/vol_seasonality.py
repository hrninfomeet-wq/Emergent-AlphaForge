"""Trailing intraday vol-seasonality time-gate. For each 5-min IST bucket,
estimate mean ATR-normalized range over the PRIOR `lookback_sessions` sessions;
gate entries to buckets whose conditional range clears the theta hurdle. Causal
(shift(1) -> prior sessions only). Phase-A trailing form; the WFO train-estimate
is Phase C."""
from __future__ import annotations
import numpy as np
import pandas as pd


def tod_bucket(ist_time: str, minutes: int = 5) -> int:
    h, m = int(ist_time[:2]), int(ist_time[3:5])
    return (h * 60 + m) // minutes


def attach_tod_tradeable(df: pd.DataFrame, lookback_sessions: int = 20,
                         min_atr_frac: float = 0.6, bucket_min: int = 5) -> np.ndarray:
    """Return a boolean array aligned to df: True where the bar's time-bucket has
    historically (prior sessions) cleared the range hurdle. Requires
    `session_date`, `ist_time`, `atr`. Cold-start (no history) -> True."""
    d = df.copy()
    d["_bucket"] = d["ist_time"].map(lambda t: tod_bucket(t, bucket_min))
    d["_rng_atr"] = (d["high"] - d["low"]) / d["atr"].replace(0, np.nan)
    per = (d.groupby(["session_date", "_bucket"])["_rng_atr"].mean()
             .reset_index().sort_values("session_date"))
    per["edge"] = (per.groupby("_bucket")["_rng_atr"]
                      .transform(lambda s: s.shift(1).rolling(lookback_sessions, min_periods=2).mean()))
    per["tradeable"] = (per["edge"] >= min_atr_frac) | per["edge"].isna()  # cold start -> True
    key = per.set_index(["session_date", "_bucket"])["tradeable"]
    pairs = list(zip(d["session_date"], d["_bucket"]))
    return np.array([bool(key.get(p, True)) for p in pairs], dtype=bool)
