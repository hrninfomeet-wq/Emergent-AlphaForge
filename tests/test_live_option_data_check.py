"""Regression: a live signal's option-data freshness check must accept the live
WS tick, not only an options_1m candle.

During a live session option premiums arrive as LTPC ticks in `ticks`; `options_1m`
is filled only by the historical fetch and has no today candles. The old check
looked at options_1m alone, so every live signal was falsely flagged option_no_data
and blocked -> no paper trade. The fix mirrors paper_auto's tick->candle order.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.deployment_evaluator import _has_recent_option_data


class _FakeColl:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, *args, **kwargs):
        return self._doc


class _FakeDB:
    def __init__(self, tick=None, candle=None):
        self.ticks = _FakeColl(tick)
        self.options_1m = _FakeColl(candle)


def _run(db, key):
    return asyncio.run(_has_recent_option_data(db, key))


def test_uses_live_tick_when_options_1m_empty():
    # The real-world live case: fresh tick, NO options_1m candle.
    assert _run(_FakeDB(tick={"ts": 1}, candle=None), "NSE_FO|50614") is True


def test_falls_back_to_warehouse_candle():
    assert _run(_FakeDB(tick=None, candle={"ts": 1}), "NSE_FO|50614") is True


def test_false_when_neither_tick_nor_candle():
    assert _run(_FakeDB(tick=None, candle=None), "NSE_FO|50614") is False


def test_false_for_empty_instrument_key():
    assert _run(_FakeDB(tick={"ts": 1}, candle={"ts": 1}), "") is False
