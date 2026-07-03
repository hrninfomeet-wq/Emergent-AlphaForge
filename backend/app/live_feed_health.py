"""Live-feed health model + auto-reconcile decision logic.

PURE + host-importable (stdlib/typing only — NO motor, NO app.db, NO Upstox).
The master liveness signal is candles_1m freshness. (Original design: the
live-feed-health truthful-liveness spec, retired to git history 2026-07-01.)
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
    if age is None:
        return out(DEGRADED, "No live candles yet — feed stalled.")
    mins = round(age / 60)
    return out(DEGRADED, f"No live candles for ~{mins} min — feed stalled.")


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


def decide_exit_monitor_action(*, market_open: bool, token_ok: bool, suppressed: bool,
                               running: bool) -> Optional[str]:
    """Pure reconciler decision for the paper tick-exit / mark-to-market monitor
    (LiveExitMonitor), in PARITY with the roller. Returns "start_exit_monitor",
    "stop_exit_monitor", or None.

    The monitor is what marks OPEN paper trades to the live premium (unrealized /
    MFE / MAE / P&L-series) and fires tick-level stop/target exits. It must run
    whenever the feed should be live. Mirrors decide_feed_actions: market close
    tears it down; token expiry (NEEDS_LOGIN) leaves it as-is (the marker
    self-guards on stale ticks); manual suppression is respected; otherwise it is
    (re)started when not already running. This is the self-heal for the
    boot-before-OAuth gap — the supervisor revives the roller, but without this the
    monitor stays dead and open trades are never marked (blotter columns stuck at 0)."""
    if not market_open:
        return "stop_exit_monitor" if running else None
    if not token_ok:
        return None            # human OAuth required; leave the monitor unchanged
    if suppressed:
        return None            # user manually stopped the feed; don't fight it
    return "start_exit_monitor" if not running else None


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
