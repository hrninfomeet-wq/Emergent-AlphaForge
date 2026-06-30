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
