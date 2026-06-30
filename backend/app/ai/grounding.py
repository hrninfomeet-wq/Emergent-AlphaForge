"""Generate the AI 'vocabulary' (available indicator columns, Signal fields,
strategy param schemas) FROM LIVE CODE, so the doc/AI prompts can never drift
from what the engine actually computes. Pure + host-safe (no motor)."""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List

import numpy as np
import pandas as pd

_RAW_COLS = {"ts", "datetime", "open", "high", "low", "close", "volume",
             "dt", "session_date", "ist_time"}


def _sample_frame(n: int = 420) -> pd.DataFrame:
    """A minimal but valid 1m OHLCV frame so precompute_all_indicators produces
    every column it would in production. Values are synthetic; only the column
    set matters here. 420 bars clears the longest rolling window (atr_avg=100)."""
    start_ms = 1_700_000_000_000
    ts = start_ms + np.arange(n) * 60_000
    base = 20000.0 + np.cumsum(np.sin(np.arange(n) / 7.0))
    high = base + 5.0
    low = base - 5.0
    close = base + np.cos(np.arange(n) / 5.0)
    vol = np.full(n, 1000.0)
    return pd.DataFrame({"ts": ts, "open": base, "high": high, "low": low,
                         "close": close, "volume": vol})


def build_grounding_catalog() -> Dict[str, Any]:
    """Return {indicator_columns, signal_fields, strategies}."""
    from app.indicators import precompute_all_indicators
    from app.regime import classify_regime_series
    from app.strategies.base import Signal, get_registry

    df = precompute_all_indicators(_sample_frame())
    df["regime"] = classify_regime_series(df)
    indicator_columns = sorted(c for c in df.columns if c not in _RAW_COLS)

    signal_fields: List[Dict[str, Any]] = []
    for f in dataclasses.fields(Signal):
        signal_fields.append({"name": f.name, "type": str(f.type)})

    reg = get_registry()
    if not reg.list_all():
        reg.auto_discover()
    strategies = reg.list_all()

    from app.features.catalog import feature_catalog_entries
    from app.features.registry import FEATURE_REGISTRY

    feature_entries = feature_catalog_entries()
    # The augmented column universe advertised to the AI: the flat, sorted set of
    # every registered feature's columns (empty in SP-1 -> []). The full per-feature
    # metadata (requires / cost_class / live_feasible / ...) is available via
    # feature_catalog_entries(); here we expose the NAMES the agent may reference.
    feature_columns = sorted(
        set().union(*(set(g.columns) for g in FEATURE_REGISTRY.values()))
    ) if FEATURE_REGISTRY else []
    all_columns = sorted(set(indicator_columns) | set(feature_columns))

    return {
        "indicator_columns": indicator_columns,
        "feature_columns": feature_columns,
        "feature_entries": feature_entries,
        "all_columns_including_features": all_columns,
        "signal_fields": signal_fields,
        "strategies": strategies,
    }
