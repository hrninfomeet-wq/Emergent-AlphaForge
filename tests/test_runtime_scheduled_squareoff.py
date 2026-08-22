"""Behavioural regression for the runtime's 15:00 paper EOD sweep.

The scheduled caller once placed ``honour_allow_overnight=True`` inside the
``latest_tick_map(...)`` call.  Both functions existed and the module compiled,
while the source-contract test saw the expected text, but the live scheduler
raised ``TypeError`` before it ever reached ``square_off_open_paper_trades``.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import runtime  # noqa: E402


class _Candles:
    async def find_one(self, *args, **kwargs):
        return None


class _DB:
    candles_1m = _Candles()


def test_runtime_15_00_cycle_calls_squareoff_with_overnight_policy(monkeypatch):
    """Drive one scheduler cycle; argument ownership must match the two APIs."""
    squareoff_calls = []
    sleep_calls = 0

    class _FridayAtSquareoff:
        @classmethod
        def now(cls, tz=None):
            # Runtime adds IST_OFFSET itself: 09:30 UTC -> 15:00 IST on Friday.
            return datetime(2026, 8, 14, 9, 30, tzinfo=timezone.utc)

    async def _sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError

    async def _squareoff(*args, **kwargs):
        squareoff_calls.append((args, kwargs))
        return []

    monkeypatch.setattr(runtime, "datetime", _FridayAtSquareoff)
    monkeypatch.setattr(runtime.asyncio, "sleep", _sleep)
    monkeypatch.setattr(runtime, "get_db", lambda: _DB())
    monkeypatch.setattr(runtime, "is_square_off_due", lambda _now: True)
    monkeypatch.setattr(runtime, "square_off_open_paper_trades", _squareoff)
    # Strict zero-argument stand-in for the production API. Passing the square-off
    # policy here reproduces the former TypeError rather than hiding it in a **kwargs fake.
    monkeypatch.setattr(runtime.upstox_stream_manager, "latest_tick_map", lambda: {})

    try:
        asyncio.run(runtime._deployment_evaluator_loop())
    except asyncio.CancelledError:
        # The broken call reaches the loop's backoff sleep; the fixed call reaches
        # the next iteration, whose cancellation is handled inside the loop.
        pass

    assert len(squareoff_calls) == 1, "the due 15:00 paper sweep was never invoked"
    args, kwargs = squareoff_calls[0]
    assert isinstance(args[0], _DB)
    assert kwargs["latest_tick_lookup"] is not None
    assert kwargs["reason"] == "auto_square_off_15_00_IST"
    assert kwargs["honour_allow_overnight"] is True


def test_sensex_bar_wakes_evaluator_while_nifty_is_stalled(monkeypatch):
    """One instrument's stalled feed must not freeze every deployment."""
    sleep_calls = 0
    evaluate_calls = []

    class _FridayDuringMarket:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 14, 4, 30, tzinfo=timezone.utc)  # 10:00 IST

    class _InstrumentCandles:
        async def find_one(self, query, *args, **kwargs):
            instrument = query["instrument"]
            # All three begin on bar 100. Only SENSEX closes bar 200 on the
            # second cycle; NIFTY deliberately remains frozen.
            ts = 200 if instrument == "SENSEX" and sleep_calls >= 2 else 100
            return {"ts": ts}

    class _InstrumentDB:
        candles_1m = _InstrumentCandles()

    async def _sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 2:
            raise asyncio.CancelledError

    async def _evaluate(*args, **kwargs):
        evaluate_calls.append(True)
        return []

    async def _follow(*args, **kwargs):
        return {"restarted": False}

    monkeypatch.setattr(runtime, "datetime", _FridayDuringMarket)
    monkeypatch.setattr(runtime.asyncio, "sleep", _sleep)
    monkeypatch.setattr(runtime, "get_db", lambda: _InstrumentDB())
    monkeypatch.setattr(runtime, "is_square_off_due", lambda _now: False)
    monkeypatch.setattr(runtime, "evaluate_active_deployments", _evaluate)
    monkeypatch.setattr(runtime, "_auto_follow_option_stream", _follow)
    monkeypatch.setattr(runtime.upstox_stream_manager, "latest_tick_map", lambda: {})

    asyncio.run(runtime._deployment_evaluator_loop())

    assert len(evaluate_calls) == 2, (
        "the SENSEX bar did not wake the evaluator after NIFTY stopped advancing")
