# Optimizer/Backtest Speedup + Advisory Trust Warnings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make optimizer/backtest trial runs faster and add free option-buying trustworthiness signals, with zero engine-result change (byte-identical) and zero blocking (every quality signal advisory).

**Architecture:** Phase 1 (Tasks 1-5) is self-contained and ship-ready: a byte-identical regression harness, latency instrumentation + a real measurement, two advisory quality signals (in-job deflated-Sharpe on run views; a spot↔option correlation metric), and a byte-identical strftime vectorization. Phase 2 (Tasks 6-10) is a **measurement-gated** stage: the dependency-keyed indicator memoization (the big speed win) plus a raw-candle LRU, per-bar micro-opts, and louder advisory realism warnings. Pure modules (`indicators.py`, `deployment_quality.py`) are host-TDD; optimizer/research call-sites are verified on the running stack.

**Tech Stack:** Python 3.11, pandas/numpy, Optuna, FastAPI, pytest. Source spec: `docs/superpowers/specs/2026-06-17-optimizer-speedup-design.md`. Branch: `feat/optimizer-speedup`.

**Standing constraints:** Host tests must NEVER import `server.py`/`optimizer.py`/`runtime.py`/`paper_auto.py` (they pull DB/async/Upstox at import). Tests run from repo root: `python -m pytest tests/...`. Do NOT push/merge without explicit user instruction. Leave the user's uncommitted work (`BacktestRunJournal.jsx`, `SortHeader.jsx`, `start.bat` deletion, root note `.md` files) untouched.

---

## File Structure

**Phase 1**
- Create `tests/test_indicator_equivalence.py` — byte-identical regression harness scaffold (Task 1) + strftime equality test (Task 5).
- Create `tests/test_spot_option_correlation.py` — host tests for the new pure correlation helper + warning (Task 4).
- Modify `backend/app/optimizer.py` — latency instrumentation (Task 2); `_save_best_as_backtest` persists `n_trials` (Task 3); compute + store spot↔option correlation, pass as evidence (Task 4).
- Modify `backend/app/routers/research.py` — `get_backtest_run` passes `n_trials` evidence (Task 3).
- Modify `backend/app/deployment_quality.py` — `compute_spot_option_correlation` helper + `objective_misalignment` warning + threshold + snapshot key (Task 4).
- Modify `backend/app/indicators.py` — vectorize `session_date`/`ist_time` (Task 5).

**Phase 2 (gated on Task 2's measurement)**
- Create `backend/app/indicator_groups.py` — indicator-group registry (param_keys, input_columns, output_columns, compute_fn) (Task 7).
- Modify `backend/app/indicators.py` — `precompute_all_indicators` delegates to the registry; public signature unchanged (Task 7).
- Modify `backend/app/optimizer.py` / `backend/app/wfo.py` — `get_enriched` uses per-group memoization (Task 7); raw-candle LRU (Task 8).
- Modify `backend/app/backtest.py` — per-bar micro-opts (Task 9).
- Modify `backend/app/deployment_quality.py` — louder advisory realism warnings (Task 10).

---

## PHASE 1 — Measure + free wins (byte-identical / advisory). Ship-ready.

### Task 1: Byte-identical regression harness scaffold

**Files:**
- Create: `tests/test_indicator_equivalence.py`
- Reads: `backend/app/indicators.py` (`precompute_all_indicators`), `backend/app/strategies` registry, `tests/_adaptive_testutil.py`

This harness is the **gate for Task 5 and all of Phase 2**. In Phase 1 it asserts determinism + full strategy/param coverage and provides the comparison seam (`_enrich_new` / `_enrich_ref`) that Phase 2 re-points at the memoized path.

- [ ] **Step 1: Write the harness with determinism + coverage assertions**

```python
# tests/test_indicator_equivalence.py
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.indicators import precompute_all_indicators
from app.regime import classify_regime_series
from tests._adaptive_testutil import make_sessions

# The comparison seam. Phase 2 re-points _enrich_new at the memoized assembly;
# in Phase 1 both are the current monolithic precompute, so equality is exact.
def _enrich_ref(df, params):
    enr = precompute_all_indicators(df, params)
    enr["regime"] = classify_regime_series(enr)
    return enr

def _enrich_new(df, params):
    enr = precompute_all_indicators(df, params)
    enr["regime"] = classify_regime_series(enr)
    return enr

def _fixture_df():
    # 3 sessions x 120 bars of a varied path so every indicator has signal.
    base = [100 + (i % 17) - (i % 5) * 0.7 for i in range(120)]
    return make_sessions([base, [x + 3 for x in base], [x - 2 for x in base]],
                         start_date="2025-01-06")

# Param sweep: defaults + single-variable variations over indicator-period keys,
# INCLUDING an atr_length variation (exercises the regime/tod hidden atr edges
# in Phase 2). Every dict is a full param set the strategies accept.
_PARAM_SWEEP = [
    {},  # defaults
    {"ema_fast": 5, "ema_slow": 13},
    {"rsi_length": 9},
    {"atr_length": 7},                 # hidden-edge probe (regime, atr_avg, tod read atr)
    {"atr_length": 28},
    {"adx_length": 20},
    {"st_period": 7, "st_mult": 2.0},
    {"swing_lookback": 3},
    {"tod_lookback_sessions": 10, "tod_min_atr_frac": 0.4},
]

def test_enrichment_is_deterministic():
    df = _fixture_df()
    for params in _PARAM_SWEEP:
        a = _enrich_new(df.copy(), params)
        b = _enrich_new(df.copy(), params)
        pd.testing.assert_frame_equal(a, b)

def test_new_matches_reference_across_param_sweep():
    df = _fixture_df()
    for params in _PARAM_SWEEP:
        ref = _enrich_ref(df.copy(), params)
        new = _enrich_new(df.copy(), params)
        pd.testing.assert_frame_equal(new, ref, check_dtype=True)

def test_expected_columns_present():
    df = _fixture_df()
    enr = _enrich_new(df.copy(), {})
    for col in ("ema9", "ema21", "rsi", "macd_hist", "atr", "atr_avg", "adx",
                "chop", "vwap", "session_date", "ist_time", "regime",
                "squeeze_on", "supertrend", "st_dir", "tod_tradeable",
                "cpr_tc", "cpr_bc", "day_type", "nr7", "fvg"):
        assert col in enr.columns, f"missing {col}"
```

- [ ] **Step 2: Run the harness — expect PASS**

Run: `python -m pytest tests/test_indicator_equivalence.py -v`
Expected: 3 passed. (Phase-1 tautology between `_enrich_new`/`_enrich_ref` is intentional — it locks the comparison machinery + sweep that Phase 2 reuses.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_indicator_equivalence.py
git commit -m "test(indicators): byte-identical enrichment regression harness (Phase-2 gate)"
```

---

### Task 2: Latency instrumentation + the measurement (gates Phase 2)

**Files:**
- Modify: `backend/app/optimizer.py` — `_evaluate` (line 263) timing; `get_enriched` (line 811); the trial loop accumulation.

This is optimizer code (forbidden host import) → **verified on the running stack, not host-TDD**. Off by default (`AF_OPT_TIMING` unset) → zero cost.

- [ ] **Step 1: Add an opt-in timing accumulator to `get_enriched` and `_evaluate`**

In `backend/app/optimizer.py`, near the top-level imports add:

```python
import os
_OPT_TIMING = os.environ.get("AF_OPT_TIMING") == "1"
```

Change `get_enriched` (lines 811-820) to time precompute on cache miss. Replace its body with:

```python
        def get_enriched(merged: Dict[str, Any]) -> pd.DataFrame:
            key = _indicator_key(merged)
            cached = enriched_cache.get(key)
            if cached is not None:
                return cached
            if _OPT_TIMING:
                import time as _t
                _t0 = _t.perf_counter()
            enr = precompute_all_indicators(raw_df, merged)
            enr["regime"] = classify_regime_series(enr)
            if _OPT_TIMING:
                _TIMING["precompute_s"] += _t.perf_counter() - _t0
                _TIMING["precompute_n"] += 1
            if len(enriched_cache) < _MAX_ENRICHED_CACHE:
                enriched_cache[key] = enr
            return enr
```

Add a per-job timing dict initialized next to `enriched_cache` (after line 809):

```python
        _TIMING = {"precompute_s": 0.0, "precompute_n": 0, "backtest_s": 0.0, "backtest_n": 0}
```

Wrap the `run_backtest` call inside `_evaluate`... but `_evaluate` is a module-level helper (line 263) without access to `_TIMING`. Instead time at the call site: in the `evaluate` closure (line 837-838), replace with:

```python
        def evaluate(params: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            if _OPT_TIMING:
                import time as _t
                _t0 = _t.perf_counter()
                out = _evaluate(get_enriched, strategy, params, instrument, costs, pretrade)
                _TIMING["backtest_s"] += _t.perf_counter() - _t0
                _TIMING["backtest_n"] += 1
                return out
            return _evaluate(get_enriched, strategy, params, instrument, costs, pretrade)
```

(Note: `backtest_s` includes the get_enriched call; subtract `precompute_s` post-hoc to isolate the bar loop. Document this in the emitted timing.)

- [ ] **Step 2: Emit timing into the finished job doc**

In the `finished = {...}` dict (lines 1152-1170), add (only meaningful when timing on):

```python
            "timing": ({
                "precompute_s": round(_TIMING["precompute_s"], 3),
                "precompute_n": _TIMING["precompute_n"],
                "evaluate_s": round(_TIMING["backtest_s"], 3),
                "evaluate_n": _TIMING["backtest_n"],
                "bar_loop_s": round(_TIMING["backtest_s"] - _TIMING["precompute_s"], 3),
            } if _OPT_TIMING else None),
```

- [ ] **Step 3: Rebuild backend, run the measurement on the running stack**

```bash
# rebuild backend container so the timing code is live, then:
# start a SMALL optimization (n_trials=60) for confluence_scalper with AF_OPT_TIMING=1
# set in the backend container env, and 2 other strategies (e.g. squeeze_expansion_breakout,
# vwap_pullback_scalp). After each finishes, read job.timing.
```

Capture for each strategy: `precompute_n` (cache-miss count — confirms thrash for confluence), `precompute_s`, `bar_loop_s`. **Record the precompute fraction = precompute_s / evaluate_s.**

- [ ] **Step 4: Record the measurement verdict in the plan's Phase-2 gate (Task 6) and commit the instrumentation**

```bash
git add backend/app/optimizer.py
git commit -m "perf(optimizer): opt-in AF_OPT_TIMING instrumentation (precompute vs bar-loop split)"
```

---

### Task 3: In-job deflated-Sharpe on saved/standalone run views

**Files:**
- Modify: `backend/app/optimizer.py` — `_save_best_as_backtest` signature (line 444) + doc dict (line 481) + caller (line 1136).
- Modify: `backend/app/routers/research.py` — `get_backtest_run` (line 360).
- Test: `tests/test_deployment_quality_option.py` (extend) — host-safe contract pin for the consuming side.

- [ ] **Step 1: Write the host-safe contract pin (consuming side)**

Append to `tests/test_deployment_quality_option.py`:

```python
def test_selection_bias_fires_only_with_n_trials_evidence():
    from app.deployment_quality import evaluate_source_quality
    doc = {
        "metrics": {"sharpe": 0.30, "trade_count": 40, "win_rate": 0.5,
                    "profit_factor": 1.1, "max_dd_pts": 50, "total_pnl_pts": 200},
    }
    # No evidence -> selection_bias cannot fire.
    q0 = evaluate_source_quality(doc)
    assert not any(w["id"] == "selection_bias" for w in q0["warnings"])
    # With n_trials evidence -> deflated Sharpe <= 0 -> selection_bias warns.
    q1 = evaluate_source_quality(doc, evidence={"n_trials": 200})
    assert any(w["id"] == "selection_bias" for w in q1["warnings"])
    assert q1["metrics_snapshot"]["n_trials"] == 200
```

- [ ] **Step 2: Run it — expect PASS (proves the engine already consumes the evidence)**

Run: `python -m pytest tests/test_deployment_quality_option.py::test_selection_bias_fires_only_with_n_trials_evidence -v`
Expected: PASS. (If it fails, the deflated-Sharpe consuming path regressed — stop and investigate.)

- [ ] **Step 3: Persist `n_trials` from the optimizer into the saved run doc**

In `backend/app/optimizer.py`, change `_save_best_as_backtest` signature (line 444) to add `n_trials: Optional[int] = None`:

```python
async def _save_best_as_backtest(job_id: str, payload: Dict[str, Any], strategy, df_enriched: pd.DataFrame, best_params: Dict[str, Any], instrument: str, costs_enabled: bool, pretrade: Dict[str, Any], run_walkforward: bool = True, option_config: Optional[Dict[str, Any]] = None, n_trials: Optional[int] = None) -> Optional[str]:
```

In the `doc = {...}` dict (line 481), add a top-level key (conditional so existing docs without it are unchanged):

```python
            **({"n_trials": int(n_trials)} if n_trials else {}),
```

At the optimizer caller (line 1136-1142), pass `n_trials=n_trials`:

```python
            best_backtest_run_id = await _save_best_as_backtest(
                job_id, payload, strategy, df_best, best_so_far["params"],
                instrument, costs, pretrade, run_walkforward=not cancelled_flag,
                option_config={**(option_cfg or {}),
                               "exit_controls": best_so_far.get("exit_controls"),
                               "daily_caps": best_so_far.get("daily_caps")} if evaluation_mode == "option_rerank" else None,
                n_trials=n_trials,
            )
```

The `wfo.py` caller (line 705) does NOT pass `n_trials` → defaults to `None` → its behavior unchanged.

- [ ] **Step 4: Pass `n_trials` evidence on the run-view read**

In `backend/app/routers/research.py`, `get_backtest_run` (lines 366-372), replace the evidence-less call:

```python
    try:
        from app.deployment_quality import evaluate_source_quality
        _nt = doc.get("n_trials")
        evidence = {"n_trials": _nt} if _nt else None
        doc["quality"] = evaluate_source_quality(doc, evidence=evidence)
    except Exception:
        pass
```

- [ ] **Step 5: Verify on the running stack**

Rebuild backend. Run a small optimization (n_trials=60) → open the saved best run via `GET /backtest/runs/{id}` → confirm `quality.metrics_snapshot.n_trials == 60` and, for an overfit case, a `selection_bias` warning is present. Confirm a run WITHOUT `n_trials` (a manual backtest) is unchanged (no `selection_bias`, no crash). Confirm a strong-OOS optimized run is NOT flipped into a false `selection_bias` (strong_oos suppression still holds).

- [ ] **Step 6: Commit**

```bash
git add backend/app/optimizer.py backend/app/routers/research.py tests/test_deployment_quality_option.py
git commit -m "feat(quality): surface selection-bias deflated-Sharpe on saved run views (persist n_trials + pass as evidence)"
```

---

### Task 4: Spot↔option correlation — pure helper + advisory warning

**Files:**
- Modify: `backend/app/deployment_quality.py` — helper + threshold + warning + snapshot.
- Create: `tests/test_spot_option_correlation.py`.
- Modify: `backend/app/optimizer.py` — compute in `_option_rerank` finalize, store in `rerank_info`, pass as evidence.

- [ ] **Step 1: Write host tests for the pure helper + warning**

```python
# tests/test_spot_option_correlation.py
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.deployment_quality import compute_spot_option_correlation, evaluate_source_quality

def _ranked(pairs):
    return [{"spot_objective": s, "option_pnl_value": o} for s, o in pairs]

def test_correlation_none_when_too_few():
    assert compute_spot_option_correlation([]) is None
    assert compute_spot_option_correlation(_ranked([(1.0, 2.0)])) is None

def test_correlation_none_on_zero_variance():
    assert compute_spot_option_correlation(_ranked([(1.0, 5.0), (1.0, 9.0)])) is None  # spot constant
    assert compute_spot_option_correlation(_ranked([(1.0, 5.0), (2.0, 5.0)])) is None  # option constant

def test_correlation_perfect_positive():
    r = _ranked([(1.0, 10.0), (2.0, 20.0), (3.0, 30.0)])
    assert compute_spot_option_correlation(r) == 1.0

def test_correlation_negative():
    r = _ranked([(1.0, 30.0), (2.0, 20.0), (3.0, 10.0)])
    assert compute_spot_option_correlation(r) == -1.0

def test_objective_misalignment_warning_fires_below_threshold():
    doc = {"metrics": {"sharpe": 1.2, "trade_count": 60, "win_rate": 0.55,
                       "profit_factor": 1.5, "max_dd_pts": 30, "total_pnl_pts": 400}}
    q_low = evaluate_source_quality(doc, evidence={"spot_option_correlation": 0.1})
    assert any(w["id"] == "objective_misalignment" for w in q_low["warnings"])
    q_high = evaluate_source_quality(doc, evidence={"spot_option_correlation": 0.8})
    assert not any(w["id"] == "objective_misalignment" for w in q_high["warnings"])
    q_none = evaluate_source_quality(doc, evidence={})
    assert not any(w["id"] == "objective_misalignment" for w in q_none["warnings"])
```

- [ ] **Step 2: Run — expect FAIL (helper/warning not defined)**

Run: `python -m pytest tests/test_spot_option_correlation.py -v`
Expected: FAIL (`ImportError: cannot import name 'compute_spot_option_correlation'`).

- [ ] **Step 3: Implement the pure helper + constant + threshold**

In `backend/app/deployment_quality.py`, add the constant near line 57:

```python
MIN_SPOT_OPTION_CORRELATION = 0.3    # spot-objective vs option-rupee Pearson below this -> misalignment warning
```

Add the threshold field to `QualityThresholds` (after line 75):

```python
    min_spot_option_correlation: float = MIN_SPOT_OPTION_CORRELATION
```

Add the pure helper (place after `deflated_sharpe`, ~line 148):

```python
def compute_spot_option_correlation(ranked: Any) -> Optional[float]:
    """Pearson correlation between each reranked candidate's spot objective and
    its option net-rupee. Returns None when fewer than 2 candidates or either
    series has zero variance. Pure; no side effects."""
    try:
        xs = [_safe_float(r.get("spot_objective")) for r in (ranked or [])]
        ys = [_safe_float(r.get("option_pnl_value")) for r in (ranked or [])]
    except AttributeError:
        return None
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(sxy / math.sqrt(sxx * syy), 4)
```

- [ ] **Step 4: Add the `objective_misalignment` warning + snapshot key**

In `evaluate_source_quality`, after the selection-bias block (~line 317) add:

```python
        # 6. Objective misalignment (advisory): the optimizer search maximizes a
        # SPOT proxy while deployment is option-buying rupees. When the option
        # re-rank ran, a low spot<->option correlation means the search may be
        # steering away from option-profitable configs. Advisory only.
        # Use the (evidence or {}) pattern — matches the Fix-B selection-bias block,
        # robust whether evidence is None or {}.
        soc = (evidence or {}).get("spot_option_correlation")
        if soc is not None and _safe_float(soc, 1.0) < th.min_spot_option_correlation:
            warnings.append({
                "id": "objective_misalignment",
                "severity": SEVERITY_WARNING,
                "label": "Spot objective weakly predicts option P&L",
                "detail": (
                    f"Across the re-ranked candidates, the correlation between the spot search "
                    f"objective and option net-rupee is {_safe_float(soc):.2f} (below "
                    f"{th.min_spot_option_correlation}). The optimizer maximizes a SPOT proxy, so it "
                    "may be steering away from option-profitable configurations. Treat the option "
                    "re-rank table — not the spot ranking — as the source of truth here."
                ),
                "value": {"spot_option_correlation": _safe_float(soc),
                          "min_spot_option_correlation": th.min_spot_option_correlation},
            })
```

Add to the `snapshot` dict (after line 441): `"spot_option_correlation": soc,`
Add to the `thresholds` echo dict (after line 457): `"min_spot_option_correlation": th.min_spot_option_correlation,`

- [ ] **Step 5: Run the host tests — expect PASS**

Run: `python -m pytest tests/test_spot_option_correlation.py -v`
Expected: 5 passed.

- [ ] **Step 6: Wire the optimizer call-site (running-stack verified)**

In `backend/app/optimizer.py`, import the helper near the deployment_quality import usage (top-level import section):

```python
from app.deployment_quality import compute_spot_option_correlation
```

Initialize `corr` before the `option_rerank` block so it is in scope at finalize. Just before `rerank_info = {...}` (line 1105), compute it; add to `rerank_info`:

```python
            spot_option_corr = compute_spot_option_correlation(ranked)
            rerank_info = {
                "top_k": rerank_top_k,
                "diversity": rerank_diversity,
                "candidates": len(candidates),
                "evaluated": len(ranked),
                "option_config": option_cfg,
                "ranked": ranked[:50],
                "survival_summary": survival_summary,
                "spot_option_correlation": spot_option_corr,
            }
```

Initialize `spot_option_corr = None` **right after `rerank_info = None` at line 1002** (alongside `survival_summary = None` at 1003), before the `option_rerank` block. This guarantees spot-mode (where the rerank block does not run) leaves it `None`. Do NOT initialize it after line 1105, or the computed value would be clobbered.

In the finalize `evaluate_source_quality` call (line 1179), thread the correlation into evidence:

```python
            finished["best_quality"] = evaluate_source_quality(best_run, evidence={
                "oos_return_pct": _oos, "n_trials": n_trials,
                "spot_option_correlation": spot_option_corr,
            })
```

- [ ] **Step 7: Verify on the running stack**

Rebuild backend. Run an `evaluation_mode="option_rerank"` optimization with enough candidates → confirm `job.rerank.spot_option_correlation` is a float and, when it is < 0.3, `job.best_quality.warnings` contains `objective_misalignment`. Confirm a `spot`-mode optimization has `spot_option_correlation` absent/None and no new warning (byte-identical behavior).

- [ ] **Step 8: Commit**

```bash
git add backend/app/deployment_quality.py backend/app/optimizer.py tests/test_spot_option_correlation.py
git commit -m "feat(quality): spot<->option correlation metric + objective_misalignment advisory warning"
```

---

### Task 5: Vectorize `session_date`/`ist_time` (byte-identical)

**Files:**
- Modify: `backend/app/indicators.py:265-271`.
- Test: `tests/test_indicator_equivalence.py` (add a targeted equality test).

- [ ] **Step 1: Write the byte-identical equality test**

Add to `tests/test_indicator_equivalence.py`:

```python
def test_session_date_and_ist_time_match_strftime_reference():
    df = _fixture_df()
    enr = precompute_all_indicators(df.copy(), {})
    dt = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    expected_date = dt.dt.strftime("%Y-%m-%d")
    expected_time = dt.dt.strftime("%H:%M")
    pd.testing.assert_series_equal(enr["session_date"], expected_date, check_names=False)
    pd.testing.assert_series_equal(enr["ist_time"], expected_time, check_names=False)
```

- [ ] **Step 2: Run — expect PASS now (current strftime), to lock the reference**

Run: `python -m pytest tests/test_indicator_equivalence.py::test_session_date_and_ist_time_match_strftime_reference -v`
Expected: PASS (this pins the exact current output before the refactor).

- [ ] **Step 3: Replace strftime with a vectorized, byte-identical construction (NaT fallback)**

In `backend/app/indicators.py`, replace lines 265-271. Current:

```python
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    df["session_date"] = df["dt"].dt.strftime("%Y-%m-%d")
    vwap = pd.Series(index=df.index, dtype="float64")
    for _, group in df.groupby("session_date", sort=False):
        vwap.loc[group.index] = session_vwap(group)
    df["vwap"] = vwap
    df["ist_time"] = df["dt"].dt.strftime("%H:%M")
```

Replace with:

```python
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    _dt = df["dt"]
    if _dt.isna().any():
        # Exact strftime fallback preserves NaT -> NaN behavior byte-for-byte.
        df["session_date"] = _dt.dt.strftime("%Y-%m-%d")
        df["ist_time"] = _dt.dt.strftime("%H:%M")
    else:
        # Vectorized C-level integer extraction + string concat (no per-element strftime).
        _d = _dt.dt.normalize()
        df["session_date"] = (_d.dt.year.astype(str).str.zfill(4) + "-"
                              + _d.dt.month.astype(str).str.zfill(2) + "-"
                              + _d.dt.day.astype(str).str.zfill(2))
        df["ist_time"] = (_dt.dt.hour.astype(str).str.zfill(2) + ":"
                          + _dt.dt.minute.astype(str).str.zfill(2))
    vwap = pd.Series(index=df.index, dtype="float64")
    for _, group in df.groupby("session_date", sort=False):
        vwap.loc[group.index] = session_vwap(group)
    df["vwap"] = vwap
```

- [ ] **Step 4: Run the equality test + the full harness — expect PASS**

Run: `python -m pytest tests/test_indicator_equivalence.py -v`
Expected: all PASS (vectorized output byte-identical to strftime on the no-NaT fixture; the whole-frame sweep equality still holds).

- [ ] **Step 5: Commit**

```bash
git add backend/app/indicators.py tests/test_indicator_equivalence.py
git commit -m "perf(indicators): vectorize session_date/ist_time (byte-identical, NaT fallback)"
```

---

## PHASE 2 — Memoization + micro-opts + advisory realism (MEASUREMENT-GATED)

> Per spec §6 sequencing note: Phase 1 ships independently. Phase 2 begins only after Task 6. If Task 2's measurement reshapes §6.1 scope, Phase 2 is re-expanded into its own detailed plan before coding. The tasks below give the concrete design + test strategy; Task 7's full TDD steps are finalized once the gate passes.

### Task 6: Measurement gate (decision)

- [ ] **Step 1: Read Task 2's `job.timing` for confluence_scalper + 2 others.**
- [ ] **Step 2: Decide.** Proceed to Tasks 7-10 if, for confluence_scalper, `precompute_n` ≈ trial count (cache thrash confirmed) AND `precompute_s / evaluate_s` ≥ ~0.20 (precompute is a material per-trial cost). If precompute is immaterial for all measured strategies, **descope Task 7** (memoization) and proceed only with Tasks 8-10 (LRU + micro-opts + advisory warnings), which are measurement-independent. Record the decision in the commit message.

### Task 7: Dependency-keyed indicator-group memoization

**Files:** Create `backend/app/indicator_groups.py` (registry: each group `{param_keys, input_columns, output_columns, compute_fn}` exactly per spec §6.1 table). Modify `backend/app/indicators.py` (`precompute_all_indicators` delegates to the registry; **public signature unchanged**). Modify `backend/app/optimizer.py` + `backend/app/wfo.py` `get_enriched` to assemble from per-group caches.

- [ ] **Step 1: Encode the spec §6.1 registry**, including the hidden edges: `atr_avg` and `regime` keyed on `atr_length` (regime reads `atr_avg`); `tod_tradeable` keyed on `atr_length` (reads `atr`); `squeeze`/`supertrend` NOT keyed on `atr_length` (own local ATR). Param-independent groups computed once; safe copy model (read-only base + each group returns only its own output columns; assembly copies onto a fresh frame).
- [ ] **Step 2: Re-point the Task-1 harness seam** `_enrich_new` at the new memoized assembly and run the FULL sweep (incl. the `atr_length` variations) — `assert_frame_equal` must be green across every strategy × param before any merge. Add the `deployment_evaluator` single-bar and `wfo` per-window caller-shape checks.
- [ ] **Step 3: Implement per-group LRU (K=4)** + measure confluence ema-group cardinality (Task 2 instrumentation) + verify peak RSS on a heavy 12-month run does not regress OOM.
- [ ] **Step 4: Stack-verify** an optimization produces identical best params/metrics vs the pre-refactor run on the same seed/window, and a measurably lower `precompute_s`.
- [ ] **Step 5: Commit** (harness green is the gate).

### Task 8: Raw-candle in-process LRU

**Files:** Modify the candle-load path used by `load_candles_df`.

- [ ] **Step 1:** Add a bounded in-process LRU keyed by `(instrument, start_ts, end_ts)` returning the raw candle frame; immutable historical data → no invalidation. Byte-identical.
- [ ] **Step 2:** Stack-verify a backtest→optimize→WFO sequence on one window loads candles once (log/inspect), with identical results.
- [ ] **Step 3:** Commit.

### Task 9: Per-bar micro-opts (byte-identical)

**Files:** Modify `backend/app/backtest.py`.

- [ ] **Step 1:** Hoist `ctx_global` out of the bar loop (build once; set `i` per entry-eval bar); drop redundant `df.reset_index(drop=True)` when index is already default-integer (guard with a check); replace Trade `__dict__` override storage with real dataclass fields.
- [ ] **Step 2:** Add a backtest equality test (trades + metrics identical pre/post on a fixture) in `tests/`; run — expect PASS.
- [ ] **Step 3:** Commit.

### Task 10: Louder advisory realism warnings (NOT gates)

**Files:** Modify `backend/app/deployment_quality.py`.

- [ ] **Step 1:** Promote option-costs-off and thin-coverage (paired-trade count / pairing ratio below threshold) to prominent advisory warnings reusing the existing `SEVERITY_WARNING` machinery + snapshot keys. Defaults stay permissive; nothing is rejected (decision 3).
- [ ] **Step 2:** Host-TDD in `tests/test_deployment_quality_option.py` (warning present when costs off / coverage thin; absent otherwise).
- [ ] **Step 3:** Commit.

---

## Completion

After all (or the gated subset of) tasks pass: **Use superpowers:finishing-a-development-branch** — verify tests (`python -m pytest tests/...`), then present merge/PR/keep/discard options. Do NOT push/merge without explicit user instruction.
