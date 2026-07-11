import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pandas as pd
from app.strategies.base import get_registry
from app.indicators import precompute_all_indicators
from tests._adaptive_testutil import make_sessions

NEW_IDS = ["squeeze_expansion_breakout", "adaptive_regime_scalper"]


def test_new_strategies_auto_discovered():
    reg = get_registry()
    reg.auto_discover()
    for sid in NEW_IDS:
        s = reg.get(sid)
        assert s is not None, f"{sid} not auto-discovered"
        assert "NIFTY" in s.supported_instruments and "SENSEX" in s.supported_instruments


def test_strategies_run_over_enriched_frame_without_error():
    # a few synthetic sessions of varied price action -> enrich -> run each strategy on every bar
    rng = np.random.default_rng(7)
    sessions = []
    for _ in range(6):
        base = 100.0 + rng.standard_normal(80).cumsum()
        sessions.append(list(base))
    df = make_sessions(sessions)
    out = precompute_all_indicators(df)
    reg = get_registry()
    reg.auto_discover()
    for sid in NEW_IDS:
        s = reg.get(sid)
        p = s.default_params()
        n_sig = 0
        for i in range(len(out)):
            row = out.iloc[i]
            prev = out.iloc[i - 1] if i > 0 else None
            ctx = {"i": i, "history_df": out, "instrument": "NIFTY"}
            sig = s.evaluate(row, prev, p, ctx)
            assert sig.direction in ("CE", "PE", "NONE")
            if sig.direction in ("CE", "PE"):
                # ATR exits attached by the base
                assert sig.spot_target_pts is not None and sig.spot_stop_pts is not None
                n_sig += 1
        # no assertion on count (synthetic data may not trigger), but must not error
