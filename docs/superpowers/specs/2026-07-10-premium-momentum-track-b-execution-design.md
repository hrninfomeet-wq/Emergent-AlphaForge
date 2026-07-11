# Design Spec: Premium-Momentum Track B — Live/Paper Execution Capability (Phase 3)

**Date:** 2026-07-10
**Status:** Draft for user review (recon-grounded against main tip `0ebaff0`)
**Predecessors:** `2026-07-10-premium-momentum-contingency-strategy-design.md` (Phases 0–2 built:
backtest sim + cost model + honest tuner, all on local main). User decisions: Track A before B ✅ (done);
Track B **straight to live-capable** (env-gated, offline-first — never a new bypass).

---

## 1. Goal

Make the app **execute** a time-based, premium-based option-buying strategy on the existing
deployment rails, with backtest↔live parity by shared pure helpers. Per session, per armed
premium-momentum deployment:

1. At the **reference time** (default 09:31 IST), **lock** the chosen-moneyness CE **and** PE
   strikes from spot (weekly expiry) and capture each option's **reference premium from live ticks**.
2. **Monitor both premiums** at bar cadence; when one rises ≥ the momentum threshold over its ref,
   emit a normal CONFIRMED signal for that side (**first-to-trigger**, one entry per session).
3. The signal flows through the **unchanged** entry chain (claim → caps → fresh-tick ref →
   executor gates → place/arm) — paper when not armed, live when armed + `LIVE_AUTOPLACE_ARMED`.
4. Exits via the **existing guard**: premium stop/target + a new **stepped X-Y trail mode** +
   inherited EOD square, Layer-2 widening re-price, confirm-flat finalization.
5. The locked strikes stay **pinned in the option-tick subscription** all session; all state
   **survives restarts** via a persisted lock doc + a recovery step.

**Explicit non-goals (v1):** lazy legs / re-entries (Phase 5), %-based trail, tick-cadence
entries, config-block graduation (Phase 4), any new order-placement path or env gate, and
**non-NIFTY instruments** (the evaluator's new-bar gate is NIFTY-keyed and all validation data
is NIFTY — §6.6). Sizing is the existing path only: qty = lots × lot_size through the current
deployment sizing/caps chain, no new sizing code.

---

## 2. Architecture: ride the existing rails

**One new pre-entry component** (the per-session strike-lock + two-strike premium monitor inside
the deployment evaluator) plus **small extensions at seven verified seams**. No parallel engine —
every safety layer (arming, caps governor, kill switch, exit claims, OCO, guard, blotter,
failure surfacing) applies to premium-momentum deployments untouched.

**Strategy vehicle:** a `premium_momentum` `StrategyBase` plugin registers the strategy (id,
params schema: reference_time, moneyness, side, momentum %/pts, stop/target %/pts, trail_x/y,
lots) so deployments/UI/params work normally. The plugin's `evaluate()` is a thin adapter —
the evaluator's Track B branch does the lock/monitor work and consults the shared pure helpers
(`app/premium_momentum.py`) directly, so the rules cannot drift from the backtest.

### Session state machine (persisted, IST-keyed)

```
PENDING_LOCK ──ref-time bar──▶ LOCKED(ce,pe,+ref premiums)
   │ no fresh tick → retry each bar until LATE-LOCK CUTOFF (10:15) ─▶ DONE(no_lock)
LOCKED ──side crosses threshold + signal journals clean──▶ TRIGGERED(side latched atomically)
TRIGGERED ──entry places/arms──▶ ENTERED(norenordno)
ENTERED ──guard confirm-flat on_close──▶ DONE(exited)      ← the ONLY exit-state driver
any state ──session_date != today──▶ superseded next session (no cron)
```

The **triggered_side latch is set only after the signal journals cleanly** (a trigger refused by
entry-window blocks must not burn the session's single entry). The transition to DONE(exited)
is driven **only** by the guard's confirmed-flat close hook — never by place-acceptance.

---

## 3. Verified extension points (main @ `0ebaff0`)

1. **Lock persistence — new `premium_locks` collection**, unique compound index
   `(deployment_id, session_date)` (modeled on the signals dedupe index, `db.py:49-54`).
   Doc: `{deployment_id, session_date, ce/pe: {instrument_key, strike, trading_symbol,
   expiry_date, lot_size, ref_premium, ref_ts}, spot_at_ref, locked_at, triggered_side,
   entered_norenordno, done_for_day, reason}`. Create-once crash-safety via duplicate-key-adopt;
   triple-duties as pin source, recovery source, audit trail.
   *Rejected:* a field on the deployment doc (read-modify-write races with `risk.*` writers).
2. **Strike lock replaces per-bar re-resolution** — the Track B evaluator branch bypasses the
   per-bar contract re-resolution from current spot (`deployment_evaluator.py:397-409`); at/after
   the reference bar it calls `lock_reference_strike` twice (CE+PE) against the active-expiry
   contract set (same expiry filter as `_resolve_option_contract` :144-147; per-session weekly via
   `next_expiry_for` :171-183), persists the lock, and every later bar resolves **from the lock**.
3. **Ref-premium capture + pre-entry monitor** — both locked keys read from
   `upstox_stream_manager.latest_tick_map()` through the canonical `resolve_premium`
   (`live/option_premium.py:123-197`; ms-normalized, 120 s freshness) — the same price contract
   the entry path uses. Refs persisted immediately (ticks are not replayable across an outage).
   Stale/missing tick ⇒ HOLD, never trigger. **Late-lock policy:** first bar with fresh ticks
   after ref time, hard cutoff 10:15 ⇒ `done_for_day(no_lock)`; actual `locked_at` recorded.
4. **Premium entry gate at bar cadence** — inside the existing evaluator loop (2 s poll gated on
   a fresh 1 m bar, `runtime.py:374-447`): per side `momentum_triggered(premium_now, ref, pct=X)`;
   first-to-trigger latches `triggered_side` via an atomic filtered `update_one` (the
   `auto_live.py:72-98` claim pattern — the bar-level signal dedupe alone cannot prevent the
   other side entering on a later bar). Emits a normal CONFIRMED signal with `option_contract`
   pre-filled **from the lock**, inheriting all three double-entry guards.
5. **Executor wiring — unchanged** — tee at `deployment_evaluator.py:637-646` (armed →
   `auto_live`, else paper); sink `auto_live_trade_for_signal` (`auto_live.py:250-519`);
   chokepoint `place_deployed_order` (`executor.py:370-531`, `LIVE_AUTOPLACE_ARMED` boundary
   :499-506). **Plus a last-line re-check**: between the fresh-tick refusal (:339-348) and
   arm/place, re-verify `momentum_triggered` on the entry tick; on failure journal
   `live_trade_error='premium_trigger_not_met'` and release the claim (live marginally more
   conservative than the backtest's trigger-bar-close fill — intentional, documented).
6. **Stepped trail = a new guard mode `stepped_xy`** — extend `_VALID_MODES`
   (`live_sl_monitor.py:52`), carry `x/y` through `build_monitor_state`'s trail-dict copy
   (:132-138), and in `evaluate_exit`'s trail branch delegate to the **same**
   `stepped_trail_stop` helper the backtest uses (entry, peak, initial_stop, x, y) via
   `_raise_stop` (:147-154 — monotonic invariant holds since the helper is monotone in
   running-high). **Must NOT be shoehorned onto `lock_trail`** (steps-from-trigger on ltp, no
   high-water cap — diverges from the red-teamed backtest semantics for Y>X).
   `live_sl_monitor` is the state machine shared by paper and live monitors, so both get it;
   plan-time task verifies the paper monitor passes the trail dict through.
7. **Subscription pinning — one shared union helper** — today **nothing pins a key**; the
   ATM-band rebuild can silently drop a locked strike (and the WS-revive + manual-restart paths
   drop it two more ways). Fix: a single helper that unions today's lock-doc keys into the
   subscription, called from all three build sites (auto-follow `runtime.py:1160-1164` — the
   open-paper-keys precedent, appended after the cap so pins are cap-exempt; the supervisor
   revive; the manual options-stream restart route).
8. **EOD / rollover / recovery — inherited + one new step** — EOD: guard `_evaluate_eod_square`
   at 15:00 IST now applies to ALL sources (Layer-1/2; no new code). Rollover: a lock with
   `session_date != today` is simply superseded (the `armed_until_today_ist` precedent).
   Recovery: **a 4th step in `live_startup_recovery`** (`runtime.py:253-309`; note main has no
   `maybe_run_live_recovery` — that name is audit-item #5 on the unmerged branch): re-read
   today's locks; re-pin keys; if `entered` and the broker still holds the position, re-register
   the guard entry **with the persisted stop/trail state, deployment_id and oco_al_id** (today's
   generic rehydrate degrades to a 50 % catastrophe stop with no close-loop link — the recovery
   step is load-bearing, not optional).

**Failure visibility:** new refusal reasons (`premium_trigger_not_met`, `strike_lock_failed`,
`strike_not_subscribed`, `ref_premium_unavailable`) written to the existing
`signals.live_trade_error` / `live_intended` fields — surfaced by the deployment live-status
route and the existing entry-refused chip with zero frontend work.

---

## 4. Layer-1/2 guard constraints (must-respect, verified on main)

- **Confirm-flat is the sole finalizer** (`live_position_guard.py:661-702`): OCO-cancel,
  close-journal, registry-drop happen only on a KNOWN broker book showing flat — never on
  place-accept. Track B's `DONE(exited)` transition hangs off this hook exclusively.
- While an entry is **`squaring`**, stops are not re-evaluated (:459-462) — the stepped trail
  must not ratchet or re-fire against a position mid-square.
- **Layer-2 widening re-price** (1 % → 2 % → 4 % band, 4 s interval) already escalates a resting
  unfilled exit — Track B builds **no** escalation of its own.
- The manual 10-min timer is **gone**; EOD applies to all sources (stale comment at
  `live_deploy_context.py:85-86` must not be trusted).
- Deployed exits transmit through the `LIVE_GUARD_ARMED`-gated square_fn (no Gate-1.5 analogue
  on the deployed path).

---

## 5. Parity contract & documented divergences

Shared pure helpers (`lock_reference_strike`, `momentum_triggered`, `stepped_trail_stop`) are
the single source of rule truth. Divergences that remain, stated up front and measured in
paper before any live arming:

| # | Divergence | Direction |
|---|---|---|
| 1 | Ref/trigger premium: backtest = option-bar close; live = fresh tick at evaluation (~bar close + seconds) | either; measured |
| 2 | Guard evaluates every ~1.5 s on broker lp; backtest ratchets the trail on 1 m closes only | live stops strictly **tighter** (earlier exits) — safe direction |
| 3 | Entry mark = Upstox tick; exit mark = broker lp | small basis; measured |
| 4 | Last-line trigger re-check can refuse an entry the backtest would fill | live more conservative — intentional |

A parity report (paper entries/exits vs same-day sim) is part of the validation checklist, not
an afterthought.

---

## 6. Key risks (from recon) and their mitigations

1. **Branch topology (top risk):** the live-guard audit cluster (`fix/broker-truth-integrity`,
   items #1/#3/#5/#6) is **not on main**, and its reconciliation is in flight in a separate
   session (`task_452d7fbb`) — both lines touch `live_position_guard.py`/`executor.py`.
   **Precondition:** land (or explicitly close) the reconciliation **before** Track B
   implementation starts; the spec targets main and must be re-based over whatever lands.
2. **Subscription blindness windows** (WS revive, manual restart, band rebuild) — closed by the
   single shared pin helper (seam 7); during any residual gap the monitor HOLDs and the entry
   path refuses with a journaled reason (fail-visible, not fail-silent).
3. **Ref-capture outage** — late-lock policy with a hard cutoff + recorded `locked_at`.
4. **Double-entry across bars** — the atomic `triggered_side` latch (bar-level dedupe alone is
   insufficient).
5. **Restart mid-position** — recovery step 4 re-registers with persisted exit state (without
   it, a restart degrades the trail to a 50 % catastrophe stop).
6. **NIFTY-only new-bar gate** (`runtime.py:421-426`) — v1 restricts premium-momentum
   deployments to NIFTY (matching all validation data); lifting it is a separate change.
7. **15:00 collisions** (expiry-day cutoff vs EOD square) — inherited windows unchanged;
   triggers from 14:50 are refused and journaled.

---

## 7. Testing & validation

- **Host:** state-machine transitions (lock/late-lock/latch/done), `stepped_xy` mode vs the
  backtest helper (same worked examples), latch atomicity (filtered-update contract), pin-union
  helper, refusal reasons.
- **Container:** evaluator branch end-to-end with MockNoren + a fake tick map (lock → trigger →
  signal → paper entry → guard registration with stepped trail); recovery rehydration; the
  three double-entry guards.
- **Adversarial review** (the session's established bar for live-money code) before any arming.
- **Paper validation:** ≥ 10 complete paper sessions (the same threshold as the existing
  forward-metrics library gate) with entries/exits + the parity report vs same-day sim.
- **Live arming last**, behind the unchanged env gates, only after paper validates — consistent
  with the project's standing "real-money readback on hold until a live signal validates" rule.
  Market-hours dependency: the PC must be running in market hours for paper sessions (known
  operating constraint).

---

## 8. Sequencing

0. **Precondition:** live-guard reconciliation (`task_452d7fbb`) lands or is explicitly closed.
1. Lock collection + state machine + evaluator branch (paper-effective immediately).
2. `stepped_xy` guard mode + exit-plan carriage.
3. Subscription pin helper (all three call sites) + recovery step 4.
4. Last-line re-check + failure-visibility reasons + UI status chip verification.
5. Adversarial review → container rebuild → paper sessions → parity report → (user decision)
   live arming.
