# tests/test_live_guard_exit_time.py
"""Phase 5B B5 — per-entry hard square time (square_at_ist) in the live guard.

The registry accepts an optional IST HH:MM square time per entry, normalized
(review C1: raw HH:MM compares are fail-open for unpadded input) and clamped
STRICTLY BEFORE the registry's EOD square at registration (the 15:00 EOD
backstop always wins; a later/equal or invalid value is dropped with a log,
never breaking registration of the stop monitor itself). _evaluate_eod_square
honors the per-entry time with reason "exit_time" and the same
ignore_square_stopped semantics; entries WITHOUT the field behave
byte-identically to the pre-5B EOD-only flow.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest

pytest.importorskip("pymongo", reason="guard imports idempotency (pymongo)")

from app.live.live_position_guard import LiveMonitorRegistry  # noqa: E402
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402


def _register(r: LiveMonitorRegistry, key: str = "N1", **kw):
    return r.register(
        key=key, tsym="NIFTYTEST", exch="NFO", qty=65, prd="I",
        entry_price=100.0, state=build_monitor_state(100.0, stop_pct=20.0),
        source="auto_live", deployment_id="D1", **kw)


def test_square_at_ist_normalized_and_stored():
    r = LiveMonitorRegistry()
    item = _register(r, square_at_ist="14:30")
    assert item["square_at_ist"] == "14:30"
    item2 = _register(r, key="N2", square_at_ist="9:45")   # unpadded valid (C1)
    assert item2["square_at_ist"] == "09:45"


def test_square_at_ist_at_or_after_eod_is_dropped():
    r = LiveMonitorRegistry()
    for bad in ("15:00", "15:13"):
        item = _register(r, key=f"N-{bad}", square_at_ist=bad)
        assert item["square_at_ist"] is None, \
            f"{bad} >= the 15:00 EOD must be dropped (EOD backstop wins)"


def test_square_at_ist_invalid_dropped_registration_survives():
    r = LiveMonitorRegistry()
    item = _register(r, square_at_ist="not-a-time")
    assert item["square_at_ist"] is None
    assert item["id"] == "N1", "an invalid exit TIME must never break the stop monitor"


def test_absent_square_at_ist_is_inert():
    r = LiveMonitorRegistry()
    item = _register(r)
    assert item["square_at_ist"] is None
