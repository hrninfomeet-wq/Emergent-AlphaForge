"""Backfill expired option contract metadata for historical option research."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

FetchExpiries = Callable[[str], Awaitable[List[str]]]
FetchContracts = Callable[[str, str], Awaitable[List[Dict[str, Any]]]]
UpsertContracts = Callable[[Any, Iterable[Dict[str, Any]]], Awaitable[Dict[str, int]]]


def _valid_date(value: str) -> Optional[str]:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return None


def select_expiries_for_range(expiries: Iterable[str], *, from_date: str, to_date: str) -> List[str]:
    """Return sorted expiry dates inside an inclusive ISO date range."""
    start = _valid_date(from_date)
    end = _valid_date(to_date)
    if not start or not end:
        raise ValueError("from_date and to_date must be YYYY-MM-DD")
    if start > end:
        raise ValueError("from_date must be before or equal to to_date")

    cleaned = sorted({
        date
        for raw in expiries or []
        for date in [_valid_date(str(raw))]
        if date and start <= date <= end
    })
    return cleaned


async def backfill_expired_option_contracts(
    db: Any,
    instrument: str,
    *,
    from_date: str,
    to_date: str,
    max_expiries: int = 12,
    confirm_large_fetch: bool = False,
    fetch_expiries: Optional[FetchExpiries] = None,
    fetch_expired_contracts: Optional[FetchContracts] = None,
    upsert_contracts: Optional[UpsertContracts] = None,
) -> Dict[str, Any]:
    """Fetch expired option contracts by expiry date and persist metadata locally."""
    if fetch_expiries is None or fetch_expired_contracts is None:
        from app import upstox_client

        fetch_expiries = fetch_expiries or upstox_client.fetch_expiries
        fetch_expired_contracts = fetch_expired_contracts or upstox_client.fetch_expired_option_contracts
    if upsert_contracts is None:
        from app.option_contract_store import upsert_option_contracts

        upsert_contracts = upsert_option_contracts

    underlying = str(instrument or "").upper()
    expiries = select_expiries_for_range(
        await fetch_expiries(underlying),
        from_date=from_date,
        to_date=to_date,
    )
    max_allowed = max(1, int(max_expiries or 1))
    if len(expiries) > max_allowed and not confirm_large_fetch:
        return {
            "status": "blocked",
            "underlying": underlying,
            "from_date": from_date,
            "to_date": to_date,
            "expiry_count": len(expiries),
            "expiries": expiries,
            "fetched_contracts": 0,
            "upserted": 0,
            "skipped": 0,
            "reason": f"Backfill would fetch {len(expiries)} expiries, above max_expiries={max_allowed}.",
        }

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    await db.warehouse_runs.insert_one({
        "id": run_id,
        "instrument": underlying,
        "source": "upstox_expired_option_contracts",
        "collection": "option_contracts",
        "started_at": started_at,
        "status": "running",
        "from_date": from_date,
        "to_date": to_date,
        "expiry_count": len(expiries),
    })

    fetched_contracts = 0
    upserted = 0
    skipped = 0
    failed: List[Dict[str, str]] = []
    per_expiry: List[Dict[str, Any]] = []

    for expiry in expiries:
        try:
            contracts = await fetch_expired_contracts(underlying, expiry)
            saved = await upsert_contracts(db, contracts)
            fetched_count = len(contracts)
            fetched_contracts += fetched_count
            upserted += int(saved.get("upserted", 0) or 0)
            skipped += int(saved.get("skipped", 0) or 0)
            per_expiry.append({
                "expiry": expiry,
                "fetched": fetched_count,
                "upserted": int(saved.get("upserted", 0) or 0),
                "skipped": int(saved.get("skipped", 0) or 0),
            })
        except Exception as exc:
            failed.append({"expiry": expiry, "error": str(exc)[:300]})

    if failed and fetched_contracts:
        status = "partial"
    elif failed:
        status = "failed"
    else:
        status = "ok"

    await db.warehouse_runs.update_one(
        {"id": run_id},
        {"$set": {
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "fetched_contracts": fetched_contracts,
            "upserted": upserted,
            "skipped": skipped,
            "failed": failed[:100],
        }},
    )
    return {
        "run_id": run_id,
        "status": status,
        "underlying": underlying,
        "from_date": from_date,
        "to_date": to_date,
        "expiry_count": len(expiries),
        "expiries": expiries,
        "fetched_contracts": fetched_contracts,
        "upserted": upserted,
        "skipped": skipped,
        "per_expiry": per_expiry,
        "failed": failed,
    }
