"""The Option Execution form's Lots must be what actually trades.

User-reported (2026-07-29): they set Lots = 5 in the Option Execution form and
every row of the Trades table showed 2 (Qty 130 = 2 x 65). Confirmed in their
saved run `... 23:12:05`:

    config.option_backtest.lots : 5     <- what they typed
    config.params.lots          : 2     <- the plugin's declared default
    premium_trigger_config.lots : 2     <- what actually ran

`runtime.py` resolved lots as `req.params.get("lots") or config.lots or 1`, so
the STRATEGY param won. That made the form's Lots field inert for any strategy
declaring `lots` — which every AI-authored premium strategy now does.

Fixed by making the form authoritative, for consistency: for every ORDINARY
strategy the Option Execution Lots field is already the one and only sizing
control, so a premium strategy quietly ignoring it is the surprising case. The
strategy's declared `lots` remains the fallback when the request carries no form
value at all (e.g. a raw API call).

Sizing is a pinned, non-optimizable param (see NON_ALPHA_PARAM_NAMES), so this
cannot reintroduce the lots=100 leverage search.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.runtime import resolve_premium_lots  # noqa: E402


def test_the_form_wins_over_the_strategy_default():
    """THE reported bug: form 5, strategy 2 -> 2 traded."""
    assert resolve_premium_lots(strategy_lots=2, form_lots=5) == 5


def test_the_strategy_default_is_used_when_the_form_has_none():
    """A raw API request with no option-execution block still sizes sanely."""
    assert resolve_premium_lots(strategy_lots=3, form_lots=None) == 3


def test_a_last_resort_of_one_lot():
    assert resolve_premium_lots(strategy_lots=None, form_lots=None) == 1


def test_zero_or_negative_form_lots_falls_back_rather_than_trading_nothing():
    """lots=0 would produce zero-quantity trades and a meaningless P&L."""
    assert resolve_premium_lots(strategy_lots=2, form_lots=0) == 2
    assert resolve_premium_lots(strategy_lots=2, form_lots=-4) == 2


def test_a_malformed_form_value_never_crashes_the_run():
    assert resolve_premium_lots(strategy_lots=2, form_lots="abc") == 2
    assert resolve_premium_lots(strategy_lots=None, form_lots="abc") == 1


def test_a_float_form_value_is_coerced_to_a_whole_lot():
    """Contracts are integral; 2.7 lots is not a tradable size."""
    assert resolve_premium_lots(strategy_lots=1, form_lots=2.7) == 2


def test_runtime_actually_uses_the_helper():
    """Source contract: the precedence must live in ONE place, so the banner's
    description of it cannot drift from the behaviour again."""
    import inspect
    from app import runtime

    src = inspect.getsource(runtime._run_paired_option_backtest)
    assert "resolve_premium_lots" in src
    assert 'req.params.get("lots") or config.lots' not in src, (
        "the old strategy-param-wins precedence is back")
