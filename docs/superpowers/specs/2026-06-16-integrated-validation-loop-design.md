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
1. **Fix-A** — the optimizer computes + stores the promoted config's **full-window paired-option result** on the saved best run, via `_run_paired_option_backtest(..., validate=False)` so it works for overlay survivors (§5.1).
2. **Fix-B** — three new option-₹ checks in `evaluate_source_quality`, read self-contained from `source_doc.option_backtest`, with a dedup vs the legacy evidence check (§5.2).
3. **Fix-C** — surface the verdict on the **backtest results page** and the **optimizer promotion** via a reusable `TrustScorecard`, reusing the same pure function (§5.3).
4. **Fix-D** — make the **deploy** evidence-gatherer read the promoted survivor's full-window number (not `rerank.ranked[0]`), so the deploy gate is honest (§5.4).

## 3. Decisions (locked with the user)

| Decision | Choice |
|---|---|
| Enforcement | **Flag everywhere, never block.** Compute + surface the verdict at every surface; the deploy "acknowledge to deploy" gate stays the one place that gates (unchanged). |
| Module | **No new module.** Extend `deployment_quality.py`; reuse `evaluate_source_quality`. A parallel `trust.py` would duplicate it. |
| Checks in scope | **Full-window fragility, ruin/equity-floor breach, coverage & sample.** **Sizing-sanity is OUT** (deferred to adaptive Plan 4 — edge-proportional sizing; the ruin check still catches the *symptom*). |
| Non-WF note | A **standing informational caveat** ("this option-₹ headline is not walk-forward validated"), not a pass/fail check. No option-WF engine is built here. |
| Fix-A seam | `_save_best_as_backtest` builds a `BacktestReq` and calls `_run_paired_option_backtest(req, trades, validate=False)` via a lazy import. The `validate=False` **bypasses the boundary overlay-validation** — the validator rejects the optimizer's `spot_exit`+`exit_controls.enabled` combo with a 400 (audit blocker), yet the promoted config is grid-derived and already valid. Preserves the survivor's **actual `exit_mode`** (do NOT force `option_levels` — that would change how the survivor was scored); full shape parity. |
| Deploy honesty | Add **Fix-D**: the deploy evidence-gatherer must read the **promoted survivor's** full-window number, not `rerank.ranked[0]` (§5.4). |
| Branch | `feat/integrated-validation-loop` off `feat/backtest-exit-controls`. |

## 4. Architecture — fix the data, extend the gate, surface everywhere

```
OPTIMIZER (run_optimization → finalize)
  _save_best_as_backtest(best_params, option_config={…,exit_controls:best,…})
     ├─ run_backtest (spot)                       (unchanged)
     └─ Fix-A: BacktestReq(auto_fetch=False) → _run_paired_option_backtest(req, trades, validate=False)
                 → doc["option_backtest"] = full-window option result  (conditional key; NO return-sig change)
  finalize: load best_run →
            job["best_option_pnl_value"] = best_run.option_backtest.portfolio.net_pnl_value  ──┐  (for Fix-D)
            job["best_quality"]          = evaluate_source_quality(best_run, evidence={oos_return_pct, n_trials})

deployment_quality.evaluate_source_quality(source_doc, evidence, thresholds)
  existing spot/WF/selection-bias/option-OOS checks               (option-OOS suppressed when option_backtest present)
  + Fix-B: when source_doc.option_backtest present →
        option_full_window_negative · ruin_floor_breach · coverage_attrition

deployments._gather_deployment_evidence
  + Fix-D: rerank-job branch reads job.best_option_pnl_value (promoted) ─◄─┘  instead of ranked[0] (base-config)

SURFACES (Fix-C, all reuse the pure verdict; none block)
  GET /backtest/runs/{id}  → attach quality = evaluate_source_quality(doc)  → <TrustScorecard/>
  Optimizer result panel   → job.best_quality                              → <TrustScorecard/>
  Deploy wizard            → already calls it (now honest, via Fix-A+D)     → existing ack-gate
```

## 5. Components

### 5.1 Fix-A — optimizer saves the promoted config's full-window option result

**File:** `backend/app/optimizer.py` — `_save_best_as_backtest` (def ~line 444; called ~line 1115 with `option_config={**option_cfg, "exit_controls": best_so_far["exit_controls"], "daily_caps": best_so_far["daily_caps"]}`).

Today the function runs `run_backtest` (spot) and stores `config.option_backtest = {**option_config, "enabled": True}` but **no option result**. Add: when `option_config` is present, compute the promoted config's full-window paired-option result and store it.

```python
# inside _save_best_as_backtest, after `res = await asyncio.to_thread(run_backtest, ...)`, before building `doc`:
option_result = None
if option_config:
    try:
        from app.runtime import _run_paired_option_backtest   # lazy: mirrors the existing lazy walk_forward import
        from app.schemas import BacktestReq, OptionBacktestReq
        req = BacktestReq(
            instrument=instrument, strategy_id=strategy.id, params=best_params,
            start_ts=payload.get("start_ts"), end_ts=payload.get("end_ts"),
            costs_enabled=costs_enabled, walkforward=False, pretrade_filters=pretrade,
            option_backtest=OptionBacktestReq(**{**option_config, "enabled": True, "auto_fetch": False}),  # actual exit_mode; NO fetch (parity)
        )
        option_result = await _run_paired_option_backtest(req, res["trades"], validate=False)
    except Exception as e:
        log.warning(f"save_best option backtest failed: {e}")  # caught -> spot-only fallback
```

**`validate=False` is load-bearing (audit BLOCKER).** Add a `validate: bool = True` parameter to `_run_paired_option_backtest` (runtime.py:411) that skips the `validate_exit_risk_config` block (runtime.py:577-585) when `False`; existing callers (research.py:188, :280) keep the default `True`. **Without this, Fix-A silently degrades to spot-only for every overlay survivor** — the optimizer's `option_cfg.exit_mode` defaults to `spot_exit` while the promoted `exit_controls.enabled=True`, and the validator rejects "enabled exit_controls under non-option_levels" (exit_controls.py:160-162) → `HTTPException(400)` (runtime.py:585), caught → no option result for exactly the configs Fix-A exists to fix. The promoted config is grid-derived (`exit_control_grid`) and already valid; the boundary validation is for user input, so bypassing it for this internally-trusted replay is correct. **Do NOT force `exit_mode='option_levels'`** to dodge the 400 — that changes the exit semantics vs how the survivor was scored (under `spot_exit` the overlay is inert; the replay must match).

Then in the `doc` — **conditional** so spot-mode runs are genuinely unchanged (audit HIGH):
```python
**({"option_backtest": option_result} if option_config else {}),   # full-window result of the PROMOTED config
```
(Keep the existing `config.option_backtest` config echo as-is. In spot mode `option_config is None` ⇒ no top-level `option_backtest` key is added — byte-identical to today.)

**Capture for Fix-D — read at finalize, NO return-signature change (re-audit BLOCKER).** Do **not** change `_save_best_as_backtest`'s return. It has **two** callers — optimizer.py:1115 **and `wfo.py:705`** (which assigns the result to a single `best_backtest_run_id` and persists it as a string id) — so a tuple return would corrupt the WFO job's run-id field (and the function's outer-except returns a bare `None`, so tuple-unpacking would `TypeError` and fail the job). Instead, the **finalize** block already loads `best_run` for `best_quality` (§5.3); read the net from it there: `finished["best_option_pnl_value"] = ((best_run.get("option_backtest") or {}).get("portfolio") or {}).get("net_pnl_value")` (None in spot/no-survivor → Fix-D falls back). No new callers touched; consumed by §5.4.

**`auto_fetch=False` is required for parity (re-audit MEDIUM).** Without it `OptionBacktestReq` defaults `auto_fetch=True`, and `_run_paired_option_backtest` (runtime.py:538-572) would upstox-fetch missing candles and pair MORE signals than the survivor was scored on — the scoring paths (`_option_rerank`, `_survival_eval_oos`) **never fetch** (they load only `db.options_1m`). The replay must be candle-load-only so the stored net reproduces the score.

**Shape parity (audit-confirmed):** `_run_paired_option_backtest(req, res["trades"], …)` is the exact pattern the manual backtest uses (research.py:188); it **uses the passed spot trades** (does not re-run spot) and returns `{metrics, portfolio, coverage, trades, skipped_trades, …}` — so the results-page UI, the evidence gatherer (`option_backtest.metrics != None`, deployments.py:104), and Fix-B all read it identically; a manual **full-window** option backtest of the promoted config reproduces this number (it is a full-window figure, distinct from the OOS survival score). The lazy import is cycle-free (runtime.py never imports optimizer; mirrors the function's existing lazy `walk_forward` import). Cost: **one extra independent option-contract + option-candle DB load** at job end (bounded by the best's trade span; it does NOT reuse the re-rank's loaded candles). `best_metrics.option_pnl_value` (the re-rank ranking value) is left untouched; `option_backtest.portfolio.net_pnl_value` is the **authoritative** full-window with-overlay number the checks + evidence consume.

### 5.2 Fix-B — three option-₹ checks in `evaluate_source_quality`

**File:** `backend/app/deployment_quality.py`. Add a block that runs **only when `source_doc.get("option_backtest")` is present** (so spot-only sources and `evidence=None` callers are byte-identical to today). Read:
`om = source_doc["option_backtest"]; port = om.get("portfolio") or {}; m = om.get("metrics") or {}; cov = om.get("coverage") or {}`.

New `QualityThresholds` fields: `ruin_floor: float = 0.0`, `min_coverage_ratio: float = 0.70`.

1. **`option_full_window_negative`** — **gate on `cov.get("paired_trade_count", 0) > 0` first**, then warn when `port.get("net_pnl_value")` is not None and **`< 0` (strict)**. A zero-pair run nets exactly `0.0` and is a *coverage* problem, not a loss — it routes to check 3, not here (audit HIGH: `≤ 0` would mislabel every empty option run as "fragile/negative"). **Escalate** the label to *"fragile — positive out-of-sample but negative over the full window"* only when **`(evidence or {}).get("oos_return_pct")`** is not None and `> 0` (the authoritative OOS signal, supplied by the optimizer surface — §5.3). **`evidence` itself can be `None`** (the results-page call passes none) — use `(evidence or {})`, NOT `evidence.get(...)`, or `None.get` raises `AttributeError`, which Fix-C's try/except swallows → the **entire scorecard silently disappears** (re-audit HIGH). **Else** a weak fallback using the spot WF — **`((wf or {}).get("is_vs_oos") or {}).get("divergence_warning")` falsey**. Guard `wf` for `None`: an `option_backtest` source can carry `walkforward: None` (optimizer.py:485 when `not run_walkforward or len(df)<200`), and a bare `wf.is_vs_oos` would `AttributeError` → swallowed by Fix-C → scorecard dropped (re-audit MEDIUM). The real key is `divergence_warning`, NOT `divergence_flag` (a local var name). Value: `{net_pnl_value, total_return_pct, oos_signal}`.
2. **`ruin_floor_breach`** — **empty-curve-safe (audit HIGH):** `eqs = [c["equity_value"] for c in (port.get("curve") or []) if c.get("equity_value") is not None]; min_equity = min(eqs) if eqs else None`. Warn when `(min_equity is not None and min_equity ≤ th.ruin_floor)` **or** `(port.get("ending_equity") is not None and ending_equity < 0)` **or** `abs(port.get("max_drawdown_pct") or 0) ≥ 100`. The `min()` MUST be guarded — a zero-pair run returns `curve: []`, and unguarded `min([])` raises `ValueError`, which Fix-C's try/except would swallow and silently drop the *entire* scorecard. Detail: *"Equity reached ₹{min_equity} (≤ floor ₹{ruin_floor}); the account would be wiped and the backtest keeps trading past ruin."* Value: `{min_equity, ending_equity, max_drawdown_pct, ruin_floor}`.
3. **`coverage_attrition`** — **measure DATA attrition, not intentional cap-skips (audit MEDIUM):** `spot = cov.get("spot_trade_count") or 0`, `paired = cov.get("paired_trade_count") or 0`, `skipped = cov.get("skipped_by_cap") or 0` (the `or 0` defaults keep §7's "never raises" true for a crafted/partial `option_backtest` whose `coverage` is absent or None-valued — production `_coverage()` always inits to 0); `addressable = spot - skipped`; warn when `addressable > 0` and `paired/addressable < th.min_coverage_ratio` — so a config with deliberately tight daily caps is NOT flagged as a data gap. Detail: *"Only {paired}/{addressable} non-capped signals ({pct}%) paired with option data — {missing_contract + missing_entry_candle} missing data ({skipped} additionally skipped by daily caps)."* Value: `{paired, spot, addressable, ratio, skipped_by_cap, missing_contract, missing_entry_candle}`.

All three use `severity: SEVERITY_WARNING`, append to `warnings`, and add their key fields to `metrics_snapshot`. The `acknowledgment_required = len(warnings) > 0` contract is unchanged.

**Dedup (audit HIGH):** when `source_doc.get("option_backtest")` is present, check 1 covers option-₹ fragility, so **suppress the evidence-driven `option_oos_negative` / `missing_option_oos`** (deployment_quality.py:316-348). Otherwise a `backtest_run` deploy source double-warns, and the optimizer-promotion call (which passes evidence WITHOUT `wfo`/`option_evidence`) would emit a spurious "no option-rupee validation" next to a fresh option result on the same doc. The evidence-driven option checks stay for **preset** sources (no self-contained option result).

**`oos_return_pct` is a NEW optional evidence key**, read only by check 1 via `(evidence or {}).get("oos_return_pct")`. No existing code reads it; the **deploy** caller does not supply it (deploy-surface escalation uses the spot-WF fallback), only the **optimizer-finalize** caller does (§5.3).

**Thresholds echo (resolves the byte-identical tension — audit HIGH):** add `ruin_floor` + `min_coverage_ratio` to the returned `thresholds` dict (deployment_quality.py:369-377). §6's "byte-identical" claim is therefore scoped to **warnings + `metrics_snapshot`** (no test asserts exact `thresholds`-dict equality — audit-confirmed); the `thresholds` echo simply gains two keys. Exposing the two knobs as `deployment_quality_route` query params (via `from_overrides`) is **optional** — the route surfaces only 4 of the 7 existing knobs today, so this is a choice, not pattern-completion.

**Surface coverage (fragility caught everywhere):** checks 1-3 read the option result **off the source doc**, firing on a **backtest_run** source (results page, optimizer best run). A **preset** source has no `option_backtest` result, so the **deploy** path relies on the evidence-driven `option_oos_negative` — and after Fix-A + Fix-D it reads the honest **promoted-survivor** number (§5.4). Net: fragility flagged at every surface.

### 5.3 Fix-C — surface the verdict on the research surfaces

- **Backtest results page.** In `GET /backtest/runs/{run_id}` (`get_backtest_run`, research.py:~360-366 — *not* :342, which is inside the POST create handler), after `doc = await db.backtest_runs.find_one(...)` and **before** `return serialize_doc(doc)`, attach `doc["quality"] = evaluate_source_quality(doc)` (pure, no evidence — the option-₹ checks are self-contained; spot-only runs get the existing in-sample checks; `serialize_doc` keeps the key and `computed_at` is already an ISO string). Compute-on-read ⇒ **retroactive**, zero new endpoint, nothing stored. (`deployment_quality` is a stdlib-only leaf module — no import cycle.)
- **Optimizer promotion.** At finalize, compute `best_quality` **only when** `best_backtest_run_id` is non-None **and** the loaded run doc is non-None — else omit it (audit HIGH: `best_backtest_run_id` is `None` for `done_no_survivor` jobs, where `best_so_far["params"] == {}` skips the save at optimizer.py:~1112, and on any `_save_best_as_backtest` exception):
  ```python
  best_run = best_backtest_run_id and await db.backtest_runs.find_one({"id": best_backtest_run_id}, {"_id": 0})
  if best_run:
      finished["best_option_pnl_value"] = ((best_run.get("option_backtest") or {}).get("portfolio") or {}).get("net_pnl_value")  # Fix-D
      oos = (best_so_far.get("metrics") or {}).get("survival", {}).get("total_return_pct")  # survival mode only; None otherwise
      finished["best_quality"] = evaluate_source_quality(best_run, evidence={"oos_return_pct": oos, "n_trials": n_trials})
  ```
  The exact OOS source is `best_so_far["metrics"]["survival"]["total_return_pct"]` (promoted at optimizer.py:~1048; **None** for non-survival rerank jobs → the fragility escalation simply doesn't fire). The §5.2 dedup suppresses the legacy `missing_option_oos` here (the evidence dict carries no `wfo`/`option_evidence`), so there's no contradictory warning; `n_trials` keeps the legitimate `selection_bias` check working.
- **Frontend.** One small reusable component `frontend/src/components/TrustScorecard.jsx` taking the `quality` object: an overall status chip (**green** when `warnings` empty, **amber** otherwise) + the warning list (`label` + `detail`), plus the standing "not walk-forward validated" caveat for option-₹ results. Render it in the backtest results view (`ResultsView`, inside `frontend/src/pages/BacktestLab.jsx:~1623`, which receives the full `result` carrying `result.quality`) and the Optimizer result panel (`frontend/src/pages/Optimizer.jsx:~1170`, which receives the full `job` carrying `job.best_quality`). The deploy wizard's existing warning UI is left as-is; reusing `TrustScorecard` there is **optional** (out of scope).

### 5.4 Fix-D — the deploy gate must read the PROMOTED survivor's number (audit BLOCKER)

**File:** `backend/app/routers/deployments.py` — `_gather_deployment_evidence` (~lines 88-100).

Even after Fix-A, the deploy gate stays blind. When deploying the promoted survivor, `_gather_deployment_evidence` finds the matching re-rank job and reads `rerank.ranked[0].option_pnl_value` (deployments.py:89,94) — the **base-config, highest-option-P&L candidate, NOT the promoted survivor** (the exit-control search mutates `r["survival"]`/`r["chosen_exit_controls"]` but never `r["option_pnl_value"]`, optimizer.py:1015-1031; `ranked` is sorted base-config). It then `break`s on `params_match` (deployments.py:99) **before** reaching the backtest_run branch (102-120) where Fix-A's honest number lives. So `option_oos_negative` reads a *positive base-config* number and the fragile survivor passes the deploy gate clean.

**Fix:** in the re-rank-job branch, when the job's `best_params == params`, source the option net from the **promoted survivor's** full-window result (the `best_option_pnl_value` Fix-A persists on the job, §5.1), not `ranked[0]`. **Critically, add `best_option_pnl_value` to that query's projection (deployments.py:86) — it is an INCLUSION projection (`{id:1, finished_at:1, best_params:1, "rerank.ranked": {$slice:1}}`), so without listing the field it is projected away and `job.get("best_option_pnl_value")` is ALWAYS None → Fix-D stays inert and blocker #3 remains open (re-audit blocker).**
```python
# query (deployments.py:~83-87): add "best_option_pnl_value": 1 to the projection
# inside the matching re-rank job branch (deployments.py:~88-98):
net = job.get("best_option_pnl_value")          # Fix-A/§5.1: promoted, with-overlay, full-window
if net is None:
    net = top.get("option_pnl_value")           # legacy fallback (base-config ranked[0])
option_evidence = {"kind": "rerank", "id": job.get("id"), "at": job.get("finished_at"),
                   "net_pnl_value": net, "win_rate": top.get("option_win_rate"),
                   "paired_trade_count": top.get("paired_trade_count"), "params_match": match}
```
**Preserve the loop control (re-audit LOW):** this `option_evidence = {…}` slots into the *existing* guarded assignment `if option_evidence is None or (match and not option_evidence.get("params_match")):` and the subsequent `if option_evidence.get("params_match"): break` (deployments.py:91,99) — only the `net_pnl_value` *source* changes; the prefer-exact-match-then-break semantics are unchanged. With it, `option_oos_negative` evaluates the actual promoted-survivor full-window net → the fragile survivor is correctly flagged at the deploy gate (acknowledge-to-deploy), closing the last slip-through. (`params_match` is dict `==` on BSON-round-tripped optimizer floats/ints — stable for the deploy-the-survivor path; the residual edge is NaN/cross-type re-serialization only, not in scope.)

## 6. Off-by-default + impact (no degradation)

- **Backward-compatible verdict:** the Fix-B block runs only when `source_doc.get("option_backtest")` is present; with `evidence=None` and no `option_backtest`, `evaluate_source_quality` returns today's **warnings + `metrics_snapshot`** unchanged. The returned `thresholds` dict gains two keys (`ruin_floor`, `min_coverage_ratio`) — no test asserts exact `thresholds` equality (audit-confirmed). New `QualityThresholds` fields are no-ops for non-option sources.
- **Optimizer:** Fix-A adds an `option_backtest` result to the saved best **only in option_rerank mode** (the doc key is **conditional** — spot-mode `option_config is None` ⇒ no key added, byte-identical). Ranking/promotion logic and `best_metrics.option_pnl_value` are untouched. `best_quality` + `best_option_pnl_value` are new additive job fields.
- **Deploy path:** improves via Fix-A+D — `option_oos_negative` now reads the honest **promoted-survivor** number; on a `backtest_run` deploy source, check 1 fires and the legacy `option_oos_negative` is suppressed (dedup, §5.2) ⇒ one option-₹ warning, not two.
- **Bounded cost:** Fix-A = one extra independent option-contract + option-candle DB load per option_rerank job (bounded by the best's trade span). Surfacing = one pure function call per run-detail GET (a `min()` over the equity curve; doc serialization already dominates that GET).

## 7. Error handling

- **Fix-A:** option sim wrapped in try/except → spot-only fallback on any failure; never fails the job. With `validate=False` the overlay-coupling 400 no longer fires; the catch covers genuine sim/data errors.
- **Fix-B:** checks read every field defensively. The ruin check's `min()` is **explicitly empty-curve-guarded** (`min(eqs) if eqs else None`) — a zero-pair `curve: []` must not raise (an unguarded `ValueError` would be swallowed by Fix-C and drop the whole scorecard). Missing/partial `option_backtest` ⇒ a check is skipped, never raises.
- **Fix-C:** `best_quality` is computed only when `best_backtest_run_id` and the loaded run doc are both non-None (else omitted) — `done_no_survivor`/save-failure jobs carry no `best_quality`. On the results page, if `evaluate_source_quality(doc)` raises, omit `quality`. The frontend renders the scorecard only when `result.quality`/`job.best_quality` is present.
- **Fix-D:** `best_option_pnl_value` may be absent (older jobs / save failure) ⇒ the gatherer falls back to the legacy `ranked[0]` number (no crash; pre-fix behavior for those jobs).

## 8. Testing & verification

- **Fix-B (TDD, host-importable pure module):** `tests/test_deployment_quality_option.py` with crafted `source_doc`s — (a) full-window-negative option portfolio + paired>0 ⇒ `option_full_window_negative` (and the "fragile" escalation when `evidence={"oos_return_pct": +x}`); (b) negative ending equity / curve dipping ≤ floor ⇒ `ruin_floor_breach`; **(b2) zero-pair run (`portfolio.curve == []`, `net_pnl_value == 0.0`) ⇒ NO crash, NO `option_full_window_negative` (routes to coverage), ruin `min()` guarded**; (c) low DATA coverage ⇒ `coverage_attrition`, **and a high-`skipped_by_cap` config ⇒ NO `coverage_attrition`** (intentional caps excluded); (d) clean positive option run ⇒ none of the three; (e) **spot-only doc + `evidence=None` ⇒ warnings + `metrics_snapshot` byte-identical to before**; **(f) dedup: a source with `option_backtest` AND `evidence` ⇒ `option_oos_negative`/`missing_option_oos` suppressed (only `option_full_window_negative` for option-₹)**; **(g) option doc + `evidence=None` (results-page call) ⇒ NO crash, escalation falls back to the spot-WF signal (re-audit HIGH)**. Plus a thresholds test (`ruin_floor`, `min_coverage_ratio` via `from_overrides` + present in the returned `thresholds` dict).
- **Contract corpus:** assert `get_backtest_run` attaches `quality` (research.py is in the corpus) — `"evaluate_source_quality"` appears on the backtest-read path. (`optimizer.py` is **not** in the corpus, so Fix-A/D are proven by running-stack only — confirmed, the established pattern.)
- **Running stack:** rebuild backend; run an `option_rerank` + `search_exit_controls` optimizer job that promotes an **overlay survivor** (`exit_controls.enabled`, default `spot_exit`) → confirm the saved best now has `option_backtest.metrics != None` (proves `validate=False` — without it, the run is spot-only) and `job.best_quality` + `job.best_option_pnl_value`; deploy that preset → the deploy gate flags the honest **promoted-survivor** option-₹ net, not a positive base-config number (proves Fix-D); open a known-fragile run (the SEB survivor) on the results page → `TrustScorecard` shows **amber** with `option_full_window_negative` (fragile) + (for the −206% run) `ruin_floor_breach` + `coverage_attrition`; a `done_no_survivor` job → no `best_quality` (no crash); a clean spot-only run → no new warnings. Frontend `npm run build` clean.

## 9. Out of scope / future

- **Sizing-sanity check** (per-trade premium %, cap-vs-risk) → adaptive **Plan 4** (edge-proportional sizing).
- **Option-aware walk-forward** as a results-page feature → not built; the non-WF status is an informational caveat only. (The WFO engine already exists separately and feeds deploy evidence.)
- **Hard blocking / refusing to promote** → explicitly not done (flag-only by decision §3).
- Reworking the deploy wizard's warning UI to the shared `TrustScorecard` → optional later.

## 10. Audit findings → resolutions

Adversarial multi-agent audit (5 lenses): **30 findings (3 blocker, 8 high, 13 medium, 6 low) + 42 confirmations**. The audit **confirmed** the field shapes (portfolio/coverage/metrics keys all exist), the lazy-import is cycle-free, `_run_paired_option_backtest` uses the passed trades, the frontend hosts exist (`ResultsView` in BacktestLab.jsx:~1623, `Optimizer.jsx:~1170`), `serialize_doc` keeps `quality`, the contract-corpus claims, and that no existing test pins exact warnings/thresholds shapes. All findings folded in:

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 1,2 | **Blocker** | Fix-A's `_run_paired_option_backtest` **400s** for overlay survivors (`spot_exit`+`exit_controls.enabled` fails `validate_exit_risk_config`) → silent spot-only fallback → Fix-A no-ops on its primary target. | **§5.1 rewritten:** add `validate=False` to `_run_paired_option_backtest`; preserve the survivor's actual `exit_mode` (do NOT force option_levels). "cannot 400" claim removed. |
| 3 | **Blocker** | Even after Fix-A, the deploy gate reads `rerank.ranked[0]` (base-config, not the promoted survivor) and short-circuits before Fix-A's saved run → fragile survivor passes deploy clean. | **New §5.4 (Fix-D):** optimizer persists `best_option_pnl_value`; `_gather_deployment_evidence` reads it instead of `ranked[0]`. |
| 4,5 | High | "byte-identical" contradicts adding `ruin_floor`/`min_coverage_ratio` to the returned `thresholds` dict (runs unconditionally). | **§5.2 + §6:** add the two keys to the `thresholds` echo; scope "byte-identical" to **warnings + `metrics_snapshot`** (no test asserts exact thresholds). |
| 6 | High | `oos_return_pct` only exists in survival mode; spec implied the optimizer always supplies it. | **§5.3:** exact source `best_so_far["metrics"]["survival"]["total_return_pct"]`; None for non-survival → escalation doesn't fire. |
| 7 | High | No null-guard on `best_backtest_run_id` (None for `done_no_survivor`/save-failure) → loading id=None / crash. | **§5.3 + §7:** compute `best_quality` only when run_id and loaded doc are both non-None; else omit. |
| 8 | High | Optimizer-promotion `evidence` lacks `wfo`/`option_evidence` → spurious `missing_option_oos` next to a fresh option result. | **§5.2 dedup:** suppress `option_oos_negative`/`missing_option_oos` when `source_doc.option_backtest` present. |
| 9 | High | Fix-A writes `option_backtest: None` unconditionally → spot-mode runs gain a new key (not byte-identical). | **§5.1:** make the doc key **conditional** (`if option_config`). §6 updated. |
| 10 | High | Ruin `min()` over empty `curve` (zero-pair run) raises `ValueError` → Fix-C swallows it → whole scorecard dropped. | **§5.2 #2 + §7:** empty-curve guard (`min(eqs) if eqs else None`). |
| 11 | High | `option_full_window_negative` fires at `net == 0` (zero-pair run) → mislabels an empty run "fragile". | **§5.2 #1:** gate on `paired_trade_count > 0` and use strict `< 0`; zero-pair routes to coverage. |
| — | Med | `coverage_attrition` counts intentional cap-skips as data attrition. | **§5.2 #3:** measure over `addressable = spot − skipped_by_cap`; caps excluded. |
| — | Med | `divergence_flag` is a local var, not a doc key. | **§5.2 #1:** use the real key `is_vs_oos.get("divergence_warning")`; prefer `oos_return_pct` as the authoritative signal. |
| — | Med | `oos_return_pct` is a brand-new evidence key no caller supplies on deploy. | **§5.2:** documented as a new optional key; deploy uses the spot-WF fallback. |
| — | Med | On a `backtest_run` deploy source, check 1 AND legacy `option_oos_negative` both fire (double warning). | Resolved by the §5.2 dedup (#8). |
| — | Med | `thresholds` echo + `deployment_quality_route` query params miss the new knobs. | **§5.2:** add both to the echo dict and `from_overrides`. |
| — | Med×3 / Low×2 | Wrong line refs: `get_backtest_run` is research.py:~360 (not :342); `_save_best_as_backtest` def ~444 / call ~1115; `ResultsView` is a component **in** BacktestLab.jsx:~1623; Optimizer panel `Optimizer.jsx:~1170`. | **§5.1/§5.3/§8:** all references corrected. |
| — | Low | Cost note understated (independent DB load, not cached subset). | **§5.1/§6:** reworded to "one extra independent option-contract + option-candle DB load". |
| — | Low | Lazy-import rationale overstated (no real cycle today). | **§5.1:** softened to "mirrors the existing lazy-import pattern". |
| — | Low | `OptionBacktestReq(**option_config)` "rejects" extras — actually Pydantic v2 ignores them. | Non-issue (confirmed); the real risk was the 400 (#1). |
| — | Low | `params_match` dict `==` on floats — NaN/cross-type edge. | **§5.4:** noted (stable for the deploy-the-survivor path; out of scope). |

### Re-audit (verification pass)
A second adversarial pass verifying the above fixes: **7 findings (1 blocker, 2 high, 2 med, 2 low) + 17 confirmations** that the core fixes are sound (`validate=False` is sufficient + faithful, dedup doesn't break preset deploy, null-guard/conditional-key/`$set`-merge/frontend-props all hold). The 7 were implementation-detail gaps **inside** the fixes, all folded:

| Sev | Finding | Resolution |
|---|---|---|
| **Blocker** | Fix-D reads `job.best_option_pnl_value` but the rerank-job query's **inclusion projection** omits it → always None → Fix-D inert (blocker #3 stays open). | **§5.4:** add `"best_option_pnl_value": 1` to the projection (deployments.py:86). |
| High | Check-1 escalation `evidence.get(...)` crashes when `evidence is None` (results-page call) → Fix-C swallows → whole scorecard dropped. | **§5.2:** `(evidence or {}).get("oos_return_pct")`. |
| High | `best_option_pnl_value` is local to `_save_best_as_backtest` → can't reach finalize. | **§5.1/§5.3:** read it at finalize from the already-loaded `best_run.option_backtest.portfolio.net_pnl_value` (no return-sig change — see completion-pass blocker). |
| Med | Fix-A replay leaves `auto_fetch=True` → may fetch candles the scoring never had → net ≠ scored (parity). | **§5.1:** `auto_fetch=False` in the BacktestReq. |
| Med | "established tunable-knob pattern" overstated (route exposes 4 of 7). | **§5.2:** softened — query-param exposure is optional. |
| Low×2 | "manual re-run reproduces the number" conflates full-window vs OOS; raise line ref `:584`→`:585`. | **§5.1:** wording + line ref corrected. |

### Completion pass (the 3 lenses that had failed on transient errors)
Re-run against the twice-hardened spec: **7 findings (2 blocker, 1 high, 2 med, 2 low) + 30 confirmations** (validate=False/auto_fetch/conditional-key/Fix-D-projection/dedup/null-guards/field-shapes/line-refs/frontend-hosts all re-confirmed consistent). The 7 (all folded):

| Sev | Finding | Resolution |
|---|---|---|
| **Blocker×2 + High** | The tuple-return (above) is unsafe: `_save_best_as_backtest` has a **second caller `wfo.py:705`** (would store `(id, None)` as the run-id) and its outer-except returns bare `None` (tuple-unpack → `TypeError` → job fails). | **§5.1/§5.3/§4:** dropped the tuple-return entirely; read `best_option_pnl_value` at finalize from `best_run`. No caller touched. |
| Med | Check-1 spot-WF fallback `wf.is_vs_oos.get(...)` raises when `wf is None` (option doc with `walkforward: None`). | **§5.2:** `((wf or {}).get("is_vs_oos") or {}).get("divergence_warning")`. |
| Med | Checks 1/3 read `cov` counts without defaults → `None` arithmetic on a coverage-less crafted/partial doc raises. | **§5.2:** `cov.get(...) or 0` defaults. |
| Low×2 | Fix-D snippet dropped the loop's `if option_evidence is None or (match and not …)` guard + `break`. | **§5.4:** noted — the net-source slots into the existing guarded assignment; semantics unchanged. |

**Net:** all blockers across all three passes are closed (Fix-A `validate=False` + `auto_fetch=False` + conditional key + finalize-read net; Fix-D + its projection + preserved loop guard; the zero-pair / None-evidence / None-`wf` / None-`cov` guards). The gate stays flag-only; no engine/scoring change. No caller signatures change.
