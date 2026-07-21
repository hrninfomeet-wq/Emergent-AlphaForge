"""TDD tests for the deployment live control-surface routes (strategy-deploy-to-live).

Routes under test (in app/routers/deployments.py):
  POST /deployments/{id}/live/enable   — switch deployment to mode="live" (guarded preflight)
  POST /deployments/{id}/live/disable  — switch deployment back to mode="paper" (no flatten)
  POST /deployments/{id}/live/stop     — flatten THIS deployment's live positions, mode="paper", status="PAUSED"
  GET  /deployments/{id}/live/status   — live state + caps + today + open positions
  POST /deployments/stop-all           — ALSO flattens + de-lives every mode="live" deployment

The per-deployment ARM ceremony (armed/armed_at/armed_until/armed_by/disarmed_reason,
the LIVE_GUARD_ARMED env gate, and the "cannot arm after 15:00 IST" window check) was
REMOVED by explicit user decision. `mode` ("live" vs "paper") is now the SOLE
authorization signal; risk.live is a pure config sub-doc (caps + catastrophe band).

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

    async def find_one(self, query, projection=None, sort=None):
        matches = [r for r in self.rows if _match(r, query)]
        if sort:
            for key, direction in reversed(list(sort)):
                try:
                    matches.sort(key=lambda r: r.get(key, ""), reverse=(direction == -1))
                except TypeError:
                    pass
        return dict(matches[0]) if matches else None

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
        self.signals = _Collection()


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
             retired=False, autoplace=False, guard_armed=False,
             promotion_allowed=True, account_max_lots=20,
             account_max_open=5, broker_expired=False, static_ip="1.2.3.4"):
    """Patch all module-level seams the routes touch."""
    monkeypatch.setattr(dep, "get_db", lambda: db)

    # broker token presence → connected
    async def _token_doc():
        from fastapi import HTTPException
        if connected:
            return {"jKey": "k", "uid": "U", "actid": "U"}
        raise HTTPException(400, "not connected")
    monkeypatch.setattr(dep, "_live_get_token_doc", _token_doc, raising=False)

    async def _broker_status():
        return {
            "configured": True,
            "connected": connected,
            "expired": broker_expired,
            "regenerate_after_6am": broker_expired,
            "static_ip_primary": static_ip,
            "static_ip_secondary": "",
        }
    monkeypatch.setattr(dep, "_live_broker_status", _broker_status, raising=False)

    monkeypatch.setattr(dep, "_live_l3_engine", lambda: _FakeEngine(can_trade), raising=False)

    async def _safety_config():
        return {
            "max_lots_per_order": account_max_lots,
            "max_open_positions": account_max_open,
            "daily_loss_limit": 5000,
        }
    monkeypatch.setattr(dep, "_live_safety_config", _safety_config, raising=False)

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

    # Pin 'now' to a deterministic instant. Enabling live is no longer session-scoped
    # (the old "reject after 15:00 IST" arm-window check was removed), but `enabled_at`
    # is still stamped from `_utcnow()`, so keep the clock deterministic for that.
    monkeypatch.setattr(dep, "_utcnow",
                        lambda: datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc), raising=False)

    async def _forward(_db, _deployment):
        return {
            "trade_count": 120, "total_pnl": 10_000,
            "session_completeness": {"complete_session_count": 60},
            "library_gate": {"min_complete_sessions": 10},
            "forward_validation": {
                "promotion_allowed": promotion_allowed,
                "phase": "promotion_ready" if promotion_allowed else "collecting",
                "failed_checks": [] if promotion_allowed else ["forward_sessions"],
            },
        }
    monkeypatch.setattr(dep, "compute_forward_metrics_for_deployment", _forward)

    return reg, squared


def _enable_body(confirm=True, lots=1, max_lots_per_day=1, max_concurrent=1, daily_loss_cap=4000.0,
                  catastrophe_stop_pct=None, catastrophe_target_pct=None,
                  accept_unvalidated_live=False):
    return dep._LiveEnableBody(
        lots=lots, max_lots_per_day=max_lots_per_day, max_concurrent=max_concurrent,
        daily_loss_cap=daily_loss_cap, confirm=confirm,
        catastrophe_stop_pct=catastrophe_stop_pct, catastrophe_target_pct=catastrophe_target_pct,
        accept_unvalidated_live=accept_unvalidated_live,
    )


# ===========================================================================
# enable  (was: arm)
# ===========================================================================

class TestEnable:
    def test_enable_success_sets_mode_live_and_writes_caps(self, monkeypatch):
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db)
        out = asyncio.run(dep.enable_deployment_live("dep-1", _enable_body()))
        assert out["lots"] == 1
        assert out["max_lots_per_day"] == 1
        assert out["max_concurrent"] == 1
        assert out["daily_loss_cap"] == 4000.0
        assert out["enabled_by"] == "user"
        assert out["last_block_reason"] is None
        assert out["autoplace_armed"] is False
        assert "note" in out                  # dry-run note when autoplace off
        # persisted: `mode` is now the SOLE authorization signal (no risk.live.armed
        # flag anymore), and OTHER risk.* keys are preserved untouched.
        stored = db.strategy_deployments.rows[0]
        assert stored["mode"] == "live"
        assert stored["risk"]["live"]["lots"] == 1
        assert stored["risk"]["sizing"] == {"lots": 2}
        assert stored["risk"]["allow_overnight"] is False
        assert stored["risk"]["live"]["evidence_consent"]["status"] == "forward_validated"
        assert stored["risk"]["live"]["account_safety_snapshot"]["max_lots_per_order"] == 20
        assert out["evidence_override_used"] is False

    def test_enable_persists_catastrophe_band_config(self, monkeypatch):
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db)
        asyncio.run(dep.enable_deployment_live(
            "dep-1", _enable_body(catastrophe_stop_pct=48, catastrophe_target_pct=140)))
        stored = db.strategy_deployments.rows[0]
        assert stored["risk"]["live"]["catastrophe_stop_pct"] == 48
        assert stored["risk"]["live"]["catastrophe_target_pct"] == 140

    def test_enable_without_catastrophe_band_persists_none(self, monkeypatch):
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db)
        asyncio.run(dep.enable_deployment_live("dep-1", _enable_body()))
        stored = db.strategy_deployments.rows[0]
        assert stored["risk"]["live"]["catastrophe_stop_pct"] is None
        assert stored["risk"]["live"]["catastrophe_target_pct"] is None

    @pytest.mark.parametrize(
        "overrides",
        [
            {"catastrophe_stop_pct": 0},
            {"catastrophe_target_pct": -1},
            {"daily_loss_cap": float("nan")},
        ],
    )
    def test_enable_rejects_non_positive_or_non_finite_risk_values(
        self, monkeypatch, overrides,
    ):
        from fastapi import HTTPException

        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.enable_deployment_live(
                "dep-1", _enable_body(**overrides)))
        assert ei.value.status_code == 400
        assert db.strategy_deployments.rows[0].get("mode") != "live"

    def test_enable_autoplace_on_has_no_dry_run_note(self, monkeypatch):
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, autoplace=True)
        out = asyncio.run(dep.enable_deployment_live("dep-1", _enable_body()))
        assert out["autoplace_armed"] is True
        assert out.get("note") in (None, "")

    def test_enable_requires_confirm_true(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.enable_deployment_live("dep-1", _enable_body(confirm=False)))
        assert ei.value.status_code == 400

    def test_enable_rejects_missing_deployment(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.enable_deployment_live("nope", _enable_body()))
        assert ei.value.status_code == 404

    def test_enable_rejects_non_active(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment(status="PAUSED"))
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.enable_deployment_live("dep-1", _enable_body()))
        assert ei.value.status_code == 400

    def test_enable_not_session_scoped_evening_still_succeeds(self, monkeypatch):
        """The old "cannot arm after 15:00 IST" rejection is GONE — enabling live
        is a one-time act, not scoped to the current session. An evening instant
        (18:30 IST = 13:00 UTC) must succeed exactly like a market-hours instant."""
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db)
        monkeypatch.setattr(dep, "_utcnow",
                            lambda: datetime(2026, 6, 25, 13, 0, tzinfo=timezone.utc), raising=False)
        out = asyncio.run(dep.enable_deployment_live("dep-1", _enable_body()))
        assert out["lots"] == 1
        assert db.strategy_deployments.rows[0]["mode"] == "live"

    def test_enable_rejects_retired_strategy(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, retired=True)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.enable_deployment_live("dep-1", _enable_body()))
        assert ei.value.status_code == 409

    def test_enable_rejects_drift_paused(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(
            _deployment(status="ACTIVE", drift_reason="strategy_source_drift"))
        _install(monkeypatch, db)
        with pytest.raises(HTTPException):
            asyncio.run(dep.enable_deployment_live("dep-1", _enable_body()))

    def test_enable_rejects_not_connected(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, connected=False)
        with pytest.raises(HTTPException):
            asyncio.run(dep.enable_deployment_live("dep-1", _enable_body()))

    @pytest.mark.parametrize(
        ("install_overrides", "message"),
        [
            ({"broker_expired": True}, "daily session is expired"),
            ({"static_ip": ""}, "static IP is not configured"),
        ],
    )
    def test_enable_rejects_invalid_broker_operational_readiness(
        self, monkeypatch, install_overrides, message,
    ):
        from fastapi import HTTPException

        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, **install_overrides)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.enable_deployment_live("dep-1", _enable_body()))
        assert ei.value.status_code == 400
        assert message in str(ei.value.detail)
        assert db.strategy_deployments.rows[0].get("mode") != "live"

    def test_enable_rejects_engine_cannot_trade(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, can_trade=False)
        with pytest.raises(HTTPException):
            asyncio.run(dep.enable_deployment_live("dep-1", _enable_body()))

    @pytest.mark.parametrize("field", ["lots", "max_lots_per_day", "max_concurrent"])
    def test_enable_rejects_cap_below_one(self, monkeypatch, field):
        """A live deployment without caps would sail past `_live_caps_configured`'s
        allow-all fast path and trade unbounded — lots / max_lots_per_day /
        max_concurrent are each individually required to be >= 1."""
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db)
        kwargs = {"lots": 1, "max_lots_per_day": 1, "max_concurrent": 1, field: 0}
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.enable_deployment_live("dep-1", _enable_body(**kwargs)))
        assert ei.value.status_code == 400
        # nothing was written — the deployment did NOT go live
        row = db.strategy_deployments.rows[0]
        assert row.get("mode") != "live"

    def test_enable_requires_explicit_consent_without_forward_promotion(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, promotion_allowed=False)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.enable_deployment_live("dep-1", _enable_body()))
        assert ei.value.status_code == 409
        assert ei.value.detail["code"] == "explicit_unvalidated_live_consent_required"
        assert ei.value.detail["consent_field"] == "accept_unvalidated_live"
        assert db.strategy_deployments.rows[0].get("mode") != "live"

    def test_enable_allows_unvalidated_candidate_after_explicit_consent(self, monkeypatch):
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, promotion_allowed=False)

        out = asyncio.run(dep.enable_deployment_live(
            "dep-1",
            _enable_body(
                lots=2,
                max_lots_per_day=5,
                max_concurrent=2,
                daily_loss_cap=5000,
                accept_unvalidated_live=True,
            ),
        ))

        stored_live = db.strategy_deployments.rows[0]["risk"]["live"]
        assert db.strategy_deployments.rows[0]["mode"] == "live"
        assert stored_live["lots"] == 2
        assert stored_live["max_lots_per_day"] == 5
        assert stored_live["max_concurrent"] == 2
        assert stored_live["daily_loss_cap"] == 5000.0
        assert stored_live["evidence_consent"]["status"] == "user_override"
        assert stored_live["evidence_consent"]["accepted"] is True
        assert stored_live["evidence_consent"]["failed_checks"] == ["forward_sessions"]
        assert out["evidence_override_used"] is True

    @pytest.mark.parametrize(
        ("body_overrides", "install_overrides", "code"),
        [
            ({"lots": 3}, {"account_max_lots": 2}, "account_lot_ceiling_exceeded"),
            ({"max_concurrent": 3}, {"account_max_open": 2}, "account_position_ceiling_exceeded"),
        ],
    )
    def test_enable_keeps_account_capital_ceilings_hard(
        self, monkeypatch, body_overrides, install_overrides, code,
    ):
        from fastapi import HTTPException

        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db, **install_overrides)

        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.enable_deployment_live(
                "dep-1", _enable_body(**body_overrides)))

        assert ei.value.status_code == 400
        assert ei.value.detail["code"] == code
        assert db.strategy_deployments.rows[0].get("mode") != "live"


# ===========================================================================
# disable  (was: disarm)
# ===========================================================================

class TestDisable:
    def test_disable_reverts_to_paper_keeps_positions(self, monkeypatch):
        db = FakeDB()
        d = _deployment(mode="live")
        d["risk"]["live"] = {"lots": 3}
        db.strategy_deployments.rows.append(d)
        reg = LiveMonitorRegistry()
        reg.register(key="o1", tsym="NIFTY25000CE", exch="NFO", qty=65, prd="I",
                     entry_price=100.0, state=build_monitor_state(100.0, stop_pct=50),
                     deployment_id="dep-1")
        _install(monkeypatch, db, registry=reg)
        out = asyncio.run(dep.disable_deployment_live("dep-1"))
        assert out["mode"] == "paper"
        assert out["deployment_id"] == "dep-1"
        assert out["live"]["last_block_reason"] == "manual_disable"
        # positions untouched — disable does NOT flatten (use /live/stop for that)
        assert len(reg) == 1
        assert db.strategy_deployments.rows[0]["mode"] == "paper"
        # live CONFIG (caps) is retained so re-enabling doesn't require re-entry
        assert db.strategy_deployments.rows[0]["risk"]["live"]["lots"] == 3

    def test_disable_missing_deployment_404(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.disable_deployment_live("nope"))
        assert ei.value.status_code == 404


# ===========================================================================
# stop
# ===========================================================================

class TestStop:
    def test_stop_squares_only_this_deployments_positions_disables_and_pauses(self, monkeypatch):
        db = FakeDB()
        d = _deployment(mode="live")
        d["risk"]["live"] = {"lots": 3}
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
        assert out["disabled"] is True
        assert out["paused"] is True
        assert out["squared_tsyms"] == []              # submission is not a fill
        assert out["exit_submitted_tsyms"] == ["NIFTY25000CE"]
        assert out["flat_confirmation_pending_tsyms"] == ["NIFTY25000CE"]
        # mode reverts to paper AND status is authoritatively PAUSED — an ACTIVE
        # live deployment whose positions were just squared must not re-enter on
        # the next confirmed signal.
        assert db.strategy_deployments.rows[0]["mode"] == "paper"
        assert db.strategy_deployments.rows[0]["status"] == "PAUSED"
        assert db.strategy_deployments.rows[0]["risk"]["live"]["last_block_reason"] == "manual_stop"
        # the other deployment's position is still registered
        assert reg.get("o2") is not None
        # Place-accept is pending: keep watching and keep the OCO until the guard
        # obtains two authenticated flat reads.
        assert reg.get("o1") is not None
        assert reg.get("o1")["squaring"] is True

    def test_stop_defers_close_loop_until_broker_confirms_flat(self, monkeypatch):
        """Place acceptance must not close the journal or manufacture P&L."""
        db = FakeDB()
        d = _deployment(mode="live")
        d["risk"]["live"] = {"lots": 3}
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
        assert out["exit_submitted_tsyms"] == ["NIFTY25000CE"]
        assert out["flat_confirmation_pending_tsyms"] == ["NIFTY25000CE"]
        row = db.live_trades.rows[0]
        assert row["status"] == "OPEN"
        assert row.get("exit_reason") is None
        assert row["realized_pnl"] is None
        assert reg.get("o1")["squaring"] is True

    def test_stop_no_positions_still_disables_and_pauses(self, monkeypatch):
        db = FakeDB()
        d = _deployment(mode="live")
        d["risk"]["live"] = {}
        db.strategy_deployments.rows.append(d)
        reg, squared = _install(monkeypatch, db)
        out = asyncio.run(dep.stop_deployment_live("dep-1"))
        assert squared == []
        assert out["disabled"] is True
        assert out["paused"] is True
        assert out["squared_tsyms"] == []
        assert db.strategy_deployments.rows[0]["mode"] == "paper"
        assert db.strategy_deployments.rows[0]["status"] == "PAUSED"

    def test_stop_missing_deployment_404(self, monkeypatch):
        from fastapi import HTTPException
        db = FakeDB()
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.stop_deployment_live("nope"))
        assert ei.value.status_code == 404


# ===========================================================================
# stop / stop-all — cancel the resting broker OCO for each flattened position
# ===========================================================================

class _FakeOcoClient:
    """A broker client that only exposes async cancel_oco (recording). The
    _install fake already handles _live_square_position, so this client just
    needs cancel_oco to satisfy the deployment-stop OCO-cancel path."""
    def __init__(self):
        self.cancel_oco_calls: List[str] = []

    async def cancel_oco(self, al_id):
        self.cancel_oco_calls.append(al_id)
        return {"ok": True}


def _patch_get_client(monkeypatch, client):
    """Make the DIRECT `from app.routers.live_broker import _get_client` inside
    _square_live_positions_for_deployment resolve to our fake client. The cancel
    path imports the function from app.routers.live_broker, so patch THAT module
    attr (there is no dep._live_get_client seam)."""
    import app.routers.live_broker as lb

    async def _gc():
        return client
    monkeypatch.setattr(lb, "_get_client", _gc, raising=False)


class TestStopCancelsOco:
    def test_stop_keeps_resting_oco_until_flat_confirmed(self, monkeypatch):
        """An accepted exit leaves the broker OCO resting until confirmed flat."""
        db = FakeDB()
        d = _deployment(mode="live")
        d["risk"]["live"] = {"lots": 3}
        db.strategy_deployments.rows.append(d)
        reg = LiveMonitorRegistry()
        reg.register(key="o1", tsym="NIFTY25000CE", exch="NFO", qty=65, prd="I",
                     entry_price=100.0, state=build_monitor_state(100.0, stop_pct=50),
                     deployment_id="dep-1", oco_al_id="OCO1")
        reg, squared = _install(monkeypatch, db, registry=reg)
        client = _FakeOcoClient()
        _patch_get_client(monkeypatch, client)
        out = asyncio.run(dep.stop_deployment_live("dep-1"))
        assert squared == ["NIFTY25000CE"]
        assert client.cancel_oco_calls == []
        assert out["squared_tsyms"] == []
        assert out["exit_submitted_tsyms"] == ["NIFTY25000CE"]
        assert reg.get("o1")["squaring"] is True

    def test_stop_without_oco_al_id_does_not_cancel(self, monkeypatch):
        """An entry with NO oco_al_id → cancel_oco is never called."""
        db = FakeDB()
        d = _deployment(mode="live")
        d["risk"]["live"] = {"lots": 3}
        db.strategy_deployments.rows.append(d)
        reg = LiveMonitorRegistry()
        reg.register(key="o1", tsym="NIFTY25000CE", exch="NFO", qty=65, prd="I",
                     entry_price=100.0, state=build_monitor_state(100.0, stop_pct=50),
                     deployment_id="dep-1")          # no oco_al_id
        reg, squared = _install(monkeypatch, db, registry=reg)
        client = _FakeOcoClient()
        _patch_get_client(monkeypatch, client)
        out = asyncio.run(dep.stop_deployment_live("dep-1"))
        assert squared == ["NIFTY25000CE"]
        assert client.cancel_oco_calls == []          # no OCO to cancel
        assert out["squared_tsyms"] == []
        assert out["exit_submitted_tsyms"] == ["NIFTY25000CE"]

    def test_stop_all_reports_exit_pending_and_keeps_oco(self, monkeypatch):
        """stop-all submits exits but does not claim or finalize fills."""
        db = FakeDB()
        armed = _deployment(dep_id="dep-1", mode="live")
        armed["risk"]["live"] = {"lots": 3}
        db.strategy_deployments.rows.append(armed)
        reg = LiveMonitorRegistry()
        reg.register(key="o1", tsym="NIFTY25000CE", exch="NFO", qty=65, prd="I",
                     entry_price=100.0, state=build_monitor_state(100.0, stop_pct=50),
                     deployment_id="dep-1", oco_al_id="OCO1")
        reg, squared = _install(monkeypatch, db, registry=reg)
        client = _FakeOcoClient()
        _patch_get_client(monkeypatch, client)

        async def _no_paper(db_, **kw):
            return []
        monkeypatch.setattr(dep, "square_off_open_paper_trades", _no_paper)

        class _Stream:
            def latest_tick_map(self):
                return {}
        monkeypatch.setattr(dep, "upstox_stream_manager", _Stream())

        out = asyncio.run(dep.stop_all_deployments())
        assert "dep-1" in out["disarmed_live_deployment_ids"]
        assert squared == ["NIFTY25000CE"]
        assert client.cancel_oco_calls == []
        report = out["live_exit_reports"]["dep-1"]
        assert report["exit_submitted_tsyms"] == ["NIFTY25000CE"]
        assert report["flat_confirmation_pending_tsyms"] == ["NIFTY25000CE"]
        assert reg.get("o1")["squaring"] is True


# ===========================================================================
# status
# ===========================================================================

class TestStatus:
    def test_status_reports_armed_caps_today_and_open_positions(self, monkeypatch):
        db = FakeDB()
        d = _deployment(mode="live")   # mode=="live" is the SOLE authorization signal
        d["risk"]["live"] = {
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
        assert out["armed"] is True          # derived from mode=="live" now
        assert out["live_mode"] is True
        assert out["armed_until"] is None    # dead field: nothing writes an arm expiry
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
        a = _deployment(dep_id="dep-1", mode="live")   # mode is the SOLE live signal
        a["risk"]["live"] = {"lots": 3, "max_lots_per_day": 20, "max_concurrent": 2,
                             "daily_loss_cap": 5000.0}
        b = _deployment(dep_id="dep-2")  # mode="paper" default → not live
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

    def test_last_entry_surfaces_latest_live_trade_error(self, monkeypatch):
        """A refused live entry (signals.live_trade_error, previously write-only)
        is surfaced on the deployment's live-status payload — the LATEST one."""
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment("dep-1"))
        db.signals.rows.extend([
            {"id": "sig-old", "deployment_id": "dep-1", "candle_ts": 1000,
             "updated_at": "2026-06-25T09:00:00+00:00", "live_trade_error": "throttled"},
            {"id": "sig-new", "deployment_id": "dep-1", "candle_ts": 2000,
             "updated_at": "2026-06-25T10:30:00+00:00",
             "live_trade_error": "live_entry_premium_unavailable_or_stale"},
        ])
        _install(monkeypatch, db)
        out = asyncio.run(dep.deployment_live_status("dep-1"))
        assert out["last_entry"]["error"] == "live_entry_premium_unavailable_or_stale"
        assert out["last_entry"]["signal_id"] == "sig-new"   # latest by candle_ts (the bar)
        assert out["last_entry"]["at"] == "2026-06-25T10:30:00+00:00"

    def test_last_entry_surfaces_dry_run_intent(self, monkeypatch):
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment("dep-1"))
        db.signals.rows.append(
            {"id": "sig-1", "deployment_id": "dep-1", "updated_at": "2026-06-25T10:00:00+00:00",
             "live_intended": {"would_send": {"tsym": "X"}, "ref_ltp": 120.0, "lots": 2}})
        _install(monkeypatch, db)
        out = asyncio.run(dep.deployment_live_status("dep-1"))
        assert out["last_entry"]["intended"]["ref_ltp"] == 120.0
        assert out["last_entry"]["error"] is None

    def test_last_entry_none_when_latest_signal_has_no_live_outcome(self, monkeypatch):
        """A paper-only / no-live-attempt latest signal → no chip (last_entry None)."""
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment("dep-1"))
        db.signals.rows.append(
            {"id": "sig-1", "deployment_id": "dep-1", "updated_at": "2026-06-25T10:00:00+00:00",
             "status": "NEW"})  # no live_trade_error / live_intended
        _install(monkeypatch, db)
        out = asyncio.run(dep.deployment_live_status("dep-1"))
        assert out["last_entry"] is None


# ===========================================================================
# stop-all (extended: paper stop-all + disable/flatten mode="live" deployments)
# ===========================================================================

class TestStopAll:
    def test_stop_all_disables_and_flattens_live_mode_deployments(self, monkeypatch):
        db = FakeDB()
        live_dep = _deployment(dep_id="dep-1", mode="live")
        live_dep["risk"]["live"] = {"lots": 3}
        paper_dep = _deployment(dep_id="dep-2")          # ACTIVE, paper-only (no mode="live")
        db.strategy_deployments.rows.extend([live_dep, paper_dep])
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
        # live behaviour added: mode="live" deployment de-lived + flattened
        assert "dep-1" in out["disarmed_live_deployment_ids"]
        assert squared == ["NIFTY25000CE"]
        # `mode` (not risk.live.armed) is the authorization signal — selector +
        # assertion both flip on it now.
        assert db.strategy_deployments.rows[0]["mode"] == "paper"
        # the paper-only deployment is not in the disarmed-live list
        assert "dep-2" not in out["disarmed_live_deployment_ids"]


# ===========================================================================
# Review-fix regression pins (v0.56.0): the durable-`mode` re-authorization class.
# Every pause/stop/retire path must demote a live deployment to paper, so a later
# resume/re-pin/un-retire (which set status=ACTIVE and inspect nothing else) can
# never silently re-authorize real trading. Only /live/enable may produce live.
# ===========================================================================

class TestPauseDemotesLive:
    def test_generic_stop_flattens_live_and_demotes_to_paper(self, monkeypatch):
        """The generic Stop button reaches live deployments in the UI. It must
        flatten the REAL positions (not just paper) and drop mode to paper so a
        later Resume cannot re-authorize real money."""
        db = FakeDB()
        d = _deployment(dep_id="dep-1", mode="live")
        d["risk"]["live"] = {"lots": 3, "max_concurrent": 2, "daily_loss_cap": 5000.0}
        db.strategy_deployments.rows.append(d)
        reg = LiveMonitorRegistry()
        reg.register(key="o1", tsym="NIFTY25000CE", exch="NFO", qty=65, prd="I",
                     entry_price=100.0, state=build_monitor_state(100.0, stop_pct=50),
                     deployment_id="dep-1")
        reg, squared = _install(monkeypatch, db, registry=reg)
        import app.runtime as _rt
        monkeypatch.setattr(_rt, "get_db", lambda: db)

        async def _no_paper(db_, **kw):
            return []
        monkeypatch.setattr(dep, "square_off_open_paper_trades", _no_paper)

        class _Stream:
            def latest_tick_map(self):
                return {}
        monkeypatch.setattr(dep, "upstox_stream_manager", _Stream())

        out = asyncio.run(dep.stop_deployment("dep-1"))
        assert squared == ["NIFTY25000CE"]              # real leg flattened
        assert out["squared_live_count"] == 0
        assert out["squared_live_tsyms"] == []
        assert out["live_exit"]["exit_submitted_tsyms"] == ["NIFTY25000CE"]
        assert out["live_exit"]["flat_confirmation_pending_tsyms"] == ["NIFTY25000CE"]
        assert reg.get("o1")["squaring"] is True
        assert db.strategy_deployments.rows[0]["mode"] == "paper"   # demoted
        assert db.strategy_deployments.rows[0]["status"] == "PAUSED"

    def test_resume_after_stop_cannot_restore_live(self, monkeypatch):
        """End-to-end of the re-authorization hole: stop a live deployment, then
        resume it — it must come back PAPER, requiring an explicit /live/enable to
        go live again."""
        db = FakeDB()
        d = _deployment(dep_id="dep-1", mode="live")
        d["risk"]["live"] = {"lots": 3, "max_concurrent": 2, "daily_loss_cap": 5000.0}
        db.strategy_deployments.rows.append(d)
        reg, squared = _install(monkeypatch, db)
        import app.runtime as _rt
        monkeypatch.setattr(_rt, "get_db", lambda: db)

        async def _no_paper(db_, **kw):
            return []
        monkeypatch.setattr(dep, "square_off_open_paper_trades", _no_paper)

        class _Stream:
            def latest_tick_map(self):
                return {}
        monkeypatch.setattr(dep, "upstox_stream_manager", _Stream())

        async def _no_stream():
            return {}
        monkeypatch.setattr(dep, "_auto_follow_option_stream", _no_stream, raising=False)

        asyncio.run(dep.stop_deployment("dep-1"))
        asyncio.run(dep.resume_deployment("dep-1"))
        assert db.strategy_deployments.rows[0]["status"] == "ACTIVE"
        assert db.strategy_deployments.rows[0]["mode"] == "paper"   # NOT re-authorized

    def test_enable_requires_daily_loss_cap(self, monkeypatch):
        """A live deployment's only day-level loss halt is daily_loss_cap (the
        deployment kill switches are paper-only), and the removal of the per-session
        arm expiry makes exposure indefinite — so it is mandatory to go live."""
        from fastapi import HTTPException
        db = FakeDB()
        db.strategy_deployments.rows.append(_deployment())
        _install(monkeypatch, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(dep.enable_deployment_live("dep-1", _enable_body(daily_loss_cap=None)))
        assert ei.value.status_code == 400
        with pytest.raises(HTTPException):
            asyncio.run(dep.enable_deployment_live("dep-1", _enable_body(daily_loss_cap=0)))


# ===========================================================================
# Contract: routes are pinned in the backend API source
# ===========================================================================

def test_backend_exposes_live_deploy_routes():
    from tests.contract_corpus import backend_api_text
    server = backend_api_text()
    for needle in (
        '@api.post("/deployments/{deployment_id}/live/enable")',
        '@api.post("/deployments/{deployment_id}/live/disable")',
        '@api.post("/deployments/{deployment_id}/live/stop")',
        '@api.get("/deployments/{deployment_id}/live/status")',
        '@api.get("/deployments/live/status")',
    ):
        assert needle in server
