"""SP-0: the canonical ctx contract is identical across backtest / live / smoke."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
from app.strategies.base import (
    StrategyBase, Signal, EVAL_CTX_KEYS, build_eval_ctx, build_live_eval_ctx,
)


def test_build_eval_ctx_has_canonical_keys_and_merges_extras():
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    ctx = build_eval_ctx(
        history_df=df, i=2, instrument="NIFTY", session_date="2025-01-02",
        mode="SCALP", session_extras={"day_open": {"2025-01-02": 100.0}},
    )
    for k in EVAL_CTX_KEYS:
        assert k in ctx, f"missing canonical key {k}"
    assert ctx["i"] == 2 and ctx["instrument"] == "NIFTY"
    assert ctx["session_date"] == "2025-01-02" and ctx["mode"] == "SCALP"
    assert ctx["history_df"] is df
    assert ctx["day_open"] == {"2025-01-02": 100.0}


def test_build_eval_ctx_defaults_mode_and_tolerates_no_extras():
    ctx = build_eval_ctx(history_df=None, i=0, instrument="NIFTY",
                         session_date="", session_extras=None)
    assert ctx["mode"] == "INTRADAY"
    assert set(EVAL_CTX_KEYS).issubset(ctx.keys())


def test_build_live_eval_ctx_calls_session_precompute():
    class _Probe(StrategyBase):
        id = "probe_live"
        def session_precompute(self, df, params):
            return {"__seen__": True, "n": len(df)}
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0], "session_date": ["d", "d", "d"]})
    ctx = build_live_eval_ctx(_Probe(), df, last_idx=2, instrument="BANKNIFTY",
                              params={"mode": "INTRADAY"})
    assert ctx["__seen__"] is True and ctx["n"] == 3       # session_precompute ran + merged
    assert ctx["i"] == 2 and ctx["instrument"] == "BANKNIFTY"
    assert ctx["session_date"] == "d" and ctx["history_df"] is df
