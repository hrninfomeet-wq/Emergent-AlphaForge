import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.expired_contract_backfill import backfill_expired_option_contracts, select_expiries_for_range  # noqa: E402
from tests.contract_corpus import backend_api_text


class FakeCollection:
    def __init__(self):
        self.inserted = []
        self.updated = []

    async def insert_one(self, doc):
        self.inserted.append(doc)

    async def update_one(self, query, update):
        self.updated.append({"query": query, "update": update})


class FakeDb:
    def __init__(self):
        self.warehouse_runs = FakeCollection()
        self.upserted_batches = []


def test_select_expiries_for_range_filters_and_sorts_dates():
    result = select_expiries_for_range(
        ["2026-05-14", "2026-05-28", "2026-05-21", "bad-date", "2026-06-04"],
        from_date="2026-05-20",
        to_date="2026-05-31",
    )

    assert result == ["2026-05-21", "2026-05-28"]


def test_backfill_expired_option_contracts_guards_large_ranges():
    async def fetch_expiries(_instrument):
        return ["2026-05-07", "2026-05-14", "2026-05-21"]

    async def fetch_contracts(_instrument, _expiry):
        raise AssertionError("contract fetch should not run when guard blocks")

    result = asyncio.run(backfill_expired_option_contracts(
        FakeDb(),
        "NIFTY",
        from_date="2026-05-01",
        to_date="2026-05-31",
        max_expiries=2,
        confirm_large_fetch=False,
        fetch_expiries=fetch_expiries,
        fetch_expired_contracts=fetch_contracts,
    ))

    assert result["status"] == "blocked"
    assert result["expiry_count"] == 3
    assert result["fetched_contracts"] == 0


def test_backfill_expired_option_contracts_fetches_and_persists_each_expiry():
    db = FakeDb()

    async def fetch_expiries(_instrument):
        return ["2026-05-21", "2026-05-28"]

    async def fetch_contracts(instrument, expiry):
        return [
            {"instrument_key": f"{instrument}-{expiry}-CE", "expiry_date": expiry, "side": "CE"},
            {"instrument_key": f"{instrument}-{expiry}-PE", "expiry_date": expiry, "side": "PE"},
        ]

    async def upsert(_db, contracts):
        _db.upserted_batches.append(list(contracts))
        return {"upserted": len(contracts), "skipped": 0}

    result = asyncio.run(backfill_expired_option_contracts(
        db,
        "NIFTY",
        from_date="2026-05-01",
        to_date="2026-05-31",
        max_expiries=5,
        confirm_large_fetch=False,
        fetch_expiries=fetch_expiries,
        fetch_expired_contracts=fetch_contracts,
        upsert_contracts=upsert,
    ))

    assert result["status"] == "ok"
    assert result["expiries"] == ["2026-05-21", "2026-05-28"]
    assert result["fetched_contracts"] == 4
    assert result["upserted"] == 4
    assert result["skipped"] == 0
    assert len(db.upserted_batches) == 2
    assert db.warehouse_runs.inserted[0]["source"] == "upstox_expired_option_contracts"
    assert db.warehouse_runs.updated[0]["update"]["$set"]["status"] == "ok"


def test_backend_exposes_expired_contract_backfill_route():
    server = backend_api_text()

    assert "ExpiredOptionContractBackfillReq" in server
    assert '@api.post("/upstox/expired-options/contracts/{instrument}/sync")' in server
