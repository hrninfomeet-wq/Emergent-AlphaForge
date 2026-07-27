# Profit-leverage analysis — where AlphaForge's edge could actually come from

**Date:** 2026-07-27 · **Item 6 of the user program** · Status: DRAFT (agent inventories pending)

> **Scope note.** This is an engineering and research-design document about what *the
> software* can and cannot express, and about what its own accumulated backtest evidence
> already shows. It is not investment advice, and it does not recommend any position in
> any instrument. Where it says "test this", it means run the experiment through the
> app's existing three-way-split harness and let the pre-registered kill criterion decide.

---

## 0. The decision this document is trying to inform

Three separate edge campaigns have now ended in a documented NO:

| Campaign | Verdict | Where |
|---|---|---|
| Option-buying survival gate (confluence / SEB / ORF, NIFTY 2025-26) | No deployable survivor | [[option-buying-edge-hunt-2026]] |
| Premium-momentum / EXP2 (~600 configs, 5 stages) | **GATE FAILED** on untouched 2026 holdout | `docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md` |
| Tiered profit protection | Replay says STOP (hurts by up to −₹1.07M) | [[architecture-audit-exit-redesign-2026]] |

The standing question is what to do next. The wrong answer — and the tempting one — is a
fourth sweep of the same shape with different parameters. This document argues that the
three failures share a *structural* cause that no parameter search can reach, and lays out
the alternatives in order of information-per-rupee-of-build.

---

## 1. The finding that reframes everything: the app can only ever buy premium

This is not a strategy choice. It is hard-coded in three independent places:

| Layer | Constraint | Evidence |
|---|---|---|
| Signal | A strategy emits `direction: "CE" \| "PE" \| "NONE"` — there is no *side* | `backend/app/strategies/base.py:21` |
| Backtest P&L | `pnl_pts = exit_price - entry_price` — the long convention, no sign | `backend/app/option_backtest.py:749` |
| Live execution | `side="B"` **always**; the comment says "long-only" outright | `backend/app/auto_live.py:449,483` |

A repo-wide search for short-side vocabulary (`SELL_CALL`, `short_premium`, `write_option`,
`sell_to_open`) returns **nothing**. Even the cost model states its assumption in its own
docstring: *"For an options-buying strategy…"* (`backend/app/option_costs.py:3`).

So every one of the ~600 premium-momentum configurations, and every configuration of the
three families before it, was a variant *within a single family*: **pay premium, hope the
underlying moves enough, fast enough, to cover decay and friction.**

## 2. What the failures were actually measuring

Read the verdict's own numbers again, but as a *measurement* rather than as a scorecard:

> "Gross points on the holdout are **−798** for the 'best' config — the marks themselves
> are negative **before a single rupee of friction**."
> — `docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`

And the standing conclusion:

> "Buying option premium AFTER a 10-25% spike pays the momentum-chaser's tax: entries are
> systematically into decaying, spread-widened premium."

Across 128 untouched holdout sessions, ~600 configurations, five structural stages, three
friction levels and multiple volatility regimes, the premium paid **systematically exceeded
the movement realized**. Train negative, holdout negative; only a period-specific
validation slice was positive, and the three-way split correctly exposed that as luck.

That is a remarkably consistent, expensively-obtained empirical result — and the app has
been standing on exactly one side of it the whole time.

## 3. The honest counter-argument, stated before the idea

The naive inference is "then do the opposite." **That inference is wrong as stated**, and
this document would be irresponsible if it did not say so first:

1. **Friction is paid in both directions.** The 1%/side spread that killed the long
   configs taxes the other side too. A −798-point gross does not become +798 net.
2. **The return distribution inverts, and it inverts into the dangerous shape.** Short
   premium produces many small wins and rare large losses. That shape is *flattering* to a
   20-month backtest and is the classic way option sellers are ruined. The warehouse
   starts 2024-11-25 — it is a question of fact, not opinion, how many genuine tail days
   it contains, and the answer is probably "very few."
3. **The capital constraint changes completely.** Long premium risks the outlay; short
   premium consumes SPAN + exposure margin. Return-on-capital is a different calculation
   and the app's sizing/capital gate is premium-outlay based.
4. **The app's entire risk machinery assumes bounded loss.** Max-loss-equals-premium-paid
   is true for a long and false for a naked short.

Points 2 and 4 together dictate the only defensible form of the experiment:
**defined-risk spreads only — never a naked short** — and a kill criterion that judges the
*tail*, not just the net.

## 3a. What it would actually cost to express the short side (measured)

A file-and-line inventory of the change surface. **Headline: ~20 files, ~30 functions for
shorts alone**, and defined-risk spreads add materially more on top.

**The block is deliberate, not accidental.** `backend/app/live/executor.py:264,461` reject
`side != "B"` with a documented rationale:

> "a sell entry would open an unprotected naked short; the SL backstop (always a
> sell-to-close) would then GROW the short instead of closing it."

**Three genuinely hard problems** (design work, not parameter-threading):

1. **There is no offline margin model, and one cannot be borrowed.** `broker_margin_verdict`
   (`live/margin.py:196-265`) uses the broker's real SPAN+exposure figure — but
   `GetOrderMargin` is a live, authenticated, real-time call (verified against
   `docs/Resources/flattrade-pi-api/endpoints/08-order-margin.md`) and **cannot be replayed
   against historical dates**. A repo-wide search for `SPAN`/`span_margin`/`exposure_margin`
   found no estimator. So a short-side backtest cannot honestly size a position or state
   return-on-capital without building a new margin approximation — itself unvalidated
   quantitative work that gates sizing, the capital gate, and every "capital required" number.
2. **The live trailing state machine would need a hand-mirrored twin.**
   `live/live_sl_monitor.py:75-254` implements five trailing modes
   (`breakeven/lock/lock_trail/trail/stepped_xy`), each hard-wired to "peak only rises, stop
   only rises" with a documented monotonic-non-decreasing invariant. Every mode needs its
   mirror, and both variants must then coexist per position.
3. **Multi-leg does not exist — and the codebase's own attempt proves it.**
   `premium_momentum`'s `leg_mode="both"` is **not** a spread: `premium_momentum_backtest.py:440-478`
   runs `walk_premium_momentum` independently per side and keeps both results as separate
   trades, each with its own `apply_costs_to_trade`. No shared position, no netting, no
   combined P&L. A real spread needs either linked legs with a `combo_id` stitched together
   at *every* read site (equity curve, journals, open positions, kill-switch flatten) or a
   new first-class multi-leg document.

**Also found — a latent hazard worth fixing regardless of any of this:** the long P&L
convention `(price - entry) * qty` is **independently reimplemented in four places**
(`option_backtest.py:749`, `premium_momentum.py:208`, `paper_trading.py:146`,
`paper_open_positions.py:36`). There is no P&L chokepoint. That duplication is both a
blast-radius multiplier and a standing correctness risk.

**Already there (would not need building):** `exit_engine.intrabar_exit:24-51` already has a
working `is_long=False` branch — the exact formula a short premium walk needs, just never
invoked that way. `live_friction.fill_premium` is already side-parameterized.
`gtt.build_oco_intent` already takes `trantype` as a real parameter. The whole
flatten/kill-switch/EOD layer is **already sign-aware** (`trantype = "S" if netqty > 0 else "B"`,
read off the broker's real position book) — an accidental short would already be squared
correctly. `evaluate_guardrails` is sign-agnostic. And `execution_policy.spot_mirror_levels`
already implements a complete long/short mirror (`sign = 1.0 if CE else -1.0`) for the
spot-mirror exit mode — proof the authors know the pattern; it simply never reached the
option-premium engine.

## 3b. Signal that already exists but no strategy can see

A second inventory asked what the app computes that a strategy cannot use. The answer is
"a lot", and the codebase already documents its own gap: `app/ai/capability.py:213-217`
lists `oi`, `pcr`, `max_pain`, `iv`, `iv_rank`, `theta`, `vega`, `gamma`, `delta` as
`NEEDS_NEW_DATA` — the AI authoring wizard actively refuses to let users write rules
against them.

| Quantity | Computed at | Why no strategy can use it |
|---|---|---|
| IV, delta/gamma/theta/vega | `live/greeks.py:43-124` (full BS solve) | Consumed only by the cockpit Greeks card; needs live broker positions + quote. No historical IV series is stored at all. |
| PCR, max pain, ATM straddle | `market_analysis.py:122-194` | Assembled only from live Upstox full-mode tick OI (`market_analysis_build.py:111-146`). Cockpit display only — never reaches a strategy or backtest. |
| IV rank | `market_analysis.py:197` | Always called with `current_iv=None, iv_history=[]` — in practice a **VIX-percentile proxy**, not an IV rank. |
| Open interest | stored per candle (`option_candles.py:53`) | **Written on ingestion and read by nothing.** |
| India VIX | stored as `candles_1m` / `INDIAVIX`; join helpers at `vix.py:47-119` | Used only to tag already-closed trades post-hoc, plus one session-start gate on the premium-momentum path. Never a per-bar column. |
| 6 ICT/SMC structural features | `features/structures.py` (swing levels, premium/discount, displacement, CHoCH, FVG zones, order blocks) | Fully built, registered and adversarially reviewed — **declared by zero shipped strategies.** |

**Two defects surfaced by this inventory and verified directly:**

- **A dead optimizer knob.** `explosive_reversal.py:93` reads `row.get("vix")` — a column
  `indicators.py`/`indicator_groups.py` never create. Its `vix_boost_threshold` parameter is
  tunable, appears in saved presets, and **does nothing**. Same class as the `early_stop`
  dead-knob found in the earlier architecture audit. Spun off as a separate task.
- **The capability manifest contradicts the ingestion code.** `capability.py:25,27` declare
  `has_oi_history: False` and `has_vix_history: False`, while `option_candles.py:53` writes
  `oi` per candle and `warehouse_autoupdate.py` runs a daily VIX top-up. The manifest drives
  what the AI wizard permits, so it may be **falsely refusing rules against data that exists.**
  Needs a measured answer, not a code reading — see §3c.

## 4. Candidate directions, ranked by information per rupee of build

*(Ranking pending the warehouse measurement in §3c, which determines whether direction A is
testable at all.)*

### A. Express the short side, as defined-risk spreads only — **strategic, largest build**
The single untested structural hypothesis, and the only one that attacks the *named*
bottleneck ("directional signal quality") by not requiring directional accuracy at all.
See §3 for why this must be spreads and why the kill criterion must be tail-aware.

### B. Un-starve regime routing by pooling all three indices — **cheapest decisive test**
Regime routing was judged "option-+EV yet **sample-starved**" — promising-unproven. The
standard reflex (tune harder) makes overfitting worse. The actual fix for a sample problem
is more sample, and BANKNIFTY + SENSEX data is already in the warehouse and barely used by
past research. No new engine capability required; the harness already exists.

### C. Measure and reduce realized friction — **the only guaranteed return**
Friction was decisive, not incidental: it is what moved configs from marginal to dead, and
sensitivity was probed at 0.5 / 1.0 / 1.5% per side. Every basis point of realized
slippage removed is a permanent, compounding improvement to *every future strategy* — and
it is the one improvement that requires predicting nothing. The app already places limit
orders; what is missing is measurement of realized fill vs. model.

### D. Non-directional expiry structure — **second experiment, moderate build**
NIFTY Tue / SENSEX Thu / BANKNIFTY monthly last-Tue expiries. Max-pain is already computed
in the market-analysis primitives. A structural, non-directional thesis rather than another
directional signal.

### E. Recognise loss avoidance as realized value — **already banked, worth stating**
The survival gate and three-way split have now refused three families, including one
sourced from a vendor PDF claiming +₹2.79L that assumed **zero slippage**. Deploying that
on its face was the counterfactual. This is not a consolation prize: a research instrument
whose main output is "don't" is doing its job.

---

## 5. What is explicitly NOT recommended

- **A fourth parameter sweep of a refuted family.** The premium-momentum kill criterion is
  already pre-registered and unchanged: a config net-positive on a NEVER-TOUCHED forward
  window at ≥1%/side friction that also beats plain both-legs there. Until that fires, the
  family stays dead. The tuner remains in the app; that is enough.
- **Building live capability for an unproven edge.** Phase 5B was built as pure capability
  by explicit user decision, with the edge verdict as an advisory — that trade-off is
  already made and is not reopened here.

---

## 6. Pre-registered kill criteria

*(To be completed — each direction gets a criterion written BEFORE the experiment runs,
following the discipline that made the premium-momentum verdict trustworthy: three-way
chronological split, holdout touched exactly once, friction mandatory throughout.)*
