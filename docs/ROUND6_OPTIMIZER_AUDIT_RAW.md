# Round-6 optimizer/backtest round-trip audit — RAW agent findings (UNVERIFIED)

Workflow `wf_2f23df6d-0a6`. **3 of 5 audit agents completed; 5 of 8 total agents
died on the account spend limit** — including ALL verifiers and the
`apply-preset-roundtrip` + `result-persistence-display` audits entirely.

So every finding below is an UNVERIFIED agent claim. Verify against source
before acting or repeating to the user.

Dimensions that produced output: option-rerank-stage2, job-lifecycle,
objective-metric-integrity.
Dimensions LOST: apply-preset-roundtrip, result-persistence-display.
(The orchestrator audited apply-preset-roundtrip by hand instead — findings
AA-DD in docs/BACKTEST_AUDIT_2026-07-30.md.)

Findings: 31 kept, 0 refuted.

## [1] HIGH — Stage-2 re-rank re-runs the spot backtest WITHOUT the job's entry window, so it ranks a different trade set than Stage 1 and the survival gate

- dim: `option-rerank-stage2`
- site: `backend/app/optimizer.py:1037`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
`_option_rerank` step 1 (optimizer.py:1032-1041):
```python
    # 1. Spot backtest each candidate (fast post Slice-1) to get its trades.
    cand_trades: List[List[Dict[str, Any]]] = []
    for cand in candidates:
        merged = strategy.merged_params(cand["params"])
        enr = get_enriched(merged)
        res = await asyncio.to_thread(
            run_backtest, enr, strategy, merged,
            instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade,
        )
```
No `trade_window_start`/`trade_window_end`. The signature (optimizer.py:1006-1010) does not even accept them, and the call site (optimizer.py:1670-1674) passes none. Every OTHER evaluation path threads them: trials via `_evaluate` (optimizer.py:1340-1341), the parallel pool (optimizer.py:1538), and the survival gate (optimizer.py:880-884):
```python
        _tw = ({"trade_window_start": trade_window_start, "trade_window_end": trade_window_end}
               if trade_window_start and trade_window_end else {})
        res = await asyncio.to_thread(
            run_backtest, test_df, strategy, merged_params,
            instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade, **_tw)
```
The default is not benign: `OptimizerStartReq` (schemas.py:224-225) ships `trade_window_start="09:25"`, `trade_window_end="14:50"`, and Optimizer.jsx:518-519 always posts them, while `run_backtest`'s own default is `TRADE_WINDOW_END = "15:00"` (backtest.py:16-20, 79-80). `trade_window_end` gates BOTH entries and the forced close (backtest.py:158 `if exit_price is None and ist >= trade_window_end:` and backtest.py:178 `if not _in_window(ist, trade_window_start, trade_window_end):`).
```

**Failure scenario:**
Default job: Confluence, `evaluation_mode="option_rerank"`, window 09:25-14:50. Stage 1 scores each trial with entries blocked from 14:50 and any open trade force-closed at 14:50. Stage 2 re-runs the same params to 15:00: it adds every 14:50-15:00 entry AND lets every trade still open at 14:50 keep running (so those trades get a different exit_ts, hence a different option exit candle and a different premium). The option rupee P&L that decides the winner is therefore computed on a trade set the search never scored, and `_survival_eval_oos` then judges that same finalist on the 14:50 set — so a candidate can be ranked #1 on rupees earned in a window live can never trade, and "Survived" is certified against a different trade list than the ranking. `best_metrics` (optimizer.py:1741-1748) literally merges the two: `spot_metrics.trade_count` from the 14:50 run next to `paired_trade_count` from the 15:00 run, so the card can show more paired option trades than spot trades.

**Suggested fix:**
Add `trade_window_start`/`trade_window_end` to `_option_rerank`'s signature, thread them from the call site at optimizer.py:1670-1674, and build the same `_tw` dict `_survival_eval_oos` uses before the `run_backtest` at optimizer.py:1037. Add a regression test asserting `_option_rerank`'s spot leg produces the identical trade list `_evaluate` does for the same params + window.

## [2] HIGH — The promoted/saved backtest run is computed on 09:25-15:00 while the job optimized 09:25-14:50, and the saved config never records the window

- dim: `option-rerank-stage2`
- site: `backend/app/optimizer.py:698`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
`_save_best_as_backtest` (optimizer.py:689-702) takes no window arguments and calls:
```python
        res = await asyncio.to_thread(run_backtest, df_enriched, strategy, merged, instrument=instrument, costs_enabled=costs_enabled, pretrade_filters=pretrade)
        ...
            wf = await asyncio.to_thread(walk_forward, df_enriched, strategy, merged, instrument=instrument, costs_enabled=costs_enabled, pretrade_filters=pretrade)
```
Both default to 15:00 (backtest.py:79-80). `res["trades"]` is then what the option replay pairs (optimizer.py:723 `option_result = await _run_paired_option_backtest(_opt_req, res["trades"], validate=False)`), and `finished["best_option_pnl_value"]` is read straight off that run (optimizer.py:1890). The persisted `config` block (optimizer.py:730-744) contains `instrument/mode/params/costs_enabled/start_ts/end_ts/pretrade_filters/...` and NO `trade_window_start`/`trade_window_end`, so BacktestLab.jsx:787-788 rehydrates it as `r.config?.trade_window_start || "09:25"` / `|| "15:00"`. `payload` (which carries the real window) is already an argument to `_save_best_as_backtest`, so the value is available and simply unused. tests/test_capital_and_live_entry_parity.py:19-20 states the intent explicitly: "The optimizer already defaults trade_window_end to 14:50 for exactly this reason".
```

**Failure scenario:**
A survivor passes the gate on 09:25-14:50 folds. The job then saves `best_backtest_run_id` computed on 09:25-15:00 — different trades, different exits, different equity curve, a different walk-forward decay, and a different option rupee net. `finished["best_metrics"]` (the 14:50 spot metrics) and the saved run's `metrics` (15:00) disagree for the SAME params, and `evaluate_source_quality(best_run, ...)` at optimizer.py:1898 grades the un-windowed artifact. Opening that run in the Backtest Lab shows the window as 15:00, so re-running reproduces the wrong number and the user has no way to discover the optimizer used 14:50.

**Suggested fix:**
Pass `trade_window_start`/`trade_window_end` into `_save_best_as_backtest` (they are already in `payload`), forward them to both `run_backtest` and `walk_forward`, and persist them in the saved `config` dict so BacktestLab rehydrates the real window instead of falling back to 15:00.

## [3] HIGH — After the re-rank promotes a new winner, the persisted `best_so_far` is never updated — the UI shows the Stage-1 SPOT winner's params while the preset saves the Stage-2 winner's

- dim: `option-rerank-stage2`
- site: `backend/app/optimizer.py:1737`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
Stage 2 replaces only the LOCAL variable (optimizer.py:1735-1750):
```python
                if survivors:
                    best = survivors[0]
                    best_so_far = {
                        "value": (best["survival"].get("calmar") if survival.objective == "calmar"
                                  else best["option_pnl_value"]),
                        "params": best["params"],
```
The final write (optimizer.py:1853-1883, flushed at optimizer.py:1902 `await _update_job(job_id, finished)`) sets `best_params`, `best_value`, `best_metrics` — there is no `best_so_far` key, and `_flush_trial_log` (the only writer of `best_so_far`, optimizer.py:646-650) is called only from inside the trial loops (optimizer.py:1423, 1471, 1518, 1573). So the stored `best_so_far` is frozen at the last Stage-1 flush. The UI reads exactly that field: Optimizer.jsx:1426 `const bsf = job.best_so_far || {};`, renders `bsf.params` at 1594, `bsf.metrics.trade_count/win_rate/profit_factor/total_pnl_pts/max_dd_pts/sharpe` at 1604-1609, and uses `bsf.value` as `spotObjective` (1447). `job.best_params` is never rendered for a non-WFO job (Optimizer.jsx:1437 uses it only when `isWfo`), while the server-side apply uses it: research.py:707 `best_params = job.get("best_params") or (job.get("best_so_far") or {}).get("params")`.
```

**Failure scenario:**
Whenever Stage 2 changes the ranking (its entire purpose — CHANGELOG notes "spot-profitable params can be net-rupee LOSERS on options"), the "Best so far" card shows config A's params and spot metrics next to the headline option rupee of config B (`optionPnl` at Optimizer.jsx:1444 comes from `job.best_option_pnl_value` / `best_metrics`). "Save as Preset" then saves config B. The user reads A, deploys B, and no screen ever shows B's parameters.

**Suggested fix:**
Include the reconciled `best_so_far` in the `finished` patch at optimizer.py:1853 (same shape `_flush_trial_log` writes), or render `job.best_params` / `job.best_metrics` in the Best-so-far card once the job is finished. Add a test asserting the persisted `best_so_far.params == best_params` after a re-rank that promotes a different candidate.

## [4] HIGH — A single raising grid combo still kills the whole job — O14's error record poisons the Top-N sort

- dim: `job-lifecycle`
- site: `backend/app/optimizer.py:1450`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
The grid branch's O14 guard records a failed combo with a null score:
```python
                except Exception as exc:
                    log.warning("grid trial %d raised (%s) — disqualified, continuing",
                                completed, exc)
                    trial_history.append({"params": params, "metrics": None,
                                          "objective_value": None, "error": str(exc)[:200]})
                    completed += 1
                    continue
```
(optimizer.py:1446-1452). The analyze stage then sorts that same list unconditionally:
```python
        sorted_trials = sorted(trial_history, key=lambda t: t["objective_value"], reverse=True)
```
(optimizer.py:1626). Verified empirically against the exact expression: `sorted([{...'objective_value':1.23},{...'objective_value':None}], key=lambda t: t['objective_value'], reverse=True)` -> `TypeError '<' not supported between instances of 'float' and 'NoneType'`. The TypeError escapes to the outer handler at optimizer.py:1904-1906, which writes `{"status": "failed", "error": str(e)}`. `_compact_trial` (optimizer.py:631-637) preserves `objective_value: None` into the persisted `trial_log`, so `resume_optimization` rehydrates the poisoned record (optimizer.py:1364) and the resumed job re-crashes at the same sort. The only test covering O14 (tests/test_optimizer_robustness_contract.py:17-23) just greps the source for the strings `"disqualified, continuing"` and `'"metrics": None'` — it never exercises the sort, so the regression is invisible to the suite.
```

**Failure scenario:**
Grid-method optimization, 200 combos. Combo 137 raises (e.g. a param combination that makes an indicator window exceed the frame, or a strategy `evaluate()` KeyError). The guard catches it and the loop finishes all 200. At the analyze stage `sorted()` raises TypeError; the job flips to `status: "failed"` with error "'<' not supported between instances of 'float' and 'NoneType'". All 199 good trials, the best params, the heatmap, robustness and the saved best backtest are discarded — the user sees a red FAILED badge with a cryptic type error. Clicking Resume (allowed for `failed`) reloads the same trial_log and fails identically, forever. This is exactly the "resume then deterministically re-hits the same combo forever" failure the O14 comment at optimizer.py:1440-1442 claims to have fixed.

**Suggested fix:**
Make the sort total-order safe: `sorted_trials = sorted((t for t in trial_history if t.get("objective_value") is not None), key=lambda t: t["objective_value"], reverse=True)` — errored combos are already recorded separately and carry no score to rank. Alternatively record `"objective_value": _DISQUALIFY` instead of `None` so the record sinks to the bottom of every downstream ranking (`select_rerank_candidates` at rerank_select.py:39 already treats `<= DISQUALIFY` as disqualified). Add a behavioural test that drives the grid branch with one raising combo and asserts the job reaches a terminal non-failed status.

## [5] HIGH — Zero-survivor refusal is defeated: apply-as-preset falls back to the stale spot best that the survival gate rejected

- dim: `job-lifecycle`
- site: `backend/app/routers/research.py:707`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
When the survival gate finds nothing deployable, the optimizer deliberately empties the best so nothing can be promoted:
```python
                    # Zero survivors: do NOT promote a disqualified candidate as "best".
                    ...
                    best_so_far = {"value": -1e9, "params": {}, "metrics": {}, "trial_num": -1}
```
(optimizer.py:1761-1766), and the final job patch writes `"best_params": best_so_far["params"]` = `{}` (optimizer.py:1858). But that terminal patch (optimizer.py:1853-1883) contains **no `best_so_far` key**, so the job document keeps the `best_so_far` written during the trial loop:
```python
                    await _update_job(job_id, {
                        "n_trials_completed": completed,
                        "best_so_far": {"value": round(best_so_far["value"], 4), "params": best_so_far["params"], ...},
                    })
```
(optimizer.py:1513-1516) — i.e. the SPOT-loop winner. Apply-as-preset then falls back to it because `{}` is falsy:
```python
    best_params = job.get("best_params") or (job.get("best_so_far") or {}).get("params")
    if not best_params:
        raise HTTPException(400, "Job has no best parameters to save (no qualifying trial yet)")
```
(research.py:707-709). The endpoint accepts `done_no_survivor` (status check at research.py:705 allows `done`), and it stamps `validation.stage = "option_ranked"` (research.py:736-740) because `_survival_on and (_surv.get("survivors") or 0) > 0` is False. The frontend gates the Apply button on the stale field, not on `best_params`: `const hasBest = (bsf.params && Object.keys(bsf.params).length > 0) || ...` (frontend/src/pages/Optimizer.jsx:1436) with `bsf = job.best_so_far` (Optimizer.jsx:1426), and `finished = status === "done" || status === "done_no_survivor"` (Optimizer.jsx:1429) — so "Save as Preset" renders for a zero-survivor job (Optimizer.jsx:1492-1497).
```

**Failure scenario:**
User runs option_rerank with Survivability ON. Every finalist blows the drawdown floor, so `survival_summary.survivors == 0` and the job ends `done_no_survivor` with `best_params: {}` — the engine's explicit "nothing is deployable". The UI still shows the Best-so-far card and a Save-as-Preset button. The user clicks it; the backend silently substitutes the spot-loop best (a config the survival gate evaluated and failed) and writes a preset tagged `validation.stage: "option_ranked"`, indistinguishable in the Saved Presets page from a genuinely option-ranked result. That preset is then loadable in Backtest Lab and prefills the deploy wizard.

**Suggested fix:**
Two independent guards. (1) In `run_optimization`'s terminal patch (optimizer.py:1853), always persist the final in-memory `best_so_far` alongside `best_params` so the document is self-consistent — in the zero-survivor branch that clears it to `{}`. (2) In `apply_opt_as_preset` (research.py:707), stop the fallback for jobs whose gate refused: reject when `job.get("status") == "done_no_survivor"` or when `(job.get("survival_summary") or {}).get("survivors") == 0`, and only fall back to `best_so_far.params` when `best_params` is absent (`is None`), never when it is present-and-empty.

## [6] HIGH — Resume silently skips up to 49 trials it then counts as completed (grid: 49 combos never evaluated)

- dim: `job-lifecycle`
- site: `backend/app/optimizer.py:1365`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
Resume takes the trial counter from the frequently-written progress field, preferring it over the actual rehydrated history:
```python
            trial_history = list(rdoc.get("trial_log") or [])
            completed = int(rdoc.get("n_trials_completed") or len(trial_history))
```
(optimizer.py:1364-1365). But the two are persisted at different cadences: `n_trials_completed` every 5 trials (`if completed % 5 == 0` — optimizer.py:1465, 1512, and `(completed // 5) > (prior // 5)` at 1567) while `trial_log` is flushed only every 50 (`if completed % 50 == 0: await _flush_trial_log(...)` — optimizer.py:1470-1471, 1517-1518, 1572-1573). `_maybe_pause` flushes both (optimizer.py:1423), so a user-initiated pause is consistent — but a server restart is not: backend/server.py:84-92 marks any `queued`/`running`/`analyzing` job `interrupted` (a resumable state) with no flush. The grid branch then resumes by slicing the combo list: `for params in combos[completed:]` (optimizer.py:1433), and the bayesian branch by `for i in range(completed, n_trials)` (optimizer.py:1481).
```

**Failure scenario:**
A 200-trial grid job is killed by a container rebuild at trial 97. The last `%5` write set `n_trials_completed: 95`; the last `%50` flush persisted 50 trial records. Startup marks it `interrupted`; the user clicks Resume. `completed = 95`, `trial_history` has 50 entries, and the loop starts at `combos[95:]` — combos 50..94 are **never evaluated** yet the job finishes reporting `n_trials_completed: 200/200`, a 100% progress bar and status `done`. For bayesian/option_rerank the same 45-trial hole silently shrinks the pool that `select_rerank_candidates(sorted_trials, top_k=rerank_top_k, ...)` (optimizer.py:1662-1663) draws the Stage-2 finalists from, plus `top_n_alternatives` and `parameter_importance` — so the resumed job's reported best is drawn from a different search than the ceiling it claims, and is not reproducible by re-running the same config.

**Suggested fix:**
Never let the counter run ahead of the evidence. Either (a) derive the resume point from the persisted history — `completed = len(trial_history)` (and record it as such), or (b) flush the trial log on the same 5-trial cadence as `n_trials_completed` so the two can never diverge by more than 5, or (c) write `n_trials_completed` only from inside `_flush_trial_log`. Additionally persist a `trials_evaluated` count distinct from `n_trials_completed` and surface the gap, so a resumed job cannot report work it never did.

## [7] HIGH — _save_best_as_backtest drops the optimizer's live entry window — the saved "Optimized ·" run, its walk-forward, its significance test and its option replay all use 09:25–15:00 while every trial used 09:25–14:50

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:698`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
Every trial is clamped to the live window (optimizer.py:1340-1341 `return _evaluate(get_enriched, strategy, params, instrument, costs, pretrade,\n                             trade_window_start, trade_window_end)`), and the UI ALWAYS sends the clamp (frontend/src/pages/Optimizer.jsx:518-519 `trade_window_start: config.trade_window_start || "09:25",` / `trade_window_end: config.trade_window_end || "14:50",`; default at :163-164 is `trade_window_end: "14:50"`).

But the promoted config is re-run with NO window argument:
optimizer.py:698 `res = await asyncio.to_thread(run_backtest, df_enriched, strategy, merged, instrument=instrument, costs_enabled=costs_enabled, pretrade_filters=pretrade)`
optimizer.py:702 `wf = await asyncio.to_thread(walk_forward, df_enriched, strategy, merged, instrument=instrument, costs_enabled=costs_enabled, pretrade_filters=pretrade)`
optimizer.py:717-722 `_opt_req = BacktestReq(instrument=instrument, strategy_id=strategy.id, params=best_params, ... )`  — no trade_window_* fields

Those defaults are 15:00, not 14:50:
backtest.py:79-80 `trade_window_start: str = TRADE_WINDOW_START,` / `trade_window_end: str = TRADE_WINDOW_END,` with backtest.py:16,20 `TRADE_WINDOW_START = "09:25"` / `TRADE_WINDOW_END = "15:00"`; walkforward.py:207-208 `trade_window_start: str = "09:25", trade_window_end: str = "15:00",`; schemas.py:97-98 `trade_window_start: str = "09:25"` / `trade_window_end: str = "15:00"`.

The window is also absent from the persisted config (optimizer.py:730-744 lists instrument/mode/strategy_id/timeframe/params/costs_enabled/walkforward/start_ts/end_ts/pretrade_filters/source/… and no trade_window key), so the Lab re-reads the wrong window on load: BacktestLab.jsx:787-788 `trade_window_start: r.config?.trade_window_start || "09:25", trade_window_end: r.config?.trade_window_end || "15:00",`. Apply-as-preset loses it too — research.py:711-721 builds the preset `config` with no trade_window keys (it is only stamped as advisory metadata at :748-749 inside `config["validation"]`). wfo.py:806-808 calls the same helper with the same omission.
```

**Failure scenario:**
User optimizes confluence_scalper on NIFTY with the UI defaults (trade_window 09:25–14:50). Trials score only entries before 14:50, and trial #83 wins with total_pnl_pts=412 / max_dd_pts=-88. The optimizer then saves "Optimized · <name>" by re-running the same params over 09:25–15:00, which admits every 14:50–15:00 entry (~10 extra eligible minutes × ~250 sessions). The saved run reports different trades, different total_pnl_pts, a different walk_forward verdict and a different `significance` badge than the trial that won — and the option replay at :723 pairs those extra live-impossible entries. Clicking "Open best in Lab" or "Apply as preset" then re-runs at 15:00 forever, so the number the user validates can never reproduce the number the optimizer reported. This is bug #6 (premium entries past live's 14:50 block) reintroduced on the ordinary path.

**Suggested fix:**
Thread the window through `_save_best_as_backtest` (add `trade_window_start`/`trade_window_end` params, pass them to `run_backtest`, `walk_forward`, and `BacktestReq`), persist them inside the saved `config` dict at :730-744, and add them to the preset `config` in research.py:711-721 so the Lab/preset round trip re-applies the clamp. Both call sites (optimizer.py:1836 and wfo.py:806) already have the values in scope.

## [8] HIGH — Stage-2 option re-rank re-runs the spot backtest WITHOUT the live entry window, so the final promoted candidate is ranked on a trade set Stage 1 and the survival gate both excluded

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:1037`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
optimizer.py:1032-1041 (`# 1. Spot backtest each candidate (fast post Slice-1) to get its trades.`):
```
    for cand in candidates:
        merged = strategy.merged_params(cand["params"])
        enr = get_enriched(merged)
        res = await asyncio.to_thread(
            run_backtest, enr, strategy, merged,
            instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade,
        )
        cand_trades.append(res.get("trades", []) or [])
```
No `trade_window_start`/`trade_window_end` — and `_option_rerank`'s signature (optimizer.py:1006-1010) has no such parameters at all, so the caller cannot supply them. The sibling evaluator DOES clamp: optimizer.py:880-884 `_tw = ({"trade_window_start": trade_window_start, "trade_window_end": trade_window_end} if trade_window_start and trade_window_end else {})` … `run_backtest, test_df, strategy, merged_params, instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade, **_tw)`, and the caller passes them at optimizer.py:1698 `trade_window_start=trade_window_start, trade_window_end=trade_window_end)`. The module docstring for the flag is explicit (optimizer.py:1193-1195): "O6: live-effective entry window (IST). Threaded into EVERY optimizer backtest (trials, survival folds, parallel workers) so selection + the survival gate agree and never reward 14:50–15:00 entries live can't take."
```

**Failure scenario:**
evaluation_mode="option_rerank" with the UI default trade_window_end=14:50. Stage 1 ranks 150 trials on pre-14:50 entries; `_option_rerank` then regenerates each finalist's spot trades over 09:25–15:00, so `union_keys`, `expiry_by_trade`, `paired_trade_count`, `option_pnl_value` and the reported `spot_trade_count` (:1144 `len(pc["trades"])`) all include 14:50–15:00 entries. The candidate promoted at :1773-1786 (`best["option_pnl_value"]`) can win purely on those last-10-minute pairs, and then `_survival_eval_oos` re-evaluates it on the CLAMPED window (:880-884) — so the ranking stage and the gate that clears it score different trade sets, and the promoted best's headline option ₹ is not achievable live.

**Suggested fix:**
Add `trade_window_start`/`trade_window_end` keyword args to `_option_rerank` (mirroring `_survival_eval_oos`'s `_tw` idiom at :880-884), pass them from the call site at optimizer.py:1670-1674, and forward them into the `run_backtest` call at :1037-1040.

## [9] HIGH — Grid search: one raising combo is recorded with objective_value=None, then the analyze stage sorts on it and the whole job dies with TypeError — defeating the explicit "must NOT crash the whole job" handler

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:1626`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
The grid loop deliberately records a failed combo with a None objective (optimizer.py:1440-1452, comment: `# O14: a single raising combo must NOT crash the whole job (resume then deterministically re-hits the same combo forever). Mirror the bayesian study.optimize(catch=Exception): disqualify + continue.`):
```
                except Exception as exc:
                    log.warning("grid trial %d raised (%s) — disqualified, continuing",
                                completed, exc)
                    trial_history.append({"params": params, "metrics": None,
                                          "objective_value": None, "error": str(exc)[:200]})
```
The analyze stage then sorts that list unguarded, outside any try:
optimizer.py:1626 `sorted_trials = sorted(trial_history, key=lambda t: t["objective_value"], reverse=True)`
Verified in this repo's Python: `sorted([{'objective_value':1.5},{'objective_value':None}], key=lambda t: t['objective_value'], reverse=True)` → `TypeError: '<' not supported between instances of 'float' and 'NoneType'`.
The only enclosing handler is the job-level one at optimizer.py:1904-1906 `except Exception as e: log.exception(...); await _update_job(job_id, {"status": "failed", "error": str(e), ...})`.
A second crash site sits just downstream for option_rerank runs — rerank_select.py:39 `if t.get("objective_value", DISQUALIFY) <= DISQUALIFY:` also raises on None.
```

**Failure scenario:**
method="grid" with 200 combos. Combo 57 raises inside `run_backtest` (e.g. a degenerate param combination). The loop correctly logs and continues, all 200 combos finish, and the job flips to "analyzing" — then line 1626 raises TypeError. The job is written as `status: "failed"` with `error: "'<' not supported between instances of 'float' and 'NoneType'"`, discarding all 199 successful trials, best_params, importance, heatmap and robustness. Resuming re-runs the loop and dies at the same place, so the run is unrecoverable.

**Suggested fix:**
Filter or coerce failed records before sorting, e.g. `sorted_trials = sorted((t for t in trial_history if t.get("objective_value") is not None), key=lambda t: t["objective_value"], reverse=True)`, and use the same coercion for the fallback-importance loop at :1641 and for `select_rerank_candidates` (rerank_select.py:39 should treat None as DISQUALIFY).

## [10] HIGH — min_trades guards the SPOT trade count only; option-rerank promotes any candidate with a single PAIRED trade, so a statistically empty config becomes the reported best

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:1773`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
The guard counts whatever `trade_count` the Stage-1 metrics carry — for an ordinary strategy that is `run_backtest`'s SPOT count (optimizer.py:147-151):
```
    tc = int(metrics.get("trade_count", 0) or 0)
    if tc == 0:
        return _DISQUALIFY  # no trades at all
    if min_trades and tc < min_trades:
        return _DISQUALIFY  # statistically meaningless sample
```
`_evaluate` feeds it `run_backtest`'s spot metrics (optimizer.py:431-432 `res = run_backtest(df_enriched, strategy, merged, ...)` → `metrics = dict(res["metrics"])`), where `trade_count` is `n = len(trades)` of SPOT trades (backtest.py:277-293).

Promotion in option-rerank mode then requires only ONE paired trade:
optimizer.py:1773 `elif ranked and ranked[0]["paired_trade_count"] > 0:`
with the ordering at optimizer.py:1157 `ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)`.
`_option_rerank` applies no minimum paired count anywhere (optimizer.py:1136-1146 records `"paired_trade_count": int(m.get("paired_trade_count", 0) or 0)` and nothing filters on it).

The codebase already documents the gap: survival.py:21-23 `# A tail statistic (ruin probability) needs more than the spot min_trades=10\n# guard (which counts SPOT trades); this counts PAIRED rupee trades.\nMIN_TRADES_FOR_RUIN = 100` — but that gate lives only in the survival path, which is OFF by default (survival.py:37 `enabled: bool = False`, and Optimizer.jsx:531 sends `enabled: Boolean(config.survival_config?.enabled)`).
```

**Failure scenario:**
evaluation_mode="option_rerank", guards on with min_trades=10, survival disabled (the default). A config takes 100 spot trades (guard passes comfortably) but only 3 pair — the strikes it picks are illiquid, or its entries fall in warehouse gaps. Those 3 pairs happen to net +₹14,000, beating a config with 60 paired trades at +₹9,000. `ranked[0]["paired_trade_count"] > 0` is true, so the 3-pair config is promoted to `best_params`, saved as the "Optimized ·" run, and reported with `best_option_pnl_value = 14000` next to spot metrics showing 100 trades — a rupee headline computed from a 3-sample tail with no minimum-sample guard anywhere in the path.

**Suggested fix:**
Apply the trade-count guard to the PAIRED count in Stage 2: filter `ranked` to `r["paired_trade_count"] >= min_trades` before the promotion at :1773 (and before the survivor sort at :1731-1734), and surface the dropped count in `rerank_info` so a low-pairing sweep is visible rather than silently promoted.

## [11] HIGH — When every trial fails the guard rails the optimizer still promotes, saves and lets the user apply a disqualified config — and the dedicated "no usable result" banner never fires

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:1498`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
A disqualified trial returns the sentinel `-1e9` (rerank_select.py:13 `DISQUALIFY = -1e9`), which still beats the `-inf` seed:
optimizer.py:1406 `best_so_far = {"value": -float("inf"), "params": {}, "metrics": {}, "trial_num": -1}`
optimizer.py:1498-1503:
```
                if study_best_val is not None and study_best_val > best_so_far["value"]:
                    best_so_far = {
                        "value": study_best_val, "params": study_best_params,
```
(grid path identically at :1454 `if val > best_so_far["value"]:`). So `best_so_far["params"]` becomes a guard-rejected config, which then triggers the save:
optimizer.py:1833-1837 `if best_so_far["params"]:` … `best_backtest_run_id = await _save_best_as_backtest(...)`
and is published as the answer: optimizer.py:1858 `"best_params": best_so_far["params"],` while only the headline number is suppressed: optimizer.py:1859 `"best_value": round(best_so_far["value"], 4) if best_so_far["value"] > -1e8 else None,`.
The UI's guard is keyed on params, not on the value, so the warning designed for exactly this case is skipped: Optimizer.jsx:1436-1437 `const hasBest = (bsf.params && Object.keys(bsf.params).length > 0) || ...` and Optimizer.jsx:1558-1560 `{(finished || cancelled) && !hasBest && (` … `No trial produced a usable result — every candidate either took no trades or was disqualified by the guard rails.` The trophy card renders instead (Optimizer.jsx:1568 `{bsf.params && Object.keys(bsf.params).length > 0 && (`), with the headline showing "—" (Optimizer.jsx:214 `const fmtBest = (v) => (v == null || v <= -1e8) ? "—" : Number(v).toFixed(3);`) but real-looking metrics from `bsf.metrics`. Apply-as-preset accepts it because status is "done" and best_params is non-empty (research.py:705-709).
```

**Failure scenario:**
User runs 150 trials with min_trades=10 on a narrow date range; every trial takes 1–6 trades, so `_objective_value` returns -1e9 for all of them. The job finishes as "done", the "no usable result" banner is suppressed, and the user sees a Best-so-far trophy card showing Trades=4, WinRate=75%, Net Pts=31 with a "—" objective, plus a saved "Optimized · …" backtest run they can open and a preset they can save and deploy — for a configuration the guard rails explicitly rejected as statistically meaningless.

**Suggested fix:**
Treat the sentinel as "no result": require `study_best_val > _DISQUALIFY` before overwriting `best_so_far` at :1498 (and `val > _DISQUALIFY` at :1454), and clear `best_so_far["params"]`/skip `_save_best_as_backtest` when the final value is `<= _DISQUALIFY` — the survival path already does exactly this at :1766 `best_so_far = {"value": -1e9, "params": {}, "metrics": {}, "trial_num": -1}`.

## [12] MEDIUM — A single raising grid combo permanently fails the whole job at the analyze stage (None objective_value poisons the sort)

- dim: `option-rerank-stage2`
- site: `backend/app/optimizer.py:1626`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
The grid loop deliberately records failures instead of crashing (optimizer.py:1440-1452):
```python
                # O14: a single raising combo must NOT crash the whole job (resume
                # then deterministically re-hits the same combo forever). ...
                except Exception as exc:
                    ...
                    trial_history.append({"params": params, "metrics": None,
                                          "objective_value": None, "error": str(exc)[:200]})
```
The analyze stage then sorts on that key unguarded (optimizer.py:1626):
```python
        sorted_trials = sorted(trial_history, key=lambda t: t["objective_value"], reverse=True)
```
Verified in this repo's interpreter: `sorted([{'objective_value':1.5},{'objective_value':None}], key=lambda t: t['objective_value'], reverse=True)` raises `TypeError: '<' not supported between instances of 'float' and 'NoneType'`. The same comparison in `select_rerank_candidates` (rerank_select.py:39 `if t.get("objective_value", DISQUALIFY) <= DISQUALIFY:`) also raises `TypeError: '<=' not supported between instances of 'NoneType' and 'float'`. The TypeError escapes to the top-level handler (optimizer.py:1904-1906) which marks the job `status: "failed"`.
```

**Failure scenario:**
`method="grid"`, one combo raises (e.g. a param combination a strategy rejects). All 150 trials complete and the log is flushed, then the analyze stage dies with a cryptic `'<' not supported between instances of 'float' and 'NoneType'`, the job is marked failed and no best/re-rank/preset is produced. Resume makes it worse: `trial_history` is rehydrated from `trial_log` including the `None` record (`_compact_trial` preserves `objective_value`, optimizer.py:635), `completed == len(trial_history)` so the loop is a no-op, and the job re-crashes at the same line — permanently unrecoverable.

**Suggested fix:**
Filter or coerce before ranking: `sorted_trials = sorted((t for t in trial_history if t.get("objective_value") is not None), key=lambda t: t["objective_value"], reverse=True)` and make `select_rerank_candidates` treat `None` as DISQUALIFY (`v = t.get("objective_value"); if v is None or v <= DISQUALIFY: continue`). Add a grid test with one raising combo asserting the job still finishes.

## [13] MEDIUM — `search_exit_controls` burns the analyze budget on a provably no-op grid whenever exit_mode is the default `spot_exit`

- dim: `option-rerank-stage2`
- site: `backend/app/optimizer.py:1719`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
The grid re-evaluates each survivor per config (optimizer.py:1710-1728):
```python
                            for gc in grid:
                                v = await _survival_eval_oos(
                                    strategy, df_enr, merged, rerank_contracts, rerank_candles,
                                    instrument, costs, pretrade, {**option_cfg, "exit_controls": gc}, survival,
                                    ...
                                better = (v.get("calmar") or -1e9) > (r["survival"].get("calmar") or -1e9)
```
But `exit_controls` only reaches the engine through `_walk_option_exit`, which is called only inside `if use_option_levels:` (option_backtest.py:782-798), and `use_option_levels` requires `exit_mode == "option_levels"` plus a level (option_backtest.py:612-618):
```python
    use_option_levels = exit_mode == "option_levels" and (
        (option_target_pts and option_target_pts > 0) ...
```
The optimizer's option_cfg default is `spot_exit` (Optimizer.jsx:133 `option_exit_mode: "spot_exit"`, mirrored by schemas.py:60), and nothing in this path runs `validate_exit_risk_config`, whose first rule is exactly this (exit_controls.py:160-162): "exit_controls require option execution (option_levels / option re-rank); premium trailing is impossible spot-only."
```

**Failure scenario:**
User enables survival + "search exit controls" but leaves the default `spot_exit`. For every survivor the optimizer runs `len(exit_control_grid())` (4 by default, up to 12) extra full survival evaluations — each 3 folds of run_backtest + simulate_paired_option_trades over the multi-million-row candle frame. Every one returns the identical verdict, `better` is a strict `>` so it is never True, `chosen_exit_controls` stays unset and the "Auto-tuned exit" column never appears. The user concludes no trailing/breakeven config helps, when in fact none was ever simulated — and on a job with a non-zero `analyze_budget_sec` this wasted work can trip `_analyze_should_stop()` and truncate real survival evaluation.

**Suggested fix:**
Before entering the grid, skip it (with a job-level warning) unless `option_cfg.get("exit_mode") == "option_levels"` and a premium level is set — or call `validate_exit_risk_config(..., option_exec_on=(option_cfg.get("exit_mode")=="option_levels"))` and surface the error. Gate the UI checkbox on `option_exit_mode === "option_levels"` as well.

## [14] MEDIUM — On a truncated survival stage, un-evaluated finalists are silently counted as non-survivors and `evaluated` over-reports

- dim: `option-rerank-stage2`
- site: `backend/app/optimizer.py:1753`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
The loop can break early (optimizer.py:1705-1707):
```python
                    await _an_progress("survival", i + 1, len(ranked), _per_item_surv)
                    if await _analyze_should_stop():  # O13: budget OR cancel/pause
                        break
```
The survivor filter then treats a missing `survival` key as a failure (optimizer.py:1708-1709):
```python
                survivors = [r for r in ranked if r.get("survival", {}).get("survived")
                             and (r["survival"].get("total_return_pct") or 0) > 0]
```
and the summary claims the full count regardless (optimizer.py:1753 `survival_summary = {"survivors": len(survivors), "evaluated": len(ranked), ...}`), as does optimizer.py:1790 `analyzed_candidates = f"{len(ranked)}"`. The zero-survivor branch attributes them to `"unknown"` (optimizer.py:1762-1765 `rs = r.get("survival", {}).get("reason", "unknown")`). The UI reports that number verbatim: Optimizer.jsx:1539 "Analyzing budget hit — evaluated {job.analyzed_candidates} candidate(s)."
```

**Failure scenario:**
`analyze_budget_sec=1800` (the backend/API default) with `rerank_top_k=50`. Survival evaluation stops after finalist 12. Finalists 13-50 have no `survival` key, so they are dropped from `survivors` exactly as if they had failed the drawdown/RoR gate, yet the UI says "evaluated 50 candidate(s)" and `survival_summary.evaluated` is 50. If none of the first 12 survived, the job reports `done_no_survivor` with `reason_counts: {"unknown": 38, ...}` — a verdict of "nothing is deployable" for 38 candidates that were never tested.

**Suggested fix:**
Track the loop index and report `"evaluated": i + 1` (and `"skipped_unevaluated": len(ranked) - (i + 1)`) in `survival_summary`; tag un-evaluated rows `r["survival"] = {"survived": False, "reason": "not_evaluated_budget"}` so they are visibly distinct from real failures, and suppress `done_no_survivor` when the stage was truncated.

## [15] MEDIUM — Stage 2 promotes a winner on option rupees with no minimum-sample guard — one paired trade is enough

- dim: `option-rerank-stage2`
- site: `backend/app/optimizer.py:1773`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
With survival off (the default: Optimizer.jsx:145-146 `survival_config: { enabled: false, ... }`) the promotion test is only "more than zero paired trades" (optimizer.py:1773-1786):
```python
            elif ranked and ranked[0]["paired_trade_count"] > 0:
                best = ranked[0]
                best_so_far = {
                    "value": best["option_pnl_value"],
```
and the ranking key is pure rupees (optimizer.py:1157 `ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)`). The Stage-1 `min_trades` guard (default 10 in schemas.py:190, 30 in the UI) is enforced only on the SPOT `trade_count` inside `_objective_value` (optimizer.py:147-151) and never re-applied to the option side. Stage 2 can shrink the sample arbitrarily: the DTE filter drops trades before the sim (optimizer.py:1070-1072) and pairing losses (`missing_contract` / `missing_entry_candle` / `missing_exit_candle`) drop more. The 100-trade floor exists only in the survival path (survival.py:23 `MIN_TRADES_FOR_RUIN = 100`), which is off by default.
```

**Failure scenario:**
A candidate keeps 40 spot trades through Stage 1, then a `dte_filter=[0]` plus thin option coverage leaves 2 paired trades, one of which is a +₹18,000 outlier. It sorts to rank #1 on `option_pnl_value`, becomes `best_so_far`, drives `best_value`, the saved backtest run and the preset — a two-trade artifact presented as the optimizer's answer. The re-rank table does show `Paired/spot` and a coverage % (Optimizer.jsx:2245-2247), so it is discoverable, but nothing blocks or flags the promotion.

**Suggested fix:**
Apply the job's `min_trades` (and optionally a coverage floor) to `paired_trade_count` before promotion: require `ranked[0]["paired_trade_count"] >= min_trades`, and either sink under-sampled candidates in the sort key or mark them `insufficient_sample` in the ranked row so the UI can flag them.

## [16] MEDIUM — apply-as-preset records the validated trade window but the Backtest Lab never restores it, so the re-test runs a different window

- dim: `option-rerank-stage2`
- site: `backend/app/routers/research.py:748`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
The preset stores the window for the record (research.py:741-750):
```python
    config["validation"] = {
        "stage": _stage,
        ...
        "trade_window_start": _cfg.get("trade_window_start"),
        "trade_window_end": _cfg.get("trade_window_end"),
    }
```
but `applyPreset` restores only instrument/mode/strategy/params plus the `execution` block (BacktestLab.jsx:295-303):
```javascript
      setConfig((c) => ({
        ...c,
        instrument: cfg.instrument || c.instrument,
        mode: cfg.mode || c.mode,
        strategy_id: cfg.strategy_id || c.strategy_id,
        params: cfg.params ? { ...cfg.params } : c.params,
        name: name,
        ...exFields,
      }));
```
`trade_window_start`/`trade_window_end` are absent, so the Lab keeps its own defaults `"09:25"`/`"15:00"` (BacktestLab.jsx:121-122) and posts them (BacktestLab.jsx:546-547). `execution_from_option_config` (preset_execution.py:37-66) carries moneyness/dte/exit_mode/lots/levels/costs/sizing but has no window field either.
```

**Failure scenario:**
User clicks "Save as Preset" on an option_rerank job validated at 09:25-14:50, loads that preset in the Backtest Lab, and runs it. The Lab runs 09:25-15:00: extra entries plus a different forced-close time for every trade open at 14:50. The rupee result does not match the optimizer's and the user cannot tell why — the discrepancy is stored one level down in `config.validation` where no UI surface reads it.

**Suggested fix:**
Read `cfg.validation?.trade_window_start/end` (falling back to the current defaults) inside `applyPreset` and set them on config, and/or promote the window to top-level preset config fields so both the Lab form and the deploy wizard prefill the window the result was validated under.

## [17] MEDIUM — Option re-rank runs to completion after Stop/Pause — neither of its two loops reads the control flags

- dim: `job-lifecycle`
- site: `backend/app/optimizer.py:1034`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
`_option_rerank` has two long loops. The first re-runs a full spot backtest per candidate with no budget and no control check at all:
```python
    cand_trades: List[List[Dict[str, Any]]] = []
    for cand in candidates:
        merged = strategy.merged_params(cand["params"])
        enr = get_enriched(merged)
        res = await asyncio.to_thread(
            run_backtest, enr, strategy, merged,
            instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade,
        )
        cand_trades.append(res.get("trades", []) or [])
```
(optimizer.py:1033-1041). The second (per-candidate option sim) checks only the wall-clock budget, never cancel/pause:
```python
        if analyze_t0 is not None and over_budget(
                elapsed=time.monotonic() - analyze_t0, budget_sec=analyze_budget_sec):
            budget_hit = True
            break
```
(optimizer.py:1152-1155). The stop signal that *does* read the flags, `_analyze_should_stop` (optimizer.py:1597-1611, calling `_job_control` at 1608), is invoked only at optimizer.py:1706 (survival loop) and 1714 (exit-control grid) — confirmed by grep: the only `_job_control(job_id)` / `_is_cancelled(job_id)` sites are optimizer.py:1434, 1482, 1525, 1582, 1608, 1815, 1847. `rerank_top_k` is validated up to 500 (research.py:566-567), and the `analyze_budget_sec` default is 1800s with 0 meaning unlimited (optimizer.py:1591) — the UI hint at Optimizer.jsx:944 recommends 0 for the evidence profile.
```

**Failure scenario:**
User starts option_rerank with `rerank_top_k=300` and Analyzing budget = 0 (unlimited, the recommended evidence setting). The trial loop ends and the job enters `analyzing`. The user presses Stop. `cancel_opt_job` sets `cancelled: True`, but the re-rank is inside `_option_rerank`: 300 spot backtests followed by a 4M-row option-candle load and 300 in-memory sims run to completion with `budget_sec=0` disabling the only break. The Stop button appears dead for tens of minutes while the machine stays pinned; the UI keeps showing ANALYZING. Pause behaves identically. The cancel is only noticed afterwards at optimizer.py:1815.

**Suggested fix:**
Thread the existing stop signal into `_option_rerank`: pass `should_stop=_analyze_should_stop` alongside the current `analyze_t0`/`analyze_budget_sec`/`progress_cb`, and check it at the top of both loops (optimizer.py:1034 and inside the sim loop next to the budget check at 1152), returning partial `ranked` with `budget_hit`/`stopped` set. The survival loop already demonstrates the pattern at optimizer.py:1706.

## [18] MEDIUM — A truncated survival sweep reports every finalist as "evaluated" and every unevaluated one as a failure reason

- dim: `job-lifecycle`
- site: `backend/app/optimizer.py:1753`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
The survival loop can break early on budget, cancel or pause:
```python
                    await _an_progress("survival", i + 1, len(ranked), _per_item_surv)
                    if await _analyze_should_stop():  # O13: budget OR cancel/pause
                        break
```
(optimizer.py:1705-1707). The summary written afterwards nevertheless reports the FULL finalist count as evaluated:
```python
                    survival_summary = {"survivors": len(survivors), "evaluated": len(ranked),
```
(optimizer.py:1753), and the zero-survivor branch attributes a failure reason to finalists that were never touched:
```python
                    reasons: Dict[str, int] = {}
                    for r in ranked:
                        rs = r.get("survival", {}).get("reason", "unknown")
                        reasons[rs] = reasons.get(rs, 0) + 1
```
(optimizer.py:1762-1765) — a finalist with no `survival` key contributes `"unknown"`, rendered verbatim to the user by `NoSurvivorBanner` (frontend/src/pages/Optimizer.jsx:2303-2308). Worse, only the budget path raises a flag: `analyze_budget_hit` is set inside `_analyze_should_stop` for `over_budget` (optimizer.py:1604-1606) but NOT for the cancel/pause return at 1611, and the final status read at optimizer.py:1847 uses `_is_cancelled` only — so a PAUSE during the survival sweep yields `final_status = "done"` with `analyze_budget_hit: False`, i.e. no truncation signal anywhere. The UI's only truncation banner is gated on that flag (Optimizer.jsx:1537-1541).
```

**Failure scenario:**
150 finalists reach the survival gate. The user presses Pause at finalist 12 (say, to free the machine). `_analyze_should_stop()` returns True, the loop breaks, and 138 finalists never run a single OOS fold. The job finalizes as `done` (or `done_no_survivor`) with `survival_summary: {survivors: 0, evaluated: 150, reason_counts: {"unknown": 138, "max_dd": 12}}` and no budget-hit banner. The user reads "No strategy survived your constraints" over a 150-candidate denominator and concludes the strategy family is dead, when 92% of the search was never evaluated. The same understated survivor rate feeds `finished["survival_summary"]` (optimizer.py:1870) and the preset's `validation.survivors` (research.py:746).

**Suggested fix:**
Track the real count: increment a `surv_evaluated` counter in the loop and write `"evaluated": surv_evaluated, "finalists": len(ranked)`. Build `reason_counts` only over `[r for r in ranked if "survival" in r]` and report the remainder as an explicit `not_evaluated` count. Set a distinct `analyze_stopped_by` field (`"budget" | "cancelled" | "paused"`) whenever `_analyze_should_stop` fires, and surface it in the UI next to the existing `analyze_budget_hit` banner so a paused/cancelled analysis is never presented as a completed one.

## [19] MEDIUM — best_so_far is never refreshed at finish — the "Best so far" card shows different params than the ones Apply saves

- dim: `job-lifecycle`
- site: `backend/app/optimizer.py:1858`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
In option_rerank mode the in-memory `best_so_far` is replaced by the Stage-2 winner (`best_so_far = {"value": ..., "params": best["params"], ...}` at optimizer.py:1737-1750 for survivors, 1775-1786 without the survival gate). The terminal job patch persists it only under a *different* key:
```python
            "best_params": best_so_far["params"],
            "best_value": round(best_so_far["value"], 4) if best_so_far["value"] > -1e8 else None,
            "best_metrics": best_so_far["metrics"],
```
(optimizer.py:1858-1860). The patch dict (optimizer.py:1853-1883) has no `best_so_far` key, so the document retains the value last written by the trial loop — the SPOT search winner (optimizer.py:1513-1516). The frontend renders that stale field as the headline param grid:
```jsx
  const bsf = job.best_so_far || {};
  ...
            {Object.entries(bsf.params).map(([k, v]) => (
```
(frontend/src/pages/Optimizer.jsx:1426 and 1594), under a headline that shows the PROMOTED candidate's option rupee: `const optionPnl = job.best_option_pnl_value ?? job.best_metrics?.option_pnl_value ?? ...` (Optimizer.jsx:1444, rendered at 1577-1580). Stage 2 exists precisely because spot rank and option rank disagree (`ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)` — optimizer.py:1157), so the promoted params are normally NOT the spot #1.
```

**Failure scenario:**
Option re-rank promotes finalist #23 (best net option rupee). The job doc ends with `best_params` = finalist #23's params but `best_so_far.params` = the spot-objective #1 (a different config). The Best-so-far card shows finalist #1's parameter values next to finalist #23's rupee headline. The user writes those parameter values down, or eyeballs them for sanity, then clicks Save as Preset — which persists `best_params` (finalist #23). The numbers displayed and the config saved describe two different strategies, with no indication of the swap.

**Suggested fix:**
Include the final in-memory value in the terminal patch: add `"best_so_far": {"value": ..., "params": best_so_far["params"], "metrics": best_so_far["metrics"], "trial_num": best_so_far["trial_num"]}` to the `finished` dict at optimizer.py:1853 (guarding `-inf` the same way `best_value` does at 1859). This also removes the divergence that finding #2 exploits. Then point the frontend's `hasBest` and param grid at `job.best_params` for terminal states, keeping `best_so_far` for in-flight polling only.

## [20] MEDIUM — WFO analyze stage reads no control flag at all — Stop/Pause is ignored through option-OOS pairing and the full walk-forward, and the job reports "done"

- dim: `job-lifecycle`
- site: `backend/app/wfo.py:791`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
Grep confirms `_job_control(job_id)` is called at exactly two places in wfo.py — lines 688 and 707, both inside the per-window trial loop. After the window loop the job announces the analyze stage and then never checks again:
```python
        # ---- Final analysis over completed windows ----
        await _update_job(job_id, {"status": "analyzing"})
```
(wfo.py:791). Everything that follows is unpoliced: `_save_best_as_backtest(..., run_walkforward=True, option_config=None)` (wfo.py:806-808), which runs the slow multi-fold `walk_forward` (optimizer.py:701-702), and `_pair_oos_with_options` (wfo.py:816-821), which issues a `to_list(length=4000000)` option-candle query (wfo.py:457) followed by a full `simulate_paired_option_trades` (wfo.py:464-479). The terminal status is computed from the loop-local flag only:
```python
        final_status = "cancelled" if (cancelled and len(completed_windows) < len(windows)) else "done"
```
(wfo.py:826) — `cancelled` is only ever set inside the window loop (wfo.py:690, 709), so a cancel that lands during `analyzing` cannot influence it. `pause_opt_job` explicitly accepts `analyzing` as a pausable status (research.py:652).
```

**Failure scenario:**
A 12-window option-aware WFO finishes its windows and enters `analyzing`. The user presses Stop (or Pause — the UI offers both for `analyzing`, Optimizer.jsx:1434,1459-1467). `cancelled: True` / `paused: True` are written to the document and then never read. The job proceeds through the full walk_forward on the final params plus a multi-million-row option-candle load and pairing sim — minutes to tens of minutes of unstoppable work — and then writes `status: "done"`. The user's Stop appears to have done nothing, and a job they explicitly cancelled is recorded as a clean completion (so `apply-as-preset` treats it as a normal finished result via research.py:705).

**Suggested fix:**
Mirror the single-run optimizer's O13 pattern in wfo.py: define an analyze-stage `_should_stop()` that reads `_job_control(job_id)`, check it before `_save_best_as_backtest` (wfo.py:803) and before `_pair_oos_with_options` (wfo.py:816), skipping the expensive tail and recording `option_oos = None` with a `stopped` marker. Fold the refreshed cancel flag into `final_status` at wfo.py:826 so a mid-analyze Stop terminates as `cancelled`, and honour a mid-analyze Pause by writing `status: "paused"` rather than `done`.

## [21] MEDIUM — An all-trials-failed job persists -Infinity into best_so_far.value, and FastAPI's allow_nan=False then 500s the whole job-history endpoint

- dim: `job-lifecycle`
- site: `backend/app/optimizer.py:1515`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
`best_so_far["value"]` starts at `-float("inf")` (optimizer.py:1406). Three progress writes round it with no finite guard:
```python
                    await _update_job(job_id, {
                        "n_trials_completed": completed,
                        "best_so_far": {"value": round(best_so_far["value"], 4), "params": best_so_far["params"], "metrics": best_so_far["metrics"], "trial_num": best_so_far["trial_num"]},
                    })
```
(optimizer.py:1513-1516; identical unguarded expressions at 1468 and 1570). `round(float("-inf"), 4)` returns `-inf`, which BSON stores as an IEEE double. The value stays `-inf` whenever no trial ever completed successfully, because the update is gated on `study.best_value` succeeding:
```python
                except Exception:
                    study_best_val = None
                    study_best_params = {}
                if study_best_val is not None and study_best_val > best_so_far["value"]:
```
(optimizer.py:1495-1498) — `study.optimize(..., catch=(Exception,))` marks raising trials FAIL, so `study.best_value` itself raises. Note the codebase already knows this is unsafe: `_flush_trial_log` writes `round(best_so_far["value"], 4) if best_so_far["value"] > -1e8 else None` (optimizer.py:647) and so does the terminal patch (optimizer.py:1859) — only the three progress writes omit the guard. Serialization does not sanitize it: `serialize_doc` passes floats through untouched (backend/app/db.py:27-35), and the installed FastAPI 0.116.1 renders with `json.dumps(content, ensure_ascii=False, allow_nan=False, ...)` (verified via `inspect.getsource(fastapi.responses.JSONResponse.render)`) which raises `ValueError: Out of range float values are not JSON compliant`. `best_so_far` is NOT in the list projection: `db.optimization_jobs.find({}, {"_id": 0, "param_space": 0, "top_n_alternatives": 0, "heatmap": 0, "robustness": 0, "rerank": 0, "trial_log": 0, "wfo": 0, "wfo_windows": 0, "wfo_oos_trades": 0})` (research.py:610-613).
```

**Failure scenario:**
A user posts `param_overrides: {"rsi_length": {"min": 30, "max": 5}}` (or a strategy bug makes every `evaluate()` raise). Every trial fails inside `study.optimize(catch=(Exception,))`, `study.best_value` keeps raising, and at `completed == 5` the job document gets `best_so_far.value = -Infinity`. From that moment `GET /optimize/jobs/{id}` 500s, so the UI's polling loop dies mid-run; worse, `GET /optimize/jobs` also 500s because the list projection keeps `best_so_far` — the entire Optimizer job-history table is permanently broken for every job, not just this one, until the poisoned document is deleted out of band.

**Suggested fix:**
Apply the guard that already exists elsewhere to all three progress writes (optimizer.py:1468, 1515, 1570): `"value": round(best_so_far["value"], 4) if best_so_far["value"] > -1e8 else None` — ideally by extracting the existing `_flush_trial_log` shaping into one `_best_so_far_doc(best_so_far)` helper used by every write site. Belt-and-braces: make `serialize_doc` (db.py:27) map non-finite floats to `None`, so no future write can 500 a whole collection endpoint.

## [22] MEDIUM — A concurrent optimizer job's fork pool is torn down by an unrelated job's finally block, failing the running job

- dim: `job-lifecycle`
- site: `backend/app/optimizer.py:1576`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
The optimizer's parallel branch shuts the module-global pool down unconditionally, even when `start_pool` handed it nothing:
```python
            pool = start_pool(raw_df, _workers)   # None -> concurrent parallel job active -> sequential in-process
            try:
                ...
            finally:
                shutdown_pool()
```
(optimizer.py:1521, 1575-1576). `start_pool` returns None when another job owns the pool (`if _POOL is not None: return None  # another parallel job owns the pool -> caller falls back to sequential` — parallel_eval.py:139-140), and `shutdown_pool` has no ownership check: `if _POOL is not None: _POOL.shutdown(cancel_futures=True); _POOL = None` (parallel_eval.py:150-156). wfo.py guards against exactly this and states the optimizer already does too — incorrectly:
```python
            # Only tear down a pool THIS job started. shutdown_pool() acts on the
            # module-global pool, so an unconditional call from a sequential job
            # (pool=None) would kill a CONCURRENT parallel job's pool — mirrors the
            # single-run optimizer which shuts down only inside its parallel branch.
            if use_parallel:
                shutdown_pool()
```
(wfo.py:783-788). The optimizer's `finally` also fires on the pause path, which returns from inside the `try` (`if pf and await _maybe_pause(): return` — optimizer.py:1529-1530).
```

**Failure scenario:**
On the Linux container (where `fork_available()` is True — parallel_eval.py:30-31), job A starts with `opt_workers=4` and owns `_POOL`. Job B starts with `opt_workers=4`; `start_pool` returns None so B silently runs sequentially in-process. B early-stops (or the user pauses it) after 60 trials; B's `finally: shutdown_pool()` calls `_POOL.shutdown(cancel_futures=True)` on **A's** pool. A's in-flight `[f.result() for f in futs]` (parallel_eval.py:184) raises BrokenProcessPool, which propagates out of `asyncio.to_thread(parallel_backtest, ...)` (optimizer.py:1535) and out of the while loop to the outer handler at optimizer.py:1904, marking the healthy job A `status: "failed"` mid-search. Its trial_log is only current to the last 50-trial flush.

**Suggested fix:**
Make the optimizer's teardown ownership-scoped exactly as wfo.py does: capture `use_parallel = pool is not None` after `start_pool` and guard the `finally` with `if use_parallel: shutdown_pool()`. Harden `parallel_eval.shutdown_pool` to take the pool object it is allowed to close (`def shutdown_pool(pool=None)`) and no-op when `_POOL is not pool`, so no caller can close a pool it did not create.

## [23] MEDIUM — A pinned (fixed) parameter override is dropped from best_params, so the saved best runs a different value than the trials did

- dim: `job-lifecycle`
- site: `backend/app/optimizer.py:1494`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
`_suggest` injects pinned params into the trial's real params without telling Optuna:
```python
    for name, info in space.items():
        if "fixed" in info:
            out[name] = info["fixed"]
            continue
```
(optimizer.py:349-352) — so the backtest genuinely runs with `info["fixed"]`. But the bayesian/genetic paths take the best from Optuna, which only knows suggested dimensions:
```python
                    study_best_val = study.best_value
                    study_best_params = dict(study.best_params)
                ...
                    best_so_far = {
                        "value": study_best_val, "params": study_best_params,
```
(optimizer.py:1493-1500; the parallel branch does the same at 1550 and 1557). The grid branch by contrast keeps the full set: `best_so_far = {"value": val, "params": dict(params), ...}` (optimizer.py:1455). Anything missing from `best_params` silently reverts to the schema default downstream:
```python
        out = self.default_params()
        if override:
            for k, v in override.items():
                if k in out or k in SHARED_INDICATOR_PARAM_KEYS:
                    out[k] = v
        return out
```
(backend/app/strategies/base.py:104-109), and `_save_best_as_backtest` re-derives from it (`merged = strategy.merged_params(best_params)` — optimizer.py:697) as does the preset (`"params": best_params` — research.py:715). `_build_param_space` honours a caller-supplied pin: `if "fixed" in ov: info["fixed"] = ov["fixed"]` (optimizer.py:313-314), and the request schema accepts it (`param_overrides: Dict[str, Any]` — backend/app/schemas.py:185).
```

**Failure scenario:**
A client posts `param_overrides: {"lots": {"fixed": 5}}` (schema default 2) with `method: "bayesian"`. All 150 trials are scored with `lots=5`. `study.best_params` never contains `lots`, so the persisted `best_params` omits it; `_save_best_as_backtest` re-runs with `merged_params(best_params)` -> `lots=2` and stores that as the authoritative "Optimized ·" backtest run, and apply-as-preset writes a preset with `lots` absent -> `lots=2` everywhere downstream. The optimizer's reported best is therefore not reproducible from the artifact it saved: the ranking used 5 lots, every displayed and deployed number uses 2. The same mechanism drops any pinned threshold, so re-running the saved preset cannot regenerate the optimizer's numbers. (The UI currently exposes only min/max — Optimizer.jsx:1341-1350 — so today this is API-reachable only, but the pin path is fully wired and validated.)

**Suggested fix:**
Never round-trip the best through `study.best_params` alone. Either (a) look the winning trial's full param dict up out of `trial_history` (which `_suggest` populated with fixed values included) instead of using `study.best_params`, or (b) overlay the pins after the fact: `params = {**{k: v["fixed"] for k, v in space.items() if "fixed" in v}, **dict(study.best_params)}` at optimizer.py:1494 and 1550. Add a test asserting that with `param_overrides={"<numeric>": {"fixed": X}}` and X != default, the persisted `best_params["<numeric>"] == X` for bayesian, grid and genetic alike.

## [24] MEDIUM — objective="profit_factor": a config with ZERO losing trades scores 0.0 — the worst possible value — so the optimizer actively steers away from the strictly-profitable configs

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:165`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
optimizer.py:164-166:
```
    if objective == "profit_factor":
        v = metrics.get("profit_factor")
        return float(v) if v is not None else 0.0
```
`profit_factor` is None precisely when there is no losing trade — backtest.py:283-297:
```
    losses = pnls[pnls <= 0]
    gross_loss = float(losses.sum()) if len(losses) > 0 else 0.0
    ...
        "profit_factor": round(gross_profit / abs(gross_loss), 3) if gross_loss < 0 else None,
```
Since PF is a ratio of non-negative magnitudes, every real value is `>= 0.0`, so mapping the all-wins case to `0.0` puts the best possible outcome at the bottom of the ranking. The premium path in the same file handles this correctly — optimizer.py:497-502:
```
    if gross_loss > 0:
        profit_factor = round(gross_win / gross_loss, 3)
    elif gross_win > 0:
        profit_factor = 999.0  # only wins: large-but-finite (inf breaks JSON/ranking)
```
`profit_factor` is a user-selectable objective: Optimizer.jsx:72 `{ id: "profit_factor", name: "Maximize Profit Factor", desc: "Gross profit / |gross loss|" },`.
```

**Failure scenario:**
objective="profit_factor", min_trades=10. Trial A produces 12 trades, all winners (a tight-target scalper on a trending window) → `profit_factor` is None → scored 0.0. Trial B produces 40 trades with gross profit 100 and gross loss -200 → PF 0.5 → scored 0.5. TPE ranks B above A, seeds its next generation around B's region, and the final `best_params` is the money-losing config. The flawless config is also reported at the bottom of Top Alternatives with PF blank.

**Suggested fix:**
Mirror the premium branch: return a large-but-finite score when there are no losses and at least one win (e.g. `999.0`), and only fall back to `0.0` when there is neither profit nor loss. Consider returning `_DISQUALIFY` instead of `0.0` for genuinely undefined cases so they cannot outrank a real PF of 0.

## [25] MEDIUM — _robustness_score counts no-op perturbations as passes: integer rounding and bound clamping make ±10/20% shifts re-evaluate the identical config, inflating the ROBUST verdict

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:544`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
optimizer.py:543-561:
```
        base_v = float(best_params[name])
        for pct in (-0.20, -0.10, 0.10, 0.20):
            t_v = base_v * (1 + pct)
            t_v = max(float(info["min"]), min(float(info["max"]), t_v))
            if info["type"] == "int":
                t_v = int(round(t_v))
            test_params = dict(best_params)
            test_params[name] = t_v
            metrics, _ = evaluate_fn(test_params)
            val = obj_fn(metrics)
            ok = val >= base_val * 0.85 and metrics.get("trade_count", 0) >= 5
```
Nothing checks that `t_v != base_v`. Verified numerically in this repo's Python with `min=1, max=30`: base=1 → perturbations `[1, 1, 1, 1]` (4 of 4 identical to base); base=2 → `[2, 2, 2, 2]` (4 of 4 identical); base=3 → `[2, 3, 3, 4]` (2 of 4 identical). Clamping does the same on a bound: for a float param with `max=95` and an optimum at 95, `[max(40.0, min(95.0, 95*(1+p))) for p in (-0.2,-0.1,0.1,0.2)]` → `[76.0, 85.5, 95.0, 95.0]` — the two upward shifts are the base config. TPE routinely lands on bounds and on small integers (confluence_scalper.py:21 `"cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 5}`, :20 `"signal_threshold": {"type": "int", "min": 40, "max": 95, "default": 62}`). The score is published as a verdict: Optimizer.jsx:1987-1988 `const color = score >= 70 ? "text-success" : ...; const label = score >= 70 ? "ROBUST" : score >= 50 ? "MODERATE" : "FRAGILE";`
```

**Failure scenario:**
Best params come back with `cooldown_bars=2` and `signal_threshold=95` (the declared max). `cooldown_bars` contributes 4 perturbations that are all exactly 2 — the base config re-run — and `signal_threshold` contributes 2 clamped-to-95 no-ops. Those 6 of ~28 tests pass by construction (`val == base_val`), so a strategy that is genuinely fragile can be scored 70+ and labelled ROBUST. The perturbation table shown to the user lists rows like `cooldown_bars / -20% / 2 / OK` that look like evidence of stability but are the unperturbed config.

**Suggested fix:**
Skip (do not count) any perturbation whose resolved value equals the base value: after clamping/rounding, `if t_v == base_v: continue`. For integer params use an absolute step of at least ±1 within bounds instead of a multiplicative shift, and record skipped dimensions in the payload so the UI can say "n params could not be perturbed" rather than crediting them.

## [26] MEDIUM — _robustness_score's pass test inverts when the best objective is negative: `val >= base_val * 0.85` demands a 15% IMPROVEMENT, so neg_max_dd runs are labelled FRAGILE by construction

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:553`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
optimizer.py:553 `            ok = val >= base_val * 0.85 and metrics.get("trade_count", 0) >= 5`
The intent is documented as a tolerance band — optimizer.py:527 `"""Perturb each numeric param by ±10% and ±20%; count fraction that stay 'profitable'.` and Optimizer.jsx:1996 `% of ±10/20% param perturbations that stayed within 85% of best objective`. Multiplying by 0.85 only relaxes the bar for positive values. Verified: base 2.0 → threshold 1.7 (relaxed); base -2.0 → threshold -1.7 (TIGHTER than base — the perturbation must beat the base by 15%).
The `neg_max_dd` objective is always `<= 0` by construction — optimizer.py:176-177:
```
    if objective == "neg_max_dd":
        return -abs(float(metrics.get("max_dd_pts", 0) or 0))
```
and it is user-selectable: Optimizer.jsx:75 `{ id: "neg_max_dd", name: "Minimize Max Drawdown", desc: "Stable equity curve" },`. The same inversion hits any run whose best `sharpe` or `risk_adjusted` is negative (a losing-but-least-bad winner, which is common when guards force a minimum trade count).
```

**Failure scenario:**
objective="neg_max_dd", best trial has max_dd_pts = -120 → base_val = -120, threshold = -102. A perturbation that reproduces the base drawdown (-120) fails; only a perturbation that shrinks drawdown below 102 pts passes. Since the best trial is by definition the minimum-drawdown config, essentially every perturbation fails, `score` collapses toward 0, and the card shows a red "FRAGILE" verdict for the most stable configuration the search found. The same happens to a `sharpe`-objective run whose winner has sharpe -0.4 (threshold -0.34).

**Suggested fix:**
Define the tolerance band on the magnitude of degradation rather than a signed multiplication, e.g. `tol = abs(base_val) * 0.15; ok = val >= base_val - tol and ...`. Guard the degenerate `base_val == 0` and `base_val <= _DISQUALIFY` cases explicitly instead of letting the multiplication decide.

## [27] MEDIUM — best_so_far is never re-persisted after the option-rerank promotion, so the UI's "Best so far" card shows the SPOT-stage winner's params/metrics while the headline ₹ and Apply-as-preset use a different config

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:1737`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
The promotion replaces `best_so_far` only in memory — optimizer.py:1735-1750:
```
                if survivors:
                    best = survivors[0]
                    best_so_far = {
                        "value": (best["survival"].get("calmar") if survival.objective == "calmar"
                                  else best["option_pnl_value"]),
                        "params": best["params"],
```
(and identically at :1774-1786 for the non-survival branch). The finish patch writes `best_params`/`best_metrics` but NOT `best_so_far` — optimizer.py:1853-1862 lists `"status"`, `"finished_at"`, `"n_trials_completed"`, `"evaluation_mode"`, `"best_params"`, `"best_value"`, `"best_metrics"`, `"best_backtest_run_id"`, … with no `best_so_far` key. The only writers of that field are inside the trial loop (optimizer.py:645-650, 1466-1469, 1512-1516, 1567-1571). The UI renders the stale document: Optimizer.jsx:1425 `const bsf = job.best_so_far || {};`, :1568 `{bsf.params && Object.keys(bsf.params).length > 0 && (`, :1602-1603 `{Object.entries(bsf.params).map(([k, v]) => (`, :1607 `{isOptionRerank && <div ...>spot backtest metrics of the best config (not the option trade)</div>}` followed by `bsf.metrics` KPIs — all next to a headline computed from the PROMOTED candidate (:1554 `const optionPnl = job.best_option_pnl_value ?? job.best_metrics?.option_pnl_value ?? ...`). Apply-as-preset also uses the promoted config: research.py:707 `best_params = job.get("best_params") or (job.get("best_so_far") or {}).get("params")`.
```

**Failure scenario:**
An option_rerank run promotes candidate #17 (highest paired option ₹) over the spot-stage winner #83. The Best-so-far card shows #83's params (ema_fast=11, rsi_bull_thr=57, …) and #83's spot metrics under a green "₹41,200 promoted option ₹" headline that belongs to #17. The user reads and records #83's parameters, then clicks "Apply as preset" — which saves #17's parameters. The preset they deploy is a config whose numbers were never displayed.

**Suggested fix:**
Include the promoted `best_so_far` in the finish patch (`finished["best_so_far"] = {"value": ..., "params": ..., "metrics": ..., "trial_num": ...}`) so the stored document matches `best_params`, or have the UI read `job.best_params`/`job.best_metrics` in preference to `job.best_so_far` once the job is finished.

## [28] MEDIUM — Parallel trial path attaches the PREVIOUS best's metrics to the NEW best params whenever the search space contains a pinned dimension, because study.best_params omits fixed params

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:1555`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
optimizer.py:1548-1558:
```
                    try:
                        study_best_val = study.best_value
                        study_best_params = dict(study.best_params)
                    except Exception:
                        ...
                    if study_best_val is not None and study_best_val > best_so_far["value"]:
                        best_metrics = next((t["metrics"] for t in reversed(trial_history)
                                             if t["params"] == study_best_params), best_so_far["metrics"])
                        best_so_far = {"value": study_best_val, "params": study_best_params,
                                       "metrics": best_metrics, "trial_num": completed - 1}
```
`trial_history` stores the FULL param dict produced by `_suggest`, which includes pinned dimensions without ever calling `trial.suggest_*` — optimizer.py:349-352:
```
    for name, info in space.items():
        if "fixed" in info:
            out[name] = info["fixed"]
            continue
```
(the appended record is `trial_history.append({"params": params, ...})` at :1546, with `params` from `param_list = [_suggest(t, space) for t in trials]` at :1533). Optuna's `study.best_params` contains only suggested distributions, so it lacks those keys and `t["params"] == study_best_params` is False for every record — `next(...)` then returns the default, the stale `best_so_far["metrics"]`.
Pinned dimensions are the norm, not an edge case: `_build_param_space` pins any numeric param lacking both bounds (optimizer.py:325-328 `elif t in ("int", "float") and not ("min" in info and "max" in info): ... info["fixed"] = info.get("default")`) and every sizing knob (:323-324 with `NON_ALPHA_PARAM_NAMES` at :202-205), and the AI compiler emits an unbounded `cooldown_bars` for authored ordinary strategies — ai/compiler.py:464 `f'        "cooldown_bars": {{"type": "int", "default": {spec.cooldown_bars!r}}},\n'`.
```

**Failure scenario:**
opt_workers=4, bayesian, on an AI-authored ordinary strategy whose schema includes a bare `cooldown_bars` (pinned). Batch 6 produces a new global best. `study_best_params` has every searched key but no `cooldown_bars`; no `trial_history` record matches, so `best_metrics` silently becomes the metrics of the previous best from batch 3. The job's `best_metrics` (trade_count, win_rate, PF, max_dd_pts, ce/pe split) therefore describes a different configuration than `best_params`, and the KPI card, the direction-split guard display and the trust scorecard all read that mismatched dict. Separately, the reported `best_params` omits the pinned dimension entirely while `top_n_alternatives` (built from `trial_history` at :1862) includes it, so the same run reports two different param shapes.

**Suggested fix:**
Compare on the searched subset only, e.g. `if {k: t["params"][k] for k in study_best_params if k in t["params"]} == study_best_params`, or better: capture the metrics at `tell` time by tracking the max `val` inside the same `for trial, params, (metrics, _m) in zip(...)` loop (as wfo.py:729-731 already does with `if val > window_best["value"]: window_best.update(...)`), and merge the pinned dimensions back into `best_so_far["params"]` so it matches what the trials actually ran.

## [29] MEDIUM — risk_adjusted/neg_max_dd mix units: max_dd_pts is index POINTS on the ordinary path and RUPEES on the premium path, and max(1.0, dd/100) zeroes the drawdown penalty for any ordinary run under 100 points

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:180`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
optimizer.py:176-181:
```
    if objective == "neg_max_dd":
        return -abs(float(metrics.get("max_dd_pts", 0) or 0))
    # risk_adjusted (default)
    sharpe = float(metrics.get("sharpe") or 0)
    dd = abs(float(metrics.get("max_dd_pts") or 1))
    return sharpe / max(1.0, dd / 100.0)
```
On the ordinary path `max_dd_pts` is a cumulative INDEX-POINT drawdown — backtest.py:287-290,300:
```
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    max_dd = float(dd.min()) if len(dd) else 0.0
```
… `"max_dd_pts": round(max_dd, 2),`. So for any run whose worst peak-to-trough is `<= 100` points, `dd/100 <= 1` and `max(1.0, ...)` returns exactly 1.0: `risk_adjusted` is byte-identical to plain `sharpe` and the drawdown term contributes nothing — while the UI advertises it as a trade-off (Optimizer.jsx:69 `{ id: "risk_adjusted", name: "Risk-Adjusted Return (default)", desc: "Sharpe / drawdown — balanced quality" },`). It is also the default objective (optimizer.py:1169 `objective = payload.get("objective", "risk_adjusted")`).
The premium path substitutes a RUPEE value into the same key — optimizer.py:517-520:
```
        # NOT a true unit match: rupee max-drawdown substituted where the spot
        # formula expects index points ...
        "max_dd_pts": abs(float(port.get("max_drawdown_value", 0.0) or 0.0)),
```
so the identical objective name divides sharpe by ~1 for a spot run and by ~200 for a premium run with a ₹20,000 drawdown. Both are surfaced through one field with one label: optimizer.py:1859 `"best_value": round(...)` rendered at Optimizer.jsx:1589 next to `obj={job.objective}` (Optimizer.jsx:1605-ish header at :1604 `obj={job.objective}`).
```

**Failure scenario:**
Two candidate configs on confluence_scalper: A has sharpe 1.8 with max_dd_pts -95, B has sharpe 1.75 with max_dd_pts -40. Both drawdowns are under 100 points, so both penalties are exactly 1.0 and `risk_adjusted` picks A — the config with more than twice the drawdown — even though the objective's stated purpose is to penalise drawdown. Separately, a user comparing job history sees `obj=risk_adjusted best_value=1.80` for the spot strategy and `obj=risk_adjusted best_value=0.009` for the premium strategy and reads the premium one as far worse, when the two numbers are divided by different physical quantities.

**Suggested fix:**
Make the denominator unit-aware and scale-free — e.g. normalise drawdown by the run's own gross P&L or equity (a Calmar-style `total_pnl / |max_dd|`) instead of the hardcoded `/100` points constant, and emit an explicit `objective_units` field (`"points"` vs `"rupees"`) on the job so `best_value` is never compared across incommensurable paths. At minimum, stop clamping at 1.0 (use `max(eps, dd/scale)`) so drawdown always contributes.

## [30] LOW — Early stop is invisible: the ceiling n_trials is reported as the trial count and stamped into the saved run's overfit evidence

- dim: `job-lifecycle`
- site: `backend/app/optimizer.py:1583`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
The backend records the distinction honestly:
```python
        await _update_job(job_id, {"status": "analyzing", "n_trials_completed": completed,
                                   "early_stopped": early_stopped, "stopped_at_trial": completed,
                                   "trials_ceiling": n_trials})
```
(optimizer.py:1583-1585). Grep over frontend/src finds ZERO references to `early_stopped`, `trials_ceiling` or `stopped_at_trial`, so the UI renders only `{job.n_trials_completed || 0} / {job.n_trials_total || 0} trials` with `pct = Math.round((job.n_trials_completed / job.n_trials_total) * 100)` (frontend/src/pages/Optimizer.jsx:1425, 1502-1519) — `n_trials_total` being the ceiling set at create time (optimizer.py:1384, 1929). Separately, the ceiling rather than the actual count is fed to both artifacts: `_save_best_as_backtest(..., n_trials=n_trials)` (optimizer.py:1842) stores `**({"n_trials": int(n_trials)} if n_trials else {})` on the backtest run (optimizer.py:756), and the trust verdict gets `evidence={... "n_trials": n_trials ...}` (optimizer.py:1899-1901), which `deployment_quality` turns into the deflated-Sharpe penalty and the literal message text ("This result was the best of {n_trials} optimizer trials over {trade_count} trades" — deployment_quality.py:328) and `expected_max_sharpe(n_trials, trade_count)` (deployment_quality.py:315-338).
```

**Failure scenario:**
With the UI's 150-trial default, `effective_warmup_patience(n_trials=150, warmup=200, patience=200)` returns warmup=50, patience=30, so the default-ON auto-stop fires around trial 50-80. The job then shows a green DONE badge over a 33%-filled progress bar reading "50 / 150 trials" with no explanation — indistinguishable from a truncated or broken run, and users are likely to re-run it. Meanwhile the saved backtest_run claims `n_trials: 150` and the trust scorecard computes the selection-bias penalty against 150 draws when only 50 occurred, so its "best of 150 trials" narrative is factually wrong (penalty direction is conservative, but the stated fact is not).

**Suggested fix:**
Surface the fields that already exist: when `job.early_stopped` is true, render a line such as "Auto-stopped at trial {stopped_at_trial} of {trials_ceiling} — no significant improvement for {patience} trials" and base `pct` on `stopped_at_trial / stopped_at_trial` (i.e. 100%) so a converged run does not read as incomplete. Pass `completed` rather than `n_trials` to `_save_best_as_backtest` (optimizer.py:1842) and to the `evidence` dict (optimizer.py:1901), keeping the ceiling as a separate `trials_ceiling` field so the deflated-Sharpe narrative quotes the number of draws that actually happened.

## [31] LOW — objective="net_pnl_inr" ignores option_config.lots and converts SPOT index points at the option lot size, so the "Net P&L (₹)" it maximises is not the rupee P&L of the run being validated

- dim: `objective-metric-integrity`
- site: `backend/app/optimizer.py:173`
- verified by a verifier agent: False

**Evidence (agent-quoted, UNVERIFIED):**
```
optimizer.py:169-173:
```
    if objective == "net_pnl_inr":
        # Net rupee P&L = net points (already cost-adjusted when costs_enabled)
        # × lot size. This is an honest index-point→rupee conversion; it does
        # not model option premium decay (see option-aware mode, future slice).
        return float(metrics.get("total_pnl_pts", 0) or 0) * float(lot_size)
```
`lot_size` is the contract lot from the newest expiry (optimizer.py:1258-1266, `_DEFAULT_LOT_SIZE = {"NIFTY": 75, "BANKNIFTY": 35, "SENSEX": 20}` at :122). The number of lots the run actually simulates lives in a different dict and is never passed to the objective: optimizer.py:1019 `lots = int(option_cfg.get("lots") or 1)` inside `_option_rerank`, and optimizer.py:867 `lots = int(option_cfg.get("lots") or 1)` inside `_survival_eval_oos`. `obj` is built with only `lot_size` (optimizer.py:1343-1347 `return _objective_value(metrics, objective, lot_size=lot_size, min_trades=min_trades, min_direction_share=min_direction_share,)`). The UI labels it as the run's rupee P&L: Optimizer.jsx:70 `{ id: "net_pnl_inr", name: "Maximize Net P&L (₹)", desc: "Net rupee P&L = net points × lot size (enable costs)" }`.
```

**Failure scenario:**
User selects "Maximize Net P&L (₹)" with option_config.lots=5 on NIFTY. Stage 1 reports and ranks on `total_pnl_pts * 75` — one lot of index points — while every option simulation in Stage 2 and in the survival gate uses 5 lots. The headline rupee objective is 5× smaller than the run's own rupee exposure, and because it is a spot-point conversion it can be positive while the paired option net (`option_pnl_value`, the number Stage 2 ranks on) is negative once premium decay and spread are applied. Trials are therefore ordered by a quantity that is neither the displayed option ₹ nor the deployed size.

**Suggested fix:**
Pass the effective contract multiplier into `_objective_value` (`lot_size * max(1, option_cfg.get("lots", 1))`) so the number matches the simulated size, and rename/relabel the objective to make the spot-proxy nature explicit (e.g. "Net P&L (₹, spot-equivalent)") — or disallow `net_pnl_inr` in `evaluation_mode="option_rerank"`, where `option_pnl_value` is the real rupee quantity.
