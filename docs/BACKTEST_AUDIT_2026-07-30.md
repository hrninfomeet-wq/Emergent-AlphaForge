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
