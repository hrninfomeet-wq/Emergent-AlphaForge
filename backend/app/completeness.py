"""The ONE definition of option-warehouse completeness: the daily ATM band.

Root cause this module fixes (found 2026-06-12): the planner selects each
minute's ATM correctly, but hygiene judged option coverage per-DAY ("any candle
that day") and per-EXPIRY ("share of expiries with data"). Spot sweeps several
strikes intraday, so strikes that were ATM for only part of a session were
never (re)fetched — and hygiene still reported "verified". Backtests then hit
MISSING_ENTRY_CANDLE on exactly the most volatile sessions.

Definition: a trading day is option-complete when EVERY strike the day's spot
range touched (rounded to the strike step, padded by `pad_steps`) has stored
candles for BOTH legs at the day's resolved (nearest upcoming) expiry.

Pure functions only — DB aggregation stays in data_hygiene.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


def strike_band(day_low: float, day_high: float, step: int, pad_steps: int = 1) -> List[int]:
    """Every tradable strike the [low, high] spot range touched, padded.

    Uses the SAME nearest-strike rounding as the planner/evaluator
    (`options_universe.round_to_step`) on both bounds, then pads by
    `pad_steps`. Matching the fetch path's rounding exactly matters: a
    floor/ceil definition would demand strikes the per-minute ATM selection
    never picks, creating permanently "missing" pairs.
    """
    from app.options_universe import round_to_step
    step = int(step)
    if step <= 0 or day_low is None or day_high is None:
        return []
    lo = float(min(day_low, day_high))
    hi = float(max(day_low, day_high))
    first = round_to_step(lo, step) - int(pad_steps) * step
    last = round_to_step(hi, step) + int(pad_steps) * step
    return [int(s) for s in range(int(first), int(last) + step, step)]


def resolve_expiry_for_day(day_iso: str, expiries_sorted: Sequence[str]) -> Optional[str]:
    """Nearest expiry on/after the day (the planner's next_available policy)."""
    for e in expiries_sorted:
        if str(e) >= str(day_iso):
            return str(e)
    return None


def expected_pairs_for_day(
    day_iso: str,
    day_low: float,
    day_high: float,
    *,
    step: int,
    expiries_sorted: Sequence[str],
    legs: Sequence[str] = ("CE", "PE"),
    pad_steps: int = 1,
) -> Set[Tuple[str, str, str, int]]:
    """(day, expiry, side, strike) keys the warehouse must hold for this day."""
    expiry = resolve_expiry_for_day(day_iso, expiries_sorted)
    if not expiry:
        return set()
    return {
        (str(day_iso), expiry, str(side).upper(), strike)
        for strike in strike_band(day_low, day_high, step, pad_steps)
        for side in legs
    }


def band_completeness(
    day_rows: Iterable[Dict[str, Any]],
    *,
    expiries_sorted: Sequence[str],
    stored_pairs: Set[Tuple[str, str, str, int]],
    step: int,
    legs: Sequence[str] = ("CE", "PE"),
    pad_steps: int = 1,
    judge_until: Optional[str] = None,
    min_spot_minutes: int = 60,
    missing_sample_cap: int = 50,
) -> Dict[str, Any]:
    """Diff expected band pairs against stored (day, expiry, side, strike) pairs.

    `day_rows`: [{date, count, low, high}] from the spot day aggregation —
    only days with >= `min_spot_minutes` spot bars are judged (a day whose spot
    data is itself missing is a SPOT problem, reported by spot coverage).
    `judge_until`: last fully-closed session (an in-progress day is never
    judged incomplete). Returns counts, coverage %, and a bounded sample of
    missing pairs for the plan payload — execute re-derives the full set.
    """
    expected: Set[Tuple[str, str, str, int]] = set()
    judged_days = 0
    for row in day_rows:
        day = str(row.get("date") or row.get("_id") or "")
        if not day:
            continue
        if judge_until and day > str(judge_until):
            continue
        if int(row.get("count") or 0) < int(min_spot_minutes):
            continue
        lo, hi = row.get("low"), row.get("high")
        pairs = expected_pairs_for_day(
            day, lo, hi, step=step, expiries_sorted=expiries_sorted,
            legs=legs, pad_steps=pad_steps,
        )
        if pairs:
            judged_days += 1
            expected |= pairs
    missing = sorted(expected - set(stored_pairs))
    planned = len(expected)
    missing_by_month: Dict[str, int] = {}
    for day, _e, _s, _k in missing:
        month = day[:7]
        missing_by_month[month] = missing_by_month.get(month, 0) + 1
    coverage_pct = round((planned - len(missing)) / planned * 100, 2) if planned else 100.0
    return {
        "judged_days": judged_days,
        "planned_pairs": planned,
        "stored_pairs": planned - len(missing),
        "missing_pairs": len(missing),
        "coverage_pct": coverage_pct,
        "missing_by_month": dict(sorted(missing_by_month.items())),
        "missing_sample": [
            {"date": d, "expiry": e, "side": s, "strike": k}
            for d, e, s, k in missing[: int(missing_sample_cap)]
        ],
    }


def missing_band_pairs(
    day_rows: Iterable[Dict[str, Any]],
    *,
    expiries_sorted: Sequence[str],
    stored_pairs: Set[Tuple[str, str, str, int]],
    step: int,
    legs: Sequence[str] = ("CE", "PE"),
    pad_steps: int = 1,
    judge_until: Optional[str] = None,
    min_spot_minutes: int = 60,
) -> List[Tuple[str, str, str, int]]:
    """Full missing (day, expiry, side, strike) list — used by execute to build
    exact per-contract per-date fetch tasks. Same judging rules as
    band_completeness."""
    expected: Set[Tuple[str, str, str, int]] = set()
    for row in day_rows:
        day = str(row.get("date") or row.get("_id") or "")
        if not day or (judge_until and day > str(judge_until)):
            continue
        if int(row.get("count") or 0) < int(min_spot_minutes):
            continue
        expected |= expected_pairs_for_day(
            day, row.get("low"), row.get("high"), step=step,
            expiries_sorted=expiries_sorted, legs=legs, pad_steps=pad_steps,
        )
    return sorted(expected - set(stored_pairs))
