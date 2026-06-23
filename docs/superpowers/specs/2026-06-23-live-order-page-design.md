# State-of-the-Art Live Order Page — Design Spec

**Date:** 2026-06-23
**Status:** Design (pending user review)
**Predecessors:** L0–L3 Safe Core (`backend/app/live/`, on `main`); the exit-redesign research (`wmxaiy0w7`) and exchange/GTT/overall-settings research (`wvoa0qz2x`).

---

## 1. Goal

A production live-trading order page for Flattrade (NSE/NFO + BSE/BFO Indian index options) that supports **direct order placement** and **strategy deployment**, full **exchange-aware order types**, **GTT**, software-monitored **per-position and overall** risk controls (SL / target / trailing / breakeven / re-entry), and a **single order choke-point** — with **every trade approved by the user** (autonomous later, once matured).

This redesign exists because three live exits failed in three different ways (entry-tick reject, kill-tick reject, square-off margin reject). Root causes: order prices built **outside** the one tick-aware builder, and a **resting SL sell** colliding with a square sell → naked-short margin. The architecture below removes both classes structurally.

### Non-goals (v1)
- Fully-automatic (no-approval) execution — gated behind a maturity switch, off by default.
- CO/BO orders — superseded by software-monitored exits (see §3); ship behind a verified-capability flag, off by default.
- Equity/cash, multi-broker, non-index instruments.

---

## 2. Execution model — Supervised-Autonomous (hard policy)

Deployed strategies and the direct ticket both produce **order intents**; **every intent requires explicit user approval before transmit** (a non-bypassable, code-enforced gate). Approval mints a **one-shot approval token** the executor consumes (extends the existing single-shot `mode` pattern). A per-strategy **graduation switch** (locked off) is the only path to later auto-approval within a preset risk envelope.

The L3 guarded executor stays the sole *entry* transmit; a single cancel-first `square_position` is the sole *exit* transmit. Approval sits in front of both.

---

## 3. The order choke-point (the core safety fix)

Exactly ONE path builds + validates every order — entry, exit, SL, square, kill, GTT — so tick-rounding/exchange-rules/freeze-split/product-pin can never be missed.

**`order_builder.validate_and_build(ticket) -> (child_intents: list, verdicts: list)`** (generalizes today's `build_intent`), applying in order:
1. **Exchange-aware order-type validation** — look up `rules_for(underlying)` (§4); reject if `prctyp`/`prd` not in the enabled set (BO/CO on BFO, SL-MKT on index opts, MKT on an illiquid strike) with a named verdict.
2. **Tick-round** (LMT/SL only) via the existing Decimal `round_to_tick` (BUY up / SELL down / trigger nearest). For `MKT`, force `prc=0.0`, skip tick/band, KEEP the liquidity gate.
3. **Freeze-qty split** — `qty = lots × lot_size`; if `> freeze_qty`, split into `ceil(qty/freeze_qty)` child intents (each ≤ freeze; remainder on the last). **Hard-reject `qty > 10×freeze_qty`.** New pure `slice_to_freeze(qty, freeze_qty) -> [int]`. Each child gets its own `client_order_id`; approval shows the parent + N children.
4. **Product-pin** — `prd` pinned to the validated product (MIS for the test path, NRML for GTT-backed; **exits read the position's prd and match it** — mismatch = a second position = margin trap).
5. **Price-band / DPR clamp** + **lot-multiple** check.
6. **Pre-trade risk caps** (fat-finger, day-envelope) — reuse `safety.py`.

The frontend fetches the rules once and renders enabled/disabled controls; the backend re-validates against the same table (never trust the client).

---

## 4. Exchange rules engine

Add `EXCHANGE_RULES` + pure `rules_for(underlying) -> {exch, products[], price_types[], lot_size, freeze_qty, tick, market_liquidity_predicate}` next to `UNDERLYING_SPEC` in `flattrade_symbol.py`. **Exchange is derived from the underlying, never user-picked.**

| | NIFTY/NFO | BANKNIFTY/NFO | SENSEX/BFO |
|---|---|---|---|
| lot size | 65 | 30 | 20 |
| freeze qty | 1,800 | 600 | 1,000 |
| tick | 0.05 | 0.05 | 0.05 |
| expiry | Tue weekly | monthly (last Tue) | Thu weekly |
| products | NRML, MIS (CO/BO flag-gated) | NRML, MIS (CO/BO flag-gated) | NRML, MIS (**no CO/BO**) |
| price types | LIMIT (default), MARKET (if-liquid), SL-LMT | same | LIMIT, MARKET (if-liquid), SL-LMT |
| SL-MKT | disabled (index opts) | disabled | disabled |
| AMO | LIMIT-only | LIMIT-only | LIMIT-only |

`GET /live-broker/order-rules` serves this to the ticket. (Lot/freeze constants flagged to re-verify vs live broker before relying on them.)

---

## 5. Exit / SL architecture — software-monitor primary + GTT disaster-backstop

**Decision (research-confirmed): never rest a standing SL-LMT.**

1. **Software monitor is authoritative.** Extend the existing `live_exit_monitor.py` (`LiveExitMonitor`, already polls positions ~1.5s for paper) to mark **live** positions: per-position SL / target / trailing / breakeven / time-stop via the parity-tested `execution_policy` + `exit_controls` deciders (byte-identical to the backtest). On a breach it routes through the **cancel-first square** (§6). No resting order ⇒ no margin block, no double-order trap, full trailing/breakeven/OCO.
2. **OCO-GTT backstop — NRML positions only** — placed at confirmed entry purely as a *PC-died* catastrophe net. GTT blocks no margin and doesn't sit in the live order book until triggered, so it's immune to the naked-short trap. **The instant the software exit fills, cancel the GTT.** Software fill is source of truth; reconcile the GTT book on reconnect. (GTT is NRML/CNC only — not MIS. Gate on `prd=="M"`; re-arm each session; re-check before weekly expiry; confirm Flattrade OCO on weekly with one live test before depending on it.)
3. **Reject resting SL-LMT as a standing stop.** (The existing `_make_arm` entry×0.7 SL is acceptable *only* as a same-session intraday net on the ≤10-min MIS test path; do not generalize it.)

---

## 6. Square-off — the canonical reliable close

Every close path (monitor exit, overall-SL exit, manual Square-now, kill, arm-abort) uses this sequence (fixes the ₹2.16L margin reject):
1. Fetch the order book; select **every** working order for this exact tsym (incl. the SL/GTT), not just the entry.
2. Cancel each; **poll-confirm terminal** before placing the close.
3. Re-read net qty from the position book (a working order may have partially filled).
4. Place **one** marketable order via the choke-point — correct direction, tick-rounded (or MKT where liquid), **same product type**, chase via `modify_order` if unfilled in N s.
5. Reconcile `netqty == 0`; else retry once then halt + alert.

`auto_square.square_position` is upgraded to cancel-ALL-working-for-scrip (today it cancels only the entry). `kill_switch.panic_squareoff` already cancels-all — add the confirm-terminal poll + route its intents through the choke-point.

---

## 7. Overall & broker-level controls (AlgoTest parity)

New pure `overall_controls.py` (sibling of `exit_controls.py`, host-testable), evaluated each tick on **basket aggregate** (Σ leg MTM, Σ entry premium). On trigger → square ALL legs (cancel-first), overriding per-leg state.

- **Overall SL / Target** — MTM mode (absolute ₹) or Total-Premium-% mode (`threshold = pct/100 × basket_premium`).
- **Trailing (monotonic floor):**
  - **Lock** — when `mtm ≥ Y` → `floor = X`; exit when `mtm < X`.
  - **Lock & Trail** — `floor = X + ⌊(mtm − Y)/A⌋·B`; exit when `mtm < floor`.
  - **Overall Trail SL** — `sl = −S₀ + ⌊profit/A⌋·B`; exit when `mtm ≤ sl` (MTM or premium-% units).
- **Re-entry** (toggled, only with Overall SL/Target on, max 5): on exit, decrement budget, **re-resolve the strike at current spot** (fresh ATM), optional Reverse (Buy↔Sell); **RE-ASAP** (immediate) / **RE-MOMENTUM** (defer until a % move). Overall and per-leg re-entry budgets are independent (avoid Tradetron's leg-TSL-loops footgun).
- **Scope split:** `overall` = per-deployment (replays the source-run config); `broker_level` = across ALL live deployments, set live, **re-armed daily** (layers on `kill_switch.SafetyConfigStore`).

Per-leg SL/target still run; whichever threshold (leg or overall) hits first wins; overall always squares the whole basket.

---

## 8. Components / modules (mapped to existing code)

**Backend** (`backend/app/live/` unless noted):
- `flattrade_symbol.py` — **ADD** `EXCHANGE_RULES` + `rules_for()`.
- `order_builder.py` — **GENERALIZE** to `validate_and_build()` + `slice_to_freeze()`; keep `round_to_tick`.
- `executor.py` — **EXTEND** for validated multi-child intents + ticket product/price-type + the approval-token gate; keep all gates (mode, fresh dry-run, can_trade, idempotency, arm-or-abort).
- `gtt.py` — **NEW** `build_gtt_intent`/`build_oco_intent`/`cancel_gtt` (PiConnect REST direct; the pip wheel lacks GTT routes).
- `overall_controls.py` — **NEW** §7 pure formulas.
- `live_exit_monitor.py` — **EXTEND** to mark live positions (per-position + overall) → cancel-first square + cancel GTT. The §5 primary.
- `auto_square.py` — **UPGRADE** `square_position` to cancel-ALL-working-for-scrip (§6).
- `kill_switch.py` — **REUSE** for Stop-all + `broker_level` guardrails.
- `execution_policy.py` / `exit_controls.py` — **REUSE** (parity deciders).
- `approval_store.py` — **NEW** approval queue + one-shot tokens.
- `routers/live_broker.py` — **ADD** `GET /order-rules`, `POST /order/build` (multi-child dry-run + verdicts), `GET/POST /order/approvals`, `POST /order/place` (generalized, consumes token), `POST /gtt` + `DELETE /gtt/{id}`, `GET/PUT /overall-settings`, `GET/PUT /broker-level-settings`, `POST /deploy`. Keep mode/square/kill/test-session/dry-run.
- deployment system (`deployments.py`, evaluator) — **EXTEND** with a `live` mode that routes signals → the approval queue → choke-point.

**Frontend** (`frontend/src/components/live/`): `OrderTicket` (rebuilt, exchange-aware), `OverallSettingsPanel` (SL/target/trailing/re-entry), `ApprovalQueue`, `PositionMonitor` (live SL/TP/trailing/distance), `DeploymentsStrip`, `RiskEnvelopeHUD` + `KillSwitch`, `GttBook`. Reuse `TradeBlotter`, `AccountHero`, `PnlCalendar`. Libs already present: recharts, lightweight-charts.

---

## 9. Safety invariants
1. No order transmits without (mode-allowed) + a valid one-shot approval token.
2. Every priced order passes the single choke-point (tick/exchange/freeze/product/band) — structurally unbypassable.
3. No resting standing SL; software monitor is authoritative; GTT backstop NRML-only and cancelled on software fill.
4. Every close = cancel-all-working → confirm → net-off close (same product) → reconcile flat.
5. Overall/broker-level SL/target/trailing square the basket and trip the latch.
6. Exchange rules enforced server-side (BO/CO blocked on BFO; MKT only when liquid).
7. Fail-closed everywhere (unknown → block); kill switch total.

---

## 10. Phased build plan
- **Phase 1 — The choke-point + reliable exit + direct ticket (MVP).** `rules_for` + `validate_and_build` + `slice_to_freeze`; `square_position` cancel-all; the live `LiveExitMonitor` (per-position SL/TP/trailing/breakeven) + cancel-first square; the exchange-aware direct OrderTicket + approval queue + one-shot token; routes `order-rules`/`order/build`/`approvals`/`order/place`/`square`/`kill`. **This alone makes supervised live trading reliable and kills all three failure modes.**
- **Phase 2 — Overall controls + strategy-deploy-to-live.** `overall_controls.py` (overall SL/target/trailing/re-entry) + the OverallSettingsPanel; deployment `live` mode → approval queue.
- **Phase 3 — GTT backstop + broker-level + polish.** `gtt.py` OCO-GTT for NRML; broker-level settings; option chain + payoff; audit log; graduation switch.

Each phase: spec→plan→multi-agent build with adversarial audits; live-validate Phase 1 (one supervised 1-lot, exit via the monitor) before Phase 2.

---

## 11. Open items (verify at plan time)
- Confirm lot/freeze constants + CO/BO/MKT availability per exchange against the live account (one test order each).
- Confirm Flattrade GTT/OCO endpoints + weekly-option eligibility.
- The exact AlgoTest "Overall Trail SL" base (doc has a typo — implement the *mechanic* `sl += B per A profit`).
