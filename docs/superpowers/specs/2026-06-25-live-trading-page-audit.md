# Live Trading Subsystem — Architecture & Refactoring Report

**Scope:** `frontend/src/pages/LiveTrading.jsx` + `frontend/src/components/live/*` + `backend/app/routers/{live_broker,deployments}.py` + `backend/app/live/*`, and the cross-page coupling with Live Signals, Paper Trading, and Signal Journal.
**Posture:** Real money. Nothing transmits today only because two env gates (`LIVE_AUTOPLACE_ARMED`, `LIVE_GUARD_ARMED`) default off — every critical finding below is load-bearing the instant either flips on.

---

## 1. System-as-built

The route `/live-trading` is a 3-line shell (`LiveTrading.jsx:13-15`) that renders one component, `LiveDashboard.jsx` — the true composition root. `LiveDashboard` owns all page state (`:333-348`) and a single 15s poll loop (`POLL_MS=15_000`, `:54`; `fetchAll`, `:353-367`) that fan-fires **8 endpoints in parallel**, each `.catch(()=>null)`: flattrade status, limits, positions, orders, reconcile, guard-status, mode, and the full deployments list. It renders a real-money banner (`LiveBanner`), a per-deployment arm/disarm/stop strip (`LiveDeploymentStrip`), a 6-tile hero metric strip, a two-column working grid (manual `LiveOrderTicket` left; `PositionMonitor` + positions/orders blotters + `GuardPanel` right), and a config row (`OverallSettingsPanel` + `GttBook`). Of 17 components in `live/`, **10 are mounted and 7 are dead** (`AccountStrip`, `LiveOrderPanel`, `OrderTicket`, `ModeSwitch`, `ApprovalQueue`, `LiveTestPanel`; `PayoffChart` is *live*, rendered by `LiveOrderTicket.jsx:5,709` — a MAP-1 error corrected by A3/F2).

Polling is fragmented. Beyond the 15s loop, four children self-poll on copy-pasted boilerplate: `PositionMonitor` (3s, `/live-broker/test-session`), `GuardPanel` (3s, `/live-broker/guard-status` — duplicating the 15s hero-tile poll of the same endpoint), `GttBook` (6s), and `LiveDeploymentStrip` (10s, **one request per deployment**, no batching/abort). The manual order path is a "one-click" flow where `LiveOrderTicket.handlePlaceConfirmed` (`:313-347`) does three sequential calls — `setLiveMode("LIVE_TEST",true)` → `createOrderApproval` → `approveOrder` — folding the global safety-mode flip into a per-order button with no client-side revert on partial failure.

On the backend, `live_broker.py` is a 2132-line god-router holding 34 routes plus six in-process store singletons. The order chokepoint is hidden in-process: route #16 `approve` calls `live_order_place` (#21) at `:1087`. State splits into two persistence classes: **Mongo-persisted** (mode, sessions, intents, safety-config latch, overall settings, token, deployment `risk.live`) and **in-memory-only, lost on restart** (the `LiveEngine.halted` flag, the `LiveMonitorRegistry` software-exit guard, the approval queue). The guard registry has **no rehydration on startup** — the central orphan risk.

Cross-page, all four pages are lenses on one `strategy_deployments` spine. The evaluator tees each clean signal to **auto_live** (writes `live_trades`) *or* auto_paper (writes `paper_trades`) via if/elif. The paper sink is fully instrumented end-to-end across three pages; the **live sink is write-mostly** — `live_trades` is read only by `/live/status` for today-counters, never as a blotter or P&L, and `/live-broker/reconcile` diffs the broker against **empty internal lists** (`:697-698`), so the ReconcileChip can never detect a real divergence. The Live page renders broker truth (`deriveDayPnl(positions)`) with no deployment/signal attribution, and the Signal Journal's enriched join is paper-only — a signal that fired a *real* order looks like a no-op.

---

## 2. Clean architecture breakdown (target vs today)

**Target layers (backend):**
- **Transport/auth router** — OAuth + token lifecycle only.
- **Broker-read router** — positions/orders/limits/trades, behind a cached client + token.
- **Order-execution service** — the chokepoint (arm→create→approve→place→revert) as one atomic, testable service, *not* an in-process HTTP call inside another route.
- **Config/store layer** — `live/registry.py` owning all singletons; mode, safety-config, overall-settings, sessions, idempotency, approvals each a store with a clear persistence contract.
- **Guard/reconcile daemon** — startup rehydration → loop, with `resume_pending`/`guardrail_tick`/`reconcile_tick` actually wired.

**Today:** all of the above live in one 2132-line file; singletons are constructed inline; the engine is always built with a `None` client in prod (`:131`, `:200`); crash-recovery methods exist but have **no caller** anywhere outside `engine.py` + tests.

**Target state model (frontend):** one `useLiveData` store/context owning a single consolidated poll, exposing `{status, mode, guard, positions, orders, deployments, armState}` as memoized derived values; children are pure presentational consumers. One shared `usePoll(fn, ms, {abort})` hook. One **derived arm-state** object computed from the 5 inputs (mode, `risk.live.armed`, autoplace env, guard env, token) with a single `would_transmit_entry/exit` verdict.

**Today:** state is split across the dashboard's 8 `setState`s (each promise resolution is its own commit → ~8 re-renders/tick), four self-polling children, derived values recomputed unmemoized in the render body (`:413-434`), and "are we armed?" smeared across the Mode tile, Guard tile, banner pill, and a **hardcoded "L3 enabled" chip that lies** (`LiveBanner.jsx:73-75`).

**Target FE↔BE contract:** consolidated read endpoint(s) (one `/live-broker/snapshot`, one batched `/deployments/live/status?ids=`); a real `/live-broker/arm-state`; reconcile fed real internal lists; a deployment-attributed live blotter endpoint. **Today:** N+8 independent polls, no batching, reconcile-vs-empty, no live blotter.

---

## 3. Critical problem areas (ranked)

| # | Title | Severity | Evidence (file:line) | One-line impact |
|---|---|---|---|---|
| 1 | Guard registry in-memory, no rehydration | **CRITICAL** | `live_position_guard.py:574-582`; `deployments.py:862-879` | Backend/PC restart silently un-watches every real position (SL/target/EOD stop firing) while UI still shows "armed". |
| 2 | Single-shot consumed *post-fill* | **CRITICAL** | `executor.py:145,319`; `mode.py` | Crash between fill and consume → restart can authorize a **second real entry** while the first is live. |
| 3 | Idempotency unique index never created in prod | **CRITICAL** | `idempotency.py:99-109`; `db.py:38-79`; `server.py:61` | The documented "REAL guard" against dup orders is absent → concurrent submits can both reach the broker. |
| 4 | Crash-recovery (`resume_pending`/`guardrail_tick`/`reconcile_tick`) built but never wired | **CRITICAL** | `engine.py:172,203,264` (no caller) | A SUBMITTING-but-unACKed intent is never adopted on restart → stranded or duplicate-resubmitted. |
| 5 | Mode-flip folded inside the Place button, no client revert | **CRITICAL** | `LiveOrderTicket.jsx:313-347` | If approve throws after arming LIVE_TEST, system stays armed with no UI to stand down. |
| 6 | `live_trades` write-mostly; reconcile-vs-empty | **HIGH** | `auto_live.py:473`; `deployments.py:863`; `live_broker.py:697-698` | No live blotter/P&L/attribution; ReconcileChip can never detect a real divergence. |
| 7 | No UI to revert mode / clear SL latch | **HIGH** | `live_broker.py:1321,1338` (no wrapper); `ModeSwitch` dead | Operator can arm but can't disarm or clear a tripped daily-loss block from the UI. |
| 8 | Arm-state fragmented across 5 inputs; static "L3" chip lies | **HIGH** | `LiveBanner.jsx:73-75`; `executor.py:328-337`; `runtime.py:96` | Operator cannot reliably answer "will a signal place a real order right now?" |
| 9 | Journal blind to live fills; no signal→live handoff | **HIGH** | `auto_live.py:494-498`; `/signals/enriched` paper-only | Real-money signals appear as no-ops in the record-of-record; Live page has zero cross-links. |
| 10 | `live/status` unindexed all-time `live_trades` scan × N deployments / 10s | **HIGH** | `deployments.py:863`; `db.py:38-79` (no index); `LiveDeploymentStrip.jsx:167-181` | O(N × history) collection scans/min — steepest scaling curve, self-DDoS as deployments/history grow. |

Runners-up (Medium): `live_broker.py` god-router (M1); read-modify-write of whole `risk` dict → lost-update (`deployments.py:787-792`); engine built with `None` client (`:131,200`); two disjoint "live position" sources with no cross-check; SELL previews but place is BUY-only with no warning (`live_broker.py:1067-1071`); **zero frontend tests** (A3/F13).

---

## 4. Refactoring strategy (phased)

**P0 — Quick wins / safety unblockers (S, high impact, low risk).** Do these *before* either env gate flips on.
- Add `idempotency.ensure_indexes(db.live_orders)` to startup (#3). **S / Critical.**
- Wire `resume_pending()` at startup + `guardrail_tick()`/`reconcile_tick()` on a loop (#4). **S / Critical.**
- Add `db.live_trades.create_index([("deployment_id",1),("created_at",-1)])` + date-bound the `/live/status` query (#10). **S / High.**
- Data-bind the `LiveBanner` "L3" chip to real `mode`; remove the redundant mount-effect mode fetch and the 15s hero guard poll (#8, perf F4). **S / High.**
- Delete the 6 dead components — but **port the SELL BUY-only warning out of `ApprovalQueue` into `LiveOrderTicket` first**; do **not** touch `PayoffChart` (A3/F1, F2). **S / Medium.**
- Add a `try/finally` mode-revert in `handlePlaceConfirmed` (#5 partial mitigation). **S / High.**

**P1 — Structural (M, high impact).**
- **Guard registry rehydration** on startup from `live_test_sessions` + `risk.live` + broker position book, before the guard loop starts (#1). **M / Critical.**
- Move single-shot consume *before* transmit, or fold into the same atomic claim as `claim_for_submit` (#2). **M / Critical.**
- Make place atomic server-side (arm+create+approve+`finally` revert in one endpoint) (#5). **M / Critical.**
- Consolidate FE polling: one `useLiveData` context + `usePoll` hook with AbortController; batch `/deployments/live/status?ids=`; memoize derived values (#10 fan-out, perf F2/F3/F5). **M / High.**
- Add `GET /live-broker/arm-state` + a single "Execution State" verdict strip; add mode-revert + reset-latch UI controls (#7, #8). **M / High.**
- Build the deployment-attributed **Live Blotter + reconcile** (feed real internal lists into `reconcile()`) (#6). **M / High.**

**P2 — Deep (L, structural debt).**
- Split `live_broker.py` into auth / broker-read / order-exec / config-store routers; move singletons to `live/registry.py`; extract the chokepoint into a service (M1). **L / Medium.**
- Field-scope all `risk.live` writes; gate paper controls from touching `risk.live`; consider isolating live arming into its own doc/collection (M2/M6). **L / Medium.**
- Unified "Live Exposure" panel merging broker book + guard + session + deployment attribution keyed by `tsym` (A4 #7). **L / High.**
- Stand up the frontend test runner; cover the place flow, the armed/unarmed partition, `deriveDayPnl/deriveCash`, OAuth redirect (A3/F13). **M / High.**

---

## 5. Improved design + logic (worst offenders)

**Premium / contract resolution.** Normalize the field-name asymmetry: `/atm-suggest` returns `premium_source`, `/option-premium` returns `source` (`LiveOrderTicket.jsx:186` vs `:226`) — pick one (`premium_source`) across both endpoints. Extract a single `useOptionPremium({underlying,strike,expiry,side})` hook returning `{premium, source, contractFound}` so the live ticket and any future surface share one code path (the logic is currently near-verbatim duplicated in dead `OrderTicket.jsx`). Fix `PayoffChart` BE/maxLoss to derive per-side (the short branch is a copy-paste identical to long, `PayoffChart.jsx:58-60`).

**Place/arm flow unification.** Replace the 3-call client chain with one server endpoint `POST /live-broker/order/place-atomic` that internally arms, creates the approval, redeems the token, places, and **always reverts mode in a `finally`**. The client makes one call and renders the outcome; the global safety mode is never left dangling by a network failure between steps. Surface the SELL constraint client-side (disable Sell or inline note) to match `live_broker.py:1067-1071`.

**Polling → consolidated poll.** Introduce `usePoll(fn, ms, {enabled, abortable})` and a `LiveDataProvider` running **one** poll cadence. Guard/mode/deployments fetched once and fanned out via context; `GuardPanel`/`PositionMonitor` polls gate behind "is there anything to monitor" (back off when last response empty). Batch per-deployment status into `GET /deployments/live/status?ids=` (one request, one `setState`). This removes the duplicate guard poll, the duplicate mode fetch, the O(N) fan-out, and the ~8-commits-per-tick re-render storm.

**State model.** Replace the dashboard's 8 `useState`s with one reducer-backed store. Compute a single **arm-state** object server-side (`/live-broker/arm-state` → `{mode, risk_live_armed, autoplace_armed, guard_armed, token_ok, would_transmit_entry, would_transmit_exit}`) and render it as one unambiguous verdict line (`ARMED · ENTRIES TRANSMIT · GUARD TRANSMITS` vs `DRY-RUN · NO REAL ORDERS`). Extract one `_env_armed(name)` backend helper to replace the 5 duplicated parser copies (`executor.py:337`, `runtime.py:96`, `deployments.py:83,88`, `live_broker.py:1280`).

**Splitting `live_broker.py`.** Five routers + `live/registry.py` for singletons + an `OrderExecutionService` so the chokepoint is a named, unit-tested function rather than an in-process HTTP handler call (`:1087`). Build the engine with the real client (or remove the dead client field) and derive `halted` from the persisted latch explicitly rather than relying on the coincidence that the Mongo latch happens to re-block after restart.

---

## 6. Hidden edge cases

- **Restart mid-guard:** real positions open, registry empty, `risk.live.armed=true` in Mongo → UI shows "armed, nothing open." *Desired:* startup rehydration + a red "UNGUARDED POSITION — guard not watching N broker position(s)" banner cross-checking broker `positions` (netqty≠0) against `guard.guarded[]`.
- **Crash between fill and single-shot consume:** *desired:* claim/consume before transmit so a restart can never re-authorize a second entry.
- **Token expiry mid-session with open armed positions:** today only a passive amber chip flips on the next 15s poll. *Desired:* expiry countdown (reuse `TokenCountdown`), amber ~30min out, red when expired-with-open-positions; broker reads/guard-squares failing should raise a loud alert, not silently stale tiles.
- **Broker outage:** each `fetchAll` call `.catch(()=>null)` silently renders stale/empty. *Desired:* track `lastSuccessfulFetch`; show "DATA STALE — last good Xs ago" overlay after ~2 missed cycles.
- **Partial fill across freeze-split children:** some children filled, some rejected, no reconciliation to "you hold X of intended Y." *Desired:* post-place outcome drawer tracking each child by `norenordno`, flagging qty divergence.
- **Armed after 15:00 / EOD square when guard isn't running:** *desired:* market-session indicator + explicit "EOD auto-square at 15:00 IST (guard active ✓/✗)" tied to guard health.
- **SELL dead-end:** previews fine, rejected only after the real-money confirm. *Desired:* disable/warn pre-preview.
- **"Stop ALL live" blast radius:** the button (correctly) flattens live *and* squares paper *and* pauses all ACTIVE deployments via `stopAllPaper` (`deployments.py:656-674`). *Desired:* rename wrapper `stopAllDeployments`, label "Stop & flatten ALL (live + paper)."

---

## 7. World-class UX features (professional cockpit)

- **Unified Live Exposure + Blotter panel** *(why: closes the #1+#6+#7 blind spot in one surface)* — merges broker book + guard registry + `live_trades` attribution + LIVE_TEST session, keyed by `tsym`, with a "source" column (manual / deployment-N / unattributed) and an "UNGUARDED" flag. Plugs into `LiveDashboard` right column, replacing the disjoint `PositionsBlotter` + `PositionMonitor` + guard views.
- **Single Execution-State verdict strip** *(why: kills the most dangerous ambiguity)* — one line from `/live-broker/arm-state`. Plugs in below `LiveBanner`.
- **Guard Health banner** *(why: makes restart-orphan visible)* — guard uptime + broker-vs-guard reconciliation alert. Plugs into the hero strip.
- **Safety/Risk Halt card** *(why: latch currently has no UI)* — latch state + `halt_reason` + typed-confirm "Reset latch" wiring `reset-latch`. Plugs into the config row.
- **Mode control with revert** *(why: no stand-down today)* — "Revert to safe mode" on the Mode tile, enabled when `mode !== "PAPER"`.
- **Token-expiry countdown + stale-data overlay** *(why: silent failure during market hours)*.
- **Post-place order-outcome drawer** *(why: partial fills invisible)* — per-child fill/reject tracking.
- **Live equity curve + per-deployment lifetime P&L** *(why: paper has it, live doesn't)* — from the new live blotter, mirroring `/paper/analytics`.

---

## 8. Cross-page integration blueprint

The four pages already share the `strategy_deployments` spine; the gap is the **live sink** being a second-class citizen. Target:
- **Shared positions/blotter model.** Define one `LiveExposure` shape (broker position + guard state + `live_trades` attribution) and one `Blotter` component parameterized by sink (paper vs live), reused on Paper and Live. The paper blotter redesign (per MEMORY) is the template; live reuses its columns + detail-drawer.
- **Signal→live handoff.** Extend `/signals/enriched` to also join `live_trades` (it stamps `live_trade_id` already, `auto_live.py:494-498`) and render a "LIVE" trade lane in `SignalJournal` so auto-live signals stop looking empty.
- **Bidirectional cross-links.** Live page armed-deployment rows → `/journal?deployment=` and `/paper?deployment=` (Live Signals already links *out*; Live Trading links *nowhere*).
- **One deployment-data source.** A shared `useDeployments` hook/context so Live (15s), Paper, and Journal stop independently re-pulling `/deployments?limit=200`; move the ARCHIVED filter server-side.
- **Unified control semantics.** Rename `stopAllPaper` → `stopAllDeployments`; field-scope `risk.live` writes so paper-side pause/resume and live-side arm/disarm can't clobber each other.

---

## 9. Prioritized roadmap (pick-able)

| ID | Title | Phase | Effort | Impact | Risk |
|---|---|---|---|---|---|
| **R1** | Create `live_orders` unique index at startup | P0 | S | Critical (dup-order guard) | Low |
| **R2** | Wire `resume_pending` (startup) + `guardrail_tick`/`reconcile_tick` (loop) | P0 | S | Critical | Low |
| **R3** | Index `live_trades` + date-bound `/live/status` query | P0 | S | High (perf) | Low |
| **R4** | Single-shot consume *before* transmit | P1 | M | Critical | Med (touches chokepoint) |
| **R5** | Guard registry rehydration on startup + Guard Health banner | P1 | M | Critical | Med |
| **R6** | Atomic server-side place (arm+create+approve+`finally` revert) | P1 | M | Critical | Med |
| **R7** | `try/finally` client revert in `handlePlaceConfirmed` (interim before R6) | P0 | S | High | Low |
| **R8** | Data-bind "L3" chip; remove dup guard/mode polls | P0 | S | High | Low |
| **R9** | Delete 6 dead components (port SELL warning first; keep PayoffChart) | P0 | S | Medium (clarity) | Low |
| **R10** | `/live-broker/arm-state` + Execution-State verdict strip | P1 | M | High | Low |
| **R11** | Mode-revert control + Safety/Risk Halt card (reset-latch UI) | P1 | M | High | Low |
| **R12** | Deployment-attributed Live Blotter + feed real lists into reconcile | P1 | M | High | Med |
| **R13** | Consolidated FE polling (`useLiveData` + `usePoll`+abort) + batch `/live/status?ids=` + memoize | P1 | M | High (perf/consistency) | Med |
| **R14** | Extend `/signals/enriched` with `live_trades` + Journal LIVE lane + Live↔page cross-links | P1 | M | High | Low |
| **R15** | Token-expiry countdown + stale-data overlay | P1 | S | Med-High | Low |
| **R16** | Cache token/client across broker reads; drop no-op reconcile call until R12 | P0 | S | Med (perf) | Low |
| **R17** | SELL disable/warn in `LiveOrderTicket` | P0 | S | Med | Low |
| **R18** | Field-scope `risk.live` writes; rename `stopAllPaper`→`stopAllDeployments` | P1 | S | Med | Low |
| **R19** | Split `live_broker.py` into 5 routers + `live/registry.py` + OrderExecutionService | P2 | L | Medium (debt) | Med |
| **R20** | Unified "Live Exposure" panel (merge broker+guard+session+attribution) | P2 | L | High | Med |
| **R21** | Frontend test runner + cover place/partition/derive/OAuth flows | P2 | M | High | Low |
| **R22** | Post-place order-outcome drawer (freeze-child tracking) | P2 | M | Med | Low |

**Recommended first slice (pre-gate hardening):** R1, R2, R3, R7, R8, R9, R16, R17 — all P0/S, mostly low-risk, and they close the dup-order and silent-failure holes before either env gate is ever flipped. **Then** R4→R6→R5 (the chokepoint/guard correctness core), **then** R10–R14 (operator visibility), with R19/R20/R21 as the structural payoff once correctness is locked.

The single highest-leverage build remains **R5 + R12 + R20 combined**: a deployment-attributed Live Exposure panel that reconciles the broker book against the guard registry and `live_trades` — it closes the biggest real-money blind spot (unguarded-after-restart), surfaces the orphaned `live_trades` data, and unifies Live ↔ Paper ↔ Journal.