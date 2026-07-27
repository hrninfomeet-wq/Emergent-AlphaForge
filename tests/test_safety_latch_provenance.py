"""The safety latch must record WHY and WHEN it tripped, and offer a way back.

Background (2026-07-27, Phase 0.1). `blocked_until_reset` halts every new live
entry and cannot self-clear. Two problems made that a dead end for an operator:

  1. `trip()` only flipped a boolean — nothing recorded the reason or the time, so
     a halted desk could not tell WHY it was halted or whether the halt was
     minutes or days old.
  2. `POST /live-broker/safety-config/reset-latch` exists but was called from
     NOWHERE in the frontend (verified: 0 references), so the only exit was a
     raw API call.

This became urgent the same day: C3 (`live_deploy_governor.check_account_caps`)
gave `engine.guardrail_tick()` its FIRST production caller, so the latch can now
actually trip in production. Before that it was effectively unreachable and the
missing provenance/reset path was harmless.

These tests pin the provenance contract. The latch's TRIP behaviour is unchanged
and is not weakened anywhere here — this is about being able to see it and
deliberately clear it.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.kill_switch import (  # noqa: E402
    DEFAULT_SAFETY_CONFIG,
    SafetyConfigStore,
    is_entry_blocked,
)


class FakeCol:
    """Minimal async collection: find_one / update_one(upsert)."""

    def __init__(self) -> None:
        self.doc: Optional[Dict[str, Any]] = None

    async def find_one(self, query):
        return dict(self.doc) if self.doc else None

    async def update_one(self, query, update, upsert=False):
        if self.doc is None:
            self.doc = {"_id": query.get("_id")}
        self.doc.update(update.get("$set", {}))
        return None


def _store():
    return SafetyConfigStore(FakeCol())


def test_trip_records_the_reason_and_the_time():
    """A halted operator must be able to see WHY and WHEN, not just THAT."""
    s = _store()
    cfg = asyncio.run(s.trip(reason="account_daily_loss_limit"))
    assert cfg["blocked_until_reset"] is True
    assert cfg["latched_reason"] == "account_daily_loss_limit"
    assert cfg["latched_at"], "a latch with no timestamp cannot be triaged"
    # ISO-8601, UTC-aware
    assert "T" in str(cfg["latched_at"])


def test_get_config_surfaces_the_provenance():
    """The provenance is useless if get_config drops it (the _ALL_KEYS trap)."""
    s = _store()
    asyncio.run(s.trip(reason="whatever"))
    cfg = asyncio.run(s.get_config())
    assert cfg["latched_reason"] == "whatever"
    assert cfg["latched_at"]


def test_reset_clears_the_provenance_too():
    """A stale reason left behind would mislead the NEXT operator into thinking
    the desk is still halted for a reason that no longer applies."""
    s = _store()
    asyncio.run(s.trip(reason="account_daily_loss_limit"))
    cfg = asyncio.run(s.reset())
    assert cfg["blocked_until_reset"] is False
    assert not cfg.get("latched_reason")
    assert not cfg.get("latched_at")
    assert is_entry_blocked(cfg) is False


def test_trip_without_a_reason_still_works_and_is_honest():
    """Existing callers pass no reason. They must keep working, and the config
    must not pretend to know a reason it was never given."""
    s = _store()
    cfg = asyncio.run(s.trip())
    assert cfg["blocked_until_reset"] is True
    assert cfg["latched_at"], "the time is always knowable even when the reason isn't"
    assert cfg.get("latched_reason") in (None, "", "unspecified")


def test_put_config_still_refuses_to_write_the_latch_or_forge_its_provenance():
    """The latch stays controlled ONLY by trip()/reset(). Allowing an operator to
    write latched_reason via the ordinary config route would let a halt be
    relabelled after the fact."""
    s = _store()
    for forbidden in ("blocked_until_reset", "latched_reason", "latched_at"):
        try:
            asyncio.run(s.put_config({forbidden: "x"}))
        except ValueError:
            continue
        raise AssertionError(f"put_config must refuse {forbidden}")


def test_defaults_still_describe_an_unlatched_desk():
    assert DEFAULT_SAFETY_CONFIG["blocked_until_reset"] is False
    assert is_entry_blocked(DEFAULT_SAFETY_CONFIG) is False


def test_guardrail_tick_passes_the_reason_through_to_the_latch():
    """The engine trips the latch on a loss breach — the operator-facing reason
    must come from that event, not be invented by the UI."""
    import inspect
    from app.live import engine as eng
    src = inspect.getsource(eng.LiveEngine.guardrail_tick)
    # Assert the KEYWORD specifically. A bare `trip()` plus the word "reason"
    # appearing elsewhere in the method (it does — in the alerts payload) would
    # make this pass vacuously.
    assert "trip(reason=" in src, (
        "guardrail_tick must tell the store WHY it is latching, otherwise the "
        "banner can only say 'halted' with no cause")
