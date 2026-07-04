# Optimizer verdict — 2026-07-05

Evidence-backed answer to "does the optimizer earn its complexity, and are its
results trustworthy for an option-buying app?" Every number below is a live
`POST /api/backtest/run` against the warehouse (real spot + option candles,
statutory costs ON, ATM pairing, `spot_exit`). Params are the exact
`best_params` the optimizer saved for each strategy; DEFAULT = the strategy's
schema defaults. Windows: IS = the optimizer's own training window; OOS-pre =
before it; OOS-post = after it (through 2026-07-03).

## The matrix (paired-option NET ₹, costs on)

| Strategy (instr) | Window | Arm | Trades | Spot pts | **Option NET ₹** | Opt WR% |
|---|---|---|---:|---:|---:|---:|
| confluence_scalper (SENSEX) | IS | OPTIMIZED | 6699 | +14,499 | **−207,190** | 42.4 |
| | IS | DEFAULT | 136 | +263 | −1,470 | 41.9 |
| | OOS-pre | OPTIMIZED | 5494 | +2,043 | **−119,858** | 41.0 |
| | OOS-pre | DEFAULT | 133 | +3.5 | −4,200 | 40.6 |
| | OOS-post | OPTIMIZED | 432 | −268 | **−15,918** | 39.4 |
| | OOS-post | DEFAULT | 11 | +50 | +161 | 45.5 |
| vwap_mean_reversion (NIFTY) | IS | OPTIMIZED | 347 | +183 | −15,509 | 22.2 |
| | IS | DEFAULT | 573 | −534 | −37,607 | 41.7 |
| | OOS-pre | OPTIMIZED | 208 | −381 | −30,215 | 23.1 |
| | OOS-pre | DEFAULT | 318 | −357 | −39,995 | 44.0 |
| | OOS-post | OPTIMIZED | 15 | +50 | −772 | 20.0 |
| | OOS-post | DEFAULT | 31 | −149 | −7,538 | 29.0 |
| smc_liquidity_sweep_fvg (NIFTY) | IS | OPTIMIZED | 325 | +2,875 | **+77,396** | 30.9 |
| | IS | DEFAULT | 2033 | −287 | −186,751 | 35.6 |
| | OOS-pre | OPTIMIZED | 188 | +338 | **+2,823** | 28.2 |
| | OOS-pre | DEFAULT | 1135 | −1,310 | −161,266 | 33.0 |
| | OOS-post | OPTIMIZED | 10 | +144 | −1,362 | 50.0 |
| | OOS-post | DEFAULT | 89 | −87 | −13,373 | 33.7 |

## Verdict

**The optimizer is structurally sound but its default objective is aimed at the
wrong target.** It maximizes a SPOT-rupee proxy (`net_pnl_inr` = `total_pnl_pts ×
lot_size`, a constant rescale of the points objective — the two are
rank-identical, `optimizer.py:161-167`). Spot-optimal is option-optimal **only
when optimization reduces trade frequency**; it is anti-optimal when it raises
frequency, because every extra option round-trip pays cost + slippage that the
spot objective is blind to.

- **confluence_scalper — the danger case.** The optimizer's saved `best_value`
  was **289,772** (a spot ₹ score). Its params drove the strategy from 136 to
  **6,699** trades — spot P&L looks great (+14,499 pts) but the real paired-option
  result is **−207,190**, and stays deeply negative in *both* out-of-sample
  windows. A user reading "289,772" in the optimizer UI would deploy a strategy
  that loses ₹2 lakh. This is the single most important finding.
- **smc_liquidity_sweep_fvg — the success case.** Here optimization *cut* trades
  (2033 → 325) and genuinely flipped option P&L from **−186,751 to +77,396** IS,
  and it **generalizes** (OOS-pre +2,823). When the optimizer prunes, it works.
- **vwap_mean_reversion — no edge.** Optimized beats default in every window, but
  both lose on options. Optimization can't manufacture an edge that isn't there.

The optimizer's own guardrails against this (option **re-rank** mode, which
optimizes on paired-option ₹, and the capital-aware **survival gate**) exist and
work — but none of these historical jobs used them; they optimized on spot and
were read on spot.

## Recommendations (not yet implemented — need your call on UX)

1. **Surface the paired-option NET ₹ next to the spot `best_value` in the
   Optimizer results.** A +289k spot score that is −207k on options must not be
   presentable as a winner. (Backend already computes it in re-rank mode.)
2. **Default to option-net objective / re-rank when option execution is
   intended**, so the search optimizes the number that will actually be traded.
3. **Trade-frequency / per-trade-edge guard:** a config whose per-trade spot edge
   is below ~2× round-trip option cost is a cost-bleed trap regardless of spot P&L.

## Setup-panel knobs — what was verified

- **`early_stop` ("Auto-stop when converged")** — was a **no-op** at every default
  budget (warmup 200 > default 150 trials). **FIXED** 2026-07-05
  (`effective_warmup_patience`, `early_stop.py`), now fires on a real plateau.
- **`mode` (SCALP/INTRADAY)** — *live*, not dead: it flows into the strategy eval
  context (`strategies/base.py:35,50,65`) and builtins read it. Keep.
- **`net_pnl_inr` vs `total_pnl_pts`** — rank-identical objectives (redundant);
  candidate for consolidation, but that's a user-facing UX cut — deferred to you.
- **5 backend knobs with no UI** (option brokerage/spread, survival `ruin_floor`,
  `min_oos_folds`, early-stop tuning) — plumbed but frozen at defaults from
  hardcoded frontend state. Expose or drop per your UX intent — deferred to you.

## Research-path edge cases (traced 2026-07-05)

| # | Edge case | Verdict | Action |
|---|---|---|---|
| 1 | **Option-chain gap at illiquid strike** — preflight `coverage_pct` checked only the ENTRY candle, so it overstated pairing (a strike with an entry print but an exit-side gap counted as would-pair, then dropped as `MISSING_EXIT_CANDLE` in the real run). | SILENT-BUG (highest) | **FIXED** — preflight now requires both entry- AND exit-side candles (`preflight_trade_pairs`), matching the sim's two gates; live-verified preflight `would_pair` == sim `paired` (31=31). |
| 2 | **Warehouse mid-window gap → indicators** — `load_candles_df` returns raw rows with no reindex to a minute grid; `run_backtest` iterates positionally, so a gap makes the post-gap bar positionally adjacent to the pre-gap bar and rolling indicators (ATR/EMA/RSI) compute across the discontinuity with no NaN and no warning. Bounded to bars adjacent to a gap; whole-day gaps are caught by the day-level audit. | SILENT-BUG | **DEFERRED** — real correctness issue but the fix (session-boundary indicator warm-up reset or per-bar `gap_before` flag) touches the indicator warm-up contract and needs parity tests. Tracked. |
| 3 | **Expiry rollover mid-backtest** — the option exit walk has no expiry boundary; it's kept safe only because the spot engine is intraday-bounded (`entry_date == exit_date`), so the option's expiry is always ≥ the exit date. The one exception: a single trailing-EOD trade in the final session can carry `exit_ts` past the option's expiry and consume stale post-expiry candles. | SILENT-OK (narrow) | **DEFERRED** — near-zero incidence; one-line `backstop_ts = min(exit_candle_ts, expiry_end_ts)` clamp when `option_backtest.py` is next touched. |
| 4 | **Session / holiday boundaries** — DTE + expiry resolution are `nse_calendar`-driven (same calendar paper/live use); backtest `TRADE_WINDOW_END="15:00"` matches paper `DEFAULT_SQUARE_OFF_IST=15:00`. | NO BUG | None. |

## Performance

Profiling confirmed the "Analyzing" phase re-ran `build_candles_by_key`
(copy + astype + mergesort + groupby of the whole option frame, **0.20s** on a
758k-row SENSEX window) on **every** `simulate_paired_option_trades` call in the
survival gate and exit-control search — ~**150s** of pure waste at K=50 finalists
(61s at K=20). **FIXED** 2026-07-05: the grouping is built once and threaded
through (byte-identical, pinned by a new test).
