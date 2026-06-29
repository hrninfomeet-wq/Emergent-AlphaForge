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
