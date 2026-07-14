"""Stage-1 trial scoring for premium_momentum — optimizer.py::_evaluate_premium_trigger.

Session 3 wired Stage 2 (_option_rerank_premium_trigger / _survival_eval_oos_premium_trigger
/ runtime's _run_paired_option_backtest) through dispatch_full_backtest, but Stage 1 —
run_optimization's per-trial `evaluate(params)` closure — still called the module-level
_evaluate -> run_backtest -> strategy.evaluate(), which is a deliberate stub for
premium_momentum (real logic lives only in deployment_evaluator.py, never touched here).
Every trial therefore scored trade_count=0 and _objective_value's UNCONDITIONAL
zero-trade guard (it fires before min_trades, so min_trades=0 can't bypass it) returned
the _DISQUALIFY sentinel — confirmed live on a real 10-trial Bayesian job. Stage 1 could
never select a candidate for the now-working Stage 2.

These tests pin the fix: _evaluate_premium_trigger dispatches a trial through
dispatch_full_backtest and reshapes the envelope into the metrics-dict contract that
_objective_value / _RESUME_METRIC_KEYS / _robustness_score / _heatmap already consume —
(metrics, merged_params), a drop-in for _evaluate's return shape.

Host-safe: pure function under test (no motor event loop, no DB); fixtures mirror
tests/test_premium_trigger_optimizer_dispatch.py.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.optimizer import _evaluate_premium_trigger, _objective_value  # noqa: E402
from app.rerank_select import DISQUALIFY  # noqa: E402

# Every objective string _objective_value can take. A metrics dict built by
# _evaluate_premium_trigger must feed ALL of them without tripping the
# zero-trade/min_trades disqualification when a real trade exists.
OBJECTIVES = ("risk_adjusted", "sharpe", "profit_factor", "total_pnl_pts",
              "net_pnl_inr", "win_rate", "neg_max_dd")

LOT_SIZE = 75  # NIFTY


# --------------------------------------------------------------------------
# Fixtures (same shape as test_premium_trigger_optimizer_dispatch.py).
# --------------------------------------------------------------------------
def _spot_bar(ts, ist, close, session="2026-07-10"):
    return {"ts": ts, "ist_time": ist, "close": close, "session_date": session}


def _opt(key, ts, close):
    return {"instrument_key": key, "ts": ts, "close": close}


def _simple_ce_wins_scenario():
    """CE premium 100 -> 150 crossing +15% at ts3. PE never crosses."""
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24010.0),
        _spot_bar(3, "09:33", 24020.0), _spot_bar(4, "09:34", 24020.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 110.0),
        _opt("CE|23950", 3, 120.0), _opt("CE|23950", 4, 150.0),
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 101.0),
        _opt("PE|24050", 3, 102.0), _opt("PE|24050", 4, 103.0),
    ])
    return spot, opt, contracts


def _ce_stop_loss_scenario():
    """CE crosses +15% at ts3 (120 >= 115) then collapses through the -20%
    premium stop at ts4. PE stays flat. One PAIRED losing trade."""
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24010.0),
        _spot_bar(3, "09:33", 24020.0), _spot_bar(4, "09:34", 24000.0),
        _spot_bar(5, "09:35", 23990.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 110.0),
        _opt("CE|23950", 3, 120.0), _opt("CE|23950", 4, 90.0),
        _opt("CE|23950", 5, 88.0),
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 100.0),
        _opt("PE|24050", 3, 100.0), _opt("PE|24050", 4, 100.0),
        _opt("PE|24050", 5, 100.0),
    ])
    return spot, opt, contracts


class _FakeStrategy:
    """Minimal stand-in for a registry strategy object — only `.id` is read
    by _evaluate_premium_trigger / dispatch_full_backtest."""
    def __init__(self, strategy_id="premium_momentum"):
        self.id = strategy_id


_TRIGGER_PARAMS = {"reference_time": "09:31", "moneyness": "itm1",
                   "side": "first_to_trigger", "momentum_pct": 15.0,
                   "stop_pct": 20.0, "lots": 1}


def _score(metrics, objective, *, min_trades=0, min_direction_share=0.0):
    return _objective_value(metrics, objective, lot_size=LOT_SIZE,
                            min_trades=min_trades,
                            min_direction_share=min_direction_share)


# =========================================================================
# (a) A real momentum trigger must produce trade_count > 0 and a metrics dict
#     that NO objective branch disqualifies.
# =========================================================================
def test_real_trigger_produces_trades_and_no_objective_disqualifies():
    spot, opt, contracts = _simple_ce_wins_scenario()
    metrics, merged = _evaluate_premium_trigger(
        _FakeStrategy(), dict(_TRIGGER_PARAMS), spot, opt, contracts,
        "NIFTY", "risk_adjusted", LOT_SIZE, 0, 0.0)
    assert metrics["trade_count"] >= 1
    for objective in OBJECTIVES:
        val = _score(metrics, objective)
        assert val > DISQUALIFY, (
            f"objective {objective!r} returned the disqualification sentinel "
            f"for a trial with {metrics['trade_count']} real trade(s)")
        assert math.isfinite(val), f"objective {objective!r} returned non-finite {val!r}"


def test_return_shape_is_drop_in_for_module_level_evaluate():
    """(metrics, merged_params) — same contract as _evaluate, so the closure's
    obj(metrics) and trial_history bookkeeping work unchanged."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    params = dict(_TRIGGER_PARAMS)
    out = _evaluate_premium_trigger(
        _FakeStrategy(), params, spot, opt, contracts,
        "NIFTY", "risk_adjusted", LOT_SIZE, 0, 0.0)
    assert isinstance(out, tuple) and len(out) == 2
    metrics, merged = out
    assert isinstance(metrics, dict)
    assert merged == params  # merged_params passes through untouched


def test_win_rate_scale_matches_spot_metrics_0_to_100():
    """backtest.py's spot metrics and option_backtest.py's _compute_metrics both
    emit win_rate on the 0-100 scale (wins/n*100) — the scale _objective_value's
    win_rate branch was written against. An all-win fixture must score 100.0,
    not 1.0 (off-by-100 would silently deflate the win_rate objective)."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    metrics, _ = _evaluate_premium_trigger(
        _FakeStrategy(), dict(_TRIGGER_PARAMS), spot, opt, contracts,
        "NIFTY", "win_rate", LOT_SIZE, 0, 0.0)
    assert metrics["win_rate"] == 100.0
    assert _score(metrics, "win_rate") == 100.0


def test_only_wins_profit_factor_is_large_but_finite():
    spot, opt, contracts = _simple_ce_wins_scenario()
    metrics, _ = _evaluate_premium_trigger(
        _FakeStrategy(), dict(_TRIGGER_PARAMS), spot, opt, contracts,
        "NIFTY", "profit_factor", LOT_SIZE, 0, 0.0)
    pf = metrics["profit_factor"]
    assert pf is not None and math.isfinite(pf) and pf > 1.0


def test_losing_trade_scores_honestly_not_disqualified():
    """A stop-out is a REAL (bad) result: it must stay in the search surface
    with honest numbers (pf 0.0, win_rate 0.0, negative pnl, positive rupee
    max-dd proxy) — not be disqualified, and not be dressed up."""
    spot, opt, contracts = _ce_stop_loss_scenario()
    metrics, _ = _evaluate_premium_trigger(
        _FakeStrategy(), dict(_TRIGGER_PARAMS), spot, opt, contracts,
        "NIFTY", "risk_adjusted", LOT_SIZE, 0, 0.0)
    assert metrics["trade_count"] == 1
    assert metrics["losses"] == 1
    assert metrics["win_rate"] == 0.0
    assert metrics["profit_factor"] == 0.0
    assert metrics["total_pnl_pts"] < 0
    assert metrics["max_dd_pts"] > 0  # rupee max-drawdown proxy, see helper comment
    for objective in OBJECTIVES:
        assert _score(metrics, objective) > DISQUALIFY
    assert _score(metrics, "neg_max_dd") < 0


# =========================================================================
# (b) No trigger -> trade_count == 0 -> honestly disqualified.
# =========================================================================
def test_no_trigger_zero_trades_is_disqualified():
    spot, opt, contracts = _simple_ce_wins_scenario()
    # 90% is VALID config (PremiumTriggerConfig caps at 100) but unreachable:
    # CE only rises +50%, PE +3% -> no side ever crosses -> zero trades.
    params = {**_TRIGGER_PARAMS, "momentum_pct": 90.0}
    metrics, _ = _evaluate_premium_trigger(
        _FakeStrategy(), params, spot, opt, contracts,
        "NIFTY", "risk_adjusted", LOT_SIZE, 0, 0.0)
    assert metrics["trade_count"] == 0
    assert metrics["ce_count"] == 0 and metrics["pe_count"] == 0
    for objective in OBJECTIVES:
        assert _score(metrics, objective) == DISQUALIFY


def test_min_trades_guard_still_applies_to_real_trades():
    """1 real trade with min_trades=10 must still disqualify — the fix routes
    around the STUB, not around the statistical guard rails."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    metrics, _ = _evaluate_premium_trigger(
        _FakeStrategy(), dict(_TRIGGER_PARAMS), spot, opt, contracts,
        "NIFTY", "risk_adjusted", LOT_SIZE, 10, 0.0)
    assert metrics["trade_count"] == 1
    assert _score(metrics, "risk_adjusted", min_trades=10) == DISQUALIFY


# =========================================================================
# (c) Direction counts reconcile.
# =========================================================================
def test_ce_count_plus_pe_count_equals_trade_count():
    spot, opt, contracts = _simple_ce_wins_scenario()
    metrics, _ = _evaluate_premium_trigger(
        _FakeStrategy(), dict(_TRIGGER_PARAMS), spot, opt, contracts,
        "NIFTY", "risk_adjusted", LOT_SIZE, 0, 0.0)
    assert metrics["ce_count"] + metrics["pe_count"] == metrics["trade_count"]
    assert metrics["ce_count"] == 1  # the CE side triggered in this fixture
    assert metrics["pe_count"] == 0


# =========================================================================
# (d) Invalid config / missing data -> clean degenerate result, no crash.
# =========================================================================
def test_invalid_config_returns_degenerate_metrics_not_crash():
    """No entry trigger at all (neither momentum_pct nor momentum_pts) ->
    dispatch_full_backtest returns None -> a zero-trade metrics dict that the
    existing _DISQUALIFY path handles exactly like a real no-trade result."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    metrics, merged = _evaluate_premium_trigger(
        _FakeStrategy(), {"stop_pct": 20.0}, spot, opt, contracts,
        "NIFTY", "risk_adjusted", LOT_SIZE, 0, 0.0)
    assert metrics["trade_count"] == 0
    assert merged == {"stop_pct": 20.0}
    assert _score(metrics, "risk_adjusted") == DISQUALIFY


def test_missing_preloaded_window_returns_degenerate_metrics_not_crash():
    """run_optimization preloads the (spot_df, option_candles, contracts) window
    once; when _load_window returned None the job must still complete — every
    trial scores as an honest zero-trade result rather than crashing."""
    metrics, _ = _evaluate_premium_trigger(
        _FakeStrategy(), dict(_TRIGGER_PARAMS), None, None, [],
        "NIFTY", "risk_adjusted", LOT_SIZE, 0, 0.0)
    assert metrics["trade_count"] == 0
    assert _score(metrics, "risk_adjusted") == DISQUALIFY
