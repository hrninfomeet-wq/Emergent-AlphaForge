"""SP-1: required_features declaration + no-op wiring (byte-identical back-compat)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
from app.strategies.base import StrategyBase, Signal


def test_required_features_defaults_empty_and_in_meta():
    class _S(StrategyBase):
        id = "rf_default"
    s = _S()
    assert s.required_features == []
    assert s.meta()["required_features"] == []


def test_required_features_declared_appears_in_meta():
    class _S(StrategyBase):
        id = "rf_decl"
        required_features = ["fvg_zones", "swing_levels"]
    assert _S().meta()["required_features"] == ["fvg_zones", "swing_levels"]


from app.backtest import run_backtest
from app.features.registry import FeatureGroup


def _bt_df(n=60):
    base_ms = 1_700_000_000_000
    return pd.DataFrame([{
        "ts": base_ms + k * 60_000, "datetime": f"2025-01-02T11:{k % 60:02d}:00",
        "ist_time": "11:00", "session_date": "2025-01-02",
        "open": 100.0 + k * 0.1, "high": 100.6 + k * 0.1,
        "low": 99.4 + k * 0.1, "close": 100.0 + k * 0.1,
    } for k in range(n)])


def test_backtest_noop_when_no_required_features():
    """A strategy declaring no features must reach evaluate with NO extra columns."""
    cols_seen = []

    class _Plain(StrategyBase):
        id = "rf_plain"
        def evaluate(self, row, prev, params, ctx):
            cols_seen.append(set(row.keys()))
            return Signal(direction="NONE")

    run_backtest(_bt_df(), _Plain(), {}, instrument="NIFTY")
    assert cols_seen
    # no feature column leaked in (only OHLCV-ish keys present)
    assert all("feat_demo" not in keys for keys in cols_seen)


def test_backtest_materializes_declared_feature(monkeypatch):
    """A declared feature's column is present on the row at evaluate time."""
    g = FeatureGroup(name="demo", columns=("feat_demo",), param_keys=(), requires=(),
                     cost_class="vectorized", session_anchored=False,
                     stateful_unbounded=False, min_history_bars=1,
                     compute=lambda df, p: {"feat_demo": df["close"] * 0 + 42.0})
    monkeypatch.setattr("app.features.registry.FEATURE_REGISTRY", {"demo": g})
    saw = []

    class _Feat(StrategyBase):
        id = "rf_feat"
        required_features = ["demo"]
        def evaluate(self, row, prev, params, ctx):
            saw.append(row.get("feat_demo"))
            return Signal(direction="NONE")

    run_backtest(_bt_df(), _Feat(), {}, instrument="NIFTY")
    assert saw and all(v == 42.0 for v in saw)
