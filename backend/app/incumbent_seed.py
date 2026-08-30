"""Incumbent seeding for the optimizer.

WHY THIS EXISTS
---------------
No code path called `study.enqueue_trial`, so the optimizer never evaluated the
strategy's own defaults, the best parameters a previous job had already found, or
a preset the operator had already validated. It could therefore return a result
WORSE than a known-good point that lies inside its own search space, and did:

    confluence_scalper / NIFTY, 2025-11-01..2026-08-26, 11-dim space, declared bounds
      optimizer best (288 trials) ...... -19,957 INR of real option P&L
      known-good preset, same space ....  +77,129 INR   <- never evaluated

Seeding those points as the study's first trials makes "never worse than the
incumbent" structural rather than a hope: whatever the sampler goes on to find,
the incumbent is already in the study, so `best` can only improve on it.

DROP, DO NOT CLAMP
------------------
A seed value outside the current bounds is NOT clamped into range. Clamping would
enqueue a DIFFERENT point and silently label it with the incumbent's name, which is
exactly the kind of quiet substitution this codebase has been bitten by before. The
out-of-range dimension is dropped instead (the sampler then chooses it) and the drop
is reported, so the operator can see that their bounds do not actually contain the
point they were trying to beat.

Pure + deterministic, so it is unit-testable without running an optimization.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Reason codes surfaced on the job document.
NOT_IN_SPACE = "not_in_space"        # the strategy/search does not tune this key
FIXED_DIMENSION = "fixed"            # pinned by the caller; Optuna never suggests it
OUT_OF_BOUNDS = "out_of_bounds"      # value lies outside the configured min/max
BAD_TYPE = "bad_type"                # value cannot be coerced to the declared type


def clean_seed_params(
    space: Dict[str, Dict[str, Any]],
    params: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Split `params` into what can be enqueued into a study built from `space`,
    and what had to be dropped (with a reason).

    The kept dict is safe to hand to `optuna.Study.enqueue_trial`: every key is a
    dimension `_suggest` will actually ask for, and every value is inside that
    dimension's distribution. A partial dict is fine — Optuna samples the rest.
    """
    kept: Dict[str, Any] = {}
    dropped: Dict[str, str] = {}
    for name, raw in (params or {}).items():
        info = (space or {}).get(name)
        if info is None:
            dropped[name] = NOT_IN_SPACE
            continue
        if "fixed" in info:
            # `_suggest` returns info["fixed"] without calling suggest_*, so the
            # dimension is absent from the trial's distributions entirely.
            dropped[name] = FIXED_DIMENSION
            continue
        t = info.get("type")
        if t == "bool":
            # Matches trial.suggest_categorical(name, [True, False]) — the seeded
            # value must be one of those two objects, not 0/1 or "true".
            if isinstance(raw, bool):
                kept[name] = raw
            else:
                dropped[name] = BAD_TYPE
            continue
        if t == "int":
            try:
                # bool is an int subclass in Python; an accidental True here would
                # silently become 1 and look like a legitimate seed.
                if isinstance(raw, bool):
                    raise TypeError("bool is not an int dimension")
                val: Any = int(raw)
            except (TypeError, ValueError):
                dropped[name] = BAD_TYPE
                continue
            lo, hi = int(info.get("min", 0)), int(info.get("max", 100))
        elif t == "float":
            try:
                if isinstance(raw, bool):
                    raise TypeError("bool is not a float dimension")
                val = float(raw)
            except (TypeError, ValueError):
                dropped[name] = BAD_TYPE
                continue
            lo, hi = float(info.get("min", 0.0)), float(info.get("max", 1.0))
        else:
            dropped[name] = BAD_TYPE
            continue
        if val < lo or val > hi:
            dropped[name] = OUT_OF_BOUNDS
            continue
        kept[name] = val
    return kept, dropped


def build_seed_trials(
    space: Dict[str, Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    *,
    max_seeds: int = 8,
) -> List[Dict[str, Any]]:
    """Turn labelled incumbent candidates into enqueue-able seed records.

    `candidates` is an ordered list of {"source": str, "params": dict}; earlier
    entries win when two produce the same point. Returns
    [{"source", "params", "dropped"}], capped at `max_seeds` so a long preset list
    cannot eat the trial budget.

    An empty `params` after cleaning is skipped: enqueueing {} would burn a trial
    on a purely random point while claiming to be an incumbent.
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for cand in candidates or []:
        if len(out) >= max_seeds:
            break
        kept, dropped = clean_seed_params(space, (cand or {}).get("params") or {})
        if not kept:
            continue
        key = tuple(sorted((k, repr(v)) for k, v in kept.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "source": str((cand or {}).get("source") or "unknown"),
            "params": kept,
            "dropped": dropped,
        })
    return out
