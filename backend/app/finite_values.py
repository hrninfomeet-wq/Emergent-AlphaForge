"""Shared recursive validation for numeric values that cross execution boundaries."""
from __future__ import annotations

import math
from numbers import Real
from typing import Any, List


def nonfinite_numeric_paths(value: Any, path: str = "") -> List[str]:
    """Return dotted/indexed paths containing NaN or positive/negative infinity.

    ``None`` and booleans are legitimate configuration values. Containers are
    traversed recursively so a finite headline score cannot hide a non-finite
    parameter, metric, risk limit, or signal override deeper in the artifact.
    """
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, Real):
        return [] if math.isfinite(float(value)) else [path or "value"]
    if isinstance(value, dict):
        out: List[str] = []
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            out.extend(nonfinite_numeric_paths(item, child))
        return out
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for idx, item in enumerate(value):
            out.extend(nonfinite_numeric_paths(item, f"{path}[{idx}]"))
        return out
    return []


def numeric_values_are_finite(value: Any) -> bool:
    """True when ``value`` contains no recursively nested non-finite number."""
    return not nonfinite_numeric_paths(value)
