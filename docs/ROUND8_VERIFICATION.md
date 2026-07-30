# Round-8 verification — recovered from workflow journal

Workflow `wf_85678c6e-c17`. Recovered 18 verdicts, 0 new findings.

## [4] CONFIRMED — HIGH — A single raising grid combo still kills the whole job — O14's error record poisons the Top-N sort

- severity if real: HIGH

**Evidence:**
```
backend/app/optimizer.py:1472-1478 (grid branch) — I read it verbatim:
```
                except Exception as exc:
                    log.warning("grid trial %d raised (%s) — disqualified, continuing",
                                completed, exc)
                    trial_history.append({"params": params, "metrics": None,
                                          "objective_value": None, "error": str(exc)[:200]})
                    completed += 1
                    continue
```
The analyze stage then sorts that same list with no None filter — backend/app/optimizer.py:1686:
```
        sorted_trials = sorted(trial_history, key=lambda t: t["objective_value"], reverse=True)
```
(I re-read 1585-1690: nothing between the loop and this line scrubs or replaces the None record.) I ran the exact expression in this repo's python: `sorted([{'objective_value':1.23},{'objective_value':None}], key=lambda t: t['objective_value'], reverse=True)` -> `TypeError: '<' not supported between instances of 'float' and 'NoneType'`.
The TypeError is caught only by the whole-function handler, backend/app/optimizer.py:1998-2000:
```
    except Exception as e:
        log.exception(f"optimization {job_id} crashed")
        await _update_job(job_id, {"status": "failed", "error": str(e), "finished_at": ...})
```
so every good trial, best_params, the saved best backtest and all analyses are discarded. The poison persists across Resume: `_compact_trial` keeps the null score (optimizer.py:635 `"objective_value": t.get("objective_value"),`), resume rehydrates it (optimizer.py:1424-1425 `trial_history = list(rdoc.get("trial_log") or [])`), and `resume_optimization` explicitly allows `failed` (optimizer.py:2041 `if doc.get("status") not in ("paused", "interrupted", "failed"): return False`) — so Resume re-hits the identical sort. Note `_rebuild_study` DOES defend against None (optimizer.py:681-682 `v = float(v) if v is not None else _DISQUALIFY`), proving the None-score hazard was known at one site and missed at the sort. The only O14 test is source-grep only — tests/test_optimizer_robustness_contract.py:17-23 asserts the strings "disqualified, continuing" and '"metrics": None' are present; 
```

**Minimal fix:** At backend/app/optimizer.py:1686 make the sort total-order safe: `sorted_trials = sorted((t for t in trial_history if t.get("objective_value") is not None), key=lambda t: t["objective_value"], reverse=True)` (errored combos carry no score and are already recorded separately). Same guard needed on the grid importance fallback at optimizer.py:1701 (`vals = [... for t in trial_history if name in t["params"]]` feeds Nones into np.mean — currently masked by a bare `except Exception: pass`, which silently drops importance). Add a behavioural test that drives the grid branch with one raising combo and asserts a terminal non-failed status.

## [6] CONFIRMED — HIGH — Resume silently skips up to 49 trials it then counts as completed (grid: 49 combos never evaluated)

- severity if real: HIGH

**Evidence:**
```
Resume trusts the high-cadence counter over the low-cadence evidence — backend/app/optimizer.py:1424-1425:
```
            trial_history = list(rdoc.get("trial_log") or [])
            completed = int(rdoc.get("n_trials_completed") or len(trial_history))
```
The two are written 10x apart in every trial loop. Grid, optimizer.py:1525-1531:
```
                if completed % 5 == 0:
                    await _update_job(job_id, {"n_trials_completed": completed, "best_so_far": {...}})
                if completed % 50 == 0:
                    await _flush_trial_log(job_id, trial_history, best_so_far, completed)
```
Sequential bayesian, optimizer.py:1572-1578 (`if completed % 5 == 0 or completed == n_trials:` vs `if completed % 50 == 0:`) and parallel, optimizer.py:1593-1599 (`if (completed // 5) > (prior // 5) ...` vs `if (completed // 50) > (prior // 50):`) are identical in shape. `_flush_trial_log` writes both keys together (optimizer.py:643-651 includes `"n_trials_completed": completed`), so only the %5-only writes create the gap — up to 49.
A user pause is consistent because `_maybe_pause` flushes first (optimizer.py:1483 `await _flush_trial_log(job_id, trial_history, best_so_far, completed)`), but a server restart is not: backend/server.py:84-92 flips any `queued`/`running`/`analyzing` job to `interrupted` (a resumable state per optimizer.py:2041) purely by DB update, with no flush:
```
        reconciled = await db.optimization_jobs.update_many(
            {"status": {"$in": ["queued", "running", "analyzing"]}},
            {"$set": {"status": "interrupted", "paused": False, ...}},
        )
```
The resumed loops then skip by the inflated counter, not by the history: grid `for params in combos[completed:]` (optimizer.py:1493), sequential `for i in range(completed, n_trials)` (optimizer.py:1541), parallel `while completed < n_trials:` (optimizer.py:1584). So with `n_trials_completed: 95` and 50 persisted records, combos 50-94 are never evaluated, yet the job reports `n_trials_completed: 200` and status `done` (optimizer.py:1883-1886). The truncated pool also feeds Stage-2 finalist selection and top_n via `sorted_trials` (optimizer.py:1686), and `_rebuild_stud
```

**Minimal fix:** Never let the counter run ahead of the evidence. Smallest change: at backend/app/optimizer.py:1425 use `completed = min(int(rdoc.get("n_trials_completed") or 0), len(trial_history))` (falling back to `len(trial_history)`), so resume restarts from the last flushed record. Better: flush the trial log on the same 5-trial cadence (change each `% 50` / `// 50` guard at optimizer.py:1530, 1577, 1598 to the 5-trial one) so the two can never diverge, and/or write `n_trials_completed` only from inside `_flush_trial_log`. Add a test that persists `n_trials_completed=95` with a 50-record `trial_log` and asserts the resumed grid loop starts at combo 50.

## [9] CONFIRMED — HIGH — Grid search: one raising combo is recorded with objective_value=None, then the analyze stage sorts on it and the whole job dies with TypeError — defeating the explicit "must NOT crash the whole job" handler

- severity if real: HIGH

**Evidence:**
```
backend/app/optimizer.py:1469-1477 (grid loop) records a None objective:
                try:
                    metrics, merged = await asyncio.to_thread(evaluate, params)
                    val = obj(metrics)
                except Exception as exc:
                    log.warning("grid trial %d raised (%s) — disqualified, continuing",
                                completed, exc)
                    trial_history.append({"params": params, "metrics": None,
                                          "objective_value": None, "error": str(exc)[:200]})
                    completed += 1
                    continue

The analyze stage then sorts that same list with NO filter and NO try, at backend/app/optimizer.py:1652:
        sorted_trials = sorted(trial_history, key=lambda t: t["objective_value"], reverse=True)

Reproduced in this repo's Python:
  sorted([{'objective_value':1.5},{'objective_value':None}], key=lambda t: t['objective_value'], reverse=True)
  -> TypeError: '<' not supported between instances of 'float' and 'NoneType'

The only enclosing handler is the job-level one, backend/app/optimizer.py:1957-1959:
    except Exception as e:
        log.exception(f"optimization {job_id} crashed")
        await _update_job(job_id, {"status": "failed", "error": str(e), "finished_at": datetime.now(timezone.utc).isoformat()})
so every successful trial's results (best_params, importance, heatmap, robustness, the saved run) are discarded.

The second crash site is real too — backend/app/rerank_select.py:39:
        if t.get("objective_value", DISQUALIFY) <= DISQUALIFY:
The key EXISTS with value None, so .get returns None, and `None <= -1e9` raises (verified: TypeError: '<=' not supported between instances of 'NoneType' and 'float').

Unrecoverable-on-resume is also confirmed: backend/app/optimizer.py:631-637 `_compact_trial` keeps the poison record verbatim — `"objective_value": t.get("objective_value"),` — so the resumed run (which skips the already-counted combo via `combos[completed:]`, optimizer.py:1458) rebuilds a trial_history that still contains None and dies at :1652 again.
```

**Minimal fix:** At backend/app/optimizer.py:1652 filter the failed records before sorting: `sorted_trials = sorted((t for t in trial_history if t.get("objective_value") is not None), key=lambda t: t["objective_value"], reverse=True)`. Also coerce None in the fallback-importance loop (optimizer.py:1673-1682, which reads t["objective_value"] into np.mean — inside a try, so it only silently loses importance) and make rerank_select.py:39 None-safe: `v = t.get("objective_value"); if v is None or v <= DISQUALIFY: continue`.

## [10] CONFIRMED — HIGH — min_trades guards the SPOT trade count only; option-rerank promotes any candidate with a single PAIRED trade, so a statistically empty config becomes the reported best

- severity if real: HIGH

**Evidence:**
```
The guard reads whatever `trade_count` Stage-1 metrics carry — backend/app/optimizer.py:145-151:
    tc = int(metrics.get("trade_count", 0) or 0)
    if tc == 0:
        return _DISQUALIFY  # no trades at all
    if min_trades and tc < min_trades:
        return _DISQUALIFY  # statistically meaningless sample

Promotion in option_rerank mode (survival OFF, the default) requires only ONE paired trade — backend/app/optimizer.py:1801-1812:
            elif ranked and ranked[0]["paired_trade_count"] > 0:
                best = ranked[0]
                best_so_far = {
                    "value": best["option_pnl_value"],
                    "params": best["params"],

and the ordering that produces ranked[0] only uses the boolean, backend/app/optimizer.py:1183:
    ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)

`_option_rerank` records the count but filters on nothing — backend/app/optimizer.py:1161-1171:
            "option_pnl_value": float(m.get("total_option_pnl_value", 0.0) or 0.0),
            ...
            "paired_trade_count": int(m.get("paired_trade_count", 0) or 0),

That promoted config is then saved and published unconditionally (optimizer.py:1861 `if best_so_far["params"]:` -> `_save_best_as_backtest`, optimizer.py:1888 `"best_params": best_so_far["params"],`).

The codebase documents the gap itself, backend/app/survival.py:21-23:
# A tail statistic (ruin probability) needs more than the spot min_trades=10
# guard (which counts SPOT trades); this counts PAIRED rupee trades.
MIN_TRADES_FOR_RUIN = 100
and that gate is off by default — backend/app/survival.py:36 `    enabled: bool = False`.

One correction to the auditor's scope: for PREMIUM-NATIVE strategies the guard IS paired-aware, because backend/app/optimizer.py:506 remaps the metric — `"trade_count": int(m.get("paired_trade_count", 0) or 0),`. The defect is real for the ORDINARY path only (run_backtest spot metrics via optimizer.py:431-432).
```

**Minimal fix:** In the option_rerank block, filter `ranked` by the paired count before both promotion paths: after the `_option_rerank` call, `_lowpair = [r for r in ranked if r["paired_trade_count"] < min_trades]` and use `ranked_ok = [r for r in ranked if r["paired_trade_count"] >= min_trades]` for the survivor sort (optimizer.py:1707-1735) and for the `elif ranked and ranked[0]["paired_trade_count"] > 0:` promotion at optimizer.py:1801 (keep the full `ranked` for the displayed table). Add `"dropped_low_pairing": len(_lowpair)` to `rerank_info` (optimizer.py:1818-1827) so a low-pairing sweep is visible instead of silently promoted.

## [11] CONFIRMED — HIGH — When every trial fails the guard rails the optimizer still promotes, saves and lets the user apply a disqualified config — and the dedicated "no usable result" banner never fires

- severity if real: HIGH

**Evidence:**
```
The sentinel is -1e9 (backend/app/rerank_select.py:13 `DISQUALIFY = -1e9`, imported as _DISQUALIFY at optimizer.py:57) and it beats the -inf seed at backend/app/optimizer.py:1432:
            best_so_far = {"value": -float("inf"), "params": {}, "metrics": {}, "trial_num": -1}

All three trial loops overwrite it with the disqualified candidate — grid at optimizer.py:1480:
                if val > best_so_far["value"]:
                    best_so_far = {"value": val, "params": dict(params), "metrics": metrics, "trial_num": completed}
sequential bayesian at optimizer.py:1524 and the parallel path at optimizer.py:1580, both:
                if study_best_val is not None and study_best_val > best_so_far["value"]:
(-1e9 > -inf is True, and study.best_params is non-empty because the trials COMPLETED with value -1e9 rather than failing.)

Non-empty params then trigger the save, backend/app/optimizer.py:1861-1866:
        if best_so_far["params"]:
            best_merged = strategy.merged_params(best_so_far["params"])
            df_best = get_enriched(best_merged)
            best_backtest_run_id = await _save_best_as_backtest(
and it is published as the answer while only the headline is blanked, optimizer.py:1888-1890:
            "best_params": best_so_far["params"],
            "best_value": round(best_so_far["value"], 4) if best_so_far["value"] > -1e8 else None,
            "best_metrics": best_so_far["metrics"],

The UI guard is keyed on params, not value — frontend/src/pages/Optimizer.jsx:1436-1437:
  const hasBest = (bsf.params && Object.keys(bsf.params).length > 0)
    || (isWfo && job.best_params && Object.keys(job.best_params).length > 0);
so the banner written for exactly this case is skipped, Optimizer.jsx:1559-1561:
      {(finished || cancelled) && !hasBest && (
        <div ... data-testid="opt-no-result">
          No trial produced a usable result — every candidate either took no trades or was disqualified by the guard rails.
and the trophy card renders instead (Optimizer.jsx:1568 `{bsf.params && Object.keys(bsf.params).length > 0 && (`) with real-looking bsf.metrics and a "—" objective (Optimizer.jsx:214 `const fmtBest = (v) => (v == null || v <= -1e8)
```

**Minimal fix:** Require the value to clear the sentinel before promoting: change optimizer.py:1480 to `if val > _DISQUALIFY and val > best_so_far["value"]:` and optimizer.py:1524 / 1580 to `if study_best_val is not None and study_best_val > _DISQUALIFY and study_best_val > best_so_far["value"]:`. best_so_far then stays `{"value": -inf, "params": {}}`, which already makes optimizer.py:1861 skip `_save_best_as_backtest`, publishes empty best_params, fires the Optimizer.jsx:1559 banner, and makes research.py:709 reject apply-as-preset — no frontend change needed.

## [12] CONFIRMED — MEDIUM — A single raising grid combo permanently fails the whole job at the analyze stage (None objective_value poisons the sort)

- severity if real: HIGH

**Evidence:**
```
backend/app/optimizer.py:1468-1478 (grid loop) records a None score instead of raising:
```
                # O14: a single raising combo must NOT crash the whole job (resume
                # then deterministically re-hits the same combo forever). Mirror the
                # bayesian study.optimize(catch=Exception): disqualify + continue.
                try:
                    metrics, merged = await asyncio.to_thread(evaluate, params)
                    val = obj(metrics)
                except Exception as exc:
                    log.warning("grid trial %d raised (%s) — disqualified, continuing",
                                completed, exc)
                    trial_history.append({"params": params, "metrics": None,
                                          "objective_value": None, "error": str(exc)[:200]})
                    completed += 1
                    continue
```
The analyze stage then sorts that list unguarded — backend/app/optimizer.py:1652:
```
        sorted_trials = sorted(trial_history, key=lambda t: t["objective_value"], reverse=True)
```
I ran the exact comparison in this repo's interpreter: `sorted([{'v':1.5},{'v':None}], key=lambda t:t['v'], reverse=True)` -> `TypeError: '<' not supported between instances of 'float' and 'NoneType'`. Nothing between 1478 and 1652 filters or coerces the None (verified by reading the whole span; the only coercion is inside `_rebuild_study`, backend/app/optimizer.py:681-682 `v = rec.get("objective_value"); v = float(v) if v is not None else _DISQUALIFY`, which mutates only the Optuna study, not `trial_history`). The TypeError escapes to the top-level handler, backend/app/optimizer.py:1957-1960: `except Exception as e: ... await _update_job(job_id, {"status": "failed", "error": str(e), ...})`. Resume re-hits it: `trial_history = list(rdoc.get("trial_log") or [])` (optimizer.py:1390) and `_compact_trial` preserves the key verbatim (optimizer.py:634 `"objective_value": t.get("objective_value")`), while `completed` is restored from the same doc so the grid loop is a no-op. Secondary confirmation: backend/app/rerank_select.py:39 `if t.get("objective_value", DISQUALIFY) <= DISQUALIFY:` also raises on an ex
```

**Minimal fix:** At backend/app/optimizer.py:1652 rank only scored trials: `sorted_trials = sorted((t for t in trial_history if t.get("objective_value") is not None), key=lambda t: t["objective_value"], reverse=True)`; also guard backend/app/rerank_select.py:39 with `v = t.get("objective_value"); if v is None or v <= DISQUALIFY: continue`, and skip None in the grid importance fallback (optimizer.py:1667). Add a grid test with one raising combo asserting the job reaches status done.

## [14] CONFIRMED — MEDIUM — On a truncated survival stage, un-evaluated finalists are silently counted as non-survivors and `evaluated` over-reports

- severity if real: MEDIUM

**Evidence:**
```
The survival loop can break mid-list — backend/app/optimizer.py:1733-1737:
```
                    await _an_progress("survival", i + 1, len(ranked), _per_item_surv)
                    if await _analyze_should_stop():  # O13: budget OR cancel/pause
                        break
                survivors = [r for r in ranked if r.get("survival", {}).get("survived")
                             and (r["survival"].get("total_return_pct") or 0) > 0]
```
`r["survival"]` is written ONLY inside that loop (optimizer.py:1722 success, 1729 `"reason": "eval_error"`, 1755 exit-grid). I read the row constructor in `_option_rerank` (optimizer.py:1162-1171) and it has no `survival` key, so every finalist past the break has no key and is dropped from `survivors` indistinguishably from a real gate failure. The summaries then claim the full list: optimizer.py:1781 `survival_summary = {"survivors": len(survivors), "evaluated": len(ranked),` and optimizer.py:1796 `"survivors": 0, "evaluated": len(ranked), "reason_counts": reasons,`, with the un-evaluated rows attributed to a fabricated reason at optimizer.py:1792 `rs = r.get("survival", {}).get("reason", "unknown")`. The same over-count reaches the UI via optimizer.py:1818 `analyzed_candidates = f"{len(ranked)}"`, rendered verbatim at frontend/src/pages/Optimizer.jsx:1539: "Analyzing budget hit — evaluated {job.analyzed_candidates ?? \"?\"} candidate(s). Raise the budget or lower Re-rank top-K for full coverage." And the terminal verdict is emitted regardless of truncation — optimizer.py:1877-1878: `if survival.enabled and survival_summary is not None and survival_summary.get("survivors") == 0: final_status = "done_no_survivor"`. Reachability is the shipped default: `analyze_budget_sec: int = 1800` and `rerank_top_k: int = 50` (backend/app/schemas.py:219, 197), and `_analyze_should_stop` (optimizer.py:1623-1637) also returns True on a user pause/cancel, not just budget.
```

**Minimal fix:** Track the loop position and report the truth: after the loop, tag the tail `for r in ranked[i+1:]: r.setdefault("survival", {"survived": False, "reason": "not_evaluated_budget"})`, set `"evaluated": i + 1` plus `"skipped_unevaluated": len(ranked) - (i + 1)` in both survival_summary dicts (optimizer.py:1781, 1796) and in `analyzed_candidates` (optimizer.py:1818), and suppress `done_no_survivor` at optimizer.py:1877 when the stage was truncated (use a distinct status such as `done_truncated`).

## [15] CONFIRMED — MEDIUM — Stage 2 promotes a winner on option rupees with no minimum-sample guard — one paired trade is enough

- severity if real: MEDIUM

**Evidence:**
```
Promotion with survival off tests only "nonzero paired trades" — backend/app/optimizer.py:1801-1806:
```
            elif ranked and ranked[0]["paired_trade_count"] > 0:
                best = ranked[0]
                best_so_far = {
                    "value": best["option_pnl_value"],
                    "params": best["params"],
```
and the ordering is pure rupees with only a zero/nonzero tiebreak — optimizer.py:1183 (also 1016 on the premium path): `ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)`. The `min_trades` guard is applied to the SPOT count only, inside `_objective_value` — optimizer.py:149-151:
```
    if tc == 0:
        return _DISQUALIFY  # no trades at all
    if min_trades and tc < min_trades:
        return _DISQUALIFY  # statistically meaningless sample
```
where `tc = int(metrics.get("trade_count", 0) or 0)` (optimizer.py:147). A grep for `min_trades` across backend/app/optimizer.py returns only lines 141/150/451/464/1213/1356/1372/1414 — the objective and its plumbing; it is never compared against `paired_trade_count` (whose only occurrences are 1169/1183/1774/1801/1811). No other gate substitutes: the 100-trade floor lives in the off-by-default survival path (backend/app/survival.py:21-23 `MIN_TRADES_FOR_RUIN = 100`), and `assess_option_research_integrity` (backend/app/option_data_integrity.py:27-120) checks identity/provenance blockers only — it computes no sample-size or coverage blocker. So the promoted `best_so_far`, `best_value`, the saved run and the preset can rest on a couple of paired trades; the UI only exposes it passively as `{paired}/{spot}` (frontend/src/pages/Optimizer.jsx:2245) with no flag.
```

**Minimal fix:** Reuse the job's `min_trades` on the option side: at backend/app/optimizer.py:1801 require `ranked[0]["paired_trade_count"] >= min_trades` (else leave the Stage-1 best / emit a `done_no_survivor`-style "insufficient paired sample" status), and mark under-sampled rows `r["insufficient_sample"] = True` in `_option_rerank` (optimizer.py:1162-1171) so the sort key sinks them and the re-rank table can flag them.

## [23] CONFIRMED — A pinned (fixed) parameter override is dropped from best_params, so the saved best runs a different value than the trials did

- severity if real: MEDIUM

**Evidence:**
```
Every quoted link in the chain exists, and I read all of it.

1. `_suggest` injects pins into the real params WITHOUT registering them with Optuna — backend/app/optimizer.py:349-353:
```
    for name, info in space.items():
        if "fixed" in info:
            out[name] = info["fixed"]
            continue
        t = info["type"]
```

2. The sequential bayesian/genetic branch takes the best from Optuna, which only knows suggested dimensions — backend/app/optimizer.py:1520-1529:
```
                    study_best_val = study.best_value
                    study_best_params = dict(study.best_params)
                except Exception:
                    study_best_val = None
                    study_best_params = {}
                if study_best_val is not None and study_best_val > best_so_far["value"]:
                    best_so_far = {
                        "value": study_best_val, "params": study_best_params,
```
The parallel branch repeats it — optimizer.py:1576 `study_best_params = dict(study.best_params)` and :1583 `best_so_far = {"value": study_best_val, "params": study_best_params,`. The grid branch does keep the full dict — optimizer.py:1481 `best_so_far = {"value": val, "params": dict(params), "metrics": metrics, "trial_num": completed}` — so grid and bayesian genuinely disagree.

3. The finish patch persists that dict as the authoritative answer — optimizer.py:1888 `"best_params": best_so_far["params"],`.

4. Anything absent silently reverts to the schema default — backend/app/strategies/base.py:98-109:
```
    def merged_params(self, override: Dict[str, Any] | None) -> Dict[str, Any]:
        ...
        out = self.default_params()
        if override:
            for k, v in override.items():
                if k in out or k in SHARED_INDICATOR_PARAM_KEYS:
                    out[k] = v
        return out
```
and base.py:95-96 `return {k: v.get("default") for k, v in self.parameter_schema.items()}`.

5. Both consumers re-derive from it: optimizer.py:697 `merged = strategy.merged_params(best_params)` inside `_save_best_as_backtest`, and backend/app/routers/research.py:707+715:
```
    best_params = job.get("best_params") or (job.get("best_so_far
```

**Minimal fix:** Overlay the pins onto the Optuna best at both sites (optimizer.py:1521 and :1576): `study_best_params = {**{k: v["fixed"] for k, v in space.items() if "fixed" in v}, **dict(study.best_params)}`. Add a test asserting `persisted best_params[p] == X` for `param_overrides={p: {"fixed": X}}`, X != default, across grid/bayesian/genetic. (wfo.py:623 builds the space the same way — check it for the same overlay need.)

## [24] CONFIRMED — objective="profit_factor": a config with ZERO losing trades scores 0.0 — the worst possible value — so the optimizer actively steers away from the strictly-profitable configs

- severity if real: HIGH

**Evidence:**
```
backend/app/optimizer.py:164-166, verbatim:
```
    if objective == "profit_factor":
        v = metrics.get("profit_factor")
        return float(v) if v is not None else 0.0
```
`profit_factor` is None precisely when no trade lost — backend/app/backtest.py:283-297:
```
    losses = pnls[pnls <= 0]
    gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_loss = float(losses.sum()) if len(losses) > 0 else 0.0
    ...
        "profit_factor": round(gross_profit / abs(gross_loss), 3) if gross_loss < 0 else None,
```
(`gross_loss` is a sum of non-positive numbers, so `gross_loss < 0` is false exactly when there is no strictly-losing trade.) Since PF is a ratio of non-negative magnitudes, every real value is >= 0.0, and PF == 0.0 happens only when gross_profit == 0 (all trades lost). So an all-winners config is scored identically to an all-losers config and strictly below any config with PF > 0. `_DISQUALIFY` is not in play — it is -1e9 (backend/app/rerank_select.py:13 `DISQUALIFY = -1e9`) and only fires on the trade-count/direction guards (optimizer.py:149-159), which an all-wins config with >= min_trades passes.

The premium branch in the same file handles it correctly, proving the intent — optimizer.py:497-502:
```
    if gross_loss > 0:
        profit_factor = round(gross_win / gross_loss, 3)
    elif gross_win > 0:
        profit_factor = 999.0  # only wins: large-but-finite (inf breaks JSON/ranking)
    else:
        profit_factor = None   # no wins and no losses -> undefined (scored 0.0)
```
Both metric producers feed the same scorer (`obj` at optimizer.py:1369-1370 -> `_objective_value`), so the ordinary/spot path is the broken one. The objective is user-selectable from the UI dropdown — frontend/src/pages/Optimizer.jsx:72 `{ id: "profit_factor", name: "Maximize Profit Factor", desc: "Gross profit / |gross loss|" },` inside `const OBJECTIVES = [` (:68), rendered at :762 `{OBJECTIVES.map((o) => <SelectItem key={o.id} value={o.id}>{o.name}</SelectItem>)}`.
```

**Minimal fix:** Mirror optimizer.py:497-502 at optimizer.py:164-166: when `profit_factor is None`, return 999.0 if there was gross profit (e.g. `metrics.get("wins", 0) > 0` / total_pnl_pts > 0) and only 0.0 (or `_DISQUALIFY`) when there is neither profit nor loss. Cleanest: make backtest.py:297 emit 999.0 for the only-wins case so the metric itself stops being None, matching the premium producer. Test: 12 all-winning trades must outrank a PF-0.5 config.

## [25] CONFIRMED — _robustness_score counts no-op perturbations as passes: integer rounding and bound clamping make ±10/20% shifts re-evaluate the identical config, inflating the ROBUST verdict

- severity if real: MEDIUM

**Evidence:**
```
backend/app/optimizer.py:538-553, read verbatim:
```
    for name, info in space.items():
        if "fixed" in info or info["type"] == "bool":
            continue
        if name not in best_params:
            continue
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
There is no `t_v != base_v` guard anywhere in the loop, and `n_total += 1` / `n_ok += 1` (:554-557) count the row regardless. I reproduced the arithmetic in this repo's Python against the real schema bounds (backend/app/strategies/builtin/confluence_scalper.py:20-21 `"signal_threshold": {"type": "int", "min": 40, "max": 95, "default": 62},` / `"cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 5},`):
  cooldown base 1 -> [1, 1, 1, 1]   (4/4 identical to base)
  cooldown base 2 -> [2, 2, 2, 2]   (4/4 identical)
  cooldown base 3 -> [2, 3, 3, 4]   (2/4 identical)
  signal_threshold base 95 -> [76, 86, 95, 95]  (both upward shifts clamp back to base)
When `t_v == base_v` the re-run yields `val == base_val`, and for a positive `base_val` the test `val >= base_val * 0.85` is satisfied by construction, so those rows are guaranteed passes. The result is published as a verdict, not a diagnostic — frontend/src/pages/Optimizer.jsx:1988 `const label = score >= 70 ? "ROBUST" : score >= 50 ? "MODERATE" : "FRAGILE";` with :1996 `% of ±10/20% param perturbations that stayed within 85% of best objective`, and the perturbation rows are tabulated for the user from the same payload.
```

**Minimal fix:** After the clamp/round at optimizer.py:546-548, `if t_v == base_v: continue` so no-ops are not counted; for `type == "int"` use an absolute step (`base_v ± max(1, round(base_v*pct))`, clamped) so small integers are genuinely perturbed; and return a `skipped` / `unperturbable_params` count in the payload so the card can say "n params could not be perturbed" instead of crediting them. Guard `n_total == 0` (already returns 0).

## [26] CONFIRMED — _robustness_score's pass test inverts when the best objective is negative: `val >= base_val * 0.85` demands a 15% IMPROVEMENT, so neg_max_dd runs are labelled FRAGILE by construction

- severity if real: MEDIUM

**Evidence:**
```
backend/app/optimizer.py:553, verbatim: `            ok = val >= base_val * 0.85 and metrics.get("trade_count", 0) >= 5`
The multiplication only relaxes the bar for positive `base_val`. I computed both cases: base 2.0 -> threshold 1.7 (a 15% degradation still passes); base -120 -> threshold -102.0 and base -0.4 -> threshold -0.34, i.e. the perturbation must BEAT the base by 15% to be counted OK. Reproducing the base value exactly fails.
The intent is explicitly a degradation tolerance, so this is not "by design" — optimizer.py:527 `"""Perturb each numeric param by ±10% and ±20%; count fraction that stay 'profitable'.` and frontend/src/pages/Optimizer.jsx:1996 `% of ±10/20% param perturbations that stayed within 85% of best objective`.
`neg_max_dd` is <= 0 by construction — optimizer.py:176-177:
```
    if objective == "neg_max_dd":
        return -abs(float(metrics.get("max_dd_pts", 0) or 0))
```
and it is user-selectable from the dropdown — Optimizer.jsx:75 `{ id: "neg_max_dd", name: "Minimize Max Drawdown", desc: "Stable equity curve" },` in `const OBJECTIVES` (:68), rendered at :762. Because the winning trial is by definition the least-drawdown config, essentially every perturbation fails, `score` (optimizer.py:560) collapses toward 0, and Optimizer.jsx:1988 renders FRAGILE for the most stable config found. The same inversion hits any `sharpe` or `risk_adjusted` run whose winner is negative (both are unbounded below: optimizer.py:161-163 and :178-180 `sharpe / max(1.0, dd / 100.0)`).
```

**Minimal fix:** Replace the signed multiplication at optimizer.py:553 with a magnitude band: `tol = abs(base_val) * 0.15` then `ok = val >= base_val - tol and metrics.get("trade_count", 0) >= 5`. Explicitly short-circuit the degenerate cases before the loop — if `base_val <= _DISQUALIFY` or `base_val == 0`, return the score as unavailable (and have the UI say so) rather than letting the comparison decide. Test with objective="neg_max_dd" that a perturbation reproducing the base drawdown is counted OK.

## [28] CONFIRMED — Parallel trial path attaches the PREVIOUS best's metrics to the NEW best params whenever the search space contains a pinned dimension, because study.best_params omits fixed params

- severity if real: MEDIUM

**Evidence:**
```
backend/app/optimizer.py:1614-1618 (HEAD ad850d3) — parallel ask/tell branch:
```
                    if study_best_val is not None and study_best_val > best_so_far["value"]:
                        best_metrics = next((t["metrics"] for t in reversed(trial_history)
                                             if t["params"] == study_best_params), best_so_far["metrics"])
                        best_so_far = {"value": study_best_val, "params": study_best_params,
                                       "metrics": best_metrics, "trial_num": completed - 1}
```
`trial_history` records the FULL dict from `_suggest`, which injects pinned dims WITHOUT calling trial.suggest_* — optimizer.py:384-387:
```
    for name, info in space.items():
        if "fixed" in info:
            out[name] = info["fixed"]
            continue
```
and the record appended is `trial_history.append({"params": params, ...})` (optimizer.py:1606) with `params` from `param_list = [_suggest(t, space) for t in trials]` (optimizer.py:1592). Optuna's `study.best_params` therefore lacks those keys, so the dict equality is never true. I reproduced it in this repo's .venv (optuna 4.8.0) driving the real `_suggest` through ask/tell with space `{"a":{int 1..10}, "pinned":{"fixed":5}}`: `best_params: {'a': 10}` and the `next(...)` lookup printed `FALLBACK` on every batch. Because `best_so_far` starts as `{"value": -inf, "params": {}, "metrics": {}, ...}` (optimizer.py:1466), the fallback propagates `{}` (fresh job) or the previously-persisted best's metrics (resume, optimizer.py:1424-1432) — and that same dict is what `finished["best_metrics"]` / `finished["best_so_far"]["metrics"]` publish (optimizer.py:1920,1932). Pinned dims are real, not hypothetical: `_build_param_space` writes `info["fixed"] = info.get("default")` for any NON_ALPHA name and for any int/float lacking both bounds (optimizer.py:355-362), and the AI compiler emits exactly such a bare param — backend/app/ai/compiler.py:464 `f'        "cooldown_bars": {{"type": "int", "default": {spec.cooldown_bars!r}}},\n'`. Reachability: this branch needs `opt_workers>1`, `method=="bayesian"` and a non-premium strategy (optimizer.py:1474-1477), i.e. an AI
```

**Minimal fix:** Compare only the searched subset, or better capture metrics at tell time inside the same zip loop (as wfo.py already does) and merge the pinned dims back into best_so_far["params"]: e.g. track `if val > batch_best: batch_best_params, batch_best_metrics = params, metrics` and promote that, so params/metrics always come from the same trial record.

## [29] CONFIRMED — risk_adjusted/neg_max_dd mix units: max_dd_pts is index POINTS on the ordinary path and RUPEES on the premium path, and max(1.0, dd/100) zeroes the drawdown penalty for any ordinary run under 100 points

- severity if real: MEDIUM

**Evidence:**
```
backend/app/optimizer.py:176-181 (HEAD):
```
    if objective == "neg_max_dd":
        return -abs(float(metrics.get("max_dd_pts", 0) or 0))
    # risk_adjusted (default)
    sharpe = float(metrics.get("sharpe") or 0)
    dd = abs(float(metrics.get("max_dd_pts") or 1))
    return sharpe / max(1.0, dd / 100.0)
```
The clamp is real: for any ordinary run with |max_dd| <= 100 the denominator is exactly 1.0, so `risk_adjusted` returns plain Sharpe and drawdown contributes nothing — yet it is the default objective (`objective = payload.get("objective", "risk_adjusted")`, optimizer.py:1228) advertised as a trade-off in the UI (frontend/src/pages/Optimizer.jsx:69 `{ id: "risk_adjusted", name: "Risk-Adjusted Return (default)", desc: "Sharpe / drawdown — balanced quality" }`). Ordinary-path units are index points, from cumulative point P&L — backend/app/backtest.py:287-290,300: `eq = np.cumsum(pnls)` / `peak = np.maximum.accumulate(eq)` / `dd = eq - peak` / `max_dd = float(dd.min()) ...` then `"max_dd_pts": round(max_dd, 2),`. The premium path substitutes a RUPEE quantity into the identical key — optimizer.py:551-554:
```
        # NOT a true unit match: rupee max-drawdown substituted where the spot
        # formula expects index points (a rupee-native premium strategy has no
        # index-points drawdown concept) — an honest, documented proxy.
        "max_dd_pts": abs(float(port.get("max_drawdown_value", 0.0) or 0.0)),
```
and both metric dicts flow through the SAME `obj` closure (optimizer.py:1406-1410 `_objective_value(metrics, objective, lot_size=lot_size, ...)`; the premium branch at optimizer.py:1396 passes the same `objective`). So a spot run divides Sharpe by ~1 while a ₹20,000-drawdown premium run divides by ~200, and both are surfaced as one `best_value` under one `objective` label. The unit substitution is documented in the source comment, but nothing clamps or labels it for the user, and the >=1.0 clamp is documented nowhere. No test in tests/ pins either behaviour.
```

**Minimal fix:** Make the denominator scale-free and unit-aware: replace `sharpe / max(1.0, dd/100.0)` with a Calmar-style `total_pnl / max(eps, |max_dd|)` (or normalise dd by the run's own gross P&L/equity) so drawdown always contributes, and emit an `objective_units` field ("points" vs "rupees") on the job so best_value is never compared across the two paths.

## [30] CONFIRMED — Early stop is invisible: the ceiling n_trials is reported as the trial count and stamped into the saved run's overfit evidence

- severity if real: MEDIUM

**Evidence:**
```
The backend does record the distinction — optimizer.py:1643-1645 (HEAD): `await _update_job(job_id, {"status": "analyzing", "n_trials_completed": completed, "early_stopped": early_stopped, "stopped_at_trial": completed, "trials_ceiling": n_trials})`. But `grep -rn "early_stopped|trials_ceiling|stopped_at_trial" frontend/src/` returns ZERO hits; the UI renders only `const pct = job.n_trials_total ? Math.round((job.n_trials_completed / job.n_trials_total) * 100) : 0;` (frontend/src/pages/Optimizer.jsx:1425) and `{job.n_trials_completed || 0} / {job.n_trials_total || 0} trials` (Optimizer.jsx:1504), with `n_trials_total` set to the ceiling (optimizer.py:1442 `"n_trials_total": n_trials`). The ceiling — not `completed` — is also fed to both artifacts: `n_trials=n_trials,` in the `_save_best_as_backtest` call (optimizer.py:1911), persisted onto the run as `**({"n_trials": int(n_trials)} if n_trials else {}),` (optimizer.py:804), and into the trust evidence `evidence={"oos_return_pct": None, "stress_return_pct": _stress, "n_trials": n_trials, ...}` (optimizer.py:1993-1995), which deployment_quality.py:311-338 turns into `deflated_sharpe(sharpe_val, n_trials, trade_count)` and the literal sentence `f"This result was the best of {n_trials} optimizer trials over {trade_count} trades. "` (deployment_quality.py:328). The auto-stop really does fire well before the ceiling on defaults: `early_stop = bool(payload.get("early_stop", True))` (optimizer.py:1229), UI sends `n_trials: 150` and `early_stop: true` (Optimizer.jsx:93,98), and backend/app/early_stop.py:42-43 gives `eff_warmup = min(200, max(30, 150//3)) = 50`, `eff_patience = min(200, max(20, 150//5)) = 30`. So a converged job reports "50 / 150 trials" at 33% under a DONE badge and its saved run claims n_trials=150.
```

**Minimal fix:** Pass `completed` (not `n_trials`) to `_save_best_as_backtest` (optimizer.py:1911) and to the quality `evidence` dict (optimizer.py:1995), keeping the ceiling as a separate `trials_ceiling`; and render the already-persisted `job.early_stopped` / `stopped_at_trial` / `trials_ceiling` in Optimizer.jsx so a converged run shows 100% plus an "auto-stopped at trial N of M" line.

## [5] REFUTED — HIGH — Zero-survivor refusal is defeated: apply-as-preset falls back to the stale spot best that the survival gate rejected

- severity if real: NONE

**Evidence:**
```
Two independent reasons, both read directly.
(1) The endpoint does NOT accept the zero-survivor status. backend/app/routers/research.py:705:
```
    if job.get("status") not in ("done", "cancelled", "paused", "interrupted", "failed"):
        raise HTTPException(400, "Job has no finished result yet")
```
"done_no_survivor" is not in that tuple, and the zero-survivor branch sets exactly that status — backend/app/optimizer.py:1878-1879:
```
        if survival.enabled and survival_summary is not None and survival_summary.get("survivors") == 0:
            final_status = "done_no_survivor"
```
So the request 400s before reaching the fallback at research.py:707. The audit's own parenthetical ("status check at research.py:705 allows `done`") misreads a tuple membership test as a prefix match.
(2) The claim's load-bearing premise — "that terminal patch (optimizer.py:1853-1883) contains **no `best_so_far` key**" — is false in current source. The finished patch re-persists it, backend/app/optimizer.py:1900-1905:
```
            "best_so_far": {
                "value": best_so_far["value"] if best_so_far["value"] > -1e8 else None,
                "params": best_so_far["params"],
                "metrics": best_so_far["metrics"],
                "trial_num": best_so_far.get("trial_num"),
            },
```
and in the zero-survivor branch that local is already emptied (optimizer.py:1794 `best_so_far = {"value": -1e9, "params": {}, "metrics": {}, "trial_num": -1}`), so the stored `best_so_far.params` is `{}` — the fallback `(job.get("best_so_far") or {}).get("params")` yields `{}`, which is falsy, so research.py:708-709 raises "Job has no best parameters to save". This re-persist is item (a) of the ALREADY FIXED commit 588208b.
The frontend claim also fails for the same reason: frontend/src/pages/Optimizer.jsx:1436 `const hasBest = (bsf.params && Object.keys(bsf.params).length > 0) || ...` is false for `params: {}`, so the Save-as-Preset button at Optimizer.jsx:1492-1497 does not render for a zero-survivor job.
```

**Minimal fix:** None required. If belt-and-braces is wanted, tighten the fallback at backend/app/routers/research.py:707 to `job.get("best_params") if job.get("best_params") is not None else ...` so a present-but-empty best can never fall through — but no reachable path today produces the claimed silent save.

## [13] REFUTED — MEDIUM — `search_exit_controls` burns the analyze budget on a provably no-op grid whenever exit_mode is the default `spot_exit`

- severity if real: NONE

**Evidence:**
```
The claimed reachable state (a job running with `search_exit_controls=True` while `exit_mode` is `spot_exit`) is rejected at the only job-creation site. backend/app/routers/research.py:585-594, inside `@api.post("/optimize/start")` (declared at research.py:556):
```
    # O5: premium-based exit-control search is a NO-OP unless option execution uses
    # premium levels (exit_mode='option_levels'). Under the default spot exit the
    # grid burns with zero effect and silently adopts nothing — reject it loudly so
    # the user picks option-levels exit or turns the search off.
    if req.search_exit_controls and str(oc.get("exit_mode") or "").strip() != "option_levels":
        raise HTTPException(
            400,
            "search_exit_controls requires option_config.exit_mode='option_levels' — "
            "premium-based exit controls (trailing/target/stop on the option leg) are "
            "a no-op under spot exit, so the grid would burn with no effect.")
```
That HTTPException fires before `job_id = await optimizer_create_job(req.model_dump())` (research.py:603), and a full grep shows research.py:603 is the ONLY caller of `optimizer.create_job`, i.e. the only writer of the `config` payload that `payload.get("search_exit_controls")` (optimizer.py:1738) later reads; `resume_optimization` (optimizer.py:1993-2010) replays that already-validated stored config. So the grid at optimizer.py:1738-1757 cannot execute under `spot_exit`, and the described symptom (silent wasted survival evaluations, budget truncation, `chosen_exit_controls` never set) is unreachable. The audit's own suggested fix ("skip it with a job-level warning unless exit_mode == option_levels") is already implemented, more strictly, as a hard 400. Residual, NOT what was claimed: the UI checkbox at frontend/src/pages/Optimizer.jsx:1166 is gated only on `evaluation_mode === "option_rerank"` and survival-enabled, not on `option_exit_mode === "option_levels"`, so such a user gets a 400 on Start rather than a disabled control — a cosmetic UX gap, not wasted compute or a false verdict.
```

**Minimal fix:** None required for correctness. Optional UX: disable/annotate the "Auto-tune exit controls" checkbox in frontend/src/pages/Optimizer.jsx:1160-1178 unless `config.option_exit_mode === "option_levels"`, so the constraint shows before Start instead of as a 400.

## [31] REFUTED — objective="net_pnl_inr" ignores option_config.lots and converts SPOT index points at the option lot size, so the "Net P&L (₹)" it maximises is not the rupee P&L of the run being validated

- severity if real: LOW

**Evidence:**
```
The mechanical half of the quote is accurate — optimizer.py:169-173 `return float(metrics.get("total_pnl_pts", 0) or 0) * float(lot_size)`, `obj` is built with only `lot_size` (optimizer.py:1406-1410), and `lots` lives elsewhere (`lots = int(option_cfg.get("lots") or 1)` at optimizer.py:881 in _survival_eval_oos and :1034 in _option_rerank). But the alleged DEFECT does not follow, for three independently checked reasons. (1) `lots` is one positive constant for the whole job (frontend/src/pages/Optimizer.jsx:416 `lots: Math.max(1, Number(config.option_lots || 1))`, default `option_lots: 1` at :132), so multiplying every trial's objective by it cannot change any trial's rank, any Top-N order, or any guard outcome (`min_trades`/`min_direction_share` return `_DISQUALIFY` before the scaling, optimizer.py:150-159) — the claim "Trials are therefore ordered by a quantity that is neither the displayed option ₹ nor the deployed size" is false; ordering is identical to `total_pnl_pts` either way. (2) The conversion is exactly what is documented, in the source ("Net rupee P&L = net points ... × lot size. This is an honest index-point→rupee conversion; it does not model option premium decay", optimizer.py:170-172) and verbatim in the UI (Optimizer.jsx:70 `desc: "Net rupee P&L = net points × lot size (enable costs)"`), with the Lots field separately labelled as the size knob (Optimizer.jsx:992). (3) In the only mode where `option_config.lots` is used at all (option_rerank), the promoted headline is NOT this objective: `best_so_far["value"] = best["option_pnl_value"]` (optimizer.py:1843-1845) and the job publishes `"best_value_metric": ("option_pnl_value" if evaluation_mode == "option_rerank" else objective)` (optimizer.py:1930-1932). The residue is a display-scale nit only: with lots>1 the Stage-1 card shows 1-lot rupees.
```

**Minimal fix:** Cosmetic only: label the objective's units, e.g. rename the surfaced value to `net_pnl_inr_per_lot` (or multiply by `option_cfg.lots` purely for display) so a lots>1 user is not comparing a 1-lot Stage-1 figure with the lots-scaled Stage-2 option rupee.
