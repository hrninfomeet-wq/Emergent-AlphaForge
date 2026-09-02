"""The kill switch sets TWO gates; the operator has ONE reset. Pin that contract.

Incident (2026-09-02). Sequence: deploy live -> Kill Switch (Flatten Everything) ->
"reset the latch" -> try to enable again. The enable was refused with

    Live engine cannot trade (engine halted: kill_switch)
    - clear the halt/latch before going live.

even though the reset had succeeded. Verified against the running backend at the
time: `blocked_until_reset` was genuinely `false`, and the backend logs showed
`LiveEngine halted: reason='kill_switch'` followed by a 200 on the reset-latch
route. The reset was not broken -- it cleared a DIFFERENT gate than the one doing
the blocking.

`LiveEngine.can_trade()` reads two independent stops:

  1. `self.halted`  -- an in-memory bool on the module-level singleton. Set by
     `_halt()`. Before this changeset NOTHING assigned it False except
     `__init__`, so there was no reset path at all: the halt outlived the
     operator's reset and only a process restart cleared it.
  2. `blocked_until_reset` -- persisted in the SafetyConfigStore, cleared by
     `POST /live-broker/safety-config/reset-latch`.

The durability was also backwards. Only `kill_switch` and
`guardrail:broker_stop_loss` tripped the persisted latch; `reconcile_mismatch`,
`order_sm_flagged`, `om_for_unknown_order`, `post_place_protection_failed` and
`place_ack_lost:<cid>` were memory-only, so a restart silently un-halted an
engine that had stopped precisely BECAUSE broker and internal state disagreed --
leaving no record that it ever happened.

These tests pin both directions. Nothing here weakens a stop: the halt now
survives a restart (strictly more blocking than before) and is still clearable
only through the one explicit, audited operator reset.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.engine import LiveEngine  # noqa: E402
from app.live.idempotency import IntentStore  # noqa: E402
from app.live.mock_noren import MockNoren  # noqa: E402
from app.live.kill_switch import (  # noqa: E402
    DEFAULT_SAFETY_CONFIG,
    SafetyConfigStore,
    is_engine_halted,
    is_entry_blocked,
)


# ---------------------------------------------------------------------------
# Minimal async collection (mirrors tests/test_live_engine.py)
# ---------------------------------------------------------------------------

class _UpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class _FakeCursor:
    def __init__(self, docs: List[dict]) -> None:
        self._docs = docs

    async def to_list(self, length: Optional[int] = None) -> List[dict]:
        return list(self._docs)


def _matches(doc: dict, query: dict) -> bool:
    return all(doc.get(k) == v for k, v in query.items())


class FakeAsyncCollection:
    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if _matches(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if _matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                return _UpdateResult(1)
        if upsert:
            new_doc = dict(query)
            if "$set" in update:
                new_doc.update(update["$set"])
            self.docs.append(new_doc)
            return _UpdateResult(1)
        return _UpdateResult(0)

    def find(self, query, projection=None):
        return _FakeCursor([dict(d) for d in self.docs if _matches(d, query)])

    async def create_index(self, field, unique=False):
        return f"{field}_1"


def _engine_on(config_col: FakeAsyncCollection) -> LiveEngine:
    """Build a LiveEngine over a GIVEN config collection.

    Building a second engine over the same collection is how a process restart
    is simulated: the in-memory halt is gone, the persisted state is not.
    """
    return LiveEngine(
        client=MockNoren(),
        orders_collection=FakeAsyncCollection(),
        intent_store=IntentStore(FakeAsyncCollection()),
        config_store=SafetyConfigStore(config_col),
    )


async def _kill_switch(engine: LiveEngine, store: SafetyConfigStore) -> None:
    """Reproduce what POST /live-broker/kill-switch does to the two gates."""
    await store.trip(reason="kill_switch")     # persisted latch
    await engine.halt("kill_switch")           # engine halt


# ---------------------------------------------------------------------------
# THE INCIDENT: reset must clear the gate that is actually blocking
# ---------------------------------------------------------------------------

def test_operator_reset_after_kill_switch_actually_restores_can_trade():
    """deploy -> kill switch -> reset -> enable again. The reported incident.

    Before the fix this failed with "engine halted: kill_switch": the reset
    cleared the persisted latch while the in-memory halt stayed set forever.
    """
    async def go():
        config_col = FakeAsyncCollection()
        store = SafetyConfigStore(config_col)
        engine = _engine_on(config_col)

        await _kill_switch(engine, store)

        blocked, why = await engine.can_trade()
        assert blocked is False, "kill switch must block new entries"
        assert why, "a block must always name its reason"

        # The one operator action: POST /safety-config/reset-latch.
        await store.reset()
        await engine.resume()

        ok, reason = await engine.can_trade()
        assert ok is True, (
            f"after an explicit operator reset the desk must trade again, got: {reason}"
        )
        assert reason == ""

    asyncio.run(go())


def test_reset_clears_both_gates_and_their_provenance():
    """One operator concept -> one reset -> no stale state left behind."""
    async def go():
        config_col = FakeAsyncCollection()
        store = SafetyConfigStore(config_col)
        engine = _engine_on(config_col)

        await _kill_switch(engine, store)
        cfg = await store.get_config()
        assert is_entry_blocked(cfg) is True
        assert is_engine_halted(cfg) is True
        assert cfg["engine_halt_reason"] == "kill_switch"
        assert cfg["engine_halted_at"] is not None

        await store.reset()
        await engine.resume()

        cfg = await store.get_config()
        # A stale reason would tell the NEXT operator the desk is stopped for a
        # cause that no longer applies.
        assert is_entry_blocked(cfg) is False
        assert is_engine_halted(cfg) is False
        assert cfg["engine_halt_reason"] is None
        assert cfg["engine_halted_at"] is None
        assert cfg["latched_reason"] is None
        assert cfg["latched_at"] is None
        assert engine.halted is False
        assert engine.halt_reason is None

    asyncio.run(go())


# ---------------------------------------------------------------------------
# THE FAIL-OPEN HOLE: a halt must survive a restart
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "halt_reason",
    [
        "reconcile_mismatch",
        "order_sm_flagged",
        "om_for_unknown_order",
        "post_place_protection_failed",
        "place_ack_lost:C1",
        "kill_switch",
    ],
)
def test_every_halt_survives_a_process_restart(halt_reason):
    """A restart must NEVER be a way to clear a halt.

    These five non-kill_switch reasons were memory-only: the engine stopped
    because broker and internal state disagreed, and a container restart
    silently resumed trading with no record. That is the fail-OPEN direction.
    """
    async def go():
        config_col = FakeAsyncCollection()
        engine = _engine_on(config_col)
        await engine.halt(halt_reason)

        # Process restart: brand-new engine object, same persisted state.
        restarted = _engine_on(config_col)
        assert restarted.halted is False, "in-memory flag genuinely starts clean"

        ok, reason = await restarted.can_trade()
        assert ok is False, (
            f"halt {halt_reason!r} was silently cleared by a restart -- fail-open"
        )
        assert halt_reason in reason, (
            f"the surviving block must still name WHY, got: {reason}"
        )

    asyncio.run(go())


def test_restart_then_reset_is_the_way_back():
    """The exit after a restart is the same explicit reset, not another restart."""
    async def go():
        config_col = FakeAsyncCollection()
        store = SafetyConfigStore(config_col)
        await _engine_on(config_col).halt("reconcile_mismatch")

        restarted = _engine_on(config_col)
        ok, _ = await restarted.can_trade()
        assert ok is False

        await store.reset()
        await restarted.resume()

        ok, reason = await restarted.can_trade()
        assert ok is True, f"explicit reset must clear a persisted halt, got: {reason}"

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Nothing here loosened a stop
# ---------------------------------------------------------------------------

def test_halt_is_still_sticky_within_a_process():
    """A clean reconcile still does NOT un-halt. Only an explicit reset does."""
    async def go():
        config_col = FakeAsyncCollection()
        engine = _engine_on(config_col)
        await engine.halt("reconcile_mismatch")

        report = await engine.reconcile_tick()   # clean books from MockNoren
        assert report["ok"] is True
        assert engine.halted is True, "a clean tick must never auto-clear a halt"

        ok, _ = await engine.can_trade()
        assert ok is False

    asyncio.run(go())


def test_halt_keeps_the_first_reason_when_it_fires_twice():
    """Idempotent on the reason: the FIRST cause wins, the trail keeps both."""
    async def go():
        config_col = FakeAsyncCollection()
        store = SafetyConfigStore(config_col)
        engine = _engine_on(config_col)

        await engine.halt("reconcile_mismatch")
        await engine.halt("kill_switch")

        assert engine.halt_reason == "reconcile_mismatch"
        cfg = await store.get_config()
        assert cfg["engine_halt_reason"] == "reconcile_mismatch", (
            "the persisted reason must agree with the in-memory one"
        )
        assert len(engine.alerts) == 2, "the full incident trail is preserved"

    asyncio.run(go())


def test_put_config_cannot_write_the_engine_halt():
    """The halt gets the same protection as the latch: no back door via PUT."""
    async def go():
        store = SafetyConfigStore(FakeAsyncCollection())
        for key, value in (
            ("engine_halted", False),
            ("engine_halt_reason", None),
            ("engine_halted_at", None),
        ):
            with pytest.raises(ValueError):
                await store.put_config({key: value})

    asyncio.run(go())


def test_persisted_halt_blocks_even_with_the_latch_clear():
    """The two gates are independent; neither one masks the other."""
    async def go():
        config_col = FakeAsyncCollection()
        store = SafetyConfigStore(config_col)
        engine = _engine_on(config_col)

        await engine.halt("reconcile_mismatch")
        cfg = await store.get_config()
        assert is_entry_blocked(cfg) is False, "this reason never trips the latch"

        ok, reason = await engine.can_trade()
        assert ok is False
        assert "reconcile_mismatch" in reason

    asyncio.run(go())


def test_defaults_carry_the_halt_keys():
    """get_config must always return the halt keys so the UI can render them."""
    for key in ("engine_halted", "engine_halted_at", "engine_halt_reason"):
        assert key in DEFAULT_SAFETY_CONFIG
    assert DEFAULT_SAFETY_CONFIG["engine_halted"] is False
