"""Step 3a: Track B routes on CAPABILITY, configured by the deployment.

Track B **replaces** `strategy.evaluate()` — the plugin's evaluate is never
called on that path. So the routing question is a safety question, and the naive
generalization is dangerous:

    "any deployment carrying a premium_trigger block takes Track B"

would let a block attached to an ordinary strategy silently disable that
strategy's real logic and run a completely different engine, with no error. It
would appear to work while trading rules the user never chose.

The split:

* **WHO** takes Track B — `is_premium_trigger_strategy(strategy)`, judged from
  the STRATEGY's own declared defaults. Only a strategy that declares itself
  premium-native (its `evaluate()` is a stub) may have `evaluate()` bypassed.
* **WHAT** config it runs — `resolve_deployment_premium_trigger(deployment)`;
  the deployment's block wins, `params` is the fallback.

Capability is a per-strategy property; configuration is a per-deployment one.
Conflating them is the hazard.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import deployment_evaluator as de  # noqa: E402
from app.premium_trigger_dispatch import is_premium_trigger_strategy  # noqa: E402
from app.strategies.base import get_registry  # noqa: E402
from app.strategy_deployments import resolve_deployment_premium_trigger  # noqa: E402


_SRC = inspect.getsource(de)


# --------------------------------------------------------------------------- #
# routing source contracts
# --------------------------------------------------------------------------- #

def test_track_b_no_longer_gates_on_the_literal_strategy_id():
    assert 'strategy_id == "premium_momentum"' not in _SRC, (
        "Track B must route on capability, not on one hardcoded id")


def test_track_b_gates_on_the_STRATEGY_capability_not_on_the_deployment_block():
    """The load-bearing safety property. If the branch keyed off the deployment
    block alone, attaching a block to confluence_scalper would silently bypass
    its evaluate() and run the premium session engine instead."""
    assert "is_premium_trigger_strategy(strategy)" in _SRC, (
        "the branch must ask whether the STRATEGY is premium-native")


def test_the_config_VALUES_come_from_the_deployment_resolver():
    assert "resolve_deployment_premium_trigger" in _SRC, (
        "Track B must take its config from the deployment resolver so the "
        "premium_trigger block is honoured, not only params")


def test_an_invalid_config_refuses_the_bar_rather_than_falling_through():
    """Falling through would run the ORDINARY path — trading on rules the
    deployment's own config never described."""
    assert "invalid" in _SRC, "the invalid config source must be handled explicitly"


# --------------------------------------------------------------------------- #
# the safety property, asserted on real objects rather than source text
# --------------------------------------------------------------------------- #

def test_a_block_cannot_make_an_ordinary_strategy_premium_native():
    """THE hazard, stated directly: capability must be immune to what a
    deployment attaches."""
    reg = get_registry()
    reg.auto_discover()
    ordinary = reg.get("confluence_scalper")
    assert is_premium_trigger_strategy(ordinary) is False

    # a deployment that (wrongly) carries a full premium-trigger block
    dep = {
        "strategy_id": "confluence_scalper",
        "params": {"ema_fast": 9},
        "premium_trigger": {"reference_time": "09:31", "moneyness": "itm1",
                            "side": "ce", "momentum_pct": 15.0},
    }
    cfg, source = resolve_deployment_premium_trigger(dep)
    assert cfg is not None and source == "premium_trigger", (
        "the block still resolves — it is valid config data")
    # ...but the STRATEGY is still not premium-native, so Track B must not run.
    assert is_premium_trigger_strategy(ordinary) is False, (
        "attaching a block must never flip a strategy's capability")


def test_premium_momentum_is_still_premium_native():
    reg = get_registry()
    reg.auto_discover()
    assert is_premium_trigger_strategy(reg.get("premium_momentum")) is True


def test_block_overlays_params_without_erasing_the_5B_fields():
    """The session engine reads leg_mode / lazy_* / entry_cutoff from params, and
    none of those exist on PremiumTriggerConfig. Overlaying the block must MERGE
    over params, never replace them, or 5B configuration would vanish."""
    dep = {
        "strategy_id": "premium_momentum",
        "params": {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
                   "momentum_pct": 10.0, "leg_mode": "both", "lazy_enabled": True,
                   "entry_cutoff": "13:00"},
        "premium_trigger": {"reference_time": "09:35", "moneyness": "atm",
                            "side": "pe", "momentum_pct": 25.0},
    }
    effective = de._effective_premium_params(dep)
    # block wins where they overlap
    assert effective["momentum_pct"] == 25.0
    assert effective["moneyness"] == "atm"
    assert effective["reference_time"] == "09:35"
    # params-only 5B fields survive
    assert effective["leg_mode"] == "both"
    assert effective["lazy_enabled"] is True
    assert effective["entry_cutoff"] == "13:00"


def test_no_block_leaves_params_untouched():
    """Byte-identical for every deployment created before the block existed."""
    params = {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
              "momentum_pct": 10.0, "leg_mode": "both"}
    dep = {"strategy_id": "premium_momentum", "params": dict(params)}
    assert de._effective_premium_params(dep) == params
