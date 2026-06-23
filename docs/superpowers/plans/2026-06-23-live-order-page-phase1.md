# Live Order Page — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use
> checkbox (`- [ ]`). Each safety-critical unit gets a dedicated adversarial-audit subagent.

**Goal:** Make supervised live trading **reliable** — a single order choke-point (exchange-aware,
tick-rounded, freeze-split, product-pinned), a cancel-first exit that can't hit the naked-short
margin trap, a software SL/TP/trailing monitor, and an exchange-aware direct ticket where **every
order is user-approved**. This eliminates the three live failures (entry-tick, kill-tick,
square-margin).

**Architecture:** Generalize + consolidate existing live modules; no resting SL. Spec:
`docs/superpowers/specs/2026-06-23-live-order-page-design.md`.

**Tech Stack:** FastAPI + Motor (async), pytest (host tests vs MockNoren), React 19 + craco.

**Branch:** `feat/live-order-page` (off `main`).

**Test runner:** `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/<file> -v`

---

# PHASE 1A — the choke-point (pure, host-testable)

## Task P1.1: Exchange rules engine

**Files:** Modify `backend/app/live/flattrade_symbol.py`; Test `tests/test_live_exchange_rules.py`.

- [ ] **Step 1 — failing tests:** `rules_for("NIFTY")` → `{exch:"NFO", lot_size:65, freeze_qty:1800,
  tick:0.05, products:["NRML","MIS"], price_types:["LIMIT","MARKET","SL-LMT"], expiry_cadence:"weekly_tue"}`;
  BANKNIFTY (NFO,30,600,monthly); SENSEX (BFO,20,1000,weekly_thu); unknown underlying → None;
  case-insensitive; CO/BO NOT in products (v1 off); SL-MKT NOT in price_types.
- [ ] **Step 2 — implement:** add `EXCHANGE_RULES` dict + `def rules_for(underlying) -> dict|None`
  (uppercases, looks up; returns a copy). Reuse the existing `UNDERLYING_SPEC` exch/lot mapping (keep
  them consistent — `rules_for` is the superset). Add a helper `def market_allowed(rules, *, expiry_date,
  strike, moneyness=None) -> bool` returning True for now (near-expiry liquid default) — the strict
  liquidity predicate is a documented Phase-3 refinement; in Phase 1 MARKET is allowed but the ticket
  defaults to LIMIT.
- [ ] **Step 3 — commit** `feat(live): exchange rules engine (rules_for: products/price-types/freeze/tick per exchange)`.

## Task P1.2: Freeze-qty split

**Files:** Modify `backend/app/live/order_builder.py`; Test `tests/test_live_order_builder.py` (extend).

- [ ] **Step 1 — failing tests:** `slice_to_freeze(65, 1800)` → `[65]`; `slice_to_freeze(3600, 1800)`
  → `[1800,1800]`; `slice_to_freeze(1900, 1800)` → `[1800,100]`; `slice_to_freeze(20, 1000)` → `[20]`;
  `qty <= 0` → `[]`; `qty > 10*freeze_qty` → raises `ValueError` (hard cap); non-int/freeze<=0 → ValueError.
- [ ] **Step 2 — implement** `def slice_to_freeze(qty: int, freeze_qty: int) -> list[int]`: validate;
  `n = ceil(qty/freeze_qty)`; return n chunks each ≤ freeze (remainder last). Sum of chunks == qty.
- [ ] **Step 3 — commit** `feat(live): slice_to_freeze (freeze-qty order splitting)`.

## Task P1.3: The generalized choke-point `validate_and_build`

**Files:** Modify `backend/app/live/order_builder.py`; Test `tests/test_live_validate_and_build.py`.

Generalizes `build_intent` for multi-child + exchange rules + order types (LIMIT/MARKET/SL-LMT).
Keep `build_intent` working (the executor still uses it; `validate_and_build` wraps the same pieces).

- [ ] **Step 1 — failing tests** (inject a sync fake_search returning a real NIFTY scrip row with
  `ti:"0.05"`): `validate_and_build(ticket) -> (child_intents|None, verdicts)` where ticket =
  `{underlying, strike, option_type:"CE"/"PE", side:"B"/"S", expiry_date, lots, order_type:
  "LIMIT"/"MARKET"/"SL-LMT", product:"MIS"/"NRML", ref_ltp, band_pct, levels, client_order_id, search_fn}`:
  - LIMIT BUY 1 lot NIFTY → 1 child, prctyp LMT, prc = tick-rounded (up), product MIS, qty 65, all verdicts ok.
  - **exchange validation:** product "CO"/"BO" → blocked (not in NFO products v1); a BFO (SENSEX) ticket
    with product "CO"/"BO" → blocked; order_type "SL-MKT" → blocked.
  - **MARKET:** order_type MARKET → prctyp "MKT", prc == 0.0, NO price_band/tick verdict (skipped), but
    fat_finger/jdata still run; passes.
  - **SL-LMT:** order_type SL-LMT with levels stop → prctyp SL-LMT, trgprc = tick-rounded stop, prc set.
  - **freeze split:** lots large enough that qty > freeze (e.g. NIFTY 30 lots = 1950 > 1800) → 2 children
    [1800,150], each a valid OrderIntent with its own client_order_id; verdicts ok.
  - any failed verdict / unknown underlying / bad order_type → `(None, verdicts)`.
- [ ] **Step 2 — implement:** `rules_for(underlying)` → validate order_type ∈ price_types + product ∈
  products (named verdicts) → `resolve(contract)` (now returns `tick`) → `qty = lots*lot_size` →
  `slice_to_freeze(qty, freeze_qty)` → per child: compute prc (LIMIT: marketable `round_to_tick(ref*(1±buf),
  tick, dir)`; MARKET: prc=0.0; SL-LMT: trgprc from `resolve_premium_levels`, tick-rounded) → build
  `OrderIntent(prctyp, prd=product, exch, tsym, qty=child, prc, trgprc, remarks=cid+childIdx)` →
  run verdicts (symbol/exchange/price_finite/price_band[skip MKT]/fat_finger/jdata). Return all-or-None.
- [ ] **Step 3 — commit** `feat(live): validate_and_build choke-point (exchange + tick + freeze-split + order-types)`.
- [ ] **ADVERSARIAL AUDIT (P1.3 — HARDEST):** *can any order skip tick-rounding (LMT/SL)? can a
  blocked product/price-type (CO/BO on BFO, SL-MKT) pass? does MARKET ever carry a non-zero prc / skip
  the fat-finger gate? does freeze-split ever lose/gain qty or exceed freeze? is product ever not pinned?
  can a child intent be built when a verdict failed?* Every priced order must be tick-valid or MKT-with-prc-0.

---

# PHASE 1B — the reliable exit (vs MockNoren)

## Task P1.4: `square_position` cancel-ALL-working + confirm

**Files:** Modify `backend/app/live/auto_square.py`; Test `tests/test_live_auto_square.py` (extend).

Fixes the ₹2.16L margin reject: cancel **every** working order for the scrip (not just the entry),
confirm terminal, then close.

- [ ] **Step 1 — failing tests** (MockNoren with a resting SL order + the entry both working for the
  tsym): `square_position` now (a) fetches `client.order_book()`, (b) cancels EVERY order for this
  `tsym` whose status ∉ TERMINAL (the SL backstop included), (c) polls until those are terminal (bounded),
  (d) re-reads netqty, (e) places ONE close. Test: a resting SL + entry → both cancelled before the
  close place; the close is the only remaining sell; a cancel that fails is tallied but the close still
  proceeds; netqty re-read after cancels; reconcile netqty==0 (mock returns flat) → squared True.
- [ ] **Step 2 — implement:** add `async def _cancel_all_working_for_scrip(client, tsym) -> list` (order_book
  → filter tsym & not-terminal → cancel each → poll-confirm via order_book up to N tries). Call it at the
  TOP of `square_position` (replacing the single working_norenordno cancel). Keep direction/tick/never-raise.
- [ ] **Step 3 — commit** `fix(live): square_position cancels ALL working orders for the scrip first (no naked-short margin)`.
- [ ] **ADVERSARIAL AUDIT (P1.4):** *can a working SL survive the close (→ margin reject)? does the
  confirm-poll have a bounded timeout (no hang)? if a cancel never confirms, does it still avoid placing
  a double-sell, or halt? netqty re-read correct after a partial SL fill?*

## Task P1.5: Live SL/TP monitor

**Files:** Create `backend/app/live/live_sl_monitor.py`; Test `tests/test_live_sl_monitor.py`.

Software-monitored per-position exits on live LTP (no resting orders).

- [ ] **Step 1 — failing tests** (pure decision logic, injected LTP + position state): `evaluate_position(pos,
  ltp, *, now) -> exit_reason|None` reusing `execution_policy.tick_exit_reason` for stop/target, plus
  trailing (high-water ratchet) + breakeven (move stop to entry after +X) + time-stop. Test: LTP ≤ stop →
  "stop_hit"; LTP ≥ target → "target_hit"; trailing ratchets the stop up on new highs, exits when LTP drops
  below the trailed stop; breakeven moves stop to entry after the trigger; stale LTP (older than max-age) →
  None (don't act on stale); none of the above → None.
- [ ] **Step 2 — implement** a `LiveSLMonitor` class mirroring `backend/app/live_exit_monitor.py`'s
  scaffolding (start/stop/status, market-hours, ~1.5s poll, staleness guard) but its exit ACTION calls
  `auto_square.square_position` (cancel-first) for the breached position. Hold per-position
  {entry, stop, target, trailing cfg, breakeven cfg, high_water, deadline}. The `evaluate_position` pure
  fn is the tested core; the loop is thin glue.
- [ ] **Step 3 — commit** `feat(live): software SL/TP/trailing/breakeven monitor (no resting orders)`.
- [ ] **ADVERSARIAL AUDIT (P1.5):** *does trailing ever ratchet DOWN? does a stale/NaN LTP trigger a
  spurious exit or a missed one? does breakeven ever move the stop the wrong way? can two exits fire for
  one position (idempotent dereg after first breach)? stop-first when both stop+target in one tick?*

---

# PHASE 1C — approval gate + routes + wiring (vs MockNoren)

## Task P1.6: Approval store + one-shot token

**Files:** Create `backend/app/live/approval_store.py`; Test `tests/test_live_approval.py`.

- [ ] **Step 1 — failing tests** (injectable collection, DB-free like idempotency): every built order
  lands as `pending_approval`; `approve(approval_id) -> token` mints a **one-shot** token bound to that
  approval's intent-hash; `consume_token(token, intent_hash) -> bool` returns True ONCE then False
  (single-use) and False on a hash mismatch (can't reuse a token for a different order) or unknown token;
  `reject(approval_id)` removes it; expired approvals (older than N) can't be approved. Restart-survivable.
- [ ] **Step 2 — implement** `ApprovalStore` (Mongo `live_approvals`): record_pending(intent, dryrun_verdicts),
  list_pending, approve→token (random hex, store hash+state used=False), consume(token, hash)
  (atomic find-and-set used=True only if hash matches + not used), reject, expire.
- [ ] **Step 3 — commit** `feat(live): approval queue + one-shot approval token`.
- [ ] **ADVERSARIAL AUDIT (P1.6):** *can a token be consumed twice (double order)? reused for a DIFFERENT
  intent (hash bypass)? can an order transmit without a token? token guessable? approve a stale/expired one?*

## Task P1.7: Routes + executor extension

**Files:** Modify `backend/app/routers/live_broker.py`, `backend/app/live/executor.py`; Test
`tests/test_live_order_routes.py`.

- [ ] **Step 1 — failing tests** (TestClient + injected mocks): `GET /order-rules?underlying=NIFTY` →
  the rules table; `POST /order/build` → `validate_and_build` result (child jdatas + verdicts), records a
  `pending_approval`, returns approval_id, NO transmit; `GET /order/approvals` lists pending; `POST
  /order/approvals/{id}/approve` → token; `POST /order/approvals/{id}/reject` removes; `POST /order/place`
  `{approval_id, token}` → executor places ONLY if mode allows + token consumes + can_trade (the only
  transmit; one place_order per child); a bad/used token → blocked, zero place. `POST /order/square`
  (cancel-first) + kill route reused.
- [ ] **Step 2 — implement:** extend `executor` to accept the validated child intents + the approval
  token (consume it as a NEW gate before transmit; keep all existing gates: mode/can_trade/idempotency/
  arm-or-abort). Wire the routes. Grep-confirm `place_order` reachable ONLY via the executor + the
  approval-token gate.
- [ ] **Step 3 — commit** `feat(live): order routes (rules/build/approvals/place) + approval-gated executor`.
- [ ] **ADVERSARIAL AUDIT (P1.7):** *is /order/place the only entry transmit, and only with a valid
  one-shot token + mode + can_trade? can the build route ever transmit? multi-child: all children
  approved by one token, or one token per child (decide + enforce)? bypass any gate?*

---

# PHASE 1D — frontend

## Task P1.8: Exchange-aware ticket + approval queue + position monitor

**Files:** `frontend/src/lib/api.js`; `frontend/src/components/live/OrderTicketV2.jsx`,
`ApprovalQueue.jsx`, `PositionMonitorV2.jsx`; mount on `LiveTrading.jsx`.

- [ ] api: getOrderRules, buildOrder, listApprovals, approveOrder, rejectOrder, placeApprovedOrder,
  squarePosition, killSwitch.
- [ ] OrderTicketV2: fetch `/order-rules` for the selected underlying → enable/disable order-type/product
  controls per exchange (hide CO/BO on BFO; default LIMIT); inputs underlying/strike/CE-PE/side/lots/
  order-type/product/validity; **Build (dry-run)** → show child split + verdicts; on all-pass → the order
  goes to the **Approval queue** (not direct place).
- [ ] ApprovalQueue: pending orders with details + verdicts + **Approve / Reject**; Approve → place via token.
- [ ] PositionMonitorV2: live positions + per-position stop/target/trailing distance + Square-now + Kill.
- [ ] `CI=false npm run build` green. Commit `feat(live-ui): exchange-aware ticket + approval queue + position monitor`.

---

# PHASE 1 EXIT GATE

**L-P1 EXIT GATE (adversarial audit + full suite):** prove end-to-end vs MockNoren: (1) the choke-point
is the sole order builder — no tick/exchange/freeze/product bypass; (2) no order transmits without a
valid one-shot approval token + mode + can_trade; (3) the exit cancels-all-working-first → no naked-short
margin path; (4) the software monitor exits correctly (trailing never ratchets down, no double-exit);
(5) every L0–L3 invariant still holds. Run the entire `tests/test_live_*.py` green. Then surface to the
user for a supervised 1-lot live validation (place via approval, exit via the monitor/square).

---

## Self-review (author)
- **Spec coverage:** §3 choke-point→P1.3; §4 rules→P1.1; §6 square→P1.4; §5 monitor→P1.5; §2 approval→
  P1.6/P1.7; ticket/queue→P1.8. (§7 overall-controls + §5 GTT = Phase 2/3, not here.) ✓
- **Type consistency:** `rules_for`/`slice_to_freeze`/`validate_and_build`/`square_position`/
  `LiveSLMonitor`/`ApprovalStore` reused across tasks; reuses `build_intent`/`round_to_tick`/`resolve`
  (now returns `tick`)/`execution_policy`/`auto_square`/`executor` gates verbatim. ✓
- **Ordering:** choke-point (P1.3) before routes (P1.7); square (P1.4) before monitor (P1.5, which calls it). ✓
- **Open items:** the strict MARKET-liquidity predicate (Phase 3); confirm lot/freeze constants live;
  whether one token covers all freeze-children or one-per-child (P1.7 decides — recommend one token →
  all children of one parent order, since they're one logical order).
