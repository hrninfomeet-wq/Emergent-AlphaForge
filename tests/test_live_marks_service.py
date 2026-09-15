"""The service that feeds the live-marks stream.

The stream emits at ~10Hz. These tests pin that the two expensive inputs behind
it — the broker REST book and the option_contracts index — do NOT run at that
rate, while the free input (the in-memory tick map) does.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_marks_service import LiveMarksService

POS = {
    "exch": "NFO", "tsym": "NIFTY15SEP26C23350", "token": "47291",
    "dname": "NIFTY 15SEP26 23350 CE ", "netqty": "325", "netavgprc": "161.90",
    "lp": "161.90", "urmtom": "0.00", "rpnl": "0", "mult": "1", "prcftr": "1",
}
CONTRACT = {"instrument_key": "NSE_FO|47291", "underlying": "NIFTY",
            "expiry_date": "2026-09-15", "strike": 23350, "side": "CE"}


def _svc(*, positions=None, ticks=None, counters=None):
    counters = counters if counters is not None else {}
    counters.setdefault("broker", 0)
    counters.setdefault("contracts", 0)
    book = positions if positions is not None else [POS]

    async def fetch_positions():
        counters["broker"] += 1
        return list(book() if callable(book) else book)

    async def load_contracts(idents):
        counters["contracts"] += 1
        return [CONTRACT]

    return LiveMarksService(
        fetch_positions=fetch_positions,
        load_contracts=load_contracts,
        tick_map_factory=lambda: (ticks() if callable(ticks) else (ticks or {})),
        now_ms=lambda: 1_000_000,
    ), counters


def test_marks_from_the_tick_not_the_broker_lp():
    svc, _ = _svc(ticks={"NSE_FO|47291": {"last_price": 171.90, "ingest_ts": 999_900}})
    out = asyncio.run(svc.payload())
    row = out["positions"][0]
    assert row["lp"] == 171.90
    assert row["urmtom"] == 3250.0
    assert row["mark_source"] == "tick"
    assert out["day_pnl"] == 3250.0


def test_ten_hz_of_emits_costs_one_broker_read_and_one_index_build():
    """This is the whole constraint: streaming must not multiply broker calls."""
    svc, counters = _svc(ticks={"NSE_FO|47291": {"last_price": 171.90, "ingest_ts": 999_900}})

    async def run():
        for _ in range(150):  # 15s at 10Hz
            await svc.payload()

    asyncio.run(run())
    assert counters["broker"] == 1, f"{counters['broker']} broker reads for 150 emits"
    assert counters["contracts"] == 1, f"{counters['contracts']} index builds for 150 emits"


def test_the_index_rebuilds_only_when_the_position_set_changes():
    book = {"rows": [POS]}
    svc, counters = _svc(positions=lambda: book["rows"],
                         ticks={"NSE_FO|47291": {"last_price": 171.9, "ingest_ts": 999_900}})

    async def run():
        await svc.payload()
        await svc.payload()
        assert counters["contracts"] == 1
        # A new contract appears in the book -> the index must be rebuilt.
        book["rows"] = [POS, {**POS, "dname": "NIFTY 15SEP26 23400 CE ",
                              "tsym": "NIFTY15SEP26C23400"}]
        svc._cache._fetched_at = None  # force a broker refresh, as the 15s tick would
        await svc.payload()

    asyncio.run(run())
    assert counters["contracts"] == 2


def test_price_moves_between_emits_without_touching_the_broker():
    """The point of the whole design: LTP updates at tick rate, broker at 15s."""
    price = {"p": 100.0}
    svc, counters = _svc(
        positions=[{**POS, "lp": "100.00", "urmtom": "0.00"}],
        ticks=lambda: {"NSE_FO|47291": {"last_price": price["p"], "ingest_ts": 999_900}},
    )

    async def run():
        seen = []
        for step in range(5):
            price["p"] = 100.0 + step
            out = await svc.payload()
            seen.append(out["positions"][0]["lp"])
        return seen

    seen = asyncio.run(run())
    assert seen == [100.0, 101.0, 102.0, 103.0, 104.0]
    assert counters["broker"] == 1


def test_payload_carries_the_freshness_the_ui_needs_to_be_honest():
    svc, _ = _svc(ticks={"NSE_FO|47291": {"last_price": 171.9, "ingest_ts": 999_900}})
    out = asyncio.run(svc.payload())
    assert out["broker_age_ms"] is not None
    assert out["broker_stale"] is False
    assert out["emitted_at_ms"] == 1_000_000
    assert out["marked"] == 1 and out["count"] == 1


def test_no_contract_row_degrades_to_the_broker_value_rather_than_guessing():
    async def no_contracts(_idents):
        return []

    svc, _ = _svc(ticks={"NSE_FO|47291": {"last_price": 171.9, "ingest_ts": 999_900}})
    svc._load_contracts = no_contracts
    out = asyncio.run(svc.payload())
    row = out["positions"][0]
    assert row["mark_source"] == "broker"
    assert row["lp"] == 161.90
