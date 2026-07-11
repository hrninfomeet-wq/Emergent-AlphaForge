# AlphaForge Audit — 2026-07-08

**Scope:** read-only audit of (1) the Live Trading page as a professional terminal, (2) the Optimizer, (3) Strategy Library & authoring, (4) innovation proposals for live trading. No code was changed.

**Method:** 13 specialist auditor agents + 2 gap-critics fanned out over the codebase, every critical/high claim then adversarially re-verified (a skeptic agent instructed to refute it against the actual code, plus manual re-verification of the highest-stakes claims). **Across all verification passes, zero findings were refuted** — every claim below survived or was only mildly adjusted.

**Verification legend:**
- ✅ **CONFIRMED** — adversarially verified against the code (agent verifier and/or manual re-check of the cited lines).
- ⚠️ **ADJUSTED** — core claim confirmed; severity or a detail corrected (correction incorporated below).
- ◻ **corroborated** — reported once with concrete `file:line` evidence, not separately re-verified (verification wave hit session limits). Given the 0% refutation rate of the verified set, treat these as credible but re-check the cited lines before building.

**Finding IDs:** `L*` = Live Trading, `O*` = Optimizer, `S*` = Strategy Library, `P*` = proposal. Each carries severity (or value), effort (S = <1 day, M = 1–3 days, L = >3 days), and the concrete files.

---

## Executive summary

**The live stack's single biggest lie is that an error can look like an empty book.** `FlattradeClient` returns `[]`/`{}` whenever Noren answers `stat != "Ok"` — including the in-band daily-token failure `"Session Expired"`. Every consumer then treats "error" as "flat": the kill switch reports **ALL FLAT on a dead token**, the 10-minute auto-square marks the session "squared", the software guard **silently un-watches a live position**, the blotter renders live rows as FLAT, and every "Connected" indicator stays green because connectivity is only a local clock check. One transport-layer fix (raise/tag on `stat != Ok`) collapses five critical findings at once.

**The automated deploy-to-live path has never been able to fire.** Upstox ticks carry epoch-**milliseconds**; `resolve_premium()` compares them against `time.time()` **seconds** with a 120 s tolerance — the "fresh live tick" branch is mathematically unreachable, so every armed auto-entry refuses with `live_trade_error`, which **no frontend component reads**. This single unit bug explains why no real market-hours signal has ever validated the live path, and it is an S-effort fix.

**The kill switch flattens but does not stop.** The route never disarms deployments, never halts the engine, never trips the safety latch — an armed deployment can re-enter on the very next bar after "ALL FLAT". It is also unserialized against the guard, auto-square, and itself (double-flatten → naked short risk), and its panel unmounts in exactly the degraded states where it is most needed.

**Recovery is a one-shot that usually misses.** Restart reconciliation runs once at boot, gated on a valid Flattrade token — with daily OAuth and a boot-then-login workflow it effectively never runs; the 10-minute auto-square timer dies with the process while the UI shows a live countdown and a green heartbeat that merely echoes server time.

**The frontend hides every failure.** The dashboard ignores all 14 per-slice poll errors (frozen money data is indistinguishable from live), no money number has an as-of timestamp, a plain 422 white-screens the page (no ErrorBoundary — losing the kill switch with a position open), and "Stop ALL live" silently squares every open *paper* trade too.

**The optimizer's wiring is sound but its promises leak at the seams.** All trial search and all of WFO still climb the **spot** surface (option-net is a post-hoc top-K re-rank); the survival gate — the capital-safety centerpiece — runs on a hard-wired **₹200,000 phantom account** with no UI knob, can be run gross of option costs, and its "OOS" folds are in-sample w.r.t. selection and re-used twice more as selection targets; the exit-control search is a **silent no-op** under the default exit mode; the deploy wizard silently pins `pretrade_profile="Balanced"`; and the optimizer trades a 09:25–15:00 window while every deployment blocks entries from 14:50 — validated results systematically include trades live can never take.

**Authoring is strong machinery with missing connective tissue.** The compile/gate/sandbox pipeline is genuinely safe, but the flagship ICT/SMC structural features are advertised as buildable and then unreachable from every generation path; a 4000-token default silently reintroduces the documented Gemini truncation failure; "delete" resurrects via git; the user's real hand-authoring workflow (`My custom strategies/` — premium-triggered AlgoTest legs) has no home and is not expressible in the engine; and after "Installed" the app offers no next step, tracks no lifecycle, and lets a strategy with zero (or negative) paper evidence arm for real money with no advisory.

The prioritized backlog is in [section 5](#5-prioritized-backlog--top-10-by-value-per-effort). The top three fixes (broker-truth integrity, the tick-units bug, kill-switch-as-stop-all) are all S/M effort and remove the majority of the real-money risk surface.

---

## 1. Live Trading page as a professional terminal

36 findings (10 critical / 17 high / 9 medium), 20 verified, 0 refuted. Near-duplicate findings from independent auditors are merged below (noted).

**Overall assessment.** The wiring layer is solid: ~60 traced controls and 14 polling loops all resolve to real endpoints (zero dead ends, request shapes match). The read surface is consistently fail-soft, broker rejections on order paths are mapped to actionable reasons, and the approvals queue's one-shot token design, the GTT/OCO catastrophe backstop, the verified-flatten kill loop, and the guard's remove-before-square idea are genuinely good bones. What is missing is exactly what separates a hobby dashboard from a fail-safe terminal: **truthful degradation** (error ≠ empty, stale ≠ live, dry-run ≠ done), **cross-actor serialization** (every flatten path is check-then-act on snapshots), and **recovery that re-runs** (one-shot boot recovery + fire-and-forget timers). Single-request invariants are strong; cross-component invariants are absent — there is no `asyncio.Lock` anywhere in the live order/flatten path.

### 1.1 Broker-truth integrity — the `Not_Ok` family

#### L1. `FlattradeClient` conflates broker errors with empty books — kill switch reports false ALL FLAT, auto-square marks "squared" on a dead token — **critical, effort M** ✅
*(merges 3 independent findings: client conflation / kill-switch false-flat / "Session Expired" swallowed)*
Files: `backend/app/live/flattrade_client.py`, `backend/app/live/kill_switch.py`, `backend/app/routers/live_broker.py`
`order_book`/`position_book`/`trade_book` return `[]` and `limits` returns `{}` whenever `stat != "Ok"` (flattrade_client.py:100–145 — only HTTP ≠ 200 raises). Noren delivers auth failure **in-band**: `{"stat":"Not_Ok","emsg":"Session Expired : Invalid Session Key"}` (docs/Resources/flattrade-pi-api/catalog.json:1229); grep for "Session Expired" in backend/ = 0 hits. `kill_switch.py:905–911` computes `all_flat = residual == []` from that read → an expired daily token makes the kill switch report "ALL FLAT" while real positions remain. `_check_and_square_if_due` (live_broker.py:479–482) marks the session "squared" the same way.
**Build prompt:** In FlattradeClient, stop conflating: on `stat != "Ok"` raise a typed `BrokerReadError(emsg)` (or return a tagged result), distinguishing the documented "no data" emsg from auth/other errors. Update all consumers: kill switch and `_check_and_square_if_due` must treat error-reads as UNKNOWN (never `all_flat=True`, never "squared"), and surface "token expired — reconnect Flattrade" to the UI. Add a regression test: position_book returning `Not_Ok/Session Expired` must produce verdict UNKNOWN, not ALL FLAT.

#### L2. `LivePositionGuard` permanently drops a guarded live position on a single empty/`Not_Ok` position-book read — **critical, effort S** ✅ *(consumer of L1)*
Files: `backend/app/live/live_position_guard.py`, `backend/app/live/flattrade_client.py`
live_position_guard.py:316–322: book shows no row → `if entry.get("seen_filled"): self._registry.remove(...)` ("closed elsewhere"). The guard polls every 1.5 s (~40 calls/min, at Noren's documented budget), so one `Not_Ok` read un-watches a real position within seconds — stop/target/EOD protection silently gone.
**Build prompt:** Once L1 gives the guard a way to distinguish error from flat, require N consecutive *authenticated* flat reads (e.g. 3) before dropping a seen_filled entry, and log + surface a "guard dropped <tsym>: closed elsewhere" event into the blotter/reconcile chip rather than dropping silently.

#### L3. Failed square recorded as success: session → "squared", mode reverted, auto-square timer stops retrying on `squared=False` — **critical, effort S** ✅
Files: `backend/app/routers/live_broker.py`, `backend/app/live/auto_square.py`
live_broker.py:484–494 (`_check_and_square_if_due`) and :1923–1933 (`/order/square`) run `square_position(...)` then **unconditionally** `update_status("squared")` + `_revert_mode()`. The timer loop (:517–527) breaks on ANY result. auto_square.py's own contract (:47–48): "The caller MUST halt on squared=False — it NEVER silently skips"; :687–694 returns `squared=False` "exit rejected twice; operator intervention required".
**Build prompt:** Branch on `result["squared"]`: on False keep the session "armed" (or a new "square_failed" status), keep the timer retrying with backoff, halt the engine, and surface a red "SQUARE FAILED — intervene" banner. Same branch in the manual route's response.

#### L4. Broker transport errors escape the executor between claim and mark-submitted — 500 to UI, possibly-live unprotected order, duplicate-order window — **critical, effort M** ✅
Files: `backend/app/live/executor.py`, `backend/app/live/flattrade_client.py`, `backend/app/routers/live_broker.py`, `backend/app/runtime.py`
`place_order` catches only `RuntimeError` (flattrade_client.py:229–232) but httpx `ReadTimeout`/`ConnectError` and `json.JSONDecodeError` are not RuntimeError → they propagate through executor.py:145 (no try/except) → FastAPI generic 500 with a claimed intent outstanding; recovery only at next process restart (`resume_pending`).
**Build prompt:** In `FlattradeClient._post`, catch `httpx.HTTPError` + `ValueError` and re-raise as a dedicated `BrokerTransportError` so `place_order` returns `OrderResult(ok=False, ...)`. In `_transmit_and_arm`, on transport-ambiguous failure run the `remarks==cid` order-book adoption scan (bounded retries): if found, adopt + arm; if not, mark the intent FAILED and return `placed=False` with an actionable reason. Never 500 with a claimed intent outstanding.

#### L5. Broker outage flips LIVE blotter rows to "FLAT" and silently removes the no-backstop banner — false all-clear — **high, effort M** ✅
Files: `backend/app/routers/live_broker.py`, `backend/app/live/live_blotter.py`, `frontend/src/components/live/LiveBlotter.jsx`, `frontend/src/components/live/LiveDashboard.jsx`
Blotter endpoint catches broker failure into `broker_positions=[]` and returns 200 with no degraded flag (live_broker.py:804–825); a live trade not found in the (empty) book renders status FLAT (live_blotter.py:119–134), and the no-backstop banner filters on `status === "LIVE"` so it vanishes too (LiveDashboard.jsx:438–441).
**Build prompt:** Carry `broker_ok: false` on the blotter response when the position-book read failed; frontend renders LIVE-attributed rows as "UNKNOWN — broker unreachable" instead of FLAT and keeps/escalates the backstop banner.

#### L6. "Connected" means only "a token doc exists and local clock < 06:00 cutoff" — broker-side invalidation undetectable — **high, effort S** ✅
Files: `backend/app/live/flattrade_token.py`, `backend/app/routers/live_broker.py`, `backend/app/live/arm_state.py`, `backend/app/runtime.py`, `frontend/src/components/live/LiveBanner.jsx`
`get_status` is pure clock math (flattrade_token.py:252–278); the arm-state route counts "a token doc exists" as connected (live_broker.py:1666–1672); client factories build from any stored jKey with no validity check (runtime.py:137–147).
**Build prompt:** Add a cheap authenticated probe (e.g. `limits()`) behind a short cache; feed its result into `/flattrade/status`, arm-state's `connected`, and the LiveBanner chip so broker-side invalidation shows within a minute. Combine with L1 so the probe can actually see `Session Expired`.

### 1.2 Kill switch — reachability, serialization, completeness

#### L7. Kill switch is not a stop-all: deployments stay armed, engine not halted, latch not tripped — new real orders can fire seconds after "ALL FLAT" — **critical, effort M** ✅ *(merges 2 findings)*
Files: `backend/app/routers/live_broker.py`, `backend/app/live/engine.py`, `backend/app/auto_live.py`, `backend/app/live/executor.py`, `backend/app/routers/deployments.py`
The kill route (live_broker.py:2089–2196) flattens, sweeps GTT, reverts mode, updates the session — and contains **zero** occurrences of `risk.live.armed`, `engine.halt`, or `.trip(` (grep-verified). `auto_live.py` checks arm only at pipeline entry; the deployed path re-checks nothing after the kill; the post-fill resting OCO is placed *after* the kill's GTT sweep.
**Build prompt:** In `live_kill_switch`, BEFORE flattening: (1) `engine.halt("kill_switch")`, (2) trip the SafetyConfig latch so `can_trade` fails persistently across restarts, (3) bulk-disarm `{"risk.live.armed": true}` deployments (reuse stop-all's disarm block in deployments.py:760–778). AFTER flattening: re-fetch both books once a few seconds later and re-run the flatten for anything new (catches the in-flight fill + the post-kill OCO). Return the disarm/halt actions in the kill report.

#### L8. Two independent exit paths (guard vs kill-switch verified flatten) can both sell the same position → naked short; kill switch unserialized against itself — **critical, effort M** ✅ *(merges 2 findings)*
Files: `backend/app/live/kill_switch.py`, `backend/app/live/live_position_guard.py`, `backend/app/live/auto_square.py`, `backend/app/routers/live_broker.py`
The kill's re-price loop cancels only ITS OWN previous order and computes `remaining` from its own attempts' fillshares — never re-reading the position book before re-placing (kill_switch.py:779–803). `square_position`'s fresh-netqty re-confirm only aborts when already flat; its step 4 cancels EVERY working order for the tsym including another path's exit (auto_square.py:551–603). No lock or in-flight flag anywhere (repo grep: the only `asyncio.Lock`s are in option_coverage_cache/market_header); kill_switch.py:427–431 itself warns panic is not self-idempotent.
**Build prompt:** Add a per-tsym in-flight-exit claim (process-singleton dict or Mongo CAS doc with TTL) that guard `_square_and_record`, `square_position`, and `panic_squareoff_verified` must claim before placing an exit — second claimant waits/skips with a logged reason. Make the kill's re-price loop re-read the position book and clamp remaining to broker netqty before each re-place. Serialize the kill route itself behind a module-level `asyncio.Lock` + reject re-entry with "kill already running".

#### L9. Kill-switch panel unmounts in exactly the degraded states where it is most needed — no flatten control anywhere else on the page — **high, effort S** ✅ *(merges 2 findings)*
Files: `frontend/src/components/live/KillSwitchPanel.jsx`, `frontend/src/components/live/LiveDataProvider.jsx`, `backend/app/routers/live_broker.py`
`visible = openPositions.length > 0 || workingOrders.length > 0 || sessionActive; if (!visible && !result) return null` (KillSwitchPanel.jsx:124–125) — all inputs come from polls that are null when `/live-broker/positions` 400s (no token / restart-before-OAuth) or fails. Exactly when broker state is unknown, the one flatten-everything control disappears.
**Build prompt:** Always render KillSwitchPanel on the Live page. Derive a tri-state: normal (positions visible), degraded ("broker state UNKNOWN — kill will attempt flatten anyway"), and idle. In degraded state keep the button enabled — the backend kill path must be reachable without a positions read (it fetches its own books).

#### L10. Kill-switch GTT/OCO sweep and `_abort_protect` gaps: sweep only after flatten; abort path hardcodes `netqty=0` and reports `squared=True` ("no position") after a possibly-filled entry — **high, effort S** ✅/◻ *(GTT-route half ADJUSTED to medium by verifier; abort-protect half corroborated)*
Files: `backend/app/live/executor.py`, `backend/app/live/auto_square.py`, `backend/app/routers/live_broker.py`
`_abort_protect` calls `square_position` with `{"netqty": 0, ...}` → cancel-only path → `{"squared": True, "via": "cancel", "note": "no position"}` (executor.py:83–96, auto_square.py:492–510) while the module docstring claims "no unprotected live position can persist". GTT place/cancel routes have no idempotency and raw-500 on transport errors (live_broker.py:1459–1479).
**Build prompt:** In `_abort_protect`, re-read the position book for the tsym and pass the real netqty so a filled entry is actually flattened; treat "couldn't read book" as failure, not "no position". Wrap GTT place/cancel transport errors into the same BrokerTransportError handling as L4 and pin a `remarks` idempotency tag on GTT placement like orders do.

### 1.3 Lifecycle races and order-path integrity

#### L11. Automated deploy-to-live entries can never fire: epoch-ms ticks compared against epoch-seconds clock; the failure is write-only — **critical, effort S** ✅ *(merges 2 findings; manually re-verified end-to-end)*
Files: `backend/app/live/option_premium.py`, `backend/app/auto_live.py`, `backend/app/routers/live_broker.py`, `backend/app/upstox_stream.py`, `frontend/src/pages/LiveSignals.jsx`
Ticks carry ms (`ltt`/`currentTs`; market_header.py:264 divides by 1000; the guard compares vs `now_ms`), but `resolve_premium` (option_premium.py:138–139) checks `abs(now_ts - tick_ts) <= 120` with `now_ts = time.time()` seconds → `|1.75e9 − 1.75e12| >> 120`, the fresh-tick branch is unreachable. The auto-entry path requires a fresh tick (candle_close=None) → always refuses with `live_trade_error: live_entry_premium_unavailable_or_stale` (auto_live.py:336–348). Grep: `live_trade_error` has **zero readers** in backend routes and frontend. Manual option-premium display falls back to an **age-unbounded** candle close.
**Build prompt:** Normalize units in `resolve_premium` (if `tick_ts > 1e11` treat as ms and divide by 1000 — or better, normalize at the tick source and assert), add a regression test with a real ms tick, and bound the candle fallback's age. Then surface `live_trade_error` + the `live_intended` dry-run audit field per deployment on the Live page strip (LiveSignals.jsx reads only kill/drift reasons today) so a refusing armed deployment is loudly visible.

#### L12. Partial fills are unmodeled end-to-end: order_sm's PARTIAL machinery is dead code (no om feed runs); arm/journal/OCO fire on ACK at full intent qty — partial fill + fired OCO = naked short with the PC off — **high, effort L** ✅ *(merges 2 findings)*
Files: `backend/app/live/flattrade_client.py`, `backend/app/live/order_sm.py`, `backend/app/auto_live.py`, `backend/app/live_deploy_context.py`, `backend/app/live/close_loop.py`
`start_order_ws` has zero call sites (repo grep); live_broker.py:752–754 states "no om-feed daemon runs... a doc written SUBMITTED never advances to COMPLETE". The resting OCO is built with `qty=intent.qty` at arm time before fill qty is known (live_deploy_context.py:181); the journal records `capped*lot_size` at the band reference price, never broker `avgprc`; `close_loop` computes realized P&L from intended qty.
**Build prompt:** Two options, in order of preference: (a) start the om websocket (`start_order_ws`) under the supervisor and let order_sm's existing PARTIAL/fillshares machinery drive OCO qty, journal true-up (avgprc), and blotter state; or (b) poll the order book after each placed entry until terminal, then true-up journal qty/price and re-size or re-place the OCO to filled qty. Either way, kill-switch/square must clamp to broker netqty (L8) so partials can't over-sell.

#### L13. Manual square and 10-min auto-square flatten the FIRST non-zero position in the whole account, unserialized against each other — wrong-position squares and double-SELL races — **high, effort S** ✅ *(merges 3 findings)*
Files: `backend/app/routers/live_broker.py`, `backend/app/live/session_store.py`, `backend/app/live/auto_square.py`
Both paths loop `for pos in positions: if nq_int != 0: ... break` (live_broker.py:466–477, 1903–1914 — comment admits "heuristic: first open"); the session doc stores no tsym; with a concurrent deployed position they can flatten the wrong leg then mark the session squared. No status re-check, no lock.
**Build prompt:** Store `tsym` (and entry norenordno) on the live-test session at entry time; both square paths must select by tsym match and no-op with a visible warning if not found. Serialize with the same per-tsym in-flight-exit claim as L8.

#### L14. `POST /order/place` has no cross-request idempotency — double-click places two real orders — **high, effort S** ⚠ *(core race confirmed; cid dedup is per-request only)*
Files: `backend/app/live/executor.py`, `backend/app/live/mode.py`, `backend/app/routers/live_broker.py`, `frontend/src/lib/api.js`
`cid = new_client_order_id()` per request → IntentStore dedup can never engage across two requests; single-shot is consumed post-fill (executor.py:259, 324); `consume_single_shot` is an unconditional `$set`, not a claim (mode.py:194–203).
**Build prompt:** Accept a client-supplied idempotency key on `/order/place` (frontend generates per ticket-open), make IntentStore claim on that key, and make `consume_single_shot` a compare-and-set that fails the second caller. Frontend: disable Place while in flight (see L16 for the ambiguity state).

#### L15. Deployed-entry authorization is evaluated once at pipeline entry — kill/disarm/caps changes mid-pipeline don't stop the transmit — **high, effort S** ◻
Files: `backend/app/auto_live.py`, `backend/app/live/executor.py`
`auto_live.py:285` checks arm at entry; the executor's final transmit re-checks nothing (executor.py:503–518). A kill or manual disarm landing between signal evaluation and transmit doesn't prevent the order.
**Build prompt:** Re-check `engine.can_trade()` + deployment `risk.live.armed` + arm_state immediately before `place_order` inside the executor (the single chokepoint), returning `blocked_by: kill_switch/disarmed` — this also closes L7's in-flight window.

#### L16. One-click place chain (arm → approval → approve) has no ambiguity handling: network failure mid-approve shows "Place failed" while the order may be live — invites a duplicate — **high, effort M** ⚠ *(confirmed; approvals list/reject exist server-side with zero UI callers)*
Files: `frontend/src/components/live/LiveOrderTicket.jsx`, `backend/app/routers/live_broker.py`, `frontend/src/lib/api.js`
`handlePlaceConfirmed` judges `placedOk` solely from the approve response; an axios timeout after the backend began executing shows "Place failed" and auto-reverts mode (LiveOrderTicket.jsx:313–361). Backend reverts processed-but-not-placed approvals to PENDING with `retryable:true` — never surfaced (api.js listOrderApprovals/rejectOrder have zero callers).
**Build prompt:** On approve-call *network* errors (vs a `placed:false` response), show "UNKNOWN — verify Working Orders before retrying", force-refetch `/live-broker/orders`, and disable Place until it completes. Add a minimal pending-approvals list UI (list + reject endpoints already exist) or auto-expire reverted approvals server-side.

### 1.4 Restart & recovery

#### L17. Restart reconciliation is a one-shot at boot gated on Flattrade connectivity — with daily OAuth and boot-before-login it effectively never runs — **critical, effort S** ✅ *(merges 2 findings)*
Files: `backend/app/runtime.py`, `backend/server.py`, `backend/app/routers/live_broker.py`
`live_startup_recovery` runs once at startup and returns immediately if the client factory yields None (runtime.py:242–245); the OAuth callback only saves the token (live_broker.py:597–605); the 20 s supervisor reconciles only the Upstox stream/roller/exit-monitor. With a stale jKey the client builds and every call fails → rehydrate returns 0. Open overnight positions come back unguarded, unreconciled, and invisible to the 15:00 EOD square (registry-only iteration). **This is the same boot-gap class as the two previously fixed incidents (candle roller, exit monitor) — the recovery loop itself was never supervisor-reconciled.**
**Build prompt:** Trigger `live_startup_recovery` from the Flattrade OAuth callback after token save, and add it to the supervisor loop with a "has it succeeded since the last token refresh?" latch (mirroring the roller/exit-monitor reconcile pattern). Expose `/live-broker/recovery-status` and show a red strip on the Live page while positions exist but recovery hasn't succeeded.

#### L18. Backend restart with an armed 10-min session: auto-square timer never re-scheduled while the UI shows a live countdown and an always-green heartbeat that echoes server time — **high, effort S** ✅
Files: `backend/app/routers/live_broker.py`, `backend/app/runtime.py`, `backend/app/live/session_store.py`, `frontend/src/components/live/PositionMonitor.jsx`
`_schedule_auto_square` is called exactly once (order place) and dies with the process; the session doc (status, deadline) survives in Mongo; `"heartbeat": now` is unconditional server time, marked green when <10 s old against a 3 s poll.
**Build prompt:** In `live_startup_recovery` (post-L17), re-arm the auto-square timer from any persisted `armed` session with a future deadline (or square immediately if past). Change the heartbeat to a real liveness signal: last successful broker read + last guard tick, and mark it red when either is stale.

#### L19. Connected-restart guard rehydrate silently replaces strategy stops with a 50% catastrophe stop and drops targets — the `source="rehydrated"` flag is stripped by the guard-status route so the UI can never warn — **high, effort S** ✅
Files: `backend/app/live/live_position_guard.py`, `backend/app/routers/live_broker.py`, `frontend/src/components/live/GuardPanel.jsx`
`rehydrate_from_broker(default_stop_pct=50.0)` re-registers with a deep-default stop and no target, tagging `source="rehydrated"` explicitly "so the UI can flag levels reset" — but the guard-status payload omits `source` (live_broker.py:1495–1505) and GuardPanel has zero occurrences of it.
**Build prompt:** Include `source` in the guard-status payload and render an amber "levels reset to 50% catastrophe stop — re-set your stop/target" chip on rehydrated rows in GuardPanel; optionally re-derive the original stop from the deployment's auto_paper stop when the position maps to a known deployment.

#### L20. Guard exit is one-shot fail-open: remove-before-square with no re-register when the square FAILS or is a dry-run; split env gates allow real entries with log-only exits — **critical, effort S** ✅ *(merges 2 findings)*
Files: `backend/app/live/live_position_guard.py`, `backend/app/runtime.py`, `backend/app/live/arm_state.py`
`self._registry.remove(...)` executes BEFORE the square; on `{"squared": False}` (including the *default* `LIVE_GUARD_ARMED` unset dry-run, which returns squared=False after a log.warning) the entry is never re-registered — tracking ends at the first stop/target/EOD trigger. `LIVE_AUTOPLACE_ARMED=1` + `LIVE_GUARD_ARMED=0` is an explicitly legal state: real entries, log-only exits.
**Build prompt:** In `_square_and_record`, re-register the entry (with a retry/backoff counter and an escalation event) whenever the square returns squared=False or raises; treat dry-run squared=False identically. Add a loud arm-state warning (backend + ExecutionStateStrip) when entries are armed but the guard is dry-run — this configuration should be a deliberate, visible choice.

#### L21. Out-of-band closes orphan the resting NRML OCO: guard flat-drop and both square routes never cancel `oco_al_id` — orphan leg can fire against a flat account — **high, effort S** ✅
Files: `backend/app/live/live_position_guard.py`, `backend/app/routers/live_broker.py`, `backend/app/live/auto_square.py`
Guard drop paths remove the registry entry without cancelling the carried `oco_al_id`; `_cancel_all_working_for_scrip` cancels order-book orders only — a resting GTT/OCO is not in the order book.
**Build prompt:** Cancel `entry["oco_al_id"]` on every guard removal path (flat-drop, age-out), and give `square_position` an optional oco_al_id to sweep; add a reconcile check that flags GTT rows whose tsym has no open position ("orphan backstop — cancel?" chip on GttBook, which already renders its slice errors).

### 1.5 Frontend truthfulness

#### L22. Dashboard ignores every per-slice poll error; no money number has an as-of timestamp — frozen data indistinguishable from live; first-fetch failure shows eternal "Loading…" — **critical, effort M** ✅ *(merges 2 findings; manually verified)*
Files: `frontend/src/components/live/LiveDashboard.jsx`, `frontend/src/hooks/usePoll.js`, `frontend/src/components/live/LiveDataProvider.jsx`, `frontend/src/components/live/LiveBanner.jsx`
`usePoll` deliberately keeps last-good data on error (correct) and the provider exposes per-slice `errors.*` — but LiveDashboard.jsx:341 destructures only data + refetch; `errors` has zero consumers on the page except GttBook/GuardPanel. No last-updated stamp exists anywhere on positions/P&L.
**Build prompt:** Stamp `lastSuccess` per slice in usePoll; add a page-level degraded banner driven by `errors.*` ("broker data stale since HH:MM:SS — last error …"), grey/badge panels whose slice age exceeds ~2× cadence, distinguish "never loaded + error" from "loading", and render a compact as-of clock on the positions/P&L hero. Keep KillSwitchPanel rendered regardless (L9).

#### L23. Any FastAPI 422 on the order ticket renders raw validation objects as React children — white-screens the Live page (no ErrorBoundary anywhere) — **critical, effort M** ✅
Files: `frontend/src/components/live/LiveOrderTicket.jsx`, `frontend/src/lib/apiError.js`, `backend/app/routers/live_broker.py`
Clearing Band % → NaN → JSON null → 422 array `detail` stored verbatim and rendered as JSX (LiveOrderTicket.jsx:297, 764–766; same at :348/:980–984). React throws; no ErrorBoundary exists in the repo. `getApiErrorMessage()` already formats 422 arrays safely but has zero imports under components/live/. Same raw-`detail` pattern in PositionMonitor, GuardPanel, GttBook, OverallSettingsPanel.
**Build prompt:** Sweep components/live/ to route every catch through `getApiErrorMessage()`; make `band_pct` Optional server-side (or omit/clamp NaN in buildPayload); add a route-level ErrorBoundary around the Live page whose fallback still mounts KillSwitchPanel + a reload button.

#### L24. "Stop ALL live" silently squares every open PAPER trade and pauses every ACTIVE paper deployment — blast radius far wider than stated; rich response discarded — **high, effort M** ✅
Files: `frontend/src/components/live/LiveDeploymentStrip.jsx`, `backend/app/routers/deployments.py`, `frontend/src/lib/api.js`
Confirm text says "every live deployment"; `stop_all_deployments` squares ALL paper positions, pauses EVERY ACTIVE deployment (paper included), then the response summary ({squared_off, paused_deployment_ids, disarmed_live_deployment_ids}) is discarded for a generic toast.
**Build prompt:** Add a live-only stop endpoint (disarm + flatten armed live deployments only) for this button, or make the dialog state the true blast radius and render the response summary ("3 paper positions squared, 5 deployments paused, 1 live disarmed"). Keep the current global behavior available as an explicit "Stop EVERYTHING".

#### L25. ReconcileChip renders mismatch objects as `[object Object]` — the position-safety chip is unreadable exactly when it fires — **high, effort S** ✅
Files: `frontend/src/components/live/LiveDashboard.jsx`, `backend/app/live/reconcile.py`
`${mismatches.slice(0, 3).join(", ")}` on `{type, detail}` dicts (LiveDashboard.jsx:280–281); the same file already knows the shape (filters `m?.type === "unknown_broker_position"` at :428–431).
**Build prompt:** Format each mismatch as `` `${m.type}: ${m.detail?.tsym ?? ""} (qty ${m.detail?.netqty ?? "?"})` `` with a tooltip carrying the full JSON.

#### L26. Upstox WS outage silently suspends all tick-driven exits; user notified only after ≥2 min and only when a deployment is ACTIVE — **high, effort M** ✅ *(reconnect/backoff itself is sound)*
Files: `backend/app/upstox_stream.py`, `backend/app/live_feed_health.py`, `frontend/src/components/live/FeedHealthBanner.jsx`, `backend/app/paper_auto.py`, `backend/app/live/live_position_guard.py`
Exits correctly *hold* on stale ticks (120 s bounds) — but detection is candle-age ≥ 120 s polled at 10 s, and the only surface is gated `if (!feedHealth || activeCount < 1) return null`. With a manual (non-deployment) live position, a feed drop is invisible.
**Build prompt:** Ungate FeedHealthBanner whenever any live position or armed session exists (not just ACTIVE deployments); add reconnect_count/last_error to the banner detail; and emit a distinct "exits suspended — ticks stale Ns" state (vs "feed degraded") since that's the money-relevant condition.

#### L27. Safety-adjacent actions swallow failures silently: Flattrade login/logout, Upstox connect, feed restart, Stand-down, arm-ceiling fetch — **medium, effort S** ✅
Files: `frontend/src/components/live/LiveBanner.jsx`, `FeedHealthBanner.jsx`, `LiveDashboard.jsx`, `DeployToLivePanel.jsx`, `frontend/src/components/TokenCountdown.jsx`
Five verified catch-and-ignore sites. Stand-down failing (mode stays LIVE_TEST) is the dangerous one.
**Build prompt:** One-line inline error (via getApiErrorMessage) per catch; for Stand-down surface failure next to ExecutionStateStrip; for the ceiling fetch show "account ceiling unavailable — server still enforces it".

#### L28. Overall Controls (basket SL/target/trailing) can silently load all-disabled defaults — backend 200s defaults on store failure — Save then wipes the real risk config — **medium, effort S** ✅
Files: `frontend/src/components/live/OverallSettingsPanel.jsx`, `backend/app/routers/live_broker.py`
GET catches ANY store exception and returns `dict(DEFAULT_OVERALL_CONFIG)` (live_broker.py:1336–1341).
**Build prompt:** Return `{config, degraded:true}` (or 503) on store failure; frontend disables Save until the user acknowledges "couldn't load saved settings — Save will overwrite", plus a dirty-diff indicator.

#### L29–L36 (remaining medium findings, all ◻ unless noted)
- **L29.** Guard loop health hidden: `/live-broker/guard-status` reports registry contents, not whether the loop is alive/erroring — a dead guard task looks like "nothing guarded". Files: `live_broker.py`, `live_position_guard.py`. *Effort S.* Build: include `last_tick_ts` + `poll_errors` in guard-status; red badge when stale.
- **L30.** Feed self-heal zombie-stream blind spot: supervisor checks task liveness, not data flow — a connected-but-silent WS passes reconcile. Files: `runtime.py`, `upstox_stream.py`. *Effort S.* Build: reconcile on last-tick age, not task state.
- **L31.** `LIVE_TEST` single-shot is check-then-act (see L14's `consume_single_shot`); two rapid places can both pass. *Covered by L14's CAS fix.*
- **L32.** DRY-RUN vs REAL signaling: the deployment strip's "N armed" count doesn't distinguish env-gate-off dry-run from transmit-armed (arm_state knows). Files: `LiveDeploymentStrip.jsx`, `arm_state.py`. *Effort S.* Build: split the badge ("armed (dry-run)" vs "armed (LIVE)") from `would_transmit_entry`.
- **L33.** Account-level guardrails (`daily_loss_limit`, `max_open_positions` in SafetyConfig) are enforced only via engine ticks that have no production caller (same dead-wiring as the om feed, L12) — they silently don't protect. Files: `live/engine.py`, `live/safety.py`. *Effort M.* Build: enforce caps inside the executor chokepoint per-order instead of via the tickless engine loops.
- **L34.** `PUT /safety-config` + `reset-latch` have no UI — a tripped latch is curl-only. Files: `live_broker.py`, Live page. *Effort S.* Build: small SafetyConfig card with latch state + reset button (fits P2's risk dashboard).
- **L35.** Boot-order duplicate of L17 (merged).
- **L36.** Blotter LIVE-row attribution trusts `remarks==cid` matching only for app-placed orders; manually-placed broker orders show as unknown mismatches with no adopt affordance. Files: `live_blotter.py`, `reconcile.py`. *Effort M.* Build: an "adopt external position" action from the reconcile chip into the guard.

---

## 2. Optimizer — architecture, speed, usability, integrity

28 findings (10 high — all ✅ CONFIRMED — 15 medium, 3 low). The backend is architecturally solid (two-tier indicator memoization with drift guards, resumable jobs, budget-governed analyze, real survival gating); the frontend panel is genuinely good for a ~45-knob surface (accurate hints, client-side invalid-combo gating, honest WFO OOS separation, visible survival verdicts). The problems concentrate in **integrity seams** — where a number's label promises more than its computation delivers — and in the **handoff chain** (job → preset → deployment) that drops or invents context.

### 2.1 Integrity traps (can mislead even when working as coded)

#### O1. All trial search (TPE/grid/CMA-ES) and all of WFO still optimize SPOT; option-net is a post-hoc top-K re-rank — **high, effort L (in-loop fix) / S (mitigation)** ✅
Files: `backend/app/optimizer.py`, `backend/app/wfo.py`, `backend/app/rerank_select.py`, `backend/app/schemas.py`
The final pick is option-rupee-honest (real progress; frontend defaults to option_rerank), but the shortlist itself is spot-biased ("a config option-profitable yet spot-mediocre never enters the shortlist" — rerank_select.py's own docstring), and **WFO selects every window's params purely on spot with no re-rank stage at all** (`final_params = usable[-1]["best_params"]`, wfo.py:763; headline `best_value` = stitched spot points). Your own prior measurement: spot-best +289k ↔ option −207k.
**Build prompt:** Increment 1 (S): default `rerank_diversity=True` and widen the shortlist when spot↔option correlation is low. Increment 2 (L, changes results by design): option-aware in-loop objective — pre-load the option universe once (the re-rank already proves the pattern) and score each trial via the paired option sim; for WFO at minimum re-rank each window's top-5 on option ₹ before promotion, and label the current option_oos block "process-level OOS (mixed params)".

#### O2. Survival gate runs on a hard-wired ₹200,000 phantom account — no UI knob; %-gates and risk-of-ruin silently mis-scaled for the real account — **high, effort S** ✅ *(2 finders converged)*
Files: `backend/app/optimizer.py` (:592), `backend/app/routers/research.py` (:499), `backend/app/portfolio.py`, `frontend/src/pages/Optimizer.jsx`
`buildOptionConfig` never sends `sizing_config`, so capital is always 200k: for a ₹50k account every DD% is understated 4×, RoR paths start 4× further from ruin, and the equity-floor hint tells the user to reason from a capital they cannot see or set.
**Build prompt:** Add "Trading capital ₹" to the Survivability panel → `option_config.sizing_config.capital`; display the capital basis on every survival badge/scatter; validate `min_equity`/`ruin_floor` against it; stamp capital into `survival_summary` and the saved best run.

#### O3. Survival gate validates the wrong costs flag — gate can judge GROSS rupee curves with option costs off — **high, effort S** ✅
Files: `backend/app/routers/research.py` (:500–504), `backend/app/survival_validate.py` (:20–21), `frontend/src/pages/Optimizer.jsx` (:365–369, 966, 992)
Validation gates on the *spot* `costs_enabled` while the survival curve's costs come solely from `option_config.cost_config` (null whenever the "Apply option costs" switch is off). The UI hint admits the dependency; nothing enforces it. Index-option spread alone flips marginal survivors.
**Build prompt:** In optimize_start, when `survival_config.enabled`, require `option_config.cost_config.enabled` (400 otherwise); frontend force-locks the option-costs switch while Survivability is on (mirroring the existing exit-search gating pattern).

#### O4. Survival's "OOS" folds are in-sample w.r.t. trial selection, then re-used twice more as selection targets, then reported as `oos_return_pct` to the trust verdict — **high, effort M** ✅
Files: `backend/app/optimizer.py` (:601–605, 1258–1270, 1412–1413), `backend/app/survival.py`, `backend/app/deployment_quality.py` (:418)
Fold rows sit inside every trial's scoring window; ~50 finalists are filtered on those folds; the 12-config exit grid additionally optimizes against them; the resulting return then flows into deploy-time quality labelled out-of-sample.
**Build prompt:** Either exclude fold rows from trial scoring (true holdout), or relabel ("stress slices, not OOS") and stop passing `survival.total_return_pct` as `oos_return_pct` evidence; warn when `search_exit_controls` also selected on the gate.

#### O5. Exit-control search is a silent no-op under the default `spot_exit` mode — burns the full grid, never adopts, no warning — **high, effort S** ✅
Files: `backend/app/option_backtest.py` (:507–512, 685), `backend/app/optimizer.py` (:1251–1268), `backend/app/routers/research.py` (:513), `frontend/src/pages/Optimizer.jsx` (:133)
`exit_cfg` is only consumed inside the `option_levels` branch; the grid injects `exit_controls` but never `exit_mode`. Backtest Lab would 400 the same config ("premium trailing is impossible spot-only"); the optimizer's replay even sets `validate=False`.
**Build prompt:** 400 (or auto-skip with a stored warning) when `search_exit_controls` is on and `option_config.exit_mode != "option_levels"`; align research.py's `option_exec_on` semantics with runtime.py's; surface "grid had no effect" when all grid verdicts are identical.

#### O6. Optimizer is pinned to a 09:25–15:00 entry window with no knob while every deployment hard-blocks entries from 14:50 — validated results systematically include trades live can never take — **medium, effort M** ◻
Files: `backend/app/schemas.py`, `backend/app/optimizer.py`, `backend/app/backtest.py`, `backend/app/deployment_evaluator.py` (:42–43), `frontend/src/pages/Optimizer.jsx`
Also: a profile's `trade_window` keys inside `pretrade_filters` are silently ignored by the backtester (`_apply_pretrade_filter` reads only confidence + regimes).
**Build prompt:** Add `trade_window_start/end` to OptimizerStartReq defaulting to the live-effective 09:25–14:50, thread into every run_backtest call in optimizer.py/wfo.py, persist into the saved run + preset, surface in the panel next to the date range.

#### O7. `net_pnl_inr` objective is spot points × lot size dressed as rupees; top-N alternatives stay spot-ranked with no option column — **medium, effort S** ◻
Files: `backend/app/optimizer.py` (:163–167, 1167–1168), `frontend/src/pages/Optimizer.jsx` (:70, 1503)
**Build prompt:** Rename/caveat the objective ("net spot pts × lot"); in option-rerank jobs join each alternative to its rerank row (option ₹, paired count, survival badge) or point to the rerank table.

#### O8. Rerank table can highlight a non-promoted row as #1 under survival; job carries two conflicting option-net numbers (with vs without chosen exit controls) — **medium, effort S** ◻ *(2 findings merged)*
Files: `backend/app/optimizer.py` (:1264–1277, 1329, 1363–1365), `frontend/src/pages/Optimizer.jsx` (:2081–2121)
**Build prompt:** Mark the PROMOTED row (params == best_params); re-sort by the survival objective when survival ran; after exit-control adoption re-simulate the winner's full-window net with chosen controls and overwrite/annotate `option_pnl_value`; add an OOS ₹ column so the guide's full-window-vs-OOS check is one glance.

#### O9. No trials-vs-sample overfitting signal at the surface; the stage-2 best-of-50 rupee pick is never bias-adjusted (deflated Sharpe fires later, on a different page, on spot Sharpe) — **medium, effort M** ◻
Files: `backend/app/optimizer.py`, `backend/app/deployment_quality.py` (:313–341), `backend/app/rerank_select.py`
**Build prompt:** Compute deflated-Sharpe (or a trials-vs-trades ratio warning) at job finish, stored next to best_value; extend expected-max adjustment to the rerank stage (n = candidates); consider a final never-touched holdout slice.

#### O10. Cancelled/failed/paused option-rerank jobs still mint deployable-looking presets from spot-only params — the stop toast even encourages it — **medium, effort S** ◻
Files: `backend/app/routers/research.py` (:618–646), `frontend/src/pages/Optimizer.jsx` (:1324), `frontend/src/pages/SavedPresets.jsx`
**Build prompt:** Stamp preset config with terminal status + stage reached (`validated: spot_only|option_ranked|survival_passed`); render it on SavedPresets + deploy wizard step 1; confirm-dialog on applying from a non-done job.

#### O11. Zero-option-coverage `option_rerank` jobs finish "done" silently promoting the SPOT best; no coverage preflight exists (Backtest Lab has one) — **medium, effort M** ◻
Files: `backend/app/routers/research.py` (:130–183 vs :484–517), `backend/app/optimizer.py` (:1305, 1375–1383)
**Build prompt:** Reuse `_option_preflight_report` at optimize_start for option_rerank (contract/candle presence for window+moneyness+DTE) with an ingest link; when every ranked candidate has `paired_trade_count==0`, finish as `done_no_pairing` and have apply-as-preset refuse the execution block.

### 2.2 Speed (results-identical unless labeled)

#### O12. Cross-job indicator-cache poisoning in the parallel-eval sequential fallback — silently WRONG optimizer results — **high, effort S** ✅ *(the one speed-family finding that is a correctness bug)*
Files: `backend/app/parallel_eval.py` (:22–23, 76, 155–156), `backend/app/indicator_groups.py` (:300–308)
The pool-None fallback runs in the parent with a never-cleared module-global cache keyed on (group, params) only — not the frame. A later job reuses another job's Series against a different frame; pandas index-aligns → NaN tails → plausible-but-meaningless best_params. Your "run ONE instrument at a time" habit exists because of symptoms like this.
**Build prompt:** In the pool-None branch build a fresh `local_caches={}` per call and pass it through (optional param on `_worker_evaluate*` defaulting to the module global for the fork path). Byte-identical for correct runs.

#### O13. Analyzing stage still can't be cancelled/paused mid-loop; the exit-control grid is entirely ungoverned by the budget — **high, effort M** ✅
Files: `backend/app/optimizer.py` (:1139, 1246–1268, 1340–1349, 1371–1375), `backend/app/routers/research.py` (:565)
Cancel is read once before analysis; the exit grid checks nothing; a cancel after trials complete is silently discarded ('done').
**Build prompt:** Thread one `should_stop()` (cancelled ∥ paused ∥ over-budget) into every analyze loop iteration (rerank stage-1 per candidate, survival per finalist, each grid cell, between heatmap rows/robustness perturbations); on cancel finalize with partial results (plumbing exists for budget_hit). Behavior-identical when nobody cancels.

#### O14. Grid method: one raising trial fails the whole job and resume deterministically re-hits the same trial forever — **high, effort S** ✅
Files: `backend/app/optimizer.py` (:1009, 1416–1418, 1459)
**Build prompt:** try/except around the grid-branch evaluate; record the combo as disqualified and continue (mirrors bayesian `catch=(Exception,)`). Byte-identical when nothing raises.

#### O15. Both cache tiers are insert-only — with `optimize_indicator_periods` on, hit-rate decays to ~zero (the winner's combo is almost never among the first 16 pinned) — **medium, effort S** ◻
Files: `backend/app/optimizer.py` (:897–898), `backend/app/wfo.py` (:573–575), `backend/app/indicator_groups.py` (:304–306)
**Build prompt:** LRU eviction at both tiers (move-to-end + popitem(last=False), same caps). Byte-identical (keys are complete; drift guards already enforce).

#### O16. Parallel workers lack the tier-1 assembled-frame memo — every opt_workers trial pays full-frame copy + 45 column assigns even when no indicator param varies — **medium, effort S** ◻
Files: `backend/app/parallel_eval.py` (:76, 99)
**Build prompt:** Worker-process-local assembled-frame memo keyed on the indicator-key tuple in front of enrich_with_cache. Workers are per-job → no staleness. Byte-identical.

#### O17. `run_backtest` re-materializes the whole frame via `to_dict("records")` every trial — the dominant per-trial invariant on cache-hit paths — **medium, effort M (needs a mutation audit)** ◻
Files: `backend/app/backtest.py` (:96, 102)
**Build prompt:** Cache records keyed on id(frame) passed in by the optimizer job — but ONLY after auditing that no strategy (especially AI-authored POWERFUL-tier) mutates `row`/`prev`, or hand strategies a read-only mapping. Label: byte-identical only post-audit.

#### O18. Structural features recomputed from scratch every trial — `run_backtest` hands `materialize_features` a throwaway `{}` cache, defeating the registry's own memo — **medium, effort S** ◻ *(disproportionately punishes exactly the authored ICT/SMC strategies)*
Files: `backend/app/backtest.py` (:90–96), `backend/app/features/registry.py` (:99–119)
**Build prompt:** Let the optimizer own per-indicator-key feature caches and pass them into run_backtest (optional param, default {} keeps one-shot paths identical) — same trick as the existing group-cache tier.

#### O19. Option re-rank loads up to 4M candle rows in one blocking, uncancellable Mongo call; cap overflow degrades pairing with only a docker-log warning — **medium, effort M** ◻
Files: `backend/app/optimizer.py` (:755–760), `backend/app/wfo.py` (:445–448)
**Build prompt:** Stream per-contract in chunks (union_keys known) with progress/cancel/budget checks between chunks; stamp `rerank_candles_capped` on the job doc and render it beside the budget banner. Byte-identical rows.

### 2.3 Usability & the preset/deploy handoff

#### O20. Deploy wizard silently pins `pretrade_profile="Balanced"` — presets never carry the profile the result was validated under; no UI to change it — **high, effort M** ✅
Files: `frontend/src/pages/LiveSignals.jsx` (:326, 469, 418–457), `backend/app/routers/research.py` (:623–646), `backend/app/deployment_evaluator.py` (:386–387)
The recommended optimizer workflow uses profile=None → deployed trade selection silently diverges from the evidence (suppressed trades erode parity trust; or unvalidated trades get real money). Neither readiness nor quality checks profile match. The guide itself warns about exactly this.
**Build prompt:** Carry `pretrade_profile` in preset config (apply_opt_as_preset + BacktestLab save paths), prefill the wizard from it, add a visible profile selector with a mismatch warning, and have deploymentReadiness flag profile mismatch.

#### O21. Clone-into-setup silently drops survival + exit-search config and downgrades legacy jobs to spot — **medium, effort S** ◻
Files: `frontend/src/pages/Optimizer.jsx` (:576–626)
**Build prompt:** Restore survival_config/search_exit_controls/exit-grid strings in cloneJobConfig; default legacy evaluation_mode to "option_rerank" or toast the downgrade.

#### O22. Param-override min>max never validated — bayesian burns the full budget on silent-failed trials, grid completes 'done' with zero trials, parallel hard-fails — three inconsistent symptoms for one typo — **medium, effort S** ◻
Files: `backend/app/routers/research.py` (:484–516), `backend/app/optimizer.py` (:195–257, 1046–1055), `frontend/src/pages/Optimizer.jsx` (:1228–1234)
**Build prompt:** Validate min≤max (and fixed ∈ [min,max]) in optimize_start with the offending param named; mirror inline in the overrides panel; have `_build_param_space` raise descriptively as backstop.

#### O23. Job-history "Best" column mixes incomparable units (spot objective / Calmar / rupees) in one sortable column — **low, effort S** ◻
Files: `frontend/src/pages/Optimizer.jsx` (:2242, 2296), `backend/app/optimizer.py`
**Build prompt:** Render value with its unit from evaluation_mode/survival objective ("₹1.52L" / "calmar 1.87" / "obj 2.431"); add a dedicated Option ₹ column; stop sorting mixed units.

#### O24. Crash-resume silently drops up to ~45 counted-complete trials (trial_log flush cadence 50 vs counter 5); re-rank candle cap is log-only while Backtest Lab surfaces the same cap — **low, effort S** ◻
Files: `backend/app/optimizer.py` (:938–952, 1023–1029, 1070–1076), `backend/app/runtime.py` (:802–812)
**Build prompt:** Flush trial_log at the counter cadence (or derive completed from len(trial_log)); stamp `resumed_with_history_gap`; stamp `rerank_candles_capped` (see O19).

#### O25. Stale "Live parity" warning in Backtest Lab says premium exits don't travel with presets — the opposite of what the code now does — **low, effort S** ◻
Files: `frontend/src/pages/BacktestLab.jsx` (:1327–1331), `backend/app/preset_execution.py` (:43–46), `frontend/src/pages/LiveSignals.jsx` (:429–435)
**Build prompt:** Rewrite the note: premium target/stop DO travel via the preset execution policy into the deploy wizard's auto-paper fallbacks; keep only the strategy-exits-take-priority caveat.

*(O26–O28 minor duplicates of the above merged: survival-capital [into O2], WFO spot scoring [into O1], ranked-table conflicts [into O8].)*

---

## 3. Strategy Library & authoring

24 findings (10 high, 11 medium, 3 low; 4 manually spot-verified ✅, rest ◻). The safety machinery is genuinely strong — deterministic compile from validated specs, pure R1–R9 feasibility classification, AST allowlist + subprocess smoke gate, truncation-aware provider backends, boot-survival of broken plugins, server-side retire enforcement at every mutation point, a real delete blast-radius gate. The failures are at the edges and in the connective tissue: the simple journey has avoidable dead-ends, the complex journey's flagship capability is advertised but unreachable, and **nothing carries a strategy from "installed" to "profitably deployed" — every hop is an unguided context switch.**

### 3.1 Creating simple strategies — where the short path breaks

#### S1. `complete_structured`'s `max_tokens=4000` default silently defeats the documented 8192 truncation fix on the spec-map and feasibility paths; full-Python on Gemini-pro keeps thinking ON inside a shared 8000 budget — **high, effort S** ✅ *(manually verified: llm_client.py:92 always overrides _gemini.py's DEFAULT_MAX_TOKENS=8192)*
Files: `backend/app/ai/llm_client.py`, `backend/app/ai/_gemini.py`, `backend/app/ai/strategy_author.py`, `backend/app/ai/authoring_agent.py`, `backend/app/ai/py_author.py`
This half-reintroduces the #1 root-caused real-world authoring failure (thinking tokens starving output). The truncation message also blames description length — wrong recovery guidance.
**Build prompt:** Make `complete_structured`'s max_tokens Optional (backends apply their own defaults) or set it to 8192; raise the POWERFUL py_author budget to 16–32k or set a bounded thinking_budget on pro; append "try the other provider/tier" to the truncation message.

#### S2. Stale feasibility REJECT permanently disables Install in Spec mode — no invalidation, no dismiss, dead-end when the AI box is empty — **high, effort S** ◻
Files: `frontend/src/components/strategy/AuthoringWizard.jsx` (:824, 223–233, 422)
An advisory LLM verdict about *old* input hard-blocks a deterministic, server-revalidated action.
**Build prompt:** Invalidate (or mark stale) `ruleSet` whenever aiSource or the form changes; add a dismiss ✕ to the Feasibility panel; downgrade REJECT to a confirm-style warning since install re-validates deterministically anyway.

#### S3. Install failures (Spec + Python) are the one error class still reported via vanishing toasts, with a regex-coupled overwrite flow — **medium, effort S** ◻
Files: `frontend/src/components/strategy/AuthoringWizard.jsx` (:296, 321–323, 27–30)
**Build prompt:** Route install failures into the same persistent error-panel pattern as genError; trigger overwritePending off HTTP 409 instead of `/already exists/i`; surface the backend's AST-extracted id before install.

#### S4. No draft persistence: SPA navigation or the doc-recommended hard-reload discards the transcript, generated form, and hand-edited Python — paid AI output lost — **medium, effort S** ◻
Files: `frontend/src/components/strategy/AuthoringWizard.jsx` (all state is plain useState; zero localStorage matches)
**Build prompt:** Persist a lightweight draft (aiSource, form state, pyCode, mode, provider) to localStorage on change; "resume draft?" on mount; beforeunload guard while dirty.

#### S5. Provider "configured" means key-present, not usable — the unfunded Anthropic account is offered as an equal choice and fails with raw API errors; anthropic-first fallback order — **medium, effort S** ◻
Files: `backend/app/ai/llm_client.py` (:39, 42–43, 73–82), `frontend/src/components/strategy/AuthoringWizard.jsx` (:384–397), `backend/app/ai/_anthropic.py`
**Build prompt:** Track last-call success per provider in providers_status and mark failing providers in the dropdown; append "try the other configured provider" to backend errors; flip `_PREFERENCE` to gemini-first to match funded reality.

### 3.2 Creating complex strategies — the advertised wall

#### S6. Structural-feature pipeline (ICT/SMC) is unreachable from every authoring path despite being advertised as buildable — **high, effort M** ✅ *(manually verified: spec-mapper prompt enumerates only indicator columns + OHLCV)*
Files: `backend/app/ai/strategy_author.py` (:28), `backend/app/routers/strategies_admin.py` (:91), `frontend/src/components/strategy/AuthoringWizard.jsx` (:160–217, 477–484), `backend/app/ai/py_author.py` (:20, 45–46), `backend/app/ai/capability.py` (:87–91)
The gate says "Detectable via the 'fvg_zones' feature", the capability panel says "Buildable now — backtest AND live" — but the spec-mapper prompt never mentions `required_features` or any feature column; the wizard form round-trip strips a feature declaration even when Gemini emits one (spec_schema supports it; the compiler allows feature columns once declared); the catalog route sends no feature columns; the py_author prompt omits them too. The wall exists exactly where the explainer promises there is none.
**Build prompt:** Plumb `required_features` end-to-end: add feature columns + the required_features field to the spec-mapper prompt (conditional on declared features); carry spec.required_features through loadFromSpec/buildSpec (hidden or chip state); include feature columns (grouped, labelled) in /strategies/catalog; mention required_features + feature columns in the py_author prompt. Acceptance: "buy on bullish FVG fill" authors, installs, backtests without manual code.

#### S7. `aggregate_gate` returns BUILD ("All rules map cleanly") for rules Spec mode cannot express (R6/R7/R8 multi-bar verdicts) — the user is never told to switch to Python mode — **medium, effort S** ◻
Files: `backend/app/ai/capability.py` (:265–282), `backend/app/ai/authoring_agent.py` (:54–78, 151–153)
**Build prompt:** Make the gate mode-aware: BUILDABLE_WITH_FEATURE with feature=None aggregates to ADVISE naming the constraint ("N rule(s) need Full-Python mode"); wizard install-gate note suggests switching modes.

#### S8. The user's hand-authoring workflow has no home: `My custom strategies/` is inert, and the strategy being authored (premium-triggered AlgoTest legs) is not expressible in the engine at all — **high, effort L (decision + build)** ◻ *(the highest-signal library finding: this is what the user is actually trying to do)*
Files: `My custom strategies/NF_CE_PE_EXP2_Strategy_Spec.md`, `backend/app/strategies/base.py` (:20–35, 186), `docs/STRATEGY_PLUGINS.md`
Discovery imports only `app.strategies.builtin` + `app.strategies.plugins`; no upload path exists; and the spec needs option-premium entry triggers ("buys whichever option's premium rises 15% from its 09:31 price"), two independent leg state machines, a 5%-step trailing ratchet, reversal legs, and a 15:13 square-off — none expressible in the bar-by-bar spot-candle Signal contract.
**Build prompt:** Short term (S): docs/UI note that hand-written strategies go in `backend/app/strategies/plugins/` or the wizard's paste-Python panel, and extend capability_summary + the R1–R9 classifier to explicitly name premium-triggered entries and trailing ratchets as unsupported so /author/converse REJECTs this spec class with an explanation. Long term (L): decide whether to add engine primitives (premium-reference triggers, trailing-step exits, re-entry/reversal hooks) — AlgoTest-style leg strategies are clearly the user's real demand; the live stack already has premium ticks and a trailing guard to build on.

### 3.3 Registry & lifecycle edges

#### S9. Deleting a FAILED (broken) plugin silently leaves the .py on disk — the ghost resurrects UN-retired on every reload/restart — **high, effort S** ✅ *(manually verified: `_delete_plugin_file` returns False for unregistered ids; endpoint ignores it and reports `deleted:true`)*
Files: `backend/app/routers/strategies_admin.py` (:36–44, 208–216), `backend/app/strategies/base.py` (:213–229)
**Build prompt:** In `_delete_plugin_file`, when registry.get() is None resolve the path from the error record (module name → `<plugins_dir>/<id>.py`, still guarded by the plugins-dir marker); make the endpoint honor the boolean (409 or `file_deleted:false`) instead of unconditionally claiming success.

#### S10. Delete is not permanent: the 11 shipped plugins are git-tracked — any checkout/stash/pull silently resurrects a deleted strategy as fully ACTIVE — **high, effort M** ◻ *(the user just deleted `opening_range_adaptive.py`; it is one `git checkout .` away from returning)*
Files: `backend/app/strategies/plugins/`, `backend/app/routers/strategies_admin.py`, `frontend/src/pages/StrategyLibrary.jsx` (:83 — "This cannot be undone" is exactly backwards)
**Build prompt:** Keep a tombstone: on delete, keep the strategy_lifecycle row with `deleted:true`; auto_discover/list_all skip + flag tombstoned ids that reappear on disk ("resurrected by git — delete again or restore"). Alternatively `git rm --cached` the shipped copies and seed from a templates dir on first boot. At minimum warn in the delete dialog that the file is version-controlled.

#### S11. Spec-path `author_install` with overwrite=true can hijack a built-in strategy id (python path blocks this; spec path doesn't); duplicate plugin ids silently last-write-win — **medium, effort S** ◻
Files: `backend/app/routers/strategies_admin.py` (:285–286 vs :430–432), `backend/app/strategies/base.py` (:139–143, 186)
**Build prompt:** Mirror the python path's builtin check in author_install (403 when origin_of(id)=='builtin'); log.warning + record `_errors` entry on register() id collision so the library page shows shadowing.

#### S12. Frontend always sends `confirm=true`, so the backend orphan (blast-radius) gate never fires for UI deletes when the references fetch failed — **medium, effort S** ◻
Files: `frontend/src/pages/StrategyLibrary.jsx` (:82–85), `backend/app/routers/strategies_admin.py` (:198)
**Build prompt:** Pass confirm=true only when the references fetch succeeded and counts were shown; on failure send confirm=false and surface the 409 references_exist payload as a second confirm.

#### S13. Hot-reload exists (`POST /strategies/reload`) but has no UI button, and STRATEGY_PLUGINS.md + a base.py comment explicitly deny it exists — **medium, effort S** ◻
Files: `backend/app/routers/strategies_admin.py` (:218–222), `backend/app/strategies/base.py` (:174–211), `docs/STRATEGY_PLUGINS.md` (:137), `frontend/src/lib/api.js` (:34 — zero callers)
**Build prompt:** Add a "Rescan plugin folder" button on StrategyLibrary → api.reloadStrategies() → refresh; rewrite the doc section (reload as primary, restart as fallback); delete the stale base.py comment.

#### S14. Library card can't answer "what does this strategy need and which file is it?" — no required_features, no param bounds/types, no module/file identity — **medium, effort S** ◻
Files: `frontend/src/pages/StrategyLibrary.jsx` (:213–227), `backend/app/strategies/base.py` (:117–130 — meta() already ships all of it)
**Build prompt:** Render required_features chips; param chips get a "type, min–max" tooltip; origin=custom cards show the backing module/filename (add to meta() via `type(s).__module__`).

#### S15. A syntax error in the volume-mounted `plugins/__init__.py` crashes the whole backend at boot (only ImportError caught at package level) — takes down kill-switch routes too — **low, effort S** ◻
Files: `backend/app/strategies/base.py` (:187–190), `backend/server.py` (:65), `docker-compose.yml` (:34)
**Build prompt:** Broaden the package-level except to Exception; log + record a synthetic registry error ("plugins package failed to import") so the app boots with builtins and the library page shows why.

#### S16. Full-Python smoke gate hard-codes `/app` paths — outside Docker every candidate 500s with zero explanation — **low, effort S** ◻
Files: `backend/app/ai/py_sandbox.py` (:232–235), `backend/app/routers/strategies_admin.py` (:406–437)
**Build prompt:** Derive app root from `__file__` (or Test-Path first) and return `{ok:false, error:"smoke sandbox unavailable on this host — run inside the backend container"}`; wrap the route call so environment errors are a 503 with that message.

### 3.4 From "authored" to "profitably deployed" — the missing connective tissue

#### S17. Authoring dead-ends: after "Installed", no next step; the required pipeline (backtest → preset → deploy) is never communicated; deploy only accepts presets without saying so — **high, effort M** ✅ *(manually verified: `ALLOWED_SOURCE_TYPES = {"preset", "backtest_run"}`)*
Files: `frontend/src/components/strategy/AuthoringWizard.jsx` (:293–319), `frontend/src/pages/StrategyLibrary.jsx` (:242–257), `frontend/src/pages/LiveSignals.jsx` (:524, 572), `backend/app/strategy_deployments.py` (:9), `frontend/src/pages/BacktestLab.jsx` (:217–229)
**Build prompt:** On successful install, replace the toast with a next-step panel: "Backtest it now" (add `?strategy=<id>` deep-link support to BacktestLab), "Optimize it" (`/optimizer?strategy=<id>`), plus a one-paragraph pipeline explainer. Add the same CTAs to StrategyCard for strategies with no runs/presets yet.

#### S18. No lifecycle tracking: authored → backtested → optimized → paper → live exists only in the user's head; SavedPresets does an N+1 readiness fan-out for its badges — **high, effort M** ◻
Files: `frontend/src/pages/StrategyLibrary.jsx` (:177–231), `frontend/src/pages/SavedPresets.jsx` (:150–151), `backend/app/routers/deployments.py` (:507–541), `backend/app/forward_metrics.py` (:247–251)
All raw data exists (backtest_runs / optimization_jobs / presets / strategy_deployments all carry strategy_id); no endpoint joins it.
**Build prompt:** Add a per-strategy pipeline endpoint (one aggregate per collection grouped by strategy_id: counts + latest timestamps) and render a 5-stage chip row (Authored/Backtested/Optimized/Paper/Live) on StrategyCard, each incomplete stage a clickable CTA. Also serves SavedPresets to kill the N+1.

#### S19. Live arming consults ZERO performance evidence — a strategy with no (or negative) paper history arms for real money with no advisory — **high, effort S** ◻
Files: `backend/app/routers/deployments.py` (:820–903), `frontend/src/components/live/DeployToLivePanel.jsx` (:157–357)
Deployment *creation* shows TrustScorecard/readiness/preflight; *arming* — weeks later, when forward evidence actually exists — shows none of it. The highest-stakes click in the app is the least informed.
**Build prompt:** In the typed-ARM dialog, fetch and display `/deployments/{id}/metrics` + stored quality warnings: forward sessions/trades/WR/total P&L, paper-vs-baseline drift, with a hard advisory line when complete sessions < N or forward P&L ≤ 0 (still overridable — aid, don't restrict). Include the same snapshot in the arm response audit trail.

#### S20. The forward-results feedback loop is effectively unreachable for this user: 10-complete-session gate + 70% session-completeness excludes nearly all trades when the PC is off during market hours — **high, effort M** ◻
Files: `backend/app/forward_metrics.py` (:19–21, 225–233), `backend/app/paper_analytics.py` (:595–597), `backend/app/routers/deployments.py` (:562–563), `frontend/src/pages/StrategyLibrary.jsx` (:280–289)
The app collects forward evidence it then refuses to ever show, and never names the root cause (PC uptime) or remedy.
**Build prompt:** Keep gated numbers as the honest headline but (a) always show the ungated all-trades tally with the exclusion reason ("42 trades excluded: sessions incomplete — PC/feed offline"), (b) count completeness only over minutes the deployment was ACTIVE, (c) one-line explainer + link to the uptime/roller fix when exclusions dominate.

#### S21. Forward-vs-backtest comparison keys on brittle exact param-dict equality; the pinned `source_snapshot.metrics` baseline is write-only — drift mostly reports no_baseline; alarm fatigue trains reflexive acknowledgment — **medium, effort M** ◻
Files: `backend/app/strategy_deployments.py` (:163–168), `backend/app/routers/deployments.py` (:241–279), `backend/app/paper_analytics.py` (:593–594), `frontend/src/pages/SavedPresets.jsx` (:61)
**Build prompt:** Use `deployment.source_snapshot.metrics` as the drift baseline of record (exact by construction); report WHICH params differ (diff keys) instead of a boolean; treat non-strategy keys/float rounding as matches.

#### S22. Optimizer silently invents search bounds (0..100 int, 0.0..1.0 float) for authored params that omit min/max — the first optimization of an authored strategy quietly poisons its own validation — **medium, effort S** ◻
Files: `backend/app/optimizer.py` (:236–240), `backend/app/ai/spec_schema.py` (:29–30), `frontend/src/components/strategy/AuthoringWizard.jsx` (:138–140), `frontend/src/pages/Optimizer.jsx` (:1233–1234)
**Build prompt:** Flag fallback-derived bounds in the job doc + amber row hint ("no declared bounds — searching 0..100"); wizard requires min/max for numeric params (default ±50% around default); /author/compile warns when absent.

#### S23. Quality gate: one checkbox blanket-acknowledges all N warnings; the snapshot is frozen at creation and never re-evaluated — **medium, effort M** ◻
Files: `backend/app/deployment_quality.py` (:514), `backend/app/routers/deployments.py` (:349–361, 445–446), `frontend/src/pages/LiveSignals.jsx` (:970–971)
**Build prompt:** Per-warning acknowledgments ({id, ack_at}); recompute quality lazily on /deployments/overview (or daily) and surface "quality changed since deploy: +selection_bias" on the card and in the ARM dialog (pairs with S19).

#### S24. Deploy-wizard preflight fetches EVERY 1m candle for the instrument (unbounded query, Python-side date filter) on each wizard open — grows forever — **low, effort S** ◻
Files: `backend/app/deployment_preflight.py` (:96–105), `frontend/src/pages/LiveSignals.jsx` (:388–397)
**Build prompt:** Bound the query by the 30-day window it already computes (or use a bounded `distinct("session_date", ...)`).

---

## 4. Innovation proposals for the Live Trading page

Proposals authored from the audit's confirmed capability map — every "builds on" module verified present during areas 1–3. Ordered by value. Several deliberately *are* the fix for confirmed findings, framed as the feature the user should get rather than the bug being patched.

#### P1. Degraded-mode state machine — one truthful banner for FEED DOWN / BROKER TOKEN DEAD / DATA STALE / RECONCILE MISMATCH — **value very-high, effort M**
Builds on: `LiveDataProvider.jsx` (per-slice `errors.*` already exposed and currently unread — L22), `live_feed_health.py`, `flattrade_token.py` + the L6 broker probe, `reconcile.py`, `FeedHealthBanner.jsx`.
What's new: a single state-machine component consuming the existing signals, with a severity-ordered banner: each state names the **money risk** ("exits suspended — ticks stale 3m; stops will NOT fire") and the **one-click remediation** (Reconnect Upstox / Flattrade OAuth / Restart roller — all endpoints exist). Kill switch stays mounted in every state (L9).
Measurement: time-to-detection of an injected feed/token failure drops from "whenever the user notices frozen numbers" (currently unbounded) to <15 s; zero silent-failure states remain reachable.

#### P2. One-glance risk dashboard with session P&L guardrails that auto-trip the kill switch — **value very-high, effort M**
Builds on: `live_blotter.py` (day P&L), `portfolio_greeks.py` (net delta/theta/vega — server-side BS IV already built), `margin.py` (utilization), `live_position_guard.py` registry (distance-to-stop per leg), `overall_controls.py` (basket SL/target/trailing engine — already polls at 3 s), `deployment_kill_switch.py` + the L7 stop-all kill.
What's new: a compact header card (day P&L vs a user-set daily loss floor, margin %, net Greeks, worst distance-to-stop) plus **one new rule in the overall-controls monitor**: when day P&L ≤ −floor, invoke the (post-L7) kill switch — flatten, disarm all, halt, latch — and require an explicit reset. This converts the existing basket-SL machinery into the account-level circuit breaker the audit found missing (L33).
Measurement: max intraday account drawdown becomes bounded by the floor + slippage; discipline failures ("one more trade") become mechanically impossible while latched.

#### P3. Morning go/no-go preflight — one click runs every gate before the session — **value very-high, effort M**
Builds on: `reconcile.py` (mismatch report), `flattrade_token.py` + `limits()` probe (L6), `live_feed_health.py` + roller status, `runtime.live_startup_recovery` (made re-triggerable by L17), `arm_state.py`, `gtt.py` (orphan sweep, L21), `margin.py`, `kill_switch.py` dry-run path.
What's new: a `/live-broker/preflight` endpoint + checklist card: token probe ✓, feed fresh ✓, roller rolling ✓, recovery ran since token refresh ✓, positions reconciled ✓, no orphan GTTs ✓, margin fetched ✓, **kill-switch self-test** ✓ (dry-run: books readable, quotes available for re-price, cancel permissions — reusing the per-leg report machinery). ARM buttons render a red "preflight failing" chip until green (advisory, not blocking — matching app philosophy).
Measurement: eliminates the entire boot-before-OAuth failure class (L17/L18) operationally; every morning starts from verified-safe instead of assumed-safe.

#### P4. Pre-trade expectancy check — the backtest inside the order ticket — **value high, effort M**
Builds on: `preset_execution.py` (deployment → source preset → params), stored `backtest_runs` trade logs (entry/exit/MFE/MAE per trade — already computed by `option_backtest.py`), `regime.py` + `scenario_classifier.py` + `market_context.py` (today's regime), `vix.py`, `RegimeBadge.jsx` (exists).
What's new: on the order ticket (manual) and in the deployment strip (auto), a small card: "This setup, in today's regime (<regime>): N historical trades, WR x%, avg R, worst MAE vs your proposed stop; historical expectancy ₹X/trade". Warn (amber) when today's regime bucket was historically negative for this strategy. Pure read-side: one endpoint joining the source run's trade log with the regime classifier.
Measurement: every real-money order carries its evidence; count of entries taken against a historically-negative regime bucket → 0 unless explicitly overridden.

#### P5. EOD reconciliation report + auto-journal — broker truth vs app truth, every day — **value high, effort S/M**
Builds on: `flattrade_client.trade_book()`, `live_blotter.py`, `close_loop.py` (realized P&L), `journals` router + SignalJournal page, `gtt.py`.
What's new: a scheduled (or on-demand) end-of-day job: diff broker trade book vs live_trades journal (fills the app never saw — the L12 partial-fill hole's safety net), verify avgprc vs journaled entry price, flag orphaned GTTs, then write a one-line day summary into the signal journal. Renders as a "Day close: 3 trades, ₹+2,140, 0 mismatches" chip.
Measurement: unjournaled fills and price drift are caught same-day instead of never; the journal becomes trustworthy enough to drive P4's expectancy stats from *live* data too.

#### P6. Live-vs-backtest drift monitor on the Live page, with auto-de-arm — **value high, effort M**
Builds on: `forward_metrics.py` (computes forward vs baseline already; gated per S20), `paper_analytics.py` drift states, `auto_live.py` journaling (entry band vs fill — once L11/L12 land, real slippage per trade), `deployment_kill_switch.py` (drift-pause plumbing exists).
What's new: per-deployment strip chip: live fills vs backtest expectation (slippage ₹, live R vs backtest R, WR delta) with S20's ungating so it actually populates; threshold breach → auto-de-arm (not kill) + amber chip. Closes the loop the library audit found severed (S20/S21).
Measurement: a deployment whose live edge decays is de-armed within N trades instead of running until the user notices.

#### P7. Position sizing advisor in the ticket — premium-at-risk vs survival capital — **value medium, effort S**
Builds on: `margin.py` (pre-check + basket margin via broker API), `survival.py` (risk-of-ruin machinery + the O2 capital knob once added), `instruments.py` lot sizes, order ticket.
What's new: next to qty in LiveOrderTicket: "this order risks ₹X (premium × lots) = y% of your capital; at your historical WR, risk-of-ruin at this size = z%" — reusing the survival math on the live account capital. Amber above a user-set %.
Measurement: per-trade risk becomes visible before transmit; oversize entries (relative to the survival evidence) require a deliberate override.

#### P8. Post-kill / post-square verification loop — "verified flat" as a first-class state — **value medium, effort S**
Builds on: `kill_switch.py` verified-flatten + per-leg report (already the best-engineered part of the live stack), `reconcile.py`, the L1 error-vs-empty fix.
What's new: after ANY flatten path (kill, manual square, auto-square, guard) schedule a +5 s and +30 s broker re-read; only then mark the session/blotter "VERIFIED FLAT (checked twice)" vs "FLATTEN SENT — verifying". Catches the L8 race-window fill and the L3 false-squared class permanently, and gives the user a state they can trust enough to walk away from the PC.
Measurement: zero states where the app claims flat while the broker holds a position (the audit found four distinct paths to that state today).

---

## 5. Prioritized backlog — top 10 by value-per-effort

Each entry is written to be pasteable as an implementation prompt. Order = build order; items 1–3 remove most of the real-money risk for under a week of work.

**1. [critical, S/M] Broker-truth integrity: stop conflating broker errors with empty books.**
> In `backend/app/live/flattrade_client.py`, `order_book`/`position_book`/`trade_book`/`limits` currently return `[]`/`{}` whenever Noren answers `stat != "Ok"` (including in-band `"Session Expired : Invalid Session Key"` — see docs/Resources/flattrade-pi-api/catalog.json:1229), and `_post` only raises on HTTP ≠ 200. Change the book/limits readers to raise a typed `BrokerReadError(emsg)` on `stat != "Ok"` (distinguishing the documented "no data" emsg, which stays an empty result). Then fix every consumer that treated error as flat: `kill_switch.py:905–911` must yield `all_flat=None/UNKNOWN` (never True) on a failed re-read; `_check_and_square_if_due` and `/order/square` in `routers/live_broker.py` must NOT mark the session "squared" (branch on `result["squared"]` — today :484–494 and :1923–1933 set it unconditionally, and the retry timer must keep retrying on failure); `live_position_guard.py:316–322` must require N consecutive *authenticated* flat reads before dropping a seen_filled entry; the blotter endpoint must return `broker_ok:false` instead of rendering live rows FLAT. Surface "token expired — reconnect Flattrade" wherever a BrokerReadError bubbles. Add container tests: a `Not_Ok/Session Expired` position_book must produce kill verdict UNKNOWN, an un-squared session, an intact guard registry, and a degraded blotter.

**2. [critical, S] Fix the ms-vs-seconds tick bug that silently disables the entire deploy-to-live path, and surface entry refusals.**
> Upstox ticks carry epoch-milliseconds (`ltt`/`currentTs`; see `market_header.py:264` dividing by 1000 and `live_position_guard.py:442` comparing vs now_ms), but `backend/app/live/option_premium.py:138–139` compares `tick_ts` against a seconds `now_ts` with `max_age_sec=120` — the fresh-tick branch is unreachable, so `resolve_live_entry_ref_ltp` always returns None and every armed auto-entry refuses with `live_trade_error: live_entry_premium_unavailable_or_stale` (`auto_live.py:336–348`). Normalize units inside `resolve_premium` (treat ts > 1e11 as ms; better, normalize at the tick source and assert), bound the candle fallback's age, and add a regression test using a real ms tick. Then surface the refusal: `live_trade_error` and the `live_intended` dry-run audit field (auto_live.py:405–427) currently have ZERO readers — add them to the deployment live-status batch and render a red "entry refused: <reason>" chip on the Live page deployment strip and LiveSignals.

**3. [critical, M] Make the kill switch a true stop-all and serialize every flatten path.**
> `POST /live-broker/kill-switch` (routers/live_broker.py:2089–2196) flattens and sweeps GTTs but never disarms deployments, halts the engine, or trips the safety latch — an armed deployment re-enters on the next bar ("ALL FLAT" then new orders). Before flattening: `engine.halt("kill_switch")`, trip the SafetyConfig latch (persist across restarts), bulk-disarm `{"risk.live.armed": true}` (reuse the disarm block from deployments.py stop-all :760–778). After flattening: re-read both books once ~5s later and re-flatten anything new (catches in-flight fills and the post-fill OCO placed after the sweep at live_deploy_context.py:190). Add a per-tsym in-flight-exit claim (Mongo CAS doc with TTL or process-singleton dict) that `panic_squareoff_verified`, `square_position`, and the guard's `_square_and_record` must claim before placing any exit — second claimant skips with a logged reason (today the kill re-price loop never re-reads the position book, kill_switch.py:779–803, and `_cancel_all_working_for_scrip` cancels other paths' exits, auto_square.py:603). Serialize the kill route behind an asyncio.Lock. Also: re-check `engine.can_trade()` + deployment armed inside the executor immediately before `place_order` (executor.py:503–518) so a kill mid-pipeline blocks the transmit. Extend the kill report with the disarm/halt/re-sweep actions.

**4. [critical, M] Live page failure visibility: error states, staleness stamps, ErrorBoundary, always-mounted kill switch.**
> `LiveDataProvider.jsx` exposes per-slice `errors.*` but `LiveDashboard.jsx:341` destructures only data — add a page-level degraded banner driven by errors ("broker data stale since HH:MM — <last error>"), stamp `lastSuccess` per slice in `usePoll.js` and grey/badge panels older than 2× their cadence, and put an as-of clock on positions/P&L. Route every catch in components/live/ through `getApiErrorMessage()` (apiError.js — it already safely formats the FastAPI 422 array that today white-screens the page when Band % is cleared: LiveOrderTicket.jsx:269→297→764 renders raw objects as JSX children; also make `band_pct` Optional server-side). Wrap the Live route in a React ErrorBoundary whose fallback still renders KillSwitchPanel. Remove KillSwitchPanel's self-unmount (`KillSwitchPanel.jsx:124–125`) — render always, with a degraded "broker state UNKNOWN — kill will still attempt flatten" state. Fix ReconcileChip to format `{type, detail}` mismatches instead of `[object Object]` (LiveDashboard.jsx:280–281). Give "Stop ALL live" an honest confirm (it also squares ALL paper trades and pauses ALL deployments — deployments.py:748–794) and render the response summary.

**5. [critical→high, S] Recovery that re-runs: OAuth-triggered reconciliation + supervisor latch + auto-square re-arm.**
> `live_startup_recovery` (runtime.py:228–284) runs once at boot and silently skips when the Flattrade client can't be built — with daily OAuth and boot-before-login it effectively never runs (same boot-gap class as the candle-roller and exit-monitor incidents). Trigger it from the Flattrade OAuth callback (routers/live_broker.py:597–605) after token save; add it to the 20s supervisor loop (runtime.py:431–479) behind a "succeeded since last token refresh" latch; expose `/live-broker/recovery-status` and show a red strip while positions exist but recovery hasn't succeeded. In the same recovery, re-arm the 10-min auto-square timer from any persisted `armed` session with a future deadline (session doc survives in Mongo but `_schedule_auto_square`'s task dies with the process — live_broker.py:378, 411–420), and square immediately if the deadline passed. Make PositionMonitor's heartbeat reflect real liveness (last successful broker read + guard tick) instead of echoing server time (live_broker.py:2040). Also fix guard rehydrate visibility: include `source` in guard-status (stripped today at live_broker.py:1495–1505) and render "levels reset to 50% catastrophe stop" on rehydrated rows.

**6. [high, S] Guard fail-open and gate-split fixes: never lose a watched position.**
> In `live_position_guard.py:376–418`, the registry entry is removed BEFORE the square and never re-registered when the square fails or is a dry-run — one trigger permanently un-watches a live position, including the 15:00 EOD square, and the default `LIVE_GUARD_ARMED` unset returns `{"squared": False, "dry_run": true}` after a log line. Re-register with retry/backoff + an escalation event whenever squared=False or the call raises; treat dry-run identically. Cancel the carried `oco_al_id` on every removal path (flat-drop/age-out orphan the resting NRML OCO today — :317–328). Add a loud arm-state + ExecutionStateStrip warning when `LIVE_AUTOPLACE_ARMED=1` and `LIVE_GUARD_ARMED=0` (real entries, log-only exits — arm_state.py:54–57 models it as legal). Store `tsym` on the live-test session and make both square paths select by tsym (today both flatten the FIRST non-zero position in the whole account — live_broker.py:466–477, 1903–1914).

**7. [high, S] Optimizer survival-gate honesty pack: capital knob, costs enforcement, exit-grid guard, honest labels.**
> (a) Add "Trading capital ₹" to the Survivability panel → `option_config.sizing_config.capital` (today hard-wired ₹200,000 at optimizer.py:592/research.py:499 with no UI field — DD%/risk-of-ruin are mis-scaled 4× for a ₹50k account); show the capital basis on every survival badge and validate min_equity/ruin_floor against it. (b) When `survival_config.enabled`, require `option_config.cost_config.enabled` (validator checks the wrong flag at survival_validate.py:20–21; UI must lock the option-costs switch while Survivability is on). (c) 400 when `search_exit_controls` is on but `exit_mode != "option_levels"` (the grid is a silent no-op today: exit_cfg only consumed in the option_levels branch, option_backtest.py:507–512). (d) Relabel survival folds honestly ("stress slices") or hold them out of trial scoring, and stop passing `survival.total_return_pct` into deployment quality as `oos_return_pct` (optimizer.py:1412–1413 → deployment_quality.py:418). All four are S-effort validation/UI seams, no result changes for honest configs.

**8. [high, S] Optimizer robustness trio: cache-poisoning, grid crash-loop, cancel responsiveness.**
> (a) `parallel_eval.py`: the pool-None sequential fallback runs in the parent with the never-cleared module-global `_WORKER_CACHES` keyed only on (group, params) — a concurrent/later job silently reuses another frame's Series (pandas index-aligns → NaN tails → plausible-but-wrong best_params). Pass a fresh per-call cache dict in the fallback branch (optional param defaulting to the global for the fork path). (b) optimizer.py:1009: wrap the grid-branch `evaluate` in try/except, record the combo as disqualified, continue — today one raising combo fails the whole job and resume deterministically re-hits it forever. (c) Thread a `should_stop()` (cancel ∥ pause ∥ budget) check into every analyze-stage loop iteration — rerank stage-1 per candidate, survival per finalist, each exit-grid cell, heatmap/robustness rows — and finalize with partial results on cancel (plumbing exists for budget_hit); today cancel is read once at :1139 and a post-trials cancel is silently discarded. All three byte-identical for clean runs.

**9. [high, M] Deployment parity pack: carry the validation context to the deployment.**
> (a) `pretrade_profile`: the deploy wizard silently pins "Balanced" (LiveSignals.jsx:326) and `apply_opt_as_preset` (research.py:623–646) never captures the job's profile — carry it in preset config from both the optimizer and BacktestLab save paths, prefill the wizard, add a visible selector with a mismatch warning, and have deploymentReadiness flag profile mismatch between evidence and deployment. (b) `trade_window`: add trade_window_start/end to OptimizerStartReq defaulting to the live-effective 09:25–14:50 (deployments hard-block entries from 14:50 at deployment_evaluator.py:42–43 while the optimizer validates 09:25–15:00 — results include trades live can never take), thread into all run_backtest calls in optimizer.py/wfo.py, persist into saved runs and presets, surface in the panel. (c) Stamp presets with the evaluation stage reached (`spot_only|option_ranked|survival_passed` + terminal job status) and render it in SavedPresets and deploy wizard step 1 so cancelled-job spot-only presets stop looking identical to survival-validated ones (research.py:618 allows apply-as-preset from cancelled/failed/paused).

**10. [high, M] Authored→deployed guided pipeline: next steps, lifecycle chips, informed arming.**
> (a) After a successful wizard install (AuthoringWizard.jsx:293–319), replace the toast with a next-step panel: "Backtest now" → add `?strategy=<id>` deep-link support to BacktestLab (it handles ?run=/?preset= today), "Optimize" → `/optimizer?strategy=<id>`, plus a 3-line pipeline explainer (backtest → save preset → deploy; deployments only accept presets/backtest_runs — strategy_deployments.py:9 — say so). (b) Add a per-strategy pipeline endpoint (aggregate counts + latest timestamps from backtest_runs/optimization_jobs/presets/strategy_deployments by strategy_id) and render Authored/Backtested/Optimized/Paper/Live stage chips on StrategyCard with CTAs (also kills SavedPresets' N+1 readiness fan-out). (c) In the live ARM dialog (DeployToLivePanel.jsx:321–357; backend arm route deployments.py:820–903 checks zero performance evidence), display forward metrics + quality warnings (sessions, trades, WR, forward P&L, drift state) with a hard advisory when forward P&L ≤ 0 or sessions < threshold — advisory, not blocking. (d) Fix the two authoring paper-cuts in the same pass: invalidate the stale feasibility REJECT lock when input changes (AuthoringWizard.jsx:824), and lift `complete_structured`'s max_tokens=4000 default (llm_client.py:92) that silently defeats the documented 8192 Gemini truncation fix.

### Near-miss items (worth queueing after the ten)
- Partial-fill modeling: start the order websocket or poll-to-terminal + journal true-up + OCO resize (L12 — L effort, prerequisite for accurate P5/P6 numbers).
- Idempotency: client-supplied key on `/order/place` + CAS single-shot (L14/L16) and the approve-ambiguity UNKNOWN state.
- P1 degraded-mode banner and P2 risk dashboard + P&L circuit breaker (both become S/M once items 1–5 land, and P3's preflight composes them).
- Plugin lifecycle: delete tombstones vs git resurrection + failed-plugin delete fix (S9/S10) and the reload button (S13).
- In-loop option-aware optimizer objective and WFO per-window re-rank (O1 increment 2 — the single biggest research-quality upgrade, L effort).

---

## Appendix — verification record

- Live: 36 findings — 16 agent-verified (14 CONFIRMED, 2 ADJUSTED), 4 manually re-verified line-by-line (L1 conflation + kill all_flat, L7 kill-route grep for disarm/halt/trip = 0 hits, L11 ms-vs-s units end-to-end, L22 dashboard `errors` never destructured), 16 corroborated.
- Optimizer: 28 findings — all 10 high adversarially CONFIRMED; 18 medium/low corroborated.
- Library: 24 findings — 4 manually verified (S1 max_tokens default, S6 spec-mapper column list, S9 delete-ghost return-value ignore, S17 preset-only deploy), 20 corroborated.
- **Refuted across all passes: 0.** Adjustments: L10's GTT-idempotency half downgraded to medium; L14/L16 details tightened (cid scope, approvals UI absence confirmed).
- Method note: findings marked ◻ cite exact file:line evidence recorded by the reporting agent; re-check the cited lines before building, as with any audit.
