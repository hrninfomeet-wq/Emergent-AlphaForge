"""The shared SSE coalescer behind every tick-driven stream.

Ticks arrive at a measured p50 40/s, p95 50/s across ~53 subscribed instruments.
Pushing one frame per tick would be 40 renders/s for a number a human reads a few
times a second, and would make a slow client stall the WS producer. So the
stream coalesces to a bounded rate and drops rather than queues.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.tick_stream import tick_event_stream


class _Harness:
    """Drives tick_event_stream with a fake manager and a controllable clock."""

    def __init__(self, *, payloads=None, disconnect_after=None):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.subscribed = 0
        self.unsubscribed = 0
        self.builds = 0
        self.emitted = 0
        self._payloads = payloads
        self._disconnect_after = disconnect_after

    def subscribe(self, **_kw):
        self.subscribed += 1
        return self.queue

    def unsubscribe(self, _q):
        self.unsubscribed += 1

    async def build(self):
        self.builds += 1
        if self._payloads is not None:
            return self._payloads[min(self.builds - 1, len(self._payloads) - 1)]
        return {"n": self.builds}

    async def is_disconnected(self):
        if self._disconnect_after is None:
            return False
        return self.emitted >= self._disconnect_after

    async def collect(self, **kwargs):
        frames = []
        async for frame in tick_event_stream(
            subscribe=self.subscribe,
            unsubscribe=self.unsubscribe,
            build_payload=self.build,
            is_disconnected=self.is_disconnected,
            **kwargs,
        ):
            frames.append(frame)
            self.emitted += 1
        return frames


def test_paints_immediately_on_connect_before_any_tick():
    """The page must not show a blank card until the first tick arrives."""
    h = _Harness(disconnect_after=1)
    frames = asyncio.run(h.collect(min_interval_s=0.0, heartbeat_s=0.01))
    assert len(frames) == 1
    assert frames[0].startswith("event: snapshot\ndata: ")
    assert frames[0].endswith("\n\n")
    assert '"n": 1' in frames[0] or '"n":1' in frames[0]


def test_a_burst_of_ticks_coalesces_into_one_emit():
    """50 ticks in one instant must produce one payload build, not 50."""
    h = _Harness(disconnect_after=2)

    async def run():
        for i in range(50):
            h.queue.put_nowait({"instrument_key": f"K{i}"})
        return await h.collect(min_interval_s=0.0, heartbeat_s=5.0)

    frames = asyncio.run(run())
    # 1 connect snapshot + 1 coalesced burst.
    assert len(frames) == 2
    assert h.builds == 2, f"expected 2 payload builds, got {h.builds}"
    assert h.queue.qsize() == 0, "the burst must be fully drained"


def test_min_interval_bounds_the_emit_rate():
    """Back-to-back ticks are rate-limited to min_interval, so a 40/s feed cannot
    drive 40 React renders/s."""
    h = _Harness(disconnect_after=3)

    async def run():
        for i in range(10):
            h.queue.put_nowait({"i": i})
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        frames = await h.collect(min_interval_s=0.05, heartbeat_s=5.0)
        return frames, loop.time() - t0

    frames, elapsed = asyncio.run(run())
    assert len(frames) == 3
    # Two gated emits after the immediate one => at least one min_interval wait.
    assert elapsed >= 0.05, f"emits were not rate-limited (elapsed {elapsed:.3f}s)"


def test_heartbeat_keeps_the_connection_open_when_nothing_ticks():
    """Without this a proxy (or an idle market) silently closes the stream."""
    h = _Harness(disconnect_after=3)
    frames = asyncio.run(h.collect(min_interval_s=0.0, heartbeat_s=0.01))
    assert frames[0].startswith("event: snapshot")
    assert any(f.startswith("event: heartbeat") for f in frames[1:])
    # A heartbeat must NOT cost a payload build (no broker/DB work while idle).
    assert h.builds == 1


def test_always_unsubscribes_even_when_the_consumer_aborts():
    """A leaked queue stays in the manager set forever and the producer keeps
    filling it — a slow memory leak plus wasted work per tick."""
    h = _Harness()

    async def run():
        agen = tick_event_stream(
            subscribe=h.subscribe, unsubscribe=h.unsubscribe,
            build_payload=h.build, is_disconnected=h.is_disconnected,
            min_interval_s=0.0, heartbeat_s=5.0,
        )
        await agen.__anext__()          # take the connect snapshot
        await agen.aclose()             # client goes away mid-stream

    asyncio.run(run())
    assert h.subscribed == 1
    assert h.unsubscribed == 1


def test_a_failing_payload_build_does_not_kill_the_stream():
    """One bad build (broker blip) must degrade to an error event, not a dead
    stream that silently stops updating a real-money screen."""
    state = {"n": 0}

    async def flaky():
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("broker blip")
        return {"n": state["n"]}

    h = _Harness(disconnect_after=3)
    h.build = flaky
    # Feed one tick per emit so the failing build and the recovering build land in
    # SEPARATE batches (a single drained batch would collapse them into one).
    original_is_disconnected = h.is_disconnected

    async def feed_then_check():
        h.queue.put_nowait({"t": h.emitted})
        return await original_is_disconnected()

    h.is_disconnected = feed_then_check

    frames = asyncio.run(h.collect(min_interval_s=0.0, heartbeat_s=5.0))
    assert any("event: stream_error" in f for f in frames), frames
    assert frames[-1].startswith("event: snapshot"), "stream must recover after the blip"
    assert frames[-1].count('"n"') == 1 and '3' in frames[-1]
