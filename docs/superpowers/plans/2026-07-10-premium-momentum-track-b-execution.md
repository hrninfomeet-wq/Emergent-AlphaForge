# Premium-Momentum Track B (Live/Paper Execution) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the premium-momentum strategy (time-locked strike, premium-trigger entry, premium exits with stepped trail) on the existing deployment rails — paper AND live-capable from day one through the unchanged gate chain, per the approved spec `docs/superpowers/specs/2026-07-10-premium-momentum-track-b-execution-design.md`.

**Architecture:** One new pre-entry component (per-session strike-lock + two-strike premium monitor) invoked as a branch inside `evaluate_deployment_on_close`, rejoining the shared signal-doc pipeline so every existing guard (idempotency, blockers, claim, caps, executor gates, software guard) applies untouched. New `premium_locks` collection is the crash-safe session state + subscription-pin source + recovery source. Stepped X-Y trail becomes a new `stepped_xy` mode in the shared `live_sl_monitor` state machine, delegating to the SAME `stepped_trail_stop` helper the backtest uses.

**Tech Stack:** Python 3.11 / FastAPI / motor (backend), pytest host+container split (container = `MSYS_NO_PATHCONV=1 docker cp … alphaforge_backend:/app/…` then `docker exec -w /app alphaforge_backend python -m pytest …`), React (read-only touch).

**Conventions for this plan:**
- HOST test = `python -m pytest tests/<file> -q` from repo root (no motor imports).
- CONTAINER test = sync files then run inside `alphaforge_backend` (container `/app/tests` resets on rebuild — re-sync `tests/` first with `MSYS_NO_PATHCONV=1 docker cp tests/. alphaforge_backend:/app/tests`).
- Every commit message ends with the repo's standard co-author line.
- **Trail configuration is single-source:** the LIVE stepped trail comes ONLY from `deployment.risk.exit_controls = {"mode":"stepped_xy","x":<pts>,"y":<pts>}` (already passed through by `resolve_live_exit_plan`, `auto_live.py:215-216`). The strategy params do NOT carry trail knobs — one source, no drift.

---

## Task 0: Anchor re-verification + baseline

The reconciliation (`23a61c1..4ec5e2b`) moved lines in the files this plan edits. Verify every anchor before coding.

**Files:** none (read-only).

- [ ] **Step 1: Verify the seam anchors exist** (symbol-level; exact lines may differ from comments in this plan — trust the grep):

```bash
cd "C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge"
grep -n "def evaluate_deployment_on_close\|_resolve_option_contract(\|def next_expiry_for\|create_signal_doc(" backend/app/deployment_evaluator.py | head
grep -n "_VALID_MODES\|def build_monitor_state\|def _raise_stop\|elif mode == \"trail\"" backend/app/live/live_sl_monitor.py
grep -n "def resolve_live_exit_plan\|resolve_live_entry_ref_ltp\|def claim_signal_for_live_trade" backend/app/auto_live.py
grep -n "def _auto_follow_option_stream\|paper_trades.distinct" backend/app/runtime.py
grep -n "def maybe_run_live_recovery\|def live_startup_recovery" backend/app/runtime.py
grep -n "def lock_reference_strike\|def momentum_triggered\|def stepped_trail_stop" backend/app/premium_momentum.py
grep -n "def resolve_premium" backend/app/live/option_premium.py
```
Expected: every symbol found (non-empty output per line). If any symbol is missing, STOP and report BLOCKED.

- [ ] **Step 2: Baseline tests green**

```bash
python -m pytest tests/test_premium_momentum.py tests/test_premium_momentum_backtest.py -q
```
Expected: all pass (32+). Container: sync `tests/` and run `tests/test_live_position_guard.py tests/test_live_sl_monitor.py -q` — expected pass (source-read contract tests may fail on the flattened layout; those are documented false-fails, judge by the rest).

---

## Task 1: `premium_locks` store (new module)

Crash-safe per-(deployment, session) state: strikes, ref premiums, trigger latch, entry, done flag.

**Files:**
- Create: `backend/app/premium_lock_store.py`
- Modify: `backend/app/db.py` (ensure_indexes — add the unique compound index)
- Test: `tests/test_premium_lock_store.py` (container)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_premium_lock_store.py
"""Track B Task 1 — premium_locks store: create-once (duplicate-key adopt),
atomic trigger latch, entered/done transitions. CONTAINER test (motor import
via app.db is NOT needed — the store takes any async collection; these tests
use an in-memory fake that mimics Mongo's filtered-update semantics)."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.premium_lock_store import (
    get_or_create_lock, capture_ref, latch_trigger, mark_entered, mark_done,
    today_locked_keys,
)


class _DupKey(Exception):
    def __str__(self):
        return "E11000 duplicate key error"


class _FakeLocks:
    """Minimal async collection: insert_one raises duplicate on (deployment_id,
    session_date) collision; update_one applies $set iff the filter matches
    (top-level equality + $exists:False only — what the store uses); find
    supports the two queries the store issues."""

    def __init__(self):
        self.docs = []

    def _key(self, d):
        return (d.get("deployment_id"), d.get("session_date"))

    async def insert_one(self, doc):
        if any(self._key(x) == self._key(doc) for x in self.docs):
            raise _DupKey()
        self.docs.append(dict(doc))

    def _matches(self, d, q):
        for k, v in q.items():
            if isinstance(v, dict) and "$exists" in v:
                if (k in d and d[k] is not None) != v["$exists"]:
                    return False
            elif d.get(k) != v:
                return False
        return True

    async def find_one(self, q, proj=None):
        for d in self.docs:
            if self._matches(d, q):
                return dict(d)
        return None

    async def update_one(self, q, upd):
        for d in self.docs:
            if self._matches(d, q):
                d.update(upd.get("$set", {}))
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def find(self, q, proj=None):
        docs = [dict(d) for d in self.docs if self._matches(d, q)]

        class _Cur:
            async def to_list(self, length=None):
                return docs
        return _Cur()


def run(c):
    return asyncio.run(c)


def _mk():
    return _FakeLocks()


def test_get_or_create_is_create_once_and_adopts_existing():
    col = _mk()
    a = run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10",
                               payload={"spot_at_ref": 24000.0}))
    b = run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10",
                               payload={"spot_at_ref": 99999.0}))   # racer loses
    assert a["spot_at_ref"] == 24000.0
    assert b["spot_at_ref"] == 24000.0          # adopted, NOT overwritten
    assert len(col.docs) == 1


def test_capture_ref_sets_side_fields_once():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10",
                           payload={"ce": {"instrument_key": "K1"}}))
    ok = run(capture_ref(col, deployment_id="D1", session_date="2026-07-10",
                         side="ce", ref_premium=101.5, ref_ts=1720600000000))
    assert ok is True
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["ce_ref_premium"] == 101.5 and doc["ce_ref_ts"] == 1720600000000


def test_latch_trigger_is_atomic_first_wins():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10", payload={}))
    assert run(latch_trigger(col, deployment_id="D1", session_date="2026-07-10", side="CE")) is True
    assert run(latch_trigger(col, deployment_id="D1", session_date="2026-07-10", side="PE")) is False
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["triggered_side"] == "CE"


def test_mark_entered_and_done():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10", payload={}))
    run(mark_entered(col, deployment_id="D1", session_date="2026-07-10",
                     norenordno="N123", entry_premium=115.0))
    run(mark_done(col, deployment_id="D1", session_date="2026-07-10", reason="exited"))
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["entered_norenordno"] == "N123"
    assert doc["done_for_day"] is True and doc["done_reason"] == "exited"


def test_today_locked_keys_unions_both_sides():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10",
                           payload={"ce": {"instrument_key": "KC"}, "pe": {"instrument_key": "KP"}}))
    run(get_or_create_lock(col, deployment_id="D2", session_date="2026-07-10",
                           payload={"ce": {"instrument_key": "KC"}}))   # dup key unioned once
    run(get_or_create_lock(col, deployment_id="D3", session_date="2026-07-09",
                           payload={"ce": {"instrument_key": "OLD"}}))  # stale session excluded
    keys = run(today_locked_keys(col, session_date="2026-07-10"))
    assert sorted(keys) == ["KC", "KP"]
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_premium_lock_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.premium_lock_store'`.

- [ ] **Step 3: Implement the store**

```python
# backend/app/premium_lock_store.py
"""Per-(deployment, session) state for premium-momentum execution (Track B).

One doc per deployment per IST session, unique on (deployment_id, session_date)
— create-once crash-safety via duplicate-key ADOPT (a racer reads the winner's
doc; the same pattern as the signals dedupe index). The doc is simultaneously:
the strike lock (never re-resolve from drifting spot), the ref-premium record,
the first-to-trigger latch, the subscription-pin source, and the recovery source.

Side fields are FLAT (ce_ref_premium, not ce.ref_premium) so filtered atomic
updates stay top-level-equality only. The store takes ANY async collection.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_or_create_lock(col: Any, *, deployment_id: str, session_date: str,
                             payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create the session lock once; a concurrent/second caller ADOPTS the
    existing doc (its payload is discarded). Never overwrites."""
    doc = {
        "deployment_id": str(deployment_id),
        "session_date": str(session_date),
        "locked_at": _now_iso(),
        "triggered_side": None,
        "entered_norenordno": None,
        "entry_premium": None,
        "done_for_day": False,
        "done_reason": None,
        **(payload or {}),
    }
    try:
        await col.insert_one(doc)
        doc.pop("_id", None)
        return doc
    except Exception as exc:  # duplicate key → adopt the existing winner
        if "duplicate" not in str(exc).lower() and "e11000" not in str(exc).lower():
            raise
        existing = await col.find_one(
            {"deployment_id": str(deployment_id), "session_date": str(session_date)},
            {"_id": 0})
        return existing or doc


async def get_lock(col: Any, *, deployment_id: str, session_date: str) -> Optional[Dict[str, Any]]:
    return await col.find_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date)}, {"_id": 0})


async def capture_ref(col: Any, *, deployment_id: str, session_date: str,
                      side: str, ref_premium: float, ref_ts: int) -> bool:
    """Persist one side's reference premium ONCE (filtered on the field being
    absent — a second capture is a no-op, the first tick wins)."""
    s = str(side).lower()
    res = await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         f"{s}_ref_premium": {"$exists": False}},
        {"$set": {f"{s}_ref_premium": float(ref_premium),
                  f"{s}_ref_ts": int(ref_ts),
                  f"{s}_ref_captured_at": _now_iso()}},
    )
    return int(getattr(res, "matched_count", 0) or 0) == 1


async def latch_trigger(col: Any, *, deployment_id: str, session_date: str, side: str) -> bool:
    """Atomically latch the first side to trigger. Filter requires the latch to
    still be None — Mongo's single-doc update makes first-wins race-safe."""
    res = await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         "triggered_side": None, "done_for_day": False},
        {"$set": {"triggered_side": str(side).upper(), "triggered_at": _now_iso()}},
    )
    return int(getattr(res, "matched_count", 0) or 0) == 1


async def unlatch_trigger(col: Any, *, deployment_id: str, session_date: str) -> None:
    """Release the latch after a journaled entry FAILURE (refusal/error) so a
    later bar may re-trigger. Mirrors release_live_trade_claim's philosophy."""
    await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date),
         "entered_norenordno": None},
        {"$set": {"triggered_side": None}},
    )


async def mark_entered(col: Any, *, deployment_id: str, session_date: str,
                       norenordno: str, entry_premium: Optional[float]) -> None:
    await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date)},
        {"$set": {"entered_norenordno": str(norenordno),
                  "entry_premium": (float(entry_premium) if entry_premium is not None else None),
                  "entered_at": _now_iso()}},
    )


async def mark_done(col: Any, *, deployment_id: str, session_date: str, reason: str) -> None:
    await col.update_one(
        {"deployment_id": str(deployment_id), "session_date": str(session_date)},
        {"$set": {"done_for_day": True, "done_reason": str(reason), "done_at": _now_iso()}},
    )


async def today_locked_keys(col: Any, *, session_date: str) -> List[str]:
    """Distinct instrument keys locked for THIS session (both sides, all
    deployments) — the subscription-pin source."""
    cur = col.find({"session_date": str(session_date)}, {"_id": 0})
    keys: List[str] = []
    for doc in await cur.to_list(length=None):
        for s in ("ce", "pe"):
            k = ((doc.get(s) or {}).get("instrument_key")) if isinstance(doc.get(s), dict) else None
            if k and k not in keys:
                keys.append(str(k))
    return keys
```

- [ ] **Step 4: Add the unique index.** In `backend/app/db.py`, find `ensure_indexes` and the existing signals compound index (grep `deployment_id.*candle_ts` ~:49-54). Add, following the same pattern/style:

```python
    # Track B: one premium-momentum session lock per deployment per IST day.
    # create-once semantics — a racing second insert hits E11000 and ADOPTS.
    await db.premium_locks.create_index(
        [("deployment_id", 1), ("session_date", 1)], unique=True,
        name="uniq_premium_lock_per_session",
    )
```

- [ ] **Step 5: Run tests, verify pass**

Run: `python -m pytest tests/test_premium_lock_store.py -q` → PASS (5).
Container: sync `backend/app/premium_lock_store.py`, `backend/app/db.py`, `tests/test_premium_lock_store.py`; run the file → PASS. Import smoke: `docker exec … python -c "import app.premium_lock_store, app.db; print('OK')"`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/premium_lock_store.py backend/app/db.py tests/test_premium_lock_store.py
git commit -m "feat(premium-momentum): premium_locks session store — create-once lock, atomic trigger latch, pin/recovery source"
```

---

## Task 2: `premium_momentum` strategy plugin (registration vehicle)

**Files:**
- Create: `backend/app/strategies/plugins/premium_momentum.py`
- Test: `tests/test_premium_momentum_plugin.py` (container — registry auto-discovery imports plugin deps)

- [ ] **Step 1: Failing test**

```python
# tests/test_premium_momentum_plugin.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.strategies.plugins.premium_momentum import PremiumMomentum


def test_plugin_identity_and_schema():
    s = PremiumMomentum()
    assert s.id == "premium_momentum"
    assert s.supported_instruments == ["NIFTY"]          # spec: v1 NIFTY-only
    schema = s.parameter_schema
    for key, default in [("reference_time", "09:31"), ("moneyness", "itm1"),
                         ("side", "first_to_trigger"), ("momentum_pct", 15.0),
                         ("stop_pct", 20.0)]:
        assert key in schema and schema[key]["default"] == default
    assert "target_pct" in schema        # optional, default None
    # trail knobs are DELIBERATELY absent: live trail is single-sourced from
    # deployment.risk.exit_controls (mode stepped_xy) — no drift.
    assert "trail_x" not in schema and "trail_y" not in schema


def test_evaluate_is_inert_none():
    # The evaluator's Track B branch does the real work; the plugin's evaluate
    # is inert so the GENERIC path can never fire a spot signal for it.
    from app.strategies.base import Signal
    s = PremiumMomentum()
    sig = s.evaluate({"close": 1.0}, {"close": 1.0}, s.default_params(), {})
    assert isinstance(sig, Signal) and sig.direction == "NONE"
```

- [ ] **Step 2: Run, verify fail** (module not found).

- [ ] **Step 3: Implement**

```python
# backend/app/strategies/plugins/premium_momentum.py
"""Premium-momentum contingency breakout (Track B execution vehicle).

Registration-only plugin: it carries the id/params so deployments, the UI, and
the arm chain treat this like any strategy. The ACTUAL per-bar logic (strike
lock at the reference time, ref-premium capture from ticks, first-to-trigger
momentum entry) runs in the deployment evaluator's Track B branch
(app.premium_momentum_live) using the SAME pure helpers as the backtest —
evaluate() here is deliberately inert so the generic spot path can never fire.
Exits: premium stop/target from these params via signal risk_hints; the stepped
X-Y trail comes ONLY from deployment.risk.exit_controls (mode 'stepped_xy')."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from app.strategies.base import Signal, StrategyBase


class PremiumMomentum(StrategyBase):
    id = "premium_momentum"
    name = "Premium Momentum (AlgoTest-style)"
    version = "1.0.0"
    description = (
        "Locks the chosen-moneyness CE+PE strikes from spot at a reference time, "
        "then buys the FIRST side whose option premium rises by the momentum "
        "threshold. Exits on premium stop/target (+ stepped trail via deployment "
        "exit_controls) — evaluated by the Track B premium branch, not this class."
    )
    supported_instruments = ["NIFTY"]          # v1: NIFTY-only (spec §1)
    supported_modes = ["INTRADAY"]
    supported_timeframes = ["1m"]
    parameter_schema: Dict[str, Any] = {
        "reference_time": {"type": "str", "default": "09:31",
                           "description": "IST HH:MM bar whose close locks the strikes + refs"},
        "moneyness": {"type": "str", "default": "itm1",
                      "description": "atm | itm1 | itm2 | otm1 | otm2 (must be warehouse/stream covered)"},
        "side": {"type": "str", "default": "first_to_trigger",
                 "description": "first_to_trigger | ce | pe"},
        "momentum_pct": {"type": "float", "default": 15.0,
                         "description": "enter when premium rises this % over its ref (None if using pts)"},
        "momentum_pts": {"type": "float", "default": None,
                         "description": "absolute premium-points trigger (exactly one of pct/pts)"},
        "stop_pct": {"type": "float", "default": 20.0,
                     "description": "premium stop % below entry (guard-enforced)"},
        "target_pct": {"type": "float", "default": None,
                       "description": "premium target % above entry (None = ride to EOD)"},
        "late_lock_cutoff": {"type": "str", "default": "10:15",
                             "description": "no lock after this IST time -> session done (no_lock)"},
    }
    is_builtin = False

    def evaluate(self, row: pd.Series, prev: pd.Series, params: Dict[str, Any],
                 ctx: Dict[str, Any]) -> Signal:
        return Signal(direction="NONE", reasons=["premium_momentum runs via the Track B evaluator branch"])
```

- [ ] **Step 4: Run tests, verify pass** (container). Also verify discovery: `docker exec … python -c "from app.strategies.base import get_registry; r=get_registry(); r.auto_discover(); print(bool(r.get('premium_momentum')))"` → `True`.

- [ ] **Step 5: Commit** — `feat(premium-momentum): registration plugin (inert evaluate; Track B branch owns the logic)`.

---

## Task 3: `premium_momentum_live` — the per-bar session engine (pure-ish, injectable)

**Files:**
- Create: `backend/app/premium_momentum_live.py`
- Test: `tests/test_premium_momentum_live.py` (container)

- [ ] **Step 1: Failing tests** — cover: pre-ref holding; lock at ref bar (CE ITM1 below / PE above spot); ref capture from fresh ticks (stale ⇒ hold); late-lock cutoff ⇒ done(no_lock); trigger fires first-to-trigger with contract from the LOCK (not current spot); already-triggered/done ⇒ no re-fire; missing contract ⇒ done(strike_lock_failed) blocker surfaced.

```python
# tests/test_premium_momentum_live.py
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.premium_momentum_live import evaluate_premium_momentum_bar
from tests.test_premium_lock_store import _FakeLocks   # reuse the fake collection


def run(c):
    return asyncio.run(c)


_CONTRACTS = [
    {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14", "lot_size": 65},
    {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14", "lot_size": 65},
]


def _tickmap(d):
    # latest_tick_map shape: {instrument_key: {"last_price": x, "ts": epoch_ms}}
    def lookup():
        return d
    return lookup


def _dep(params=None):
    return {"id": "D1", "strategy_id": "premium_momentum",
            "params": {"reference_time": "09:31", "moneyness": "itm1",
                       "side": "first_to_trigger", "momentum_pct": 15.0,
                       "stop_pct": 20.0, "late_lock_cutoff": "10:15",
                       **(params or {})}}


# candle_ts helpers: 2026-07-10 09:31 IST == 04:01 UTC
TS_0929 = 1783999140000   # placeholder values are WRONG on purpose for the
TS_0931 = 1783999260000   # implementer: compute real epoch-ms in the test file
#                           with: int(datetime(2026,7,10,9,31,tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()*1000)
#                           and assert _ist_hhmm(ts) == "09:31" as a self-check.


def test_holding_before_reference_time():
    locks = _FakeLocks()
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0929, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=TS_0929 / 1000 + 60,
    ))
    assert out["outcome"] == "pre_reference"
    assert locks.docs == []                       # nothing persisted yet


def test_lock_and_ref_capture_at_reference_bar():
    locks = _FakeLocks()
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out["outcome"] == "monitoring"
    doc = locks.docs[0]
    assert doc["ce"]["strike"] == 23950 and doc["pe"]["strike"] == 24050
    assert doc["ce_ref_premium"] == 100.0 and doc["pe_ref_premium"] == 110.0


def test_stale_tick_holds_never_captures():
    locks = _FakeLocks()
    stale = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 - 10 * 60_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 - 10 * 60_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(stale), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out["outcome"] == "awaiting_ref"       # strikes locked, refs NOT captured
    doc = locks.docs[0]
    assert "ce_ref_premium" not in doc


def test_late_lock_cutoff_marks_done():
    locks = _FakeLocks()
    ts_1016 = TS_0931 + 45 * 60_000               # 10:16 IST
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=ts_1016, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=ts_1016 / 1000 + 60,
    ))
    assert out["outcome"] == "done"
    assert locks.docs[0]["done_reason"] == "no_lock"


def test_trigger_first_to_cross_uses_locked_contract():
    locks = _FakeLocks()
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    run(evaluate_premium_momentum_bar(          # bar 1: lock + refs
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    ts2 = TS_0931 + 60_000
    ticks2 = {"CE|23950": {"last_price": 116.0, "ts": ts2 + 55_000},   # +16% > 15%
              "PE|24050": {"last_price": 111.0, "ts": ts2 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=ts2, spot_close=26000.0,        # spot moved FAR — lock must hold
        contracts=_CONTRACTS, latest_tick_map=_tickmap(ticks2), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "triggered"
    assert out["direction"] == "CE"
    assert out["contract"]["instrument_key"] == "CE|23950"   # from LOCK, not spot 26000
    assert out["ref_premium"] == 100.0 and out["premium_now"] == 116.0
    # latch is NOT set here — the evaluator sets it only after the signal journals
    assert locks.docs[0]["triggered_side"] is None


def test_no_refire_when_done_or_triggered():
    locks = _FakeLocks()
    run(locks.insert_one({"deployment_id": "D1", "session_date": "2026-07-10",
                          "triggered_side": "CE", "done_for_day": False,
                          "entered_norenordno": "N1"}))
    ts2 = TS_0931 + 120_000
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "holding_position"
```

**Implementer note (not a placeholder — a required correction):** the two `TS_*` constants above MUST be computed properly in the test file header via `zoneinfo` as shown in the comment, then the placeholder literals deleted. Add a `_ist_hhmm(ts)` self-check assertion so a wrong constant fails loudly.

- [ ] **Step 2: Run, verify fail** (module not found).

- [ ] **Step 3: Implement**

```python
# backend/app/premium_momentum_live.py
"""Track B per-bar session engine for premium-momentum deployments.

Called from the deployment evaluator's Track B branch once per closed bar. Owns
the session state machine over the premium_locks store:

    pre_reference -> (lock strikes at the ref bar's close, capture refs from
    FRESH ticks) -> monitoring -> triggered (first side to cross) -> the
    EVALUATOR journals the signal + latches; entry/exit/done transitions are
    driven by auto_live + the guard's confirmed-flat hook, never here.

Uses the SAME pure helpers as the backtest (lock_reference_strike,
momentum_triggered) and the SAME live price contract as entries
(option_premium.resolve_premium, fresh-only). Stale/absent ticks HOLD — this
module never invents a price. It does NOT latch the trigger (spec: latch only
after the signal journals clean) and never touches order placement."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.live.option_premium import resolve_premium
from app.premium_lock_store import (
    capture_ref, get_lock, get_or_create_lock, mark_done,
)
from app.premium_momentum import lock_reference_strike

log = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)


def _ist_hhmm(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc) + IST_OFFSET
    return dt.strftime("%H:%M")


def _ist_session_date(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc) + IST_OFFSET
    return dt.strftime("%Y-%m-%d")


def _fresh_premium(latest_tick_map: Callable[[], Dict[str, Any]],
                   instrument_key: str, now_ts: float) -> Optional[Dict[str, Any]]:
    """FRESH tick premium via the canonical resolver, else None (HOLD)."""
    try:
        tick = (latest_tick_map() or {}).get(instrument_key)
    except Exception:
        tick = None
    res = resolve_premium(instrument_key=instrument_key, tick=tick,
                          candle_close=None, now_ts=now_ts)
    if res.get("fresh") is True and res.get("premium") is not None:
        return {"premium": float(res["premium"]), "ts": int(res.get("tick_ts") or 0)}
    return None


def _sides(params: Dict[str, Any]) -> List[str]:
    p = str(params.get("side") or "first_to_trigger").lower()
    if p == "ce":
        return ["CE"]
    if p == "pe":
        return ["PE"]
    return ["CE", "PE"]


async def evaluate_premium_momentum_bar(
    *, locks_col: Any, deployment: Dict[str, Any], instrument: str,
    candle_ts: int, spot_close: float, contracts: List[Dict[str, Any]],
    latest_tick_map: Callable[[], Dict[str, Any]], now_ts: float,
) -> Dict[str, Any]:
    """One bar of the premium-momentum session machine. Returns
    {"outcome": pre_reference|awaiting_ref|monitoring|triggered|holding_position|done,
     and on triggered: direction, contract, ref_premium, premium_now, blockers[]}."""
    dep_id = str(deployment.get("id") or "")
    params = dict(deployment.get("params") or {})
    ref_time = str(params.get("reference_time") or "09:31")
    cutoff = str(params.get("late_lock_cutoff") or "10:15")
    moneyness = str(params.get("moneyness") or "itm1")
    sides = _sides(params)
    bar_hhmm = _ist_hhmm(candle_ts)
    session = _ist_session_date(candle_ts)

    if bar_hhmm < ref_time:
        return {"outcome": "pre_reference"}

    lock = await get_lock(locks_col, deployment_id=dep_id, session_date=session)

    # --- session terminal states first ---
    if lock and lock.get("done_for_day"):
        return {"outcome": "done", "reason": lock.get("done_reason")}
    if lock and (lock.get("triggered_side") or lock.get("entered_norenordno")):
        return {"outcome": "holding_position"}

    # --- create the lock at/after the reference bar (strikes from THIS close) ---
    if lock is None:
        if bar_hhmm > cutoff:
            # never locked and past the cutoff: the session is honestly dead.
            await get_or_create_lock(locks_col, deployment_id=dep_id,
                                     session_date=session, payload={})
            await mark_done(locks_col, deployment_id=dep_id, session_date=session,
                            reason="no_lock")
            return {"outcome": "done", "reason": "no_lock"}
        payload: Dict[str, Any] = {"spot_at_ref": float(spot_close),
                                   "reference_bar_ts": int(candle_ts)}
        for side in sides:
            locked = lock_reference_strike(contracts=contracts, underlying=instrument,
                                           spot_at_ref=float(spot_close), side=side,
                                           moneyness=moneyness)
            if not locked:
                await get_or_create_lock(locks_col, deployment_id=dep_id,
                                         session_date=session, payload=payload)
                await mark_done(locks_col, deployment_id=dep_id, session_date=session,
                                reason="strike_lock_failed")
                return {"outcome": "done", "reason": "strike_lock_failed",
                        "blockers": [f"strike_lock_failed ({side} {moneyness})"]}
            # persist the FULL contract doc for the side (entry + audit need it)
            full = next((c for c in contracts
                         if str(c.get("instrument_key")) == locked["instrument_key"]), {})
            payload[side.lower()] = {**full, **locked}
        lock = await get_or_create_lock(locks_col, deployment_id=dep_id,
                                        session_date=session, payload=payload)

    # --- capture refs from FRESH ticks (first fresh tick wins; stale = HOLD) ---
    missing_ref = False
    for side in sides:
        s = side.lower()
        if lock.get(f"{s}_ref_premium") is not None:
            continue
        key = str(((lock.get(s) or {}).get("instrument_key")) or "")
        fp = _fresh_premium(latest_tick_map, key, now_ts) if key else None
        if fp is None:
            missing_ref = True
            continue
        await capture_ref(locks_col, deployment_id=dep_id, session_date=session,
                          side=s, ref_premium=fp["premium"], ref_ts=fp["ts"])
        lock[f"{s}_ref_premium"] = fp["premium"]
    if missing_ref:
        if bar_hhmm > cutoff:
            await mark_done(locks_col, deployment_id=dep_id, session_date=session,
                            reason="no_lock")
            return {"outcome": "done", "reason": "no_lock"}
        return {"outcome": "awaiting_ref",
                "blockers": ["ref_premium_unavailable (stale/absent tick — holding)"]}

    # --- monitor: first side to cross wins (CE first on a same-bar tie) ---
    from app.premium_momentum import momentum_triggered
    mom_pct = params.get("momentum_pct")
    mom_pts = params.get("momentum_pts")
    for side in sides:
        s = side.lower()
        key = str(((lock.get(s) or {}).get("instrument_key")) or "")
        fp = _fresh_premium(latest_tick_map, key, now_ts) if key else None
        if fp is None:
            continue   # this side's feed is stale THIS bar — hold it, try the other
        ref = float(lock[f"{s}_ref_premium"])
        if momentum_triggered(premium_now=fp["premium"], ref_premium=ref,
                              pct=mom_pct, pts=mom_pts):
            return {"outcome": "triggered", "direction": side,
                    "contract": dict(lock.get(s) or {}),
                    "ref_premium": ref, "premium_now": fp["premium"],
                    "blockers": []}
    return {"outcome": "monitoring"}
```

- [ ] **Step 4: Run, verify pass** (container, after computing the real TS constants).
- [ ] **Step 5: Commit** — `feat(premium-momentum): live session engine — lock/ref-capture/monitor/trigger at bar cadence`.

---

## Task 4: Evaluator branch wiring (the invasive edit — quoted anchors)

**Files:**
- Modify: `backend/app/deployment_evaluator.py`
- Test: `tests/test_premium_momentum_evaluator.py` (container)

- [ ] **Step 1: Failing container test** — build a fake deployment (`strategy_id="premium_momentum"`, ACTIVE, params as Task 3) + seed `candles_1m` fixture rows + fake tick map via the `latest_tick_lookup`/monkeypatched `upstox_stream_manager.latest_tick_map`, then call `evaluate_deployment_on_close` twice (ref bar, then trigger bar) and assert: (a) ref bar → outcome `no_setup`-equivalent (`premium_monitoring`) and a lock doc exists; (b) trigger bar → a CONFIRMED signal journaled with `option_contract.instrument_key == the LOCKED key`, `risk_hints.stop_pct == 20.0`, and the lock's `triggered_side == "CE"`; (c) same trigger bar re-run → `already_evaluated_this_bar` (idempotency intact). Write the test with the repo's `_FakeCol`-style fakes for `strategy_deployments`/`signals`/`premium_locks` — copy the harness style from `tests/test_live_recovery.py`.

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement the branch.** In `backend/app/deployment_evaluator.py`, locate the block that begins `# Strategy evaluate` (the `try: eval_ctx = build_live_eval_ctx(...)` / `sig = strategy.evaluate(...)` — verified at ~:371-378) and INSERT BEFORE it:

```python
    # ---- Track B: premium-momentum deployments use the premium session engine
    # instead of the generic spot evaluate + per-bar contract re-resolution. The
    # branch REJOINS the shared signal pipeline below (audit/lifecycle/dedupe all
    # apply). See docs/superpowers/specs/2026-07-10-premium-momentum-track-b-*.md
    pm_result = None
    if strategy_id == "premium_momentum":
        from app.premium_momentum_live import evaluate_premium_momentum_bar
        from app.runtime import upstox_stream_manager
        pm_contracts = await db.option_contracts.find(
            {"underlying": instrument,
             "expiry_date": {"$gte": _ist_session_date_of_ts(candle_ts)}},
            {"_id": 0},
        ).sort([("expiry_date", 1), ("strike", 1), ("side", 1)]).to_list(length=None)
        # per-session weekly: keep only the nearest expiry >= session (blueprint
        # "current weekly"); mirrors the backtest's expiry_for_session.
        _expiries = sorted({str(c.get("expiry_date")) for c in pm_contracts if c.get("expiry_date")})
        if _expiries:
            pm_contracts = [c for c in pm_contracts if str(c.get("expiry_date")) == _expiries[0]]
        pm_result = await evaluate_premium_momentum_bar(
            locks_col=db.premium_locks, deployment=deployment, instrument=instrument,
            candle_ts=candle_ts, spot_close=float(last_bar["close"]),
            contracts=pm_contracts,
            latest_tick_map=upstox_stream_manager.latest_tick_map,
            now_ts=datetime.now(timezone.utc).timestamp(),
        )
        if pm_result.get("outcome") != "triggered":
            await _mark_deployment_evaluated(db, deployment_id, candle_ts)
            return {"deployment_id": deployment_id, "outcome": "no_setup",
                    "reason": f"premium_{pm_result.get('outcome')}",
                    "pm": {k: pm_result.get(k) for k in ("outcome", "reason", "blockers")},
                    "candle_ts": candle_ts}
```

Add the tiny helper next to `_ist_time_of_ts` (same style):

```python
def _ist_session_date_of_ts(ts_ms: int) -> str:
    dt_utc = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
    return (dt_utc + IST_OFFSET).strftime("%Y-%m-%d")
```

Then make the DOWNSTREAM pipeline consume the branch's result. Immediately after the generic `sig = strategy.evaluate(...)` block, wrap the generic-only pieces:

```python
    if pm_result is None:
        direction = str(getattr(sig, "direction", "NONE") or "NONE").upper()
    else:
        direction = str(pm_result["direction"]).upper()
```

And at the contract-resolution site (`contract, contract_blocker = await _resolve_option_contract(...)` — verified ~:403-409) replace with:

```python
    if pm_result is not None:
        # Track B: the contract comes from the SESSION LOCK — never re-resolved
        # from the current bar's drifting spot (the audit's L-bypass site).
        contract, contract_blocker = dict(pm_result["contract"]), None
    else:
        contract, contract_blocker = await _resolve_option_contract(
            db,
            instrument=instrument,
            spot_price=spot_price,
            direction=direction,
            moneyness=moneyness,
        )
```

In the `risk_hints` block (verified ~:483-489), override for the branch (premium stop/target from strategy params — these WIN in `resolve_live_exit_plan`):

```python
    if pm_result is not None:
        signal_doc["risk_hints"] = {
            "target_pct": (params or {}).get("target_pct"),
            "stop_pct": (params or {}).get("stop_pct"),
            "spot_target_pts": None, "spot_stop_pts": None,
            "time_stop_minutes": None,
        }
        signal_doc["premium_momentum"] = {
            "ref_premium": pm_result["ref_premium"],
            "premium_now": pm_result["premium_now"],
        }
```

Finally, AFTER the successful `db.signals.insert_one(signal_doc)` + before `_mark_deployment_evaluated` (verified ~:513-534), latch — **only on a clean journal** (spec: a blocked signal must not burn the session):

```python
    if pm_result is not None and outcome == "clean":
        from app.premium_lock_store import latch_trigger
        await latch_trigger(db.premium_locks,
                            deployment_id=deployment_id,
                            session_date=_ist_session_date_of_ts(candle_ts),
                            side=direction)
```

Also handle the plugin's inert evaluate: in the branch case `sig` is never produced — restructure so the `try/except strategy.evaluate` block is skipped entirely when `strategy_id == "premium_momentum"` (set `sig = None`; every later `getattr(sig, ...)` already has defaults, but for the branch the score/confidence should be set explicitly: use `confidence=100` and `reasons=[f"premium +{...}% over ref"]` when building `create_signal_doc` for the branch — mirror the exact call at ~:467-476 with those two args swapped).

- [ ] **Step 4: Run the Task 4 container test → PASS**, plus the existing evaluator suite: `tests/test_deployment_evaluator.py` → PASS (generic path byte-identical: `pm_result is None` leaves every line untouched).
- [ ] **Step 5: Commit** — `feat(premium-momentum): evaluator Track B branch — lock-driven contract, journal-then-latch, shared signal pipeline`.

---

## Task 5: `stepped_xy` guard trail mode

**Files:**
- Modify: `backend/app/live/live_sl_monitor.py` (`_VALID_MODES` :52; `build_monitor_state` trail copy :132-138; `evaluate_exit` trail chain :193-219)
- Test: `tests/test_live_sl_monitor.py` (append)

- [ ] **Step 1: Failing tests** (append to `tests/test_live_sl_monitor.py`, matching its existing style):

```python
# --- Track B: stepped_xy trail (AlgoTest X-Y ratchet, backtest-parity) --------
from app.premium_momentum import stepped_trail_stop


def test_stepped_xy_matches_backtest_helper_worked_example():
    # entry 200, stop 20% -> base 160; x=20 y=20: peak 220 -> stop 195? NO —
    # helper: base + floor(favorable/x)*y capped at peak. favorable=20 -> 160+20=180.
    st = build_monitor_state(200.0, stop_pct=20.0,
                             trail={"mode": "stepped_xy", "x": 20.0, "y": 20.0})
    r1 = evaluate_exit(st, 220.0)                 # new peak 220
    # ratchet uses the PREVIOUS peak (200) => favorable 0 => stop stays 160
    assert r1["state"]["stop_level"] == 160.0
    r2 = evaluate_exit(r1["state"], 221.0)        # prev peak 220 -> favorable 20
    expected = stepped_trail_stop(entry_premium=200.0, running_high=220.0,
                                  base_stop=160.0, x=20.0, y=20.0)
    assert r2["state"]["stop_level"] == expected == 180.0


def test_stepped_xy_new_high_tick_never_exits_against_its_own_ratchet():
    # Aggressive y >> x: the tick that makes the new high must NOT be judged
    # against a stop ratcheted BY that same tick (backtest look-ahead parity).
    st = build_monitor_state(200.0, stop_pct=20.0,
                             trail={"mode": "stepped_xy", "x": 10.0, "y": 100.0})
    r = evaluate_exit(st, 260.0)                  # huge up-tick
    assert r["exit"] is False                     # no same-tick self-trap


def test_stepped_xy_monotonic_and_capped_at_prior_peak():
    st = build_monitor_state(200.0, stop_pct=20.0,
                             trail={"mode": "stepped_xy", "x": 10.0, "y": 100.0})
    r1 = evaluate_exit(st, 260.0)                 # peak now 260, stop still 160
    r2 = evaluate_exit(r1["state"], 250.0)        # prev peak 260: base+6*100 capped at 260
    assert r2["state"]["stop_level"] == 260.0     # cap = prior traded high
    assert r2["exit"] is True                     # 250 <= 260 -> trailing_stop
    assert r2["reason"] == "trailing_stop"


def test_stepped_xy_requires_base_stop_and_xy():
    # mode present but x/y missing -> behaves as fixed stop (no ratchet, no crash)
    st = build_monitor_state(200.0, stop_pct=20.0, trail={"mode": "stepped_xy"})
    r = evaluate_exit(st, 240.0)
    assert r["exit"] is False and r["state"]["stop_level"] == 160.0
```

- [ ] **Step 2: Run, verify fail** — `ValueError: trail mode 'stepped_xy' not in (...)`.

- [ ] **Step 3: Implement.** Three edits in `live_sl_monitor.py`:

(a) `_VALID_MODES = ("none", "breakeven", "lock", "lock_trail", "trail", "stepped_xy")`

(b) In `build_monitor_state`, extend the trail-dict copy (:132-138) with the two new keys:

```python
        "trail": {
            "trigger": trail.get("trigger"),
            "lock_profit": trail.get("lock_profit"),
            "step": trail.get("step"),
            "raise_by": trail.get("raise_by"),
            "gap": trail.get("gap"),
            "x": trail.get("x"),
            "y": trail.get("y"),
        },
```

(c) In `evaluate_exit`, CAPTURE the prior peak before the peak update (insert immediately before `# 2. Peak`):

```python
    prev_peak = new_state["peak"]
```

and add the mode branch after the `elif mode == "trail":` block:

```python
    elif mode == "stepped_xy":
        # AlgoTest discrete X-Y ratchet, delegated to the SAME pure helper the
        # backtest uses (byte-parity incl. the never-above-high-water cap). The
        # ratchet consumes the peak through the PREVIOUS observation (prev_peak)
        # so the tick that sets a new high is never judged against a stop it
        # raised itself — mirroring the backtest's loop-end high-water update.
        x, y = trail.get("x"), trail.get("y")
        initial = new_state.get("initial_stop")
        if _finite_pos(x) and _finite_pos(y) and initial is not None:
            from app.premium_momentum import stepped_trail_stop
            _raise_stop(new_state, stepped_trail_stop(
                entry_premium=entry, running_high=prev_peak,
                base_stop=float(initial), x=float(x), y=float(y)))
```

- [ ] **Step 4: Run the sl-monitor suite → PASS** (new + all pre-existing; other modes byte-identical). Also re-run `tests/test_live_position_guard.py` (the guard consumes `evaluate_exit` unchanged).
- [ ] **Step 5: Commit** — `feat(guard): stepped_xy trail mode — backtest-parity X-Y ratchet via the shared pure helper`.

---## Task 6: Subscription pin helper (all three drop paths)

**Files:**
- Create: `backend/app/premium_pin.py`
- Modify: `backend/app/runtime.py` (`_auto_follow_option_stream`, the paper-keys union at the `db.paper_trades.distinct(...)` anchor ~:1257)
- Test: `tests/test_premium_pin.py` (container) + a host string-pin

- [ ] **Step 1: Failing tests**

```python
# tests/test_premium_pin.py
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.premium_pin import premium_pin_keys
from tests.test_premium_lock_store import _FakeLocks


def run(c):
    return asyncio.run(c)


def test_pin_keys_are_todays_lock_keys(monkeypatch=None):
    locks = _FakeLocks()
    run(locks.insert_one({"deployment_id": "D1", "session_date": "2026-07-10",
                          "ce": {"instrument_key": "KC"}, "pe": {"instrument_key": "KP"}}))
    keys = run(premium_pin_keys(locks, now_session_date="2026-07-10"))
    assert sorted(keys) == ["KC", "KP"]


def test_pin_survives_lock_read_failure():
    class _Boom:
        def find(self, *a, **k):
            raise RuntimeError("db down")
    keys = run(premium_pin_keys(_Boom(), now_session_date="2026-07-10"))
    assert keys == []          # pin failure must NEVER break a stream restart


def test_auto_follow_unions_premium_pins_source_pin():
    # host string-pin: the union must sit in _auto_follow_option_stream AFTER the
    # cap (same as open paper keys) so pins are cap-exempt.
    src = (Path(__file__).resolve().parents[1] / "backend/app/runtime.py").read_text(encoding="utf-8")
    assert "premium_pin_keys" in src
    i_pin = src.index("premium_pin_keys")
    i_paper = src.index('db.paper_trades.distinct("instrument_key"')
    assert abs(i_pin - i_paper) < 2000   # same union block, not a distant stray
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

```python
# backend/app/premium_pin.py
"""Subscription pinning for premium-momentum locked strikes (Track B).

Today NOTHING pins an option key: the live subscription is an ATM-centered band
that is periodically rebuilt, so a strike locked at 09:31 silently drops out of
the tick feed when spot drifts. This helper returns today's locked keys so every
subscription (re)build unions them in — cap-exempt, same as open paper keys.
Fail-soft: any store error returns [] (a pin failure must never break a stream
restart; the monitor then HOLDs and entries refuse visibly)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from app.premium_lock_store import today_locked_keys

log = logging.getLogger(__name__)


def _today_ist() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


async def premium_pin_keys(locks_col: Any, *, now_session_date: Optional[str] = None) -> List[str]:
    try:
        return await today_locked_keys(locks_col, session_date=now_session_date or _today_ist())
    except Exception as exc:
        log.warning("premium_pin_keys failed (%s) — no pins this rebuild", exc)
        return []
```

In `backend/app/runtime.py`, inside `_auto_follow_option_stream`, find the open-paper-keys union (anchor: `open_keys = await db.paper_trades.distinct("instrument_key", {"status": "OPEN"})`) and add directly below it, mirroring how `open_keys` is appended after the cap:

```python
        # Track B: pin today's premium-momentum LOCKED strikes (cap-exempt, like
        # open paper keys) so ATM drift / rebuilds never drop a locked feed.
        from app.premium_pin import premium_pin_keys
        pin_keys = await premium_pin_keys(db.premium_locks)
```

…and union `pin_keys` into the final subscription list exactly where `open_keys` is unioned (same list-extend/dedupe lines — quote them in the diff when editing).

Then grep for OTHER build sites: `grep -rn "build_live_option_universe(" backend/app --include=*.py` — for EACH call site that constructs a subscription (supervisor revive, manual options-stream restart route), apply the same two-line union. Expected: 1-2 additional sites; if a site cannot accept extra keys, report DONE_WITH_CONCERNS naming it.

- [ ] **Step 4: Run tests → PASS** (container + host pin). Re-run `tests/test_live_recovery.py` (runtime import surface).
- [ ] **Step 5: Commit** — `feat(premium-momentum): pin locked strikes into every option-stream rebuild (cap-exempt)`.

---

## Task 7: Recovery — rehydrate locks + entered positions

**Files:**
- Modify: `backend/app/runtime.py` (`live_startup_recovery` — add step; `maybe_run_live_recovery` wraps it already)
- Test: `tests/test_premium_momentum_recovery.py` (container)

- [ ] **Step 1: Failing test** — fake `premium_locks` with one entered lock (`entered_norenordno="N1"`, `entry_premium=115.0`, ce contract, deployment D1, today's session) + a fake broker book showing the position still held + a fake registry; call the new `rehydrate_premium_momentum(...)` and assert: registry.register called with `entry_price=115.0` (the PERSISTED entry, not a 50% default), `source="auto_live"`, `deployment_id="D1"`, and a `stepped_xy` trail dict when the deployment's `risk.exit_controls` carries one. A lock whose position is NO LONGER in the broker book → `mark_done(reason="exited_while_down")` and no register.

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** a new function in `runtime.py` (placed next to the existing recovery steps; full code):

```python
async def rehydrate_premium_momentum(db, registry, broker_positions_by_tsym) -> Dict[str, Any]:
    """Track B recovery: re-attach the guard to premium-momentum positions using
    the PERSISTED lock state (entry premium, deployment, exit plan) instead of
    the generic 50%-catastrophe rehydrate. Locks whose position is gone are
    closed out honestly (done_for_day='exited_while_down'). Never raises."""
    from app.live.live_sl_monitor import build_monitor_state
    from app.premium_lock_store import mark_done
    out = {"reattached": 0, "closed": 0, "errors": 0}
    try:
        today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        locks = await db.premium_locks.find(
            {"session_date": today, "entered_norenordno": {"$ne": None},
             "done_for_day": False}, {"_id": 0}).to_list(length=None)
        for lock in locks:
            try:
                side = str(lock.get("triggered_side") or "").lower()
                contract = dict(lock.get(side) or {})
                tsym = str(contract.get("trading_symbol") or contract.get("tsym") or "")
                dep = await db.strategy_deployments.find_one(
                    {"id": lock["deployment_id"]}, {"_id": 0})
                pos = broker_positions_by_tsym.get(tsym)
                if not pos:
                    await mark_done(db.premium_locks, deployment_id=lock["deployment_id"],
                                    session_date=today, reason="exited_while_down")
                    out["closed"] += 1
                    continue
                params = dict((dep or {}).get("params") or {})
                risk = dict((dep or {}).get("risk") or {})
                entry = float(lock.get("entry_premium") or 0) or None
                if entry is None:
                    continue  # no persisted entry -> leave to the generic rehydrate
                state = build_monitor_state(
                    entry, stop_pct=params.get("stop_pct") or 50.0,
                    target_pct=params.get("target_pct"),
                    trail=risk.get("exit_controls"))
                registry.register(
                    key=str(lock["entered_norenordno"]), tsym=tsym,
                    exch=str(contract.get("exch") or "NFO"),
                    qty=int(pos.get("netqty") or 0), prd="I",
                    entry_price=entry, state=state, source="auto_live",
                    deployment_id=str(lock["deployment_id"]))
                out["reattached"] += 1
            except Exception:
                out["errors"] += 1
                log.exception("premium-momentum rehydrate failed for lock %s", lock.get("deployment_id"))
    except Exception:
        out["errors"] += 1
        log.exception("premium-momentum rehydrate scan failed")
    return out
```

Wire it as a numbered step inside `live_startup_recovery` right after the existing guard `rehydrate_from_broker` step (grep `rehydrate_from_broker` for the anchor), building `broker_positions_by_tsym` from the SAME position-book read that step already performs (do NOT add a second broker read — reuse the in-scope book variable; quote it when editing). Run it BEFORE the generic rehydrate if ordering allows so premium positions get their persisted state instead of the 50% default; if the generic step keys by norenordno and skips already-registered keys, order after is also safe — verify which and note in the commit message.

- [ ] **Step 4: Run tests → PASS**; re-run `tests/test_live_recovery.py`.
- [ ] **Step 5: Commit** — `feat(premium-momentum): recovery rehydrates entered positions with persisted exit state + closes dead locks`.

---

## Task 8: Last-line trigger re-check + failure visibility + UI labels

**Files:**
- Modify: `backend/app/auto_live.py` (after the `resolve_live_entry_ref_ltp` refusal), `backend/app/premium_lock_store.py` (already has `unlatch_trigger`), `frontend/src/components/live/LiveDeploymentStrip.jsx` (entryErrorLabel map)
- Test: `tests/test_premium_momentum_entry_recheck.py` (container) + host JSX pin appended to `tests/test_premium_momentum.py`

- [ ] **Step 1: Failing test** — call `auto_live_trade_for_signal` with a premium-momentum signal (`signal_doc["premium_momentum"] = {"ref_premium": 100.0, ...}`, deployment params `momentum_pct=15`) and a tick lookup whose fresh premium is **114.0** (below the 115 trigger): assert the result refuses with `live_trade_error == "premium_trigger_not_met"` journaled on the signal, the claim released, and the lock latch released (`triggered_side` back to None so a later bar can re-trigger). With premium **116.0** → proceeds past the re-check (reaches the normal place/arm flow of the existing harness fakes).

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement.** In `auto_live_trade_for_signal`, find the fresh-tick refusal block (anchor: the `resolve_live_entry_ref_ltp(...)` call and its `live_trade_error` journaling — grep `live_entry_premium` in auto_live.py, quote the exact refusal shape). Immediately AFTER `ref_ltp` resolves successfully, insert:

```python
    # Track B last-line re-check: the momentum trigger was decided on the bar
    # close; the premium may have collapsed in the seconds before placement.
    # Re-verify against the FRESH entry tick (ref_ltp IS premium_now). On
    # failure: journal a distinct refusal, release the claim AND the session
    # latch so a later bar may re-trigger. Marginally more conservative than
    # the backtest's trigger-bar-close fill — intentional (spec §5.4).
    pm = signal_doc.get("premium_momentum") or {}
    if pm.get("ref_premium") is not None:
        from app.premium_momentum import momentum_triggered
        dep_params = dict(deployment.get("params") or {})
        if not momentum_triggered(premium_now=float(ref_ltp),
                                  ref_premium=float(pm["ref_premium"]),
                                  pct=dep_params.get("momentum_pct"),
                                  pts=dep_params.get("momentum_pts")):
            await db.signals.update_one(
                {"id": signal_doc["id"]},
                {"$set": {"live_trade_error": "premium_trigger_not_met",
                          "live_intended": {"ref_premium": pm["ref_premium"],
                                            "premium_at_entry": float(ref_ltp)}}})
            await release_live_trade_claim(db, signal_doc["id"])
            from app.premium_lock_store import unlatch_trigger
            from datetime import timedelta as _td
            _sess = (datetime.now(timezone.utc) + _td(hours=5, minutes=30)).strftime("%Y-%m-%d")
            await unlatch_trigger(db.premium_locks,
                                  deployment_id=str(deployment.get("id") or ""),
                                  session_date=_sess)
            return {"created": False, "reason": "premium_trigger_not_met"}
```

(Adapt the journaling keys to the EXACT shape of the adjacent stale-premium refusal — quote it and mirror field-for-field.) Then in `LiveDeploymentStrip.jsx`, extend the `entryErrorLabel` map (grep `entryErrorLabel`) with:

```js
  premium_trigger_not_met: "premium fell back below the trigger before placement",
  strike_lock_failed: "could not lock the strike at the reference time",
  ref_premium_unavailable: "no fresh option tick to capture the reference premium",
```

Append to `tests/test_premium_momentum.py` a host pin asserting those three keys exist in the JSX source. **Post-fill hook:** in the same function, at the success path where the order is placed/armed (the point that returns `created: True` — quote it), call `mark_entered(db.premium_locks, deployment_id=…, session_date=…, norenordno=<the placed order id in scope>, entry_premium=float(ref_ltp))`.

**Confirmed-flat → done:** in `runtime.py`, the guard's close hook (`_live_guard_on_close` — grep it; it journals live_trades on a real fill) gains two lines: if the closed entry's `deployment_id` belongs to a premium-momentum deployment (`db.strategy_deployments.find_one` on id, check `strategy_id`), call `mark_done(db.premium_locks, …, reason="exited")` for today's session. Full code mirrors the hook's existing style; never raises.

- [ ] **Step 4: Run tests → PASS** (container + host pins + babel-parse the JSX).
- [ ] **Step 5: Commit** — `feat(premium-momentum): last-line trigger re-check, entered/exited lock transitions, refusal labels`.

---

## Task 9: Full sweep, adversarial review gate, rebuild

- [ ] **Step 1: Host sweep** — `python -m pytest tests/test_premium_momentum*.py tests/test_premium_lock_store.py tests/test_premium_pin.py -q` → all pass.
- [ ] **Step 2: Container sweep** — sync `backend/app` + `tests/`, run: all Track B files + `test_live_sl_monitor.py test_live_position_guard.py test_deployment_evaluator.py test_live_recovery.py test_auto_live*.py -q` → pass (modulo documented flattened-layout false-fails).
- [ ] **Step 3: STOP — adversarial review checkpoint.** This is live-money code: run the session's established multi-lens red-team (look-ahead/fail-open/concurrency/recovery lenses) over the Track B diff BEFORE rebuild. Fix confirmed findings; re-run sweeps.
- [ ] **Step 4: Rebuild + smoke** — `docker compose up -d --build backend` → health `{"db":"ok"}`; create a paper premium-momentum deployment via the existing deployments API and confirm the evaluator logs `premium_pre_reference`/`premium_monitoring` outcomes (off-hours: `pre_reference` is the expected steady state).
- [ ] **Step 5: Commit any review fixes; update the memory file** with Track B status. Do NOT push (standing rule).

---

## Self-review notes (author)

- **Spec coverage:** seam 1→Task 1; seam 2+4→Tasks 3/4; seam 3→Task 3; seam 5+last-line→Task 8; seam 6→Task 5; seam 7→Task 6; seam 8→Task 7 (+EOD inherited, no task needed); failure visibility→Task 8; no-arming-gate→no task adds any gate (verify in review); Layer-1/2 constraints→Tasks 4/5/8 notes (latch-after-journal, prev-peak ratchet, done-on-confirm-flat only).
- **Known intentional deviations:** none. v1 NIFTY-only is enforced by the plugin's `supported_instruments`.
- **Type consistency:** lock doc field names (`ce`/`pe` dicts, `ce_ref_premium` flat) used identically in Tasks 1/3/6/7/8; `evaluate_premium_momentum_bar` outcomes consumed by Task 4 exactly as returned by Task 3.
- **Anchors:** quoted-code anchors are used everywhere lines may have drifted; Task 0 verifies symbols before any edit.
