"""MongoDB connection + serialization helpers (datetime-safe)."""
import os
from datetime import datetime, date
from typing import Any
from motor.motor_asyncio import AsyncIOMotorClient

_client: AsyncIOMotorClient | None = None
_db = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        _client = AsyncIOMotorClient(url)
    return _client


def get_db():
    global _db
    if _db is None:
        name = os.environ.get("DB_NAME", "alphaforge")
        _db = get_client()[name]
    return _db


def serialize_doc(doc: Any) -> Any:
    """Recursively convert datetimes/dates and strip MongoDB _id for JSON safety."""
    if isinstance(doc, dict):
        return {k: serialize_doc(v) for k, v in doc.items() if k != "_id"}
    if isinstance(doc, list):
        return [serialize_doc(item) for item in doc]
    if isinstance(doc, (datetime, date)):
        return doc.isoformat()
    return doc


async def ensure_indexes() -> None:
    db = get_db()
    await db.candles_1m.create_index([("instrument", 1), ("ts", 1)], unique=True)
    await db.warehouse_runs.create_index([("started_at", -1)])
    await db.integrity_hashes.create_index([("instrument", 1), ("date", 1)], unique=True)
    await db.backtest_runs.create_index([("created_at", -1)])
    await db.signals.create_index([("created_at", -1)])
    await db.presets.create_index([("name", 1)], unique=True)
    await db.pretrade_profiles.create_index([("name", 1)], unique=True)
    await db.optimization_jobs.create_index([("created_at", -1)])
