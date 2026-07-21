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


def test_select_contract_requires_exact_strike_no_fallback():
    """Regression pin (2026-06-12 audit): selection must EXACT-match the target
    strike or return None. A 'nearest available' fallback would let a backtest
    or live signal silently trade a far strike when the true ATM contract is
    missing from metadata — refusal is the correct behavior."""
    from app.options_universe import select_contract_for_signal

    contracts = [
        # The true ATM (23550) is deliberately absent; nearest available is
        # 400 points away.
        {"underlying": "NIFTY", "side": "CE", "strike": 23950.0,
         "instrument_key": "NSE_FO|x1", "expiry_date": "2026-05-26"},
        {"underlying": "NIFTY", "side": "CE", "strike": 23150.0,
         "instrument_key": "NSE_FO|x2", "expiry_date": "2026-05-26"},
    ]
    picked = select_contract_for_signal(
        contracts=contracts, underlying="NIFTY",
        spot_price=23535.4, direction="CE", moneyness="atm",
    )
    assert picked is None  # no silent far-strike substitution, ever


def test_canonical_instrument_key_strips_only_dated_suffix():
    from app.instruments import canonical_instrument_key as c
    assert c("NSE_FO|72171|26-05-2026") == "NSE_FO|72171"
    assert c("NSE_FO|72171|2026-05-26") == "NSE_FO|72171"
    assert c("BSE_FO|823065|05-02-2026") == "BSE_FO|823065"
    assert c("NSE_FO|72171") == "NSE_FO|72171"
    assert c("NSE_INDEX|Nifty 50") == "NSE_INDEX|Nifty 50"
    assert c("") == ""
    assert c(None) == ""


def test_contract_identity_key_keeps_reused_token_expiries_distinct():
    from app.instruments import contract_identity_key as c
    assert c("NSE_FO|52526", "2025-01-02") == "NSE_FO|52526|2025-01-02"
    assert c("NSE_FO|52526", "2026-03-30") == "NSE_FO|52526|2026-03-30"
    assert c("NSE_FO|52526|30-03-2026") == "NSE_FO|52526|2026-03-30"
    assert c("NSE_FO|52526") == "NSE_FO|52526"
