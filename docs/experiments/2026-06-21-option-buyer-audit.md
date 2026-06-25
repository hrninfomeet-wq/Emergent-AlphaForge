# Option-Buyer Strategy Audit — Formal Retire Table

**Date:** 2026-06-21
**Instrument:** NIFTY (2025-09-01 → 2026-06-19), ATM, spot_exit, costs on (1.0% spread)
**Question:** For each of the 12 registered strategies, can ANY optimized config produce
positive option-BUYING P&L? Rank by fitness; retire the unfit.
**Robustness of this audit (adversarial-reviewed):** MEDIUM — see Caveats.

## Maturity ladder

- **L1 (scout):** option-rerank screen — survival OFF, `opt_workers=8`, small `rerank_top_k`,
  `n_trials` 50–100, `rerank_diversity` on. Selection ranks on **real paired-option net rupee**
  (`option_pnl_value`). ADVANCE if ≥3 reranked candidates option-positive OR best > ₹5,000.
- **L3 (verdict):** walk-forward (`/optimize/wfo`, `option_aware`) — 60train/20test rolling, 6 windows.
  **Important:** each window optimizes a **spot** objective (`net_pnl_inr = total_pnl_pts × lot_size`,
  no theta/spread — `optimizer.py:152`); options are paired **post-hoc** on the stitched OOS spot
  trades as a rupee reality-check (`wfo.py:783`). So L3 measures *the option P&L of the spot-optimal
  config*, not of an option-optimized buyer.

## Verdict table (option-BUYING fitness)

| # | Strategy | Family | L1 (option-selected, in-sample) | L3 walk-forward (spot-opt, options post-hoc) | Verdict |
|---|----------|--------|----------------------------------|----------------------------------------------|---------|
| 1 | smc_liquidity_sweep_fvg | SMC/FVG | +₹50,996 / 18-of-40 ADVANCE | **−₹114,304 · 0/6 windows · spot_eff −0.618** | **RETIRE** (proven) |
| 2 | opening_range_regime_router (ORR) | OR regime-router | +₹29,255 / 8-of-20 ADVANCE | **−₹101k · ~0/6** (prior run) | **RETIRE** (proven) |
| 3 | vwap_pullback_scalp | VWAP scalp | +₹7,946 / 2-of-8 ADVANCE | **−₹103,157 · 1/6 · spot_OOS +874.5pt / eff +0.594** | **RETIRE** (proven) |
| 4 | vwap_mean_reversion | mean-reversion | −₹40,510 / 0-of-8 | — | **RETIRE** (proven) |
| 5 | fibonacci_pullback | pullback | −₹27,565 / 0-of-40 | — | **RETIRE** (proven) |
| 6 | squeeze_expansion_breakout (SEB) | vol breakout | −₹4,081 / 0-of-20 | — | **RETIRE** (proven) |
| 7 | adaptive_regime_scalper (ARS) | regime scalp | −₹479 / 0-of-8 | — | **RETIRE** (friction-limited) |
| 8 | opening_range_breakout (ORB) | OR breakout | +₹2,554 / 2-of-40 (median −32,908) | — | **RETIRE** (friction-limited) |
| 9 | explosive_reversal | momentum reversal | +₹2,841 / **only 1-of-40 qualified** | — | **RETIRE — PROVISIONAL** ⚠️ never fairly measured |
| 10 | opening_range_adaptive (ORF) | OR adaptive | **STUCK** (O(N²) prep-hang, never scored) | — | **RETIRE — PROVISIONAL** ⚠️ unmeasured |
| 11 | gap_fade | gap mean-reversion | **STUCK ×2** (prep-hang, never scored) | — | **RETIRE — PROVISIONAL** (buyer-hostile + sibling neg) |
| 12 | confluence_scalper | multi-confluence scalp | not re-screened (known −41% loser) | — | **RETIRE** (known prior evidence) |

**Tiers:** *proven* = WFO-failed OOS or clearly-negative on the option-selected screen; *friction-limited*
= near-break-even, can't cleanly separate no-edge from friction; *provisional* = retired by logic/prior
evidence, **not** by a completed fair screen (the one set worth re-testing if buying is ever revisited).

## The decisive datum

`vwap_pullback_scalp` is the cleanest proof of the cost barrier: its spot signal **generalized
out-of-sample** (+874 pts, efficiency 0.594 — the best of any candidate), 48% option win rate, and it
**still lost ₹103k as a buyer.** Even a real, generalizing directional edge does not survive
ATM + spot_exit + 1% spread option-buying friction. The structural edge is on the **sell** side.

## Caveats (verified against code in adversarial review)

1. **No option-objective walk-forward exists.** L3 optimizes spot and prices options post-hoc
   (`wfo.py:588,783`; `optimizer.py:152`). The −100k/−114k/−103k are the option P&L of the
   *spot-optimal* config, not of an option-optimized buyer. L3 cannot *strictly* refute L1.
2. **Buyer-hostile config throughout.** All screens used `spot_exit` (option force-sold at the spot
   exit candle, eating full theta) + **ATM** (highest theta). The `option_levels` exit (premium
   target/trailing via `_walk_option_exit`) and OTM strikes were never walk-forward-tested.
3. **Two strategies never scored.** ORF and gap_fade hit an **O(N²) candle-prep hang** — a harness
   perf bug: only `opening_range_breakout` is special-cased with a per-session precompute
   (`backtest.py:122`); others recompute per-session constants every bar (`gap_fade.py:63`). Fixable.
4. **explosive_reversal is guard-starved.** Its low-frequency trials are DISQUALIFY-ed by
   `min_trades`/`min_direction_share` before the option rerank → screened on ~1 config, at ATM/spot_exit
   despite an **OTM-convexity** thesis. Its result is not its true option behavior.
5. **Friction double-count.** Default 0.5pt point-slippage is layered on the 1% spread, contradicting
   `option_costs.py:20` ("EITHER/OR"). ~₹65/round-trip/lot — small, but worsens every near-break-even row.
6. **NIFTY-only.** SENSEX/BANKNIFTY (warehouse complete) were not screened.

## Bottom line

**Practically, none of the 12 is a deployable option-buyer** on NIFTY 2025-26 — the 9 fairly-measured
names range from clearly-negative to WFO-killed, and the strongest generalizing directional signal still
lost ₹103k. **The pivot to option SELLING / spreads is well-motivated and recommended.**

But state it precisely: option-buying is **unfavorable and mostly falsified, not strictly proven dead
across all 12** — 3 names (explosive_reversal, ORF, gap_fade) were never fairly screened, and no stage
ever walk-forward-*optimized* a buyer. Treat buying as "unproven, not falsified" for those three.
If buying is ever revisited, the single highest-value follow-up is re-screening **explosive_reversal**
at OTM1 + `option_levels` exit (its thesis was structurally truncated here).

Machine-readable results: `docs/experiments/option-buyer-audit-results.json`.
