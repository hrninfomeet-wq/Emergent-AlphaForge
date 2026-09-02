"""Opt-in `pct_of_index` search bounds for point-denominated parameters.

WHY
---
Parameters like `spot_target_pts` / `spot_stop_pts` are ABSOLUTE INDEX POINTS, so
a search box means a different thing on every instrument. Measured on the
2026-09-01 `explosive_reversal` jobs, which ran BYTE-IDENTICAL overrides
(`spot_target_pts` max 200, `spot_stop_pts` max 80) on both indices:

    NIFTY   median close 24,468   median 1m true range 0.0320% of index
    SENSEX  median close 80,176   median 1m true range 0.0328% of index

Same relative volatility, ~3.28x the point scale. That box comfortably holds
NIFTY's +Rs 514,052 optimum (stop 77.16 pts = 0.317% of index — a true INTERIOR
peak; widening the bound does not improve NIFTY). On SENSEX the same geometry
needs ~253 points and the ceiling was 80, so the profitable region was not merely
unfound, it was unreachable: the search fell into stops of 5-9 points (~0.3x of a
single 1-minute bar's range), and the best of its 50 re-ranked candidates lost
Rs 932,976.

Expressing the bound as a PERCENT OF INDEX makes it mean the same thing
everywhere. 0.317% is 77.6 points on NIFTY and 254 on SENSEX.

DESIGN CONSTRAINTS
------------------
1. STRICTLY OPT-IN. The default (`points`) returns the overrides untouched, so
   every existing job, preset and stored result stays reproducible byte-for-byte.
   `tests/test_bounds_unit.py` pins this.
2. The conversion happens BEFORE `optimizer._build_param_space`, which therefore
   never learns about units and is not modified at all. What the search actually
   ran under is still recorded in the job's `param_space` — already persisted.
3. The AUTHORED percentages stay in the job config; only the resolved copy is
   converted. A resumed job re-derives the same reference from the same window,
   so resume is deterministic.
4. FAIL CLOSED. The one catastrophic failure mode is a percentage being read as
   points — "0.317" becoming a 0.317-POINT stop, an order of magnitude *inside*
   the noise that caused the original loss. Every path that cannot produce a
   trustworthy reference price raises instead of passing values through.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

BOUNDS_UNIT_POINTS = "points"
BOUNDS_UNIT_PCT = "pct_of_index"
VALID_BOUNDS_UNITS: Tuple[str, ...] = (BOUNDS_UNIT_POINTS, BOUNDS_UNIT_PCT)

#: Override keys that carry a numeric bound and therefore need converting.
_BOUND_KEYS = ("min", "max", "fixed")


class BoundsUnitError(ValueError):
    """A percentage bound could not be converted to points, safely."""


def normalize_bounds_unit(value: Any) -> str:
    """Canonical unit name. Absent/blank means today's behaviour, `points`.

    An unrecognised unit RAISES rather than defaulting: silently treating an
    unknown unit as `points` would read percentages as points, which is the
    failure this module exists to prevent.
    """
    if value is None:
        return BOUNDS_UNIT_POINTS
    text = str(value).strip().lower()
    if not text:
        return BOUNDS_UNIT_POINTS
    if text not in VALID_BOUNDS_UNITS:
        raise BoundsUnitError(
            f"Unknown bounds_unit {value!r}; expected one of {list(VALID_BOUNDS_UNITS)}")
    return text


def reference_index_price(df: Any) -> Optional[float]:
    """Median close of the run window, or None when it cannot be trusted.

    The MEDIAN, not the first or last close: it is insensitive to a gap at either
    edge of the window and is the same statistic used to establish the 3.28x
    scale factor in the first place. It is derived from the same candles the
    backtest runs on, so a resumed job lands on the identical number.

    Returns None — never a guess — when the frame is missing, empty, has no
    `close`, or yields a non-finite / non-positive median. Callers must treat
    None as fatal for a percentage conversion; see `resolve_bounds_overrides`.
    """
    if df is None:
        return None
    try:
        if getattr(df, "empty", False) or "close" not in getattr(df, "columns", []):
            return None
        median = float(df["close"].median(skipna=True))
    except Exception:
        return None
    if not math.isfinite(median) or median <= 0:
        return None
    return median


def _as_positive_number(value: Any, param: str, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BoundsUnitError(
            f"{param}.{key} = {value!r} is not a number, so it cannot be read as a "
            f"percent of index")
    number = float(value)
    if not math.isfinite(number):
        raise BoundsUnitError(f"{param}.{key} = {value!r} is not finite")
    if number < 0:
        raise BoundsUnitError(
            f"{param}.{key} = {value!r} is negative; a percent-of-index bound "
            f"must be >= 0")
    return number


def resolve_bounds_overrides(
    *,
    overrides: Optional[Dict[str, Any]],
    bounds_unit: Any,
    pct_params: Optional[Iterable[str]],
    reference_price: Optional[float],
    parameter_schema: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Convert selected percent-of-index overrides into absolute index points.

    Returns ``(resolved_overrides, audit)``. The input mapping is never mutated —
    the job config keeps the percentages the operator actually authored, and only
    the resolved copy is handed to `_build_param_space`.

    Only params that are BOTH named in *pct_params* AND declared by the strategy
    AND actually carry an override are converted. A named param the strategy does
    not declare is recorded in ``audit["ignored"]`` and left alone rather than
    raising: it cannot affect the search (`_build_param_space` iterates the
    schema), and raising would brick a cloned config whose strategy has since
    dropped the param — the same reasoning as the existing foreign-override audit.
    """
    unit = normalize_bounds_unit(bounds_unit)
    source: Dict[str, Any] = overrides or {}
    resolved = copy.deepcopy(source)
    schema = parameter_schema or {}
    selected = [str(p) for p in (pct_params or [])]

    audit: Dict[str, Any] = {
        "unit": unit,
        "applied": False,
        "reference_price": None,
        "converted": {},
        "ignored": [],
    }

    if unit == BOUNDS_UNIT_POINTS:
        return resolved, audit

    targets: List[str] = []
    for name in selected:
        ov = source.get(name)
        if not isinstance(ov, dict) or not any(k in ov for k in _BOUND_KEYS):
            # Selected but carrying no bound: nothing to convert, and inventing
            # one would fabricate a search range the operator never set.
            continue
        if name not in schema:
            audit["ignored"].append(name)
            continue
        targets.append(name)

    if not targets:
        return resolved, audit

    # Only now does a reference price become load-bearing. Validating earlier
    # would break the default path for a window whose candles have not loaded.
    if reference_price is None or not isinstance(reference_price, (int, float)) \
            or isinstance(reference_price, bool) or not math.isfinite(float(reference_price)) \
            or float(reference_price) <= 0:
        raise BoundsUnitError(
            f"bounds_unit={BOUNDS_UNIT_PCT} needs a positive reference index price to "
            f"convert {targets} into points, got {reference_price!r}. Refusing to "
            f"continue: passing the percentages through unconverted would make a "
            f"0.3% stop into a 0.3-POINT stop.")
    reference = float(reference_price)
    audit["reference_price"] = reference

    for name in targets:
        is_int = str(schema.get(name, {}).get("type")) == "int"
        record: Dict[str, Any] = {}
        for key in _BOUND_KEYS:
            if key not in source[name]:
                continue
            pct = _as_positive_number(source[name][key], name, key)
            points = pct / 100.0 * reference
            points = int(round(points)) if is_int else round(points, 4)
            resolved[name][key] = points
            record[key] = {"pct": source[name][key], "pts": points}
        if record:
            audit["converted"][name] = record

    audit["applied"] = bool(audit["converted"])
    return resolved, audit
