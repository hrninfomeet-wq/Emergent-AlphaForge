# Quick Trade audit checkpoint — 2026-10-25

**Confirmed: Quick Trade does not meet the requested unattended live-trading safety contract. Part B is stopped at the user's Sev-1 checkpoint.** The decisive evidence is loss of the original exit plan on recovery, reproduced using the actual guard. No application fixes, live orders, mode changes, database mutations, rebuilds or restarts were performed in this audit.

## 1. Audit report

These findings concern the current working tree, including pre-existing uncommitted changes. Running-image parity is **not verified**. “Confirmed” means the named source or executable check was inspected/run in this session; it does not mean exchange acceptance.

| ID / severity | Finding | File / line | Evidence | Required fix / disposition |
|---|---|---|---|---|
| A1 / **Sev-1 — critical** | Confirmed: recovery discards the exit plan and ratchet, replacing them with a stop based on the recovery mark. | `backend/app/live/live_position_guard.py:1909`, `:1934`; `backend/app/live/session_store.py:75` | [Recovery log](recovery-reproduction.log): locked stop 105 becomes 50, target 120 becomes absent, lock becomes none. At premium 100 the original plan exits; recovered plan makes zero exit calls. Same result for all three synthetic index identities. | Durable plan, order-specific fills, peak/activation/ratchet and working-exit state; restore and reconcile before operation. **Invalidates extending the existing guard unchanged.** |
| A2 / **Sev-1** | Confirmed: manual orders have no broker-held protective stop. Authenticated broker loss leaves the monitor unable to exit; backend loss stops its code entirely. | `backend/app/routers/live_broker.py:356-394`; `backend/app/live/live_position_guard.py:743-748` | Manual simulation has one entry and `sl_norenordno=None`. Expired-session reproduction retains a position, reports reconnect, submits zero exits. Repository supervisors share the backend process. | Capability-tested resting protection when possible; independent heartbeat/alerts otherwise, with best-effort flatten and explicit unresolved exposure. A watchdog cannot guarantee an authenticated order through expired credentials. |
| A3 / **Sev-1** | Confirmed: fixed stop/target are calculated from editable reference premium, not entry fill. | `backend/app/routers/live_broker.py:367-381`; `backend/app/live/live_position_guard.py:821-851` | Each index simulation: reference 200, fill 201, stop 10%, target 20% leaves stop 180 and target 240; fill-based values are 180.90 and 241.20. Guard changes trailing entry basis but preserves these fixed levels. | Initialize from order-specific fill VWAP/filled quantity, with monotonic updates for partial fills. Never substitute aggregate day-buy average for an order's fills. |
| A4 / **Sev-2** | Confirmed: Quick Trade is a supervised real-order ticket, not a paper ticket. | `frontend/src/components/live/cockpit/QuickTrade.jsx:24`; `frontend/src/components/live/LiveOrderTicket.jsx:383`; `backend/app/live/mode.py:67` | PAPER executor calls return `mode_not_live_test` for all three indices. UI confirmation explicitly selects LIVE_TEST. | Separate paper execution adapter and default paper UI for the future feature. No running-app paper E2E is claimed. |
| A5 / **Sev-2** | Confirmed: preview offers instructions the manual execution path refuses: multiple lots, NRML and SL-LMT. | `backend/app/live/manual_ticket.py:9-24`; `tests/test_manual_ticket_contract.py` | Existing capability/drift tests pass; manual execution is one-lot BUY/MIS/LIMIT only. Unsupported instructions cannot redeem an approval. | Align controls with executable capabilities, or implement a separately verified path. Existing fail-closed block is appropriate. |
| A6 / **Sev-2** | Confirmed: BUY SL-LMT preview uses a protective-stop calculation below the reference. | `backend/app/live/order_builder.py:587-606` | Reference 200, stop 50% produces trigger 100 and limit 100.05; approval is denied. | Remove that entry preview or provide separate buy-stop entry semantics. Do not enable it as-is. |
| A7 / **Sev-2** | Confirmed: exact band boundary can falsely reject valid arithmetic. | `backend/app/live/safety.py:96-97` | Builder at reference 200 / band 0.2% rejects 200.40 from floating-point comparison. | Decimal/tick boundary comparison; leave unchanged during this read-only audit. |
| A8 / **Sev-2** | Confirmed: stop percentages at/above 100 are accepted and floored to 0.05 premium; blank/zero normalizes to 50%. | `frontend/src/components/live/LiveOrderTicket.jsx:260-265`; `backend/app/execution_policy.py:45-84` | Monitor executions at stop 100% and 150% both yield 0.05. UI confirmation test verifies normalized values are disclosed. | Explicit bounded input validation; retain full-premium risk disclosure. |
| A9 / **Sev-2** | Confirmed: instrument metadata is only partly authoritative. Broker lot is resolved, but freeze/segment/strike-step rules remain constants; bad/missing tick can fall back to 0.05. | `backend/app/live/flattrade_symbol.py:90-105`, `:265-289`, `:444-474`; `backend/app/instruments.py:29-31`; `backend/app/upstox_client.py:683-697` | Source trace and public master sample. Public values agree with freeze constants today; normalization drops tick/freeze. | Preserve versioned master metadata, validate units/freshness, reject missing metadata; derive available strike spacing and split from authoritative freeze. |
| A10 / **Sev-2** | Confirmed by source: guard-close journal can use ordered quantity rather than actual closed quantity. | `backend/app/runtime.py:397-403`; `backend/app/live/close_loop.py:83-84` | Caller omits quantity; callee falls back to journal quantity. Full partial-fill accounting not exercised here. | Fill/exit reconciliation before using journal P&L for conditional risk limits. |
| A11 / **Sev-3** | Confirmed: “1-click” badge does not describe the current confirmation flow. | `frontend/src/components/live/cockpit/QuickTrade.jsx:16` | Ticket requires preview, approval preparation, final confirmation. | Rename to supervised live entry. |

**Complete field dictionary:** [field-trace.md](field-trace.md) documents every editable field and ATM action: label, default, units, input normalization/validation, sent payload, effective broker/guard field, and source lines. Hidden cap/buffer and derived displays are included. There are no conditional-entry, trailing, profit-lock, time-window or forced-square-time controls in the current panel.

**Band % explained.** Confirmed against the builder and simulation: it is a permitted deviation from the entered **option-premium reference**, not an underlying-price condition and not a percent automatically added in full. BUY limit starts at `reference × (1 + min(0.5, band)/100)`, rounds to a marketable tick, then undergoes a band check. Reference 200 with Band 3 produces 201 because `200 × 1.005 = 201`, not 206. Band zero produces 200 and may not fill. Default UI band is 3%; blank/invalid input becomes 5%. It neither guarantees a fill nor bounds deviation from an independently refreshed quote. No reference timestamp is bound to the placement.

**Stop % explained.** Confirmed: it uses option premium, not NIFTY/SENSEX spot points. A 10% stop on reference 200 starts at `200 × 0.90 = 180`. With actual fill 201, the requested fill-based calculation is `201 × 0.90 = 180.90`; current code retains 180. Target 20% similarly remains `200 × 1.20 = 240` instead of `201 × 1.20 = 241.20`.

### Per-instrument verdict and broker roles

Confirmed: Flattrade places orders; Upstox supplies primary market data and option-universe data. Flattrade position-book LTP is the guard fallback when Upstox ticks are unavailable/stale. [Broker rules and primary-source links](broker-rules.md) contain the complete API/rate/IP/session audit.

| Index | Data segment → execution segment | Lot / freeze observed in public Upstox master | Tick evidence | Stop verdict |
|---|---|---|---|---|
| NIFTY 50 | `NSE_FO` → `NFO`, symbol family `NIFTY` | 65 / 3510 units | Raw Upstox `tick_size=5.0`; execution reads Flattrade `ti`. Current Flattrade tick **not verified**. | Confirmed: simulated software stop requests a SELL LMT. No broker stop rests on manual path. Not safe as restart-proof unattended protection. |
| BANK NIFTY | `NSE_FO` → `NFO`, symbol family `BANKNIFTY` | 30 / 1440 units | Same limitation | Same verdict, separately executed with BANKNIFTY fixture. |
| SENSEX | `BSE_FO` → `BFO`, Flattrade symbol family `BSXOPT` | 20 / 1000 units | Same limitation | Same verdict, separately executed with BFO fixture. |

The [public master evidence](upstox-public-master-sample.json) was fetched this session; it is not a current authenticated Flattrade contract check. Raw Upstox tick units were preserved, not silently converted to rupees. Synthetic simulation tick is 0.05; this is a fixture, not a live metadata claim. Expiry/type/strike are matched against SearchScrip identity; tokens alone are not contract identity. Current UI expiry is a typed date, not a master-driven selector.

Confirmed from official sources: Flattrade API v2 allows LMT/SL-LMT, prohibits market/SL-M and CO/BO, requires registered static IP for order writes and daily OAuth. Order budget is 10/second and 40/minute; general API budget 40/second and 200/minute. Upstox notices disallow SL-M for NSE options and BSE instruments despite generic schema enums. Upstox regular order budget is 10/second, 500/minute and 2000/30 minutes; registered algorithms get 50/second with the same longer windows. Standard read budgets are 50/second, 500/minute, 2000/30 minutes. Standard Upstox access expires at the next 03:30 daily boundary. Upstox execution now requires static IP/market protection, but this app does not route orders there. See the linked official sources in [broker-rules.md](broker-rules.md).

I don't know whether this account currently accepts and retains a protective SL-LMT for each requested contract. To find out: obtain broker confirmation of product/margin support and later perform an operator-authorized minimum-size market-session placement with trigger-pending readback and cancellation/fill reconciliation. Meanwhile: Confirmed: the existing manual path deliberately places no such protective order (checked `_make_arm` and mock broker order books). No live order was placed for this audit.

### Failure and control coverage

Confirmed: browser close does not own the backend guard. Backend/process loss stops it; recovery then loses original settings (A1). Feed loss falls back to broker LTP, rather than implementing a configurable stale-feed flatten. Broker-session expiry produces an explicit error but no exit. A stop-limit can remain unfilled after a gap; stop-based planned loss must not be presented as guaranteed maximum loss. Full premium exposure plus costs remains relevant for a long option.

Confirmed: the manual path has mode/single-shot, margin, symbol, band, one-lot, live halt/latch and atomic intent gates. Unknown placement acknowledgement retains an indeterminate claim and halts. Guard pending cancellation/repricing and fill-race tests exist. These primitives do not provide a durable conditional-order lifecycle.

Confirmed by source: `LiveEngine.can_trade` checks live halt/latch, not session calendar. Manual ticket validation/executor have no dedicated market-hours check; actual out-of-hours broker behavior is **not verified**. Future conditional arming/triggering needs explicit exchange-calendar gating and timeout/reprice rules. Current manual maximum executable size is one lot; preview freeze splitting is not evidence of executable multi-lot conditional orders.

Confirmed: live Kill Switch trips the persisted live latch and halts entries before broker reads; paper Stop ALL uses a paper-only route with no live latch mutation. There is no conditional engine or separate conditional-paper latch to verify. Existing pending approvals are in-memory with a TTL, not persisted armed conditions; a new process loses them. No claim is made that the current kill switch cancels nonexistent conditional orders or that current manual approval records provide their lifecycle.

## 2. Design checkpoint — implementation withheld

Part B is **not done**, as required by the user's instruction to stop when a Sev-1 invalidates the design. The original goal remains viable, but not by extending this guard unchanged. No complete Part B implementation design or schema is approved by this audit.

Required foundation for a resumed design: durable conditional state with a unique intent key, atomic arm/trigger claims, broker acceptance/fill reconciliation before retry, and durable exit state restored without loosening. Protection must be established for every filled slice and survive browser close. External supervision must expose failed flatten attempts when the engine, feed or broker session fails; it cannot promise execution without working credentials. Paper and live state/latches need separate adapters and persistence. Fresh contract metadata, calendar/rate budgets and ownership are prerequisites to dispatch.

Two specification details need resolution in that design: an ATM premium reference sampled at arm time identifies a particular contract, while trigger-time ATM may identify a different contract; both identities must be explicit rather than silently comparing different premiums. Also, “max loss” must distinguish planned stop loss from full premium exposure because neither app exits nor SL-LMT guarantee execution. No question or permission request is pending at this stop checkpoint.

## 3. Code and test evidence

Application code fixes: **none**. A1–A3 remain open. The stop instruction prevents layering Part B over them; resolving them requires a coherent persistence/fill/protection lifecycle, not a cosmetic panel patch. Existing unrelated work was preserved.

New audit-only executable files:

- [Manual simulation test](../../tests/test_quick_trade_audit.py): actual manual executor → actual router arm closure → guard tick pass → actual exit builder, all against an injected MockNoren and in-memory stores. Socket connections are forbidden during task execution. **9 passed**; [full log](manual-simulation.log).
- [Recovery/outage test](../../tests/test_quick_trade_recovery_audit.py): actual recovery/guard methods, new registry and synthetic broker snapshots. **4 passed**; [full log](recovery-reproduction.log).
- Selected existing backend tests: **320 passed**; [log](regression.log). Covers builder/approval, executor/lost ACK, stop/target/trailing/profit-lock decider, guard/ticks, reboot, kill/latch and paper-only stop cases. This does not certify unimplemented conditional rules.
- Existing rendered React ticket/capability tests: **6 passed**; [log and exact invocation](frontend-tests.log). Initial plain invocation failed on the `@/` Jest alias; documented `moduleNameMapper` resolved it without source edits. These are DOM tests, not a running browser session.

| Synthetic per-index run | Entry | Tick result | Exit evidence |
|---|---|---|---|
| NIFTY / NFO | limit 201, injected fill 200, fixture quantity 65 | 190: no exit; 180: one sell | SELL LMT 178.20 submitted; injected fill 180; registry removed only after flat confirmations |
| BANKNIFTY / NFO | same prices, fixture quantity 30 | same sequence, independently executed | same lifecycle |
| SENSEX / BFO | same prices, fixture quantity 20 | same sequence, independently executed | same lifecycle |

These are **not** live fills, UI-to-server E2E runs, or the app's paper trading mode. Simulated exit fills and flat books are injected observations. Tests intentionally reproduce current defects; green here means the audit observation is reproducible, not that the defect is fixed.

## 4. Final status, gaps and risk

Delivered: audit tables, all-field dictionary, Band explanation, per-instrument verdict, official API rules, independent safety review, executable simulations and checkpoint artifacts.

**Not done:** conditional UI/engine, cross/level/premium AND triggers, expiry, trigger-time strike selection, durable exactly-once recovery, broker OCO lifecycle, conditional paper/live latch integration, configurable watchdog, complete conditional test matrix and per-instrument app-paper E2E. Reason: the explicit Sev-1 stop checkpoint, plus no executable paper path in current Quick Trade.

**Not verified:** current running UI/source parity; authenticated Flattrade master lot/tick/freeze and actual protective-stop acceptance/margin; live SL-LMT trigger/fill, LPP, partial fill and cancel race behavior; actual process/database crash; external watchdog infrastructure outside the repo; production market-hours behavior; rate-limit enforcement and registered IP/session status on the live account. These were not inferred from mocks or historical notes.

**Critical risk:** treating registration after restart as restoration of protection would permit a substantially weaker stop and missing target. If the synthetic recovery reproductions fail to represent a separate production recovery override, that override must be located and executed before changing this verdict; the reviewed production method currently rebuilds the default directly. Independent checks were source-to-store tracing and before/after executable stop comparison. No contrary restoration path was found.

Core lesson: test recovery against the exact pre-crash trading promise, not merely a nonempty guard registry. Confirmed approach: mock broker boundary with actual executor/guard methods and explicit injected fills. Dead ends avoided: interpreting generic order enums as segment permissions, using public master values as authenticated execution proof, claiming paper E2E from a mocked LIVE_TEST, or treating passing defect-characterization tests as fixes.
