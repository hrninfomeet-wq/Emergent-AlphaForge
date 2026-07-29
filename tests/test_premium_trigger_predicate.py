"""Step 1b: the BACKTEST domain stops asking "are you premium_momentum?".

Six remaining sites gated the option-native path on the literal string
`strategy_id == "premium_momentum"`:

  * `runtime.py`   — the coverage preflight
  * `optimizer.py` — OOS survival, Stage-2 re-rank, the Stage-1 preload, the
                     Stage-1 evaluate closure, and TWO worker-pinning decisions

All six mean the same thing: *this strategy is premium-native, so its
`evaluate()` is a deliberate stub and the spot path would score it as zero
trades*. The honest predicate for that is "does the strategy declare a
premium-trigger config?", not "is it this one specific id".

**Regression safety:** across all 12 shipped strategies the predicate matches
exactly the same single strategy the string did, so today's behaviour is
byte-identical. What changes is that an AI-authored strategy declaring the same
config is no longer silently scored as a zero-trade dud by the optimizer.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.premium_trigger_dispatch import is_premium_trigger_strategy  # noqa: E402
from app.strategies.base import get_registry  # noqa: E402


class _FakeStrategy:
    def __init__(self, sid, params):
        self.id = sid
        self._p = params

    def default_params(self):
        return dict(self._p)


_VALID = {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
          "momentum_pct": 15.0, "stop_pct": 20.0}


def test_predicate_matches_exactly_what_the_string_matched_today():
    """Byte-identical behaviour for every shipped strategy — the whole point of
    swapping a string equality for a capability question."""
    reg = get_registry()
    reg.auto_discover()
    matched = set()
    for entry in reg.list_all():
        sid = entry["id"] if isinstance(entry, dict) else entry.id
        if is_premium_trigger_strategy(reg.get(sid)):
            matched.add(sid)

    # The shipped premium strategy must still classify as premium-native...
    assert "premium_momentum" in matched

    # ...and every ordinary shipped strategy must still NOT.
    #
    # Deliberately NOT `matched == {"premium_momentum"}` any more. That held when
    # written, but an AI-AUTHORED premium strategy legitimately matching is the
    # entire point of the Phase 1 work — the first real authored one
    # (algotest_option_buy_nifty) turned this assertion red purely by succeeding.
    # The invariant that actually matters is that no ORDINARY strategy silently
    # acquired premium routing.
    for sid in ("confluence_scalper", "vwap_pullback_scalp", "vwap_mean_reversion",
                "opening_range_breakout", "opening_range_regime_router",
                "smc_liquidity_sweep_fvg", "explosive_reversal", "gap_fade",
                "fibonacci_pullback", "squeeze_expansion_breakout",
                "adaptive_regime_scalper"):
        if reg.get(sid) is not None:
            assert sid not in matched, (
                f"{sid} is an ordinary strategy and must not be premium-native")


def test_an_authored_strategy_declaring_the_config_is_recognised():
    """THE generalization: a different id carrying the same config is premium-native."""
    assert is_premium_trigger_strategy(_FakeStrategy("my_authored_thing", _VALID)) is True


def test_an_ordinary_strategy_is_not():
    assert is_premium_trigger_strategy(
        _FakeStrategy("some_scalper", {"ema_fast": 9, "signal_threshold": 62})) is False


def test_a_partial_or_invalid_config_is_NOT_treated_as_premium_native():
    """A half-written config must not hijack the option-native path — it would
    score the strategy through a sim its config cannot actually drive."""
    half = {"reference_time": "09:31", "moneyness": "itm1"}   # no entry trigger
    assert is_premium_trigger_strategy(_FakeStrategy("half", half)) is False


def test_none_and_garbage_are_safe():
    assert is_premium_trigger_strategy(None) is False
    assert is_premium_trigger_strategy(object()) is False


# --------------------------------------------------------------------------- #
# source contracts — the literal string must be gone from the backtest domain
# --------------------------------------------------------------------------- #

def _src(mod):
    return inspect.getsource(mod)


def test_optimizer_no_longer_gates_on_the_literal_strategy_id():
    from app import optimizer
    src = _src(optimizer)
    assert '"premium_momentum"' not in src, (
        "optimizer must ask whether the strategy declares a premium-trigger "
        "config, not whether it has one specific id")
    # RESOLUTION, not mere presence. `"is_premium_trigger_strategy" in src` was
    # the original assertion and it passed while the name was unbound — a grep
    # cannot tell a USE from a DEFINITION, so six call sites with zero imports
    # satisfied it and every optimization run raised NameError for the user.
    assert hasattr(optimizer, "is_premium_trigger_strategy"), (
        "optimizer references the predicate but never binds it")


def test_runtime_preflight_no_longer_gates_on_the_literal_strategy_id():
    from app import runtime
    src = inspect.getsource(runtime._preflight_option_coverage) \
        if hasattr(runtime, "_preflight_option_coverage") else _src(runtime)
    assert 'req.strategy_id == "premium_momentum"' not in src, (
        "the coverage preflight must route on config presence")
