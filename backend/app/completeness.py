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
    known_empty: Optional[Set[Tuple[str, str, str, int]]] = None,
) -> Dict[str, Any]:
    """Diff expected band pairs against stored (day, expiry, side, strike) pairs.

    `day_rows`: [{date, count, low, high}] from the spot day aggregation —
    only days with >= `min_spot_minutes` spot bars are judged (a day whose spot
    data is itself missing is a SPOT problem, reported by spot coverage).
    `judge_until`: last fully-closed session (an in-progress day is never
    judged incomplete). Returns counts, coverage %, and a bounded sample of
    missing pairs for the plan payload — execute re-derives the full set.

    `known_empty`: pairs the broker has already PROVEN it has no data for (a
    clean fetch returned zero candles — the option_known_empty ledger). They
    are excluded from `missing_pairs` and from the coverage denominator, and
    reported separately as `broker_empty_pairs`, so the status can honestly
    reach "verified" instead of flagging unfixable gaps forever.
    """
    expected: Set[Tuple[str, str, str, int]] = set()
    judged_days = 0
    day_expected: Dict[str, int] = {}
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
            day_expected[day] = len(pairs)
    excused = expected & set(known_empty or ())
    missing = sorted(expected - set(stored_pairs) - excused)
    planned = len(expected)
    actionable_planned = planned - len(excused)
    missing_by_month: Dict[str, int] = {}
    day_missing: Dict[str, int] = {}
    for day, _e, _s, _k in missing:
        month = day[:7]
        missing_by_month[month] = missing_by_month.get(month, 0) + 1
        day_missing[day] = day_missing.get(day, 0) + 1
    day_excused: Dict[str, int] = {}
    for day, _e, _s, _k in excused:
        day_excused[day] = day_excused.get(day, 0) + 1
    coverage_pct = (
        round((actionable_planned - len(missing)) / actionable_planned * 100, 2)
        if actionable_planned else 100.0
    )
    # Per-day band truth for the option coverage heatmap: a day is complete
    # when every ACTIONABLE pair it demands is stored (broker-empty excluded).
    per_day = []
    for day in sorted(day_expected):
        exp_n = day_expected[day]
        miss_n = day_missing.get(day, 0)
        exc_n = day_excused.get(day, 0)
        denom = exp_n - exc_n
        per_day.append({
            "date": day,
            "expected": exp_n,
            "missing": miss_n,
            "broker_empty": exc_n,
            "coverage_pct": round((denom - miss_n) / denom * 100, 2) if denom else 100.0,
        })
    return {
        "judged_days": judged_days,
        "planned_pairs": planned,
        "stored_pairs": actionable_planned - len(missing),
        "missing_pairs": len(missing),
        "broker_empty_pairs": len(excused),
        "coverage_pct": coverage_pct,
        "missing_by_month": dict(sorted(missing_by_month.items())),
        "missing_sample": [
            {"date": d, "expiry": e, "side": s, "strike": k}
            for d, e, s, k in missing[: int(missing_sample_cap)]
        ],
        "per_day": per_day,
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
    known_empty: Optional[Set[Tuple[str, str, str, int]]] = None,
) -> List[Tuple[str, str, str, int]]:
    """Full missing (day, expiry, side, strike) list — used by execute to build
    exact per-contract per-date fetch tasks. Same judging rules as
    band_completeness; pairs in `known_empty` (broker proven empty) are never
    requested again."""
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
    return sorted(expected - set(stored_pairs) - set(known_empty or ()))
