"""Synthetic tick injector — the measurement harness for tick-to-pixel latency.

WHY IT EXISTS
-------------
"How fresh is the number on screen?" can only be answered honestly end to end,
and the market is open four and a quarter hours a day. This injects ticks into
the SAME in-memory path a real Upstox frame takes (``_latest_ticks`` + the
pub/sub fan-out), so the stream, the coalescer, the SSE transport and the React
render are all measured for real — the only synthetic part is where the bytes
came from.

WHY IT CANNOT MOVE MONEY
------------------------
Injecting a fake premium into a live process is dangerous: the exit monitor and
the position guard both act on ``_latest_ticks``. Three independent gates, all of
which must hold:

1. **Reserved namespace.** Keys are forced to the ``HARNESS|`` prefix. No
   position, contract, deployment or subscription in this system references that
   prefix, so nothing can mark, exit, or square against an injected price. This
   is the load-bearing gate — it holds even if the other two are wrong.
2. **Opt-in env var.** ``ALPHAFORGE_TICK_REPLAY=1``. Off in every normal run.
3. **Never persisted.** Synthetic ticks are not written to the ``ticks``
   collection, so they cannot leak into the warehouse or any backtest.

An injected tick still *wakes* the streams (the fan-out is not key-filtered), so
the emit path is exercised exactly as a real tick would exercise it.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List

#: Reserved prefix. Nothing else in the system uses it.
HARNESS_PREFIX = "HARNESS|"
ENV_FLAG = "ALPHAFORGE_TICK_REPLAY"


def replay_enabled() -> bool:
    return str(os.environ.get(ENV_FLAG, "")).strip().lower() in ("1", "true", "yes", "on")


def harness_key(n: int) -> str:
    return f"{HARNESS_PREFIX}{int(n)}"


def make_tick(key: str, *, price: float, now_ms: int) -> Dict[str, Any]:
    return {
        "instrument_key": key,
        "ts": now_ms,
        "received_ts": now_ms,
        "ingest_ts": now_ms,       # the clock tick-to-pixel is measured against
        "last_price": round(float(price), 2),
        "last_trade_quantity": 1,
        "close_price": round(float(price), 2),
        "source": "tick_replay_harness",
        "mode": "ltpc",
    }


#: Hard ceilings so a harness can never run away inside a live process.
MAX_RATE_HZ = 500.0
MAX_DURATION_S = 120.0
MAX_KEYS = 64


def clamp_params(rate_hz: float, duration_s: float, keys: int):
    """Clamp harness parameters to safe bounds. Pure, so it is testable without
    actually running a 120-second injection."""
    return (
        max(0.1, min(MAX_RATE_HZ, float(rate_hz))),
        max(0.1, min(MAX_DURATION_S, float(duration_s))),
        max(1, min(MAX_KEYS, int(keys))),
    )


async def replay(
    manager: Any,
    *,
    rate_hz: float = 40.0,
    duration_s: float = 10.0,
    keys: int = 4,
) -> Dict[str, Any]:
    """Inject ticks at ``rate_hz`` for ``duration_s``. Returns what it did.

    Defaults mirror the measured live feed: p50 40 ticks/s across the subscribed
    set.
    """
    if not replay_enabled():
        raise PermissionError(
            f"tick replay is disabled; set {ENV_FLAG}=1 to enable the measurement harness"
        )
    rate_hz, duration_s, keys = clamp_params(rate_hz, duration_s, keys)
    key_list: List[str] = [harness_key(i) for i in range(keys)]
    interval = 1.0 / rate_hz
    sent = 0
    started = time.time()
    deadline = started + duration_s
    loop = asyncio.get_event_loop()
    next_at = loop.time()

    while time.time() < deadline:
        now_ms = int(time.time() * 1000)
        key = key_list[sent % len(key_list)]
        tick = make_tick(key, price=100.0 + (sent % 100) * 0.05, now_ms=now_ms)
        # Same two steps the real receive loop performs, in the same order.
        manager._latest_ticks[key] = tick          # noqa: SLF001 — harness by design
        manager._broadcast([tick])                 # noqa: SLF001
        sent += 1
        next_at += interval
        delay = next_at - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        else:
            next_at = loop.time()
            await asyncio.sleep(0)

    elapsed = time.time() - started
    return {
        "injected": sent,
        "elapsed_s": round(elapsed, 3),
        "actual_rate_hz": round(sent / elapsed, 1) if elapsed else None,
        "keys": key_list,
        "requested_rate_hz": rate_hz,
        "requested_duration_s": duration_s,
        "persisted": False,
    }


def purge(manager: Any) -> int:
    """Remove every harness key from the live tick map."""
    keys = [k for k in list(manager._latest_ticks) if str(k).startswith(HARNESS_PREFIX)]
    for key in keys:
        manager._latest_ticks.pop(key, None)       # noqa: SLF001
    return len(keys)
