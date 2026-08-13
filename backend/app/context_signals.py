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

    highs = window.loc[window.get("is_swing_high", False) == True, "high"].tolist() if "is_swing_high" in window else []
    lows = window.loc[window.get("is_swing_low", False) == True, "low"].tolist() if "is_swing_low" in window else []
    return {"resistance": _cluster_levels(highs, tol), "support": _cluster_levels(lows, tol)}


def _cluster_levels(values: List[float], tol: Optional[float]) -> List[float]:
    """Group nearby swing prices into levels. Lifted out of `recent_sr_levels`
    verbatim so the indexed fast path below calls the SAME clustering rather than
    a copy of it — a second implementation would be free to drift."""
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


# ─── Indexed fast path ───────────────────────────────────────────────────────
# The three helpers above each slice `df.iloc[i-lookback : i+1]` on EVERY bar and
# then index into it, which builds a DataFrame and a stack of Series per bar. In
# the optimizer that dominated everything: a single explosive_reversal backtest
# spent ~28s making 833,700 DataFrame.__getitem__ and 98,238 _slice calls.
#
# But all three only ever consult SWING PIVOTS, and pivots are sparse — a few
# hundred in ~57k bars. Precompute their positions once (`build_pivot_index`,
# from a strategy's session_precompute) and each per-bar lookup becomes two
# binary searches into an int array. The functions below MUST return exactly what
# their DataFrame twins return; tests/test_context_signals_indexed_equivalence.py
# checks that at EVERY bar of a real frame, not on a sample.


def build_pivot_index(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Precompute the sparse swing-pivot index the `*_indexed` lookups consume.

    Call ONCE per frame (session_precompute), never per bar. Returns None when the
    frame cannot support the lookups, and callers then fall back to the DataFrame
    helpers — so a missing column degrades to today's behaviour, not to a wrong
    answer."""
    if df is None or getattr(df, "empty", True):
        return None
    if "is_swing_high" not in df or "is_swing_low" not in df:
        return None

    def _col(name: str):
        # `== True` mirrors the mask the DataFrame helpers build, so an object
        # column holding NaN is False here exactly as it is there. A bare
        # astype(bool) would turn NaN into True and invent pivots.
        return df[name].to_numpy() if name in df else None

    return {
        "high": _col("high"), "low": _col("low"), "atr": _col("atr"),
        "rsi": _col("rsi"), "macd_hist": _col("macd_hist"),
        "hi_idx": np.flatnonzero((df["is_swing_high"] == True).to_numpy()),
        "lo_idx": np.flatnonzero((df["is_swing_low"] == True).to_numpy()),
        "n": int(len(df)),
    }


def _pivots_in_window(idx_arr, lo: int, i: int):
    """Absolute indices of the pivots inside the INCLUSIVE window [lo, i] — the
    same span `df.iloc[lo : i+1]` covers."""
    a = int(np.searchsorted(idx_arr, lo, side="left"))
    b = int(np.searchsorted(idx_arr, i, side="right"))
    return idx_arr[a:b]


def _divergence_indexed(px, i: int, lookback: int, value_key: str, guard_nan: bool) -> Optional[str]:
    """Shared body of the two indexed divergence lookups. `guard_nan` reproduces a
    real asymmetry in the originals: rsi_divergence skips NaN readings explicitly,
    macd_divergence does not (its comparisons just fall through). Preserved rather
    than tidied — tidying it would change results."""
    vals = (px or {}).get(value_key)
    if not px or vals is None or px.get("high") is None or px.get("low") is None:
        return None
    lo = max(0, i - lookback)
    hi = _pivots_in_window(px["hi_idx"], lo, i)
    if len(hi) >= 2:
        p1, p2 = int(hi[-2]), int(hi[-1])
        price1, price2 = float(px["high"][p1]), float(px["high"][p2])
        v1, v2 = float(vals[p1]), float(vals[p2])
        if (not (guard_nan and (pd.isna(v1) or pd.isna(v2)))) and price2 > price1 and v2 < v1:
            return "BEARISH"
    lw = _pivots_in_window(px["lo_idx"], lo, i)
    if len(lw) >= 2:
        p1, p2 = int(lw[-2]), int(lw[-1])
        price1, price2 = float(px["low"][p1]), float(px["low"][p2])
        v1, v2 = float(vals[p1]), float(vals[p2])
        if (not (guard_nan and (pd.isna(v1) or pd.isna(v2)))) and price2 < price1 and v2 > v1:
            return "BULLISH"
    return None


def rsi_divergence_indexed(px, i: int, *, lookback: int = 40) -> Optional[str]:
    """`rsi_divergence` against a prebuilt pivot index."""
    return _divergence_indexed(px, i, lookback, "rsi", guard_nan=True)


def macd_divergence_indexed(px, i: int, *, lookback: int = 40) -> Optional[str]:
    """`macd_divergence` against a prebuilt pivot index."""
    return _divergence_indexed(px, i, lookback, "macd_hist", guard_nan=False)


def recent_sr_levels_indexed(px, i: int, *, lookback: int = 60,
                             cluster_atr_mult: float = 0.4) -> Dict[str, List[float]]:
    """`recent_sr_levels` against a prebuilt pivot index."""
    empty: Dict[str, List[float]] = {"resistance": [], "support": []}
    if not px or i < 0 or i >= px["n"] or px.get("high") is None or px.get("low") is None:
        return empty
    lo = max(0, i - lookback)
    atr_arr = px.get("atr")
    atr = 0.0
    if atr_arr is not None:
        _a = atr_arr[i]
        atr = 0.0 if pd.isna(_a) else float(_a)
    tol = max(1e-6, cluster_atr_mult * atr) if atr > 0 else None
    highs = [float(v) for v in px["high"][_pivots_in_window(px["hi_idx"], lo, i)]]
    lows = [float(v) for v in px["low"][_pivots_in_window(px["lo_idx"], lo, i)]]
    return {"resistance": _cluster_levels(highs, tol), "support": _cluster_levels(lows, tol)}


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
