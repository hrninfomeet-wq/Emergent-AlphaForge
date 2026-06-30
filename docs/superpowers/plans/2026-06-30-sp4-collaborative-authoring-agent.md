# SP-4 — Collaborative Authoring Agent + Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace silent degradation with a collaborative gate — parse a strategy's source into ordered Rules, classify each through SP-3's pure `classify_rule`, aggregate to a single decision (BUILD / ASK / ADVISE / REJECT), expose it at `POST /strategies/author/converse`, and surface it in the wizard as a per-rule RuleSet panel with Install gated on BUILD — so the user is told exactly what can be built, what's backtest-only, what needs clarification, and what's impossible, instead of getting a quietly-degraded proxy.

**Architecture:** Net-new `backend/app/ai/authoring_agent.py` holds: the Pydantic models (`ParsedRule`/`ParsedRuleSet` = the LLM's structured output; `GateRule`/`GateResult` = the response), the **pure** `aggregate_gate(rules) -> decision` (host-tested, no LLM), and `map_source_to_ruleset(source, provider)` which calls the existing `llm_client.complete_structured` seam (Gemini-default) to parse, then runs SP-3's pure `classify_rule` per rule, then `aggregate_gate`. A thin `POST /strategies/author/converse` route in `strategies_admin.py` wraps it (mirroring `author_from_source`). The wizard (`AuthoringWizard.jsx`) gains a RuleSet panel rendered from the converse response, with the Install button gated on `decision == "BUILD"`. The Full-Python sandbox + its 36-evasion battery are NOT touched.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI; React (CRA/craco) + shadcn; pytest (host venv — no motor; the LLM is mocked at `llm_client.complete_structured`). Live Gemini validation is a final manual step (needs the configured `GEMINI_API_KEY`).

---

## Background the implementer needs (verified by recon)

**LLM seam — `backend/app/ai/llm_client.py`:**
```python
def complete_structured(*, tier: str, system: str, user: str,
                        output_model: Type[T], provider: Optional[str] = None,
                        max_tokens: int = 4000) -> T: ...
```
`tier` is `llm_client.FAST` or `llm_client.POWERFUL`. Gemini models: `gemini-2.5-flash` (FAST) / `gemini-2.5-pro` (POWERFUL). In tests, patch `app.ai.llm_client.complete_structured` to return a canned `output_model` instance — do NOT call a real LLM.

**SP-3 classifier — `backend/app/ai/capability.py`:**
```python
classify_rule(tokens: RuleTokens, *, required_features=()) -> Verdict
# RuleTokens(cols, concepts, barspan, window, session_anchored, ohlcv_derivable)  [frozenset for cols/concepts]
# Verdict(feasibility: FeasibilityClass, message, feature, live_feasible)
# FeasibilityClass: BUILDABLE_NOW | BUILDABLE_WITH_FEATURE | NEEDS_NEW_DATA | INFEASIBLE
capability_report() -> {"columns", "features", "warehouse"}
```

**Existing Spec authoring — `backend/app/ai/strategy_author.py`:** `map_source_to_spec(source_text, provider=None) -> {spec, fidelity, errors}` (tier=FAST, grounds via `build_grounding_catalog`). `Fidelity{captured, couldnt_map, ambiguous}`. This is the pattern `map_source_to_ruleset` mirrors. SP-4 does NOT replace it — `converse` is the new gated front door; `from-source` stays.

**Routes — `backend/app/routers/strategies_admin.py`** (`api = APIRouter()`): `author_from_source` (POST `/strategies/author/from-source`, body `StrategyFromSourceReq{source, provider?}`) is at ~line 262-288. Add `POST /strategies/author/converse` right after it, with the same provider-validation boilerplate. Schemas live in `backend/app/schemas.py` (the author req models are ~lines 410-435) — add `ConverseReq` there.

**Wizard — `frontend/src/components/strategy/AuthoringWizard.jsx`:** `export default function AuthoringWizard({ open, onOpenChange, onInstalled })`. Mode toggle `spec | python` (`useState("spec")`). The "Describe with AI" section + fidelity readback ends ~line 423; the RuleSet panel inserts after it (inside `mode === "spec"`). Install button: `data-testid="author-install-btn"` ~line 644. API calls in `frontend/src/lib/api.js` (~lines 33-44) — add `authorConverse`.

**Sandbox red-team — `tests/test_py_sandbox.py`** (36 tests): SP-4 must NOT modify `py_sandbox.py`; if anything there changes, the whole battery must stay green. SP-4 is gate/parse logic only — it should not touch the sandbox.

**Test conventions:** new test modules start with
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
```
Full-suite baseline: ~5 motor failures + 16 motor collection errors (unchanged); run with `--continue-on-collection-errors`.

---

## File Structure

- **Create** `backend/app/ai/authoring_agent.py` — models + pure `aggregate_gate` + `map_source_to_ruleset`.
- **Modify** `backend/app/schemas.py` — add `ConverseReq`.
- **Modify** `backend/app/routers/strategies_admin.py` — add `POST /strategies/author/converse`.
- **Modify** `frontend/src/lib/api.js` — add `authorConverse`.
- **Modify** `frontend/src/components/strategy/AuthoringWizard.jsx` — RuleSet panel + BUILD-gated Install.
- **Create** `tests/test_authoring_agent.py` — pure `aggregate_gate` + `map_source_to_ruleset` (mocked LLM).
- **Modify** `tests/test_strategy_authoring_routes.py` — the `/converse` route test.

---

## Task 1: Agent models + pure `aggregate_gate`

**Files:**
- Create: `backend/app/ai/authoring_agent.py`
- Test: `tests/test_authoring_agent.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_authoring_agent.py`:
```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_authoring_agent.py -q`
Expected: FAIL — `app.ai.authoring_agent` does not exist.

- [ ] **Step 3: Create `backend/app/ai/authoring_agent.py` (models + aggregate_gate)**

```python
"""SP-4 collaborative authoring agent: parse source -> Rules -> classify -> gate.

The LLM (via llm_client) only PARSES source into ParsedRuleSet (text + the
deterministic facts per rule). The decision is deterministic: classify_rule
(SP-3, pure) per rule + aggregate_gate (pure, here). Host-importable.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# ---- LLM structured output (what the model returns) ----

RuleKind = Literal["ENTRY", "EXIT", "FILTER", "GATE", "SIZING", "SESSION", "META"]
Criticality = Literal["CORE", "OPTIONAL"]


class ParsedRule(BaseModel):
    id: str
    text: str                                   # the rule restated in plain English
    kind: RuleKind
    criticality: Criticality = "CORE"
    # deterministic facts the LLM extracts for the SP-3 classifier:
    cols: List[str] = Field(default_factory=list)
    concepts: List[str] = Field(default_factory=list)
    barspan: int = 1
    window: int = 0
    session_anchored: bool = False
    ohlcv_derivable: bool = False
    ambiguous: bool = False                      # LLM couldn't pin it down
    question: str = ""                           # the clarifying question (when ambiguous)


class ParsedRuleSet(BaseModel):
    rules: List[ParsedRule] = Field(default_factory=list)


# ---- the gated result (what the endpoint returns) ----

class GateRule(BaseModel):
    id: str
    text: str
    kind: str
    criticality: str
    decision_class: str                          # FeasibilityClass value OR "AMBIGUOUS"
    message: str
    feature: Optional[str] = None
    live_feasible: Optional[bool] = None
    question: Optional[str] = None


class GateResult(BaseModel):
    decision: str                                # BUILD | ASK | ADVISE | REJECT
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
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `python -m pytest tests/test_authoring_agent.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/authoring_agent.py tests/test_authoring_agent.py
git commit -m "feat(ai): SP-4 authoring-agent models + pure aggregate_gate (BUILD/ASK/ADVISE/REJECT)"
```

---

## Task 2: `map_source_to_ruleset` (parse → classify → gate)

**Files:**
- Modify: `backend/app/ai/authoring_agent.py`
- Test: `tests/test_authoring_agent.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_authoring_agent.py`)

```python
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
    assert out["decision"] == "ADVISE"                      # fvg_zones is backtest-only
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_authoring_agent.py -k map_source -q`
Expected: FAIL — `map_source_to_ruleset` undefined.

- [ ] **Step 3: Implement `map_source_to_ruleset` (append to `authoring_agent.py`)**

```python
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
        "  - concepts: named structural/data concepts (e.g. fvg, order_block, bos, "
        "choch, premium_discount, sweep, oi, order_flow, relative_strength). "
        f"Buildable structural features: {feats}.\n"
        "  - barspan: how many bars back the rule looks (1-2 = simple; >2 = needs history).\n"
        "  - window: rolling-window length the rule needs (0 if none).\n"
        "  - session_anchored: true if it needs this session's range / opening range.\n"
        "  - ohlcv_derivable: true if it's a math quantity computable from OHLCV but "
        "not in the column list above.\n"
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
        bt = [r.feature for r in rules
              if r.decision_class == "BUILDABLE_WITH_FEATURE" and r.live_feasible is False]
        return ("Buildable with caveats — backtest-only feature(s): "
                + ", ".join(sorted(set(f for f in bt if f))) + ".")
    return ("Can't build this faithfully — a core rule needs data/precision the app "
            "doesn't have. See the rejected rule(s).")
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `python -m pytest tests/test_authoring_agent.py -q`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/authoring_agent.py tests/test_authoring_agent.py
git commit -m "feat(ai): SP-4 map_source_to_ruleset (LLM parse -> classify_rule -> aggregate_gate)"
```

---

## Task 3: `POST /strategies/author/converse` endpoint

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/routers/strategies_admin.py`
- Test: `tests/test_strategy_authoring_routes.py`

- [ ] **Step 1: Write the failing route test** (append to `tests/test_strategy_authoring_routes.py`; reuse that file's existing TestClient/app fixture + its `llm_client` patch pattern — inspect the file first and mirror how `from-source` is tested)

```python
def test_converse_route_returns_gate_decision(monkeypatch, client):
    # `client` + the app wiring follow this file's existing fixtures; mirror the
    # from-source test. Patch the LLM seam to return a canned ParsedRuleSet.
    from app.ai.authoring_agent import ParsedRuleSet, ParsedRule
    import app.ai.llm_client as llm
    monkeypatch.setattr(llm, "complete_structured",
                        lambda **kw: ParsedRuleSet(rules=[
                            ParsedRule(id="r1", text="rsi>70", kind="ENTRY",
                                       criticality="CORE", cols=["rsi"])]))
    resp = client.post("/api/strategies/author/converse",
                       json={"source": "enter when rsi over 70"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "BUILD"
    assert body["rules"][0]["decision_class"] == "BUILDABLE_NOW"
```
(Adjust the URL prefix `/api/...` and the `client` fixture to match how the other tests in this file call the router.)

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_strategy_authoring_routes.py -k converse -q`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Add `ConverseReq` to `backend/app/schemas.py`** (alongside the other author req models, ~line 435)

```python
class ConverseReq(BaseModel):
    source: str
    provider: Optional[str] = None
```

- [ ] **Step 4: Add the route to `backend/app/routers/strategies_admin.py`** (right after `author_from_source`)

Mirror `author_from_source`'s provider-validation + error handling. Use the exact import + error pattern already in that handler:
```python
@api.post("/strategies/author/converse")
def author_converse(req: ConverseReq):
    """Collaborative gate: parse source -> per-rule feasibility -> BUILD/ASK/ADVISE/REJECT."""
    from app.ai.authoring_agent import map_source_to_ruleset
    try:
        return map_source_to_ruleset(req.source, provider=req.provider)
    except RuntimeError as e:                       # no provider configured
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:                          # LLM/parse failure
        raise HTTPException(status_code=502, detail=f"author/converse failed: {e}")
```
Add `ConverseReq` to the schemas import at the top of the router (match how `StrategyFromSourceReq` is imported). If `author_from_source` resolves the source kind (e.g. detects a YouTube URL) before mapping, reuse that same helper here so `converse` accepts the same inputs.

- [ ] **Step 5: Run the route test — expect PASS**

Run: `python -m pytest tests/test_strategy_authoring_routes.py -k converse -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/routers/strategies_admin.py tests/test_strategy_authoring_routes.py
git commit -m "feat(ai): SP-4 POST /strategies/author/converse endpoint"
```

---

## Task 4: Wizard RuleSet panel + BUILD-gated Install

**Files:**
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/components/strategy/AuthoringWizard.jsx`

- [ ] **Step 1: Add the API call** in `frontend/src/lib/api.js` (alongside `authorFromSource`):

```js
authorConverse: (source, provider) =>
  http.post("/strategies/author/converse", { source, provider }).then((r) => r.data),
```
(Match the file's existing http helper + return shape used by `authorFromSource`.)

- [ ] **Step 2: Add RuleSet state + the converse trigger** in `AuthoringWizard.jsx`

Near the other `useState`s, add:
```jsx
const [ruleSet, setRuleSet] = useState(null);   // { decision, rules, summary }
const [conversing, setConversing] = useState(false);
```
Add a "Check feasibility" button in the "Describe with AI" section (next to "Generate with AI") that calls converse:
```jsx
const runConverse = async () => {
  setConversing(true);
  try {
    const res = await api.authorConverse(sourceText, provider);
    setRuleSet(res);
  } catch (e) {
    toast.error(e.response?.data?.detail || e.message);
  } finally {
    setConversing(false);
  }
};
```
(Use the existing `sourceText` + `provider` state the "Generate with AI" button already uses.)

- [ ] **Step 3: Render the RuleSet panel** after the fidelity readback (~line 423, inside `mode === "spec"`):

```jsx
{ruleSet && (
  <div className={sectionCls} data-testid="ruleset-panel">
    <div className="flex items-center gap-2 mb-2">
      <span className="text-xs font-semibold uppercase tracking-wider text-dim">Feasibility</span>
      <span data-testid="ruleset-decision"
        className={`text-[10px] px-2 py-0.5 rounded-full border font-mono ${
          ruleSet.decision === "BUILD" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
          : ruleSet.decision === "ADVISE" ? "border-amber-500/30 bg-amber-500/10 text-amber-300"
          : ruleSet.decision === "ASK" ? "border-sky-500/30 bg-sky-500/10 text-sky-300"
          : "border-rose-500/30 bg-rose-500/10 text-rose-300"}`}>
        {ruleSet.decision}
      </span>
      <span className="text-[11px] text-dimmer">{ruleSet.summary}</span>
    </div>
    <div className="space-y-1">
      {ruleSet.rules.map((r) => (
        <div key={r.id} className="flex items-start gap-2 text-[11px]" data-testid="ruleset-rule">
          <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${
            r.decision_class === "BUILDABLE_NOW" ? "bg-emerald-500"
            : r.decision_class === "BUILDABLE_WITH_FEATURE" ? (r.live_feasible === false ? "bg-amber-500" : "bg-emerald-500")
            : r.decision_class === "AMBIGUOUS" ? "bg-sky-500" : "bg-rose-500"}`} />
          <div className="min-w-0">
            <div className="text-foreground">{r.text} <span className="text-dimmer">· {r.kind}/{r.criticality}</span></div>
            <div className="text-dimmer">{r.question || r.message}</div>
          </div>
        </div>
      ))}
    </div>
  </div>
)}
```
(Reuse the wizard's existing `sectionCls` class string. The colors use the project's verified palette — `bg-emerald/amber/sky/rose-500` resolve; avoid `bg-dimmer`/`bg-dim` which compute transparent.)

- [ ] **Step 4: Gate Install on BUILD** at the Install button (`data-testid="author-install-btn"`, ~line 644):

Add to the button's `disabled`: `|| (ruleSet && ruleSet.decision === "REJECT")`, and when `ruleSet && ruleSet.decision !== "BUILD"`, show a one-line caveat above the footer:
```jsx
{ruleSet && ruleSet.decision !== "BUILD" && (
  <div className="text-[11px] text-amber-300" data-testid="install-gate-note">
    {ruleSet.decision === "REJECT"
      ? "Can't install — a core rule isn't buildable. See Feasibility above."
      : ruleSet.decision === "ASK"
      ? "Answer the clarifying question(s) above, then re-check."
      : "Installing with caveats (some rules are backtest-only)."}
  </div>
)}
```
Keep REJECT as a hard block (disabled); ASK/ADVISE are soft (a visible caveat, install still allowed for ADVISE; for ASK, leave install enabled but warned — the user may proceed knowingly). Do NOT hard-disable on ADVISE (backtest-only is a legitimate choice).

- [ ] **Step 5: Build the frontend**

Run: `npm run build` (from `frontend/`) — must compile clean. (yarn may not be on PATH; npm works.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api.js frontend/src/components/strategy/AuthoringWizard.jsx
git commit -m "feat(ui): SP-4 wizard RuleSet feasibility panel + BUILD-gated install"
```

---

## Task 5: Integration, regression, live-validation note

**Files:**
- Test: `tests/test_authoring_agent.py` (append a flagship ICT case)

- [ ] **Step 1: Flagship end-to-end (mocked LLM)** — a realistic multi-rule ICT strategy

Append to `tests/test_authoring_agent.py`:
```python
def test_flagship_ict_multirule_advise(monkeypatch):
    from app.ai.authoring_agent import map_source_to_ruleset, ParsedRuleSet, ParsedRule
    parsed = ParsedRuleSet(rules=[
        ParsedRule(id="r1", text="bias from premium/discount", kind="FILTER",
                   criticality="CORE", concepts=["premium_discount"]),       # live ok
        ParsedRule(id="r2", text="enter at a bullish FVG", kind="ENTRY",
                   criticality="CORE", concepts=["fvg"]),                     # backtest-only
        ParsedRule(id="r3", text="target the opposing liquidity", kind="EXIT",
                   criticality="CORE", concepts=["sweep"]),                   # live ok
    ])
    _patch_llm(monkeypatch, parsed)
    out = map_source_to_ruleset("ICT: in discount, buy the bullish FVG, target liquidity")
    assert out["decision"] == "ADVISE"                 # fvg_zones backtest-only -> caveat
    classes = {r["id"]: r["decision_class"] for r in out["rules"]}
    assert classes == {"r1": "BUILDABLE_WITH_FEATURE", "r2": "BUILDABLE_WITH_FEATURE",
                       "r3": "BUILDABLE_WITH_FEATURE"}
    assert "fvg_zones" in out["summary"]
```

- [ ] **Step 2: Run the agent suite**

Run: `python -m pytest tests/test_authoring_agent.py tests/test_strategy_authoring_routes.py tests/test_capability_report.py -q`
Expected: PASS.

- [ ] **Step 3: Confirm the sandbox red-team is untouched + green**

Run: `python -m pytest tests/test_py_sandbox.py -q`
Expected: PASS (all 36 — SP-4 did not touch `py_sandbox.py`).

- [ ] **Step 4: Full host suite**

Run: `python -m pytest tests/ --continue-on-collection-errors -q`
Expected: prior passing count + the new SP-4 tests; ONLY the motor baseline fails (~5 failures + ~16 collection errors). Any new non-motor failure → stop, systematic-debugging.

- [ ] **Step 5: Commit**

```bash
git add tests/test_authoring_agent.py
git commit -m "test(ai): SP-4 flagship ICT multi-rule converse end-to-end + regression"
```

- [ ] **Step 6: LIVE Gemini validation (manual — needs the user + GEMINI_API_KEY)**

After the stack is rebuilt, in the wizard: paste a real ICT FVG strategy, click "Check feasibility", and confirm the live Gemini parse → the RuleSet panel shows the FVG rule as BUILDABLE_WITH_FEATURE / backtest-only and the decision is ADVISE (not a silently-degraded BUILD). This exercises the real `complete_structured(gemini-2.5-flash)` path the host tests mock. **Record the result; do NOT auto-arm or install live.** This step is the user's to run.

---

## Self-Review (run before handing off)

**1. Spec coverage (§8 of the design):**
- §8.1 Rule + RuleSet (kind, criticality, class, evidence, proposal, question) → `ParsedRule`/`GateRule` (Task 1). ✓ (`evidence`→`text`; `proposal`→`feature`; `class`→`decision_class`.)
- §8 the gate (BUILD/ASK/ADVISE/REJECT) → pure `aggregate_gate` (Task 1) + the flow `map_source_to_ruleset` (Task 2). ✓
- §8 `POST /strategies/author/converse` → Task 3. ✓
- §8 wizard RuleSet panel + Install gated on BUILD → Task 4. ✓
- §8 "needs Gemini key for live validation" + "re-run the 36-evasion red-team on sandbox changes" → Task 5 Steps 3 (battery untouched) + 6 (live). ✓

**2. Placeholder scan:** every backend code step is complete; the route + frontend steps say "mirror the existing X pattern" where the exact local idiom must be matched — the implementer MUST read `author_from_source` (route) and the wizard's existing "Generate with AI" handler before writing, and is told so. No TBD.

**3. Type consistency:** `GateRule.decision_class` holds a `FeasibilityClass` value string or `"AMBIGUOUS"`; `aggregate_gate` returns one of the 4 decision strings used identically in tests, the summary, and the wizard; `map_source_to_ruleset` returns `GateResult.model_dump()` whose keys (`decision`, `rules`, `summary`) match the route test + the wizard.

**4. Intentional decisions:**
- The LLM only PARSES (extracts facts); all feasibility decisions are deterministic (`classify_rule` + `aggregate_gate`) — so the gate is testable without an LLM and can't be talked out of a REJECT by the model.
- ADVISE does not hard-block install (backtest-only is a legitimate user choice); only REJECT hard-blocks. ASK is a soft warning.
- SP-4 leaves `from-source`/`python-from-source` intact — `converse` is an additive front door, not a replacement.
- The Full-Python sandbox is untouched (the 36-evasion battery is a regression guard, not modified).

---

## Execution note

Implement via **superpowers:subagent-driven-development**, Tasks 1→5 in order (Task 2 needs Task 1's models; Task 3 needs Task 2; Task 4 needs Task 3's response shape). Two-stage review per task; the adversarial skeptic should focus on: (a) `aggregate_gate` precedence (REJECT > ASK > ADVISE > BUILD; a CORE INFEASIBLE must never resolve to BUILD/ADVISE), (b) the LLM never deciding feasibility (only parsing facts — the deterministic classifier owns the verdict), and (c) the sandbox battery staying green. Task 5 Step 6 (live Gemini) is the user's manual validation.
