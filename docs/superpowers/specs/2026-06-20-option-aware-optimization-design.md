# Option-Aware Optimization — Design

**Date:** 2026-06-20
**Branch:** `feat/option-aware-optimization`
**Status:** Brainstorm approved (direction + objective decision); spec for user review.

## 1. Goal

Add an **opt-in** optimization mode in which the optimizer's per-trial objective is
computed on the **real paired-option P&L** instead of the spot backtest — so the
sampler (Optuna TPE / CMA-ES) is steered toward configs that are profitable as an
**option buyer**, not configs that merely look good on the index and happen to be
option-negative. This fixes the long-standing spot↔option **misalignment** (the
optimizer optimizes spot; option ₹ is only scored post-hoc), which produced
calmar-"survivors" that lose real option money (e.g. NIFTY ORR Phase 1: a survivor
at **−₹37,564** full-window option P&L).

## 2. Non-negotiable constraint (user)

This is **opt-in and additive**. When the new mode is OFF, every existing path
(`spot`, `option_rerank`) is **byte-identical** — zero risk to the current working
solution. Only after option-aware is **proven worthy on real results** would we
consider retiring the spot-only path (a future, separate decision — NOT in this
spec).

## 3. What changes (one sentence)

A new `evaluation_mode = "option_aware"` makes the trial objective = *your selected
objective, computed on the paired-option P&L*; everything else (guards, survival
gate, walk-forward, the spot and option_rerank modes) is unchanged.

## 4. Why feasible now

Per-trial option pairing was the 10-hour problem. The vectorized `_walk_option_exit`
(AGF work, ~480×, ~0.5s/candidate) makes pairing inside the trial loop tractable:
~200 trials of pairing ≈ today's K=80 re-rank run ~2.5×. The `analyze_budget_sec`
governance + live ETA already exist to bound it.

## 5. Architecture

### 5.1 One-time option-data load (ADDITIVE — the existing re-rank is NOT touched)

The option-aware trial loop needs contracts + option candles loaded once before the
search. To honor the do-not-disturb constraint, this is **purely additive**: add a
SEPARATE loader `load_option_pairing_context(db, instrument, spot_trades_union,
option_cfg)` → `(contracts, candles_df, candles_by_key, expiry_resolver)`, used
**only** by the option-aware branch. `_option_rerank` is **NOT refactored** — it
keeps its own inline load verbatim. The new loader calls the same low-level helpers
(`build_candles_by_key`, the contract/candle queries, `select_contract_for_signal`)
but modifies no existing function. (Minor duplication is accepted as the price of
leaving the proven re-rank path literally unchanged.) The option-aware loop calls it
**once** before the search (over the warm-up trade union — see 5.4).

### 5.2 Per-trial option scorer

A new pure-ish helper pairs one trial's spot trades and returns the objective inputs:

```
def score_trial_option(spot_trades, *, contracts, candles_by_key, candles_df,
                       instrument, option_cfg, expiry_resolver) -> dict (option metrics)
```
- Calls `simulate_paired_option_trades(...)` with the SAME fixed `option_cfg`
  (exit_mode, target/stop, costs, sizing) the re-rank uses — reusing
  `candles_by_key` (no re-grouping).
- Returns an **objective-ready option-metrics dict** with the keys
  `_objective_value` consumes: `trade_count` (= paired_trade_count), `total_pnl_pts`
  (= option pts), an explicit **net option ₹** field, `win_rate`, `profit_factor`,
  `sharpe`, `max_dd_pts`, `ce_count`, `pe_count` — computed on the paired option
  trades. Risk metrics (`sharpe`/`max_dd`/return%) come from the **rupee equity
  curve** built by `app/portfolio.py` (the SAME builder `survival.py` already uses),
  so option risk-adjusted scoring is consistent with the survival gate.

### 5.3 The objective mapping (`_option_objective_value`)

A dedicated scorer mirrors `_objective_value`'s objective→metric switch but on option
metrics, so the user's dropdown selection is honored on option P&L:

| Objective | Spot today | Option-aware |
|---|---|---|
| `net_pnl_inr` | `total_pnl_pts × lot_size` | **real option ₹** (`total_option_pnl_value`, already × lots × lot_size — NOT re-multiplied) |
| `total_pnl_pts` | spot pts | option pts |
| `sharpe` | spot sharpe | option-equity sharpe |
| `risk_adjusted` (default) | spot sharpe/dd | option-equity sharpe / option maxDD |
| `profit_factor` | spot | option gross-profit / |gross-loss| |
| `win_rate` | spot | option win_rate |
| `neg_max_dd` | spot maxDD | option-equity maxDD |

Guards apply to **option** trades: `_DISQUALIFY` when paired_trade_count == 0,
< min_trades, or option CE/PE minority share < min_direction_share. (The
`net_pnl_inr` double-`lot_size` hazard is why this is a dedicated function, not a
reuse of `_objective_value` with a re-keyed dict.)

### 5.4 Trial-loop integration (`run_optimization`)

Add a branch keyed on `evaluation_mode == "option_aware"`:
- **Before the loop:** run a single warm-up evaluation at default params to get a
  representative trade set, then `load_option_pairing_context(...)` ONCE. (Contracts
  are loaded for the whole window; candles for the union of strikes the warm-up
  touched + the band — the same windowing the re-rank uses. Trials that wander to
  strikes outside the loaded band degrade gracefully to "unpaired" — acceptable for
  a proof; a later phase can widen the band.)
- **Per trial:** spot backtest → trades (as today) → `score_trial_option(...)` →
  `_option_objective_value(option_metrics, objective, ...)` → the trial's score.
  `trial_history` records both the spot metrics and the option metrics.
- **Phase A is SEQUENTIAL** (`opt_workers` ignored for option_aware). Per-trial
  option pairing inside fork workers (sharing candles_by_key) is a later
  optimization; sequential + the analyze budget is the safe, tractable proof path.
- **Default invariant:** when `evaluation_mode != "option_aware"`, this branch is
  never entered → spot and option_rerank are byte-identical.

### 5.5 Analyzing-stage reconciliation

In option_aware mode the trials are ALREADY option-ranked, so the post-search
`_option_rerank` (which re-pairs top-K spot candidates) is redundant. The analyzing
stage becomes: take the top-N trials by their option objective, run the **survival
gate** (`_survival_eval_oos`, unchanged) on them, and finalize. The survival gate +
optional walk-forward remain the OOS guardrails — *more important than ever*, since
the search now fits option ₹ in-sample. (Implementation: gate the rerank call so
option_aware uses an option-metrics-carrying candidate list straight into survival.)

## 6. Frontend

- Add `option_aware` to the evaluation-mode selector (alongside `spot`,
  `option_rerank`). Selecting it shows the same option sub-panel (moneyness/DTE/lots/
  exit) as `option_rerank` and keeps the objective dropdown (now labeled as applying
  to option P&L). A short note: *"Each trial is scored on real paired-option ₹ — the
  search optimizes option outcomes directly. Slower; survival/walk-forward still
  required."*
- Payload: `evaluation_mode: "option_aware"` + the same `option_config` +
  `survival_config` shape already sent for `option_rerank`.

## 7. Invariants / acceptance

1. **Byte-identical default + ZERO edits to existing paths.** The ONLY edits to
   existing code are (a) a guarded `if evaluation_mode == "option_aware": <new branch>`
   dispatch that leaves the else-path untouched, and (b) the frontend selector gaining
   one new option. No existing function (`_evaluate`, the spot trial loop,
   `_option_rerank`, `simulate_paired_option_trades`, the survival gate) is modified.
   `evaluation_mode != "option_aware"` → spot & option_rerank results, persistence, and
   timings are exactly as today (inertness invariant, same discipline as the
   analyze-budget work).
2. **Objective parity (host test).** `_option_objective_value` on a synthetic option
   sim result returns the expected score per objective (esp. net_pnl_inr = option ₹,
   not ×lot²); guards disqualify correctly on option trades.
3. **Steering works (stack).** An option_aware run's `best` is option-₹-ranked; its
   `best_option_pnl_value` is the trial-objective winner, and survival still gates.
4. **Budget honored.** The analyze/search budget stops a long option_aware run with
   a flagged partial (reuse the AGF governance).

## 8. Phasing

- **Phase A (this spec):** option_aware **single-run**, sequential, opt-in, host-
  tested + stack-verified. Default paths byte-identical.
- **Phase B (proof, controller-run):** A/B on NIFTY + SENSEX — same strategy/window,
  `option_rerank` vs `option_aware`. Worth test = does option_aware surface an
  option-₹ survivor (positive full-window AND OOS) that the spot path missed? Record
  the verdict.
- **Phase C (future, gated on B):** parallelize per-trial pairing; extend to walk-
  forward; and ONLY if clearly superior, consider making option_aware default /
  retiring spot. Not in scope now.

## 9. Out of scope (now)

Walk-forward option-aware; parallel-worker option-aware; retiring the spot path;
widening the loaded strike band beyond the warm-up/re-rank window; option-aware for
the `grid`/`genetic` methods (bayesian first).

## 10. Risks

- **Overfitting** — option ₹ is fit in-sample; survival OOS + walk-forward are
  mandatory before trusting any option_aware winner. The UI note + the existing
  trust scorecard cover this.
- **Speed** — sequential per-trial pairing; bounded by `analyze_budget_sec`. If a
  proof run is too slow, lower n_trials or trust early-stop; parallelization is
  Phase C.
- **Strike-band coverage** — trials wandering outside the warm-up band pair fewer
  trades (graceful degrade); flagged via coverage, widenable later.
- **It may still find no edge** — that's a valid, now-trustworthy outcome (the whole
  point: remove the misalignment confound).
