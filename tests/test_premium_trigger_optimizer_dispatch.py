"""Phase 4 engine dispatch, session 3 — wiring into the Optimizer / Backtest Lab.

Emergent's session shipped PremiumTriggerConfig + dispatch_backtest (a config-driven
backtest, byte-identical to the bespoke /premium-momentum route) but explicitly deferred
"Optimizer wiring" and "deployment_evaluator dispatch" — see premium_trigger_dispatch.py's
own module docstring. This closes the Optimizer half: running the shipped `premium_momentum`
plugin through the general Optimizer/Backtest Lab produced "Option re-rank produced no
paired results" because those pages call `run_backtest` -> `strategy.evaluate()`, which is
a deliberate stub for this strategy (the real logic lives only in deployment_evaluator.py's
dedicated branch, never touched here).

`dispatch_full_backtest` is the fix: given `strategy_id == "premium_momentum"`, it runs the
option-native sim directly and reshapes its trades into option_backtest.py's canonical
PAIRED-trade contract, then reuses option_backtest.py's own pure aggregators
(_compute_metrics, build_option_equity_curve, build_context_breakdown) and
portfolio.py's build_rupee_equity_curve to build a result indistinguishable in shape from
what simulate_paired_option_trades produces for any other strategy — so every existing
consumer (optimizer.py's _option_rerank/_survival_eval_oos, runtime.py's
_run_paired_option_backtest, the Optimizer/BacktestLab frontend pages) can read it unchanged.

For any OTHER strategy id, dispatch_full_backtest must return None immediately with zero
side effects — that's the entire regression-safety mechanism; every other strategy's
existing spot-backtest-then-pair code path is completely untouched.

Host-safe / pure: no motor, no LLM, no network. Fixtures mirror
tests/test_premium_trigger_dispatch_parity.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.premium_trigger_dispatch import dispatch_full_backtest


# --------------------------------------------------------------------------
# Fixtures (same shape as test_premium_trigger_dispatch_parity.py).
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


class _FakeStrategy:
    """Minimal stand-in for a registry strategy object — only `.id` is read
    by dispatch_full_backtest's guard."""
    def __init__(self, strategy_id):
        self.id = strategy_id


# =========================================================================
# The regression-safety guard — must return None for any other strategy,
# with ZERO side effects (no DB access, no exception).
# =========================================================================
def test_returns_none_for_non_premium_momentum_strategy():
    result = dispatch_full_backtest(
        strategy_id="confluence_scalper",
        merged_params={"rsi_period": 14},
        spot_df=pd.DataFrame(), option_candles=pd.DataFrame(), contracts=[],
        instrument="NIFTY",
    )
    assert result is None


def test_returns_none_for_strategy_object_with_different_id():
    strategy = _FakeStrategy("opening_range_breakout")
    result = dispatch_full_backtest(
        strategy_id=strategy.id,
        merged_params={}, spot_df=pd.DataFrame(), option_candles=pd.DataFrame(),
        contracts=[], instrument="NIFTY",
    )
    assert result is None


# =========================================================================
# The actual dispatch — premium_momentum must produce a real, non-empty,
# correctly-shaped paired-option-trade envelope.
# =========================================================================
def test_dispatch_produces_paired_trades_not_empty():
    """This is the literal bug repro: before this fix, running premium_momentum
    through the general Optimizer/Backtest Lab produced zero paired trades
    ("Option re-rank produced no paired results"). After the fix, a real
    momentum-triggered session must produce >=1 PAIRED trade."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    result = dispatch_full_backtest(
        strategy_id="premium_momentum",
        merged_params={"reference_time": "09:31", "moneyness": "itm1",
                       "side": "first_to_trigger", "momentum_pct": 15.0,
                       "stop_pct": 20.0, "lots": 2},
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
    )
    assert result is not None
    assert result["coverage"]["paired_trade_count"] >= 1
    paired = [t for t in result["trades"] if t["status"] == "PAIRED"]
    assert len(paired) >= 1
    assert paired[0]["direction"] == "CE"
    assert paired[0]["option_pnl_value"] != 0.0


def test_dispatch_envelope_has_every_key_optimizer_and_backtestlab_read():
    """optimizer.py's _option_rerank/_survival_eval_oos and BacktestLab.jsx read
    these top-level + per-trade keys directly (see the Phase 4-5 contract trace).
    A missing key here means a KeyError or a silently-blank UI card, not a
    clean failure."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    result = dispatch_full_backtest(
        strategy_id="premium_momentum",
        merged_params={"momentum_pct": 15.0, "stop_pct": 20.0, "lots": 1},
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
    )
    for top_key in ("enabled", "underlying", "moneyness", "coverage", "metrics",
                    "equity_curve", "portfolio", "context_breakdown", "trades"):
        assert top_key in result, f"missing top-level key {top_key!r}"
    for cov_key in ("spot_trade_count", "paired_trade_count", "missing_contract",
                    "missing_entry_candle", "missing_exit_candle", "skipped_by_cap"):
        assert cov_key in result["coverage"]
    for metric_key in ("paired_trade_count", "win_rate", "total_option_pnl_value",
                       "total_option_pnl_pts"):
        assert metric_key in result["metrics"]
    port = result["portfolio"]
    for port_key in ("starting_capital", "ending_equity", "net_pnl_value",
                     "total_return_pct", "max_drawdown_pct", "curve"):
        assert port_key in port
    trade = next(t for t in result["trades"] if t["status"] == "PAIRED")
    for trade_key in ("status", "direction", "side", "option_pnl_value", "option_pnl_pts",
                      "entry_option_price", "exit_option_price", "option_exit_reason",
                      "total_charges", "lots", "lot_size", "quantity", "trading_symbol",
                      "instrument_key", "option_entry_ts", "option_exit_ts",
                      "risk_exceeded", "sizing_mode"):
        assert trade_key in trade, f"missing per-trade key {trade_key!r}"


def test_dispatch_maps_exit_reason_to_option_backtest_vocabulary():
    """premium_momentum_backtest tags exits STOP/TARGET/EOD; option_backtest.py's
    _compute_metrics buckets by OPTION_STOP/OPTION_TARGET/OPTION_SIGNAL_EXIT. An
    unmapped raw string would silently zero out every exit-reason counter."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    result = dispatch_full_backtest(
        strategy_id="premium_momentum",
        merged_params={"momentum_pct": 15.0, "stop_pct": 20.0},
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
    )
    trade = next(t for t in result["trades"] if t["status"] == "PAIRED")
    assert trade["option_exit_reason"] in (
        "OPTION_STOP", "OPTION_TARGET", "OPTION_SIGNAL_EXIT",
    )
    assert result["metrics"]["option_stop_exits"] + result["metrics"]["option_target_exits"] \
        + result["metrics"]["option_signal_exits"] == result["metrics"]["paired_trade_count"]


def test_dispatch_filters_merged_params_to_known_config_fields():
    """merged_params (from strategy.merged_params()) may carry bookkeeping fields
    outside PremiumTriggerConfig's strict (extra='forbid') schema — e.g. a
    strategy-generic field the registry injects. The adapter must filter to
    PremiumTriggerConfig's known fields rather than raising or crashing."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    result = dispatch_full_backtest(
        strategy_id="premium_momentum",
        merged_params={
            "momentum_pct": 15.0, "stop_pct": 20.0,
            "some_unrelated_registry_bookkeeping_field": "ignore_me",
            "id": "premium_momentum", "name": "Premium Momentum (AlgoTest-style)",
        },
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
    )
    assert result is not None
    assert result["coverage"]["paired_trade_count"] >= 1


def test_dispatch_no_trigger_produces_well_formed_empty_result():
    """A session where momentum never crosses must produce a clean, well-formed
    empty envelope (zero trades) — not a crash, not a malformed dict."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    result = dispatch_full_backtest(
        strategy_id="premium_momentum",
        # 90% is a VALID config value (PremiumTriggerConfig caps momentum_pct at
        # 100) but unreachable by this fixture: CE only rises +50% (100->150),
        # PE only +3% (100->103) -> neither side ever crosses -> no trigger.
        merged_params={"momentum_pct": 90.0, "stop_pct": 20.0},
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
    )
    assert result is not None
    assert result["coverage"]["paired_trade_count"] == 0
    assert result["trades"] == []
    assert result["metrics"]["paired_trade_count"] == 0
    assert result["portfolio"]["net_pnl_value"] == 0.0


def test_dispatch_trade_timestamps_and_strike_are_native_python_types():
    """Regression pin for a real bug caught in live verification: pandas-derived
    ts/strike fields arrive as numpy.int64/float64, which pandas/pytest tolerate
    silently but Mongo's BSON encoder rejects outright (bson.errors.InvalidDocument
    on backtest_runs.insert_one). isinstance(np.int64(5), int) is False on this
    platform, so this check genuinely catches a numpy leak, not a false pass."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    result = dispatch_full_backtest(
        strategy_id="premium_momentum",
        merged_params={"momentum_pct": 15.0, "stop_pct": 20.0},
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
    )
    trade = next(t for t in result["trades"] if t["status"] == "PAIRED")
    for ts_key in ("signal_entry_ts", "signal_exit_ts", "option_entry_ts", "option_exit_ts"):
        v = trade[ts_key]
        assert v is None or isinstance(v, int), f"{ts_key} is {type(v)!r}, not a native int"
    assert trade["strike"] is None or isinstance(trade["strike"], float)


def test_dispatch_invalid_config_returns_none_not_raise():
    """A merged_params dict with no entry trigger at all (neither momentum_pct
    nor momentum_pts) is an invalid PremiumTriggerConfig. The adapter must fail
    soft (None) so the caller falls through to its normal path/error handling,
    not raise an unhandled ValidationError deep inside the optimizer loop."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    result = dispatch_full_backtest(
        strategy_id="premium_momentum",
        merged_params={"stop_pct": 20.0},  # no momentum_pct/pts
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
    )
    assert result is None
