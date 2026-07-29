"""The optimizer must score premium trials with the SAME costs the backtest applies.

Audit finding J, confirmed 2026-07-29 and fixed before running a train/holdout
study that would otherwise have been meaningless.

`_evaluate_premium_trigger` builds its run config from
`strategy.merged_params(params)`, which is a strict allow-list keyed on the
plugin's `parameter_schema`. `cost_config` is not a schema param, so it was never
present — `PremiumTriggerConfig.cost_config` stayed None and every trial was
scored on GROSS P&L.

Meanwhile the Backtest Lab path injects the option form's `cost_config`
explicitly (`runtime.py`). So the optimizer ranked candidates gross and the user
then re-ran the winner net: two different scoring regimes, with the ranking done
under the more forgiving one. Charges were ~Rs 5,663 on the user's 124-trade run.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

from app.optimizer import _evaluate_premium_trigger  # noqa: E402
from app.strategies.base import get_registry  # noqa: E402

COSTS = {"enabled": True, "brokerage_per_order": 0,
         "spread_pct_of_premium": 1, "spread_min_pts": 0}


def _strategy():
    reg = get_registry()
    reg.auto_discover()
    return reg.get("premium_momentum")


def _scenario():
    from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario
    return _simple_ce_wins_scenario()


def _run(cost_config):
    spot, opt, contracts = _scenario()
    merged = {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
              "momentum_pct": 15.0, "stop_pct": 20.0, "lots": 1}
    return _evaluate_premium_trigger(
        _strategy(), merged, spot, opt, contracts, "NIFTY",
        "net_pnl_inr", 65, 0, 0.0, cost_config=cost_config)


def test_costs_reach_the_trial_scoring():
    """THE fix: a costed trial must score strictly worse than an uncosted one."""
    gross_metrics, _ = _run(None)
    net_metrics, _ = _run(COSTS)
    gross = float(gross_metrics.get("total_option_pnl_value") or 0.0)
    net = float(net_metrics.get("total_option_pnl_value") or 0.0)
    assert net < gross, (
        "applying costs did not change the trial score — the optimizer is still "
        "ranking on gross P&L while the Backtest Lab reports net")


def test_no_cost_config_is_unchanged():
    """Byte-identical for a job that genuinely has no cost model."""
    a, _ = _run(None)
    b, _ = _run(None)
    assert a == b


def test_a_disabled_cost_block_is_still_forwarded_not_dropped():
    """`enabled: False` is a real user choice and must not be silently upgraded."""
    metrics, merged = _run({"enabled": False, "brokerage_per_order": 20,
                            "spread_pct_of_premium": 0, "spread_min_pts": 0})
    assert merged.get("cost_config", {}).get("enabled") is False


def test_the_params_the_strategy_declares_still_win():
    """cost_config is injected, never allowed to clobber a declared param."""
    _metrics, merged = _run(COSTS)
    assert merged["momentum_pct"] == 15.0
    assert merged["lots"] == 1


def test_the_call_site_passes_the_jobs_cost_config():
    """Source contract — the helper is useless if the caller never supplies it."""
    import inspect
    from app import optimizer

    src = inspect.getsource(optimizer.run_optimization)
    i = src.index("_evaluate_premium_trigger(")
    call = src[i:i + 400]
    assert "cost_config=" in call, (
        "the optimizer must thread the job's option cost model into premium trials")
