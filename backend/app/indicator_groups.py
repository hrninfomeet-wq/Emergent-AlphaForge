"""Per-group indicator memoization for the optimizer/WFO hot path.

`precompute_all_indicators` (in `app.indicators`) recomputes ALL ~25 indicators
whenever any indicator-period param changes. For the flagship
`confluence_scalper` (which varies only `ema_fast`/`ema_slow`), that thrashes —
most of the per-trial precompute is redundant.

This module decomposes that monolithic precompute into an *ordered* registry of
indicator GROUPS. Each group declares the param keys whose values change its
output (including transitive value-level edges surfaced by the audit — e.g.
`atr_avg`/`tod`/`regime` all read the global `atr` column, so they key on
`atr_length`). `enrich_with_cache` memoizes each group's output columns keyed on
its own param tuple, recomputing ONLY the groups whose params changed and
reusing the rest.

Byte-identical by construction: every group calls the SAME helper(s) with the
SAME args, in the SAME order, reading from the working frame (which already
carries prior groups' columns) exactly as `precompute_all_indicators` does. The
host harness `tests/test_indicator_equivalence.py` proves equality against the
unchanged golden reference (precompute + classify_regime_series).

This module is host-importable: it imports only from pure modules
(`app.indicators`, `app.regime`, `app.cpr`, `app.vol_seasonality`).
"""
from __future__ import annotations

from typing import Callable, Dict, Tuple

import numpy as np
import pandas as pd

from app.indicators import (
    ema,
    rsi,
    macd,
    atr,
    adx,
    choppiness_index,
    session_vwap,
    detect_fvg,
    detect_swing_points,
    velocity_accel,
    variance_ratio,
    squeeze,
    supertrend,
    vwap_sigma_bands,
    nr7,
    cpr_levels,
    attach_tod_tradeable,
    candle_geometry,
)
from app.regime import classify_regime_series


class IndicatorGroup:
    """One indicator computation: its param-edge keys + a pure compute fn.

    `compute(df, p)` reads from `df` (the working frame, which already has prior
    groups' columns) and returns a dict {column_name: Series} of ONLY its own
    output columns — never mutating shared columns.
    """

    __slots__ = ("name", "param_keys", "compute")

    def __init__(self, name: str, param_keys: Tuple[str, ...],
                 compute: Callable[[pd.DataFrame, dict], Dict[str, pd.Series]]):
        self.name = name
        self.param_keys = param_keys
        self.compute = compute


# ---------------------------------------------------------------------------
# Group compute functions — each mirrors the corresponding precompute line(s)
# in `indicators.precompute_all_indicators` (lines ~248-306), calling identical
# helpers with identical args so output is byte-identical by construction.
# ---------------------------------------------------------------------------

def _compute_ema(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {
        "ema9": ema(df["close"], int(p.get("ema_fast", 9))),
        "ema21": ema(df["close"], int(p.get("ema_slow", 21))),
    }


def _compute_ema50(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"ema50": ema(df["close"], 50)}


def _compute_rsi(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"rsi": rsi(df["close"], int(p.get("rsi_length", 14)))}


def _compute_macd(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    macd_line, signal_line, hist = macd(
        df["close"],
        int(p.get("macd_fast", 12)),
        int(p.get("macd_slow", 26)),
        int(p.get("macd_signal", 9)),
    )
    return {"macd_line": macd_line, "macd_signal": signal_line, "macd_hist": hist}


def _compute_atr(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"atr": atr(df, int(p.get("atr_length", 14)))}


def _compute_adx(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"adx": adx(df, int(p.get("adx_length", 14)))}


def _compute_chop(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"chop": choppiness_index(df, int(p.get("chop_length", 14)))}


def _compute_time(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    # Session VWAP per day (anchored) — datetime columns. Mirrors precompute
    # lines ~265-278 exactly, including the NaT strftime fallback.
    dt = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    _dt = dt
    if _dt.isna().any():
        session_date = _dt.dt.strftime("%Y-%m-%d")
        ist_time = _dt.dt.strftime("%H:%M")
    else:
        _d = _dt.dt.normalize()
        session_date = (_d.dt.year.astype(str).str.zfill(4) + "-"
                        + _d.dt.month.astype(str).str.zfill(2) + "-"
                        + _d.dt.day.astype(str).str.zfill(2))
        ist_time = (_dt.dt.hour.astype(str).str.zfill(2) + ":"
                    + _dt.dt.minute.astype(str).str.zfill(2))
    return {"dt": dt, "session_date": session_date, "ist_time": ist_time}


def _compute_vwap(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    # Reads `session_date` (so the `time` group must run first). Replicates the
    # per-session groupby loop exactly.
    vwap = pd.Series(index=df.index, dtype="float64")
    for _, group in df.groupby("session_date", sort=False):
        vwap.loc[group.index] = session_vwap(group)
    return {"vwap": vwap}


def _compute_atr_avg(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    # Rolling mean of the global `atr` column -> keyed on atr_length.
    return {"atr_avg": df["atr"].rolling(100, min_periods=20).mean()}


def _compute_fvg(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"fvg": detect_fvg(df)}


def _compute_swing(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    # detect_swing_points RETURNS a copied frame with the two columns appended;
    # extract just our own columns.
    out = detect_swing_points(df, lookback=int(p.get("swing_lookback", 5)))
    return {"is_swing_high": out["is_swing_high"], "is_swing_low": out["is_swing_low"]}


def _compute_velocity(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    vel_z, accel_z = velocity_accel(
        df["close"], int(p.get("vel_n", 2)), int(p.get("vel_z_window", 60)))
    return {"vel_z": vel_z, "accel_z": accel_z}


def _compute_variance_ratio(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    vr, regime_score = variance_ratio(
        df["close"], int(p.get("vr_q", 4)), int(p.get("vr_lookback", 90)),
        float(p.get("vr_scale", 0.5)))
    return {"vr": vr, "regime_score": regime_score}


def _compute_squeeze(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    on, fire, mom = squeeze(
        df, int(p.get("bb_len", 20)), float(p.get("bb_mult", 2.0)),
        int(p.get("kc_len", 20)), float(p.get("kc_atr_mult", 1.5)),
        int(p.get("sqz_mom_len", 20)))
    return {"squeeze_on": on, "squeeze_fire": fire, "sqz_mom": mom}


def _compute_supertrend(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    st, st_dir = supertrend(
        df, int(p.get("st_period", 10)), float(p.get("st_mult", 3.0)))
    return {"supertrend": st, "st_dir": st_dir}


def _compute_vwap_sigma(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    sigma, u1, u2, l1, l2 = vwap_sigma_bands(df)
    return {"vwap_sigma": sigma, "vwap_u1": u1, "vwap_u2": u2,
            "vwap_l1": l1, "vwap_l2": l2}


def _compute_nr7(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"nr7": nr7(df)}


def _compute_cpr(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    cpr = cpr_levels(
        df, float(p.get("cpr_narrow_pctile", 30.0)),
        float(p.get("cpr_wide_pctile", 70.0)),
        int(p.get("cpr_pctile_window", 20)))
    return {c: cpr[c] for c in cpr.columns}


def _compute_orb_width(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    """Opening-range width as % of the prior-day pivot (cpr_p), scale-free.
    orb_width_pct_partial: current session's 09:15..(09:15+or_minutes) high-low /cpr_p,
      NaN until or_minutes bars have elapsed (no look-ahead).
    orb_width_pct_prior: the PRIOR completed session's value (shift across sessions),
      always available at session start.
    Reuses session_date + cpr_p (already computed by the time/cpr groups)."""
    or_minutes = int(p.get("or_minutes", 30))
    partial = pd.Series(np.nan, index=df.index, dtype="float64")
    per_session = {}
    for sdate, g in df.groupby("session_date", sort=False):
        start = g["dt"].iloc[0]
        cutoff = start + pd.Timedelta(minutes=or_minutes)
        win = g[g["dt"] < cutoff]
        if len(win):
            hi, lo = float(win["high"].max()), float(win["low"].min())
            piv = float(g["cpr_p"].iloc[0]) if "cpr_p" in g and len(g) else 0.0
            w = 100.0 * (hi - lo) / piv if piv else np.nan
            per_session[sdate] = w
            # partial known only from the cutoff bar onward (causal)
            partial.loc[g.index[g["dt"] >= cutoff]] = w
    order = list(dict.fromkeys(df["session_date"].tolist()))
    prior_map = {order[i]: (per_session.get(order[i-1], np.nan) if i > 0 else np.nan)
                 for i in range(len(order))}
    prior = df["session_date"].map(lambda s: prior_map.get(s, np.nan)).astype("float64")
    return {"orb_width_pct_partial": partial, "orb_width_pct_prior": prior}


def _compute_tod_tradeable(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    # Reads global `df['atr']` -> atr_length edge.
    return {"tod_tradeable": attach_tod_tradeable(
        df, int(p.get("tod_lookback_sessions", 20)),
        float(p.get("tod_min_atr_frac", 0.6)))}


def _compute_geometry(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return candle_geometry(df)


def _compute_regime(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    # Reads adx/chop/atr/atr_avg -> keyed on adx_length, atr_length, chop_length.
    # ALWAYS assembled last; df here has NO pre-existing `regime` column.
    return {"regime": classify_regime_series(df)}


# ---------------------------------------------------------------------------
# Ordered registry — SAME order as precompute writes its columns.
# Param-independent groups have param_keys = ().
# ---------------------------------------------------------------------------
GROUPS = [
    IndicatorGroup("ema", ("ema_fast", "ema_slow"), _compute_ema),
    IndicatorGroup("ema50", (), _compute_ema50),
    IndicatorGroup("rsi", ("rsi_length",), _compute_rsi),
    IndicatorGroup("macd", ("macd_fast", "macd_slow", "macd_signal"), _compute_macd),
    IndicatorGroup("atr", ("atr_length",), _compute_atr),
    IndicatorGroup("adx", ("adx_length",), _compute_adx),
    IndicatorGroup("chop", ("chop_length",), _compute_chop),
    IndicatorGroup("time", (), _compute_time),
    IndicatorGroup("vwap", (), _compute_vwap),
    IndicatorGroup("atr_avg", ("atr_length",), _compute_atr_avg),
    IndicatorGroup("fvg", (), _compute_fvg),
    IndicatorGroup("swing", ("swing_lookback",), _compute_swing),
    IndicatorGroup("velocity", ("vel_n", "vel_z_window"), _compute_velocity),
    IndicatorGroup("variance_ratio", ("vr_q", "vr_lookback", "vr_scale"), _compute_variance_ratio),
    IndicatorGroup("squeeze", ("bb_len", "bb_mult", "kc_len", "kc_atr_mult", "sqz_mom_len"), _compute_squeeze),
    IndicatorGroup("supertrend", ("st_period", "st_mult"), _compute_supertrend),
    IndicatorGroup("vwap_sigma", (), _compute_vwap_sigma),
    IndicatorGroup("nr7", (), _compute_nr7),
    IndicatorGroup("cpr", ("cpr_narrow_pctile", "cpr_wide_pctile", "cpr_pctile_window"), _compute_cpr),
    IndicatorGroup("orb_width", ("or_minutes",), _compute_orb_width),
    IndicatorGroup("tod_tradeable", ("tod_lookback_sessions", "tod_min_atr_frac", "atr_length"), _compute_tod_tradeable),
    IndicatorGroup("geometry", (), _compute_geometry),
    IndicatorGroup("regime", ("adx_length", "atr_length", "chop_length"), _compute_regime),
]


def enrich_with_cache(raw_df: pd.DataFrame, params: dict,
                      group_caches: Dict[str, Dict], *, max_per_group: int = 4) -> pd.DataFrame:
    """Assemble the enriched frame, recomputing only groups whose params changed.

    `group_caches`: dict[group_name -> dict[param_key_tuple -> dict[col->Series]]].
    Param-independent groups have key () -> computed once, reused forever. Cached
    Series are valid because `raw_df` is fixed per job (same index) and every
    transitive param edge is encoded in the group's param_keys.
    """
    df = raw_df.copy()
    for group in GROUPS:
        key = tuple(params.get(k) for k in group.param_keys)
        cache = group_caches.setdefault(group.name, {})
        cols = cache.get(key)
        if cols is None:
            cols = group.compute(df, params)
            if len(cache) < max_per_group:
                cache[key] = cols
        for c, s in cols.items():
            df[c] = s
    return df


def run_all_groups(raw_df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Same group loop with NO cache (fresh group_caches each call). Convenience
    used by the harness to exercise the cache-miss path."""
    return enrich_with_cache(raw_df, params, {})


# ---------------------------------------------------------------------------
# Shared indicator-period params (single source of truth for the seam fix).
#
# These tune the SHARED enrichment (precompute_all_indicators /
# enrich_with_cache), not any one strategy. StrategyBase.merged_params()
# accepts them for EVERY strategy so that optimizer-tuned indicator periods
# genuinely flow through trials, saved presets, Backtest Lab re-runs, and
# paper deployments. optimizer.py keeps its own literal INDICATOR_PARAM_KEYS
# (host tests string-pin it there) with an import-time guard that the two
# tuples never drift.
# ---------------------------------------------------------------------------
SHARED_INDICATOR_PARAM_KEYS = (
    "ema_fast", "ema_slow", "rsi_length",
    "macd_fast", "macd_slow", "macd_signal",
    "atr_length", "adx_length", "chop_length", "swing_lookback",
    "vel_n", "vel_z_window", "vr_q", "vr_lookback", "vr_scale",
    "bb_len", "bb_mult", "kc_len", "kc_atr_mult", "sqz_mom_len",
    "st_period", "st_mult",
    "cpr_narrow_pctile", "cpr_wide_pctile", "cpr_pctile_window",
    "or_minutes",
    "tod_lookback_sessions", "tod_min_atr_frac",
)
