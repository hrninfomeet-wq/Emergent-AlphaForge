# Phase 5B market-hours validation runbook

**Target: Monday 2026-07-20** (or the next weekday the PC can be on from ~09:00 IST).
**Mode: PAPER ONLY on day 1.** No live order is placed anywhere in this runbook's day-1 flow.

This is the first real market-hours session for everything built since Track B merged
(2026-07-12): the Layer-1/2 guard, kill-switch stop-all, recovery, and all of Phase 5B
(v0.55.0). The full host suite (3478) proves internal consistency; this day proves the
rails against the real broker/data world, where the worst historical bugs (candle-roller
boot gap, Upstox-vs-Noren recovery symbol-space) lived invisible to tests.

## What day 1 (paper) can and cannot prove — read this first

The 5B **entry** machinery runs in the evaluator and is fully exercised in paper:
both-legs locking/triggering, VIX gate, day-stop blocking, arm advisories.

The 5B **exit** machinery lives entirely in the **live position guard**
(`_live_guard_on_close` in `backend/app/runtime.py`, `square_at_ist` in
`backend/app/live/live_position_guard.py`). Paper exits go through the separate
`LiveExitMonitor`, which never touches premium locks. Therefore, **in paper mode**:

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

## 6. Only after a clean paper day: the 1-lot live day (separate, later)

Governed by `docs/live-readback-checklist.md` (static IP, `LIVE_AUTOPLACE_ARMED`,
Flattrade daily OAuth, smallest lot). That day — and only that day — proves the live-guard
half of 5B: guard-driven per-leg exits + confirm-flat finalize, lazy reversal arming off a
real STOP-class exit, the `exit_time` square, and (via a deliberate mid-session backend
restart WITH a live position open) the recovery symbol-space join shipped in `fa1432c`.
