# Phase 5A.2 — session day-stop + VIX gate overlays, then the structured edge hunt

Addendum to `2026-07-14-premium-momentum-phase5a-backtest-contingency.md` (same scope
discipline: **backtest-only by mechanism** — plugin schema and `PremiumTriggerConfig`
still NOT extended; same forbidden-files list). Purpose: add the two session-level
overlays the edge hunt needs, then run a train/holdout search campaign for a config
that is profitable net of costs. Phase 5B (live) stays gated on the campaign's result.

## Data reality (probed 2026-07-14, drives the window design)

- `options_1m` NIFTY: continuous 2024-11 → 2026-07. Spot: same.
- `INDIAVIX` (`candles_1m`, instrument "INDIAVIX"): **2025-06 → 2026-07 only.**
- Campaign windows: SEARCH = 2024-11-25 → 2025-12-31 (tuner's internal chronological
  train/val split does selection); HOLDOUT = 2026-01-01 → 2026-07-10, touched once by
  finalists only. (Caveat on record: EXP2-default configs A/B/C already saw the holdout
  in the 0.54.0 verdict; any NEW config sees it fresh.) VIX-gated configs are only
  evaluable from 2025-06 — their effective search window is shorter; report N honestly.

## 1. Session day-stop (max loss / max profit per session)

Params: `session_max_loss_rupees` (positive; stop when realized session net P&L
<= -value), `session_max_profit_rupees` (stop when >= +value). Both optional,
default None = today's behavior.

**Semantics — REALIZED P&L, bar-close honest (document verbatim in the docstring):**
1. Walk all legs exactly as today (no change to the walks themselves).
2. Sort completed trades by (exit_ts, entry_ts, side) — deterministic.
3. Scan cumulative REALIZED `net_pnl_rupees` (cost-adjusted) in that order; the first
   trade whose cumulative breaches a cap defines `breach_ts` = its exit_ts. Trades
   sharing that same exit_ts remain realized (they exited on the same bar; they are
   not "blocked").
4. Any trade with entry_ts > breach_ts is DROPPED (blocked entry) → counter
   `blocked_day_stop` (also decrement nothing else — lazy counters keep their
   pre-block meaning; a lazy ARMING whose parent stop-out is itself after breach_ts is
   likewise blocked and counted in `blocked_day_stop`, not in lazy_armed).
5. Any trade OPEN at breach (entry_ts <= breach_ts < exit_ts) is FORCE-CLOSED at the
   first bar of ITS OWN premium series with ts >= breach_ts, at that bar's CLOSE,
   exit_reason `"DAY_STOP"`; recompute pnl + costs for the truncated trade.
6. One pass only. This is exact for the breach decision because it is defined on
   REALIZED exits: forced exits realize AT breach_ts, never before it, so they cannot
   move the breach earlier. A mark-to-market day-stop (open positions' unrealized
   losses triggering the stop) is a DIFFERENT, richer rule — explicitly deferred;
   say so in the docstring, don't imply this rule catches intraday MTM bleed.

Coverage: `blocked_day_stop`, `forced_day_stop_exits`. Summary: nothing new needed
(by_leg already splits).

## 2. India VIX gate

Params: `vix_min`, `vix_max` (floats, either/both optional, default None = no gate).

- Sim stays PURE: it receives `vix_by_session: Optional[Dict[str, float]]` (session_date
  -> gate value) as a new function argument, no I/O inside.
- Gate at session start: value outside [vix_min, vix_max] → skip session, counter
  `sessions_excluded_vix_gate`. Gate configured but session missing from the map →
  skip, counter `sessions_excluded_vix_missing` (trading an unverifiable gate would be
  dishonest; do NOT silently pass).
- Route builds the map: load INDIAVIX candles for [start_ts - 5 days, end_ts]; per
  session, the gate value = VIX close asof <= that session's REF BAR ts (within the
  session; fall back to the previous session's last close within 5 calendar days; else
  absent from map). Ref-bar-time VIX is known at the lock moment — no look-ahead.
  (Live parity for 5B: the evaluator can read the latest VIX the same asof way.)

## 3. TUNABLE_KEYS additions

`session_max_loss_rupees`, `session_max_profit_rupees`, `vix_min`, `vix_max`,
`entry_cutoff`, `exit_time` (the last two are strings — the tune grid passes values
verbatim into params; nothing about the preload depends on them). `reference_time`
stays NON-tunable (changes the preload's strike locks); the campaign sweeps it across
separate tune calls instead.

## 4. Tests (host, TDD — same fixture style as tests/test_premium_momentum_contingency.py)

Day-stop: (a) breach on max-loss blocks later entries (counter), (b) open leg
force-closed at first bar >= breach at close with DAY_STOP reason and recomputed
costs, (c) same-bar tie: two exits sharing breach_ts both stay realized, (d) max-profit
variant, (e) no caps = byte-identical (parity extension), (f) a lazy arming whose
parent stop-out is post-breach is blocked. VIX: (g) gate excludes out-of-band session
with counter, (h) gate + missing VIX = excluded with the MISSING counter, not a pass,
(i) no gate + no map = byte-identical, (j) map ignored when no gate configured.
Fail-loud: (k) vix_min > vix_max raises ValueError; negative rupee caps raise.

## 5. The campaign (after the build; run against the local API, scripted)

Stage 1 (structure, cheap): both-legs fixed (0.54.0 finding); sweep momentum_pct
(10/15/20/25) x stop_pct (10/15/20/25/30) x trail pct pairs (none, 5/5, 10/5) x
target_pct (none, 25, 50) on SEARCH window, ref 09:31 itm1, costs 1%/side, no lazy.
Stage 2 (time): top-5 of stage 1 re-tuned at ref_time 09:46 / 10:01 / 10:16 and
entry_cutoff 11:30 / 13:00 / 14:40, exit_time 14:30 / 15:13 / none.
Stage 3 (overlays): top-5 so far x day-stop grids (loss 3k/5k/8k, profit 4k/8k/none
per 2 lots) x VIX gates (none, >=12, >=14, <=20, 12-20) — VIX rows evaluated on the
2025-06+ subwindow only, N reported.
Stage 4 (contingency last): add lazy legs (mom 10/15/20, stop 10/15, trail 5/5) to the
top-3 — the 0.54.0 verdict says lazy must EARN its way back in.
Stage 5 (verdict): top-3 finalists → single-shot HOLDOUT run; friction sensitivity at
0.5% / 1.0% / 1.5% spread per side; entry-hour P&L histogram (informational).
GATE for 5B: a finalist must be net-positive on the HOLDOUT at 1%/side AND stay
non-catastrophic at 1.5%, AND beat plain both-legs on the holdout. Otherwise: report
honestly and put the 5B build decision back to the user — building live execution for
a config that only works at optimistic friction is the exact mistake the honest tuner
exists to prevent.

## Deferred (unchanged from 5A): UI fields for the new overlays (API-first; add after
the campaign shows what's worth exposing), mark-to-market day-stop, live/5B wiring.
