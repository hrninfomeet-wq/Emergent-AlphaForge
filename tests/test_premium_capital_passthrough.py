"""A premium-native run must use the capital the user configured.

Found during the 2026-07-29 audit. `runtime._run_paired_option_backtest` calls
`dispatch_full_backtest(...)` with no `capital=` argument, so it silently fell
back to the signature default of 200_000. The Backtest Lab form's
`option_backtest.sizing_config.capital` never reached the sim.

Everything rupee-relative is then computed off the wrong base: "Highest/Lowest
Acct Value", the Account (rupee) card's Capital and Ending Equity, Return %, and
Max DD % — the exact cards the user was reading when they reported an implausible
account value.

`build_rupee_equity_curve(trades, capital=...)` has always accepted it; the value
was simply never threaded through.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

from app.premium_trigger_dispatch import dispatch_full_backtest  # noqa: E402


def _scenario():
    from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario
    return _simple_ce_wins_scenario()


def _params():
    return {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
            "momentum_pct": 15.0, "lots": 1}


def _run(**kw):
    spot, opt, contracts = _scenario()
    return dispatch_full_backtest(
        strategy_id="probe", merged_params=_params(),
        spot_df=spot.copy(), option_candles=opt.copy(), contracts=list(contracts),
        instrument="NIFTY", **kw)


def test_the_configured_capital_reaches_the_equity_curve():
    out = _run(capital=750_000.0)
    assert out is not None
    assert float(out["portfolio"]["starting_capital"]) == 750_000.0


def test_the_default_is_unchanged_when_no_capital_is_given():
    """Byte-identical for every existing caller."""
    out = _run()
    assert float(out["portfolio"]["starting_capital"]) == 200_000.0


def test_runtime_actually_forwards_the_form_capital():
    """THE bug: the plumbing existed, the call site just never used it.

    A source contract rather than a live run because the call site is inside an
    async DB-bound function; asserting the ARGUMENT is passed is what the defect
    was about.
    """
    from app import runtime

    src = inspect.getsource(runtime._run_paired_option_backtest)
    i = src.index("dispatch_full_backtest(")
    call = src[i:i + 400]
    assert "capital=" in call, (
        "the premium dispatch must forward the form's capital; without it every "
        "account-value and drawdown-% figure is computed off a hardcoded 200000")
