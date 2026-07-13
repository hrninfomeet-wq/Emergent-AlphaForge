"""SP-4 collaborative authoring agent: parse source -> Rules -> classify -> gate.

The LLM (via llm_client) only PARSES source into ParsedRuleSet (text + the
deterministic facts per rule). The decision is deterministic: classify_rule
(SP-3, pure) per rule + aggregate_gate (pure, here). Host-importable.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

RuleKind = Literal["ENTRY", "EXIT", "FILTER", "GATE", "SIZING", "SESSION", "META"]
Criticality = Literal["CORE", "OPTIONAL"]


class ParsedRule(BaseModel):
    id: str
    text: str
    kind: RuleKind
    criticality: Criticality = "CORE"
    cols: List[str] = Field(default_factory=list)
    concepts: List[str] = Field(default_factory=list)
    barspan: int = 1
    window: int = 0
    session_anchored: bool = False
    ohlcv_derivable: bool = False
    ambiguous: bool = False
    question: str = ""


class ParsedRuleSet(BaseModel):
    rules: List[ParsedRule] = Field(default_factory=list)


class GateRule(BaseModel):
    id: str
    text: str
    kind: str
    criticality: str
    decision_class: str
    message: str
    feature: Optional[str] = None
    live_feasible: Optional[bool] = None
    question: Optional[str] = None


class GateResult(BaseModel):
    decision: str
    rules: List[GateRule]
    summary: str


def aggregate_gate(rules: List[GateRule]) -> str:
    """Pure first-principles decision over per-rule verdicts.

    REJECT  if any CORE rule cannot be built at all (INFEASIBLE / NEEDS_NEW_DATA).
    ASK     else if any rule is AMBIGUOUS (needs user clarification first).
    ADVISE  else if buildable but with caveats (a backtest-only feature, or an
            OPTIONAL rule that will be dropped).
    BUILD   else (every CORE rule maps cleanly, no caveats).
    """
    core = [r for r in rules if r.criticality == "CORE"]
    if any(r.decision_class in ("INFEASIBLE", "NEEDS_NEW_DATA") for r in core):
        return "REJECT"
    if any(r.decision_class == "AMBIGUOUS" for r in rules):
        return "ASK"
    backtest_only = any(
        r.decision_class == "BUILDABLE_WITH_FEATURE" and r.live_feasible is False
        for r in rules
    )
    dropped_optional = any(
        r.criticality == "OPTIONAL" and r.decision_class in ("INFEASIBLE", "NEEDS_NEW_DATA")
        for r in rules
    )
    if backtest_only or dropped_optional:
        return "ADVISE"
    return "BUILD"


def _ruleset_system_prompt(report: dict) -> str:
    cols = ", ".join(report["columns"])
    feats = ", ".join(f["feature"] for f in report["features"])
    return (
        "You decompose an option-buying strategy description into ordered RULES.\n"
        "For each rule emit: id, text (plain English restatement), kind "
        "(ENTRY/EXIT/FILTER/GATE/SIZING/SESSION/META), criticality (CORE if the "
        "strategy is meaningless without it, else OPTIONAL), and the DETERMINISTIC "
        "FACTS a downstream checker needs:\n"
        "  - cols: existing column names the rule references (choose ONLY from: "
        f"{cols}).\n"
        "  - concepts: named structural/data concepts. Emit the SPECIFIC name from "
        "the vocabulary below, NOT a paraphrase.\n"
        "\n"
        "    ICT / SMC structure: fvg, order_block, bos, choch, premium_discount, "
        "sweep, swing_level, breaker, ote, equal_highs, equal_lows, divergence, mtf.\n"
        "    (`premium_discount` = the ICT PRICE-based premium/discount ZONE, i.e. "
        "the current price sitting in the upper or lower half of a swing range. "
        "This is NOT an option-premium rule.)\n"
        "\n"
        "    Options-premium-native (shipped `premium_momentum` config family — "
        "use these for rules like 'BUY the CE when its PREMIUM rises 20% from the "
        "09:31 snapshot on the ITM1 strike'):\n"
        "      option_premium_trigger, option_premium_momentum, premium_momentum, "
        "locked_strike, strike_lock, premium_snapshot, moneyness_selection, "
        "stepped_premium_trail, premium_stop_pct, premium_target_pct.\n"
        "\n"
        "    Session-level gates (handled at the DEPLOYMENT layer — enter/exit "
        "windows, EOD square-off, re-entry cutoffs, day caps, global TP/SL "
        "closing everything, POSITION SIZE / lot count). DO NOT try to encode "
        "these as per-bar rules — emit the concept name and the classifier will "
        "map them to the deployment layer for the user:\n"
        "      entry_time_gate, exit_time_gate, eod_squareoff, re_entry_cutoff, "
        "max_positions_per_day, max_lots_per_day, global_target_sl, "
        "position_size, lot_size, sizing.\n"
        "\n"
        "    Blocked data (no warehouse coverage yet): oi, open_interest, pcr, "
        "max_pain, iv, iv_rank, greeks (delta/gamma/theta/vega), news, sentiment, "
        "order_flow, orderflow, depth, l2, tape.\n"
        "\n"
        "    Cross-instrument: relative_strength, pairs, ratio_spread.\n"
        "\n"
        "    Phase-5 future work (still emit — the classifier will mark it "
        "buildable-not-yet-shipped, not a blanket reject): lazy_leg_contingency, "
        "opposite_side_activation, contingent_leg.\n"
        "\n"
        f"    Buildable structural features already in the codebase: {feats}.\n"
        "  - barspan: how many bars back the rule looks (1-2 = simple; >2 = needs history).\n"
        "  - window: rolling-window length the rule needs (0 if none).\n"
        "  - session_anchored: true if it needs this session's range / opening range.\n"
        "  - ohlcv_derivable: true if it's a math quantity computable from OHLCV but "
        "not in the column list above.\n"
        "\n"
        "IMPORTANT — do NOT map an OPTION-premium rule to `premium_discount`. "
        "`premium_discount` is the ICT price-range zone concept. Option-premium "
        "rules (rules about the option's own PRICE/PREMIUM rising or falling from "
        "a reference-time snapshot) MUST use `option_premium_trigger` (and any "
        "companions like `locked_strike`, `stepped_premium_trail`, etc.).\n"
        "\n"
        "If you cannot pin a rule down, set ambiguous=true and put ONE clarifying "
        "question in `question`. Do NOT invent column names. Do NOT decide feasibility "
        "yourself — just extract the facts."
    )


def map_source_to_ruleset(source_text: str, provider: Optional[str] = None) -> dict:
    """Parse source -> Rules (LLM) -> classify each (pure) -> aggregate (pure)."""
    from app.ai import llm_client
    from app.ai.capability import capability_report, classify_rule, RuleTokens

    report = capability_report()
    parsed: ParsedRuleSet = llm_client.complete_structured(
        tier=llm_client.FAST,
        system=_ruleset_system_prompt(report),
        user=source_text,
        output_model=ParsedRuleSet,
        provider=provider,
    )

    gate_rules: List[GateRule] = []
    for pr in parsed.rules:
        if pr.ambiguous:
            gate_rules.append(GateRule(
                id=pr.id, text=pr.text, kind=pr.kind, criticality=pr.criticality,
                decision_class="AMBIGUOUS", message="Needs clarification.",
                question=pr.question or "Please clarify this rule."))
            continue
        tokens = RuleTokens(
            cols=frozenset(pr.cols), concepts=frozenset(c.lower() for c in pr.concepts),
            barspan=pr.barspan, window=pr.window,
            session_anchored=pr.session_anchored, ohlcv_derivable=pr.ohlcv_derivable)
        v = classify_rule(tokens)
        gate_rules.append(GateRule(
            id=pr.id, text=pr.text, kind=pr.kind, criticality=pr.criticality,
            decision_class=v.feasibility.value, message=v.message,
            feature=v.feature, live_feasible=v.live_feasible))

    if not gate_rules:
        # The LLM extracted no rules — don't pretend that's a confident BUILD.
        return GateResult(
            decision="ASK", rules=[],
            summary="I couldn't extract any rules from that — describe the entry "
                    "and exit more concretely.").model_dump()

    decision = aggregate_gate(gate_rules)
    return GateResult(decision=decision, rules=gate_rules,
                      summary=_gate_summary(decision, gate_rules)).model_dump()


def _gate_summary(decision: str, rules: List[GateRule]) -> str:
    n = len(rules)
    if decision == "BUILD":
        return f"All {n} rules map cleanly. Ready to build."
    if decision == "ASK":
        qs = [r.question for r in rules if r.decision_class == "AMBIGUOUS" and r.question]
        return "Need clarification before building: " + " ".join(qs)
    if decision == "ADVISE":
        # ADVISE has two causes: a backtest-only feature, and/or a dropped
        # OPTIONAL rule. Narrate whichever applies (never an empty list).
        bt = sorted(set(r.feature for r in rules
                        if r.decision_class == "BUILDABLE_WITH_FEATURE"
                        and r.live_feasible is False and r.feature))
        dropped = [r.id for r in rules if r.criticality == "OPTIONAL"
                   and r.decision_class in ("INFEASIBLE", "NEEDS_NEW_DATA")]
        parts = []
        if bt:
            parts.append("backtest-only feature(s): " + ", ".join(bt))
        if dropped:
            parts.append("dropped optional rule(s): " + ", ".join(dropped))
        return "Buildable with caveats — " + "; ".join(parts) + "."
    return ("Can't build this faithfully — a core rule needs data/precision the app "
            "doesn't have. See the rejected rule(s).")
