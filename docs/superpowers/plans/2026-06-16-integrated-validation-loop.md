# Piece 3 — Trustworthy Validation Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the app's existing trust verdict (`deployment_quality.evaluate_source_quality`) **correct** and **omnipresent** — compute the optimizer's promoted-config full-window option result, add three option-₹ checks (full-window fragility, ruin/equity-floor breach, coverage attrition), surface the verdict on the backtest results page + optimizer promotion, and make the deploy gate read the honest promoted-survivor number. **Flag everywhere, never block. No engine/scoring change. No caller-signature change.**

**Architecture:** Four coordinated changes over existing machinery — **Fix-A** (optimizer `_save_best_as_backtest` runs the promoted config through `_run_paired_option_backtest(validate=False, auto_fetch=False)` and stores the option result; finalize reads `best_option_pnl_value`/`best_quality` off the saved run), **Fix-B** (3 self-contained option-₹ checks + dedup in the pure `deployment_quality` module), **Fix-C** (attach `quality` on `GET /backtest/runs/{id}`; render `TrustScorecard`), **Fix-D** (`_gather_deployment_evidence` reads the promoted net via a projection fix). A new `validate` param on `_run_paired_option_backtest` lets the optimizer replay a grid-derived overlay that would otherwise 400.

**Tech Stack:** FastAPI + Pydantic + Motor (backend), pytest (host tests; `deployment_quality` is a pure stdlib-only module — directly importable), React (frontend). The audited spec is `docs/superpowers/specs/2026-06-16-integrated-validation-loop-design.md`. Branch: `feat/integrated-validation-loop`.

**Testing convention:** `deployment_quality` is host-importable (pure, no motor/optuna) → **TDD with unit tests**. `optimizer.py`/`runtime.py`/`routers` import motor → **not** host-importable; they get **contract-corpus string assertions** (the corpus = `server.py` + `app/schemas.py` + `app/runtime.py` + `app/routers/*.py`; `optimizer.py` is NOT in it) plus **running-stack verification** (Task 8). Frontend = build + running-stack. Tests run from repo root: `python -m pytest tests/...`.

---

## File map

| File | Change | Responsibility |
|---|---|---|
| `backend/app/deployment_quality.py` | Modify | Fix-B: 2 new `QualityThresholds` fields + 3 option-₹ checks + dedup + snapshot/thresholds echo. |
| `backend/app/runtime.py` | Modify (`_run_paired_option_backtest`, l.411 + l.577) | Add `validate: bool = True`; gate the overlay-validation block on it. |
| `backend/app/optimizer.py` | Modify (`_save_best_as_backtest` l.444; finalize l.~1115-1150) | Fix-A: compute + store the promoted option result (conditional key); finalize reads `best_option_pnl_value` + `best_quality`. |
| `backend/app/routers/deployments.py` | Modify (`_gather_deployment_evidence` l.~83-100) | Fix-D: projection + read `job.best_option_pnl_value`. |
| `backend/app/routers/research.py` | Modify (`get_backtest_run` l.~360-366) | Fix-C: attach `doc["quality"]`. |
| `frontend/src/components/TrustScorecard.jsx` | Create | Reusable green/amber scorecard. |
| `frontend/src/pages/BacktestLab.jsx` | Modify (`ResultsView` ~l.1623) | Render `<TrustScorecard quality={result.quality}/>`. |
| `frontend/src/pages/Optimizer.jsx` | Modify (`CurrentJobView` ~l.1168) | Render `<TrustScorecard quality={job.best_quality}/>`. |
| `tests/test_deployment_quality_option.py` | Create | Fix-B unit tests (TDD). |
| `tests/test_contract_validation_loop.py` | Create | Contract pins for Fix-A/C/D (validate param, quality attach, best_option_pnl_value). |

---

## Task 1: Fix-B — option-₹ checks in `deployment_quality` (TDD, pure module)

This is the core logic and the only host-testable piece. Build it test-first.

**Files:**
- Modify: `backend/app/deployment_quality.py`
- Test: `tests/test_deployment_quality_option.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_deployment_quality_option.py`:

```python
"""Tests for the Fix-B option-rupee trust checks (Piece 3)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.deployment_quality import (  # noqa: E402
    QualityThresholds,
    evaluate_source_quality,
)


def _ids(res):
    return {w["id"] for w in res["warnings"]}


def _opt_source(net, *, paired=100, spot=100, skipped=0, curve=None,
                ending=None, max_dd=-20.0, missing_contract=0, missing_entry=0,
                metrics=None, walkforward="omit"):
    """A backtest_run-shaped source doc with a paired-option result."""
    if curve is None:
        curve = [{"equity_value": 200000 + net}]
    if ending is None:
        ending = 200000 + net
    src = {
        "metrics": metrics or {"trade_count": 120, "sharpe": 1.0, "win_rate": 50.0,
                               "max_dd_pts": 50.0, "total_pnl_pts": 100.0},
        "option_backtest": {
            "portfolio": {"net_pnl_value": net, "total_return_pct": net / 2000.0,
                          "ending_equity": ending, "max_drawdown_pct": max_dd, "curve": curve},
            "coverage": {"spot_trade_count": spot, "paired_trade_count": paired,
                         "skipped_by_cap": skipped, "missing_contract": missing_contract,
                         "missing_entry_candle": missing_entry},
        },
    }
    if walkforward != "omit":
        src["walkforward"] = walkforward
    return src


# (a) full-window negative with paired>0 -> option_full_window_negative
def test_full_window_negative_fires():
    res = evaluate_source_quality(_opt_source(-24451))
    assert "option_full_window_negative" in _ids(res)
    assert res["acknowledgment_required"] is True


# (a2) escalation: OOS positive but full-window negative -> "fragile" label
def test_fragile_escalation_when_oos_positive():
    res = evaluate_source_quality(_opt_source(-24451), evidence={"oos_return_pct": 9.66})
    w = next(w for w in res["warnings"] if w["id"] == "option_full_window_negative")
    assert "ragile" in w["label"]
    assert w["value"]["oos_signal"] == 9.66


# (b) ruin: negative ending equity / DD>=100 -> ruin_floor_breach
def test_ruin_floor_breach_fires_on_negative_equity():
    res = evaluate_source_quality(
        _opt_source(-412306, curve=[{"equity_value": -212306}], ending=-212306, max_dd=-211.5))
    assert "ruin_floor_breach" in _ids(res)


# (b2) zero-pair run: curve [], net 0.0 -> NO crash, NO fragility, NO ruin
def test_zero_pair_run_no_crash_no_false_negative():
    res = evaluate_source_quality(_opt_source(0, paired=0, curve=[], ending=200000, max_dd=0.0))
    assert "option_full_window_negative" not in _ids(res)
    assert "ruin_floor_breach" not in _ids(res)


# (c) low DATA coverage -> coverage_attrition
def test_coverage_attrition_fires_on_low_data_coverage():
    res = evaluate_source_quality(_opt_source(100, spot=100, paired=40, skipped=0, missing_contract=60))
    assert "coverage_attrition" in _ids(res)


# (c2) intentional caps must NOT trigger coverage_attrition
def test_intentional_cap_skips_not_flagged_as_attrition():
    res = evaluate_source_quality(_opt_source(100, spot=100, paired=40, skipped=60))
    assert "coverage_attrition" not in _ids(res)


# (d) clean positive option run -> none of the three
def test_clean_positive_option_run_no_option_warnings():
    res = evaluate_source_quality(_opt_source(50000, paired=100, spot=100))
    assert _ids(res).isdisjoint({"option_full_window_negative", "ruin_floor_breach", "coverage_attrition"})


# (e) spot-only doc + evidence=None -> unchanged (no option warnings, no crash)
def test_spot_only_source_byte_identical_warnings():
    src = {"metrics": {"trade_count": 120, "sharpe": 1.0, "win_rate": 50.0,
                       "max_dd_pts": 50.0, "total_pnl_pts": 100.0},
           "walkforward": {"is_vs_oos": {"avg_is_win_rate": 60.0, "avg_oos_win_rate": 55.0,
                                         "divergence_warning": False}}}
    res = evaluate_source_quality(src)
    assert _ids(res).isdisjoint({"option_full_window_negative", "ruin_floor_breach", "coverage_attrition"})


# (f) dedup: source WITH option_backtest AND evidence -> legacy option_oos suppressed
def test_dedup_suppresses_legacy_option_oos_when_option_backtest_present():
    res = evaluate_source_quality(_opt_source(-24451), evidence={"oos_return_pct": 5.0, "n_trials": 50})
    ids = _ids(res)
    assert "option_full_window_negative" in ids
    assert "option_oos_negative" not in ids
    assert "missing_option_oos" not in ids


# (g) option doc + evidence=None (results-page call) -> no crash, escalation falls back
def test_option_doc_with_evidence_none_does_not_crash():
    res = evaluate_source_quality(_opt_source(-24451, walkforward={"is_vs_oos": {"divergence_warning": False}}))
    assert "option_full_window_negative" in _ids(res)  # did not raise


# (g2) option doc with walkforward None -> no crash in the wf fallback
def test_option_doc_with_walkforward_none_does_not_crash():
    res = evaluate_source_quality(_opt_source(-24451, walkforward=None))
    assert "option_full_window_negative" in _ids(res)


# thresholds: new knobs in returned dict + from_overrides
def test_new_thresholds_present_and_overridable():
    res = evaluate_source_quality(_opt_source(50000))
    assert "ruin_floor" in res["thresholds"]
    assert "min_coverage_ratio" in res["thresholds"]
    th = QualityThresholds.from_overrides(ruin_floor=-5000.0, min_coverage_ratio=0.5)
    assert th.ruin_floor == -5000.0 and th.min_coverage_ratio == 0.5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_deployment_quality_option.py -v`
Expected: FAIL (warnings missing / `thresholds` lacks new keys).

- [ ] **Step 3: Add the two `QualityThresholds` fields**

In `backend/app/deployment_quality.py`, in the `QualityThresholds` dataclass (after `wf_efficiency_min`):

```python
    wf_efficiency_min: float = WF_EFFICIENCY_MIN
    ruin_floor: float = 0.0            # equity-floor for ruin breach (rupees)
    min_coverage_ratio: float = 0.70   # paired / addressable below this -> coverage warning
```

- [ ] **Step 4: Resolve `om` once + dedup the legacy option-OOS block**

Near the top of `evaluate_source_quality`, right after `wf = _walkforward(source_doc)`:

```python
    om = source_doc.get("option_backtest")   # self-contained option result (Fix-B); also drives the dedup
```

Then gate the EXISTING evidence-driven option-OOS sub-block so it is suppressed when a self-contained option result is present. Wrap the block that resolves `option_oos_net` and appends `option_oos_negative` / `missing_option_oos` (the `# 6. Option-rupee OOS …` section) in `if not om:`:

```python
        # 6. Option-rupee OOS — does the spot edge survive premium/spread/costs?
        if not om:   # Fix-B dedup: self-contained option_full_window_negative covers this when om present
            if wfo_ev.get("option_oos_net") is not None:
                option_oos_net = _safe_float(wfo_ev.get("option_oos_net"))
                option_oos_source = "option-aware walk-forward (OOS)"
            elif opt_ev.get("net_pnl_value") is not None:
                option_oos_net = _safe_float(opt_ev.get("net_pnl_value"))
                option_oos_source = f"option backtest ({opt_ev.get('kind') or 'run'})"

            if option_oos_net is not None and option_oos_net <= 0:
                warnings.append({ ... })   # existing option_oos_negative block, unchanged
            elif option_oos_net is None:
                warnings.append({ ... })   # existing missing_option_oos block, unchanged
```

(Keep the `selection_bias` block above it untouched — it stays active.)

- [ ] **Step 5: Add the three option-₹ checks**

Immediately AFTER the `if isinstance(evidence, dict):` block and BEFORE the `snapshot = {...}` assignment, add:

```python
    # --- Option-rupee checks (Fix-B): self-contained from the source's option result ---
    om_net = None
    om_min_equity = None
    om_ratio = None
    if om:
        port = om.get("portfolio") or {}
        cov = om.get("coverage") or {}
        paired = cov.get("paired_trade_count") or 0
        spot = cov.get("spot_trade_count") or 0
        skipped = cov.get("skipped_by_cap") or 0
        om_net = port.get("net_pnl_value")
        oos_rp = (evidence or {}).get("oos_return_pct")

        # 1. Full-window fragility (gate on paired>0, strict <0; zero-pair routes to coverage)
        if paired > 0 and om_net is not None and om_net < 0:
            oos_positive = (oos_rp is not None and oos_rp > 0)
            wf_ok = wf is not None and not (((wf or {}).get("is_vs_oos") or {}).get("divergence_warning"))
            fragile = oos_positive or wf_ok
            if fragile:
                label = "Fragile: positive out-of-sample, negative full-window"
                detail = (f"Option result is ₹{om_net:,.0f} over the full window even though it looked "
                          "positive out-of-sample. The recent slice carried it — do not deploy on the OOS number alone.")
            else:
                label = "Negative full-window option result"
                detail = (f"Option result is ₹{om_net:,.0f} over the full window after premium decay, "
                          "bid-ask spread and charges.")
            warnings.append({
                "id": "option_full_window_negative", "severity": SEVERITY_WARNING,
                "label": label, "detail": detail,
                "value": {"net_pnl_value": om_net, "total_return_pct": port.get("total_return_pct"),
                          "oos_signal": oos_rp},
            })

        # 2. Ruin / equity-floor breach (empty-curve guarded)
        eqs = [c.get("equity_value") for c in (port.get("curve") or []) if c.get("equity_value") is not None]
        om_min_equity = min(eqs) if eqs else None
        ending = port.get("ending_equity")
        max_dd = port.get("max_drawdown_pct")
        if ((om_min_equity is not None and om_min_equity <= th.ruin_floor)
                or (ending is not None and ending < 0)
                or (abs(max_dd or 0) >= 100)):
            shown = om_min_equity if om_min_equity is not None else (ending if ending is not None else 0.0)
            warnings.append({
                "id": "ruin_floor_breach", "severity": SEVERITY_WARNING,
                "label": "Account ruin / equity-floor breach",
                "detail": (f"Equity reached ₹{shown:,.0f} (floor ₹{th.ruin_floor:,.0f}). The account would "
                           "be wiped, yet the backtest keeps trading past ruin — the rupee result is fiction."),
                "value": {"min_equity": om_min_equity, "ending_equity": ending,
                          "max_drawdown_pct": max_dd, "ruin_floor": th.ruin_floor},
            })

        # 3. Coverage attrition (DATA only; intentional cap-skips excluded)
        addressable = spot - skipped
        if addressable > 0 and (paired / addressable) < th.min_coverage_ratio:
            om_ratio = round(paired / addressable, 3)
            missing = (cov.get("missing_contract") or 0) + (cov.get("missing_entry_candle") or 0)
            warnings.append({
                "id": "coverage_attrition", "severity": SEVERITY_WARNING,
                "label": "Low option-data coverage",
                "detail": (f"Only {paired}/{addressable} non-capped signals ({round(100 * paired / addressable, 1)}%) "
                           f"paired with option data — {missing} missing option data "
                           f"({skipped} additionally skipped by daily caps). Result may not be representative."),
                "value": {"paired": paired, "spot": spot, "addressable": addressable, "ratio": om_ratio,
                          "skipped_by_cap": skipped, "missing_contract": cov.get("missing_contract"),
                          "missing_entry_candle": cov.get("missing_entry_candle")},
            })
```

- [ ] **Step 6: Add option fields to the snapshot + the new thresholds to the echo**

In the `snapshot = {...}` dict, add:

```python
        "option_net_pnl_value": om_net,
        "option_min_equity": om_min_equity,
        "option_coverage_ratio": om_ratio,
```

In the returned `thresholds` dict, add the two new keys:

```python
            "ruin_floor": th.ruin_floor,
            "min_coverage_ratio": th.min_coverage_ratio,
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_deployment_quality_option.py -v`
Expected: PASS (all 12).

- [ ] **Step 8: Run the existing suite to confirm no regression**

Run: `python -m pytest tests/test_deployment_quality.py -v`
Expected: PASS (unchanged — Fix-B is gated on `option_backtest` presence; existing tests use spot-only sources).

- [ ] **Step 9: Commit**

```bash
git add backend/app/deployment_quality.py tests/test_deployment_quality_option.py
git commit -m "feat(trust): option-rupee checks (fragility, ruin, coverage) + dedup in deployment_quality"
```

---

## Task 2: Runtime — `validate` param on `_run_paired_option_backtest`

Lets the optimizer replay a grid-derived `spot_exit`+`exit_controls` overlay (which would otherwise 400). `runtime.py` is in the contract corpus.

**Files:**
- Modify: `backend/app/runtime.py:411` (signature) + `:577` (gate)
- Test: `tests/test_contract_validation_loop.py`

- [ ] **Step 1: Write the failing contract test**

Create `tests/test_contract_validation_loop.py`:

```python
from tests.contract_corpus import backend_api_text

API = backend_api_text()


def test_run_paired_option_backtest_has_validate_param():
    # the optimizer replays a grid overlay through the runtime with validation off
    assert "validate: bool = True" in API


def test_get_backtest_run_attaches_quality():
    # Fix-C: the run-detail read computes the trust verdict
    assert "evaluate_source_quality" in API


def test_deploy_evidence_reads_promoted_net():
    # Fix-D: the deploy evidence gatherer reads the promoted full-window net
    assert "best_option_pnl_value" in API
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_contract_validation_loop.py::test_run_paired_option_backtest_has_validate_param -v`
Expected: FAIL (`validate: bool = True` not in corpus).

- [ ] **Step 3: Add the param + gate the validation**

In `backend/app/runtime.py`, change the signature (line 411):

```python
async def _run_paired_option_backtest(req: BacktestReq, spot_trades: List[Dict[str, Any]], validate: bool = True) -> Optional[Dict[str, Any]]:
```

And gate the validation block (line 577):

```python
    if validate and (config.exit_controls or config.daily_caps):
        from app.exit_controls import validate_exit_risk_config
        errs = validate_exit_risk_config(
            config.exit_controls.model_dump() if config.exit_controls else None,
            config.daily_caps.model_dump() if config.daily_caps else None,
            costs_on=bool((config.cost_config or {}).get("enabled")),
            option_exec_on=(config.exit_mode == "option_levels"))
        if errs:
            raise HTTPException(400, "; ".join(errs))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_contract_validation_loop.py::test_run_paired_option_backtest_has_validate_param -v`
Expected: PASS. (The other two tests in this file still FAIL until Tasks 4 and 5 — that's expected.)

- [ ] **Step 5: Byte-compile + commit**

```bash
python -m py_compile backend/app/runtime.py
git add backend/app/runtime.py tests/test_contract_validation_loop.py
git commit -m "feat(runtime): validate=False bypass on _run_paired_option_backtest for trusted internal replays"
```

---

## Task 3: Fix-A — optimizer stores the promoted config's full-window option result

**Files:**
- Modify: `backend/app/optimizer.py` — `_save_best_as_backtest` (def ~444) + the doc dict + the finalize block (~1115-1150).

- [ ] **Step 1: Compute + store the option result in `_save_best_as_backtest`**

In `_save_best_as_backtest`, AFTER `res = await asyncio.to_thread(run_backtest, ...)` and BEFORE the `doc = {...}` literal, add:

```python
        option_result = None
        if option_config:
            try:
                from app.runtime import _run_paired_option_backtest   # lazy: cycle-free, mirrors walk_forward import
                from app.schemas import BacktestReq, OptionBacktestReq
                req = BacktestReq(
                    instrument=instrument, strategy_id=strategy.id, params=best_params,
                    start_ts=payload.get("start_ts"), end_ts=payload.get("end_ts"),
                    costs_enabled=costs_enabled, walkforward=False, pretrade_filters=pretrade,
                    option_backtest=OptionBacktestReq(**{**option_config, "enabled": True, "auto_fetch": False}),
                )
                option_result = await _run_paired_option_backtest(req, res["trades"], validate=False)
            except Exception as e:
                log.warning(f"save_best option backtest failed: {e}")
```

- [ ] **Step 2: Add the conditional top-level `option_backtest` key to the doc**

In the `doc = {...}` literal, add this entry (mirroring the existing conditional `config.option_backtest` echo so spot-mode is byte-identical):

```python
            **({"option_backtest": option_result} if option_config else {}),
```

- [ ] **Step 3: Finalize — read `best_option_pnl_value` + `best_quality` off the saved run**

In the finalize block (where `best_backtest_run_id = await _save_best_as_backtest(...)` is followed by building/persisting the `finished` dict), after `best_backtest_run_id` is set, add:

```python
        from app.deployment_quality import evaluate_source_quality   # leaf module, no cycle
        best_run = best_backtest_run_id and await db.backtest_runs.find_one({"id": best_backtest_run_id}, {"_id": 0})
        if best_run:
            finished["best_option_pnl_value"] = ((best_run.get("option_backtest") or {}).get("portfolio") or {}).get("net_pnl_value")
            _oos = (best_so_far.get("metrics") or {}).get("survival", {}).get("total_return_pct")
            finished["best_quality"] = evaluate_source_quality(
                best_run, evidence={"oos_return_pct": _oos, "n_trials": n_trials})
```

(Use whatever the finalize dict is actually named — it is `$set`-merged via `_update_job`, so these keys are additive. `best_run` is None for `done_no_survivor`/save-failure, so the block is skipped.)

- [ ] **Step 4: Byte-compile**

Run: `python -m py_compile backend/app/optimizer.py`
Expected: compile OK. (Optimizer is not host-importable nor in the contract corpus; correctness is proven in Task 8 running-stack.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/optimizer.py
git commit -m "feat(optimizer): save promoted config full-window option result + best_quality/best_option_pnl_value"
```

---

## Task 4: Fix-D — deploy gate reads the promoted survivor's number

**Files:**
- Modify: `backend/app/routers/deployments.py` — `_gather_deployment_evidence` (query projection ~l.86 + the rerank-job loop ~l.88-100).

- [ ] **Step 1: Add `best_option_pnl_value` to the rerank-job query projection**

In `_gather_deployment_evidence`, the rerank-job query (`db.optimization_jobs.find({...}, {projection})`) uses an inclusion projection. Add the field:

```python
        {"_id": 0, "id": 1, "finished_at": 1, "best_params": 1,
         "best_option_pnl_value": 1, "rerank.ranked": {"$slice": 1}},
```

- [ ] **Step 2: Source `net` from the promoted value inside the existing guard**

In the rerank-job loop, the existing matching-assignment builds `option_evidence`. Change ONLY the `net_pnl_value` source to prefer the promoted number, keeping the surrounding `if option_evidence is None or (match and not option_evidence.get("params_match")):` guard and the `if option_evidence.get("params_match"): break`:

```python
        if option_evidence is None or (match and not option_evidence.get("params_match")):
            net = job.get("best_option_pnl_value")          # Fix-D: promoted, with-overlay, full-window
            if net is None:
                net = top.get("option_pnl_value")           # legacy fallback (base-config ranked[0])
            option_evidence = {
                "kind": "rerank", "id": job.get("id"), "at": job.get("finished_at"),
                "net_pnl_value": net,
                "win_rate": top.get("option_win_rate"),
                "paired_trade_count": top.get("paired_trade_count"),
                "params_match": match,
            }
        if option_evidence.get("params_match"):
            break
```

- [ ] **Step 3: Run the Fix-D contract test to verify it passes**

Run: `python -m pytest tests/test_contract_validation_loop.py::test_deploy_evidence_reads_promoted_net -v`
Expected: PASS (`best_option_pnl_value` now in `deployments.py`).

- [ ] **Step 4: Byte-compile + commit**

```bash
python -m py_compile backend/app/routers/deployments.py
git add backend/app/routers/deployments.py
git commit -m "feat(deploy): trust gate reads the promoted survivor's full-window option net (Fix-D)"
```

---

## Task 5: Fix-C (backend) — attach `quality` on the run-detail read

**Files:**
- Modify: `backend/app/routers/research.py` — `get_backtest_run` (~l.360-366).

- [ ] **Step 1: Attach the verdict before serialization**

In `get_backtest_run`, after `doc = await db.backtest_runs.find_one({"id": run_id}, {"_id": 0})` and before `return serialize_doc(doc)`:

```python
    if doc:
        from app.deployment_quality import evaluate_source_quality
        try:
            doc["quality"] = evaluate_source_quality(doc)
        except Exception:
            pass   # never break the read; scorecard is omitted if it can't compute
    return serialize_doc(doc)
```

(Match the existing not-found handling — if `get_backtest_run` raises `404` on a missing doc today, keep that; only attach when `doc` is truthy.)

- [ ] **Step 2: Run the Fix-C contract test to verify it passes**

Run: `python -m pytest tests/test_contract_validation_loop.py -v`
Expected: PASS (all 3 — `evaluate_source_quality` now in `research.py`).

- [ ] **Step 3: Byte-compile + commit**

```bash
python -m py_compile backend/app/routers/research.py
git add backend/app/routers/research.py
git commit -m "feat(backtest): attach trust verdict (quality) on GET /backtest/runs/{id} (Fix-C)"
```

---

## Task 6: Fix-C (frontend) — `TrustScorecard` + wiring

**Files:**
- Create: `frontend/src/components/TrustScorecard.jsx`
- Modify: `frontend/src/pages/BacktestLab.jsx` (`ResultsView`, ~l.1623)
- Modify: `frontend/src/pages/Optimizer.jsx` (`CurrentJobView`, ~l.1168)

- [ ] **Step 1: Create the component**

`frontend/src/components/TrustScorecard.jsx`:

```jsx
import { AlertTriangle, ShieldCheck } from "lucide-react";

/**
 * Advisory trust verdict — never blocks. Green when no warnings, amber otherwise.
 * `quality` is the object from deployment_quality.evaluate_source_quality
 * ({ acknowledgment_required, warnings: [{id,label,detail}], ... }).
 */
export function TrustScorecard({ quality }) {
  if (!quality) return null;
  const warnings = quality.warnings || [];
  const ok = warnings.length === 0;
  return (
    <div className={`rounded-lg border p-3 ${ok ? "border-success/40 bg-success/5" : "border-amber-400/40 bg-amber-400/5"}`}
         data-testid="trust-scorecard">
      <div className="flex items-center gap-2 mb-2">
        {ok ? <ShieldCheck className="w-4 h-4 text-success" /> : <AlertTriangle className="w-4 h-4 text-amber-400" />}
        <span className="text-[11px] font-semibold uppercase tracking-wider text-dim">
          Trust {ok ? "· no warnings" : `· ${warnings.length} warning${warnings.length > 1 ? "s" : ""}`}
        </span>
      </div>
      {ok ? (
        <div className="text-[11px] text-dimmer">No trust warnings on this result.</div>
      ) : (
        <ul className="space-y-1.5">
          {warnings.map((w) => (
            <li key={w.id} className="text-[11px]">
              <span className="text-amber-300 font-medium">{w.label}</span>
              <span className="text-dimmer"> — {w.detail}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="text-[10px] text-dimmer mt-2 leading-snug">
        Advisory only — nothing is blocked. Option-₹ headline figures are full-window, not walk-forward validated.
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Render in the backtest results view**

In `frontend/src/pages/BacktestLab.jsx`: add `import { TrustScorecard } from "@/components/TrustScorecard";` near the other imports, and inside `ResultsView` (which receives `result`), render `<TrustScorecard quality={result?.quality} />` near the top of the results (e.g. just above `<PerformanceOverview result={result} />`).

- [ ] **Step 3: Render in the optimizer result panel**

In `frontend/src/pages/Optimizer.jsx`: add the same import, and inside `CurrentJobView` (which receives `job`), render `<TrustScorecard quality={job?.best_quality} />` in the result/summary area of a finished job.

- [ ] **Step 4: Build clean**

Run: `cd frontend && npm run build`
Expected: succeeds, no new warnings beyond pre-existing. (`lucide-react` icons are already used across the app.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TrustScorecard.jsx frontend/src/pages/BacktestLab.jsx frontend/src/pages/Optimizer.jsx
git commit -m "feat(ui): TrustScorecard on backtest results + optimizer promotion"
```

---

## Task 7: Optional — expose the two new knobs on the quality preview route

YAGNI-gated: only if the deploy-quality preview route should let the operator tune the new thresholds. The spec marks this **optional** (the route exposes 4 of 7 knobs today). Skip unless requested.

**Files:** `backend/app/routers/deployments.py` (`deployment_quality_route` query params + `from_overrides`).

- [ ] **Step 1 (if doing):** add `ruin_floor: float | None = Query(None)` and `min_coverage_ratio: float | None = Query(None)` to `deployment_quality_route`'s signature and pass them through `QualityThresholds.from_overrides(...)`. Commit. Otherwise mark this task skipped.

---

## Task 8: Running-stack verification (the real proof for Fix-A/C/D + frontend)

`optimizer.py` is not in the contract corpus and `runtime`/routers aren't host-importable, so the engine wiring is proven by observation (per the verify skill + prior pieces).

**Files:** none (observation only).

- [ ] **Step 1: Rebuild + restart the backend container**

Run: `docker compose up -d --build backend` (repo root). Confirm `GET /api/health` → `{"db":"ok"}`.

- [ ] **Step 2: Fix-A — overlay survivor gets a non-None option result (proves `validate=False`)**

Run a small `option_rerank` + `search_exit_controls` optimization that promotes an **overlay survivor** (exit_controls.enabled, default `spot_exit`). Confirm via API: the job's `best_backtest_run_id` run has `option_backtest.metrics != None` (without `validate=False` it would be spot-only) and the job carries `best_option_pnl_value` + `best_quality`. A `done_no_survivor` job has **no** `best_quality` (no crash).

- [ ] **Step 3: Fix-C — scorecard on a fragile run**

Open a known-fragile run (e.g. the SEB survivor: OOS-positive, full-window ≈ −12%) in the Backtest Lab → `TrustScorecard` shows **amber** with `option_full_window_negative` (fragile label). Open the −206% run → also `ruin_floor_breach` + `coverage_attrition`. A clean spot-only run → green / no option warnings. (Rebuild + cache-bust the **frontend** container too — prior pieces showed stale builds otherwise.)

- [ ] **Step 4: Fix-D — deploy gate reads the promoted net**

Apply the survivor preset → open the deploy wizard → the quality warnings reflect the **promoted-survivor** option-₹ net (negative → `option_oos_negative` fires), not a positive base-config number. Confirm `_gather_deployment_evidence` returns `option_evidence.net_pnl_value` == the promoted `best_option_pnl_value`.

- [ ] **Step 5: Report** — capture the optimizer job doc (`best_quality`/`best_option_pnl_value`), the scorecard screenshot, and the deploy-gate evidence. Verdict PASS/FAIL/BLOCKED per the verify skill.

---

## Self-review notes (done during planning)

- **Spec coverage:** §5.1 Fix-A → Tasks 2+3; §5.2 Fix-B → Task 1; §5.3 Fix-C → Tasks 5+6; §5.4 Fix-D → Task 4; §6 no-degradation → Task 1 Step 8 + the conditional key (Task 3 Step 2) + running-stack Task 8; §8 testing → Tasks 1/2 (unit + contract) + Task 8 (running-stack); §7 error handling → the guards in Task 1 Step 5 (empty-curve `min`, `(evidence or {})`, `(wf or {})`, `cov ... or 0`) + the try/excepts in Tasks 3/5. All covered.
- **Type/name consistency:** `option_backtest` (top-level result), `portfolio.net_pnl_value`, `coverage.{spot,paired}_trade_count`/`skipped_by_cap`/`missing_contract`/`missing_entry_candle`, `quality`, `best_quality`, `best_option_pnl_value`, `evaluate_source_quality`, `validate`, `auto_fetch` — used identically across tasks and matching the audit-confirmed real shapes.
- **No caller-signature change:** `_save_best_as_backtest` keeps returning `run_id` (Task 3 reads the net at finalize, not via the return) — so `wfo.py:705` is untouched. Confirmed by the completion-pass audit.
- **Order/dependency:** Task 1 (pure, independent) first; Task 2 (runtime param) before Task 3 (uses it); Task 3 (writes `best_option_pnl_value`) before Task 4 (reads it); Tasks 5/6 (surfacing) before Task 8 (verify).
