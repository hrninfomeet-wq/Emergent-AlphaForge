"""The lazy-leg / two-leg verdict must reflect what actually shipped.

User-reported (2026-07-29), and my earlier fix was incomplete: I corrected
`capability_summary()["future"]` but left `classify_rule`'s own PHASE-5 branch,
which is what actually renders in the feasibility report. It claimed:

    "Lazy-leg contingency ... is Phase 5 future work — the design is committed
     (docs/.../phase4-5-full-contingency-design.md) but not yet shipped.
     Not-yet live-feasible; today the single-leg first-to-trigger shape ships."

All three clauses are false. Phase 5B shipped 2026-07-17: the plugin declares
`leg_mode` and five `lazy_*` params, and the lazy-arm hooks are wired for BOTH
live (`runtime` guard-close) and paper (`paper_auto` exit marker).

**It was not merely cosmetic.** `aggregate_gate` returns ADVISE when any rule is
`BUILDABLE_WITH_FEATURE` with `live_feasible is False` — so this stale verdict
*forced* the whole strategy to ADVISE, i.e. "installing with caveats", for a
capability that is fully shipped and live-capable.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.ai import capability as cap  # noqa: E402
from app.ai.authoring_agent import GateRule, aggregate_gate  # noqa: E402
from app.strategies.base import get_registry  # noqa: E402

_LAZY_CONCEPTS = ["lazy_leg_contingency", "lazy_leg", "contingent_leg",
                  "two_leg_contingency", "opposite_side_activation"]


def _verdict(concept):
    return cap.classify_rule(cap.RuleTokens(cols=set(), concepts={concept}))


def _shipped_params():
    reg = get_registry()
    reg.auto_discover()
    return set(reg.get("premium_momentum").parameter_schema or {})


def test_the_capability_really_is_shipped():
    """Guard the guard: if this ever fails, the verdict below is right and these
    tests are the thing that is wrong."""
    shipped = _shipped_params()
    assert "leg_mode" in shipped
    assert {"lazy_enabled", "lazy_momentum_pct", "lazy_stop_pct",
            "lazy_target_pct", "lazy_moneyness"} <= shipped


def test_lazy_leg_is_no_longer_called_future_work():
    for c in _LAZY_CONCEPTS:
        msg = _verdict(c).message.lower()
        assert "future work" not in msg, f"{c}: still advertised as future work"
        assert "not yet shipped" not in msg, f"{c}: still advertised as unshipped"


def test_lazy_leg_is_LIVE_feasible():
    """The load-bearing field. live_feasible=False is what forced ADVISE."""
    for c in _LAZY_CONCEPTS:
        assert _verdict(c).live_feasible is not False, (
            f"{c}: still marked not-live-feasible, which forces ADVISE")


def test_a_lazy_leg_rule_no_longer_drags_the_whole_strategy_to_ADVISE():
    """End-to-end through the real aggregator — the user's actual symptom."""
    v = _verdict("lazy_leg_contingency")
    rules = [
        GateRule(id="r1", text="buy whichever premium rises 15% first",
                 kind="ENTRY", criticality="CORE",
                 decision_class="BUILDABLE_NOW", live_feasible=True,
                 message="Buildable now."),
        GateRule(id="r2", text="on primary SL, activate the opposite leg",
                 kind="GATE", criticality="CORE",
                 decision_class=v.feasibility.value, live_feasible=v.live_feasible,
                 message=v.message),
    ]
    assert aggregate_gate(rules) == "BUILD", (
        "a shipped, live-capable capability must not force 'installing with caveats'")


def test_the_verdict_points_at_the_real_config_fields():
    """A user told 'this is buildable' must be told HOW. The names are derived
    from the shipped plugin, so they cannot drift into another phantom."""
    msg = _verdict("lazy_leg_contingency").message
    assert "lazy_enabled" in msg
    for name in ("lazy_momentum_pct", "lazy_stop_pct", "lazy_moneyness"):
        assert name in msg, f"{name} not offered to the user"


def test_every_field_the_verdict_names_actually_exists_on_the_plugin():
    """Same anti-drift rule as the prompt and the wizard panel."""
    import re
    msg = _verdict("lazy_leg_contingency").message
    shipped = _shipped_params()
    for name in re.findall(r"\b(lazy_[a-z_]+|leg_mode)\b", msg):
        assert name in shipped, (
            f"verdict names {name!r}, which the shipped plugin does not declare")


def test_the_stale_spec_doc_reference_is_gone():
    """Citing a 'design committed but not shipped' doc tells the user to go read
    a plan for something that already exists."""
    msg = _verdict("lazy_leg_contingency").message
    assert "phase4-5-full-contingency-design" not in msg
