"""SP-2 seed structural features (ICT vocabulary) for capability-aware authoring.

Each feature is causal (trailing-window / shift / forward-pass only), reuses the
pure helpers in app.indicators where possible, and is registered into the SP-1
FeatureGroup registry at import time via app.features.catalog.register_feature.

Host-importable: imports only pandas / numpy / app.indicators / app.features.* --
no motor, no I/O (same discipline as indicator_groups.py).

Live feasibility (see feature_live_feasible): swing_levels / premium_discount /
displacement are vectorized + bounded -> live-correct. fvg_zones / choch /
order_block are stateful_unbounded -> backtest-only in v1.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from app.features.registry import FeatureGroup
from app.features.catalog import register_feature

# ---- compute fns + registrations are appended by the following tasks ----


# ---------------------------------------------------------------------------
# FEATURE 1 — swing_levels
# ---------------------------------------------------------------------------

def compute_swing_levels(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    high, low = df["high"], df["low"]
    is_sh, is_sl = df["is_swing_high"], df["is_swing_low"]
    last_high = high.where(is_sh).ffill().shift(1)
    last_low = low.where(is_sl).ffill().shift(1)
    swept_high = (high > last_high).fillna(False)
    swept_low = (low < last_low).fillna(False)
    return {
        "last_swing_high_level": last_high,
        "last_swing_low_level": last_low,
        "swing_high_swept": swept_high,
        "swing_low_swept": swept_low,
    }


register_feature(
    FeatureGroup(
        name="swing_levels",
        columns=("last_swing_high_level", "last_swing_low_level",
                 "swing_high_swept", "swing_low_swept"),
        param_keys=(),
        requires=(),
        cost_class="vectorized",
        session_anchored=False,
        stateful_unbounded=False,
        min_history_bars=2,
        compute=compute_swing_levels,
    ),
    description="Most-recent confirmed swing high/low price levels (causal, shifted) "
                "plus liquidity-sweep flags. Foundation for premium/discount, BOS, "
                "and order blocks.",
    data_requirements=["ohlcv_1m"],
)
