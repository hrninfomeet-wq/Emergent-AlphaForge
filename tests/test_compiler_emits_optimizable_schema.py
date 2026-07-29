"""An AI-authored premium plugin must be as optimizer-safe as the hand-written one.

User-reported (2026-07-29): optimizing the authored `algotest_option_buy_nifty`
returned `lots: 100` and an account curve 50x the configured size.

Root cause chain:
  * `compiler.py` emitted every premium_trigger key as a bare
    `{"type": ..., "default": ...}` — no `min`/`max`, no `"fixed"`.
  * `optimizer._build_param_space` admits any int/float/bool, and `_suggest`
    invented `[0, 100]` for an unbounded int.
  * `PremiumTriggerConfig.lots` is `ge=1, le=100`, so the invented ceiling
    coincided exactly with the config max — 100 validated and shipped.

The shipped `premium_momentum` plugin encodes the right judgement BY HAND: it
never declares `lots`, puts real bounds on `momentum_pct`/`stop_pct`, and pins
risk knobs with `"fixed"`. Authored plugins were getting none of it.

Bounds are DERIVED from that shipped plugin rather than hardcoded here, so the
two cannot drift apart — the same anti-drift rule the authoring prompt, the
wizard panel and the capability verdict already follow.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.ai.compiler import compile_spec, premium_param_schema_entry  # noqa: E402
from app.ai.spec_schema import StrategySpec  # noqa: E402
from app.optimizer import NON_ALPHA_PARAM_NAMES, _build_param_space  # noqa: E402
from app.strategies.base import get_registry  # noqa: E402

#: The config the user actually authored.
AUTHORED_CONFIG = {
    "reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
    "leg_mode": "both", "momentum_pct": 15, "stop_pct": 20, "target_pct": 50,
    "trail_x_pct": 5, "trail_y_pct": 5, "lots": 2,
    "entry_cutoff": "15:09", "exit_time": "15:13",
    "lazy_enabled": True, "lazy_moneyness": "itm1", "lazy_momentum_pct": 10,
    "lazy_stop_pct": 10, "lazy_target_pct": 50,
    "lazy_trail_x_pct": 5, "lazy_trail_y_pct": 5,
}


def _shipped_schema():
    reg = get_registry()
    reg.auto_discover()
    return reg.get("premium_momentum").parameter_schema or {}


def _spec():
    return StrategySpec(
        id="authored_premium_probe", name="Probe", description="probe",
        instruments=["NIFTY"], timeframe="1m",
        premium_trigger=dict(AUTHORED_CONFIG),
    )


def _compiled_space():
    """Compile the spec, exec the plugin source, read its real schema through
    the real optimizer space builder — end to end, no stubs."""
    src = compile_spec(_spec())
    ns = {}
    exec(compile(src, "<authored>", "exec"), ns)  # noqa: S102 - compiling our own output
    cls = next(v for v in ns.values()
               if isinstance(v, type) and getattr(v, "id", None) == "authored_premium_probe")
    return cls.parameter_schema, _build_param_space(cls.parameter_schema, None)


def _searchable(space):
    return {k for k, v in space.items() if "fixed" not in v}


# --------------------------------------------------------------------------- #
# sizing must never become a search dimension
# --------------------------------------------------------------------------- #

def test_lots_is_emitted_pinned():
    schema, _ = _compiled_space()
    assert "fixed" in schema["lots"], (
        "an authored plugin must pin position size the way premium_momentum does")


def test_lots_is_not_searchable_end_to_end():
    _, space = _compiled_space()
    assert "lots" not in _searchable(space)


def test_the_pinned_value_is_the_authored_one():
    """Pinned, not reset: the strategy must still trade the size the user wrote."""
    schema, _ = _compiled_space()
    assert schema["lots"]["fixed"] == 2
    assert schema["lots"]["default"] == 2


# --------------------------------------------------------------------------- #
# real alpha knobs stay tunable, with bounds derived from the shipped plugin
# --------------------------------------------------------------------------- #

def test_alpha_knobs_carry_real_bounds_and_stay_searchable():
    schema, space = _compiled_space()
    shipped = _shipped_schema()
    searched = _searchable(space)
    for name in ("momentum_pct", "stop_pct"):
        assert name in searched, f"{name} is a genuine alpha knob and must be tunable"
        assert schema[name]["min"] == shipped[name]["min"], f"{name} min must track the shipped plugin"
        assert schema[name]["max"] == shipped[name]["max"], f"{name} max must track the shipped plugin"


def test_bounds_are_derived_not_hardcoded():
    """Guard the guard: the helper must read the shipped plugin, so a future
    change to premium_momentum's curated ranges propagates automatically."""
    shipped = _shipped_schema()
    entry = premium_param_schema_entry("momentum_pct", 15)
    assert entry.get("min") == shipped["momentum_pct"]["min"]
    assert entry.get("max") == shipped["momentum_pct"]["max"]


def test_a_knob_with_no_curated_bounds_is_pinned_not_invented():
    """`trail_y_pct` has no shipped guidance. Inventing [0,100] gave the user
    trail_y_pct=78 and lazy_trail_y_pct=97 — noise presented as a result."""
    schema, _ = _compiled_space()
    for name in ("trail_x_pct", "trail_y_pct", "lazy_trail_x_pct", "lazy_trail_y_pct"):
        assert "fixed" in schema[name], f"{name} was searched over an invented range"


def test_the_search_space_is_no_longer_mostly_noise():
    """Before: 22 dimensions, 10 of them inert indicator periods, 1 leverage.
    After: only knobs that are both real and bounded."""
    _, space = _compiled_space()
    searched = _searchable(space)
    assert searched, "the strategy must remain optimizable at all"
    assert not (searched & NON_ALPHA_PARAM_NAMES)
    for name in searched:
        info = space[name]
        assert info["type"] == "bool" or ("min" in info and "max" in info), (
            f"{name} is searched without declared bounds")


def test_string_params_are_untouched():
    schema, _ = _compiled_space()
    for name in ("reference_time", "moneyness", "side", "leg_mode", "entry_cutoff", "exit_time"):
        assert schema[name]["type"] == "str"
        assert schema[name]["default"] == AUTHORED_CONFIG[name]


def test_every_authored_key_still_survives_compilation():
    """The pinning must not silently drop a field — that is the bug class this
    whole audit is about."""
    schema, _ = _compiled_space()
    for key in AUTHORED_CONFIG:
        assert key in schema, f"{key} vanished from the emitted schema"
