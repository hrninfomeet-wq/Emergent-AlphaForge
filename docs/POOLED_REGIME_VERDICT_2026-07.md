# Pooled multi-index regime routing — verdict (2026-07-27)

**KILLED AT VALIDATION. The pooled hypothesis is dead, and the holdout was never
touched.** No configuration is net-positive on both train and validation across both
indices. Every survivor is SENSEX-only, and NIFTY has **no gross edge at all**.

Reproduce: `python backend/scripts/run_pooled_regime_campaign.py --slice train`
then `--slice validation`, then `python backend/scripts/verdict_pooled_regime.py`.
Unlike the premium-momentum campaign, this harness is committed.

## The hypothesis

Regime routing (`opening_range_regime_router`) was the one signal the project had
already judged **"option-+EV yet sample-starved"** — promising-unproven. The standard
reflex is to tune harder, which makes overfitting worse; the correct fix for a sample
problem is more sample. `docs/PROFIT_LEVERAGE_ANALYSIS_2026-07.md` §4 ranked this first
because it needed **zero engine changes**.

## Method

Pre-registered before running (analysis §6): three-way chronological split **by date, not
by index**, so no index leaks across time. Costs mandatory at **1%/side** — the level the
premium-momentum finalists were judged at. Verdict metric is **option-net rupees**, never
spot P&L, because the project has already proven those diverge violently (a +₹289k spot
"best" was −₹207k on real option fills).

| Slice | Window |
|---|---|
| Train | 2024-11-25 → 2025-08-31 |
| Validation | 2025-09-01 → 2025-12-31 |
| Holdout | 2026-01-01 → 2026-07-24 — **never touched** |

**Correction made on first contact with the code:** pooling is **2.0×, not 2.97×**. The
router declares `supported_instruments = ["NIFTY", "SENSEX"]`, and that exclusion is
principled — BANKNIFTY is **monthly**-expiry (21 expiries in the window vs NIFTY's 87 and
SENSEX's 88), so its DTE range is 0–30 rather than 0–6. Pooling it with weekly indices
would blend two option regimes and call the mixture a bigger sample. Usable pooled sample:
**818 option index-days vs 408 NIFTY-only.**

**Amendment 1, declared with the holdout still clean:** the original grid was
`trend_target_atr ∈ [3, 4, 6]` and *every* net-positive config landed on 6.0 — the
boundary — while the schema allows 8.0. An edge-of-grid winner means the search found the
edge of the box, not an optimum, so 8.0 was added **once**. It did not rescue the
hypothesis; it produced *more* SENSEX-only survivors, confirming the pattern. There was no
amendment 2: extending a grid until something survives is the luck-mining the split exists
to prevent.

## What the campaign found

72 configurations per slice (36 per index).

| Slice | Index | gross > 0 | net > 0 |
|---|---|---|---|
| Train | **NIFTY** | **0 / 36** | **0 / 36** |
| Train | SENSEX | 33 / 36 | 13 / 36 |
| Validation | NIFTY | 9 / 36 | 6 / 36 |
| Validation | SENSEX | 27 / 36 | 16 / 36 |

**Configs surviving both slices on both indices: ZERO.** Thirteen survive on SENSEX alone.

### 1. NIFTY has no edge to find — this is the decisive result

Not one configuration out of 36, across nine months, produced positive P&L **before a
single rupee of friction**. This is not a costs problem or a sizing problem. On NIFTY the
router's directional call is simply wrong, and no exit parameter reaches that.

### 2. NIFTY's validation "winners" are the exact trap the split exists to catch

Validation shows 6 net-positive NIFTY configs — from a set that train proved has no gross
edge whatsoever. Ranking on validation alone would have promoted a config that nine months
of prior data had already refuted. This is the same mechanism that produced the
premium-momentum campaign's +₹103.5k validation-best and its −₹153.8k holdout.

### 3. SENSEX has a real gross edge that friction very nearly eats

33 of 36 configs positive gross on train, but only 13 net. The best config returned
**≈ +₹3,400 over nine months on 1 lot — about ₹4.70 per trade net**, against roughly ₹13–14
per trade of charges. The edge is real and consistently signed; it is also thin enough that
friction is the dominant term.

Higher targets genuinely help rather than merely trading less: at `fade 2.0 / stop 1.2`,
gross per trade rises from ₹5.93 (target 3.0) to ₹18.53 (target 6.0) while cost per trade
barely moves. Letting winners run is doing real work.

## Why "sample-starved" was the wrong diagnosis

The premise was that regime routing needed more data. Pooling doubled the sample and the
result did not improve — it **resolved**. The effect was never sample-limited; it is
**index-specific**. That is a more useful answer than a bigger *n* would have been, and it
could only be obtained by pooling. The experiment did its job by refuting its own premise.

## What this does NOT say

It does not say SENSEX has no edge. SENSEX's gross consistency (33/36, then 27/36 on a
different window) is not what noise looks like. But "SENSEX-specific regime routing" is a
**different hypothesis**, selected *after* seeing these slices, and it has never been tested
on untouched data. Claiming it now on this evidence would be precisely the post-hoc fishing
this whole method exists to prevent.

**The holdout is clean and is the right place to settle it — once.** Before spending it,
note the economics: the best observed configuration is ≈₹380/month on 1 lot. Friction here
is a *percentage* of premium, so lots scale reward and cost together; 10 lots is ≈₹3,800/month
against real capital, real margin and real tail risk. **A surviving result would be
statistically interesting and economically marginal.** That should inform whether the
holdout is worth spending at all.

Pre-registered criterion for that future test, if it is run:

> SENSEX-only regime routing is KILLED unless a config chosen from train+validation is
> net-positive on the untouched 2026 holdout at ≥1%/side friction **and** beats the untuned
> baseline there. One shot. No grid amendments. If it survives, it is still a candidate for
> paper validation, never a direct live deployment.

## Standing conclusion

Four edge campaigns have now ended in a documented NO: option-buying survival gate,
premium-momentum/EXP2, tiered profit protection, and pooled regime routing. Three of the
four were killed by the same mechanism — **an effect that exists on one slice or one
instrument and does not generalise** — and each was caught before any money was at risk.

That is the app working as designed. Its demonstrated, repeatable value so far is as a
**research and risk-control instrument**: it has refused four families, including one
sourced from a vendor PDF claiming +₹2.79L that assumed zero slippage. What it has not yet
produced is a validated source of directional alpha, and no amount of further parameter
search on these families will change that.
