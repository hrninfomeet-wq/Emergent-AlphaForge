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

import functools
import os
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import live_deploy_context as ldc  # noqa: E402
from app.live.oco_levels import compute_catastrophe_band  # noqa: E402


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
    """Captures the register() call so the test can assert on its kwargs, and
    stores the live entry under its key so a later ``get(key)`` returns the SAME
    mutable dict the OCO block stamps ``oco_al_id`` onto."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self._items: Dict[str, Dict[str, Any]] = {}

    def register(self, **kwargs):
        self.calls.append(dict(kwargs))
        item = dict(kwargs)            # the stored entry (a live reference)
        self._items[str(kwargs.get("key"))] = item
        return item

    def get(self, key):
        return self._items.get(str(key))


class FakeOcoClient:
    """Records place_oco intents; returns a scripted result."""

    def __init__(self, result: Dict[str, Any]):
        self._result = result
        self.oco_calls: List[Dict[str, Any]] = []

    async def place_oco(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        self.oco_calls.append(intent)
        return dict(self._result)


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
async def test_arm_for_registration_failure_raises_for_abort_protect(monkeypatch):
    """Registration is MANDATORY for a deployed position (no 10-min backstop): a
    registry failure must PROPAGATE so the executor's _abort_protect squares + halts
    — a deployed fill is never left live-and-unguarded."""
    class Boom:
        def register(self, **kwargs):
            raise RuntimeError("registry down")
    monkeypatch.setattr(ldc, "get_registry", lambda: Boom())

    arm = ldc.arm_for(_plan(), _signal(), ref_ltp=100.0)
    with pytest.raises(RuntimeError, match="registry down"):
        await arm(FakeIntent(), "N4")


@pytest.mark.asyncio
async def test_arm_for_spot_only_plan_still_registers_with_premium_floor(monkeypatch):
    """REGRESSION: a spot-only-stop plan (levels carry no premium stop/target/trail
    but spot_exit is set) MUST still register — build_monitor_state needs a premium
    input, so arm_for seeds the 50% catastrophe floor. Previously this silently
    failed to register (build_monitor_state raised, swallowed) → unguarded position."""
    reg = FakeRegistry()
    monkeypatch.setattr(ldc, "get_registry", lambda: reg)

    plan = _plan(levels={"stop_pct": None, "target_pct": None,
                         "stop_pts": None, "target_pts": None, "trail": None})
    arm = ldc.arm_for(plan, _signal("dep-9"), ref_ltp=100.0)
    await arm(FakeIntent(), "N6")

    assert len(reg.calls) == 1                       # registered, not silently dropped
    call = reg.calls[0]
    assert call["spot_exit"] is not None             # spot-mirror still carried (additive)
    # 50% catastrophe floor seeded → a valid monitor state with a downside stop.
    assert call["state"]["stop_level"] == 50.0       # 100 - 50%


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
# arm_for — resting broker OCO backstop (B3)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_arm_for_places_resting_oco_with_catastrophe_band(monkeypatch):
    """A deployed fill registers the guard AND places a resting broker OCO whose
    SL/TP triggers equal compute_catastrophe_band(ref_ltp, guard_stop_pct=<resolved>,
    stop_pct=<catastrophe_stop_pct>, target_pct=<catastrophe_target_pct>); the OCO
    legs are NRML (prd='M'); the registry entry records the broker al_id; _arm
    returns the al_id."""
    reg = FakeRegistry()
    monkeypatch.setattr(ldc, "get_registry", lambda: reg)
    client = FakeOcoClient({"ok": True, "al_id": "OCO1"})

    ref_ltp = 100.0
    # The resolved guard stop_pct is the plan's premium stop (default _plan() = 30.0).
    arm = functools.partial(ldc.arm_for, client=client, uid="UID1", actid="ACT1")(
        _plan(), _signal("dep-7"), ref_ltp=ref_ltp,
        catastrophe_stop_pct=48, catastrophe_target_pct=140,
    )
    # FakeIntent must be NRML so build_oco_intent (NRML-only) does not fail closed —
    # but arm_for hardcodes prd='M' on the OCO regardless of the entry intent's prd.
    oco_al_id = await arm(FakeIntent(prd="M"), "N1")

    # (a) place_oco was called once with the catastrophe-band triggers + prd='M'.
    assert len(client.oco_calls) == 1
    sent = client.oco_calls[0]
    band = compute_catastrophe_band(ref_ltp, guard_stop_pct=30.0, stop_pct=48, target_pct=140)
    assert band is not None
    sl_t, sl_l, tp_t, tp_l = band
    # The OCO carries the SL trigger as oivariable x and the TP trigger as y.
    oiv = {row["var_name"]: float(row["d"]) for row in sent["oivariable"]}
    assert oiv["x"] == pytest.approx(sl_t)
    assert oiv["y"] == pytest.approx(tp_t)
    # Both legs are NRML SELL legs.
    assert sent["place_order_params"]["prd"] == "M"
    assert sent["place_order_params_leg2"]["prd"] == "M"
    assert sent["place_order_params"]["trantype"] == "S"

    # (b) the registry entry now records the broker OCO al_id.
    ent = reg.get("N1")
    assert ent is not None
    assert ent["oco_al_id"] == "OCO1"

    # (c) _arm returned the al_id.
    assert oco_al_id == "OCO1"


@pytest.mark.asyncio
async def test_arm_for_points_stop_oco_sl_strictly_below_guard(monkeypatch):
    """BLOCKER REGRESSION: a deployment with a POINTS stop (stop_pts=70, no
    stop_pct) at ref_ltp=100 has a guard stop LEVEL of 30.0. _arm must derive the
    catastrophe band from that RESOLVED absolute level (guard_stop_pct=70), NOT
    from the None pct that previously collapsed to the 50% default — so the placed
    OCO SL trigger lands STRICTLY BELOW 30.0 and can never fire before the software
    guard. With the old code the band yielded an SL trigger of 50.0 (>= 30.0),
    inverting the design."""
    reg = FakeRegistry()
    monkeypatch.setattr(ldc, "get_registry", lambda: reg)
    client = FakeOcoClient({"ok": True, "al_id": "OCO_PTS"})

    ref_ltp = 100.0
    plan = _plan(levels={"stop_pct": None, "target_pct": None,
                         "stop_pts": 70.0, "target_pts": None, "trail": None})
    arm = functools.partial(ldc.arm_for, client=client, uid="U", actid="A")(
        plan, _signal("dep-pts"), ref_ltp=ref_ltp,
    )
    oco_al_id = await arm(FakeIntent(prd="M"), "NPTS")

    # The guard's resolved absolute stop level is 30.0 (100 - 70 pts).
    guard_stop_level = 30.0
    # registration captured that resolved level.
    assert reg.calls[0]["state"]["stop_level"] == pytest.approx(guard_stop_level)

    # The placed OCO SL trigger must be STRICTLY BELOW the guard stop level.
    assert len(client.oco_calls) == 1
    sent = client.oco_calls[0]
    oiv = {row["var_name"]: float(row["d"]) for row in sent["oivariable"]}
    sl_trigger = oiv["x"]
    assert sl_trigger < guard_stop_level, (
        f"pts-stop OCO SL trigger {sl_trigger} NOT strictly below guard "
        f"stop level {guard_stop_level} — broker OCO would fire before the guard"
    )
    assert oco_al_id == "OCO_PTS"


@pytest.mark.asyncio
async def test_arm_for_oco_reject_leaves_no_backstop_but_never_raises(monkeypatch):
    """place_oco → {"ok": False} → registry entry oco_al_id stays None, _arm returns
    None, and NO exception is raised (a transient OCO reject must NEVER unwind an
    already-filled+guarded entry)."""
    reg = FakeRegistry()
    monkeypatch.setattr(ldc, "get_registry", lambda: reg)
    client = FakeOcoClient({"ok": False, "al_id": None})

    arm = functools.partial(ldc.arm_for, client=client, uid="U", actid="A")(
        _plan(), _signal(), ref_ltp=100.0, catastrophe_stop_pct=48,
    )
    oco_al_id = await arm(FakeIntent(prd="M"), "N1")

    assert oco_al_id is None
    ent = reg.get("N1")
    assert ent is not None                          # entry still registered
    assert ent.get("oco_al_id") is None             # but no broker backstop


@pytest.mark.asyncio
async def test_arm_for_oco_exception_never_unwinds_filled_entry(monkeypatch):
    """If place_oco RAISES, the OCO block swallows it: _arm returns None and the
    entry stays registered — the fill is never unwound."""
    reg = FakeRegistry()
    monkeypatch.setattr(ldc, "get_registry", lambda: reg)

    class BoomOco:
        async def place_oco(self, intent):
            raise RuntimeError("broker OCO endpoint down")

    arm = functools.partial(ldc.arm_for, client=BoomOco(), uid="U", actid="A")(
        _plan(), _signal(), ref_ltp=100.0,
    )
    oco_al_id = await arm(FakeIntent(prd="M"), "N1")

    assert oco_al_id is None
    assert reg.get("N1") is not None
    assert reg.get("N1").get("oco_al_id") is None


@pytest.mark.asyncio
async def test_arm_for_no_client_skips_oco_and_returns_none(monkeypatch):
    """With no client bound (the manual / context-less default), no OCO is placed,
    _arm returns None, and the guard registration still happens."""
    reg = FakeRegistry()
    monkeypatch.setattr(ldc, "get_registry", lambda: reg)

    arm = ldc.arm_for(_plan(), _signal(), ref_ltp=100.0)   # client defaults to None
    oco_al_id = await arm(FakeIntent(prd="M"), "N1")

    assert oco_al_id is None
    assert len(reg.calls) == 1                      # still registered
    assert reg.get("N1").get("oco_al_id") is None


@pytest.mark.asyncio
async def test_arm_for_register_failure_still_propagates_with_client(monkeypatch):
    """The MANDATORY register() must still propagate even when a client is bound for
    the OCO — the OCO best-effort try/except sits AFTER register, so a register
    failure reaches the executor's _abort_protect, and NO OCO is placed."""
    class Boom:
        def register(self, **kwargs):
            raise RuntimeError("registry down")
    monkeypatch.setattr(ldc, "get_registry", lambda: Boom())
    client = FakeOcoClient({"ok": True, "al_id": "OCO1"})

    arm = functools.partial(ldc.arm_for, client=client, uid="U", actid="A")(
        _plan(), _signal(), ref_ltp=100.0, catastrophe_stop_pct=48,
    )
    with pytest.raises(RuntimeError, match="registry down"):
        await arm(FakeIntent(prd="M"), "N1")
    assert client.oco_calls == []                   # OCO never reached


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


# --------------------------------------------------------------------------- #
# build_live_deploy_context — the account ceiling is FAIL-CLOSED
# --------------------------------------------------------------------------- #

class _FakeConfigStore:
    def __init__(self, cfg=None, exc=None):
        self._cfg = cfg
        self._exc = exc

    async def get_config(self):
        if self._exc is not None:
            raise self._exc
        return dict(self._cfg or {})


def _wire_connected_broker(monkeypatch, config_store):
    """Patch the status probe + every live_broker collaborator getter so the
    build reaches the safety-config read using fakes (no real broker/token)."""
    from app.routers import live_broker as lb

    async def _status(_uid):
        return {"connected": True, "expired": False}

    async def _client():
        return MagicMock()

    async def _token_doc():
        return {"uid": "u1", "actid": "a1"}

    async def _search_fn(_client):
        return lambda exch, query: []

    monkeypatch.setattr(ldc, "get_status", _status)
    monkeypatch.setattr(lb, "_get_client", _client)
    monkeypatch.setattr(lb, "_get_token_doc", _token_doc)
    monkeypatch.setattr(lb, "_intent_store", lambda: MagicMock())
    monkeypatch.setattr(lb, "_l3_engine", lambda: MagicMock())
    monkeypatch.setattr(lb, "_config_store", lambda: config_store)
    monkeypatch.setattr(ldc, "_build_search_fn", _search_fn)


@pytest.mark.asyncio
async def test_build_context_reads_account_max_from_safety_config(monkeypatch):
    """Control: with a readable safety config the context builds and carries the
    configured ceiling (proves the fail-closed tests below fail for the right
    reason, not because the wired happy path is broken)."""
    _wire_connected_broker(monkeypatch, _FakeConfigStore(cfg={"max_lots_per_order": 3}))
    ctx = await ldc.build_live_deploy_context(db=MagicMock())
    assert ctx is not None
    assert ctx["account_max"] == 3


@pytest.mark.asyncio
async def test_build_context_fails_closed_when_safety_config_unreadable(monkeypatch):
    """Release-audit finding H3: an unreadable safety config must DISABLE live
    for this cadence (None → evaluator falls through to paper), never default to
    a guessed 20-lot ceiling."""
    _wire_connected_broker(monkeypatch, _FakeConfigStore(exc=RuntimeError("mongo down")))
    ctx = await ldc.build_live_deploy_context(db=MagicMock())
    assert ctx is None


@pytest.mark.asyncio
async def test_build_context_fails_closed_on_invalid_ceiling(monkeypatch):
    """A present-but-nonsensical ceiling (0 / None) is misconfiguration: refuse
    rather than trade on a guessed limit."""
    _wire_connected_broker(monkeypatch, _FakeConfigStore(cfg={"max_lots_per_order": 0}))
    ctx = await ldc.build_live_deploy_context(db=MagicMock())
    assert ctx is None
