# Phase 5B — live/paper multi-leg execution for premium-momentum (capability build)

Status: DRAFT pending recon verification of the seam anchors (§6). Parent:
`2026-07-13-premium-momentum-phase4-5-full-contingency-design.md` §4.

**Context that must never be dropped**: the edge hunt CLOSED with a failed gate
(`docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`) — no configuration of this family is
net-profitable out-of-sample at honest friction. The user explicitly chose to build 5B
anyway **as pure capability** (Track-B precedent: the app should be able to execute
time-based, premium-based multi-leg strategies regardless of the default family's edge).
Consequence for the build: an **informational arm advisory** (never a gate) must surface
the verdict on premium_momentum deployments that enable the 5B features.

## 1. Scope

IN (live + paper deployments of `premium_momentum`):
- `leg_mode="both"`: CE and PE primaries independent — up to two concurrent positions
  per deployment per session, each with its own trigger latch, signal journal row,
  entry, and guard registration.
- Lazy reversal legs: when a PRIMARY position's guard exit is a STOP, arm the opposite
  side once — fresh strike from current spot, fresh ref premium from ticks, own
  momentum/stop/target/trail params. One shot per side per session.
- `entry_cutoff` (IST HH:MM): evaluator blocks new triggers AND lazy armings at/after it.
- `exit_time`: per-deployment square time, **clamped to be no later than the system EOD
  square** — the EOD backstop always wins; a later exit_time is ignored with an advisory.
- Session day-stop (`session_max_loss_rupees` / `session_max_profit_rupees`): blocks new
  entries/armings once the deployment's REALIZED session P&L (live_trades) breaches a
  cap; on breach also requests a deployment-level square of its open premium-momentum
  positions via the EXISTING deployment-stop/kill-switch square path (recon §6 verifies
  which mechanism; if only pct-based daily_loss exists, add a rupee variant to it rather
  than a parallel mechanism). Realized-based, like the backtest; NOT mark-to-market.
- VIX gate (`vix_min`/`vix_max`): evaluator session-start check via the existing asof
  helper over stored INDIAVIX data; unverifiable VIX with a configured gate = no_setup
  with an explicit reason (never a silent pass) — mirrors the backtest counter honesty.
- Plugin `parameter_schema` extension with ALL the above (this deliberately opens the
  seam 5A kept closed — that closure existed to prevent silent backtest↔live divergence
  while live support didn't exist; 5B builds the live support, so the seam opens WITH it).
- `premium_locks` multi-leg schema (additive), recovery, UI (deploy wizard param fields
  ride the existing schema-driven form; Live strip shows per-leg state), arm advisory.

OUT: multi-leg exposure through `PremiumTriggerConfig`/the general Optimizer path (stays
single-leg; the bespoke page + tuner remain the multi-leg research surface); re-entries
beyond one lazy shot; mark-to-market day-stop; any instrument beyond NIFTY (v1).

## 2. Non-negotiable invariants (all pre-existing, none relaxed)

No new arming gate of any kind (standing user decision — the advisory is informational).
`live/executor.py` = sole ENTRY chokepoint; exits only via guard/auto_square/kill paths.
Confirm-flat remains the sole finalizer (DONE transitions hang off `_live_guard_on_close`
only). Offline-first env gates unchanged. Recovery re-run-safe (watched-set guard
extends to multi-leg). Existing single-leg deployments behave byte-identically (every
new param defaults to the single-leg shape; `premium_locks` changes are additive).

## 3. State model — premium_locks (additive)

Existing flat fields keep their exact meaning for single-leg deployments. Additions
(flat naming, filtered-update compatible): per-leg records for `pce`/`ppe` (primaries,
aliasing the existing ce_/pe_ fields where they already exist — recon decides alias vs
duplicate) and `lce`/`lpe` (lazy): `<leg>_instrument_key/tsym/strike`, `<leg>_ref_premium`,
`<leg>_ref_ts`, `<leg>_triggered`, `<leg>_entered_norenordno`, `<leg>_entry_premium`,
`<leg>_exited`. Plus `lazy_armed_ce/pe` (set by the guard-close hook),
`lazy_blocked_reason`, `session_realized_rupees` (day-stop accumulator, updated in the
same confirmed-flat hook that finalizes exits). Latches stay atomic first-wins PER LEG
(the existing single latch generalizes; in `first_to_trigger` mode the old cross-side
latch semantics are preserved exactly).

## 4. Live semantic mapping (the honesty-critical decisions)

- **Lazy arming trigger**: the backtest arms at the stop-out BAR; live arms when the
  guard CONFIRMS the primary flat with a stop-class exit reason, then the NEXT evaluator
  bar performs the fresh lock + ref capture from ticks. Divergence (seconds-to-a-bar
  later than backtest's bar-close approximation) is documented, not hidden — same class
  as the accepted trigger-cadence divergence from Track B.
- **What counts as a STOP** for arming: the guard's exit reason taxonomy (recon §6
  verifies exact values) — stop/trail-stop reasons arm; target/EOD/manual/kill do NOT
  (matches backtest: STOP only).
- **Two positions, one deployment**: caps/margin flow through the existing governor
  checks per entry; the second leg's entry is just another governed, journaled,
  re-checked order. Nothing new bypasses anything.
- **Day-stop accumulator source**: realized P&L of THIS deployment's live_trades for the
  IST session (the paper path mirrors with paper trades). Computed in the evaluator gate
  (cheap query or lock-accumulated) — recon confirms the cheapest honest source.

## 5. Parity

Same discipline as Track B: shared pure helpers (`momentum_triggered`,
`stepped_trail_stop[_pct]`, `lock_reference_strike`, `vix.py` asof) are the parity
mechanism, not a shared loop. A parity table in the plan lists each divergence
(bar-cadence triggers, lazy arming latency, realized-only day-stop, VIX staleness
fallback) with direction-of-error noted.

## 6. Recon anchors to verify before planning (workflow output feeds the plan)

1. `premium_lock_store.py` — full current field list + latch/adopt semantics;
   `premium_momentum_live.py` — full state machine + outcomes consumed by the evaluator.
2. `deployment_evaluator.py` premium branch — exact current structure (lock-driven
   contract, journal-then-latch, pretrade bypass marker, sink tee) + where per-side
   generalization lands; `auto_live.py` last-line re-check + `mark_entered` shape.
3. `_live_guard_on_close` hook — exact signature, what exit-reason data it receives
   today, where `mark_done(reason="exited")` fires; guard exit-reason taxonomy
   (`live_position_guard.py` / `live_sl_monitor.py`).
4. Deployment-stop / kill-switch mechanics — `deployment_kill_switch.py` daily-loss
   fields (pct? rupees?), the deployment-level square path, and the EOD square
   constant/mechanism (`auto_square.py` docstring says EOD 15:00 is the manual backstop;
   verify the deployed-strategy square timing source).
5. `runtime.py` — `rehydrate_premium_momentum` watched-set guard current shape;
   `premium_pin.py` pin surface (lazy legs add mid-session pins — verify the pin union
   sites accept per-session ADDITIONS, i.e. the stream rebuild cadence); whether the
   live roller ingests INDIAVIX intraday (else the VIX gate uses last stored close via
   the 5-day-staleness asof — same helper as the backtest route).
6. Plugin schema / `merged_params` allow-list — confirm additive schema params flow to
   the evaluator for existing deployments without migration; arm-advisories surface
   (`forward_metrics.build_arm_advisories`) for the verdict advisory.
