import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pandas as pd
import pytest

from app.strategies.base import get_registry
from app.backtest import run_backtest
from app.strategies.plugins.dte_opening_shock_breakout import DTEOpeningShockBreakout


IST = "Asia/Kolkata"


def _frame(times, closes, *, high_pad=1.0, low_pad=1.0):
    idx = pd.DatetimeIndex(pd.to_datetime(times)).tz_localize(IST)
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "ts": idx.as_unit("ms").asi8.astype("int64"),
        "open": close,
        "high": close + float(high_pad),
        "low": close - float(low_pad),
        "close": close,
        "session_date": idx.strftime("%Y-%m-%d"),
        "ist_time": idx.strftime("%H:%M"),
    })


def _prior_session(close=100.0):
    return _frame(
        ["2026-01-04 15:28", "2026-01-04 15:29"],
        [close, close],
    )


def _current_session(closes, *, date="2026-01-05"):
    times = pd.date_range(f"{date} 09:15", periods=len(closes), freq="1min")
    return _frame(times, closes)


def _evaluate_all(frame, strategy=None):
    strategy = strategy or DTEOpeningShockBreakout()
    params = strategy.default_params()
    context = strategy.session_precompute(frame, params)
    signals = []
    for i in range(len(frame)):
        ctx = {**context, "history_df": frame, "i": i,
               "instrument": "NIFTY", "session_date": frame.iloc[i]["session_date"]}
        prev = frame.iloc[i - 1] if i else None
        signals.append(strategy.evaluate(frame.iloc[i], prev, params, ctx))
    return signals


def _signal_indices(signals):
    return [i for i, signal in enumerate(signals) if signal.direction in ("CE", "PE")]


def test_plugin_metadata_is_research_only_and_keeps_execution_policy_out():
    strategy = DTEOpeningShockBreakout()

    assert strategy.id == "dte_opening_shock_breakout"
    assert strategy.supported_instruments == ["NIFTY", "SENSEX"]
    assert strategy.supported_modes == ["INTRADAY"]
    assert strategy.supported_timeframes == ["1m"]
    assert strategy.live_lookback_bars == 400
    assert "research" in strategy.description.lower()
    assert "unvalidated" in strategy.description.lower()
    assert set(strategy.parameter_schema) == {"spot_target_pts", "signal_threshold"}
    assert not ({"dte", "dte_filter", "moneyness", "weekday"} & set(strategy.parameter_schema))


def test_plugin_is_auto_discovered():
    registry = get_registry()
    registry.auto_discover()
    discovered = registry.get("dte_opening_shock_breakout")
    assert discovered is not None
    assert discovered.id == "dte_opening_shock_breakout"
    assert type(discovered).__name__ == "DTEOpeningShockBreakout"


def test_ce_shock_emits_once_on_first_post_range_cross_with_boundary_stop():
    opening = [100.0] * 29 + [101.0]  # 30th close > prior-session close
    current = _current_session(opening + [102.0, 103.0, 101.0, 104.0])
    frame = pd.concat([_prior_session(100.0), current], ignore_index=True)

    signals = _evaluate_all(frame)

    assert _signal_indices(signals) == [33]
    signal = signals[33]
    assert signal.direction == "CE"
    assert signal.score == 65
    assert signal.spot_stop_pts == pytest.approx(1.0)  # entry 103 - OR high 102
    assert signal.spot_target_pts == pytest.approx(40.0)
    assert any("prior close" in reason.lower() for reason in signal.reasons)
    assert any("opening range high" in reason.lower() for reason in signal.reasons)


def test_pe_shock_emits_once_on_first_post_range_cross_with_boundary_stop():
    opening = [100.0] * 29 + [99.0]  # 30th close < prior-session close
    current = _current_session(opening + [98.0, 97.0, 99.0, 96.0])
    frame = pd.concat([_prior_session(100.0), current], ignore_index=True)

    signals = _evaluate_all(frame)

    assert _signal_indices(signals) == [33]
    signal = signals[33]
    assert signal.direction == "PE"
    assert signal.spot_stop_pts == pytest.approx(1.0)  # OR low 98 - entry 97
    assert any("opening range low" in reason.lower() for reason in signal.reasons)


def test_full_history_and_rolling_prefix_choose_the_same_first_cross():
    opening = [100.0] * 29 + [101.0]
    current = _current_session(opening + [102.0, 103.0, 104.0, 105.0])
    full = pd.concat([_prior_session(100.0), current], ignore_index=True)
    pre_cross = full.iloc[:33].copy().reset_index(drop=True)
    prefix = full.iloc[:34].copy().reset_index(drop=True)  # includes first crossing bar

    full_signals = _evaluate_all(full)
    pre_cross_signals = _evaluate_all(pre_cross)
    prefix_signals = _evaluate_all(prefix)

    assert _signal_indices(full_signals) == [33]
    assert _signal_indices(pre_cross_signals) == []
    assert _signal_indices(prefix_signals) == [33]
    assert full_signals[33] == prefix_signals[33]


@pytest.mark.parametrize("case", ["missing_prior", "equal_shock", "missing_opening_bar"])
def test_unverifiable_or_directionless_opening_shock_fails_closed(case):
    opening = [100.0] * 29 + [101.0]
    current = _current_session(opening + [103.0])
    if case == "missing_prior":
        frame = current.reset_index(drop=True)
    elif case == "equal_shock":
        equal = _current_session([100.0] * 30 + [102.0])
        frame = pd.concat([_prior_session(100.0), equal], ignore_index=True)
    else:
        frame = pd.concat([_prior_session(100.0), current], ignore_index=True)
        frame = frame.loc[frame["ist_time"] != "09:20"].reset_index(drop=True)

    assert _signal_indices(_evaluate_all(frame)) == []


def test_cross_on_1449_bar_is_not_eligible_because_decision_would_be_at_cutoff():
    opening = [100.0] * 29 + [101.0]
    current = _current_session(opening + [102.0] * (334 - 30) + [103.0])
    assert current.iloc[-1]["ist_time"] == "14:49"
    frame = pd.concat([_prior_session(100.0), current], ignore_index=True)

    assert _signal_indices(_evaluate_all(frame)) == []


def test_cross_on_1448_bar_is_last_eligible_signal():
    opening = [100.0] * 29 + [101.0]
    current = _current_session(opening + [102.0] * (333 - 30) + [103.0])
    assert current.iloc[-1]["ist_time"] == "14:48"
    frame = pd.concat([_prior_session(100.0), current], ignore_index=True)

    assert _signal_indices(_evaluate_all(frame)) == [len(frame) - 1]


def test_signal_threshold_and_missing_required_values_fail_closed():
    opening = [100.0] * 29 + [101.0]
    current = _current_session(opening + [103.0])
    frame = pd.concat([_prior_session(100.0), current], ignore_index=True)
    strategy = DTEOpeningShockBreakout()

    params = strategy.default_params()
    params["signal_threshold"] = 66
    context = strategy.session_precompute(frame, params)
    i = len(frame) - 1
    signal = strategy.evaluate(frame.iloc[i], frame.iloc[i - 1], params,
                               {**context, "history_df": frame, "i": i})
    assert signal.direction == "NONE"

    broken = frame.copy()
    broken.loc[broken["ist_time"] == "09:30", "open"] = np.nan
    assert _signal_indices(_evaluate_all(broken, strategy)) == []


def test_standard_backtest_engine_consumes_the_plugin_without_special_dispatch():
    opening = [100.0] * 29 + [101.0]
    # Include the 15:00 bar. The plugin owns the 14:48 last-signal gate while
    # run_backtest's end controls the forced close, so research runs use 15:00.
    current = _current_session(opening + [102.0, 103.0] + [104.0] * (346 - 32))
    frame = pd.concat([_prior_session(100.0), current], ignore_index=True)
    strategy = DTEOpeningShockBreakout()

    result = run_backtest(
        frame, strategy, strategy.default_params(), instrument="NIFTY",
        costs_enabled=False, trade_window_start="09:15", trade_window_end="15:00",
    )

    assert len(result["trades"]) == 1
    assert result["trades"][0]["direction"] == "CE"
    assert result["trades"][0]["entry_price"] == pytest.approx(103.0)
    assert result["trades"][0]["ist_time"] == "09:46"
    assert result["trades"][0]["exit_reason"] == "TIME_EXIT"
