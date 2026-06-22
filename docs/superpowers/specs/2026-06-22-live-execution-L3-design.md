# Live Execution L3 — Manual Live-Test Order Path (Design Spec)

**Date:** 2026-06-22
**Status:** Design approved (pending written-spec review)
**Branch:** `feat/live-execution-l3` (off `main` @ 2d5e2ea)
**Predecessor:** the L0–L2 Safe Core (`backend/app/live/`, merged to `main`), see
`docs/superpowers/specs/2026-06-21-live-execution-safe-core-design.md`.

---

## 1. Goal

Enable the **first real broker order** — a single, manually-confirmed, 1-lot index-option
order placed through Flattrade — under hard, code-enforced safety rails: **1 lot maximum**
and a **forced square-off within 10 minutes**. This is the "Live One-Click" rung of the
offline-first rollout (`Paper → Live-Offline → Live One-Click (THIS) → Fully-Auto`).

L3 turns the L0–L2 machinery from "everything except transmit" into "transmit exactly one
controlled order, on a human's click, with automatic exit." It is the smallest possible step
that exercises the real `place_order` / `cancel_order` path end-to-end against a real account.

### Non-goals (explicitly out of scope for L3)
- Strategy-wired automatic order firing (deferred — the next rung).
- Multiple simultaneous positions (LIVE_TEST is single-shot, single-position).
- Lot sizes > 1, or any instrument other than a user-selected liquid index option.
- Fully-automatic trading (L4).
- Market orders (Flattrade has none; LMT / SL-LMT only — unchanged from L0–L2).

### The human/agent boundary (non-negotiable)
The assistant builds the entire path and all rails. The assistant **never** transmits a real
order. The actual `Place` click and the decision to enter `LIVE_TEST` mode are the **user's**.
The 1-lot cap and 10-minute square-off are enforced in code so that even the user's click
cannot exceed them. All host tests run against `MockNoren`; the one real fill is the user's
manual validation at the very end.

---

## 2. Architecture overview

Five additions on top of the Safe Core, each a focused unit:

| Unit | File | Responsibility |
|---|---|---|
| Mode gate | `backend/app/live/mode.py` | The `PAPER / LIVE_OFFLINE / LIVE_TEST / LIVE_ARMED` state machine + the live-mode config singleton. Real orders reachable ONLY in `LIVE_TEST`. |
| Guarded executor | `backend/app/live/executor.py` | The single chokepoint that calls `client.place_order` / `cancel_order`. Enforces the full precondition chain. |
| Auto-square | `backend/app/live/auto_square.py` | The ≤10-minute forced exit: server timer + the broker SL-LMT backstop + square-now, with partial-fill handling. |
| Margin pre-check | `backend/app/live/margin.py` (or fold into `order_builder`) | Compares broker `limits()` cash against the 1-lot premium requirement; a new dry-run verdict. |
| Live friction | `backend/app/live/live_friction_profile.py` (or config) | Zero-brokerage-but-statutory cost profile for honest test P&L. |

Plus routes in `backend/app/routers/live_broker.py` and the **Manual Live-Test panel** in the
frontend (`frontend/src/pages/LiveTrading.jsx` + new `frontend/src/components/live/`).

The L0–L2 modules are reused verbatim: `build_intent` (dry-run + verdicts), `safety`,
`idempotency` (claim → submit → mark), `order_sm` (om → state), `reconcile`, `kill_switch`
(`panic_squareoff`, guardrails, latch), `engine` (`can_trade`, sticky halt), `flattrade_client`
(now actually invoked for orders), `execution_policy` (exit parity).

---

## 3. The mode gate (master safety switch)

`backend/app/live/mode.py`

### States
- `PAPER` — default at boot. No live order path reachable.
- `LIVE_OFFLINE` — read-only live data + alerts; the L0–L2 state. No orders.
- `LIVE_TEST` — the new capability. Single-shot, single-position, 1-lot hard cap. Real
  orders reachable ONLY here.
- `LIVE_ARMED` — reserved for L4; **not implemented in L3** (transitions into it are rejected).

### Transition rules
- Boot/default → `PAPER`.
- A mode change is an explicit `PUT /live-broker/mode` with the target state. Entering
  `LIVE_TEST` requires: a connected, non-expired token; `engine.can_trade()` true (not halted /
  not latched); and an explicit `confirm: true` flag in the request body.
- `LIVE_TEST` is **single-shot**: once a real order is placed, the mode is "consumed". After the
  position is squared (auto, manual, or kill), the mode **auto-reverts to `LIVE_OFFLINE`**. A
  second test requires a fresh, deliberate `LIVE_TEST` entry.
- Any halt / kill / guardrail-latch forces the mode back to `LIVE_OFFLINE` (or `PAPER`) and
  blocks re-entry to `LIVE_TEST` until reset.

### Config singleton
Mongo `live_mode` (singleton `_id="singleton"`): `{mode, since, test_session_id, confirmed_by,
single_shot_consumed: bool}`. Injectable store (mirrors `idempotency` / `SafetyConfigStore`),
DB-free for host tests via a fake collection. `current_mode()` defaults to `PAPER` if absent
(fail-safe: unknown → no live orders).

### Invariant
`is_live_order_allowed(mode_doc) -> bool` returns True **only** when `mode == "LIVE_TEST"` and
`single_shot_consumed == False`. Every real-order path asserts this first.

---

## 4. The guarded executor (the only transmission chokepoint)

`backend/app/live/executor.py`

`async place_live_test_order(intent, *, client, mode_store, intent_store, config_store, engine,
lot_size, ref_ltp, band_pct) -> dict`

The precondition chain — **every** gate must pass, in order, or NO transmission occurs and the
function returns `{placed: False, reason, verdicts}`:

1. **Mode** — `is_live_order_allowed(current_mode())` is True (`LIVE_TEST`, not consumed).
2. **Lot cap** — `intent.qty == lot_size` exactly (1 lot). Any other qty → reject (defense in
   depth behind the panel's locked input and `fat_finger_cap=1`).
3. **Fresh dry-run all-pass** — re-run `build_intent` (NOT a stale client-supplied intent) and
   require **every** verdict ok, including the new `margin` verdict (§6). Re-deriving server-side
   prevents a tampered/stale intent from bypassing checks.
4. **Engine clearance** — `await engine.can_trade()` is `(True, "")` (not halted, not latched).
5. **Idempotency claim** — `claim_for_submit(cid)` returns True (wins the per-cid claim). The cid
   is `intent.remarks` (pinned at build time) so the broker echoes it.
6. **Transmit** — `await client.place_order(intent)` (the real call). On `ok=False` → record the
   reject, classify it, do NOT consume single-shot (a rejected order placed nothing). On `ok=True`
   → `mark_submitted(cid, norenordno)`, persist the order doc, **consume single-shot**.
7. **Arm auto-square** — immediately register the deadline (§5) and place the broker SL-LMT
   backstop. If arming the square FAILS, immediately cancel/square the just-placed order and halt
   (an unprotected live position is unacceptable — **arm-or-abort**).

The executor is the **only ENTRY chokepoint** — the only path that places an *opening* order.
Exits also use `place_order` (a marketable square IS a place) plus `cancel_order`, but those calls
live exclusively in the auto-square / square-now / kill paths (§5), which are **exit-only** (they
can only reduce/flatten an existing position, never open one). So: exactly one way to open a live
position (the gated executor), several always-available ways to close one.

### Order construction
A `LIVE_TEST` entry is a **marketable-limit BUY** (option buying), 1 lot, `remarks = cid`. The
matching protective **SL-LMT** uses the trigger-relative band fix (the L1-audit follow-up: the
band references the stop trigger, not the LTP, so a legitimately-far stop isn't fail-closed).

---

## 5. The ≤10-minute auto-square (defense in depth)

`backend/app/live/auto_square.py`

When the entry **fills** (om reports `fillshares > 0`), arm three independent backstops:

1. **Server timer (primary, time-based).** An `asyncio` task scheduled for `fill_time + 10:00`.
   On fire → square the position via a marketable-limit exit (reuse `panic_squareoff` for the
   single position, or a dedicated single-position squarer). The deadline is stored in
   `live_orders` / the test-session doc so it survives a reconnect within the process.
2. **Broker SL-LMT (backstop, price-based).** Placed at entry (step 7 above) as a protective stop
   on the long option. Covers catastrophic price moves even if the server timer is delayed. Does
   NOT cover the *time* dimension (the broker has no time-trigger) — that's the server timer's job.
3. **Manual controls (always live).** `Square now` (immediate marketable exit) and the existing
   `Kill switch` (`panic_squareoff` — cancel all working + flatten all), both exit-only and never
   throttled.

### Partial fills
If only part of the 1 lot fills (unlikely for 1 lot but handled): the square targets `fillshares`,
and any still-working entry remainder is cancelled first. `order_sm`'s cumulative-max fillshares
make this deterministic.

### Entry that never fills
If the marketable-limit entry sits unfilled at the deadline, the timer **cancels the working
order** (no position) — a clean no-op exit. Single-shot is still consumed (the test happened).

### The honest risk (documented, surfaced in the UI)
The **time** guarantee depends on the backend process staying alive for those 10 minutes. If the
PC/backend dies mid-test:
- the **broker SL-LMT still caps price risk** (it lives at the broker), but
- the **time exit will not fire** until the backend is back and reconciles.

Mitigations: the test is short and **supervised** (the user is at the desk); the SL-LMT is placed
tight; on restart the engine's `resume_pending` + `reconcile_tick` detect the open position and
the auto-square re-arms / squares immediately. The UI shows a **live countdown** and a **backend
heartbeat** indicator so the user can see the timer is alive. This risk is acceptable for a
supervised single-lot test and is called out explicitly to the user.

---

## 6. The 1-lot hard cap + margin pre-check

- **1-lot cap.** In `LIVE_TEST`, `fat_finger_cap` is pinned to `1` (not user-editable). The panel's
  Lots input is locked to 1. The executor independently asserts `qty == lot_size` (§4.2). Three
  layers; any one alone blocks > 1 lot.
- **Margin pre-check** (`margin` verdict). Before the Place button arms, compare broker `limits()`
  available cash against the 1-lot premium requirement (`ref_ltp × lot_size`, plus a small buffer
  for the marketable cross + statutory charges). If cash is insufficient → `margin` verdict fails →
  Place stays disabled. "Insufficient funds" can never reach the broker. Fail-closed: if `limits()`
  can't be read or returns a non-finite cash figure, the verdict fails.

---

## 7. Zero-brokerage-but-statutory friction

`backend/app/live/live_friction_profile.py` (or a config consumed by the P&L display).

Flattrade charges **zero brokerage**, but statutory charges remain and matter for thin option
premiums: **STT (sell-side), exchange transaction charge, GST, SEBI turnover fee, stamp duty
(buy-side)**. The live-test P&L display uses this profile (brokerage = 0, statutory kept) so the
realized test P&L is honest — more accurate than the existing brokerage-inclusive backtest
friction. Used only for display in L3; wiring it into a live-cost-aware optimizer is out of scope.

---

## 8. Routes (`backend/app/routers/live_broker.py`)

All new routes are guarded; only the executor transmits.

- `GET /live-broker/mode` → current mode doc.
- `PUT /live-broker/mode` `{mode, confirm}` → transition (validates §3 rules). Entering
  `LIVE_TEST` requires `confirm: true` + connected token + `can_trade`.
- `POST /live-broker/order/place` `{contract, side, ref_ltp, band_pct, levels}` → the guarded
  executor (§4). Returns `{placed, norenordno|null, verdicts, reason}`. The ONLY transmitting route.
- `POST /live-broker/order/square` `{position_id}` → square-now (exit-only).
- `GET /live-broker/test-session` → the active test-session status: position, fill, the
  auto-square deadline + remaining seconds, backend-heartbeat timestamp, SL-LMT status.
- (Reuse from L2: `POST /live-broker/kill-switch` — but in L3 it becomes **executing** for the
  test position via `panic_squareoff`; `GET/PUT /live-broker/safety-config`, `reset-latch`.)

Note: in L3 the kill-switch route transitions from "plan only" (L2) to **executing** exits for the
single test position. This is exit-only (cancel + flatten) and remains safe — you must always be
able to exit. Entries remain gated behind the executor + mode.

---

## 9. Data model

- `live_mode` (singleton): §3.
- `live_orders` (existing): extended with `mode`, `test_session_id`, `auto_square_deadline`,
  `sl_norenordno` (the backstop), `squared_at`, `square_reason` (timer|manual|kill|sl).
- `live_test_sessions` (new): one doc per `LIVE_TEST` entry — `{session_id, contract, intent_cid,
  entry_norenordno, fill_time, deadline, status (armed|filled|squared|rejected|expired),
  realized_pnl, friction, heartbeat}`. The audit trail of a test.

---

## 10. Error handling & edge cases

- **Reject on place** — classify (`transient`/`terminal`); surface to the panel; do NOT consume
  single-shot; do NOT arm a square (nothing was placed).
- **Arm-square failure after a successful place** — immediately square/cancel the placed order and
  halt the engine (arm-or-abort, §4.7).
- **Token expiry mid-test** — reads/exits fail; the engine halts; the broker SL-LMT remains as the
  price backstop; the UI shows "reconnect to square". On re-auth, `resume_pending` + reconcile
  drive the square.
- **Partial fill** — §5.
- **Disconnect / backend death** — §5 honest risk + restart-reconcile re-arm.
- **Double-place attempt** — the per-cid `claim_for_submit` + single-shot consumption prevent a
  second entry.
- **Reconcile mismatch at any tick** — engine halts (sticky); the panel shows halted; exits still
  available via kill/square.

---

## 11. Testing strategy

Everything host-testable runs against `MockNoren` (no network, deterministic) — TDD per unit:
- `mode.py` — transition matrix, single-shot consume/revert, fail-safe default, latch/halt forces
  revert.
- `executor.py` — the full precondition chain; each gate independently blocks; only `LIVE_TEST` +
  all-pass + can_trade + claim transmits; arm-or-abort on square-arm failure; 1-lot assertion;
  fresh-dry-run re-derivation (a tampered intent can't bypass).
- `auto_square.py` — timer fires at deadline; square targets fillshares; unfilled entry cancelled;
  partial fill; square-now; kill; SL-LMT placement; re-arm on restart.
- `margin.py` — sufficient passes, insufficient blocks, unreadable limits fails closed.
- `live_friction_profile.py` — statutory components correct, brokerage 0.
- Routes — mode transitions, place gated, square exit-only; grep-confirm `place_order` reachable
  ONLY via the executor.

**The one real order is NOT automated.** The final validation is the user, in `LIVE_TEST`, placing
a single 1-lot order via the panel during market hours and watching the ≤10-min auto-square. The
assistant prepares everything and verifies the mock path exhaustively first.

---

## 12. Safety invariants (L3 exit gate must prove all)

1. **Real orders reachable ONLY in `LIVE_TEST`, single-shot.** No other mode/path transmits.
2. **≤ 1 lot, always.** Three independent layers; > 1 lot impossible.
3. **Arm-or-abort.** A filled live position is ALWAYS protected by an armed auto-square + SL-LMT,
   or it is immediately exited and the engine halts. No unprotected live position can persist.
4. **The 10-minute deadline always resolves** to a square (or a cancelled unfilled entry) while the
   backend is alive; the broker SL-LMT is the price backstop if it isn't.
5. **Margin-checked.** An order that wouldn't fit the account cash never transmits.
6. **The executor is the sole transmission chokepoint.** `place_order` is called nowhere else;
   `cancel_order`/`place_order` for exits are reached only via kill/square/auto-square (exit-only).
7. **Every L0–L2 invariant still holds** (fail-closed, idempotency, reconcile-halt, exit parity).
8. **Default-safe.** Unknown/missing mode → `PAPER`; any halt/latch → no live orders.

---

## 13. Build approach

- Branch `feat/live-execution-l3` off `main`; same multi-agent + adversarial-audit loop.
- The **executor** and **auto-square** get the hardest adversarial audits (*can it place > 1 lot?
  place when halted/latched? place outside `LIVE_TEST`? place on a stale/tampered dry-run?
  double-place? leave a filled position unprotected if arming fails? can single-shot be re-armed
  without a deliberate re-entry?*).
- An L3 exit-gate audit proves §12 end-to-end against `MockNoren`.
- Then surface to the user for the **manual live validation** (their click, 1 lot, ≤10-min square),
  which the assistant cannot and will not perform.

---

## 14. Open considerations (flag at plan time, not blockers)
- Exact statutory rates (STT/exchange/GST/SEBI/stamp) for NFO/BFO options — verify current values
  when wiring §7.
- Whether the SL-LMT backstop should be a fixed % or derived from the strategy's stop config
  (default: a conservative fixed protective % for the manual test, since there is no strategy).
- Heartbeat mechanism (a timestamp the backend bumps each engine tick; the UI flags stale).
