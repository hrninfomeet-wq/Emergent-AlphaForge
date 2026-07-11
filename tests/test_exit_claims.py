"""Unit tests for the per-tsym exit-claim registry (host — no motor)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.exit_claims import ExitClaimRegistry, claim_exit, registry, reset_exit_claims  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_second_claim_on_same_tsym_is_blocked():
    async def _t():
        reg = ExitClaimRegistry(ttl_seconds=100.0, clock=lambda: 0.0)
        assert await reg.claim("X", "a") is True
        assert await reg.claim("X", "b") is False    # held
        await reg.release("X", "a")
        assert await reg.claim("X", "c") is True      # freed
    run(_t())


def test_release_requires_matching_token():
    async def _t():
        reg = ExitClaimRegistry(ttl_seconds=100.0, clock=lambda: 0.0)
        await reg.claim("X", "owner")
        await reg.release("X", "not-owner")           # wrong token → no-op
        assert await reg.claim("X", "b") is False      # still held by owner
    run(_t())


def test_ttl_expiry_frees_a_dead_holder():
    async def _t():
        clock = {"t": 0.0}
        reg = ExitClaimRegistry(ttl_seconds=30.0, clock=lambda: clock["t"])
        assert await reg.claim("X", "dead") is True
        clock["t"] = 31.0                              # past TTL
        assert await reg.claim("X", "new") is True     # expired holder is reclaimable
        # the dead holder's late release must NOT delete the new claim
        await reg.release("X", "dead")
        assert await reg.claim("X", "z") is False       # new still holds it
    run(_t())


def test_context_manager_serializes_and_releases():
    async def _t():
        reset_exit_claims()
        async with claim_exit("Y", "first") as g1:
            assert g1 is True
            async with claim_exit("Y", "second") as g2:
                assert g2 is False                     # busy while first holds it
        async with claim_exit("Y", "third") as g3:
            assert g3 is True                          # released after the block
        reset_exit_claims()
    run(_t())


def test_empty_tsym_is_never_serialized():
    async def _t():
        async with claim_exit("", "x") as g:
            assert g is True                           # nothing to serialize on
    run(_t())


def test_module_singleton_shared():
    assert registry() is registry()


def test_claim_ttl_override_outlives_default():
    """A per-claim ttl_seconds override (the kill switch passes ~900s) must keep
    blocking after the registry default would have expired (review fix: an
    expired kill claim mid-flatten reopens the double-sell race)."""
    t = {"now": 0.0}
    reg = ExitClaimRegistry(ttl_seconds=10.0, clock=lambda: t["now"])
    assert asyncio.run(reg.claim("TSYM", "kill_token", ttl_seconds=100.0)) is True
    t["now"] = 50.0                                # past the 10s default
    assert asyncio.run(reg.claim("TSYM", "guard_token")) is False   # still held
    t["now"] = 150.0                               # past the 100s override
    assert asyncio.run(reg.claim("TSYM", "guard_token")) is True    # expired now
