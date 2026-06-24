"""Map free-text strategy descriptions to a constrained StrategySpec + a fidelity readback."""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.ai.spec_schema import StrategySpec


class Fidelity(BaseModel):
    captured: List[str] = Field(default_factory=list)      # plain-English of what was encoded
    couldnt_map: List[str] = Field(default_factory=list)   # source bits with no spec representation
    ambiguous: List[str] = Field(default_factory=list)     # bits needing user clarification


class MappedSpec(BaseModel):
    spec: StrategySpec
    fidelity: Fidelity


def _system_prompt(catalog: Dict[str, Any]) -> str:
    """Build the grounding system prompt: the FIXED vocabulary the AI must map into.

    Everything the model is allowed to emit is enumerated from live code (the
    indicator columns come from the grounding catalog), so the model cannot
    invent a column. Anything it cannot express HONESTLY goes into fidelity."""
    cols = sorted(catalog["indicator_columns"]) + ["open", "high", "low", "close", "volume"]
    col_list = ", ".join(cols)
    return f"""You are a precise strategy-mapping assistant for an Indian-index option-buying \
backtester. You convert a free-text description of a trading strategy (a blog post, a \
transcript, a rule list) into a STRICT, machine-checkable StrategySpec, plus an honest \
fidelity readback of what you could and could not encode.

This strategy BUYS OPTIONS (it never sells/writes). A bullish/long-the-underlying signal \
buys a CALL (entry_ce); a bearish/short-the-underlying signal buys a PUT (entry_pe). There \
is no separate short side — "go short" means "buy a put".

# The ONLY columns you may reference (left/right of a condition)
You may reference ONLY these exact column names. Do NOT invent, alias, or pluralise any \
name. If the source mentions an indicator that is not in this list, you CANNOT map it — \
say so in fidelity.couldnt_map (do not substitute a different indicator).
{col_list}

Notes on common names: `close` is the spot/underlying price; `ema9`/`ema20`/etc. are EMAs \
of the given period; `rsi` is RSI; `regime` is a categorical market-regime label (see gates \
below). If you are unsure which exact column a phrase means, list it in fidelity.ambiguous \
rather than guessing.

# Condition shape
Each condition is {{"left": <column>, "op": <operator>, "right": <number | column | "param:NAME">, \
"label": <short reason text, optional>}}.
- `op` is EXACTLY one of: >  >=  <  <=  ==  !=  cross_above  cross_below
  (`cross_above`/`cross_below` mean `left` crossed over/under `right` on THIS bar.)
- `right` may be a number (e.g. 30), another column from the list above, or "param:NAME" to \
reference a tunable parameter you declare in `params`.
- entry_ce conditions are ANDed together (ALL must hold to buy a CALL). Same for entry_pe \
(buy a PUT). At least one of entry_ce / entry_pe must be non-empty.

# Tunable params (optional)
If a rule has a numeric threshold the user would likely want to tune (e.g. an RSI level, an \
EMA gap), declare it in `params` as {{"name": <lower_snake>, "type": "int"|"float"|"bool", \
"min": <num>, "max": <num>, "default": <num>}} and reference it from a condition's `right` \
as "param:NAME". Otherwise just put the literal number in `right`. Prefer literals when the \
text gives a fixed number and does not ask for tuning.

# Exits (the `exits` object)
Set ONLY the fields the source actually specifies; leave the rest null:
- spot_target_pts / spot_stop_pts : profit target / stop measured in UNDERLYING (spot) points.
- target_pct / stop_pct           : profit target / stop as a percent of the OPTION premium.
- time_stop_minutes               : exit N minutes after entry regardless of P&L.
A SCALP/INTRADAY strategy needs at least one exit. If the source gives none, list "no exit \
rule stated" in fidelity.ambiguous and pick the most defensible single exit you can justify.

# Regime gates (optional)
`gate_skip_regimes` is a list of market regimes in which entries are SKIPPED. Allowed values \
ONLY: TREND, TREND_EXPANDING, CHOP, VOLATILE_CHOP, MIXED, UNKNOWN. Use this when the source \
says to avoid e.g. choppy/range-bound markets (-> ["CHOP","VOLATILE_CHOP"]).

# Output fields
- id: a lowercase slug matching ^[a-z][a-z0-9_]*$ (e.g. "ema9_pullback").
- name: a short human title.
- description: one sentence.
- Use defaults for version/supported_instruments/supported_modes/supported_timeframes \
unless the text clearly states otherwise.

# Fidelity (BE HONEST — this is the point of the readback)
- captured: plain-English bullet for EACH rule you encoded into the spec.
- couldnt_map: source rules/indicators with NO representation in the vocabulary above \
(e.g. an indicator not in the column list, options-Greeks logic, multi-leg structures).
- ambiguous: bits that need user clarification (vague thresholds, unstated direction, \
missing exits).

# Discipline
Prefer FEWER, CORRECT conditions over guessing. Never fabricate a column or operator to \
satisfy a rule — put the unmappable rule in couldnt_map instead. It is better to encode a \
faithful subset and be honest about the gaps than to produce a spec that misrepresents the \
source."""


def map_source_to_spec(source_text: str) -> Dict[str, Any]:
    """Sonnet maps the text to {spec, fidelity}. Returns plain dicts. The grounding
    catalog + validate are derived from live code so the AI can't hallucinate columns."""
    from app.ai.grounding import build_grounding_catalog
    from app.ai.compiler import validate_spec
    from app.ai import llm_client

    catalog = build_grounding_catalog()
    mapped: MappedSpec = llm_client.complete_structured(
        model=llm_client.SONNET,
        system=_system_prompt(catalog),
        user=source_text,
        output_model=MappedSpec,
    )
    errors = validate_spec(mapped.spec)  # catch any column/op the AI got wrong
    return {
        "spec": mapped.spec.model_dump(),
        "fidelity": mapped.fidelity.model_dump(),
        "errors": errors,
    }
