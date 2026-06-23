# Strategy Library — Lifecycle + Doc/Catalog (Phase 0 + 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Strategy Library page redesign with a two-tier Retire/Delete lifecycle (user ask #1), plus correct `STRATEGY_PLUGINS.md` and a code-generated "grounding catalog" that keeps it from drifting again — laying the registry/reload plumbing the Phase 2 authoring wizard needs.

**Architecture:** Keep the engine untouched. Add pure registry lifecycle methods (`unregister`/`reload`/`origin_of`) to the in-memory `StrategyRegistry`. Put all strategy endpoints in a NEW **host-importable** router `app/routers/strategies_admin.py` (heavy deps — square-off, deployment status — behind patchable module seams, mirroring `live_broker`). Persist retired state in a `strategy_lifecycle` Mongo collection, merged onto `GET /strategies` at request time so the registry stays DB-free. Frontend gains badges, filter chips, a per-card ⋯ menu (Retire/Un-retire/Delete) and a Retired shelf.

**Tech Stack:** Backend — FastAPI, Motor (Mongo), pandas; tests via `pytest` + `fastapi.testclient.TestClient` + hand-rolled in-memory fake collections (no motor on host). Frontend — React (CRA/craco), axios, shadcn/ui (`dropdown-menu`, `badge`, `input`, `button`), `sonner` toasts; verified via `npm run build` (no FE unit runner).

**Conventions baked in (from the codebase):**
- Tests live in repo-root `tests/`, run with `python -m pytest tests -q`. Each file self-bootstraps: `sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))`, then imports `from app....`.
- **Tests NEVER import `server.py` and never import a module that imports `motor`/`optuna` at top.** `app/db.py` imports motor at module top (`db.py:5`) — so test routers must NOT do `from app.db import get_db` at module top; use a patchable `_db()` seam that imports lazily.
- Router pattern: bare `api = APIRouter()`; mounted under `/api` in `server.py`. `HTTPException(code, "msg")` positional. Timestamps = `datetime.now(timezone.utc).isoformat()`. Strip `_id` via projection or `serialize_doc`.
- Frontend: `@/` = `frontend/src`; `api.*` returns unwrapped `.data`; toasts `import { toast } from "sonner"`; error text `e.response?.data?.detail || e.message`.

---

## PHASE 0 — Doc correction + grounding catalog

### Task 0.1: Grounding catalog module

**Files:**
- Create: `backend/app/ai/__init__.py`
- Create: `backend/app/ai/grounding.py`
- Test: `tests/test_grounding_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_catalog.py
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.grounding import build_grounding_catalog


def test_catalog_has_core_and_adaptive_columns():
    cat = build_grounding_catalog()
    cols = set(cat["indicator_columns"])
    # core
    for c in ["ema9", "ema21", "ema50", "rsi", "vwap", "atr", "adx", "chop", "fvg", "regime"]:
        assert c in cols, f"missing core column {c}"
    # adaptive toolkit (the audit said these were undocumented)
    for c in ["vel_z", "accel_z", "regime_score", "squeeze_on", "squeeze_fire",
              "supertrend", "vwap_sigma", "nr7", "cpr_p", "day_type", "tod_tradeable"]:
        assert c in cols, f"missing adaptive column {c}"


def test_catalog_signal_fields_complete():
    cat = build_grounding_catalog()
    names = {f["name"] for f in cat["signal_fields"]}
    for f in ["direction", "score", "reasons", "blockers", "target_pct", "stop_pct",
              "time_stop_minutes", "spot_target_pts", "spot_stop_pts",
              "scenario", "spot_target_level", "exit_mode"]:
        assert f in names, f"missing Signal field {f}"


def test_catalog_lists_strategies_with_param_schema():
    cat = build_grounding_catalog()
    ids = {s["id"] for s in cat["strategies"]}
    assert "confluence_scalper" in ids
    cs = next(s for s in cat["strategies"] if s["id"] == "confluence_scalper")
    assert isinstance(cs["parameter_schema"], dict) and cs["parameter_schema"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_grounding_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ai'`

- [ ] **Step 3: Create the package + implementation**

```python
# backend/app/ai/__init__.py
"""AI-assisted authoring subsystem (grounding catalog, spec mapper, compiler).
Phase 0 ships only the grounding catalog. See
docs/superpowers/specs/2026-06-23-strategy-library-authoring-and-lifecycle-design.md
"""
```

```python
# backend/app/ai/grounding.py
"""Generate the AI 'vocabulary' (available indicator columns, Signal fields,
strategy param schemas) FROM LIVE CODE, so the doc/AI prompts can never drift
from what the engine actually computes. Pure + host-safe (no motor)."""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List

import numpy as np
import pandas as pd

# Raw OHLCV/time columns that are inputs, not computed indicators — excluded
# from the indicator catalog.
_RAW_COLS = {"ts", "datetime", "open", "high", "low", "close", "volume",
             "dt", "session_date", "ist_time"}


def _sample_frame(n: int = 420) -> pd.DataFrame:
    """A minimal but valid 1m OHLCV frame so precompute_all_indicators produces
    every column it would in production. Values are synthetic; only the column
    set matters here. 420 bars clears the longest rolling window (atr_avg=100)."""
    start_ms = 1_700_000_000_000  # fixed epoch ms (no Date.now/now() — deterministic)
    ts = start_ms + np.arange(n) * 60_000
    base = 20000.0 + np.cumsum(np.sin(np.arange(n) / 7.0))
    high = base + 5.0
    low = base - 5.0
    close = base + np.cos(np.arange(n) / 5.0)
    vol = np.full(n, 1000.0)
    return pd.DataFrame({"ts": ts, "open": base, "high": high, "low": low,
                         "close": close, "volume": vol})


def build_grounding_catalog() -> Dict[str, Any]:
    """Return {indicator_columns, signal_fields, strategies}."""
    from app.indicators import precompute_all_indicators
    from app.regime import classify_regime_series
    from app.strategies.base import Signal, get_registry

    df = precompute_all_indicators(_sample_frame())
    df["regime"] = classify_regime_series(df)  # regime is added by callers, not precompute
    indicator_columns = sorted(c for c in df.columns if c not in _RAW_COLS)

    signal_fields: List[Dict[str, Any]] = []
    for f in dataclasses.fields(Signal):
        signal_fields.append({"name": f.name, "type": str(f.type)})

    reg = get_registry()
    if not reg.list_all():          # ensure discovery ran (idempotent)
        reg.auto_discover()
    strategies = reg.list_all()

    return {
        "indicator_columns": indicator_columns,
        "signal_fields": signal_fields,
        "strategies": strategies,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_grounding_catalog.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/__init__.py backend/app/ai/grounding.py tests/test_grounding_catalog.py
git commit -m "feat(ai): grounding catalog generated from live code (indicators/Signal/strategies)"
```

---

### Task 0.2: Rewrite `STRATEGY_PLUGINS.md` + doc/catalog sync test

**Files:**
- Modify: `docs/STRATEGY_PLUGINS.md`
- Test: `tests/test_strategy_plugins_doc.py`

- [ ] **Step 1: Write the failing test** (asserts the doc mentions every catalog indicator column — the anti-drift guarantee)

```python
# tests/test_strategy_plugins_doc.py
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.grounding import build_grounding_catalog

DOC = ROOT / "docs" / "STRATEGY_PLUGINS.md"


def test_doc_documents_every_indicator_column():
    text = DOC.read_text(encoding="utf-8")
    cols = build_grounding_catalog()["indicator_columns"]
    missing = [c for c in cols if not re.search(rf"`{re.escape(c)}`", text)]
    assert not missing, f"STRATEGY_PLUGINS.md is missing indicator columns: {missing}"


def test_doc_documents_extra_signal_fields():
    text = DOC.read_text(encoding="utf-8")
    for f in ["scenario", "spot_target_level", "exit_mode"]:
        assert re.search(rf"`{re.escape(f)}`", text), f"doc missing Signal field {f}"


def test_doc_template_sets_is_builtin_false():
    text = DOC.read_text(encoding="utf-8")
    assert "is_builtin = False" in text, "Template must set is_builtin = False"


def test_doc_documents_session_precompute():
    text = DOC.read_text(encoding="utf-8")
    assert "session_precompute" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_strategy_plugins_doc.py -v`
Expected: FAIL — `missing indicator columns: ['accel_z', 'cpr_bc', ...]`, and the `is_builtin = False` / `session_precompute` asserts fail.

- [ ] **Step 3: Edit the doc** — apply all of Appendix A from the spec. Concretely:
  1. In the Template (around `docs/STRATEGY_PLUGINS.md:21`), add `is_builtin = False` right after the `description` line, with a comment: `# REQUIRED for custom plugins — StrategyBase defaults this to True (built-in badge)`.
  2. Add a new `## Per-session precompute (perf)` section documenting `session_precompute(self, df, params) -> dict` (runs once pre-loop, return merged into `ctx`, source of `ctx["orb_hi"]/orb_lo`; point to `app/strategies/session_features.py` and `opening_range_breakout.py`).
  3. In the Signal section, add `scenario`, `spot_target_level`, `exit_mode` with one-line descriptions.
  4. Change "the 6 built-in strategies" → "the 12 built-in strategies".
  5. Replace the "Available Indicators" table so it includes the full catalog. Add an "Adaptive toolkit" block listing each column in backticks: `vel_z`, `accel_z`, `vr`, `regime_score`, `squeeze_on`, `squeeze_fire`, `sqz_mom`, `supertrend`, `st_dir`, `vwap_sigma`, `vwap_u1`, `vwap_u2`, `vwap_l1`, `vwap_l2`, `nr7`, `cpr_p`, `cpr_tc`, `cpr_bc`, `cpr_width_pct`, `day_type`, `R1`, `S1`, `R2`, `S2`, `orb_width_pct_partial`, `orb_width_pct_prior`, `tod_tradeable`. (The test enforces completeness, so if any are still missing it will name them.)
  6. Note `regime` is added by `classify_regime_series()` after `precompute_all_indicators()`, not inside it.
  7. Fix the `time_stop_minutes` Risk-Hints row: "captured AND enforced live (reason `time_stop`, backtest parity)".
  8. Add a short "Optional base classes" section (`AdaptiveStrategyBase`, `ScenarioRoutedStrategyBase`; examples `gap_fade.py`, `opening_range_regime_router.py`).
  9. Add "no hot-reload at startup" note to Restart Backend (and a forward-reference: in-app reload arrives with the authoring tool).
  10. Document `ctx["instrument"]` in the `evaluate()` ctx description.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_strategy_plugins_doc.py -v`
Expected: PASS (4 tests). If `test_doc_documents_every_indicator_column` still names columns, add those backticked names to the table and re-run.

- [ ] **Step 5: Commit**

```bash
git add docs/STRATEGY_PLUGINS.md tests/test_strategy_plugins_doc.py
git commit -m "docs(plugins): correct drift (is_builtin, session_precompute, 12 builtins, full indicator/Signal catalog) + sync test"
```

---

## PHASE 1 — Registry lifecycle + endpoints + page redesign

### Task 1.1: Registry lifecycle methods (pure, host-safe)

**Files:**
- Modify: `backend/app/strategies/base.py` (`StrategyBase.meta` ~`:71-82`; `StrategyRegistry` ~`:85-143`)
- Test: `tests/test_registry_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_registry_lifecycle.py
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategies.base import get_registry, StrategyRegistry, StrategyBase


def test_meta_includes_origin_for_builtin():
    reg = get_registry(); reg.auto_discover()
    items = {s["id"]: s for s in reg.list_all()}
    assert items["confluence_scalper"]["origin"] == "builtin"


def test_unregister_removes_strategy():
    reg = get_registry(); reg.auto_discover()
    assert reg.get("confluence_scalper") is not None
    assert reg.unregister("confluence_scalper") is True
    assert reg.get("confluence_scalper") is None
    assert reg.unregister("confluence_scalper") is False  # idempotent
    reg.reload()  # restore for other tests
    assert reg.get("confluence_scalper") is not None


def test_origin_of_unknown_is_none():
    reg = StrategyRegistry()
    assert reg.origin_of("nope") is None


def test_reload_repopulates():
    reg = get_registry(); reg.auto_discover()
    n = len(reg.list_all())
    reg.unregister("vwap_mean_reversion")
    reg.reload()
    assert len(reg.list_all()) == n
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_registry_lifecycle.py -v`
Expected: FAIL — `KeyError: 'origin'` / `AttributeError: 'StrategyRegistry' object has no attribute 'unregister'`.

- [ ] **Step 3: Implement**

In `StrategyBase.meta()` add the `origin` key (computed from the defining module):

```python
    def meta(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "supported_instruments": self.supported_instruments,
            "supported_modes": self.supported_modes,
            "supported_timeframes": self.supported_timeframes,
            "parameter_schema": self.parameter_schema,
            "is_builtin": self.is_builtin,
            "origin": ("custom" if type(self).__module__.startswith("app.strategies.plugins")
                       else "builtin"),
        }
```

In `StrategyRegistry.__init__` add the package map; add `unregister`, `origin_of`, `reload`; stamp `origin` on failed entries; record the package on errors in `auto_discover`:

```python
class StrategyRegistry:
    def __init__(self):
        self._strategies: Dict[str, StrategyBase] = {}
        self._errors: Dict[str, str] = {}
        self._error_pkgs: Dict[str, str] = {}  # error-id -> package name (origin of failed plugin)

    # ... register() / get() unchanged ...

    def unregister(self, strategy_id: str) -> bool:
        return self._strategies.pop(strategy_id, None) is not None

    def origin_of(self, strategy_id: str) -> Optional[str]:
        s = self._strategies.get(strategy_id)
        if s is not None:
            return ("custom" if type(s).__module__.startswith("app.strategies.plugins")
                    else "builtin")
        pkg = self._error_pkgs.get(strategy_id)
        if pkg is not None:
            return "custom" if pkg.endswith("plugins") else "builtin"
        return None

    def list_all(self) -> List[Dict[str, Any]]:
        items = [s.meta() for s in self._strategies.values()]
        for plug_id, err in self._errors.items():
            pkg = self._error_pkgs.get(plug_id, "")
            items.append({
                "id": plug_id, "name": plug_id, "version": "?", "description": "",
                "supported_instruments": [], "supported_modes": [], "supported_timeframes": [],
                "parameter_schema": {}, "is_builtin": False,
                "origin": ("custom" if pkg.endswith("plugins") else "builtin"),
                "is_loaded": False, "error": err,
            })
        return items

    def reload(self) -> None:
        self._errors.clear()
        self._error_pkgs.clear()
        self.auto_discover()
```

In `auto_discover`, record the package alongside each error (two sites):

```python
                except Exception as e:
                    self._errors[modname] = f"import failed: {e}"
                    self._error_pkgs[modname] = pkg_name
                    log.exception(f"Failed to import strategy {full}")
                    continue
                # ...
                        except Exception as e:
                            self._errors[cls.__name__] = f"instantiation failed: {e}"
                            self._error_pkgs[cls.__name__] = pkg_name
                            log.exception(f"Failed to instantiate {cls.__name__}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_registry_lifecycle.py -v`
Expected: PASS (4 tests). Also run the existing registry test to confirm no regression: `python -m pytest tests/test_new_strategies_integration.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/app/strategies/base.py tests/test_registry_lifecycle.py
git commit -m "feat(strategies): registry unregister/reload/origin_of + origin in meta()"
```

---

### Task 1.2: New host-safe router — `GET /strategies` (retired-merged) + `GET /strategies/{id}`; remove the two routes from research.py; wire into server.py

**Files:**
- Create: `backend/app/routers/strategies_admin.py`
- Modify: `backend/app/routers/research.py` (delete the `/strategies` + `/strategies/{id}` handlers at `:56-66`)
- Modify: `backend/server.py` (import + include at `:195` and the include block `:197-202`)
- Test: `tests/test_strategy_admin_routes.py`

- [ ] **Step 1: Write the failing test** (defines the reusable fake DB used by Tasks 1.2–1.5)

```python
# tests/test_strategy_admin_routes.py
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.strategies_admin as sa


# ---- in-memory fake Mongo (flat-equality matcher; multi-collection) ----
def _matches(doc, query):
    return all(doc.get(k) == v for k, v in query.items())

class _Cursor:
    def __init__(self, docs): self._docs = docs
    async def to_list(self, length=None): return list(self._docs)

class FakeColl:
    def __init__(self): self.docs = []
    async def find_one(self, q, projection=None):
        return next((dict(d) for d in self.docs if _matches(d, q)), None)
    def find(self, q, projection=None):
        return _Cursor([dict(d) for d in self.docs if _matches(d, q)])
    async def update_one(self, q, update, upsert=False):
        for d in self.docs:
            if _matches(d, q):
                d.update(update.get("$set", {})); return Mock(matched_count=1)
        if upsert:
            nd = {k: v for k, v in q.items() if not k.startswith("$")}
            nd.update(update.get("$set", {})); self.docs.append(nd)
        return Mock(matched_count=0)
    async def delete_one(self, q):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _matches(d, q)]
        return Mock(deleted_count=before - len(self.docs))

class FakeDB:
    def __init__(self): self._c = {}
    def __getattr__(self, name):
        # attribute access => collection (created on demand)
        c = self.__dict__.setdefault("_c", {})
        return c.setdefault(name, FakeColl())

def _make_app(db=None, registry_items=None, origin_map=None):
    app = FastAPI()
    app.include_router(sa.api)
    db = db if db is not None else FakeDB()
    patches = [patch.object(sa, "_db", lambda: db)]
    if registry_items is not None or origin_map is not None:
        reg = Mock()
        reg.list_all.return_value = registry_items or []
        reg.origin_of.side_effect = lambda sid: (origin_map or {}).get(sid)
        reg.unregister.return_value = True
        patches.append(patch.object(sa, "get_registry", lambda: reg))
    for p in patches: p.start()
    tc = TestClient(app, raise_server_exceptions=True)
    tc._patches = patches; tc._db = db
    return tc

def _stop(tc):
    for p in tc._patches: p.stop()


def test_list_merges_retired_flag():
    db = FakeDB()
    db.strategy_lifecycle.docs.append({"strategy_id": "foo", "retired": True})
    tc = _make_app(db=db, registry_items=[
        {"id": "foo", "name": "Foo", "origin": "custom"},
        {"id": "bar", "name": "Bar", "origin": "builtin"},
    ])
    try:
        r = tc.get("/strategies")
        assert r.status_code == 200
        items = {s["id"]: s for s in r.json()["items"]}
        assert items["foo"]["is_retired"] is True
        assert items["bar"]["is_retired"] is False
    finally:
        _stop(tc)


def test_get_single_404():
    tc = _make_app(registry_items=[], origin_map={})
    try:
        # get() lookup is via the real-ish registry mock; patch get() too
        with patch.object(sa, "get_registry") as gr:
            reg = gr.return_value
            reg.get.return_value = None
            r = tc.get("/strategies/missing")
            assert r.status_code == 404
    finally:
        _stop(tc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_strategy_admin_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.routers.strategies_admin'`.

- [ ] **Step 3: Create the router** (host-safe: no `from app.db import …` at top; `_db()` seam imports lazily)

```python
# backend/app/routers/strategies_admin.py
"""Strategy read + lifecycle routes (list/get/retire/un-retire/delete/reload).

Host-importable: heavy deps (motor DB, square-off, deployment status) are behind
module-level seams that import lazily, so router tests can patch them without
importing motor. Mirrors the isolation pattern in app/routers/live_broker.py.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.strategies.base import get_registry

api = APIRouter()
log = logging.getLogger(__name__)


# ---- patchable seams -------------------------------------------------------
def _db():
    from app.db import get_db  # lazy: app.db imports motor at top
    return get_db()


def _delete_plugin_file(strategy_id: str) -> bool:
    """Remove the .py for a custom plugin. Returns True if a file was removed.
    Only deletes files physically under .../strategies/plugins/ as a safety net."""
    from app.strategy_source_hash import strategy_file_path
    s = get_registry().get(strategy_id)
    if s is None:
        return False
    path = strategy_file_path(s)
    plugins_marker = os.path.join("strategies", "plugins")
    if path and os.path.isfile(path) and plugins_marker in path:
        os.remove(path)
        return True
    return False


async def _square_off_strategy_deployments(strategy_id: str) -> List[Dict[str, Any]]:
    """Pause + scoped square-off every ACTIVE deployment of a strategy.
    Lazily imports the heavy deployment/paper modules."""
    from app.paper_squareoff import square_off_open_paper_trades
    from app.runtime import _set_deployment_status, upstox_stream_manager
    db = _db()
    active = await db.strategy_deployments.find(
        {"strategy_id": strategy_id, "status": "ACTIVE"}, {"_id": 0, "id": 1}
    ).to_list(length=None)
    summaries: List[Dict[str, Any]] = []
    for d in active:
        s = await square_off_open_paper_trades(
            db, deployment_id=d["id"],
            latest_tick_lookup=upstox_stream_manager.latest_tick_map().get,
            reason="manual_retire",
        )
        summaries.extend(s)
        await _set_deployment_status(d["id"], "PAUSED")
    return summaries


# ---- read routes -----------------------------------------------------------
@api.get("/strategies")
async def list_strategies():
    items = get_registry().list_all()
    db = _db()
    rows = await db.strategy_lifecycle.find({"retired": True}, {"_id": 0}).to_list(length=None)
    retired = {r["strategy_id"] for r in rows}
    for it in items:
        it["is_retired"] = it["id"] in retired
    return {"items": items}


@api.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: str):
    s = get_registry().get(strategy_id)
    if not s:
        raise HTTPException(404, f"Strategy {strategy_id} not found")
    meta = s.meta()
    life = await _db().strategy_lifecycle.find_one({"strategy_id": strategy_id}, {"_id": 0})
    meta["is_retired"] = bool(life and life.get("retired"))
    return meta
```

Now **remove** the duplicate handlers from `backend/app/routers/research.py` (the `@api.get("/strategies")` at `:56-58` and `@api.get("/strategies/{strategy_id}")` at `:61-66`). Leave the `from app.strategies.base import get_registry` import — it is still used by `optimize_start` etc.

Wire the router in `backend/server.py`:
- Line ~195: `from app.routers import broker, deployments, journals, live_broker, research, strategies_admin, warehouse  # noqa: E402`
- In the include block (after `api.include_router(research.api)`): `api.include_router(strategies_admin.api)`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_strategy_admin_routes.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/strategies_admin.py backend/app/routers/research.py backend/server.py tests/test_strategy_admin_routes.py
git commit -m "feat(strategies): host-safe strategies_admin router; move /strategies here with is_retired merge"
```

---

### Task 1.3: Retire + Un-retire endpoints

**Files:**
- Modify: `backend/app/routers/strategies_admin.py`
- Test: `tests/test_strategy_admin_routes.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_retire_sets_flag_and_squares_off():
    db = FakeDB()
    tc = _make_app(db=db, registry_items=[{"id": "foo", "origin": "custom"}], origin_map={"foo": "custom"})
    try:
        with patch.object(sa, "_square_off_strategy_deployments",
                          AsyncMock(return_value=[{"id": "t1"}, {"id": "t2"}])):
            r = tc.post("/strategies/foo/retire")
            assert r.status_code == 200
            body = r.json()
            assert body["retired"] is True and body["squared_off_count"] == 2
            life = db.strategy_lifecycle.docs[0]
            assert life["strategy_id"] == "foo" and life["retired"] is True
    finally:
        _stop(tc)


def test_retire_unknown_404():
    tc = _make_app(registry_items=[], origin_map={})
    try:
        with patch.object(sa, "get_registry") as gr:
            gr.return_value.get.return_value = None
            gr.return_value.origin_of.return_value = None
            r = tc.post("/strategies/nope/retire")
            assert r.status_code == 404
    finally:
        _stop(tc)


def test_unretire_clears_flag():
    db = FakeDB()
    db.strategy_lifecycle.docs.append({"strategy_id": "foo", "retired": True})
    tc = _make_app(db=db, registry_items=[{"id": "foo", "origin": "custom"}], origin_map={"foo": "custom"})
    try:
        r = tc.post("/strategies/foo/un-retire")
        assert r.status_code == 200 and r.json()["retired"] is False
        assert db.strategy_lifecycle.docs[0]["retired"] is False
    finally:
        _stop(tc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_strategy_admin_routes.py -k "retire" -v`
Expected: FAIL — 404 from the missing routes (`/strategies/foo/retire` not found) / `KeyError`.

- [ ] **Step 3: Implement** (append to `strategies_admin.py`)

```python
def _exists(strategy_id: str) -> bool:
    reg = get_registry()
    return reg.get(strategy_id) is not None or reg.origin_of(strategy_id) is not None


@api.post("/strategies/{strategy_id}/retire")
async def retire_strategy(strategy_id: str):
    if not _exists(strategy_id):
        raise HTTPException(404, f"Strategy {strategy_id} not found")
    summaries = await _square_off_strategy_deployments(strategy_id)
    now = datetime.now(timezone.utc).isoformat()
    await _db().strategy_lifecycle.update_one(
        {"strategy_id": strategy_id},
        {"$set": {"strategy_id": strategy_id, "retired": True, "retired_at": now}},
        upsert=True,
    )
    return {"strategy_id": strategy_id, "retired": True,
            "squared_off": summaries, "squared_off_count": len(summaries)}


@api.post("/strategies/{strategy_id}/un-retire")
async def unretire_strategy(strategy_id: str):
    await _db().strategy_lifecycle.update_one(
        {"strategy_id": strategy_id},
        {"$set": {"strategy_id": strategy_id, "retired": False, "retired_at": None}},
        upsert=True,
    )
    return {"strategy_id": strategy_id, "retired": False}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_strategy_admin_routes.py -k "retire" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/strategies_admin.py tests/test_strategy_admin_routes.py
git commit -m "feat(strategies): retire/un-retire endpoints (scoped square-off + pause deployments)"
```

---

### Task 1.4: Delete endpoint + guards (404 / 403 built-in / 409 not-retired / 409 deployed / success)

**Files:**
- Modify: `backend/app/routers/strategies_admin.py`
- Test: `tests/test_strategy_admin_routes.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def _delete_app(origin, *, retired=False, deployments=None):
    db = FakeDB()
    if retired:
        db.strategy_lifecycle.docs.append({"strategy_id": "foo", "retired": True})
    for dep in (deployments or []):
        db.strategy_deployments.docs.append(dep)
    tc = _make_app(db=db, registry_items=[{"id": "foo", "origin": origin}], origin_map={"foo": origin})
    return tc, db


def test_delete_unknown_404():
    tc = _make_app(registry_items=[], origin_map={})
    try:
        r = tc.delete("/strategies/foo")
        assert r.status_code == 404
    finally:
        _stop(tc)


def test_delete_builtin_403():
    tc, _ = _delete_app("builtin", retired=True)
    try:
        r = tc.delete("/strategies/foo")
        assert r.status_code == 403
    finally:
        _stop(tc)


def test_delete_not_retired_409():
    tc, _ = _delete_app("custom", retired=False)
    try:
        r = tc.delete("/strategies/foo")
        assert r.status_code == 409
    finally:
        _stop(tc)


def test_delete_with_live_deployment_409():
    tc, _ = _delete_app("custom", retired=True,
                        deployments=[{"id": "d1", "strategy_id": "foo", "status": "ACTIVE"}])
    try:
        r = tc.delete("/strategies/foo")
        assert r.status_code == 409
    finally:
        _stop(tc)


def test_delete_success_removes_file_and_lifecycle():
    tc, db = _delete_app("custom", retired=True,
                         deployments=[{"id": "d1", "strategy_id": "foo", "status": "ARCHIVED"}])
    try:
        with patch.object(sa, "_delete_plugin_file", Mock(return_value=True)):
            r = tc.delete("/strategies/foo")
            assert r.status_code == 200 and r.json()["deleted"] is True
            assert db.strategy_lifecycle.docs == []
    finally:
        _stop(tc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_strategy_admin_routes.py -k "delete" -v`
Expected: FAIL — route not found (405/404 for DELETE).

- [ ] **Step 3: Implement** (append to `strategies_admin.py`; note: filter deployments in Python so the flat-equality fake works — no `$ne`)

```python
@api.delete("/strategies/{strategy_id}")
async def delete_strategy(strategy_id: str):
    reg = get_registry()
    origin = reg.origin_of(strategy_id)
    if origin is None:
        raise HTTPException(404, f"Strategy {strategy_id} not found")
    if origin != "custom":
        raise HTTPException(403, "Built-in strategies cannot be deleted — retire them instead")
    db = _db()
    life = await db.strategy_lifecycle.find_one({"strategy_id": strategy_id}, {"_id": 0})
    if not (life and life.get("retired")):
        raise HTTPException(409, "Retire the strategy before deleting its file")
    deps = await db.strategy_deployments.find({"strategy_id": strategy_id}, {"_id": 0}).to_list(length=None)
    blocking = [d for d in deps if d.get("status") != "ARCHIVED"]
    if blocking:
        raise HTTPException(409, f"{len(blocking)} deployment(s) still reference this strategy; archive them first")
    _delete_plugin_file(strategy_id)
    reg.unregister(strategy_id)
    await db.strategy_lifecycle.delete_one({"strategy_id": strategy_id})
    return {"strategy_id": strategy_id, "deleted": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_strategy_admin_routes.py -k "delete" -v`
Expected: PASS (5 tests). Full file: `python -m pytest tests/test_strategy_admin_routes.py -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/strategies_admin.py tests/test_strategy_admin_routes.py
git commit -m "feat(strategies): guarded delete (404/403-builtin/409-not-retired/409-deployed) + file removal"
```

---

### Task 1.5: Reload endpoint + `is_retired` helper for run-blocking

**Files:**
- Modify: `backend/app/routers/strategies_admin.py`
- Test: `tests/test_strategy_admin_routes.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_reload_returns_count():
    tc = _make_app()
    try:
        with patch.object(sa, "get_registry") as gr:
            gr.return_value.reload.return_value = None
            gr.return_value.list_all.return_value = [{"id": "a"}, {"id": "b"}]
            r = tc.post("/strategies/reload")
            assert r.status_code == 200 and r.json()["count"] == 2
            gr.return_value.reload.assert_called_once()
    finally:
        _stop(tc)


def test_is_retired_helper():
    import asyncio
    db = FakeDB()
    db.strategy_lifecycle.docs.append({"strategy_id": "foo", "retired": True})
    with patch.object(sa, "_db", lambda: db):
        assert asyncio.run(sa.is_retired("foo")) is True
        assert asyncio.run(sa.is_retired("bar")) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_strategy_admin_routes.py -k "reload or is_retired" -v`
Expected: FAIL — route/attr missing.

- [ ] **Step 3: Implement** (append)

```python
@api.post("/strategies/reload")
async def reload_strategies():
    reg = get_registry()
    reg.reload()
    return {"count": len(reg.list_all())}


async def is_retired(strategy_id: str) -> bool:
    life = await _db().strategy_lifecycle.find_one({"strategy_id": strategy_id}, {"_id": 0})
    return bool(life and life.get("retired"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_strategy_admin_routes.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/strategies_admin.py tests/test_strategy_admin_routes.py
git commit -m "feat(strategies): reload endpoint + is_retired helper"
```

---

### Task 1.6: Block retired strategies from new deployments + the `strategy_lifecycle` index (Docker-verified)

These edits touch modules that import motor/optuna, so they are **not host-unit-tested** — verify in the running Docker stack. The `is_retired` helper they call is host-tested (Task 1.5).

**Files:**
- Modify: `backend/app/routers/deployments.py` (deployment-create paths at `:213` and `:563`)
- Modify: `backend/app/db.py` (`ensure_indexes`, after the existing indexes ~`:40`)

- [ ] **Step 1: Add the guard at deployment create** — after the existing `get_registry().get(strategy_id)` resolution in each create path, add:

```python
    from app.routers.strategies_admin import is_retired
    if await is_retired(strategy_id):
        raise HTTPException(400, f"Strategy {strategy_id} is retired — un-retire it before deploying")
```

- [ ] **Step 2: Add the lifecycle index** in `ensure_indexes()`:

```python
    await db.strategy_lifecycle.create_index("strategy_id", unique=True)
```

- [ ] **Step 3: Verify in Docker**

```bash
docker compose restart backend && docker compose logs --tail 20 backend
# then: retire a strategy via UI, attempt to deploy it -> expect the 400 above.
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/deployments.py backend/app/db.py
git commit -m "feat(deployments): block deploying a retired strategy; index strategy_lifecycle"
```

> **Tracked follow-up (not in this plan):** also block retired strategies at the direct backtest/optimizer/wfo run endpoints (`research.py:149`, `optimizer.py:842`, `wfo.py:520`). Lower priority for Phase 1 because the UI pickers (Task 1.8) already hide retired strategies; this only matters for hand-crafted API calls.

---

### Task 1.7: Frontend API methods

**Files:**
- Modify: `frontend/src/lib/api.js` (next to `listStrategies`/`getStrategy`, ~`:26-27`)

- [ ] **Step 1: Add methods** (mirror existing action/delete idioms — return unwrapped `.data`)

```js
  retireStrategy: (id) => apiClient.post(`/strategies/${id}/retire`).then((r) => r.data),
  unretireStrategy: (id) => apiClient.post(`/strategies/${id}/un-retire`).then((r) => r.data),
  deleteStrategy: (id) => apiClient.delete(`/strategies/${id}`).then((r) => r.data),
  reloadStrategies: () => apiClient.post("/strategies/reload").then((r) => r.data),
```

- [ ] **Step 2: Verify the bundle compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds (2 known pre-existing exhaustive-deps warnings are OK).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.js
git commit -m "feat(api): retire/un-retire/delete/reload strategy methods"
```

---

### Task 1.8: Strategy Library page redesign (badges, search, filter chips, ⋯ menu, Retired shelf)

**Files:**
- Modify: `frontend/src/pages/StrategyLibrary.jsx` (full rewrite of the page + card)

No FE unit runner — verify via `npm run build` + Chrome. Uses the `dropdown-menu` primitive (first consumer) and `window.confirm` for the destructive delete.

- [ ] **Step 1: Rewrite the page**

```jsx
import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { toast } from "sonner";
import {
  Library, CheckCircle2, AlertCircle, TrendingUp, MoreVertical,
  PauseCircle, PlayCircle, Trash2, Search,
} from "lucide-react";

const FILTERS = ["All", "Built-in", "Custom", "Failed", "Retired"];

export default function StrategyLibrary() {
  const [strategies, setStrategies] = useState([]);
  const [metricsByStrategy, setMetricsByStrategy] = useState({});
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All");

  const load = useCallback(async () => {
    try {
      const strategyData = await api.listStrategies();
      setStrategies(strategyData.items || []);
      try {
        const metricData = await api.listDeploymentMetrics({ include_ineligible: 1 });
        const grouped = {};
        for (const item of metricData.items || []) {
          if (!(item.closed_trade_count > 0)) continue;
          const key = item.strategy_id || "";
          grouped[key] = [...(grouped[key] || []), item];
        }
        setMetricsByStrategy(grouped);
      } catch {
        setMetricsByStrategy({});
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function onRetire(s) {
    try {
      const res = await api.retireStrategy(s.id);
      toast.success(`Retired ${s.name}${res.squared_off_count ? ` · squared off ${res.squared_off_count} trade(s)` : ""}.`);
      load();
    } catch (e) {
      toast.error(`Retire failed: ${e.response?.data?.detail || e.message}`);
    }
  }
  async function onUnretire(s) {
    try {
      await api.unretireStrategy(s.id);
      toast.success(`Un-retired ${s.name}.`);
      load();
    } catch (e) {
      toast.error(`Un-retire failed: ${e.response?.data?.detail || e.message}`);
    }
  }
  async function onDelete(s) {
    if (!window.confirm(`Delete the file for "${s.name}" permanently? This cannot be undone.`)) return;
    try {
      await api.deleteStrategy(s.id);
      toast.success(`Deleted ${s.name}.`);
      load();
    } catch (e) {
      toast.error(`Delete failed: ${e.response?.data?.detail || e.message}`);
    }
  }

  if (loading) {
    return (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-40 bg-bg-1" />)}
      </div>
    );
  }

  const q = query.trim().toLowerCase();
  const matchesQuery = (s) =>
    !q || (s.name || "").toLowerCase().includes(q) || (s.id || "").toLowerCase().includes(q);
  const matchesFilter = (s) => {
    if (filter === "Retired") return s.is_retired;
    if (s.is_retired) return false; // retired live in the shelf unless explicitly filtered
    if (filter === "Built-in") return s.origin === "builtin";
    if (filter === "Custom") return s.origin === "custom";
    if (filter === "Failed") return s.is_loaded === false;
    return true; // All
  };

  const visible = strategies.filter(matchesQuery).filter(matchesFilter);
  const retired = strategies.filter(matchesQuery).filter((s) => s.is_retired);
  const activeCount = strategies.filter((s) => !s.is_retired).length;

  return (
    <div className="space-y-3" data-testid="strategy-library-page">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="text-sm text-dim">{activeCount} active · {retired.length} retired</div>
        <div className="flex-1" />
        <div className="relative">
          <Search className="w-3.5 h-3.5 text-dimmer absolute left-2 top-1/2 -translate-y-1/2" />
          <input
            value={query} onChange={(e) => setQuery(e.target.value)} placeholder="search…"
            className="text-xs pl-7 pr-2 py-1.5 rounded-md bg-bg-2 border border-line text-foreground"
            data-testid="strategy-search"
          />
        </div>
      </div>

      <div className="flex gap-1.5 flex-wrap">
        {FILTERS.map((f) => (
          <button
            key={f} onClick={() => setFilter(f)}
            className={`text-[11px] px-2.5 py-1 rounded-full border ${
              filter === f ? "bg-info/15 border-info/50 text-foreground" : "bg-bg-1 border-line text-dim"
            }`}
            data-testid={`strategy-filter-${f}`}
          >{f}</button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {visible.map((s) => (
          <StrategyCard key={s.id} s={s} metrics={metricsByStrategy[s.id] || []}
            onRetire={onRetire} onUnretire={onUnretire} onDelete={onDelete} />
        ))}
      </div>

      {filter !== "Retired" && retired.length > 0 && (
        <details className="rounded-lg border border-dashed border-line bg-bg-1/50 p-3">
          <summary className="text-xs text-dim cursor-pointer">Retired ({retired.length}) — hidden from pickers, deployments paused</summary>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
            {retired.map((s) => (
              <StrategyCard key={s.id} s={s} metrics={metricsByStrategy[s.id] || []}
                onRetire={onRetire} onUnretire={onUnretire} onDelete={onDelete} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function StrategyCard({ s, metrics, onRetire, onUnretire, onDelete }) {
  const loaded = s.is_loaded !== false;
  const isCustom = s.origin === "custom";
  return (
    <div className={`rounded-lg border border-line bg-bg-1 p-3 ${s.is_retired ? "opacity-60" : ""}`} data-testid={`strategy-card-${s.id}`}>
      <div className="flex items-start gap-3 mb-2">
        <div className="w-9 h-9 rounded-md bg-bg-3 border border-line-strong flex items-center justify-center shrink-0">
          <Library className="w-4 h-4 text-info" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm font-semibold">{s.name}</div>
            <span className="font-mono text-[10px] text-dimmer">v{s.version}</span>
            {loaded ? (
              <Badge className="bg-emerald-950 text-emerald-200 border-emerald-900"><CheckCircle2 className="w-3 h-3 mr-1" />loaded</Badge>
            ) : (
              <Badge className="bg-rose-950 text-rose-200 border-rose-900"><AlertCircle className="w-3 h-3 mr-1" />failed</Badge>
            )}
            {isCustom ? (
              <Badge className="bg-sky-950 text-sky-200 border-sky-900">custom</Badge>
            ) : (
              <Badge className="bg-bg-3 text-dim border-line">built-in</Badge>
            )}
            {s.is_retired && <Badge className="bg-amber-950 text-amber-200 border-amber-900">retired</Badge>}
          </div>
          <div className="text-[11px] font-mono text-dimmer mt-0.5">{s.id}</div>
        </div>
        <StrategyMenu s={s} isCustom={isCustom} onRetire={onRetire} onUnretire={onUnretire} onDelete={onDelete} />
      </div>
      <div className="text-xs text-dim leading-snug mb-3">{s.description}</div>
      <ForwardMetricsBlock metrics={metrics} />
      {!loaded && s.error && (
        <div className="text-[11px] text-rose-300 bg-rose-950/50 border border-rose-900 rounded-md p-2 mb-2 font-mono">
          {s.error}
        </div>
      )}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <Pill label="Instruments" items={s.supported_instruments} />
        <Pill label="Modes" items={s.supported_modes} />
        <Pill label="Timeframes" items={s.supported_timeframes} />
      </div>
      {s.parameter_schema && Object.keys(s.parameter_schema).length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Parameters ({Object.keys(s.parameter_schema).length})</div>
          <div className="flex flex-wrap gap-1">
            {Object.entries(s.parameter_schema).map(([k, def]) => (
              <span key={k} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-bg-2 border border-line text-dim">
                {k}=<span className="text-foreground">{String(def.default)}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StrategyMenu({ s, isCustom, onRetire, onUnretire, onDelete }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="p-1 rounded hover:bg-bg-2 text-dimmer shrink-0" data-testid={`strategy-menu-${s.id}`} aria-label="Strategy actions">
          <MoreVertical className="w-4 h-4" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        {s.is_retired ? (
          <DropdownMenuItem onClick={() => onUnretire(s)}><PlayCircle className="w-3.5 h-3.5 mr-2" />Un-retire</DropdownMenuItem>
        ) : (
          <DropdownMenuItem onClick={() => onRetire(s)}><PauseCircle className="w-3.5 h-3.5 mr-2" />Retire</DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={!isCustom}
          onClick={() => isCustom && onDelete(s)}
          className={isCustom ? "text-rose-300" : "opacity-40"}
          title={isCustom ? "" : "Built-in strategies can only be retired"}
        >
          <Trash2 className="w-3.5 h-3.5 mr-2" />Delete file
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ForwardMetricsBlock, Metric, fmtNum, fmtPct, fmtSigned, Pill — unchanged from the
// current file; keep them as-is below this line.
```

Keep the existing `ForwardMetricsBlock`, `Metric`, `fmtNum`, `fmtPct`, `fmtSigned`, and `Pill` helper functions from the current `StrategyLibrary.jsx` (copy them verbatim into the rewritten file).

- [ ] **Step 2: Update the run-pickers to hide retired strategies**

In `frontend/src/pages/Optimizer.jsx` (dropdown ~`:652`) and `frontend/src/pages/BacktestLab.jsx` (dropdown ~`:783`), change the existing filter:

```js
// before: strategies.filter((s) => s.is_loaded !== false)
strategies.filter((s) => s.is_loaded !== false && !s.is_retired)
```

- [ ] **Step 3: Verify the bundle compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds (only the 2 known exhaustive-deps warnings).

- [ ] **Step 4: Manual Chrome verification** (running Docker stack)

```
docker compose up -d --build frontend backend
```
Then in Chrome on the Strategy Library page: confirm badges (built-in/custom/retired), the search box + filter chips, the ⋯ menu (Retire on a built-in; Delete disabled on built-in; Retire then card moves to the Retired shelf; Un-retire restores it). For a custom plugin: Retire → Delete file removes it. Confirm no console errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/StrategyLibrary.jsx frontend/src/pages/Optimizer.jsx frontend/src/pages/BacktestLab.jsx
git commit -m "feat(strategy-library): redesign with badges, filters, search, per-card retire/delete menu + retired shelf"
```

---

### Task 1.9: Full-suite regression + wrap-up

- [ ] **Step 1: Run the whole backend suite**

Run: `python -m pytest tests -q`
Expected: all pass (prior count + the new `test_grounding_catalog.py`, `test_strategy_plugins_doc.py`, `test_registry_lifecycle.py`, `test_strategy_admin_routes.py`).

- [ ] **Step 2: Final FE build**

Run: `cd frontend && npm run build`
Expected: success.

- [ ] **Step 3: Verify the running stack end-to-end** (Docker) — list, retire (with a deployed strategy → confirm it pauses + squares off), un-retire, delete a custom plugin, reload.

---

## Self-review notes (author)

- **Spec coverage:** Phase 0 (§5.4 doc fix, §5.3b catalog) → Tasks 0.1–0.2. Phase 1 lifecycle (§5.2) → Tasks 1.1–1.6; endpoints retire/un-retire/delete/reload all present; picker exclusion → Task 1.8 Step 2 + the `is_retired` deployment guard → 1.6; page redesign (§5.1) → Task 1.8. **Deferred (documented):** AI badge / View-source / Edit-spec / `＋ New strategy` button belong to Phase 2 (authoring) and are intentionally absent here; direct backtest/optimizer run-blocking for retired is a tracked follow-up under Task 1.6.
- **Type/name consistency:** `origin` ∈ {"builtin","custom"}; `is_retired` boolean on every `/strategies` item; registry methods `unregister/reload/origin_of`; router seams `_db/_delete_plugin_file/_square_off_strategy_deployments/is_retired` are the exact names patched in tests.
- **Host-safety:** `strategies_admin.py` imports only `app.strategies.base` (pandas-only) at top; all motor/optuna/runtime deps are lazy inside functions and patched in tests — so `tests/test_strategy_admin_routes.py` is host-importable.
