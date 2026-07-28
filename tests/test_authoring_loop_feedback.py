"""Step 8: make the authoring loop an actual loop.

Three user-reported defects, all the same class — the UI implies a capability or a
conversation that the code does not provide.

**8.1** `capability_summary()["future"]` advertised two capabilities that SHIPPED
in Phase 5B (two-leg entry, lazy-leg contingency). A user reads that and designs
AROUND a feature they already have.

**8.2** The ASK verdict says "Answer the clarifying question(s) above, then
re-check" — but `/author/converse` accepted only the source text. No answer field
existed anywhere. The endpoint is *named* converse and was one-shot.

**8.3** Feasibility and generation were two independent LLM calls over the same
text. The verdict was rendered and thrown away, so running Check feasibility did
not improve what Generate produced — backwards from what the UI implies.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.ai import capability as cap  # noqa: E402
from app.ai.strategy_author import _augmented_user_message  # noqa: E402
from app.strategies.base import get_registry  # noqa: E402


# --------------------------------------------------------------------------- #
# 8.1 — the future list must not advertise shipped capability
# --------------------------------------------------------------------------- #

def _future_text() -> str:
    summary = cap.capability_summary()
    # the premium-trigger family block carries the future list
    for value in _walk(summary):
        pass
    return " ".join(_collect_future(summary)).lower()


def _walk(obj):
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _collect_future(summary):
    out = []
    for node in _walk(summary):
        if isinstance(node, dict) and isinstance(node.get("future"), list):
            out.extend(str(x) for x in node["future"])
    return out


def test_future_list_does_not_advertise_capability_the_plugin_already_has():
    """THE regression: a user designs around a feature that already shipped.

    Cross-checked against the SHIPPED plugin's declared params rather than a
    hand-maintained list, so this cannot rot again.
    """
    reg = get_registry()
    reg.auto_discover()
    shipped = set(reg.get("premium_momentum").parameter_schema or {})
    future = _future_text()
    assert future, "expected a non-empty future list to check"

    if "leg_mode" in shipped:
        assert "two-leg entry" not in future, (
            "future list still calls two-leg entry unshipped, but leg_mode exists")
    if any(k.startswith("lazy") for k in shipped):
        assert "lazy-leg" not in future, (
            "future list still calls lazy-leg unshipped, but the lazy_* params exist")


def test_the_genuinely_future_item_is_still_listed():
    """Don't over-correct: session-level config as DECLARED strategy config really
    is not built (it remains deployment-layer)."""
    assert "session-level" in _future_text()


# --------------------------------------------------------------------------- #
# 8.2 + 8.3 — the feasibility verdict must reach the generator
# --------------------------------------------------------------------------- #

_RULESET = {
    "decision": "ASK",
    "summary": "one rule is ambiguous",
    "rules": [
        {"id": "r1", "text": "buy when premium jumps 15%", "kind": "ENTRY",
         "criticality": "CORE", "decision_class": "BUILDABLE_NOW",
         "message": "Buildable now."},
        {"id": "r2", "text": "when it is strong", "kind": "GATE",
         "criticality": "CORE", "decision_class": "AMBIGUOUS",
         "question": "Do you mean the option premium or the index price?"},
        {"id": "r3", "text": "use order-flow imbalance", "kind": "GATE",
         "criticality": "NICE_TO_HAVE", "decision_class": "INFEASIBLE",
         "message": "Requires tick-level depth 1m bars can't reconstruct."},
    ],
}


def test_generation_message_is_plain_source_when_no_feasibility_was_run():
    """Byte-identical to today for anyone who skips Check feasibility."""
    assert _augmented_user_message("my strategy", None, None) == "my strategy"
    assert _augmented_user_message("my strategy", None, "") == "my strategy"


def test_the_ruleset_reaches_the_generator():
    msg = _augmented_user_message("my strategy", _RULESET, None)
    assert "my strategy" in msg, "the original description must survive"
    assert "buy when premium jumps 15%" in msg


def test_unbuildable_rules_are_named_so_the_generator_cannot_invent_them():
    """An INFEASIBLE rule must be reported honestly, not approximated — that is
    the whole point of feeding the verdict forward."""
    msg = _augmented_user_message("my strategy", _RULESET, None)
    assert "order-flow imbalance" in msg
    assert "couldnt_map" in msg, (
        "the generator must be told to record unbuildable rules honestly")


def test_the_users_answers_reach_the_generator():
    """8.2 + 8.3 together: the answer channel is worthless if the answers only
    feed the re-check and never the thing that builds the strategy."""
    msg = _augmented_user_message(
        "my strategy", _RULESET, "By strong I mean the option premium, not spot.")
    assert "option premium, not spot" in msg
    assert "r2" in msg or "ambiguous" in msg.lower()


def test_answers_alone_still_reach_the_generator():
    """A user may answer without a stored ruleset (e.g. after an edit)."""
    msg = _augmented_user_message("my strategy", None, "I mean the premium.")
    assert "I mean the premium." in msg
    assert "my strategy" in msg


def test_the_request_schemas_accept_the_new_fields():
    from app.schemas import ConverseReq, StrategyFromSourceReq
    assert "answers" in ConverseReq.model_fields
    assert "answers" in StrategyFromSourceReq.model_fields
    assert "ruleset" in StrategyFromSourceReq.model_fields


def test_the_wizard_sends_them():
    path = os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                        "components", "strategy", "AuthoringWizard.jsx")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "author-answers" in src, "no answer box for an ASK verdict"
    assert "answers" in src and "ruleSet" in src
    api_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                            "lib", "api.js")
    with open(api_path, encoding="utf-8") as fh:
        api_src = fh.read()
    i = api_src.index("authorFromSource")
    assert "answers" in api_src[i:i + 400] and "ruleset" in api_src[i:i + 400], (
        "authorFromSource must forward the verdict and the answers")
