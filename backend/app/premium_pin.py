"""Subscription pinning for premium-momentum locked strikes (Track B).

Today NOTHING pins an option key: the live subscription is an ATM-centered band
that is periodically rebuilt, so a strike locked at 09:31 silently drops out of
the tick feed when spot drifts. This helper returns today's locked keys so every
subscription (re)build unions them in — cap-exempt, same as open paper keys.
Fail-soft: any store error returns [] (a pin failure must never break a stream
restart; the monitor then HOLDs and entries refuse visibly)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from app.premium_lock_store import today_locked_keys

log = logging.getLogger(__name__)


def _today_ist() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


async def premium_pin_keys(locks_col: Any, *, now_session_date: Optional[str] = None) -> List[str]:
    try:
        return await today_locked_keys(locks_col, session_date=now_session_date or _today_ist())
    except Exception as exc:
        log.warning("premium_pin_keys failed (%s) — no pins this rebuild", exc)
        return []
