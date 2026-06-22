"""Pure, host-testable helpers for ATM-strike suggestion.

Two public functions:

    nearest_expiry(contracts, *, today_iso) -> str | None
        From the distinct expiry_date values in contracts, return the nearest
        one that is >= today (the front weekly/monthly).  None if none.

    atm_strike(contracts, *, spot, expiry_date, side="CE") -> dict | None
        Among contracts matching expiry_date + side, return the row whose
        strike is NEAREST to spot (float-tolerant, no strike-step assumption).
        None if none or spot is non-finite.

These helpers contain zero I/O.  The FastAPI route wires them to real sources.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# nearest_expiry
# ---------------------------------------------------------------------------

def nearest_expiry(
    contracts: List[Dict[str, Any]],
    *,
    today_iso: str,
) -> Optional[str]:
    """Return the nearest expiry date >= today_iso among contracts.

    Expiry values are normalised to ISO-date strings (first 10 chars).
    Past expiries are silently skipped.  Returns None if no valid future
    expiry exists (empty list, all past, or garbage expiry values).

    Never raises.
    """
    seen: set[str] = set()
    for row in contracts:
        raw = str(row.get("expiry_date") or "")[:10]
        if len(raw) == 10 and raw >= today_iso:
            seen.add(raw)
    if not seen:
        return None
    return min(seen)


# ---------------------------------------------------------------------------
# atm_strike
# ---------------------------------------------------------------------------

def atm_strike(
    contracts: List[Dict[str, Any]],
    *,
    spot: Any,
    expiry_date: Optional[str],
    side: str = "CE",
) -> Optional[Dict[str, Any]]:
    """Return the contract whose strike is nearest to spot.

    Filters to contracts matching expiry_date AND side (case-insensitive).
    Rows with non-numeric strikes are silently skipped.

    Returns None when:
    * spot is None, NaN, or infinite
    * no matching contracts remain after filtering
    * contracts list is empty

    Ties (equidistant strikes) are broken by taking the lower strike, making
    the result deterministic regardless of input order.

    Never raises.
    """
    # Reject non-finite spot
    try:
        spot_f = float(spot)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(spot_f):
        return None

    if expiry_date is None:
        return None

    side_upper = str(side).upper()
    expiry_str = str(expiry_date)

    best_row: Optional[Dict[str, Any]] = None
    best_dist = float("inf")
    best_strike = float("inf")

    for row in contracts:
        # Filter by expiry and side
        if str(row.get("expiry_date") or "")[:10] != expiry_str[:10]:
            continue
        if str(row.get("side") or "").upper() != side_upper:
            continue
        # Parse strike (skip non-numeric)
        try:
            row_strike = float(row.get("strike", ""))
        except (TypeError, ValueError):
            continue

        dist = abs(row_strike - spot_f)
        # Tie-break: prefer lower strike (deterministic)
        if dist < best_dist or (dist == best_dist and row_strike < best_strike):
            best_dist = dist
            best_strike = row_strike
            best_row = row

    return best_row
