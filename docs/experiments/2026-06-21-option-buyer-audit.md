# Option-Buyer Strategy Audit — Experimental Ledger

**Started:** 2026-06-21 · **Goal:** find a profitable option-BUYING strategy for NIFTY/SENSEX, rank all strategies by option-buying fitness, retire the unfit. **Backdrop:** prior honest walk-forwards killed confluence/SEB/ORF/ARS/ORR option-buying (bottleneck = directional signal too weak to beat theta+spread); this audit makes the verdict definitive and surfaces any untested winner.

## Protocol — maturity ladder (cheap → expensive, early-exit)

| Rung | Config | Gate to advance |
|---|---|---|
| **L1 Scout** | NIFTY, 2025-09→2026-06, option_rerank, ATM/nearest-wk/1lot/spot_exit, costs, survival(calmar), guards(≥20 trades/≥20% minority), 100 trials, `top_k=50`+diversity, 20-min budget, `opt_workers=1` | best option-₹ **> 0** (else RETIRE) |
| **L2 Refine** | warm-started: narrow bounds around L1's best region for high-impact params, FIX low-impact ones (`param_overrides`), 150 trials, `top_k=80` | a config that **survives AND is full-window option-positive** (else RETIRE) |
| **L3 Verdict** | **walk-forward option-aware, DEFAULT bounds** (unbiased), train60/test20/rolling/6 windows | option-OOS **>0 AND ≥4/6 windows positive AND stable params** → KEEP; else RETIRE (mirage) |
| **L4 Generalize** | repeat L3 on **SENSEX** | same KEEP gate |

**Guardrail:** L1/L2 are in-sample FILTERS, never verdicts. The verdict is ALWAYS L3 (walk-forward, default bounds) — in-sample narrowing cannot bias it. (This is what separated ORR's −₹101k OOS from its +₹14.6k in-sample mirage.)

**Cumulative engine:** (1) between rungs I read the top-N trials → tight-clustered params = important (narrow around best), spread params = unimportant (fix); (2) this ledger is the durable cross-session memory (results JSON at `docs/experiments/option-buyer-audit-results.json`); (3) learnings from advancers seed later strategies' L1 bounds.

**Execution:** one run at a time, sequential; poll ~45s; stuck-watch (wall-clock > budget+margin & not terminal → flag + stop for manual restart); orchestrator appends to the results JSON after each run.

## Scope (decision 2026-06-21): 4 untested buyer-friendly first, then decide.
`opening_range_breakout` · `fibonacci_pullback` · `smc_liquidity_sweep_fvg` · `explosive_reversal`

## Speed fix (2026-06-21)
L1 screen runs with **survival OFF** (it's an in-sample filter, not a deploy gate — saves the ~5-min survival OOS pass), **`opt_workers=8`** (parallel search, ~800% CPU), **`top_k=40`**. Each scout ~1.5–7 min (vs ~7 with survival). NOTE: `opt_workers` only parallelizes the trial SEARCH, not the analyzing re-rank; and the candle-prep before the re-rank loop is NOT budget-protected (a heavy `top_k` can hang it — keep `top_k` modest). The analyzing-stage-ignores-pause + prep-not-budgeted gaps are real product follow-ups.

## Ledger

### L1 screen — NIFTY (in-sample triage, complete 2026-06-21)
| Strategy | best opt-₹ | positive / 40 | L1 verdict |
|---|---|---|---|
| opening_range_breakout | +₹2,554 | 2 | RETIRE (noise) |
| fibonacci_pullback | −₹27,565 | 0 | RETIRE (dead) |
| explosive_reversal | +₹2,841 | 1/40 qualified (barely trades) | RETIRE |
| **smc_liquidity_sweep_fvg** | **+₹50,996** | **18** | **ADVANCE** |

`smc_fvg` is the sole L1 survivor → **L3 walk-forward (option-aware) running** (the decisive verdict; L2 refine skipped for speed — WFO re-optimizes per window with default bounds anyway). Its best L1 params: sweep_lookback 17, min_displacement_atr 0.45, fvg_lookback 5, require_fvg true, signal_threshold 37, spot_target 193 / spot_stop 31.

### L3 verdict (walk-forward)
_(smc_fvg WFO in progress — KEEP iff option-OOS net > 0 AND ≥⅔ of windows positive; else RETIRE = mirage)_

## Cross-strategy learnings
_(to be filled as strategies climb)_
