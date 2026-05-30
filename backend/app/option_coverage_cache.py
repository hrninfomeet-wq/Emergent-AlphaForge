"""Cached option-coverage summaries.

The raw `get_option_coverage` aggregation scans the entire `options_1m`
collection (5M+ docs) and takes several seconds. That is far too slow to run
on every Data Warehouse page load. This module precomputes the same summary
into a tiny `option_coverage_cache` collection (one small doc per underlying)
so the read path is a single indexed lookup (~milliseconds).

Cache lifecycle:
  - Warmed on backend startup.
  - Refreshed after an option-candle fetch job completes.
  - Refreshed after option data is cleared.
  - Force-refreshable via the API (`?refresh=1`).

The cached payload is byte-for-byte the same shape `get_option_coverage`
returns, so the API response and the frontend heatmap are unchanged.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.option_coverage import get_option_coverage

log = logging.getLogger(__name__)

CACHE_COLLECTION = "option_coverage_cache"
# Underlyings we always want a cache row for, even when empty.
KNOWN_UNDERLYINGS = ("NIFTY", "BANKNIFTY", "SENSEX")

# Single-flight guard: the underlying aggregation scans 5M+ docs and takes
# ~10s. Without this, the startup warm-up and a concurrent page request would
# both launch full aggregations at once (a cache stampede), doubling DB load.
# The lock serializes recomputes; waiters re-check the cache after acquiring it
# and usually find it already populated, so they return without recomputing.
_refresh_lock: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    # Created lazily so it binds to the running event loop (important because
    # the test suite may use a fresh loop per test).
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    return _refresh_lock


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def refresh_option_coverage_cache(
    db: Any,
    underlying: Optional[str] = None,
) -> Dict[str, Any]:
    """Recompute the coverage summary and upsert it into the cache collection.

    If `underlying` is given, only that row is refreshed; otherwise every known
    underlying is refreshed. Returns the freshly computed summary keyed by
    underlying (same shape as `get_option_coverage`).
    """
    computed = await get_option_coverage(db, underlying=underlying)
    computed_at = _now_iso()

    targets = [underlying.upper()] if underlying else list(KNOWN_UNDERLYINGS)
    for inst in targets:
        payload = computed.get(inst)
        if payload is None:
            # No stored candles for this underlying: cache an explicit empty row
            # so the read path never has to fall back to the slow aggregation.
            payload = {
                "underlying": inst,
                "total_candles": 0,
                "contract_count": 0,
                "first_date": None,
                "last_date": None,
                "days": [],
            }
        await db[CACHE_COLLECTION].update_one(
            {"underlying": inst},
            {"$set": {
                "underlying": inst,
                "computed_at": computed_at,
                "data": payload,
            }},
            upsert=True,
        )
    log.info("Option coverage cache refreshed for %s", ", ".join(targets))
    return computed


async def read_option_coverage_cache(
    db: Any,
    underlying: Optional[str] = None,
) -> Dict[str, Any]:
    """Read the cached coverage summary. Returns {} if nothing is cached yet.

    Output shape matches `get_option_coverage`: a dict keyed by underlying.
    """
    query: Dict[str, Any] = {}
    if underlying:
        query["underlying"] = underlying.upper()
    out: Dict[str, Any] = {}
    cursor = db[CACHE_COLLECTION].find(query, {"_id": 0})
    async for doc in cursor:
        inst = doc.get("underlying")
        data = doc.get("data")
        if inst and data is not None:
            out[inst] = data
    return out


async def get_option_coverage_cached(
    db: Any,
    underlying: Optional[str] = None,
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Coverage read path used by the API.

    - force_refresh=True: recompute, update cache, return fresh.
    - cache hit: return cached payload (fast path).
    - cache miss: compute under a single-flight lock, populate cache, return.
    """
    if force_refresh:
        return await refresh_option_coverage_cache(db, underlying=underlying)

    cached = await read_option_coverage_cache(db, underlying=underlying)
    if cached:
        return cached

    # Cold cache: serialize the expensive recompute so concurrent callers
    # (e.g. startup warm-up + first page load) don't stampede the database.
    async with _get_lock():
        # Re-check: another waiter may have populated the cache while we waited.
        cached = await read_option_coverage_cache(db, underlying=underlying)
        if cached:
            return cached
        return await refresh_option_coverage_cache(db, underlying=underlying)
