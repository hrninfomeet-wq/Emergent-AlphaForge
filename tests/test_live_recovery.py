"""Tests for item #5 — re-runnable live recovery (per-token latch).

The boot-time recovery is SKIPPED when the PC boots before the daily Flattrade
OAuth, so recovery must also fire when a token first appears and be retried by the
supervisor (a per-token latch runs it once per token). The original commit also
re-armed the 10-min manual auto-square timer here; that timer was removed (EOD
square + guard stop are the manual backstops), so those tests are gone with it.
Container tests (import motor-backed modules)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

def _run(c):
    return asyncio.run(c)


# --- maybe_run_live_recovery (per-token latch) -------------------------------

def test_maybe_run_recovery_skips_without_token():
    from app import runtime as rt
    rt._live_recovery_state.update({"succeeded": False, "token_fingerprint": None})
    with patch.object(rt, "_live_token_doc", AsyncMock(return_value=None)):
        res = _run(rt.maybe_run_live_recovery())
    assert res == {"ran": False, "reason": "no_token"}


def test_maybe_run_recovery_runs_once_per_token():
    from app import runtime as rt
    rt._live_recovery_state.update(
        {"succeeded": False, "token_fingerprint": None, "last_result": None})
    calls = []

    async def _stub():
        calls.append(1)
        return True                              # COMPLETE run

    with patch.object(rt, "_live_token_doc",
                      AsyncMock(return_value={"jKey": "TOKEN_ABC123", "uid": "U"})), \
         patch.object(rt, "live_startup_recovery", _stub):
        r1 = _run(rt.maybe_run_live_recovery())
        r2 = _run(rt.maybe_run_live_recovery())
    assert r1 == {"ran": True, "reason": "ok"}
    assert r2 == {"ran": False, "reason": "already_recovered"}
    assert len(calls) == 1                       # ran EXACTLY once for the token
    assert rt.live_recovery_status()["succeeded"] is True


def test_maybe_run_recovery_incomplete_does_not_latch_and_retries():
    """A run that HAPPENED but could not do its job (broker unreachable at boot:
    every step swallowed its failure) must NOT latch success — the supervisor
    keeps retrying until a COMPLETE run. This was the review's top finding: a
    latched incomplete run left an overnight position unguarded all day behind a
    green recovery-status strip."""
    from app import runtime as rt
    rt._live_recovery_state.update(
        {"succeeded": False, "token_fingerprint": None, "last_result": None})
    calls = []

    async def _incomplete():
        calls.append(1)
        return False                             # ran, but INCOMPLETE

    async def _complete():
        calls.append(1)
        return True

    with patch.object(rt, "_live_token_doc",
                      AsyncMock(return_value={"jKey": "TOKEN_ABC123", "uid": "U"})):
        with patch.object(rt, "live_startup_recovery", _incomplete):
            r1 = _run(rt.maybe_run_live_recovery())
            r2 = _run(rt.maybe_run_live_recovery())     # supervisor tick: RETRIES
        assert r1 == {"ran": True, "reason": "incomplete"}
        assert r2 == {"ran": True, "reason": "incomplete"}
        assert rt.live_recovery_status()["succeeded"] is False
        assert len(calls) == 2                          # not latched — kept retrying
        with patch.object(rt, "live_startup_recovery", _complete):
            r3 = _run(rt.maybe_run_live_recovery())     # broker back → completes
        assert r3 == {"ran": True, "reason": "ok"}
        assert rt.live_recovery_status()["succeeded"] is True


def test_maybe_run_recovery_reruns_on_new_token():
    from app import runtime as rt
    rt._live_recovery_state.update(
        {"succeeded": True, "token_fingerprint": "OLDTOKEN0000", "last_result": "ok"})
    calls = []

    async def _stub():
        calls.append(1)
        return True                              # COMPLETE run

    with patch.object(rt, "_live_token_doc",
                      AsyncMock(return_value={"jKey": "NEWTOKEN9999", "uid": "U"})), \
         patch.object(rt, "live_startup_recovery", _stub):
        res = _run(rt.maybe_run_live_recovery())
    assert res == {"ran": True, "reason": "ok"}   # a NEW daily token re-runs recovery
    assert len(calls) == 1


# --- guard-status surfaces the rehydrate source ------------------------------

def test_guard_status_surfaces_rehydrated_source():
    from app.routers import live_broker as lb

    class _Reg:
        def snapshot(self):
            return [
                {"tsym": "X", "qty": 65, "entry_price": 100.0, "seen_filled": True,
                 "source": "rehydrated", "state": {"stop_level": 50.0}},
                {"tsym": "Y", "qty": 30, "entry_price": 200.0, "seen_filled": True,
                 "source": None, "state": {"stop_level": 180.0}},
            ]

        def __len__(self):
            return 2

    with patch.object(lb, "_get_live_registry", lambda: _Reg()):
        out = _run(lb.guard_status())
    assert out["rehydrated_count"] == 1
    g = {r["tsym"]: r for r in out["guarded"]}
    assert g["X"]["source"] == "rehydrated"
    assert g["Y"]["source"] is None
