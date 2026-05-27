"""Response shaping helpers for option warehouse planning."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


HEAVY_ITEM_FIELDS = {"selected_dates", "fetch_dates", "selected_date_counts"}


def _date_bounds(dates: List[str]) -> tuple[str | None, str | None]:
    if not dates:
        return None, None
    ordered = sorted(str(item) for item in dates if item)
    if not ordered:
        return None, None
    return ordered[0], ordered[-1]


def compact_option_plan_for_response(
    plan: Dict[str, Any],
    *,
    max_items: int = 1000,
    max_missing: int = 0,
) -> Dict[str, Any]:
    """Return a UI/API-safe option plan without per-contract date payloads.

    Fetch jobs need exact `selected_dates`, `fetch_dates`, and per-date counts,
    but those arrays can become multi-megabyte responses for 12-18 month plans.
    The UI only needs counts and first/last dates unless a future drill-down is
    added, so this helper keeps coverage auditable while avoiding timeouts.
    """
    source_items = list(plan.get("items", []) or [])
    source_missing = list(plan.get("missing", []) or [])
    result = {
        key: deepcopy(value)
        for key, value in plan.items()
        if key not in ("items", "missing")
    }

    compact_items: List[Dict[str, Any]] = []
    for item in source_items[: max(0, int(max_items or 0))]:
        selected_dates = sorted(str(date) for date in item.get("selected_dates", []) or [] if date)
        fetch_dates = sorted(str(date) for date in item.get("fetch_dates", []) or [] if date)
        first_selected, last_selected = _date_bounds(selected_dates)
        first_fetch, last_fetch = _date_bounds(fetch_dates)
        compact = {
            key: deepcopy(value)
            for key, value in item.items()
            if key not in HEAVY_ITEM_FIELDS
        }
        compact.update({
            "selected_date_count": len(selected_dates),
            "fetch_date_count": len(fetch_dates),
            "first_selected_date": first_selected,
            "last_selected_date": last_selected,
            "first_fetch_date": first_fetch,
            "last_fetch_date": last_fetch,
        })
        compact_items.append(compact)

    result["items"] = compact_items
    result["item_count"] = len(source_items)
    result["items_truncated"] = len(source_items) > len(compact_items)
    result["missing_count"] = len(source_missing)
    result["missing"] = source_missing[: max(0, int(max_missing or 0))]
    result["missing_truncated"] = len(source_missing) > len(result["missing"])

    summary = result.setdefault("summary", {})
    expected = sum(int(item.get("expected_candles", 0) or 0) for item in source_items)
    stored_selected = sum(int(item.get("stored_selected_date_candles", 0) or 0) for item in source_items)
    selected_date_count = sum(len(item.get("selected_dates", []) or []) for item in source_items)
    fetch_date_count = sum(len(item.get("fetch_dates", []) or []) for item in source_items)
    summary["expected_candles_per_selected_dates"] = int(expected)
    summary["stored_selected_date_candles"] = int(stored_selected)
    summary["selected_date_count"] = int(selected_date_count)
    summary["fetch_date_count"] = int(fetch_date_count)
    summary["planned_coverage_pct"] = round(min(100.0, (stored_selected / expected) * 100), 2) if expected else 0.0
    return result
