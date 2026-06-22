"""Tests for the Upstox->Noren symbol resolver (fail-closed).

All tests use a fake search_fn; no network.

Real Flattrade SearchScrip field names (verified live):
    tsym      trading symbol             e.g. "NIFTY23JUN26C25000"
    token     instrument token           e.g. "56432"
    ls        lot size (string)          e.g. "65"
    symname   symbol name                e.g. "NIFTY", "BANKNIFTY", "BSXOPT"
    optt      option type                "CE" or "PE"
    exd       expiry DD-MON-YYYY (UPPER) e.g. "23-JUN-2026"
    dname     display name               e.g. "NIFTY 23JUN26 25000 CE "

NOTE: There is NO ``strprc`` field. Strike is parsed from ``dname``.
SENSEX symname is "BSXOPT" (not "SENSEX").
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest

from app.live.flattrade_symbol import (
    LOT_SIZE_EXPECTED,
    SymbolResolutionError,
    resolve,
    _parse_exd,
    _strike_from_dname,
    _contract_expiry_iso,
    _normalise_tick,
)


# ---------------------------------------------------------------------------
# Real-fixture scrip rows (verified live from Flattrade SearchScrip)
# ---------------------------------------------------------------------------

# NIFTY NFO real shape
REAL_NIFTY_CE_25000 = {
    "symname": "NIFTY",
    "optt": "CE",
    "exd": "23-JUN-2026",
    "ls": "65",
    "token": "56432",
    "tsym": "NIFTY23JUN26C25000",
    "instname": "OPTIDX",
    "dname": "NIFTY 23JUN26 25000 CE ",
}

# BANKNIFTY NFO real shape
REAL_BANKNIFTY_CE_52000 = {
    "symname": "BANKNIFTY",
    "optt": "CE",
    "exd": "30-JUN-2026",
    "ls": "30",
    "token": "75446",
    "tsym": "BANKNIFTY30JUN26C52000",
    "instname": "OPTIDX",
    "dname": "BANKNIFTY 30JUN26 52000 CE ",
}

# SENSEX BFO real shape (symname = BSXOPT, NOT SENSEX)
REAL_SENSEX_CE_80000 = {
    "symname": "BSXOPT",
    "optt": "CE",
    "exd": "25-JUN-2026",
    "ls": "20",
    "token": "880601",
    "tsym": "SENSEX26JUN80000CE",
    "instname": "OPTIDX",
    "dname": "SENSEX 25 JUN 80000 CE",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_contract(
    underlying="NIFTY",
    strike=25000.0,
    side="CE",
    expiry_date="2026-06-23",
    lot_size=65,
) -> dict:
    return {
        "underlying": underlying,
        "strike": strike,
        "side": side,
        "expiry_date": expiry_date,
        "lot_size": lot_size,
        "trading_symbol": f"NSE_FO|{underlying}{side}",
        "instrument_key": f"NSE_FO|43215",
    }


def make_nifty_scrip(
    tsym="NIFTY23JUN26C25000",
    token="56432",
    ls="65",
    symname="NIFTY",
    optt="CE",
    exd="23-JUN-2026",
    dname="NIFTY 23JUN26 25000 CE ",
) -> dict:
    return {
        "tsym": tsym,
        "token": token,
        "ls": ls,
        "symname": symname,
        "optt": optt,
        "exd": exd,
        "dname": dname,
        "instname": "OPTIDX",
    }


def fake_search(rows):
    """Return a search_fn that ignores the query and returns `rows`."""
    def search_fn(exch, text):
        return rows
    return search_fn


# ---------------------------------------------------------------------------
# _parse_exd (replaces old _parse_noren_expiry — now uppercase months)
# ---------------------------------------------------------------------------

def test_parse_exd_nifty():
    assert _parse_exd("23-JUN-2026") == "2026-06-23"


def test_parse_exd_banknifty():
    assert _parse_exd("30-JUN-2026") == "2026-06-30"


def test_parse_exd_sensex():
    assert _parse_exd("25-JUN-2026") == "2026-06-25"


def test_parse_exd_all_months():
    assert _parse_exd("01-JAN-2026") == "2026-01-01"
    assert _parse_exd("28-FEB-2026") == "2026-02-28"
    assert _parse_exd("31-DEC-2026") == "2026-12-31"
    assert _parse_exd("15-MAR-2026") == "2026-03-15"
    assert _parse_exd("10-SEP-2026") == "2026-09-10"


def test_parse_exd_bad_format_raises():
    with pytest.raises(SymbolResolutionError):
        _parse_exd("2026-06-23")   # ISO, not Flattrade format


def test_parse_exd_bad_month_raises():
    with pytest.raises(SymbolResolutionError):
        _parse_exd("23-XXX-2026")


def test_parse_exd_lowercase_month_still_works():
    # _parse_exd normalises via .upper(), so mixed-case also parses
    assert _parse_exd("23-Jun-2026") == "2026-06-23"


# ---------------------------------------------------------------------------
# _strike_from_dname
# ---------------------------------------------------------------------------

def test_strike_from_dname_nifty():
    assert _strike_from_dname("NIFTY 23JUN26 25000 CE ") == 25000.0


def test_strike_from_dname_banknifty():
    assert _strike_from_dname("BANKNIFTY 30JUN26 52000 CE ") == 52000.0


def test_strike_from_dname_sensex():
    # BFO format: "SENSEX 25 JUN 80000 CE" — more tokens but still works
    assert _strike_from_dname("SENSEX 25 JUN 80000 CE") == 80000.0


def test_strike_from_dname_pe():
    assert _strike_from_dname("NIFTY 23JUN26 25000 PE ") == 25000.0


def test_strike_from_dname_bad_last_token_raises():
    with pytest.raises(SymbolResolutionError):
        _strike_from_dname("NIFTY 23JUN26 25000 CALL")


def test_strike_from_dname_non_numeric_strike_raises():
    with pytest.raises(SymbolResolutionError):
        _strike_from_dname("NIFTY 23JUN26 ATM CE")


def test_strike_from_dname_too_few_tokens_raises():
    with pytest.raises(SymbolResolutionError):
        _strike_from_dname("CE")


# ---------------------------------------------------------------------------
# _contract_expiry_iso
# ---------------------------------------------------------------------------

def test_contract_expiry_iso_string():
    assert _contract_expiry_iso("2026-06-23") == "2026-06-23"


def test_contract_expiry_iso_date_object():
    import datetime
    assert _contract_expiry_iso(datetime.date(2026, 6, 23)) == "2026-06-23"


def test_contract_expiry_iso_blank_raises():
    with pytest.raises(SymbolResolutionError):
        _contract_expiry_iso("")


def test_contract_expiry_iso_bad_format_raises():
    with pytest.raises(SymbolResolutionError):
        _contract_expiry_iso("23-JUN-2026")  # Flattrade format, not ISO


# ---------------------------------------------------------------------------
# Exchange routing (real fixtures)
# ---------------------------------------------------------------------------

def test_nifty_routes_to_nfo():
    result = resolve(
        make_contract(underlying="NIFTY", strike=25000.0, expiry_date="2026-06-23", lot_size=65),
        search_fn=fake_search([REAL_NIFTY_CE_25000]),
    )
    assert result["exch"] == "NFO"


def test_banknifty_routes_to_nfo():
    result = resolve(
        make_contract(underlying="BANKNIFTY", strike=52000.0, side="CE",
                      expiry_date="2026-06-30", lot_size=30),
        search_fn=fake_search([REAL_BANKNIFTY_CE_52000]),
    )
    assert result["exch"] == "NFO"


def test_sensex_routes_to_bfo():
    result = resolve(
        make_contract(underlying="SENSEX", strike=80000.0, side="CE",
                      expiry_date="2026-06-25", lot_size=20),
        search_fn=fake_search([REAL_SENSEX_CE_80000]),
    )
    assert result["exch"] == "BFO"


# ---------------------------------------------------------------------------
# Exact match — real fixtures confirm correct tsym/token
# ---------------------------------------------------------------------------

def test_exact_match_nifty_returns_correct_tsym_and_token():
    result = resolve(
        make_contract(underlying="NIFTY", strike=25000.0, side="CE",
                      expiry_date="2026-06-23", lot_size=65),
        search_fn=fake_search([REAL_NIFTY_CE_25000]),
    )
    assert result["tsym"] == "NIFTY23JUN26C25000"
    assert result["token"] == "56432"
    assert result["lot_size"] == 65


def test_exact_match_banknifty_returns_correct_tsym_and_token():
    result = resolve(
        make_contract(underlying="BANKNIFTY", strike=52000.0, side="CE",
                      expiry_date="2026-06-30", lot_size=30),
        search_fn=fake_search([REAL_BANKNIFTY_CE_52000]),
    )
    assert result["tsym"] == "BANKNIFTY30JUN26C52000"
    assert result["token"] == "75446"
    assert result["lot_size"] == 30


def test_exact_match_sensex_returns_correct_tsym_and_token():
    result = resolve(
        make_contract(underlying="SENSEX", strike=80000.0, side="CE",
                      expiry_date="2026-06-25", lot_size=20),
        search_fn=fake_search([REAL_SENSEX_CE_80000]),
    )
    assert result["tsym"] == "SENSEX26JUN80000CE"
    assert result["token"] == "880601"
    assert result["lot_size"] == 20


def test_exact_match_pe_side():
    row = make_nifty_scrip(
        tsym="NIFTY23JUN26P25000",
        token="56433",
        optt="PE",
        dname="NIFTY 23JUN26 25000 PE ",
    )
    result = resolve(
        make_contract(side="PE"),
        search_fn=fake_search([row]),
    )
    assert result["tsym"] == "NIFTY23JUN26P25000"


# ---------------------------------------------------------------------------
# Near-miss strike rejection
# ---------------------------------------------------------------------------

def test_near_miss_strike_25050_raises_when_25000_requested():
    """Row with strike 25050 in dname must NOT match contract strike 25000."""
    row = make_nifty_scrip(
        tsym="NIFTY23JUN26C25050",
        token="99",
        dname="NIFTY 23JUN26 25050 CE ",
    )
    with pytest.raises(SymbolResolutionError, match="no Noren scrip found"):
        resolve(make_contract(strike=25000.0), search_fn=fake_search([row]))


def test_multiple_strikes_only_exact_matches():
    """search_fn returns three strike rows; only the exact 25000 one is returned."""
    rows = [
        make_nifty_scrip(tsym="NIFTY23JUN26C24950", token="11111",
                         dname="NIFTY 23JUN26 24950 CE "),
        make_nifty_scrip(tsym="NIFTY23JUN26C25000", token="56432",
                         dname="NIFTY 23JUN26 25000 CE "),
        make_nifty_scrip(tsym="NIFTY23JUN26C25050", token="22222",
                         dname="NIFTY 23JUN26 25050 CE "),
    ]
    result = resolve(make_contract(strike=25000.0), search_fn=fake_search(rows))
    assert result["token"] == "56432"


# ---------------------------------------------------------------------------
# Wrong side rejection
# ---------------------------------------------------------------------------

def test_wrong_side_ce_row_when_pe_requested_raises():
    """Contract requests PE, but only CE row returned."""
    row = make_nifty_scrip(optt="CE", dname="NIFTY 23JUN26 25000 CE ")
    with pytest.raises(SymbolResolutionError, match="no Noren scrip found"):
        resolve(make_contract(side="PE"), search_fn=fake_search([row]))


def test_mixed_ce_pe_rows_filters_to_requested_side():
    """search_fn returns CE and PE; only CE matches when CE is requested."""
    rows = [
        make_nifty_scrip(tsym="NIFTY23JUN26P25000", token="11110",
                         optt="PE", dname="NIFTY 23JUN26 25000 PE "),
        make_nifty_scrip(tsym="NIFTY23JUN26C25000", token="56432",
                         optt="CE", dname="NIFTY 23JUN26 25000 CE "),
    ]
    result = resolve(make_contract(side="CE"), search_fn=fake_search(rows))
    assert result["token"] == "56432"


# ---------------------------------------------------------------------------
# Wrong expiry rejection
# ---------------------------------------------------------------------------

def test_wrong_expiry_raises():
    """Row exd is 30-JUN-2026 but contract expects 2026-06-23."""
    row = make_nifty_scrip(exd="30-JUN-2026")
    with pytest.raises(SymbolResolutionError, match="no Noren scrip found"):
        resolve(make_contract(expiry_date="2026-06-23"), search_fn=fake_search([row]))


def test_same_strike_side_wrong_expiry_raises():
    """Same strike+side, two different expiry rows — wrong expiry must raise."""
    rows = [make_nifty_scrip(exd="30-JUN-2026")]  # contract wants 23-JUN-2026
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(expiry_date="2026-06-23"), search_fn=fake_search(rows))


# ---------------------------------------------------------------------------
# symname filter — NIFTYNXT50 row must NOT match NIFTY contract
# ---------------------------------------------------------------------------

def test_niftynxt50_symname_not_matched_for_nifty_contract():
    """A row with symname='NIFTYNXT50' must be filtered out for a NIFTY contract."""
    # row looks like NIFTY on optt/exd/strike but has wrong symname
    row = make_nifty_scrip(symname="NIFTYNXT50")
    with pytest.raises(SymbolResolutionError, match="no Noren scrip found"):
        resolve(make_contract(underlying="NIFTY"), search_fn=fake_search([row]))


def test_bsxopt_symname_required_for_sensex():
    """SENSEX contract must NOT match a row with symname='SENSEX' (correct is 'BSXOPT')."""
    row = {
        "symname": "SENSEX",   # wrong — real Flattrade uses BSXOPT
        "optt": "CE",
        "exd": "25-JUN-2026",
        "ls": "20",
        "token": "999",
        "tsym": "SENSEX26JUN80000CE",
        "dname": "SENSEX 25 JUN 80000 CE",
    }
    with pytest.raises(SymbolResolutionError, match="no Noren scrip found"):
        resolve(
            make_contract(underlying="SENSEX", strike=80000.0, side="CE",
                          expiry_date="2026-06-25", lot_size=20),
            search_fn=fake_search([row]),
        )


# ---------------------------------------------------------------------------
# Ambiguous — two identical matches must raise
# ---------------------------------------------------------------------------

def test_multiple_matching_rows_raises():
    """Two rows pass all filters — must raise as ambiguous."""
    row = make_nifty_scrip()
    rows = [row, dict(row, token="99999")]
    with pytest.raises(SymbolResolutionError, match="ambiguous"):
        resolve(make_contract(), search_fn=fake_search(rows))


# ---------------------------------------------------------------------------
# Lot-size: broker scrip ls is authoritative — stale/absent contract value must NOT block
# ---------------------------------------------------------------------------

def test_stale_contract_lot_size_does_not_block():
    """The user's real bug: scrip ls=65 but contract says lot_size=75 (stale).

    The old code raised; the new code returns lot_size=65 (broker authoritative).
    """
    row = make_nifty_scrip(ls="65")   # broker authoritative
    result = resolve(make_contract(lot_size=75), search_fn=fake_search([row]))
    assert result["lot_size"] == 65, (
        f"expected broker ls=65 to be used; got {result['lot_size']}"
    )


def test_absent_contract_lot_size_resolves_fine():
    """Contract with no lot_size key (None) resolves successfully using broker ls."""
    contract = make_contract(lot_size=65)
    contract.pop("lot_size")          # simulate absent field
    row = make_nifty_scrip(ls="65")
    result = resolve(contract, search_fn=fake_search([row]))
    assert result["lot_size"] == 65


def test_spec_mismatch_warns_not_raises(caplog):
    """scrip ls differs from UNDERLYING_SPEC — logs a warning but returns broker ls, no raise."""
    import logging
    # NIFTY UNDERLYING_SPEC says 65; scrip ls="30" triggers the mismatch path
    row = make_nifty_scrip(ls="30")
    with caplog.at_level(logging.WARNING, logger="app.live.flattrade_symbol"):
        result = resolve(
            make_contract(underlying="NIFTY", lot_size=30),
            search_fn=fake_search([row]),
        )
    assert result["lot_size"] == 30, "broker ls must be returned even when it differs from UNDERLYING_SPEC"
    assert any("authoritative" in r.message or "differs from" in r.message for r in caplog.records), (
        "expected a warning about spec mismatch"
    )


def test_banknifty_lot_35_resolves_to_broker_ls():
    """BANKNIFTY scrip with ls=35 now resolves to 35 (broker authoritative); no raise."""
    row = {**REAL_BANKNIFTY_CE_52000, "ls": "35"}
    result = resolve(
        make_contract(underlying="BANKNIFTY", strike=52000.0, side="CE",
                      expiry_date="2026-06-30", lot_size=35),
        search_fn=fake_search([row]),
    )
    assert result["lot_size"] == 35


def test_sensex_lot_size_20_correct():
    """SENSEX lot_size=20 matches scrip ls and resolves correctly."""
    result = resolve(
        make_contract(underlying="SENSEX", strike=80000.0, side="CE",
                      expiry_date="2026-06-25", lot_size=20),
        search_fn=fake_search([REAL_SENSEX_CE_80000]),
    )
    assert result["lot_size"] == 20


def test_banknifty_lot_size_30_correct():
    """BANKNIFTY lot_size=30 passes (warehouse value)."""
    result = resolve(
        make_contract(underlying="BANKNIFTY", strike=52000.0, side="CE",
                      expiry_date="2026-06-30", lot_size=30),
        search_fn=fake_search([REAL_BANKNIFTY_CE_52000]),
    )
    assert result["lot_size"] == 30


# ---------------------------------------------------------------------------
# LOT_SIZE_EXPECTED constant
# ---------------------------------------------------------------------------

def test_lot_size_expected_values():
    assert LOT_SIZE_EXPECTED["NIFTY"] == 65
    assert LOT_SIZE_EXPECTED["SENSEX"] == 20
    assert LOT_SIZE_EXPECTED["BANKNIFTY"] == 30


# ---------------------------------------------------------------------------
# Missing/bad contract fields
# ---------------------------------------------------------------------------

def test_missing_underlying_raises():
    contract = make_contract()
    contract.pop("underlying")
    with pytest.raises(SymbolResolutionError, match="underlying"):
        resolve(contract, search_fn=fake_search([]))


def test_unknown_underlying_finnifty_raises():
    """underlying='FINNIFTY' is not in the allow-list."""
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(underlying="FINNIFTY"), search_fn=fake_search([]))


def test_unknown_underlying_bankex_raises():
    """underlying='BANKEX' is not in the allow-list."""
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(underlying="BANKEX"), search_fn=fake_search([]))


def test_missing_expiry_raises():
    contract = make_contract()
    contract["expiry_date"] = ""
    with pytest.raises(SymbolResolutionError, match="expiry_date"):
        resolve(contract, search_fn=fake_search([]))


def test_invalid_side_raises():
    contract = make_contract(side="CALL")  # must be CE or PE
    with pytest.raises(SymbolResolutionError, match="side"):
        resolve(contract, search_fn=fake_search([]))


def test_no_rows_raises():
    with pytest.raises(SymbolResolutionError, match="no Noren scrip found"):
        resolve(make_contract(), search_fn=fake_search([]))


# ---------------------------------------------------------------------------
# Non-finite / garbage strike
# ---------------------------------------------------------------------------

def test_nan_strike_in_contract_raises():
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(strike=float("nan")), search_fn=fake_search([]))


def test_inf_strike_in_contract_raises():
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(strike=float("inf")), search_fn=fake_search([]))


# ---------------------------------------------------------------------------
# Blank tsym / token must raise
# ---------------------------------------------------------------------------

def test_blank_tsym_raises():
    """tsym='   ' (whitespace-only) must raise SymbolResolutionError after strip."""
    row = make_nifty_scrip(tsym="   ")
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(), search_fn=fake_search([row]))


def test_blank_token_raises():
    """token='   ' (whitespace-only) must raise SymbolResolutionError after strip."""
    row = make_nifty_scrip(token="   ")
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(), search_fn=fake_search([row]))


# ---------------------------------------------------------------------------
# Non-dict row and raising search_fn
# ---------------------------------------------------------------------------

def test_non_dict_scrip_raises_symbol_resolution_error():
    """A non-dict entry in search_fn results must surface as SymbolResolutionError."""
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(), search_fn=fake_search(["not-a-dict"]))


def test_raising_search_fn_raises_symbol_resolution_error():
    """A search_fn that raises must be wrapped as SymbolResolutionError."""
    def bad_search(exch, text):
        raise RuntimeError("network down")

    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(), search_fn=bad_search)


# ---------------------------------------------------------------------------
# Fractional / negative lot size in scrip
# ---------------------------------------------------------------------------

def test_fractional_lot_raises():
    """ls="65.7" must raise SymbolResolutionError, NOT silently truncate to 65."""
    row = make_nifty_scrip(ls="65.7")
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(lot_size=65), search_fn=fake_search([row]))


def test_negative_lot_raises():
    """ls="-65" (negative) must raise SymbolResolutionError."""
    row = make_nifty_scrip(ls="-65")
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(lot_size=65), search_fn=fake_search([row]))


# ---------------------------------------------------------------------------
# All three underlyings resolve correctly (regression guard)
# ---------------------------------------------------------------------------

def test_all_three_underlyings_resolve():
    """NIFTY, BANKNIFTY, SENSEX must all resolve correctly."""
    # NIFTY
    r = resolve(
        make_contract(underlying="NIFTY", strike=25000.0, side="CE",
                      expiry_date="2026-06-23", lot_size=65),
        search_fn=fake_search([REAL_NIFTY_CE_25000]),
    )
    assert r["exch"] == "NFO"
    assert r["tsym"] == "NIFTY23JUN26C25000"

    # BANKNIFTY
    r = resolve(
        make_contract(underlying="BANKNIFTY", strike=52000.0, side="CE",
                      expiry_date="2026-06-30", lot_size=30),
        search_fn=fake_search([REAL_BANKNIFTY_CE_52000]),
    )
    assert r["exch"] == "NFO"
    assert r["tsym"] == "BANKNIFTY30JUN26C52000"

    # SENSEX
    r = resolve(
        make_contract(underlying="SENSEX", strike=80000.0, side="CE",
                      expiry_date="2026-06-25", lot_size=20),
        search_fn=fake_search([REAL_SENSEX_CE_80000]),
    )
    assert r["exch"] == "BFO"
    assert r["tsym"] == "SENSEX26JUN80000CE"


# ---------------------------------------------------------------------------
# _normalise_tick
# ---------------------------------------------------------------------------

def test_normalise_tick_standard_005():
    assert _normalise_tick("0.05") == 0.05


def test_normalise_tick_integer_string():
    assert _normalise_tick("1") == 1.0


def test_normalise_tick_float_value():
    assert _normalise_tick("0.25") == 0.25


def test_normalise_tick_missing_defaults_to_005(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="app.live.flattrade_symbol"):
        result = _normalise_tick(None)
    assert result == 0.05
    assert any("tick" in r.message.lower() or "ti" in r.message.lower() for r in caplog.records)


def test_normalise_tick_garbage_defaults_to_005(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="app.live.flattrade_symbol"):
        result = _normalise_tick("bad_value")
    assert result == 0.05


def test_normalise_tick_zero_defaults_to_005(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="app.live.flattrade_symbol"):
        result = _normalise_tick("0")
    assert result == 0.05


def test_normalise_tick_negative_defaults_to_005(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="app.live.flattrade_symbol"):
        result = _normalise_tick("-0.05")
    assert result == 0.05


# ---------------------------------------------------------------------------
# resolve() returns 'tick' key
# ---------------------------------------------------------------------------

def test_resolve_returns_tick_key_005():
    """scrip row with ti='0.05' → resolved dict has tick==0.05"""
    row = {**make_nifty_scrip(), "ti": "0.05"}
    result = resolve(make_contract(), search_fn=fake_search([row]))
    assert "tick" in result, "resolve() must return a 'tick' key"
    assert result["tick"] == 0.05


def test_resolve_missing_ti_defaults_to_005(caplog):
    """scrip row missing ti → resolved dict has tick==0.05 (with warning)"""
    import logging
    row = make_nifty_scrip()  # no 'ti' field
    with caplog.at_level(logging.WARNING, logger="app.live.flattrade_symbol"):
        result = resolve(make_contract(), search_fn=fake_search([row]))
    assert result["tick"] == 0.05


def test_resolve_garbage_ti_defaults_to_005(caplog):
    """scrip row with ti='garbage' → resolved dict has tick==0.05 (with warning)"""
    import logging
    row = {**make_nifty_scrip(), "ti": "garbage"}
    with caplog.at_level(logging.WARNING, logger="app.live.flattrade_symbol"):
        result = resolve(make_contract(), search_fn=fake_search([row]))
    assert result["tick"] == 0.05
