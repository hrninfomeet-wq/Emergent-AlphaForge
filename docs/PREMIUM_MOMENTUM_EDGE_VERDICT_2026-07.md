# Premium-momentum edge hunt — final verdict (2026-07-15)

**GATE FAILED. No configuration of the premium-momentum family is net-profitable on the
untouched 2026 holdout at honest friction — and none beats the plain both-legs baseline
there.** Phase 5B (live multi-leg execution) is therefore NOT justified by this campaign.

> **Status: CLOSED, binding.** Do not re-run without a never-touched forward window (the
> revival criterion is at the end). Phase 5B was later built anyway as a **pure capability,
> by operator decision** (CHANGELOG `[0.55.0]`); this verdict travels with every multi-leg
> deployment as the informational `premium_edge_verdict` advisory, which never gates. The
> advisory text in `backend/app/forward_metrics.py` cites this file by name
> (`tests/test_premium_momentum_advisory_ui.py` pins it), so do not rename it.

## Method

### Pre-registered campaign (written 2026-07-14, before the run)

The grids and the gate were fixed in the Phase 5A.2 plan before any config was scored. That
plan was retired in the 2026-09-30 docs cleanup; its campaign section is reproduced here.

- **Windows.** SEARCH 2024-11-25 → 2025-12-31; the tuner's internal chronological
  train/validation split does the selection. HOLDOUT 2026-01-01 → 2026-07-10, touched once,
  by the finalists only.
- **Stage 1 (structure).** Both-legs fixed (the 0.54.0 finding). `momentum_pct` {10, 15, 20, 25}
  × `stop_pct` {10, 15, 20, 25, 30} × trail pct pair {none, 5/5, 10/5} × `target_pct`
  {none, 25, 50}. Reference 09:31, ITM1, costs 1%/side, no lazy legs.
- **Stage 2 (time).** Top-5 re-tuned at `reference_time` {09:46, 10:01, 10:16} ×
  `entry_cutoff` {11:30, 13:00, 14:40} × `exit_time` {14:30, 15:13, none}.
  `reference_time` is deliberately NOT a tunable key (it changes the preload's strike locks),
  so it was swept across separate tune calls.
- **Stage 3 (overlays).** Top-5 × day-stop loss {₹3k, ₹5k, ₹8k} / profit {₹4k, ₹8k, none}
  per 2 lots × VIX gate {none, ≥12, ≥14, ≤20, 12–20}. VIX rows ran on the 2025-06+
  subwindow only, with N reported.
- **Stage 4 (contingency).** Lazy legs (mom {10, 15, 20}, stop {10, 15}, trail 5/5) added to
  the top-3; lazy had to out-rank no-lazy to survive.
- **Stage 5 (verdict).** Top-3 finalists get one HOLDOUT run at friction 0.5 / 1.0 / 1.5%
  per side, plus an entry-hour P&L histogram (informational).
- **GATE for 5B.** A finalist must be net-positive on the holdout at 1%/side AND
  non-catastrophic at 1.5%/side AND beat plain both-legs on the holdout. Otherwise report
  honestly and return the 5B build decision to the user.
- **Caveat on record.** The EXP2-default configs A/B/C had already seen the holdout in the
  0.54.0 verdict; configs new to this campaign saw it fresh.

The overlay params (`session_max_loss_rupees`, `session_max_profit_rupees`, `vix_min`,
`vix_max`) were deliberately API/tuner-only: the `/premium-momentum` page has no fields for
them (still true: `frontend/src/pages/PremiumMomentum.jsx` does not reference them).

### Split as run

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
own friction out-of-sample. This matches the earlier survival-gated optimizer finding
(CHANGELOG `[0.45.x]`: no deployable survivor across three strategies and several NIFTY
windows) that the bottleneck is directional signal quality, not exit engineering.

**Do not build Phase 5B live execution for this family on current evidence.** The full
capability to keep hunting stays in the app (`POST /api/premium-momentum/tune` accepts 22
grid keys, `TUNABLE_KEYS` in `routers/premium_momentum_routes.py`, including the day-stop,
VIX-gate and session-window overlays; the `/premium-momentum` page exposes only part of that grid) —
the kill-criterion for reviving 5B is unchanged and pre-registered: a config
net-positive on a NEVER-TOUCHED forward window at ≥1%/side friction that also beats
plain both-legs there.
