"""Tests for automatic paper trading on confirmed signals (app/paper_auto.py).

Trading-critical invariants covered here:
- entry price is OPTION PREMIUM (live tick, else fresh options_1m candle),
  never the spot index level, and trade creation is REFUSED when no premium
  source exists (with the reason journaled on the signal);
- stop/target derive from the strategy's own risk hints first (shared decision
  engine), deployment-level percentages second;
- auto trading applies only to paper-mode deployments that opted in;
- the minute marker closes trades on stop/target and exits the linked signal.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.paper_auto import (  # noqa: E402
    auto_paper_enabled,
    auto_paper_trade_for_signal,
    compute_auto_risk_levels,
    mark_open_deployment_trades,
    resolve_option_entry_price,
)
from app.signal_lifecycle import create_signal_doc, transition_signal  # noqa: E402


# ---------- minimal in-memory Mongo stand-in -----------------------------------

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


def make_paper_deployment(**risk) -> Dict[str, Any]:
    return {"id": "dep-1", "mode": "paper",
            "risk": {"auto_paper": True, "default_lots": 1, **risk}}


# ---------- resolve_option_entry_price ------------------------------------------

@pytest.mark.asyncio
async def test_entry_price_prefers_live_tick_over_candle():
    db = FakeDB()
    db.options_1m.rows.append({"instrument_key": KEY, "ts": now_ms(), "close": 140.0})
    price = await resolve_option_entry_price(
        db, KEY, latest_tick_lookup={KEY: {"last_price": 151.5}}.get)
    assert price == 151.5


@pytest.mark.asyncio
async def test_entry_price_falls_back_to_fresh_candle():
    db = FakeDB()
    db.options_1m.rows.append({"instrument_key": KEY, "ts": now_ms() - 60_000, "close": 140.0})
    price = await resolve_option_entry_price(db, KEY, latest_tick_lookup=None)
    assert price == 140.0


@pytest.mark.asyncio
async def test_entry_price_rejects_stale_candle():
    db = FakeDB()
    stale_ts = now_ms() - 10 * 60_000  # older than the 5-minute freshness window
    db.options_1m.rows.append({"instrument_key": KEY, "ts": stale_ts, "close": 140.0})
    assert await resolve_option_entry_price(db, KEY, latest_tick_lookup=None) is None


@pytest.mark.asyncio
async def test_entry_price_invalid_tick_falls_through_to_candle():
    db = FakeDB()
    db.options_1m.rows.append({"instrument_key": KEY, "ts": now_ms(), "close": 140.0})
    price = await resolve_option_entry_price(
        db, KEY, latest_tick_lookup={KEY: {"last_price": None}}.get)
    assert price == 140.0


@pytest.mark.asyncio
async def test_entry_price_empty_key_is_none():
    assert await resolve_option_entry_price(FakeDB(), "") is None


# ---------- compute_auto_risk_levels --------------------------------------------

def test_risk_levels_strategy_hints_win_over_deployment():
    stop, target = compute_auto_risk_levels(
        100.0,
        {"target_pct": 40, "stop_pct": 30},
        {"auto_paper_target_pct": 80, "auto_paper_stop_pct": 60},
    )
    assert target == 140.0  # from hints, not 180
    assert stop == 70.0     # from hints, not 40


def test_risk_levels_deployment_fallback_when_no_hints():
    stop, target = compute_auto_risk_levels(
        100.0, None, {"auto_paper_target_pct": 50, "auto_paper_stop_pct": 25})
    assert target == 150.0
    assert stop == 75.0


def test_risk_levels_none_when_unconfigured():
    assert compute_auto_risk_levels(100.0, {}, {}) == (None, None)


def test_risk_levels_stop_floors_at_tick():
    stop, _ = compute_auto_risk_levels(1.0, {"stop_pct": 99.9}, {})
    assert stop == 0.05  # never zero/negative premium


def test_risk_levels_deployment_pts_fallback():
    stop, target = compute_auto_risk_levels(
        150.0, None, {"auto_paper_target_pts": 40, "auto_paper_stop_pts": 30})
    assert target == 190.0  # entry + pts
    assert stop == 120.0    # entry - pts


def test_risk_levels_deployment_pts_win_over_pct():
    # Points take precedence over percent at the deployment level, matching the
    # backtest's _resolve_option_levels rule.
    stop, target = compute_auto_risk_levels(
        100.0, None,
        {"auto_paper_target_pts": 40, "auto_paper_stop_pts": 30,
         "auto_paper_target_pct": 80, "auto_paper_stop_pct": 60},
    )
    assert target == 140.0  # pts (not 180 from pct)
    assert stop == 70.0     # pts (not 40 from pct)


def test_risk_levels_strategy_hints_win_over_deployment_pts():
    stop, target = compute_auto_risk_levels(
        100.0,
        {"target_pct": 40, "stop_pct": 30},
        {"auto_paper_target_pts": 90, "auto_paper_stop_pts": 90},
    )
    assert target == 140.0
    assert stop == 70.0


def test_risk_levels_pts_stop_floors_at_tick():
    stop, _ = compute_auto_risk_levels(1.0, None, {"auto_paper_stop_pts": 50})
    assert stop == 0.05


def test_risk_levels_mixed_units_per_leg():
    # Target configured in pts only, stop in pct only — each leg resolves
    # independently.
    stop, target = compute_auto_risk_levels(
        100.0, None, {"auto_paper_target_pts": 25, "auto_paper_stop_pct": 20})
    assert target == 125.0
    assert stop == 80.0


# ---------- auto_paper_enabled ----------------------------------------------------

def test_auto_paper_only_for_opted_in_paper_mode():
    assert auto_paper_enabled({"mode": "paper", "risk": {"auto_paper": True}}) is True
    assert auto_paper_enabled({"mode": "shadow", "risk": {"auto_paper": True}}) is False
    assert auto_paper_enabled({"mode": "recommendation", "risk": {"auto_paper": True}}) is False
    assert auto_paper_enabled({"mode": "paper", "risk": {}}) is False  # pre-existing deployments
    assert auto_paper_enabled({"mode": "paper"}) is False


# ---------- auto_paper_trade_for_signal -------------------------------------------

@pytest.mark.asyncio
async def test_auto_trade_uses_option_premium_not_spot():
    db = FakeDB()
    sig = make_confirmed_signal(risk_hints={"target_pct": 40, "stop_pct": 30})
    db.signals.rows.append(dict(sig))
    deployment = make_paper_deployment(default_lots=2)

    res = await auto_paper_trade_for_signal(
        db, deployment, sig, latest_tick_lookup={KEY: {"last_price": 152.0}}.get)

    assert res["created"] is True
    assert len(db.paper_trades.rows) == 1
    trade = db.paper_trades.rows[0]
    assert trade["entry_price"] == 152.0          # premium, NOT the 23950 spot
    assert trade["quantity"] == 2 * 75            # lots x contract lot_size
    assert trade["deployment_id"] == "dep-1"
    assert trade["source"] == "paper_auto_on_signal"
    assert trade["auto_created"] is True
    assert trade["risk"]["target_price"] == round(152.0 * 1.4, 2)
    assert trade["risk"]["stop_price"] == round(152.0 * 0.7, 2)
    # Signal advanced and linked
    stored = db.signals.rows[0]
    assert stored["state"] == "ACTIVE"
    assert stored["paper_trade_id"] == trade["id"]


@pytest.mark.asyncio
async def test_auto_trade_refused_without_premium_and_journals_error():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))

    res = await auto_paper_trade_for_signal(db, make_paper_deployment(), sig,
                                            latest_tick_lookup=None)

    assert res["created"] is False
    assert "option_entry_price_unavailable" in res["error"]
    assert len(db.paper_trades.rows) == 0
    assert "option_entry_price_unavailable" in db.signals.rows[0]["paper_trade_error"]
    assert db.signals.rows[0]["state"] == "CONFIRMED"  # untouched, still approvable


@pytest.mark.asyncio
async def test_auto_trade_skips_blocked_and_duplicate_signals():
    db = FakeDB()
    blocked = make_confirmed_signal()
    blocked["blocked"] = True
    res1 = await auto_paper_trade_for_signal(db, make_paper_deployment(), blocked)
    assert res1["created"] is False and res1["reason"] == "signal_blocked"

    dup = make_confirmed_signal()
    dup["paper_trade_id"] = "existing-trade"
    res2 = await auto_paper_trade_for_signal(db, make_paper_deployment(), dup)
    assert res2["created"] is False and res2["reason"] == "paper_trade_already_exists"
    assert len(db.paper_trades.rows) == 0


@pytest.mark.asyncio
async def test_auto_trade_disabled_for_shadow_mode():
    db = FakeDB()
    sig = make_confirmed_signal()
    res = await auto_paper_trade_for_signal(
        db, {"id": "dep-1", "mode": "shadow", "risk": {"auto_paper": True}}, sig)
    assert res["created"] is False and res["reason"] == "auto_paper_disabled"


# ---------- mark_open_deployment_trades -------------------------------------------

@pytest.mark.asyncio
async def test_marker_auto_closes_on_target_and_exits_signal():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    deployment = make_paper_deployment()
    await auto_paper_trade_for_signal(
        db, deployment, sig, latest_tick_lookup={KEY: {"last_price": 100.0}}.get)
    # Give the trade a target so the mark can hit it
    db.paper_trades.rows[0]["risk"]["target_price"] = 120.0

    marked = await mark_open_deployment_trades(
        db, latest_tick_lookup={KEY: {"last_price": 125.0}}.get)

    assert len(marked) == 1 and marked[0]["closed"] is True
    trade = db.paper_trades.rows[0]
    assert trade["status"] == "CLOSED"
    assert trade["exit_reason"] == "target_hit"
    assert trade["realized_pnl"] == round((125.0 - 100.0) * 75, 2)
    assert db.signals.rows[0]["state"] == "EXITED"


@pytest.mark.asyncio
async def test_marker_marks_without_closing_when_inside_levels():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    await auto_paper_trade_for_signal(
        db, make_paper_deployment(), sig, latest_tick_lookup={KEY: {"last_price": 100.0}}.get)

    marked = await mark_open_deployment_trades(
        db, latest_tick_lookup={KEY: {"last_price": 104.0}}.get)

    assert len(marked) == 1 and marked[0]["closed"] is False
    trade = db.paper_trades.rows[0]
    assert trade["status"] == "OPEN"
    assert trade["last_price"] == 104.0
    assert trade["unrealized_pnl"] == round(4.0 * 75, 2)


@pytest.mark.asyncio
async def test_marker_leaves_tickless_trades_untouched():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    await auto_paper_trade_for_signal(
        db, make_paper_deployment(), sig, latest_tick_lookup={KEY: {"last_price": 100.0}}.get)

    marked = await mark_open_deployment_trades(db, latest_tick_lookup={}.get)

    assert marked == []
    assert db.paper_trades.rows[0]["last_price"] == 100.0  # no stale-price marks


@pytest.mark.asyncio
async def test_marker_noop_without_lookup():
    assert await mark_open_deployment_trades(FakeDB(), latest_tick_lookup=None) == []


# ---------- spot-mirror exits (the backtest's spot_exit mode, live) ---------------

from app.paper_auto import (  # noqa: E402
    claim_signal_for_paper_trade,
    compute_spot_exit_levels,
    spot_exit_reason,
)

SPOT_KEY = "NSE_INDEX|Nifty 50"


def test_spot_exit_levels_ce_direction():
    sig = make_confirmed_signal(risk_hints={"spot_target_pts": 30, "spot_stop_pts": 15})
    levels = compute_spot_exit_levels(sig)
    # Long CE: profits when spot rises — target above entry spot, stop below.
    assert levels["instrument_key"] == SPOT_KEY
    assert levels["entry_spot"] == 23950.0
    assert levels["spot_target"] == 23980.0
    assert levels["spot_stop"] == 23935.0


def test_spot_exit_levels_pe_direction():
    sig = make_confirmed_signal(risk_hints={"spot_target_pts": 30, "spot_stop_pts": 15})
    sig["direction"] = "PE"
    levels = compute_spot_exit_levels(sig)
    # Long PE: profits when spot falls — target below entry spot, stop above.
    assert levels["spot_target"] == 23920.0
    assert levels["spot_stop"] == 23965.0


def test_spot_exit_levels_none_without_hints():
    assert compute_spot_exit_levels(make_confirmed_signal()) is None
    assert compute_spot_exit_levels(make_confirmed_signal(risk_hints={})) is None


def test_spot_exit_reason_direction_aware():
    ce = {"direction": "CE", "spot_target": 23980.0, "spot_stop": 23935.0}
    assert spot_exit_reason(ce, 23985.0) == "spot_target_hit"
    assert spot_exit_reason(ce, 23930.0) == "spot_stop_hit"
    assert spot_exit_reason(ce, 23950.0) is None
    pe = {"direction": "PE", "spot_target": 23920.0, "spot_stop": 23965.0}
    assert spot_exit_reason(pe, 23915.0) == "spot_target_hit"
    assert spot_exit_reason(pe, 23970.0) == "spot_stop_hit"
    assert spot_exit_reason(pe, 23950.0) is None


@pytest.mark.asyncio
async def test_auto_trade_carries_spot_exit_from_builtin_style_hints():
    """Builtin strategies provide exits as SPOT POINTS — the trade must carry
    spot-mirror levels so those exits actually fire (review finding 2026-06-11)."""
    db = FakeDB()
    sig = make_confirmed_signal(risk_hints={"spot_target_pts": 30, "spot_stop_pts": 15})
    db.signals.rows.append(dict(sig))

    res = await auto_paper_trade_for_signal(
        db, make_paper_deployment(), sig,
        latest_tick_lookup={KEY: {"last_price": 150.0}}.get)

    assert res["created"] is True
    trade = db.paper_trades.rows[0]
    assert trade["spot_exit"]["spot_target"] == 23980.0
    assert trade["spot_exit"]["spot_stop"] == 23935.0
    # No premium-% hints and no deployment fallback -> no premium levels.
    assert trade["risk"]["target_price"] is None
    assert trade["risk"]["stop_price"] is None


@pytest.mark.asyncio
async def test_marker_closes_on_spot_mirror_target():
    db = FakeDB()
    sig = make_confirmed_signal(risk_hints={"spot_target_pts": 30, "spot_stop_pts": 15})
    db.signals.rows.append(dict(sig))
    await auto_paper_trade_for_signal(
        db, make_paper_deployment(), sig,
        latest_tick_lookup={KEY: {"last_price": 150.0}}.get)

    # Spot rallies through the target; option premium now 171.
    ticks = {KEY: {"last_price": 171.0}, SPOT_KEY: {"last_price": 23985.0}}
    marked = await mark_open_deployment_trades(db, latest_tick_lookup=ticks.get)

    assert marked[0]["closed"] is True
    trade = db.paper_trades.rows[0]
    assert trade["exit_reason"] == "spot_target_hit"
    assert trade["exit_price"] == 171.0  # closed at option PREMIUM, not spot
    assert trade["realized_pnl"] == round((171.0 - 150.0) * 75, 2)
    assert trade["spot_exit"]["hit_spot_price"] == 23985.0
    assert trade["exit_price_stale"] is False  # filled on a fresh option tick
    assert trade["exit_price_source"] == "live_tick"
    assert db.signals.rows[0]["state"] == "EXITED"


@pytest.mark.asyncio
async def test_marker_spot_stop_uses_last_premium_when_option_tick_missing():
    db = FakeDB()
    sig = make_confirmed_signal(risk_hints={"spot_target_pts": 30, "spot_stop_pts": 15})
    db.signals.rows.append(dict(sig))
    await auto_paper_trade_for_signal(
        db, make_paper_deployment(), sig,
        latest_tick_lookup={KEY: {"last_price": 150.0}}.get)

    # Spot breaks the stop but the option tick is absent this minute.
    ticks = {SPOT_KEY: {"last_price": 23930.0}}
    marked = await mark_open_deployment_trades(db, latest_tick_lookup=ticks.get)

    assert marked[0]["closed"] is True
    trade = db.paper_trades.rows[0]
    assert trade["exit_reason"] == "spot_stop_hit"
    assert trade["exit_price"] == 150.0  # last known premium fallback
    # No fresh option tick existed -> the fill is the last mark, flagged stale so
    # the journal shows it is an estimate (not a real fill at the exit minute).
    assert trade["exit_price_stale"] is True
    assert trade["exit_price_source"] == "last_mark"
    assert marked[0]["exit_price_stale"] is True


@pytest.mark.asyncio
async def test_marker_ignores_stale_option_tick():
    """A tick older than the staleness bound is not booked as a fill — the trade
    is left OPEN rather than marked/closed on a minutes-old premium."""
    db = FakeDB()
    sig = make_confirmed_signal(risk_hints={"spot_target_pts": 30, "spot_stop_pts": 15})
    db.signals.rows.append(dict(sig))
    await auto_paper_trade_for_signal(
        db, make_paper_deployment(), sig,
        latest_tick_lookup={KEY: {"last_price": 150.0}}.get)

    stale_ms = now_ms() - 10 * 60_000  # 10 minutes old
    ticks = {KEY: {"last_price": 999.0, "received_ts": stale_ms}}  # stale + far price
    marked = await mark_open_deployment_trades(db, latest_tick_lookup=ticks.get)

    assert marked == []  # nothing fresh to act on
    trade = db.paper_trades.rows[0]
    assert trade["status"] == "OPEN"       # not closed on a stale tick
    assert trade["last_price"] == 150.0    # not marked to the stale price


# ---------- atomic claim (race guard) ----------------------------------------------

@pytest.mark.asyncio
async def test_claim_is_single_winner():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    assert await claim_signal_for_paper_trade(db, sig["id"], "auto_paper") is True
    # Second claimant (e.g. the manual approve route) must lose.
    assert await claim_signal_for_paper_trade(db, sig["id"], "manual_approval") is False
    assert db.signals.rows[0]["paper_trade_claim"]["source"] == "auto_paper"


@pytest.mark.asyncio
async def test_auto_trade_refuses_claimed_signal():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    await claim_signal_for_paper_trade(db, sig["id"], "manual_approval")

    res = await auto_paper_trade_for_signal(
        db, make_paper_deployment(), sig,
        latest_tick_lookup={KEY: {"last_price": 150.0}}.get)

    assert res["created"] is False
    assert res["reason"] == "signal_claimed_elsewhere"
    assert len(db.paper_trades.rows) == 0


@pytest.mark.asyncio
async def test_failed_premium_resolution_releases_claim():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))

    res = await auto_paper_trade_for_signal(db, make_paper_deployment(), sig,
                                            latest_tick_lookup=None)

    assert res["created"] is False
    # The claim must be released so a later attempt (or approval) can retry.
    assert "paper_trade_claim" not in db.signals.rows[0]
    retry_ok = await claim_signal_for_paper_trade(db, sig["id"], "manual_approval")
    assert retry_ok is True
