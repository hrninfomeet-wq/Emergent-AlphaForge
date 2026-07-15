# Premium-momentum edge hunt — final verdict (2026-07-15)

**GATE FAILED. No configuration of the premium-momentum family is net-profitable on the
untouched 2026 holdout at honest friction — and none beats the plain both-legs baseline
there.** Phase 5B (live multi-leg execution) is therefore NOT justified by this campaign.

## Method (pre-registered in `docs/superpowers/plans/2026-07-14-premium-momentum-phase5a2-overlays-edge-hunt.md` §5)

Three-way chronological split — the standard discipline:

| Slice | Window | Role |
|---|---|---|
| Train | 2024-11-25 → ~2025-08 (192 sessions) | tuner selects within each grid |
| Validation | ~2025-09 → 2025-12-31 (83 sessions) | stage-to-stage carry ranking |
| **Holdout** | 2026-01-01 → 2026-07-10 (128 sessions) | **touched exactly once, by 3 finalists** |

Costs mandatory throughout (1%/side spread; finalists also probed at 0.5% and 1.5%).
NIFTY, 2 lots, ITM1 unless swept. Five stages, ~600 configs total: structure
(momentum × stop × target × trail), time (reference time × entry cutoff × exit time),
overlays (session day-stop ₹3k/5k/8k, max-profit ₹4k/8k; India VIX gates on the
VIX-covered subwindow), lazy reversal legs (had to out-rank no-lazy rows on validation
to survive), then the single-shot holdout + friction sensitivity. Campaign runtime:
680s after the `split_candles_by_key` perf fix (`5783dbb`).

## What the search found — and what the holdout did to it

The validation-best config (`ref 10:01, mom 10%, stop 30%, target 50%, cutoff 13:00,
exit 14:30, day-stop ₹8k`) scored **+₹103,499** on the validation slice. On the holdout:

| Finalist (all = the config above ± day-stop variants) | 0.5%/side | 1.0%/side | 1.5%/side |
|---|---|---|---|
| #1 | −₹132,686 | **−₹153,828** | −₹174,969 |
| #2 (+profit-cap 8k) | −₹129,855 | **−₹150,766** | −₹171,676 |
| #3 (+profit-cap 4k) | −₹133,904 | **−₹154,668** | −₹175,432 |
| Plain both-legs baseline (mom 15/stop 20) | — | **−₹135,275** | — |

Every finalist is worse than the untuned baseline it had to beat. Gross points on the
holdout are −798 for the "best" config — the marks themselves are negative before a
single rupee of friction.

## Why this is a robust NO, not an unlucky draw

1. **The train slice already said no.** Every stage-1 top-by-validation config had a
   deeply negative train (−₹127k to −₹215k over ~9.5 months). Nothing was positive on
   train AND validation. The validation window (Sep–Dec 2025) was simply a favorable
   regime; ranking on it mined luck, and the holdout exposed that — which is exactly
   what the three-way split is for.
2. **Three independent periods, one direction**: train negative, holdout negative,
   validation positive only for period-specific picks. A real edge should survive at
   least two of three.
3. **Overlays didn't bind**: day-stop caps at ₹8k/2-lots left the validation number
   identical to no-cap (the same +₹103,499 with and without) — they trimmed nothing
   that mattered. VIX gates never out-ranked ungated rows. Lazy legs scored ~half the
   no-lazy validation net (+₹49.6k vs +₹103.5k) — they failed to earn their way back in
   even on the friendly slice, consistent with the 0.54.0 verdict.
4. **The one structural finding that DID replicate** (search and holdout): both-legs
   mode beats first-to-trigger everywhere — but never crosses zero. It's a smaller
   loss, not an edge.
5. Consistent with the project's prior evidence: the AlgoTest EXP2 PDF's +₹2.79L
   (2024) assumed ZERO slippage; `docs/NF_CE_PE_EXP2_Strategy_Spec.md` §9's own red
   flags (favorable-year sample, ~10 tuned parameters, no-slippage fills) are exactly
   what this campaign observed failing.

## Standing conclusion

Buying option premium AFTER a 10-25% spike pays the momentum-chaser's tax: entries are
systematically into decaying, spread-widened premium. Across ~600 configurations of
structure, timing, session overlays, VIX regimes and reversal legs, no variant paid its
own friction out-of-sample. This matches [[option-buying-edge-hunt-2026]]'s earlier
finding that the bottleneck is directional signal quality, not exit engineering.

**Do not build Phase 5B live execution for this family on current evidence.** The full
capability to keep hunting stays in the app (the `/premium-momentum` page's honest
tuner now sweeps 16 tunable keys including day-stop, VIX gates and session windows) —
the kill-criterion for reviving 5B is unchanged and pre-registered: a config
net-positive on a NEVER-TOUCHED forward window at ≥1%/side friction that also beats
plain both-legs there.
