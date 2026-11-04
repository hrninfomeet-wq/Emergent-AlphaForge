"""Isolated storage and quote adapters for the real chart watchlist router.

Only the explicit allowlisted database can be opened. This module never imports
production runtime, broker wiring, or the production database configuration.
"""
import asyncio
import copy
import time
from types import SimpleNamespace

FIXTURE_DATABASE = 'alphaforge_chart_terminal_fixture_20261104'


class MemoryCollection:
    def __init__(self):
        self.doc = None

    async def find_one(self, query):
        return copy.deepcopy(self.doc)

    async def insert_one(self, doc):
        from pymongo.errors import DuplicateKeyError
        if self.doc is not None:
            raise DuplicateKeyError('Fixture default already exists')
        self.doc = copy.deepcopy(doc)

    async def replace_one(self, query, doc, upsert=False):
        matched = self.doc is not None and all(self.doc.get(k) == v for k, v in query.items())
        if matched:
            self.doc = copy.deepcopy(doc)
        return SimpleNamespace(matched_count=int(matched))


def open_collection(database=None):
    """Return (collection, client); no implicit durable storage or DB drops."""
    if database is None:
        return MemoryCollection(), None
    if database != FIXTURE_DATABASE:
        raise ValueError(f'Only isolated database {FIXTURE_DATABASE!r} is permitted')
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient('mongodb://127.0.0.1:27017', serverSelectionTimeoutMS=3000)
    return client[FIXTURE_DATABASE].chart_watchlists, client


class SyntheticQuoteManager:
    def __init__(self, instruments, price):
        self.instruments, self.price = instruments, price
        self.queues = set()
        self.leases = {}

    def subscribe(self, *, max_queue, instrument_keys):
        queue = asyncio.Queue(maxsize=max_queue)
        self.queues.add(queue)
        return queue

    def unsubscribe(self, queue):
        self.queues.discard(queue)

    async def acquire_quote_batch(self, keys):
        lease = object()
        self.leases[lease] = tuple(keys)
        return lease

    async def release_quote_batch(self, lease):
        self.leases.pop(lease, None)

    def status(self):
        return {'connected': True}

    def latest_tick_map(self):
        now = int(time.time() * 1000)
        return {item['instrument_key']: {'last_price': self.price(item['instrument_key']),
                'close_price': self.price(item['instrument_key']) - 1,
                'ts': now, 'ingest_ts': now} for item in self.instruments}
