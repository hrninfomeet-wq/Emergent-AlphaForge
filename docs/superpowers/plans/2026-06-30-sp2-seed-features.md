# SP-2 — Seed Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the seed feature set for capability-aware authoring — always-on candle geometry (as a normal indicator group) plus the opt-in structural ICT features (`swing_levels`, `premium_discount`, `displacement`, `choch`, `fvg_zones`, `order_block`) registered through the SP-1 `FeatureGroup` framework — each causal, parity-tested, causality-tested, and correctly classified for live feasibility, with **zero** change to existing-strategy trades or the warehouse.

**Architecture:** Geometry columns are pure 1-bar math with no carry-forward, so per the spec (§6.1, critique H3) they go in as a param-independent `IndicatorGroup` appended last in `indicator_groups.GROUPS` AND emitted by `precompute_all_indicators` via one shared `app.indicators.candle_geometry` helper — byte-identical by construction (same helper, both paths). The structural features are stateful/causal and go through the SP-1 framework: a new `backend/app/features/structures.py` holds the compute fns (importing only pandas/numpy/`app.indicators`), each registered via `app.features.catalog.register_feature`. A new pure `feature_live_feasible()` helper in the registry derives the live-deployability flag from `session_anchored` / `stateful_unbounded` / `min_history_bars`, surfaced in the catalog for SP-3 to consume. Features read already-enriched indicator columns (`is_swing_high`, `is_swing_low`, `atr`) plus other features' columns via the framework's topo-sorted dependency closure.

**Tech Stack:** Python 3.12, pandas, numpy, pytest (host venv — no motor; tests insert `backend/` on `sys.path`).

---

## Background the implementer needs

**The SP-1 framework (already on this branch — do NOT re-implement):**

- `backend/app/features/registry.py`:
  - `FeatureGroup` (frozen dataclass): `name`, `columns: Tuple[str,...]`, `param_keys: Tuple[str,...]`, `requires: Tuple[str,...]`, `cost_class: str` (`"vectorized"` | `"session_loop"`), `session_anchored: bool`, `stateful_unbounded: bool`, `min_history_bars: int`, `compute: Callable[[pd.DataFrame, dict], Dict[str, pd.Series]]`, `causal: bool = True`.
  - `FEATURE_REGISTRY: Dict[str, FeatureGroup]`.
  - `resolve_features(required) -> List[FeatureGroup]` — dependency closure + topo-sort.
  - `materialize_features(df, params, required, feature_caches, *, max_per_group=4) -> pd.DataFrame` — copy-on-write; no-op early-return when `required` is empty.
  - `FeatureError(code, *, name="", available=None)`.
- `backend/app/features/catalog.py`:
  - `register_feature(group, *, description, data_requirements)` — registers into `FEATURE_REGISTRY` + `FEATURE_CATALOG` (idempotent by name).
  - `feature_catalog_entries() -> List[dict]` — the advertised feature list (drives grounding/AI).
  - `FEATURE_CATALOG: Dict[str, dict]`.

**Materialization happens inside `run_backtest`** (right after `df.reset_index`, fresh `{}` cache per call) ONLY when `strategy.required_features` is non-empty. The enriched frame already carries every indicator column (`atr`, `is_swing_high`, `is_swing_low`, `fvg`, ...) because indicator enrichment runs before `run_backtest` materializes features. So a feature compute fn may read indicator columns directly.

**Reuse helpers in `backend/app/indicators.py`:**
- `detect_fvg(df) -> pd.Series` — per-bar `"UP"` / `"DOWN"` / `None`; UP at bar i when `low[i] > high[i-2]`, DOWN when `high[i] < low[i-2]`.
- `detect_swing_points(df, lookback=5) -> pd.DataFrame` — adds boolean `is_swing_high` / `is_swing_low` (already enriched as columns by the `swing` indicator group).
- `fibonacci_levels(swing_high, swing_low) -> dict` (not needed here, FYI).

**Causality rule (the non-negotiable invariant):** truncating bars *after* index `i` must never change any feature value at index `i`. Achieved by trailing-window / `.shift(1)` / forward-pass-only constructs. Every structural feature gets a causality test that asserts this.

**Indicator helper home + byte-identical pattern (geometry):** add the helper to `app/indicators.py`, call it from BOTH `precompute_all_indicators` (monolithic golden path) and a new `_compute_geometry` group in `indicator_groups.py` (cached path), so `tests/test_indicator_equivalence.py` stays green by construction.

**Test conventions:** every new test module starts with
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
```
Run the host suite with `python -m pytest tests/ --continue-on-collection-errors -q` (baseline: ~2148 passed + 5 motor failures + 19 motor collection errors — those 24 are the unchanged motor-absent baseline, NOT regressions).

---

## File Structure

- **Create** `backend/app/features/structures.py` — the six structural compute fns + their `register_feature` calls (executed at import).
- **Modify** `backend/app/features/catalog.py` — `import app.features.structures` at the bottom so importing the package registers the seed features; surface `live_feasible` in `feature_catalog_entries()`.
- **Modify** `backend/app/features/registry.py` — add the pure `feature_live_feasible()` helper.
- **Modify** `backend/app/indicators.py` — add `candle_geometry()` helper + call it inside `precompute_all_indicators`.
- **Modify** `backend/app/indicator_groups.py` — add `_compute_geometry` + append the `geometry` group to `GROUPS`.
- **Create** `tests/test_candle_geometry.py` — geometry unit + byte-identical + causality.
- **Create** `tests/test_structural_features.py` — per-feature correctness + causality + dependency-resolution + parity (fvg/order_block slow-vs-impl).
- **Create** `tests/test_feature_live_feasibility.py` — the `feature_live_feasible()` classification + catalog surfacing.

---

## Task 1: Always-on candle geometry (indicator group, byte-identical)

**Files:**
- Modify: `backend/app/indicators.py` (add `candle_geometry`; call in `precompute_all_indicators`)
- Modify: `backend/app/indicator_groups.py` (add `_compute_geometry`; append `geometry` to `GROUPS`)
- Test: `tests/test_candle_geometry.py`

- [ ] **Step 1: Write the failing geometry unit test**

Create `tests/test_candle_geometry.py`:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np
import pandas as pd
import pytest

from app.indicators import candle_geometry


def _frame(rows):
    # rows: list of (open, high, low, close)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_body_and_wick_fractions_basic():
    # one bar: o=10 h=14 l=9 c=12 -> range=5, body=2, upper=14-12=2, lower=10-9=1
    df = _frame([(10, 14, 9, 12)])
    g = candle_geometry(df)
    assert g["body_frac"].iloc[0] == pytest.approx(2 / 5)
    assert g["upper_wick_frac"].iloc[0] == pytest.approx(2 / 5)
    assert g["lower_wick_frac"].iloc[0] == pytest.approx(1 / 5)


def test_zero_range_bar_is_safe():
    df = _frame([(10, 10, 10, 10)])
    g = candle_geometry(df)
    assert g["body_frac"].iloc[0] == 0.0
    assert g["upper_wick_frac"].iloc[0] == 0.0
    assert g["lower_wick_frac"].iloc[0] == 0.0


def test_inside_bar_flag():
    # bar1 wide (h=20,l=5); bar2 inside (h<20,l>5); bar3 not inside (h>prev h)
    df = _frame([(10, 20, 5, 12), (11, 18, 7, 14), (12, 25, 8, 20)])
    g = candle_geometry(df)
    assert bool(g["inside_bar"].iloc[0]) is False     # first bar: no prev
    assert bool(g["inside_bar"].iloc[1]) is True
    assert bool(g["inside_bar"].iloc[2]) is False


def test_close_z_is_nan_during_warmup_then_finite():
    close = pd.Series(np.linspace(100, 110, 80))
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close})
    g = candle_geometry(df, z_window=60)
    assert g["close_z"].iloc[:59].isna().all()        # warmup
    assert np.isfinite(g["close_z"].iloc[70])         # finite once full window
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_candle_geometry.py -q`
Expected: FAIL — `ImportError: cannot import name 'candle_geometry'`.

- [ ] **Step 3: Implement `candle_geometry` in `app/indicators.py`**

Add (place near the other pure helpers, e.g. just after `detect_swing_points`):
```python
def candle_geometry(df: pd.DataFrame, *, z_window: int = 60) -> Dict[str, pd.Series]:
    """Always-on 1-bar candle geometry (no carry-forward, fully causal).

    body_frac / upper_wick_frac / lower_wick_frac are fractions of the bar range
    (0 on a zero-range bar). inside_bar is 2-bar range containment. close_z is the
    trailing rolling z-score of close (NaN until `z_window` bars exist). These are
    additive columns no existing strategy references, so emitting them keeps both
    the monolithic and cached enrichment paths byte-identical for existing trades.
    """
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l)
    safe = rng.where(rng > 0, np.nan)
    body = (c - o).abs()
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    body_frac = (body / safe).fillna(0.0)
    upper_wick_frac = (upper / safe).fillna(0.0)
    lower_wick_frac = (lower / safe).fillna(0.0)
    inside_bar = ((h < h.shift(1)) & (l > l.shift(1))).fillna(False)
    mean = c.rolling(z_window, min_periods=z_window).mean()
    std = c.rolling(z_window, min_periods=z_window).std(ddof=0)
    close_z = (c - mean) / std.replace(0.0, np.nan)
    return {
        "body_frac": body_frac,
        "upper_wick_frac": upper_wick_frac,
        "lower_wick_frac": lower_wick_frac,
        "inside_bar": inside_bar,
        "close_z": close_z,
    }
```
Ensure `Dict` is imported in `indicators.py` (`from typing import Dict`); if not present add it. `np` and `pd` are already imported.

- [ ] **Step 4: Run the unit test — expect PASS**

Run: `python -m pytest tests/test_candle_geometry.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Wire geometry into BOTH enrichment paths**

In `app/indicators.py` `precompute_all_indicators(...)`, immediately before the function returns its enriched frame, add the geometry columns from the same helper:
```python
    for _gname, _gser in candle_geometry(df).items():
        df[_gname] = _gser
```
(Use whatever the local enriched-frame variable is named — match the surrounding code; it is the frame being returned.)

In `app/indicator_groups.py`:
- import the helper: add `candle_geometry` to the existing `from app.indicators import (...)` block.
- add the group compute fn near the other `_compute_*`:
```python
def _compute_geometry(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return candle_geometry(df)
```
- append to `GROUPS` (LAST entry, after `regime`):
```python
    IndicatorGroup("geometry", (), _compute_geometry),
```

- [ ] **Step 6: Add the byte-identical + group-equivalence test**

Append to `tests/test_candle_geometry.py`:
```python
def test_geometry_emitted_identically_by_both_paths():
    from app.indicators import precompute_all_indicators
    from app.indicator_groups import run_all_groups
    rng = np.random.default_rng(7)
    n = 300
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close + rng.uniform(0, 2, n),
        "low": close - rng.uniform(0, 2, n),
        "close": close,
        "volume": rng.integers(1, 100, n),
    })
    df.index = pd.date_range("2026-01-01 09:15", periods=n, freq="1min")
    params = {}
    mono = precompute_all_indicators(df.copy(), params)
    cached = run_all_groups(df.copy(), params)
    for col in ["body_frac", "upper_wick_frac", "lower_wick_frac", "inside_bar", "close_z"]:
        assert col in mono.columns and col in cached.columns
        pd.testing.assert_series_equal(
            mono[col], cached[col], check_names=False, check_dtype=False)
```
Note: if `precompute_all_indicators` / `run_all_groups` need specific params, pass the same `params={}` to both (param-independent groups ignore it). Adjust the synthetic-frame construction only if these functions require additional columns (they consume OHLCV).

- [ ] **Step 7: Run geometry tests + the full indicator-equivalence suite**

Run: `python -m pytest tests/test_candle_geometry.py tests/test_indicator_equivalence.py -q`
Expected: PASS (geometry tests + the existing monolithic-vs-cached golden parity stays green, proving the additive columns did not perturb existing indicators).

- [ ] **Step 8: Commit**

```bash
git add backend/app/indicators.py backend/app/indicator_groups.py tests/test_candle_geometry.py
git commit -m "feat(features): SP-2a always-on candle geometry indicator group (byte-identical)"
```

---

## Task 2: `feature_live_feasible()` helper (registry) + structures.py scaffold

**Files:**
- Modify: `backend/app/features/registry.py`
- Create: `backend/app/features/structures.py`
- Modify: `backend/app/features/catalog.py`
- Test: `tests/test_feature_live_feasibility.py`

- [ ] **Step 1: Write the failing feasibility test**

Create `tests/test_feature_live_feasibility.py`:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.features.registry import FeatureGroup, feature_live_feasible


def _g(**kw):
    base = dict(
        name="x", columns=("c",), param_keys=(), requires=(),
        cost_class="vectorized", session_anchored=False,
        stateful_unbounded=False, min_history_bars=10,
        compute=lambda df, p: {"c": df["close"]},
    )
    base.update(kw)
    return FeatureGroup(**base)


def test_vectorized_short_history_is_live_feasible():
    assert feature_live_feasible(_g()) is True


def test_session_anchored_is_not_live_feasible():
    assert feature_live_feasible(_g(session_anchored=True)) is False


def test_stateful_unbounded_is_not_live_feasible():
    assert feature_live_feasible(_g(stateful_unbounded=True)) is False


def test_long_history_exceeds_live_window():
    assert feature_live_feasible(_g(min_history_bars=200)) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_feature_live_feasibility.py -q`
Expected: FAIL — `cannot import name 'feature_live_feasible'`.

- [ ] **Step 3: Implement `feature_live_feasible` in `registry.py`**

Add at module scope:
```python
def feature_live_feasible(group: "FeatureGroup", *, live_window: int = 200,
                          max_history: int = 150) -> bool:
    """Pure derivation of live-deployability on the rolling live window.

    A feature is live-correct on the ~200-bar deployment window only when it is
    NOT session-anchored (needs this session's range), NOT stateful-unbounded
    (carry-forward selection may depend on history older than the window), and
    its warm-up fits inside the window with headroom. Otherwise it is
    backtest-only in v1 (SP-4's agent refuses live deploy + explains).
    """
    if group.session_anchored:
        return False
    if group.stateful_unbounded:
        return False
    if group.min_history_bars > max_history:
        return False
    return True
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/test_feature_live_feasibility.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Create the `structures.py` scaffold (header + imports only)**

Create `backend/app/features/structures.py`:
```python
"""SP-2 seed structural features (ICT vocabulary) for capability-aware authoring.

Each feature is causal (trailing-window / shift / forward-pass only), reuses the
pure helpers in app.indicators where possible, and is registered into the SP-1
FeatureGroup registry at import time via app.features.catalog.register_feature.

Host-importable: imports only pandas / numpy / app.indicators / app.features.* —
no motor, no I/O (same discipline as indicator_groups.py).

Live feasibility (see feature_live_feasible): swing_levels / premium_discount /
displacement are vectorized + bounded -> live-correct. fvg_zones / choch /
order_block are stateful_unbounded -> backtest-only in v1.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from app.features.registry import FeatureGroup
from app.features.catalog import register_feature

# ---- compute fns + registrations are appended by the following tasks ----
```

- [ ] **Step 6: Wire structures import + surface `live_feasible` in the catalog**

In `backend/app/features/catalog.py`:
- add `live_feasible` to each dict in `feature_catalog_entries()` (import the helper at top: `from app.features.registry import FEATURE_REGISTRY, FeatureGroup, feature_live_feasible`):
```python
            "live_feasible": feature_live_feasible(g),
```
- at the very bottom of `catalog.py`, register the seed features by importing the module for its side effects:
```python
# Importing structures registers the seed FeatureGroups (side-effect import at
# the bottom to avoid a circular import: structures imports register_feature).
from app.features import structures as _structures  # noqa: E402,F401
```

- [ ] **Step 7: Run — confirm import wiring is clean (empty structures still imports)**

Run: `python -m pytest tests/test_feature_live_feasibility.py tests/test_feature_registry.py -q`
Expected: PASS (no circular-import error; registry still has 0 features until Task 3 adds them).

- [ ] **Step 8: Commit**

```bash
git add backend/app/features/registry.py backend/app/features/structures.py backend/app/features/catalog.py tests/test_feature_live_feasibility.py
git commit -m "feat(features): SP-2 structures.py scaffold + feature_live_feasible() helper + catalog live_feasible"
```

---

## Task 3: `swing_levels` feature (foundation, vectorized)

**Files:**
- Modify: `backend/app/features/structures.py`
- Test: `tests/test_structural_features.py`

**Contract:** `swing_levels` — columns `last_swing_high_level`, `last_swing_low_level`, `swing_high_swept`, `swing_low_swept`. `cost_class="vectorized"`, `requires=()`, `session_anchored=False`, `stateful_unbounded=False`, `min_history_bars=2`. Reads enriched `is_swing_high` / `is_swing_low` / `high` / `low`.

Definitions (all causal):
- `last_swing_high_level = high.where(is_swing_high).ffill().shift(1)` — value of the most recent swing high confirmed at or before the *previous* bar (so the bar that sets a swing can't use its own level).
- `last_swing_low_level = low.where(is_swing_low).ffill().shift(1)`.
- `swing_high_swept = high > last_swing_high_level` (NaN level → False).
- `swing_low_swept = low < last_swing_low_level` (NaN level → False).

(Per spec §6.2 critique-L2, `prev_swing_*_level` columns are intentionally dropped — BOS in Task 5 uses `last_swing_*_level`, which is the standard break-of-structure reference, so the extra columns add complexity with no consumer.)

- [ ] **Step 1: Write the failing test**

Create `tests/test_structural_features.py`:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np
import pandas as pd
import pytest

from app.features.registry import FEATURE_REGISTRY, resolve_features, materialize_features
import app.features.catalog  # noqa: F401  -> registers seed features


def _ohlcv(n=400, seed=3):
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1.0, n)))
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close + rng.uniform(0.1, 2.0, n),
        "low": close - rng.uniform(0.1, 2.0, n),
        "close": close,
        "volume": rng.integers(1, 100, n).astype(float),
    })
    df.index = pd.date_range("2026-01-01 09:15", periods=n, freq="1min")
    return df


def _enrich(df, params):
    """Enrich with all indicator groups (provides is_swing_high/low, atr, etc.)."""
    from app.indicator_groups import run_all_groups
    return run_all_groups(df.copy(), params)


def _materialize(df, params, required):
    return materialize_features(df.reset_index(drop=True), params, required, {})


def test_swing_levels_registered():
    assert "swing_levels" in FEATURE_REGISTRY
    g = FEATURE_REGISTRY["swing_levels"]
    assert set(g.columns) == {
        "last_swing_high_level", "last_swing_low_level",
        "swing_high_swept", "swing_low_swept"}
    assert g.cost_class == "vectorized"
    assert g.stateful_unbounded is False and g.session_anchored is False


def test_swing_levels_values_and_causality():
    params = {"swing_lookback": 5}
    df = _enrich(_ohlcv(), params)
    out = _materialize(df, params, ["swing_levels"])
    # last_swing_high_level is the ffill+shift of high at swing-high bars
    is_sh = df["is_swing_high"].reset_index(drop=True)
    expected = df["high"].reset_index(drop=True).where(is_sh).ffill().shift(1)
    pd.testing.assert_series_equal(
        out["last_swing_high_level"], expected, check_names=False)
    # swept flag never True where level is NaN
    assert not (out["swing_high_swept"] & out["last_swing_high_level"].isna()).any()


def test_swing_levels_is_causal_under_truncation():
    params = {"swing_lookback": 5}
    full = _enrich(_ohlcv(), params)
    i = 250
    out_full = _materialize(full, params, ["swing_levels"])
    trunc = _enrich(_ohlcv().iloc[: i + 1], params)
    out_trunc = _materialize(trunc, params, ["swing_levels"])
    for col in ["last_swing_high_level", "last_swing_low_level"]:
        a = out_full[col].iloc[i]
        b = out_trunc[col].iloc[i]
        assert (pd.isna(a) and pd.isna(b)) or a == pytest.approx(b)
```

Note the causality test re-enriches the truncated frame: because `is_swing_high` (from the `swing` group) is itself causal (trailing window), truncation must not change `is_swing_high[i]` and therefore must not change the feature at `i`. If `_enrich` of the truncated frame changes a *swing* value at `i` (it must not — `detect_swing_points` is causal), that is a separate pre-existing bug; the assertion guards the whole chain.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_structural_features.py -q`
Expected: FAIL — `swing_levels` not in `FEATURE_REGISTRY`.

- [ ] **Step 3: Implement + register `swing_levels` in `structures.py`**

Append to `structures.py`:
```python
def compute_swing_levels(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    high, low = df["high"], df["low"]
    is_sh, is_sl = df["is_swing_high"], df["is_swing_low"]
    last_high = high.where(is_sh).ffill().shift(1)
    last_low = low.where(is_sl).ffill().shift(1)
    swept_high = (high > last_high).fillna(False)
    swept_low = (low < last_low).fillna(False)
    return {
        "last_swing_high_level": last_high,
        "last_swing_low_level": last_low,
        "swing_high_swept": swept_high,
        "swing_low_swept": swept_low,
    }


register_feature(
    FeatureGroup(
        name="swing_levels",
        columns=("last_swing_high_level", "last_swing_low_level",
                 "swing_high_swept", "swing_low_swept"),
        param_keys=(),
        requires=(),
        cost_class="vectorized",
        session_anchored=False,
        stateful_unbounded=False,
        min_history_bars=2,
        compute=compute_swing_levels,
    ),
    description="Most-recent confirmed swing high/low price levels (causal, shifted) "
                "plus liquidity-sweep flags. Foundation for premium/discount, BOS, "
                "and order blocks.",
    data_requirements=["ohlcv_1m"],
)
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/test_structural_features.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/features/structures.py tests/test_structural_features.py
git commit -m "feat(features): SP-2 swing_levels feature (causal, vectorized)"
```

---

## Task 4: `premium_discount` feature (vectorized, requires swing_levels)

**Files:**
- Modify: `backend/app/features/structures.py`
- Test: `tests/test_structural_features.py`

**Contract:** `premium_discount` — columns `premium_discount_pct`, `range_state`. `cost_class="vectorized"`, `requires=("swing_levels",)`, `session_anchored=False`, `stateful_unbounded=False`, `min_history_bars=2`.
- `premium_discount_pct = 100 * (close - last_swing_low_level) / (last_swing_high_level - last_swing_low_level)` (NaN when the range is non-positive or levels NaN).
- `range_state`: `"premium"` when `pct > 55`, `"discount"` when `pct < 45`, else `"equilibrium"`; `None` when `pct` is NaN.

- [ ] **Step 1: Write the failing test** (append to `tests/test_structural_features.py`)

```python
def test_premium_discount_values():
    params = {"swing_lookback": 5}
    df = _enrich(_ohlcv(), params)
    out = _materialize(df, params, ["premium_discount"])
    hi = out["last_swing_high_level"]
    lo = out["last_swing_low_level"]
    rng = (hi - lo)
    valid = rng > 0
    exp = 100 * (df["close"].reset_index(drop=True) - lo) / rng.where(valid, np.nan)
    pd.testing.assert_series_equal(
        out["premium_discount_pct"], exp, check_names=False)
    # state agrees with pct on valid bars
    prem = out["range_state"] == "premium"
    assert (out.loc[prem, "premium_discount_pct"] > 55).all()


def test_premium_discount_requires_swing_levels_auto_resolved():
    # declaring only premium_discount must pull in swing_levels via the DAG
    groups = [g.name for g in resolve_features(["premium_discount"])]
    assert groups.index("swing_levels") < groups.index("premium_discount")
```

- [ ] **Step 2: Run — expect FAIL** (`premium_discount` not registered).

Run: `python -m pytest tests/test_structural_features.py -k premium -q`

- [ ] **Step 3: Implement + register** (append to `structures.py`)

```python
def compute_premium_discount(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    hi = df["last_swing_high_level"]
    lo = df["last_swing_low_level"]
    rng = (hi - lo)
    pct = 100.0 * (df["close"] - lo) / rng.where(rng > 0, np.nan)
    state = np.where(pct.isna(), None,
             np.where(pct > 55.0, "premium",
              np.where(pct < 45.0, "discount", "equilibrium")))
    return {
        "premium_discount_pct": pct,
        "range_state": pd.Series(state, index=df.index, dtype=object),
    }


register_feature(
    FeatureGroup(
        name="premium_discount",
        columns=("premium_discount_pct", "range_state"),
        param_keys=(),
        requires=("swing_levels",),
        cost_class="vectorized",
        session_anchored=False,
        stateful_unbounded=False,
        min_history_bars=2,
        compute=compute_premium_discount,
    ),
    description="Position of price within the last swing range as a 0-100 percent "
                "(premium >55, discount <45, equilibrium between). Requires swing_levels.",
    data_requirements=["ohlcv_1m"],
)
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/test_structural_features.py -k "premium or swing" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/features/structures.py tests/test_structural_features.py
git commit -m "feat(features): SP-2 premium_discount feature (requires swing_levels)"
```

---

## Task 5: `displacement` feature (vectorized, requires swing_levels) — displacement + BOS

**Files:**
- Modify: `backend/app/features/structures.py`
- Test: `tests/test_structural_features.py`

**Contract:** `displacement` — columns `displacement`, `bos_up`, `bos_down`. `cost_class="vectorized"`, `requires=("swing_levels",)`, `session_anchored=False`, `stateful_unbounded=False`, `min_history_bars=2`. Param keys `("disp_atr_mult", "disp_body_frac_min")` (defaults 1.5 / 0.5).
- `displacement = (|close-open| >= disp_atr_mult*atr) & (|close-open|/range >= disp_body_frac_min)` (range>0; NaN atr → False).
- `bos_up = close > last_swing_high_level` (break of structure up); `bos_down = close < last_swing_low_level` (NaN level → False).

Reads enriched `atr`.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_displacement_and_bos():
    params = {"swing_lookback": 5, "disp_atr_mult": 1.5, "disp_body_frac_min": 0.5}
    df = _enrich(_ohlcv(), params)
    out = _materialize(df, params, ["displacement"])
    atr = df["atr"].reset_index(drop=True)
    o = df["open"].reset_index(drop=True)
    c = df["close"].reset_index(drop=True)
    h = df["high"].reset_index(drop=True)
    l = df["low"].reset_index(drop=True)
    body = (c - o).abs()
    rng = (h - l)
    exp_disp = (body >= 1.5 * atr) & ((body / rng.where(rng > 0, np.nan)) >= 0.5)
    exp_disp = exp_disp.fillna(False)
    pd.testing.assert_series_equal(
        out["displacement"].astype(bool), exp_disp.astype(bool), check_names=False)
    # bos_up only where close exceeds the (shifted) last swing high
    assert (out.loc[out["bos_up"], "close"]
            > out.loc[out["bos_up"], "last_swing_high_level"]).all()


def test_displacement_param_keys():
    g = FEATURE_REGISTRY["displacement"]
    assert set(g.param_keys) == {"disp_atr_mult", "disp_body_frac_min"}
```

- [ ] **Step 2: Run — expect FAIL.**

Run: `python -m pytest tests/test_structural_features.py -k displacement -q`

- [ ] **Step 3: Implement + register** (append to `structures.py`)

```python
def compute_displacement(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    atr_mult = float(params.get("disp_atr_mult", 1.5))
    body_min = float(params.get("disp_body_frac_min", 0.5))
    o, c, h, l = df["open"], df["close"], df["high"], df["low"]
    atr = df["atr"]
    body = (c - o).abs()
    rng = (h - l)
    body_frac = body / rng.where(rng > 0, np.nan)
    disp = ((body >= atr_mult * atr) & (body_frac >= body_min)).fillna(False)
    bos_up = (c > df["last_swing_high_level"]).fillna(False)
    bos_down = (c < df["last_swing_low_level"]).fillna(False)
    return {"displacement": disp, "bos_up": bos_up, "bos_down": bos_down}


register_feature(
    FeatureGroup(
        name="displacement",
        columns=("displacement", "bos_up", "bos_down"),
        param_keys=("disp_atr_mult", "disp_body_frac_min"),
        requires=("swing_levels",),
        cost_class="vectorized",
        session_anchored=False,
        stateful_unbounded=False,
        min_history_bars=2,
        compute=compute_displacement,
    ),
    description="Displacement (large impulsive body vs ATR) and break-of-structure "
                "flags (close beyond the last swing level). Requires swing_levels.",
    data_requirements=["ohlcv_1m"],
)
```

- [ ] **Step 4: Run — expect PASS.**

Run: `python -m pytest tests/test_structural_features.py -k "displacement or swing" -q`

- [ ] **Step 5: Commit**

```bash
git add backend/app/features/structures.py tests/test_structural_features.py
git commit -m "feat(features): SP-2 displacement + BOS feature (requires swing_levels)"
```

---

## Task 6: `choch` feature (session_loop, stateful_unbounded, requires displacement)

**Files:**
- Modify: `backend/app/features/structures.py`
- Test: `tests/test_structural_features.py`

**Contract:** `choch` — columns `choch_up`, `choch_down`. `cost_class="session_loop"`, `requires=("displacement",)`, `session_anchored=False`, `stateful_unbounded=True`, `min_history_bars=2`. Forward pass over `bos_up`/`bos_down`: maintain running structural direction (0/±1); a `bos_up` while running dir is `-1` flags `choch_up` at that bar (and flips dir to +1); symmetric for `choch_down`.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_choch_flips_on_direction_change():
    # construct bos_up/bos_down directly and run the reference forward pass
    from app.features.structures import compute_choch
    df = pd.DataFrame({
        "bos_up":   [False, True, False, False, True, False],
        "bos_down": [False, False, False, True, False, False],
    })
    out = compute_choch(df, {})
    # bar1 first up -> dir +1, no choch (was 0). bar3 down -> choch_down. bar4 up -> choch_up.
    assert out["choch_down"].tolist() == [False, False, False, True, False, False]
    assert out["choch_up"].tolist() == [False, False, False, False, True, False]


def test_choch_is_stateful_unbounded_and_backtest_only():
    from app.features.registry import feature_live_feasible
    g = FEATURE_REGISTRY["choch"]
    assert g.stateful_unbounded is True
    assert feature_live_feasible(g) is False


def test_choch_causal_under_truncation():
    params = {"swing_lookback": 5}
    df = _enrich(_ohlcv(), params)
    out_full = _materialize(df, params, ["choch"])
    i = 200
    df_t = _enrich(_ohlcv().iloc[: i + 1], params)
    out_t = _materialize(df_t, params, ["choch"])
    # a forward pass over [0..i] is identical whether or not bars >i exist
    assert bool(out_full["choch_up"].iloc[i]) == bool(out_t["choch_up"].iloc[i])
    assert bool(out_full["choch_down"].iloc[i]) == bool(out_t["choch_down"].iloc[i])
```

- [ ] **Step 2: Run — expect FAIL** (`compute_choch` undefined).

Run: `python -m pytest tests/test_structural_features.py -k choch -q`

- [ ] **Step 3: Implement + register** (append to `structures.py`)

```python
def compute_choch(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    bu = df["bos_up"].to_numpy(dtype=bool, na_value=False)
    bd = df["bos_down"].to_numpy(dtype=bool, na_value=False)
    n = len(df)
    up = np.zeros(n, dtype=bool)
    down = np.zeros(n, dtype=bool)
    direction = 0
    for i in range(n):
        new = direction
        if bu[i]:
            new = 1
        elif bd[i]:
            new = -1
        if new == 1 and direction == -1:
            up[i] = True
        elif new == -1 and direction == 1:
            down[i] = True
        direction = new
    return {
        "choch_up": pd.Series(up, index=df.index),
        "choch_down": pd.Series(down, index=df.index),
    }


register_feature(
    FeatureGroup(
        name="choch",
        columns=("choch_up", "choch_down"),
        param_keys=(),
        requires=("displacement",),
        cost_class="session_loop",
        session_anchored=False,
        stateful_unbounded=True,
        min_history_bars=2,
        compute=compute_choch,
    ),
    description="Change-of-character: the running market-structure direction flips "
                "(bullish<->bearish) on a counter break of structure. Stateful "
                "(depends on history before the rolling window) -> backtest-only in v1.",
    data_requirements=["ohlcv_1m"],
)
```
Note `to_numpy(na_value=...)` requires a nullable/bool series; `bos_up`/`bos_down` are produced with `.fillna(False)` in Task 5 so they are plain bool — `na_value` is harmless. If `to_numpy(dtype=bool, na_value=False)` raises on a plain bool series in the host pandas, use `df["bos_up"].fillna(False).to_numpy(dtype=bool)`.

- [ ] **Step 4: Run — expect PASS.**

Run: `python -m pytest tests/test_structural_features.py -k choch -q`

- [ ] **Step 5: Commit**

```bash
git add backend/app/features/structures.py tests/test_structural_features.py
git commit -m "feat(features): SP-2 choch feature (session_loop, backtest-only)"
```

---

## Task 7: `fvg_zones` feature (session_loop, stateful_unbounded) + slow-vs-impl parity

**Files:**
- Modify: `backend/app/features/structures.py`
- Test: `tests/test_structural_features.py`

**Contract:** `fvg_zones` — columns `fvg_top`, `fvg_bottom`, `fvg_ce`, `fvg_dir`, `fvg_state`. `cost_class="session_loop"`, `requires=()`, `session_anchored=False`, `stateful_unbounded=True`, `min_history_bars=3`.
- Detection (vectorized via `detect_fvg`): at bar i, UP gap = `(high[i-2], low[i])` ordered `(bottom, top)`; DOWN gap = `(high[i], low[i-2])` ordered `(bottom, top)`.
- Forward pass carries the most-recently-formed gap as the active zone until price fills it: for an active UP gap, filled when `low[i] <= bottom`; for DOWN, filled when `high[i] >= top`. A newly-formed gap at bar i replaces the carried zone (state `active`). `fvg_ce` = midpoint. `fvg_state ∈ {active, filled, none}`; `fvg_dir ∈ {UP, DOWN, None}`. While no gap has ever formed: top/bottom/ce NaN, dir None, state `none`.

- [ ] **Step 1: Write the failing test** (append) — slow reference + parity + causality

```python
def _fvg_reference(df):
    """Obvious O(N) reference: detect gaps, carry the latest until filled."""
    fdir = df.get("fvg")
    if fdir is None:
        from app.indicators import detect_fvg
        fdir = detect_fvg(df)
    fdir = fdir.reset_index(drop=True)
    high = df["high"].reset_index(drop=True).to_numpy()
    low = df["low"].reset_index(drop=True).to_numpy()
    n = len(df)
    top = np.full(n, np.nan); bot = np.full(n, np.nan)
    state = np.array([None] * n, dtype=object)
    direction = np.array([None] * n, dtype=object)
    cur_top = cur_bot = np.nan; cur_dir = None; cur_state = "none"
    for i in range(n):
        d = fdir.iloc[i]
        if d == "UP" and i >= 2:
            cur_bot, cur_top, cur_dir, cur_state = high[i - 2], low[i], "UP", "active"
        elif d == "DOWN" and i >= 2:
            cur_bot, cur_top, cur_dir, cur_state = high[i], low[i - 2], "DOWN", "active"
        elif cur_state == "active":
            if cur_dir == "UP" and low[i] <= cur_bot:
                cur_state = "filled"
            elif cur_dir == "DOWN" and high[i] >= cur_top:
                cur_state = "filled"
        top[i], bot[i], direction[i], state[i] = cur_top, cur_bot, cur_dir, cur_state
    return pd.DataFrame({"fvg_top": top, "fvg_bottom": bot, "fvg_dir": direction,
                         "fvg_state": state})


def test_fvg_zones_matches_reference():
    params = {}
    df = _enrich(_ohlcv(seed=11), params)
    out = _materialize(df, params, ["fvg_zones"])
    ref = _fvg_reference(df)
    pd.testing.assert_series_equal(out["fvg_top"], ref["fvg_top"], check_names=False)
    pd.testing.assert_series_equal(out["fvg_bottom"], ref["fvg_bottom"], check_names=False)
    assert out["fvg_dir"].tolist() == ref["fvg_dir"].tolist()
    assert out["fvg_state"].tolist() == ref["fvg_state"].tolist()
    fin = out["fvg_top"].notna()
    exp_ce = (out["fvg_top"] + out["fvg_bottom"]) / 2
    pd.testing.assert_series_equal(out.loc[fin, "fvg_ce"], exp_ce.loc[fin], check_names=False)


def test_fvg_zones_backtest_only():
    from app.features.registry import feature_live_feasible
    assert feature_live_feasible(FEATURE_REGISTRY["fvg_zones"]) is False


def test_fvg_zones_causal_under_truncation():
    params = {}
    df = _enrich(_ohlcv(seed=11), params)
    out_full = _materialize(df, params, ["fvg_zones"])
    i = 180
    df_t = _enrich(_ohlcv(seed=11).iloc[: i + 1], params)
    out_t = _materialize(df_t, params, ["fvg_zones"])
    a, b = out_full["fvg_top"].iloc[i], out_t["fvg_top"].iloc[i]
    assert (pd.isna(a) and pd.isna(b)) or a == pytest.approx(b)
    assert out_full["fvg_state"].iloc[i] == out_t["fvg_state"].iloc[i]
```

- [ ] **Step 2: Run — expect FAIL** (`fvg_zones` not registered).

Run: `python -m pytest tests/test_structural_features.py -k fvg -q`

- [ ] **Step 3: Implement + register** (append to `structures.py`)

```python
def compute_fvg_zones(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    from app.indicators import detect_fvg
    fdir = df["fvg"] if "fvg" in df.columns else detect_fvg(df)
    fdir = fdir.to_numpy(dtype=object)
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)
    top = np.full(n, np.nan)
    bot = np.full(n, np.nan)
    ce = np.full(n, np.nan)
    state = np.empty(n, dtype=object)
    direction = np.empty(n, dtype=object)
    cur_top = cur_bot = np.nan
    cur_dir = None
    cur_state = "none"
    for i in range(n):
        d = fdir[i]
        if d == "UP" and i >= 2:
            cur_bot, cur_top, cur_dir, cur_state = high[i - 2], low[i], "UP", "active"
        elif d == "DOWN" and i >= 2:
            cur_bot, cur_top, cur_dir, cur_state = high[i], low[i - 2], "DOWN", "active"
        elif cur_state == "active":
            if cur_dir == "UP" and low[i] <= cur_bot:
                cur_state = "filled"
            elif cur_dir == "DOWN" and high[i] >= cur_top:
                cur_state = "filled"
        top[i] = cur_top
        bot[i] = cur_bot
        ce[i] = (cur_top + cur_bot) / 2.0 if cur_dir is not None else np.nan
        direction[i] = cur_dir
        state[i] = cur_state
    idx = df.index
    return {
        "fvg_top": pd.Series(top, index=idx),
        "fvg_bottom": pd.Series(bot, index=idx),
        "fvg_ce": pd.Series(ce, index=idx),
        "fvg_dir": pd.Series(direction, index=idx, dtype=object),
        "fvg_state": pd.Series(state, index=idx, dtype=object),
    }


register_feature(
    FeatureGroup(
        name="fvg_zones",
        columns=("fvg_top", "fvg_bottom", "fvg_ce", "fvg_dir", "fvg_state"),
        param_keys=(),
        requires=(),
        cost_class="session_loop",
        session_anchored=False,
        stateful_unbounded=True,
        min_history_bars=3,
        compute=compute_fvg_zones,
    ),
    description="Fair Value Gap zones: the active 3-candle imbalance boundaries "
                "(top/bottom/midpoint), direction, and fill state. The active gap may "
                "predate the rolling window -> backtest-only in v1.",
    data_requirements=["ohlcv_1m"],
)
```

- [ ] **Step 4: Run — expect PASS.**

Run: `python -m pytest tests/test_structural_features.py -k fvg -q`

- [ ] **Step 5: Commit**

```bash
git add backend/app/features/structures.py tests/test_structural_features.py
git commit -m "feat(features): SP-2 fvg_zones feature (session_loop, parity-tested, backtest-only)"
```

---

## Task 8: `order_block` feature (session_loop, requires displacement, bounded lookback)

**Files:**
- Modify: `backend/app/features/structures.py`
- Test: `tests/test_structural_features.py`

**Contract:** `order_block` — columns `ob_top`, `ob_bottom`, `ob_dir`, `ob_active`. `cost_class="session_loop"`, `requires=("displacement",)`, `session_anchored=False`, `stateful_unbounded=True`, `min_history_bars=2`. Param keys `("ob_lookback",)` default 10, hard-capped at 20.
- At a bullish displacement bar (`displacement` & `close>open`): the order block is the most recent *down* candle (`close<open`) within the prior `ob_lookback` bars; `ob_top`/`ob_bottom` = that candle's `high`/`low`; `ob_dir="bull"`; `ob_active=True`. Symmetric for a bearish displacement (`close<open`) → most recent *up* candle, `ob_dir="bear"`.
- Carry the OB forward until mitigated: a bull OB is mitigated when `low[i] <= ob_bottom`; a bear OB when `high[i] >= ob_top` → `ob_active=False` (levels still carried for reference). A new OB replaces the carried one.
- Bounded scan: `ob_lookback` is a small constant (≤20), so the per-displacement back-scan is O(20), never O(N²).

- [ ] **Step 1: Write the failing test** (append) — reference + bounded-lookback + causality

```python
def _ob_reference(df, lookback=10):
    o = df["open"].reset_index(drop=True).to_numpy()
    h = df["high"].reset_index(drop=True).to_numpy()
    l = df["low"].reset_index(drop=True).to_numpy()
    c = df["close"].reset_index(drop=True).to_numpy()
    disp = df["displacement"].reset_index(drop=True).to_numpy(dtype=bool)
    n = len(df)
    lb = min(int(lookback), 20)
    top = np.full(n, np.nan); bot = np.full(n, np.nan)
    direction = np.array([None] * n, dtype=object); active = np.zeros(n, dtype=bool)
    cur_top = cur_bot = np.nan; cur_dir = None; cur_active = False
    for i in range(n):
        if disp[i] and c[i] > o[i]:
            for j in range(i - 1, max(-1, i - 1 - lb), -1):
                if c[j] < o[j]:
                    cur_top, cur_bot, cur_dir, cur_active = h[j], l[j], "bull", True
                    break
        elif disp[i] and c[i] < o[i]:
            for j in range(i - 1, max(-1, i - 1 - lb), -1):
                if c[j] > o[j]:
                    cur_top, cur_bot, cur_dir, cur_active = h[j], l[j], "bear", True
                    break
        elif cur_active:
            if cur_dir == "bull" and l[i] <= cur_bot:
                cur_active = False
            elif cur_dir == "bear" and h[i] >= cur_top:
                cur_active = False
        top[i], bot[i], direction[i], active[i] = cur_top, cur_bot, cur_dir, cur_active
    return pd.DataFrame({"ob_top": top, "ob_bottom": bot, "ob_dir": direction,
                         "ob_active": active})


def test_order_block_matches_reference():
    params = {"swing_lookback": 5, "ob_lookback": 10}
    df = _enrich(_ohlcv(seed=21), params)
    df = _materialize(df, params, ["displacement"])  # need displacement column present
    # materialize order_block on a frame that already has displacement
    from app.features.structures import compute_order_block
    out = compute_order_block(df, params)
    ref = _ob_reference(df, lookback=10)
    pd.testing.assert_series_equal(
        pd.Series(out["ob_top"]).reset_index(drop=True), ref["ob_top"], check_names=False)
    assert list(out["ob_dir"]) == ref["ob_dir"].tolist()
    assert list(out["ob_active"].astype(bool)) == ref["ob_active"].tolist()


def test_order_block_requires_displacement_chain():
    groups = [g.name for g in resolve_features(["order_block"])]
    # full chain: swing_levels -> displacement -> order_block
    assert groups.index("swing_levels") < groups.index("displacement") < groups.index("order_block")


def test_order_block_lookback_hard_capped():
    params = {"ob_lookback": 999}
    df = _enrich(_ohlcv(seed=21), params)
    df = _materialize(df, params, ["displacement"])
    from app.features.structures import compute_order_block
    out = compute_order_block(df, params)  # must not raise / not O(N^2) blow-up
    assert "ob_top" in out
```

- [ ] **Step 2: Run — expect FAIL** (`compute_order_block` undefined).

Run: `python -m pytest tests/test_structural_features.py -k order_block -q`

- [ ] **Step 3: Implement + register** (append to `structures.py`)

```python
def compute_order_block(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    lb = min(int(params.get("ob_lookback", 10)), 20)
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    disp = df["displacement"].to_numpy(dtype=bool)
    n = len(df)
    top = np.full(n, np.nan)
    bot = np.full(n, np.nan)
    direction = np.empty(n, dtype=object)
    active = np.zeros(n, dtype=bool)
    cur_top = cur_bot = np.nan
    cur_dir = None
    cur_active = False
    for i in range(n):
        if disp[i] and c[i] > o[i]:
            for j in range(i - 1, max(-1, i - 1 - lb), -1):
                if c[j] < o[j]:
                    cur_top, cur_bot, cur_dir, cur_active = h[j], l[j], "bull", True
                    break
        elif disp[i] and c[i] < o[i]:
            for j in range(i - 1, max(-1, i - 1 - lb), -1):
                if c[j] > o[j]:
                    cur_top, cur_bot, cur_dir, cur_active = h[j], l[j], "bear", True
                    break
        elif cur_active:
            if cur_dir == "bull" and l[i] <= cur_bot:
                cur_active = False
            elif cur_dir == "bear" and h[i] >= cur_top:
                cur_active = False
        top[i] = cur_top
        bot[i] = cur_bot
        direction[i] = cur_dir
        active[i] = cur_active
    idx = df.index
    return {
        "ob_top": pd.Series(top, index=idx),
        "ob_bottom": pd.Series(bot, index=idx),
        "ob_dir": pd.Series(direction, index=idx, dtype=object),
        "ob_active": pd.Series(active, index=idx),
    }


register_feature(
    FeatureGroup(
        name="order_block",
        columns=("ob_top", "ob_bottom", "ob_dir", "ob_active"),
        param_keys=("ob_lookback",),
        requires=("displacement",),
        cost_class="session_loop",
        session_anchored=False,
        stateful_unbounded=True,
        min_history_bars=2,
        compute=compute_order_block,
    ),
    description="Order block: the last opposing candle before a displacement (bounded "
                "lookback <=20), carried until mitigated. Requires displacement; "
                "stateful -> backtest-only in v1.",
    data_requirements=["ohlcv_1m"],
)
```

- [ ] **Step 4: Run — expect PASS.**

Run: `python -m pytest tests/test_structural_features.py -k order_block -q`

- [ ] **Step 5: Commit**

```bash
git add backend/app/features/structures.py tests/test_structural_features.py
git commit -m "feat(features): SP-2 order_block feature (bounded lookback, backtest-only)"
```

---

## Task 9: Integration — full materialization, catalog advertisement, byte-identical regression

**Files:**
- Test: `tests/test_structural_features.py` (append), `tests/test_feature_live_feasibility.py` (append)
- Possibly modify: `backend/app/ai/grounding.py` only if it does NOT already enumerate `feature_catalog_entries()` (verify first; SP-1 wired grounding to advertise features — confirm it still surfaces the now-non-empty registry).

- [ ] **Step 1: Write the end-to-end materialization + advertisement test** (append to `tests/test_structural_features.py`)

```python
def test_all_features_materialize_together():
    params = {"swing_lookback": 5}
    df = _enrich(_ohlcv(seed=31), params)
    required = ["premium_discount", "order_block", "fvg_zones", "choch"]
    out = _materialize(df, params, required)
    for col in ["last_swing_high_level", "premium_discount_pct", "range_state",
                "displacement", "bos_up", "choch_up", "fvg_top", "fvg_state",
                "ob_top", "ob_active"]:
        assert col in out.columns, col
    assert len(out) == len(df)


def test_catalog_advertises_all_seed_features():
    from app.features.catalog import feature_catalog_entries
    names = {e["feature"] for e in feature_catalog_entries()}
    assert {"swing_levels", "premium_discount", "displacement", "choch",
            "fvg_zones", "order_block"} <= names
    by = {e["feature"]: e for e in feature_catalog_entries()}
    assert by["swing_levels"]["live_feasible"] is True
    assert by["premium_discount"]["live_feasible"] is True
    assert by["displacement"]["live_feasible"] is True
    assert by["fvg_zones"]["live_feasible"] is False
    assert by["choch"]["live_feasible"] is False
    assert by["order_block"]["live_feasible"] is False
```

- [ ] **Step 2: Run the new integration test — expect PASS**

Run: `python -m pytest tests/test_structural_features.py::test_all_features_materialize_together tests/test_structural_features.py::test_catalog_advertises_all_seed_features -q`

- [ ] **Step 3: Verify grounding advertises the seed features (no code change if already wired)**

Run a quick check that `capability`/grounding surfaces them:
```bash
python -c "import sys; sys.path.insert(0,'backend'); import app.features.catalog as c; print(sorted(e['feature'] for e in c.feature_catalog_entries()))"
```
Expected: the six feature names printed. If `app/ai/grounding.py` builds its feature list from `feature_catalog_entries()` (SP-1 wiring), nothing to change. If it hard-coded an empty list, update it to call `feature_catalog_entries()` (and add/adjust the relevant grounding test). Do NOT materialize features eagerly anywhere that would change existing non-declaring behaviour.

- [ ] **Step 4: Byte-identical regression — existing strategies unaffected**

Run the indicator-equivalence + any existing backtest determinism / strategy-trade tests:
```bash
python -m pytest tests/test_indicator_equivalence.py tests/test_feature_registry.py tests/test_required_features_wiring.py -q
```
Expected: PASS. The geometry columns are additive; no strategy declares the new structural features, so `materialize_features` is never invoked for them → existing trades identical.

- [ ] **Step 5: Full host suite — confirm no regressions vs baseline**

Run: `python -m pytest tests/ --continue-on-collection-errors -q`
Expected: prior passing count + the new tests all pass; the ONLY failures/errors are the unchanged motor-absent baseline (~5 failures + ~19 collection errors). If any *non-motor* test newly fails, stop and treat it as a regression (systematic-debugging).

- [ ] **Step 6: Commit**

```bash
git add tests/test_structural_features.py tests/test_feature_live_feasibility.py backend/app/ai/grounding.py
git commit -m "test(features): SP-2 end-to-end materialization + catalog advertisement + byte-identical regression"
```
(Only `git add` `grounding.py` if Step 3 actually modified it.)

---

## Self-Review (run before handing off)

**1. Spec coverage (§6 of the design):**
- §6.1 always-on geometry (body_frac, upper/lower_wick_frac, inside_bar, close_z) → Task 1. ✓ (the optional band columns bb/keltner/donchian are explicitly deferred — YAGNI for v1; not required by any seed-feature consumer.)
- §6.2 swing_levels → Task 3; premium_discount → Task 4; displacement+BOS → Task 5; CHoCH → Task 6; fvg_zones → Task 7; order_block → Task 8. ✓
- §6.2 dependency DAG (swing_levels → {premium_discount, displacement}; displacement → order_block; fvg_zones independent; choch → displacement) → enforced by `requires=` + tested in Tasks 4/7/8. ✓
- §6.3 live-feasibility classification (session_anchored / stateful_unbounded) → `feature_live_feasible()` (Task 2) + per-feature flags + catalog surfacing (Task 9). ✓
- §5.5 byte-identical gate → Task 1 (geometry both paths) + Task 9 Steps 4-5. ✓

**2. Placeholder scan:** every code step contains complete code; every test step asserts concrete values; no TBD/TODO. ✓

**3. Type consistency:** `compute_*(df, params) -> Dict[str, pd.Series]` uniformly; `FeatureGroup(...)` kwargs match the SP-1 dataclass exactly; column tuples in each `register_feature` match the keys returned by its compute fn; `feature_live_feasible` signature consistent between registry definition (Task 2) and test/catalog use. ✓

**4. Intentional deviations from spec (documented):**
- `prev_swing_*_level` columns dropped (spec §6.2 critique-L2 explicitly permits this) — BOS uses `last_swing_*_level`.
- `choch` split into its own `session_loop` group (rather than folded into `displacement`) to keep `cost_class` honest per group; DAG unchanged (choch → displacement).
- Optional geometry band columns (bb/keltner/donchian) deferred — no v1 consumer.

---

## Execution note

Implement via **superpowers:subagent-driven-development**: one fresh subagent per task (Tasks 1→9 in order — they have a strict dependency chain: geometry, then registry helper + scaffold, then swing_levels before premium_discount/displacement, displacement before choch/order_block), each followed by the two-stage review (spec-compliance reviewer + code-quality reviewer). Pay special attention in review to: (a) causality (no future leakage — the truncation tests are the guard), (b) byte-identical geometry (both enrichment paths), and (c) the live_feasible flags being correct (the SP-4 agent will trust them to gate live deploys).
