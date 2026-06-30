# SP-3 — Capability Surface + Feasibility Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the deterministic half of capability-aware authoring — make `allowed_columns()` feature-aware (the C3 fix), add a `capability_report()` that composes the indicator columns + the feature registry + a static warehouse data-limits manifest, and add a pure, host-tested R1–R9 `classify_rule()` feasibility classifier — so the SP-4 agent (next) can route a parsed rule to BUILDABLE_NOW / BUILDABLE_WITH_FEATURE / NEEDS_NEW_DATA / INFEASIBLE without an LLM in the loop for the decision.

**Architecture:** SP-3 is purely additive and LLM-free. (1) `compiler.allowed_columns(required_features=())` gains an optional declared-feature arg and unions in those features' columns via `resolve_features`; `StrategySpec` gains a `required_features` field; `validate_spec` and `_py_smoke_driver` thread it through so a Spec/Full-Python strategy may reference `fvg_top` ONLY when it declared `required_features=["fvg_zones"]` (advertise ≠ allow). (2) A new host-importable `backend/app/ai/capability.py` holds the static `WAREHOUSE_MANIFEST`, `capability_report()` (composes `build_grounding_catalog()` + the manifest), and the pure `classify_rule(tokens, *, required_features=())` R1–R9 classifier with its closed concept taxonomy. No motor, no I/O, no LLM — all unit-tested.

**Tech Stack:** Python 3.12, Pydantic v2 (`StrategySpec`), pytest (host venv — no motor; tests insert `backend/` on `sys.path`).

---

## Background the implementer needs (verified by recon — current code shapes)

**`backend/app/ai/compiler.py`** (the sole column-whitelist gatekeeper):
- `_RAW_OHLCV = {"open", "high", "low", "close", "volume"}` (line 30).
- `allowed_columns() -> Set[str]` (lines 42-52) — **takes no args today**:
  ```python
  def allowed_columns() -> Set[str]:
      from app.ai.grounding import build_grounding_catalog
      cols = set(build_grounding_catalog()["indicator_columns"])
      cols |= _RAW_OHLCV
      return cols
  ```
- `validate_spec(spec)` (line 59) calls `cols = allowed_columns()` (line 62), then `_validate_condition(side, i, c, cols, pnames)`.

**`backend/app/ai/spec_schema.py`** — `StrategySpec` (lines 42-56) has NO `required_features` field. Last field is `exits: ExitSpec = Field(default_factory=ExitSpec)` (line 55).

**`backend/app/ai/_py_smoke_driver.py`** `main()` (line 79): `cols = sorted(allowed_columns())` then `run_smoke(inst, cols)` builds a synthetic frame with exactly those columns. `inst` is the loaded `StrategyBase` instance; `StrategyBase.required_features` exists (SP-1, defaults to `[]`).

**`backend/app/ai/grounding.py`** `build_grounding_catalog()` already returns:
- `indicator_columns` (sorted indicator + regime names),
- `feature_columns` (sorted flat union of all registered `FeatureGroup.columns`),
- `feature_entries` (per-feature dicts: `{feature, columns, needs_declaration, requires, cost_class, session_anchored, stateful_unbounded, min_history_bars, data_requirements, description, live_feasible}`),
- `all_columns_including_features`, `signal_fields`, `strategies`.

**`backend/app/features/registry.py`**: `resolve_features(required) -> List[FeatureGroup]` (dep closure + topo sort); `FEATURE_REGISTRY`; `feature_live_feasible(group)`. The 6 seed features (swing_levels, premium_discount, displacement = live_feasible True; choch, fvg_zones, order_block = live_feasible False) register on `import app.features.catalog`.

**Warehouse data limits** (from `app.data_hygiene`): collections are `candles_1m` (1m spot OHLCV), `options_1m` (1m option candles, ATM±1 band only), `option_contracts` (metadata). `DEFAULT_START_DATE = "2024-11-27"`. The warehouse has NO per-strike greeks history, NO OI time-series history, NO L2 depth, NO tick orderflow, NO VIX time-series. **Do NOT `import app.data_hygiene`** in `capability.py` (it pulls motor — not host-importable); mirror the date as a literal with a comment.

**Test conventions:** each new test module starts with
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
```
Run with `python -m pytest <paths> -q` from the worktree root. Full-suite baseline: ~5 motor failures + 16 motor collection errors (unchanged); run with `--continue-on-collection-errors`.

---

## File Structure

- **Modify** `backend/app/ai/spec_schema.py` — add `required_features: List[str]` to `StrategySpec`.
- **Modify** `backend/app/ai/compiler.py` — `allowed_columns(required_features=())` feature-aware; `validate_spec` passes `spec.required_features`.
- **Modify** `backend/app/ai/_py_smoke_driver.py` — pass `inst.required_features` to `allowed_columns`.
- **Create** `backend/app/ai/capability.py` — `WAREHOUSE_MANIFEST`, `capability_report()`, `FeasibilityClass`, `RuleTokens`, `Verdict`, the concept taxonomy, `classify_rule()`.
- **Create** `tests/test_allowed_columns_feature_aware.py` — the C3 fix (declared vs undeclared feature columns).
- **Create** `tests/test_capability_report.py` — the composed surface + manifest.
- **Create** `tests/test_feasibility_classifier.py` — R1–R9 per-rule unit tests incl. the flagship ICT-FVG case.

---

## Task 1: Feature-aware `allowed_columns()` + `StrategySpec.required_features` (the C3 fix)

**Files:**
- Modify: `backend/app/ai/spec_schema.py`
- Modify: `backend/app/ai/compiler.py`
- Modify: `backend/app/ai/_py_smoke_driver.py`
- Test: `tests/test_allowed_columns_feature_aware.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_allowed_columns_feature_aware.py`:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.features.catalog  # noqa: F401  -> registers seed features

from app.ai.compiler import allowed_columns, validate_spec
from app.ai.spec_schema import StrategySpec, Condition


def test_base_columns_exclude_feature_columns():
    base = allowed_columns()
    assert "close" in base and "rsi" in base
    assert "fvg_top" not in base          # advertise != allow: undeclared feature col is NOT allowed
    assert "last_swing_high_level" not in base


def test_declared_feature_columns_are_allowed():
    cols = allowed_columns(["fvg_zones"])
    assert "fvg_top" in cols and "fvg_bottom" in cols
    assert "close" in cols                # base still present


def test_declared_feature_pulls_dependency_columns():
    # order_block requires displacement requires swing_levels -> all their cols allowed
    cols = allowed_columns(["order_block"])
    assert "ob_top" in cols
    assert "displacement" in cols              # dependency feature's column
    assert "last_swing_high_level" in cols     # transitive dependency


def test_validate_spec_rejects_feature_col_when_undeclared():
    spec = StrategySpec(
        id=" x".strip().replace(" ", "") or "t1", name="t1",
        entry_ce=[Condition(left="close", op=">", right="fvg_top")],
    )
    errors = validate_spec(spec)
    assert any("fvg_top" in e for e in errors)   # not declared -> invalid reference


def test_validate_spec_accepts_feature_col_when_declared():
    spec = StrategySpec(
        id="t2", name="t2", required_features=["fvg_zones"],
        entry_ce=[Condition(left="close", op=">", right="fvg_top")],
    )
    errors = validate_spec(spec)
    assert not any("fvg_top" in e for e in errors)   # declared -> reference is valid
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_allowed_columns_feature_aware.py -q`
Expected: FAIL — `StrategySpec` has no `required_features` (TypeError/validation) and/or `allowed_columns()` takes no args.

- [ ] **Step 3: Add `required_features` to `StrategySpec`**

In `backend/app/ai/spec_schema.py`, inside `StrategySpec`, add a field (place it right after `exits`):
```python
    required_features: List[str] = Field(default_factory=list)  # opt-in structural features
```

- [ ] **Step 4: Make `allowed_columns()` feature-aware**

In `backend/app/ai/compiler.py`, replace `allowed_columns` with:
```python
def allowed_columns(required_features: "list | tuple" = ()) -> Set[str]:
    """Whitelist of columns a Condition may reference.

    = grounding-catalog indicator columns (computed indicators + regime) + raw
    OHLCV, PLUS the columns of any DECLARED structural features (and their
    dependency closure). Advertise != allow: a feature column is only allowed
    once the strategy declares the feature in required_features, so a Spec can't
    reference fvg_top unless it asked for fvg_zones (which the engine then
    materializes for it). build_grounding_catalog() is imported lazily."""
    from app.ai.grounding import build_grounding_catalog

    cols = set(build_grounding_catalog()["indicator_columns"])
    cols |= _RAW_OHLCV
    if required_features:
        from app.features.registry import resolve_features
        for g in resolve_features(list(required_features)):
            cols |= set(g.columns)
    return cols
```

- [ ] **Step 5: Thread the declared features through `validate_spec`**

In `backend/app/ai/compiler.py` `validate_spec`, change line 62 from `cols = allowed_columns()` to:
```python
    cols = allowed_columns(spec.required_features)
```

- [ ] **Step 6: Thread declared features through the smoke driver**

In `backend/app/ai/_py_smoke_driver.py` `main()`, change `cols = sorted(allowed_columns())` to:
```python
        cols = sorted(allowed_columns(getattr(inst, "required_features", ())))
```

- [ ] **Step 7: Run the test — expect PASS**

Run: `python -m pytest tests/test_allowed_columns_feature_aware.py -q`
Expected: PASS (6 tests).

- [ ] **Step 8: Regression — the existing compiler tests stay green**

Run: `python -m pytest tests/test_spec_compiler.py tests/test_required_features_wiring.py -q`
Expected: PASS (the no-arg `allowed_columns()` default `()` preserves existing behavior; a spec with no `required_features` is byte-identical to before).

- [ ] **Step 9: Commit**

```bash
git add backend/app/ai/spec_schema.py backend/app/ai/compiler.py backend/app/ai/_py_smoke_driver.py tests/test_allowed_columns_feature_aware.py
git commit -m "feat(ai): SP-3 feature-aware allowed_columns + StrategySpec.required_features (C3)"
```

---

## Task 2: `WAREHOUSE_MANIFEST` + `capability_report()`

**Files:**
- Create: `backend/app/ai/capability.py`
- Test: `tests/test_capability_report.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_capability_report.py`:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.features.catalog  # noqa: F401

from app.ai.capability import capability_report, WAREHOUSE_MANIFEST


def test_manifest_states_what_we_have_and_lack():
    m = WAREHOUSE_MANIFEST
    assert m["has_1m_ohlcv"] is True
    assert m["has_option_candles"] is True
    assert m["has_per_strike_greeks_history"] is False
    assert m["has_oi_history"] is False
    assert m["has_l2_depth"] is False
    assert m["has_tick_orderflow"] is False
    assert set(m["instruments"]) == {"NIFTY", "BANKNIFTY", "SENSEX"}


def test_capability_report_composes_three_sources():
    rep = capability_report()
    # 1) columns (MAPPED surface): indicators + raw OHLCV + always-on geometry
    assert "close" in rep["columns"] and "rsi" in rep["columns"]
    assert "body_frac" in rep["columns"]            # always-on geometry is a normal column
    # 2) features (buildable): the 6 seed features with live_feasible flags
    feats = {f["feature"]: f for f in rep["features"]}
    assert {"swing_levels", "fvg_zones", "order_block"} <= set(feats)
    assert feats["swing_levels"]["live_feasible"] is True
    assert feats["fvg_zones"]["live_feasible"] is False
    # 3) warehouse data-limits manifest
    assert rep["warehouse"]["has_oi_history"] is False


def test_capability_report_columns_exclude_feature_columns():
    # feature columns are advertised under `features`, NOT mixed into `columns`
    rep = capability_report()
    assert "fvg_top" not in rep["columns"]
    assert any("fvg_top" in f["columns"] for f in rep["features"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_capability_report.py -q`
Expected: FAIL — `app.ai.capability` does not exist.

- [ ] **Step 3: Create `backend/app/ai/capability.py` (manifest + report)**

```python
"""The capability surface + the deterministic feasibility classifier (SP-3).

Host-importable: no motor, no I/O, no LLM. Three things live here:
  * WAREHOUSE_MANIFEST  — static truth about what data the warehouse has / lacks.
  * capability_report() — composes the grounding catalog (columns + buildable
    features) with the manifest into one object fed to BOTH the LLM prompt and
    the pure checker.
  * classify_rule()     — the pure R1-R9 feasibility classifier (see below).
"""
from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

# Mirrors app.data_hygiene.DEFAULT_START_DATE. Inlined (not imported) so this
# module stays host-importable — data_hygiene pulls motor.
_DATA_START = "2024-11-27"

# Static truth about the warehouse. Drives the NEEDS_NEW_DATA branch.
WAREHOUSE_MANIFEST: Dict[str, Any] = {
    "has_1m_ohlcv": True,                    # candles_1m, spot, all 3 indices
    "has_option_candles": True,              # options_1m, ATM +-1 band only
    "has_per_strike_greeks_history": False,
    "has_oi_history": False,
    "has_l2_depth": False,
    "has_tick_orderflow": False,
    "has_vix_history": False,
    "date_range": {"start": _DATA_START, "end": None},
    "instruments": ["NIFTY", "BANKNIFTY", "SENSEX"],
}

# Raw OHLCV always present (mirrors compiler._RAW_OHLCV).
_RAW_OHLCV = frozenset({"open", "high", "low", "close", "volume"})


def capability_report() -> Dict[str, Any]:
    """Compose the three capability sources into one object.

    columns  -> the MAPPED surface (indicator columns + raw OHLCV + always-on
                geometry; feature columns are NOT here — advertise != allow).
    features -> the buildable structural features (feature_entries: name,
                columns, requires, cost_class, live_feasible, ...).
    warehouse-> the static data-limits manifest.
    """
    from app.ai.grounding import build_grounding_catalog

    cat = build_grounding_catalog()
    columns = sorted(set(cat["indicator_columns"]) | _RAW_OHLCV)
    return {
        "columns": columns,
        "features": cat["feature_entries"],
        "warehouse": WAREHOUSE_MANIFEST,
    }
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `python -m pytest tests/test_capability_report.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/capability.py tests/test_capability_report.py
git commit -m "feat(ai): SP-3 WAREHOUSE_MANIFEST + capability_report() composed surface"
```

---

## Task 3: The pure R1–R9 feasibility classifier `classify_rule()`

**Files:**
- Modify: `backend/app/ai/capability.py` (append the taxonomy + classifier)
- Test: `tests/test_feasibility_classifier.py`

**Design.** The SP-4 agent (next) parses each source rule into a `RuleTokens` — the deterministic facts about a rule. `classify_rule` applies R1–R9 **first-match-wins** and returns a `Verdict`. No LLM here; the LLM only fills the tokens.

`RuleTokens` fields (all the classifier needs):
- `cols: FrozenSet[str]` — column names the rule references.
- `concepts: FrozenSet[str]` — canonical lowercase concept tokens (e.g. `"fvg"`, `"oi"`, `"order_flow"`).
- `barspan: int` — how many bars back the rule reaches (1–2 = Spec-expressible; >2 needs history).
- `window: int` — rolling-window depth the rule needs (drives live-feasibility; default 0).
- `session_anchored: bool` — needs this-session anchoring (opening range / session VWAP / prior-session).
- `ohlcv_derivable: bool` — the LLM asserts the quantity is vectorized-causal-derivable from OHLCV but is not yet a column (drives R6/R7).

`FeasibilityClass` (str enum): `BUILDABLE_NOW`, `BUILDABLE_WITH_FEATURE`, `NEEDS_NEW_DATA`, `INFEASIBLE`.

`Verdict`: `{feasibility, message, feature: Optional[str], live_feasible: Optional[bool]}`.

Concept taxonomy (closed sets / maps):
- `DATA_BLOCKED_CONCEPTS` (R2 → NEEDS_NEW_DATA): OI, PCR, max-pain, IV-rank, greeks history, vol structure, news, sentiment.
- `RELATIVE_STRENGTH_CONCEPTS` (R3 → BUILDABLE_WITH_FEATURE, engine plumbing/Phase A).
- `ORDERFLOW_CONCEPTS` (R4 → INFEASIBLE): order flow, footprint, depth, L2, tape.
- `STRUCTURE_FEATURE_MAP` (R5 → BUILDABLE_WITH_FEATURE, maps a structure concept to its seed feature so the verdict carries that feature's `live_feasible` caveat). Structure concepts with no seed feature yet → BUILDABLE_WITH_FEATURE, `feature=None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_feasibility_classifier.py`:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.features.catalog  # noqa: F401

from app.ai.capability import classify_rule, RuleTokens, FeasibilityClass as FC


def T(**kw):
    base = dict(cols=frozenset(), concepts=frozenset(), barspan=1, window=0,
                session_anchored=False, ohlcv_derivable=False)
    base.update(kw)
    # allow passing plain sets
    base["cols"] = frozenset(base["cols"])
    base["concepts"] = frozenset(base["concepts"])
    return RuleTokens(**base)


def test_r1_buildable_now_from_existing_columns():
    v = classify_rule(T(cols={"close", "rsi"}, barspan=1))
    assert v.feasibility == FC.BUILDABLE_NOW


def test_r1_does_not_fire_when_barspan_exceeds_two():
    v = classify_rule(T(cols={"close"}, barspan=5))
    assert v.feasibility != FC.BUILDABLE_NOW


def test_r2_oi_needs_new_data():
    v = classify_rule(T(concepts={"oi"}))
    assert v.feasibility == FC.NEEDS_NEW_DATA
    assert "oi" in v.message.lower()


def test_r3_relative_strength_is_engine_plumbing_feature():
    v = classify_rule(T(concepts={"relative_strength"}))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE


def test_r4_order_flow_is_infeasible():
    v = classify_rule(T(concepts={"order_flow"}))
    assert v.feasibility == FC.INFEASIBLE


def test_r5_fvg_maps_to_seed_feature_with_backtest_only_caveat():
    # The flagship ICT case: FVG-level rule -> buildable via fvg_zones, but
    # fvg_zones is stateful-unbounded -> live_feasible False -> caveat.
    v = classify_rule(T(concepts={"fvg"}))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.feature == "fvg_zones"
    assert v.live_feasible is False
    assert "backtest" in v.message.lower()


def test_r5_premium_discount_maps_to_live_feasible_feature():
    v = classify_rule(T(concepts={"premium_discount"}))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.feature == "premium_discount"
    assert v.live_feasible is True


def test_r5_already_declared_feature_column_is_buildable_now():
    # If the rule references fvg_top AND fvg_zones is already declared, the
    # column is in allowed_columns(decl) so R1 fires first (BUILDABLE_NOW).
    v = classify_rule(T(cols={"fvg_top"}, barspan=1), required_features=["fvg_zones"])
    assert v.feasibility == FC.BUILDABLE_NOW


def test_r6_ohlcv_derivable_short_window_is_live_safe_feature():
    v = classify_rule(T(ohlcv_derivable=True, window=20))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.live_feasible is True


def test_r7_ohlcv_derivable_long_window_is_live_gated():
    v = classify_rule(T(ohlcv_derivable=True, window=300))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.live_feasible is False


def test_r7_session_anchored_is_live_gated():
    v = classify_rule(T(ohlcv_derivable=True, window=10, session_anchored=True))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.live_feasible is False


def test_r8_history_beyond_two_bars_is_full_python_feature():
    v = classify_rule(T(cols={"close"}, barspan=8))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert "full-python" in v.message.lower() or "history" in v.message.lower()


def test_r9_unrecognised_is_infeasible():
    v = classify_rule(T(concepts={"astrology"}))
    assert v.feasibility == FC.INFEASIBLE
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_feasibility_classifier.py -q`
Expected: FAIL — `classify_rule`/`RuleTokens`/`FeasibilityClass` not defined.

- [ ] **Step 3: Append the taxonomy + classifier to `backend/app/ai/capability.py`**

```python
class FeasibilityClass(str, Enum):
    BUILDABLE_NOW = "BUILDABLE_NOW"
    BUILDABLE_WITH_FEATURE = "BUILDABLE_WITH_FEATURE"
    NEEDS_NEW_DATA = "NEEDS_NEW_DATA"
    INFEASIBLE = "INFEASIBLE"


@dataclasses.dataclass(frozen=True)
class RuleTokens:
    cols: FrozenSet[str] = frozenset()
    concepts: FrozenSet[str] = frozenset()
    barspan: int = 1
    window: int = 0
    session_anchored: bool = False
    ohlcv_derivable: bool = False


@dataclasses.dataclass(frozen=True)
class Verdict:
    feasibility: FeasibilityClass
    message: str
    feature: Optional[str] = None
    live_feasible: Optional[bool] = None


# R2 — concepts that need data the warehouse does not store.
DATA_BLOCKED_CONCEPTS: FrozenSet[str] = frozenset({
    "oi", "open_interest", "pcr", "max_pain", "iv_rank", "iv", "implied_vol",
    "theta", "vega", "gamma", "delta", "historical_greeks", "greeks",
    "vol_structure", "term_structure", "news", "sentiment",
})

# R3 — needs a second instrument's aligned bars (engine plumbing, Phase A).
RELATIVE_STRENGTH_CONCEPTS: FrozenSet[str] = frozenset({
    "relative_strength", "pairs", "cross_instrument", "ratio_spread", "spread",
})

# R4 — needs tick-level depth/tape 1m bars can't reconstruct.
ORDERFLOW_CONCEPTS: FrozenSet[str] = frozenset({
    "order_flow", "orderflow", "footprint", "depth", "l2", "tape",
    "bid_ask_imbalance", "delta_volume", "cvd",
})

# R5 — structure concepts -> the seed feature that materializes them (or None
# if detectable but no seed feature exists yet).
STRUCTURE_FEATURE_MAP: Dict[str, Optional[str]] = {
    "fvg": "fvg_zones", "fair_value_gap": "fvg_zones", "imbalance": "fvg_zones",
    "order_block": "order_block", "ob": "order_block",
    "bos": "displacement", "break_of_structure": "displacement",
    "displacement": "displacement",
    "choch": "choch", "change_of_character": "choch",
    "premium_discount": "premium_discount", "premium": "premium_discount",
    "discount": "premium_discount", "equilibrium": "premium_discount",
    "sweep": "swing_levels", "liquidity_sweep": "swing_levels",
    "swing": "swing_levels", "swing_level": "swing_levels",
    # detectable structure with no seed feature yet (-> a NEW feature is needed):
    "breaker": None, "ote": None, "equal_highs": None, "equal_lows": None,
    "divergence": None, "mtf": None,
}

_LIVE_WINDOW_MAX = 150


def _feature_live_feasible(name: str) -> Optional[bool]:
    from app.features.registry import FEATURE_REGISTRY, feature_live_feasible
    g = FEATURE_REGISTRY.get(name)
    return None if g is None else feature_live_feasible(g)


def classify_rule(tokens: RuleTokens, *, required_features=()) -> Verdict:
    """Pure R1-R9 first-match-wins feasibility classification of one rule.

    The LLM (SP-4) fills `tokens`; this function makes the deterministic call.
    """
    from app.ai.compiler import allowed_columns

    # R1 — every referenced column is already available (incl. declared
    # features) AND the rule fits Spec's 2-bar window AND no extra concept.
    if tokens.cols and not tokens.concepts and tokens.barspan <= 2:
        if tokens.cols <= allowed_columns(required_features):
            return Verdict(FeasibilityClass.BUILDABLE_NOW,
                           "Buildable now from existing columns.")

    # R2 — needs data the warehouse does not store.
    blocked = tokens.concepts & DATA_BLOCKED_CONCEPTS
    if blocked:
        c = sorted(blocked)[0]
        return Verdict(FeasibilityClass.NEEDS_NEW_DATA,
                       f"'{c}' needs data the warehouse does not store "
                       f"(only 1m OHLCV + ATM-band option candles).")

    # R3 — relative strength / pairs: needs a second instrument's aligned bars.
    if tokens.concepts & RELATIVE_STRENGTH_CONCEPTS:
        return Verdict(FeasibilityClass.BUILDABLE_WITH_FEATURE,
                       "Needs the other instrument's aligned bars in ctx — an "
                       "engine change (Phase A). Feasible-but-nontrivial.",
                       feature=None, live_feasible=False)

    # R4 — order flow / depth / tape: 1m bars can't reconstruct it.
    if tokens.concepts & ORDERFLOW_CONCEPTS:
        return Verdict(FeasibilityClass.INFEASIBLE,
                       "Requires tick-level depth/tape that 1m bars can't "
                       "reconstruct. Infeasible to backtest.")

    # R5 — ICT/SMC structure: detectable, but the tradeable level isn't a
    # column yet -> add (or reuse) a feature; carry its live caveat.
    struct = tokens.concepts & set(STRUCTURE_FEATURE_MAP)
    if struct:
        concept = sorted(struct)[0]
        feat = STRUCTURE_FEATURE_MAP[concept]
        lf = _feature_live_feasible(feat) if feat else None
        if feat and lf is False:
            msg = (f"Detectable via the '{feat}' feature, but it is stateful "
                   f"(carry-forward) -> backtest-only on the live window.")
        elif feat:
            msg = f"Detectable via the '{feat}' feature. Safe in backtest + live."
        else:
            msg = (f"'{concept}' is detectable from price but needs a new "
                   f"structural feature built first.")
        return Verdict(FeasibilityClass.BUILDABLE_WITH_FEATURE, msg,
                       feature=feat, live_feasible=lf)

    # R6/R7 — a vectorized quantity derivable from OHLCV but not yet a column.
    if tokens.ohlcv_derivable:
        live = (not tokens.session_anchored) and tokens.window <= _LIVE_WINDOW_MAX
        if live:
            return Verdict(FeasibilityClass.BUILDABLE_WITH_FEATURE,
                           "One vectorized feature from OHLCV. Safe in backtest + live.",
                           feature=None, live_feasible=True)
        return Verdict(FeasibilityClass.BUILDABLE_WITH_FEATURE,
                       "Buildable, but exceeds the live window / is session-anchored "
                       "-> backtest-correct, live-gated (declared backtest-only).",
                       feature=None, live_feasible=False)

    # R8 — needs more than 2 bars of history, only expressible via the history
    # frame (Full-Python), not the 2-bar Spec window.
    if tokens.barspan > 2:
        return Verdict(FeasibilityClass.BUILDABLE_WITH_FEATURE,
                       "Exceeds Spec's 2-bar window. Full-Python via the history "
                       "frame, or a small *_Nago column.",
                       feature=None, live_feasible=True)

    # R9 — default: nothing maps.
    return Verdict(FeasibilityClass.INFEASIBLE,
                   "Can't map this to anything derivable from 1m OHLCV. Give the "
                   "precise calculation or it's out of scope.")
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `python -m pytest tests/test_feasibility_classifier.py -q`
Expected: PASS (13 tests). If `test_r5_already_declared_feature_column_is_buildable_now` fails, confirm R1 runs BEFORE R5 and that `allowed_columns(["fvg_zones"])` includes `fvg_top` (Task 1).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/capability.py tests/test_feasibility_classifier.py
git commit -m "feat(ai): SP-3 pure R1-R9 feasibility classifier classify_rule()"
```

---

## Task 4: Integration (flagship ICT-FVG end-to-end) + full regression

**Files:**
- Test: `tests/test_capability_report.py` (append the end-to-end case)

- [ ] **Step 1: Write the end-to-end flagship test**

Append to `tests/test_capability_report.py`:
```python
def test_flagship_ict_fvg_end_to_end():
    """The motivating case: 'enter when price returns to a bullish FVG'.
    The agent (SP-4) would parse this into an FVG-structure rule; SP-3's
    classifier routes it to BUILDABLE_WITH_FEATURE(fvg_zones, backtest-only),
    and once fvg_zones is declared the column becomes allowed."""
    from app.ai.capability import classify_rule, RuleTokens, FeasibilityClass
    from app.ai.compiler import allowed_columns

    # before declaring the feature: structure concept -> buildable-with-feature
    v = classify_rule(RuleTokens(concepts=frozenset({"fvg"})))
    assert v.feasibility == FeasibilityClass.BUILDABLE_WITH_FEATURE
    assert v.feature == "fvg_zones" and v.live_feasible is False

    # after the agent declares required_features=["fvg_zones"], the column the
    # generated Spec/Full-Python strategy references is now allowed
    assert "fvg_top" in allowed_columns(["fvg_zones"])
    assert "fvg_top" not in allowed_columns()   # advertise != allow
```

- [ ] **Step 2: Run the new test — expect PASS**

Run: `python -m pytest tests/test_capability_report.py::test_flagship_ict_fvg_end_to_end -q`
Expected: PASS.

- [ ] **Step 3: Full SP-3 test sweep**

Run: `python -m pytest tests/test_allowed_columns_feature_aware.py tests/test_capability_report.py tests/test_feasibility_classifier.py tests/test_spec_compiler.py tests/test_grounding_catalog.py -q`
Expected: PASS (all SP-3 tests + the existing compiler/grounding tests unchanged).

- [ ] **Step 4: Full host suite — confirm no regressions vs baseline**

Run: `python -m pytest tests/ --continue-on-collection-errors -q`
Expected: prior passing count + the new SP-3 tests; the ONLY failures/errors are the unchanged motor-absent baseline (~5 failures + ~16 collection errors). Any NEW non-motor failure → stop, systematic-debugging.

- [ ] **Step 5: Commit**

```bash
git add tests/test_capability_report.py
git commit -m "test(ai): SP-3 flagship ICT-FVG end-to-end (classify -> declare -> allow) + regression"
```

---

## Self-Review (run before handing off)

**1. Spec coverage (§7 of the design):**
- §7.1 `capability_report()` composing 3 sources (columns + feature registry + warehouse manifest) → Task 2. ✓
- §7.2 `allowed_columns()` feature-aware (C3 — declared-scoped; advertise ≠ allow); threaded through `validate_spec` + `_py_smoke_driver` → Task 1. ✓ (the third thread point — `grounding.py` advertising all features — already done in SP-1/SP-2; `capability_report` advertises, `allowed_columns` stays declaration-scoped.)
- §7.3 the pure R1–R9 classifier, host-importable + unit-tested, shipping AFTER ≥1 real feature (the 6 seed features exist) → Task 3. ✓

**2. Placeholder scan:** every code step has complete code; every test asserts concrete classes/columns; no TBD. ✓

**3. Type consistency:** `allowed_columns(required_features=())` signature consistent across compiler definition + the two call sites + the tests; `RuleTokens`/`Verdict`/`FeasibilityClass` field names consistent between the classifier and its tests; `capability_report()` returns `{columns, features, warehouse}` consistently used in tests. ✓

**4. Intentional design decisions (documented):**
- `capability.py` is a NEW module (not added to `grounding.py` as §7.1's prose suggests) — keeps `grounding.py` focused and groups the capability surface + classifier together; `capability_report()` still composes `build_grounding_catalog()`. 
- `WAREHOUSE_MANIFEST` is a static dict with the data-start date inlined (not imported from `data_hygiene`, which pulls motor and would break host-importability).
- `RuleTokens` carries `ohlcv_derivable` + `session_anchored` + `window` flags so R6/R7/R8 are decidable purely from the (LLM-filled) tokens — the classifier itself stays LLM-free.
- The classifier is the deterministic gate only; parsing source text → `RuleTokens` is SP-4's LLM job.

---

## Execution note

Implement via **superpowers:subagent-driven-development**: Tasks 1→4 in order (Task 1 — the C3 allowed_columns fix — is the foundation Task 3's R1 depends on). Two-stage review per task (spec-compliance + code-quality), with the adversarial skeptic focused on: (a) advertise≠allow (an undeclared feature column must NOT validate), (b) the no-arg `allowed_columns()` default preserving byte-identical existing behavior, and (c) the R1–R9 first-match-wins ordering (esp. R1-before-R5 when a feature is already declared, and R2 data-blocked never mis-routing to BUILDABLE).
