"""Broker download chunk sizing helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


def date_span_days(from_date: str, to_date: str) -> int:
    start = datetime.fromisoformat(from_date).date()
    end = datetime.fromisoformat(to_date).date()
    if start > end:
        raise ValueError("from_date must be before or equal to to_date")
    return (end - start).days + 1


def _manual_or_none(requested_chunk_days: Optional[int]) -> Optional[int]:
    if requested_chunk_days in (None, ""):
        return None
    return max(1, min(int(requested_chunk_days), 30))


def chunk_guidance_for_index(
    from_date: str,
    to_date: str,
    requested_chunk_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Choose broker request chunking for one index instrument."""
    day_count = date_span_days(from_date, to_date)
    manual = _manual_or_none(requested_chunk_days)
    if manual:
        chunk_days = manual
        mode = "manual"
    else:
        chunk_days = 7
        mode = "auto"
    calls = max(1, (day_count + chunk_days - 1) // chunk_days)
    return {
        "mode": mode,
        "chunk_days": chunk_days,
        "calendar_days": day_count,
        "contracts": 1,
        "estimated_api_calls": calls,
        "guidance": "Auto uses conservative 7-day broker calls for one index. Manual 1-3 is safer after failures; 14-30 is faster but heavier.",
    }


def chunk_guidance_for_options(
    from_date: str,
    to_date: str,
    contract_count: int,
    requested_chunk_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Choose broker request chunking for many option contracts."""
    day_count = date_span_days(from_date, to_date)
    manual = _manual_or_none(requested_chunk_days)
    if manual:
        chunk_days = manual
        mode = "manual"
    elif contract_count >= 75:
        chunk_days = 1
        mode = "auto"
    elif contract_count >= 25:
        chunk_days = 2
        mode = "auto"
    elif contract_count >= 10 or day_count > 120:
        chunk_days = 3
        mode = "auto"
    else:
        chunk_days = 7
        mode = "auto"
    calls_per_contract = max(1, (day_count + chunk_days - 1) // chunk_days)
    return {
        "mode": mode,
        "chunk_days": chunk_days,
        "calendar_days": day_count,
        "contracts": int(contract_count),
        "estimated_api_calls": int(contract_count * calls_per_contract),
        "guidance": "Auto uses smaller date chunks as contract count grows. Lower values are slower but more reliable for large option downloads.",
    }
