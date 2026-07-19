"""Tests for the continuous live sink (app/auto_live.py).

auto_live.py is a structural clone of paper_auto.py whose success path places a
REAL order (via an injected place_fn) instead of inserting a paper trade.

Trading-critical invariants covered here:
- authorization delegates to is_deployment_live_allowed (armed ∧ window ∧ connected);
- a signal can be claimed by paper OR live, never both (shared paper_trade_claim field);
- the live ENTRY ref_ltp is the OPTION premium from a FRESH live tick ONLY — a
  stale tick / last-candle / absent tick is REFUSED (never spot, never stale);
- lots are the user's fixed risk.live.lots, clamped to the account ceiling;
- a deployed position is NEVER unprotected (deep-default 50% premium stop floor);
- the orchestrator: governor caps, atomic claim, fresh-premium gate, executor
  call (side="B", capped lots, plan levels), live_trades journal, signal -> ACTIVE.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.auto_live import (  # noqa: E402
    auto_live_enabled,
    auto_live_trade_for_signal,
    claim_signal_for_live_trade,
    release_live_trade_claim,
    resolve_capped_lots,
    resolve_live_entry_ref_ltp,
    resolve_live_exit_plan,
    _GUARD_DEFAULT_STOP_PCT,
)
from app.paper_auto import claim_signal_for_paper_trade  # noqa: E402
from app.signal_lifecycle import create_signal_doc, transition_signal  # noqa: E402


# ---------- minimal in-memory Mongo stand-in (cloned from test_paper_auto) ------

class FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)

    def sort(self, key: str, direction: int = 1):
        self._rows.sort(key=lambda r: r.get(key, 0), reverse=(direction == -1))
        return self

    def limit(self, n: int):
        self._rows = self._rows[: int(n)]
        return self

    async def to_list(self, length: Optional[int] = None):
        return list(self._rows if length is None else self._rows[: int(length)])


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        if isinstance(v, dict) and "$exists" in v:
            if bool(k in row) != bool(v["$exists"]):
                return False
        elif isinstance(v, dict) and "$gte" in v:
            rv = row.get(k)
            if rv is None or rv < v["$gte"]:
                return False
        elif row.get(k) != v:
            return False
    return True


class FakeCollection:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def find(self, query=None, projection=None):
        return FakeCursor([r for r in self.rows if _matches(r, query or {})])

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if _matches(r, query):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if _matches(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                if "$unset" in update:
                    for key in update["$unset"]:
                        r.pop(key, None)
                return MagicMock(matched_count=1)
        return MagicMock(matched_count=0)

    async def replace_one(self, query, replacement, upsert=False):
        for i, r in enumerate(self.rows):
            if _matches(r, query):
                self.rows[i] = dict(replacement)
                return MagicMock(matched_count=1)
        return MagicMock(matched_count=0)


class FakeDB:
    def __init__(self):
        self.options_1m = FakeCollection()
        self.signals = FakeCollection()
        self.paper_trades = FakeCollection()
        self.live_trades = FakeCollection()
        self.strategy_deployments = FakeCollection()


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


KEY = "NSE_FO|TEST|23950CE"


def make_confirmed_signal(*, instrument_key: str = KEY, lot_size: int = 75,
                          risk_hints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A signal doc shaped like the evaluator's output, advanced to CONFIRMED."""
    doc = create_signal_doc(
        instrument="NIFTY",
        direction="CE",
        strategy_id="stub",
        entry_price=23950.0,  # SPOT close — must never become the trade entry
        confidence=80,
        reasons=["test"],
        option_contract={
            "instrument_key": instrument_key,
            "trading_symbol": "NIFTYTEST23950CE",
            "lot_size": lot_size,
            "strike": 23950.0,
            "side": "CE",
            "expiry_date": "2026-06-25",
        },
        context={},
    )
    doc = transition_signal(doc, "FORMING", reason="test")
    doc = transition_signal(doc, "CONFIRMED", reason="test")
    doc["deployment_id"] = "dep-1"
    doc["blocked"] = False
    if risk_hints is not None:
        doc["risk_hints"] = risk_hints
    return doc


# armed_until 15:00 IST == 09:30 UTC on 2026-06-25
ARMED_UNTIL = "2026-06-25T09:30:00+00:00"
# 11:30 IST — well inside the window
NOW = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)


def make_live_deployment(*, lots: int = 2, armed: bool = True,
                         armed_until: str = ARMED_UNTIL, **live) -> Dict[str, Any]:
    base = {
        "armed": armed, "armed_until": armed_until, "lots": lots,
        # Realistic per-deployment caps. mode == "live" IS the authorization now
        # (the per-session arm ceremony is gone — see is_deployment_live_allowed),
        # so check_live_caps FAILS CLOSED for any live-mode deployment with no
        # caps configured (live_deploy_governor.py). These defaults keep every
        # generic fixture in this file a VALID, cap-bounded live deployment;
        # test_orchestrator_live_no_caps_fails_closed below proves the fail-closed
        # path directly with a caps-less fixture.
        "max_concurrent": 5, "max_lots_per_day": 100,
    }
    base.update(live)
    return {"id": "dep-1", "mode": "live", "risk": {"live": base}}


# Canned place_fn results -------------------------------------------------------

def _fresh_tick(price: float) -> Dict[str, Any]:
    return {"last_price": price, "ts": NOW.timestamp()}


def _stale_tick(price: float) -> Dict[str, Any]:
    return {"last_price": price, "ts": NOW.timestamp() - 10_000}  # way past freshness


def make_place_fn(result: Dict[str, Any], calls: List[Dict[str, Any]]):
    async def _place(contract, **kwargs):
        calls.append({"contract": contract, **kwargs})
        return dict(result)
    return _place


# ====================== auto_live_enabled truth table ==========================

def test_auto_live_enabled_armed_connected_in_window():
    assert auto_live_enabled(make_live_deployment(), NOW, connected=True) is True


def test_auto_live_enabled_false_when_not_live_mode():
    """mode is now THE authorization (the per-session arm ceremony is gone) — a
    deployment whose mode isn't "live" is refused regardless of risk.live.armed."""
    dep = make_live_deployment()
    dep["mode"] = "paper"
    assert auto_live_enabled(dep, NOW, connected=True) is False


def test_auto_live_enabled_false_after_armed_until():
    past = datetime(2026, 6, 25, 10, 0, tzinfo=timezone.utc)  # 15:30 IST
    assert auto_live_enabled(make_live_deployment(), past, connected=True) is False


def test_auto_live_enabled_false_when_disconnected():
    assert auto_live_enabled(make_live_deployment(), NOW, connected=False) is False


def test_auto_live_enabled_false_on_malformed():
    assert auto_live_enabled({}, NOW, connected=True) is False
    assert auto_live_enabled({"risk": {"live": "x"}}, NOW, connected=True) is False


# ====================== claim / mutual exclusion ===============================

@pytest.mark.asyncio
async def test_claim_single_winner():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    assert await claim_signal_for_live_trade(db, sig["id"], "auto_live") is True
    # second claim loses (already claimed)
    assert await claim_signal_for_live_trade(db, sig["id"], "auto_live") is False


@pytest.mark.asyncio
async def test_live_claim_blocks_later_paper_claim():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    assert await claim_signal_for_live_trade(db, sig["id"], "auto_live") is True
    # paper claim must lose on the SAME signal (shared paper_trade_claim field)
    assert await claim_signal_for_paper_trade(db, sig["id"], "auto_paper") is False


@pytest.mark.asyncio
async def test_paper_claim_blocks_later_live_claim():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    assert await claim_signal_for_paper_trade(db, sig["id"], "auto_paper") is True
    assert await claim_signal_for_live_trade(db, sig["id"], "auto_live") is False


@pytest.mark.asyncio
async def test_release_live_claim_allows_reclaim():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    assert await claim_signal_for_live_trade(db, sig["id"], "auto_live") is True
    await release_live_trade_claim(db, sig["id"])
    assert await claim_signal_for_live_trade(db, sig["id"], "auto_live") is True


# ====================== resolve_live_entry_ref_ltp =============================

def test_ref_ltp_fresh_tick_returns_premium():
    db = FakeDB()
    ltp = resolve_live_entry_ref_ltp(
        db, KEY, latest_tick_lookup={KEY: _fresh_tick(151.5)}.get,
        now_ts=NOW.timestamp())
    assert ltp == 151.5


def test_ref_ltp_stale_tick_refused():
    db = FakeDB()
    ltp = resolve_live_entry_ref_ltp(
        db, KEY, latest_tick_lookup={KEY: _stale_tick(151.5)}.get,
        now_ts=NOW.timestamp())
    assert ltp is None


def test_ref_ltp_no_tick_refused():
    db = FakeDB()
    ltp = resolve_live_entry_ref_ltp(
        db, KEY, latest_tick_lookup={}.get, now_ts=NOW.timestamp())
    assert ltp is None


def test_ref_ltp_absent_lookup_refused():
    db = FakeDB()
    ltp = resolve_live_entry_ref_ltp(db, KEY, latest_tick_lookup=None,
                                     now_ts=NOW.timestamp())
    assert ltp is None


# ====================== resolve_capped_lots ====================================

def test_capped_lots_clamps_to_account_max():
    dep = make_live_deployment(lots=50)
    assert resolve_capped_lots(dep, account_max=20) == 20


def test_capped_lots_under_ceiling_passthrough():
    dep = make_live_deployment(lots=2)
    assert resolve_capped_lots(dep, account_max=20) == 2


def test_capped_lots_missing_defaults_to_one():
    dep = {"id": "dep-1", "risk": {"live": {}}}
    assert resolve_capped_lots(dep, account_max=20) == 1


def test_capped_lots_nonnumeric_defaults_to_one():
    dep = make_live_deployment(lots="abc")
    assert resolve_capped_lots(dep, account_max=20) == 1


def test_capped_lots_zero_floors_to_one():
    dep = make_live_deployment(lots=0)
    assert resolve_capped_lots(dep, account_max=20) == 1


# ====================== resolve_live_exit_plan =================================

def test_exit_plan_premium_hint_wins():
    sig = make_confirmed_signal(risk_hints={"stop_pct": 0.4, "target_pct": 0.8})
    dep = make_live_deployment()
    dep["risk"]["auto_paper_stop_pct"] = 0.2  # should be overridden by the hint
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["levels"]["stop_pct"] == 0.4
    assert plan["levels"]["target_pct"] == 0.8


def test_exit_plan_deployment_fallback():
    sig = make_confirmed_signal(risk_hints={})
    dep = make_live_deployment()
    dep["risk"]["auto_paper_stop_pct"] = 0.25
    dep["risk"]["auto_paper_target_pct"] = 0.6
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["levels"]["stop_pct"] == 0.25
    assert plan["levels"]["target_pct"] == 0.6


def test_exit_plan_deployment_pts_fallback():
    sig = make_confirmed_signal(risk_hints={})
    dep = make_live_deployment()
    dep["risk"]["auto_paper_stop_pts"] = 12.0
    dep["risk"]["auto_paper_target_pts"] = 30.0
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["levels"]["stop_pts"] == 12.0
    assert plan["levels"]["target_pts"] == 30.0
    assert plan["levels"]["stop_pct"] is None


def test_exit_plan_trail_from_exit_controls():
    sig = make_confirmed_signal(risk_hints={"stop_pct": 0.4})
    dep = make_live_deployment()
    ec = {"enabled": True, "unit": "pct",
          "trailing": {"activation": 0.3, "distance": 0.2}}
    dep["risk"]["exit_controls"] = ec
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["levels"]["trail"] == ec


def test_exit_plan_trail_none_when_no_exit_controls():
    sig = make_confirmed_signal(risk_hints={"stop_pct": 0.4})
    dep = make_live_deployment()
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["levels"]["trail"] is None


def test_exit_plan_carries_spot_exit_and_time_stop():
    sig = make_confirmed_signal(risk_hints={
        "spot_target_pts": 40.0, "spot_stop_pts": 20.0, "time_stop_minutes": 30,
    })
    dep = make_live_deployment()
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["spot_exit"] is not None
    assert plan["spot_exit"]["direction"] == "CE"
    assert plan["time_stop_minutes"] == 30


def test_exit_plan_deep_default_stop_when_nothing_configured():
    sig = make_confirmed_signal(risk_hints={})
    dep = make_live_deployment()  # no auto_paper_*; no spot hints
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["levels"]["stop_pct"] == _GUARD_DEFAULT_STOP_PCT
    assert plan["spot_exit"] is None


def test_exit_plan_spot_only_stop_still_seeds_premium_floor():
    """A spot-only-stop signal (spot_stop_pts/spot_target_pts, no premium stop/target,
    no exit_controls) MUST still get the 50% premium catastrophe stop — build_monitor_state
    needs a premium input to register the position, and every live position needs a
    premium downside net. The spot-mirror exit remains ADDITIVE on top."""
    sig = make_confirmed_signal(risk_hints={"spot_stop_pts": 20.0, "spot_target_pts": 40.0})
    dep = make_live_deployment()  # no auto_paper_*; no exit_controls
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["levels"]["stop_pct"] == _GUARD_DEFAULT_STOP_PCT  # seeded floor
    assert plan["spot_exit"] is not None                          # spot-mirror additive


def test_exit_plan_spot_only_stop_carries_time_stop_when_present():
    sig = make_confirmed_signal(risk_hints={
        "spot_stop_pts": 20.0, "spot_target_pts": 40.0, "time_stop_minutes": 45,
    })
    dep = make_live_deployment()
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["levels"]["stop_pct"] == _GUARD_DEFAULT_STOP_PCT
    assert plan["spot_exit"] is not None
    assert plan["time_stop_minutes"] == 45


def test_exit_plan_time_stop_only_seeds_premium_floor():
    """A time-stop-only signal (no premium stop/target, no spot stop) gets the floor."""
    sig = make_confirmed_signal(risk_hints={"time_stop_minutes": 30})
    dep = make_live_deployment()
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["levels"]["stop_pct"] == _GUARD_DEFAULT_STOP_PCT
    assert plan["time_stop_minutes"] == 30


def test_exit_plan_premium_target_but_no_stop_seeds_floor():
    """A premium-target-but-no-stop signal still gets the 50% stop seeded; the
    target is preserved untouched."""
    sig = make_confirmed_signal(risk_hints={"target_pct": 0.8})
    dep = make_live_deployment()  # no premium/spot stop
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["levels"]["stop_pct"] == _GUARD_DEFAULT_STOP_PCT  # stop seeded
    assert plan["levels"]["target_pct"] == 0.8                    # target preserved


def test_exit_plan_no_override_when_premium_stop_present():
    """Existing behavior unchanged: a real premium stop is NOT replaced by the floor."""
    sig = make_confirmed_signal(risk_hints={"stop_pct": 0.4})
    dep = make_live_deployment()
    plan = resolve_live_exit_plan(sig, dep)
    assert plan["levels"]["stop_pct"] == 0.4  # no 50% override


# ====================== orchestrator ===========================================

_SUCCESS = {"placed": True, "protected": True, "norenordno": "ABC",
            "cid": "c1", "verdicts": []}
_DRY_RUN = {"placed": False, "dry_run": True, "would_send": {"x": 1}, "verdicts": []}
_THROTTLE = {"placed": False, "reason": "rate_throttled", "verdicts": []}


def _arm_for_factory(captured: List[Dict[str, Any]]):
    # Mirrors the real arm_for signature: auto_live always forwards the per-deployment
    # catastrophe pct as keyword args, so the fake must accept **kwargs.
    def _arm_for(plan, signal_doc, ref_ltp, **kwargs):
        captured.append({"plan": plan, "signal_doc": signal_doc, "ref_ltp": ref_ltp})
        return MagicMock(name="arm_callable")
    return _arm_for


@pytest.mark.asyncio
async def test_orchestrator_success_inserts_live_trade_and_activates_signal():
    db = FakeDB()
    sig = make_confirmed_signal(risk_hints={"stop_pct": 0.4, "target_pct": 0.8})
    db.signals.rows.append(dict(sig))
    calls: List[Dict[str, Any]] = []
    armed: List[Dict[str, Any]] = []
    out = await auto_live_trade_for_signal(
        db, make_live_deployment(lots=2), sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get,
        now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, calls),
        arm_for=_arm_for_factory(armed),
        account_max=20,
    )
    assert out["created"] is True
    assert out["norenordno"] == "ABC"
    assert out["entry_price"] == 151.5
    assert out["lots"] == 2

    # live_trades doc written with the canonical fields
    assert len(db.live_trades.rows) == 1
    trade = db.live_trades.rows[0]
    assert trade["source"] == "auto_live_on_signal"
    assert trade["norenordno"] == "ABC"
    assert trade["cid"] == "c1"
    assert trade["deployment_id"] == "dep-1"
    assert trade["lots"] == 2
    assert trade["entry_price"] == 151.5
    assert trade["status"] == "OPEN"
    assert trade["instrument_key"] == KEY

    # signal advanced CONFIRMED -> ACTIVE + live_trade_id stamped
    stored_sig = db.signals.rows[0]
    assert stored_sig["state"] == "ACTIVE"
    assert stored_sig["live_trade_id"] == trade["id"]

    # place_fn called with side="B", capped lots, plan levels, account_max
    assert len(calls) == 1
    call = calls[0]
    assert call["side"] == "B"
    assert call["capped_lots"] == 2
    assert call["account_max_lots"] == 20
    assert call["ref_ltp"] == 151.5
    assert call["levels"]["stop_pct"] == 0.4
    # contract built from the signal (long-only CE leg)
    assert call["contract"]["underlying"] == "NIFTY"
    assert call["contract"]["strike"] == 23950.0
    assert call["contract"]["side"] == "CE"
    assert call["contract"]["expiry_date"] == "2026-06-25"

    # arm_for received the plan + ref_ltp
    assert len(armed) == 1
    assert armed[0]["ref_ltp"] == 151.5
    assert armed[0]["plan"]["levels"]["stop_pct"] == 0.4


# ---- B3: resting OCO backstop journaling + per-deployment band wiring ----------

_SUCCESS_OCO = {"placed": True, "protected": True, "norenordno": "ABC",
                "cid": "c1", "verdicts": [], "oco_al_id": "OCO1"}
_SUCCESS_NO_OCO = {"placed": True, "protected": True, "norenordno": "ABC",
                   "cid": "c1", "verdicts": [], "oco_al_id": None}


def _arm_for_factory_capturing_kwargs(captured: List[Dict[str, Any]]):
    """Like _arm_for_factory but also records the keyword args (catastrophe pct)."""
    def _arm_for(plan, signal_doc, ref_ltp, **kwargs):
        captured.append({"plan": plan, "signal_doc": signal_doc,
                         "ref_ltp": ref_ltp, "kwargs": kwargs})
        return MagicMock(name="arm_callable")
    return _arm_for


@pytest.mark.asyncio
async def test_orchestrator_journals_oco_al_id_when_backstop_placed():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    out = await auto_live_trade_for_signal(
        db, make_live_deployment(lots=2), sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get,
        now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS_OCO, []),
        arm_for=_arm_for_factory([]),
        account_max=20,
    )
    assert out["created"] is True
    trade = db.live_trades.rows[0]
    assert trade["oco_al_id"] == "OCO1"
    assert trade["oco_error"] is None


@pytest.mark.asyncio
async def test_orchestrator_journals_no_broker_backstop_when_oco_missing():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    out = await auto_live_trade_for_signal(
        db, make_live_deployment(lots=2), sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get,
        now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS_NO_OCO, []),
        arm_for=_arm_for_factory([]),
        account_max=20,
    )
    assert out["created"] is True
    trade = db.live_trades.rows[0]
    assert trade["oco_al_id"] is None
    assert trade["oco_error"] == "no_broker_backstop"


@pytest.mark.asyncio
async def test_orchestrator_passes_per_deployment_catastrophe_pct_to_arm_for():
    """The per-deployment catastrophe band reaches arm_for via the PER-SIGNAL call
    (the live context is deployment-agnostic): risk.live.catastrophe_stop_pct /
    catastrophe_target_pct are forwarded as keyword args."""
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    captured: List[Dict[str, Any]] = []
    dep = make_live_deployment(lots=2, catastrophe_stop_pct=48, catastrophe_target_pct=140)
    out = await auto_live_trade_for_signal(
        db, dep, sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get,
        now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS_OCO, []),
        arm_for=_arm_for_factory_capturing_kwargs(captured),
        account_max=20,
    )
    assert out["created"] is True
    assert len(captured) == 1
    assert captured[0]["kwargs"]["catastrophe_stop_pct"] == 48
    assert captured[0]["kwargs"]["catastrophe_target_pct"] == 140


@pytest.mark.asyncio
async def test_orchestrator_catastrophe_pct_default_none_when_unset():
    """When the deployment has no risk.live catastrophe pct, arm_for is called with
    catastrophe_stop_pct=None / catastrophe_target_pct=None (band falls to defaults)."""
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    captured: List[Dict[str, Any]] = []
    out = await auto_live_trade_for_signal(
        db, make_live_deployment(lots=2), sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get,
        now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS_OCO, []),
        arm_for=_arm_for_factory_capturing_kwargs(captured),
        account_max=20,
    )
    assert out["created"] is True
    assert captured[0]["kwargs"]["catastrophe_stop_pct"] is None
    assert captured[0]["kwargs"]["catastrophe_target_pct"] is None


@pytest.mark.asyncio
async def test_orchestrator_disabled_when_not_live_mode():
    """mode is now THE authorization — a paper-mode deployment never reaches the
    live sink regardless of risk.live.armed."""
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    dep = make_live_deployment()
    dep["mode"] = "paper"
    out = await auto_live_trade_for_signal(
        db, dep, sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, []))
    assert out["created"] is False
    assert out["reason"] == "auto_live_disabled"
    assert len(db.live_trades.rows) == 0


@pytest.mark.asyncio
async def test_orchestrator_live_no_caps_fails_closed():
    """PROVES the fail-closed behavior in check_live_caps: a live-mode deployment
    with NO caps configured (max_concurrent / max_lots_per_day / daily_loss_cap
    all absent) must be refused with reason "live_caps_missing" and PAUSED — never
    allowed to trade unbounded just because mode == "live"."""
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    dep = {
        "id": "dep-1", "mode": "live",
        "risk": {"live": {"armed": True, "armed_until": ARMED_UNTIL, "lots": 2}},
    }
    db.strategy_deployments.rows.append(dict(dep))
    calls: List[Dict[str, Any]] = []
    out = await auto_live_trade_for_signal(
        db, dep, sig, latest_tick_lookup={KEY: _fresh_tick(151.5)}.get,
        now_utc=NOW, place_fn=make_place_fn(_SUCCESS, calls))
    assert out["created"] is False
    assert out["reason"] == "live_caps_missing"
    assert out["paused"] is True
    assert len(calls) == 0
    assert len(db.live_trades.rows) == 0
    stored_dep = db.strategy_deployments.rows[0]
    assert stored_dep["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_orchestrator_stale_tick_refuses_and_releases_claim():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    calls: List[Dict[str, Any]] = []
    out = await auto_live_trade_for_signal(
        db, make_live_deployment(), sig,
        latest_tick_lookup={KEY: _stale_tick(151.5)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, calls))
    assert out["created"] is False
    assert "live_entry_premium_unavailable_or_stale" in out["error"]
    assert len(calls) == 0                       # never reached the executor
    assert len(db.live_trades.rows) == 0
    stored = db.signals.rows[0]
    assert stored["state"] == "CONFIRMED"        # stays confirmed for retry
    assert stored.get("live_trade_error")        # journaled
    assert "paper_trade_claim" not in stored     # claim released


@pytest.mark.asyncio
async def test_orchestrator_dry_run_no_insert_releases_claim_audits_signal():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    calls: List[Dict[str, Any]] = []
    out = await auto_live_trade_for_signal(
        db, make_live_deployment(), sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get, now_utc=NOW,
        place_fn=make_place_fn(_DRY_RUN, calls))
    assert out["created"] is False
    assert out["dry_run"] is True
    assert len(calls) == 1                       # the executor WAS called (dry-run)
    assert len(db.live_trades.rows) == 0         # but no trade booked
    stored = db.signals.rows[0]
    assert stored["state"] == "CONFIRMED"
    assert "paper_trade_claim" not in stored     # released for a later real arm
    assert stored.get("live_intended") is not None  # intended-order audit set


@pytest.mark.asyncio
async def test_orchestrator_throttle_no_insert_records_reason():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    out = await auto_live_trade_for_signal(
        db, make_live_deployment(), sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get, now_utc=NOW,
        place_fn=make_place_fn(_THROTTLE, []))
    assert out["created"] is False
    assert out["reason"] == "rate_throttled"
    assert len(db.live_trades.rows) == 0
    stored = db.signals.rows[0]
    assert stored["state"] == "CONFIRMED"
    assert stored.get("live_trade_error") == "rate_throttled"
    assert "paper_trade_claim" not in stored     # released


@pytest.mark.asyncio
async def test_orchestrator_governor_max_concurrent_skips_without_placing():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    # one OPEN live trade already exists for this deployment
    db.live_trades.rows.append({
        "deployment_id": "dep-1", "status": "OPEN", "lots": 1,
        "created_at": NOW.isoformat(),
    })
    dep = make_live_deployment(max_concurrent=1)
    calls: List[Dict[str, Any]] = []
    out = await auto_live_trade_for_signal(
        db, dep, sig, latest_tick_lookup={KEY: _fresh_tick(151.5)}.get,
        now_utc=NOW, place_fn=make_place_fn(_SUCCESS, calls))
    assert out["created"] is False
    assert out["reason"] == "max_concurrent"
    assert len(calls) == 0                       # executor never called
    assert len(db.live_trades.rows) == 1         # no new trade


@pytest.mark.asyncio
async def test_orchestrator_daily_loss_cap_pauses_deployment():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    # a big realized loss today breaches the cap
    db.live_trades.rows.append({
        "deployment_id": "dep-1", "status": "CLOSED", "lots": 1,
        "realized_pnl": -8000.0,
        "created_at": NOW.isoformat(), "closed_at": NOW.isoformat(),
    })
    dep = make_live_deployment(daily_loss_cap=5000.0)
    db.strategy_deployments.rows.append(dict(dep))
    calls: List[Dict[str, Any]] = []
    out = await auto_live_trade_for_signal(
        db, dep, sig, latest_tick_lookup={KEY: _fresh_tick(151.5)}.get,
        now_utc=NOW, place_fn=make_place_fn(_SUCCESS, calls))
    assert out["created"] is False
    assert out["reason"] == "daily_loss_cap"
    assert out["paused"] is True
    assert len(calls) == 0
    # deployment row updated: status="PAUSED" is the load-bearing stop now
    # (evaluate_all only iterates {"status": "ACTIVE"}); last_block_reason /
    # disabled_at are audit-only fields, never read as authorization.
    stored_dep = db.strategy_deployments.rows[0]
    assert stored_dep["status"] == "PAUSED"
    assert stored_dep["risk"]["live"]["last_block_reason"] == "daily_loss"
    assert stored_dep["risk"]["live"]["disabled_at"]


@pytest.mark.asyncio
async def test_orchestrator_claim_loser_returns_claimed_elsewhere():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    # pre-claim the signal (simulate a concurrent paper/live writer winning)
    await claim_signal_for_paper_trade(db, sig["id"], "auto_paper")
    calls: List[Dict[str, Any]] = []
    out = await auto_live_trade_for_signal(
        db, make_live_deployment(), sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, calls))
    assert out["created"] is False
    assert out["reason"] == "signal_claimed_elsewhere"
    assert len(calls) == 0
    assert len(db.live_trades.rows) == 0


@pytest.mark.asyncio
async def test_orchestrator_not_confirmed_skips():
    db = FakeDB()
    sig = make_confirmed_signal()
    sig = transition_signal(sig, "TRIGGERED", reason="test")
    db.signals.rows.append(dict(sig))
    out = await auto_live_trade_for_signal(
        db, make_live_deployment(), sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, []))
    assert out["created"] is False
    assert "signal_not_confirmed" in out["reason"]


@pytest.mark.asyncio
async def test_orchestrator_no_option_contract_skips():
    db = FakeDB()
    sig = make_confirmed_signal(instrument_key="")
    db.signals.rows.append(dict(sig))
    out = await auto_live_trade_for_signal(
        db, make_live_deployment(), sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, []))
    assert out["created"] is False
    assert out["reason"] == "no_option_contract"


@pytest.mark.asyncio
async def test_orchestrator_ref_ltp_is_option_premium_not_spot():
    """The booked entry must be the option premium (151.5), never the signal's
    spot entry_price (23950.0)."""
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    calls: List[Dict[str, Any]] = []
    out = await auto_live_trade_for_signal(
        db, make_live_deployment(), sig,
        latest_tick_lookup={KEY: _fresh_tick(151.5)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, calls))
    assert out["entry_price"] == 151.5
    assert calls[0]["ref_ltp"] == 151.5
    assert db.live_trades.rows[0]["entry_price"] == 151.5
