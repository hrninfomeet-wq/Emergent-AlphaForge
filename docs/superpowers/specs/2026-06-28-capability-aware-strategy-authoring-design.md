# Capability-Aware Strategy Authoring + Opt-In Structural Feature Framework

**Date:** 2026-06-28
**Status:** Design v1 (grounded by a 4-track multi-agent exploration + adversarial critique) → ready for plan
**Branch:** `feat/capability-aware-authoring` (worktree `af-wt-strategy-library`, stacked off `feat/strategy-full-python` / PR #6).
**Builds on:** [Part 1 multi-provider authoring](2026-06-27-multi-provider-ai-authoring-design.md) (Spec mode) + [Part 2 full-Python escape hatch](2026-06-27-strategy-authoring-full-python-escape-hatch-design.md) (Full-Python mode).

---

## 1. Context & motivation

Today's AI authoring (Spec mode + Full-Python mode) grounds the LLM **only on the ~25 columns `precompute_all_indicators` emits**, and instructs it: *"if the source mentions an indicator not in this list, you CANNOT map it — put it in `fidelity.couldnt_map`."* The model then **emits a strategy anyway** with the unmappable rules silently dropped. The user installs a **proxy that quietly omits its core logic**.

This was demonstrated live: an ICT *Fair Value Gap* strategy was reduced to a momentum proxy using the `fvg` direction flag + `vel_z`, with six core rules (CE level, liquidity sweep, premium/discount, order-block proximity, FVG re-entry) dumped into `couldnt_map`. The fidelity readback was honest about *what was dropped*, but the product still shipped the degraded artifact as if it were the strategy.

Two root causes, both verified in code:
1. **The grounding catalog has no vocabulary for structure.** `fvg` is direction-only (`"UP"/"DOWN"/None`) — no top/bottom boundary levels. `is_swing_high/is_swing_low` are *booleans*, not the swing *price* levels. `fibonacci_levels()` exists as a function, not a column. So almost every ICT/SMC concept is "structure detected, but the *level* needed to trade it is missing."
2. **There is no fidelity *gate*, only a fidelity *readback*.** When a core rule can't be mapped, the flow installs a proxy instead of stopping to ask/advise/reject.

**The goal:** (a) extend the feature engine with the missing structural features **without** burdening backtest/optimizer/paper or degrading the warehouse; (b) a principled feasibility boundary that **rejects-with-explanation** when something truly can't be built; (c) a **collaborative, capability-aware authoring agent** that introspects app capability + stored-data limits, asks the user for more info when needed, advises on engine limits, and can generate code outside the one-shot window.

**Threat model / framing:** single-user local tool. The risk is *silent degradation* (a proxy shipped as the real strategy), not a remote attacker. The fix is an explicit, explained decision at every gap — never an unattended substitution.

---

## 2. Scope & decisions (locked)

**Roadmap:** v1 = **Approach B (gate-early, staged)**; then **Phase A** broadens the feature library to the remaining buildable archetypes. (User decision.)

**Locked decisions:**
- **Live correctness (Q1 → unify ctx):** unify the `ctx` contract across backtest / paper-live / smoke so structural strategies run identically everywhere, and **deploy structural strategies live** when their history fits the live window. Features that cannot be made live-correct (session-anchored or unbounded-history) are **declared backtest-only** and the agent refuses to deploy them live, with explanation.
- **Criticality ownership (Q2 → LLM proposes, user overrides):** the LLM proposes per-rule `CORE`/`OPTIONAL`; the user can override via a checkbox. This is the one place human judgment about "what makes this strategy *this* strategy" lives.
- **"Build without it" escape hatch (Q3 → explicit consent):** when a CORE rule needs an unbuildable feature/data, building the mappable subset requires an **explicit per-rule consent click**, defaulting to queue/cancel. Anything looser is today's silent degradation with a nicer label.
- **Param-keyed features (Q4 → param-independent only in v1):** all v1 features have `param_keys=()`. This sidesteps the entire optimizer `_indicator_key` cache-desync risk class. Param-keyed features are Phase A, gated by an invariant test.
- **Out-of-app authoring (Q5 → in-wizard now, export Phase 2):** v1 = a multi-turn collaborative loop inside the New Strategy wizard. The capability-gap report + scaffolded-stub export (paste into Full-Python / edit in Claude Code, re-import via the existing Validate→Install) is Phase 2 — it composes for free on the existing gate.

**Findings folded in as first-class work** (from the adversarial critique):
- **F1 (ctx contract drift):** the `ctx` passed to `evaluate()` is **inconsistent across the three paths** — backtest sets `i` per-bar (in the loop, `backtest.py:179`) and calls `session_precompute`; **`deployment_evaluator` never calls `session_precompute`** (and lacks `instrument`/`session_date`); the smoke driver has **neither `history_df` nor `i`** and never calls `session_precompute`. The existing `session_precompute` builtins (ORB/gap/scenario-routed) carry a per-bar `history_df` fallback (verified by `test_session_precompute_parity.py::test_session_open_fallback_matches_reference`), so they stay *correct* in live but lose their O(1) fast path (a per-tick perf regression). The real blockers SP-0 closes: **(a)** a NEW structural strategy that computes only in `session_precompute` (no fallback) gets `{}` and degrades live; **(b)** the smoke gate can't validate any structural Full-Python strategy because its ctx has no `history_df`/`i`/`session_precompute` — so the "Full-Python absorbs structure" boundary is currently **unexercised (a hollow gate)**.
- **F2 (live-correctness landmine):** `deployment_evaluator` recomputes over a fixed **200-bar window (~½ session) that straddles the session boundary**. Any *session-anchored* feature would compute over *yesterday's* bars for the first ~3 hours of a session → **silently wrong live signals**. The feasibility boundary gains a **`session_anchored`** axis; such features are live-correct only with a session-aware loader, else backtest-only.

**Out of scope (this whole project):** deploy-time/runtime sandboxing of installed plugins (unchanged from Part 2); warehouse feature persistence; multi-leg option-spread P&L; ingesting new data classes (OI/IV/greeks history, L2/tape, news). See §12.

---

## 3. Architecture — three layers, one source of truth

```
┌─────────────────────────────────────────────────────────────────────┐
│  AUTHORING (SP-4)  — the router ABOVE both existing authors;         │
│  never installs through a gap.                                       │
│   source ─► classify_rules(source, capability_report, mode)  (1 LLM) │
│                 └─► RuleSet (each Rule: criticality CORE/OPTIONAL,    │
│                              class ∈ 5 states)                        │
│             aggregate_gate(ruleset)   ── PURE, no LLM ──►             │
│               BUILD │ ASK │ ADVISE │ REJECT                          │
│                 │  on BUILD → existing map_source_to_spec OR          │
│                 │            author_python → validate/smoke → Install │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ grounded on
┌──────────────────────────────▼──────────────────────────────────────┐
│  CAPABILITY SURFACE (SP-3)  — capability_report() = 3 live sources:  │
│   1. indicator_columns (existing grounding, incl. always-on geometry)│
│   2. FEATURE REGISTRY (new — name, columns, kind, requires,          │
│      cost_class, min_history_bars, session_anchored, live_feasible,  │
│      data_requirements)                                              │
│   3. warehouse data-limits manifest (1m OHLCV + option candles only) │
│   → feeds BOTH the LLM prompt AND the pure feasibility checker (R1–R9)│
└──────────────────────────────┬──────────────────────────────────────┘
                               │ materialized by
┌──────────────────────────────▼──────────────────────────────────────┐
│  FEATURE FRAMEWORK (SP-1/SP-2)  — opt-in, declared via               │
│  StrategyBase.required_features = [...]                              │
│   resolve_features() topo-sorts the DAG → materialize_features()     │
│   runs AFTER the indicator frame at all 4 call sites, GATED on a     │
│   non-empty declaration (empty ⇒ no-op early return ⇒ byte-identical)│
└──────────────────────────────┬──────────────────────────────────────┘
                               │ runs on
┌──────────────────────────────▼──────────────────────────────────────┐
│  UNIFIED ctx CONTRACT (SP-0)  — identical ctx keys across            │
│  backtest / deployment_evaluator / smoke driver:                     │
│   { history_df, i, instrument, session_date } + merged               │
│   session_precompute(df, params)                                     │
└──────────────────────────────────────────────────────────────────────┘
```

**The invariant that justifies the whole project:**
> **No artifact is installed while a CORE rule is in {AMBIGUOUS, MAPPABLE_NEW, NEEDS_DATA, INFEASIBLE}.** Every CORE rule ends MAPPED or explicitly-consent-dropped before compile/author runs.

This replaces today's silent `couldnt_map` proxy.

---

## 4. SP-0 — Unify the `ctx` contract (the true prerequisite)

**Problem (verified):** the dict passed as `ctx` to `evaluate()` differs across the three execution paths:

| Path | File | ctx keys today | `session_precompute` called? |
|---|---|---|---|
| Backtest | `backtest.py:~112` + `:179` | `history_df`, `instrument`, `i` (set per-bar at :179) | **Yes** |
| Paper/Live | `deployment_evaluator.py:~370` | `history_df`, `i` | **No** |
| Smoke-test | `_py_smoke_driver.py:~56` | `instrument`, `mode`, `session_date` | **No** |

So a strategy reading `ctx["i"]` works live but KeyErrors in backtest; one reading `ctx["history_df"]` works in backtest+live but KeyErrors in smoke (the gate that's supposed to *prove* it runs). The "Full-Python absorbs structure via `session_precompute`/`history_df`" boundary is **not exercised by the existing smoke gate** — it's hollow.

**Target — one canonical ctx, identical in all three paths:**

```python
ctx = {
    "history_df": df_enriched,     # full enriched frame up to & incl. current bar
    "i": <int current row index>,  # position in history_df
    "instrument": instrument,      # "NIFTY" | "BANKNIFTY" | "SENSEX"
    "session_date": <str>,         # current bar's session date
    # + merged result of strategy.session_precompute(df_enriched, params)
}
```

**Changes:**
1. `backtest.py` — add `"i"` (already has the loop var) + `"session_date"` to `ctx_global`/per-bar ctx. (Already calls `session_precompute`.)
2. `deployment_evaluator.py` — add `ctx.update(strategy.session_precompute(df_enriched, merged_params))` before `evaluate()`, plus `"instrument"`, `"session_date"`. This **restores the O(1) `session_precompute` fast path** in live for ORB/gap/scenario builtins (today they fall back to a slower per-bar derivation) **and makes structural-only strategies viable live** (they no longer need to carry a fallback).
3. `_py_smoke_driver.py` — build a real `history_df` (the synthetic frame it already constructs), pass a real `i`, call `session_precompute`, and include `instrument`/`session_date`. So a structural Full-Python strategy that reads `ctx["history_df"]`/`session_precompute` output **actually executes under smoke**.

**Gate (⛯ parity):** a cross-path test asserting that for the **same bar + same strategy**, the ctx built by the live path (now with `session_precompute` maps merged) yields an **identical `Signal`** to the per-bar `history_df`-fallback ctx, for the ORB/gap/scenario builtins. This is the de-risking deliverable: it is independently valuable (closes the hollow smoke gate for structural Full-Python, restores the live fast path, and guarantees backtest/live ctx parity) even if nothing structural is ever built.

**Note on F2 (live window):** SP-0 unifies the *contract*; it does **not** by itself make session-anchored features live-correct, because the live `history_df` is still the rolling 200-bar window. That correctness boundary is handled by the `session_anchored` registry axis (§6.3) — session-anchored features are declared backtest-only unless/until the live loader is changed to "today's session so far" (Phase A).

---

## 5. SP-1 — Feature framework infrastructure (zero features)

### 5.1 The registry (sibling to indicator groups, reusing the same machinery)

Indicators are an ordered registry of `IndicatorGroup` consumed by a two-tier cache (`indicator_groups.enrich_with_cache`). We **do not** build a second cache system; we add a structurally-identical sibling that adds what *structural* features need.

```python
# app/features/registry.py  (NEW)
@dataclass(frozen=True)
class FeatureGroup:
    name: str                      # "fvg_zones", "swing_levels", ...
    columns: tuple[str, ...]       # exact output column names (grounding + parity)
    param_keys: tuple[str, ...]    # v1: always ()  (Q4 param-independent only)
    requires: tuple[str, ...]      # other FeatureGroup names this depends on (DAG)
    cost_class: str                # "vectorized" | "session_loop"
    session_anchored: bool         # True ⇒ needs a full/prior session ⇒ live caveat (F2)
    stateful_unbounded: bool       # True ⇒ carry-forward selection depends on history
                                   #   older than the live window (FVG active-gap, OB carry,
                                   #   CHoCH) ⇒ live forward pass may miss zones (F2)
    min_history_bars: int          # max trailing bars needed for correctness
    compute: Callable[[pd.DataFrame, dict], dict[str, pd.Series]]
    causal: bool = True            # invariant; asserted by tests, never False
    # live_feasible is DERIVED, not stored: a feature is live-feasible iff
    #   (not session_anchored) and (not stateful_unbounded) and
    #   (min_history_bars <= LIVE_WINDOW_BARS). capability_report() computes it.

FEATURE_REGISTRY: dict[str, FeatureGroup] = {}
```

Features live in a **later lifecycle stage than indicators**: they compute **after** the indicator frame is assembled, so a feature's `compute` may read any indicator column plus any feature column listed in its `requires`. The indicator path is 100% untouched (the byte-identical guarantee for non-declaring strategies falls out for free).

### 5.2 The `required_features` declaration

```python
class StrategyBase:
    ...
    required_features: List[str] = []   # default empty = the entire back-compat story
```

- Every built-in + every existing plugin declares nothing → empty list → **zero new columns, zero behavior change.**
- `meta()` adds `"required_features": self.required_features`.
- A `required_features` literal list of strings passes Part-2 `static_check` (`_is_literal` already handles `List`/`Tuple` of `Constant`) with **no sandbox/allowlist change** — it is data, not code.

```python
# app/features/registry.py
def resolve_features(required: list[str]) -> list[FeatureGroup]:
    """Closure over `requires` edges, then topo-sort the DAG.
    Raises FeatureError('unknown_feature', name=..., available=[...]) on an
    undeclared name — the feasibility boundary's first gate."""

def materialize_features(df, params, required, feature_caches, *, max_per_group=4) -> pd.DataFrame:
    """Mirror enrich_with_cache's contract. Copies once on first feature write
    (df is the already-enriched frame; don't mutate the indicator cache's frame).
    Empty `required` ⇒ caller never invokes this (no-op at the call site)."""
```

### 5.3 The four call sites (additive; no-op on empty declaration)

Every site already produces `df_enriched` (indicators + regime). Feature materialization is a single post-step **guarded on a non-empty declaration** — when empty it is not even called (same object identity, same column set).

```python
df_enriched = precompute_all_indicators(df, merged_params)
df_enriched["regime"] = classify_regime_series(df_enriched)
if strategy.required_features:                         # empty -> untouched
    df_enriched = materialize_features(
        df_enriched, merged_params, strategy.required_features, _feature_cache)
```

**Refinement discovered during SP-1 implementation:** for the **backtest path**, materialization lives **inside `run_backtest` itself** (right after `df.reset_index`, before the row-dict pre-materialization), with a **fresh `{}` cache per call** — NOT in the callers. This is strictly safer and simpler than the original call-site + shared-cache plan: (a) every `run_backtest` caller (the one-shot `runtime.py` path AND the optimizer trials) gets features automatically with no per-caller wiring and no chance of a caller forgetting; (b) computing features fresh on the exact enriched frame each call **eliminates a cross-trial staleness bug** the shared `_feature_caches` would have risked — structural features read indicator columns (e.g. `displacement` reads `atr`), so a feature cached under `param_keys=()` but reused across trials with different `atr_length` would be silently stale. The cost is per-trial re-materialization in the optimizer; acceptable for v1 (cheap, mostly-vectorized features). If a heavy `session_loop` feature later proves slow under optimization, the fix is to key that feature on its transitive indicator params (mirroring `INDICATOR_PARAM_KEYS`) — deferred until measured. The **live path** (`deployment_evaluator`) does not use `run_backtest`, so it materializes itself before `build_live_eval_ctx`.

| Site | File | Materialization |
|---|---|---|
| Backtest (all callers) | inside `run_backtest` (`backtest.py`) | fresh `{}` per call; every caller incl. the optimizer gets features free — **no caller-side materialize** (so `runtime.py` does NOT materialize; that would double-materialize). |
| Optimizer | `optimizer.py` `get_enriched` | **no feature wiring needed** — `run_backtest` owns it (SP-2b just confirms parity; revisit per-trial cost only if a heavy feature is measured slow). |
| Grounding | `grounding.py:~37` | materialize **all** registered features on the sample frame to advertise their columns + metadata (the `couldnt_map` fix — the agent now *sees* `fvg_top`/…). |
| Paper/Live | `deployment_evaluator.py:~344` | materialize on the 200-bar frame before `build_live_eval_ctx`; a process-level last-frame cache keyed `(deployment_id, last_closed_bar_ts)` is optional. **Note (critique M3):** the existing idempotency guard already short-circuits a re-tick of the same closed bar, so the effective compute rate is **once per new closed bar per deployment** regardless. |

### 5.4 Where the code lives

```
app/features/
  __init__.py
  registry.py     # FeatureGroup, FEATURE_REGISTRY, resolve_features, materialize_features, FeatureError
  structures.py   # the seed compute fns (causal/vectorized); reuse indicators.detect_fvg/detect_swing_points/fibonacci_levels
  catalog.py      # FEATURE_CATALOG metadata (descriptions, value domains, cost_class, data_requirements) for grounding/AI
```
`structures.py` imports only `pandas`/`numpy`/`app.indicators` — host-importable, no motor, no I/O (same discipline as `indicator_groups.py`).

### 5.5 Byte-identical / back-compat (⛯ gate)

1. **No new global columns from features.** `precompute_all_indicators` / `indicator_groups.GROUPS` are **untouched** by the framework, so `test_indicator_equivalence.py` (monolithic vs cached golden parity) stays green by construction.
2. **No-op on empty declaration.** Every call site guards `if strategy.required_features:`. Existing strategies → `materialize_features` never called → identical DataFrame object → identical trades. Same property `session_precompute` already relies on (base returns `{}`).
3. **Optimizer cache untouched for non-declaring strategies** (`_indicator_key` unchanged; `_feature_caches` empty).
4. **New parity discipline mirrors the indicator one:** `tests/test_feature_equivalence.py` (golden reference vs registry path, byte-identical) + `tests/test_feature_causality.py` (truncating future bars must not change feature[i]) + a dup-ts/ordering fuzz battery (the `_walk_option_exit` precedent).

---

## 6. SP-2 — Seed features (split by cost)

### 6.1 Always-on additive columns (trivial per-bar, O(1)) — NOT through the framework

Per the critique (H3): pure 1-bar geometry needs no carry-forward, no DAG, no live budget. Adding them through `required_features` is over-engineering. They go in as a **normal param-independent `IndicatorGroup` appended last** in `indicator_groups.GROUPS` (cheaper than `rsi`), so they're available **in Spec mode for free** and stay byte-identical for existing strategies (additive columns no existing strategy references; both monolithic and cached paths emit them identically).

- `body_frac`, `upper_wick_frac`, `lower_wick_frac` — candle geometry (pin bar / hammer).
- `inside_bar` — 2-bar range containment.
- `close_z` — rolling price z-score (mirrors `velocity_accel`; window ≤ 60).
- *(optional)* surface existing band math as columns: `bb_upper`/`bb_lower` (from `bollinger`), `keltner_upper`/`keltner_lower` (from `keltner`), `donchian_high`/`donchian_low`.

⛯ Validate the "additive is byte-identical" claim once: full golden suite + existing-strategy-trade-identity.

### 6.2 Opt-in structural features (stateful) — through the framework

All causal (trailing-window only), parity-tested, NaN/`None` until enough history. Reuse `detect_fvg`/`detect_swing_points`/`fibonacci_levels`.

**`swing_levels`** (foundation; `cost_class=vectorized`, `session_anchored=False`)
- `last_swing_high_level`, `last_swing_low_level` — forward-fill of `high`/`low` at existing `is_swing_*` flags, **`.shift(1)`'d** so a bar can't sweep the level it just set.
- `prev_swing_high_level`, `prev_swing_low_level` — the swing before the current (for BOS). *(Critique L2: BOS could derive these from a shift of the carried level; keep only if it materially simplifies — decide at impl.)*
- `swing_high_swept`, `swing_low_swept` (bool) — `high > last_swing_high_level.shift(1)` / `low < last_swing_low_level.shift(1)`.

**`fvg_zones`** (`cost_class=session_loop`, `session_anchored=False`, but **stateful-unbounded** — see §6.3)
- `fvg_top`, `fvg_bottom` (high/low-ordered boundaries from the 3-candle imbalance), `fvg_ce` = midpoint, `fvg_dir`, `fvg_state` ∈ {active, filled, none}. Detection vectorized via `detect_fvg`; the active-gap carry + fill is a single O(N) forward pass over numpy arrays.

**`premium_discount`** (`cost_class=vectorized`, `requires=["swing_levels"]`, `session_anchored=False`)
- `premium_discount_pct` = `100*(close - last_swing_low_level)/(last_swing_high_level - last_swing_low_level)` using shifted levels (causal); `range_state` ∈ {premium, discount, equilibrium}.

**`displacement` + BOS/CHoCH** (`requires=["swing_levels"]`)
- `displacement` (bool, vectorized): `abs(close-open) >= disp_atr_mult*atr` and body fraction ≥ threshold.
- `bos_up`/`bos_down` (bool, vectorized): `close > prev_swing_high_level.shift(1)` / `<` low.
- `choch_up`/`choch_down` (bool, `session_loop`): sign-flip of the running structural direction (small forward pass).

**`order_block`** (`cost_class=session_loop`, `requires=["displacement"]`, **stateful-unbounded**)
- `ob_top`, `ob_bottom`, `ob_dir`, `ob_active`. Identify the last opposing candle before displacement within a **bounded** trailing `ob_lookback` (default 10, hard-max 20 — never an O(N²) per-bar lookback); carry-forward + mitigation is a forward pass.

**Dependency DAG:** `swing_levels` → {`premium_discount`, `displacement`}; `displacement` → `order_block`; `fvg_zones` independent. `resolve_features` topo-sorts.

### 6.3 Live-feasibility classification (the F2 axis)

Each feature carries `session_anchored: bool` **and** a `stateful_unbounded` property (carry-forward selection that depends on history older than the live window). The live correctness rule:

- **vectorized + `min_history_bars ≤ ~150` + not session-anchored** → live-correct on the 200-bar window. Deployable.
- **session-anchored** (needs "this session's range / opening range / prior session") → on the 200-bar window in the first ~3 hours the window is mostly *yesterday's* bars → **silently wrong** → **declared backtest-only** in v1 (agent refuses live deploy; explanation). Made live-correct only by a session-aware loader (Phase A).
- **stateful-unbounded** (`fvg_zones` active-gap selection, `order_block` carry, `choch`): the zone may have been *established* before the 200-bar window → live forward pass never sees it → live reports "no active zone" while backtest does. **v1: classify these `live_feasible=False` (backtest-only) OR require explicit state-seeding (Phase A).** SP-0's ctx unification is necessary but **not sufficient** for these — do not claim it fixes them.

This axis is what lets the agent say *"I can build the FVG-zone version and backtest it; it is not live-correct on the current rolling window, so I'll mark it backtest-only — here's why"* instead of shipping a wrong live signal.

---

## 7. SP-3 — Capability surface + feasibility classifier

### 7.1 `capability_report()` (in `app/ai/grounding.py`)

Three live sources composed into one object fed to BOTH the LLM prompt and the pure checker:
1. **Columns** — `build_grounding_catalog()["indicator_columns"]` + raw OHLCV (incl. always-on geometry). Drives `MAPPED`.
2. **Feature registry** — `FEATURE_REGISTRY` metadata (name, columns, kind, requires, cost_class, `session_anchored`, `min_history_bars`, `live_feasible`, `data_requirements`). Drives `MAPPABLE_WITH_NEW_FEATURE` vs `INFEASIBLE`.
3. **Warehouse data-limits manifest** — static: `{has_1m_ohlcv, has_option_candles, has_per_strike_greeks_history: false, has_oi_history: false, has_l2_depth: false, has_tick_orderflow: false, date_range, instruments}`. Drives `NEEDS_NEW_DATA`.

The classifier **never invents** — the prompt enumerates the closed vocabulary (columns) **plus** the buildable features **plus** known data gaps, so it routes a rule to the correct branch instead of dumping into `couldnt_map`.

### 7.2 `allowed_columns()` becomes feature-aware (critique C3 — required, non-trivial)

Today `allowed_columns()` = `set(build_grounding_catalog()["indicator_columns"])` + raw OHLCV — a flat set. If grounding materializes **all** features on the sample frame (so the agent sees them), a flat `allowed_columns()` would let the Spec compiler validate a spec referencing `fvg_top` **even when `required_features` is empty** → a Spec strategy that references a column the engine never computes for it → KeyError/silent-NaN at run time.

**Resolution:** `allowed_columns(required_features=())` returns base columns ∪ the **declared** features' columns. Threaded through:
- `compiler.py` `validate_spec`/`_validate_condition` — take the spec's declared feature set; only allow a feature column when declared.
- `_py_smoke_driver.py` — build the synthetic frame from `allowed_columns(strategy.required_features)` so a structural Full-Python strategy's columns exist under smoke (and a *fabricated* column still fails).
- `grounding.py` — `capability_report()` advertises **all** features (for the agent to map to), but `allowed_columns` stays declaration-scoped (for validation). Advertise ≠ allow.

### 7.3 The mechanical classifier (pure, no LLM) — `R1–R9`

The agent parses a candidate rule into referenced tokens (`COLS`, `CONCEPTS`, `BARSPAN`, `WINDOW`) and applies, first-match-wins:

| Rule | Condition | Class | Message template |
|---|---|---|---|
| R1 | every COL ∈ `allowed_columns(decl)` **and** BARSPAN ≤ 2 | **BUILDABLE_NOW** | "Buildable now from {cols}." |
| R2 | CONCEPTS ⊆ DATA_BLOCKED {OI, PCR, max-pain, IV-rank, theta, historical greeks, multi-leg vol structure, news, sentiment, order flow, footprint, L2, tape} | **NEEDS_NEW_DATA** | "{concept} needs {named_data}; warehouse stores only 1m OHLCV + option OHLCV. {ingest_note}." |
| R3 | CONCEPT == relative-strength / pairs (2nd instrument frame) | **BUILDABLE_WITH_FEATURE** *(engine-plumbing, Phase A)* | "Needs the other index's aligned bars in ctx — a real engine change (see parked XRS). Feasible-but-nontrivial." |
| R4 | CONCEPT == order-flow / footprint / depth / tape, no historical tick data | **INFEASIBLE** | "Requires tick-level depth/tape 1m bars can't reconstruct. Infeasible to backtest." |
| R5 | CONCEPT ∈ STRUCTURE {FVG-level, OB, breaker, sweep, BOS/CHoCH, premium/discount, OTE, equal-highs, divergence, MTF} | **BUILDABLE_WITH_FEATURE** | "Detectable but the tradeable level isn't a column yet. I'll add {feature}{ via session_precompute}. {live_caveat if session_anchored/stateful_unbounded}." |
| R6 | COLs vectorized-causal-derivable from OHLCV, WINDOW ≤ ~150, column-expressible | **BUILDABLE_WITH_FEATURE** | "One vectorized feature {feature}. Safe in backtest + live." |
| R7 | as R6 but WINDOW > ~150 **or** session-anchored | **BUILDABLE_WITH_FEATURE (+ live caveat)** | "Buildable; exceeds the live window — backtest-correct, live-gated (declared backtest-only)." |
| R8 | needs >2 bars, expressible only via `ctx.history_df` | **BUILDABLE_WITH_FEATURE (Full-Python only)** | "Exceeds Spec's 2-bar window. Full-Python via the history frame, or a small *_2ago column." |
| R9 | default / unrecognised, no OHLCV mapping | **INFEASIBLE** | "Can't map {concept} to anything derivable from 1m OHLCV. Give the precise calc or it's out of scope." |

**Deliverable:** a **host-importable, unit-tested classifier that needs no LLM** — the deterministic half of the gate. **Ships after ≥1 real feature exists** (critique M1): a classifier on an empty registry rejects the flagship ICT case it's meant to enable. So SP-3's *schema* is co-designed with SP-2; its *classifier* lands after SP-2's first features.

---

## 8. SP-4 — Collaborative authoring agent (the gate + wizard)

### 8.1 The Rule + RuleSet

Decompose source (text + optional Pine Script + optional transcript) into ordered **Rules**:

```
Rule { id, text, kind ∈ {ENTRY,EXIT,FILTER,GATE,SIZING,SESSION,META},
       criticality ∈ {CORE, OPTIONAL},        # LLM proposes; user overrides (Q2)
       class ∈ {MAPPED, MAPPABLE_WITH_NEW_FEATURE, NEEDS_NEW_DATA, AMBIGUOUS, INFEASIBLE},
       evidence, proposal?(feature descriptor), question?(for AMBIGUOUS) }
```

Class precedence (most-blocking wins): `INFEASIBLE > NEEDS_NEW_DATA > MAPPABLE_WITH_NEW_FEATURE > AMBIGUOUS > MAPPED`.

### 8.2 The fidelity gate (pure `aggregate_gate`, no LLM) — 4 outcomes

Aggregate over **CORE rules only**:
- **BUILD** — every CORE rule MAPPED → hand off to the **existing** `map_source_to_spec` (Spec) or `author_python` (Full-Python) → existing `validate_spec` / `static_check`+`smoke_test` → Install. OPTIONAL non-mapped rules reported, don't block.
- **ASK** *(HITL mandatory)* — a CORE rule is AMBIGUOUS (nothing harder-blocked). Return ≤3 targeted structured questions; may also request artifacts ("paste the Pine Script"). Answers re-enter classification.
- **ADVISE** *(HITL mandatory at the decision)* — a CORE rule is MAPPABLE_NEW or NEEDS_DATA. Per blocked rule: `[Queue feature]` / `[Build without it]` (explicit consent, Q3) / `[Cancel]`. **Never auto-pick build-without.**
- **REJECT** *(terminal for this attempt)* — a CORE rule is INFEASIBLE. Specific explanation citing the contract violation; always offers the **mappable-subset fallback** ("build the 3 of 5 feasible rules?" → ASK/BUILD on survivors, dropped rules recorded loudly in the description + fidelity).

**Determinism (critique M2):** classify the RuleSet **once**; subsequent rounds **augment** with answers/decisions rather than re-decomposing, so the gate can't flip BUILD↔ADVISE on identical core logic between rounds.

### 8.3 Mode composition (the principled Spec vs Full-Python boundary)

The classifier takes `mode` as input and yields a **different outcome per mode**:
- **Spec mode** — BUILD only if all CORE rules map to the scalar Condition/op/column vocabulary. A CORE rule needing structure routes to ADVISE *or* a mode-upgrade suggestion ("this needs custom logic — switch to Full Python?"). Spec stays scalar-only; zone/list structures are never expressed in the DSL.
- **Full-Python mode** — BUILD can additionally absorb multi-bar structure **derivable in `session_precompute`/`ctx.history_df`** *without* a registry feature (e.g. swing-price levels from existing booleans + raw OHLC). Those reclassify MAPPABLE_NEW → MAPPED **for Full-Python only**. A **shared registry feature is needed only** when the structure must be cached/reused across strategies, is too heavy for per-strategy recompute, **or must be live-feasible**.

### 8.4 Backend + wizard

```
POST /strategies/author/converse
  body: { session_id?, source_text, attachments?{pine?, transcript?},
          answers?[{rule_id,value}], decisions?[{rule_id, action: queue|drop|keep}],
          mode: "spec"|"python", provider? }
  → { session_id, outcome: BUILD|ASK|ADVISE|REJECT, rules[Rule...],
      questions[...], advisories[...], artifact?{spec?|code?}, capability_gaps[...] }
```
Internals: `classify_rules` (1 structured LLM call → RuleSet) → pure `aggregate_gate` → on BUILD, existing author + existing validate/smoke. State lives client-side in the transcript; backend stays functionally pure (host-safe style).

**Wizard (`AuthoringWizard.jsx`):** replace the passive `captured/couldnt_map/ambiguous` fidelity block with the **classed RuleSet panel** — action rows (`[Queue feature]`/`[Build without]`/answer controls), per-rule `criticality` checkbox, a **multi-turn transcript**, and **Install gated on `outcome==BUILD`** (disabled with a tooltip otherwise). Keep the Spec/Full-Python toggle; route BUILD to the matching existing install path. Re-run the 36-evasion red-team on any sandbox/prompt change.

### 8.5 Human-in-the-loop (where consent is mandatory)

| Moment | HITL |
|---|---|
| Set `criticality` per rule | optional (model proposes, user overrides) |
| ASK — answer clarifying questions | **mandatory** |
| ADVISE — "build without it" consent | **mandatory** (the anti-silent-degradation gate) |
| REJECT — accept mappable-subset fallback | **mandatory** to proceed |
| Final Install | **mandatory** (unchanged Validate→Install) |

The model **never** auto-installs through a gap.

---

## 9. Cross-system impact (matrix)

| Subsystem | Change | Risk | Mitigation |
|---|---|---|---|
| **Backtest (`runtime.py`/`backtest.py`)** | SP-0 ctx keys; feature post-step | LOW — look-ahead leakage in a feature | causal-by-construction + per-feature look-ahead test + parity test |
| **Optimizer (`optimizer.py`/`indicator_groups.py`)** | sibling `_feature_caches`; geometry as a param-independent group | MED — `_indicator_key` desync **(avoided in v1: param_keys=())**; the existing import-time guard already enforces indicator-group `param_keys ⊆ INDICATOR_PARAM_KEYS` | param-independent only (Q4); features kept OUT of `enriched_cache` (H2); keep the guard |
| **Paper/Live (`deployment_evaluator.py`)** | SP-0 `session_precompute`+ctx; opt-in feature post-step; last-frame cache | **HIGH** — F1 parity gap; per-tick cost; session/stateful live-wrongness (F2) | SP-0 + cross-path parity test; opt-in gating (non-declaring deployments pay zero); `session_anchored`/`stateful_unbounded` → backtest-only |
| **Warehouse (`warehouse.py`)** | **none** — `persist_candles_df` is a hardcoded 8-field `$set`; no column can reach Mongo | LOW — a future `df.to_dict` "optimization" would leak | regression test: enrich-all-features → persist → assert exactly 8 OHLCV fields + a load-bearing comment |
| **Grounding (`grounding.py`)** | `capability_report()` (3 sources); advertise feature columns + metadata | MED — agent blind to features = the original problem | single host-importable registry feeds prompt + checker + docs; `_sample_frame` long enough for every feature's `min_history_bars` |
| **Spec compiler (`compiler.py`)** | `allowed_columns(required_features)` feature-aware (C3) | MED — flat allowlist would validate undeclared feature cols | declaration-scoped allowlist; ctx structures explicitly out of Spec |
| **Part-2 sandbox (`py_sandbox.py`)** | smoke-frame builder injects declared feature columns + real ctx; **no allowlist change** | MED — smoke false-fail on feature refs; `required_features` attack surface | feature-aware smoke frame; `required_features` is literal data (passes `_is_literal`); re-run 36-evasion battery |
| **Docs (`STRATEGY_PLUGINS.md`)** | document `required_features`, column-vs-ctx features, causality, live boundary | LOW — doc drift | generate the feature section FROM the registry (or a test asserting doc ⊇ registry) |
| **Back-compat** | additive-only, opt-in | MED — unconditional features would break golden parity | empty declaration = no-op; geometry additive-and-byte-identical; full golden suite gates every phase |

### Top risks (ranked)
1. **Backtest↔live parity / live-wrongness for structural strategies** (F1+F2). → SP-0 + parity test + `session_anchored`/`stateful_unbounded` backtest-only gating, before any structural feature ships live.
2. **Optimizer cache staleness** — avoided in v1 by param-independent-only + keeping features out of `enriched_cache`; the import-time guard stays.
3. **Per-tick live cost regression** — opt-in gating + vectorized features + once-per-closed-bar reality; measure before enabling.
4. **Warehouse leak via future refactor** — lock with the exactly-8-fields test.
5. **Agent still blind to features** — the single registry as source of truth for prompt + checker + docs.

---

## 10. Build sequence (corrected by the critique) + parity gates ⛯

1. **SP-0 — Unify ctx contract** across backtest/live/smoke. ⛯ cross-path Signal-parity test. *Independently shippable; fixes a real latent bug.*
2. **SP-1 — Framework infra, zero features** (`app/features/*`, `required_features`, no-op wiring, separate `_feature_caches`). ⛯ full golden parity + all existing strategies byte-identical.
3. **SP-2a — Always-on geometry columns** (a param-independent indicator group). ⛯ additive-byte-identical + monolithic-vs-cached parity.
4. **SP-2b + SP-3 schema (co-designed)** — first structural features (`swing_levels`, `fvg_zones`, `premium_discount`) through the framework, registry schema populated. ⛯ per-feature parity ⛯ look-ahead ⛯ live-feasibility classification ⛯ smoke-frame feature-aware ⛯ warehouse-no-leak.
5. **SP-3 classifier** — `capability_report()` + pure `R1–R9` classifier + `allowed_columns(required_features)` feature-aware (C3). Unit-tested without an LLM.
6. **SP-4 — Collaborative agent + wizard** — `classify_rules` + pure `aggregate_gate` + `/author/converse` + RuleSet panel + Install-gated-on-BUILD. ⛯ re-run 36-evasion red-team.
7. **SP-2c — remaining structural features** (`displacement`/BOS/CHoCH, `order_block`) once 4–6 prove the path.

Each step gates on its ⛯ parity test before merge (the codebase's byte-identical discipline).

---

## 11. Phase A (after v1)

- Broaden the feature library to the remaining `BUILDABLE_WITH_FEATURE` archetypes (OTE, equal-highs/liquidity pools, MTF `htf_trend`, Donchian/Keltner/BB explicit, `weekday`/`minute_of_session`, `rsi/macd_divergence`).
- **Session-aware live loader** — replace the fixed 200-bar window with "today's session so far (+ bounded prior context)" so `session_anchored` features become live-correct (lifts the F2 backtest-only gate).
- **Out-of-app export (Q5 Phase 2)** — `CAPABILITY_GAP_REPORT.md` (RuleSet + each blocked rule's class/evidence + the **exact emitted column names per feature** — critique M4) + a scaffolded `StrategyBase` stub (`session_precompute` pre-stubbed for structural rules; `# TODO(rule rX)` markers), edited externally and re-imported via the existing Full-Python Validate→Install. No new validation surface.
- **Param-keyed structural features** — add the param to BOTH the group and `INDICATOR_PARAM_KEYS`; ⛯ the desync invariant test.
- **Cross-instrument relative strength (XRS)** — a real engine change (second aligned frame into ctx); the parked branch already explored it.
- **Async/background authoring** — wrap `/author/converse` as an enqueue-able job that re-runs when a queued feature lands, then notifies.

---

## 12. YAGNI — explicitly NOT building (v1)

- Persisting features to the warehouse / a feature store (provably impossible via the 8-field `$set`; locked by a test).
- A cross-tick indicator cache or a per-tick **budget governor** in `deployment_evaluator` (critique L1 — measure first; opt-in vectorized features + once-per-closed-bar make it unnecessary).
- Param-keyed structural features (Q4).
- Structural zone/list shapes in the Spec DSL (route to Full-Python).
- New sandbox imports / allowlist relaxation (`required_features` is data, not code).
- A second code-execution path "outside the app" (the export re-imports through the *existing* gate).
- Unbounded auto-extension of the live window (cap it; beyond-cap features are backtest-only by declaration).
- `NEEDS_NEW_DATA` features (OI/PCR/max-pain, IV-rank/greeks history, order-flow/L2/tape, news) — **rejected at the feasibility boundary by design**; order-flow/tape is flatly **infeasible** to backtest from 1m bars.
- Cross-instrument RS feature (Phase A).

---

## 13. Testing strategy

- **Parity (golden):** `test_indicator_equivalence.py` (existing, must stay green) + new `test_feature_equivalence.py` (golden reference vs registry path, byte-identical).
- **Causality:** `test_feature_causality.py` — truncating future bars must not change feature[i] (the `detect_swing_points` invariant as a test), per feature.
- **Fuzz:** dup-ts / ordering non-determinism battery (the `_walk_option_exit` precedent) before features reach the live path.
- **Cross-path Signal parity (SP-0):** same bar + strategy → identical Signal in backtest vs deployment_evaluator (ORB/gap builtins).
- **Warehouse lock:** enrich-all-features → `persist_candles_df` → assert exactly 8 OHLCV fields + instrument.
- **Optimizer guard:** keep/extend the import-time `param_keys ⊆ INDICATOR_PARAM_KEYS` assertion.
- **Gate (pure):** exhaustive unit tests of `aggregate_gate` over crafted RuleSets (every outcome, precedence, criticality override) — no LLM.
- **Classifier (pure):** `R1–R9` against the archetype taxonomy fixtures.
- **Smoke (Part-2):** a structural Full-Python strategy validates + runs identically in backtest, live, and smoke (the C1 closure); re-run the 36-evasion battery after any sandbox/prompt change.
- **Back-compat:** full host suite (currently ~2537) green; all existing strategies produce identical trades.

---

## 14. Open questions / risks acknowledged

- **Live session-aware loader is deferred to Phase A**, so several flagship ICT features (`fvg_zones`, `order_block`, anything session-anchored) ship **backtest-only** in v1. This is the honest feasibility boundary, not a workaround — surfaced to the user by the agent at authoring time. If live deployment of these is a v1 must-have, the session-aware loader moves into v1 (re-scope).
- **`stateful_unbounded` features** (`fvg_zones` active-gap, `order_block` carry, `choch`) are backtest-only in v1 even with SP-0, because the live 200-bar window can't see a zone established earlier. Phase-A state-seeding or the session-aware loader lifts this.
- **Classifier accuracy** depends on the LLM's rule decomposition; the pure `aggregate_gate` + per-rule human override + Install-gated-on-BUILD bound the blast radius (a misclassification produces an ASK/ADVISE, not a bad install).
