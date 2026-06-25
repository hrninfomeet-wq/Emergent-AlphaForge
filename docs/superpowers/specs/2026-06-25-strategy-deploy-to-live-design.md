# Strategy → Deploy to Live — Continuous Auto-Place (Design Spec)

**Date:** 2026-06-25
**Status:** Design approved (pending written-spec review)
**Branch:** `feat/strategy-deploy-to-live` (worktree off `main`)
**Predecessors (all merged to `main`):**
- L0–L2 Safe Core + L3 manual order — `backend/app/live/`, specs
  `2026-06-21-live-execution-safe-core-design.md`, `2026-06-22-live-execution-L3-design.md`,
  `2026-06-23-live-order-page-design.md`.
- Software exit guard + GTT/OCO backstop (2026-06-25 HANDOFF §2).
- Signal/paper path — `deployment_evaluator.py`, `paper_auto.py`, paper deployment controls.

---

## 1. Goal

Let a **deployed backtest strategy** route its **continuous live signals** through the existing
Live-Trading order choke-point + software exit guard, so a chosen, armed deployment places **real
Flattrade orders** under hard caps — instead of only auto-paper-trading.

Today the two paths never touch: `deployment_evaluator.evaluate_active_deployments` tees a clean
CONFIRMED signal into `paper_auto.auto_paper_trade_for_signal` (paper only), while real orders are
reachable only via the manual single-shot `LIVE_TEST` path
(`POST /live-broker/order/place` → `executor.place_live_test_order`). This feature builds the
**continuous live tee** and the **arm/caps envelope** that makes unattended auto-placement safe.

### 1.1 Explicit authorization change (recorded deliberately)

The prior live work carried a hard rule: *"the user does every Place click."* **The user has
explicitly changed this rule for deployed strategies** (brainstorming, 2026-06-25): a deployment,
once **armed** by the user with explicit caps, may **auto-place** real orders within that envelope
**without a per-order click**. The human's authorization moves from *per-order* to *per-deployment
arm*. This is a deliberate, user-made decision and overrides the earlier per-order constraint.

**What does NOT change:**
- **The assistant never personally transmits or squares a real order.** The assistant builds the
  machinery and all rails; the *user's arm* is the authorization; the running backend transmits
  autonomously inside the user-set envelope. All host tests run against `MockNoren`; the only real
  fills are the user's own live validation.
- The **per-order safety chain stays in force**: long-only (option BUY only), fresh server-side
  dry-run + margin + idempotency + `engine.can_trade`, a rate throttle, the software exit guard,
  the kill switch, market-hours-only, and offline-first env gates.

### 1.2 Non-goals (explicitly out of scope for v1)

- **SELL/short-premium or spread strategies** — long-only only; a CE/PE signal maps to **buying**
  that option leg, never selling-to-open. Short/spread deployments are disallowed for live routing.
- **Overnight / positional live** — v1 live positions are **MIS intraday**, force-squared at
  15:00 IST. No `allow_overnight` for live.
- **Shadow paper-alongside-live** — an armed deployment routes to live **only** (auto-paper
  suppressed). The per-signal live-vs-paper parity scorecard is a future enhancement.
- **Market orders** — Flattrade has none; LMT / SL-LMT only (unchanged).
- **A new manual per-signal Place queue / approvals UI** — superseded by armed auto-place; the
  dormant `approval_store` stays dormant.
- **Fully-automatic arm (no human)** — the human always performs the explicit arm.

---

## 2. The eight locked decisions (from brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | Gating model | **Armed auto-place within caps** (user changed the per-order-click rule) |
| 2 | Lots per signal | **User-set per deployment**, overriding the strategy's own sizing |
| 3 | Arm scope & lifetime | **Per-deployment arm, daily auto-disarm** (EOD 15:30 IST / token expiry) |
| 4 | Lot ceiling | **Account-level absolute max** (default **20**) + per-deployment lots clamped within it |
| 5 | Daily-loss breach | **Pause only that deployment** (extends the soft daily governor to live) |
| 6 | Exit parity | **Full parity** — add spot-mirror + time-stop to the live guard; premium SL/TP/trail + 15:00 EOD + GTT/OCO as nets |
| 7 | Paper vs live | **Replace** — armed deployment routes live-only; auto-paper suppressed |
| 8 | Offline-first master gate | **`LIVE_AUTOPLACE_ARMED=1`** required on the backend; else dry-run-log intended places |

---

## 3. Architecture overview

Build on the existing units; add focused new ones. New = **bold**.

| Unit | File | Responsibility |
|---|---|---|
| Deployment live-arm state | `strategy_deployments.py` + `deployment` doc `risk.live` | Per-deployment armed/caps/armed_until; the `is_deployment_live_allowed` predicate |
| **Continuous live sink** | **`backend/app/auto_live.py`** (mirrors `paper_auto.py`) | Resolve fresh premium + capped lots + source-run exit plan; claim the signal; call the executor; journal a `live_trades` doc; advance the signal |
| Shared transmit core | `backend/app/live/executor.py` | Factor the post-claim transmit+arm+abort-protect into one helper; add `place_deployed_order(...)` sibling to `place_live_test_order` |
| **Live deployment governor** | **`backend/app/live/live_deploy_governor.py`** (+ reuse `deployment_kill_switch`) | Per-deployment caps: `max_concurrent`, `max_lots_per_day`, `daily_loss_cap`; breach → pause that deployment only |
| Account lot ceiling + throttle | `backend/app/live/safety.py` + `kill_switch` config | Account absolute `max_lots_per_order` (default 20); wire `RateThrottle` (SEBI <10/sec) into the deployed path |
| Full-parity guard | `backend/app/live/live_position_guard.py` + `live_sl_monitor.py` | Add live **spot-tick** read → spot-mirror + time-stop exits + 15:00 IST EOD square; premium SL/TP/trail unchanged; multi-position |
| Routing tee | `backend/app/deployment_evaluator.py` | After the clean-signal branch: armed → `auto_live`; else (paper-mode) → `auto_paper` |
| Endpoints | `backend/app/routers/deployments.py` + `live_broker.py` | `live/arm`, `live/disarm`, `live/stop`, `live/status`; extend `stop-all`; account ceiling in safety-config |
| Frontend | `frontend/src/pages/LiveTrading.jsx` + `PaperTrading.jsx` + `components/live/` | Deploy-to-Live caps form + danger arm dialog; Live Deployments strip; banner |

Reused verbatim: `executor` gate primitives, `order_builder.build_intent`/`validate_and_build`,
`margin`, `idempotency` (per-signal cid + `deployment_id`), `auto_square.square_position`,
`execution_policy` (exit parity), `engine` (`can_trade`, sticky halt), `kill_switch`
(`panic_squareoff`), `flattrade_client`, `overall_controls`, GTT/OCO.

### 3.1 Data flow (one armed signal)

```
1m close → evaluate_deployment_on_close → signals doc (CONFIRMED, option_contract, direction, risk_hints)
        → evaluate_active_deployments:
             if auto_live_enabled(deployment):                       # armed ∧ allowed ∧ env-gated
                 auto_live_trade_for_signal(db, deployment, signal):
                     claim_signal_for_live_trade (atomic, one-trade-per-signal)
                     ref_ltp = resolve FRESH option premium (refuse if stale)
                     capped_lots = clamp(deployment.risk.live.lots, account_ceiling)
                     levels = source-run exit plan (premium SL/TP/trail + spot-mirror + time-stop)
                     governor.check (max_concurrent / max_lots_per_day / daily_loss_cap)  → skip+pause on breach
                     executor.place_deployed_order(contract, side="B", ref_ltp, levels, capped_lots, ...):
                         Gate0 long-only · Gate1 deployment-arm · Gate2 dry-run(lots=capped) · Gate3 margin
                         · Gate4 verdicts · Gate5 qty==capped·lot_size · Gate6 can_trade · Gate7 idem-claim
                         · Gate8 RateThrottle · [LIVE_AUTOPLACE_ARMED? place : dry-run-log] · arm-or-abort
                     arm → guard.register(stop/target/trail + spot_exit + time_stop + underlying_key)
                     journal live_trades doc; increment per-deployment day counters; advance signal lifecycle
             elif auto_paper_enabled(deployment):                    # unchanged paper path
                 auto_paper_trade_for_signal(...)
Guard loop (~1.5s, market hours):
   read broker position book (premium lp) + live spot tick map
   per position: premium SL/TP/trail (evaluate_exit) ∪ spot-mirror ∪ time-stop → remove-before-square
   basket overall controls; 15:00 IST → square all deployed positions
```

---

## 4. Authorization & the deployment `risk.live` model

Manual `LIVE_TEST` single-shot is **untouched** (separate concern, separate state). Live-arm is a
**per-deployment** flag so N deployments arm independently (chosen over a shared global
`LIVE_ARMED` mode, which would coarsely enable the manual ticket too).

### 4.1 `deployment.risk.live` schema (new)

```jsonc
"risk": {
  "live": {
    "armed": true,
    "armed_at": "2026-06-25T03:50:00Z",
    "armed_until": "2026-06-25T09:30:00Z",   // 15:00 IST today (== the EOD square cutoff); see §4.3
    "lots": 2,                                // user-set lots PER SIGNAL (overrides strategy sizing)
    "max_lots_per_day": 10,                   // sum of lots placed today across signals
    "max_concurrent": 1,                      // open live positions for this deployment at once
    "daily_loss_cap": 5000.0,                 // ₹; realized+unrealized today → pause this deployment
    "armed_by": "user",
    "disarmed_reason": null                   // "eod" | "token_expiry" | "manual" | "daily_loss" | "halt"
  }
}
```

`lots` is the user's chosen per-signal lot **count** (not the strategy's sizing replay). It is
clamped to the account ceiling at place time (§5.3). All caps optional with safe defaults
(`max_concurrent=1`, `max_lots_per_day = lots`, `daily_loss_cap=None` → no per-deployment loss
pause, only account-level + manual).

### 4.2 `is_deployment_live_allowed(deployment, now_utc, *, connected)` (pure-ish predicate)

Returns `(ok: bool, reason: str)`. True iff **all**:
- `risk.live.armed is True` (literal),
- `now_utc < armed_until`,
- in market hours (09:25–14:50 IST signal window already applied by the evaluator; the guard/EOD
  handle 15:00),
- `connected is True` (a broker token is stored).

Fail-closed: missing/malformed `risk.live`, expired arm, not connected → `(False, reason)`. The
**env master gate** `LIVE_AUTOPLACE_ARMED` is checked separately at the transmit boundary (§7), so
the predicate stays unit-testable without env.

### 4.3 Arm lifetime & auto-disarm

- On arm: `armed_until = today 15:00 IST` (== the EOD square cutoff; new entries are already blocked
  after 14:50 by the signal-window guard, so `armed_until` is the hard backstop on top of that).
- **Daily auto-disarm:** the evaluator/guard set `armed=False, disarmed_reason="eod"` when
  `now >= armed_until`; arming must be repeated each trading day.
- **Token-expiry disarm:** if the Flattrade token is absent/expired at evaluate time, treat as not
  allowed (`connected=False`) and disarm with `disarmed_reason="token_expiry"`.
- **Loss/halt disarm:** governor breach (§6) or `engine.halt` disarms with the matching reason.

---

## 5. Shared transmit core + `place_deployed_order` (`executor.py`)

The executor remains the **sole entry chokepoint** (the grep contract in
`tests/test_live_l3_routes.py` must still pass — exactly one `client.place_order` call site in the
module, reachable only through executor functions).

### 5.1 Refactor (behavior-preserving)

Extract the post-claim core of `place_live_test_order` — `record_intent` → `claim_for_submit` →
**the single `client.place_order`** → `mark_submitted` → arm, with the `_abort_protect`
exception-total wrapper — into a private helper `_transmit_and_arm(...)`. `place_live_test_order`
calls it with `consume_single_shot` in the post-fill block (unchanged). This keeps one
`place_order` call site shared by both entry functions.

### 5.2 `place_deployed_order(...)` (new sibling)

Same signature shape as `place_live_test_order` plus `capped_lots: int` and
`deployment_id: str`, minus the single-shot semantics. Gate chain (numbers map to the existing
doc):

- **Gate 0 — long-only.** `side != "B"` → blocked `side_must_be_buy` (verbatim reuse).
- **Gate 1 — authorization.** Instead of `is_live_order_allowed(mode)`, require an injected
  `allow_fn() -> (bool, reason)` bound to `is_deployment_live_allowed(deployment, now, connected)`.
  No `consume_single_shot`. (The mode store stays `PAPER`/`LIVE_OFFLINE`; the deployed path does
  not flip global mode.)
- **Gate 2 — fresh server-side dry-run.** `build_intent(..., lots=capped_lots,
  fat_finger_cap=account_ceiling, ...)`. `capped_lots` is pre-clamped (§5.3); `fat_finger_cap` is
  the **account ceiling** (default 20), not 1. Non-numeric ceiling → default-deny (verbatim
  behavior).
- **Gate 3 — margin.** `margin_verdict(limits, ref_ltp, lot_size)` with broker-resolved lot size,
  for the **full `capped_lots × lot_size`** quantity (margin must cover the real size).
- **Gate 4 — all verdicts pass** (intent non-None).
- **Gate 5 — qty defense-in-depth.** `intent.qty == capped_lots × resolved_lot_size` AND
  `capped_lots <= account_ceiling`; else `not_within_lot_cap`. This is the fat-finger backstop that
  replaces the old `qty == lot_size` (1-lot) check.
- **Gate 6 — `engine.can_trade()`** (sticky halt / latch).
- **Gate 7 — idempotency claim.** Fresh `new_client_order_id()`; `record_intent(intent,
  mode="live", deployment_id=...)` then `claim_for_submit(cid)` (atomic, one winner).
- **Gate 8 — RateThrottle (new in this path).** `safety.RateThrottle` token-bucket (9/sec). On
  throttle → blocked `rate_throttled` (no place); the signal is journaled and naturally retried on
  the next bar (per-bar cadence keeps real volume far below the limit; the throttle protects
  against a multi-deployment same-bar burst approaching the SEBI 10/sec ORL).
- **Transmit boundary** — if `LIVE_AUTOPLACE_ARMED` env is **not** set: **do not call
  `place_order`**; return `{placed: False, dry_run: True, would_send, verdicts}` (offline-first).
  If set: `_transmit_and_arm(...)` (the one real `place_order`), then arm (no single-shot consume,
  no SessionStore, no 10-min timer — see §8). Any post-fill exception → `_abort_protect`
  (best-effort square + `engine.halt`), as today.

The 1-lot path (`place_live_test_order`) is **unchanged**; the manual ticket keeps its hard pin.

### 5.3 Lot clamping (single source)

`capped_lots = max(1, min(int(deployment.risk.live.lots), account_max_lots_per_order))`. Computed
in `auto_live` and **re-verified** at Gate 5. `account_max_lots_per_order` from safety-config
(default 20).

---

## 6. Live deployment governor (`live_deploy_governor.py`)

Pure, host-testable cap checks run by `auto_live` **before** `place_deployed_order`:

- **`max_concurrent`** — count this deployment's open live positions (from `live_trades` status
  OPEN, cross-checked against the guard registry). At/over cap → skip (`reason=max_concurrent`), no
  pause (self-clears as positions close).
- **`max_lots_per_day`** — sum of `lots` placed today for this deployment (from `live_trades`
  created today). If placing this signal would exceed the budget → **skip the signal** (no place);
  no pause, no disarm — the budget naturally stops further entries for the day.
- **`daily_loss_cap`** — realized + open-MTM ₹ for this deployment today. Breach → **pause only
  this deployment** (`status=PAUSED`, `risk.live.armed=False`, `disarmed_reason="daily_loss"`),
  mirroring `deployment_kill_switch.check_soft_daily_governor` (which `auto_paper` already calls).
  Reuse that machinery extended to live realized+unrealized.

Account-level limits stay in the existing `safety`/`kill_switch` config and `engine.halt`; a global
halt stops **all** deployed placing (Gate 6) and disarms with `disarmed_reason="halt"`.

---

## 7. Continuous live sink (`auto_live.py`)

Structural clone of `paper_auto.py` so journaling/exit semantics stay aligned; the side effect is a
**real order** instead of a paper trade.

- `auto_live_enabled(deployment)` → `is_deployment_live_allowed(...)` (armed ∧ within window ∧
  connected). Env master gate handled inside the executor transmit boundary.
- `claim_signal_for_live_trade(db, signal_id, "auto_live")` — the **same atomic claim** pattern as
  `claim_signal_for_paper_trade` (one trade per signal; paper and live can never both fire one
  signal). Replace model: a deployment is either paper-claiming or live-claiming, never both.
- `resolve_live_entry_ref_ltp(...)` — option premium for the contract from the **live tick within
  the freshness window**; **require `fresh=True`** (reuse `option_premium.resolve_premium`'s `fresh`
  flag). A stale candle-close ref_ltp during market hours could mis-band the live order, so on
  stale/absent tick → **refuse** (journal `live_trade_error`, release claim). Never spot-price.
- `resolve_capped_lots(...)` — §5.3 (user-set lots, clamped). **Not** the sizing-replay
  `resolve_deployment_lots` (live uses the user's fixed lot count by decision #2).
- `resolve_live_exit_plan(signal_doc, deployment)` — build the `levels` dict for the guard:
  premium `stop_pct/target_pct` (strategy hints → deployment `auto_paper_*` fallback, via the same
  `compute_auto_risk_levels`), `trail` (deployment `exit_controls`), plus **spot-mirror**
  (`compute_spot_exit_levels`) and **`time_stop_minutes`** carried to the guard. If a deployment has
  **no** configured stop on any axis → use the guard deep-default premium stop
  (`_GUARD_DEFAULT_STOP_PCT`) as the catastrophe floor (never unprotected).
- On success: insert a `live_trades` doc (mirrors the paper-trade doc fields + `norenordno`, `cid`,
  `deployment_id`, `source="auto_live_on_signal"`, the verdicts snapshot), advance the signal
  lifecycle (TRIGGERED → ACTIVE) with a `live` audit snapshot, increment day counters.
- On dry-run (`LIVE_AUTOPLACE_ARMED` unset): journal an intended-order audit on the signal, no
  `live_trades` insert, release the claim so a real arm later can place it (or not — it's per-bar).

**Routing tee** in `evaluate_active_deployments`: after the clean-signal re-read, branch on
`auto_live_enabled` first (replace), else `auto_paper_enabled` (unchanged). One claim per signal.

---

## 8. Full-parity multi-position guard (`live_position_guard.py` + `live_sl_monitor.py`)

The registry is already multi-position and the loop is the right home (runs in `server.py`
lifespan, market-hours-gated, reads the broker position book every ~1.5s). Additions:

### 8.1 Spot-mirror + time-stop exits

- `register(...)` gains optional `spot_exit` (`{instrument_key, direction, spot_target, spot_stop}`
  from `compute_spot_exit_levels`), `time_stop_minutes`, and `entry_ts`.
- The cycle gains a **live spot-tick read** via an injected `spot_tick_fn` bound to
  `upstox_stream_manager.latest_tick_map()` (host-testable; the broker book stays the premium
  source). Per guarded position, after the premium `evaluate_exit`, if still open:
  - **spot-mirror:** `execution_policy.spot_mirror_exit_reason(direction, spot_price, ...)` →
    square (same `square_position`, remove-before-square). Direction-aware, stop-first — identical
    to the paper marker and backtest `spot_exit` mode.
  - **time-stop:** `now - entry_ts >= time_stop_minutes` → square (`reason=time_stop`).
- Staleness: spot-mirror/time-stop fire only on a **fresh** spot tick; a stale/zero premium `lp`
  still fails closed in `evaluate_exit` (no spurious square) — so the EOD square + GTT/OCO remain
  the independent catastrophe nets.

### 8.2 15:00 IST EOD square (no overnight v1)

The cycle, when `IST time >= 15:00` (configurable; before the broker's own MIS auto-square), squares
**all guarded deployed positions** via `square_position` (remove-before-square), reason
`eod_square`. Manual `LIVE_TEST` positions are unaffected (they have their own 10-min timer).
Distinguish deployed vs manual entries in the registry by a `source`/`deployment_id` field so EOD
only sweeps deployed ones.

### 8.3 What does NOT carry from the manual path

- **No `SessionStore`** (single-position) and **no 10-min `_schedule_auto_square`** for deployed
  positions — those are LIVE_TEST test-order safeties. Deployed exits = strategy SL/TP/trail +
  spot-mirror + time-stop + 15:00 EOD. The arm for a deployed order registers with the guard and
  records a `live_trades` doc; it does not touch the global session singleton.
- `LIVE_GUARD_ARMED=1` is still required for the guard to **transmit** squares (offline-first);
  dry-run logs intended squares otherwise. (Independent of `LIVE_AUTOPLACE_ARMED`.)

---

## 9. Endpoints

In `routers/deployments.py` (sibling to the existing pause/resume/stop/stop-all):

- **`POST /api/deployments/{id}/live/arm`** — body `{lots, max_lots_per_day, max_concurrent,
  daily_loss_cap, confirm: true}`. Guards: deployment ACTIVE + not retired + not drifted; broker
  connected; `engine.can_trade`; strategy long-only-eligible; `confirm` literal True (danger).
  Sets `risk.live` armed + `armed_until` (EOD). Returns the armed state + a clear note when
  `LIVE_AUTOPLACE_ARMED` is **not** set (so the UI shows "armed but backend dry-run").
- **`POST /api/deployments/{id}/live/disarm`** — clears `armed` (does **not** flatten open
  positions; the guard keeps protecting them).
- **`POST /api/deployments/{id}/live/stop`** — scoped flatten of this deployment's open live
  positions (via `panic_squareoff` restricted to its tsyms / registry entries) **then** disarm.
- **`GET /api/deployments/{id}/live/status`** — armed, armed_until countdown, caps, today's
  counters (orders/lots/realized ₹), open live positions, env-gate state, guard status.
- Extend **`/deployments/stop-all`** to also flatten + disarm all live deployments.

In `routers/live_broker.py`: extend `_SafetyConfigBody` + the store with
**`max_lots_per_order`** (account ceiling, default 20, validated ≥1).

No new "place" route — placement is the backend evaluator loop (the auto-place decision).

---

## 10. Frontend

- **Deploy-to-Live action per deployment** (on `PaperTrading.jsx`'s deployment strip and/or
  `LiveTrading.jsx`): a caps form (lots/signal, max lots/day, max concurrent, daily loss cap) → a
  **danger typed-confirm arm dialog** (matches the existing danger-confirm pattern). Surfaces the
  account ceiling and clamps the lots input to it; warns prominently when `LIVE_AUTOPLACE_ARMED` is
  unset ("backend will dry-run-log, not transmit").
- **Live Deployments strip** (extends the paper deployment-controls strip): per armed deployment —
  `armed_until` countdown (reuse `TokenCountdown`), today's orders/lots/realized ₹, open positions,
  **Disarm** / **Stop** (flatten+disarm) buttons, master **Stop-all live**.
- **`LiveBanner`** reflects "N deployments armed live" and the env-gate state.
- Reuse `GuardPanel` for per-position guard state; theme tokens + kebab-case testids per the Kiro
  conventions bible; contract tests in the same commit.

---

## 11. Safety envelope (the invariants, in one place)

1. **Two independent offline-first env kills:** `LIVE_AUTOPLACE_ARMED=1` (entries) and
   `LIVE_GUARD_ARMED=1` (square transmits). Either unset → that side dry-run-logs only.
2. **Explicit per-deployment arm** with typed confirm; **daily auto-disarm** (EOD / token expiry /
   loss / halt).
3. **Long-only** — option BUY only; CE/PE → buy that leg; SELL/short/spread deployments rejected.
4. **Per-order chain:** fresh dry-run + margin (full size) + idempotency + `engine.can_trade` +
   RateThrottle, every signal.
5. **Lot caps:** per-deployment `lots` clamped to account ceiling (default 20); Gate-5 re-check;
   fat-finger default-deny on non-numeric.
6. **Per-deployment caps:** `max_concurrent`, `max_lots_per_day`, `daily_loss_cap` → breach pauses
   only that deployment.
7. **Full-parity exits** + 15:00 EOD square + GTT/OCO + kill switch (flatten+halt) as nets.
8. **Fresh-tick entry** — no stale-candle live orders.
9. **The assistant never transmits** — host tests use `MockNoren`; the user performs the live arm
   and validates the one real fill.

---

## 12. Residual risk (documented, not hidden)

**If the PC dies mid-position, the software guard stops** (it runs in the user's backend). For MIS
intraday positions the broker's own session-end auto-square is the fallback; an optional manual
NRML GTT/OCO is the only true PC-off protection. v1 documents this clearly; it does not claim the
guard always protects. (Consistent with the operating reality that the PC is rarely on in market
hours — live arming is an explicit, supervised act.)

---

## 13. Testing approach (TDD, host-only, `MockNoren`)

- **Executor:** `place_deployed_order` gate-by-gate — long-only block, arm-allow predicate,
  capped-lots dry-run, margin for full size, Gate-5 `not_within_lot_cap` (over-ceiling and
  qty-mismatch), throttle block, idempotency one-winner, dry-run vs transmit on the env gate,
  arm-or-abort on post-fill failure. The single-`place_order`-call-site grep contract still passes.
- **Governor:** `max_concurrent` skip, `max_lots_per_day` skip/disarm, `daily_loss_cap` pause-this-
  deployment-only.
- **`auto_live`:** atomic claim (paper vs live mutual exclusion), fresh-tick refusal, replace
  routing in `evaluate_active_deployments`, exit-plan resolution (premium + spot-mirror + time-stop
  + deep-default fallback), counters, lifecycle transitions.
- **Guard:** spot-mirror exit (direction-aware, stop-first), time-stop exit, 15:00 EOD square of
  deployed-only positions, remove-before-square (no double exit), stale-tick fail-closed.
- **Mode/arm:** `is_deployment_live_allowed` truth table; arm/disarm/stop endpoints; daily
  auto-disarm; account-ceiling safety-config validation.
- **Routes:** arm/disarm/stop/status + stop-all flatten; contract-corpus string asserts.
- **Frontend:** `CI=true npm run build`; testid contract tests.
- **Docker + live readback:** rebuild backend; with `LIVE_AUTOPLACE_ARMED` unset, confirm armed
  deployments **dry-run-log** (no transmit). Then the user's supervised first arm: 1 lot, one real
  fill, guard squares clean, EOD square verified — a real-money-critical readback.

---

## 14. Build order (for the plan)

1. Deployment `risk.live` model + `is_deployment_live_allowed` + account-ceiling safety-config.
2. Executor refactor (`_transmit_and_arm`) + `place_deployed_order` + throttle gate (TDD).
3. `live_deploy_governor` (TDD).
4. `auto_live.py` + the `evaluate_active_deployments` tee (TDD).
5. Guard: spot-mirror + time-stop + EOD square (TDD).
6. Endpoints (arm/disarm/stop/status; stop-all; safety-config) + contract tests.
7. Frontend (caps form + danger arm + live strip + banner) + build.
8. Docker rebuild + dry-run verification + user-supervised live readback.

Each step: failing test → implement → green → code-quality review, per the project's TDD +
adversarial-audit loop.
