# SP-0: Unified `ctx` Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `ctx` dict passed to `StrategyBase.evaluate()` identical across the backtest, paper/live, and smoke-test paths — same canonical keys and `session_precompute` always merged — so structural strategies run and validate identically everywhere.

**Architecture:** Introduce two host-safe builders in `app/strategies/base.py` — `build_eval_ctx(...)` (assembles the canonical key set + merges `session_precompute` extras) and `build_live_eval_ctx(strategy, df_enriched, last_idx, instrument, params)` (calls `session_precompute` then `build_eval_ctx`). Wire all three execution paths to use them. The change is **additive and behavior-preserving**: existing `session_precompute` builtins already produce correct results via their `history_df` fallback; SP-0 restores their O(1) fast path in live and makes the smoke gate able to execute structural strategies (today its ctx has no `history_df`/`i` and never calls `session_precompute`).

**Tech Stack:** Python 3.12, pandas, pytest. Backend at `C:\Users\haroo\af-wt-strategy-library\backend`; tests at `C:\Users\haroo\af-wt-strategy-library\tests` (each test inserts `ROOT/"backend"` on `sys.path`). Spec: `docs/superpowers/specs/2026-06-28-capability-aware-strategy-authoring-design.md` §4.

> All shell commands assume cwd = `C:\Users\haroo\af-wt-strategy-library` (the worktree root, branch `feat/capability-aware-authoring`). The host venv has `pandas`/`numpy`/`pytest` but **not** `motor` — every file touched/imported by these tests is host-safe.

---

## Canonical contract (the target)

Every path's `ctx` at `evaluate()` time:

```
{ "history_df": <full enriched frame>,   # DataFrame
  "i": <int row index>,
  "instrument": <str>,                   # "NIFTY" | "BANKNIFTY" | "SENSEX"
  "session_date": <str>,                 # current bar's session date ("" if unknown)
  "mode": <str>,                         # "SCALP" | "INTRADAY" (default "INTRADAY")
  **session_precompute(df, params) }     # strategy's per-session constants
```

## File structure

- **Modify** `backend/app/strategies/base.py` — add `EVAL_CTX_KEYS`, `build_eval_ctx`, `build_live_eval_ctx` (host-safe; pandas only).
- **Modify** `backend/app/backtest.py` — build `ctx_global` via `build_eval_ctx`; set `session_date` per-bar alongside the existing per-bar `i`.
- **Modify** `backend/app/deployment_evaluator.py` — replace the inline `{"history_df":…, "i":…}` with `build_live_eval_ctx(...)` (this calls `session_precompute`).
- **Modify** `backend/app/ai/_py_smoke_driver.py` — extract the eval loop into a host-importable `run_smoke(inst, cols)` that calls `session_precompute` and builds ctx via `build_eval_ctx`.
- **Create** `tests/test_eval_ctx_contract.py` — unit tests for the builders + the three wirings.

---

## Task 1: ctx builders in `base.py`

**Files:**
- Modify: `backend/app/strategies/base.py`
- Test: `tests/test_eval_ctx_contract.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_ctx_contract.py`:

```python
"""SP-0: the canonical ctx contract is identical across backtest / live / smoke."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
from app.strategies.base import (
    StrategyBase, Signal, EVAL_CTX_KEYS, build_eval_ctx, build_live_eval_ctx,
)


def test_build_eval_ctx_has_canonical_keys_and_merges_extras():
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    ctx = build_eval_ctx(
        history_df=df, i=2, instrument="NIFTY", session_date="2025-01-02",
        mode="SCALP", session_extras={"day_open": {"2025-01-02": 100.0}},
    )
    for k in EVAL_CTX_KEYS:
        assert k in ctx, f"missing canonical key {k}"
    assert ctx["i"] == 2 and ctx["instrument"] == "NIFTY"
    assert ctx["session_date"] == "2025-01-02" and ctx["mode"] == "SCALP"
    assert ctx["history_df"] is df
    assert ctx["day_open"] == {"2025-01-02": 100.0}


def test_build_eval_ctx_defaults_mode_and_tolerates_no_extras():
    ctx = build_eval_ctx(history_df=None, i=0, instrument="NIFTY",
                         session_date="", session_extras=None)
    assert ctx["mode"] == "INTRADAY"
    assert set(EVAL_CTX_KEYS).issubset(ctx.keys())


def test_build_live_eval_ctx_calls_session_precompute():
    class _Probe(StrategyBase):
        id = "probe_live"
        def session_precompute(self, df, params):
            return {"__seen__": True, "n": len(df)}
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0], "session_date": ["d", "d", "d"]})
    ctx = build_live_eval_ctx(_Probe(), df, last_idx=2, instrument="BANKNIFTY",
                              params={"mode": "INTRADAY"})
    assert ctx["__seen__"] is True and ctx["n"] == 3       # session_precompute ran + merged
    assert ctx["i"] == 2 and ctx["instrument"] == "BANKNIFTY"
    assert ctx["session_date"] == "d" and ctx["history_df"] is df
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_eval_ctx_contract.py -v`
Expected: FAIL — `ImportError: cannot import name 'EVAL_CTX_KEYS'` (and `build_eval_ctx`/`build_live_eval_ctx`).

- [ ] **Step 3: Implement the builders**

In `backend/app/strategies/base.py`, after the `Signal` dataclass and before `class StrategyBase` (so `StrategyBase` methods could reference them later), add:

```python
EVAL_CTX_KEYS = ("history_df", "i", "instrument", "session_date", "mode")


def build_eval_ctx(*, history_df, i, instrument, session_date, mode="INTRADAY",
                   session_extras=None) -> Dict[str, Any]:
    """Assemble the canonical evaluate() ctx. The SAME builder is used by the
    backtest, paper/live, and smoke paths so the contract can never drift again.
    `session_extras` (a strategy's session_precompute() output) is merged last."""
    ctx: Dict[str, Any] = {
        "history_df": history_df,
        "i": int(i),
        "instrument": instrument,
        "session_date": session_date,
        "mode": mode,
    }
    if session_extras:
        ctx.update(session_extras)
    return ctx


def build_live_eval_ctx(strategy: "StrategyBase", df_enriched, last_idx: int,
                        instrument: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Build the canonical ctx for the single-bar paper/live path: call the
    strategy's session_precompute ONCE on the rolling window, then build_eval_ctx.
    Host-safe (no motor import) so it is unit-testable without the live module."""
    session_extras = strategy.session_precompute(df_enriched, params or {})
    last_row = df_enriched.iloc[last_idx]
    return build_eval_ctx(
        history_df=df_enriched, i=last_idx, instrument=instrument,
        session_date=str(last_row.get("session_date") or ""),
        mode=str((params or {}).get("mode") or "INTRADAY"),
        session_extras=session_extras,
    )
```

(`Dict`, `Any` are already imported at the top of `base.py`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_eval_ctx_contract.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd "C:/Users/haroo/af-wt-strategy-library" && git add backend/app/strategies/base.py tests/test_eval_ctx_contract.py && git commit -m "feat(sp0): canonical ctx builders (build_eval_ctx + build_live_eval_ctx)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: wire `backtest.py` to the canonical builder

**Files:**
- Modify: `backend/app/backtest.py` (ctx construction at ~line 112; per-bar update at ~line 179)
- Test: `tests/test_eval_ctx_contract.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eval_ctx_contract.py`:

```python
from app.backtest import run_backtest


def _probe_df(n=60):
    """n in-window bars with the OHLC/ts/ist_time/session_date run_backtest reads."""
    base_ms = 1_700_000_000_000
    rows = []
    for k in range(n):
        rows.append({
            "ts": base_ms + k * 60_000,
            "datetime": f"2025-01-02T11:{k % 60:02d}:00",
            "ist_time": "11:00",
            "session_date": "2025-01-02",
            "open": 100.0 + k * 0.1, "high": 100.6 + k * 0.1,
            "low": 99.4 + k * 0.1, "close": 100.0 + k * 0.1,
        })
    return pd.DataFrame(rows)


def test_backtest_passes_canonical_ctx_to_evaluate():
    seen = []

    class _Probe(StrategyBase):
        id = "probe_bt"
        def session_precompute(self, df, params):
            return {"__probe_extra__": 7}
        def evaluate(self, row, prev, params, ctx):
            seen.append(dict(ctx))   # snapshot keys+values at this bar
            return Signal(direction="NONE")

    run_backtest(_probe_df(), _Probe(), {}, instrument="NIFTY")
    assert seen, "evaluate was never reached"
    canonical = set(EVAL_CTX_KEYS) | {"__probe_extra__"}
    for snap in seen:
        assert canonical.issubset(snap.keys())
        assert snap["instrument"] == "NIFTY"
        assert snap["session_date"] == "2025-01-02"
        assert snap["__probe_extra__"] == 7
        assert isinstance(snap["i"], int)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_eval_ctx_contract.py::test_backtest_passes_canonical_ctx_to_evaluate -v`
Expected: FAIL — `AssertionError` on `canonical.issubset(...)` (today's backtest ctx lacks `session_date`/`mode`).

- [ ] **Step 3: Implement the wiring**

In `backend/app/backtest.py`:

(a) Add `build_eval_ctx` to the existing import from `app.strategies.base`, e.g.:
```python
from app.strategies.base import StrategyBase, Signal, build_eval_ctx
```
(Keep whatever other names the line already imports — add `build_eval_ctx`.)

(b) Replace lines ~112–113:
```python
    ctx_global: Dict[str, Any] = {"history_df": df, "instrument": instrument}
    ctx_global.update(strategy.session_precompute(df, params))
```
with:
```python
    _session_extras = strategy.session_precompute(df, params)
    ctx_global: Dict[str, Any] = build_eval_ctx(
        history_df=df, i=0, instrument=instrument, session_date=None,
        mode=str(params.get("mode") or "INTRADAY"), session_extras=_session_extras,
    )
```

(c) At the per-bar update (~line 179, where `ctx_global["i"] = i` is set), add the per-bar `session_date` right after it:
```python
        ctx_global["i"] = i
        ctx_global["session_date"] = row.get("session_date")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_eval_ctx_contract.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Guard against regressions in existing backtest behavior**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_backtest_characterization.py tests/test_session_precompute_parity.py tests/test_scenario_routing_base.py -v`
Expected: PASS (all green — the in-place `ctx_global` reuse + per-bar `i` semantics are preserved; `session_date` is additive).

- [ ] **Step 6: Commit**

```bash
cd "C:/Users/haroo/af-wt-strategy-library" && git add backend/app/backtest.py tests/test_eval_ctx_contract.py && git commit -m "feat(sp0): backtest builds ctx via build_eval_ctx (+ per-bar session_date)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: wire `deployment_evaluator.py` to call `session_precompute`

**Files:**
- Modify: `backend/app/deployment_evaluator.py` (import at ~line 32; evaluate call at ~line 369–370)
- Test: covered by `test_build_live_eval_ctx_calls_session_precompute` (Task 1) — the live ctx logic lives in the host-safe `build_live_eval_ctx`, so no `motor`-dependent test is needed.

- [ ] **Step 1: Confirm the existing live-path test exists and passes the helper contract**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_eval_ctx_contract.py::test_build_live_eval_ctx_calls_session_precompute -v`
Expected: PASS (from Task 1).

- [ ] **Step 2: Implement the wiring**

In `backend/app/deployment_evaluator.py`:

(a) Add `build_live_eval_ctx` to the base import at line ~32:
```python
from app.strategies.base import StrategyBase, get_registry, build_live_eval_ctx
```

(b) Replace the evaluate call at lines ~369–370. Current:
```python
    try:
        sig = strategy.evaluate(last_bar, prev_bar, merged_params, {"history_df": df_enriched, "i": last_idx})
```
New (session_precompute now runs inside the existing try, so a failure is reported like any evaluate failure):
```python
    try:
        eval_ctx = build_live_eval_ctx(strategy, df_enriched, last_idx, instrument, merged_params)
        sig = strategy.evaluate(last_bar, prev_bar, merged_params, eval_ctx)
```

- [ ] **Step 3: Verify the module still imports and the deployment tests pass**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_deployment_preflight.py tests/test_deployment_quality.py tests/test_deployment_kill_switch.py -v`
Expected: PASS (the change is additive — non-`session_precompute` strategies get the same ctx plus extra keys they ignore; `session_precompute` builtins now get their fast-path maps merged).

- [ ] **Step 4: Commit**

```bash
cd "C:/Users/haroo/af-wt-strategy-library" && git add backend/app/deployment_evaluator.py && git commit -m "fix(sp0): live evaluator calls session_precompute via build_live_eval_ctx

Restores the O(1) session_precompute fast path in paper/live (ORB/gap/scenario
builtins) and makes structural-only strategies viable live.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: smoke driver runs `session_precompute` + provides `history_df`/`i`

**Files:**
- Modify: `backend/app/ai/_py_smoke_driver.py` (extract `run_smoke`; build ctx via `build_eval_ctx`)
- Test: `tests/test_eval_ctx_contract.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eval_ctx_contract.py`:

```python
from app.ai._py_smoke_driver import run_smoke


def test_smoke_driver_provides_history_df_i_and_session_precompute():
    captured = {}

    class _Structural(StrategyBase):
        id = "probe_smoke"
        def session_precompute(self, df, params):
            return {"__sp__": len(df)}
        def evaluate(self, row, prev, params, ctx):
            captured["keys"] = set(ctx.keys())
            captured["sp"] = ctx.get("__sp__")
            # a structural strategy indexes the history frame — must not KeyError
            _ = ctx["history_df"].iloc[ctx["i"]]["close"]
            return Signal(direction="NONE")

    out = run_smoke(_Structural(), ["open", "high", "low", "close"])
    assert out["ok"] is True, out
    assert {"history_df", "i", "instrument", "session_date", "mode"}.issubset(captured["keys"])
    assert captured["sp"] and captured["sp"] > 0   # session_precompute ran
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_eval_ctx_contract.py::test_smoke_driver_provides_history_df_i_and_session_precompute -v`
Expected: FAIL — `ImportError: cannot import name 'run_smoke'` (it does not exist yet).

- [ ] **Step 3: Refactor the driver to expose `run_smoke`**

In `backend/app/ai/_py_smoke_driver.py`, extract the synthetic-frame build + eval loop (currently inline in `main()` lines ~40–66) into a module-level function, and have `main()` call it. Replace that block so the file reads:

```python
def run_smoke(inst, cols):
    """Build a synthetic ~2-session frame over `cols`, run the strategy's
    session_precompute + evaluate() across ~18 bars with the CANONICAL ctx.
    Returns {ok, error?, signal_repr?}. Host-importable (no /app cwd needed)."""
    import pandas as pd
    import numpy as np
    from app.strategies.base import Signal, build_eval_ctx

    n = 120
    frame = {c: np.linspace(100, 110, n) for c in cols}
    frame["regime"] = ["TREND"] * n
    if "day_type" in cols:
        frame["day_type"] = ["TREND_DAY"] * n
    df = pd.DataFrame(frame)
    base = pd.Timestamp("2026-06-01 09:15:00")
    df["ts"] = [(base + pd.Timedelta(minutes=i)).value // 10**6 for i in range(n)]
    df["datetime"] = [(base + pd.Timedelta(minutes=i)).isoformat() for i in range(n)]
    df["ist_time"] = [(base + pd.Timedelta(minutes=i)).strftime("%H:%M") for i in range(n)]
    df["session_date"] = ["2026-06-01" if i < n // 2 else "2026-06-02" for i in range(n)]

    params = inst.merged_params(None)
    session_extras = inst.session_precompute(df, params)   # may raise -> caught by main()
    last_repr = None
    for i in range(2, min(n, 20)):
        row, prev = df.iloc[i], df.iloc[i - 1]
        ctx = build_eval_ctx(
            history_df=df, i=i, instrument="NIFTY",
            session_date=str(df.iloc[i].get("session_date") or ""),
            mode="INTRADAY", session_extras=session_extras,
        )
        sig = inst.evaluate(row, prev, params, ctx)
        if not isinstance(sig, Signal):
            return {"ok": False, "error": f"evaluate returned {type(sig).__name__}, not Signal"}
        if sig.direction not in ("CE", "PE", "NONE"):
            return {"ok": False, "error": f"invalid direction {sig.direction!r}"}
        last_repr = repr(sig)
    return {"ok": True, "signal_repr": last_repr}
```

And change `main()` so its body (after instantiating `inst` and computing `cols = sorted(allowed_columns())`) becomes:

```python
        import pandas as pd  # noqa: F401  (kept if other code in main still needs it)
        cols = sorted(allowed_columns())
        return _result(result_path, run_smoke(inst, cols))
```

Remove the now-duplicated inline frame-build/eval-loop from `main()` (lines ~40–66). Keep the `allowed_columns` import and the strategy-class discovery in `main()` unchanged.

- [ ] **Step 4: Run to verify it passes**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_eval_ctx_contract.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Verify the sandbox suite (the smoke harness + evasion battery) still passes**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/test_py_sandbox.py -v`
Expected: PASS (the monkeypatched `smoke_test` tests and the static-check evasion battery are unaffected; `main()` still writes the result file via `_result`).

- [ ] **Step 6: Commit**

```bash
cd "C:/Users/haroo/af-wt-strategy-library" && git add backend/app/ai/_py_smoke_driver.py tests/test_eval_ctx_contract.py && git commit -m "feat(sp0): smoke driver runs session_precompute + canonical ctx (history_df/i)

Closes the hollow smoke gate: a structural Full-Python strategy that indexes
ctx['history_df'] / reads session_precompute output now executes under smoke.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: full-suite regression + wrap-up

**Files:** none (verification only)

- [ ] **Step 1: Run the full host test suite**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && python -m pytest tests/ -q`
Expected: PASS — the pre-existing count (≈2537) plus the 6 new `test_eval_ctx_contract.py` tests, zero failures. Investigate any failure before proceeding; SP-0 is behavior-preserving, so a real failure indicates a wiring mistake.

- [ ] **Step 2: Sanity-check the diff is additive**

Run: `cd "C:/Users/haroo/af-wt-strategy-library" && git diff --stat feat/strategy-full-python..HEAD`
Expected: only `base.py`, `backtest.py`, `deployment_evaluator.py`, `_py_smoke_driver.py`, and `tests/test_eval_ctx_contract.py` changed (plus the spec/plan docs). No other source files.

- [ ] **Step 3: Verify the doc parity claim still holds**

Confirm `STRATEGY_PLUGINS.md` does not yet need the `required_features` note (that lands in SP-1). No action; just don't forget it for the next plan.

---

## Self-Review

**1. Spec coverage (SP-0 section §4 of the design):**
- Unified canonical ctx keys across all three paths → Tasks 1–4. ✓
- `deployment_evaluator` calls `session_precompute` → Task 3. ✓
- Smoke provides `history_df` + `i` + `session_precompute` (closes the hollow gate) → Task 4. ✓
- Cross-path parity / behavior-preservation gate → Task 2 Step 5 + Task 3 Step 3 + Task 5 (existing `test_session_precompute_parity.py` proves the fallback is byte-identical; the new tests prove the fast path is now wired). ✓
- The F2 live-window correctness boundary is **explicitly NOT** in SP-0 (it is the `session_anchored` registry axis in SP-2/SP-3). ✓ (Design §4 "Note on F2".)

**2. Placeholder scan:** No "TBD"/"add error handling"/"similar to" — every code step shows complete code. The one prose instruction ("add `build_eval_ctx` to the existing import line") names the exact symbol and shows the resulting line. ✓

**3. Type/name consistency:** `build_eval_ctx`, `build_live_eval_ctx`, `EVAL_CTX_KEYS`, `run_smoke` are spelled identically in their definitions (Task 1, Task 4) and every call site (Tasks 2, 3, 4) and test. `session_extras` is the merged-arg name throughout. `last_idx`/`i` are ints everywhere. ✓

**Notes for the executor:**
- The host venv lacks `motor`; do **not** add a test that imports `app.deployment_evaluator` (it transitively pulls broad deps). The live-path logic is fully covered by `build_live_eval_ctx` (host-safe).
- The smoke `run_smoke` deliberately does **not** wrap `session_precompute` in its own try/except — a structural strategy whose `session_precompute` raises on the synthetic frame *should* fail smoke; `main()`'s outer try reports it.
- After all tasks, a real end-to-end smoke validation (a structural Full-Python strategy through the running stack) belongs to the SP-2/SP-4 Docker verification, not SP-0.
