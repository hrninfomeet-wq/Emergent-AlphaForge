# Backtest results audit — 2026-07-30

Checkpoint file. Append as work lands; never rewrite history.
User asked: re-run NF_CE_PE_EXP2_Base under a new name, audit the result, compare
against the previous runs for anomalies/inconsistencies in results, calculations
and display; then run the saved Confluence optimized preset at 5 lots and
compare. **Change nothing yet — analysis first.**

## Reference runs already on disk (pre-existing, user-created)

### A. `algotest_option_buy_nifty · NIFTY · 2026-07-30 00:16:06`
Window **2026-01-01 → 2026-07-24**, form lots 5 (params.lots 2 → form won, the
2026-07-29 precedence fix working), `dropped_params: []`.
116 trades, WR 44.83%, net **₹394,378.39**, return **+197.19%**, max DD −12.51%.

### B. `algotest_option_buy_nifty · NIFTY · itm1_1yr_5lot`
Window **2025-07-25 → 2026-07-24** (1 year), same params, lots 5.
208 trades, WR 42.79%, net **₹234,839.32**, return **+117.42%**, max DD **−55.59%**.

### FIRST ANOMALY (found before running anything)
B covers A entirely plus the preceding ~5 months, yet B's net is **₹159,539
LOWER** than A's and its drawdown is **4.4× deeper** (−55.59% vs −12.51%).
⇒ Jul–Dec 2025 lost roughly **₹1.6 lakh**. The "+197%" headline in A is a
window artifact, consistent with the failed May–Jul holdout
(`docs/PREMIUM_NATIVE_REPORTING_AUDIT_2026-07.md` Round 4).
A −55.59% drawdown is also close to un-tradeable at this sizing.

### SECOND ANOMALY — the +197% run has ZERO option costs (HIGH)
```
Run A "00:16:06":  run costs_enabled=true
                   option cost_config = null
                   gross 394,378.39 | charges 0 | net 394,378.39
Run B "itm1_1yr":  option cost_config = {brokerage 1/order, spread 0.5%}
                   gross 258,310.41 | charges 23,471.09 | net 234,839.32
```
A's headline **+197.19% is GROSS** — no brokerage, no STT, no spread. B pays
₹23,471 over 208 trades (~₹113/trade).

**The trap**: the run-level "Apply realistic costs" toggle WAS on. It governs
INDEX-SIDE slippage only, and a premium-native strategy has no spot leg — so it
does nothing at all here. The one switch that matters is "Apply rupee costs" in
the Option Execution block, which was off. A user can therefore have costs
"enabled" and receive a fully gross result. The BacktestLab hint text does say
this, but for a premium-native strategy the toggle is not merely partial, it is
**completely inert** — and nothing says so.

⇒ **A and B are not comparable to each other either** (no costs vs 0.5%+₹1).

### Preset to use for the premium re-run (user-directed)
`algotest_option_buy_nifty · NIFTY · 4lotP&L197%` — params identical to A's, and
its `execution` block is `{moneyness: atm, dte_filter: [], exit_mode: spot_exit,
lots: 5}` with **no cost_config**. So replaying it faithfully reproduces the
gross result. NB `moneyness: atm` in the execution block is ignored for a
premium-native run (strike selection follows the strategy's own `itm1`).

## Confluence preset to be run
`Confluence_507% DD 26.10% · NIFTY · 2026-06-28 22:59:01` →
strategy `confluence_scalper`; params ema_fast 20 / ema_slow 23 /
rsi_bull 54.19 / rsi_bear 38.21 / signal_threshold 59 / cooldown 19 /
spot_target 161.36 pts / spot_stop 96.30 pts;
execution: moneyness **atm**, exit_mode **option_levels**, target 28% / stop 18%,
preset lots **10** (user asked for **5**), cost spread **0.7%**.

⚠️ **Comparability caveat**: the premium strategy runs a **1.0%** spread, this
preset **0.7%**. Not like-for-like on costs; quantify before comparing.

## Plan
1. Re-run A's exact config under a new name → check reproducibility.
2. Run Confluence preset at 5 lots, SAME window as A.
3. Audit both; compare; report. No code changes.

## Premium re-runs (2026-01-01 → 2026-07-24, 5 lots)

| | P1 as-saved (no cost model) | P2 + costs (0 brokerage, 0.5% spread) |
|---|---|---|
| trades | 116 | 116 |
| win rate | 44.83% | 44.83% |
| gross | 394,378.39 | **354,684.80** |
| charges | 0.00 | 14,923.98 |
| net | 394,378.39 | 339,760.82 |
| return | +197.19% | +169.88% |
| max DD | −12.51% (₹78,178) | −13.77% (₹85,463) |
| Sharpe | 3.53 | 3.05 |
| max Buy ₹ | 167,228.75 (83.6% of capital) | 168,006.19 (84.0%) |

**P1 reproduces run A exactly** (116 / 44.83 / 394,378.39 / 197.189 / −12.509)
⇒ the engine is deterministic; no reproducibility defect.

### THIRD ANOMALY — "gross" already has the spread deducted (HIGH, reporting)
P1 gross 394,378.39 vs P2 gross 354,684.80. Gross should be cost-invariant, and
it moved by **₹39,693.59** when the cost model was switched on.

Cause (`premium_momentum.py:226-230`):
```python
entry_fill = entry + spread_pts_for_premium(entry, cost_cfg) / 2.0
exit_fill  = max(0.0, exit_ - spread_pts_for_premium(exit_, cost_cfg) / 2.0)
gross_rupees = round((exit_fill - entry_fill) * qty, 2)
```
So `gross_pnl_rupees` is computed on SPREAD-ADJUSTED fills. **Verified the
ordinary path does exactly the same** (`option_backtest.py:706-762`: entry/exit
prices come from the slippage/spread-aware fill, and `gross_pnl_value =
pnl_pts * quantity`). So this is NOT a premium-only bug and NOT a maths error —
the arithmetic is self-consistent.

It IS a disclosure problem. The Option Execution cost summary renders
`gross 354,684.80 · charges −14,923.98 · net 339,760.82`, so a user reads total
cost = ₹14,924. The real cost of the model is **₹54,617.57**
(spread 39,694 + charges 14,924) — understated **3.7×**. "Gross" here means
"before statutory charges", not "before all costs", and nothing says so.

### FOURTH ANOMALY — the backtest takes trades live cannot (MEDIUM)
4 of 116 entries are at/after **14:50**, 3 at/after 15:00 (`time_of_day: CLOSE`).
`deployment_evaluator.BLOCK_CLOSE_FROM = 14:50` blocks live/paper entries from
14:50. The premium path applies only the strategy's `entry_cutoff` (15:09) and no
live-effective trade window — while the OPTIMIZER already defaults
`trade_window_end` to 14:50 for exactly this reason (O6). So backtest and
optimizer disagree with each other AND the backtest counts ~3.4% of trades that
could never be taken.

### FIFTH — walk-forward threshold is too lax, AGAIN (MEDIUM)
IS 48.437% → OOS 38.783% = **9.65-point decay**, reported as
`divergence_warning: False` because the cut is a hardcoded 10 points. This is the
SECOND run to land at 9.4–9.7 (Round 4 was 9.44). A boolean at 10 points is
hiding a consistent, real signal. Report the delta.

### SIXTH — the account cannot fund this strategy at 5 lots (HIGH, practical)
Max Buy ₹167,229 = **83.6% of the ₹200,000 capital for ONE leg**. With
`leg_mode: both` two primaries can be open at once, plus a lazy leg — so peak
concurrent requirement is ~2–3× that. **Nothing in the backtest checks capital
feasibility at entry**; the equity curve just compounds. So the +197% is computed
on a capital base that could not actually have supported the trades.

### Note on max DD %
−12.51% with a value of −₹78,178 on ₹200,000 capital: the % is measured against
PEAK equity (~₹625k), which is the standard convention and correct. But it reads
as mild next to a drawdown that is **39% of the initial deposit**. Presentation
risk, not a bug.

## Confluence preset runs (same window, 5 lots)

| | C1 as-saved (0.7%) | C2 cost-matched (0.5%) |
|---|---|---|
| SPOT trades | 253 | 253 |
| **paired option trades** | **10** | **10** |
| missing_entry_candle | **243** | 243 |
| net | 13,007.09 | 13,330.32 |
| return | +6.50% | +6.67% |
| trading days | 7 | 7 |

**The Confluence numbers are meaningless as produced** — 96% of its signals never
paired. Not a strategy result at all.

## ★ ROOT CAUSE — CRITICAL BUG: `contract_key` NaN collapse

`option_backtest.py:469-472` (`build_candles_by_key`):
```python
explicit = candles["contract_key"] if "contract_key" in candles.columns else [None] * len(candles)
candles["_contract_key"] = [
    str(ck) if ck else contract_identity_key(ik, expiry)
    for ik, expiry, ck in zip(candles["instrument_key"], expiries, explicit)
]
```
`contract_key` exists on only **171,749 of 7,354,037** `options_1m` docs (2.3%) —
`db.py:72` confirms "historical rows created before v0.56.1 have no
contract_key". When a loaded frame MIXES both shapes, pandas materialises the
column and the missing entries become **NaN**. `bool(float('nan')) is True`, so
`str(ck)` wins and every contract_key-less row is keyed **`'nan'`**.

Proven with a minimal repro:
```
A: no row has contract_key   -> keys ['NSE_FO|40477|2026-01-06', 'NSE_FO|40477']
B: ONE unrelated row has it  -> keys ['NSE_FO|57030|2026-06-02', 'nan', 'NSE_FO|57030']
                                'NSE_FO|40477|2026-01-06' present? False
```
Verified against the real run: **all 243** missing trades have expiry-matched
candles in the warehouse within the entry-age window
(`diag_all.py` → "CANDLE EXISTS (exp-matched) → engine bug: 243").
The 10 that DID pair fall on 2026-05-29, 06-01, 07-20…07-24 — exactly the recent
dates whose docs carry `contract_key`.

**Blast radius**: every ORDINARY (paired-option) strategy — confluence, VWAP,
ORB, SMC… — over any window that mixes pre/post-v0.56.1 candles. Fails SILENTLY
as `no_candles_for_strike`, i.e. it looks like missing data, so a user is sent to
re-fetch data that is already there. Grows with every new ingestion until all
historical rows are backfilled (then it self-heals, because nothing is NaN).

**Premium-native path is NOT affected**: `premium_ohlc_for_key`
(`premium_momentum.py:71`) matches on `canonical_instrument_key(instrument_key)`
and never touches `contract_key`. Consistent with 116/116 pairing.

This also explains the user's own observation that the older
`Confluence_507% DD 26.10%` run (saved 2026-06-28) DID populate correctly: at
that time few/no rows carried `contract_key`.

### SEVENTH — preflight and the real run disagree, and CANNOT agree by design
For the identical config/window the preflight reports **253/253, coverage 100.0%,
missing_candle 0** while the run pairs 10/253. The preflight is CORRECT about
availability. But it queries `options_1m` directly per (key, ts) and never calls
`build_candles_by_key`, so it structurally cannot detect this failure class —
the one tool meant to certify a backtest is trustworthy is blind to the bug that
invalidates it.

### EIGHTH — trust signals describe the SPOT layer, money describes the OPTION layer
C1: `significance = SIGNIFICANT, CI [48.0, 60.2], "based on 253 trades"` and
walk-forward IS 52.88 / OOS 53.73 — both computed on the 253 SPOT trades, while
the headline P&L came from 10 option trades. A user reads a confident
significance badge next to a number built on 4% of the sample.

### NINTH — `measured` flag asymmetry
`premium_walk_forward` returns `measured: True/False`; the spot `walk_forward`
never sets it (C1 shows `measured: None`). Not a live defect (the UI falls through
correctly) but the two paths report different shapes for the same panel.

## FIXES (user approved all six, 2026-07-30)

### FIX 1 — contract_key NaN collapse — LANDED
`option_backtest.py` `build_candles_by_key`: only a genuine non-blank **string**
may substitute for the derived identity.
```python
ck.strip() if isinstance(ck, str) and ck.strip() else contract_identity_key(ik, expiry)
```
`tests/test_candles_by_key_mixed_contract_key.py` — 10 tests (mixed frame, blank/
whitespace/None ck, explicit ck still authoritative, token-reuse separation
preserved, and the sim's real lookup expression end-to-end).

**Measured impact on the SAME Confluence config/window:**

| | before | after |
|---|---|---|
| paired | **10 / 253** | **253 / 253** |
| missing_entry_candle | 243 | **0** |
| net | ₹13,007.09 | **₹290,443.22** |
| return | +6.50% | **+145.22%** |
| max DD | −6.80% | −21.28% |
| trading days | 7 | 138 |
| win rate | 50.0% (n=10) | 45.45% (n=253) |

Suite 4115 passed / 0 failed.

⚠️ **Every previously saved paired-option run is suspect** and should be re-run —
including `Confluence_507% DD 26.10%`. Premium-native runs are unaffected.

## Status
- [x] Reference runs inspected, anomalies 1-2
- [x] Premium re-runs P1/P2 + anomalies 3-6
- [x] Confluence runs C1/C2
- [x] ★ CRITICAL root cause isolated and proven (contract_key NaN collapse)
- [x] Anomalies 7-9
- [ ] USER DECISION on what to fix (no code changed — as instructed)

### FIX 2 — preflight now certifies against the REAL lookup — LANDED
`candle_contract_identity(instrument_key, expiry_date, contract_key)` in
`option_backtest.py` is now the SINGLE definition of a candle's identity, used by
`build_candles_by_key` AND by `_option_preflight_report`. The preflight groups
through `build_candles_by_key` itself and looks up identity-then-canonical in the
same order the sim does, instead of indexing by bare canonical token.

Two defects closed:
 * it could not detect the Fix-1 class at all (never called the grouper);
 * a token-keyed ts list merged every expiry that reused an exchange token, so a
   trade could be certified against a different contract's candles.

The projection now includes `expiry_date` + `contract_key` — without them no
identity can be derived.

Verified live: preflight 253/253 AND the real run 253/253 — they agree.

⚠️ Caught during this fix: `contract_identity_key` was used in `runtime.py` while
only `canonical_instrument_key` was imported. The accompanying tests were SOURCE
CONTRACTS, which never execute the path — the identical blind spot that shipped
the original `is_premium_trigger_strategy` NameError. Import added, and TEN more
shared helpers added to `tests/test_no_unbound_helper_names.py`'s AST guard so
this class is structurally covered rather than spotted by luck.

Suite 4123 passed / 0 failed.

### FIX 3+4 — cost disclosure — LANDED
`_compute_metrics` now also reports `total_spread_cost_value` and
`total_cost_value`. The cost summary shows `gross · spread · charges · TOTAL COST ·
net`, with a tooltip stating that gross is measured at spread-adjusted fills.
When there is NO option cost model the panel used to render nothing at all; it now
shows an amber "these figures are GROSS" warning that also explains why the
run-level *Apply realistic costs* toggle does not cover it (index-side slippage
only; a premium-native strategy has no index leg).
Measured on the re-run: spread **₹38,239** + charges **₹14,385** = **₹52,625** true
cost, against ₹14,385 previously displayed — the 3.7× understatement confirmed.
`tests/test_cost_disclosure.py` — 11 tests.

### FIX 5 — walk-forward reports the decay — LANDED
`SOFT_DIVERGENCE_PTS = 5.0` / `HARD_DIVERGENCE_PTS = 10.0` and a shared
`_divergence_fields()` used by BOTH engines, adding signed `avg_win_rate_delta`
(positive = OOS worse) and `divergence_soft`. Hard thresholds deliberately
unchanged — retuning them would rewrite what every saved run meant. The panel now
always shows the decay, colour-coded, plus a soft-caution block.
The 10-vs-15 / one-sided-vs-two-sided discrepancy between the engines is recorded
here rather than silently unified. `tests/test_walkforward_delta_reporting.py` — 15.

### FIX 6 — capital feasibility + live entry parity — LANDED
* `clamp_entry_cutoff_to_live()` pulls the effective entry cutoff back to the live
  block, read from `deployment_evaluator.BLOCK_CLOSE_FROM` so it cannot drift.
  An ABSENT cutoff is clamped too ("no cutoff" silently meant "trade to 15:30").
  Disclosed as `entry_cutoff_clamped` / `live_entry_cutoff` on the result.
* `computeKeyMetrics` gains `peakConcurrentCapital` (sweep line over overlapping
  positions — the real funding requirement) and `capitalShortfall`, with a new
  "Peak capital needed" card and a red not-fundable warning.
`tests/test_capital_and_live_entry_parity.py` — 14 tests.

## Post-fix re-run (premium preset, 2026-01-01 → 2026-07-24, 5 lots)

| | before fixes | after fixes |
|---|---|---|
| trades | 116 | **112** (4 live-blocked entries removed) |
| net (costed) | 339,760.82 | 343,768.84 |
| return | +169.88% | +171.88% |
| WFO decay | 9.65 pts → **False** | 13.19 pts → **True** |
| true cost shown | ₹14,924 of ₹54,618 | **₹52,625 of ₹52,625** |

Removing the 4 undeployable entries made the decay LARGER and the hard warning now
fires correctly — the strategy is flagged as overfit, which the holdout already
proved.

**Correction to an earlier speculation in this doc**: I assumed `leg_mode: both`
would need 2–3× the single-leg capital. Measured peak concurrent =
**₹168,006 = the single-leg max**, i.e. legs did NOT overlap in this run, and there
is no shortfall at ₹200,000. The claim was mine, unmeasured, and wrong.

Suite 4163 passed / 0 failed. Containers rebuilt and verified live.

## Status — ALL SIX FIXES LANDED
- [x] 1 contract_key NaN collapse (CRITICAL)
- [x] 2 preflight certifies against the real lookup
- [x] 3 spread shown as a cost
- [x] 4 gross-run warning
- [x] 5 walk-forward delta + soft band
- [x] 6 capital feasibility + live entry parity
- [ ] USER: re-run every saved paired-option preset (Confluence_507% especially)

## Round 5 — user-reported: "Backtest failed: name 'df_enriched' is not defined"

### V. THE REPORTED BUG — my `sed`, two call sites, one verified
A `sed -i 's|..._run_paired_option_backtest(req, res["trades"])|...(..., context_df=df_enriched)|g'`
hit BOTH call sites in `research.py`. In `backtest_run` that local exists; in
`run_backtest_job` the enriched frame is a local named `de` INSIDE the `_compute()`
closure and was never returned. Every async run raised NameError.
Fix: `_compute` now returns `de`; the caller forwards it.

### W. THE WORSE ONE — my Round-3 fix never reached the path the UI uses (HIGH)
The Backtest Lab calls `api.startBacktest` -> **`POST /backtest/start`** ->
`run_backtest_job`. The premium walk-forward + option-based significance added
2026-07-29 were applied ONLY to the sync `/backtest/run` handler — which is the
path my own verification scripts used. **Confirmed on the user's own UI run
`... 00:16:06`**: 3 folds, every fold 0 trades, `divergence_warning: false`,
`significance: INSUFFICIENT "0 trades"` — the exact defect I had reported fixed.

Cure is structural, not another parallel edit: both handlers now call ONE
`resolve_wf_and_significance(...)`, so a family-routing decision cannot land on one
path and not the other. `tests/test_backtest_paths_are_equivalent.py` asserts both
delegate to it and that neither routes inline again.

**Verified through `/backtest/start` after the fix:**
* premium — `measured: True`, delta **16.26 pts**, soft AND hard warnings firing,
  significance on 108 trades (was "0 trades").
* confluence — delta −0.85 (OOS better), no warning; costs shown
  spread 59,348 + charges 22,220 = **81,568 total**.

### X. A REAL pre-existing NameError found by pyflakes (HIGH, live code)
`routers/deployments.py:1341` calls `logging.getLogger(__name__).debug(...)` inside
an `except Exception` handler with **no `logging` import**. A benign last-entry
lookup failure therefore raised NameError *from the error handler* and 500'd the
**live-status endpoint**. Import added.

### Y. Guard: pyflakes undefined-name scan over `backend/app`
Third NameError shipped in a week (`is_premium_trigger_strategy`,
`contract_identity_key`, `df_enriched`). `test_no_unbound_helper_names.py` only
watched a hand-maintained SHARED_HELPERS set, so it could never have caught
`df_enriched` — an ordinary local. Widening that list per incident is chasing
instances. `tests/test_no_undefined_names.py` now runs pyflakes with a small,
explicitly VERIFIED baseline (7 entries, every one a false positive on a string
annotation or a `Literal[...]` member) plus a test that the baseline cannot rot.
Also fixed my own `compiler.py` `Dict`/`Any` (used in annotations, never imported).

**Why the source-contract tests kept missing this class**: they assert a STRING
appears in a file, and a string appears in a *use* — so a use with no binding
satisfies them. Only real analysis or executing the path distinguishes the two.

### Z. Run-to-run variance is a DATA effect, not a code one (MEDIUM)
Sync vs async runs showed premium 112 vs 108 and confluence 253 vs 246. Not a path
difference — `option_contracts` grew **63,868 -> 64,848** between the runs while
`options_1m` stayed at 7,354,037. Contract METADATA arrived without candles.
Expiry resolution picks the nearest expiry >= the session from metadata, so a newly
known-but-uningested expiry is selected and then has no data:
```
expiry 2026-07-07  200 contracts   33/120 sampled keys have candles
expiry 2026-07-14   94 contracts   18/120 have candles
expiry 2026-07-21  354 contracts    0/120 have candles
```
The 7 new confluence misses are all early-July trades resolving to 2026-07-14.
**Consequence for the user: a contract sync can silently REDUCE backtest coverage,
and two identical backtests minutes apart can differ.** Run the option preflight
before trusting a comparison. Not fixed here — flagged for a decision.

Suite **4179 passed / 0 failed**.

## Round 6 — re-optimize BOTH strategies, then backtest, then audit the process

User asked: optimize both strategies again, load into backtest, audit the
optimization AND backtest functionality/process for abnormalities, gaps, technical
issues.

### Setup (deliberate choices)
Window **2026-01-01 → 2026-07-24** — the same one the user has been using, so the
audit reflects their real workflow. **IN-SAMPLE by construction; the point of this
round is process/functionality, not edge.**
Objective `sharpe` for both (scale-invariant, not monotonic in trade count —
unlike `net_pnl_inr`, which drove `momentum_pct` to its floor in Round 4).
Costs: brokerage ₹0 (per the account owner) + 0.5% spread, both strategies, so the
two are cost-matched.
* confluence_scalper — `evaluation_mode: option_rerank`, top-K 25, lots 5, ATM,
  option_levels 28%/18%. Chose the two-stage mode ON PURPOSE: the documented
  weakness of `spot` mode is that it optimises the index leg, not the option net.
  Job `fd40ecff-9027-4d80-b0c0-58b465db25b4`.
* algotest_option_buy_nifty — `evaluation_mode: spot` (Stage-1 is already
  option-native for premium), lots 5, itm1, spot_exit.

Parallel code audit: workflow `wf_2f23df6d-0a6`, 5 dimensions x adversarial verify
(apply-preset round-trip, option_rerank Stage-2, job lifecycle, objective/metric
integrity on the ORDINARY path, result persistence/display across both endpoints).

### Status
- [x] Confluence optimization started
- [ ] Confluence optimization finished
- [ ] Premium optimization
- [ ] Backtest replay of both winners
- [ ] Audit synthesis

### Confluence optimization result (job `fd40ecff`)
`objective: sharpe`, `evaluation_mode: option_rerank`, early-stopped 68/150,
25 candidates re-ranked, budget not hit.
```
best_value            : 134864.61      <-- rupee P&L, NOT a Sharpe
best_option_pnl_value : 134864.61
best_metrics.sharpe   : 1.168          <-- Stage-1 best was 1.499 (trial 37)
best_metrics          : trade_count 222, paired 220, option_win_rate 43.18
saved best run        : spot 227 trades, paired 220/227, net 134,864.61,
                        spread 50,982 + charges 19,023, ret +67.43%, DD -28.16%
survival_summary/robustness: null
```

### AA. `best_value` holds a RUPEE amount while `objective` says "sharpe" — HIGH
After an `option_rerank`, `best_value` is overwritten with the Stage-2 option
rupee P&L, but `objective` still reads `sharpe`. The Job History "Best" column
therefore shows `134864.61` for a Sharpe objective. Two different quantities in
one field, distinguished nowhere.

### BB. `option_rerank` silently OVERRIDES the chosen objective — HIGH
Stage 1's best was **Sharpe 1.499** (trial 37). The finally-selected config has
**Sharpe 1.168** — Stage 2 re-ranked on option ₹ and picked a different candidate.
That is the documented purpose of the mode, but it means **choosing
`objective: sharpe` does not give you the highest-Sharpe configuration**. The
objective selector governs Stage-1 shortlisting only; final selection is always
option-₹. Nothing in the UI says so.

### CC. ★ The optimizer's best is NOT reproducible in the Backtest Lab — HIGH
The optimizer scores with `trade_window_end: 14:50` (`schemas.py`
`OptimizerStartReq`, deliberate: "so the optimizer never rewards 14:50–15:00
entries that live can never take (O6)"). But:
* `preset_execution.execution_from_option_config` carries moneyness / dte_filter /
  exit_mode / lots / target / stop / cost_config / exit_controls / daily_caps —
  **not the trade window**;
* `apply-as-preset` stores it ONLY under `config.validation.*`, i.e. as metadata;
* `BacktestLab.jsx:121-122` defaults `trade_window_end: "15:00"`, and preset load
  does not override it.

Verified on the real preset (`AUDIT-ROUNDTRIP-conf`):
```
validation.trade_window_end : "14:50"     <-- known
execution                   : no trade window field at all
top-level config            : no trade_window_* key
```
⇒ **Optimize at 14:50, replay at 15:00.** The app HAS the right value and does not
apply it. Measured effect on this job: optimizer `best_metrics.trade_count` 222 vs
its own saved run's 227 spot trades (+5, +2.3%) — and the extra entries are exactly
the 14:50–15:00 ones live refuses. Direction is systematically optimistic.
NB `_save_best_as_backtest` writes `trade_window_start/end: None`, so even the
optimizer's OWN saved run disagrees with its own scoring.
Premium strategies are protected (entry_cutoff clamped in Round 4/5); this gap is
ORDINARY-strategy-specific.

### DD. `execution.cost_config` drops `spread_min_pts` — LOW
`execution_from_option_config` emits only `{enabled, brokerage_per_order,
spread_pct_of_premium}`. A configured `spread_min_pts` is silently lost on
apply-as-preset.

### Premium optimization result (job `d085b8ab`) — CLEAN
`objective: sharpe`, `evaluation_mode: spot`, early-stopped 134/150.
`best_value: 3.402` (a REAL Sharpe), `best_so_far.params == best_params` ✓.
best_params: momentum_pct 49, stop_pct 33, lazy_enabled true, lazy_momentum_pct 5,
lazy_stop_pct 31. best_metrics: 109 trades, WR 44.95, PF 1.544, option ₹140,328.
⇒ The defects below are specific to **`option_rerank` mode**, not to optimization
in general. Premium (spot mode) round-trips consistently.

### EE. ★★ The Optimizer UI shows one set of params and SAVES ANOTHER — HIGH
Verified end-to-end on job `fd40ecff` (real data + real source):
```
best_so_far.params   ema_fast/ema_slow/signal_threshold = 6/43/74   sharpe 1.499
best_params          ema_fast/ema_slow/signal_threshold = 5/57/79   sharpe 1.168
SAME PARAMS? -> false
```
* `Optimizer.jsx:1426` `const bsf = job.best_so_far || {}` — the results card renders
  `best_so_far` params + metrics. Job History's "Best" column also reads
  `best_so_far?.value` (`Optimizer.jsx:2402, 2471`).
* `research.py:707` `best_params = job.get("best_params") or ...` — apply-as-preset
  saves `best_params`.

After an `option_rerank` promotes a Stage-2 winner, only `best_params`/`best_value`/
`best_metrics` are written; `best_so_far` is never refreshed (its only writer is
`_flush_trial_log`, called from the trial loops). **So the user reads config A on
screen and saves config B to the preset.** Independently found by 3 of 3 surviving
audit dimensions and confirmed here with real job data.

### CORRECTION to finding AA (mine, wrong)
I claimed the Job History "Best" column displays the rupee `best_value` under a
Sharpe objective. It does not — it reads `best_so_far?.value`, which IS a Sharpe
(1.499). What remains true: **`best_value` itself stores a rupee amount while
`objective` says `sharpe`** (a data-model inconsistency, verified: `best_value =
134864.61`), and it disagrees with the Sharpe the UI shows. The specific
"history shows rupees" claim was mine and unfounded.

### CC — MEASURED (window round-trip gap), and my direction claim was wrong
Same winning params, same everything except the entry window:

| window | spot | paired | wr% | net | ret% | maxDD% |
|---|---|---|---|---|---|---|
| **14:50** (what the optimizer scored) | 222 | 215 | 42.79 | **₹142,636.01** | +71.32% | −29.40% |
| **15:00** (BacktestLab default) | 227 | 220 | 43.18 | **₹134,864.61** | +67.43% | −28.16% |

Delta: **+5 spot trades, net −₹7,771.40 (−5.4%)**.

I previously wrote that the direction is "systematically optimistic" for the
backtest. **Wrong** — here the extra 14:50–15:00 trades LOST money, so the replay
UNDERSTATES. The direction is not predictable; the point is only that it is a
DIFFERENT trade set.
Also note the optimizer's reported `best_option_pnl_value` (134,864.61) matches the
**15:00** run, not the 14:50 one — so the promoted/saved run also dropped the
window. The 14:50 figure (₹142,636) is what nothing reports.

### Agent findings — 31 raw, UNVERIFIED
`docs/ROUND6_OPTIMIZER_AUDIT_RAW.md`. Workflow `wf_2f23df6d-0a6`: 3 of 5 audit
dimensions completed, **5 of 8 agents died on the account spend limit** including
ALL verifiers and the `apply-preset-roundtrip` + `result-persistence-display`
dimensions entirely (I audited apply-preset-roundtrip by hand instead → AA–DD, EE).
Verified by me so far: EE, CC, AA(corrected), BB, DD.
Everything else in that file is an unverified claim.
