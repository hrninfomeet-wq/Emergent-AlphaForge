"""Persistence store for the overall-controls config (basket-level SL / target /
trailing / re-entry) — the single shared contract used by both the backend
exit engine and the frontend overall-controls panel.

Config shape (ALL numbers; ``unit``/``mode`` picks ₹ (``mtm``) vs % (``premium_pct``))::

    {
      "sl":      { "enabled": bool, "mode": "mtm"|"premium_pct", "value": number },
      "target":  { "enabled": bool, "mode": "mtm"|"premium_pct", "value": number },
      "trailing": {
        "mode": "none"|"lock"|"lock_trail"|"overall_trail",
        "unit": "mtm"|"premium_pct",
        "lock_at":    number,   # Y — profit at which Lock / Lock&Trail activates
        "lock_floor": number,   # X — locked-in floor profit once activated
        "trail_per":  number,   # A — profit step size (lock_trail / overall_trail)
        "trail_by":   number,   # B — how much the floor rises per step
        "base_sl":    number    # S0 — overall_trail initial stop (loss MAGNITUDE, +ve)
      },
      "reentry": { "enabled": bool, "max": int<=5, "type": "asap"|"momentum",
                   "reverse": bool, "momentum_pct": number }
    }

Semantics (AlgoTest parity), evaluated on the BASKET aggregate
(``mtm`` = Σ leg MTM in ₹; ``basket_premium`` = Σ entry premium × qty):
- ``premium_pct`` mode threshold = value/100 × basket_premium.
- Trailing floor is MONOTONIC NON-DECREASING (ratchets up; never hands back
  locked profit):
    * lock:          once mtm ≥ lock_at → floor = lock_floor; exit when mtm < floor.
    * lock_trail:    once mtm ≥ lock_at → floor = lock_floor +
                     floor((mtm − lock_at)/trail_per)·trail_by; exit when mtm < floor.
    * overall_trail: sl = −base_sl + floor(max(0,mtm)/trail_per)·trail_by;
                     exit when mtm ≤ sl.
- Per-leg SL/target still run elsewhere; whichever (leg or overall) hits first
  wins; overall squares the WHOLE basket.

This module owns ONLY persistence + validation of the config object — the
evaluation/exit logic lives in the exit engine.

Architecture mirror
--------------------
Structurally identical to ``kill_switch.SafetyConfigStore``: an async store over
an injectable collection (``find_one`` / ``update_one`` upsert), a ``_SINGLETON_ID``
document, ``get_config`` / ``put_config`` with a whitelist, and a ``default_store()``
that defers the ``app.db`` import so the module stays host-testable.

One extra wrinkle: the SAME class backs two singletons.  The "overall" controls
(per the running engine) and a "broker_level" copy share the class — pick via the
``scope`` constructor arg, which becomes the document ``_id``.
"""
from __future__ import annotations

import math
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Allowed enum values (the single source of truth for validation)
# ---------------------------------------------------------------------------

_THRESHOLD_MODES: frozenset[str] = frozenset({"mtm", "premium_pct"})
_TRAILING_MODES: frozenset[str] = frozenset({"none", "lock", "lock_trail", "overall_trail"})
_TRAILING_UNITS: frozenset[str] = frozenset({"mtm", "premium_pct"})
_REENTRY_TYPES: frozenset[str] = frozenset({"asap", "momentum"})

#: Hard ceiling on re-entry attempts (broker/AlgoTest parity).
_REENTRY_MAX_CAP = 5


# ---------------------------------------------------------------------------
# DEFAULT_OVERALL_CONFIG — a fully-disabled config matching the shape above.
# Deep-copied on every read so callers can never mutate the constant.
# ---------------------------------------------------------------------------

DEFAULT_OVERALL_CONFIG: Dict[str, Any] = {
    "sl":     {"enabled": False, "mode": "mtm", "value": 0},
    "target": {"enabled": False, "mode": "mtm", "value": 0},
    "trailing": {
        "mode": "none",
        "unit": "mtm",
        "lock_at": 0,
        "lock_floor": 0,
        "trail_per": 0,
        "trail_by": 0,
        "base_sl": 0,
    },
    "reentry": {
        "enabled": False,
        "max": 0,
        "type": "asap",
        "reverse": False,
        "momentum_pct": 0,
    },
}

#: Per-section field schemas.  Each entry maps a field name to its coercer; the
#: coercer raises ValueError on bad input (fail-closed).  Enum/clamp rules are
#: applied on top of these in ``_validate_section``.
_BOOL_FIELDS: frozenset[str] = frozenset({"enabled", "reverse"})
_NUMBER_FIELDS: frozenset[str] = frozenset({
    "value", "lock_at", "lock_floor", "trail_per", "trail_by", "base_sl",
    "momentum_pct",
})
_STR_FIELDS: frozenset[str] = frozenset({"mode", "unit", "type"})
# "max" is handled specially (int + clamp).

#: The allowed sub-keys per section (anything else → ValueError, fail-closed).
_SECTION_KEYS: Dict[str, frozenset[str]] = {
    sect: frozenset(DEFAULT_OVERALL_CONFIG[sect]) for sect in DEFAULT_OVERALL_CONFIG
}


# ---------------------------------------------------------------------------
# Coercion helpers (fail-closed: raise ValueError on un-coercible input)
# ---------------------------------------------------------------------------

def _coerce_bool(value: Any) -> bool:
    """Coerce to a strict bool.  Accepts native bools and truthy ints (0/1)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):  # 0 / 1 from a checkbox payload
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off", ""}:
            return False
    raise ValueError(f"Cannot coerce {value!r} to bool")


def _coerce_number(value: Any) -> float:
    """Coerce to a finite float (ints kept as ints by the caller via _as_jsonable)."""
    if isinstance(value, bool):
        raise ValueError(f"Refusing to coerce bool {value!r} to a number")
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Cannot coerce {value!r} to a number")
    if not math.isfinite(num):
        raise ValueError(f"Non-finite number not allowed: {value!r}")
    return num


def _as_jsonable_number(num: float) -> Any:
    """Return an int when the float is integral (5.0 → 5), else the float.

    Keeps the persisted document clean (avoids ``5.0`` where the UI sends ``5``).
    """
    if num == int(num):
        return int(num)
    return num


def _coerce_int(value: Any) -> int:
    """Coerce to int (truncating), fail-closed."""
    num = _coerce_number(value)
    return int(num)


# ---------------------------------------------------------------------------
# Section validation
# ---------------------------------------------------------------------------

def _validate_section(section: str, updates: Any) -> Dict[str, Any]:
    """Validate + coerce a single config section's update dict.

    Returns a NEW dict containing only the (coerced) keys present in *updates*.
    Raises ValueError on: non-dict section, unknown sub-key, bad enum, or an
    un-coercible value.
    """
    if not isinstance(updates, dict):
        raise ValueError(f"Section {section!r} must be an object, got {type(updates).__name__}")

    allowed = _SECTION_KEYS[section]
    unknown = set(updates) - allowed
    if unknown:
        raise ValueError(f"Unknown {section!r} keys: {sorted(unknown)}")

    out: Dict[str, Any] = {}
    for key, raw in updates.items():
        if key in _BOOL_FIELDS:
            out[key] = _coerce_bool(raw)
        elif key == "max":
            n = _coerce_int(raw)
            # Clamp to [0, cap] — never raise on an out-of-range count.
            out[key] = max(0, min(_REENTRY_MAX_CAP, n))
        elif key in _NUMBER_FIELDS:
            out[key] = _as_jsonable_number(_coerce_number(raw))
        elif key in _STR_FIELDS:
            sval = str(raw)
            _check_enum(section, key, sval)
            out[key] = sval
        else:  # pragma: no cover — defended by the unknown-key check above
            raise ValueError(f"Unhandled key {section}.{key}")
    return out


def _check_enum(section: str, key: str, value: str) -> None:
    """Raise ValueError if *value* is not in the allowed set for this enum field."""
    if key == "mode" and section in ("sl", "target"):
        allowed = _THRESHOLD_MODES
    elif key == "mode" and section == "trailing":
        allowed = _TRAILING_MODES
    elif key == "unit":
        allowed = _TRAILING_UNITS
    elif key == "type":
        allowed = _REENTRY_TYPES
    else:  # pragma: no cover — only enum fields reach here
        return
    if value not in allowed:
        raise ValueError(
            f"Invalid {section}.{key}={value!r}; allowed: {sorted(allowed)}"
        )


def _deep_default() -> Dict[str, Any]:
    """Return a fresh deep copy of DEFAULT_OVERALL_CONFIG (nested dicts copied)."""
    return {sect: dict(fields) for sect, fields in DEFAULT_OVERALL_CONFIG.items()}


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class OverallSettingsStore:
    """Async store for the overall-controls config singleton.

    Backed by any async collection exposing ``find_one`` / ``update_one`` (upsert).
    Production code uses ``default_store()``; tests inject a ``FakeAsyncCollection``.

    Exactly one document per ``scope`` — the document ``_id`` IS the scope, so a
    single class serves both the ``"overall"`` and ``"broker_level"`` singletons
    (even sharing one collection without colliding).
    """

    def __init__(self, collection: Any, *, scope: str = "overall") -> None:
        self._col = collection
        self._scope = scope

    @property
    def _SINGLETON_ID(self) -> str:  # noqa: N802 — mirror SafetyConfigStore naming
        return self._scope

    async def get_config(self) -> Dict[str, Any]:
        """Return the merged config (defaults + stored).  Never returns None.

        Stored sections override defaults key-by-key, so a partially-stored
        section still has its missing fields filled from the defaults.
        """
        doc = await self._col.find_one({"_id": self._SINGLETON_ID})
        merged = _deep_default()
        if doc:
            for sect in merged:
                stored = doc.get(sect)
                if isinstance(stored, dict):
                    merged[sect].update({
                        k: v for k, v in stored.items() if k in _SECTION_KEYS[sect]
                    })
        return merged

    async def put_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Validate + persist *updates*, returning the full merged config.

        Validation (fail-closed — raises ``ValueError`` on any violation):
        - reject unknown TOP-LEVEL keys (only sl/target/trailing/reentry allowed);
        - reject unknown sub-keys within a section;
        - enum fields (mode/unit/type) must be in their allowed set;
        - numeric fields coerced to finite numbers; bool fields to strict bools;
        - ``reentry.max`` coerced to int and CLAMPED to [0, 5].

        Merge semantics: only the provided keys are written (``$set`` of the
        validated, namespaced fields), so prior values survive an unrelated put.
        """
        if not isinstance(updates, dict):
            raise ValueError("put_config expects an object")

        unknown_top = set(updates) - set(DEFAULT_OVERALL_CONFIG)
        if unknown_top:
            raise ValueError(f"Unknown top-level config keys: {sorted(unknown_top)}")

        # Validate every section first (all-or-nothing — never persist a partial
        # update when a later section is invalid).
        validated: Dict[str, Dict[str, Any]] = {}
        for sect, sect_updates in updates.items():
            validated[sect] = _validate_section(sect, sect_updates)

        # Read-merge-write at the SECTION level.  We $set whole section objects
        # (not dotted paths) so the same code path is correct against both the
        # injected FakeAsyncCollection and a real Mongo, while still preserving
        # the fields within each section that the caller did not touch.
        current = await self.get_config()
        set_doc: Dict[str, Any] = {}
        for sect, fields in validated.items():
            merged_section = dict(current[sect])
            merged_section.update(fields)
            set_doc[sect] = merged_section

        if set_doc:
            await self._col.update_one(
                {"_id": self._SINGLETON_ID},
                {"$set": set_doc},
                upsert=True,
            )
        return await self.get_config()


def default_store(scope: str = "overall") -> "OverallSettingsStore":
    """Return an OverallSettingsStore backed by the production Mongo collection.

    Deferred import keeps this module host-testable without a running Mongo
    instance (mirrors ``SafetyConfigStore.default_store``).
    """
    from app.db import get_db  # type: ignore[import]

    return OverallSettingsStore(get_db().live_overall_settings, scope=scope)
