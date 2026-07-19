# Phase 5B market-hours validation runbook

**Target: Monday 2026-07-20** (or the next weekday the PC can be on from ~09:00 IST).
**Mode: PAPER ONLY on day 1.** No live order is placed anywhere in this runbook's day-1 flow.

This is the first real market-hours session for everything built since Track B merged
(2026-07-12): the Layer-1/2 guard, kill-switch stop-all, recovery, and all of Phase 5B
(v0.55.0). The full host suite proves internal consistency; this day proves the
rails against the real broker/data world, where the worst historical bugs (candle-roller
boot gap, Upstox-vs-Noren recovery symbol-space) lived invisible to tests.

## ⚠ THE AUTHORIZATION MODEL CHANGED (v0.56.0) — read before anything else

This runbook was written against the old ARM model. That model is **gone**:

| Before | Now |
|---|---|
| Deploy (paper) → click ARM each session → armed until 15:00 IST | Deploy → **Enable live** once; it persists across sessions until disabled |
| `LIVE_GUARD_ARMED=1` needed for the guard to transmit exits | **Removed** — the guard ALWAYS transmits stop/target/trail/EOD |
| `risk.live.armed` was the live marker | `deployment.mode == "live"` is the authorization |
| Disarm / Stop | **Disable** (back to paper, positions untouched) / **Stop** (flatten + disable + PAUSE) |

`LIVE_AUTOPLACE_ARMED` **survives** as the single master switch for automated entries.

**The consequence that matters today: there is no longer a "deployed but harmless" state.**
A deployment in live mode with the broker connected trades real money on the next confirmed
signal. Leave a deployment in **paper** mode for everything in sections 1-5 below.

## What day 1 (paper) can and cannot prove — read this first

The 5B **entry** machinery runs in the evaluator and is fully exercised in paper:
both-legs locking/triggering, VIX gate, day-stop blocking, arm advisories.

The 5B **exit** machinery lives entirely in the **live position guard**
(`_live_guard_on_close` in `backend/app/runtime.py`, `square_at_ist` in
`backend/app/live/live_position_guard.py`). Paper exits go through the separate
`LiveExitMonitor`, which never touches premium locks. (The guard being always-armed
now does NOT change this — an always-transmitting guard still only watches *broker*
positions, and paper trades never create one.) Therefore, **in paper mode**:

- **Lazy reversal legs will NOT arm** after a paper stop-out. This is expected, not a bug.
- **`exit_time` squares will NOT fire** for paper positions (paper uses its own
  stop/target/trail + the paper EOD/auto_paper_stop path).
- Per-leg confirm-flat finalize and the recovery order-book symbol join are live-only.

Those four things get their proof on the later **1-lot live day** (step 4 below).

## 1. Pre-market checklist (done by ~09:00 IST)

- [ ] PC on; Docker Desktop running; `docker compose up -d` from the repo root.
- [ ] `curl localhost:8001/api/health` → `{"db":"ok"}`; frontend `localhost:3000` loads
      (hard-refresh Ctrl+Shift+R if the bundle looks stale).
- [ ] **Upstox daily OAuth** via the UI banner. Then verify the **candle roller LED is
      green** — the supervisor should auto-start it after OAuth; the stream indicator is
      NOT the roller. If the LED is red past 09:16, that's finding #1 of the day.
- [ ] Optional: Flattrade OAuth too (not needed for paper placement, but each Flattrade
      OAuth forces `maybe_run_live_recovery`, giving one extra free recovery exercise).

## 2. Deploy two paper deployments (premium_momentum, NIFTY)

**Deployment A — "everything on, permissive"** (should actually trade):
- `leg_mode: both`; lazy fields configured (they'll persist but won't fire in paper — fine);
  `exit_time` set (e.g. `14:45`); a **wide** VIX band (e.g. 5–60) so the resolution code
  runs but passes; day-stop set generously high (or off) so it doesn't block the run;
  standard stop/target/trail in `risk.exit_controls`.

**Deployment B — "narrow VIX"** (should REFUSE and say so):
- Same, but a VIX band today's value is outside of. Expected: no entries + an honest
  `vix_gate` (or `vix_unverifiable`) label on the Live strip. A silent no-trade with no
  label = bug.

Also check the **deploy/arm panel** for both: the informational `premium_edge_verdict`
arm advisory chip must appear (B8) and must NOT block arming.

## 3. In-session observables (deployment A)

- [ ] At reference time: a `premium_locks` doc for today with locked CE+PE strikes.
- [ ] Per-leg ref premiums captured (`pce_ref_premium` / `ppe_ref_premium`, fresh ticks).
- [ ] On a premium spike ≥ momentum%: that leg latches (`pce_triggered`/`ppe_triggered`),
      signal journals, paper trade opens. **Legs are independent** — the second leg can
      still trigger later on its own side.
- [ ] Paper blotter live columns keep updating (LiveExitMonitor alive); exits carry honest
      reason buckets (stop/trail/target/EOD).
- [ ] Day-stop (if testable): once realized P&L breaches it, further entries blocked +
      `day_stop` strip label; in paper it must NOT square anything (block-only by design).
- [ ] EOD: paper positions square via the paper EOD path; lock's `done_for_day` semantics
      end the session cleanly.

## 4. The deliberate mid-session restart (with an open paper position)

Run `docker compose restart backend`, then verify within ~2 minutes:

- [ ] Health OK; roller comes back (supervisor reconcile) — candles keep forming.
- [ ] The open paper trade keeps being monitored (blotter live columns resume updating —
      the supervisor must self-heal the LiveExitMonitor without a manual poke).
- [ ] The `premium_locks` doc is UNTOUCHED by recovery: not marked done/exited, no
      re-lock, no duplicate entry on the next bar.
- [ ] Nothing in the logs claims `exited_while_down` for the paper position.

Note: this restart does **not** exercise the Noren order-book join (paper entries never
write broker order numbers) — that specific proof only happens on the live day's restart.

## 5. Evidence to capture (for the follow-up session)

- `docker logs alphaforge_backend` around: OAuth, lock creation, each trigger, each exit,
  the restart. Screenshots of the strip labels, the arm advisory, and the final blotter.
- Any silent non-event (no lock by ref time + a few minutes, trigger never fired despite a
  visible ≥ momentum% premium move) is a finding — capture the timestamp and logs.

## 5b. Validate the NEW authorization model (no real money required)

These checks exercise the v0.56.0 model itself. Everything here can be done with the
broker connected but **`LIVE_AUTOPLACE_ARMED` unset**, so entries dry-run-log instead of
transmitting — you are validating the control surface, not placing orders.

- [ ] **Enable requires caps.** In Deploy-to-Live, try to enable with lots or
      max_lots_per_day or max_concurrent set to 0 → must be **refused (400)**. This is the
      guard against the old allow-all fast path that would have traded unbounded.
- [ ] **Enable runs the preflight chain.** Try enabling a deployment that is PAUSED, or
      whose strategy is retired, or with the broker disconnected → each must refuse with a
      distinct, honest message. These seven checks moved from the arm route; if any now
      passes silently, it is enforced nowhere.
- [ ] **Enable is not session-scoped.** Enable after 15:00 IST → must **succeed** (the old
      "cannot arm after 15:00" rejection is gone). Confirm the deployment shows LIVE and
      stays LIVE — no expiry countdown anywhere in the UI.
- [ ] **The entry cutoff still bites.** With a deployment live, confirm that after 15:00 IST
      the arm-state/live strip stops counting it as able to transmit an entry
      (reason `after_entry_cutoff`). This is the ONLY thing preventing a late entry now.
- [ ] **Disable vs Stop are distinct.** Disable → deployment returns to paper, open
      positions untouched. Stop → flattens, returns to paper, AND pauses.
- [ ] **Stop-ALL and the kill switch find live deployments.** With ≥1 live deployment,
      hit Stop-ALL: it must list that deployment in its response (`disarmed_live_deployment_ids`)
      and leave it paper + PAUSED. Same for the kill switch. **If either reports an empty
      list while a live deployment exists, stop immediately** — that is the exact
      silent-vacuous-selector failure this release was fixing.
- [ ] **A killed deployment cannot re-enter.** After a kill, confirm the deployment is
      PAUSED and that a subsequent confirmed signal produces no live attempt.

## 6. Only after a clean paper day: the 1-lot live day (separate, later)

Governed by `docs/live-readback-checklist.md` (static IP, `LIVE_AUTOPLACE_ARMED`,
Flattrade daily OAuth, smallest lot). That day — and only that day — proves the live-guard
half of 5B: guard-driven per-leg exits + confirm-flat finalize, lazy reversal arming off a
real STOP-class exit, the `exit_time` square, and (via a deliberate mid-session backend
restart WITH a live position open) the recovery symbol-space join shipped in `fa1432c`.

Under the v0.56.0 model the live day is reached by: enable live on ONE deployment with
`lots: 1` and tight caps, set `LIVE_AUTOPLACE_ARMED=1`, rebuild, connect the broker.
Two things to watch that are new:

- **The guard's exits are now real from the first minute.** There is no dry-run rehearsal
  of the exit path any more — the first stop/target/trail the guard computes will transmit.
  Watch the first exit closely and be ready on the kill switch.
- **Nothing expires.** The deployment stays live tomorrow unless you Disable or Stop it.
  End the live day by explicitly disabling it; do not rely on an arm lapsing at 15:00.
