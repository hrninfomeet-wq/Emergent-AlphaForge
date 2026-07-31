"""Behavioral regressions for the optimizer MED integrity closure.

Findings #14 and #23 were already closed by the broader HIGH #18/#28 fixes and
remain pinned in ``test_optimizer_verified_high_regressions.py``.  This module
covers the six MED findings that still require implementation.
"""
from __future__ import annotations

import inspect
import os
import sys
from types import SimpleNamespace

import pandas as pd
import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import optimizer, wfo  # noqa: E402


def _candidate(period: int) -> dict:
    return {
        "params": {"period": period},
        "objective_value": float(period),
        "metrics": {"trade_count": 20, "sharpe": 1.0},
    }


@pytest.mark.asyncio
async def test_17_premium_rerank_honours_stop_and_keeps_partial_result(monkeypatch):
    """A pause/cancel after finalist one must not evaluate finalist two."""
    from app import premium_trigger_dispatch
    from app.routers import premium_momentum_routes

    calls = []

    async def fake_load_window(*_args, **_kwargs):
        return pd.DataFrame({"ts": [1, 2]}), pd.DataFrame(), []

    def fake_dispatch(**kwargs):
        calls.append(dict(kwargs["merged_params"]))
        return {
            "metrics": {
                "total_option_pnl_value": 100.0,
                "total_option_pnl_pts": 10.0,
                "win_rate": 50.0,
                "paired_trade_count": 10,
            },
            "coverage": {"paired_trade_count": 10},
        }

    async def should_stop():
        return len(calls) >= 1

    monkeypatch.setattr(premium_momentum_routes, "_load_window", fake_load_window)
    monkeypatch.setattr(premium_trigger_dispatch, "dispatch_full_backtest", fake_dispatch)

    strategy = SimpleNamespace(
        id="premium_test",
        merged_params=lambda params: dict(params),
    )
    ranked, _contracts, _candles, budget_hit, stopped = (
        await optimizer._option_rerank_premium_trigger(
            [_candidate(1), _candidate(2)],
            lambda _params: pd.DataFrame({"ts": [1, 2]}),
            strategy,
            "NIFTY",
            min_trades=0,
            should_stop=should_stop,
        )
    )

    assert [row["params"] for row in ranked] == [{"period": 1}]
    assert calls == [{"period": 1}]
    assert budget_hit is False
    assert stopped is True


def test_17_ordinary_rerank_checks_stop_in_both_long_loops():
    src = inspect.getsource(optimizer._option_rerank)
    assert "should_stop" in src
    assert src.count("await should_stop()") >= 2
