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


def test_degraded_when_running_past_warmup_but_no_bar_ever():
    now = _ist(11, 0)
    h = _health(now_ist=now, now_ms=_ms(now), stream_running=True, roller_running=True,
                roller_started_ms=_ms(_ist(9, 30)),  # long past grace
                last_candle_ts=None, supervisor_backoff_active=False)
    assert h["state"] == DEGRADED
    assert "feed stalled" in h["reason"] and "?" not in h["reason"]


def test_health_never_raises_on_bad_inputs():
    h = compute_feed_health(now_ist=_ist(11, 0), now_ms=_ms(_ist(11, 0)), is_trading_day=True,
                            token=None, stream_running=None, roller_running=None,
                            roller_started_ms=None, last_candle_ts=None)
    assert h["state"] in (NEEDS_LOGIN, MARKET_CLOSED, DEGRADED, WARMING_UP)


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


# --- exit-monitor reconcile (paper tick-exit/mark loop, parity with the roller) ---
from app.live_feed_health import decide_exit_monitor_action  # noqa: E402


def test_exit_monitor_started_when_feed_live_but_monitor_dead():
    # THE BUG: market open + token ok + feed revived by the supervisor, but the
    # tick-exit/mark monitor was left dead (boot happened before the daily OAuth).
    # It must be (re)started so open paper trades get marked-to-market + auto-exited.
    assert decide_exit_monitor_action(
        market_open=True, token_ok=True, suppressed=False, running=False
    ) == "start_exit_monitor"


def test_exit_monitor_idempotent_when_already_running():
    assert decide_exit_monitor_action(
        market_open=True, token_ok=True, suppressed=False, running=True
    ) is None


def test_exit_monitor_stopped_at_session_end():
    assert decide_exit_monitor_action(
        market_open=False, token_ok=True, suppressed=False, running=True
    ) == "stop_exit_monitor"
    assert decide_exit_monitor_action(
        market_open=False, token_ok=True, suppressed=False, running=False
    ) is None


def test_exit_monitor_left_as_is_on_token_expiry_midsession():
    # Parity with decide_feed_actions: token expiry (needs login) does NOT tear the
    # feed down; the monitor self-guards on stale ticks, so leave it unchanged.
    assert decide_exit_monitor_action(
        market_open=True, token_ok=False, suppressed=False, running=True
    ) is None
    assert decide_exit_monitor_action(
        market_open=True, token_ok=False, suppressed=False, running=False
    ) is None


def test_exit_monitor_respects_manual_suppression():
    # User manually stopped the feed (STOP button) — don't fight it.
    assert decide_exit_monitor_action(
        market_open=True, token_ok=True, suppressed=True, running=False
    ) is None
