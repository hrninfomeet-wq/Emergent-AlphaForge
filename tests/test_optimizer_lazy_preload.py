"""The optimizer's premium preload must cover what its trials can enable.

Caught 2026-07-29 while setting up a train/holdout run, BEFORE it was launched.

`lazy_enabled` is a bool in the authored plugin's schema, so the optimizer varies
it. But `optimizer.py`'s premium preload called
`_load_window(..., moneynesses=[default], sides=["CE","PE"])` with no
`lazy_enabled=`, so it never widened to the full moneyness band.

The lazy reversal leg locks a FRESH opposite-side strike mid-session from a moved
spot — an unpredictable strike at preload time. With the narrow preload every
lazy activation degrades to `lazy_excluded_no_data`, i.e. a silent zero. Trials
with `lazy_enabled=True` would therefore score identically to `False`, and the
optimizer would conclude "the lazy leg does not help" — a false negative baked in
by the data loader rather than measured from the strategy.

This is the same failure the Backtest Lab had (`runtime.py` also omitted the
argument); the optimizer copy was missed because its preload is a separate call
site with its own `sides=["CE","PE"]` already hardcoded, which made it look
complete.

Widening is a strict superset of candles: it can only add data, never change a
non-lazy trial's result.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.optimizer import premium_preload_needs_lazy  # noqa: E402
from app.strategies.base import get_registry  # noqa: E402


class _Fake:
    def __init__(self, schema, defaults=None):
        self.id = "fake"
        self.parameter_schema = schema
        self._d = defaults or {}

    def merged_params(self, p=None):
        out = {k: v.get("default") for k, v in (self.parameter_schema or {}).items()}
        out.update(self._d)
        out.update(p or {})
        return out

    def default_params(self):
        return self.merged_params({})


def test_a_strategy_that_can_enable_lazy_widens_the_preload():
    """THE case: default False, but the optimizer may flip it to True."""
    s = _Fake({"lazy_enabled": {"type": "bool", "default": False},
               "lazy_momentum_pct": {"type": "int", "min": 5, "max": 50, "default": 10}})
    assert premium_preload_needs_lazy(s) is True


def test_a_strategy_with_lazy_on_by_default_widens_it():
    s = _Fake({"lazy_enabled": {"type": "bool", "default": True}})
    assert premium_preload_needs_lazy(s) is True


def test_a_strategy_with_lazy_pinned_off_does_not():
    """`fixed: False` means no trial can ever turn it on — don't pay the cost."""
    s = _Fake({"lazy_enabled": {"type": "bool", "fixed": False, "default": False}})
    assert premium_preload_needs_lazy(s) is False


def test_a_strategy_with_no_lazy_capability_does_not():
    s = _Fake({"momentum_pct": {"type": "int", "min": 5, "max": 50, "default": 15}})
    assert premium_preload_needs_lazy(s) is False


def test_the_shipped_premium_momentum_pins_lazy_off():
    """It hand-writes `fixed: False` to keep multi-leg out of the search space."""
    reg = get_registry()
    reg.auto_discover()
    assert premium_preload_needs_lazy(reg.get("premium_momentum")) is False


def test_the_authored_plugin_needs_the_wide_preload():
    """The user's strategy declares lazy_enabled as a live bool dimension."""
    reg = get_registry()
    reg.auto_discover()
    s = reg.get("algotest_option_buy_nifty")
    if s is None:
        return
    assert premium_preload_needs_lazy(s) is True


def test_garbage_is_safe():
    assert premium_preload_needs_lazy(None) is False
    assert premium_preload_needs_lazy(object()) is False


def test_the_optimizer_preload_actually_passes_it():
    """Source contract: the argument must reach `_load_window`, or the widening
    helper above is dead code and the false negative returns."""
    from app import optimizer

    src = inspect.getsource(optimizer.run_optimization)
    i = src.index("_load_window(")
    call = src[i:i + 400]
    assert "lazy_enabled=" in call, (
        "the optimizer preload must widen when a trial can enable the lazy leg")
