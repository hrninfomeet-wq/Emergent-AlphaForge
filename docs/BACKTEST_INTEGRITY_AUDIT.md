# Backtest & optimizer integrity audit — permanent register

**Period:** 2026-07-29 → 2026-07-31 · original audit and the 2026-07-31/08-01
HIGH/MED follow-ups are merged and pushed to `origin/main`.
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
| **Persistence** | List projection inverted → **155.3 MB → 3.5 MB (97.8%)**; preset carries window/sizing/spread_min_pts; `candles_capped` measured; incomplete/failed run status explicitly warned and acknowledged (hard veto superseded by operator policy); Monte Carlo samples randomly + discloses; `data_coverage` on both paths | `23ccfed` + current tree |

## 5. VERIFIED FINDINGS — current status

All independently verified against source. `minimal_fix` guidance is in the commit
history of `139a1f2` (or `git show 23ccfed:docs/ROUND8_VERIFICATION.md`).

### Closed 2026-07-31 — published to `origin/main` 2026-08-01

| # | Sev | Resolution |
|---|---|---|
| 11 | **HIGH** | One shared promotion path binds the exact params/metrics tuple across Grid, sequential and parallel trials and rebuilds resume state from logged evidence. Per the operator's 2026-07-31 policy, every recursively finite candidate — including a running-job snapshot, a zero-parameter strategy, and the `_DISQUALIFY` guardrail sentinel — retains params/metrics for optional save/deploy with an explicit qualification warning. Only errored candidates or candidates with NaN/infinity anywhere in params/metrics produce no promotable result. |
| 18 | **HIGH** | Survival summaries now separate evaluated/finalist/not-evaluated counts, count failure reasons only for evaluated rows, persist budget/cancel/pause stop cause, and disclose incomplete coverage in the UI. |
| 22 | **HIGH** | The optimizer mirrors WFO's ownership guard: `shutdown_pool()` runs only when `start_pool()` returned a pool to this job. |
| 28 | **HIGH** | Parallel promotion happens at tell time from the exact `(params, metrics, value)` tuple, retaining pinned dimensions and eliminating the partial-`study.best_params` history lookup. |

Regression proof lives in `tests/test_optimizer_verified_high_regressions.py`; the
promotion-policy cases were observed failing against the former hard gates and pass
after the policy implementation. The completion review also regression-pins recursive
finite values, zero-param/running snapshots, deterministic finite `Signal` output,
optimizer-param deployment parity, and resume/live-enable revalidation. Verification:
focused promotion/deployment/evaluator set 264/264, full host **4,326 passed / 4 xfailed /
0 failed**, and selected in-container route/Motor regressions **212 passed / 4
source-layout tests deselected / 0 failed**; compileall, optimized frontend build and
hard-refreshed browser smoke pass.

### Closed 2026-08-01 — all 8 confirmed MED findings

| # | Sev | Resolution |
|---|---|---|
| 14 | MED | Already subsumed by HIGH #18 before this Stage-1 pass: the survival summary counts reasons only across `ranked[:evaluated]` and reports evaluated/finalist/not-evaluated separately. The prior red regression remains in `test_optimizer_verified_high_regressions.py`. |
| 17 | MED | Both ordinary and premium option re-rank loops now read the shared cancel/pause/budget callback and retain the finite partial ranking when stopped (`9865314`). |
| 20 | MED | WFO rechecks cancel/pause before final analysis, save and option pairing, preserves completed-window evidence, and cannot overwrite the stop state with `done` (`1c3c5b8`). |
| 23 | MED | Already subsumed by HIGH #28 before this pass: trial promotion carries the exact params+metrics tuple, including pinned dimensions, instead of looking up a partial `study.best_params`. The earlier pinned-dimension regression remains the proof. |
| 25 | MED | Robustness excludes clamped/rounded no-ops and duplicate perturbations; its denominator is the number of distinct effective trials and skipped cases are disclosed (`f7f0546`). |
| 26 | MED | The degradation tolerance is symmetric around positive and negative maximizing objectives, so an improvement from a negative baseline no longer fails by construction (`5bf5582`). |
| 29 | MED | Drawdown objectives use a unitless `abs(max_drawdown) / sum(abs(trade_pnl))` fraction in points for ordinary runs and rupees for premium-native runs. Serial, fork-worker, WFO compact-persistence and resume paths carry the same denominator (`9184a06`, `675d4fd`). |
| 30 | MED | Saved evidence separates actual completed trials from the requested ceiling and the UI names auto-stop explicitly (`2c31cc9`). |

Regression coverage for the six newly implemented findings lives in
`tests/test_optimizer_medium_integrity.py`; #14/#23 remain covered by the earlier HIGH
regressions because their MED descriptions were stale rows, not separate surviving
defects. Final Stage-1 verification: host **4,354 passed / 4 xfailed / 0 failed**;
selected in-container route/Motor/optimizer set **142 passed / 9 source-layout tests
deselected / 0 failed**; compileall, optimized frontend/Docker builds, service health,
runtime payload/preflight checks and canonical-`localhost` browser smoke pass.

### Still open — 1 disputed LOW

| # | Sev | Finding |
|---|---|---|
| 31 | LOW | `net_pnl_inr` ignores `option_config.lots` and converts SPOT points at the option lot size. One verifier refuted this; it remains explicitly disputed and was not folded into Stage 1. |

**SUPERSEDED BY OPERATOR POLICY:** #5's former zero-survivor refusal. A finite best
candidate is now retained and `done_no_survivor` is accepted by apply-as-preset, with
the failed survival screen carried as an acknowledgment warning.

**REFUTED — do not re-raise:** #13 (`search_exit_controls` no-op grid) · #21 was
refuted by me and later **CONFIRMED** and fixed — see §7.

**Code handoff audit complete; broker validation still absent.** Promotion/deployment
competency and resume/live-enable revalidation were audited with the HIGH closure. No live
order has ever occurred, so real broker behavior remains unvalidated; see `AGENT_TODO.md`.

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
11. **The market itself can invalidate a backtest.** History is not a stationary
    substrate. See §8 — on 2026-08-03 the exchange changed the session shape, so
    every strategy validated on earlier data was validated on a microstructure that
    no longer exists. Nothing in the code was wrong; the world moved.

## 8. Market-structure break — 2026-08-03 session split

**This is not a code defect. It is a regime boundary in the data**, and it is the
one entry in this register that no amount of code review would have surfaced.

SEBI's Closing Auction Session framework took effect across NSE/BSE/MSEI on
**2026-08-03**, splitting a session shape that had been uniform for the app's whole
history:

* **Cash / index** — continuous trading now ends **15:15**, with an auction to
  15:35 printing the official close. Every NIFTY 50 / SENSEX constituent is
  F&O-eligible, so *nothing trades* during the auction and the published index
  simply stops. Measured on NIFTY, 2026-08-03: **14 consecutive zero-range bars**
  at 24573.35, then **24774.30 — a +200.95 point (+0.82%) one-bar gap.**
* **Equity derivatives** — trade on to **15:40**. Spot and options no longer share
  a day length: 375 bars vs 385.

### Why it matters for backtests

Any run spanning 2026-08-03 mixes two microstructures. Feeding the frozen tail to a
stateful indicator is actively harmful — ATR and Bollinger width decay toward zero
across the flat bars and then every breakout gate fires on a synthetic gap. **A
strategy tuned across this boundary is fitting an artifact.**

### What was done

`backend/app/session_spec.py` is the single date- and segment-aware source of
session bounds, keyed on `CAS_EFFECTIVE_DATE`. Auction bars are flagged
`in_cas_window` and **excluded from every indicator's input** (state indicators
hold their last real value; event markers read empty), while remaining in the
candle series because the 15:29 bar carries the official close. Live guards now
watch to 15:40. Pre-2026-08-03 output is unchanged, enforced by
`tests/test_cas_indicator_suppression.py::test_pre_cas_day_is_untouched`.

### Measured 2026-08-10 — the warehouse is complete

`backend/scripts/audit_cas_session_coverage.py` was run against the live warehouse.
**Upstox serves the full extended session: 385 bars/contract, last bar 15:39, on
every post-CAS day for NIFTY, BANKNIFTY and SENSEX — every contract complete.** No
vendor escalation is needed. (Flattrade's *historical* API does compress the window
into its 15:29 bar; that is a Flattrade-only artifact and does not affect our data.)

Spot behaves exactly as described: 375 bars, last 15:29, with 14 frozen tail bars on
every post-CAS index day. **INDIA VIX does not freeze** (0–1 flat bars) because it is
derived from the option order book, which keeps trading — and it is correctly never
CAS-masked, being an `AUX_INSTRUMENT_KEYS` series that no indicator path enriches.

### The damage, quantified on real data

Running `precompute_all_indicators` over the stored NIFTY frame for 2026-08-03, with
and without suppression:

| IST | ATR suppressed | ATR unsuppressed | |
|---|---|---|---|
| 15:14 | 9.689 | 9.689 | last real bar |
| 15:20 | 9.689 | 6.221 | decayed to 64% |
| 15:28 | 9.689 | 3.439 | **decayed to 35%** |
| 15:29 | 9.689 | 17.547 | **spiked to 181%** |

Unsuppressed ATR swings **3.44 → 17.55, a 5.1× jump in one minute**, entirely on
artifact. Note our stored 15:29 bar has a true range of **200.95** (Upstox captures
the transition within the bar; Flattrade only prints the final value).

### ★ It bleeds into the NEXT MORNING — this is why it matters

The auction window itself is untradeable, so the distortion above would be harmless
on its own. **It is not confined to that window.** `gap_before_mask` deliberately does
not flag cross-date boundaries — whole-frame EWM/rolling indicators are designed to
carry across them — so the poisoned ATR state propagates straight into the next
session's open.

Measured across four consecutive post-CAS days (NIFTY, error = unsuppressed vs fixed):

| IST | 04-Aug | 05-Aug | 06-Aug | 07-Aug |
|---|---|---|---|---|
| 09:15 | **+57.2%** | −29.4% | **−38.7%** | −29.5% |
| 09:25 *(signal window opens)* | **+34.4%** | −19.8% | −20.0% | −15.2% |
| 09:40 | +17.5% | −9.5% | −8.6% | −7.1% |
| 10:30 | +0.6% | −0.3% | −0.4% | −0.3% |

**ATR is wrong by 15–34% at the exact minute the signal window opens**, and does not
converge until roughly 10:30. The sign flips day to day, because the EWM carries both
the decayed flat-bar state and the jump-bar spike and either can dominate.

So the real exposure was never the dead auction window — it was **the first ~75
minutes of every trading day from 2026-08-03 onward**, covering the whole morning
trend-development window the strategies actually trade. Anything ATR-scaled — stop
sizing, volatility gates, regime classification, breakout thresholds — was affected.

### Resolved — the BANKNIFTY gap

**BANKNIFTY had no data at all for 2026-08-03** (0 spot bars, 0 option contracts)
while the other instruments had a full session. Operator re-synced on 2026-08-10; it
now reads 375 spot bars with 14 frozen tail bars and 20 option contracts at 385 bars.
A failed ingest that day, not a CAS effect.

## 9. Reproducing the evidence

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
