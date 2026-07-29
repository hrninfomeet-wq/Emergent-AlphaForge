"""Every parameter the premium sim READS must be declarable by an author.

User-reported (2026-07-29), third round: after widening the spec surface to the
plugin's declared params, one `couldnt_map` remained —

    "Separate trailing SL parameters specific to Lazy Legs
     (schema shares primary trail parameters)."

Correct again, and it exposed a layer deeper than the last fix. The ENGINE reads
lazy-specific trail params (`lazy_trail_x/_y`, `lazy_trail_x_pct/_y_pct` are
resolved via `_resolve_trail` and passed to the lazy walk), plus points-variants
and percent-trails — but none of them appear in the plugin's `parameter_schema`
or in `PremiumTriggerConfig`, so the authoring surface could not express them:

    lazy_momentum_pts, lazy_stop_pts, lazy_target_pts,
    lazy_trail_x, lazy_trail_y, lazy_trail_x_pct, lazy_trail_y_pct,
    trail_x_pct, trail_y_pct

The lesson from the previous two rounds: deriving the authoring surface from a
declaration only helps if the declaration is complete. So the sim now declares
what it accepts, next to where it reads it, and the test below scrapes every
`params.get(...)` key out of the module and asserts the declaration covers them.
A future param added to the engine and forgotten in the declaration fails here
rather than surfacing months later as a user's "couldn't map".
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.premium_momentum_backtest import ENGINE_PARAM_KEYS  # noqa: E402
from app.premium_trigger_dispatch import premium_trigger_allowed_keys  # noqa: E402

_SRC = os.path.join(os.path.dirname(__file__), "..", "backend", "app",
                    "premium_momentum_backtest.py")


def _keys_the_engine_reads() -> set:
    with open(_SRC, encoding="utf-8") as fh:
        src = fh.read()
    return set(re.findall(r"""params\.get\(["']([a-z_]+)["']""", src))


def test_the_declaration_covers_every_param_the_engine_reads():
    """THE anti-drift guard. A param added to the sim but not declared is
    invisible to authoring — exactly how the lazy trail knobs went missing."""
    missing = sorted(_keys_the_engine_reads() - set(ENGINE_PARAM_KEYS))
    assert not missing, (
        f"the engine reads {missing} but ENGINE_PARAM_KEYS does not declare them, "
        "so an author cannot express them")


def test_the_declaration_does_not_invent_params():
    """The reverse: declaring something the sim ignores would advertise a knob
    that silently does nothing — the dead-knob class."""
    phantom = sorted(set(ENGINE_PARAM_KEYS) - _keys_the_engine_reads())
    assert not phantom, (
        f"ENGINE_PARAM_KEYS declares {phantom}, which the sim never reads")


def test_the_lazy_trail_knobs_are_now_expressible():
    """The user's actual report."""
    allowed = premium_trigger_allowed_keys()
    for name in ("lazy_trail_x", "lazy_trail_y", "lazy_trail_x_pct", "lazy_trail_y_pct"):
        assert name in allowed, f"{name} still not expressible in a spec"


def test_the_points_and_percent_variants_are_expressible():
    allowed = premium_trigger_allowed_keys()
    for name in ("lazy_momentum_pts", "lazy_stop_pts", "lazy_target_pts",
                 "trail_x_pct", "trail_y_pct"):
        assert name in allowed, f"{name} still not expressible in a spec"


def test_a_spec_with_lazy_trail_params_validates():
    from app.ai.compiler import validate_spec
    from app.ai.spec_schema import StrategySpec

    spec = StrategySpec(
        id="lazy_trail_thing", name="Lazy Trail",
        premium_trigger={
            "reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
            "momentum_pct": 15.0, "stop_pct": 20.0,
            "lazy_enabled": True, "lazy_momentum_pct": 20.0, "lazy_stop_pct": 25.0,
            "lazy_trail_x": 5.0, "lazy_trail_y": 5.0,
        })
    assert validate_spec(spec) == []


def test_an_invented_field_is_STILL_refused():
    """Widening must never turn the block into a free-form dict."""
    from app.ai.compiler import validate_spec
    from app.ai.spec_schema import StrategySpec

    spec = StrategySpec(
        id="bad_thing", name="Bad",
        premium_trigger={"reference_time": "09:31", "moneyness": "itm1",
                         "side": "ce", "momentum_pct": 15.0,
                         "lazy_trail_z": 5.0})
    errors = validate_spec(spec)
    assert errors and any("premium_trigger" in e for e in errors)


def test_the_prompt_teaches_the_lazy_trail_knobs():
    """If the model is not told they exist it will keep reporting them as
    unmappable — which is precisely the bug being fixed."""
    from app.ai.grounding import build_grounding_catalog
    from app.ai.strategy_author import _system_prompt
    prompt = _system_prompt(build_grounding_catalog())
    assert "lazy_trail_x" in prompt
