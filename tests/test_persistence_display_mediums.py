"""The six remaining result-persistence-display findings (all CONFIRMED).

1. **List projection is inverted.** `GET /backtest/runs` strips the SMALL spot
   `trades` array and keeps the much larger `option_backtest.trades` (~45 keys per
   row, plus nested `charges`/`context`, plus `option_backtest.equity_curve` and
   `portfolio.curve`). The journal asks for `limit=200`, so the trimming the
   projection exists to provide never happens.

2. **`buildExecutionFromRun` drops fields the backend builder carries.** It claims
   parity with `execution_from_option_config` but omits `trade_window_start/end`,
   `sizing_config` and `spread_min_pts` — so a preset saved from a displayed run
   deploys at a different size and window from the run that justified it.

3. **`candles_capped` is hardcoded False for premium runs** while the loader
   truncates oldest-first at `OPTION_CANDLE_LOAD_CAP`. The ordinary path detects
   and reports this; the UI warning is keyed on the flag, so it can never fire for
   a premium run.

4. **A failed or still-running backtest needs an explicit warning.** Run status is
   advisory evidence rather than a promotion veto, but it must be visible and
   acknowledged before a technically valid config is deployed.

5. **Monte Carlo silently truncates to the first 1000 trades** and reports that as
   the sample size, taking the HEAD rather than a random sample.

6. **`data_coverage` is written only by the sync endpoint**; the async one the UI
   actually calls discards it.
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "backtestMetrics.js")
_LAB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "pages",
                    "BacktestLab.jsx")


def _src(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# 1. list projection
# --------------------------------------------------------------------------- #

def test_the_list_endpoint_uses_an_inclusion_projection():
    from app.routers import research
    src = inspect.getsource(research.list_backtest_runs)
    assert '"trades": 0' not in src, (
        "exclusion projection kept the LARGER option_backtest.trades array")
    assert '"option_backtest.metrics": 1' in src


def test_the_list_projection_carries_what_the_journal_renders():
    """The journal's family-aware KPIs need metrics + portfolio + dispatch."""
    from app.routers import research
    src = inspect.getsource(research.list_backtest_runs)
    for field in ('"id": 1', '"created_at": 1', '"name": 1', '"instrument": 1',
                  '"strategy_id": 1', '"status": 1', '"metrics": 1',
                  '"significance": 1', '"option_backtest.metrics": 1',
                  '"option_backtest.portfolio": 1', '"option_backtest.dispatch": 1'):
        assert field in src, f"journal needs {field}"


def test_the_list_projection_excludes_the_heavy_arrays():
    from app.routers import research
    src = inspect.getsource(research.list_backtest_runs)
    assert '"option_backtest.trades"' not in src
    assert '"equity_curve": 1' not in src


def test_profit_factor_is_persisted_so_a_trimmed_row_can_still_show_it():
    """PF was derived client-side from the full option trade array. Once the list
    stops shipping that array, PF must come from the metrics dict."""
    from app.option_backtest import _compute_metrics
    m = _compute_metrics([
        {"status": "PAIRED", "option_pnl_pts": 1.0, "option_pnl_value": 300.0,
         "gross_option_pnl_value": 300.0, "total_charges": 0.0, "quantity": 1},
        {"status": "PAIRED", "option_pnl_pts": -1.0, "option_pnl_value": -100.0,
         "gross_option_pnl_value": -100.0, "total_charges": 0.0, "quantity": 1},
    ])
    assert m["profit_factor"] == 3.0


def test_profit_factor_is_none_with_no_losses_matching_the_spot_convention():
    from app.option_backtest import _compute_metrics
    m = _compute_metrics([
        {"status": "PAIRED", "option_pnl_pts": 1.0, "option_pnl_value": 300.0,
         "gross_option_pnl_value": 300.0, "total_charges": 0.0, "quantity": 1},
    ])
    assert m["profit_factor"] is None


# --------------------------------------------------------------------------- #
# 2. preset built from a displayed run
# --------------------------------------------------------------------------- #

def test_build_execution_from_run_carries_the_trade_window():
    src = _src(_LAB)
    i = src.index("buildExecutionFromRun")
    body = src[i:i + 3000]
    assert "trade_window_start" in body and "trade_window_end" in body


def test_build_execution_from_run_carries_sizing_config():
    src = _src(_LAB)
    i = src.index("buildExecutionFromRun")
    assert "sizing_config" in src[i:i + 3000]


def test_build_execution_from_run_carries_spread_min_pts():
    src = _src(_LAB)
    i = src.index("buildExecutionFromRun")
    assert "spread_min_pts" in src[i:i + 3000]


# --------------------------------------------------------------------------- #
# 3. candles_capped must be measured, not asserted
# --------------------------------------------------------------------------- #

def test_the_premium_loader_reports_whether_it_truncated():
    from app.routers import premium_momentum_routes as pmr
    src = inspect.getsource(pmr._load_window)
    assert "OPTION_CANDLE_LOAD_CAP" in src
    assert "capped" in src.lower()


def test_runtime_no_longer_hardcodes_candles_capped_false():
    from app import runtime
    src = inspect.getsource(runtime._run_paired_option_backtest)
    assert '"candles_capped": False' not in src, (
        "the flag is asserted rather than measured, so the UI cap warning can "
        "never fire for a premium run")


# --------------------------------------------------------------------------- #
# 4. incomplete deployment source is advisory and explicit
# --------------------------------------------------------------------------- #

def test_backtest_run_loader_does_not_hard_block_on_result_status():
    from app import runtime
    src = inspect.getsource(runtime._load_deployment_source)
    assert "deploy only from a completed run" not in src


def test_incomplete_run_quality_warning_is_explicit():
    from app import deployment_quality
    src = inspect.getsource(deployment_quality.evaluate_source_quality)
    assert "incomplete_backtest_run" in src
    assert "acknowledgment" in inspect.getsource(deployment_quality)


# --------------------------------------------------------------------------- #
# 5. Monte Carlo truncation
# --------------------------------------------------------------------------- #

def test_monte_carlo_no_longer_silently_takes_the_head():
    src = _src(_LAB)
    i = src.index("monte-carlo-card") if "monte-carlo-card" in src else src.index("pnlKey")
    block = src[max(0, i - 4000):i + 4000]
    assert ".slice(0, 1000)" not in block, "still takes the first 1000 trades silently"


def test_monte_carlo_filters_to_paired_trades_explicitly():
    """It relied on Number.isFinite to drop MISSING_* rows."""
    src = _src(_LAB)
    i = src.index("pnlKey")
    block = src[max(0, i - 1500):i + 1500]
    assert "PAIRED" in block


def test_monte_carlo_discloses_the_sample_when_it_truncates():
    src = _src(_LAB)
    assert "mc-sample-note" in src


# --------------------------------------------------------------------------- #
# 6. data_coverage on the endpoint the UI uses
# --------------------------------------------------------------------------- #

def test_the_async_endpoint_persists_data_coverage():
    from app.routers import research
    src = inspect.getsource(research.run_backtest_job)
    assert "data_coverage" in src, (
        "the endpoint the UI calls discards the coverage the sync one promises")


def test_the_async_endpoint_does_not_discard_the_coverage_return():
    from app.routers import research
    src = inspect.getsource(research.run_backtest_job)
    assert "df, _ = await attach_required_data" not in src
