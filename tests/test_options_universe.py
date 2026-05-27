import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import options_universe  # noqa: E402


def test_round_to_step_uses_nearest_strike_step():
    assert options_universe.round_to_step(25974, 50) == 25950
    assert options_universe.round_to_step(25976, 50) == 26000
    assert options_universe.round_to_step(81751, 100) == 81800


def test_strike_offset_for_moneyness_matches_reference_semantics():
    assert options_universe.strike_offset_for_moneyness("atm", "CE") == 0
    assert options_universe.strike_offset_for_moneyness("otm2", "CE") == 2
    assert options_universe.strike_offset_for_moneyness("itm1", "CE") == -1
    assert options_universe.strike_offset_for_moneyness("otm2", "PE") == -2
    assert options_universe.strike_offset_for_moneyness("itm1", "PE") == 1


def test_select_contract_for_signal_uses_spot_atm_moneyness_and_side():
    contracts = [
        {"instrument_key": "ce-atm", "side": "CE", "strike": 26000, "trading_symbol": "NIFTY ATM CE"},
        {"instrument_key": "ce-otm1", "side": "CE", "strike": 26050, "trading_symbol": "NIFTY OTM1 CE"},
        {"instrument_key": "pe-otm1", "side": "PE", "strike": 25950, "trading_symbol": "NIFTY OTM1 PE"},
    ]

    ce = options_universe.select_contract_for_signal(
        contracts=contracts,
        underlying="NIFTY",
        spot_price=26012,
        direction="CE",
        moneyness="otm1",
    )
    pe = options_universe.select_contract_for_signal(
        contracts=contracts,
        underlying="NIFTY",
        spot_price=26012,
        direction="PE",
        moneyness="otm1",
    )

    assert ce["instrument_key"] == "ce-otm1"
    assert ce["atm"] == 26000
    assert ce["moneyness"] == "otm1"
    assert pe["instrument_key"] == "pe-otm1"


def test_select_atm_band_keeps_ce_pe_contracts_around_atm():
    contracts = [
        {"instrument_key": "far-pe", "side": "PE", "strike": 25600},
        {"instrument_key": "atm-ce", "side": "CE", "strike": 26000},
        {"instrument_key": "otm-ce", "side": "CE", "strike": 26100},
        {"instrument_key": "otm-pe", "side": "PE", "strike": 25900},
        {"instrument_key": "far-ce", "side": "CE", "strike": 26400},
    ]

    selected = options_universe.select_atm_band(
        contracts=contracts,
        underlying="NIFTY",
        spot_price=26010,
        radius=2,
    )

    assert [c["instrument_key"] for c in selected] == ["atm-ce", "otm-pe", "otm-ce"]
    assert selected[0]["moneyness"] == "ATM"
    assert selected[1]["rank"] == 2
