"""Causal confluence primitives for the explosive-reversal detector.

These are the building blocks for catching sharp reversals that, near expiry and
in elevated volatility, can multiply OTM option premiums. Everything here is
CAUSAL (uses only the current and trailing bars) so there is no look-ahead bias.

Primitives:
  - round_level_proximity:  how close price is to a psychological/round level
                            (per-instrument step: NIFTY 50, BANKNIFTY/SENSEX 500).
  - support_resistance:     recent swing-cluster S/R levels and proximity to them.
  - rsi_divergence:         regular bullish/bearish RSI divergence vs price.
  - macd_divergence:        regular MACD-histogram divergence vs price.
  - reversal_candle:        bullish/bearish rejection candle (wick + close).

Each returns simple, explainable scalars/labels so the strategy can combine them
into a flexible confluence SCORE rather than a brittle all-or-nothing gate.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# Round-number step per instrument. NIFTY reacts at 50/100; the larger indices
# at 500 (user-specified for BANKNIFTY and SENSEX).
ROUND_STEP = {
    "NIFTY": 100,
    "BANKNIFTY": 500,
    "SENSEX": 500,
}
# Finer round step (half-step) that also carries psychological weight.
ROUND_STEP_MINOR = {
    "NIFTY": 50,
    "BANKNIFTY": 500,
    "SENSEX": 500,
}


def round_level_proximity(price: float, instrument: str, atr: Optional[float] = None) -> Dict[str, Any]:
    """Distance from price to the nearest round level, normalized by ATR.

    Returns {nearest_level, distance_pts, distance_atr, is_near}. `is_near` is
    True when within 0.5*ATR (or 0.1% of price when ATR is unavailable).
    """
    inst = str(instrument or "").upper()
    step = ROUND_STEP.get(inst, 100)
    minor = ROUND_STEP_MINOR.get(inst, step)
    # Consider both the major and minor round grids; take whichever is closer.
    candidates = []
    for s in {step, minor}:
        lvl = round(price / s) * s
        candidates.append(lvl)
    nearest = min(candidates, key=lambda lv: abs(price - lv))
    dist = abs(price - nearest)
    if atr and atr > 0:
        dist_atr = dist / atr
        is_near = dist_atr <= 0.5
    else:
        dist_atr = None
        is_near = dist <= price * 0.001
    return {
        "nearest_level": float(nearest),
        "distance_pts": round(float(dist), 2),
        "distance_atr": round(float(dist_atr), 3) if dist_atr is not None else None,
        "is_near": bool(is_near),
    }


def recent_sr_levels(
    df: pd.DataFrame,
    i: int,
    *,
    lookback: int = 60,
    cluster_atr_mult: float = 0.4,
) -> Dict[str, List[float]]:
    """Derive recent support/resistance from trailing swing points (causal).

    Uses the `is_swing_high`/`is_swing_low` columns (already causal) within the
    trailing `lookback` bars ending at i, clustering nearby swings into levels.
    Returns {"resistance": [...], "support": [...]} sorted by recency-weighted
    strength (here simply by proximity grouping).
    """
    lo = max(0, i - lookback)
    window = df.iloc[lo : i + 1]
    if window.empty:
        return {"resistance": [], "support": []}
    atr = float(window["atr"].iloc[-1]) if "atr" in window and not pd.isna(window["atr"].iloc[-1]) else 0.0
    tol = max(1e-6, cluster_atr_mult * atr) if atr > 0 else None

    def _cluster(values: List[float]) -> List[float]:
        if not values:
            return []
        if tol is None:
            return sorted(set(round(v, 2) for v in values))
        values = sorted(values)
        clusters: List[List[float]] = [[values[0]]]
        for v in values[1:]:
            if abs(v - clusters[-1][-1]) <= tol:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        return [round(float(np.mean(c)), 2) for c in clusters]

    highs = window.loc[window.get("is_swing_high", False) == True, "high"].tolist() if "is_swing_high" in window else []
    lows = window.loc[window.get("is_swing_low", False) == True, "low"].tolist() if "is_swing_low" in window else []
    return {"resistance": _cluster(highs), "support": _cluster(lows)}


def nearest_sr_proximity(price: float, levels: Dict[str, List[float]], atr: Optional[float]) -> Dict[str, Any]:
    """Closest S/R level to price and whether price is reacting at it."""
    all_levels = [("resistance", lv) for lv in levels.get("resistance", [])] + \
                 [("support", lv) for lv in levels.get("support", [])]
    if not all_levels:
        return {"kind": None, "level": None, "distance_pts": None, "distance_atr": None, "is_near": False}
    kind, lvl = min(all_levels, key=lambda kl: abs(price - kl[1]))
    dist = abs(price - lvl)
    if atr and atr > 0:
        dist_atr = dist / atr
        is_near = dist_atr <= 0.5
    else:
        dist_atr = None
        is_near = dist <= price * 0.001
    return {
        "kind": kind,
        "level": float(lvl),
        "distance_pts": round(float(dist), 2),
        "distance_atr": round(float(dist_atr), 3) if dist_atr is not None else None,
        "is_near": bool(is_near),
    }


def _last_two_pivots(series: pd.Series, pivot_mask: pd.Series) -> List[int]:
    """Return positional indices of the last two True pivots in series order."""
    idxs = [pos for pos, flag in enumerate(pivot_mask.tolist()) if flag]
    return idxs[-2:] if len(idxs) >= 2 else idxs


def rsi_divergence(df: pd.DataFrame, i: int, *, lookback: int = 40) -> Optional[str]:
    """Detect REGULAR RSI divergence at bar i using trailing swing pivots.

    Bearish: price makes a higher swing-high but RSI makes a lower high -> "BEARISH".
    Bullish: price makes a lower swing-low but RSI makes a higher low -> "BULLISH".
    Returns None when no clean divergence. Causal: only trailing bars used.
    """
    lo = max(0, i - lookback)
    w = df.iloc[lo : i + 1]
    if w.empty or "rsi" not in w or "is_swing_high" not in w:
        return None
    # Bearish via swing highs.
    hi_pos = _last_two_pivots(w["high"], w["is_swing_high"] == True)
    if len(hi_pos) == 2:
        p1, p2 = hi_pos
        price1, price2 = float(w["high"].iloc[p1]), float(w["high"].iloc[p2])
        r1, r2 = float(w["rsi"].iloc[p1]), float(w["rsi"].iloc[p2])
        if not (pd.isna(r1) or pd.isna(r2)) and price2 > price1 and r2 < r1:
            return "BEARISH"
    # Bullish via swing lows.
    lo_pos = _last_two_pivots(w["low"], w["is_swing_low"] == True)
    if len(lo_pos) == 2:
        p1, p2 = lo_pos
        price1, price2 = float(w["low"].iloc[p1]), float(w["low"].iloc[p2])
        r1, r2 = float(w["rsi"].iloc[p1]), float(w["rsi"].iloc[p2])
        if not (pd.isna(r1) or pd.isna(r2)) and price2 < price1 and r2 > r1:
            return "BULLISH"
    return None


def macd_divergence(df: pd.DataFrame, i: int, *, lookback: int = 40) -> Optional[str]:
    """Regular MACD-histogram divergence at bar i (same logic as RSI divergence)."""
    lo = max(0, i - lookback)
    w = df.iloc[lo : i + 1]
    if w.empty or "macd_hist" not in w or "is_swing_high" not in w:
        return None
    hi_pos = _last_two_pivots(w["high"], w["is_swing_high"] == True)
    if len(hi_pos) == 2:
        p1, p2 = hi_pos
        price1, price2 = float(w["high"].iloc[p1]), float(w["high"].iloc[p2])
        m1, m2 = float(w["macd_hist"].iloc[p1]), float(w["macd_hist"].iloc[p2])
        if price2 > price1 and m2 < m1:
            return "BEARISH"
    lo_pos = _last_two_pivots(w["low"], w["is_swing_low"] == True)
    if len(lo_pos) == 2:
        p1, p2 = lo_pos
        price1, price2 = float(w["low"].iloc[p1]), float(w["low"].iloc[p2])
        m1, m2 = float(w["macd_hist"].iloc[p1]), float(w["macd_hist"].iloc[p2])
        if price2 < price1 and m2 > m1:
            return "BULLISH"
    return None


def reversal_candle(row: pd.Series) -> Optional[str]:
    """Classify a single candle as a bullish/bearish rejection (wick) candle.

    Bullish: long lower wick, close in upper third (buyers rejected lows).
    Bearish: long upper wick, close in lower third (sellers rejected highs).
    """
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    rng = h - l
    if rng <= 0:
        return None
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    close_pos = (c - l) / rng  # 0 = at low, 1 = at high
    if lower_wick >= 0.5 * rng and close_pos >= 0.6:
        return "BULLISH"
    if upper_wick >= 0.5 * rng and close_pos <= 0.4:
        return "BEARISH"
    return None
