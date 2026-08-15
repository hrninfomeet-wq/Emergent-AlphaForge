"""Which minutes of a trading day are MISSING — completeness, not freshness.

`live_feed_health.py` answers "is the newest bar recent?" (``FRESH_THRESHOLD_SEC =
120``). That is a liveness signal, and it is silent about a hole. A backend that
boots mid-session starts the tick roller, receives a bar within two minutes, and
reports LIVE while the entire morning is absent.

That is exactly what happened on 2026-08-14: the backend booted ~09:49 and NIFTY
stored 340 bars beginning at 09:50 — a clean 35-bar leading hole with no interior
gap, so even `indicators.gap_before` had nothing to flag (it detects interior gaps
only, and by design "never flags a cross-date boundary"). The evaluator's 200-bar
lookback silently spanned into the previous session: at the first live bar it held
199 prior-session bars and exactly one of today's. Nothing detected it during the
session. The warehouse was repaired at 09:13 IST the FOLLOWING day, by which time
every trading decision had already been made on data that no longer exists.

This module is PURE — no DB, no clock, no network; the caller supplies ``now_ms``.
It derives everything from ``ts`` (epoch ms), never from the ``datetime`` string,
because `candles_1m` contains TWO datetime formats: the roller writes
``2026-08-14 09:50:00`` and the historical ingest writes
``2026-08-14T09:15:00+05:30``, and those sort INVERTED against each other
(``" " < "T"``). Only ``ts`` — which carries the collection's unique index — is
safe to reason about.

Session shape comes from `session_spec`, which already models the 2026-08-03
closing-auction split: spot is 375 bars (09:15..15:29) and options 385
(09:15..15:39), because the index freezes for the auction while F&O trades on.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Iterable, List, Optional, Set, Tuple

from app.nse_calendar import is_trading_day
from app.session_spec import SPOT, session_spec

MINUTE_MS = 60_000
IST = timezone(timedelta(hours=5, minutes=30))

#: Cap on reported ranges. A pathological day (alternate minutes missing) would
#: otherwise produce hundreds of entries and make the report unreadable in a log
#: line or an API payload. The COUNT is always exact; only the listing truncates.
MAX_REPORTED_RANGES = 20


def _ist_minute_ms(iso_date: str, minute_of_day: int) -> int:
    d = date.fromisoformat(iso_date)
    dt = datetime.combine(d, dtime(minute_of_day // 60, minute_of_day % 60), tzinfo=IST)
    return int(dt.timestamp() * 1000)


def expected_minute_ts(
    iso_date: str,
    *,
    segment: str = SPOT,
    now_ms: Optional[int] = None,
) -> List[int]:
    """Every minute timestamp that SHOULD have a candle, ascending.

    Empty for a non-trading day and before the open — both are "nothing is
    expected", not "something is broken". Reporting a gap every holiday would
    train the operator to ignore the signal, which costs more than the gap does.

    ``now_ms`` bounds the grid to minutes that have CLOSED. The minute in progress
    is deliberately excluded: demanding it would make every healthy live system
    look permanently one bar short, and would make backfill try to write a partial
    bar over a complete one.
    """
    iso = str(iso_date)
    if not is_trading_day(iso):
        return []
    spec = session_spec(iso, segment)
    if int(spec.expected_candles or 0) <= 0:
        return []

    open_ms = _ist_minute_ms(iso, int(spec.open_min))
    # close_min is EXCLUSIVE (15:30 for spot), so the last candle opens a minute
    # earlier — 15:29 — which is why a full spot day is 375 and not 376.
    last_ms = _ist_minute_ms(iso, int(spec.close_min)) - MINUTE_MS

    if now_ms is not None:
        # The most recently CLOSED minute at now_ms.
        closed_ms = (int(now_ms) // MINUTE_MS) * MINUTE_MS - MINUTE_MS
        last_ms = min(last_ms, closed_ms)

    if last_ms < open_ms:
        return []
    return list(range(open_ms, last_ms + MINUTE_MS, MINUTE_MS))


def _clean(present: Iterable[Any]) -> Set[int]:
    """Coerce a bag of timestamps to a set of ints, discarding anything unusable.

    This consumes whatever a Mongo projection produced. A malformed row must not
    be able to raise inside a readiness check that gates live trading.
    """
    out: Set[int] = set()
    for value in present or ():
        try:
            if value is None or isinstance(value, bool):
                continue
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out


def missing_ranges(
    present: Iterable[Any],
    expected: Iterable[int],
) -> List[Tuple[int, int, int]]:
    """Contiguous missing spans as ``(first_ts, last_ts, count)``, ascending.

    Timestamps present but NOT expected (a pre-open tick that leaked through, a
    manual ingest that ignored session bounds) are ignored rather than treated as
    an error: this function answers "what is missing", and extra rows are a
    different question.
    """
    have = _clean(present)
    want = sorted(int(t) for t in expected or ())
    out: List[Tuple[int, int, int]] = []
    run_start: Optional[int] = None
    run_last: Optional[int] = None
    run_len = 0
    for ts in want:
        if ts in have:
            if run_start is not None:
                out.append((run_start, run_last, run_len))
                run_start, run_last, run_len = None, None, 0
            continue
        if run_start is None:
            run_start, run_last, run_len = ts, ts, 1
        else:
            run_last, run_len = ts, run_len + 1
    if run_start is not None:
        out.append((run_start, run_last, run_len))
    return out


def assess_completeness(
    iso_date: str,
    present: Iterable[Any],
    *,
    segment: str = SPOT,
    now_ms: Optional[int] = None,
) -> dict:
    """Verdict on one instrument-day.

    Returns ``{complete, reason, expected, present, missing, coverage_pct,
    ranges, ranges_truncated, first_missing, last_missing}``.

    ``complete`` is True when nothing is expected (holiday, pre-open) as well as
    when everything expected is present — an unready day and an idle day are
    different things, and only the former is worth an alarm.

    Never raises. This feeds a gate that can block live activation, and a
    readiness check that 500s on a malformed row is worse than the gap it looks
    for.
    """
    iso = str(iso_date)
    expected = expected_minute_ts(iso, segment=segment, now_ms=now_ms)
    have = _clean(present)

    if not expected:
        reason = "not_a_trading_day" if not is_trading_day(iso) else "before_open"
        return {
            "date": iso, "segment": segment, "complete": True, "reason": reason,
            "expected": 0, "present": 0, "missing": 0, "coverage_pct": 100.0,
            "ranges": [], "ranges_truncated": False,
            "first_missing": None, "last_missing": None,
        }

    want = set(expected)
    hit = len(want & have)
    ranges = missing_ranges(have, expected)
    missing = sum(r[2] for r in ranges)
    truncated = len(ranges) > MAX_REPORTED_RANGES
    return {
        "date": iso,
        "segment": segment,
        "complete": missing == 0,
        "reason": "complete" if missing == 0 else "incomplete",
        "expected": len(expected),
        "present": hit,
        "missing": missing,
        "coverage_pct": round(100.0 * hit / len(expected), 2),
        "ranges": ranges[:MAX_REPORTED_RANGES],
        "ranges_truncated": truncated,
        "first_missing": ranges[0][0] if ranges else None,
        "last_missing": ranges[-1][1] if ranges else None,
    }


def format_ranges_ist(ranges: Iterable[Tuple[int, int, int]]) -> List[str]:
    """Human-readable ``09:15-09:49 (35)`` spans for logs and the operator report."""
    out: List[str] = []
    for first, last, count in ranges or ():
        a = datetime.fromtimestamp(first / 1000, IST).strftime("%H:%M")
        b = datetime.fromtimestamp(last / 1000, IST).strftime("%H:%M")
        out.append(f"{a}-{b} ({count})" if count > 1 else f"{a} (1)")
    return out
