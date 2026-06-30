# SP-1: Feature Framework Infrastructure (zero features) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the opt-in structural-feature framework as pure, dormant plumbing — a `FeatureGroup` registry, a `required_features` declaration on `StrategyBase`, a `materialize_features` step wired (as a guarded no-op) into the single-frame execution paths, and an (empty) feature block in the grounding catalog — with **zero behavior change** for every existing strategy.

**Architecture:** Mirror the existing `app/indicator_groups.py` machinery. A new `app/features/` package defines `FeatureGroup` (structurally like `IndicatorGroup`, plus `columns`/`requires`/`cost_class`/`session_anchored`/`stateful_unbounded`/`min_history_bars`), an (initially empty) `FEATURE_REGISTRY`, `resolve_features` (dependency-closure + topo-sort, raising `FeatureError` on an unknown name), and `materialize_features` (a cache loop mirroring `enrich_with_cache`). Strategies opt in via `required_features = [...]` (default `[]`). Execution paths call `materialize_features` **only when the list is non-empty** — so existing strategies (all declare nothing) hit a no-op early return and stay byte-identical.

**Tech Stack:** Python 3.12, pandas, pytest. Backend `C:\Users\haroo\af-wt-strategy-library\backend`; tests `C:\Users\haroo\af-wt-strategy-library\tests`. Branch `feat/capability-aware-authoring` (SP-0 already merged into it). Spec: `docs/superpowers/specs/2026-06-28-capability-aware-strategy-authoring-design.md` §5.

> cwd for all commands = `C:\Users\haroo\af-wt-strategy-library`. Host venv has pandas/numpy/pytest, NOT motor — every file these tests import is host-safe. **Scope note:** the optimizer (`get_enriched`) feature wiring is **intentionally deferred to SP-2b** (it needs a real feature to verify cache reuse + the param-key guard); with zero features the optimizer needs no feature step. SP-1 wires only the two single-frame paths (backtest `runtime.py`, live `deployment_evaluator.py`) + grounding.

## File structure
- **Create** `backend/app/features/__init__.py` — public exports.
- **Create** `backend/app/features/registry.py` — `FeatureGroup`, `FEATURE_REGISTRY`, `FeatureError`, `resolve_features`, `materialize_features`.
- **Create** `backend/app/features/catalog.py` — `FEATURE_CATALOG` (empty) + `feature_catalog_entries()` (grounding metadata).
- **Modify** `backend/app/strategies/base.py` — `required_features` class attr + `meta()` key.
- **Modify** `backend/app/runtime.py` (~815) — guarded `materialize_features` after regime.
- **Modify** `backend/app/deployment_evaluator.py` (~344) — guarded `materialize_features` before `build_live_eval_ctx`.
- **Modify** `backend/app/ai/grounding.py` — `feature_columns` + `all_columns_including_features` blocks.
- **Create** `tests/test_feature_registry.py`, `tests/test_required_features_wiring.py`.

---

## Task 1: the feature registry

**Files:**
- Create: `backend/app/features/registry.py`
- Test: `tests/test_feature_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_feature_registry.py`:

```python
"""SP-1: the opt-in feature registry (resolve + materialize), zero real features."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.features.registry import (
    FeatureGroup, FEATURE_REGISTRY, FeatureError, resolve_features, materialize_features,
)


def _register(monkeypatch, *groups):
    reg = {g.name: g for g in groups}
    monkeypatch.setattr("app.features.registry.FEATURE_REGISTRY", reg)
    return reg


def test_registry_is_empty_by_default():
    assert FEATURE_REGISTRY == {}


def test_resolve_unknown_feature_raises_feature_error():
    with pytest.raises(FeatureError) as ei:
        resolve_features(["nope"])
    assert ei.value.name == "nope"
    assert "nope" in str(ei.value)


def test_resolve_closes_over_requires_and_topo_sorts(monkeypatch):
    base = FeatureGroup(name="base", columns=("b",), param_keys=(), requires=(),
                        cost_class="vectorized", session_anchored=False,
                        stateful_unbounded=False, min_history_bars=1,
                        compute=lambda df, p: {"b": df["close"]})
    dep = FeatureGroup(name="dep", columns=("d",), param_keys=(), requires=("base",),
                       cost_class="vectorized", session_anchored=False,
                       stateful_unbounded=False, min_history_bars=1,
                       compute=lambda df, p: {"d": df["b"] + 1})
    _register(monkeypatch, base, dep)
    order = [g.name for g in resolve_features(["dep"])]   # asked only for dep
    assert order == ["base", "dep"]                       # base pulled in + ordered first


def test_materialize_applies_columns_in_dependency_order(monkeypatch):
    base = FeatureGroup(name="base", columns=("b",), param_keys=(), requires=(),
                        cost_class="vectorized", session_anchored=False,
                        stateful_unbounded=False, min_history_bars=1,
                        compute=lambda df, p: {"b": df["close"] * 2})
    dep = FeatureGroup(name="dep", columns=("d",), param_keys=(), requires=("base",),
                       cost_class="vectorized", session_anchored=False,
                       stateful_unbounded=False, min_history_bars=1,
                       compute=lambda df, p: {"d": df["b"] + 1})
    _register(monkeypatch, base, dep)
    df = pd.DataFrame({"close": [10.0, 20.0]})
    out = materialize_features(df, {}, ["dep"], {})
    assert list(out["b"]) == [20.0, 40.0]
    assert list(out["d"]) == [21.0, 41.0]
    assert "b" not in df.columns and "d" not in df.columns   # caller's df not mutated


def test_materialize_caches_param_independent_group_once(monkeypatch):
    calls = {"n": 0}
    def _compute(df, p):
        calls["n"] += 1
        return {"b": df["close"]}
    g = FeatureGroup(name="base", columns=("b",), param_keys=(), requires=(),
                     cost_class="vectorized", session_anchored=False,
                     stateful_unbounded=False, min_history_bars=1, compute=_compute)
    _register(monkeypatch, g)
    df = pd.DataFrame({"close": [1.0, 2.0]})
    cache = {}
    materialize_features(df, {}, ["base"], cache)
    materialize_features(df, {}, ["base"], cache)
    assert calls["n"] == 1            # second call served from cache
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_feature_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.features'`.

- [ ] **Step 3: Implement the registry**

Create `backend/app/features/registry.py`:

```python
"""Opt-in structural-feature registry (FVG zones, swing levels, order blocks, ...).

Sibling to `app.indicator_groups`: features are computed AFTER the indicator
frame is assembled, ONLY for strategies that declare them via
`StrategyBase.required_features`. Empty declaration => this module is never
invoked => existing strategies are byte-identical.

Host-importable: pandas only, no motor/IO.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd


@dataclass(frozen=True)
class FeatureGroup:
    name: str
    columns: Tuple[str, ...]
    param_keys: Tuple[str, ...]
    requires: Tuple[str, ...]
    cost_class: str                       # "vectorized" | "session_loop"
    session_anchored: bool                # needs a full/prior session (live caveat)
    stateful_unbounded: bool              # carry-forward depends on pre-window history
    min_history_bars: int
    compute: Callable[[pd.DataFrame, dict], Dict[str, pd.Series]]
    causal: bool = True


# Populated by app.features.catalog at import time (empty in SP-1).
FEATURE_REGISTRY: Dict[str, FeatureGroup] = {}


class FeatureError(Exception):
    """Raised when a strategy declares a feature the engine cannot provide.
    The authoring/feasibility layer converts this into a REJECT-with-explanation."""

    def __init__(self, code: str, *, name: str = "", available: Optional[List[str]] = None):
        self.code = code
        self.name = name
        self.available = available or []
        super().__init__(
            f"{code}: feature {name!r} is not registered. "
            f"available={sorted(self.available)}"
        )


def resolve_features(required: List[str]) -> List[FeatureGroup]:
    """Expand `required` to the dependency-closed, topologically-ordered group
    list to materialize. Raises FeatureError('unknown_feature') on any name (or
    transitive `requires` name) not in FEATURE_REGISTRY."""
    ordered: List[FeatureGroup] = []
    seen: set = set()
    visiting: set = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        if name not in FEATURE_REGISTRY:
            raise FeatureError("unknown_feature", name=name,
                               available=list(FEATURE_REGISTRY))
        if name in visiting:
            raise FeatureError("cyclic_feature", name=name,
                               available=list(FEATURE_REGISTRY))
        visiting.add(name)
        g = FEATURE_REGISTRY[name]
        for dep in g.requires:
            visit(dep)
        visiting.discard(name)
        seen.add(name)
        ordered.append(g)

    for n in required:
        visit(n)
    return ordered


def materialize_features(df: pd.DataFrame, params: dict, required: List[str],
                         feature_caches: Dict[str, Dict], *,
                         max_per_group: int = 4) -> pd.DataFrame:
    """Append the declared features' columns to a COPY of `df` (never mutates the
    caller's frame, which may be an indicator-cache frame). Mirrors
    `indicator_groups.enrich_with_cache`'s cache contract:
    feature_caches[name][param_key_tuple] -> {col: Series}."""
    if not required:
        return df
    out = df.copy()
    for g in resolve_features(required):
        key = tuple(params.get(k) for k in g.param_keys)
        cache = feature_caches.setdefault(g.name, {})
        cols = cache.get(key)
        if cols is None:
            cols = g.compute(out, params)
            if len(cache) < max_per_group:
                cache[key] = cols
        for c, s in cols.items():
            out[c] = s
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_feature_registry.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd "C:/Users/haroo/af-wt-strategy-library" && git add backend/app/features/registry.py tests/test_feature_registry.py && git commit -m "feat(sp1): opt-in feature registry (FeatureGroup, resolve_features, materialize_features)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: feature catalog + package exports

**Files:**
- Create: `backend/app/features/catalog.py`, `backend/app/features/__init__.py`
- Test: `tests/test_feature_registry.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feature_registry.py`:

```python
def test_catalog_entries_shape_empty_in_sp1():
    from app.features.catalog import feature_catalog_entries
    entries = feature_catalog_entries()
    assert entries == []          # no features registered yet


def test_package_exports():
    import app.features as feats
    assert hasattr(feats, "materialize_features")
    assert hasattr(feats, "resolve_features")
    assert hasattr(feats, "FEATURE_REGISTRY")
    assert hasattr(feats, "FeatureError")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_feature_registry.py::test_catalog_entries_shape_empty_in_sp1 tests/test_feature_registry.py::test_package_exports -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.features.catalog'`.

- [ ] **Step 3: Implement the catalog + package exports**

Create `backend/app/features/catalog.py`:

```python
"""Feature metadata for grounding + the capability surface. Importing this module
registers every built-in FeatureGroup into FEATURE_REGISTRY. In SP-1 there are
none; SP-2 adds them here.

Each catalog entry advertises a feature to the AI authoring layer:
  {feature, columns, needs_declaration, requires, cost_class, session_anchored,
   stateful_unbounded, min_history_bars, data_requirements, description}.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.features.registry import FEATURE_REGISTRY, FeatureGroup

# name -> human metadata (kept beside the registry; SP-2 populates both).
FEATURE_CATALOG: Dict[str, Dict[str, Any]] = {}


def register_feature(group: FeatureGroup, *, description: str,
                     data_requirements: List[str]) -> None:
    """Register a FeatureGroup + its catalog metadata. Idempotent by name."""
    FEATURE_REGISTRY[group.name] = group
    FEATURE_CATALOG[group.name] = {
        "description": description,
        "data_requirements": data_requirements,
    }


def feature_catalog_entries() -> List[Dict[str, Any]]:
    """The advertised feature list for grounding/AI (empty in SP-1)."""
    out: List[Dict[str, Any]] = []
    for name, g in FEATURE_REGISTRY.items():
        meta = FEATURE_CATALOG.get(name, {})
        out.append({
            "feature": name,
            "columns": list(g.columns),
            "needs_declaration": True,
            "requires": list(g.requires),
            "cost_class": g.cost_class,
            "session_anchored": g.session_anchored,
            "stateful_unbounded": g.stateful_unbounded,
            "min_history_bars": g.min_history_bars,
            "data_requirements": meta.get("data_requirements", ["ohlcv_1m"]),
            "description": meta.get("description", ""),
        })
    return out
```

Create `backend/app/features/__init__.py`:

```python
"""Opt-in structural feature framework. See registry.py + catalog.py."""
from app.features.registry import (
    FeatureGroup,
    FEATURE_REGISTRY,
    FeatureError,
    resolve_features,
    materialize_features,
)
from app.features.catalog import (
    FEATURE_CATALOG,
    feature_catalog_entries,
    register_feature,
)

__all__ = [
    "FeatureGroup", "FEATURE_REGISTRY", "FeatureError",
    "resolve_features", "materialize_features",
    "FEATURE_CATALOG", "feature_catalog_entries", "register_feature",
]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_feature_registry.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
cd "C:/Users/haroo/af-wt-strategy-library" && git add backend/app/features/catalog.py backend/app/features/__init__.py tests/test_feature_registry.py && git commit -m "feat(sp1): feature catalog metadata + package exports (empty registry)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `required_features` on `StrategyBase`

**Files:**
- Modify: `backend/app/strategies/base.py` (class attr + `meta()`)
- Test: `tests/test_required_features_wiring.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_required_features_wiring.py`:

```python
"""SP-1: required_features declaration + no-op wiring (byte-identical back-compat)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
from app.strategies.base import StrategyBase, Signal


def test_required_features_defaults_empty_and_in_meta():
    class _S(StrategyBase):
        id = "rf_default"
    s = _S()
    assert s.required_features == []
    assert s.meta()["required_features"] == []


def test_required_features_declared_appears_in_meta():
    class _S(StrategyBase):
        id = "rf_decl"
        required_features = ["fvg_zones", "swing_levels"]
    assert _S().meta()["required_features"] == ["fvg_zones", "swing_levels"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_required_features_wiring.py -v`
Expected: FAIL — `KeyError: 'required_features'` from `meta()` (and/or `AttributeError`).

- [ ] **Step 3: Implement**

In `backend/app/strategies/base.py`:

(a) Add the class attribute to `StrategyBase` (next to the other declared class attrs like `is_builtin`):
```python
    is_builtin: bool = True
    required_features: List[str] = []
```

(b) Add the key to the dict returned by `meta()`:
```python
            "is_builtin": self.is_builtin,
            "required_features": self.required_features,
            "origin": _origin_from_module(type(self).__module__),
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_required_features_wiring.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Guard existing strategy meta() consumers**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_strategy_library_routes.py tests/test_new_strategies_integration.py -q`
Expected: PASS (meta() gained an additive key; existing consumers ignore it). If `test_strategy_library_routes.py` does not exist, substitute any test that asserts on `meta()`/strategy listing and run it; otherwise skip this step.

- [ ] **Step 6: Commit**

```bash
cd "C:/Users/haroo/af-wt-strategy-library" && git add backend/app/strategies/base.py tests/test_required_features_wiring.py && git commit -m "feat(sp1): StrategyBase.required_features declaration (default empty) + meta()

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: wire `materialize_features` into backtest + live (guarded no-op)

**Files:**
- Modify: `backend/app/runtime.py` (~815, after `regime`)
- Modify: `backend/app/deployment_evaluator.py` (~344, after `regime`, before `build_live_eval_ctx`)
- Test: `tests/test_required_features_wiring.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_required_features_wiring.py`:

```python
from app.backtest import run_backtest
from app.features.registry import FeatureGroup


def _bt_df(n=60):
    base_ms = 1_700_000_000_000
    return pd.DataFrame([{
        "ts": base_ms + k * 60_000, "datetime": f"2025-01-02T11:{k % 60:02d}:00",
        "ist_time": "11:00", "session_date": "2025-01-02",
        "open": 100.0 + k * 0.1, "high": 100.6 + k * 0.1,
        "low": 99.4 + k * 0.1, "close": 100.0 + k * 0.1,
    } for k in range(n)])


def test_backtest_noop_when_no_required_features():
    """A strategy declaring no features must reach evaluate with NO extra columns."""
    cols_seen = []

    class _Plain(StrategyBase):
        id = "rf_plain"
        def evaluate(self, row, prev, params, ctx):
            cols_seen.append(set(row.keys()))
            return Signal(direction="NONE")

    run_backtest(_bt_df(), _Plain(), {}, instrument="NIFTY")
    assert cols_seen
    # no feature column leaked in (only OHLCV-ish keys present)
    assert all("feat_demo" not in keys for keys in cols_seen)


def test_backtest_materializes_declared_feature(monkeypatch):
    """A declared feature's column is present on the row at evaluate time."""
    g = FeatureGroup(name="demo", columns=("feat_demo",), param_keys=(), requires=(),
                     cost_class="vectorized", session_anchored=False,
                     stateful_unbounded=False, min_history_bars=1,
                     compute=lambda df, p: {"feat_demo": df["close"] * 0 + 42.0})
    monkeypatch.setattr("app.features.registry.FEATURE_REGISTRY", {"demo": g})
    saw = []

    class _Feat(StrategyBase):
        id = "rf_feat"
        required_features = ["demo"]
        def evaluate(self, row, prev, params, ctx):
            saw.append(row.get("feat_demo"))
            return Signal(direction="NONE")

    run_backtest(_bt_df(), _Feat(), {}, instrument="NIFTY")
    assert saw and all(v == 42.0 for v in saw)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_required_features_wiring.py::test_backtest_materializes_declared_feature -v`
Expected: FAIL — `assert saw and all(...)` fails because `feat_demo` is never materialized (column absent → `row.get` returns None).

- [ ] **Step 3: Implement the backtest wiring**

In `backend/app/runtime.py`, add the import near the other app imports at the top of the file:
```python
from app.features import materialize_features
```
Then, immediately after the regime line at ~816:
```python
    df_enriched = precompute_all_indicators(df, params)
    df_enriched["regime"] = classify_regime_series(df_enriched)
    if strategy.required_features:
        df_enriched = materialize_features(df_enriched, params, strategy.required_features, {})
    res = run_backtest(
```
(There may be more than one `precompute_all_indicators(...) -> regime` site in `runtime.py`. Apply the same 2-line guarded block after EACH `df_enriched["regime"] = classify_regime_series(df_enriched)` that is immediately followed by a `run_backtest(...)` call. Grep: `cd "C:/Users/haroo/af-wt-strategy-library" && python -c "import re,io; s=open('backend/app/runtime.py').read(); print(s.count('classify_regime_series'))"` to count sites, and wire each one that precedes a `run_backtest`.)

**Note:** `run_backtest` reads the row via `df.to_dict('records')`, so a materialized column appears in `row[...]` automatically.

- [ ] **Step 4: Implement the live wiring**

In `backend/app/deployment_evaluator.py`, add to the imports:
```python
from app.features import materialize_features
```
After the regime line at ~344, before the evaluate/`build_live_eval_ctx`:
```python
    df_enriched = precompute_all_indicators(df, merged_params)
    df_enriched["regime"] = classify_regime_series(df_enriched)
    if strategy.required_features:
        df_enriched = materialize_features(df_enriched, merged_params, strategy.required_features, {})
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_required_features_wiring.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Byte-identical regression**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_backtest_characterization.py tests/test_indicator_equivalence.py tests/test_deployment_preflight.py -q`
Expected: PASS — existing strategies declare no features, so the guard is a no-op and results are byte-identical.

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/haroo/af-wt-strategy-library" && git add backend/app/runtime.py backend/app/deployment_evaluator.py tests/test_required_features_wiring.py && git commit -m "feat(sp1): wire materialize_features into backtest + live (guarded no-op)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: surface features in the grounding catalog

**Files:**
- Modify: `backend/app/ai/grounding.py` (`build_grounding_catalog`)
- Test: `tests/test_feature_registry.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feature_registry.py`:

```python
def test_grounding_catalog_has_feature_block():
    from app.ai.grounding import build_grounding_catalog
    cat = build_grounding_catalog()
    assert "feature_columns" in cat
    assert cat["feature_columns"] == []          # empty in SP-1
    assert "all_columns_including_features" in cat
    # with no features, the augmented column set equals the indicator columns
    assert set(cat["all_columns_including_features"]) == set(cat["indicator_columns"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_feature_registry.py::test_grounding_catalog_has_feature_block -v`
Expected: FAIL — `KeyError: 'feature_columns'`.

- [ ] **Step 3: Implement**

In `backend/app/ai/grounding.py` `build_grounding_catalog()`, before the `return`:
```python
    from app.features.catalog import feature_catalog_entries
    from app.features.registry import FEATURE_REGISTRY, materialize_features

    feature_columns = feature_catalog_entries()
    # Materialize ALL registered features on the sample frame so the column NAMES
    # are real-verified (empty in SP-1 -> no-op). resolve+materialize over the
    # whole registry advertises the augmented column universe to the AI.
    all_feature_cols = sorted(
        set().union(*(set(g.columns) for g in FEATURE_REGISTRY.values()))
    ) if FEATURE_REGISTRY else []
    all_columns = sorted(set(indicator_columns) | set(all_feature_cols))
```
and extend the returned dict:
```python
    return {
        "indicator_columns": indicator_columns,
        "feature_columns": feature_columns,
        "all_columns_including_features": all_columns,
        "signal_fields": signal_fields,
        "strategies": strategies,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_feature_registry.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Guard grounding/compiler consumers**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_grounding_catalog.py tests/test_compiler.py -q`
Expected: PASS (additive keys). If those test files do not exist, run `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/ -q -k "grounding or compiler or catalog" --continue-on-collection-errors` and confirm no new failures.

- [ ] **Step 6: Commit**

```bash
cd "C:/Users/haroo/af-wt-strategy-library" && git add backend/app/ai/grounding.py tests/test_feature_registry.py && git commit -m "feat(sp1): grounding catalog advertises feature_columns + augmented column set (empty)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: full-suite regression + wrap-up

- [ ] **Step 1: Full host suite**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/ -q --continue-on-collection-errors`
Expected: prior baseline (2044 passed + the SP-0 additions) PLUS the new SP-1 tests, with the SAME pre-existing motor failures/collection-errors as baseline and ZERO new failures. (`--continue-on-collection-errors` is required because ~16 motor-dependent files error at collection on the host venv — this is the documented baseline, not a regression.)

- [ ] **Step 2: Confirm additive diff**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && git diff --stat b528dc4..HEAD`
Expected: only `app/features/*` (new), `app/strategies/base.py`, `app/runtime.py`, `app/deployment_evaluator.py`, `app/ai/grounding.py`, and the two new test files.

---

## Self-Review

**1. Spec coverage (design §5 — framework infra):**
- `FeatureGroup` registry mirroring indicator groups → Task 1. ✓
- `required_features` opt-in declaration + `meta()` → Task 3. ✓
- `materialize_features` no-op-on-empty wired at the single-frame paths (backtest, live) → Task 4. ✓ (Optimizer wiring deferred to SP-2b — documented in the header; with zero features it is a no-op anyway.)
- Grounding advertises features + augmented column set → Task 5. ✓
- Byte-identical back-compat proof → Task 4 Step 6 + Task 6 Step 1 (golden + characterization suites). ✓
- `FeatureError` (unknown feature) = the feasibility boundary's first gate → Task 1. ✓

**2. Placeholder scan:** Every code step shows complete code. The two "if the test file doesn't exist, substitute…" steps (3.5, 5.5) are robustness instructions for the executor, not placeholders — the primary action and command are concrete. ✓

**3. Type/name consistency:** `FeatureGroup` field names (`columns`, `param_keys`, `requires`, `cost_class`, `session_anchored`, `stateful_unbounded`, `min_history_bars`, `compute`, `causal`) are identical in the dataclass (Task 1), every test constructor (Tasks 1, 4), and `feature_catalog_entries` (Task 2). `materialize_features(df, params, required, feature_caches)` signature matches all call sites. `required_features` spelled consistently. ✓

**Notes for the executor:**
- Host venv lacks `motor`; do NOT import `app.deployment_evaluator` in a test. Its wiring is covered indirectly (the guarded block is identical to backtest's, which IS tested) and by the full-suite deployment tests.
- The optimizer `get_enriched` wiring is intentionally OUT of scope here (SP-2b). Do not add it.
- `materialize_features` copies the frame on first feature write; for a no-features run it returns the same object — preserving identity for the byte-identical path.
