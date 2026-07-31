# Backtest & optimizer integrity audit — permanent register

**Period:** 2026-07-29 → 2026-07-31 · **All work merged and pushed** (`origin/main`).
Supersedes and replaces four running logs (`BACKTEST_AUDIT_2026-07-30.md`,
`PREMIUM_NATIVE_REPORTING_AUDIT_2026-07.md`, `ROUND6_OPTIMIZER_AUDIT_RAW.md`,
`ROUND8_VERIFICATION.md`) — recover any of them with
`git show 23ccfed:docs/<name>` if the raw evidence is ever needed.

> **Read this before trusting ANY backtest number, and before touching
> `optimizer.py`, `option_backtest.py`, `runtime.py` or `premium_trigger_dispatch.py`.**

---

## 1. Why this audit happened

A user optimized an AI-authored premium strategy and got `lots: 100` when the form
said 5, a +197% headline, blank KPI cards and an empty Trades pane. Pulling that
thread found a defect class that ran through the whole reporting layer, and one
critical bug that had silently invalidated **every saved paired-option backtest in
the database**.

## 2. The defect class (this is the thing to internalise)

**One surface reads another surface's data envelope and gets a plausible-looking
wrong answer.** It recurred in eight places. A backtest result has two envelopes:

| | ORDINARY strategy (e.g. `confluence_scalper`) | PREMIUM-NATIVE (e.g. `algotest_option_buy_nifty`) |
|---|---|---|
| `result.metrics`, `result.trades` | the real spot result | **a zero-filled stub** — `evaluate()` is a deliberate inert stub |
| `result.option_backtest.*` | the paired option overlay | **the entire real result** |

Anything reading `result.metrics` for a premium run gets `trade_count: 0`,
`win_rate: 0.0`, `sharpe: None` — and renders it as fact. Route with
`option_backtest.dispatch == "premium_trigger_config"`; on the frontend use
`isPremiumNative()` / `resultKpis()` from `lib/backtestMetrics.js`.

## 3. ★ The critical bug — `contract_key` NaN collapse

`option_backtest.build_candles_by_key` keyed candle rows as
`str(ck) if ck else contract_identity_key(...)`. `contract_key` arrived in v0.56.1
and only ~2.3% of `options_1m` docs carry it, so a loaded frame routinely MIXES
both shapes — pandas materialises the column and absent entries become **NaN**.
**`bool(float("nan")) is True`**, so every legacy candle was keyed as the literal
string `"nan"` and its real identity vanished from the index.

**Measured on one Confluence config:** paired **10 of 253** signals → **253 of 253**
after the fix; net ₹13,007 → **₹290,443**; return +6.50% → **+145.22%**.

Three things made it expensive to find: it surfaced as `MISSING_ENTRY_CANDLE` (so
it looked like a data gap and sent the user to re-fetch candles already stored);
the preflight queried Mongo directly and never called the grouper, so it certified
**100% coverage** on the same config; and it worsens with every ingestion, then
self-heals once every row has the field — so it looks intermittent.

> **⚠ Every paired-option backtest saved before 2026-07-30 is wrong.** Re-run any
> preset you intend to rely on. Premium-native runs were never affected (they match
> on canonical `instrument_key` and never read `contract_key`).

## 4. What was fixed (all merged, with commits)

| Area | Fix | Commit |
|---|---|---|
| **Candle grouping** | Only a non-blank **string** `contract_key` is an identity; NaN/None/blank fall back to the derived one | `dcaf722` |
| **Preflight** | Certifies through the REAL grouper via shared `candle_contract_identity`; stopped indexing by bare exchange token (which merged reused tokens across expiries) | `75ca8ce` |
| **Premium KPIs / trades** | KPI grid + Trades pane read the option envelope; relabelled to ₹; `profitFactor` computed | `c61dd40` |
| **Optimizer param space** | Bounds are never invented; sizing/risk knobs pinned (`NON_ALPHA_PARAM_NAMES`); indicator periods not injected for premium | `c61dd40` |
| **AI compiler** | Emits curated `min`/`max` derived from the shipped plugin, or an explicit pin | `c61dd40` |
| **Full engine surface** | `build_engine_params` forwards all 34 `ENGINE_PARAM_KEYS` (lazy legs, `leg_mode`, session times, percent trails) — enabling the lazy leg used to change nothing | `ed560a6` |
| **Walk-forward** | `premium_walk_forward` over option trades; `measured: False` instead of a fake pass; signed `avg_win_rate_delta` + `divergence_soft` | `ed560a6`, `a7b2584` |
| **Costs** | Spread reported explicitly (`total_spread_cost_value`, `total_cost_value`); gross-run warning | `a7b2584` |
| **Live entry parity** | Premium entry cutoff clamped to `BLOCK_CLOSE_FROM` (14:50) | `a7b2584` |
| **Capital** | `peakConcurrentCapital` sweep-line + not-fundable warning | `a7b2584` |
| **Optimizer preload/costs** | Preload widens for the lazy leg; premium trials scored WITH costs | `1835c8a` |
| **Both backtest paths** | `/backtest/run` and `/backtest/start` share `resolve_wf_and_significance` | `2ac02b9` |
| **Optimizer coherence** | `best_so_far` re-persisted at finish; `best_value_metric`; ONE entry window across Stage 1 / Stage 2 / saved run / preset | `588208b` |
| **Grid robustness** | `scored_trials()` — one raising combo no longer kills a completed search | `ad850d3` |
| **Objective / resume / sample** | `profit_factor` zero-loss inversion; `stage2_rank_key` min-sample; `resolve_resume_completed` | `8085ddd` |
| **JSON safety** | `best_so_far_doc()` — `-Infinity` no longer 500s the job-history endpoint | `405d37c` |
| **Journal / trust** | Journal + trust scorecard read the option envelope; orphaned runs reconciled as failed | `03f6ae7` |
| **Persistence** | List projection inverted → **155.3 MB → 3.5 MB (97.8%)**; preset carries window/sizing/spread_min_pts; `candles_capped` measured; failed runs undeployable; Monte Carlo samples randomly + discloses; `data_coverage` on both paths | `23ccfed` |

## 5. STILL OPEN — verified, not yet fixed

All independently verified against source. `minimal_fix` guidance is in the commit
history of `139a1f2` (or `git show 23ccfed:docs/ROUND8_VERIFICATION.md`).

| # | Sev | Finding |
|---|---|---|
| 11 | **HIGH** | When every trial fails the guard rails, the optimizer still promotes/saves a **disqualified** config and lets the user apply it; the "no usable result" banner never fires. Fix: require `val > _DISQUALIFY` before promoting. |
| 18 | **HIGH** | A truncated survival sweep reports every finalist as "evaluated" and every unevaluated one as a failure reason. |
| 22 | **HIGH** | A concurrent optimizer job's fork pool is torn down by an unrelated job's `finally` block, failing the running job. Fix: mirror `wfo.py`'s `use_parallel` guard. |
| 28 | **HIGH** | Parallel trial path attaches the PREVIOUS best's metrics to the NEW best params whenever the space contains a pinned dimension (`study.best_params` omits fixed params). |
| 14 | MED | Truncated survival stage counts un-evaluated finalists as non-survivors. |
| 17 | MED | Option re-rank ignores Stop/Pause — neither of its loops reads the control flags. |
| 20 | MED | WFO analyze stage reads no control flag at all; job still reports "done". |
| 23 | MED | A pinned (`fixed`) param override is dropped from `best_params`, so the saved best runs a different value than the trials did. |
| 25 | MED | `_robustness_score` counts no-op perturbations as passes (int rounding / bound clamping), inflating the ROBUST verdict. |
| 26 | MED | `_robustness_score`'s pass test inverts on a negative objective — `neg_max_dd` runs are FRAGILE by construction. |
| 29 | MED | `risk_adjusted` mixes units: `max_dd_pts` is index POINTS (ordinary) vs RUPEES (premium), and `max(1.0, dd/100)` zeroes the drawdown penalty for any ordinary run under 100 points. Intra-job ranking is unaffected; cross-job comparison is meaningless. |
| 30 | MED | Early stop is invisible — the ceiling `n_trials` is reported as the trial count and stamped into the saved run's overfit evidence. |
| 31 | LOW | `net_pnl_inr` ignores `option_config.lots` and converts SPOT points at the option lot size. (One verifier refuted this; treat as disputed.) |

**REFUTED — do not re-raise:** #5 (zero-survivor refusal holds — `done_no_survivor`
is not in apply-as-preset's accepted-status tuple) · #13 (`search_exit_controls`
no-op grid) · #21 was refuted by me and later **CONFIRMED** and fixed — see §7.

**Never audited:** the deploy → paper → live handoff. See `AGENT_TODO.md`.

## 6. Strategy verdict (do not re-litigate without new data)

`algotest_option_buy_nifty` / **NF_CE_PE_EXP2_Base** has **no demonstrated edge**.

* Train Jan–Apr 2026, `sharpe` objective: +₹1,21,655 / **+60.83%** / Sharpe **4.49**
* Untouched holdout May–Jul 2026: **−₹3,300 / −1.65% / Sharpe −0.27**

A daily Sharpe of 4.49 over 50 trading days was itself the tell, not the result.
The user's own 1-year run nets **less** than its 6.5-month subset (Jul–Dec 2025 lost
~₹1.6 lakh, DD −55.6%). This is the **second** independent holdout failure for the
family — see `PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`.

**If revisited, use more data, not more tuning.** The warehouse reaches back to
2024-11-25: train 2024-11 → 2025-12, hold out all of 2026 (~4× the history).

## 7. Lessons that generalise

1. **`bool(NaN) is True`.** Never truth-test a pandas cell. `isinstance(x, str) and x.strip()`.
2. **A source-contract test asserting a STRING appears in a file cannot tell a USE
   from a BINDING.** Three `NameError`s shipped this way. `tests/test_no_undefined_names.py`
   now runs pyflakes over `backend/app` with a 7-entry verified baseline; it has
   since caught two more before any test ran.
3. **Test frontend logic by EXECUTING it through node**, not by grepping JSX.
4. **Fix every path, not the one you're looking at.** The premium walk-forward fix
   landed on `/backtest/run` while the UI calls `/backtest/start`; the user kept the
   defect I had reported fixed. Cure: one shared function, plus a test asserting
   both callers delegate to it.
5. **Verify each call site of a bulk edit.** A `sed -i .../g` hit two call sites; I
   checked one. The other referenced a local that did not exist in that scope.
6. **A certification tool must reproduce the lookup it certifies.** The preflight
   reported 100% coverage on a run that paired 4%.
7. **Threshold booleans hide signal.** A 10-point walk-forward cut hid 9.44- and
   9.65-point decays on two strategies that then failed out of sample.
8. **Objectives monotonic in something you don't want maximised will maximise it.**
   `net_pnl_inr` is monotonic in `lots` AND in trade count.
9. **Spot-check breadth before declaring a refutation.** I refuted claim 21 after
   checking 2 of **5** writers of `best_so_far`; the other three were unguarded and
   the agent was right.
10. **Workflow verifier agents die on spend limits.** Findings without a verifier are
    UNVERIFIED claims. Recover partial work from
    `.../subagents/workflows/<run>/journal.jsonl` — the result key is `result`, not `value`.

## 8. Reproducing the evidence

```bash
# a run's real numbers (premium runs: read option_backtest, NOT metrics)
docker exec alphaforge_mongo mongosh --quiet alphaforge --eval '
  const r = db.backtest_runs.findOne({}, {}, {sort:{_id:-1}});
  print(JSON.stringify(r.option_backtest.metrics));'

# what an optimization actually chose
docker exec alphaforge_mongo mongosh --quiet alphaforge --eval '
  const j = db.optimization_jobs.findOne({}, {}, {sort:{_id:-1}});
  print(j.objective, j.best_value, j.best_value_metric);
  print(JSON.stringify(j.best_params));'
```

Backend API is on **port 8001** (`http://127.0.0.1:8001/api`), not 8000.
