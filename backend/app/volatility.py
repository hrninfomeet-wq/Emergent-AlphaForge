"""Post-hoc volatility detector.

Given a stored spot candle frame, compute per-minute realized volatility and
compare to the same instrument's 30-day rolling average. Bars where the ratio
exceeds the spike threshold (default 2.5x) get tagged as `volatility_spike`.

Why post-hoc instead of an event calendar (per user spec 2026-05-29):
  - Reliable scheduled-event timestamps for India do not exist in a
    machine-readable form we can trust.
  - This approach measures what actually happened in the market - true noise,
    earnings shocks, RBI surprises, even technical sweeps. No external feed.
  - Used by the slippage model to widen tails on flagged minutes, and by
    forward-metrics analysis to filter "trades that fired during chaos".

Computation:
  - 5-minute realized vol  = std(close.pct_change()) over a trailing 5 bars
  - 30-day baseline        = mean of the same on the previous 30 trading days
                             (about 30 * 375 = 11,250 bars). Recomputed once
                             per session_date and cached on the row.
  - ratio                  = realized / baseline
  - flag                   = ratio >= spike_threshold (default 2.5)

We do not annualize. The ratio is what matters, not the absolute number.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


SPIKE_THRESHOLD_DEFAULT = 2.5
REALIZED_WINDOW = 5            # bars
BASELINE_LOOKBACK_BARS = 11250  # ~30 trading days * 375 minutes


@dataclass
class VolatilityConfig:
    spike_threshold: float = SPIKE_THRESHOLD_DEFAULT
    realized_window: int = REALIZED_WINDOW
    baseline_lookback_bars: int = BASELINE_LOOKBACK_BARS

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "VolatilityConfig":
        if not data:
            return cls()
        cfg = cls()
        for k in ("spike_threshold", "realized_window", "baseline_lookback_bars"):
            if k in data:
                try:
                    setattr(cfg, k, type(getattr(cfg, k))(data[k]))
                except (TypeError, ValueError):
                    pass
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spike_threshold": self.spike_threshold,
            "realized_window": self.realized_window,
            "baseline_lookback_bars": self.baseline_lookback_bars,
        }


def annotate_volatility(df: pd.DataFrame, cfg: Optional[VolatilityConfig] = None) -> pd.DataFrame:
    """Return a copy of df with three new columns:
      - realized_vol_5m
      - vol_baseline_30d
      - vol_ratio
      - volatility_spike   (bool: ratio >= threshold)

    Input frame must have at least `close` and `ts` columns. The frame is
    assumed to be sorted by ts ascending (the warehouse already does this).

    Empty or short input is handled by returning a frame with the new columns
    set to NaN / False, never raising.
    """
    cfg = cfg or VolatilityConfig()
    out = df.copy()
    if out.empty or "close" not in out.columns:
        out["realized_vol_5m"] = np.nan
        out["vol_baseline_30d"] = np.nan
        out["vol_ratio"] = np.nan
        out["volatility_spike"] = False
        return out

    closes = out["close"].astype(float)
    returns = closes.pct_change()
    realized = returns.rolling(window=int(cfg.realized_window), min_periods=int(cfg.realized_window)).std()
    # Baseline: shift forward by realized_window so we don't include the just-computed
    # realized vol in its own baseline window
    baseline_input = realized.shift(int(cfg.realized_window))
    baseline = baseline_input.rolling(
        window=int(cfg.baseline_lookback_bars),
        min_periods=int(cfg.baseline_lookback_bars) // 2,
    ).mean()
    ratio = realized / baseline
    out["realized_vol_5m"] = realized
    out["vol_baseline_30d"] = baseline
    out["vol_ratio"] = ratio
    out["volatility_spike"] = (ratio >= float(cfg.spike_threshold)).fillna(False)
    return out


def summarize_spikes(df_with_vol: pd.DataFrame) -> Dict[str, Any]:
    """Quick numerical summary for a backtest run's audit context."""
    if df_with_vol.empty or "volatility_spike" not in df_with_vol.columns:
        return {"total_bars": 0, "spike_bars": 0, "spike_pct": 0.0, "max_ratio": None}
    total = int(len(df_with_vol))
    spikes = int(df_with_vol["volatility_spike"].sum())
    max_ratio = df_with_vol["vol_ratio"].max()
    return {
        "total_bars": total,
        "spike_bars": spikes,
        "spike_pct": round(spikes / max(1, total) * 100, 2),
        "max_ratio": float(max_ratio) if pd.notna(max_ratio) else None,
    }


def trades_during_spikes(
    *,
    trades: List[Dict[str, Any]],
    spot_df_with_vol: pd.DataFrame,
    entry_ts_field: str = "entry_ts",
) -> List[Dict[str, Any]]:
    """Tag each trade with whether its entry minute was a volatility spike.

    Returns a list of trade IDs / index positions plus their spike flags so the
    backtest result can show "12 of 47 trades fired during high-vol minutes".
    Unobtrusive - does not mutate the input trades list.
    """
    if not trades or spot_df_with_vol.empty or "volatility_spike" not in spot_df_with_vol.columns:
        return []
    spike_ts = set(
        int(ts) for ts, flag in zip(spot_df_with_vol["ts"], spot_df_with_vol["volatility_spike"]) if flag
    )
    out: List[Dict[str, Any]] = []
    for i, t in enumerate(trades):
        ts = t.get(entry_ts_field)
        if ts is None:
            continue
        try:
            ts_int = int(ts)
        except (TypeError, ValueError):
            continue
        out.append({
            "trade_index": i,
            "entry_ts": ts_int,
            "during_spike": ts_int in spike_ts,
        })
    return out
