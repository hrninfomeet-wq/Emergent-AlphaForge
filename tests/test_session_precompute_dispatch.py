"""The generalized per-session precompute dispatch in run_backtest.

run_backtest must merge ANY strategy's session_precompute() output into the
per-bar ctx (generalizing the former opening_range_breakout-only special case),
and the Opening-Range-Breakout precompute must stay byte-identical after moving
onto the hook.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd

from app.backtest import run_backtest
from app.strategies.base import StrategyBase, Signal
from app.strategies.plugins.opening_range_breakout import OpeningRangeBreakout


def _run_df(n=60, sess="2025-01-02"):
    rows = []
    for k in range(n):
        mm = 30 + k
        ist = f"09:{mm:02d}" if mm < 60 else f"10:{mm - 60:02d}"
        px = 100.0 + k * 0.1
        rows.append({"ts": 1_700_000_000 + k * 60, "session_date": sess, "ist_time": ist,
                     "datetime": f"2025-01-02 {ist}", "open": px, "high": px + 1.0,
                     "low": px - 1.0, "close": px})
    return pd.DataFrame(rows)


def test_run_backtest_merges_session_precompute_into_ctx():
    seen = {}

    class _Probe(StrategyBase):
        id = "probe_sp"
        parameter_schema = {}

        def session_precompute(self, df, params):
            return {"sentinel_map": {"x": 1}}

        def evaluate(self, row, prev, params, ctx):
            seen["has"] = ctx.get("sentinel_map") == {"x": 1}
            return Signal(direction="NONE")

    run_backtest(_run_df(), _Probe(), {}, instrument="NIFTY")
    assert seen.get("has") is True


def test_strategy_without_hook_still_runs():
    """A strategy that does not override session_precompute backtests fine."""
    class _Plain(StrategyBase):
        id = "plain_sp"
        parameter_schema = {}

        def evaluate(self, row, prev, params, ctx):
            return Signal(direction="NONE")

    res = run_backtest(_run_df(), _Plain(), {}, instrument="NIFTY")
    assert "trades" in res and "metrics" in res


def _ref_orb(df, range_minutes):
    """Frozen copy of the original _compute_orb_for_session."""
    orb_hi, orb_lo = {}, {}
    for date, grp in df.groupby("session_date"):
        first_bars = grp.head(range_minutes)
        if len(first_bars) > 0:
            orb_hi[date] = float(first_bars["high"].max())
            orb_lo[date] = float(first_bars["low"].min())
    return {"orb_hi": orb_hi, "orb_lo": orb_lo}


def test_orb_session_precompute_byte_identical_to_reference():
    df = pd.concat([_run_df(40, "2025-01-02"), _run_df(40, "2025-01-03")], ignore_index=True)
    s = OpeningRangeBreakout()
    p = s.default_params()
    assert s.session_precompute(df, p) == _ref_orb(df, int(p["range_minutes"]))
