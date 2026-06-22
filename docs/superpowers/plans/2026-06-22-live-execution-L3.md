# Live Execution L3 — Manual Live-Test Order Path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Each safety unit gets a
> dedicated adversarial-audit subagent after its spec/quality review.

**Goal:** Build the first real Flattrade order path — a mode-gated, human-clicked, 1-lot
index-option order with a guarded executor and a ≤10-minute auto-square — proven exhaustively
against `MockNoren`, with the one real fill left to the user.

**Architecture:** Five backend units (`mode`, `margin`, `auto_square`, `executor`,
`live_friction_profile`) + routes + a frontend Live-Test panel, layered on the L0–L2 Safe Core.
The executor is the sole entry chokepoint; exits are always available.

**Tech Stack:** FastAPI + Motor (async), pytest (host tests vs `MockNoren`), React 19 + craco.

**Spec:** `docs/superpowers/specs/2026-06-22-live-execution-L3-design.md` (read it).

**Branch:** `feat/live-execution-l3` (off `main` @ 2d5e2ea). Worktree: the primary repo.

**Test runner:** `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/test_live_<x>.py -v`

---

# PHASE L3-A — backend safety core (all host-testable vs MockNoren)

## Task L3.1: Mode gate

**Files:** Create `backend/app/live/mode.py`; Test `tests/test_live_mode.py`.

The master switch. States `PAPER` (default) / `LIVE_OFFLINE` / `LIVE_TEST` / `LIVE_ARMED`
(L4-reserved, rejected here).

- [ ] **Step 1 — failing tests.** Cover: default `current_mode()` → `PAPER` when store empty;
  `is_live_order_allowed` True ONLY for `LIVE_TEST` + `single_shot_consumed=False`; False for every
  other mode and for consumed; transition validation (`set_mode` rejects unknown/`LIVE_ARMED`,
  rejects entering `LIVE_TEST` without `confirm=True`); `consume_single_shot()` flips the flag and
  a second `is_live_order_allowed` → False; `revert_to_offline()` sets `LIVE_OFFLINE` + clears the
  test session; a fake collection (DB-free).

- [ ] **Step 2 — implement.**
```python
# backend/app/live/mode.py
from __future__ import annotations
from typing import Any, Dict, Optional

MODES = ("PAPER", "LIVE_OFFLINE", "LIVE_TEST", "LIVE_ARMED")
DEFAULT_MODE = "PAPER"
_L3_ALLOWED_TARGETS = ("PAPER", "LIVE_OFFLINE", "LIVE_TEST")  # LIVE_ARMED is L4, rejected here

def is_live_order_allowed(mode_doc: Optional[Dict[str, Any]]) -> bool:
    """True ONLY in LIVE_TEST with an unconsumed single-shot. Fail-safe: None/unknown -> False."""
    if not mode_doc:
        return False
    return mode_doc.get("mode") == "LIVE_TEST" and not bool(mode_doc.get("single_shot_consumed"))

class ModeStore:
    def __init__(self, collection):
        self._c = collection
    async def get(self) -> Dict[str, Any]:
        doc = await self._c.find_one({"_id": "singleton"})
        if not doc:
            return {"_id": "singleton", "mode": DEFAULT_MODE, "single_shot_consumed": False,
                    "test_session_id": None}
        doc.setdefault("mode", DEFAULT_MODE)
        doc.setdefault("single_shot_consumed", False)
        return doc
    async def set_mode(self, target: str, *, confirm: bool = False, can_trade: bool = True,
                       connected: bool = True, now_iso: Optional[str] = None) -> Dict[str, Any]:
        if target not in _L3_ALLOWED_TARGETS:
            raise ValueError(f"mode {target!r} not allowed in L3")
        if target == "LIVE_TEST":
            if not confirm:
                raise ValueError("entering LIVE_TEST requires confirm=True")
            if not connected:
                raise ValueError("cannot enter LIVE_TEST: broker not connected")
            if not can_trade:
                raise ValueError("cannot enter LIVE_TEST: engine halted or latched")
        await self._c.update_one({"_id": "singleton"},
            {"$set": {"mode": target, "since": now_iso, "single_shot_consumed": False}}, upsert=True)
        return await self.get()
    async def consume_single_shot(self) -> None:
        await self._c.update_one({"_id": "singleton"}, {"$set": {"single_shot_consumed": True}}, upsert=True)
    async def revert_to_offline(self, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
        await self._c.update_one({"_id": "singleton"},
            {"$set": {"mode": "LIVE_OFFLINE", "single_shot_consumed": False,
                      "test_session_id": None, "since": now_iso}}, upsert=True)
        return await self.get()

def default_store():
    from app.db import get_db
    return ModeStore(get_db().live_mode)
```

- [ ] **Step 3 — run + commit** `feat(live): L3 mode gate (PAPER->LIVE_TEST single-shot)`.

- [ ] **ADVERSARIAL AUDIT (L3.1):** *can `is_live_order_allowed` ever return True outside LIVE_TEST,
  or for a consumed shot, or for a malformed/None doc? Can `set_mode` enter LIVE_TEST without
  confirm, while halted, or while disconnected? Can a generic write flip `single_shot_consumed` back
  to False without a deliberate re-entry? Does an unknown/missing mode default to anything but no-live?*
  Each must fail safe (no live orders).

## Task L3.2: Margin pre-check verdict

**Files:** Create `backend/app/live/margin.py`; Test `tests/test_live_margin.py`.

- [ ] **Step 1 — failing tests.** `check_margin(limits, *, premium_required) -> (ok, detail)`:
  sufficient cash → ok; insufficient → block; `limits` missing/empty/`cash` non-finite/negative/string
  → block (fail-closed); `premium_required` non-finite/<=0 → block. The cash field key in Noren
  `limits()` is `cash` (string per Noren); parse via `float()` defensively.

- [ ] **Step 2 — implement.** `premium_required = ref_ltp * lot_size * buffer` where `buffer` covers
  the marketable cross + a statutory pad (e.g. 1.05). Reuse the `_finite_num` pattern from `safety.py`.
  Return `(False, reason)` on any parse failure or shortfall; `(True, detail)` only when
  `cash >= premium_required` with both finite-positive.

- [ ] **Step 3 — commit** `feat(live): L3 margin pre-check verdict (fail-closed)`.

## Task L3.3: Auto-square engine

**Files:** Create `backend/app/live/auto_square.py`; Test `tests/test_live_auto_square.py`.

The ≤10-minute forced exit. Built BEFORE the executor (the executor arms it).

`AutoSquare` holds the armed deadline + drives the square. Time is INJECTED (no wall-clock in
logic) so tests are deterministic.

- [ ] **Step 1 — failing tests** (vs `MockNoren`, injected `now`): arming records the deadline =
  `fill_time + 600s`; `due(now)` True only at/after deadline; `square(client, position)` issues a
  marketable-limit exit in the correct direction (long→SELL, short→BUY), qty = filled qty; an
  UNFILLED entry at deadline → `cancel_order` (no position), returns `squared via cancel`; partial
  fill → squares the filled part + cancels the working remainder; a square that the broker rejects
  is tallied + retried-once then surfaced (never silently leaves a position); `square_now` and
  `kill` paths both exit; placing the SL-LMT backstop builds a valid SL-LMT intent (trigger-relative
  band); re-arm from a persisted deadline (restart) works.

- [ ] **Step 2 — implement.** Key contracts:
```python
SQUARE_HORIZON_SEC = 600  # 10 minutes — the hard cap

def deadline_iso(fill_time_iso, *, horizon_sec=SQUARE_HORIZON_SEC) -> str: ...
def is_due(deadline_iso, now_iso) -> bool: ...  # now >= deadline

async def place_sl_backstop(client, *, intent_ctx, stop_trigger, uid, actid) -> OrderResult:
    """A protective SL-LMT on the long option. trgprc=stop_trigger (trigger-relative band),
    prc=max(0.05, round(stop_trigger-0.05,2)). Exit-only."""

async def square_position(client, position, *, reason, uid="", actid="") -> dict:
    """Marketable-limit exit of `position` (filled qty, correct direction). Cancels any working
    entry remainder first. Retries a rejected square ONCE. Returns
    {squared: bool, norenordno|None, reason, failures:[...]}. NEVER raises, NEVER silently leaves
    a filled position open (failure -> failures[] + squared=False so the engine halts/alerts)."""
```
  Reuse `kill_switch.panic_squareoff` semantics for the single position OR a focused single-position
  squarer; do NOT apply fat-finger/throttle to an exit. Cancel-before-square for the remainder.

- [ ] **Step 3 — commit** `feat(live): L3 auto-square (10-min timer + SL-LMT backstop + square-now)`.

- [ ] **ADVERSARIAL AUDIT (L3.3):** *can a filled position EVER be left unsquared after the deadline
  (a rejected square that's swallowed, a wrong-direction exit that grows the position, a partial
  fill where the remainder is forgotten)? Can the SL backstop ever be a non-protective price (>=
  entry for a long)? Does `square` ever raise (a raised exception mid-square strands the position)?
  Is the deadline ever computed as > 10 min? Can `is_due` miss a passed deadline (clock-skew/format)?*

## Task L3.4: Guarded executor — the sole entry chokepoint

**Files:** Create `backend/app/live/executor.py`; Test `tests/test_live_executor.py`.

Depends on L3.1 (mode), L3.2 (margin), L3.3 (auto-square), + `build_intent`, `idempotency`, `engine`.

- [ ] **Step 1 — failing tests** (vs `MockNoren` + fakes). The precondition chain — each gate, when
  it fails, MUST return `{placed: False, reason, verdicts}` and make ZERO `place_order` call:
  - mode not `LIVE_TEST` (or consumed) → no place;
  - `qty != lot_size` (e.g. 2 lots) → no place;
  - any dry-run verdict fails (out-of-band, over-cap, bad symbol, margin shortfall) → no place;
  - `engine.can_trade()` returns halted/latched → no place;
  - idempotency claim returns False (already claimed) → no place;
  - happy path: all gates pass → exactly ONE `place_order`, `mark_submitted` called, single-shot
    CONSUMED, auto-square ARMED + SL placed, returns `{placed: True, norenordno}`;
  - a broker REJECT on place → recorded, single-shot NOT consumed, no square armed;
  - **arm-or-abort:** if arming the auto-square / placing the SL RAISES or fails after a successful
    place → the executor immediately squares/cancels the just-placed order AND halts the engine,
    returns `{placed: True, protected: False, halted: True}`;
  - **fresh dry-run:** a caller-supplied stale/tampered intent does NOT bypass — the executor
    re-derives the intent server-side from the contract and re-runs all verdicts.

- [ ] **Step 2 — implement** the chain EXACTLY in spec §4 order. Pseudocode:
```python
async def place_live_test_order(contract, *, side, ref_ltp, band_pct, levels, client,
        mode_store, intent_store, config_store, engine, lot_size, fat_finger_cap=1,
        uid="", actid="", now_iso=None) -> dict:
    verdicts = []
    mode_doc = await mode_store.get()
    if not is_live_order_allowed(mode_doc):                      # gate 1
        return _blocked("mode_not_live_test", verdicts)
    cid = new_client_order_id()
    intent, verdicts = build_intent(contract, side=side, order_kind="entry", lots=1,
        ref_ltp=ref_ltp, band_pct=band_pct, fat_finger_cap=min(fat_finger_cap, 1),  # pin to 1
        levels=levels, client_order_id=cid, search_fn=...)       # gate 3 (fresh dry-run)
    # gate 2 (qty) + margin verdict appended inside/after build_intent
    if intent is None or any(not v["ok"] for v in verdicts):
        return _blocked("dry_run_failed", verdicts)
    if intent.qty != lot_size:                                   # gate 2 (defense in depth)
        return _blocked("not_one_lot", verdicts)
    ok, why = await engine.can_trade()                           # gate 4
    if not ok:
        return _blocked(f"cannot_trade:{why}", verdicts)
    if not await intent_store.claim_for_submit(cid):             # gate 5
        return _blocked("already_claimed", verdicts)
    result = await client.place_order(intent)                    # TRANSMIT (the only entry place)
    if not result.ok:
        # record reject; DO NOT consume single-shot; no square
        return {"placed": False, "reason": f"reject:{result.rejreason}", "verdicts": verdicts}
    await intent_store.mark_submitted(cid, result.norenordno)
    await mode_store.consume_single_shot()                       # single-shot consumed
    try:                                                         # gate 7 arm-or-abort
        await auto_square.arm(...); await auto_square.place_sl_backstop(...)
    except Exception as exc:
        await auto_square.square_position(client, position, reason="arm_failed", ...)
        await engine.halt("auto_square_arm_failed")
        return {"placed": True, "protected": False, "halted": True, "norenordno": result.norenordno}
    return {"placed": True, "protected": True, "norenordno": result.norenordno, "verdicts": verdicts}
```

- [ ] **Step 3 — commit** `feat(live): L3 guarded executor (sole entry chokepoint, arm-or-abort)`.

- [ ] **ADVERSARIAL AUDIT (L3.4 — HARDEST):** *can it place > 1 lot via any input? place outside
  LIVE_TEST / when consumed / halted / latched? place on a stale or tampered dry-run (does it truly
  re-derive)? double-place (claim race)? leave a filled position unprotected if arming fails (is
  arm-or-abort total)? consume single-shot on a REJECT (it must not)? Is `place_order` reachable from
  anywhere but this function?* Map each to the spec §12 invariants.

## Task L3.5: Live friction profile

**Files:** Create `backend/app/live/live_friction_profile.py`; Test `tests/test_live_friction_profile.py`.

- [ ] **Step 1 — failing tests.** `live_charges(turnover_buy, turnover_sell, *, segment) -> dict`:
  brokerage = 0; STT sell-side only; exchange txn + GST(18% on brokerage+txn) + SEBI turnover +
  stamp duty (buy-side). NFO vs BFO rates. Round to paise. (Use current published rates; flag exact
  values for verification in the plan's open items.)
- [ ] **Step 2 — implement** a pure function; document each rate + its source.
- [ ] **Step 3 — commit** `feat(live): L3 zero-brokerage statutory friction profile`.

---

# PHASE L3-B — routes + wiring

## Task L3.6: Routes + kill-switch becomes executing

**Files:** Modify `backend/app/routers/live_broker.py`; Test `tests/test_live_l3_routes.py`.

- [ ] **Step 1 — failing tests** (FastAPI TestClient + injected MockNoren/fakes): `GET/PUT
  /live-broker/mode` (PUT validates confirm + connection); `POST /live-broker/order/place` routes to
  the executor and NEVER transmits unless every gate passes; `POST /live-broker/order/square`
  exit-only; `GET /live-broker/test-session` returns position + deadline + remaining secs + heartbeat;
  the kill-switch route now EXECUTES the single test position's exit (was plan-only in L2).
- [ ] **Step 2 — implement.** Wire the real `FlattradeClient` (orders) behind the executor only.
  Grep-assert: `place_order(` appears in routes ONLY through `executor.place_live_test_order`;
  `cancel_order`/`place_order` for exits ONLY through square/kill paths.
- [ ] **Step 3 — commit** `feat(live): L3 routes (mode/place/square/test-session) + executing kill`.
- [ ] **ADVERSARIAL AUDIT (L3.6):** *is `/order/place` the only transmitting route, and only via the
  executor? Can any route place an entry bypassing the executor? Does `/mode` enforce the §3 rules?*

---

# PHASE L3-C — frontend Live-Test panel

## Task L3.7: API client + panel components

**Files:** Modify `frontend/src/lib/api.js`; Create `frontend/src/components/live/LiveTestPanel.jsx`,
`OrderTicket.jsx`, `PositionMonitor.jsx`. Test: build + manual render.

- [ ] API methods: `getMode`, `setMode`, `dryRunOrder`, `placeLiveTestOrder`, `squarePosition`,
  `getTestSession`. **Match the mockup** (`live_test_order_panel_mockup`): order ticket with locked
  1-lot, dry-run verdict list, a red Place button that arms ONLY when every verdict passes + mode is
  LIVE_TEST; position monitor with a 10:00 countdown, Square-now, Kill, and a backend-heartbeat dot.
- [ ] The Place button requires a two-step confirm (arm → confirm) and shows REAL MONEY framing.
- [ ] Commit `feat(live-ui): L3 Live-Test panel (ticket + verdicts + position monitor)`.

## Task L3.8: Assemble + verify

**Files:** Modify `frontend/src/pages/LiveTrading.jsx`.
- [ ] Mount the panel; mode switcher (PAPER/LIVE_OFFLINE/LIVE_TEST with confirm); advance the page's
  badge from L0. `yarn build` (craco) green. Manual render check.
- [ ] Commit `feat(live-ui): mount L3 Live-Test panel on /live-trading`.

---

# PHASE L3-D — exit gate

**L3 EXIT GATE (final adversarial audit + full suite):** an auditor proves spec §12's 8 invariants
end-to-end vs `MockNoren`: real orders ONLY in LIVE_TEST single-shot; ≤1 lot always; arm-or-abort
(no unprotected filled position); the 10-min deadline always resolves; margin-checked; executor is
the sole entry chokepoint; every L0–L2 invariant still holds; default-safe. Run the ENTIRE
`tests/test_live_*.py` green. Then **surface to the user for the manual 1-lot live validation** —
which the assistant cannot and will not perform.

---

## Self-review (author)
- **Spec coverage:** §3→L3.1, §6→L3.2, §5→L3.3, §4→L3.4, §7→L3.5, §8→L3.6, §UI→L3.7-8, §12→L3-D. ✓
- **Type consistency:** `is_live_order_allowed`/`ModeStore`/`AutoSquare`/`place_live_test_order`
  names reused across tasks; reuses L0–L2 `build_intent`/`OrderIntent`/`idempotency`/`engine`/
  `panic_squareoff` verbatim. ✓
- **Ordering:** auto-square (L3.3) precedes the executor (L3.4) that arms it. ✓
- **Open items (verify at plan-exec):** statutory rates (L3.5); the real `search_fn` wiring into the
  executor's `build_intent` (reuse the dry-run route's async→sync adapter); heartbeat field.
