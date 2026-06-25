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
    await db.warehouse_runs.create_index([("status", 1), ("updated_at", -1)])
    await db.integrity_hashes.create_index([("instrument", 1), ("date", 1)], unique=True)
    await db.backtest_runs.create_index([("created_at", -1)])
    await db.signals.create_index([("created_at", -1)])
    # Idempotency guard for the deployment evaluator: ensure no two signals exist
    # for the same (deployment_id, candle_ts) pair. Partial index so manual research
    # signals (which have no deployment_id) are unaffected.
    await db.signals.create_index(
        [("deployment_id", 1), ("candle_ts", 1)],
        unique=True,
        name="signals_deployment_bar_unique",
        partialFilterExpression={"deployment_id": {"$exists": True, "$type": "string"}},
    )
    await db.strategy_deployments.create_index([("created_at", -1)])
    await db.strategy_deployments.create_index([("status", 1), ("updated_at", -1)])
    await db.strategy_deployments.create_index([("source_type", 1), ("source_id", 1)])
    await db.option_contracts.create_index([("underlying", 1), ("expiry_date", 1), ("strike", 1), ("side", 1)])
    await db.option_contracts.create_index([("instrument_key", 1)], unique=True)
    await db.options_1m.create_index([("instrument_key", 1), ("ts", 1)], unique=True)
    await db.options_1m.create_index([("underlying", 1), ("expiry_date", 1), ("strike", 1), ("side", 1), ("ts", 1)])
    await db.option_coverage_cache.create_index([("underlying", 1)], unique=True)
    # Broker-empty ledger: band pairs a clean fetch proved Upstox has no data
    # for (data_hygiene.KNOWN_EMPTY_COLLECTION). One doc per pair, never
    # re-requested, excluded from hygiene missing counts.
    await db.option_known_empty.create_index(
        [("underlying", 1), ("date", 1), ("expiry", 1), ("side", 1), ("strike", 1)],
        unique=True,
    )
    await db.ticks.create_index([("session_id", 1), ("ts", 1)])
    await db.ticks.create_index([("instrument_key", 1), ("ts", 1)])
    await db.chain_snapshots.create_index([("created_at", -1)])
    await db.paper_trades.create_index([("created_at", -1)])
    await db.paper_trades.create_index([("status", 1), ("updated_at", -1)])
    await db.paper_trades.create_index([("deployment_id", 1), ("status", 1), ("closed_at", -1)])
    await db.presets.create_index([("name", 1)], unique=True)
    await db.pretrade_profiles.create_index([("name", 1)], unique=True)
    await db.optimization_jobs.create_index([("created_at", -1)])
    await db.strategy_lifecycle.create_index("strategy_id", unique=True)
    # Live execution: the unique index on live_orders.client_order_id is the REAL
    # dup-order race guard (idempotency.record_intent's DuplicateKeyError fallback
    # depends on it). Without it a crashed-then-resumed same-cid submit could reach
    # the broker twice. Declared in app.live.idempotency; imported lazily to avoid
    # any import-order coupling from db.py.
    from app.live.idempotency import ensure_indexes as _ensure_live_order_indexes
    await _ensure_live_order_indexes(db.live_orders)
    # live_trades (auto-live deployment trades): index per-deployment so the
    # live-cap governor + /live/status counters do an index scan, not a full
    # ~all-history collection scan on every poll. Mirrors the paper_trades indexes.
    await db.live_trades.create_index([("created_at", -1)])
    await db.live_trades.create_index([("deployment_id", 1), ("created_at", -1)])
    await db.live_trades.create_index([("deployment_id", 1), ("status", 1), ("closed_at", -1)])
