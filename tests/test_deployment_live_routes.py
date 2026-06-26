"""TDD tests for the deployment live control-surface routes (strategy-deploy-to-live).

Routes under test (in app/routers/deployments.py):
  POST /deployments/{id}/live/arm      — authorize live auto-placing (guarded)
  POST /deployments/{id}/live/disarm   — clear armed (no flatten)
  POST /deployments/{id}/live/stop     — flatten THIS deployment's live positions + disarm
  GET  /deployments/{id}/live/status   — armed state + caps + today + open positions
  POST /deployments/stop-all           — ALSO disarms + flattens every armed live deployment

Harness: the deployments router reaches Mongo through the module-global
``app.routers.deployments.get_db`` and helper functions imported into that module.
motor is importable host-side but no real Mongo runs, so we call the async handler
functions DIRECTLY with a FakeDB + monkeypatched module getters — the same
"patch the module-level seam" pattern the other live route tests use.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.deployments as dep  # noqa: E402
from app.live.live_position_guard import LiveMonitorRegistry  # noqa: E402
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo stand-in (cloned shape from test_auto_live)
# ---------------------------------------------------------------------------

class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)

    def sort(self, key, direction=-1):
        try:
            self._rows.sort(key=lambda r: r.get(key, 0), reverse=(direction == -1))
        except TypeError:
            pass
        return self

    def limit(self, n):
        self._rows = self._rows[: int(n)]
        return self

    async def to_list(self, length=None):
        return list(self._rows if length is None else self._rows[: int(length)])


def _get_dotted(row: Dict[str, Any], key: str) -> Any:
    """Resolve a (possibly dotted) Mongo query key against a nested doc."""
    cur: Any = row
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _match(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        rv = _get_dotted(row, k)
        if isinstance(v, dict) and "$in" in v:
            if rv not in v["$in"]:
                return False
        elif isinstance(v, dict) and "$ne" in v:
            if rv == v["$ne"]:
                return False
        elif rv != v:
            return False
    return True


class _Collection:
    def __init__(self, rows=None):
        self.rows: List[Dict[str, Any]] = list(rows or [])

    def find(self, query=None, projection=None):
        query = query or {}
        return _Cursor([dict(r) for r in self.rows if _match(r, query)])

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if _match(r, query):
                return dict(r)
        return None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if _match(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                return type("R", (), {"matched_count": 1})()
        return type("R", (), {"matched_count": 0})()

    async def update_many(self, query, update):
        n = 0
        for r in self.rows:
            if _match(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                n += 1
        return type("R", (), {"matched_count": n})()

    async def replace_one(self, query, doc, upsert=False):
        for i, r in enumerate(self.rows):
            if _match(r, query):
                self.rows[i] = dict(doc)
                return type("R", (), {"matched_count": 1})()
        return type("R", (), {"matched_count": 0})()


class FakeDB:
    def __init__(self):
        self.strategy_deployments = _Collection()
        self.live_trades = _Collection()
        self.paper_trades = _Collection()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _deployment(dep_id="dep-1", status="ACTIVE", **extra) -> Dict[str, Any]:
    d = {
        "id": dep_id,
        "name": f"{dep_id} live",
        "strategy_id": "confluence_scalper",
        "instrument": "NIFTY",
        "status": status,
        "risk": {"allow_overnight": False, "sizing": {"lots": 2}},
        "updated_at": "2026-06-25T00:00:00+00:00",
    }
    d.update(extra)
    return d


class _FakeEngine:
    def __init__(self, can=True):
        self._can = can

    async def can_trade(self):
        return (self._can, "ok" if self._can else "halted")


def _install(monkeypatch, db, *, connected=True, can_trade=True, registry=None,
             retired=False, autoplace=False, guard_armed=False):
    """Patch all module-level seams the routes touch."""
    monkeypatch.setattr(dep, "get_db", lambda: db)

    # broker token presence → connected
    async def _token_doc():
        from fastapi import HTTPException
        if connected:
            return {"jKey": "k", "uid": "U", "actid": "U"}
        raise HTTPException(400, "not connected")
    monkeypatch.setattr(dep, "_live_get_token_doc", _token_doc, raising=False)

    monkeypatch.setattr(dep, "_live_l3_engine", lambda: _FakeEngine(can_trade), raising=False)

    reg = registry if registry is not None else LiveMonitorRegistry()
    monkeypatch.setattr(dep, "_live_registry", lambda: reg, raising=False)

    squared: List[str] = []

    async def _fake_square(client, position, *, reason, **kw):
        squared.append(position.get("tsym"))
        return {"squared": True, "tsym": position.get("tsym"), "reason": reason}
    monkeypatch.setattr(dep, "_live_square_position", _fake_square, raising=False)

    async def _retired(sid):
        return retired
    # is_retired is imported lazily inside routes from strategies_admin
    import app.routers.strategies_admin as sa
    monkeypatch.setattr(sa, "is_retired", _retired)

    if autoplace:
        monkeypatch.setenv("LIVE_AUTOPLACE_ARMED", "1")
    else:
        monkeypatch.delenv("LIVE_AUTOPLACE_ARMED", raising=False)
    if guard_armed:
        monkeypatch.setenv("LIVE_GUARD_ARMED", "1")
    else:
        monkeypatch.delenv("LIVE_GUARD_ARMED", raising=False)

    # Pin 'now' to a deterministic MARKET-HOURS instant (11:30 IST = 06:00 UTC) so the
    # arm-window guard (reject after 15:00 IST) is not subject to the test wall clock.
    monkeypatch.setattr(dep, "_utcnow",
                        lambda: datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc), raising=False)

    return reg, squared


def _arm_body(confirm=True, lots=3, max_lots_per_day=20, max_concurrent=2, daily_loss_cap=5000.0):
    return dep._LiveArmBody(
        lots=lots, max_lots_per_day=max_lots_per_day, max_concurrent=max_concurrent,
        daily_loss_cap=daily_loss_cap, confirm=confirm,
    )


# ===========================================================================
# arm
# ===========================================================================

class TestArm:
    def test_arm_success_writes_risk_live_and_caps(self, monkeypatch):
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db)
        out = asyncio.run(dep.arm_deployment_live("dep-1", _arm_body()))
        assert out["armed"] is True
        assert out["lots"] == 3
        assert out["max_lots_per_day"] == 20
        assert out["max_concurrent"] == 2
        assert out["daily_loss_cap"] == 5000.0
        assert out["armed_until"]            # EOD IST cutoff present
        assert out["armed_by"] == "user"
        assert out["disarmed_reason"] is None
        assert out["autoplace_armed"] is False
        assert "note" in out                  # dry-run note when autoplace off
        # persisted, and OTHER risk.* keys preserved
        stored = db.strategy_deployments.rows[0]
        assert stored["risk"]["live"]["armed"] is True
        assert stored["risk"]["sizing"] == {"lots": 2}
        assert stored["risk"]["allow_overnight"] is False

    def test_arm_autoplace_on_has_no_dry_run_note(self, monkeypatch):
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, autoplace=True)
        out = asyncio.run(dep.arm_deployment_live("dep-1", _arm_body()))
        assert out["autoplace_armed"] is True
        assert out.get("note") in (None, "")

    def test_arm_requires_confirm_true(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.arm_deployment_live("dep-1", _arm_body(confirm=False)))
        assert ei.value.status_code == 400

    def test_arm_rejects_missing_deployment(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.arm_deployment_live("nope", _arm_body()))
        assert ei.value.status_code == 404

    def test_arm_rejects_non_active(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment(status="PAUSED"))
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.arm_deployment_live("dep-1", _arm_body()))
        assert ei.value.status_code == 400

    def test_arm_rejected_after_1500_ist_cutoff(self, monkeypatch):
        """Arming after 15:00 IST (the session is over) must be rejected, not silently
        write a born-expired arm. Nothing is persisted."""
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db)
        # Override the pinned clock to an EVENING instant: 18:30 IST = 13:00 UTC, past
        # today's 15:00 IST cutoff.
        monkeypatch.setattr(dep, "_utcnow",
                            lambda: datetime(2026, 6, 25, 13, 0, tzinfo=timezone.utc), raising=False)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.arm_deployment_live("dep-1", _arm_body()))
        assert ei.value.status_code == 400
        assert "15:00 IST" in str(ei.value.detail)
        # nothing was written — the deployment is NOT armed
        row = db.strategy_deployments.rows[0]
        assert not ((row.get("risk") or {}).get("live") or {}).get("armed")

    def test_arm_rejects_retired_strategy(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, retired=True)
        with pytest.raises(HTTPException):
            asyncio.run(dep.arm_deployment_live("dep-1", _arm_body()))

    def test_arm_rejects_drift_paused(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(
            _deployment(status="ACTIVE", drift_reason="strategy_source_drift"))
        _install(monkeypatch, db)
        with pytest.raises(HTTPException):
            asyncio.run(dep.arm_deployment_live("dep-1", _arm_body()))

    def test_arm_rejects_not_connected(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, connected=False)
        with pytest.raises(HTTPException):
            asyncio.run(dep.arm_deployment_live("dep-1", _arm_body()))

    def test_arm_rejects_engine_cannot_trade(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, can_trade=False)
        with pytest.raises(HTTPException):
            asyncio.run(dep.arm_deployment_live("dep-1", _arm_body()))


# ===========================================================================
# disarm
# ===========================================================================

class TestDisarm:
    def test_disarm_clears_armed_keeps_positions(self, monkeypatch):
        db = FakeDB()
        d = _deployment()
        d["risk"]["live"] = {"armed": True, "lots": 3}
        db.strategy_deployments.rows.append(d)
        reg = LiveMonitorRegistry()
        reg.register(key="o1", tsym="NIFTY25000CE", exch="NFO", qty=65, prd="I",
                     entry_price=100.0, state=build_monitor_state(100.0, stop_pct=50),
                     deployment_id="dep-1")
        _install(monkeypatch, db, registry=reg)
        out = asyncio.run(dep.disarm_deployment_live("dep-1"))
        assert out["armed"] is False
        assert out["disarmed_reason"] == "manual"
        # positions untouched
        assert len(reg) == 1
        assert db.strategy_deployments.rows[0]["risk"]["live"]["armed"] is False

    def test_disarm_missing_deployment_404(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.disarm_deployment_live("nope"))
        assert ei.value.status_code == 404


# ===========================================================================
# stop
# ===========================================================================

class TestStop:
    def test_stop_squares_only_this_deployments_positions_and_disarms(self, monkeypatch):
        db = FakeDB()
        d = _deployment()
        d["risk"]["live"] = {"armed": True, "lots": 3}
        db.strategy_deployments.rows.append(d)
        reg = LiveMonitorRegistry()
        reg.register(key="o1", tsym="NIFTY25000CE", exch="NFO", qty=65, prd="I",
                     entry_price=100.0, state=build_monitor_state(100.0, stop_pct=50),
                     deployment_id="dep-1")
        reg.register(key="o2", tsym="OTHER24000PE", exch="NFO", qty=65, prd="I",
                     entry_price=80.0, state=build_monitor_state(80.0, stop_pct=50),
                     deployment_id="dep-2")
        reg, squared = _install(monkeypatch, db, registry=reg)
        out = asyncio.run(dep.stop_deployment_live("dep-1"))
        assert squared == ["NIFTY25000CE"]              # ONLY this deployment
        assert out["disarmed"] is True
        assert "NIFTY25000CE" in out["squared_tsyms"]
        assert db.strategy_deployments.rows[0]["risk"]["live"]["armed"] is False
        assert db.strategy_deployments.rows[0]["risk"]["live"]["disarmed_reason"] == "manual_stop"
        # the other deployment's position is still registered
        assert reg.get("o2") is not None
        assert reg.get("o1") is None                     # this one removed after square

    def test_stop_closes_loop_journals_realized_pnl(self, monkeypatch):
        """A user stop of a deployment with an OPEN live_trades doc closes the
        loop: status→CLOSED + realized_pnl from the entry's last broker mark,
        linked by norenordno."""
        db = FakeDB()
        d = _deployment()
        d["risk"]["live"] = {"armed": True, "lots": 3}
        db.strategy_deployments.rows.append(d)
        # OPEN live_trades doc for this deployment, keyed by norenordno "o1".
        db.live_trades.rows.append({
            "id": "lt-1", "norenordno": "o1", "deployment_id": "dep-1",
            "trading_symbol": "NIFTY25000CE", "entry_price": 100.0, "quantity": 65,
            "status": "OPEN", "realized_pnl": None,
        })
        reg = LiveMonitorRegistry()
        reg.register(key="o1", tsym="NIFTY25000CE", exch="NFO", qty=65, prd="I",
                     entry_price=100.0, state=build_monitor_state(100.0, stop_pct=50),
                     source="auto_live", deployment_id="dep-1")
        reg.get("o1")["position"]["lp"] = 130.0   # last broker mark → exit estimate
        _install(monkeypatch, db, registry=reg)
        out = asyncio.run(dep.stop_deployment_live("dep-1"))
        assert "NIFTY25000CE" in out["squared_tsyms"]
        row = db.live_trades.rows[0]
        assert row["status"] == "CLOSED"
        assert row["exit_reason"] == "manual_stop"
        assert row["realized_pnl"] == (130.0 - 100.0) * 65   # +1950 long-only buy

    def test_stop_no_positions_still_disarms(self, monkeypatch):
        db = FakeDB()
        d = _deployment()
        d["risk"]["live"] = {"armed": True}
        db.strategy_deployments.rows.append(d)
        reg, squared = _install(monkeypatch, db)
        out = asyncio.run(dep.stop_deployment_live("dep-1"))
        assert squared == []
        assert out["disarmed"] is True
        assert out["squared_tsyms"] == []

    def test_stop_missing_deployment_404(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.stop_deployment_live("nope"))
        assert ei.value.status_code == 404


# ===========================================================================
# status
# ===========================================================================

class TestStatus:
    def test_status_reports_armed_caps_today_and_open_positions(self, monkeypatch):
        db = FakeDB()
        d = _deployment()
        d["risk"]["live"] = {
            "armed": True, "armed_until": "2099-01-01T09:30:00+00:00",
            "lots": 3, "max_lots_per_day": 20, "max_concurrent": 2, "daily_loss_cap": 5000.0,
        }
        db.strategy_deployments.rows.append(d)
        # today IST trades for this deployment
        today_ist = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT10:00:00+05:30")
        db.live_trades.rows.append({
            "deployment_id": "dep-1", "status": "CLOSED", "lots": 2,
            "realized_pnl": 1500.0, "entry_value": 13000.0,
            "created_at": today_ist, "closed_at": today_ist,
        })
        db.live_trades.rows.append({
            "deployment_id": "dep-1", "status": "OPEN", "lots": 1,
            "realized_pnl": None, "created_at": today_ist,
        })
        # an unrelated deployment's trade must NOT count
        db.live_trades.rows.append({
            "deployment_id": "dep-2", "status": "OPEN", "lots": 9, "created_at": today_ist})
        reg = LiveMonitorRegistry()
        reg.register(key="o1", tsym="NIFTY25000CE", exch="NFO", qty=65, prd="I",
                     entry_price=100.0, state=build_monitor_state(100.0, stop_pct=50),
                     deployment_id="dep-1")
        reg.register(key="o2", tsym="OTHER", exch="NFO", qty=65, prd="I",
                     entry_price=80.0, state=build_monitor_state(80.0, stop_pct=50),
                     deployment_id="dep-2")
        _install(monkeypatch, db, registry=reg, autoplace=True, guard_armed=True)
        out = asyncio.run(dep.deployment_live_status("dep-1"))
        assert out["armed"] is True
        assert out["armed_until"] == "2099-01-01T09:30:00+00:00"
        assert out["caps"]["lots"] == 3
        assert out["caps"]["max_lots_per_day"] == 20
        assert out["caps"]["max_concurrent"] == 2
        assert out["caps"]["daily_loss_cap"] == 5000.0
        assert out["today"]["orders"] == 2          # both this-deployment trades
        assert out["today"]["lots"] == 3            # 2 + 1
        assert out["today"]["realized_pnl"] == 1500.0
        # open positions filtered to this deployment only
        assert [p["tsym"] for p in out["open_positions"]] == ["NIFTY25000CE"]
        assert out["autoplace_armed"] is True
        assert out["guard_armed"] is True

    def test_status_not_armed_defaults(self, monkeypatch):
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())   # no risk.live
        _install(monkeypatch, db)
        out = asyncio.run(dep.deployment_live_status("dep-1"))
        assert out["armed"] is False
        assert out["open_positions"] == []
        assert out["today"]["orders"] == 0

    def test_status_missing_deployment_404(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.deployment_live_status("nope"))
        assert ei.value.status_code == 404


# ===========================================================================
# status — batched (?ids=)
# ===========================================================================

class TestStatusBatch:
    def _two_deployments(self):
        db = FakeDB()
        a = _deployment(dep_id="dep-1")
        a["risk"]["live"] = {"armed": True, "armed_until": "2099-01-01T09:30:00+00:00",
                             "lots": 3, "max_lots_per_day": 20, "max_concurrent": 2,
                             "daily_loss_cap": 5000.0}
        b = _deployment(dep_id="dep-2")  # no risk.live → unarmed defaults
        db.strategy_deployments.rows.extend([a, b])
        return db

    def test_batch_returns_payloads_keyed_by_id(self, monkeypatch):
        db = self._two_deployments()
        _install(monkeypatch, db)
        out = asyncio.run(dep.deployments_live_status_batch(ids="dep-1,dep-2"))
        assert set(out.keys()) == {"dep-1", "dep-2"}
        assert out["dep-1"]["armed"] is True
        assert out["dep-1"]["caps"]["lots"] == 3
        assert out["dep-2"]["armed"] is False

    def test_batch_omits_unknown_ids_without_failing(self, monkeypatch):
        db = self._two_deployments()
        _install(monkeypatch, db)
        out = asyncio.run(dep.deployments_live_status_batch(ids="dep-1,ghost"))
        assert "dep-1" in out
        assert "ghost" not in out          # unknown id omitted, batch still succeeds

    def test_batch_payload_byte_identical_to_per_id_route(self, monkeypatch):
        db = self._two_deployments()
        _install(monkeypatch, db)
        per_id = asyncio.run(dep.deployment_live_status("dep-1"))
        batched = asyncio.run(dep.deployments_live_status_batch(ids="dep-1"))
        assert batched["dep-1"] == per_id   # same shared helper → identical shape

    def test_batch_dedups_and_strips_whitespace(self, monkeypatch):
        db = self._two_deployments()
        _install(monkeypatch, db)
        out = asyncio.run(dep.deployments_live_status_batch(ids=" dep-1 , dep-1 ,"))
        assert list(out.keys()) == ["dep-1"]   # deduped, whitespace stripped, empty dropped

    def test_batch_empty_ids_returns_empty_map(self, monkeypatch):
        db = self._two_deployments()
        _install(monkeypatch, db)
        out = asyncio.run(dep.deployments_live_status_batch(ids=""))
        assert out == {}


# ===========================================================================
# stop-all (extended: paper stop-all + disarm/flatten armed live)
# ===========================================================================

class TestStopAll:
    def test_stop_all_disarms_and_flattens_armed_live(self, monkeypatch):
        db = FakeDB()
        armed = _deployment(dep_id="dep-1")
        armed["risk"]["live"] = {"armed": True, "lots": 3}
        not_armed = _deployment(dep_id="dep-2")          # ACTIVE, paper-only
        db.strategy_deployments.rows.extend([armed, not_armed])
        reg = LiveMonitorRegistry()
        reg.register(key="o1", tsym="NIFTY25000CE", exch="NFO", qty=65, prd="I",
                     entry_price=100.0, state=build_monitor_state(100.0, stop_pct=50),
                     deployment_id="dep-1")
        reg, squared = _install(monkeypatch, db, registry=reg)
        # neutralize the paper square-off + stream lookup (global, unrelated to live)
        async def _no_paper(db_, **kw):
            return []
        monkeypatch.setattr(dep, "square_off_open_paper_trades", _no_paper)

        class _Stream:
            def latest_tick_map(self):
                return {}
        monkeypatch.setattr(dep, "upstox_stream_manager", _Stream())

        out = asyncio.run(dep.stop_all_deployments())
        # paper behaviour preserved: both ACTIVE deployments paused
        assert set(out["paused_deployment_ids"]) == {"dep-1", "dep-2"}
        # live behaviour added: armed deployment disarmed + flattened
        assert "dep-1" in out["disarmed_live_deployment_ids"]
        assert squared == ["NIFTY25000CE"]
        assert db.strategy_deployments.rows[0]["risk"]["live"]["armed"] is False
        # the non-armed deployment is not in the disarmed-live list
        assert "dep-2" not in out["disarmed_live_deployment_ids"]


# ===========================================================================
# Contract: routes are pinned in the backend API source
# ===========================================================================

def test_backend_exposes_live_deploy_routes():
    from tests.contract_corpus import backend_api_text
    server = backend_api_text()
    for needle in (
        '@api.post("/deployments/{deployment_id}/live/arm")',
        '@api.post("/deployments/{deployment_id}/live/disarm")',
        '@api.post("/deployments/{deployment_id}/live/stop")',
        '@api.get("/deployments/{deployment_id}/live/status")',
        '@api.get("/deployments/live/status")',
    ):
        assert needle in server
