"""Pure BASKET-level overall controls evaluator (AlgoTest parity).

This is the basket-aggregate sibling of ``live_sl_monitor`` (which runs PER-LEG).
Where the per-leg monitor watches one option's premium, this module watches the
WHOLE basket's mark-to-market (₹) — the sum of every leg's MTM — and decides
whether an overall stop / target / trailing rule has fired.  When it does, the
caller squares the ENTIRE basket (per-leg SL/target still run elsewhere;
whichever — leg or overall — hits first wins).

It mirrors ``live_sl_monitor.evaluate_exit`` (a pure, deterministic, non-mutating
decision function with a MONOTONIC stop) and ``kill_switch.evaluate_guardrails``
(fail-closed: a stale/garbage reading is treated as "do not act", never as a
spurious exit).

The single shared config contract (the SAME object the frontend panel emits)::

    {
      "sl":      {"enabled": bool, "mode": "mtm"|"premium_pct", "value": number},
      "target":  {"enabled": bool, "mode": "mtm"|"premium_pct", "value": number},
      "trailing": {
        "mode": "none"|"lock"|"lock_trail"|"overall_trail",
        "unit": "mtm"|"premium_pct",
        "lock_at":    number,   # Y — profit at which Lock / Lock&Trail activates
        "lock_floor": number,   # X — locked-in floor profit once activated
        "trail_per":  number,   # A — profit step size (lock_trail / overall_trail)
        "trail_by":   number,   # B — how much the floor rises per step
        "base_sl":    number    # S0 — overall_trail initial stop (loss magnitude, +ve)
      },
      "reentry": {"enabled": bool, "max": int<=5, "type": "asap"|"momentum",
                  "reverse": bool, "momentum_pct": number}
    }

Semantics (all on the basket aggregate, mtm in ₹; basket_premium = Σ entry premium × qty):
- ``premium_pct`` threshold = ``value/100 × basket_premium``.
- The trailing floor is MONOTONIC NON-DECREASING — it can only ratchet up; once
  locked, profit is never handed back (every update uses ``max``).
  - ``lock``:         once mtm ≥ lock_at → floor = lock_floor; exit when mtm < floor.
  - ``lock_trail``:   once mtm ≥ lock_at → floor = lock_floor
                      + floor((mtm − lock_at)/trail_per)·trail_by; exit when mtm < floor.
  - ``overall_trail``: sl = −base_sl + floor(max(0,mtm)/trail_per)·trail_by;
                       exit when mtm ≤ sl.

INVARIANTS
----------
- PURE: no I/O, no mutation of the input state; a NEW state dict is returned.
- FAIL-CLOSED on stale data: a non-finite / None mtm → NO exit, state unchanged.
- MONOTONIC: the trailing floor (lock/lock_trail) and the overall_trail sl_level
  can only rise — verified by tests with a falling price.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

_SL_TARGET_MODES = ("mtm", "premium_pct")
_TRAIL_MODES = ("none", "lock", "lock_trail", "overall_trail")
_REENTRY_TYPES = ("asap", "momentum")
_MAX_REENTRY = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finite(x: Any) -> bool:
    """True iff x is a real finite number (bool is NOT a number here)."""
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and math.isfinite(x)
    )


def _num(x: Any, default: float = 0.0) -> float:
    """Coerce x to a finite float, falling back to default on junk/non-finite."""
    if _finite(x):
        return float(x)
    return float(default)


def _resolve_threshold(mode: str, value: float, basket_premium: float) -> float:
    """Resolve an sl/target ``value`` to an absolute ₹ magnitude.

    ``mtm`` → the value is already ₹.  ``premium_pct`` → value/100 × basket_premium.
    The sign convention (stop = negative loss, target = positive profit) is applied
    by the caller; this returns a non-negative magnitude for a non-negative value.
    """
    if mode == "premium_pct":
        return value / 100.0 * basket_premium
    return value


# ---------------------------------------------------------------------------
# 1. Pure constructor — config + basket_premium → absolute-level state
# ---------------------------------------------------------------------------

def build_overall_state(config: Dict[str, Any], basket_premium: Any) -> Dict[str, Any]:
    """Build the overall-controls evaluation state from a config + basket premium.

    Resolves every ``premium_pct`` threshold to an absolute ₹ level using
    ``basket_premium`` (= Σ entry premium × qty for the basket).  Validates the
    config and raises ``ValueError`` when nothing is enabled or a mode is invalid
    (a controls object that can never fire is a configuration bug, not a no-op).

    Returns a state dict consumed by ``evaluate_overall`` with keys:
        ``sl_level``     — signed ₹ stop level (negative = loss), or None.
        ``target_level`` — ₹ profit target, or None.
        ``trailing``     — {mode, unit, lock_at, lock_floor, trail_per, trail_by, base_sl}
                           with lock_at/lock_floor already resolved to ₹.
        ``floor``        — current monotonic trailing floor (₹), or None until activated.
        ``activated``    — whether a lock/lock_trail floor has engaged.
        ``peak_mtm``     — running peak basket MTM (starts at 0.0).
        ``basket_premium`` — echoed back for reference.
    """
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")

    sl_cfg = dict(config.get("sl") or {})
    target_cfg = dict(config.get("target") or {})
    trail_cfg = dict(config.get("trailing") or {})

    sl_enabled = bool(sl_cfg.get("enabled"))
    target_enabled = bool(target_cfg.get("enabled"))

    trail_mode = str(trail_cfg.get("mode") or "none").strip().lower()
    if trail_mode not in _TRAIL_MODES:
        raise ValueError(f"trailing.mode {trail_mode!r} not in {_TRAIL_MODES}")
    trail_active = trail_mode != "none"

    # Nothing to monitor → configuration error (mirrors build_monitor_state).
    if not sl_enabled and not target_enabled and not trail_active:
        raise ValueError(
            "overall controls need at least one of sl/target/trailing enabled"
        )

    # basket_premium must be a finite positive number whenever any premium_pct
    # threshold is in play (a % of zero/garbage premium is meaningless).
    bp = _num(basket_premium, 0.0)
    needs_premium = (
        (sl_enabled and str(sl_cfg.get("mode") or "mtm") == "premium_pct")
        or (target_enabled and str(target_cfg.get("mode") or "mtm") == "premium_pct")
        or (trail_active and str(trail_cfg.get("unit") or "mtm") == "premium_pct")
    )
    if needs_premium and not (bp > 0):
        raise ValueError(
            f"basket_premium must be a finite positive number for premium_pct mode, got {basket_premium!r}"
        )

    # --- SL level (signed ₹: a loss is negative) ---
    sl_level: Optional[float] = None
    if sl_enabled:
        sl_mode = str(sl_cfg.get("mode") or "mtm").strip().lower()
        if sl_mode not in _SL_TARGET_MODES:
            raise ValueError(f"sl.mode {sl_mode!r} not in {_SL_TARGET_MODES}")
        mag = _resolve_threshold(sl_mode, _num(sl_cfg.get("value")), bp)
        sl_level = -abs(mag)

    # --- target level (positive ₹ profit) ---
    target_level: Optional[float] = None
    if target_enabled:
        target_mode = str(target_cfg.get("mode") or "mtm").strip().lower()
        if target_mode not in _SL_TARGET_MODES:
            raise ValueError(f"target.mode {target_mode!r} not in {_SL_TARGET_MODES}")
        target_level = abs(_resolve_threshold(target_mode, _num(target_cfg.get("value")), bp))

    # --- trailing params, resolving lock_at / lock_floor to ₹ when premium_pct ---
    trail_unit = str(trail_cfg.get("unit") or "mtm").strip().lower()
    if trail_active and trail_unit not in _SL_TARGET_MODES:
        raise ValueError(f"trailing.unit {trail_unit!r} not in {_SL_TARGET_MODES}")

    def _to_rupees(v: Any) -> float:
        return _resolve_threshold(trail_unit, _num(v), bp)

    trailing = {
        "mode": trail_mode,
        "unit": trail_unit,
        "lock_at": _to_rupees(trail_cfg.get("lock_at")),
        "lock_floor": _to_rupees(trail_cfg.get("lock_floor")),
        "trail_per": _to_rupees(trail_cfg.get("trail_per")),
        "trail_by": _to_rupees(trail_cfg.get("trail_by")),
        "base_sl": _to_rupees(trail_cfg.get("base_sl")),
    }

    # overall_trail seeds the SL line at -base_sl (it then ratchets up with profit).
    if trail_mode == "overall_trail":
        seed = -abs(trailing["base_sl"])
        # If a discrete SL was also configured, take the TIGHTER (higher) of the two
        # so neither is silently lost; otherwise just seed from base_sl.
        if sl_level is None or seed > sl_level:
            sl_level = seed

    return {
        "sl_level": sl_level,
        "target_level": target_level,
        "trailing": trailing,
        "floor": None,
        "activated": False,
        "peak_mtm": 0.0,
        "basket_premium": bp,
    }


# ---------------------------------------------------------------------------
# 2. Pure decision — update trailing floor / sl + decide exit
# ---------------------------------------------------------------------------

def evaluate_overall(state: Dict[str, Any], mtm: Any) -> Dict[str, Any]:
    """Decide whether the basket should be squared off at the current MTM.

    PURE + deterministic.  Returns ``{"exit": bool, "reason": str|None,
    "state": <NEW state>}``.  The input state is NEVER mutated.

    Order of operations:
      1. Guard mtm — a non-finite / None reading is stale: NO exit, state
         returned UNCHANGED (we never square the whole basket on a bad reading).
      2. Update peak_mtm = max(peak_mtm, mtm).
      3. Apply the trailing rule to RAISE the floor / sl (monotonic — never lower).
      4. Exit if mtm ≤ sl_level (overall_sl / overall_trailing) OR
         mtm ≥ target_level (overall_target) OR mtm < floor (overall_trailing).

    The lock/lock_trail floor and the overall_trail sl_level are MONOTONIC
    NON-DECREASING — they can only ratchet up.
    """
    # Stale guard — return the input snapshot UNCHANGED (deep-copied trailing so a
    # caller mutating the result can't reach back into the original).
    if not _finite(mtm):
        return {
            "exit": False,
            "reason": None,
            "state": {**state, "trailing": dict(state.get("trailing") or {})},
        }

    mtm = float(mtm)

    new_state: Dict[str, Any] = {**state, "trailing": dict(state.get("trailing") or {})}
    trailing = new_state["trailing"]
    mode = trailing.get("mode", "none")

    # 2. Peak (monotonic).
    new_state["peak_mtm"] = max(_num(new_state.get("peak_mtm"), 0.0), mtm)

    # 3. Trailing — raise the floor / sl where applicable (always via max()).
    lock_at = _num(trailing.get("lock_at"))
    lock_floor = _num(trailing.get("lock_floor"))
    trail_per = _num(trailing.get("trail_per"))
    trail_by = _num(trailing.get("trail_by"))

    if mode == "lock":
        if new_state.get("activated") or mtm >= lock_at:
            new_state["activated"] = True
            cur = new_state.get("floor")
            new_state["floor"] = lock_floor if cur is None else max(cur, lock_floor)
    elif mode == "lock_trail":
        if new_state.get("activated") or mtm >= lock_at:
            new_state["activated"] = True
            steps = 0
            if trail_per > 0 and mtm > lock_at:
                steps = math.floor((mtm - lock_at) / trail_per)
            candidate = lock_floor + steps * trail_by
            cur = new_state.get("floor")
            new_state["floor"] = candidate if cur is None else max(cur, candidate)
    elif mode == "overall_trail":
        base_sl = abs(_num(trailing.get("base_sl")))
        steps = 0
        if trail_per > 0:
            steps = math.floor(max(0.0, mtm) / trail_per)
        candidate = -base_sl + steps * trail_by
        cur = new_state.get("sl_level")
        new_state["sl_level"] = candidate if cur is None else max(cur, candidate)

    # 4. Exit decision.  Trailing (floor / overall_trail sl) is checked first so a
    # trailing breach is reported as "overall_trailing"; a plain configured SL as
    # "overall_sl".
    floor = new_state.get("floor")
    sl_level = new_state.get("sl_level")
    target_level = new_state.get("target_level")

    if mode == "overall_trail":
        if sl_level is not None and mtm <= sl_level:
            return {"exit": True, "reason": "overall_trailing", "state": new_state}
    else:
        if floor is not None and mtm < floor:
            return {"exit": True, "reason": "overall_trailing", "state": new_state}
        if sl_level is not None and mtm <= sl_level:
            return {"exit": True, "reason": "overall_sl", "state": new_state}

    if target_level is not None and mtm >= target_level:
        return {"exit": True, "reason": "overall_target", "state": new_state}

    return {"exit": False, "reason": None, "state": new_state}


# ---------------------------------------------------------------------------
# 3. Pure re-entry budget — decrement + report whether a re-entry is allowed
# ---------------------------------------------------------------------------

def consume_reentry(reentry_state: Dict[str, Any]) -> Dict[str, Any]:
    """Consume one re-entry from the budget, returning whether it is allowed.

    PURE — does not mutate the input.  Returns ``{"allow": bool, "remaining": int,
    "state": <NEW reentry state>}``.

    Budget semantics:
      - ``enabled`` False → never allowed (allow=False, remaining=0).
      - The budget is capped at ``_MAX_REENTRY`` (5); a configured ``max`` above
        that is clamped down.
      - On the FIRST call the budget is initialised from ``remaining`` if present,
        else from the clamped ``max``.  A re-entry is allowed iff remaining > 0;
        when allowed, remaining is decremented by one in the returned state.

    ``type`` ("asap"/"momentum"), ``reverse`` (bool) and ``momentum_pct`` are
    carried through unchanged for the caller to act on (this helper only governs
    the budget; it does NOT decide momentum / reversal triggers).
    """
    rs = dict(reentry_state or {})
    enabled = bool(rs.get("enabled"))

    raw_max = rs.get("max", 0)
    try:
        cap = int(raw_max)
    except (TypeError, ValueError):
        cap = 0
    cap = max(0, min(cap, _MAX_REENTRY))

    # remaining is initialised lazily from the clamped max on first use.
    if "remaining" in rs and rs.get("remaining") is not None:
        try:
            remaining = int(rs["remaining"])
        except (TypeError, ValueError):
            remaining = cap
    else:
        remaining = cap
    remaining = max(0, min(remaining, _MAX_REENTRY))

    rtype = str(rs.get("type") or "asap").strip().lower()
    if rtype not in _REENTRY_TYPES:
        rtype = "asap"

    carried = {
        "enabled": enabled,
        "max": cap,
        "type": rtype,
        "reverse": bool(rs.get("reverse")),
        "momentum_pct": _num(rs.get("momentum_pct")),
    }

    if not enabled or remaining <= 0:
        return {
            "allow": False,
            "remaining": remaining if enabled else 0,
            "state": {**carried, "remaining": remaining if enabled else 0},
        }

    new_remaining = remaining - 1
    return {
        "allow": True,
        "remaining": new_remaining,
        "state": {**carried, "remaining": new_remaining},
    }
