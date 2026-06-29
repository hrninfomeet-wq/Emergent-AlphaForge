# Live-Feed Health — Backend Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure feed-health model + an auto-reconcile supervisor that keeps the Upstox stream + candle roller running during market hours, plus a `GET /live-feed/health` endpoint — so paper/live trading can never again silently sit "active" with no live candles.

**Architecture:** A new pure, host-testable module `app/live_feed_health.py` holds the state machine (`compute_feed_health`), the reconciler decision (`decide_feed_actions`), and an injectable executor (`execute_feed_actions` / `supervise_once`). `runtime.py` adds a 20 s supervisor loop that feeds real inputs into `supervise_once` and exposes its state; `server.py` starts the loop at boot; `broker.py` adds the health endpoint + manual-stop suppression. The master liveness signal is `candles_1m` freshness (< 120 s).

**Tech Stack:** Python 3.12, FastAPI, asyncio, pytest. Backend `C:\Users\haroo\af-wt-livefeed\backend`; tests `C:\Users\haroo\af-wt-livefeed\tests` (bootstrap: `sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))`). Branch `feat/live-feed-health`. Spec: `docs/superpowers/specs/2026-06-29-live-feed-health-truthful-liveness-design.md`.

> cwd for all commands = `C:\Users\haroo\af-wt-livefeed`. The host venv has pandas/pytest but NOT motor — `app/live_feed_health.py` must import only stdlib/typing so it stays host-importable. Do NOT import `app.runtime`, `app.db`, or `app.routers.*` in any test (they pull motor). The supervisor LOOP wiring (`runtime.py`) + the endpoint (`broker.py`) are verified by the pure tests below + a Docker market-hours check (Plan 1 Task 5 note), not by host unit tests.

## File structure
- **Create** `backend/app/live_feed_health.py` — constants, `market_open_ist`, `compute_feed_health`, `decide_feed_actions`, `execute_feed_actions`, `supervise_once`. Pure / injectable; host-safe.
- **Modify** `backend/app/runtime.py` — `_feed_supervisor` state dict + `feed_supervisor_state()` + `_live_feed_supervisor_loop()` (calls `supervise_once`).
- **Modify** `backend/server.py` — start `_live_feed_supervisor_loop()` at boot.
- **Modify** `backend/app/routers/broker.py` — `GET /live-feed/health` + set/clear `_feed_supervisor["suppressed"]` in the manual stop/start endpoints.
- **Create** `tests/test_live_feed_health.py` — exhaustive pure tests.

---

## Task 1: the feed-health state machine (pure)

**Files:**
- Create: `backend/app/live_feed_health.py`
- Test: `tests/test_live_feed_health.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_live_feed_health.py`:

```python
"""Pure tests for the live-feed health model + reconciler decision (no motor/Upstox)."""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_feed_health import (  # noqa: E402
    compute_feed_health, market_open_ist,
    LIVE, WARMING_UP, DEGRADED, NEEDS_LOGIN, MARKET_CLOSED,
    FRESH_THRESHOLD_SEC, WARMUP_GRACE_SEC,
)

IST = timezone(timedelta(hours=5, minutes=30))
TOKEN_OK = {"connected": True, "expired": False, "configured": True, "expires_at": "2026-06-29T22:00:00+00:00"}
TOKEN_EXPIRED = {"connected": True, "expired": True, "configured": True}
TOKEN_NONE = {"connected": False, "configured": True}
TOKEN_UNCONFIG = {"connected": False, "configured": False}


def _ist(h, m):
    return datetime(2026, 6, 29, h, m, tzinfo=IST)  # 2026-06-29 is a Monday


def _ms(ist_dt):
    return int(ist_dt.astimezone(timezone.utc).timestamp() * 1000)


def _health(**kw):
    base = dict(now_ist=_ist(11, 0), now_ms=_ms(_ist(11, 0)), is_trading_day=True,
                token=TOKEN_OK, stream_running=True, roller_running=True,
                roller_started_ms=_ms(_ist(9, 30)), last_candle_ts=_ms(_ist(10, 59, )),
                supervisor_backoff_active=False, supervisor_last_error=None)
    base.update(kw)
    return compute_feed_health(**base)


def test_market_open_ist_boundaries():
    assert market_open_ist(_ist(9, 15), True) is True
    assert market_open_ist(_ist(9, 14), True) is False
    assert market_open_ist(_ist(15, 29), True) is True
    assert market_open_ist(_ist(15, 30), True) is False
    assert market_open_ist(_ist(11, 0), False) is False   # holiday/weekend


def test_market_closed_outside_hours():
    h = _health(now_ist=_ist(16, 0), now_ms=_ms(_ist(16, 0)))
    assert h["state"] == MARKET_CLOSED and h["candles_fresh"] is False


def test_needs_login_when_token_missing_or_expired():
    assert _health(token=TOKEN_NONE)["state"] == NEEDS_LOGIN
    assert _health(token=TOKEN_NONE)["cta"] == "connect_upstox"
    assert _health(token=TOKEN_EXPIRED)["state"] == NEEDS_LOGIN
    assert _health(token=TOKEN_UNCONFIG)["state"] == NEEDS_LOGIN
    assert _health(token=TOKEN_UNCONFIG)["cta"] is None   # nothing to click if unconfigured


def test_live_when_candles_fresh():
    now = _ist(11, 0)
    h = _health(now_ist=now, now_ms=_ms(now), last_candle_ts=_ms(now) - 30_000)  # 30s old
    assert h["state"] == LIVE and h["candles_fresh"] is True
    assert h["last_candle_age_sec"] == 30


def test_warming_up_just_started_no_fresh_bar():
    now = _ist(9, 20)
    h = _health(now_ist=now, now_ms=_ms(now), roller_started_ms=_ms(now) - 30_000,
                last_candle_ts=None)  # running 30s, no bar yet
    assert h["state"] == WARMING_UP


def test_warming_up_when_feed_down_but_no_backoff():
    now = _ist(9, 35)
    h = _health(now_ist=now, now_ms=_ms(now), stream_running=False, roller_running=False,
                roller_started_ms=None, last_candle_ts=None, supervisor_backoff_active=False)
    assert h["state"] == WARMING_UP   # supervisor is mid-start, not a failure


def test_degraded_when_feed_down_with_backoff():
    now = _ist(11, 0)
    h = _health(now_ist=now, now_ms=_ms(now), stream_running=False, roller_running=False,
                roller_started_ms=None, last_candle_ts=None,
                supervisor_backoff_active=True, supervisor_last_error="rate limited")
    assert h["state"] == DEGRADED and "rate limited" in h["reason"]


def test_degraded_when_running_but_candles_stale():
    now = _ist(12, 0)
    h = _health(now_ist=now, now_ms=_ms(now), roller_started_ms=_ms(_ist(9, 30)),
                last_candle_ts=_ms(now) - 5 * 60_000)  # 5 min stale, long past warmup
    assert h["state"] == DEGRADED and "min" in h["reason"]


def test_health_never_raises_on_bad_inputs():
    h = compute_feed_health(now_ist=_ist(11, 0), now_ms=_ms(_ist(11, 0)), is_trading_day=True,
                            token=None, stream_running=None, roller_running=None,
                            roller_started_ms=None, last_candle_ts=None)
    assert h["state"] in (NEEDS_LOGIN, MARKET_CLOSED, DEGRADED, WARMING_UP)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "C:/Users/haroo/af-wt-livefeed" && python -m pytest tests/test_live_feed_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.live_feed_health'`.

- [ ] **Step 3: Implement the state machine**

Create `backend/app/live_feed_health.py`:

```python
"""Live-feed health model + auto-reconcile decision logic.

PURE + host-importable (stdlib/typing only — NO motor, NO app.db, NO Upstox).
The master liveness signal is candles_1m freshness. See
docs/superpowers/specs/2026-06-29-live-feed-health-truthful-liveness-design.md.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

LIVE = "LIVE"
WARMING_UP = "WARMING_UP"
DEGRADED = "DEGRADED"
NEEDS_LOGIN = "NEEDS_LOGIN"
MARKET_CLOSED = "MARKET_CLOSED"

FRESH_THRESHOLD_SEC = 120
WARMUP_GRACE_SEC = 90
SUPERVISE_POLL_SEC = 20

_SESSION_START_MIN = 9 * 60 + 15
_SESSION_END_MIN = 15 * 60 + 30


def market_open_ist(now_ist, is_trading_day: bool) -> bool:
    """True iff `now_ist` (a tz-aware IST datetime) is within NSE 09:15-15:30 on a trading day."""
    if not is_trading_day:
        return False
    minute = now_ist.hour * 60 + now_ist.minute
    return _SESSION_START_MIN <= minute < _SESSION_END_MIN


def _age_sec(now_ms: int, last_candle_ts: Optional[int]) -> Optional[float]:
    if not last_candle_ts:
        return None
    try:
        return max(0.0, (int(now_ms) - int(last_candle_ts)) / 1000.0)
    except (TypeError, ValueError):
        return None


def _token_ok(token: Optional[Dict[str, Any]]) -> bool:
    return bool(token and token.get("connected") and not token.get("expired"))


def compute_feed_health(*, now_ist, now_ms: int, is_trading_day: bool,
                        token: Optional[Dict[str, Any]], stream_running, roller_running,
                        roller_started_ms: Optional[int], last_candle_ts: Optional[int],
                        supervisor_backoff_active: bool = False,
                        supervisor_last_error: Optional[str] = None) -> Dict[str, Any]:
    """Return the feed-health dict. Never raises (defensive)."""
    open_ = market_open_ist(now_ist, is_trading_day)
    age = _age_sec(now_ms, last_candle_ts)
    fresh = age is not None and age < FRESH_THRESHOLD_SEC
    base = {
        "market_open": open_,
        "token": token,
        "stream_running": bool(stream_running),
        "roller_running": bool(roller_running),
        "last_candle_ts": last_candle_ts,
        "last_candle_age_sec": (round(age) if age is not None else None),
        "candles_fresh": bool(open_ and fresh),
    }

    def out(state: str, reason: str, cta: Optional[str] = None) -> Dict[str, Any]:
        return {**base, "state": state, "reason": reason, "cta": cta}

    if not open_:
        return out(MARKET_CLOSED, "Market is closed.")
    if not _token_ok(token):
        if token is not None and not token.get("configured", True):
            return out(NEEDS_LOGIN, "Upstox isn't configured.", None)
        return out(NEEDS_LOGIN, "Upstox isn't connected — connect to go live.", "connect_upstox")
    if fresh:
        return out(LIVE, "Live — receiving fresh candles.")
    running = bool(stream_running) and bool(roller_running)
    within_grace = (
        roller_started_ms is not None
        and (int(now_ms) - int(roller_started_ms)) < WARMUP_GRACE_SEC * 1000
    )
    if (running and within_grace) or (not running and not supervisor_backoff_active):
        return out(WARMING_UP, "Feed starting — first candle shortly.")
    if not running:
        down = "stream" if not stream_running else "roller"
        msg = f"Live feed not running ({down})."
        if supervisor_last_error:
            msg += f" {supervisor_last_error}"
        return out(DEGRADED, msg)
    mins = round(age / 60) if age is not None else "?"
    return out(DEGRADED, f"No live candles for ~{mins} min — feed stalled.")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd "C:/Users/haroo/af-wt-livefeed" && python -m pytest tests/test_live_feed_health.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
cd "C:/Users/haroo/af-wt-livefeed" && git add backend/app/live_feed_health.py tests/test_live_feed_health.py && git commit -m "feat(livefeed): pure feed-health state machine (compute_feed_health)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: the reconciler decision + executor (pure / injectable)

**Files:**
- Modify: `backend/app/live_feed_health.py` (append `decide_feed_actions`, `execute_feed_actions`, `supervise_once`)
- Test: `tests/test_live_feed_health.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_live_feed_health.py`:

```python
import asyncio
import pytest
from app.live_feed_health import decide_feed_actions, execute_feed_actions, supervise_once


class FakeManager:
    """Stands in for upstox_stream_manager / live_candle_roller: start/stop + status."""
    def __init__(self, running=False, fail=False):
        self._running = running
        self._fail = fail
        self.start_calls = 0
        self.stop_calls = 0
    def status(self):
        return {"running": self._running}
    async def start(self, **kwargs):
        self.start_calls += 1
        if self._fail:
            raise RuntimeError("rate limited")
        self._running = True
    async def stop(self):
        self.stop_calls += 1
        self._running = False


def test_decide_actions_market_closed_stops_running_feed():
    assert decide_feed_actions(market_open=False, token_ok=True, stream_running=True,
                               roller_running=True, suppressed=False) == ["stop_feed"]
    assert decide_feed_actions(market_open=False, token_ok=True, stream_running=False,
                               roller_running=False, suppressed=False) == []


def test_decide_actions_blocked_without_token():
    assert decide_feed_actions(market_open=True, token_ok=False, stream_running=False,
                               roller_running=False, suppressed=False) == ["blocked_needs_login"]


def test_decide_actions_starts_whats_down():
    assert decide_feed_actions(market_open=True, token_ok=True, stream_running=False,
                               roller_running=False, suppressed=False) == ["start_stream", "start_roller"]
    assert decide_feed_actions(market_open=True, token_ok=True, stream_running=True,
                               roller_running=False, suppressed=False) == ["start_roller"]
    assert decide_feed_actions(market_open=True, token_ok=True, stream_running=True,
                               roller_running=True, suppressed=False) == []


def test_decide_actions_respects_manual_suppression():
    assert decide_feed_actions(market_open=True, token_ok=True, stream_running=False,
                               roller_running=False, suppressed=True) == []


def test_execute_starts_managers_and_clears_error():
    stream, roller = FakeManager(), FakeManager()
    state = {"suppressed": False, "backoff_active": True, "last_error": "old"}
    asyncio.run(execute_feed_actions(["start_stream", "start_roller"], stream_manager=stream,
                                     roller=roller, instrument_keys=["k"], mode="full", state=state))
    assert stream.start_calls == 1 and roller.start_calls == 1
    assert stream._running and roller._running
    assert state["backoff_active"] is False and state["last_error"] is None


def test_execute_records_backoff_on_failure():
    stream = FakeManager(fail=True)
    roller = FakeManager()
    state = {"suppressed": False, "backoff_active": False, "last_error": None}
    asyncio.run(execute_feed_actions(["start_stream"], stream_manager=stream, roller=roller,
                                     instrument_keys=["k"], mode="full", state=state))
    assert state["backoff_active"] is True and "rate limited" in state["last_error"]


def test_supervise_once_brings_feed_up_when_market_open_and_token_ok():
    stream, roller = FakeManager(), FakeManager()
    state = {"suppressed": False, "backoff_active": False, "last_error": None}
    actions = asyncio.run(supervise_once(market_open=True, token_ok=True, stream_manager=stream,
                                         roller=roller, instrument_keys=["k"], mode="full", state=state))
    assert set(actions) == {"start_stream", "start_roller"}
    assert stream._running and roller._running


def test_supervise_once_resets_suppression_at_session_end():
    stream, roller = FakeManager(running=True), FakeManager(running=True)
    state = {"suppressed": True, "backoff_active": False, "last_error": None}
    asyncio.run(supervise_once(market_open=False, token_ok=True, stream_manager=stream,
                               roller=roller, instrument_keys=["k"], mode="full", state=state))
    assert state["suppressed"] is False          # fresh next session
    assert stream._running is False              # feed stopped at close
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "C:/Users/haroo/af-wt-livefeed" && python -m pytest tests/test_live_feed_health.py -k "decide or execute or supervise" -v`
Expected: FAIL — `ImportError: cannot import name 'decide_feed_actions'`.

- [ ] **Step 3: Implement**

Append to `backend/app/live_feed_health.py`:

```python
def decide_feed_actions(*, market_open: bool, token_ok: bool, stream_running: bool,
                        roller_running: bool, suppressed: bool) -> List[str]:
    """Pure reconciler decision: what to do this tick. Idempotent — `start_*` map
    to managers' start() which no-op when already running."""
    if not market_open:
        return ["stop_feed"] if (stream_running or roller_running) else []
    if not token_ok:
        return ["blocked_needs_login"]   # human OAuth required; supervisor can't fix
    if suppressed:
        return []                        # user manually stopped; don't fight it
    actions: List[str] = []
    if not stream_running:
        actions.append("start_stream")
    if not roller_running:
        actions.append("start_roller")
    return actions


async def execute_feed_actions(actions: List[str], *, stream_manager, roller,
                               instrument_keys, mode, state: Dict[str, Any]) -> None:
    """Execute reconciler actions against the live managers. Updates `state`
    (backoff_active / last_error). Each start is wrapped so one failure (e.g. an
    Upstox rate limit) records a reason and is simply retried next tick (~20 s)."""
    for action in actions:
        try:
            if action == "start_stream":
                await stream_manager.start(instrument_keys=instrument_keys, mode=mode, persist=True)
            elif action == "start_roller":
                await roller.start()
            elif action == "stop_feed":
                await roller.stop()
                await stream_manager.stop()
            # "blocked_needs_login" / noop -> nothing to execute
            state["last_error"] = None
            state["backoff_active"] = False
        except Exception as exc:   # noqa: BLE001 - record + retry next tick
            state["last_error"] = str(exc)[:200]
            state["backoff_active"] = True


async def supervise_once(*, market_open: bool, token_ok: bool, stream_manager, roller,
                         instrument_keys, mode, state: Dict[str, Any]) -> List[str]:
    """One reconciler tick: decide actions from the managers' live status, reset
    manual suppression at session end, then execute. Returns the actions taken."""
    actions = decide_feed_actions(
        market_open=market_open,
        token_ok=token_ok,
        stream_running=bool((stream_manager.status() or {}).get("running")),
        roller_running=bool((roller.status() or {}).get("running")),
        suppressed=bool(state.get("suppressed")),
    )
    if not market_open:
        state["suppressed"] = False
    await execute_feed_actions(actions, stream_manager=stream_manager, roller=roller,
                               instrument_keys=instrument_keys, mode=mode, state=state)
    return actions
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd "C:/Users/haroo/af-wt-livefeed" && python -m pytest tests/test_live_feed_health.py -v`
Expected: PASS (18 passed).

- [ ] **Step 5: Commit**

```bash
cd "C:/Users/haroo/af-wt-livefeed" && git add backend/app/live_feed_health.py tests/test_live_feed_health.py && git commit -m "feat(livefeed): reconciler decision + injectable executor (supervise_once)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: wire the supervisor loop into runtime + boot

**Files:**
- Modify: `backend/app/runtime.py` (singletons at ~71–82; add state + loop near `_deployment_evaluator_loop` ~322)
- Modify: `backend/server.py` (startup, near `_deployment_evaluator_loop` task ~148)
- Test: covered by Task 1–2 pure tests (`supervise_once`); the loop wrapper is verified by the boot-log + Docker market-hours check (Task 5). Do NOT add a host test importing `app.runtime` (motor).

- [ ] **Step 1: Add the supervisor state + loop to `runtime.py`**

Near the existing live singletons (after `live_candle_roller = LiveCandleRoller(...)`, ~line 82), add:

```python
from app.live_feed_health import supervise_once as _supervise_once, SUPERVISE_POLL_SEC as _SUPERVISE_POLL_SEC

# Auto-reconcile supervisor state (exposed to /live-feed/health). `suppressed` is
# set True by a manual stop endpoint so the loop won't fight a deliberate Stop.
_feed_supervisor: Dict[str, Any] = {
    "suppressed": False, "backoff_active": False, "last_error": None,
    "last_actions": [], "last_tick_at": None,
}


def feed_supervisor_state() -> Dict[str, Any]:
    return dict(_feed_supervisor)
```

Then add the loop (mirror `_deployment_evaluator_loop`'s structure: market-hours/IST gate, 20 s cadence). Place it next to `_deployment_evaluator_loop`:

```python
async def _live_feed_supervisor_loop() -> None:
    """Keep the Upstox stream + candle roller running during market hours whenever
    the token is valid. Fixes the 'app started before the daily OAuth' gap and
    self-heals mid-session drops. Never touches credentials — when the token is
    missing/expired it does nothing (health surfaces NEEDS_LOGIN)."""
    from datetime import time as _time
    from app.nse_calendar import is_trading_day
    log.info("Live-feed supervisor loop initialized")
    while True:
        try:
            await asyncio.sleep(_SUPERVISE_POLL_SEC)
            ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            today_iso = ist_now.strftime("%Y-%m-%d")
            t = ist_now.time()
            market_open = (
                ist_now.weekday() < 5 and is_trading_day(today_iso)
                and _time(9, 15) <= t < _time(15, 30)
            )
            token = await upstox_client.get_connection_status()
            token_ok = bool(token.get("connected") and not token.get("expired"))
            keys = _default_stream_instrument_keys()
            actions = await _supervise_once(
                market_open=market_open, token_ok=token_ok,
                stream_manager=upstox_stream_manager, roller=live_candle_roller,
                instrument_keys=keys, mode=DEFAULT_STREAM_MODE, state=_feed_supervisor,
            )
            _feed_supervisor["last_actions"] = actions
            _feed_supervisor["last_tick_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:   # noqa: BLE001 - never kill the loop
            log.exception("live-feed supervisor tick failed: %s", exc)
```

(Confirm `upstox_client`, `_default_stream_instrument_keys`, `DEFAULT_STREAM_MODE`, `datetime`, `timezone`, `timedelta` are already imported/defined in `runtime.py` — they are used by the existing startup/stream code; reuse them. `DEFAULT_STREAM_MODE` is imported at line 34.)

- [ ] **Step 2: Start the loop at boot in `server.py`**

In `backend/server.py`, add `_live_feed_supervisor_loop` to the import from `app.runtime` (the block at ~34–45 that already imports `_deployment_evaluator_loop`, `live_candle_roller`, …), then start it right after the deployment-evaluator task (~line 148–149):

```python
    asyncio.create_task(_deployment_evaluator_loop(), name="deployment-evaluator")
    log.info("Deployment evaluator scheduler started")

    asyncio.create_task(_live_feed_supervisor_loop(), name="live-feed-supervisor")
    log.info("Live-feed supervisor started")
```

The existing one-shot startup bring-up block (`server.py:112–130`) stays as-is — it's a fast first attempt and is idempotent with the supervisor (`start()` no-ops if already running). The supervisor handles every case the one-shot block misses (late OAuth, drops).

- [ ] **Step 3: Verify the module imports cleanly (syntax/import smoke)**

Run: `cd "C:/Users/haroo/af-wt-livefeed" && python -m pytest tests/test_live_feed_health.py -q && python -c "import ast; ast.parse(open('backend/app/runtime.py').read()); ast.parse(open('backend/server.py').read()); print('parse-ok')"`
Expected: `18 passed` then `parse-ok` (host can't import motor-bound `runtime.py`, so AST-parse confirms no syntax error; runtime import is exercised at Docker boot in Task 5).

- [ ] **Step 4: Commit**

```bash
cd "C:/Users/haroo/af-wt-livefeed" && git add backend/app/runtime.py backend/server.py && git commit -m "feat(livefeed): auto-reconcile supervisor loop wired at boot

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `/live-feed/health` endpoint + manual-stop suppression

**Files:**
- Modify: `backend/app/routers/broker.py` (add endpoint near `/live-candles/status` ~111; set/clear suppression in the stop/start endpoints ~122–133, 194–225)
- Test: verified by Task 1–2 pure tests (the endpoint just gathers inputs → `compute_feed_health`) + Docker check (Task 5).

- [ ] **Step 1: Add the health endpoint**

In `backend/app/routers/broker.py`, near the other status endpoints (after `/live-candles/status`, ~line 114), add:

```python
@api.get("/live-feed/health")
async def live_feed_health_endpoint():
    """Truthful live-feed health: is the pipeline (token -> stream -> roller ->
    fresh candles_1m) actually delivering, or what's blocking it?"""
    from datetime import datetime, timezone, timedelta
    from app.live_feed_health import compute_feed_health
    from app.nse_calendar import is_trading_day
    from app.runtime import (
        upstox_stream_manager, live_candle_roller, feed_supervisor_state,
    )
    db = get_db()
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    token = await upstox_client.get_connection_status()
    roller_status = live_candle_roller.status()
    roller_started_ms = None
    started_at = roller_status.get("started_at")
    if started_at:
        try:
            roller_started_ms = int(datetime.fromisoformat(
                str(started_at).replace("Z", "+00:00")).timestamp() * 1000)
        except (ValueError, TypeError):
            roller_started_ms = None
    latest = await db.candles_1m.find_one({"instrument": "NIFTY"}, {"_id": 0, "ts": 1},
                                          sort=[("ts", -1)])
    last_candle_ts = int(latest["ts"]) if latest and latest.get("ts") else None
    sup = feed_supervisor_state()
    health = compute_feed_health(
        now_ist=ist_now, now_ms=now_ms,
        is_trading_day=is_trading_day(ist_now.strftime("%Y-%m-%d")),
        token=token,
        stream_running=bool((upstox_stream_manager.status() or {}).get("running")),
        roller_running=bool(roller_status.get("running")),
        roller_started_ms=roller_started_ms, last_candle_ts=last_candle_ts,
        supervisor_backoff_active=bool(sup.get("backoff_active")),
        supervisor_last_error=sup.get("last_error"),
    )
    return serialize_doc(health)
```

- [ ] **Step 2: Set suppression on manual stop, clear on manual start**

In the same file, update the manual roller/stream endpoints so a deliberate Stop isn't overridden by the supervisor. In `live_candle_roller_stop` (~129) and `upstox_stream_stop` (~215), set the flag; in `live_candle_roller_start` (~122) and `upstox_stream_start` (~194), clear it. Add at the start of each handler body:

```python
    from app.runtime import _feed_supervisor
    _feed_supervisor["suppressed"] = True    # in the two STOP handlers
```
```python
    from app.runtime import _feed_supervisor
    _feed_supervisor["suppressed"] = False   # in the two START handlers
```

(Confirm `get_db` and `serialize_doc` are already imported in `broker.py` — they are used by neighbouring endpoints.)

- [ ] **Step 3: Verify syntax**

Run: `cd "C:/Users/haroo/af-wt-livefeed" && python -c "import ast; ast.parse(open('backend/app/routers/broker.py').read()); print('parse-ok')"`
Expected: `parse-ok`.

- [ ] **Step 4: Commit**

```bash
cd "C:/Users/haroo/af-wt-livefeed" && git add backend/app/routers/broker.py && git commit -m "feat(livefeed): GET /live-feed/health + manual-stop suppression

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: regression + runtime verification

**Files:** none (verification only)

- [ ] **Step 1: Full host suite (pure tests + no regressions)**

Run: `cd "C:/Users/haroo/af-wt-livefeed" && python -m pytest tests/ -q --continue-on-collection-errors`
Expected: the new `test_live_feed_health.py` (18) pass; the pre-existing baseline is unchanged (motor-dependent files error at collection on the host — that's the documented baseline, not a regression). Investigate any NEW failure.

- [ ] **Step 2: Docker boot + endpoint smoke (runtime wiring)**

This is the runtime check for the loop + endpoint (host can't import motor). From the worktree, build + run a side stack (do NOT touch the main `:8001` app), then hit the endpoint:

Run: `cd "C:/Users/haroo/af-wt-livefeed" && docker compose -p alphaforge_lf up -d --build backend 2>&1 | tail -5`
Then: `cd "C:/Users/haroo/af-wt-livefeed" && docker logs alphaforge_lf_backend 2>&1 | grep -iE "Live-feed supervisor|Deployment evaluator scheduler" | head -5`
Expected: `Live-feed supervisor started` + `Live-feed supervisor loop initialized` in the boot log.
Then (after the backend is healthy): `curl -s http://localhost:<lf_backend_port>/api/live-feed/health` returns a JSON health object with a `state` field (likely `MARKET_CLOSED` outside hours).
> Note: the `alphaforge_lf` project/ports must be set via an override compose file the same way `alphaforge_wt` is (alt ports, alt mongo) so it never collides with the main stack. If a side stack is impractical, the user verifies on the main stack at the next market open per the spec §11 rollout.

- [ ] **Step 3: Confirm additive diff**

Run: `cd "C:/Users/haroo/af-wt-livefeed" && git diff --stat main..HEAD`
Expected: only `backend/app/live_feed_health.py` (new), `backend/app/runtime.py`, `backend/server.py`, `backend/app/routers/broker.py`, `tests/test_live_feed_health.py`, plus the spec/plan docs.

---

## Self-Review

**1. Spec coverage (Plan 1 = the backend half of the spec):**
- Pure feed-health model, 5 states, master signal = candle freshness → Task 1. ✓
- Reconciler decision + executor + `supervise_once` (start-whats-down, stop-at-close, blocked-needs-login, honor manual stop, backoff/last_error) → Task 2. ✓
- Supervisor loop at boot (market-hours gated, IST, 20 s, never kills the loop) → Task 3. ✓
- `GET /live-feed/health` assembling token/stream/roller/last-candle/supervisor → Task 4. ✓
- Manual-stop suppression so auto-reconcile doesn't fight a deliberate Stop → Task 4. ✓
- Offline-first / never-raises → Task 1 (`compute_feed_health` defensive) + Task 3 (loop try/except). ✓
- (Frontend LED + banner = Plan 2, separate.) The spec's per-deployment liveness is client-side (Plan 2).

**2. Placeholder scan:** every code step shows complete code; the "confirm X is already imported" notes name exact symbols and are verification instructions, not placeholders. ✓

**3. Type/name consistency:** `compute_feed_health`, `decide_feed_actions`, `execute_feed_actions`, `supervise_once`, `feed_supervisor_state`, `_feed_supervisor`, `_live_feed_supervisor_loop`, state names (`LIVE`/`WARMING_UP`/`DEGRADED`/`NEEDS_LOGIN`/`MARKET_CLOSED`), and the `state` dict keys (`suppressed`/`backoff_active`/`last_error`) are spelled identically across definitions, call sites, and tests. The supervisor `state` dict passed to `supervise_once`/`execute_feed_actions` is the same `_feed_supervisor` exposed by `feed_supervisor_state()`. ✓

**Notes for the executor:**
- Keep `app/live_feed_health.py` import-clean (stdlib/typing only) — it must stay host-importable; never add an `app.db`/motor/Upstox import to it.
- The supervisor `state` is a single shared mutable dict (`_feed_supervisor`); `supervise_once`/`execute_feed_actions` mutate it in place — that's intended (the endpoint reads the same dict via `feed_supervisor_state()`).
- Do not remove the existing `server.py` one-shot bring-up block; it's idempotent with the supervisor.
