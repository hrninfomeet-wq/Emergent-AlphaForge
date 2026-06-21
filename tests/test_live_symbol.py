"""Tests for the Upstox->Noren symbol resolver (fail-closed).

All tests use a fake search_fn; no network.

Noren scrip field names assumed by the resolver (documented in flattrade_symbol.py):
    tsym    trading symbol
    token   instrument token
    ls      lot size (string)
    strprc  strike price (string, may include decimals e.g. "25000.00")
    optt    option type "CE" or "PE"
    exd     expiry date "DD-Mon-YYYY" e.g. "26-Jun-2025"
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.flattrade_symbol import (
    LOT_SIZE_EXPECTED,
    SymbolResolutionError,
    resolve,
    _parse_noren_expiry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_contract(
    underlying="NIFTY",
    strike=25000.0,
    side="CE",
    expiry_date="2025-06-26",
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


def make_scrip(
    tsym="NIFTY25JUN2025C25000",
    token="43215",
    ls="65",
    strprc="25000.00",
    optt="CE",
    exd="26-Jun-2025",
) -> dict:
    return {
        "tsym": tsym,
        "token": token,
        "ls": ls,
        "strprc": strprc,
        "optt": optt,
        "exd": exd,
    }


def fake_search(rows):
    """Return a search_fn that ignores the query and returns `rows`."""
    def search_fn(exch, text):
        return rows
    return search_fn


# ---------------------------------------------------------------------------
# _parse_noren_expiry
# ---------------------------------------------------------------------------

def test_parse_noren_expiry_standard():
    assert _parse_noren_expiry("26-Jun-2025") == "2025-06-26"


def test_parse_noren_expiry_all_months():
    assert _parse_noren_expiry("01-Jan-2025") == "2025-01-01"
    assert _parse_noren_expiry("28-Feb-2025") == "2025-02-28"
    assert _parse_noren_expiry("31-Dec-2025") == "2025-12-31"


def test_parse_noren_expiry_bad_format_raises():
    import pytest
    with pytest.raises(SymbolResolutionError):
        _parse_noren_expiry("2025-06-26")   # ISO, not Noren format


def test_parse_noren_expiry_bad_month_raises():
    import pytest
    with pytest.raises(SymbolResolutionError):
        _parse_noren_expiry("26-XXX-2025")


# ---------------------------------------------------------------------------
# Exchange routing
# ---------------------------------------------------------------------------

def test_nifty_routes_to_nfo():
    rows = [make_scrip()]
    result = resolve(make_contract(underlying="NIFTY"), search_fn=fake_search(rows))
    assert result["exch"] == "NFO"


def test_sensex_routes_to_bfo():
    rows = [make_scrip(
        tsym="SENSEX72000CE25JUN",
        token="99001",
        ls="20",
        strprc="72000.00",
        optt="CE",
        exd="26-Jun-2025",
    )]
    contract = make_contract(underlying="SENSEX", strike=72000.0, side="CE", lot_size=20)
    result = resolve(contract, search_fn=fake_search(rows))
    assert result["exch"] == "BFO"


def test_banknifty_routes_to_nfo():
    rows = [make_scrip(
        tsym="BANKNIFTY52000CE25JUN",
        token="55001",
        ls="30",
        strprc="52000.00",
        optt="CE",
        exd="26-Jun-2025",
    )]
    contract = make_contract(underlying="BANKNIFTY", strike=52000.0, side="CE", lot_size=30)
    result = resolve(contract, search_fn=fake_search(rows))
    assert result["exch"] == "NFO"


# ---------------------------------------------------------------------------
# Exact match
# ---------------------------------------------------------------------------

def test_exact_match_returns_correct_tsym_and_token():
    rows = [make_scrip(tsym="NIFTY25JUN2025C25000", token="43215")]
    result = resolve(make_contract(), search_fn=fake_search(rows))
    assert result["tsym"] == "NIFTY25JUN2025C25000"
    assert result["token"] == "43215"
    assert result["lot_size"] == 65


def test_exact_match_pe_side():
    rows = [make_scrip(
        tsym="NIFTY25JUN2025P25000",
        token="43216",
        optt="PE",
    )]
    contract = make_contract(side="PE")
    result = resolve(contract, search_fn=fake_search(rows))
    assert result["tsym"] == "NIFTY25JUN2025P25000"


def test_match_filters_out_other_strikes_from_response():
    """search_fn returns multiple strikes; only the exact one matches."""
    rows = [
        make_scrip(tsym="NIFTY25JUN2025C24950", token="11111", strprc="24950.00"),
        make_scrip(tsym="NIFTY25JUN2025C25000", token="43215", strprc="25000.00"),
        make_scrip(tsym="NIFTY25JUN2025C25050", token="22222", strprc="25050.00"),
    ]
    result = resolve(make_contract(strike=25000.0), search_fn=fake_search(rows))
    assert result["token"] == "43215"


def test_match_filters_out_pe_when_ce_requested():
    """search_fn returns CE and PE; only CE matches."""
    rows = [
        make_scrip(tsym="NIFTY25JUN2025P25000", token="11110", optt="PE"),
        make_scrip(tsym="NIFTY25JUN2025C25000", token="43215", optt="CE"),
    ]
    result = resolve(make_contract(side="CE"), search_fn=fake_search(rows))
    assert result["token"] == "43215"


# ---------------------------------------------------------------------------
# Near-miss rejection (adversarial cases)
# ---------------------------------------------------------------------------

def test_near_miss_strike_raises():
    """25050 in row does not match contract strike 25000."""
    import pytest
    rows = [make_scrip(strprc="25050.00", tsym="NIFTY25JUN2025C25050", token="99")]
    with pytest.raises(SymbolResolutionError, match="no Noren scrip found"):
        resolve(make_contract(strike=25000.0), search_fn=fake_search(rows))


def test_wrong_side_raises():
    """Contract asks for CE, only PE row returned."""
    import pytest
    rows = [make_scrip(optt="PE", tsym="NIFTY25JUN2025P25000", token="99")]
    with pytest.raises(SymbolResolutionError, match="no Noren scrip found"):
        resolve(make_contract(side="CE"), search_fn=fake_search(rows))


def test_wrong_expiry_raises():
    """Row's expiry is 03-Jul-2025 but contract expects 2025-06-26."""
    import pytest
    rows = [make_scrip(exd="03-Jul-2025")]
    with pytest.raises(SymbolResolutionError, match="no Noren scrip found"):
        resolve(make_contract(expiry_date="2025-06-26"), search_fn=fake_search(rows))


def test_same_day_different_expiry_raises():
    """Two series expiring on different dates; wrong one supplied."""
    import pytest
    # Contract wants Jun 26, row has Jun 19 (same day number in different month/week)
    rows = [make_scrip(exd="19-Jun-2025")]
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(expiry_date="2025-06-26"), search_fn=fake_search(rows))


def test_no_rows_raises():
    """Empty search result raises SymbolResolutionError."""
    import pytest
    with pytest.raises(SymbolResolutionError, match="no Noren scrip found"):
        resolve(make_contract(), search_fn=fake_search([]))


# ---------------------------------------------------------------------------
# Lot-size cross-check
# ---------------------------------------------------------------------------

def test_lot_size_mismatch_between_scrip_and_contract_raises():
    """scrip ls=65 but contract says lot_size=30."""
    import pytest
    rows = [make_scrip(ls="65")]   # NIFTY expected 65
    with pytest.raises(SymbolResolutionError, match="lot size mismatch"):
        resolve(make_contract(lot_size=30), search_fn=fake_search(rows))


def test_lot_size_mismatch_against_expected_constant_raises():
    """Contract and scrip agree on 30, but LOT_SIZE_EXPECTED says NIFTY=65."""
    import pytest
    rows = [make_scrip(ls="30")]
    with pytest.raises(SymbolResolutionError, match="lot size mismatch"):
        resolve(make_contract(underlying="NIFTY", lot_size=30), search_fn=fake_search(rows))


def test_sensex_lot_size_correct():
    """SENSEX lot_size=20 matches both scrip and LOT_SIZE_EXPECTED."""
    rows = [make_scrip(
        tsym="SENSEX72000CE25JUN", token="99001", ls="20",
        strprc="72000.00", optt="CE", exd="26-Jun-2025",
    )]
    contract = make_contract(underlying="SENSEX", strike=72000.0, side="CE", lot_size=20)
    result = resolve(contract, search_fn=fake_search(rows))
    assert result["lot_size"] == 20


def test_banknifty_lot_size_30_correct():
    """BANKNIFTY lot_size=30 passes (warehouse value — verify-live comment in code)."""
    rows = [make_scrip(
        tsym="BANKNIFTY52000CE25JUN", token="55001", ls="30",
        strprc="52000.00", optt="CE", exd="26-Jun-2025",
    )]
    contract = make_contract(underlying="BANKNIFTY", strike=52000.0, side="CE", lot_size=30)
    result = resolve(contract, search_fn=fake_search(rows))
    assert result["lot_size"] == 30


# ---------------------------------------------------------------------------
# Multi-match (ambiguous) raises
# ---------------------------------------------------------------------------

def test_multiple_matching_rows_raises():
    """If two rows pass all filters, resolution is ambiguous — must raise."""
    import pytest
    row = make_scrip()
    rows = [row, dict(row, token="99999")]   # duplicate with different token
    with pytest.raises(SymbolResolutionError, match="ambiguous"):
        resolve(make_contract(), search_fn=fake_search(rows))


# ---------------------------------------------------------------------------
# LOT_SIZE_EXPECTED constant
# ---------------------------------------------------------------------------

def test_lot_size_expected_values():
    assert LOT_SIZE_EXPECTED["NIFTY"] == 65
    assert LOT_SIZE_EXPECTED["SENSEX"] == 20
    assert LOT_SIZE_EXPECTED["BANKNIFTY"] == 30


# ---------------------------------------------------------------------------
# Missing contract fields
# ---------------------------------------------------------------------------

def test_missing_underlying_raises():
    import pytest
    contract = make_contract()
    contract.pop("underlying")
    with pytest.raises(SymbolResolutionError, match="underlying"):
        resolve(contract, search_fn=fake_search([]))


def test_missing_expiry_raises():
    import pytest
    contract = make_contract()
    contract["expiry_date"] = ""
    with pytest.raises(SymbolResolutionError, match="expiry_date"):
        resolve(contract, search_fn=fake_search([]))


def test_invalid_side_raises():
    import pytest
    contract = make_contract(side="CALL")  # must be CE or PE
    with pytest.raises(SymbolResolutionError, match="side"):
        resolve(contract, search_fn=fake_search([]))


# ---------------------------------------------------------------------------
# Scrip field edge cases
# ---------------------------------------------------------------------------

def test_strprc_with_trailing_zeros_matches():
    """strprc="25000.00" should match contract strike 25000.0."""
    rows = [make_scrip(strprc="25000.00")]
    result = resolve(make_contract(strike=25000.0), search_fn=fake_search(rows))
    assert result["tsym"] == "NIFTY25JUN2025C25000"


def test_strprc_integer_string_matches():
    """strprc="25000" (no decimal) should also match."""
    rows = [make_scrip(strprc="25000")]
    result = resolve(make_contract(strike=25000.0), search_fn=fake_search(rows))
    assert result["tsym"] == "NIFTY25JUN2025C25000"


# ---------------------------------------------------------------------------
# HOLE-1 — lot truncation: non-integer / non-positive ls must be rejected
# ---------------------------------------------------------------------------

def test_hole1_fractional_lot_raises():
    """ls="65.7" must raise SymbolResolutionError, NOT silently truncate to 65."""
    import pytest
    rows = [make_scrip(ls="65.7")]
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(lot_size=65), search_fn=fake_search(rows))


def test_hole1_negative_lot_raises():
    """ls="-65" (negative) must raise SymbolResolutionError."""
    import pytest
    rows = [make_scrip(ls="-65")]
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(lot_size=65), search_fn=fake_search(rows))


# ---------------------------------------------------------------------------
# HOLE-2 — non-finite strike: nan / inf must raise SymbolResolutionError
# ---------------------------------------------------------------------------

def test_hole2_nan_strike_in_contract_raises():
    """contract strike=nan must raise SymbolResolutionError (not bubble a bare error)."""
    import math
    import pytest
    rows = [make_scrip()]
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(strike=float("nan")), search_fn=fake_search(rows))


def test_hole2_inf_strike_in_contract_raises():
    """contract strike=inf must raise SymbolResolutionError."""
    import pytest
    rows = [make_scrip()]
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(strike=float("inf")), search_fn=fake_search(rows))


# ---------------------------------------------------------------------------
# HOLE-3 — unknown underlying: must raise, not default to NFO / skip lot gate
# ---------------------------------------------------------------------------

def test_hole3_finnifty_raises():
    """underlying='FINNIFTY' is not in the allow-list and must raise SymbolResolutionError."""
    import pytest
    rows = [make_scrip()]
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(underlying="FINNIFTY"), search_fn=fake_search(rows))


def test_hole3_bankex_raises():
    """underlying='BANKEX' is not in the allow-list and must raise SymbolResolutionError."""
    import pytest
    rows = [make_scrip()]
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(underlying="BANKEX"), search_fn=fake_search(rows))


def test_hole3_known_underlyings_still_resolve():
    """NIFTY, BANKNIFTY, SENSEX must all still resolve correctly after the allow-list is added."""
    # NIFTY → NFO
    rows_nifty = [make_scrip()]
    result = resolve(make_contract(underlying="NIFTY"), search_fn=fake_search(rows_nifty))
    assert result["exch"] == "NFO"

    # BANKNIFTY → NFO
    rows_bnf = [make_scrip(
        tsym="BANKNIFTY52000CE25JUN", token="55001", ls="30",
        strprc="52000.00", optt="CE", exd="26-Jun-2025",
    )]
    result = resolve(
        make_contract(underlying="BANKNIFTY", strike=52000.0, side="CE", lot_size=30),
        search_fn=fake_search(rows_bnf),
    )
    assert result["exch"] == "NFO"

    # SENSEX → BFO
    rows_sx = [make_scrip(
        tsym="SENSEX72000CE25JUN", token="99001", ls="20",
        strprc="72000.00", optt="CE", exd="26-Jun-2025",
    )]
    result = resolve(
        make_contract(underlying="SENSEX", strike=72000.0, side="CE", lot_size=20),
        search_fn=fake_search(rows_sx),
    )
    assert result["exch"] == "BFO"


# ---------------------------------------------------------------------------
# HOLE-4 — blank tsym / token in scrip row must raise SymbolResolutionError
# ---------------------------------------------------------------------------

def test_hole4_whitespace_tsym_raises():
    """tsym='   ' (whitespace-only) must raise SymbolResolutionError after strip."""
    import pytest
    rows = [make_scrip(tsym="   ")]
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(), search_fn=fake_search(rows))


def test_hole4_whitespace_token_raises():
    """token='   ' (whitespace-only) must raise SymbolResolutionError after strip."""
    import pytest
    rows = [make_scrip(token="   ")]
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(), search_fn=fake_search(rows))


# ---------------------------------------------------------------------------
# NOTE — robustness: non-dict scrip row and raising search_fn
# ---------------------------------------------------------------------------

def test_note_non_dict_scrip_raises_symbol_resolution_error():
    """A non-dict entry in search_fn results must surface as SymbolResolutionError."""
    import pytest
    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(), search_fn=fake_search(["not-a-dict"]))


def test_note_raising_search_fn_raises_symbol_resolution_error():
    """A search_fn that raises must be wrapped as SymbolResolutionError, not re-raised raw."""
    import pytest

    def bad_search(exch, text):
        raise RuntimeError("network down")

    with pytest.raises(SymbolResolutionError):
        resolve(make_contract(), search_fn=bad_search)
