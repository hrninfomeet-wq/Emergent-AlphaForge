# Piece 3 — Integrated optimize→validate→accept loop (trustworthy validation) — Design Spec

**Date:** 2026-06-16
**Status:** Approved design (pending spec self-review + user review)
**Branch:** `feat/integrated-validation-loop` off `feat/backtest-exit-controls` (the newest stack tip; see §3)
**Scope:** Make the app's existing "deployment quality" trust verdict **correct** and **omnipresent** — so a fragile (out-of-sample-positive but full-window-negative) or account-ruining configuration is **flagged loudly at every surface** (backtest results, optimizer promotion, deploy), while never being blocked. Turns the manual cross-check discipline (from the 2026-06-16 option-buying hunt) into the product.

> **Origin.** A survival-gated optimizer sweep repeatedly produced *fragile* survivors — OOS-positive but full-window option-₹ negative — and a user run that read "+291%" was actually −206% (account went negative). The validation that should have caught these exists (`deployment_quality.py`) but is **starved of the right data** and **only runs at deploy**. This piece fixes the data, extends the existing gate, and surfaces it everywhere. **No new trust module; no blocking.**

---

## 1. Problem / objective

The app already has a mature, pure trust verdict — `app/deployment_quality.py::evaluate_source_quality` — that warns (never blocks) and requires explicit acknowledgment before a deployment is created. But three gaps make it untrustworthy in practice:

1. **The optimizer never computes the promoted config's full-window option result.** `_save_best_as_backtest` (optimizer.py:445) runs `run_backtest` — **spot only**. The saved "best" run carries `config.option_backtest` (the config) but **no `option_backtest` result**. So the honest, exact-params, with-overlay option-₹ number for the thing you're about to deploy **does not exist anywhere** — which is why the deploy gate showed *"Option rupee evidence exists but for different params"* and why the hunt required a manual cross-check.
2. **The verdict only runs at deploy.** `evaluate_source_quality` is called when a deployment is created, **not** on the backtest results page (where −206% showed with no flag) nor on the optimizer promotion (which promotes a fragile survivor silently).
3. **Two genuinely-missing checks.** The gate has a *points*-based drawdown check but no option-₹ **ruin/equity-floor breach** (the −₹212k negative-equity case) and no **coverage-attrition** check.

**Objective:** close gap 1 (the linchpin), extend the gate with the two missing option-₹ checks (gap 3), and surface the same verdict on the research surfaces (gap 2) — so "flag everywhere, never block" actually holds.

## 2. What already exists vs what this adds

**Already exists (verified in code):**
- `evaluate_source_quality(source_doc, *, evidence=None, thresholds=None)` (deployment_quality.py:167) — pure, no DB/network. Returns `{acknowledgment_required, warnings[], metrics_snapshot, thresholds, computed_at}`. Philosophy (file docstring): *"Surface them as warnings — never block… the user must explicitly acknowledge."* Checks today: `missing_walk_forward`, `walk_forward_divergence`, `low_trade_count`, `weak_sharpe`, `large_drawdown` (points), and evidence-driven `selection_bias` (deflated Sharpe) + `option_oos_negative`/`missing_option_oos`.
- `QualityThresholds` (deployment_quality.py:63) — tunable knobs via `from_overrides`.
- The deploy path: `_gather_deployment_evidence` (deployments.py:38) → `evaluate_source_quality` (deployments.py:179); a preview route `/deployments/quality`; a `/deployments/readiness` route. The deploy wizard renders the warnings + an "acknowledge to deploy" gate.
- The evidence gatherer's backtest-run source (deployments.py:102) filters `config.option_backtest.enabled:True` **AND `option_backtest.metrics != None`** — so a spot-only saved run is **invisible** to it; its other source reads `rerank.ranked[0]` (the highest-option-P&L candidate, base-config — *not* necessarily the promoted survivor).

**This piece adds:**
1. **Fix-A** — the optimizer computes + stores the promoted config's **full-window paired-option result** on the saved best run (§5.1).
2. **Fix-B** — three new option-₹ checks in `evaluate_source_quality`, read self-contained from `source_doc.option_backtest` (§5.2).
3. **Fix-C** — surface the verdict on the **backtest results page** and the **optimizer promotion** via a reusable `TrustScorecard`, reusing the same pure function (§5.3).

## 3. Decisions (locked with the user)

| Decision | Choice |
|---|---|
| Enforcement | **Flag everywhere, never block.** Compute + surface the verdict at every surface; the deploy "acknowledge to deploy" gate stays the one place that gates (unchanged). |
| Module | **No new module.** Extend `deployment_quality.py`; reuse `evaluate_source_quality`. A parallel `trust.py` would duplicate it. |
| Checks in scope | **Full-window fragility, ruin/equity-floor breach, coverage & sample.** **Sizing-sanity is OUT** (deferred to adaptive Plan 4 — edge-proportional sizing; the ruin check still catches the *symptom*). |
| Non-WF note | A **standing informational caveat** ("this option-₹ headline is not walk-forward validated"), not a pass/fail check. No option-WF engine is built here. |
| Fix-A seam | `_save_best_as_backtest` builds a `BacktestReq` and calls `_run_paired_option_backtest(req, trades)` via a **lazy import** (mirrors the function's existing lazy imports) → **full shape parity** with a manual option backtest. |
| Branch | `feat/integrated-validation-loop` off `feat/backtest-exit-controls`. |

## 4. Architecture — fix the data, extend the gate, surface everywhere

```
OPTIMIZER (run_optimization → finalize)
  _save_best_as_backtest(best_params, option_config={…,exit_controls:best,…})
     ├─ run_backtest (spot)                       (unchanged)
     └─ Fix-A: build BacktestReq → _run_paired_option_backtest(req, trades)
                 → doc["option_backtest"] = full-window option result  ──┐
  finalize: job["best_quality"] = evaluate_source_quality(best_run, evidence={…OOS…})

deployment_quality.evaluate_source_quality(source_doc, evidence, thresholds)
  existing spot/WF/selection-bias/option-OOS checks               (unchanged)
  + Fix-B: when source_doc.option_backtest present →
        option_full_window_negative · ruin_floor_breach · coverage_attrition

SURFACES (Fix-C, all reuse the pure verdict; none block)
  GET /backtest/runs/{id}  → attach quality = evaluate_source_quality(doc)  → <TrustScorecard/>
  Optimizer result panel   → job.best_quality                              → <TrustScorecard/>
  Deploy wizard            → already calls it (now honest, via Fix-A)      → existing ack-gate
```

## 5. Components

### 5.1 Fix-A — optimizer saves the promoted config's full-window option result

**File:** `backend/app/optimizer.py` — `_save_best_as_backtest` (line 445; called at 1132 with `option_config={**option_cfg, "exit_controls": best_so_far["exit_controls"], "daily_caps": best_so_far["daily_caps"]}`).

Today the function runs `run_backtest` (spot) and stores `config.option_backtest = {**option_config, "enabled": True}` but **no option result**. Add: when `option_config` is present, after the spot backtest, compute the full-window paired-option result and store it.

```python
# inside _save_best_as_backtest, after `res = run_backtest(...)`, before building `doc`:
option_result = None
if option_config:
    try:
        from app.runtime import _run_paired_option_backtest   # lazy: avoids module-load cycle
        from app.schemas import BacktestReq, OptionBacktestReq
        req = BacktestReq(
            instrument=instrument, strategy_id=strategy.id, params=best_params,
            start_ts=payload.get("start_ts"), end_ts=payload.get("end_ts"),
            costs_enabled=costs_enabled, walkforward=False,
            pretrade_filters=pretrade,
            option_backtest=OptionBacktestReq(**{**option_config, "enabled": True}),
        )
        option_result = await _run_paired_option_backtest(req, res["trades"])
    except Exception as e:
        log.warning(f"save_best option backtest failed: {e}")  # fall back to spot-only
```

Then in the `doc`:
```python
"option_backtest": option_result,    # full-window result of the PROMOTED config (params + chosen overlay)
```
(Keep the existing `config.option_backtest` config echo as-is.)

**Why `_run_paired_option_backtest` (not `simulate_paired_option_trades`):** it produces the **exact same shape** as a manual backtest (portfolio + metrics + coverage + segregated `skipped_trades`), so (a) the results-page UI reads it identically, (b) the evidence gatherer's `option_backtest.metrics != None` filter (deployments.py:104) now matches → the run is discoverable as **exact-params** option evidence, and (c) a manual re-run of the same config reproduces the number. The lazy import inside the function avoids any module-load circular import (the function already lazy-imports `walk_forward`). Cost: one extra option sim at job end (candles re-loaded for the best's trades — a subset of the re-rank's union load, so lighter than the re-rank stage). Validation inside `_run_paired_option_backtest` cannot 400 here (the promoted overlay comes from `exit_control_grid`, valid by construction); any failure is caught → spot-only fallback (today's behavior).

**Note (no ranking change):** `best_metrics.option_pnl_value` (the re-rank's base-config ranking value) is left untouched; the new `option_backtest.portfolio.net_pnl_value` is the **authoritative** full-window with-overlay number that the quality checks + evidence consume.

### 5.2 Fix-B — three option-₹ checks in `evaluate_source_quality`

**File:** `backend/app/deployment_quality.py`. Add a block that runs **only when `source_doc.get("option_backtest")` is present** (so spot-only sources and `evidence=None` callers are byte-identical to today). Read:
`om = source_doc["option_backtest"]; port = om.get("portfolio") or {}; m = om.get("metrics") or {}; cov = om.get("coverage") or {}`.

New `QualityThresholds` fields: `ruin_floor: float = 0.0`, `min_coverage_ratio: float = 0.70`.

1. **`option_full_window_negative`** — when `port.get("net_pnl_value")` is not None and `≤ 0` (fallback to `port.get("total_return_pct") ≤ 0`). **Escalate the label/detail to "fragile — positive out-of-sample but negative over the full window"** when an OOS-positive signal is available: spot WF OOS positive (`wf.is_vs_oos.avg_oos_win_rate` healthy / `divergence_flag` false) **or** an explicit `evidence["oos_return_pct"] > 0` (the optimizer passes the survival OOS return here — see §5.3). Value: `{net_pnl_value, total_return_pct, oos_signal}`.
2. **`ruin_floor_breach`** — compute `min_equity = min(c["equity_value"] for c in port.get("curve", []) if equity present)`; warn when `min_equity ≤ th.ruin_floor` **or** `port.get("ending_equity") < 0` **or** `abs(port.get("max_drawdown_pct") or 0) ≥ 100`. Detail: *"Equity reached ₹{min_equity} (≤ floor ₹{ruin_floor}); the account would be wiped and the backtest keeps trading past ruin."* Value: `{min_equity, ending_equity, max_drawdown_pct, ruin_floor}`.
3. **`coverage_attrition`** — `spot = cov.get("spot_trade_count")`, `paired = cov.get("paired_trade_count")`; when `spot > 0` and `paired/spot < th.min_coverage_ratio`, warn. Detail breaks it down: *"Only {paired}/{spot} signals ({pct}%) traded — {skipped_by_cap} skipped by daily caps, {missing} missing option data."* Value: `{paired, spot, ratio, skipped_by_cap, missing_contract, missing_entry_candle}`.

All three use `severity: SEVERITY_WARNING`, append to `warnings`, and add their key fields to `metrics_snapshot`. Existing checks and the `acknowledgment_required = len(warnings) > 0` contract are unchanged (the new warnings simply participate).

**Surface coverage (why this catches fragility everywhere):** these three checks read the option result **off the source doc**, so they fire on a **backtest_run** source (results page, optimizer best run). A **preset** source has no `option_backtest` result, so for the **deploy** path the *existing* evidence-driven `option_oos_negative` remains the fragility catch — and after Fix-A it reads the honest exact-params number. Net: a fragile config is flagged at every surface, via the self-contained check where an option result exists and via the evidence check where it doesn't.

### 5.3 Fix-C — surface the verdict on the research surfaces

- **Backtest results page.** In `GET /backtest/runs/{id}` (`get_backtest_run`, research.py:342), after loading the doc, attach `doc["quality"] = evaluate_source_quality(doc)` (pure, no evidence needed — the new option-₹ checks are self-contained; spot-only runs get the existing in-sample checks). Compute-on-read ⇒ works **retroactively** on old runs, zero new endpoint, nothing stored.
- **Optimizer promotion.** At optimizer finalize (after `_save_best_as_backtest` returns `best_backtest_run_id`), load that run doc and compute `job["best_quality"] = evaluate_source_quality(best_run, evidence={"oos_return_pct": <survival.total_return_pct>, "n_trials": …})`, so the fragility check gets the OOS contrast. Stored on the job doc; surfaced in the Optimizer result panel.
- **Frontend.** One small reusable component `frontend/src/components/TrustScorecard.jsx` taking the `quality` object: an overall status chip (**green** when `warnings` empty, **amber** otherwise) + the warning list (`label` + `detail`), plus the standing "not walk-forward validated" caveat for option-₹ results. Render it in the backtest `ResultsView` (reads `result.quality`) and the Optimizer result panel (reads `job.best_quality`). The deploy wizard's existing warning UI is left as-is (it already renders `warnings`); reusing `TrustScorecard` there is **optional** (out of scope for this piece).

## 6. Off-by-default + impact (no degradation)

- **Backward-compatible verdict:** the Fix-B block runs only when `source_doc.option_backtest` is present; with `evidence=None` and no `option_backtest`, `evaluate_source_quality` returns exactly today's result (existing callers/tests unchanged). New `QualityThresholds` fields default to today's effective behavior (no `option_backtest` ⇒ no new warnings).
- **Optimizer:** Fix-A only adds an `option_backtest` result to the saved best in **option_rerank** mode; spot-mode and the ranking/promotion logic are untouched. `best_metrics.option_pnl_value` is unchanged.
- **Deploy path:** unchanged code; it simply benefits — the exact-params option run now exists (Fix-A) so `option_oos_negative` reads the honest number instead of "different params."
- **Bounded cost:** Fix-A = one extra option sim per optimizer job (lighter than the re-rank). Surfacing = one pure function call per run-detail GET.

## 7. Error handling

- Fix-A option sim wrapped in try/except → spot-only fallback (today's behavior) on any failure; never fails the job.
- Fix-B checks read every field defensively (`.get`, presence guards) → missing/partial `option_backtest` ⇒ that check is skipped, never raises.
- Fix-C: if `evaluate_source_quality(doc)` raises, omit `quality` from the response (frontend renders the scorecard only when `result.quality`/`job.best_quality` is present).

## 8. Testing & verification

- **Fix-B (TDD, host-importable pure module):** `tests/test_deployment_quality_option.py` with crafted `source_doc`s — (a) full-window-negative option portfolio ⇒ `option_full_window_negative` (and the "fragile" escalation when an OOS-positive signal is supplied); (b) negative ending equity / curve dipping ≤ floor ⇒ `ruin_floor_breach`; (c) low `paired/spot` ⇒ `coverage_attrition`; (d) clean positive option run ⇒ none of the three; (e) **spot-only doc + `evidence=None` ⇒ result byte-identical to before** (no new warnings). Plus a thresholds test (`ruin_floor`, `min_coverage_ratio` via `from_overrides`).
- **Contract corpus:** add an assertion that `get_backtest_run` attaches `quality` (research.py is in the corpus) — e.g. `"evaluate_source_quality"` appears in the API text on the backtest-read path. (`optimizer.py` is **not** in the corpus, so Fix-A is proven by running-stack only — the established pattern.)
- **Running stack:** rebuild backend; run an `option_rerank` optimizer job → confirm the saved best run now has `option_backtest.metrics != None` and `job.best_quality`; deploy that preset → the deploy gate now shows a clean exact-params option-₹ warning (not "different params"); open a known-fragile run (e.g. the SEB survivor) on the results page → `TrustScorecard` shows **amber** with `option_full_window_negative` (fragile) + (for the −206% run) `ruin_floor_breach` + `coverage_attrition`; a clean spot-only run → no new warnings. Frontend `npm run build` clean.

## 9. Out of scope / future

- **Sizing-sanity check** (per-trade premium %, cap-vs-risk) → adaptive **Plan 4** (edge-proportional sizing).
- **Option-aware walk-forward** as a results-page feature → not built; the non-WF status is an informational caveat only. (The WFO engine already exists separately and feeds deploy evidence.)
- **Hard blocking / refusing to promote** → explicitly not done (flag-only by decision §3).
- Reworking the deploy wizard's warning UI to the shared `TrustScorecard` → optional later.

## 10. Audit findings → resolutions
_(Optional adversarial multi-agent audit before the plan, on request — consistent with prior pieces.)_
