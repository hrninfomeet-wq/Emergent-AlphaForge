"""Tests for the post-hoc volatility detector (slice 7, part B)."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.volatility import (  # noqa: E402
    VolatilityConfig,
    annotate_volatility,
    summarize_spikes,
    trades_during_spikes,
)


def _smooth_frame(n: int = 200, base_price: float = 24000.0, drift: float = 0.05) -> pd.DataFrame:
    """A series with steady tiny moves - should never trip the spike detector."""
    rng = np.random.default_rng(42)
    closes = base_price + np.cumsum(rng.normal(drift, 0.5, size=n))
    return pd.DataFrame({
        "ts": [1779700000000 + i * 60000 for i in range(n)],
        "close": closes,
    })


def _frame_with_shock(n: int = 200, shock_at: int = 100, shock_magnitude_pct: float = 2.0) -> pd.DataFrame:
    """A smooth series with one large jump at `shock_at` - should trip the detector."""
    rng = np.random.default_rng(7)
    closes = 24000.0 + np.cumsum(rng.normal(0, 0.5, size=n))
    closes[shock_at:] += closes[shock_at - 1] * (shock_magnitude_pct / 100.0)
    return pd.DataFrame({
        "ts": [1779700000000 + i * 60000 for i in range(n)],
        "close": closes,
    })


# ---- annotate_volatility output schema ------------------------------------


def test_annotate_volatility_adds_required_columns():
    df = _smooth_frame(20)
    out = annotate_volatility(df)
    for col in ("realized_vol_5m", "vol_baseline_30d", "vol_ratio", "volatility_spike"):
        assert col in out.columns
    assert len(out) == len(df)


def test_annotate_empty_frame_returns_empty_with_columns():
    out = annotate_volatility(pd.DataFrame())
    for col in ("realized_vol_5m", "vol_baseline_30d", "vol_ratio", "volatility_spike"):
        assert col in out.columns


def test_annotate_short_frame_does_not_raise():
    """A frame shorter than the realized window must yield NaNs, not exceptions."""
    df = _smooth_frame(3)
    out = annotate_volatility(df)
    assert out["realized_vol_5m"].isna().all()
    assert out["volatility_spike"].sum() == 0


# ---- spike detection -------------------------------------------------------


def test_smooth_series_produces_no_spikes():
    """Tiny noise + drift should never exceed the 2.5x threshold."""
    df = _smooth_frame(2000)
    cfg = VolatilityConfig(spike_threshold=2.5, baseline_lookback_bars=375)
    out = annotate_volatility(df, cfg)
    # Allow a tiny tail of spurious spikes from random noise; <1% is acceptable
    spike_pct = out["volatility_spike"].sum() / max(1, len(out)) * 100
    assert spike_pct < 1.0, f"smooth series flagged {spike_pct}% spikes"


def test_explicit_shock_is_flagged_as_spike():
    """A 2% jump should produce at least one flagged minute."""
    df = _frame_with_shock(n=2000, shock_at=1500, shock_magnitude_pct=2.0)
    cfg = VolatilityConfig(spike_threshold=2.5, baseline_lookback_bars=200)
    out = annotate_volatility(df, cfg)
    # The bars right after the shock are where realized vol explodes
    post_shock = out.iloc[1500:1510]
    assert post_shock["volatility_spike"].any(), "the shock minutes were not flagged"


# ---- summarize_spikes ------------------------------------------------------


def test_summarize_spikes_counts_correctly():
    df = _frame_with_shock(n=1000, shock_at=600, shock_magnitude_pct=3.0)
    out = annotate_volatility(df, VolatilityConfig(spike_threshold=2.5, baseline_lookback_bars=200))
    summary = summarize_spikes(out)
    assert summary["total_bars"] == len(out)
    assert summary["spike_bars"] >= 1
    assert summary["spike_pct"] > 0


def test_summarize_empty_frame_returns_zeros():
    summary = summarize_spikes(pd.DataFrame())
    assert summary == {"total_bars": 0, "spike_bars": 0, "spike_pct": 0.0, "max_ratio": None}


# ---- trades_during_spikes --------------------------------------------------


def test_trades_during_spikes_tags_each_trade():
    df = _frame_with_shock(n=1000, shock_at=600, shock_magnitude_pct=3.0)
    enriched = annotate_volatility(df, VolatilityConfig(spike_threshold=2.5, baseline_lookback_bars=200))
    # Pick two trades: one before the shock window, one inside it
    base_ts = int(df["ts"].iloc[0])
    quiet_trade = {"entry_ts": base_ts + 100 * 60000}
    # Find a ts where volatility_spike is True
    spike_rows = enriched[enriched["volatility_spike"]]
    assert len(spike_rows) > 0
    spike_ts = int(spike_rows["ts"].iloc[0])
    spike_trade = {"entry_ts": spike_ts}

    tags = trades_during_spikes(
        trades=[quiet_trade, spike_trade],
        spot_df_with_vol=enriched,
    )
    assert len(tags) == 2
    by_idx = {t["trade_index"]: t for t in tags}
    assert by_idx[0]["during_spike"] is False
    assert by_idx[1]["during_spike"] is True


def test_trades_during_spikes_handles_missing_input_gracefully():
    assert trades_during_spikes(trades=[], spot_df_with_vol=pd.DataFrame()) == []


# ---- config round-trip -----------------------------------------------------


def test_volatility_config_from_dict_keeps_unspecified_defaults():
    cfg = VolatilityConfig.from_dict({"spike_threshold": 3.0})
    assert cfg.spike_threshold == 3.0
    assert cfg.realized_window == 5
    assert cfg.baseline_lookback_bars == 11250


def test_volatility_config_round_trip():
    cfg = VolatilityConfig(spike_threshold=3.5, realized_window=10, baseline_lookback_bars=500)
    cfg2 = VolatilityConfig.from_dict(cfg.to_dict())
    assert cfg2.spike_threshold == 3.5
    assert cfg2.realized_window == 10
    assert cfg2.baseline_lookback_bars == 500
