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


def _patch_llm(monkeypatch, parsed):
    import app.ai.llm_client as llm
    def fake(*, tier, system, user, output_model, provider=None, max_tokens=4000):
        return parsed
    monkeypatch.setattr(llm, "complete_structured", fake)


def test_map_source_to_ruleset_build(monkeypatch):
    from app.ai.authoring_agent import map_source_to_ruleset, ParsedRuleSet, ParsedRule
    parsed = ParsedRuleSet(rules=[
        ParsedRule(id="r1", text="enter when rsi>70", kind="ENTRY",
                   criticality="CORE", cols=["rsi"], barspan=1),
    ])
    _patch_llm(monkeypatch, parsed)
    out = map_source_to_ruleset("enter when rsi over 70")
    assert out["decision"] == "BUILD"
    assert out["rules"][0]["decision_class"] == "BUILDABLE_NOW"


def test_map_source_to_ruleset_fvg_is_advise(monkeypatch):
    from app.ai.authoring_agent import map_source_to_ruleset, ParsedRuleSet, ParsedRule
    parsed = ParsedRuleSet(rules=[
        ParsedRule(id="r1", text="enter at a bullish FVG", kind="ENTRY",
                   criticality="CORE", concepts=["fvg"]),
    ])
    _patch_llm(monkeypatch, parsed)
    out = map_source_to_ruleset("buy when price returns to a bullish FVG")
    assert out["decision"] == "ADVISE"
    r = out["rules"][0]
    assert r["decision_class"] == "BUILDABLE_WITH_FEATURE"
    assert r["feature"] == "fvg_zones" and r["live_feasible"] is False


def test_map_source_to_ruleset_oi_core_is_reject(monkeypatch):
    from app.ai.authoring_agent import map_source_to_ruleset, ParsedRuleSet, ParsedRule
    parsed = ParsedRuleSet(rules=[
        ParsedRule(id="r1", text="enter when OI rises", kind="ENTRY",
                   criticality="CORE", concepts=["oi"]),
    ])
    _patch_llm(monkeypatch, parsed)
    out = map_source_to_ruleset("enter when open interest spikes")
    assert out["decision"] == "REJECT"


def test_map_source_to_ruleset_ambiguous_is_ask(monkeypatch):
    from app.ai.authoring_agent import map_source_to_ruleset, ParsedRuleSet, ParsedRule
    parsed = ParsedRuleSet(rules=[
        ParsedRule(id="r1", text="enter on a strong move", kind="ENTRY",
                   criticality="CORE", ambiguous=True, question="How big is 'strong'?"),
    ])
    _patch_llm(monkeypatch, parsed)
    out = map_source_to_ruleset("enter on a strong move")
    assert out["decision"] == "ASK"
    assert out["rules"][0]["question"] == "How big is 'strong'?"


def test_build_summary_is_present(monkeypatch):
    from app.ai.authoring_agent import map_source_to_ruleset, ParsedRuleSet, ParsedRule
    _patch_llm(monkeypatch, ParsedRuleSet(rules=[
        ParsedRule(id="r1", text="rsi>70", kind="ENTRY", criticality="CORE", cols=["rsi"])]))
    out = map_source_to_ruleset("enter when rsi over 70")
    assert out["decision"] == "BUILD"
    assert "map cleanly" in out["summary"]


def test_dropped_optional_advise_summary_is_sensible(monkeypatch):
    # ADVISE driven SOLELY by a dropped OPTIONAL rule must NOT emit the degenerate
    # "backtest-only feature(s): ." string.
    from app.ai.authoring_agent import map_source_to_ruleset, ParsedRuleSet, ParsedRule
    _patch_llm(monkeypatch, ParsedRuleSet(rules=[
        ParsedRule(id="r1", text="rsi>70", kind="ENTRY", criticality="CORE", cols=["rsi"]),
        ParsedRule(id="r2", text="scale on OI", kind="SIZING", criticality="OPTIONAL",
                   concepts=["oi"]),
    ]))
    out = map_source_to_ruleset("enter rsi>70; scale up on high OI")
    assert out["decision"] == "ADVISE"
    assert "dropped optional rule(s): r2" in out["summary"]
    assert "feature(s): ." not in out["summary"]


def test_backtest_only_advise_summary_names_feature(monkeypatch):
    from app.ai.authoring_agent import map_source_to_ruleset, ParsedRuleSet, ParsedRule
    _patch_llm(monkeypatch, ParsedRuleSet(rules=[
        ParsedRule(id="r1", text="enter at FVG", kind="ENTRY", criticality="CORE",
                   concepts=["fvg"])]))
    out = map_source_to_ruleset("buy the bullish FVG")
    assert out["decision"] == "ADVISE"
    assert "fvg_zones" in out["summary"]


def test_empty_parse_is_ask_not_build(monkeypatch):
    from app.ai.authoring_agent import map_source_to_ruleset, ParsedRuleSet
    _patch_llm(monkeypatch, ParsedRuleSet(rules=[]))
    out = map_source_to_ruleset("nonsense that yields no rules")
    assert out["decision"] == "ASK"
    assert out["rules"] == []
    assert "couldn't extract" in out["summary"]


def test_flagship_ict_multirule_advise(monkeypatch):
    # The motivating case: a real ICT strategy whose FVG rule is backtest-only ->
    # ADVISE (not a silently-degraded BUILD).
    from app.ai.authoring_agent import map_source_to_ruleset, ParsedRuleSet, ParsedRule
    parsed = ParsedRuleSet(rules=[
        ParsedRule(id="r1", text="bias from premium/discount", kind="FILTER",
                   criticality="CORE", concepts=["premium_discount"]),   # live ok
        ParsedRule(id="r2", text="enter at a bullish FVG", kind="ENTRY",
                   criticality="CORE", concepts=["fvg"]),                 # backtest-only
        ParsedRule(id="r3", text="target the opposing liquidity", kind="EXIT",
                   criticality="CORE", concepts=["sweep"]),               # live ok
    ])
    _patch_llm(monkeypatch, parsed)
    out = map_source_to_ruleset("ICT: in discount, buy the bullish FVG, target liquidity")
    assert out["decision"] == "ADVISE"
    classes = {r["id"]: r["decision_class"] for r in out["rules"]}
    assert classes == {"r1": "BUILDABLE_WITH_FEATURE", "r2": "BUILDABLE_WITH_FEATURE",
                       "r3": "BUILDABLE_WITH_FEATURE"}
    # pin the per-rule live-feasibility so "only the FVG rule drives ADVISE" is load-bearing
    live = {r["id"]: r["live_feasible"] for r in out["rules"]}
    assert live == {"r1": True, "r2": False, "r3": True}
    assert "fvg_zones" in out["summary"]
