"""Persistence helpers for option contract metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable


async def upsert_option_contracts(
    db: Any,
    contracts: Iterable[Dict[str, Any]],
    fetched_at: datetime | None = None,
) -> Dict[str, int]:
    """Upsert normalized option contracts by instrument key."""
    sync_time = fetched_at or datetime.now(timezone.utc)
    upserted = 0
    skipped = 0

    for contract in contracts or []:
        instrument_key = contract.get("instrument_key")
        if not instrument_key:
            skipped += 1
            continue

        doc = {**contract, "last_synced_at": sync_time}
        await db.option_contracts.update_one(
            {"instrument_key": instrument_key},
            {"$set": doc},
            upsert=True,
        )
        upserted += 1

    return {"upserted": upserted, "skipped": skipped}
