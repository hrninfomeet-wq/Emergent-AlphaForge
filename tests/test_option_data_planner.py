import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.option_data_planner import build_option_warehouse_plan  # noqa: E402


def _spot(ts: str, close: float):
    dt = pd.Timestamp(ts, tz="Asia/Kolkata")
    return {
        "ts": int(dt.tz_convert("UTC").value // 10**6),
        "datetime": dt.isoformat(),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
    }


def _contract(key: str, expiry: str, strike: int, side: str):
    return {
        "instrument_key": key,
        "underlying": "NIFTY",
        "expiry_date": expiry,
        "strike": float(strike),
        "side": side,
        "trading_symbol": f"NIFTY{expiry}{strike}{side}",
        "lot_size": 65,
    }


def test_plan_resolves_next_available_expiry_for_each_spot_date():
    spot = pd.DataFrame([
        _spot("2026-05-22 09:15", 23680),
        _spot("2026-05-27 09:15", 23790),
    ])
    contracts = [
        _contract("may_23700_ce", "2026-05-26", 23700, "CE"),
        _contract("jun_23800_ce", "2026-06-02", 23800, "CE"),
    ]

    result = build_option_warehouse_plan(
        spot_candles=spot,
        contracts=contracts,
        underlying="NIFTY",
        moneyness=["atm"],
        legs=["CE"],
        sample_interval_minutes=1,
    )

    assert result["summary"]["spot_candles_used"] == 2
    assert result["summary"]["planned_contracts"] == 2
    assert result["summary"]["missing_contract_count"] == 0
    assert {item["instrument_key"] for item in result["items"]} == {"may_23700_ce", "jun_23800_ce"}
    assert result["summary"]["expiries_used"] == ["2026-05-26", "2026-06-02"]


def test_plan_selects_multiple_moneyness_values_and_legs_without_duplicates():
    spot = pd.DataFrame([
        _spot("2026-05-22 09:15", 23720),
        _spot("2026-05-22 09:16", 23722),
    ])
    contracts = [
        _contract("atm_ce", "2026-05-26", 23700, "CE"),
        _contract("atm_pe", "2026-05-26", 23700, "PE"),
        _contract("otm1_ce", "2026-05-26", 23750, "CE"),
        _contract("otm1_pe", "2026-05-26", 23650, "PE"),
    ]

    result = build_option_warehouse_plan(
        spot_candles=spot,
        contracts=contracts,
        underlying="NIFTY",
        moneyness=["atm", "otm1"],
        legs=["CE", "PE"],
        sample_interval_minutes=1,
    )

    assert result["summary"]["spot_candles_used"] == 2
    assert result["summary"]["planned_contracts"] == 4
    assert result["summary"]["selection_count"] == 8
    assert {item["selected_as"] for item in result["items"]} == {"atm CE", "atm PE", "otm1 CE", "otm1 PE"}


def test_plan_reports_missing_contracts_for_requested_leg_or_moneyness():
    spot = pd.DataFrame([_spot("2026-05-22 09:15", 23720)])
    contracts = [_contract("atm_ce", "2026-05-26", 23700, "CE")]

    result = build_option_warehouse_plan(
        spot_candles=spot,
        contracts=contracts,
        underlying="NIFTY",
        moneyness=["atm"],
        legs=["CE", "PE"],
        sample_interval_minutes=1,
    )

    assert result["summary"]["planned_contracts"] == 1
    assert result["summary"]["missing_contract_count"] == 1
    assert result["missing"][0]["side"] == "PE"
    assert result["missing"][0]["expiry_date"] == "2026-05-26"


def test_plan_samples_spot_candles_by_requested_interval():
    spot = pd.DataFrame([
        _spot("2026-05-22 09:15", 23720),
        _spot("2026-05-22 09:16", 23722),
        _spot("2026-05-22 09:30", 23724),
    ])
    contracts = [_contract("atm_ce", "2026-05-26", 23700, "CE")]

    result = build_option_warehouse_plan(
        spot_candles=spot,
        contracts=contracts,
        underlying="NIFTY",
        moneyness=["atm"],
        legs=["CE"],
        sample_interval_minutes=15,
    )

    assert result["summary"]["spot_candles_seen"] == 3
    assert result["summary"]["spot_candles_used"] == 2
    assert result["items"][0]["selection_count"] == 2
