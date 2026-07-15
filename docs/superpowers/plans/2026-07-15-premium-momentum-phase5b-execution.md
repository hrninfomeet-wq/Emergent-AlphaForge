# Phase 5B execution plan — live/paper multi-leg premium-momentum (capability build)

Parent spec: `2026-07-15-premium-momentum-phase5b-live-multileg-design.md`. All anchors
below were recon-verified against main `5f69ee0` (6-agent workflow wf_fde2c724); where
the spec draft assumed wrong, this plan records the corrected reality. TDD per task;
host/container split per DEVELOPER_GUIDE §B. NO new arming gate anywhere. Edge-verdict
context: `docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md` — this is a capability build.

## Recon corrections that bind this plan

1. Per-leg latches = **new additive functions** (`latch_trigger_leg`, `mark_entered_leg`,
   `unlatch_trigger_leg`) — the existing `latch_trigger`/`mark_entered`/`unlatch_trigger`
   stay textually untouched (stronger byte-identity for first_to_trigger than mode-flag
   branches inside them). `premium_lock_store.py:71-99`.
2. `today_locked_keys` (`premium_lock_store.py:115`) hardcodes `("ce","pe")` — MUST also
   scan lazy sub-docs or mid-session lazy pins silently never subscribe. Pin cadence
   itself is fine (~1/min via the evaluator-bar site, `runtime.py:1520`; subset guard
   restarts exactly when a new key appears).
3. `done_for_day` is whole-doc; multi-leg needs per-leg `<leg>_exited` accounting and a
   `mark_done` that fires only when every armed/entered leg is resolved. The current
   rehydrate (`runtime.py:315`) and guard-close hook would otherwise finalize the whole
   session while another leg is still open — recon-flagged, must be closed by design.
4. The live ₹ daily-loss cap (`live_deploy_governor.py` `daily_loss_cap`) is
   **mark-to-market** — the realized-only `session_max_loss/profit_rupees` cannot reuse
   it. Own realized-only check (query of this deployment's session live_trades /
   paper trades, same pattern as the governor's `_realized_today`).
5. Arm advisories (`build_arm_advisories`) have **zero frontend consumers today** —
   `deploymentMetrics` is defined in `api.js` but never called. T8 builds the surface.
6. `_live_guard_on_close(entry, exit_price, reason, result)` — `entry` has
   `deployment_id`/`id`(=norenordno)/`tsym` but NO side; the leg is identified by
   matching `entry["id"]` against the lock's `<leg>_entered_norenordno`.
   STOP-class reasons that arm a lazy leg: `stop`, `breakeven_stop`, `trailing_stop`,
   `spot_stop_hit`. NOT arming: `target`, `spot_target_hit`, `eod_square`, `time_stop`,
   and the basket-level `overall_*` (they square everything — arming a reversal into a
   basket stop would fight the operator's own risk control). Kill-switch squares never
   reach this hook (separate path) — correct, no arming there.

## Design decisions (parity-divergence table — state these in code comments and docs)

| Decision | Live behavior | Backtest behavior | Direction of error |
|---|---|---|---|
| Same-bar double-cross (both mode) | ONE entry decision per deployment-bar: CE this bar, PE next bar via its own still-unlatched leg | both enter at the same bar close | live is later/fewer — conservative |
| Lazy arming | guard confirmed-flat STOP → `lazy_armed_<side>` flag → NEXT evaluator bar locks fresh strike + ref from ticks | armed at the stop-out bar, ref = that bar's close | live later by flat-confirm + ≤1 bar — conservative |
| Day-stop | realized-only; breach blocks new entries/armings AND calls `_square_live_positions_for_deployment(dep_id, reason="premium_day_stop")` (`routers/deployments.py:113-182`); paper = block-only (no paper squarer in v1, documented) | realized-only breach; open legs force-closed at breach bar | comparable; paper block-only is more permissive on open legs |
| exit_time | per-entry `square_at_ist` honored by the guard's EOD evaluator, **clamped strictly earlier than the global 15:00 EOD** (`live_position_guard.py:302`); a later value (e.g. EXP2's 15:13) is ignored + advisory | sliced to exit_time verbatim (15:13 allowed) | live exits earlier — conservative, stated loudly |
| VIX gate | last stored INDIAVIX close asof ≤ session ref bar, 5-day staleness (shared `vix_by_session_map` semantics); unverifiable ⇒ no_setup with reason | same helper on stored data | equivalent; live value may be a day staler intraday |

## Tasks (Cluster A = entry path; Cluster B = exits/recovery/UI; sequential workflows)

### A1 — premium_lock_store per-leg primitives (host TDD)
Legs: `pce`/`ppe` (primaries; existing `ce`/`pe` sub-docs and `{side}_ref_*` fields KEEP
their current meaning and become the pce/ppe contract+ref storage — no duplication) and
`lce`/`lpe` (lazy: own sub-doc + `l{side}_ref_premium/_ref_ts` + flags). New:
`latch_trigger_leg(col, ..., leg)` (atomic, filtered on `<leg>_triggered` absent AND
`done_for_day: False`), `unlatch_trigger_leg` (filtered on `<leg>_entered_norenordno`
None — never releases a completed entry, never touches other legs),
`mark_entered_leg`, `mark_leg_exited`, `set_lazy_armed(col, ..., side, parent_reason)`
(idempotent one-shot: filtered on the flag absent), `capture_ref_leg` (lazy ref),
`legs_unresolved(lock, params)` pure helper (which legs are armed/entered but not
exited — drives the whole-doc done decision), extend `today_locked_keys` to scan
`("ce","pe","lce","lpe")`. Existing functions byte-identical (string-level test pin).

### A2 — plugin schema extension (host TDD)
`strategies/plugins/premium_momentum.py`: add `leg_mode` (str, default
"first_to_trigger"), `lazy_enabled` (bool, default False, `"fixed": False` for the
optimizer space), `lazy_momentum_pct` (5-50, default None→schema default only when
lazy on), `lazy_stop_pct` (10-40), `lazy_target_pct` (fixed None), `lazy_moneyness`
(str "itm1"), `entry_cutoff`/`exit_time` (str, default None), `session_max_loss_rupees`
/`session_max_profit_rupees` (float, fixed None — day-stops are risk controls, not
search dimensions), `vix_min`/`vix_max` (float, fixed None). merged_params passthrough
test for a pre-5B stored params dict (no migration — recon-confirmed allow-list
mechanics, `base.py:88-102`).

### A3 — live session engine both-mode + gates (host TDD)
`premium_momentum_live.py`: (a) mode-aware terminal check at the line-100 seam —
first_to_trigger keeps the session-global check verbatim; both-mode checks per-leg
(`<leg>_triggered`/`<leg>_entered_norenordno`) and only skips RESOLVED legs; (b)
entry_cutoff gate (no triggers, no lazy locks at/after it); (c) VIX gate at session
start (evaluator passes the resolved vix value in; engine stays pure — outcome
`no_setup` reason `vix_gate`/`vix_unverifiable`); (d) lazy pickup: when
`lazy_armed_<side>` is set and the lazy leg has no lock yet → fresh
`lock_reference_strike` from current bar spot + fresh tick ref via the existing
`_fresh_premium`, stored via A1 primitives; (e) ONE triggered leg per bar (CE-first
priority preserved; second leg next bar), outcome dict gains `leg` (pce/ppe/lce/lpe)
and lazy legs use lazy params for the trigger check.

### A4 — evaluator + auto_live per-leg plumbing (container TDD)
`deployment_evaluator.py` pm branch: thread vix value (stored-VIX asof helper, only
when the gate is configured), day-stop gate (realized-only query; on breach: journal
outcome `day_stop`, block, and fire the deployment square once per session —
idempotent flag on the lock), `signal_doc["premium_momentum"]["leg"]`, latch via
`latch_trigger_leg` after clean journal (downgrade `latch_refused` per leg unchanged
in shape). `auto_live.py`: re-check failure → `unlatch_trigger_leg` (that leg only);
success → `mark_entered_leg` (+ keep legacy `mark_entered` for first_to_trigger mode
only); pass `square_at_ist` risk hint (from exit_time, clamped) into the guard
registration path. Paper tee mirrors leg identity.

### B5 — guard per-entry square-time (container TDD, touches live/)
`live_position_guard.py`: `register()` accepts optional `square_at_ist` (IST "HH:MM");
`_evaluate_eod_square` checks the entry-level time first (same `_issue_square(...,
ignore_square_stopped=True)` semantics, reason `"exit_time"`), global 15:00 unchanged
and always wins if earlier. Clamp at registration: `square_at_ist >= eod` → dropped
with a log (and the advisory covers user-facing honesty).

### B6 — guard-close hook: lazy arming + per-leg finalize (container TDD)
`runtime._live_guard_on_close`: identify the leg by norenordno match; `mark_leg_exited`;
update `session_realized_rupees` on the lock (from the close-loop's realized figure);
if primary + STOP-class reason (per correction 6) + lazy_enabled + before entry_cutoff
+ lazy not already armed → `set_lazy_armed`. Whole-doc `mark_done(reason="exited")`
ONLY when `legs_unresolved()` is empty. first_to_trigger mode keeps today's behavior
byte-identically (single leg ⇒ resolved ⇒ done, same observable transitions).

### B7 — recovery multi-leg (container TDD)
`rehydrate_premium_momentum`: per-leg extraction loop (up to 4 leg records) instead of
the single `triggered_side` read; watched-set guard unchanged (already leg-agnostic);
NO whole-doc `mark_done` while any leg is unresolved (recon correction 3); per-leg
exit-state rehydration mirrors the existing persisted-exit-state pattern.

### B8 — advisory surface + UI states (host string-pin TDD + babel parse)
Backend: deployments metrics route adds advisory id `premium_edge_verdict` (severity
warning, message pointing at the verdict doc) for premium_momentum deployments with
`leg_mode=="both"` or `lazy_enabled` — informational ONLY (assert no arming code path
reads advisories: pin that `liveArm` flow is untouched). Frontend:
`DeployToLivePanel.jsx` fetches `deploymentMetrics(id)` and renders `arm_advisories`
(first-ever consumer — recon correction 5); `LiveDeploymentStrip.jsx` shows per-leg
chips (pce/ppe/lce/lpe state) + new refusal labels (`vix_gate`, `day_stop`,
`exit_time_clamped`).

### B9 — sweep + adversarial checkpoint + rebuild
Full premium-family + live-regression container sweep + full host suite; Fable
adversarial review (lenses: per-leg latch races, double-entry via lazy+primary same
side, premature whole-doc done, day-stop square idempotency, first_to_trigger
byte-identity); rebuild backend+frontend; smoke: single-leg deployment behaves
identically (lock doc diff empty vs pre-5B fields), both-mode dry-run produces two
independent leg states.

## Out of scope (unchanged from spec)
General-Optimizer/PremiumTriggerConfig multi-leg exposure; re-entries beyond one lazy
shot; MTM day-stop; paper-mode day-stop squaring; non-NIFTY.
