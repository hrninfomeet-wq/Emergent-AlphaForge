"""Tests for the option-chain recorder's I/O path.

The parser is covered by `test_chain_snapshot.py`. What is pinned here is the
behaviour that decides whether the dataset is TRUSTWORTHY rather than merely
present:

* an empty or failed fetch writes NOTHING — a run of empty snapshots is worse
  than a gap, because a gap is visible and an empty row looks like a reading;
* capture timestamps are bucketed, so a restart mid-minute cannot silently
  double-record and later analysis gets evenly spaced samples;
* the market gate follows `nse_calendar`, including the two phases the
  2026-08-03 session split introduced (`cas`, `derivatives_only`) where the
  index is frozen or settled but OPTIONS STILL TRADE — the whole point of the
  capture;
* one instrument failing never stops the others.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.chain_recorder import (  # noqa: E402
    CAPTURE_INTERVAL_SEC,
    RECORDED_INSTRUMENTS,
    bucket_ts_ms,
    capture_once,
    should_capture,
)

IST = timezone(timedelta(hours=5, minutes=30))


class FakeCollection:
    def __init__(self):
        self.inserted = []
        self.raise_duplicate = False

    async def insert_one(self, doc):
        if self.raise_duplicate:
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError("duplicate key")
        self.inserted.append(doc)


class FakeDb:
    def __init__(self):
        self.chain_snapshots = FakeCollection()


def _leg():
    return {
        "instrument_key": "NSE_FO|1",
        "market_data": {"ltp": 100.0, "oi": 1000.0, "volume": 10.0,
                        "bid_price": 99.5, "ask_price": 100.5},
        "option_greeks": {"iv": 12.0, "delta": 0.5},
    }


def _payload(strikes=(24200.0, 24250.0)):
    return {"status": "success", "data": [
        {"expiry": "2026-08-27", "pcr": 0.9, "strike_price": s,
         "underlying_key": "NSE_INDEX|Nifty 50", "underlying_spot_price": 24252.0,
         "call_options": _leg(), "put_options": _leg()}
        for s in strikes
    ]}


def _run(db, instrument="NIFTY", *, payload=None, fetch_exc=None, expiry="2026-08-27",
         now=None):
    async def fetch_chain(instrument_key, expiry_date):
        if fetch_exc is not None:
            raise fetch_exc
        return payload

    async def next_expiry(_db, _inst):
        return expiry

    return asyncio.run(capture_once(
        db, instrument, now=now or datetime(2026, 8, 27, 11, 30, 20, tzinfo=IST),
        force=True, fetch_chain=fetch_chain, next_expiry=next_expiry,
    ))


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

def test_a_good_fetch_writes_exactly_one_snapshot():
    db = FakeDb()
    res = _run(db, payload=_payload())
    assert res["ok"] is True
    assert res["reason"] == "written"
    assert res["strike_count"] == 2
    assert len(db.chain_snapshots.inserted) == 1
    doc = db.chain_snapshots.inserted[0]
    assert doc["instrument"] == "NIFTY"
    assert doc["expiry_date"] == "2026-08-27"
    assert doc["session_date"] == "2026-08-27"
    assert doc["strike_count"] == 2
    assert doc["strikes"][0]["ce"]["bid_price"] == pytest.approx(99.5)


def test_the_document_records_both_the_bucket_and_the_true_fetch_time():
    """`ts` is the join/dedup key; `captured_at_ms` is when the read actually
    happened. Spread calibration needs the real time, joins need the bucket."""
    db = FakeDb()
    now = datetime(2026, 8, 27, 11, 30, 47, tzinfo=IST)
    _run(db, payload=_payload(), now=now)
    doc = db.chain_snapshots.inserted[0]
    true_ms = int(now.timestamp() * 1000)
    assert doc["ts"] == bucket_ts_ms(true_ms)
    assert doc["captured_at_ms"] == true_ms
    assert doc["ts"] <= doc["captured_at_ms"]


def test_no_bson_date_field_is_written_so_a_ttl_can_never_be_attached():
    """`ticks` carries a 30-day TTL. A TTL index requires a BSON date field; if
    this collection never stores one, an accidental TTL cannot delete history
    that is by definition unrecoverable."""
    db = FakeDb()
    _run(db, payload=_payload())
    doc = db.chain_snapshots.inserted[0]
    for key, value in doc.items():
        assert not isinstance(value, datetime), f"{key} is a BSON date — TTL-able"


# ---------------------------------------------------------------------------
# Nothing is ever written on a bad read
# ---------------------------------------------------------------------------

def test_a_fetch_that_raises_writes_nothing_and_does_not_propagate():
    db = FakeDb()
    res = _run(db, fetch_exc=RuntimeError("Upstox token expired or invalid."))
    assert res["ok"] is False
    assert res["reason"] == "fetch_failed"
    assert "token expired" in (res.get("error") or "")
    assert db.chain_snapshots.inserted == []


def test_an_empty_chain_is_not_recorded_as_a_reading():
    db = FakeDb()
    res = _run(db, payload={"status": "success", "data": []})
    assert res["ok"] is False
    assert res["reason"] == "empty_chain"
    assert db.chain_snapshots.inserted == []


def test_no_resolvable_expiry_writes_nothing():
    db = FakeDb()
    res = _run(db, payload=_payload(), expiry=None)
    assert res["ok"] is False
    assert res["reason"] == "no_expiry"
    assert db.chain_snapshots.inserted == []


def test_a_duplicate_bucket_is_swallowed_rather_than_crashing_the_loop():
    db = FakeDb()
    db.chain_snapshots.raise_duplicate = True
    res = _run(db, payload=_payload())
    assert res["ok"] is False
    assert res["reason"] == "duplicate"


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------

def test_bucket_floors_to_the_interval():
    base = 1_787_000_000_000
    base -= base % (CAPTURE_INTERVAL_SEC * 1000)
    for offset_sec in (0, 1, 17, CAPTURE_INTERVAL_SEC - 1):
        assert bucket_ts_ms(base + offset_sec * 1000) == base
    assert bucket_ts_ms(base + CAPTURE_INTERVAL_SEC * 1000) == base + CAPTURE_INTERVAL_SEC * 1000


def test_two_captures_inside_one_bucket_share_a_timestamp():
    """Which is what makes the unique index able to reject the second."""
    db = FakeDb()
    now_a = datetime(2026, 8, 27, 11, 30, 5, tzinfo=IST)
    now_b = datetime(2026, 8, 27, 11, 30, 55, tzinfo=IST)
    _run(db, payload=_payload(), now=now_a)
    _run(db, payload=_payload(), now=now_b)
    assert db.chain_snapshots.inserted[0]["ts"] == db.chain_snapshots.inserted[1]["ts"]


# ---------------------------------------------------------------------------
# The market gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phase,expected", [
    ("open", True),
    ("cas", True),                # index frozen, OPTIONS STILL TRADE
    ("derivatives_only", True),   # cash settled, F&O running
    ("pre_open", False),
    ("closed", False),
    ("holiday", False),
    ("weekend", False),
])
def test_the_gate_captures_exactly_when_options_are_tradeable(phase, expected):
    market = {"phase": phase, "is_open": phase in ("open", "cas", "derivatives_only"),
              "is_trading_day": phase not in ("holiday", "weekend")}
    assert should_capture(market) is expected


def test_the_gate_fails_closed_on_an_unusable_status():
    for bad in (None, {}, {"phase": "open"}, "nonsense"):
        assert should_capture(bad) is False


def test_recorded_instruments_are_the_three_option_underlyings():
    from app.instruments import INSTRUMENT_KEYS
    assert set(RECORDED_INSTRUMENTS) <= set(INSTRUMENT_KEYS)
    assert "NIFTY" in RECORDED_INSTRUMENTS and "SENSEX" in RECORDED_INSTRUMENTS


# ---------------------------------------------------------------------------
# The writer applies the gate too — defense in depth
# ---------------------------------------------------------------------------

def _run_gated(db, now, *, payload=None):
    async def fetch_chain(instrument_key, expiry_date):
        return payload if payload is not None else _payload()

    async def next_expiry(_db, _inst):
        return "2026-08-27"

    return asyncio.run(capture_once(db, "NIFTY", now=now,
                                    fetch_chain=fetch_chain, next_expiry=next_expiry))


def test_the_writer_refuses_an_off_hours_capture_even_when_called_directly():
    """Off hours the endpoint still answers, with the PREVIOUS session's closing
    book. Storing that under today's session_date records a chain on a date it
    never traded. A smoke test at 00:11 IST wrote three such rows before this
    guard existed — the loop's gate alone was not enough, because the loop is
    not the only possible caller.
    """
    db = FakeDb()
    res = _run_gated(db, datetime(2026, 8, 27, 0, 11, 0, tzinfo=IST))
    assert res["ok"] is False
    assert res["reason"] == "market_closed"
    assert db.chain_snapshots.inserted == []


def test_a_weekend_capture_is_refused():
    db = FakeDb()
    # 2026-08-29 is a Saturday.
    res = _run_gated(db, datetime(2026, 8, 29, 11, 30, 0, tzinfo=IST))
    assert res["reason"] == "market_closed"
    assert db.chain_snapshots.inserted == []


def test_an_in_hours_capture_passes_the_gate_without_force():
    db = FakeDb()
    res = _run_gated(db, datetime(2026, 8, 27, 11, 30, 0, tzinfo=IST))
    assert res["ok"] is True, res
    assert len(db.chain_snapshots.inserted) == 1


def test_force_is_the_documented_escape_hatch_for_a_deliberate_read():
    db = FakeDb()
    res = _run(db, payload=_payload(), now=datetime(2026, 8, 27, 0, 11, 0, tzinfo=IST))
    assert res["ok"] is True
    assert len(db.chain_snapshots.inserted) == 1


@pytest.mark.parametrize("phase", ["closed", "pre_open", "holiday", "weekend",
                                   "muhurat", "block_deal", ""])
def test_an_is_open_flag_cannot_override_a_non_tradeable_phase(phase):
    """The phase allowlist is not redundant with `is_open`.

    Today `market_status` derives one from the other, so a consistent payload
    never exercises this branch — which is why mutating the phase check away
    left every other test green. It guards the case where the two disagree: a
    future phase added to the calendar with `is_open=True` (a Muhurat evening
    session, a block-deal window) would otherwise be captured silently, and a
    chain recorded under the wrong session semantics cannot be un-recorded.
    """
    assert should_capture({"is_open": True, "phase": phase}) is False


def test_a_tradeable_phase_still_needs_is_open():
    """Both conditions are necessary; neither alone is sufficient."""
    assert should_capture({"is_open": False, "phase": "open"}) is False
    assert should_capture({"is_open": True, "phase": "open"}) is True
