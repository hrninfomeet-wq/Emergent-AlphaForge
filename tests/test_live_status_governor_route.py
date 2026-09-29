"""The /live/status routes carry the governor's own verdict (`governor` key).

Additive only: every existing field keeps its shape. The account context (safety
config, all live_trades, broker session) is read ONCE per request, so a batch of
N deployments costs one live_trades read — not N, and not 2N.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.routers.deployments as dep  # noqa: E402
from app.live_deploy_governor import check_live_caps  # noqa: E402
from tests.test_deployment_live_routes import FakeDB, _deployment, _install  # noqa: E402


def _db_with(*deps):
    db = FakeDB()
    db.strategy_deployments.rows.extend(deps)
    return db


def _live(dep_id, **caps):
    d = _deployment(dep_id=dep_id, mode="live")
    d["risk"]["live"] = caps or {"lots": 3, "max_lots_per_day": 20,
                                 "max_concurrent": 2, "daily_loss_cap": 5000.0}
    return d


def test_the_payload_carries_the_governors_verdict(monkeypatch):
    d = _live("dep-1")
    db = _db_with(d)
    _install(monkeypatch, db)
    out = asyncio.run(dep.deployment_live_status("dep-1"))
    gov = out["governor"]
    assert gov["error"] is None
    assert gov["verdict"] == asyncio.run(check_live_caps(
        db, d, capped_lots=gov["next_entry_lots"]))
    assert gov["caps"]["daily_loss_cap"] == 5000.0


def test_existing_fields_keep_their_shape(monkeypatch):
    db = _db_with(_live("dep-1"))
    _install(monkeypatch, db)
    out = asyncio.run(dep.deployment_live_status("dep-1"))
    for key in ("armed", "live_mode", "live_paused", "armed_until", "caps", "today",
                "open_positions", "last_entry", "autoplace_armed", "guard_armed"):
        assert key in out, key


def test_a_batch_reads_live_trades_once(monkeypatch):
    db = _db_with(_live("dep-1"), _live("dep-2"), _live("dep-3"))
    _install(monkeypatch, db)
    calls = []
    orig = db.live_trades.find

    def counting(query=None, projection=None):
        calls.append(query)
        return orig(query, projection)
    db.live_trades.find = counting
    out = asyncio.run(dep.deployments_live_status_batch(ids="dep-1,dep-2,dep-3"))
    assert set(out) == {"dep-1", "dep-2", "dep-3"}
    assert len(calls) == 1, f"{len(calls)} live_trades reads for one batch"


def test_a_nan_cap_does_not_500_the_batch(monkeypatch):
    """The response is serialized with allow_nan=False; the raw `caps` echo used
    to carry the NaN straight into it."""
    db = _db_with(_live("dep-1", daily_loss_cap=float("nan"), max_concurrent=2))
    _install(monkeypatch, db)
    out = asyncio.run(dep.deployments_live_status_batch(ids="dep-1"))
    json.dumps(out, allow_nan=False)
    assert out["dep-1"]["caps"]["daily_loss_cap"] is None
    assert out["dep-1"]["governor"]["verdict"]["reason"] == "invalid_daily_loss_cap"


def test_an_expired_session_binds_at_authorization(monkeypatch):
    """`connected` is the entry path's own test: a token doc that exists but has
    expired is NOT connected — the very state the operator mistook for connected
    on 2026-09-26."""
    db = _db_with(_live("dep-1"))
    _install(monkeypatch, db, connected=True, broker_expired=True)
    gov = asyncio.run(dep.deployment_live_status("dep-1"))["governor"]
    assert gov["authorization"] == {"allow": False, "reason": "not_connected"}
    assert gov["binding"]["layer"] == "authorization"


def test_an_unreadable_broker_status_reads_as_not_connected(monkeypatch):
    db = _db_with(_live("dep-1"))
    _install(monkeypatch, db)

    async def _boom():
        raise RuntimeError("status store down")
    monkeypatch.setattr(dep, "_live_broker_status", _boom)
    gov = asyncio.run(dep.deployment_live_status("dep-1"))["governor"]
    assert gov["authorization"]["allow"] is False
