# Strategy → Deploy to Live Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an armed deployment auto-place real Flattrade orders for its continuous signals, through the existing executor choke-point + a full-parity multi-position software guard, under hard per-deployment + account caps.

**Architecture:** A new `auto_live.py` tee in `evaluate_active_deployments` (mirrors `paper_auto.py`) resolves a fresh premium + capped lots + the source-run exit plan and calls a new `executor.place_deployed_order` (a sibling of `place_live_test_order` sharing one `client.place_order` site via an extracted `_transmit_and_arm`). Exits run in the extended `live_position_guard` (premium SL/TP/trail + spot-mirror + time-stop + 15:00 EOD square). Authorization is a per-deployment `risk.live` arm with daily auto-disarm; placement is dry-run-logged unless `LIVE_AUTOPLACE_ARMED=1`.

**Tech Stack:** Python 3.12 / FastAPI / motor (Mongo) backend; pytest (host-only, `MockNoren`, never imports `server.py`); React (CRA) frontend.

**Spec:** `docs/superpowers/specs/2026-06-25-strategy-deploy-to-live-design.md` (read it first).

**Worktree:** Implement in an isolated git worktree off `main` (branch `feat/strategy-deploy-to-live`), created via `superpowers:using-git-worktrees` at execution start. **Do NOT build on `main`.**

**Reference (do NOT trust blindly):** `git stash@{0}` ("runaway plan-drafter code … partial 3/7 sections") holds an *unreviewed* partial implementation of Tasks 4–6 from a drafting mishap. You MAY `git stash show -p stash@{0}` to consult an approach, but every line must be re-derived through this plan's TDD + review — especially the executor (real-money choke-point).

**Conventions:** premium-never-spot fills; lot size always from the broker-resolved scrip; OPEN trades never deletable; IST everywhere; theme tokens + kebab-case testids on the FE; contract tests in the same commit as the route/testid they pin (`tests/contract_corpus.py`). Commit after every green step (no push without explicit user instruction).

---

## File structure (decomposition lock-in)

| File | New/Mod | Responsibility |
|---|---|---|
| `backend/app/live/mode.py` | Mod | + `is_deployment_live_allowed(deployment, now_utc, *, connected)` + `armed_until_today_ist()` pure helpers. Manual single-shot untouched. |
| `backend/app/live/kill_switch.py` | Mod | `SafetyConfigStore` gains `max_lots_per_order` (default 20, ≥1). |
| `backend/app/live/executor.py` | Mod | Extract `_transmit_and_arm`; add `place_deployed_order`. One `client.place_order` site preserved. |
| `backend/app/live/safety.py` | Read/Mod | Confirm/expose `RateThrottle.allow(...)` sync API. |
| `backend/app/live_deploy_governor.py` | New | `check_live_caps(db, deployment, *, capped_lots, now_utc)` per-deployment caps (reuses `deployment_kill_switch`). |
| `backend/app/auto_live.py` | New | Continuous live sink (clone of `paper_auto.py`): enable predicate, atomic claim, capped lots, exit plan, fresh-premium, orchestrator. |
| `backend/app/deployment_evaluator.py` | Mod | Tee: armed → `auto_live` (replace), else → `auto_paper`. |
| `backend/app/live/live_position_guard.py` | Mod | `register(...)` + cycle: spot-mirror + time-stop + 15:00 IST EOD square for deployed positions. |
| `backend/app/live/live_sl_monitor.py` | Read | Reuse `build_monitor_state` / `evaluate_exit` (no change expected). |
| `backend/server.py` | Mod | Wire `spot_tick_fn` + EOD into the `LivePositionGuard` construction (verified in Docker; contract-corpus string assert). |
| `backend/app/routers/deployments.py` | Mod | `POST /{id}/live/arm|disarm|stop`, `GET /{id}/live/status`; extend `/stop-all`. |
| `backend/app/routers/live_broker.py` | Mod | `_SafetyConfigBody` + PUT gains `max_lots_per_order`. |
| `frontend/src/lib/api.js` | Mod | Live-deploy API methods. |
| `frontend/src/pages/LiveTrading.jsx` / `PaperTrading.jsx` + `components/live/*` | Mod/New | Caps form + danger arm dialog + Live Deployments strip + banner. |
| `tests/test_live_mode.py`, `test_live_kill_switch.py`, `test_live_executor_deployed.py`(new), `test_live_deploy_governor.py`(new), `test_auto_live.py`(new), `test_deployment_evaluator.py`, `test_live_position_guard.py`, route + contract tests | New/Mod | TDD coverage. |

**Canonical `deployment.risk.live` shape (single source — all tasks use this exact shape):**
```jsonc
"risk": { "live": {
  "armed": true, "armed_at": "<iso utc>", "armed_until": "<iso utc = today 15:00 IST>",
  "lots": 2, "max_lots_per_day": 10, "max_concurrent": 1, "daily_loss_cap": 5000.0,
  "armed_by": "user", "disarmed_reason": null
}}
```
Caps live **directly under `risk.live`** (not a `risk.live_caps` sub-key). `live_trades` is a new Mongo collection mirroring `paper_trades` fields + `{norenordno, cid, deployment_id, source:"auto_live_on_signal", lots, entry_price, risk:{stop_price,target_price}, spot_exit, time_stop_minutes, status, verdicts, created_at}`.

---

## Task 1: `is_deployment_live_allowed` + `armed_until_today_ist` (authorization predicate)

**Files:**
- Modify: `backend/app/live/mode.py` (add two module-level functions; `ModeStore` untouched)
- Test: `tests/test_live_mode.py` (append)

- [ ] **Step 1: Write the failing test**
```python
# append to tests/test_live_mode.py
from datetime import datetime, timezone, timedelta
from app.live.mode import is_deployment_live_allowed, armed_until_today_ist

_IST = timedelta(hours=5, minutes=30)

def _dep(**live):
    base = {"armed": True, "armed_until": "2026-06-25T09:30:00+00:00"}  # 15:00 IST
    base.update(live)
    return {"risk": {"live": base}}

def test_live_allowed_when_armed_connected_before_until():
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)  # 11:30 IST
    ok, reason = is_deployment_live_allowed(_dep(), now, connected=True)
    assert ok is True and reason == "ok"

def test_live_blocked_when_not_armed():
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
    ok, reason = is_deployment_live_allowed(_dep(armed=False), now, connected=True)
    assert ok is False and reason == "not_armed"

def test_live_blocked_after_armed_until():
    now = datetime(2026, 6, 25, 10, 0, tzinfo=timezone.utc)  # 15:30 IST, past 15:00
    ok, reason = is_deployment_live_allowed(_dep(), now, connected=True)
    assert ok is False and reason == "arm_expired"

def test_live_blocked_when_not_connected():
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
    ok, reason = is_deployment_live_allowed(_dep(), now, connected=False)
    assert ok is False and reason == "not_connected"

def test_live_fail_closed_on_malformed():
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
    assert is_deployment_live_allowed({}, now, connected=True)[0] is False
    assert is_deployment_live_allowed({"risk": {"live": "x"}}, now, connected=True)[0] is False
    assert is_deployment_live_allowed({"risk": {"live": {"armed": True}}}, now, connected=True)[0] is False  # no armed_until

def test_armed_until_today_ist_is_1500_ist_in_utc():
    now = datetime(2026, 6, 25, 4, 0, tzinfo=timezone.utc)  # 09:30 IST
    out = armed_until_today_ist(now)
    # 15:00 IST == 09:30 UTC
    assert out == "2026-06-25T09:30:00+00:00"
```

- [ ] **Step 2: Run it (expect FAIL — ImportError)**
Run: `python -m pytest tests/test_live_mode.py -k "deployment_live or armed_until" -q`
Expected: FAIL (`cannot import name 'is_deployment_live_allowed'`).

- [ ] **Step 3: Implement (append to `backend/app/live/mode.py`)**
```python
def armed_until_today_ist(now_utc: "datetime") -> str:
    """ISO-UTC timestamp for 15:00 IST on now_utc's IST date (the EOD square cutoff)."""
    from datetime import datetime, timezone, timedelta
    ist = now_utc.astimezone(timezone.utc) + timedelta(hours=5, minutes=30)
    cutoff_ist = ist.replace(hour=15, minute=0, second=0, microsecond=0)
    return (cutoff_ist - timedelta(hours=5, minutes=30)).replace(tzinfo=timezone.utc).isoformat()


def is_deployment_live_allowed(deployment, now_utc, *, connected: bool):
    """(ok, reason) — True iff risk.live armed, now_utc < armed_until, and connected.
    Fail-closed: any missing/malformed field or expired arm → (False, reason)."""
    from datetime import datetime, timezone
    if not isinstance(deployment, dict):
        return False, "no_deployment"
    live = ((deployment.get("risk") or {}).get("live")) if isinstance(deployment.get("risk"), dict) else None
    if not isinstance(live, dict):
        return False, "not_armed"
    if live.get("armed") is not True:
        return False, "not_armed"
    raw = live.get("armed_until")
    if not raw:
        return False, "arm_expired"
    try:
        until = datetime.fromisoformat(str(raw))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False, "arm_expired"
    if now_utc >= until:
        return False, "arm_expired"
    if connected is not True:
        return False, "not_connected"
    return True, "ok"
```

- [ ] **Step 4: Run it (expect PASS)**
Run: `python -m pytest tests/test_live_mode.py -q`
Expected: PASS (existing mode tests + the 6 new ones).

- [ ] **Step 5: Commit**
```bash
git add backend/app/live/mode.py tests/test_live_mode.py
git commit -m "feat(live): is_deployment_live_allowed + armed_until_today_ist (per-deployment arm predicate)"
```

---

## Task 2: account lot ceiling `max_lots_per_order` in `SafetyConfigStore`

**Files:**
- Modify: `backend/app/live/kill_switch.py` (`SafetyConfigStore` defaults + `put_config` validation)
- Test: `tests/test_live_kill_switch.py` (append)

First **read** `backend/app/live/kill_switch.py` to match the existing `SafetyConfigStore` default-doc + `put_config` numeric-validation pattern (it already validates `daily_loss_limit` etc.).

- [ ] **Step 1: Write the failing test**
```python
# append to tests/test_live_kill_switch.py
import pytest
from app.live.kill_switch import SafetyConfigStore

class _FakeColl:
    def __init__(self): self.doc = None
    async def find_one(self, *_a, **_k): return self.doc
    async def update_one(self, _f, update, upsert=False):
        self.doc = {**(self.doc or {}), **update["$set"]}

@pytest.mark.asyncio
async def test_max_lots_per_order_defaults_to_20():
    store = SafetyConfigStore(_FakeColl())
    cfg = await store.get_config()
    assert cfg["max_lots_per_order"] == 20

@pytest.mark.asyncio
async def test_put_max_lots_per_order_accepts_positive_int():
    store = SafetyConfigStore(_FakeColl())
    cfg = await store.put_config({"max_lots_per_order": 5})
    assert cfg["max_lots_per_order"] == 5

@pytest.mark.asyncio
async def test_put_max_lots_per_order_rejects_below_one():
    store = SafetyConfigStore(_FakeColl())
    with pytest.raises(ValueError):
        await store.put_config({"max_lots_per_order": 0})
```
(If the existing tests use a different fake-collection helper, reuse that instead of `_FakeColl`.)

- [ ] **Step 2: Run it** — `python -m pytest tests/test_live_kill_switch.py -k max_lots_per_order -q` → FAIL (KeyError / no validation).

- [ ] **Step 3: Implement** — in `kill_switch.py`: add `"max_lots_per_order": 20` to the `SafetyConfigStore` default config dict; in `put_config`, where numeric fields are validated, add:
```python
if "max_lots_per_order" in updates:
    v = updates["max_lots_per_order"]
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise ValueError("max_lots_per_order must be an integer >= 1")
```

- [ ] **Step 4: Run it** — `python -m pytest tests/test_live_kill_switch.py -q` → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/live/kill_switch.py tests/test_live_kill_switch.py
git commit -m "feat(live): account-level max_lots_per_order ceiling (default 20) in SafetyConfigStore"
```

---

## Task 3: Executor refactor — extract `_transmit_and_arm` (behavior-preserving)

**Files:**
- Modify: `backend/app/live/executor.py` (`place_live_test_order` post-claim block → new `_transmit_and_arm`)
- Test: `tests/test_live_executor.py` (existing suite is the regression guard) + `tests/test_live_executor_deployed.py` (new, Step 1 below)

**Read first:** `backend/app/live/executor.py` in full and `tests/test_live_executor.py` for the `MockNoren` + fake `mode_store`/`intent_store`/`engine`/`arm` fixtures.

The refactor: factor the block from `record_intent` → `claim_for_submit` → **the single `await client.place_order(intent)`** → `mark_submitted` → `post_fill()` → `arm`, all inside the existing `_abort_protect` try/except, into:
```python
async def _transmit_and_arm(*, client, intent, cid, engine, intent_store, arm,
                            ref_ltp, band_pct, uid, actid, verdicts,
                            post_fill=None, mode="live", deployment_id=None):
    """The SOLE place_order site + atomic claim + arm-or-abort. NEVER lets an
    unprotected fill persist. post_fill (e.g. consume_single_shot) runs after
    mark_submitted, before arm — preserving the manual path's step ordering."""
    await intent_store.record_intent(intent, mode=mode, **({"deployment_id": deployment_id} if deployment_id else {}))
    if not await intent_store.claim_for_submit(cid):
        return _blocked("already_claimed", verdicts)
    result = await client.place_order(intent)          # THE ONLY place_order CALL
    if not result.ok:
        return {"placed": False, "reason": f"reject:{result.rejreason}", "verdicts": verdicts}
    try:
        await intent_store.mark_submitted(cid, result.norenordno)
        if post_fill is not None:
            await post_fill()
        await arm(intent, result.norenordno)
        return {"placed": True, "protected": True, "norenordno": result.norenordno, "cid": cid, "verdicts": verdicts}
    except Exception as exc:
        return await _abort_protect(client, engine, intent, result.norenordno,
                                    ref_ltp, band_pct, uid, actid, reason=f"post_place_failed:{exc}")
```
`place_live_test_order` keeps Gates 0–6 unchanged and replaces its tail (record→claim→place→consume→arm) with `return await _transmit_and_arm(..., post_fill=mode_store.consume_single_shot, mode="live")`. (`record_intent` forwards `deployment_id` only when set, so the older test fakes that don't accept it still work.)

- [ ] **Step 1: Write the failing test** (new file — pins the single-call-site invariant + manual-path regression)
```python
# tests/test_live_executor_deployed.py  (Task 3 portion; Task 4 appends more)
import ast, pathlib

def test_executor_has_exactly_one_place_order_call_site():
    src = pathlib.Path("backend/app/live/executor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "place_order"]
    assert len(calls) == 1, f"executor.py must have exactly ONE place_order call site, found {len(calls)}"
```

- [ ] **Step 2: Run it** — `python -m pytest tests/test_live_executor_deployed.py -q` → may PASS already (1 site today); the real guard is Step 4's full regression.

- [ ] **Step 3: Implement** the `_transmit_and_arm` extraction as above; rewire `place_live_test_order`'s tail to call it.

- [ ] **Step 4: Run the full executor regression** (this is the behavior-preserving gate)
Run: `python -m pytest tests/test_live_executor.py tests/test_live_validate_and_build.py tests/test_live_executor_deployed.py -q`
Expected: PASS — every existing `place_live_test_order` test (place-once, reject-no-consume, arm-abort, gate blocks) still green, plus the single-call-site assertion.

- [ ] **Step 5: Commit**
```bash
git add backend/app/live/executor.py tests/test_live_executor_deployed.py
git commit -m "refactor(live): extract _transmit_and_arm (single place_order site) — behavior-preserving"
```

---

## Task 4: Executor — `place_deployed_order` + RateThrottle gate

**Files:**
- Modify: `backend/app/live/executor.py` (add `place_deployed_order`, `_autoplace_armed`, `_would_send`)
- Read: `backend/app/live/safety.py` (confirm `RateThrottle.allow(...)` signature — adapt the test/impl to the real one), `backend/app/live/order_builder.py` (`build_intent`), `backend/app/live/margin.py` (`margin_verdict`)
- Test: `tests/test_live_executor_deployed.py` (append)

Signature & gates per spec §5.2 (use the contract names verbatim):
```python
async def place_deployed_order(contract, *, side, ref_ltp, band_pct, levels, capped_lots,
                               client, intent_store, engine, search_fn, arm,
                               allow_fn, throttle, account_max_lots, deployment_id,
                               autoplace_armed=None, uid="", actid="", buffer_pct=0.5):
```
- Gate 0 long-only: `side != "B"` → `_blocked("side_must_be_buy", [])`.
- Gate 1 authorization: `ok, why = allow_fn();` not ok → `_blocked(f"not_armed:{why}", [])`. (No `consume_single_shot`, no global mode flip.)
- Gate 2 dry-run: `cid = new_client_order_id()`; `intent, verdicts, lot = build_intent(contract, side=side, order_kind="entry", lots=capped_lots, ref_ltp=ref_ltp, band_pct=band_pct, fat_finger_cap=account_max_lots, levels=levels, client_order_id=cid, buffer_pct=buffer_pct, search_fn=search_fn)`. (`account_max_lots` non-numeric → `build_intent`/`check_fat_finger` default-denies.)
- Gate 3 margin: `limits = await client.limits()`; if `lot is not None`: `verdicts.append(margin_verdict(limits, ref_ltp=ref_ltp, lot_size=lot * capped_lots))` — margin must cover the **full** size.
- Gate 4: `if intent is None or any(not v["ok"] for v in verdicts): return _blocked("dry_run_failed", verdicts)`.
- Gate 5: `if lot is None or capped_lots > account_max_lots or intent.qty != capped_lots * lot: return _blocked("not_within_lot_cap", verdicts)`.
- Gate 6: `ok, why = await engine.can_trade();` not ok → `_blocked(f"cannot_trade:{why}", verdicts)`.
- Gate 8 throttle: `if throttle is not None and not throttle.allow(is_cancel=False): return _blocked("rate_throttled", verdicts)` (adapt to the real `RateThrottle.allow` signature found in safety.py).
- Transmit boundary:
```python
armed = _autoplace_armed() if autoplace_armed is None else bool(autoplace_armed)
if not armed:
    return {"placed": False, "dry_run": True, "would_send": _would_send(intent, uid, actid), "verdicts": verdicts}
return await _transmit_and_arm(client=client, intent=intent, cid=cid, engine=engine,
        intent_store=intent_store, arm=arm, ref_ltp=ref_ltp, band_pct=band_pct,
        uid=uid, actid=actid, verdicts=verdicts, post_fill=None, mode="live", deployment_id=deployment_id)
```
Helpers:
```python
def _autoplace_armed() -> bool:
    import os
    return os.environ.get("LIVE_AUTOPLACE_ARMED", "0").strip().lower() in ("1", "true", "yes", "on")

def _would_send(intent, uid, actid):
    try:
        return intent.to_jdata(uid=uid, actid=actid) if intent is not None else None
    except Exception:
        return None
```

- [ ] **Step 1: Write the failing tests** (append to `tests/test_live_executor_deployed.py`; reuse `MockNoren` + fakes imported from `tests/test_live_executor.py`). Cover, one test each:
```python
# Sketch — fill in using the fakes from test_live_executor.py.
# _always_allow = lambda: (True, "ok"); _deny = lambda: (False, "not_armed")
# Build a 1-lot-cap and a >ceiling case; a passing-margin MockNoren and a thin-margin one.

async def test_deployed_long_only_blocks_sell():            # side="S" -> side_must_be_buy
async def test_deployed_allow_fn_false_blocks():            # allow_fn -> (False,"x") -> not_armed:x, no place
async def test_deployed_dry_run_capped_lots_qty():          # capped_lots=2 -> intent.qty == 2*lot_size; dry_run True when env unset
async def test_deployed_over_ceiling_blocked():             # capped_lots=25, account_max=20 -> not_within_lot_cap
async def test_deployed_qty_mismatch_blocked():             # force build_intent qty != capped*lot -> not_within_lot_cap
async def test_deployed_margin_full_size_blocks():          # thin limits vs 2-lot premium -> dry_run_failed
async def test_deployed_throttle_blocks():                  # throttle.allow()->False -> rate_throttled, no claim
async def test_deployed_idempotency_one_winner():           # second claim_for_submit False -> already_claimed
async def test_deployed_env_unset_is_dry_run_no_place(monkeypatch):   # LIVE_AUTOPLACE_ARMED unset -> placed False, dry_run True, MockNoren.place_calls == 0
async def test_deployed_env_set_places_and_arms(monkeypatch):         # set env -> placed True, protected True, arm called once
async def test_deployed_post_fill_raise_aborts(monkeypatch):         # arm raises -> _abort_protect: placed True protected False halted True
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_live_executor_deployed.py -q` → FAIL (`place_deployed_order` undefined).

- [ ] **Step 3: Implement** `place_deployed_order` + helpers per above.

- [ ] **Step 4: Run** — `python -m pytest tests/test_live_executor_deployed.py tests/test_live_executor.py -q` → PASS (new gate tests + single-call-site still 1 + manual regression). Confirm the AST test still reports exactly ONE place_order site.

- [ ] **Step 5: Commit**
```bash
git add backend/app/live/executor.py tests/test_live_executor_deployed.py
git commit -m "feat(live): place_deployed_order — capped-lots gate chain + RateThrottle + offline-first transmit boundary"
```

---

## Task 5: `live_deploy_governor.check_live_caps`

**Files:**
- Create: `backend/app/live_deploy_governor.py`
- Read: `backend/app/deployment_kill_switch.py` (reuse `daily_realized_summary`, `_ist_date`, `_float`, `IST`, and the `check_soft_daily_governor` pattern), `backend/app/paper_auto.py` (trade-doc fields)
- Test: `tests/test_live_deploy_governor.py` (new)

Reads caps from `deployment["risk"]["live"]` (`max_concurrent`, `max_lots_per_day`, `daily_loss_cap`). Returns `{"allow": bool, "reason": str, "pause": bool}`. Order: **daily_loss_cap first** (so a loss breach is the headline), then `max_lots_per_day`, then `max_concurrent`. No cap configured → return `{"allow": True, "reason": "ok", "pause": False}` **without a DB query**.

```python
"""Per-deployment LIVE caps (spec §6). Pure-ish: queries db.live_trades only when a cap is set."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from app.deployment_kill_switch import daily_realized_summary, _ist_date, _float, IST  # reuse, no new pnl math

def _today_ist(now_utc: Optional[datetime]) -> str:
    return _ist_date((now_utc or datetime.now(timezone.utc)))

async def check_live_caps(db, deployment: Dict[str, Any], *, capped_lots: int,
                          now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    live = (deployment.get("risk") or {}).get("live") or {}
    dep_id = str(deployment.get("id") or "")
    max_conc = live.get("max_concurrent")
    max_lots = live.get("max_lots_per_day")
    loss_cap = live.get("daily_loss_cap")
    if not any(v is not None for v in (max_conc, max_lots, loss_cap)):
        return {"allow": True, "reason": "ok", "pause": False}

    rows = await db.live_trades.find({"deployment_id": dep_id}).to_list(length=None)
    today = _today_ist(now_utc)

    if loss_cap is not None:
        realized = float(daily_realized_summary(rows, today).get("net") or 0.0)
        open_unreal = sum(_float(r.get("unrealized_pnl")) or 0.0 for r in rows
                          if str(r.get("status")) == "OPEN" and _ist_date_of_trade(r) == today)
        if realized + open_unreal <= -abs(float(loss_cap)):
            return {"allow": False, "reason": "daily_loss_cap", "pause": True}

    if max_lots is not None:
        lots_today = sum(int(r.get("lots") or 0) for r in rows if _ist_date_of_trade(r) == today)
        if lots_today + int(capped_lots) > int(max_lots):
            return {"allow": False, "reason": "max_lots_per_day", "pause": False}

    if max_conc is not None:
        open_n = sum(1 for r in rows if str(r.get("status")) == "OPEN")
        if open_n >= int(max_conc):
            return {"allow": False, "reason": "max_concurrent", "pause": False}

    return {"allow": True, "reason": "ok", "pause": False}
```
(`_ist_date_of_trade(r)` = `_ist_date` of the row's `created_at`; define a tiny local helper using `IST` consistent with `deployment_kill_switch`. If `db.live_trades.find(...)` in the FakeDB is sync, the orchestrator's real motor cursor needs `.to_list(length=None)` — keep the `await ... .to_list` form and make the FakeCursor awaitable, matching `tests/test_paper_auto.py`'s FakeCursor.)

- [ ] **Step 1: Write the failing tests** (`tests/test_live_deploy_governor.py`, FakeDB with a `live_trades` collection cloned from `test_paper_auto.py`):
```python
# 13 cases:
# - no caps -> allow True, no DB hit
# - all-pass (caps set, under all)
# - max_concurrent: at/over OPEN count -> block (pause False); below -> allow; ignores CLOSED + other deployments
# - max_lots_per_day: lots_today+capped > limit -> block; exactly == limit -> allow; ignores other days
# - daily_loss_cap: realized breach -> pause True; open-unrealized breach -> pause True; not breached -> allow;
#   loss headline wins over a simultaneous max_concurrent block; open-pnl counted only for today's OPEN trades
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_live_deploy_governor.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** `backend/app/live_deploy_governor.py` per above.

- [ ] **Step 4: Run** — `python -m pytest tests/test_live_deploy_governor.py tests/test_deployment_kill_switch.py -q` → PASS (new + the reused-helper suite unaffected).

- [ ] **Step 5: Commit**
```bash
git add backend/app/live_deploy_governor.py tests/test_live_deploy_governor.py
git commit -m "feat(live): live_deploy_governor.check_live_caps (max_concurrent / max_lots_per_day / daily_loss_cap)"
```

---

## Task 6: `auto_live.py` helpers (enable / claim / capped lots / exit plan / fresh premium)

**Files:**
- Create: `backend/app/auto_live.py`
- Read: `backend/app/paper_auto.py` (clone structure), `backend/app/live/option_premium.py` (`resolve_premium` `fresh` flag), `backend/app/routers/live_broker.py:297` (`_GUARD_DEFAULT_STOP_PCT = 50.0`), `backend/app/exit_controls.py` (`ExitControlsConfig`)
- Test: `tests/test_auto_live.py` (new)

`auto_live.py` is a structural clone of `paper_auto.py`. Member mapping (build these helpers in this task; the orchestrator is Task 7):

| `paper_auto` | `auto_live` | Change |
|---|---|---|
| `auto_paper_enabled` | `auto_live_enabled(deployment, now_utc, *, connected)` | delegates to `is_deployment_live_allowed`; **does NOT** check `LIVE_AUTOPLACE_ARMED` (that's the executor's transmit-boundary concern). |
| `claim_signal_for_paper_trade` | `claim_signal_for_live_trade(db, sid, "auto_live")` | **identical filter** incl. the **same `paper_trade_claim` field** → paper↔live mutual exclusion (one trade per signal). Also `release_live_trade_claim` mirroring `release_paper_trade_claim`. |
| `resolve_option_entry_price` (tick→fresh-candle) | `resolve_live_entry_ref_ltp(db, key, *, latest_tick_lookup, now_ts)` | built on `resolve_premium`; **requires `fresh=True`** — a `last_candle` (`fresh=False`) is **refused** (a stale ref_ltp mis-bands the live LMT). Never spot. |
| `resolve_deployment_lots` (sizing replay) | `resolve_capped_lots(deployment, account_max)` | `max(1, min(int(risk.live.lots), account_max))` — NOT sizing replay (decision #2). |
| `compute_auto_risk_levels` + `compute_spot_exit_levels` | `resolve_live_exit_plan(signal_doc, deployment)` | reuse both verbatim (import from `paper_auto`); returns `{"stop_pct","target_pct","trail","spot_exit","time_stop_minutes"}`; trail from `risk.exit_controls`; if no stop on any axis → `stop_pct = _GUARD_DEFAULT_STOP_PCT` (50.0) catastrophe floor. |

Key code:
```python
from app.live.mode import is_deployment_live_allowed
from app.paper_auto import (compute_auto_risk_levels, compute_spot_exit_levels,
                            claim_signal_for_paper_trade as _claim_pattern)  # reference only; reimplement claim
from app.live.option_premium import resolve_premium

_GUARD_DEFAULT_STOP_PCT = 50.0  # mirror live_broker.py:297 (catastrophe floor)

def auto_live_enabled(deployment, now_utc, *, connected) -> bool:
    return is_deployment_live_allowed(deployment, now_utc, connected=connected)[0]

def resolve_capped_lots(deployment, account_max: int) -> int:
    lots = ((deployment.get("risk") or {}).get("live") or {}).get("lots")
    try: lots = int(lots)
    except (TypeError, ValueError): lots = 1
    return max(1, min(lots, int(account_max)))

def resolve_live_exit_plan(signal_doc, deployment) -> dict:
    risk = deployment.get("risk") or {}
    hints = signal_doc.get("risk_hints") or {}
    # premium SL/TP from strategy hints → deployment auto_paper_* fallback (same math as paper)
    # NOTE compute_auto_risk_levels needs an entry price; the guard derives premium levels from
    # ref_ltp at arm time, so here pass the PCT/PTS through as levels for build_intent + guard:
    stop_pct = hints.get("stop_pct") or risk.get("auto_paper_stop_pct")
    target_pct = hints.get("target_pct") or risk.get("auto_paper_target_pct")
    trail = (risk.get("exit_controls") or {}) or None
    spot_exit = compute_spot_exit_levels(signal_doc)
    plan = {
        "stop_pct": stop_pct,
        "target_pct": target_pct,
        "trail": trail,
        "spot_exit": spot_exit,
        "time_stop_minutes": hints.get("time_stop_minutes"),
    }
    if plan["stop_pct"] is None and not (hints.get("spot_stop_pts")):
        plan["stop_pct"] = _GUARD_DEFAULT_STOP_PCT  # never unprotected
    return plan
```
`claim_signal_for_live_trade` / `release_live_trade_claim`: copy `claim_signal_for_paper_trade` / `release_paper_trade_claim` verbatim but pass `source="auto_live"` — **keep the same `paper_trade_claim` field name** (intentional; it is the paper↔live mutual-exclusion mechanism).
`resolve_live_entry_ref_ltp`: call `resolve_premium(instrument_key=key, tick=latest_tick_lookup(key) if latest_tick_lookup else None, candle_close=None, now_ts=now_ts)` and return its `premium` **only if `fresh is True`**, else `None`.

- [ ] **Step 1: Write the failing tests** (`tests/test_auto_live.py`, clone the FakeDB/FakeCursor/`make_confirmed_signal` harness from `tests/test_paper_auto.py`; add a `live_trades` collection):
```python
# helpers-level cases:
# - auto_live_enabled truth table (armed/expired/disconnected/malformed) — mirrors Task 1
# - claim_signal_for_live_trade single-winner; a later claim_signal_for_paper_trade on the SAME signal loses (mutual exclusion) and vice-versa
# - resolve_capped_lots: lots 50 + ceiling 20 -> 20; lots 2 -> 2; missing/zero -> 1
# - resolve_live_entry_ref_ltp: fresh tick -> premium; last_candle/no-tick (fresh False) -> None
# - resolve_live_exit_plan: premium hint wins; deployment fallback; trail from exit_controls; spot_exit + time_stop carried; deep-default 50% when nothing configured
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_auto_live.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** the helpers in `backend/app/auto_live.py`.
- [ ] **Step 4: Run** — `python -m pytest tests/test_auto_live.py -q` → PASS.
- [ ] **Step 5: Commit**
```bash
git add backend/app/auto_live.py tests/test_auto_live.py
git commit -m "feat(live): auto_live helpers — enable/claim/capped-lots/fresh-premium/exit-plan (clone of paper_auto)"
```

---

## Task 7: `auto_live_trade_for_signal` orchestrator

**Files:**
- Modify: `backend/app/auto_live.py` (add the orchestrator + `release_live_trade_claim`)
- Test: `tests/test_auto_live.py` (append; inject a fake `place_fn`, fake `allow_fn`, a real `safety.RateThrottle`)

Orchestration (clone the prologue of `auto_paper_trade_for_signal`):
```python
async def auto_live_trade_for_signal(db, deployment, signal_doc, *, latest_tick_lookup=None,
                                     now_utc=None, place_fn=None, account_max=20,
                                     client=None, intent_store=None, engine=None,
                                     search_fn=None, arm=None, throttle=None, connected=True):
    # 1. enable gate; 2. CONFIRMED + not blocked + no existing live_trade_id;
    # 3. governor: check_live_caps(...) — on pause: PAUSE deployment + disarm (disarmed_reason="daily_loss"); on block: return reason;
    # 4. instrument_key present; 5. atomic claim_signal_for_live_trade (loser -> signal_claimed_elsewhere);
    # 6. ref_ltp = resolve_live_entry_ref_ltp(... require fresh) -> on None: journal signal.live_trade_error + release claim, return {created False, error};
    # 7. capped = resolve_capped_lots(deployment, account_max); levels = resolve_live_exit_plan(signal, deployment);
    # 8. allow_fn = lambda: is_deployment_live_allowed(deployment, now_utc, connected=connected)
    #    result = await (place_fn or executor.place_deployed_order)(contract, side="B", ref_ltp=ref_ltp,
    #             band_pct=..., levels=levels, capped_lots=capped, client=client, intent_store=intent_store,
    #             engine=engine, search_fn=search_fn, arm=arm, allow_fn=allow_fn, throttle=throttle,
    #             account_max_lots=account_max, deployment_id=dep_id, uid=..., actid=...)
    # 9. if result.get("dry_run"): journal intended-order audit on signal, RELEASE claim, return {created False, dry_run True};
    #    if not result.get("placed"): journal reason (e.g. rate_throttled / reject), RELEASE claim, return {created False, reason};
    #    else: insert live_trades doc (mirror paper-trade fields + norenordno/cid/deployment_id/source/lots/entry_price/
    #          risk{stop_price,target_price}/spot_exit/time_stop_minutes/verdicts/status="OPEN"/created_at);
    #          transition_signal CONFIRMED->TRIGGERED->ACTIVE with a "live" snapshot; stamp signal.live_trade_id;
    #          return {created True, trade_id, norenordno, entry_price, lots}.
```
Side="B" always (long-only — CE/PE → buy that leg). The dry-run branch releases the claim so a later real arm can place a fresh signal (per-bar cadence).

- [ ] **Step 1: Write the failing tests** (append):
```python
# - success: place_fn -> {placed True, protected True, norenordno "ABC", cid, verdicts}; assert live_trades doc with
#   source="auto_live_on_signal" + norenordno; signal -> ACTIVE + live_trade_id; place_fn called with side="B",
#   capped_lots, levels, account_max/throttle injected
# - fresh-tick refusal: stale tick -> created False, signal.live_trade_error set, claim released, state stays CONFIRMED
# - ref_ltp is option premium not spot
# - dry-run: place_fn -> {placed False, dry_run True} -> no live_trades insert, claim released, intended-order audit on signal
# - throttle: place_fn -> {placed False, reason "rate_throttled"} -> no insert, journaled
# - governor max_concurrent skip (no place_fn call); daily_loss_cap pause -> deployment PAUSED + risk.live.armed False
# - claim loser -> signal_claimed_elsewhere
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_auto_live.py -q` → FAIL (orchestrator missing).
- [ ] **Step 3: Implement** the orchestrator + `release_live_trade_claim`.
- [ ] **Step 4: Run** — `python -m pytest tests/test_auto_live.py -q` → PASS.
- [ ] **Step 5: Commit**
```bash
git add backend/app/auto_live.py tests/test_auto_live.py
git commit -m "feat(live): auto_live_trade_for_signal — fresh-premium guard, governor, executor call, live_trades journal"
```

---

## Task 8: Evaluator tee — armed → live (replace), else paper

**Files:**
- Modify: `backend/app/deployment_evaluator.py` (`evaluate_active_deployments`, the auto-paper tail ~L581–602)
- Test: `tests/test_deployment_evaluator.py` (append)

Replace the unconditional auto-paper hook with an armed-first branch (preserving the re-read CONFIRMED race guard + one-claim-per-signal). The `connected` flag comes from a token check; in the evaluator pass a `connected` resolved once per pass (a Flattrade token presence check) — or inject it for testability.
```python
from app.auto_live import auto_live_enabled, auto_live_trade_for_signal
from app.paper_auto import auto_paper_enabled, auto_paper_trade_for_signal
# ... inside the per-result loop, after the re-read `sig` CONFIRMED/blocked guard:
now = now_utc or datetime.now(timezone.utc)
if auto_live_enabled(deployment, now, connected=live_connected):
    r["auto_live"] = await auto_live_trade_for_signal(
        db, deployment, sig, latest_tick_lookup=latest_tick_lookup, now_utc=now,
        client=live_client, intent_store=live_intent_store, engine=live_engine,
        search_fn=live_search_fn, arm=live_arm_factory(sig), throttle=_LIVE_THROTTLE,
        account_max=account_max_lots, connected=live_connected)
elif auto_paper_enabled(deployment):
    r["auto_paper"] = await auto_paper_trade_for_signal(db, deployment, sig, latest_tick_lookup=latest_tick_lookup)
```
The live collaborators (`live_client`, `live_intent_store`, `live_engine`, `live_search_fn`, `live_arm_factory`, `_LIVE_THROTTLE`, `account_max_lots`, `live_connected`) are resolved from the live wiring (mirror `routers/live_broker.py`'s `_get_client`/`_intent_store`/`_l3_engine`/`_make_arm`/safety-config). For host-testability keep these injectable on `evaluate_active_deployments` with defaults that lazily build the real ones; tests pass fakes. **The arm factory must register with `live_position_guard` carrying `spot_exit`/`time_stop_minutes`/`entry_ts`/underlying key (Task 9/10).**

- [ ] **Step 1: Write the failing tests** (append to `tests/test_deployment_evaluator.py`):
```python
# - armed deployment routes to auto_live and SUPPRESSES paper:
#     results[0]["auto_live"]["created"] is True (with a fake place_fn) AND len(db.paper_trades.rows) == 0
# - non-armed paper deployment still routes to auto_paper (unchanged) and creates a paper trade
# - a blocked/non-CONFIRMED signal routes to neither (existing guard intact)
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_deployment_evaluator.py -q` → FAIL.
- [ ] **Step 3: Implement** the tee with injectable live collaborators.
- [ ] **Step 4: Run** — `python -m pytest tests/test_deployment_evaluator.py tests/test_auto_live.py tests/test_paper_auto.py -q` → PASS (paper path unchanged).
- [ ] **Step 5: Commit**
```bash
git add backend/app/deployment_evaluator.py tests/test_deployment_evaluator.py
git commit -m "feat(live): evaluate_active_deployments tee — armed deployment routes live (replace paper)"
```

---

## Task 9: Guard — extend `register(...)` for spot-mirror / time-stop / source

**Files:**
- Modify: `backend/app/live/live_position_guard.py` (`LiveMonitorRegistry.register`)
- Test: `tests/test_live_position_guard.py` (append)

Add optional kwargs (defaults keep manual-path behavior identical): `spot_exit=None, time_stop_minutes=None, entry_ts=None, source="manual", deployment_id=None`. Store them on the registry item; the manual `_make_arm` continues to call `register` without them (so its items have `source="manual"`, `spot_exit=None`).

- [ ] **Step 1: Failing test**
```python
def test_register_carries_spot_exit_and_source():
    reg = LiveMonitorRegistry()
    item = reg.register(key="N1", tsym="X", exch="NFO", qty=75, prd="I", entry_price=100.0,
                        state={"stop_level": 50.0}, spot_exit={"direction":"CE","spot_target":25100,"spot_stop":24900,"instrument_key":"NSE|IDX"},
                        time_stop_minutes=30, entry_ts="2026-06-25T05:00:00+00:00", source="auto_live", deployment_id="dep1")
    assert item["spot_exit"]["direction"] == "CE"
    assert item["time_stop_minutes"] == 30 and item["source"] == "auto_live" and item["deployment_id"] == "dep1"

def test_register_defaults_preserve_manual():
    reg = LiveMonitorRegistry()
    item = reg.register(key="M1", tsym="X", exch="NFO", qty=75, prd="I", entry_price=100.0, state={})
    assert item["source"] == "manual" and item["spot_exit"] is None and item["time_stop_minutes"] is None
```
- [ ] **Step 2: Run** → FAIL (unexpected kwargs).
- [ ] **Step 3: Implement** — add the params + store on the item dict.
- [ ] **Step 4: Run** `python -m pytest tests/test_live_position_guard.py -q` → PASS (existing guard tests unaffected).
- [ ] **Step 5: Commit**
```bash
git add backend/app/live/live_position_guard.py tests/test_live_position_guard.py
git commit -m "feat(live): guard registry carries spot_exit/time_stop/source for deployed positions"
```

---

## Task 10: Guard — spot-mirror + time-stop in the cycle

**Files:**
- Modify: `backend/app/live/live_position_guard.py` (`LivePositionGuard.__init__` + `_cycle`)
- Read: `backend/app/paper_auto.py` (`spot_exit_reason`, the time-stop logic in `mark_open_deployment_trades`), `backend/app/execution_policy.py` (`spot_mirror_exit_reason`)
- Test: `tests/test_live_position_guard.py` (append)

`__init__` gains `spot_tick_fn: Callable[[], dict] | None = None` (returns the live tick map) and `eod_square_ist=dtime(15,0)`. In `_cycle`, for each guarded entry that is **still OPEN after** the premium `evaluate_exit`:
- **spot-mirror** (only if `entry["spot_exit"]` and a FRESH spot tick exists): `reason = spot_mirror_exit_reason(direction, spot_price, spot_target=..., spot_stop=...)`; on reason → remove-before-square via `square_fn(..., reason=f"software_{reason}")`.
- **time-stop** (only if `entry["time_stop_minutes"]` and `entry_ts`): if `now - entry_ts >= minutes` → remove-before-square (`reason="software_time_stop"`).
Use the same remove-before-square ordering already in the cycle. Spot price freshness: reuse the staleness rule from `paper_auto.mark_open_deployment_trades` (`MARK_TICK_MAX_AGE_SECONDS`). The spot tick map key is the underlying index key (`entry["spot_exit"]["instrument_key"]`).

- [ ] **Step 1: Failing tests**
```python
# inject a fake position_book client + a fake spot_tick_fn returning {idx_key: {"last_price":..,"ts":..}}
# - spot-mirror CE target hit -> squared, removed from registry, reason software_spot_target
# - spot-mirror stop hit (stop-first) -> squared
# - time-stop elapsed -> squared reason software_time_stop
# - stale spot tick -> NO square (held)
# - a manual entry (source="manual", spot_exit None) is untouched by spot/time logic
# - remove-before-square: a slow square_fn is never issued twice (entry gone from registry before await)
```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the cycle additions (after the existing premium `evaluate_exit` block, before the overall-basket block).
- [ ] **Step 4: Run** `python -m pytest tests/test_live_position_guard.py tests/test_live_sl_monitor.py -q` → PASS.
- [ ] **Step 5: Commit**
```bash
git add backend/app/live/live_position_guard.py tests/test_live_position_guard.py
git commit -m "feat(live): guard enforces spot-mirror + time-stop exits (parity with paper marker)"
```

---

## Task 11: Guard — 15:00 IST EOD square for deployed positions

**Files:**
- Modify: `backend/app/live/live_position_guard.py` (`_cycle` tail)
- Test: `tests/test_live_position_guard.py` (append)

After the per-position + overall-basket evaluation, if IST time ≥ `eod_square_ist`: square **every** registry entry with `source != "manual"` (deployed only; manual LIVE_TEST keeps its own 10-min timer), remove-before-square, `reason="eod_square"`. Inject a clock (`now_utc` param or a `_now_fn`) so the test can force 15:00 IST. Each EOD square also disarms the parent deployment for the day is handled by the evaluator's daily auto-disarm (Task 8 / arm lifetime) — the guard only flattens.

- [ ] **Step 1: Failing tests**
```python
# - at 15:00 IST: a source="auto_live" position is squared (reason eod_square); a source="manual" position is NOT
# - before 15:00 IST: no EOD square
```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the EOD sweep with an injectable clock.
- [ ] **Step 4: Run** `python -m pytest tests/test_live_position_guard.py -q` → PASS.
- [ ] **Step 5: Commit**
```bash
git add backend/app/live/live_position_guard.py tests/test_live_position_guard.py
git commit -m "feat(live): guard 15:00 IST EOD square for deployed (MIS) positions; manual untouched"
```

---

## Task 12: Wire the guard's `spot_tick_fn` + EOD in `server.py` (Docker-verified)

**Files:**
- Modify: `backend/server.py` (the `LivePositionGuard(...)` construction in lifespan)
- Test: `tests/contract_corpus.py` string-assert (host tests cannot import `server.py`)

Pass `spot_tick_fn=lambda: upstox_stream_manager.latest_tick_map()` and `eod_square_ist=dtime(15,0)` into the existing `LivePositionGuard(...)` construction. Add a contract-corpus assertion that `backend_api_text()` contains `spot_tick_fn=` near the guard construction (pin the wiring).

- [ ] **Step 1: Failing test** — add to a contract test (e.g. `tests/test_live_position_guard.py` or the contract suite): `assert "spot_tick_fn=" in backend_api_text()`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the wiring in `server.py`.
- [ ] **Step 4: Run** the contract test → PASS. (End-to-end verified in Docker at Task 16.)
- [ ] **Step 5: Commit**
```bash
git add backend/server.py tests/  # the contract test file touched
git commit -m "feat(live): wire guard spot_tick_fn + 15:00 EOD into server lifespan"
```

---

## Task 13: Endpoints — arm / disarm / stop / status + safety-config ceiling

**Files:**
- Modify: `backend/app/routers/deployments.py` (4 routes + extend `/stop-all`)
- Modify: `backend/app/routers/live_broker.py` (`_SafetyConfigBody` + PUT gains `max_lots_per_order`)
- Read: existing `deployments.py` pause/resume/stop/stop-all/square-off patterns; `live_broker.py` `put_safety_config`; `tests/contract_corpus.py`
- Test: `tests/test_strategy_deployments.py` (or a new `tests/test_live_deploy_routes.py`) + contract-corpus paths

Routes:
- `POST /api/deployments/{id}/live/arm` — body `{lots:int, max_lots_per_day:int, max_concurrent:int, daily_loss_cap:float|None, confirm:bool}`. Guards: deployment exists + ACTIVE + not retired/drifted; broker connected (token present); `engine.can_trade()`; `confirm is True` (else 400). Writes `risk.live` armed + `armed_until = armed_until_today_ist(now)` + `armed_at`/`armed_by`. Response includes `autoplace_armed` (the env flag) with a clear note when False ("backend will dry-run-log, not transmit").
- `POST /api/deployments/{id}/live/disarm` — set `risk.live.armed=False, disarmed_reason="manual"`. Does NOT flatten.
- `POST /api/deployments/{id}/live/stop` — scoped flatten of this deployment's open live positions (filter the broker position book / registry to this deployment's tsyms, then `panic_squareoff`/`square_position`), then disarm. Mirrors the paper `square-off` + stop pattern but for live (gated by `LIVE_GUARD_ARMED` like other live squares).
- `GET /api/deployments/{id}/live/status` — `{armed, armed_until, caps, today:{orders,lots,realized_pnl}, open_positions, autoplace_armed, guard:{...}}`.
- Extend `POST /api/deployments/stop-all` to also disarm + flatten armed live deployments.
- `live_broker.py`: add `max_lots_per_order: Optional[int] = None` to `_SafetyConfigBody`; it already flows through `put_config` (Task 2 validates it).

- [ ] **Step 1: Failing tests** — route tests via the existing pattern (TestClient or contract-corpus string asserts as used in this repo). Cover: arm requires `confirm=True` (400 otherwise); arm rejected when not connected / not can_trade / not ACTIVE; arm sets `risk.live.armed` + `armed_until`; disarm clears armed without flatten; status shape; stop-all disarms live. Add the 4 new route paths + the `max_lots_per_order` field to `tests/contract_corpus.py` assertions **in this task**.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the routes + the safety-config field.
- [ ] **Step 4: Run** `python -m pytest tests/test_strategy_deployments.py tests/test_live_order_page_routes.py tests/ -k "live_deploy or safety_config or contract" -q` → PASS.
- [ ] **Step 5: Commit**
```bash
git add backend/app/routers/deployments.py backend/app/routers/live_broker.py tests/
git commit -m "feat(live): deployment live arm/disarm/stop/status routes + max_lots_per_order safety-config"
```

---

## Task 14: Frontend — caps form + danger arm dialog + Live Deployments strip + banner

**Files:**
- Modify: `frontend/src/lib/api.js` (add `liveArm`, `liveDisarm`, `liveStop`, `liveStatus`, and `getSafetyConfig`/`putSafetyConfig` if absent)
- Modify/Create: `frontend/src/pages/LiveTrading.jsx` + `frontend/src/pages/PaperTrading.jsx` (Deploy-to-Live action on the deployment strip) + `frontend/src/components/live/DeployToLivePanel.jsx` (new) + `LiveBanner.jsx`
- Read: existing paper deployment-controls strip (Pause/Resume/Stop), the danger typed-confirm dialog pattern, `TokenCountdown` usage, theme tokens, kebab-case testid convention (Kiro bible per HANDOFF §11)
- Test: `CI=true npm run build` + any contract-corpus testid pins added in Task 13

- [ ] **Step 1:** Add the API methods in `frontend/src/lib/api.js` (match the existing axios wrapper style):
```js
export const liveArm = (id, body) => http.post(`/deployments/${id}/live/arm`, body).then(r => r.data);
export const liveDisarm = (id) => http.post(`/deployments/${id}/live/disarm`).then(r => r.data);
export const liveStop = (id) => http.post(`/deployments/${id}/live/stop`).then(r => r.data);
export const liveStatus = (id) => http.get(`/deployments/${id}/live/status`).then(r => r.data);
```
- [ ] **Step 2:** Build `DeployToLivePanel.jsx` — caps form (lots input clamped to the account ceiling fetched from `getSafetyConfig()`; max_lots_per_day; max_concurrent; daily_loss_cap) → a danger typed-confirm dialog ("type ARM to authorize live orders for <strategy>") calling `liveArm`. Prominent warning banner when `autoplace_armed` is False ("backend dry-run only"). kebab-case testids (`deploy-to-live-arm`, `live-caps-lots`, …).
- [ ] **Step 3:** Add the **Live Deployments strip** (extend the paper controls strip): per armed deployment — `armed_until` countdown via `TokenCountdown`, today orders/lots/₹, open positions, Disarm/Stop buttons, master "Stop-all live". `LiveBanner` shows "N armed live" + env state. Poll `liveStatus` on the existing job/poll cadence.
- [ ] **Step 4:** Run `cd frontend && CI=true npm run build` → Expected: compiles clean (no new errors). Smoke-render later in Docker.
- [ ] **Step 5: Commit**
```bash
git add frontend/src/lib/api.js frontend/src/pages/LiveTrading.jsx frontend/src/pages/PaperTrading.jsx frontend/src/components/live/
git commit -m "feat(live): Deploy-to-Live caps form + danger arm dialog + Live Deployments strip + banner"
```

---

## Task 15: Full host-suite + frontend gate

- [ ] **Step 1:** `python -m pytest tests -q` → all pass (existing ~2200+ plus the new live-deploy tests). Investigate any regression before proceeding.
- [ ] **Step 2:** `cd frontend && CI=true npm run build` → compiles clean.
- [ ] **Step 3:** `git add -A && git commit -m "test(live): full host-suite + FE build green for strategy-deploy-to-live"` (only if anything changed; else skip).

---

## Task 16: Docker dry-run verification + supervised live readback

**This task involves the user — the assistant never transmits.**

- [ ] **Step 1:** `docker compose up -d --build backend frontend` from the repo root; `curl -s localhost:8001/api/health` → `{"db":"ok"}`.
- [ ] **Step 2 (dry-run, env unset):** With `LIVE_AUTOPLACE_ARMED` **unset**, arm a test deployment via the UI (Flattrade connected, market hours or a forced signal). Confirm via logs + `live/status` that signals are **dry-run-logged** (`placed:false, dry_run:true`), **no broker order** is sent, and **no `live_trades` doc** is inserted. Confirm `guard-status` shows the offline-first state.
- [ ] **Step 3 (supervised live, user-gated):** The **user** sets `LIVE_AUTOPLACE_ARMED=1` + `LIVE_GUARD_ARMED=1`, arms ONE deployment with `lots=1`, and lets one signal fire. Verify: a real 1-lot BUY fill; the guard registers it; a stop/target/spot-mirror or 15:00 EOD square exits it cleanly; `live/status` + the broker position book reconcile to flat. This is the real-money-critical readback — **the user performs the arm and watches; the assistant only observes/reports.**
- [ ] **Step 4:** Capture the readback result in `CHANGELOG.md` (0.47.x) + HANDOFF §2; update agent memory (`live-execution-build-2026`, new `strategy-deploy-to-live` note).
- [ ] **Step 5: Commit** docs (no push without explicit instruction).

---

## Dependencies & ordering

Strict order: 1→2 (config) → 3→4 (executor) → 5 (governor) → 6→7 (auto_live) → 8 (tee) → 9→10→11→12 (guard) → 13 (routes) → 14 (FE) → 15 (full gate) → 16 (Docker + readback). Tasks 1, 2 are independent and may be done in parallel. The guard chain (9–12) is independent of the auto_live chain (6–8) until the tee's arm factory (Task 8) references the extended `register` (Task 9) — do Task 9 before Task 8's arm-factory wiring, or stub the extra kwargs.

## Self-review note (author)
Spec coverage verified: §4 (Tasks 1,13), §5 (Tasks 3,4), §6 (Task 5), §7 (Tasks 6,7,8), §8 (Tasks 9–12), §9 (Task 13), §10 (Task 14), §11 invariants (distributed), §12 residual risk (Task 16 doc), §13 testing (every task + Task 15), §14 build order (this ordering). Caps standardized under `risk.live` (not `risk.live_caps`). `live_deploy_governor.py` placed at `backend/app/` (with the other deployment modules), not `backend/app/live/`.
