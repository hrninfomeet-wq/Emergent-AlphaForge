"""Promotion requires deterministic, finite strategy outputs in every engine path."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.backtest import run_backtest  # noqa: E402
from app.strategies.base import Signal, StrategyBase, validate_signal  # noqa: E402


class _SignalStrategy(StrategyBase):
    id = "signal_competency_test"
    parameter_schema = {}

    def __init__(self, signal):
        self._signal = signal

    def evaluate(self, row, prev, params, ctx):
        return self._signal


def _frame():
    n = 60
    return pd.DataFrame({
        "ts": np.arange(n, dtype=np.int64) * 60_000,
        "datetime": [f"2026-06-01T10:{i:02d}:00" for i in range(n)],
        "ist_time": ["10:00"] * n,
        "session_date": ["2026-06-01"] * n,
        "open": np.linspace(100, 110, n),
        "high": np.linspace(101, 111, n),
        "low": np.linspace(99, 109, n),
        "close": np.linspace(100, 110, n),
        "volume": np.ones(n),
    })


@pytest.mark.parametrize("signal,field", [
    (Signal(direction="CE", score=float("nan")), "score"),
    (Signal(direction="CE", score=80, spot_target_pts=float("inf")), "spot_target_pts"),
    (Signal(direction="CE", score=80, stop_pct=-float("inf")), "stop_pct"),
])
def test_validate_signal_rejects_nonfinite_numeric_fields(signal, field):
    with pytest.raises(ValueError, match=field):
        validate_signal(signal)


def test_backtest_rejects_nonfinite_signal_before_recording_trade():
    strategy = _SignalStrategy(Signal(direction="CE", score=float("nan")))
    with pytest.raises(ValueError, match="score"):
        run_backtest(_frame(), strategy, {}, costs_enabled=False)


def test_validate_signal_accepts_finite_no_trade_and_entry_signals():
    assert validate_signal(Signal(direction="NONE"))
    assert validate_signal(Signal(
        direction="PE", score=75, spot_target_pts=20.0, spot_stop_pts=10.0,
        time_stop_minutes=30,
    ))
