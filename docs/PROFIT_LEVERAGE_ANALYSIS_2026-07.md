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

## 4. Candidate directions, ranked by information per rupee of build

*(Build-cost estimates and the data facts they depend on are pending the three inventory
agents; this section is the reasoning, and will be completed with measured numbers.)*

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
