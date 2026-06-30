import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.features.catalog  # noqa: F401

from app.ai.authoring_agent import GateRule, aggregate_gate


def G(criticality="CORE", decision_class="BUILDABLE_NOW", live_feasible=None):
    return GateRule(id="r", text="t", kind="ENTRY", criticality=criticality,
                    decision_class=decision_class, message="m",
                    feature=None, live_feasible=live_feasible, question=None)


def test_all_core_buildable_now_is_build():
    assert aggregate_gate([G(), G()]) == "BUILD"


def test_core_infeasible_is_reject():
    assert aggregate_gate([G(), G(decision_class="INFEASIBLE")]) == "REJECT"


def test_core_needs_new_data_is_reject():
    assert aggregate_gate([G(decision_class="NEEDS_NEW_DATA")]) == "REJECT"


def test_ambiguous_is_ask_when_not_rejected():
    assert aggregate_gate([G(), G(decision_class="AMBIGUOUS")]) == "ASK"


def test_reject_beats_ask():
    rules = [G(decision_class="INFEASIBLE"), G(decision_class="AMBIGUOUS")]
    assert aggregate_gate(rules) == "REJECT"


def test_backtest_only_feature_is_advise():
    rules = [G(), G(decision_class="BUILDABLE_WITH_FEATURE", live_feasible=False)]
    assert aggregate_gate(rules) == "ADVISE"


def test_optional_infeasible_is_advise_not_reject():
    rules = [G(), G(criticality="OPTIONAL", decision_class="INFEASIBLE")]
    assert aggregate_gate(rules) == "ADVISE"


def test_live_feasible_feature_is_plain_build():
    rules = [G(), G(decision_class="BUILDABLE_WITH_FEATURE", live_feasible=True)]
    assert aggregate_gate(rules) == "BUILD"
