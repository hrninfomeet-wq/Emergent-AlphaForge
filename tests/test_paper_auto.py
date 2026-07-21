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

import asyncio
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
    build_auto_trade,
    compute_auto_risk_levels,
    mark_open_deployment_trades,
    resolve_deployment_lots,
    resolve_option_entry_quote,
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


@pytest.mark.asyncio
async def test_entry_quote_retains_full_point_in_time_surface():
    tick = {
        "last_price": 100.25, "received_ts": now_ms(), "ts": now_ms(),
        "source": "upstox_ws_v3", "mode": "full",
        "best_bid_price": 100.0, "best_bid_quantity": 130,
        "best_ask_price": 100.5, "best_ask_quantity": 195,
        "open_interest": 250000, "implied_volatility": 0.18,
    }
    quote = await resolve_option_entry_quote(
        FakeDB(), KEY, latest_tick_lookup={KEY: tick}.get)
    assert quote["source"] == "live_tick"
    assert quote["market_data"]["point_in_time_surface_complete"] is True
    assert quote["market_data"]["best_ask_price"] == 100.5


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


# ---------- resolve_deployment_lots -----------------------------------------------

def test_resolve_lots_premium_at_risk_matches_size_position():
    from app.portfolio import SizingConfig, size_position
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 5.0, "max_lots": 10}
    risk_cfg = {"sizing": {"sizing_config": sizing, "lots": 1}}
    # budget 200000*5% = 10000; entry 100, stop 70 -> risk/unit 30;
    # lot_size 75 -> per-lot 2250 -> floor(10000/2250) = 4 lots.
    lots, audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, 70.0)
    expected = size_position(entry_premium=100.0, lot_size=75, stop_level=70.0,
                             cfg=SizingConfig.from_dict(sizing))
    assert lots == 4
    assert lots == int(expected["lots"])
    assert audit["sizing_mode"] == "premium_at_risk"
    assert audit["risk_exceeded"] is False


def test_resolve_lots_adapts_to_contract_lot_size():
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 1.0, "max_lots": 50}
    risk_cfg = {"sizing": {"sizing_config": sizing, "lots": 1}}
    # budget 2000; risk/unit 30.
    # NIFTY lot 75 -> per-lot 2250 -> floor 0 -> min 1 lot (risk_exceeded).
    nifty_lots, nifty_audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, 70.0)
    # BANKNIFTY lot 15 -> per-lot 450 -> floor(2000/450) = 4 lots.
    bn_lots, _ = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 15}, 70.0)
    assert nifty_lots == 1
    assert nifty_audit["risk_exceeded"] is True
    assert bn_lots == 4


def test_resolve_lots_fixed_lots_pin():
    risk_cfg = {"sizing": {"sizing_config": {"enabled": False, "mode": "fixed_lots"}, "lots": 3}}
    lots, audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, None)
    assert lots == 3
    assert audit["sizing_mode"] == "fixed_lots"


def test_resolve_lots_legacy_fallback_to_default_lots():
    risk_cfg = {"default_lots": 2}
    lots, audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, None)
    assert lots == 2
    assert audit["sizing_mode"] == "fixed_lots_legacy"


def test_resolve_lots_pin_without_sizing_config_uses_pin_lots():
    # A malformed pin (sizing present but sizing_config dropped) must honour the
    # pin's own lots, not silently fall back to default_lots.
    risk_cfg = {"sizing": {"lots": 2}}
    lots, audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, None)
    assert lots == 2
    assert audit["sizing_mode"] == "fixed_lots"


def test_resolve_lots_caps_at_max_lots():
    # Budget would buy ~44 lots, but max_lots clamps to 2.
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 50.0, "max_lots": 2}
    risk_cfg = {"sizing": {"sizing_config": sizing, "lots": 1}}
    lots, audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, 70.0)
    assert lots == 2
    assert audit["sizing_mode"] == "premium_at_risk"


def test_resolve_lots_premium_at_risk_no_stop_uses_assumed_pct():
    from app.portfolio import SizingConfig, size_position
    # No premium stop -> size_position uses assumed_stop_pct_of_premium (default 50%).
    # entry 100 -> risk/unit 50; budget 200000*10% = 20000; lot 75 -> per-lot 3750 -> 5 lots.
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 10.0, "max_lots": 50}
    risk_cfg = {"sizing": {"sizing_config": sizing, "lots": 1}}
    lots, audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, None)
    expected = size_position(entry_premium=100.0, lot_size=75, stop_level=None,
                             cfg=SizingConfig.from_dict(sizing))
    assert lots == 5
    assert lots == int(expected["lots"])
    assert audit["sizing_mode"] == "premium_at_risk"


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


@pytest.mark.asyncio
async def test_auto_trade_snapshot_carries_sizing_audit():
    db = FakeDB()
    sig = make_confirmed_signal()  # default contract lot_size 75
    db.signals.rows.append(dict(sig))
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 1.0, "max_lots": 10}
    deployment = make_paper_deployment(sizing={"sizing_config": sizing, "lots": 1})

    res = await auto_paper_trade_for_signal(
        db, deployment, sig, latest_tick_lookup={KEY: {"last_price": 100.0}}.get)

    assert res["created"] is True
    trade = db.paper_trades.rows[0]
    assert trade["sizing_mode"] == "premium_at_risk"
    # entry 100, no premium stop -> assumed 50% -> risk/unit 50; lot 75 -> per-lot
    # 3750; budget 2000 -> floor 0 -> 1 lot, risk_exceeded.
    snap = db.signals.rows[0]["auto_paper"]
    assert snap["sizing_mode"] == "premium_at_risk"
    assert snap["risk_exceeded"] is True
    assert snap["risk_per_unit"] == 50.0
    assert "risk_amount" in snap


# ---------- build_auto_trade sizing integration -----------------------------------

def test_build_auto_trade_replays_premium_at_risk_policy():
    sig = make_confirmed_signal(lot_size=15)  # BANKNIFTY-like contract lot
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 1.0, "max_lots": 50}
    deployment = make_paper_deployment(
        sizing={"sizing_config": sizing, "lots": 1},
        auto_paper_stop_pct=30,  # premium stop 30% below entry -> stop 70 on entry 100
    )
    trade = build_auto_trade(sig, deployment, entry_price=100.0)
    # entry 100, stop 70 -> risk/unit 30; budget 2000; per-lot 30*15=450 -> 4 lots
    assert trade["lots"] == 4
    assert trade["quantity"] == 4 * 15
    assert trade["sizing_mode"] == "premium_at_risk"
    assert trade["risk"]["stop_price"] == 70.0


def test_build_auto_trade_legacy_uses_default_lots():
    sig = make_confirmed_signal(lot_size=75)
    deployment = make_paper_deployment(default_lots=2)  # no risk.sizing pinned
    trade = build_auto_trade(sig, deployment, entry_price=120.0)
    assert trade["lots"] == 2
    assert trade["sizing_mode"] == "fixed_lots_legacy"


def test_build_auto_trade_tags_risk_exceeded_on_trade_doc():
    # Spec edge case: when one lot exceeds the risk budget, still trade one lot
    # and tag risk_exceeded=True ON THE TRADE DOC (not just the helper audit).
    sig = make_confirmed_signal(lot_size=75)  # NIFTY lot — one lot blows the budget
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 1.0, "max_lots": 50}
    deployment = make_paper_deployment(
        sizing={"sizing_config": sizing, "lots": 1}, auto_paper_stop_pct=30)
    trade = build_auto_trade(sig, deployment, entry_price=100.0)
    # budget 2000; risk/unit 30; per-lot 30*75=2250 -> floor 0 -> 1 lot, exceeded
    assert trade["lots"] == 1
    assert trade["risk_exceeded"] is True
    assert trade["sizing_mode"] == "premium_at_risk"


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
async def test_marker_computes_top_of_book_execution_pnl_for_complete_surface():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    entry_tick = {
        "last_price": 100.0, "received_ts": now_ms(), "ts": now_ms(),
        "source": "upstox_ws_v3", "mode": "full",
        "best_bid_price": 99.5, "best_bid_quantity": 200,
        "best_ask_price": 100.5, "best_ask_quantity": 200,
    }
    await auto_paper_trade_for_signal(
        db, make_paper_deployment(), sig,
        latest_tick_lookup={KEY: entry_tick}.get)
    db.paper_trades.rows[0]["risk"]["target_price"] = 104.0
    exit_tick = {
        "last_price": 105.0, "received_ts": now_ms(), "ts": now_ms(),
        "source": "upstox_ws_v3", "mode": "full",
        "best_bid_price": 104.5, "best_bid_quantity": 200,
        "best_ask_price": 105.0, "best_ask_quantity": 200,
    }

    await mark_open_deployment_trades(
        db, latest_tick_lookup={KEY: exit_tick}.get)

    trade = db.paper_trades.rows[0]
    assert trade["execution_evidence"]["point_in_time_surface_complete"] is True
    assert trade["execution_evidence"]["entry_best_ask"] == 100.5
    assert trade["execution_evidence"]["exit_best_bid"] == 104.5
    assert trade["execution_realized_pnl"] < (104.5 - 100.5) * 75


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


# ---------- live trail ratchet (Task 10) -------------------------------------------

class _RatchetCursor:
    def __init__(self, docs): self._docs = docs
    async def to_list(self, length=None): return [d for d in self._docs if d.get("status") == "OPEN"]


class _RatchetTrades:
    def __init__(self, docs): self.docs = docs; self.replaced = []
    def find(self, *a, **k): return _RatchetCursor(self.docs)
    async def replace_one(self, flt, doc, upsert=False):
        self.replaced.append(doc)
        for i, d in enumerate(self.docs):
            if d.get("id") == flt.get("id"):
                self.docs[i] = doc            # next mark cycle sees the update
        class R: matched_count = 1
        return R()


class _RatchetDB:
    def __init__(self, trades): self.paper_trades = _RatchetTrades(trades)
    @property
    def signals(self):
        class _S:
            async def find_one(self, *a, **k): return None
            async def replace_one(self, *a, **k): return None
        return _S()


def test_live_trail_ratchets_stop_up_over_two_marks():
    # Prior-running-max design (parity with the sim): the tick that sets a new peak
    # does NOT stop itself; the trail ratchets on the NEXT cycle off the prior max.
    trade = {"id": "t1", "status": "OPEN", "instrument_key": "OPT|1",
             "entry_price": 100.0, "quantity": 75, "running_max_premium": 100.0,
             "risk": {"stop_price": 80.0, "target_price": None},
             "exit_controls": {"enabled": True, "unit": "pct",
                               "trailing": {"activation": 0.10, "distance": 0.25}}}
    db = _RatchetDB([trade])
    # Cycle 1: tick 200. eff uses PRIOR running_max=100 -> activation 110 not reached ->
    #          stop stays 80; running_max advances to 200.
    ticks = {"OPT|1": {"last_price": 200.0}}
    asyncio.run(mark_open_deployment_trades(db, latest_tick_lookup=lambda k: ticks.get(k)))
    after1 = db.paper_trades.docs[0]
    assert after1["running_max_premium"] == 200.0
    assert after1["risk"]["stop_price"] == 80.0
    assert after1["status"] == "OPEN"
    # Cycle 2: tick 160. eff uses PRIOR running_max=200 -> trail = 200*0.75 = 150 (raised);
    #          160 > 150 so no close.
    ticks = {"OPT|1": {"last_price": 160.0}}
    asyncio.run(mark_open_deployment_trades(db, latest_tick_lookup=lambda k: ticks.get(k)))
    after2 = db.paper_trades.docs[0]
    assert after2["risk"]["stop_price"] == 150.0
    assert after2["status"] == "OPEN"
    # Cycle 3: tick 100 < trailed stop 150 -> the trade CLOSES at the ratcheted stop.
    ticks = {"OPT|1": {"last_price": 100.0}}
    asyncio.run(mark_open_deployment_trades(db, latest_tick_lookup=lambda k: ticks.get(k)))
    after3 = db.paper_trades.docs[0]
    assert after3["status"] == "CLOSED"
    assert after3.get("exit_reason")            # a premium-stop reason was set on close
