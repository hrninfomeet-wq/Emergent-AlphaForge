# Live Trading — Supervised Real-Money Readback Checklist

> **Purpose:** validate AlphaForge's Flattrade execution path without confusing an
> accepted order with a filled order. Current model: the v0.56.0 authorization
> (`mode == "live"`, no per-deployment ARM) plus the 2026-09-26..30 live controls;
> checked against the code 2026-09-30.
>
> **Where this sits:** the market-session runbook is
> [`LIVE_VALIDATION_PLAN_2026-08.md`](LIVE_VALIDATION_PLAN_2026-08.md) — run its
> §1 (the Live Deployments controls) and its paper phases first. This checklist is
> the supervised real-money drill that plan's live phase follows. Route and
> field reference: [`STRATEGY_DEPLOYMENTS.md`](STRATEGY_DEPLOYMENTS.md) "Live path".
>
> **Operator boundary:** only the account owner authorizes live mode, places a
> manual order, or invokes a square/kill action. An assistant may inspect read-only
> state and logs, but must never press those controls or use the Flattrade MCP to
> place, modify, or cancel orders.

## The authorization model

- A deployment is authorized only when `mode == "live"`, it is `ACTIVE`, no
  operator hold (`risk.live.paused`) is set, the broker is connected, the live
  caps (account and per-deployment) allow the entry, and the time is before
  15:00 IST. Just before transmit the executor's fence re-reads the deployment
  and re-checks status, mode, hold, the cutoff (fresh clock) and the
  per-deployment caps (`auto_live._recheck_authorization`; refusals read
  `stale_authorization:*`).
- `POST /api/deployments/{id}/live/enable` is the only transition into live mode.
  There is no per-deployment ARM record and no `LIVE_GUARD_ARMED` setting.
- Passing forward validation is the recommended evidence path. A failed,
  incomplete, or unavailable cohort requires the operator to separately accept
  the unvalidated-live warning; that evidence decision is stored under
  `risk.live.evidence_consent` and does not bypass the controls in this checklist.
- `LIVE_AUTOPLACE_ARMED=1` is the one machine-level switch for automated entries.
  Keep it `0` until the intended deployment and caps have been reviewed.
- Software guard exits always transmit for a monitored real position. The broker
  OCO is **off by default** (`LIVE_BROKER_OCO_ENABLED=0` since 2026-09-03), so
  there is **no PC-down net**: the in-process guard runs only while the app runs.
  Re-enable the OCO only after §E1.
- Stop, Stop-all, drift pause, daily-loss halt, and kill pause the deployment,
  demote it to paper and prevent re-entry. Resume returns it to paper; going live
  again requires a fresh live-enable confirmation.
- Three controls keep the deployment live: **Pause** (`/live/pause`, a hold on new
  entries; the open book stays guarded), **Tighten caps** (`/live/caps`,
  loosening refused with 409) and **Flatten** (`/live/flatten`, optionally with the
  hold). None of them re-collects consent.

## The most important close rule

**Exit submitted is not flat.** A successful order-place response only proves that
Flattrade accepted an exit instruction. AlphaForge must keep the position in the
guard registry (and any OCO intact) until authenticated broker reads confirm the
position flat. Only then may it cancel any remaining OCO and journal the realized
close. The UI says "exit submitted — awaiting fill confirmation", never
"flattened", and `/live/flatten` always returns `fill_confirmed: false`.

The Stop APIs therefore report separate states:

- `exit_submitted_tsyms`: exit order accepted; fill confirmation pending.
- `already_flat_tsyms` / `cancel_confirmed_tsyms`: fresh broker read found no
  position or confirmed the relevant cancellation; guard confirmation still owns
  finalization.
- `flat_confirmation_pending_tsyms`: every accepted close path still being watched.
- `deferred_tsyms`: another close path already owns the in-flight exit.
- `failed_tsyms`: no close instruction was accepted; investigate immediately.
- Deprecated `squared_*` aliases remain empty because submission is not proof of a
  fill.

Do not declare success until all four agree: broker position book is flat, order
book shows the exit filled (or position already absent), the OCO is gone, and the
AlphaForge blotter/journal is CLOSED with realized P&L.

## A. Before market hours — read-only checks

- [ ] Re-login through **AlphaForge's** Flattrade login, after 06:00 IST (a token
  issued earlier counts as expired). Never call the MCP login or logout;
  AlphaForge owns the only OAuth redirect and last-login-wins token.
- [ ] `GET /api/flattrade/status`: connected, token not expired, correct UID, and
  registered static IP.
- [ ] Running services match the intended local commit (rebuilt with
  `docker compose up -d --build`, never a plain restart); backend health is OK.
- [ ] Backend log: `live startup recovery: reboot reconcile ... status=ok` (or
  `status=flat_confirmed`), then `live recovery: completed`;
  `GET /api/live-broker/recovery-status` agrees. Any other status
  (`unknown_position_book`, `trade_book_unreadable`) leaves recovery INCOMPLETE and
  it retries every supervisor tick: the broker books could not be read or
  confirmed, so OPEN journal rows are unproven — resolve it before trading.
- [ ] `GET /api/live-broker/preopen-readiness` reports today's verdict (08:45 run).
- [ ] Broker positions are flat and no unexpected GTT/OCO is resting.
- [ ] `/live-trading` loads positions, orders, cash, reconcile, Greeks, guard, and
  deployment blotter without console errors.
- [ ] `LIVE_AUTOPLACE_ARMED=0` while performing read-only checks.
- [ ] No existing live deployment is ACTIVE.

## B. Risk contract for the first one-lot readback

For the ₹2,00,000 account, do not use an optimizer result as permission to trade.
The first live exercise is operational validation only:

- [ ] One liquid ATM option lot; long-only.
- [ ] `max_lots_per_day=1`, `max_concurrent=1`.
- [ ] Daily live loss cap no greater than ₹4,000; preferred first-day planned loss
  is ₹1,000–₹2,000.
- [ ] No overnight permission.
- [ ] Marketable LIMIT / SL-LIMIT only; Flattrade Pi does not support market orders
  for this path.
- [ ] Verify `GetOrderMargin` for the exact contract, product, quantity, and price.
- [ ] Keep broker terminal access available as an independent emergency control.

## C. Stage 1 — connectivity, no order

- [ ] Broker banner connected and reconcile green.
- [ ] Execution strip shows automated entries not transmitting.
- [ ] Position book, order book, GTT/OCO book, and available cash refresh.
- [ ] Greeks endpoint returns a valid zero-position payload when flat.
- [ ] Kill-switch preview contains no unexpected target.

Stop if any read is stale, contradictory, or unauthenticated.

## D. Stage 2 — manual one-lot round trip

The user performs every action in this section.

- [ ] Select one liquid ATM contract and enter a marketable LIMIT buy for one lot.
- [ ] Review the irreversible confirmation and place once.
- [ ] Wait for broker **COMPLETE/FILLED**; do not infer a fill from order acceptance.
- [ ] Confirm the same net quantity in broker positions and AlphaForge.
- [ ] Confirm the live blotter and Greeks card resolve the contract (the Greeks
  card is priced off the broker book).
- [ ] Invoke Square once. If the API says submitted, wait; do not submit a second
  sell while an exit is in flight.
- [ ] Observe any widening/re-price through the same tracked exit order path.
- [ ] Confirm broker position flat, exit fill, journal CLOSED, realized P&L, and
  Greeks back to zero.

This manual MIS path does not prove deployment auto-entry or the broker OCO.

### D1. Manual ticket — the transmission-unconfirmed branch

`LiveOrderTicket.jsx`'s transmission-unconfirmed branch (`3da45b6`) is unit-pinned
but has never met a real transmit. Why it exists: a transport failure on the
approve/redeem call (no response, timeout, dropped connection, or any 5xx) can
happen AFTER the order reached Flattrade, so treating it as "Place failed" invites a
duplicate live position. Only a structured 4xx from our backend is a genuine
refusal. During market hours, with the operator placing one lot, force a lost
response on the approve call (DevTools offline or throttling) and confirm:

- [ ] the amber **TRANSMISSION UNCONFIRMED** panel renders (not a red failure);
- [ ] Place stays disabled while the panel is shown (the preview is cleared);
- [ ] live execution mode is NOT stood down to `LIVE_OFFLINE`;
- [ ] **Refresh order book** re-reads the broker books, and placing comes back only
  through the explicit "I've checked — re-enable placing" button plus a fresh
  preview;
- [ ] the broker order book shows whether the order actually went out; reconcile
  before anything else is placed.

## E. Stage 3 — deployed one-lot path (software guard; no broker OCO by default)

The recommended path is to run this only after Stage 2 passes and a paper
candidate meets the forward-validation policy. If the user chooses the explicit
unvalidated-live override, mark the exercise **UNVALIDATED** in the run record,
record the failed checks, and use the smallest caps; positive paper P&L alone is
not evidence that this is safe or profitable.

- [ ] Review the deployment's frozen strategy hash, option policy, one-lot sizing,
  no-overnight rule, and live caps.
- [ ] User reviews forward evidence and ticks the consent checkbox. If evidence
  failed or is unavailable the checkbox instead reads "I explicitly approve
  unvalidated real-money trading" and lists the failed checks. (Before
  2026-09-08 this step also required typing `ENABLE`; the checkbox replaced it.)
- [ ] User sets `LIVE_AUTOPLACE_ARMED=1` and verifies the execution strip.
- [ ] On a signal, margin pre-check passes before order transmit.
- [ ] Entry order fills as NRML; the guard registers the actual filled quantity.
- [ ] The Live Deployments row shows cap headroom and the binding chip from the
  `governor` payload, and a `blocked: …` reason whenever it cannot trade.
- [ ] **No broker OCO is expected.** `LIVE_BROKER_OCO_ENABLED` is 0 by default, so
  `oco_al_id` stays null, the blotter shows a "no broker net" chip and the alert
  rail shows the software-guard-only banner. On the default path that is the PASS
  condition, not a failure — **there is no PC-down net during this exercise.**
- [ ] Software stop/target/time exit submits at most one tracked flatten attempt.
- [ ] After submission the guard remains present until broker-confirmed flat.
- [ ] Only after flat confirmation does the final journal completion occur.
- [ ] Compare journal `realized_pnl` with the broker's own P&L for that trade. The
  guard journals its close at the last-seen broker `lp` (an estimate), so record
  a difference as `ESTIMATED_EXIT` rather than a pass; a partial fill is still
  journaled on the ORDERED quantity by a live guard close (AGENT_TODO O1), so
  check the filled quantity too.

### E1. Re-enabling the broker OCO — the pairing readback

This is the procedure `live_deploy_context._broker_oco_enabled` points at. Do NOT
set `LIVE_BROKER_OCO_ENABLED=1` for real trading until every box here is ticked.

Background: on 2026-09-03 the OCO stop leg reached the order book one second after
the fill and was LPP-rejected. A resting GTT/OCO never reaches the order book — it
lives in `GetPendingGTTOrder` until its trigger fires. The broker's trigger note
was "Ltp 144.85 is above 50.75", i.e. the STOP leg fired on an LTP-**above**
condition that was already true at placement. `gtt.py` documents the `oivariable`
x/y -> leg mapping as UNCONFIRMED; that readback says leg1/`x` is the ABOVE slot,
so stop and target are swapped.

- [ ] With `LIVE_BROKER_OCO_ENABLED=1`, arm ONE lot and let a single entry fill.
- [ ] Immediately call `GetPendingGTTOrder` (Flattrade MCP read tools are fine) and
  capture the raw payload. Do not rely on the app's own view of it.
- [ ] In that payload, confirm the leg whose `oivariable` is the ABOVE condition
  carries the **TARGET** price, and the BELOW condition carries the **STOP**. If
  the ABOVE leg carries the stop, the pairing is still swapped — **abort, set the
  flag back to 0**, and fix `gtt.build_oco_intent` before retrying.
- [ ] Confirm the order book is EMPTY of both legs — a leg in the order book means
  it fired at placement rather than resting, which is the original defect.
- [ ] Confirm neither leg is priced through the market: a stop priced far below a
  marketable level is a guaranteed reject; a stop priced marketably would flatten
  the position immediately on acceptance.
- [ ] Only after all four hold, record the readback in this file with the date and
  the raw payload, and leave the flag on.

## F. Stop, Stop-all, or kill verification

- [ ] The deployment becomes PAUSED immediately, so no new signal can re-enter.
- [ ] Read the returned `live_exit` / `live_exit_reports`; investigate every failed
  target.
- [ ] Treat `exit_submitted_tsyms` as pending, not complete.
- [ ] Watch the guard until `flat_confirmation_pending_tsyms` resolves in the broker
  book and AlphaForge close loop.
- [ ] Confirm no orphan OCO remains.
- [ ] Resume returns paper mode; live requires a fresh authorization.
- [ ] **Flatten & hold** (`/live/flatten` with `hold`) squares only this
  deployment, keeps it `mode == "live"` + ACTIVE with the hold set, and lists any
  position it could not attribute under `unguarded_open_tsyms`. Out of market
  hours it refuses with 409 `market_closed` and sends nothing.
- [ ] Stop-all lists every live deployment in `disarmed_live_deployment_ids`; the
  kill switch lists them in `stop_all.disarmed_deployment_ids`. An empty list
  while a live deployment exists is a FAIL — stop and investigate.

If the application and broker disagree, the broker book is the exposure truth.
Use the broker terminal to manage risk, then preserve logs and reconcile—do not try
repeated blind API closes.

## G. Optional PC-down proof

Only possible with `LIVE_BROKER_OCO_ENABLED=1` after §E1 has passed; on the default
path there is no broker net to prove, so skip this stage. It intentionally exposes
one real position to its broker-resting OCO.

- [ ] With one NRML position and verified resting OCO, stop only the backend.
- [ ] Confirm the OCO remains visible at the broker while AlphaForge is offline.
- [ ] Restart the backend. Reconciliation must re-link a still-open position/OCO,
  journal the true broker fill if the OCO fired, and cancel any genuine orphan.
- [ ] Confirm the final broker position, order, OCO, blotter, and journal agree.

## H. Stand down

- [ ] Account flat; no pending or rejected close; no resting orphan OCO.
- [ ] All AlphaForge live rows CLOSED with realized P&L.
- [ ] All deployments paper/paused (live mode persists across sessions, so end a
  live day by explicitly disabling it); `LIVE_AUTOPLACE_ARMED=0`.
- [ ] Save broker order IDs, timestamps, relevant logs, observed slippage, margin,
  and any divergence in the market-validation record.

Passing this checklist validates plumbing, not profitability. Capital promotion is
governed separately by `forward-validation-policy.md`.
