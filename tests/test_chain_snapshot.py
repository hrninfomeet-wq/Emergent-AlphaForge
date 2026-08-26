"""Tests for the pure option-chain snapshot normalizer.

`chain_snapshots` is the one dataset in this repo that CANNOT be backfilled —
option-chain structure is not recoverable after the fact, which is why the
collection sat empty with an index and no writer until now. That makes the
parser's correctness unusually load-bearing: a field silently dropped today is
a field that never existed, and no future session can repair it.

Two properties are therefore pinned harder than they would be elsewhere:

1. **Missing is None; zero is zero.** An absent `oi` and an `oi` of 0 are
   different facts. Collapsing them is exactly the defect that made the old
   `vix_boost_threshold` knob worthless — a strategy scoring zero on missing
   data looks identical to one scoring zero on real data.
2. **Every field the API offers is captured.** Especially `bid_price` /
   `ask_price`: the optimizer's own `research_eligibility` guard currently
   blocks promotion with `no_point_in_time_execution_surface`, and this is the
   only stream that can ever close it.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.chain_snapshot import (  # noqa: E402
    CHAIN_SOURCE,
    GREEK_FIELDS,
    MARKET_FIELDS,
    normalize_chain_response,
)

TS = 1_787_000_000_000
SESSION = "2026-08-27"


def _leg(**over):
    market = {"ltp": 120.5, "close_price": 118.0, "volume": 45000, "oi": 1_500_000,
              "prev_oi": 1_400_000, "bid_price": 120.0, "bid_qty": 75,
              "ask_price": 121.0, "ask_qty": 150}
    greeks = {"vega": 8.1, "theta": -14.2, "gamma": 0.0007, "delta": 0.52,
              "iv": 12.4, "pop": 47.5}
    market.update(over.pop("market", {}))
    greeks.update(over.pop("option_greeks", {}))
    out = {"instrument_key": "NSE_FO|54321", "market_data": market,
           "option_greeks": greeks}
    out.update(over)
    return out


def _row(strike=24250.0, **over):
    row = {
        "expiry": "2026-08-27",
        "pcr": 0.87,
        "strike_price": strike,
        "underlying_key": "NSE_INDEX|Nifty 50",
        "underlying_spot_price": 24252.0,
        "call_options": _leg(),
        "put_options": _leg(),
    }
    row.update(over)
    return row


def _payload(rows=None):
    return {"status": "success", "data": rows if rows is not None else [_row()]}


def _norm(payload, **over):
    kwargs = {"instrument": "NIFTY", "captured_ts_ms": TS, "session_date": SESSION}
    kwargs.update(over)
    return normalize_chain_response(payload, **kwargs)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

def test_document_carries_its_own_provenance():
    doc = _norm(_payload())
    assert doc["instrument"] == "NIFTY"
    assert doc["ts"] == TS
    assert doc["session_date"] == SESSION
    assert doc["expiry_date"] == "2026-08-27"
    assert doc["underlying_key"] == "NSE_INDEX|Nifty 50"
    assert doc["spot"] == pytest.approx(24252.0)
    assert doc["source"] == CHAIN_SOURCE
    assert doc["strike_count"] == 1


def test_every_market_and_greek_field_survives_the_round_trip():
    """A field dropped here is a field that never existed."""
    ce = _norm(_payload())["strikes"][0]["ce"]
    for name in MARKET_FIELDS + GREEK_FIELDS:
        assert name in ce, f"{name} was dropped by the normalizer"
    assert ce["bid_price"] == pytest.approx(120.0)
    assert ce["ask_price"] == pytest.approx(121.0)
    assert ce["oi"] == pytest.approx(1_500_000)
    assert ce["iv"] == pytest.approx(12.4)
    assert ce["instrument_key"] == "NSE_FO|54321"


def test_strikes_are_sorted_ascending_regardless_of_api_order():
    payload = _payload([_row(24300.0), _row(24200.0), _row(24250.0)])
    assert [s["strike"] for s in _norm(payload)["strikes"]] == [24200.0, 24250.0, 24300.0]


def test_vendor_pcr_stays_on_its_own_strike_and_is_never_promoted():
    """The API's `pcr` is PER-STRIKE (PE OI / CE OI at that strike), not a
    chain-level ratio. The first real capture returned 789.09 on the lowest
    strike; stored at document level that reads as a chain-wide put/call ratio
    and is wrong by three orders of magnitude. Caught by smoke-testing the
    parser against a live response rather than against the vendor's docs.
    """
    doc = _norm(_payload([_row(24200.0, pcr=789.0886), _row(24250.0, pcr=0.87)]))
    assert [s["pcr"] for s in doc["strikes"]] == [pytest.approx(789.0886),
                                                  pytest.approx(0.87)]
    for promoted in ("pcr", "pcr_reported", "max_pain", "ce_oi_total", "pe_oi_total"):
        assert promoted not in doc, f"{promoted} must not sit at document level"


def test_a_strike_without_a_vendor_pcr_records_none():
    row = _row()
    del row["pcr"]
    assert _norm(_payload([row]))["strikes"][0]["pcr"] is None


# ---------------------------------------------------------------------------
# Missing is None; zero is zero
# ---------------------------------------------------------------------------

def test_absent_field_is_none_not_zero():
    row = _row()
    del row["call_options"]["market_data"]["oi"]
    del row["call_options"]["option_greeks"]["iv"]
    ce = _norm(_payload([row]))["strikes"][0]["ce"]
    assert ce["oi"] is None
    assert ce["iv"] is None


def test_a_real_zero_is_preserved_as_zero():
    """An OI of 0 is a measurement. Collapsing it into None loses a fact."""
    row = _row()
    row["call_options"]["market_data"]["oi"] = 0
    row["call_options"]["market_data"]["volume"] = 0
    ce = _norm(_payload([row]))["strikes"][0]["ce"]
    assert ce["oi"] == 0.0
    assert ce["volume"] == 0.0
    assert ce["oi"] is not None


@pytest.mark.parametrize("bad", [None, "", "n/a", float("nan"), float("inf"), float("-inf"), {}])
def test_unusable_values_become_none_never_zero(bad):
    row = _row()
    row["call_options"]["market_data"]["bid_price"] = bad
    ce = _norm(_payload([row]))["strikes"][0]["ce"]
    assert ce["bid_price"] is None


def test_a_leg_absent_entirely_is_none_not_an_empty_dict():
    """No CE quoted at this strike is different from a CE quoted at zero."""
    row = _row()
    row["call_options"] = None
    strike = _norm(_payload([row]))["strikes"][0]
    assert strike["ce"] is None
    assert strike["pe"] is not None


def test_flat_legs_without_nested_objects_still_parse():
    """Defensive: tolerate an API build that flattens market_data/option_greeks
    rather than silently recording an all-None strike."""
    row = _row()
    row["call_options"] = {"instrument_key": "NSE_FO|99", "ltp": 55.0, "oi": 900.0,
                           "bid_price": 54.5, "ask_price": 55.5, "iv": 11.1}
    ce = _norm(_payload([row]))["strikes"][0]["ce"]
    assert ce["ltp"] == pytest.approx(55.0)
    assert ce["oi"] == pytest.approx(900.0)
    assert ce["bid_price"] == pytest.approx(54.5)
    assert ce["iv"] == pytest.approx(11.1)


# ---------------------------------------------------------------------------
# Degenerate input never raises and never fabricates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [None, {}, {"data": None}, {"data": []},
                                     {"data": "nonsense"}, [], "nope"])
def test_unusable_payload_yields_an_empty_snapshot_rather_than_raising(payload):
    doc = _norm(payload)
    assert doc["strikes"] == []
    assert doc["strike_count"] == 0
    assert doc["instrument"] == "NIFTY"


def test_a_strike_without_a_price_is_dropped_not_recorded_as_zero():
    rows = [_row(24250.0), _row(None)]
    doc = _norm(_payload(rows))
    assert [s["strike"] for s in doc["strikes"]] == [24250.0]


def test_spot_falls_back_across_rows_when_the_first_row_omits_it():
    rows = [_row(24200.0, underlying_spot_price=None), _row(24250.0)]
    assert _norm(_payload(rows))["spot"] == pytest.approx(24252.0)


def test_spot_absent_everywhere_is_none_not_zero():
    rows = [_row(24200.0, underlying_spot_price=None),
            _row(24250.0, underlying_spot_price=None)]
    assert _norm(_payload(rows))["spot"] is None


def test_expiry_is_normalized_to_a_ten_character_iso_date():
    """The API types expiry as a datetime; the warehouse keys expiry_date as a
    10-char ISO string everywhere else (option_contracts, options_1m)."""
    rows = [_row(24250.0, expiry="2026-08-27T00:00:00+05:30")]
    assert _norm(_payload(rows))["expiry_date"] == "2026-08-27"


def test_no_ttl_marker_is_written_into_the_document():
    """`ticks` carries a 30-day TTL on `stored_at`. If this collection ever grows
    one, the entire point of recording is defeated — so the document must not
    carry a field an accidental TTL index could key on."""
    doc = _norm(_payload())
    assert "stored_at" not in doc
    assert "expires_at" not in doc


def test_a_boolean_is_not_recorded_as_a_price():
    """`isinstance(True, int)` is True in Python, so an unguarded float() turns a
    stray boolean into a price of 1.0 — a fabricated quote in a dataset that can
    never be re-derived."""
    row = _row()
    row["call_options"]["market_data"]["ltp"] = True
    row["call_options"]["market_data"]["oi"] = False
    ce = _norm(_payload([row]))["strikes"][0]["ce"]
    assert ce["ltp"] is None
    assert ce["oi"] is None


def test_a_string_number_from_the_api_is_still_read():
    """Vendors serialise numerics as strings more often than their docs admit."""
    row = _row()
    row["call_options"]["market_data"]["oi"] = "1500000"
    row["put_options"]["market_data"]["bid_price"] = "119.25"
    strike = _norm(_payload([row]))["strikes"][0]
    assert strike["ce"]["oi"] == pytest.approx(1_500_000.0)
    assert strike["pe"]["bid_price"] == pytest.approx(119.25)
