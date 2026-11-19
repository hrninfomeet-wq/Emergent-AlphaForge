"""Date-aware statutory defaults; no broker, DB or startup imports."""
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.option_costs import CostConfig, round_trip_charges


def _charges(cfg=None, **kw):
    return round_trip_charges(entry_premium=100, exit_premium=120, quantity=75,
                              cfg=cfg or CostConfig(enabled=True), **kw)


def test_missing_date_uses_current_schedule():
    # 120 * 75 = 9,000 sell turnover; 0.15% = 13.50 rupees.
    assert _charges()["stt"] == 13.50


@pytest.mark.parametrize("exit_ts,expected", [
    ("2026-03-31", 9.00),
    ("2026-04-01", 13.50),
    ("2026-03-31T18:29:59+00:00", 9.00),
    ("2026-03-31T18:30:00+00:00", 13.50),
    (datetime(2026, 3, 31, 18, 30, tzinfo=timezone.utc), 13.50),
    (int(datetime(2026, 3, 31, 18, 30, tzinfo=timezone.utc).timestamp() * 1000), 13.50),
])
def test_sell_date_uses_ist_boundary(exit_ts, expected):
    assert _charges(exit_ts=exit_ts)["stt"] == expected


@pytest.mark.parametrize("rate", [0.001, 0.000625, 0.0])
def test_explicit_saved_rate_survives_roundtrip_and_date(rate):
    cfg = CostConfig.from_dict({"enabled": True, "stt_sell_rate": rate})
    reloaded = CostConfig.from_dict(cfg.to_dict())
    assert reloaded.stt_sell_rate == rate
    assert _charges(reloaded, exit_ts="2026-04-01")["stt"] == round(9000 * rate, 2)


def test_automatic_config_remains_automatic_after_serialization():
    cfg = CostConfig.from_dict({"enabled": True})
    assert cfg.to_dict()["stt_sell_rate"] is None
    reloaded = CostConfig.from_dict(cfg.to_dict())
    assert _charges(reloaded, exit_ts="2026-03-31")["stt"] == 9.0
    assert _charges(reloaded, exit_ts="2026-04-01")["stt"] == 13.5


def test_invalid_date_is_not_silently_replaced_by_today():
    with pytest.raises(ValueError):
        _charges(exit_ts="not-a-date")


def test_statutory_change_only_changes_stt_and_total():
    before = _charges(exit_ts="2026-03-31")
    after = _charges(exit_ts="2026-04-01")
    assert after["stt"] - before["stt"] == 4.5
    assert after["total_charges"] - before["total_charges"] == pytest.approx(4.5)
    assert {k: v for k, v in before.items() if k not in ("stt", "total_charges")} == {
        k: v for k, v in after.items() if k not in ("stt", "total_charges")}


@pytest.mark.parametrize("day,expected", [("2026-03-31", 9.0), ("2026-04-01", 13.5)])
def test_paired_backtest_prices_costs_at_exit_date(day, expected):
    import pandas as pd
    from app.option_backtest import simulate_paired_option_trades
    start = int(datetime.fromisoformat(day + "T04:00:00+00:00").timestamp() * 1000)
    result = simulate_paired_option_trades(
        spot_trades=[{"direction": "CE", "entry_ts": start, "exit_ts": start + 60000,
                      "entry_price": 24000, "exit_price": 24050}],
        contracts=[{"instrument_key": "ce", "side": "CE", "strike": 24000, "lot_size": 75}],
        option_candles=pd.DataFrame([
            {"instrument_key": "ce", "ts": start, "close": 100, "high": 101, "low": 99},
            {"instrument_key": "ce", "ts": start + 60000, "close": 120, "high": 121, "low": 119}]),
        underlying="NIFTY", moneyness="atm", slippage_config={"atm_pts": 0},
        cost_config={"enabled": True})
    assert result["trades"][0]["charges"]["stt"] == expected


def test_premium_overlay_uses_trade_exit_date():
    from app.premium_momentum import apply_costs_to_trade
    trade = {"entry_premium": 100, "exit_premium": 120, "exit_ts": "2026-03-31"}
    result = apply_costs_to_trade(trade, cost_cfg=CostConfig(enabled=True), lot_size=75, lots=1)
    assert result["charges_rupees"] == _charges(exit_ts="2026-03-31")["total_charges"]


@pytest.mark.parametrize("enabled", [False, True])
def test_paper_friction_uses_exit_date_without_changing_gross_policy(enabled):
    from app.live_friction import FrictionConfig, close_economics
    ts = int(datetime(2026, 3, 31, 10, tzinfo=timezone.utc).timestamp() * 1000)
    result = close_economics(raw_exit_premium=120, entry_price=100, raw_entry_price=100,
                            quantity=75, friction=FrictionConfig.from_dict({
                                "enabled": enabled, "slippage": {"atm_pts": 0},
                                "costs": {"enabled": True}}), ts_ms=ts)
    assert result["charges"]["stt"] == 9.0
    if not enabled:
        assert result["realized_pnl"] == 1500.0


def test_live_realized_fields_reuses_recorded_close_date():
    from app.live.close_loop import realized_fields
    result = realized_fields({"quantity": 75, "entry_price": 100,
                              "closed_at": "2026-03-31T10:00:00+00:00"}, 120)
    assert result["charges"]["stt"] == 9.0
    assert result["realized_pnl"] == 1500.0


def test_live_close_passes_close_timestamp_to_statutory_calculation():
    import asyncio
    from app.live.close_loop import close_live_trade
    from tests.test_live_charges_journaled import _db
    db = _db({"norenordno": "test", "quantity": 75, "entry_price": 100})
    asyncio.run(close_live_trade(db, norenordno="test", exit_price=120,
                                exit_reason="test", now_iso="2026-03-31T10:00:00+00:00"))
    assert db.live_trades.updated["charges"]["stt"] == 9.0
    assert db.live_trades.updated["realized_pnl"] == 1500.0


def test_paper_touch_evidence_uses_closed_date_without_startup_imports():
    import ast
    from typing import Any, Dict, Optional
    source = Path("backend/app/paper_auto.py").read_text(encoding="utf-8")
    names = {"_positive_float", "_attach_execution_surface_evidence"}
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"Any": Any, "Dict": Dict, "Optional": Optional}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "paper_auto.py:pure-helper", "exec"), ns)
    opened = {"quantity": 75, "entry_market_data": {
        "point_in_time_surface_complete": True, "best_ask_price": 100, "best_ask_quantity": 75}}
    closed = {"closed_at": "2026-03-31T10:00:00+00:00"}
    ns["_attach_execution_surface_evidence"](opened, closed, {
        "point_in_time_surface_complete": True, "best_bid_price": 120, "best_bid_quantity": 75})
    assert closed["execution_evidence"]["round_trip_charges"] == _charges(
        exit_ts="2026-03-31")["total_charges"]
