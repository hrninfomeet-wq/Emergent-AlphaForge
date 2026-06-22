# Live Execution Safe Core (L0–L2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Each safety/logic task gets an implementer + a spec/quality review + an **adversarial-audit subagent** that tries to break the control. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build the host-testable, **zero-real-order** Flattrade execution foundation: broker connectivity, Upstox→Noren symbol resolver, read-only account, the pre-trade safety engine, kill switch, order state machine, reconciliation, and a mock broker — all proven by tests + adversarial audits before any real order (L3).

**Architecture:** A new bounded `backend/app/live/` module group, async (mirrors `upstox_client.py`). All order-placement paths route through a `BrokerClient` Protocol whose **only** real implementation in this plan is `MockNoren`; read endpoints hit the real Flattrade API. Live exit *decisions* reuse `execution_policy.py` (the parity invariant). Frontend: a display-only `/live-trading` page + safety panel + dry-run ticket.

**Tech Stack:** Python/FastAPI + MongoDB (motor) + httpx + pytest; React 19 + Tailwind dark tokens + recharts.

**Spec:** [docs/superpowers/specs/2026-06-21-live-execution-safe-core-design.md](../specs/2026-06-21-live-execution-safe-core-design.md)

---

## Conventions & confirmed environment facts

- **Worktree (set up at execution start):** built off `main` in a sibling worktree (e.g. `C:/Users/haroo/af-wt-live`), `node_modules` junctioned, the running Docker app on `main` left undisturbed. Absolute paths; git via `git -C <wt>`.
- **Pytest (main venv):** `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest "<abs path>" -v`. No `conftest.py`; pure-logic tasks use pytest; DB/route tasks verify via `asyncio.run` against the running Mongo.
- **FastAPI app:** `backend/server.py` (`from server import app`); routers mounted under `/api`. New router registered in `server.py` alongside the others.
- **Reuse (read these before implementing):** `app/execution_policy.py` (exit decider — parity), `app/upstox_client.py` (client + Mongo token-store + `get_connection_status` pattern to MIRROR), `app/routers/broker.py` (Upstox auth/status routes to mirror), `app/deployment_kill_switch.py` (switch logic to promote), `app/paper_squareoff.py` + `routers/deployments.py` `/stop`,`/stop-all` (square-off template), `app/schemas.py`.
- **Hard Flattrade facts (from the spec §4):** REST `POST https://piconnect.flattrade.in/PiConnectAPI/<Route>` body `jData=<json>&jKey=<token>`; order types **LMT / SL-LMT only**; `prd='I'`, `ret='DAY'`, `trantype` B/S; `SL-LMT` requires `trgprc`+`prc`; `qty` string, exact lot multiple; `om` WS fields `status`/`reporttype`/`rejreason`/`fillshares`/`avgprc`; daily token regen after 6 AM IST; static IP; <10 orders/sec.
- **NON-NEGOTIABLE:** no code path outside `app/live/` may import the order-placement client; in this plan the real client's order methods are exercised **only by `MockNoren`** — no task places a real order.
- **Commits:** per task, with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; honor the user's per-changeset cadence.

## File structure

`backend/app/live/__init__.py`, `broker_protocol.py` (the `BrokerClient` Protocol + `OrderIntent`/`OrderResult` dataclasses), `flattrade_client.py` (real async client), `mock_noren.py` (mock client), `flattrade_token.py` (daily token store), `flattrade_symbol.py` (resolver), `safety.py` (pre-trade checks), `idempotency.py` (client-order-id + intent store), `order_sm.py` (state machine), `reconcile.py` (diff), `kill_switch.py` (panic + guardrails). `backend/app/routers/live_broker.py`. Frontend `frontend/src/pages/LiveTrading.jsx` + `frontend/src/components/live/*`. Tests under `tests/test_live_*.py`.

---

# PHASE L0 — connectivity, symbol resolver, read-only, page shell

## Task L0.1: Broker Protocol + data contracts

**Files:** Create `backend/app/live/__init__.py` (empty), `backend/app/live/broker_protocol.py`; Test `tests/test_live_protocol.py`.

- [ ] **Step 1 — failing test:**
```python
import sys; from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "backend"))
from app.live.broker_protocol import OrderIntent, OrderResult, ORDER_STATES

def test_order_intent_defaults_and_validation_shape():
    oi = OrderIntent(client_order_id="cid1", trantype="B", prctyp="LMT", exch="NFO",
                     tsym="NIFTY25000CE", qty=65, prc=158.5, prd="I", ret="DAY")
    assert oi.trgprc is None and oi.prd == "I" and oi.ret == "DAY"
    d = oi.to_jdata(uid="U1", actid="U1")
    assert d["prctyp"] == "LMT" and d["qty"] == "65" and d["ordersource"] == "API"
    assert "trgprc" not in d  # omitted when None

def test_sl_lmt_jdata_includes_trigger():
    oi = OrderIntent(client_order_id="c", trantype="S", prctyp="SL-LMT", exch="NFO",
                     tsym="X", qty=65, prc=119.0, trgprc=120.0, prd="I", ret="DAY")
    d = oi.to_jdata(uid="U1", actid="U1")
    assert d["trgprc"] == "120" and d["prctyp"] == "SL-LMT"

def test_order_states_constant():
    assert {"INTENT","SUBMITTED","ACKED","OPEN","TRIGGER_PENDING","PARTIAL","COMPLETE","REJECTED","CANCELED"} <= set(ORDER_STATES)
```
- [ ] **Step 2:** run → FAIL (no module).
- [ ] **Step 3 — implement** `broker_protocol.py`:
```python
"""Broker-agnostic order contracts + the BrokerClient Protocol. The ONLY real
order-placing implementation in the L0-L2 plan is MockNoren; FlattradeClient's
order methods stay untested-against-real until L3."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

ORDER_STATES = ("INTENT", "SUBMITTED", "ACKED", "OPEN", "TRIGGER_PENDING",
                "PARTIAL", "COMPLETE", "REJECTED", "CANCELED")
ALLOWED_PRCTYP = ("LMT", "SL-LMT")     # Flattrade API: market/CO/BO/IOC blocked
ALLOWED_PRD = ("I", "M")
ALLOWED_RET = ("DAY",)


def _num_str(v: Any) -> str:
    f = float(v)
    return str(int(f)) if f == int(f) else str(f)


@dataclass
class OrderIntent:
    client_order_id: str
    trantype: str          # B / S
    prctyp: str            # LMT / SL-LMT
    exch: str              # NFO / BFO
    tsym: str
    qty: int               # units = lots * lot_size
    prc: float
    prd: str = "I"
    ret: str = "DAY"
    trgprc: Optional[float] = None
    remarks: Optional[str] = None

    def to_jdata(self, *, uid: str, actid: str) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "ordersource": "API", "uid": uid, "actid": actid,
            "trantype": self.trantype, "prd": self.prd, "exch": self.exch,
            "tsym": self.tsym, "qty": _num_str(self.qty), "dscqty": "0",
            "prctyp": self.prctyp, "prc": _num_str(self.prc), "ret": self.ret,
        }
        if self.trgprc is not None:
            d["trgprc"] = _num_str(self.trgprc)
        if self.remarks:
            d["remarks"] = self.remarks
        return d


@dataclass
class OrderResult:
    ok: bool
    norenordno: Optional[str] = None
    rejreason: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class BrokerClient(Protocol):
    async def place_order(self, intent: OrderIntent) -> OrderResult: ...
    async def cancel_order(self, norenordno: str) -> OrderResult: ...
    async def modify_order(self, norenordno: str, *, prc: float, trgprc: Optional[float] = None) -> OrderResult: ...
    async def order_book(self) -> List[Dict[str, Any]]: ...
    async def position_book(self) -> List[Dict[str, Any]]: ...
    async def limits(self) -> Dict[str, Any]: ...
    async def search_scrip(self, exch: str, text: str) -> List[Dict[str, Any]]: ...
```
- [ ] **Step 4:** run → PASS. **Step 5:** commit `feat(live): broker Protocol + OrderIntent/OrderResult contracts`.
- [ ] **ADVERSARIAL AUDIT:** dispatch an auditor — *can a market order be expressed?* (`prctyp` not in `ALLOWED_PRCTYP` must be impossible to submit downstream); *does `to_jdata` ever emit a non-string qty/price, or include `trgprc` for a plain LMT?* Confirm the dataclass can't silently produce an invalid jData.

## Task L0.2: MockNoren broker (test backbone)

**Files:** Create `backend/app/live/mock_noren.py`; Test `tests/test_live_mock_noren.py`.

Build a deterministic in-memory `BrokerClient`: `place_order` appends to an internal order book with a generated `norenordno` and a scripted outcome (configurable to return `REJECTED` with a `rejreason`), `cancel_order`/`modify_order` mutate it, `order_book`/`position_book`/`limits`/`search_scrip` return injected fixtures, and an `emit_om(event)` helper + `om_events` list lets tests drive the order-update stream (status transitions, partial fills, rejects). **No network.** Full code + tests: each method round-trips; a scripted reject returns `ok=False`+`rejreason`; `emit_om` appends well-formed `om` dicts (`status`,`reporttype`,`fillshares`,`avgprc`,`rejreason`,`norenordno`). Commit `feat(live): deterministic MockNoren broker for host tests`.
- [ ] **ADVERSARIAL AUDIT:** *does MockNoren faithfully model documented Noren `om` shapes + ordering* (NEW→OPEN→COMPLETE, TRIGGER_PENDING→OPEN, partials accumulate `fillshares`)? Flag any divergence from the spec §4 lifecycle, since the whole state machine is tested against this fiction.

## Task L0.3: Upstox→Noren symbol resolver (Tier-0 hazard)

**Files:** Create `backend/app/live/flattrade_symbol.py`; Test `tests/test_live_symbol.py`. Read `app/live_option_universe.py` for the `option_contract` shape (`underlying`, `strike`, `side` CE/PE, `expiry_date`, `lot_size`, `trading_symbol`, `instrument_key`).

Pure resolver: `resolve(contract, *, search_fn) -> {tsym, token, exch, lot_size}` where `exch = "BFO" if underlying == "SENSEX" else "NFO"`; it calls `search_fn(exch, query)` (the broker `search_scrip`) with a query built from `underlying`+`strike`, then **selects the row matching strike+side+expiry**, and **cross-checks `lot_size`** against the contract's, raising `SymbolResolutionError` on mismatch or no match (fail-closed). A `LOT_SIZE_EXPECTED = {"NIFTY":65,"SENSEX":20,"BANKNIFTY":30}` sanity map (BANKNIFTY-35 overlap flagged in a comment). Tests use a fake `search_fn` returning canned scrip rows: exact match resolves; strike/side/expiry mismatch raises; lot-size mismatch raises; SENSEX→BFO, NIFTY→NFO. Commit `feat(live): Upstox->Noren symbol resolver (SearchScrip, fail-closed)`.
- [ ] **ADVERSARIAL AUDIT:** *can a wrong-strike or wrong-expiry scrip ever be returned as a match?* Try near-miss rows (strike 25000 vs 25050, same-day different-expiry, CE vs PE) and confirm each is rejected, not silently accepted. This is the #1 silent-wrong-fill hazard.

## Task L0.4: Flattrade token store + async client (read methods) — mirror upstox_client

**Files:** Create `backend/app/live/flattrade_token.py`, `backend/app/live/flattrade_client.py`; Test `tests/test_live_token.py` (token-store logic, no network).

`flattrade_token.py` MIRRORS `app/upstox_client.py`'s token store: Mongo `live_broker_tokens` doc `{user, jKey, actid, uid, issued_at, expires_at}`; `save_token`, `get_token`, `get_status()` → `{connected, expired, regenerate_after_6am, actid, uid}` (expired if past `expires_at` OR before today's 6 AM IST regen). Host-test the status logic with injected times (connected/expired/needs-regen). `flattrade_client.py` implements `BrokerClient` over httpx: `_post(route, jdata)` posting `jData=<json>&jKey=<token>`; `place_order`/`cancel_order`/`modify_order` (build/parse, **but untested against real** in this plan), and the read methods `order_book`/`position_book`/`limits`/`search_scrip` parsing Noren responses; plus `start_order_ws(on_om)` consuming the `om` stream. Reads are wired to the **real** API; only host-test the request-building + response-parsing with a stubbed httpx transport. Commit two: `feat(live): Flattrade daily-token store (mirrors upstox_client)` and `feat(live): Flattrade async client (read methods + WS)`.

## Task L0.5: Reconciliation reader

**Files:** Create `backend/app/live/reconcile.py`; Test `tests/test_live_reconcile.py`.

Pure diff: `reconcile(internal_orders, internal_positions, broker_orders, broker_positions) -> {ok, mismatches:[...]}` — match by `norenordno`/symbol; flag orders we think are working that the broker doesn't have (and vice-versa) and position qty mismatches. `ok=False` on any mismatch. Full code + tests: clean match → ok; missing broker order → mismatch; qty divergence → mismatch. Commit `feat(live): broker reconciliation diff (halt-on-mismatch)`.
- [ ] **ADVERSARIAL AUDIT:** *can a real divergence read as ok?* (a partially-filled order, a manual broker trade creating an unknown position, a stale internal order) — confirm each surfaces as a mismatch.

## Task L0.6: Read-only routes + Live Trading page shell

**Files:** Create `backend/app/routers/live_broker.py`; modify `backend/server.py` (register router); create `frontend/src/pages/LiveTrading.jsx` + `frontend/src/components/live/*`; add api.js methods + the `/live-trading` nav entry in `Layout.jsx`.

Routes (read-only): `GET /flattrade/status` (token status), `GET /flattrade/auth/start` + `/auth/callback` + `POST /flattrade/disconnect` (mirror Upstox OAuth routes), `GET /live-broker/positions|orders|trades|limits` (real), `GET /live-broker/reconcile` (diff report), `GET /live-broker/symbol/resolve` (resolver preview). The page: bold LIVE banner + broker-status chip + token countdown (reuse `TokenCountdown`), account/margin strip, read-only positions + working-orders blotters (reuse the redesigned paper blotter patterns), and the safety-rails panel rendering **read-only** config (editable in L1). Verify routes via `asyncio.run` against running Mongo; verify the page via `yarn build` + a Chrome render against a worktree backend (scheduler-free verify app, per [[paper-trading-redesign-2026]]). Commit per file group.

**L0 EXIT GATE (adversarial audit of the phase):** an auditor confirms no order-placing path is reachable from any route or page in L0; reads are read-only; the symbol resolver + reconciler fail closed.

---

# PHASE L1 — pre-trade safety engine + idempotency + dry-run

## Task L1.1: Pre-trade safety engine (full TDD)

**Files:** Create `backend/app/live/safety.py`; Test `tests/test_live_safety.py`.

Pure checks returning `(allowed: bool, reason: str|None)`:
- `check_fat_finger(lots, cap)` — **default-deny**: `cap is None` → block; `lots > cap` → block.
- `check_price_band(price, ref_ltp, pct)` — `ref_ltp` None/≤0 → block (stale/no ref); `abs(price-ref)/ref*100 > pct` → block.
- `validate_jdata(intent)` — `prctyp` not in `ALLOWED_PRCTYP` → block; `prd` not in `ALLOWED_PRD`; `ret` not in `ALLOWED_RET`; `SL-LMT` with no `trgprc` → block; `qty` not a multiple of `lot_size` → block.
- `class RateThrottle` — token bucket (<10/sec); `allow(is_cancel)` **always returns True for `is_cancel`** (cancels/exits never throttled).
Full code + tests for EVERY branch (no-cap deny, over-cap, out-of-band, stale-ref, market-type, missing-trigger, bad-lot-multiple, throttle-blocks-entry-but-not-cancel). Commit `feat(live): pre-trade safety engine (fat-finger, price-band, jdata, throttle)`.
- [ ] **ADVERSARIAL AUDIT:** dedicated breaker — *can a cancel ever be throttle-blocked? can a stale/zero reference price pass the band? can a market order pass validation? does a missing cap default to allow?* Each must fail safe.

## Task L1.2: Idempotency + intent store (restart-survivable, full TDD)

**Files:** Create `backend/app/live/idempotency.py`; Test `tests/test_live_idempotency.py`.

`new_client_order_id()` (UUID4 str); an intent store over Mongo `live_orders` with `record_intent(intent) -> doc` (state INTENT, written **before** any POST), `mark_submitted(cid, norenordno)`, `is_already_submitted(cid) -> bool`, and `resume_unsubmitted() -> [intents]` (intents stuck in INTENT after a restart). The dedup rule: `place` must call `is_already_submitted` and refuse to re-POST an intent that already has a `norenordno`. Tests (with a fake async store / motor mock or `asyncio.run` against a test collection): record→submitted→is_already_submitted True; a second submit attempt for a submitted cid is refused; resume returns only INTENT-state intents. Commit `feat(live): idempotency + restart-survivable intent store`.
- [ ] **ADVERSARIAL AUDIT:** *simulate a network timeout after intent-write but before/after the broker assigned a norenordno, then a process restart* — confirm reconcile+resume never double-submits the same intent.

## Task L1.3: Order builder + dry-run route

**Files:** Create `backend/app/live/order_builder.py`; modify `routers/live_broker.py` (add `POST /live-broker/order/dry-run`); Test `tests/test_live_order_builder.py`.

`build_intent(contract, *, side, lots, ref_ltp, band_pct, fat_finger_cap, sizing, levels) -> (intent|None, verdicts)`: resolve symbol (L0.3), compute a **marketable-limit** price (BUY = ref_ltp×(1+buffer) clamped by `band_pct`; SELL mirrored), compute the SL-LMT trigger/limit from `execution_policy.resolve_premium_levels(stop_floor=0.05)`, run ALL safety checks (L1.1), and only return an intent if every check passes — else `(None, verdicts)`. The dry-run route returns `{would_send: jdata|null, verdicts:[...]}` and **never transmits**. Full code + tests: a valid contract yields a band-clamped marketable limit + passes; an out-of-band ref blocks; over-cap blocks; the SL-LMT trigger equals `resolve_premium_levels`'s stop. Commit `feat(live): order builder (marketable-limit + execution_policy parity) + dry-run route`.
- [ ] **ADVERSARIAL AUDIT:** *can the marketable-limit buffer ever exceed the price band?* (the clamp must win); *does the stop ever differ from `execution_policy`?* (parity).

**L1 EXIT GATE:** auditor confirms the dry-run route is the only order path, it never transmits, and every safety reject is enforced.

---

# PHASE L2 — order state machine + kill switch + reconciliation loop (all vs MockNoren)

## Task L2.1: Order state machine from `om` events (full TDD)

**Files:** Create `backend/app/live/order_sm.py`; Test `tests/test_live_order_sm.py`.

`apply_om(order_doc, om) -> order_doc'`: map Noren `status`/`reporttype` → `ORDER_STATES`, accumulate `fillshares`/`avgprc`, set `rejreason` on REJECTED; `classify_reject(rejreason) -> "transient"|"terminal"` (session/throttle/timeout = transient; disallowed-type/lot/symbol/RMS = terminal). Full code + tests for every transition incl. partial fill accumulation, TRIGGER_PENDING→OPEN→COMPLETE, REJECTED with reason classification, out-of-order/duplicate `om` idempotence. Commit `feat(live): om-driven order state machine + reject classification`.
- [ ] **ADVERSARIAL AUDIT:** replay duplicated/out-of-order `om` events (COMPLETE before OPEN, double fills) — confirm no state corruption or double-counted fills.

## Task L2.2: Kill switch + account guardrails (full TDD, vs MockNoren)

**Files:** Create `backend/app/live/kill_switch.py`; modify `routers/live_broker.py` (`POST /live-broker/kill-switch`, `GET/PUT /live-broker/safety-config`); Test `tests/test_live_kill_switch.py`.

`panic_squareoff(client, open_orders, open_positions) -> report`: cancel every working order (bypassing throttle), then flatten each position via a marketable-limit exit intent; returns canceled/flattened counts + any failures. `evaluate_guardrails(mtm, open_count, config) -> action` where action ∈ `{none, broker_stop_loss, profit_lock, max_open_block}` and a tripped broker-stop-loss sets a **`blocked_until_reset` latch** that blocks new entries until a manual `reset`. Config CRUD over `live_safety_config` (singleton). Full code + tests against `MockNoren`: kill cancels all + flattens all; a guardrail breach trips + latches + blocks; `reset` clears. Commit `feat(live): kill switch + account-level guardrails (latched broker-stop-loss)`.
- [ ] **ADVERSARIAL AUDIT (highest-stakes):** *after panic, is any working order left? any position un-flattened? can a new entry slip through while latched? can the latch be cleared without an explicit reset?* The kill switch must be total.

## Task L2.3: Reconciliation loop wiring + state-machine integration

**Files:** modify `routers/live_broker.py` + a small `app/live/engine.py` glue (read-only orchestrator: pulls broker books, runs `reconcile`, feeds `om` to `order_sm` — all against MockNoren in tests). Test `tests/test_live_engine.py`.

Wire: on (mock) `om` events → `order_sm.apply_om` updates `live_orders`; a reconcile tick compares to `position_book`/`order_book` and halts (sets an engine `halted` flag + would-alert) on mismatch. Full integration test against MockNoren: drive an order INTENT→SUBMITTED→om OPEN→om COMPLETE, reconcile clean; then inject a broker-only position → reconcile halts. Commit `feat(live): engine glue — om→state-machine + reconcile-halt`.

**L2 EXIT GATE (phase adversarial audit + full suite):** run the entire `tests/test_live_*.py` suite green; a final auditor verifies the complete safe core: no real-order path, kill switch total, idempotency restart-proof, reconciliation halts on mismatch, exit parity holds, every safety control fails closed.

---

## Self-review (author check)

- **Spec coverage:** §5 components → L0.1–L0.6, L1.1–L1.3, L2.1–L2.3 (client/token L0.4, symbol L0.3, safety L1.1, idempotency L1.2, order_sm L2.1, reconcile L0.5+L2.3, kill_switch L2.2, mock L0.2, routes/page L0.6+dry-run L1.3). §6 safety tiers → each Tier-0/1 control has a task + adversarial audit. §9 testing → host tests + adversarial-audit subagents + (L0.6) live read-only verification. §10 out-of-scope (real orders) honored — no task transmits.
- **Placeholder scan:** pure-logic tasks have full code; integration tasks (client/token/routes/page) give exact files+interfaces+tests+the mirrored pattern to read — acceptable for a subagent that reads the referenced existing file.
- **Type consistency:** `OrderIntent`/`OrderResult`/`ORDER_STATES`/`ALLOWED_*` defined in L0.1 are reused verbatim in L0.2/L1.1/L1.3/L2.1/L2.2; `BrokerClient` Protocol satisfied by both `MockNoren` (L0.2) and `FlattradeClient` (L0.4); `resolve()` return shape consistent across L0.3 and L1.3; `live_orders`/`live_safety_config` collection names consistent across L1.2/L2.2/L2.3.
- **Gate:** no task in this plan places a real broker order; the real client's order methods are exercised only by MockNoren. L3 (separate spec) wires them to the real client behind Offline→One-Click.

## Execution handoff

Per-task: implementer → spec review → quality review → **adversarial audit** (for every safety/logic task) → fix loop → next. Phases gated (L0→L1→L2 exit gates). Run autonomously L0→L2; surface to the user before L3 (the first real-order spec).
