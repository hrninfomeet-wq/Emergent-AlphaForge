"""Tape preservation for sub-minute replay.

The app already records every Upstox ``full``-mode tick (5-level depth, best bid/ask, LTQ, OI) into
``ticks`` — but ``ticks`` carries a 30-day TTL (``db.py``), so a recorded session is deleted a month
later. A session not preserved is a session that can never be replayed. This module copies the
NIFTY / SENSEX index ticks and NFO / BFO option ticks into ``tick_archive`` (no TTL, no BSON date
field — the chain_recorder rule that makes an accidental TTL structurally impossible).

Idempotent: ``$merge`` on ``_id`` with ``keepExisting``. Incremental: only ticks stored since the
archive's high-water mark (minus a 1-hour overlap) are scanned, via the ``stored_at`` TTL index.

Known limitation (docs/scalping/02-measurements.md §1.4): ``persist_ticks`` upserts on
(instrument_key, ltt, session_id), so quote-only updates that share a last-trade time overwrite one
another. At the measured ~1 Hz cadence this loses little, but the tape is not a full quote history.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

log = logging.getLogger("alphaforge.scalping.recorder")

IST = timezone(timedelta(hours=5, minutes=30))
ARCHIVE_COLLECTION = "tick_archive"
KEY_REGEX = r"^(NSE_FO|BSE_FO|NSE_INDEX|BSE_INDEX)\|"
ENV_FLAG = "SCALP_TAPE_ARCHIVE"          # default ON: preservation is read-only on `ticks`
DAILY_AT_IST = (15, 50)                  # after the 15:40 F&O close
OVERLAP = timedelta(hours=1)


def archive_enabled() -> bool:
    return str(os.environ.get(ENV_FLAG, "1")).strip().lower() not in ("0", "false", "no", "off")


def archive_pipeline(since: Optional[datetime]) -> list:
    match: Dict[str, Any] = {"mode": "full", "instrument_key": {"$regex": KEY_REGEX}}
    if since is not None:
        match["stored_at"] = {"$gte": since}
    return [
        {"$match": match},
        {"$set": {"stored_at_ms": {"$cond": [{"$eq": [{"$type": "$stored_at"}, "date"]},
                                             {"$toLong": "$stored_at"}, None]},
                  "archived_from": "ticks"}},
        {"$unset": "stored_at"},
        {"$merge": {"into": ARCHIVE_COLLECTION, "on": "_id", "whenMatched": "keepExisting",
                    "whenNotMatched": "insert"}},
    ]


async def ensure_archive_indexes(db) -> None:
    await db[ARCHIVE_COLLECTION].create_index([("instrument_key", 1), ("ts", 1)])
    await db[ARCHIVE_COLLECTION].create_index([("session_id", 1), ("ts", 1)])
    await db[ARCHIVE_COLLECTION].create_index([("stored_at_ms", -1)])


async def archive_once(db, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Copy new full-mode ticks into the archive. Returns counts; never raises on an empty source.

    The scan is always bounded through the ``stored_at`` TTL index: from the archive's high-water mark
    (minus an overlap), or - on an empty archive - from 31 days ago, which covers everything the TTL
    still holds. An unbounded match would walk the whole 27M-doc collection."""
    await ensure_archive_indexes(db)
    last = await db[ARCHIVE_COLLECTION].find_one({"stored_at_ms": {"$ne": None}}, {"stored_at_ms": 1},
                                                 sort=[("stored_at_ms", -1)])
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=31)
    if last and last.get("stored_at_ms"):
        since = datetime.fromtimestamp(int(last["stored_at_ms"]) / 1000, timezone.utc) - OVERLAP
    before = await db[ARCHIVE_COLLECTION].estimated_document_count()
    cursor = db.ticks.aggregate(archive_pipeline(since), allowDiskUse=True)
    async for _ in cursor:      # $merge yields nothing; iterate to execute
        pass
    after = await db[ARCHIVE_COLLECTION].estimated_document_count()
    return {"since": since.isoformat() if since else None, "archived_before": before, "archived_after": after,
            "added_estimate": after - before}


async def tape_archive_loop() -> None:
    """Archive at boot (catch-up, only OUTSIDE market hours) and daily after the close. Never kills the
    process. The archive indexes are created regardless of the flag, so the status route never scans."""
    from app.db import get_db
    from app.nse_calendar import market_status
    try:
        await ensure_archive_indexes(get_db())
    except Exception:  # noqa: BLE001
        log.exception("Tape archive index creation failed")
    if not archive_enabled():
        log.info("Tape archive disabled (%s=0)", ENV_FLAG)
        return
    # A mid-session redeploy must not start a bulk write into the mongod the live path is using; the
    # boot catch-up is deferred to the post-close run when the market is open.
    first = not market_status(datetime.now(IST)).get("is_open", False)
    while True:
        try:
            now = datetime.now(IST)
            if not first:
                target = now.replace(hour=DAILY_AT_IST[0], minute=DAILY_AT_IST[1], second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                await asyncio.sleep((target - now).total_seconds())
            first = False
            res = await archive_once(get_db())
            log.info("Tape archive: %s", res)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Tape archive iteration failed; retrying next cycle")
            await asyncio.sleep(600)
