# Profit-leverage analysis — where AlphaForge's edge could actually come from

**Date:** 2026-07-27 · **Item 6 of the user program** · Status: **COMPLETE** — all three inventories measured and merged

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

## 3c. The warehouse, measured — and the finding that settles direction A

Queried directly against Mongo (`alphaforge` db, read-only aggregations over the full
history — not sampled, not read off a manifest).

**Coverage is excellent, and larger than the research has used:**

| | NIFTY | BANKNIFTY | SENSEX | Pooled |
|---|---|---|---|---|
| Spot sessions | 413 | 411 | 411 | 1,235 |
| Option sessions | 408 | 392 | 410 | **1,210** |
| Option candles | 2.04M | 2.34M | 2.92M | 7.30M |

Spot is essentially complete — 374.2 candles/session against an expected 375. Range is
2024-11-25 → today.

**But the option chain is far too thin for spreads. Two measured facts kill direction A:**

1. **Every single day, for every index, stores exactly ONE expiry — 100% of 408/392/410
   days.** There is not one day in the entire two-year history with two concurrent
   expiries. **Calendar spreads are untestable, period.**
2. **Median distinct strikes per day (CE+PE combined) is 6 / 8 / 9** — roughly 3–5 per
   side — spanning about ±1–1.5% of spot. A vertical spread's further-OTM leg exists only
   when it happens to land inside that narrow near-ATM band. Wide verticals and genuine
   tail-hedge legs are **not testable with this data**.

This is the answer to the question §3 left open, and it is decisive:

> **The only defensible form of the short-side experiment (defined-risk spreads) is the one
> the data cannot support. The form the data *can* support (a naked single-leg short) is the
> one §3 argues is indefensible on tail risk — and which the executor already blocks by
> design.**

So direction A is not merely a large build. It is a large build (~20 files, ~30 functions),
*plus* a novel offline margin model, *plus* multi-leg representation, *plus* a historical
data-ingestion campaign for a wider strike band that may not even be purchasable. Four
serial dependencies before the first honest result. **Do not start it.** The cheap,
correctly-scoped first step is a scoped question, not a build: *can a wider strike band and
a second expiry be obtained historically at all, and at what cost?* Everything else stays
blocked behind that answer.

**Two further measured facts that redirect the other options:**

- **India VIX exists for 280 sessions (2025-06-09 → 2026-07-24), gap-free within its
  window — covering the most recent 67.6% of history**, with the first ~13 months blacked
  out. This **confirms the manifest is wrong**: `capability.py:27` says
  `has_vix_history: False` against 104,685 stored VIX candles. The AI wizard is refusing
  VIX rules against data that demonstrably exists.
- **BANKNIFTY has a real, index-specific option gap**: one isolated day (2024-11-27), then
  nothing for ~16 trading days until 2024-12-20. Not present in NIFTY or SENSEX, and not
  present in BANKNIFTY's own spot data. Any pooled study must exclude that window
  explicitly rather than silently averaging over it.

## 4. Candidate directions, ranked by information per rupee of build

The ranking below is what the measurements support, not what is most interesting.

### 1st — **B. Un-starve regime routing by pooling all three indices** *(cheapest decisive test)*
Regime routing is the one signal the project has already judged **"option-+EV yet
sample-starved"** — promising-unproven. The reflex fix (tune harder) makes overfitting
worse; the correct fix for a sample problem is more sample. Pooling gives **1,210 option
index-days against 408 for NIFTY alone — 2.97×** — and requires **zero engine changes**:
the harness, the three-way split and the cost model all already exist.

*Honest caveat, and it must be pre-registered:* the three indices differ in strike step
(50/100/100), lot size (65/35/20) and volatility regime, and they share market-wide
regimes. Pooling triples the raw day count but does **not** produce three i.i.d. copies.
The criterion below is written to catch exactly that.

### 2nd — **C. Measure and reduce realized friction** *(the only guaranteed return)*
Friction was decisive rather than incidental in every campaign — it is what moved configs
from marginal to dead, which is why the verdict probed 0.5 / 1.0 / 1.5% per side. Every
basis point of *realized* slippage removed is a permanent improvement to **every future
strategy**, and it is the one improvement that requires predicting nothing. The app already
places limit orders and already models friction (`live_friction.fill_premium`); what is
missing is measurement of **realized fill versus model**. Paper and live journals already
record entry/exit prices — the comparison is largely an analysis job, not a build.

### 3rd — **D. Non-directional expiry structure** *(the data limitation is not a limitation here)*
Worth a reframe: the warehouse stores **only the front expiry** — fatal for calendars, but
that *is* the expiry a 0DTE/expiry-day study trades. The data is well-matched to this
question, and max-pain is already implemented (`market_analysis.py:139`). Expiries are
staggered across the week (NIFTY Tue / SENSEX Thu / BANKNIFTY monthly last-Tue), so the
three indices give three partly-independent expiry-day samples rather than one.

### 4th — **F. Wire the signal that already exists** *(cheap, unblocks everything above)*
Before hunting new edges, make the existing inputs reachable. VIX is stored for 280
sessions and is **never a per-bar column**; OI is written on every option candle and **read
by nothing**; six ICT/SMC structural features have **zero consumers**. This is not a
strategy — it is removing the reason a whole class of hypotheses is currently unaskable,
and it fixes a live defect (the dead `vix_boost_threshold` knob) on the way.

### Deferred — **A. Short side via defined-risk spreads**
Blocked on data (§3c). Reduce to the scoped procurement question first.

### Standing — **E. Loss avoidance is realized value, not a consolation prize**
The survival gate and three-way split have now refused three families, including one
sourced from a vendor PDF claiming +₹2.79L that assumed **zero slippage**. Deploying that
at face value was the live counterfactual. A research instrument whose main output is
"don't" is working correctly.

---

## 5. What is explicitly NOT recommended

- **A fourth parameter sweep of a refuted family.** The premium-momentum kill criterion is
  pre-registered and unchanged: net-positive on a NEVER-TOUCHED forward window at ≥1%/side
  that also beats plain both-legs there. Until that fires, the family stays dead. The tuner
  stays in the app; that is enough.
- **A naked short-premium strategy**, testable though a single leg technically is. The tail
  is unbounded, the 20-month sample contains too few genuine tail days to price it, and the
  executor's buy-only gate is a deliberate safety invariant that should not be relaxed for
  an experiment.
- **Building live capability for an unproven edge.** Phase 5B was built as pure capability
  by explicit user decision with the edge verdict as advisory — that trade-off is already
  made and is not reopened here.

---

## 6. Pre-registered kill criteria

Written **before** each experiment runs, following the discipline that made the
premium-momentum verdict trustworthy: three-way chronological split, holdout touched
exactly once, friction mandatory throughout.

**B — pooled regime routing.** Split by **date, not by index**, so no index leaks across
time. Exclude BANKNIFTY 2024-11-28 → 2024-12-19 (measured option gap) explicitly.
*Killed unless* the pooled-best config is net-positive on the untouched holdout at
≥1%/side **and** beats both the untuned baseline **and** the NIFTY-only version there.
**Additionally report per-index holdout separately: if the result is positive on only one
index, that is not a pooled edge — it is the old sample-starved result wearing a larger n,
and it is killed.**

**C — friction.** Not an edge test, so no P&L criterion. Success = a measured distribution
of realized-fill-minus-model slippage from the paper/live journals. *Stopped* if realized
slippage is already at or below the 1%/side model — then there is nothing to harvest and
the work ends there rather than becoming a project.

**D — expiry structure.** Same three-way split and friction floor. *Killed unless* the
effect appears on **at least two of the three indices' expiry days** on the holdout. A
single-index effect on ~50 expiry days is indistinguishable from noise and is killed.

**F — wiring.** Not an edge test. Success = the features are reachable, causal (a bar at
time T sees only data at or before T), and byte-identical for strategies that do not
request them. Any lookahead found = revert.

**A — short side.** No experiment is authorised. Gate: a written answer on whether a wider
historical strike band and a second expiry are obtainable, and at what cost. Only if that
answer is yes does the build question reopen.

---

## 7. Recommendation

Run **F then B**, in that order, and treat **C** as a parallel analysis task since it needs
no engine work.

F first because it is cheap, it fixes a live defect, and it makes VIX- and OI-conditioned
hypotheses askable at all — including inside B. Then B, because it is the only remaining
signal the project has already measured as +EV, its stated weakness (sample starvation) has
a measured 2.97× remedy sitting unused in the warehouse, and it needs no new engine
capability. C in parallel because reducing realized friction pays into every future
strategy regardless of which edge, if any, survives.

If B fails its criterion, that is a real result and should be published to the same
standard as the premium-momentum verdict — at which point the honest conclusion is that
this app's value is as a research and risk-control instrument rather than as a source of
directional alpha, and the next question becomes procurement (direction A's data gate)
rather than another sweep.
