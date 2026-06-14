# Survivable Optimization — Design Spec (piece 1 of 3)

**Date:** 2026-06-14
**Status:** Approved design, pre-implementation
**Scope:** Optimizer ↔ Backtest improvement track, **piece 1**: make the optimizer
select strategies that are *survivable* (don't bankrupt the ₹ account), not just
high on a spot-points number.

> This spec was hardened by two review passes: a self-review (10 fixes) and an
> adversarial multi-agent audit (4 blockers + ~14 new issues, code-confirmed).
> The audit verdict was **ship-with-changes**; every change is folded in below.

---

## 1. Problem

The optimizer maximizes a **spot-index-points** objective (e.g. `net_pnl_inr =
total_pnl_pts × lot_size`, or `risk_adjusted = sharpe / max_dd_pts`). But the thing
that bankrupts a trader lives on the **₹-capital option-equity curve**, a different
series. So a strategy can win the metric the optimizer scores and still blow up the
account it never scored.

Concrete case (user's run "Optimized · confluence 10 · band-fix verification"):
+291% return on capital, win rate 53%, **but account equity reached −₹49,130** —
i.e. the account went *negative*, untradeable in practice. It was optimized for
`net_pnl_inr`. The well-behaved counter-example ("band-fix verification test
replicate result") had +156% RoC with a bearable −38% drawdown and equity never
below +₹157,014 — the "sweet spot" we want the optimizer to find *systematically*.

Even the existing `risk_adjusted` objective is a **soft ratio on spot points**
(`max_dd_pts`), not a hard survival constraint on the ₹ equity. There is no
mechanism that says "never select a strategy that ruins the account."

## 2. Goal (rephrased objective)

Evolve the optimizer from *"maximize a spot-points number"* into a **capital-aware,
risk-constrained search**: among strategies that **survive** hard constraints on the
₹ option-equity curve, pick the one that maximizes a **configurable objective**
(risk-adjusted return *or* total ₹), and prove survival **out-of-sample** so the
winner generalizes.

## 3. Decisions (locked)

| Decision | Choice |
|---|---|
| Sequence | All three pieces; **survivable optimization first**, then exit/risk controls, then integrated loop. |
| Survival rule | **Absolute equity-floor (primary)** + **max-drawdown-% cap** + **risk-of-ruin** (all three). |
| Equity floor | Hard floor at **₹0** (reject if realized equity ever ≤ 0). RoR's ruin level is margin-relevant, not 0. |
| Win metric | **Configurable**: Calmar (risk-adjusted, default) **or** Total ₹ — *both* run under the survival gate. |
| OOS evaluation | **Per walk-forward fold** (most rigorous; reuses existing WFO). |
| Architecture | **Approach A**: gate at the finalist/rerank stage. **Approach B** (capital-aware trials) documented as the future upgrade. |

## 4. Architecture — Approach A (gate at the rerank stage)

The optimizer already runs fast **spot trials**, then re-ranks the top-K candidates
on **real paired-option ₹** in [`_option_rerank`](../../../backend/app/optimizer.py)
(optimizer.py:517) — which re-runs each candidate's spot backtest, loads option
contracts once, and calls `simulate_paired_option_trades` to produce per-trade ₹
P&L. The survival gate lives **inside that existing loop**. Trials stay fast; the
search is unchanged when survival mode is off.

**Data flow (survival mode ON):**

```
spot trials (unchanged) ─▶ select top-K finalists (K widened, e.g. 50→80)
   └▶ for each finalist, per walk-forward OOS fold:
        run paired-option backtest on the fold's OOS slice
        → sim['portfolio'] (₹ equity curve) + sim['trades'] (per-trade ₹)   [REUSED, not recomputed]
   └▶ survival_verdict(...) over the stitched-OOS ₹ series + per-fold checks
   └▶ keep SURVIVORS with total_return_pct > 0
   └▶ rank survivors by objective (Calmar | net_inr), tie-band → stable key
   └▶ best = top survivor   |   zero survivors → null best + survival_summary
```

## 5. Components

### 5.1 `app/survival.py` (NEW, pure-Python)

Strictly additive. **No** `motor`/`optuna` imports (host-testable like
`app/rerank_select.py`). **Never** changes the signatures of
`build_rupee_equity_curve` or `simulate_paired_option_trades` — `test_portfolio.py`
and option-backtest tests pin them.

- `calmar(return_pct: float, dd_pct: float) -> float`
  Returns `return_pct / max(abs(dd_pct), 5.0)`. Denominator floored at a
  **meaningful 5% DD** (not 1%) so near-zero-DD flukes can't explode the score.
  Units are **percent** (e.g. dd_pct = −12.0), pinned in the docstring/signature.

- `monte_carlo_risk_of_ruin(daily_pnls, capital, ruin_floor, n_paths=10000,
  horizon=None, seed=42) -> {ror_pct, ror_ci_high, insufficient}`
  Bootstraps **per-day** ₹ P&L (preserves loss-streak clustering — i.i.d. per-trade
  bootstrap understates ruin in the *unsafe* direction). Simulates cumulative equity
  paths from `capital` over a **common horizon** across candidates; counts paths
  whose equity ever falls `≤ ruin_floor`. **Path 0 is seeded with the actual
  observed order** so the real min-equity is always counted. Returns the ruin %, a
  binomial **upper CI bound**, and `insufficient=True` when the finite sample is
  below `MIN_TRADES_FOR_RUIN`. Seeded via `numpy.random.default_rng(seed)` →
  reproducible. Fully vectorized; **short-circuited** by the caller when DD already
  failed.

- `survival_verdict(equity_metrics, daily_pnls, curve, cfg, coverage) ->
  {survived, calmar, ror_pct, min_equity, max_dd_pct, reason, flags}`
  Runs guards **first**, then gates **in priority order**:
  1. **Guards (fail-closed):** drop non-finite ₹ values; if finite paired count `==
     0` → `survived=False, insufficient_sample=True`; if `coverage.paired/spot <
     0.8` → `survived=False` (hard disqualifier, not a flag — pairing fails on the
     worst/illiquid trades exactly when ruin happens); non-finite metrics →
     `survived=False, reason="non_finite"`.
  2. **PRIMARY — absolute floor:** `min(curve.equity_value) <= cfg.min_equity`
     (default ₹0) → reject. Deterministic; this is what catches the −₹49k case.
  3. **DD% cap:** `abs(max_dd_pct) > cfg.max_drawdown_pct` → reject. **Magnitude
     compare** — `max_dd_pct` is a NEGATIVE number (portfolio.py:160,198), so a
     naive `max_dd_pct <= cap` is always true and would pass every blown account.
  4. **Risk-of-ruin:** `ror_ci_high > cfg.max_ror_pct` → reject (fail-closed:
     "can't prove safe" = not a survivor).
  `MIN_TRADES_FOR_RUIN` is derived **once** from the finite list and is ≥100 (a tail
  statistic needs more than the spot `min_trades=10`, which counts *spot* trades).

### 5.2 Config — `survival_config` (NEW optional payload block)

```jsonc
survival_config: {
  enabled: false,            // default OFF → optimizer behaves byte-identically to today
  min_equity: 0,             // PRIMARY gate: reject if realized ₹ equity ever ≤ this
  max_drawdown_pct: 35,      // secondary: reject if |peak DD%| exceeds this
  max_ror_pct: 5,            // reject if RoR upper-CI exceeds this
  ruin_floor: 0,             // RoR ruin level; default ₹0 (account wiped). Raise to model
                             //   "too little left to trade" (e.g. one lot's premium ≈ ₹10–15k). Validated 0 ≤ ruin_floor < capital.
  objective: "calmar",       // "calmar" | "net_inr" — survivor ranking metric
  min_oos_folds: "all"       // folds the floor+DD% must hold in ("all" | majority count)
}
```

The Monte-Carlo RoR **always** uses the per-day (clustering-aware) bootstrap — it is
strictly safer than per-trade i.i.d., so there is no toggle (YAGNI).

### 5.3 Optimizer integration ([optimizer.py](../../../backend/app/optimizer.py))

- In `_option_rerank` (the existing finalist loop ~617–640): **capture** `port =
  sim.get("portfolio")` and `trade_pnls = [t["option_pnl_value"] for t in
  sim.get("trades", []) if t.get("status") == "PAIRED"]`. These already exist in the
  sim return (option_backtest.py:524–528) but are currently **discarded** — capture
  them; **reuse** `sim["portfolio"]` (do not re-call `build_rupee_equity_curve`).
- **OOS per fold:** reuse the existing `walk_forward` fold boundaries; for each
  finalist, run the paired-option evaluation on each walk-forward **OOS** (test)
  slice. Floor + DD% must hold in `min_oos_folds`; RoR is computed once on the
  **stitched OOS** ₹ series (chronologically concatenated OOS segments) for sample
  size. **Performance:** this is ≈ `n_folds`× the option backtests of today's
  single-window rerank (option contracts are still loaded once); with K≈80 and
  `n_folds=3` that is ~240 paired-option sims at the finalist stage. Acceptable on
  the async optimizer worker, but the implementation must keep the option-data load
  shared across folds and short-circuit RoR when a fold already fails the floor/DD.
- Keep only survivors with `total_return_pct > 0`; rank by `survival_config.objective`
  with a **tie-band** → stable secondary key (net_inr, then paired_trade_count).
- **Do NOT swap the trial objective.** The user's chosen `objective` is preserved.
  Densify survivors by **widening K** (real default is 50; raise to ~80) and/or
  `rerank_diversity=True`. (A future explicit `survival_config.trial_objective` could
  bias the search, defaulting to the user's objective — not in this piece.)
- **Zero-survivor guard** — an **independent block gated behind
  `survival_config.enabled`**, placed after `ranked` is built and **before** the save
  (~optimizer.py:940), NOT threaded into the existing best-promotion branch (keeps
  `enabled=false` byte-identical): blank `best_so_far["params"]`, set
  `best_so_far["value"]` to the `≤ -1e8` sentinel (so `best_value → None`), persist
  `survival_summary {survivors: 0, reason, suggestions}`, and set a distinct
  `final_status = "done_no_survivor"` (or a `survivors` flag the UI reads).
- Persist `survival_config` + per-candidate survival fields + `survival_summary` into
  the job doc and the saved best backtest.

### 5.4 Validation ([routers/research.py](../../../backend/app/routers/research.py), in the contract corpus)

When `survival_config.enabled`, the optimize-start endpoint returns clear **400s**:
- requires `evaluation_mode == "option_rerank"` (the gate lives there),
- requires option execution enabled (₹ equity is impossible spot-only),
- requires `costs_enabled == true` (else RoR/Calmar run on **gross** P&L — fatal for
  Indian index-option scalping where round-trip charges eat the edge),
- requires `0 <= ruin_floor < capital` and `max_drawdown_pct`, `max_ror_pct` in sane
  ranges.

`survival_config` and `survival_summary` shapes are pinned in
[schemas.py](../../../backend/app/schemas.py) so contract source-text asserts can see
them (`optimizer.py`/`portfolio.py` are invisible to the corpus).

### 5.5 Frontend ([Optimizer.jsx](../../../frontend/src/pages/Optimizer.jsx))

- **Setup — Survivability panel:** toggle; min-equity floor (default ₹0); DD% cap
  (35); max RoR % (5); objective pick (Calmar default / Total ₹); advanced
  (ruin_floor, min_oos_folds). Enabling it surfaces the requirement that option
  execution + costs must be on (mirrors the 400 validation).
- **Results:** per-finalist **Survived / Disqualified (reason)** badge; the ₹ Calmar,
  ₹ min-equity, ₹ max-DD%, RoR (with CI); a **return-vs-drawdown scatter** of all
  finalists so the user picks the knee; the honest **0-survivor** message +
  suggestions (loosen cap, widen bounds, extend window, lower RoR target). The chosen
  survivor flows into "Apply as preset" unchanged.

## 6. Error handling

- Survival on + option exec/costs off / bad `ruin_floor` → 400 (no silent config
  mutation).
- 0 survivors → honest `survival_summary`, null best, distinct status — never
  silently return the least-bad as "best".
- Insufficient sample / low coverage / non-finite → candidate marked not-a-survivor
  with a reason; surfaced in `survival_summary.suggestions`.

## 7. Determinism & backward compatibility

- The survival **verdict** is deterministic (seeded MC, fixed inputs). The optimizer
  **search** (Optuna TPE) stays stochastic exactly as today.
- `survival_config.enabled = false` ⇒ **byte-identical** behavior to today. The gate
  is strictly additive and behind the flag; `survival.py` is a new module; the
  signatures of `build_rupee_equity_curve` and `simulate_paired_option_trades` are
  untouched (existing tests pin them).

## 8. Testing

- **Unit (`survival.py`, host-safe):** the −40%-vs-cap-35 **sign regression**
  (`survived=False`); the +291%/−124% case → `survived=False`; absolute-floor catches
  `min_equity ≤ 0`; NaN-laced and empty and 1-trade inputs → not a survivor; seed
  reproducibility (same inputs → same `ror_pct`); RoR CI fail-closed; `n` in
  {0,1,99,100,101} around `MIN_TRADES_FOR_RUIN`; `ruin_floor ≥ capital` rejected.
- **Contract:** `survival_config` / `survival_summary` fields present in schemas +
  router validation strings; the validation 400s.
- **Backward compat:** existing optimizer/portfolio tests stay green; `enabled=false`
  path unchanged.

## 9. Audit findings → resolutions (traceability)

| # | Finding (severity) | Resolution |
|---|---|---|
| B1 | DD% gate is a no-op (negative sign) | Magnitude compare `abs(dd) > cap` + sign regression test |
| B2 | Gate can't read its inputs (sim trades/portfolio discarded) | Capture + reuse `sim["portfolio"]`/`trades` in the existing loop |
| B3 | From-peak DD% wrong axis for the −49k ruin | **Absolute ₹0 floor as PRIMARY gate** |
| B4 | In-sample double-selection overfits | **Per-fold OOS** survival evaluation |
| H | i.i.d. bootstrap understates ruin | **Per-day** bootstrap; gate on worse of {realized, MC} |
| H | Calmar fluke ranking | Floor denom at 5% DD; require `return>0`; tie-band |
| H | Silent trial-objective swap | **Removed** — widen K / diversity instead |
| H | RoR/Calmar on gross P&L | Hard-require `costs_enabled=true` |
| H | NaN/zero-trade false survivor | Guards first, fail-closed |
| M | Zero-survivor still promotes spot best | Independent `enabled`-gated null-best block |
| M | Low coverage advisory only | **Hard** disqualifier (<0.8) |
| M | RoR horizon/`n_paths` under-spec | Common horizon; `n_paths≥10k`; CI fail-closed; short-circuit |
| M | `ruin_floor≥capital` silent disable | Router 400 validation |
| L | All-survivors-losers promotes a loser | Require `total_return_pct>0` to promote |
| — | Mapper false claims | Corrected: `_option_rerank` exists (optimizer.py:517); tests DO import `portfolio.py`/`option_backtest.py` |

## 10. Out of scope (this piece) / future

- **Approach B — capital-aware trials** (run the option sim inside every trial so the
  *search* directly optimizes ₹ survival). The documented upgrade if survivor density
  proves thin under Approach A. Cost: 3–10× slower trials, needs full option-data
  coverage.
- **Piece 2 — exit/risk controls:** trailing / breakeven / move-SL-on-profit, and
  per-day loss/target/max-trades caps *inside* the backtest sim (today they exist only
  in live deployment kill-switches). Own spec.
- **Piece 3 — integrated Optimizer↔Backtest loop:** optimize → validate-OOS → accept,
  with the survival constraints wired into the accept/reject gate. Own spec.
- No real broker orders — permanent. All simulation.
