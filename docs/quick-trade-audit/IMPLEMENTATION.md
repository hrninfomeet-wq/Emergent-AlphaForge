# Conditional Quick Trade — implementation checkpoint

**Latest deployment status:** [ROLLOUT.md](ROLLOUT.md) records the user-authorized local-main commit, rebuild and visible panel verification. The implementation-only snapshot below predates that deployment; behavioural/live-trading validation remains open.

The user authorized proceeding after the audit, then requested implementation only and deferred the test campaign. The design below was recorded before coding. The audit remains historical evidence, not a live certification.

## Design (one page)

Add a Conditional tab to Quick Trade, defaulting to paper. Keep the existing manual ticket in a separate tab. Flattrade remains execution; Upstox remains data. A server worker owns all conditions/exits; the browser only previews, confirms, arms and displays records.

Persist each order in Mongo with its immutable specification, arm-time reference contract/premium, deadline, previous spot tick, resolved execution contract, stable child action IDs, fill quantities/VWAP, current stop, peak and activation state, alerts and event history. Use atomic revisions and a single-worker lease; persist action intent before transmitting. Unknown acknowledgements reconcile by stable remarks/order ID and never blindly resubmit. Recovery restores the exact stored exits and reconciles working orders/positions first. No default-stop substitution.

All selected entry conditions must hold on the same evaluation. Cross mode requires previous and current fresh ticks on opposite sides; reject already-true arms. Level mode allows already-true conditions. Premium change watches the explicitly named arm-time reference contract. Resolve ATM/ITM/OTM from the actual available strike ladder at trigger; direct strike remains fixed. Expiry comes from the master. Deadlines and forced square-off use IST and enforce the session calendar.

Use metadata-provided segment, lot, tick and freeze; refuse missing/stale/ambiguous fields. Split into whole-lot children. Entry is a tick-valid marketable LIMIT with buffer min(0.5%, Band), bounded timeout and cancel/reprice. Track each cumulative fill; protect partial exposure and never treat cancellation acknowledgement as no fill. Stops/targets derive from fill VWAP. Trailing and profit-lock ratchet monotonically. A single exit winner cancels the other exit path before selling residual quantity. Broker SL-LMT is used when capability is established; otherwise display software-only protection and maintain heartbeat/failure alerts. Failed flatten remains unresolved and retryable through reconciliation.

Paper/live controls and latches are separate. Existing live Kill Switch and paper Stop ALL also latch/cancel conditional orders in their own mode. Every dispatch rechecks the appropriate latch. Live arm requires a server-issued preview confirmation with instrument/rule/lots/conditions/deadline, planned loss and full premium exposure; no test may transmit live.

## Work pieces and binary checks

1. Rules/models and metadata: done when input boundaries, cross/level/premium logic, ladder resolution, rounding and exit ratchets pass deterministic tests.
2. Critical durable engine: done when claim-before-send, partial/rejected/unknown orders, restart, OCO, expiry and mode-specific kill/failsafe tests pass.
3. Data/execution adapters: done when injected-client tests prove exact payloads, fill identity, cancellation reconciliation, no metadata fallback, and paper/live separation.
4. Panel/API/runtime: done when rendered UI/API tests cover preview/confirm/arm/status and startup/shutdown wiring is inspected and tested.
5. Review and paper runs: done when each requested index completes a simulated server lifecycle with logs and an independent review has no unresolved critical defect.

## Shared request contract

`mode` paper/live (default paper), `underlying` NIFTY/BANKNIFTY/SENSEX, `option_type` CE/PE, `expiry` YYYY-MM-DD, `strike` {kind: ATM/ITM/OTM/DIRECT, offset: nonnegative integer, price: optional}, `lots`, `band_pct`.

`spot_condition`: null or {direction: above/below, price, mode: cross/level}; `premium_condition`: null or {direction: rise/fall, value, unit: points/percent}. `valid_minutes` (default 30) or `valid_until` HH:MM. `exits`: {stop: {value,unit}, target: optional {value,unit}, trailing: optional {activation,distance,step}, profit_lock: optional {activation,profit,step,raise_by}, square_off: HH:MM}. Trailing/profit-lock amounts are premium points; UI labels this explicitly. `entry_timeout_seconds` default 15, `max_reprices` default 1, `stale_seconds` default 5, `failsafe` flatten/alert (default flatten).

## Source completion checkpoint — 2026-10-26

Confirmed: source implementation is saved. Python compileall, Babel JSX/module parsing and git diff whitespace checks passed; see `implementation-syntax-checks.txt`. These checks do not establish trading correctness. No rollout, backend restart, broker login, live-enable action or live order was performed.

### Implemented

- `backend/app/conditional/models.py`, `rules.py`: strict request validation, master-ladder strike resolution, cross/level/reference-premium predicates, IST deadlines, fill-based stop/target, monotonic trailing/profit-lock, tick rounding and freeze slicing.
- `market.py`: Upstox JSON/CSV master identity join, freshness checks, additive subscriptions and ordered received-spot history with contemporaneous premium. Overflow/gaps invalidate crossing baselines. The preview pins a reference contract; arm captures its fresh premium and the UI displays it. Execution strike resolves from the triggering spot.
- `store.py`, `engine.py`: Mongo state, optimistic revisions, stable child remarks, worker lease, separate latch generations, contract reservations, partial fills, cancel/readback, exit winner/OCO, timeout/reprice, forced exit and unresolved-outage alerts. Pending stop amendments are durable before transmission. Exit prices do not inherit the entry band's rejection boundary.
- `broker.py`: separate paper and Flattrade adapters; exact symbol/expiry/type/lot/tick lookup; ownership/position checks; no blind replay after an unknown acknowledgement. Stops/exits close actual residual units, including partial lots. Live transmission uses the existing executor module.
- `service.py`, `watchdog.py`, `routers/conditional_orders.py`: startup/shutdown, API and separate exit-only watchdog process configured in Compose. Existing live Kill Switch, paper Stop ALL and global Stop ALL include conditional orders in their respective modes. Paper cancellation failures/pending closes appear in its existing failure/skipped response.
- `ConditionalOrderPanel.jsx`, `conditionalApi.js`, `cockpit/QuickTrade.jsx`: paper-default conditional form, all entry/exit fields, master expiry/direct strikes, preview/live confirmation, events, actual reference, fill/remaining quantities, protection/health and mode-specific kill/reset. Manual ticket remains a separate tab.

### Configuration

- Flattrade executes live; Upstox supplies data/master. Existing OAuth ownership is unchanged.
- `CONDITIONAL_MAX_LOTS` defaults to 5. Live requires the existing automatic-placement switch and engine safety gates; this work does not enable them.
- `CONDITIONAL_BROKER_STOP_VERIFIED` defaults to 0. Enable only after account/product-specific SL-LMT acceptance/readback is established. Otherwise the UI prominently discloses software-only protection.
- Live requires a maximum entry premium; confirmation shows planned stop loss and full premium exposure. Neither guarantees loss including charges.
- Compose defines `conditional-watchdog`; it was not launched. Takeover waits for the primary's lease to expire (90-second lease). It never creates entries. Missing credentials, usable prices or broker evidence keep exposure unresolved.
- New collections: `conditional_orders`, `conditional_controls`, `conditional_reservations`. Initialization adds indexes/control records; there is no destructive migration.
- API root `/api/conditional-orders`: metadata, preview, arm, list/status, heartbeat, cancel, kill and reset.

### Not verified / deferred

- The requested rules/engine/restart/OCO/kill/stale-feed acceptance suite and per-index paper runs have **not been run** for this implementation. Adapter/UI test sources are authored but unexecuted. Historical audit counts are not implementation evidence.
- Browser rendering, production bundle, API/DB integration, real recovery/failover and concurrent legacy/conditional exits are **not verified**. Existing symbol exit mutexes are process-local; the conditional lease coordinates conditional primary/watchdog workers, not arbitrary simultaneous legacy/external activity. Broker capacity checks do not prove race-free account-wide coordination.
- Current live master compatibility, broker acceptance of orders/partial-lot closes/stops/amendments, rate behaviour, subscription acknowledgements, exchange calendar and session/static-IP operation are **not verified**. Entries refuse unavailable/contradictory metadata.
- Alerts are durable events, server logs and panel messages. External push notification delivery is **not implemented**; unattended operation needs an external log-alert integration.
- Entry tick history is bounded and process-local. Gaps can miss an entry rather than synthesize one. Exits evaluate current snapshots; tick-by-tick exit timing is **not verified**.
- Watchdog shares the host/database. Total host/network/database loss or unusable broker credentials can prevent flattening. SL-LMT can remain unfilled through a gap; software-only orders remain exposed to full premium loss plus charges.
- Deployment is **not done**. The running application has not been changed by this implementation pass.

### Team checkpoint

Rules, adapters and UI were bounded file-owned tasks with individual checkpoints in this directory. Primary reviewed contracts, wrote persistence/engine/API/runtime/watchdog, and requested adapter revisions for durable pending amendments and residual closes. The final UI helper hit its usage limit; primary completed those edits. Next work is isolated behavioural testing and paper runs, then a reviewed rollout. Real-money trading is not the first acceptance test.
