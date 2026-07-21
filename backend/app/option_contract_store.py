"""Persistence helpers for option contract metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable

from app.instruments import contract_identity_key


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

        doc = {
            **contract,
            "contract_key": contract.get("contract_key") or contract_identity_key(
                instrument_key, contract.get("expiry_date")),
            "last_synced_at": sync_time,
        }
        # Only the current/BOD-like master proves the contract was observable at
        # this time.  An expired-contract lookup is retrospective evidence.
        if str(contract.get("source") or "") == "current_option_contract":
            doc["master_snapshot_at"] = sync_time
        await db.option_contracts.update_one(
            {"instrument_key": instrument_key},
            {"$set": doc, "$setOnInsert": {"first_seen_at": sync_time}},
            upsert=True,
        )
        upserted += 1

    return {"upserted": upserted, "skipped": skipped}
