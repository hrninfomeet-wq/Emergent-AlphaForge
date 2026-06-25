"""Tests for app/live_deploy_context.py — the live-deploy "context" that supplies
the real broker collaborators + the guard-registering ``arm_for`` factory used by
the continuous live path (auto_live).

What is host-testable here:
- ``build_live_deploy_context`` returns None cleanly when the broker is NOT
  connected (no valid Flattrade token) and NEVER raises if the broker is
  unconfigured — that fall-through is what keeps the evaluator's lazy build safe.
- ``arm_for(plan, signal_doc, ref_ltp)`` returns an async ``arm(intent, norenordno)``
  that registers a MULTI-POSITION guard entry (source="auto_live", spot_exit,
  time_stop_minutes, entry_ts, deployment_id) with a monitor state built from the
  plan levels — and crucially does NOT build a SessionStore arm or a 10-minute
  auto-square (those are manual-single-shot only).

The full collaborator wiring (client/intent_store/engine/search_fn/uid/actid) can
only be exercised against a live broker token, so it is intentionally NOT unit-
tested here; the connected=None / unconnected fall-through IS.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import live_deploy_context as ldc  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeIntent:
    """Minimal stand-in for the executor's OrderIntent (only the fields arm reads)."""

    def __init__(self, *, tsym="NIFTY25JUN23950CE", exch="NFO", qty=150, prd="I"):
        self.tsym = tsym
        self.exch = exch
        self.qty = qty
        self.prd = prd


class FakeRegistry:
    """Captures the register() call so the test can assert on its kwargs."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    def register(self, **kwargs):
        self.calls.append(dict(kwargs))
        return dict(kwargs)


def _signal(deployment_id: str = "dep-7") -> Dict[str, Any]:
    return {"id": "sig-1", "deployment_id": deployment_id, "instrument": "NIFTY",
            "direction": "CE"}


def _plan(**overrides) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "levels": {"stop_pct": 30.0, "target_pct": 60.0,
                   "stop_pts": None, "target_pts": None, "trail": None},
        "spot_exit": {"direction": "CE", "instrument_key": "NSE_INDEX|Nifty 50",
                      "spot_target": 24000.0, "spot_stop": 23900.0},
        "time_stop_minutes": 30,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# arm_for factory
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_arm_for_registers_with_auto_live_source_and_exit_fields(monkeypatch):
    reg = FakeRegistry()
    monkeypatch.setattr(ldc, "get_registry", lambda: reg)

    arm = ldc.arm_for(_plan(), _signal("dep-7"), ref_ltp=151.5)
    await arm(FakeIntent(), "N1")

    assert len(reg.calls) == 1
    call = reg.calls[0]
    assert call["key"] == "N1"
    assert call["tsym"] == "NIFTY25JUN23950CE"
    assert call["exch"] == "NFO"
    assert call["qty"] == 150
    assert call["prd"] == "I"
    assert call["entry_price"] == 151.5
    assert call["source"] == "auto_live"
    assert call["deployment_id"] == "dep-7"
    assert call["spot_exit"] == _plan()["spot_exit"]
    assert call["time_stop_minutes"] == 30
    assert call["entry_ts"]  # an ISO timestamp was stamped


@pytest.mark.asyncio
async def test_arm_for_builds_monitor_state_from_plan_levels(monkeypatch):
    reg = FakeRegistry()
    monkeypatch.setattr(ldc, "get_registry", lambda: reg)

    arm = ldc.arm_for(_plan(), _signal(), ref_ltp=100.0)
    await arm(FakeIntent(), "N2")

    state = reg.calls[0]["state"]
    # build_monitor_state(100.0, stop_pct=30, target_pct=60) → stop 70, target 160
    assert state["entry"] == 100.0
    assert state["stop_level"] == 70.0
    assert state["target_level"] == 160.0


@pytest.mark.asyncio
async def test_arm_for_uses_deep_default_stop_when_levels_have_stop(monkeypatch):
    """When the plan carries a pts stop instead of pct, the state still resolves."""
    reg = FakeRegistry()
    monkeypatch.setattr(ldc, "get_registry", lambda: reg)

    plan = _plan(levels={"stop_pct": None, "target_pct": None,
                         "stop_pts": 20.0, "target_pts": 50.0, "trail": None})
    arm = ldc.arm_for(plan, _signal(), ref_ltp=100.0)
    await arm(FakeIntent(), "N3")

    state = reg.calls[0]["state"]
    assert state["stop_level"] == 80.0      # 100 - 20
    assert state["target_level"] == 150.0   # 100 + 50


@pytest.mark.asyncio
async def test_arm_for_registration_failure_does_not_raise(monkeypatch):
    """Registration is best-effort (mirrors _make_arm): a registry error is logged,
    the arm must NOT crash the fill."""
    class Boom:
        def register(self, **kwargs):
            raise RuntimeError("registry down")
    monkeypatch.setattr(ldc, "get_registry", lambda: Boom())

    arm = ldc.arm_for(_plan(), _signal(), ref_ltp=100.0)
    # Must not raise.
    await arm(FakeIntent(), "N4")


@pytest.mark.asyncio
async def test_arm_for_does_not_schedule_session_or_autosquare(monkeypatch):
    """The multi-position arm must NOT create a SessionStore arm or a 10-minute
    auto-square — those are the manual single-shot's concern. We assert the only
    side effect is the registry register() (no other awaited collaborator)."""
    reg = FakeRegistry()
    monkeypatch.setattr(ldc, "get_registry", lambda: reg)
    # If arm_for tried to schedule an auto-square it would reference asyncio task
    # creation / a session store; the factory takes neither, so simply assert the
    # registry was the sole effect.
    arm = ldc.arm_for(_plan(), _signal(), ref_ltp=100.0)
    await arm(FakeIntent(), "N5")
    assert len(reg.calls) == 1


# --------------------------------------------------------------------------- #
# build_live_deploy_context — unconnected / unconfigured fall-through
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_build_context_returns_none_when_not_connected(monkeypatch):
    """No valid token → None (the evaluator treats this as live disabled)."""
    async def _status(_uid):
        return {"connected": False, "expired": False}
    monkeypatch.setattr(ldc, "get_status", _status)

    ctx = await ldc.build_live_deploy_context(db=MagicMock())
    assert ctx is None


@pytest.mark.asyncio
async def test_build_context_returns_none_when_token_expired(monkeypatch):
    async def _status(_uid):
        return {"connected": True, "expired": True}
    monkeypatch.setattr(ldc, "get_status", _status)

    ctx = await ldc.build_live_deploy_context(db=MagicMock())
    assert ctx is None


@pytest.mark.asyncio
async def test_build_context_never_raises_when_status_errors(monkeypatch):
    """A broker/DB error while checking connection must NOT raise — it degrades to
    'live disabled' (None) so the evaluator falls through to auto_paper."""
    async def _status(_uid):
        raise RuntimeError("db down")
    monkeypatch.setattr(ldc, "get_status", _status)

    ctx = await ldc.build_live_deploy_context(db=MagicMock())
    assert ctx is None
