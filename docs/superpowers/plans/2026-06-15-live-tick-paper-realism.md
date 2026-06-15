# Live Tick-Driven Paper-Trading Realism — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make deployed-strategy paper trading reflect live execution — tick-level exit monitoring (~1.5s), entries ~2–3s after bar close (no repaint), every held contract always markable, and a live Paper page — so the Signal Journal + Paper Trade pages are trustworthy forward-test evidence.

**Architecture:** A new `LiveExitMonitor` background loop calls the existing, proven `mark_open_deployment_trades` every ~1.5s (vs 60s today); the evaluator loop is retriggered on each *new closed bar* (poll-for-new-bar) instead of a fixed `minute+10s`; `_auto_follow_option_stream` is extended to subscribe the union of the ATM band ∪ every open-trade contract; and the Paper page polls a lightweight open-positions feed every ~2s. Entries stay candle-close (backtest parity). No broker orders.

**Tech Stack:** Python 3.12, FastAPI, Motor (async Mongo); React (CRA), axios. Tests: pytest (host-safe via the existing `tests/test_deployment_evaluator.py` `FakeDB`/`FakeCollection` pattern).

**Spec:** [docs/superpowers/specs/2026-06-15-live-tick-paper-realism-design.md](../specs/2026-06-15-live-tick-paper-realism-design.md)

---

## File Structure

- **Create** `backend/app/live_exit_monitor.py` — `LiveExitMonitor` class (start/stop/status + the ~1.5s cycle that delegates to `mark_open_deployment_trades`). Mirrors `LiveCandleRoller`. One responsibility: fast tick-level exit marking.
- **Create** `tests/test_live_exit_monitor.py` — host-safe tests for the cycle + status + the exit-fires-at-breach-premium behavior (via `FakeDB`).
- **Modify** `backend/app/runtime.py` — instantiate `live_exit_monitor`; extend `_auto_follow_option_stream` to union open-trade contracts; restructure `_deployment_evaluator_loop` to poll-for-new-bar and drop the once-per-minute mark (the monitor owns exits).
- **Modify** `backend/server.py` — start/stop `live_exit_monitor` in the lifespan.
- **Modify** `backend/app/routers/broker.py` — add `/live-exit-monitor/status`.
- **Modify** `backend/app/paper_auto.py` — enforce tick-level time-stop + exit-friction parity inside the mark path.
- **Modify** `backend/app/routers/journals.py` — add `GET /paper/open-positions` (live unrealized P&L from the latest tick).
- **Modify** `frontend/src/lib/api.js` + `frontend/src/pages/PaperTrading.jsx` — fast (~2s) open-positions poll; keep history/stats at ~30s.

---

## PHASE 1 — Live exit monitor (the core)

### Task 1: `LiveExitMonitor` class + cycle

**Files:**
- Create: `backend/app/live_exit_monitor.py`
- Test: `tests/test_live_exit_monitor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_live_exit_monitor.py
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_exit_monitor import LiveExitMonitor


def test_cycle_delegates_to_mark_and_records_stats():
    calls = {"n": 0}

    async def fake_mark(db, *, latest_tick_lookup):
        calls["n"] += 1
        return [{"id": "t1", "closed": True, "exit_reason": "option_target"},
                {"id": "t2", "closed": False}]

    mon = LiveExitMonitor(
        db_factory=lambda: object(),
        tick_lookup_factory=lambda: (lambda k: None),
        mark_fn=fake_mark,
    )
    summaries = asyncio.run(mon._cycle())
    assert calls["n"] == 1
    assert len(summaries) == 2
    st = mon.status()
    assert st["cycles"] == 1
    assert st["auto_closes"] == 1          # only the closed one counts
    assert st["last_error"] is None


def test_cycle_records_error_without_raising():
    async def boom(db, *, latest_tick_lookup):
        raise RuntimeError("kaboom")

    mon = LiveExitMonitor(
        db_factory=lambda: object(),
        tick_lookup_factory=lambda: (lambda k: None),
        mark_fn=boom,
    )
    summaries = asyncio.run(mon._cycle())   # must NOT raise
    assert summaries == []
    assert "kaboom" in (mon.status()["last_error"] or "")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_live_exit_monitor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.live_exit_monitor'`

- [ ] **Step 3: Implement the module**

```python
# backend/app/live_exit_monitor.py
"""Fast tick-level exit monitor for OPEN paper trades.

Mirrors LiveCandleRoller's lifecycle (start/stop/status). Every ~1.5s during NSE
market hours it calls the existing, proven mark_open_deployment_trades — which is
idempotent, status-conditional, and staleness-guarded — so stop/target/spot-mirror/
time-stop exits fire at near-tick latency against the LIVE premium instead of once
per minute. It owns NO subscription state (the option-stream auto-follow guarantees
every held contract stays markable) and never places broker orders.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

POLL_SECONDS = 1.5
_IST = timedelta(hours=5, minutes=30)


def _in_market_hours(now_utc: Optional[datetime] = None) -> bool:
    ist = (now_utc or datetime.now(timezone.utc)) + _IST
    if ist.weekday() >= 5:
        return False
    return dtime(9, 15) <= ist.time() < dtime(15, 30)


class LiveExitMonitor:
    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        tick_lookup_factory: Callable[[], Callable[[str], Optional[Dict[str, Any]]]],
        mark_fn: Callable[..., Awaitable[List[Dict[str, Any]]]],
        poll_seconds: float = POLL_SECONDS,
    ):
        self._db_factory = db_factory
        self._tick_lookup_factory = tick_lookup_factory
        self._mark_fn = mark_fn
        self._poll_seconds = float(poll_seconds)
        self._task: Optional[asyncio.Task] = None
        self._stats: Dict[str, Any] = {
            "running": False, "started_at": None, "cycles": 0,
            "open_trades_checked": 0, "auto_closes": 0,
            "last_run_at": None, "last_error": None,
        }

    def status(self) -> Dict[str, Any]:
        return dict(self._stats)

    async def _cycle(self) -> List[Dict[str, Any]]:
        """One exit-marking pass. Never raises — records errors in stats."""
        try:
            db = self._db_factory()
            tick_lookup = self._tick_lookup_factory()
            summaries = await self._mark_fn(db, latest_tick_lookup=tick_lookup)
            closed = sum(1 for s in (summaries or []) if s.get("closed"))
            self._stats["cycles"] += 1
            self._stats["open_trades_checked"] += len(summaries or [])
            self._stats["auto_closes"] += closed
            self._stats["last_run_at"] = datetime.now(timezone.utc).isoformat()
            self._stats["last_error"] = None
            return summaries or []
        except Exception as exc:  # a single bad cycle must never kill the loop
            self._stats["last_error"] = str(exc)[:240]
            log.exception("live exit monitor cycle failed: %s", exc)
            return []

    async def _run(self) -> None:
        self._stats["running"] = True
        self._stats["started_at"] = datetime.now(timezone.utc).isoformat()
        try:
            while True:
                await asyncio.sleep(self._poll_seconds)
                if not _in_market_hours():
                    continue
                summaries = await self._cycle()
                closed = [s for s in summaries if s.get("closed")]
                if closed:
                    log.info("exit monitor auto-closed %d trade(s): %s", len(closed),
                             ", ".join(f"{s.get('id','')[:8]}/{s.get('exit_reason')}" for s in closed[:5]))
        except asyncio.CancelledError:
            return
        finally:
            self._stats["running"] = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="live-exit-monitor")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_live_exit_monitor.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/live_exit_monitor.py tests/test_live_exit_monitor.py
git commit -m "feat(live): LiveExitMonitor — fast tick-level exit-marking loop

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Exit fires at the breach premium (end-to-end, via FakeDB)

This pins the *behavior* the monitor delivers, using the real `mark_open_deployment_trades` + the existing `FakeDB`.

**Files:**
- Test: `tests/test_live_exit_monitor.py` (append)

- [ ] **Step 1: Append the test**

```python
# tests/test_live_exit_monitor.py (append)
from tests.test_deployment_evaluator import FakeDB  # reuse the in-memory async db
from app.paper_auto import mark_open_deployment_trades


def _open_trade(stop=170.0, target=260.0):
    return {
        "id": "trd1", "status": "OPEN", "instrument_key": "NSE_FO|50614",
        "direction": "PE", "lots": 1, "quantity": 75,
        "entry_price": 200.0, "stop_price": stop, "target_price": target,
        "signal_id": None,
    }


def test_mark_closes_on_stop_at_live_premium():
    db = FakeDB()
    db.paper_trades.rows.append(_open_trade())
    # live tick at 168 (below the 170 stop) -> must auto-close
    tick = {"NSE_FO|50614": {"last_price": 168.0, "ts": None}}
    summaries = asyncio.run(mark_open_deployment_trades(db, latest_tick_lookup=tick.get))
    assert summaries and summaries[0]["closed"] is True
    closed = [t for t in db.paper_trades.rows if t["id"] == "trd1"][0]
    assert str(closed["status"]).upper() == "CLOSED"


def test_mark_leaves_trade_open_when_no_fresh_tick():
    db = FakeDB()
    db.paper_trades.rows.append(_open_trade())
    summaries = asyncio.run(mark_open_deployment_trades(db, latest_tick_lookup=lambda k: None))
    assert summaries == []   # no tick -> untouched, no phantom close
    assert str(db.paper_trades.rows[0]["status"]).upper() == "OPEN"
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_live_exit_monitor.py -q`. If `mark_trade_to_market`'s exact stop semantics differ (e.g. it needs `last_price` set first or a different field name), READ `backend/app/paper_trading.py` `mark_trade_to_market` + `close_trade` and adjust the trade dict keys to match (do not change production code for the test). Expected after alignment: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_live_exit_monitor.py
git commit -m "test(live): exit monitor closes on stop at live premium; no fresh tick -> untouched

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Wire the monitor into runtime + lifespan + status route

**Files:**
- Modify: `backend/app/runtime.py` (near the `live_candle_roller = LiveCandleRoller(...)` instantiation ~line 69)
- Modify: `backend/server.py` (lifespan: start ~line 122 region, stop ~line 157; import ~line 43)
- Modify: `backend/app/routers/broker.py` (after `/live-candles/status` ~line 113; import ~line 27)

- [ ] **Step 1: Instantiate in `runtime.py`** — after the `live_candle_roller = LiveCandleRoller(...)` block (~line 69–78), add:

```python
# backend/app/runtime.py
from app.live_exit_monitor import LiveExitMonitor  # top-of-file import block

live_exit_monitor = LiveExitMonitor(
    db_factory=get_db,
    tick_lookup_factory=lambda: upstox_stream_manager.latest_tick_map().get,
    mark_fn=mark_open_deployment_trades,
)
```

> Confirm `mark_open_deployment_trades` is imported in runtime.py (it's already used at line 184 — `from app.paper_auto import ... mark_open_deployment_trades`). If imported under a different alias, use that.

- [ ] **Step 2: Start/stop in `server.py` lifespan** — mirror `live_candle_roller`. Add to the imports (line ~40-43):

```python
# backend/server.py
from app.runtime import (
    ...,
    live_exit_monitor,
)
```

After `await live_candle_roller.start()` (~line 122):

```python
                await live_exit_monitor.start()
```

In the shutdown block, after `await live_candle_roller.stop()` (~line 157):

```python
        try:
            await live_exit_monitor.stop()
        except Exception as exc:
            log.warning("live_exit_monitor.stop() failed: %s", exc)
```

- [ ] **Step 3: Status route in `broker.py`** — import `live_exit_monitor` (add to the `from app.runtime import (...)` block ~line 27), then after the `/live-candles/status` handler (~line 113):

```python
# backend/app/routers/broker.py
@api.get("/live-exit-monitor/status")
async def live_exit_monitor_status():
    return serialize_doc(live_exit_monitor.status())
```

- [ ] **Step 4: Syntax-check + commit**

Run: `python -m py_compile backend/app/runtime.py backend/server.py backend/app/routers/broker.py`
Expected: no errors. (Full behavior is verified in the container in Phase 6.)

```bash
git add backend/app/runtime.py backend/server.py backend/app/routers/broker.py
git commit -m "feat(live): start LiveExitMonitor in lifespan + /live-exit-monitor/status

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## PHASE 2 — Subscription coverage (every held contract stays markable)

### Task 4: Union open-trade contracts into the auto-follow

**Files:**
- Modify: `backend/app/runtime.py` `_auto_follow_option_stream` (~line 826–840)

- [ ] **Step 1: Extend the desired-key set.** In `_auto_follow_option_stream`, after `option_keys = universe.get("instrument_keys") or []` (~line 831), union the OPEN paper-trade contracts so a drifted-strike position stays subscribed:

```python
# backend/app/runtime.py  (inside _auto_follow_option_stream, after option_keys = ...)
        # Always keep EVERY open paper trade's contract subscribed, even if its
        # strike has drifted out of the ATM band — else the exit monitor loses its
        # premium feed and a stop could blow past un-monitored.
        open_keys = await db.paper_trades.distinct("instrument_key", {"status": "OPEN"})
        option_keys = list(dict.fromkeys([*option_keys, *(str(k) for k in open_keys if k)]))
```

> This is inside the `radius > 0` path, which is how the per-minute loop calls it (`min_radius=OPTION_CHAIN_BASELINE_RADIUS=3`), so `radius` is always ≥3 during market hours. The downstream idempotency check (`set(option_keys).issubset(current_keys)`) now includes the open contracts, so a missing held key triggers a re-center restart automatically.

- [ ] **Step 2: Trigger auto-follow immediately on auto-open.** In `_deployment_evaluator_loop` (runtime.py ~line 178), right after the `auto_opened` log, refresh coverage so a newly-opened trade's contract is subscribed before the next exit cycle:

```python
# backend/app/runtime.py  (in _deployment_evaluator_loop, after the auto_opened block ~line 180)
            if auto_opened:
                await _auto_follow_option_stream(min_radius=OPTION_CHAIN_BASELINE_RADIUS)
```

- [ ] **Step 3: Syntax-check + commit**

Run: `python -m py_compile backend/app/runtime.py`
Expected: no errors.

```bash
git add backend/app/runtime.py
git commit -m "feat(live): auto-follow subscribes union of ATM band + every open-trade contract

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## PHASE 3 — Entries ~2–3s after close (poll-for-new-bar); drop the per-minute mark

### Task 5: Retrigger evaluation on each new closed bar

**Files:**
- Modify: `backend/app/runtime.py` `_deployment_evaluator_loop` (~line 151–217)

- [ ] **Step 1: Replace the fixed `minute+10s` sleep with a fast new-bar poll.** Read the current loop (runtime.py:151–217) first. Then change the timing so the loop wakes every ~2s, and only runs `evaluate_active_deployments` when a NEW closed bar has appeared in `candles_1m` for a tracked instrument (else it just does housekeeping cheaply). Replace the sleep/loop head:

```python
# backend/app/runtime.py  (_deployment_evaluator_loop)
    last_bar_ts = 0
    EVAL_POLL_SECONDS = 2.0
    FALLBACK_MAX_WAIT = 70.0  # never wait longer than a bar+grace even if the feed gaps
    while True:
        try:
            await asyncio.sleep(EVAL_POLL_SECONDS)

            ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            today_ist = ist_now.strftime("%Y-%m-%d")
            if ist_now.weekday() >= 5:
                continue
            t = ist_now.time()
            if t < _time(9, 15) or t >= _time(15, 30):
                continue

            # New-bar detection: only evaluate when the roller has flushed a NEW
            # closed 1-min spot bar (so entries fire ~2-3s after close, never on the
            # forming bucket). NIFTY is the lead instrument for the cadence.
            latest = await db.candles_1m.find_one(
                {"instrument": "NIFTY"}, {"_id": 0, "ts": 1}, sort=[("ts", -1)])
            latest_ts = int((latest or {}).get("ts") or 0)
            fresh_bar = latest_ts > last_bar_ts
            if not fresh_bar:
                continue
            last_bar_ts = latest_ts

            tick_lookup = upstox_stream_manager.latest_tick_map().get
            results = await evaluate_active_deployments(db, latest_tick_lookup=tick_lookup)
```

Keep the existing `interesting` / `auto_opened` logging and the Task-4 `_auto_follow_option_stream` call. **Delete** the once-per-minute `mark_open_deployment_trades` block (the `marked = await mark_open_deployment_trades(...)` ~line 184–191) — exits are now owned by `LiveExitMonitor`. Keep the 15:00 square-off + the auto-follow housekeeping (they run on each fresh bar, ~once/min, which is the right cadence for them).

> The `_time`, `datetime`, `timedelta` imports already exist in this function/module. `last_squareoff_ist_date` and the square-off block stay unchanged.

- [ ] **Step 2: Syntax-check**

Run: `python -m py_compile backend/app/runtime.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add backend/app/runtime.py
git commit -m "feat(live): evaluate on each new closed bar (~2-3s); exits now owned by LiveExitMonitor

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## PHASE 4 — Tick-level time-stop + exit-friction parity

### Task 6: Enforce time-stop + exit friction in the mark path

**Files:**
- Modify: `backend/app/paper_auto.py` (`mark_open_deployment_trades`, inside the per-trade loop ~line 494–538; read `compute_auto_risk_levels` + `close_trade` + `app/live_friction.py` first)
- Test: `tests/test_live_exit_monitor.py` (append)

- [ ] **Step 1: Append the time-stop test**

```python
# tests/test_live_exit_monitor.py (append)
import datetime as _dt


def test_mark_closes_on_time_stop():
    db = FakeDB()
    entry_ts = int((_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=20)).timestamp() * 1000)
    tr = _open_trade()
    tr["entry_ts"] = entry_ts
    tr["risk_hints"] = {"time_stop_minutes": 10}   # 10-min stop, 20 min elapsed
    db.paper_trades.rows.append(tr)
    # premium between stop and target -> only the time-stop should close it
    tick = {"NSE_FO|50614": {"last_price": 205.0, "ts": None}}
    summaries = asyncio.run(mark_open_deployment_trades(db, latest_tick_lookup=tick.get))
    assert summaries and summaries[0]["closed"] is True
    closed = [t for t in db.paper_trades.rows if t["id"] == "trd1"][0]
    assert "time_stop" in str(closed.get("exit_reason") or "")
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_live_exit_monitor.py -k time_stop -q`. Expected: FAIL (no time-stop handling yet).

- [ ] **Step 3: Implement the time-stop** in `mark_open_deployment_trades`, inside the per-trade loop, AFTER the premium mark (≈ after line 509, before the spot-mirror block), so a time-stop closes at the current premium:

```python
# backend/app/paper_auto.py  (inside the per-trade loop in mark_open_deployment_trades)
            # Tick-level time-stop: close at the live premium when the strategy's
            # time_stop_minutes has elapsed (parity with the backtest's time exit).
            if str(updated.get("status") or "").upper() == "OPEN":
                tsm = (updated.get("risk_hints") or {}).get("time_stop_minutes")
                entry_ts = updated.get("entry_ts")
                if tsm and entry_ts and option_price is not None:
                    elapsed_min = (now_ms - int(entry_ts)) / 60000.0
                    if elapsed_min >= float(tsm):
                        updated = close_trade(updated, exit_price=option_price,
                                              reason="time_stop", at=at)
                        updated["exit_price_source"] = "live_tick"
                        updated["exit_price_stale"] = False
                        wrote = True
```

- [ ] **Step 4: Exit-friction parity.** Read `backend/app/live_friction.py` for `apply_exit_friction(price, cfg, ts_ms=...)` and how `build_auto_trade` builds `FrictionConfig`. In `mark_open_deployment_trades`, where a CLOSE is booked at a live premium (the premium-stop/target path via `mark_trade_to_market`, the spot-mirror path, and the new time-stop), slip the exit fill with `apply_exit_friction` when the trade carries friction context — mirroring the entry. If `mark_trade_to_market` already applies exit friction internally, leave that path; only add friction to the close paths that bypass it (spot-mirror, time-stop). Add the friction `moneyness`/`expiry_iso` from the trade's stored context (same fields `build_auto_trade` set). Show the exact lines you add.

> If the trade docs don't yet carry the friction config needed at exit, thread the minimal fields through `build_auto_trade` (store `friction` on the trade) — but only if needed; prefer reusing what's already on the doc. Keep this change small and DRY.

- [ ] **Step 5: Run to verify the time-stop test passes** — `python -m pytest tests/test_live_exit_monitor.py -q`. Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add backend/app/paper_auto.py tests/test_live_exit_monitor.py
git commit -m "feat(live): tick-level time-stop + exit-friction parity in the mark path

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## PHASE 5 — Live Paper page

### Task 7: `GET /paper/open-positions` (live unrealized P&L from latest tick)

**Files:**
- Modify: `backend/app/routers/journals.py` (after `list_paper_trades` ~line 264)
- Test: `tests/test_live_paper_feed.py` (new) — host-safe via FakeDB

- [ ] **Step 1: Write the failing test**

```python
# tests/test_live_paper_feed.py
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.paper_open_positions import build_open_positions


def test_open_positions_live_unrealized_from_tick():
    trades = [{
        "id": "t1", "status": "OPEN", "instrument_key": "NSE_FO|50614",
        "entry_price": 200.0, "quantity": 75, "lots": 1,
        "stop_price": 170.0, "target_price": 260.0,
    }]
    # live premium 230 -> unrealized = (230-200)*75 = 2250
    tick_lookup = (lambda k: {"last_price": 230.0, "ts": None}).__call__
    out = build_open_positions(trades, latest_tick_lookup=lambda k: {"last_price": 230.0, "ts": None})
    p = out["items"][0]
    assert p["live_premium"] == 230.0
    assert round(p["unrealized_pnl"], 2) == 2250.0
    assert p["dist_to_stop"] == round(230.0 - 170.0, 2)
    assert p["dist_to_target"] == round(260.0 - 230.0, 2)
    assert out["open_mtm"] == round(2250.0, 2)


def test_open_positions_stale_falls_back_to_persisted_mark():
    trades = [{
        "id": "t2", "status": "OPEN", "instrument_key": "NSE_FO|99999",
        "entry_price": 100.0, "quantity": 75, "lots": 1, "unrealized_pnl": -750.0,
    }]
    out = build_open_positions(trades, latest_tick_lookup=lambda k: None)  # no live tick
    p = out["items"][0]
    assert p["live_stale"] is True
    assert p["unrealized_pnl"] == -750.0   # persisted fallback
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_live_paper_feed.py -q`. Expected: FAIL (`No module named 'app.paper_open_positions'`).

- [ ] **Step 3: Implement the pure builder** (host-testable; no motor):

```python
# backend/app/paper_open_positions.py
"""Pure shaping of OPEN paper trades into a live open-positions view: unrealized
P&L computed from the latest tick at request time, with a persisted-mark fallback
when no fresh tick exists. No DB access — the router supplies the rows + lookup."""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional


def _live_price(tick_lookup, key: str) -> Optional[float]:
    if not key:
        return None
    tick = tick_lookup(key)
    if not tick or tick.get("last_price") in (None, ""):
        return None
    try:
        p = float(tick["last_price"])
    except (TypeError, ValueError):
        return None
    return p if p > 0 else None


def build_open_positions(
    trades: List[Dict[str, Any]],
    *,
    latest_tick_lookup: Callable[[str], Optional[Dict[str, Any]]],
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    open_mtm = 0.0
    for t in trades:
        key = str(t.get("instrument_key") or "")
        qty = float(t.get("quantity") or 0) or (float(t.get("lots") or 0) * 0)
        entry = float(t.get("entry_price") or 0)
        live = _live_price(latest_tick_lookup, key)
        stale = live is None
        if live is not None and qty:
            unreal = round((live - entry) * qty, 2)
        else:
            unreal = round(float(t.get("unrealized_pnl") or 0), 2)
        premium = live if live is not None else (
            float(t.get("last_price") or t.get("entry_price") or 0) or None)
        stop = t.get("stop_price")
        target = t.get("target_price")
        items.append({
            "id": t.get("id"),
            "instrument_key": key,
            "deployment_name": t.get("deployment_name"),
            "direction": t.get("direction"),
            "entry_price": entry,
            "live_premium": premium,
            "live_stale": stale,
            "unrealized_pnl": unreal,
            "dist_to_stop": (round(float(premium) - float(stop), 2)
                             if premium is not None and stop is not None else None),
            "dist_to_target": (round(float(target) - float(premium), 2)
                               if premium is not None and target is not None else None),
            "entry_ts": t.get("entry_ts"),
            "mfe_pts": t.get("mfe_pts"), "mae_pts": t.get("mae_pts"),
        })
        open_mtm += unreal
    return {"items": items, "open_mtm": round(open_mtm, 2), "count": len(items)}
```

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_live_paper_feed.py -q`. Expected: PASS.

- [ ] **Step 5: Add the route** in `journals.py` (after `list_paper_trades`, ~line 264). Import `build_open_positions` + the tick lookup:

```python
# backend/app/routers/journals.py
from app.paper_open_positions import build_open_positions
from app.runtime import upstox_stream_manager


@api.get("/paper/open-positions")
async def paper_open_positions():
    """Live OPEN positions: unrealized P&L from the latest tick at request time.
    Lightweight (OPEN only) so the Paper page can poll it every ~2s."""
    db = get_db()
    rows = await db.paper_trades.find({"status": "OPEN"}, {"_id": 0, "events": 0}).to_list(length=500)
    dep_ids = sorted({str(r.get("deployment_id")) for r in rows if r.get("deployment_id")})
    if dep_ids:
        names = {str(d["id"]): str(d.get("name") or "") for d in
                 await db.strategy_deployments.find({"id": {"$in": dep_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(length=len(dep_ids))}
        for r in rows:
            r["deployment_name"] = names.get(str(r.get("deployment_id") or ""), "")
    out = build_open_positions(rows, latest_tick_lookup=upstox_stream_manager.latest_tick_map().get)
    return serialize_doc(out)
```

> Confirm `get_db`, `serialize_doc` are already imported in journals.py (they are — used by `list_paper_trades`). If importing `upstox_stream_manager` from `app.runtime` risks a circular import, import it lazily inside the handler.

- [ ] **Step 6: Run full suite + commit**

Run: `python -m pytest tests -q`
Expected: all pass.

```bash
git add backend/app/paper_open_positions.py backend/app/routers/journals.py tests/test_live_paper_feed.py
git commit -m "feat(live): GET /paper/open-positions — live unrealized P&L from latest tick

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Paper page — fast open-positions poll

**Files:**
- Modify: `frontend/src/lib/api.js` (add `openPositions`)
- Modify: `frontend/src/pages/PaperTrading.jsx` (split refresh)

- [ ] **Step 1: Add the API method** in `api.js` near `listPaperTrades`:

```js
// frontend/src/lib/api.js
openPositions: () => apiClient.get("/paper/open-positions").then((r) => r.data),
```

- [ ] **Step 2: Read `PaperTrading.jsx`** to find the state holding rows + the `setInterval(fetchRows, 30000)` (~line 153) and how OPEN rows render `unrealized_pnl` / open-MTM (~lines 83, 214, 384).

- [ ] **Step 3: Add a fast open-positions poll** alongside the existing 30s `fetchRows`. Add state + a 2s interval that fetches `api.openPositions()` and merges the live fields (`live_premium`, `unrealized_pnl`, `live_stale`, `dist_to_stop`, `dist_to_target`) onto the matching OPEN rows by `id`, and updates the header Open MTM from `open_mtm`:

```jsx
// frontend/src/pages/PaperTrading.jsx
const [livePos, setLivePos] = useState({ items: [], open_mtm: 0 });
useEffect(() => {
  let alive = true;
  const tick = async () => {
    try {
      const data = await api.openPositions();
      if (alive) setLivePos(data || { items: [], open_mtm: 0 });
    } catch { /* transient; keep last */ }
  };
  tick();
  const id = window.setInterval(tick, 2000);
  return () => { alive = false; window.clearInterval(id); };
}, []);
// when rendering OPEN rows, prefer the live value:
//   const live = livePos.items.find((p) => p.id === t.id);
//   const upnl = live ? live.unrealized_pnl : Number(t.unrealized_pnl || 0);
// and show live.live_premium, live.dist_to_stop/target, a "stale" dot when live.live_stale.
// header Open MTM: use livePos.open_mtm when available, else the 30s-derived value.
```

Keep `setInterval(fetchRows, 30000)` for the full journal + stats. Wire the live values into the OPEN-row cells + the Open-MTM header (match the file's existing components/classes).

- [ ] **Step 4: Build + commit**

```bash
cd frontend && npm run build
```
Expected: compiles (warnings OK).

```bash
git add frontend/src/lib/api.js frontend/src/pages/PaperTrading.jsx
git commit -m "feat(paper-ui): live open-positions poll (~2s) — live P&L/premium/distance, fast Open MTM

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## PHASE 6 — Full-stack verification + docs

### Task 9: Rebuild, verify live behavior, document

- [ ] **Step 1: Rebuild** (backend + frontend changed):

```bash
docker compose up -d --build
```

- [ ] **Step 2: Backend regression** (in the container, where motor/optuna exist):

```bash
docker compose exec backend python -m pytest tests -q
```
Expected: all pass (host suite + new live tests).

- [ ] **Step 3: Health + monitors alive:**

```bash
curl -s http://localhost:8001/api/live-exit-monitor/status
curl -s http://localhost:8001/api/live-candles/status
```
Expected: exit monitor `running:true`, `cycles` incrementing; roller running.

- [ ] **Step 4: Live behavior (market hours).** With an ACTIVE paper deployment that produces a signal: confirm a paper trade opens at the live tick; on the Paper page the open P&L/premium tick **every ~2s**; force/await a stop and confirm the trade closes within ~1.5s at the live premium (check `exit_reason` + that `closed_at` is seconds after the breach, not at the next minute). Confirm `GET /paper/open-positions` returns live `unrealized_pnl`.

- [ ] **Step 5: Off-path safety.** Outside market hours both loops idle (no closes, no error spam). A trade with no fresh tick is left OPEN (no phantom close).

- [ ] **Step 6: Update CHANGELOG + HANDOFF** (0.41.x: live tick-driven paper realism — fast exit monitor, new-bar eval, union subscription, time-stop, live Paper page) and commit:

```bash
git add CHANGELOG.md docs/HANDOFF.md
git commit -m "docs: record live tick-driven paper realism + verification"
```

---

## Self-Review Notes (author)

- **Spec coverage:** exit monitor ~1.5s (Task 1–3) ✓; entries ~2–3s new-bar, no repaint (Task 5) ✓; union-subscription for held contracts (Task 4) ✓; tick-level time-stop (Task 6) ✓; exit-friction parity (Task 6) ✓; live Paper page (Task 7–8) ✓; status/observability (Task 3) ✓; staleness/idempotency/no-broker safety (reused machinery + Task 1) ✓; no per-minute mark after monitor owns exits (Task 5) ✓.
- **Open implementation risks to watch:** (1) Task 2/6 — align the test trade-dict keys with the real `mark_trade_to_market`/`close_trade` field names (read `paper_trading.py` first; don't change prod for tests). (2) Task 5 — read the real loop before editing; preserve the square-off + `last_squareoff_ist_date`. (3) Task 7 — guard the `app.runtime` import in `journals.py` against circular import (lazy-import if needed). (4) Task 6 exit-friction — only add friction to close paths that bypass `mark_trade_to_market`; keep DRY.
- **Timeliness audit (spec §6.4)** is surfaced via the existing `bar_ts`/`decision_ts` on signals + the new `entry_ts`/age on open positions; no separate task needed. **Health indicator (spec §6.5)** backend is Task 3; the cockpit tile is a thin FE follow-up, noted but out of this plan's critical path.
