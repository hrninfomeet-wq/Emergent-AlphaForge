import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pandas as pd
import pytest
from app.vol_seasonality import tod_bucket, attach_tod_tradeable


def test_tod_bucket_5min():
    assert tod_bucket("09:15", 5) == tod_bucket("09:19", 5)
    assert tod_bucket("09:20", 5) != tod_bucket("09:15", 5)


def test_dead_bucket_blocked_live_bucket_open():
    # Build 10 sessions: one bucket always wide, one always flat.
    rows = []
    for d in range(10):
        for b in range(6):
            wide = b == 0
            rng = 10.0 if wide else 0.1
            rows.append({"session_date": f"2025-01-{d+1:02d}", "ist_time": f"09:{15+b*5:02d}",
                         "high": 100 + rng, "low": 100 - rng, "atr": 5.0})
    df = pd.DataFrame(rows)
    out = attach_tod_tradeable(df, lookback_sessions=5, min_atr_frac=0.6)
    df = df.assign(tradeable=out)
    last = df[df["session_date"] == "2025-01-10"]
    assert last[last["ist_time"] == "09:15"]["tradeable"].iloc[0]      # wide bucket -> tradeable
    assert not last[last["ist_time"] == "09:20"]["tradeable"].iloc[0]  # flat bucket -> blocked


def test_cold_start_defaults_tradeable():
    df = pd.DataFrame([{"session_date": "2025-01-01", "ist_time": "09:15",
                        "high": 100.1, "low": 99.9, "atr": 5.0}])
    out = attach_tod_tradeable(df, lookback_sessions=5)
    assert bool(out[0]) is True  # no history -> do not block
