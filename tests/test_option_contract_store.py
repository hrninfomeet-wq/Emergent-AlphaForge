import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import option_contract_store  # noqa: E402


class FakeCollection:
    def __init__(self):
        self.calls = []

    async def update_one(self, query, update, upsert=False):
        self.calls.append({"query": query, "update": update, "upsert": upsert})


class FakeDb:
    def __init__(self):
        self.option_contracts = FakeCollection()


def test_upsert_option_contracts_uses_instrument_key_and_stamps_sync_time():
    db = FakeDb()
    fetched_at = datetime(2026, 5, 26, 9, 15, tzinfo=timezone.utc)
    contracts = [
        {"instrument_key": "NSE_FO|1", "underlying": "NIFTY", "expiry_date": "2026-05-26", "strike": 26000, "side": "CE"},
        {"underlying": "NIFTY", "expiry_date": "2026-05-26", "strike": 26000, "side": "PE"},
    ]

    result = asyncio.run(option_contract_store.upsert_option_contracts(db, contracts, fetched_at=fetched_at))

    assert result == {"upserted": 1, "skipped": 1}
    assert db.option_contracts.calls == [
        {
            "query": {"instrument_key": "NSE_FO|1"},
            "update": {
                "$set": {
                    "instrument_key": "NSE_FO|1",
                    "contract_key": "NSE_FO|1|2026-05-26",
                    "underlying": "NIFTY",
                    "expiry_date": "2026-05-26",
                    "strike": 26000,
                    "side": "CE",
                    "last_synced_at": fetched_at,
                },
                "$setOnInsert": {"first_seen_at": fetched_at},
            },
            "upsert": True,
        }
    ]
