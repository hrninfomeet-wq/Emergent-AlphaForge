# Live Trading — Supervised Real-Money Readback Checklist

> **Purpose:** validate AlphaForge's Flattrade execution path without confusing an
> accepted order with a filled order. Current model: v0.56.1.
>
> **Operator boundary:** only the account owner authorizes live mode, places a
> manual order, or invokes a square/kill action. An assistant may inspect read-only
> state and logs, but must never press those controls or use the Flattrade MCP to
> place, modify, or cancel orders.

## The authorization model

- A deployment is authorized only when `mode == "live"`, it is `ACTIVE`, the
  broker is connected, the live caps are valid, and the time is before 15:00 IST.
- `POST /api/deployments/{id}/live/enable` is the only transition into live mode.
  There is no per-deployment ARM record and no `LIVE_GUARD_ARMED` setting.
- Passing forward validation is the recommended evidence path. A failed,
  incomplete, or unavailable cohort requires the operator to separately accept
  the unvalidated-live warning; that evidence decision is stored under
  `risk.live.evidence_consent` and does not bypass the controls in this checklist.
- `LIVE_AUTOPLACE_ARMED=1` is the one machine-level switch for automated entries.
  Keep it `0` until the intended deployment and caps have been reviewed.
- Software guard exits always transmit for a monitored real position. The resting
  broker OCO is the PC-down catastrophe backstop.
- Stop, Stop-all, drift pause, daily-loss halt, and kill pause the deployment and
  prevent re-entry. Resume returns it to paper; going live again requires a fresh
  live-enable confirmation.

## The most important close rule

**Exit submitted is not flat.** A successful order-place response only proves that
Flattrade accepted an exit instruction. AlphaForge must keep the position in the
guard registry and keep its OCO intact until authenticated broker reads confirm the
position flat. Only then may it cancel any remaining OCO and journal the realized
close.

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

- [ ] Re-login through **AlphaForge's** Flattrade login. Never call the MCP login or
  logout; AlphaForge owns the only OAuth redirect and last-login-wins token.
- [ ] `GET /api/flattrade/status`: connected, token not expired, correct UID, and
  registered static IP.
- [ ] Running services match the intended local commit; backend health is OK.
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
- [ ] Confirm the live blotter and Greeks card resolve the contract.
- [ ] Invoke Square once. If the API says submitted, wait; do not submit a second
  sell while an exit is in flight.
- [ ] Observe any widening/re-price through the same tracked exit order path.
- [ ] Confirm broker position flat, exit fill, journal CLOSED, realized P&L, and
  Greeks back to zero.

This manual MIS path does not prove deployment auto-entry or the broker OCO.

## E. Stage 3 — deployed one-lot path with OCO

The recommended path is to run this only after Stage 2 passes and a paper
candidate meets the forward-validation policy. If the user chooses the explicit
unvalidated-live override, mark the exercise **UNVALIDATED** in the run record,
record the failed checks, and use the smallest caps; positive paper P&L alone is
not evidence that this is safe or profitable.

- [ ] Review the deployment's frozen strategy hash, option policy, one-lot sizing,
  no-overnight rule, and live caps.
- [ ] User reviews forward evidence; if it failed/is unavailable, separately
  checks the explicit unvalidated real-money consent, then types `ENABLE`.
- [ ] User sets `LIVE_AUTOPLACE_ARMED=1` and verifies the execution strip.
- [ ] On a signal, margin pre-check passes before order transmit.
- [ ] Entry order fills as NRML; the guard registers the actual filled quantity.
- [ ] A two-leg resting OCO appears at Flattrade and the blotter shows its ID and
  catastrophe band. An OCO creation failure is a stop condition.
- [ ] Software stop/target/time exit submits at most one tracked flatten attempt.
- [ ] After submission, guard and OCO remain present until broker-confirmed flat.
- [ ] Only after flat confirmation does OCO cancellation/final journal completion
  occur.

## F. Stop, Stop-all, or kill verification

- [ ] The deployment becomes PAUSED immediately, so no new signal can re-enter.
- [ ] Read the returned `live_exit` / `live_exit_reports`; investigate every failed
  target.
- [ ] Treat `exit_submitted_tsyms` as pending, not complete.
- [ ] Watch the guard until `flat_confirmation_pending_tsyms` resolves in the broker
  book and AlphaForge close loop.
- [ ] Confirm no orphan OCO remains.
- [ ] Resume returns paper mode; live requires a fresh authorization.

If the application and broker disagree, the broker book is the exposure truth.
Use the broker terminal to manage risk, then preserve logs and reconcile—do not try
repeated blind API closes.

## G. Optional PC-down proof

This intentionally exposes one real position to its broker-resting OCO and should
only be attempted after the normal OCO path is proven.

- [ ] With one NRML position and verified resting OCO, stop only the backend.
- [ ] Confirm the OCO remains visible at the broker while AlphaForge is offline.
- [ ] Restart the backend. Reconciliation must re-link a still-open position/OCO,
  journal the true broker fill if the OCO fired, and cancel any genuine orphan.
- [ ] Confirm the final broker position, order, OCO, blotter, and journal agree.

## H. Stand down

- [ ] Account flat; no pending or rejected close; no resting orphan OCO.
- [ ] All AlphaForge live rows CLOSED with realized P&L.
- [ ] All deployments paper/paused; `LIVE_AUTOPLACE_ARMED=0`.
- [ ] Save broker order IDs, timestamps, relevant logs, observed slippage, margin,
  and any divergence in the market-validation record.

Passing this checklist validates plumbing, not profitability. Capital promotion is
governed separately by `forward-validation-policy.md`.
