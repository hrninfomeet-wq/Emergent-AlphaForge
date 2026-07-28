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



def _premium_trigger_field_doc() -> str:
    """Enumerate the premium-trigger config fields FROM THE MODEL.

    Never hand-typed. A hand-copied list is exactly what produced the phantom
    `expiry` field that shipped in ai/capability.py for months: a prompt that
    advertises a nonexistent field induces the LLM to emit it, and it then dies
    against PremiumTriggerConfig's extra="forbid" with an error the user cannot
    act on. Deriving it means the prompt cannot drift from the schema.
    """
    from app.premium_trigger_config import PremiumTriggerConfig

    import typing

    parts = []
    for name, field in PremiumTriggerConfig.model_fields.items():
        ann = field.annotation
        args = typing.get_args(ann)

        # Literal choices are surfaced verbatim so the model cannot invent a value.
        literals = [a for a in args if isinstance(a, str)]
        if literals:
            parts.append(f"`{name}` ({' | '.join(repr(a) for a in literals)})")
            continue

        # Unwrap Optional[X] -> X. Rendering the bare word "Optional" would tell
        # the model nothing about the value it should produce: `momentum_pct
        # (Optional)` gives no hint that it is a percentage float.
        inner = [a for a in args if a is not type(None)]
        target = inner[0] if inner else ann
        text = getattr(target, "__name__", None) or str(target)
        if type(None) in args:
            text += ", optional"
        parts.append(f"`{name}` ({text})")
    return ", ".join(parts)


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

# Premium-trigger strategies (the `premium_trigger` object) — READ THIS FIRST
Some strategies do NOT decide from per-bar indicator conditions at all. They lock an option strike at a reference time and enter when the OPTION PREMIUM ITSELF moves by a threshold (e.g. "at 09:31 note the ITM1 call and put premium; buy whichever rises 15% first; stop 20%"). That family is expressed with the `premium_trigger` object, NOT with entry_ce/entry_pe.

If the source describes that shape, set `premium_trigger` and leave entry_ce/entry_pe EMPTY. Setting both is REJECTED by the compiler: a premium-trigger strategy is run by the premium session engine, which replaces the per-bar evaluate entirely, so entry conditions could never fire and you would be describing rules the engine ignores.

Fields (use ONLY these exact names; anything else is rejected):
{_premium_trigger_field_doc()}

Rules the compiler enforces — violating them fails the build:
- Exactly ONE of `momentum_pct` or `momentum_pts` (the entry trigger). Never both, never neither.
- `trail_x` and `trail_y` are set together or not at all.
- Times are "HH:MM" 24-hour, zero-padded (e.g. "09:31", not "9:31").
- A premium-trigger strategy's exits live HERE (stop_pct/stop_pts/target_pct/target_pts), not in the `exits` object. Leaving target unset means "ride to end of day", which is valid.

If the source is an ordinary indicator strategy, ignore this section entirely and leave `premium_trigger` null.

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



def _augmented_user_message(source_text: str, ruleset=None, answers=None) -> str:
    """Fold a prior feasibility verdict + the user's clarifications into the
    generation request.

    Feasibility and generation used to be two INDEPENDENT calls over the same
    text: the verdict was rendered and thrown away, so running "Check feasibility"
    did not improve what "Generate with AI" produced — backwards from what the UI
    implies. This is what makes the loop an actual loop.

    Returns *source_text* UNCHANGED when there is nothing to add, so a user who
    skips the feasibility step gets byte-identical behaviour to before.

    The analysis is appended to the USER message, not the system prompt: it is
    per-request data, and the system prompt must stay a stable, cacheable
    description of the fixed vocabulary.
    """
    rules = list((ruleset or {}).get("rules") or [])
    answers = (answers or "").strip()
    if not rules and not answers:
        return source_text

    parts = [source_text, "", "# PRIOR FEASIBILITY ANALYSIS (authoritative — do not re-litigate)"]
    if rules:
        parts.append(
            "A deterministic classifier already judged each rule below. Honour these "
            "verdicts: they come from the engine's real capabilities, not from a model."
        )
        for r in rules:
            cls = str(r.get("decision_class") or "")
            text = str(r.get("text") or "").strip()
            note = str(r.get("question") or r.get("message") or "").strip()
            parts.append(f"- [{cls}] {text}" + (f" — {note}" if note else ""))

        blocked = [str(r.get("text") or "").strip() for r in rules
                   if str(r.get("decision_class") or "") in
                   ("INFEASIBLE", "NEEDS_NEW_DATA", "REJECT")]
        if blocked:
            parts += [
                "",
                "These rules are NOT buildable. Do NOT approximate them with a "
                "different indicator or invent a proxy — put each one verbatim in "
                "fidelity.couldnt_map and encode the rest:",
            ] + [f"- {b}" for b in blocked]

    if answers:
        parts += [
            "",
            "# THE USER'S ANSWERS to the clarifying questions",
            "These resolve the AMBIGUOUS rules above. Treat them as the user's own "
            "words and encode accordingly — do not ask again.",
            answers,
        ]
    return "\n".join(parts)


def map_source_to_spec(source_text: str, provider: str | None = None, *,
                       ruleset: Dict[str, Any] | None = None,
                       answers: str | None = None) -> Dict[str, Any]:
    """Sonnet maps the text to {spec, fidelity}. Returns plain dicts. The grounding
    catalog + validate are derived from live code so the AI can't hallucinate columns.

    Args:
        source_text: Free-text strategy description to map into a StrategySpec.
        provider: Optional override for the AI provider (e.g. "anthropic", "gemini").
                  When set, overrides the AI_PROVIDER env-var / router default.
    """
    from app.ai.grounding import build_grounding_catalog
    from app.ai.compiler import validate_spec
    from app.ai import llm_client

    catalog = build_grounding_catalog()
    mapped: MappedSpec = llm_client.complete_structured(
        tier=llm_client.FAST,
        system=_system_prompt(catalog),
        user=_augmented_user_message(source_text, ruleset, answers),
        output_model=MappedSpec,
        provider=provider,
    )
    errors = validate_spec(mapped.spec)  # catch any column/op the AI got wrong
    return {
        "spec": mapped.spec.model_dump(),
        "fidelity": mapped.fidelity.model_dump(),
        "errors": errors,
    }
