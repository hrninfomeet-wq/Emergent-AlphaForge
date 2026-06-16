"""Central Pivot Range + floor pivots + day-type, computed once per session
from the PRIOR completed session's OHLC. Width day-type uses a rolling
percentile (scale-free -> portable NIFTY<->SENSEX). Pure + causal."""
from __future__ import annotations
import numpy as np
import pandas as pd

_LEVEL_COLS = ["cpr_p", "cpr_tc", "cpr_bc", "cpr_width_pct", "day_type", "R1", "S1", "R2", "S2"]


def cpr_levels(df: pd.DataFrame, narrow_pctile: float = 30.0, wide_pctile: float = 70.0,
               pctile_window: int = 20) -> pd.DataFrame:
    """Attach CPR + pivots + day_type per bar (keyed by `session_date`).
    Requires a `session_date` column. Returns a frame with `_LEVEL_COLS`
    aligned to df's index."""
    g = df.groupby("session_date", sort=False)
    sess = pd.DataFrame({
        "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
    }).sort_index()
    ph, pl, pc = sess["high"].shift(1), sess["low"].shift(1), sess["close"].shift(1)
    P = (ph + pl + pc) / 3.0
    BC = (ph + pl) / 2.0
    TC = 2.0 * P - BC
    tc = pd.concat([TC, BC], axis=1).max(axis=1)
    bc = pd.concat([TC, BC], axis=1).min(axis=1)
    width = (tc - bc) / P * 100.0
    lo = width.rolling(pctile_window, min_periods=3).quantile(narrow_pctile / 100.0)
    hi = width.rolling(pctile_window, min_periods=3).quantile(wide_pctile / 100.0)
    day_type = pd.Series("NEUTRAL", index=width.index)
    day_type[width <= lo] = "TREND"
    day_type[width >= hi] = "RANGE"
    day_type[width.isna()] = "NEUTRAL"
    sess_levels = pd.DataFrame({
        "cpr_p": P, "cpr_tc": tc, "cpr_bc": bc, "cpr_width_pct": width, "day_type": day_type,
        "R1": 2 * P - pl, "S1": 2 * P - ph, "R2": P + (ph - pl), "S2": P - (ph - pl),
    })
    joined = df[["session_date"]].join(sess_levels, on="session_date")
    return joined[_LEVEL_COLS]
