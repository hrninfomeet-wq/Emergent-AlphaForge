import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import option_coverage_cache  # noqa: E402
from app.option_coverage_cache import (  # noqa: E402
    get_option_coverage_cached,
    read_option_coverage_cache,
    refresh_option_coverage_cache,
)


class _FakeCacheCollection:
    """Minimal async stand-in for a Mongo collection used by the cache."""

    def __init__(self):
        self.docs = {}

    async def update_one(self, query, update, upsert=False):
        key = query["underlying"]
        doc = self.docs.get(key, {})
        doc.update(update["$set"])
        self.docs[key] = doc

    def find(self, query, projection=None):
        underlying = query.get("underlying") if query else None
        rows = [
            {k: v for k, v in doc.items() if k != "_id"}
            for key, doc in self.docs.items()
            if underlying is None or key == underlying
        ]
        return _AsyncCursor(rows)


class _AsyncCursor:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        self._it = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _FakeCacheCollection())


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def patched_compute(monkeypatch):
    """Stub the slow aggregation so tests don't need a real Mongo with 5M docs."""
    calls = {"count": 0}

    async def _fake_get_option_coverage(db, underlying=None):
        calls["count"] += 1
        return {
            "NIFTY": {
                "underlying": "NIFTY",
                "total_candles": 1250,
                "contract_count": 4,
                "first_date": "2024-11-28",
                "last_date": "2024-11-29",
                "days": [{"date": "2024-11-28", "candles": 750}],
            }
        }

    monkeypatch.setattr(option_coverage_cache, "get_option_coverage", _fake_get_option_coverage)
    return calls


@pytest.mark.asyncio
async def test_refresh_populates_cache_for_all_known_underlyings(fake_db, patched_compute):
    result = await refresh_option_coverage_cache(fake_db, underlying=None)
    assert result["NIFTY"]["total_candles"] == 1250

    cached = await read_option_coverage_cache(fake_db)
    # Known underlyings without data get an explicit empty row, so the read
    # path never falls back to the slow aggregation.
    assert set(cached.keys()) == {"NIFTY", "BANKNIFTY", "SENSEX"}
    assert cached["NIFTY"]["total_candles"] == 1250
    assert cached["BANKNIFTY"]["total_candles"] == 0
    assert cached["SENSEX"]["days"] == []


@pytest.mark.asyncio
async def test_cache_hit_does_not_recompute(fake_db, patched_compute):
    await refresh_option_coverage_cache(fake_db, underlying=None)
    assert patched_compute["count"] == 1

    # Subsequent reads through the cached path must NOT trigger another compute.
    await get_option_coverage_cached(fake_db)
    await get_option_coverage_cached(fake_db)
    assert patched_compute["count"] == 1


@pytest.mark.asyncio
async def test_force_refresh_recomputes(fake_db, patched_compute):
    await get_option_coverage_cached(fake_db)  # cold -> compute (1)
    await get_option_coverage_cached(fake_db, force_refresh=True)  # forced (2)
    assert patched_compute["count"] == 2


@pytest.mark.asyncio
async def test_cold_cache_computes_once_then_serves_cached(fake_db, patched_compute):
    # First read is a cache miss -> computes and persists.
    first = await get_option_coverage_cached(fake_db)
    assert first["NIFTY"]["total_candles"] == 1250
    assert patched_compute["count"] == 1

    # Second read served from the now-warm cache.
    second = await get_option_coverage_cached(fake_db)
    assert second["NIFTY"]["total_candles"] == 1250
    assert patched_compute["count"] == 1
