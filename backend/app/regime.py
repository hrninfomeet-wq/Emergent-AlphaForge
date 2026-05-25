"""Market regime detection — ADX + Choppiness + ATR expansion."""
import pandas as pd
import numpy as np


def regime_label(adx_val: float, chop_val: float, atr_now: float, atr_avg: float, direction_hint: str = "") -> str:
    if pd.isna(adx_val) or pd.isna(chop_val):
        return "UNKNOWN"
    expanding = (not pd.isna(atr_now)) and (not pd.isna(atr_avg)) and atr_avg > 0 and atr_now / atr_avg >= 1.15
    if adx_val >= 25 and chop_val < 40:
        return "TREND_EXPANDING" if expanding else "TREND"
    if adx_val < 20 and chop_val > 60:
        return "CHOP" if not expanding else "VOLATILE_CHOP"
    return "MIXED"


def classify_regime_series(df: pd.DataFrame) -> pd.Series:
    if "regime" in df.columns:
        return df["regime"]
    return pd.Series(
        [regime_label(a, c, t, ta) for a, c, t, ta in zip(df["adx"], df["chop"], df["atr"], df["atr_avg"])],
        index=df.index,
    )
