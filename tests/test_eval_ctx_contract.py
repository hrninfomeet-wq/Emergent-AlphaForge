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


from app.backtest import run_backtest


def _probe_df(n=60):
    """n in-window bars with the OHLC/ts/ist_time/session_date run_backtest reads."""
    base_ms = 1_700_000_000_000
    rows = []
    for k in range(n):
        rows.append({
            "ts": base_ms + k * 60_000,
            "datetime": f"2025-01-02T11:{k % 60:02d}:00",
            "ist_time": "11:00",
            "session_date": "2025-01-02",
            "open": 100.0 + k * 0.1, "high": 100.6 + k * 0.1,
            "low": 99.4 + k * 0.1, "close": 100.0 + k * 0.1,
        })
    return pd.DataFrame(rows)


def test_backtest_passes_canonical_ctx_to_evaluate():
    seen = []

    class _Probe(StrategyBase):
        id = "probe_bt"
        def session_precompute(self, df, params):
            return {"__probe_extra__": 7}
        def evaluate(self, row, prev, params, ctx):
            seen.append(dict(ctx))   # snapshot keys+values at this bar
            return Signal(direction="NONE")

    run_backtest(_probe_df(), _Probe(), {}, instrument="NIFTY")
    assert seen, "evaluate was never reached"
    canonical = set(EVAL_CTX_KEYS) | {"__probe_extra__"}
    for snap in seen:
        assert canonical.issubset(snap.keys())
        assert snap["instrument"] == "NIFTY"
        assert snap["session_date"] == "2025-01-02"
        assert snap["__probe_extra__"] == 7
        assert isinstance(snap["i"], int)
