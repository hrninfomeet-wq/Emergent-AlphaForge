import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import option_candles  # noqa: E402


class FakeUpdateResult:
    def __init__(self, upserted_id=None, modified_count=0):
        self.upserted_id = upserted_id
        self.modified_count = modified_count


class FakeCollection:
    def __init__(self):
        self.calls = []

    async def update_one(self, query, update, upsert=False):
        self.calls.append({"query": query, "update": update, "upsert": upsert})
        return FakeUpdateResult(upserted_id="new")


class FakeDb:
    def __init__(self):
        self.options_1m = FakeCollection()


def test_candles_to_df_normalizes_option_rows_with_oi_and_contract_metadata():
    candles = [["2026-05-26T09:15:00+05:30", 100, 110, 95, 105, 1234, 555]]
    contract = {
        "underlying": "NIFTY",
        "expiry_date": "2026-05-26",
        "strike": 26000.0,
        "side": "CE",
        "trading_symbol": "NIFTY 26000 CE",
    }

    df = option_candles.candles_to_df(candles, instrument_key="NSE_FO|123", contract=contract)

    row = df.to_dict(orient="records")[0]
    assert row["instrument_key"] == "NSE_FO|123"
    assert row["underlying"] == "NIFTY"
    assert row["expiry_date"] == "2026-05-26"
    assert row["strike"] == 26000.0
    assert row["side"] == "CE"
    assert row["open"] == 100.0
    assert row["volume"] == 1234.0
    assert row["oi"] == 555.0


def test_persist_option_candles_upserts_by_instrument_key_and_ts():
    db = FakeDb()
    df = option_candles.candles_to_df(
        [["2026-05-26T09:15:00+05:30", 100, 110, 95, 105, 1234, 555]],
        instrument_key="NSE_FO|123",
        contract={"underlying": "NIFTY", "expiry_date": "2026-05-26", "strike": 26000.0, "side": "CE"},
    )

    result = asyncio.run(option_candles.persist_option_candles_df(db, df))

    assert result == {"candles_added": 1, "candles_updated": 0}
    call = db.options_1m.calls[0]
    assert call["query"] == {"instrument_key": "NSE_FO|123", "ts": int(df.iloc[0]["ts"])}
    assert call["update"]["$set"]["underlying"] == "NIFTY"
    assert call["update"]["$set"]["oi"] == 555.0
    assert call["upsert"] is True
