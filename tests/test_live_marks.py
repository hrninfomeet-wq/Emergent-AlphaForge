"""Tick-marking of the broker position book.

The Live page's LTP / MTM come from the Flattrade REST position book at a 15s
cadence. These tests pin the pure layer that re-marks that book against the
Upstox WS tick already in memory, so the number on screen is tick-fresh while
the broker book stays the source of truth for qty / avg / realized.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_marks import (
    build_contract_index,
    mark_positions,
    parse_position_identity,
    resolve_tick_key,
)

# A real Flattrade position row (trimmed), captured from the live account.
POS = {
    "exch": "NFO", "tsym": "NIFTY15SEP26C23350", "token": "47291",
    "symname": "NIFTY", "dname": "NIFTY 15SEP26 23350 CE ", "instname": "OPTIDX",
    "ls": "65", "ti": "0.05", "mult": "1", "prcftr": "1.000000",
    "netqty": "325", "netavgprc": "161.90", "lp": "161.90",
    "urmtom": "0.00", "rpnl": "-0.00",
}

CONTRACTS = [
    {"instrument_key": "NSE_FO|47291", "exchange_token": "47291", "segment": "NSE_FO",
     "underlying": "NIFTY", "expiry_date": "2026-09-15", "strike": 23350, "side": "CE"},
    # Same exchange token, EXPIRED contracts — token reuse across expiries is real.
    {"instrument_key": "NSE_FO|47291|19-12-2024", "exchange_token": "47291", "segment": "NSE_FO",
     "underlying": "NIFTY", "expiry_date": "2024-12-19", "strike": 22600, "side": "PE"},
    {"instrument_key": "NSE_FO|47291|21-08-2025", "exchange_token": "47291", "segment": "NSE_FO",
     "underlying": "NIFTY", "expiry_date": "2025-08-21", "strike": 25350, "side": "PE"},
]


def test_parse_position_identity_from_dname():
    ident = parse_position_identity(POS)
    assert ident == ("NIFTY", "2026-09-15", 23350.0, "CE")


def test_resolve_tick_key_picks_the_live_contract_not_a_recycled_token():
    """Exchange tokens are reused across expiries. Resolving by token alone would
    pick a 2024 contract; identity must win."""
    idx = build_contract_index(CONTRACTS)
    assert resolve_tick_key(POS, idx) == "NSE_FO|47291"


def test_resolve_tick_key_returns_none_when_ambiguous_or_unknown():
    idx = build_contract_index(CONTRACTS)
    assert resolve_tick_key({**POS, "dname": "BANKNIFTY 15SEP26 23350 CE"}, idx) is None
    assert resolve_tick_key({"exch": "NFO", "tsym": "GARBAGE"}, idx) is None


def test_mark_anchors_on_the_broker_number_so_an_equal_tick_is_a_no_op():
    """The re-mark must reproduce the broker's own MTM exactly when the tick
    equals the broker's lp — we never re-derive Noren's MTM formula, we apply the
    delta to it."""
    idx = build_contract_index(CONTRACTS)
    ticks = {"NSE_FO|47291": {"last_price": 161.90, "ingest_ts": 1_000_000}}
    out = mark_positions([POS], tick_lookup=ticks.get, now_ms=1_000_100, max_age_ms=5_000, contract_index=idx)
    row = out["positions"][0]
    assert row["lp"] == 161.90
    assert row["urmtom"] == 0.0
    assert row["mark_source"] == "tick"


def test_mark_applies_qty_weighted_delta_to_broker_mtm():
    idx = build_contract_index(CONTRACTS)
    ticks = {"NSE_FO|47291": {"last_price": 171.90, "ingest_ts": 1_000_000}}
    out = mark_positions([POS], tick_lookup=ticks.get, now_ms=1_000_100, max_age_ms=5_000, contract_index=idx)
    row = out["positions"][0]
    # +10.00 on 325 units = +3250 on top of the broker's 0.00
    assert row["lp"] == 171.90
    assert row["urmtom"] == 3250.0
    assert out["day_pnl"] == 3250.0


def test_short_position_marks_the_other_way():
    idx = build_contract_index(CONTRACTS)
    short = {**POS, "netqty": "-325"}
    ticks = {"NSE_FO|47291": {"last_price": 171.90, "ingest_ts": 1_000_000}}
    out = mark_positions([short], tick_lookup=ticks.get, now_ms=1_000_100, max_age_ms=5_000, contract_index=idx)
    assert out["positions"][0]["urmtom"] == -3250.0


def test_stale_tick_falls_back_to_the_broker_value_and_says_so():
    idx = build_contract_index(CONTRACTS)
    ticks = {"NSE_FO|47291": {"last_price": 999.0, "ingest_ts": 1_000_000}}
    out = mark_positions([POS], tick_lookup=ticks.get, now_ms=1_099_000, max_age_ms=5_000, contract_index=idx)
    row = out["positions"][0]
    assert row["lp"] == 161.90          # broker value, NOT the stale 999
    assert row["mark_source"] == "broker"


def test_missing_tick_falls_back_to_the_broker_value():
    idx = build_contract_index(CONTRACTS)
    out = mark_positions([POS], tick_lookup={}.get, now_ms=1_000_100, max_age_ms=5_000, contract_index=idx)
    row = out["positions"][0]
    assert row["lp"] == 161.90
    assert row["urmtom"] == 0.0
    assert row["mark_source"] == "broker"


def test_realized_is_never_re_marked():
    """rpnl only moves on a fill; a tick must never touch it."""
    idx = build_contract_index(CONTRACTS)
    pos = {**POS, "rpnl": "1234.50"}
    ticks = {"NSE_FO|47291": {"last_price": 171.90, "ingest_ts": 1_000_000}}
    out = mark_positions([pos], tick_lookup=ticks.get, now_ms=1_000_100, max_age_ms=5_000, contract_index=idx)
    assert out["positions"][0]["rpnl"] == 1234.50
    assert out["day_pnl"] == 3250.0 + 1234.50


def test_unparseable_position_is_passed_through_untouched():
    """A row we cannot identify must still render at its broker values."""
    idx = build_contract_index(CONTRACTS)
    junk = {"exch": "NFO", "tsym": "???", "netqty": "50", "lp": "12.5", "urmtom": "7.5", "rpnl": "0"}
    out = mark_positions([junk], tick_lookup={}.get, now_ms=1, max_age_ms=5_000, contract_index=idx)
    row = out["positions"][0]
    assert row["lp"] == 12.5 and row["urmtom"] == 7.5
    assert row["mark_source"] == "broker"


def test_tick_keys_reports_what_needs_subscribing():
    """The stream auto-follow needs to know which contracts the LIVE book holds."""
    idx = build_contract_index(CONTRACTS)
    out = mark_positions([POS], tick_lookup={}.get, now_ms=1, max_age_ms=5_000, contract_index=idx)
    assert out["tick_keys"] == ["NSE_FO|47291"]
