"""A fully-missing interior session must not be invisible forever.

The operator's PC is rarely up during market hours. When it misses a WHOLE
session the day is not merely unfetched — it is undetectable:

  * `compute_catch_up_plan` appends forward from `last_spot_date + 1`, and the
    missed day sits BELOW that high-water mark the moment a later day is stored.
  * the sub-high-water repair uses `incomplete_spot_days`, which iterates
    AGGREGATION ROWS. A day with zero stored candles produces no row at all, so
    it can never be reported as short.

PC off Tuesday, roller stores Wednesday's live bars, `last_spot_date` jumps to
Wednesday — Tuesday is gone forever, and no automatic path will ever fetch it.

The fix already exists for India VIX and was never ported to spot:
`vix_topup_from_date` computes `missing = trading_days_in_range(...) - days_present`
SEPARATELY from `incomplete_spot_days`, precisely because the latter cannot see a
day that stored nothing. Its docstring says it outright: "Plain forward-append can
never fill a MID-window HOLE".
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.data_hygiene import missing_spot_days  # noqa: E402


def _rows(dates_and_counts):
    return [{"date": d, "count": c, "expected": 375} for d, c in dates_and_counts]


# 2026-08-03 Mon .. 2026-08-07 Fri are all trading days.
_MON, _TUE, _WED, _THU, _FRI = ("2026-08-03", "2026-08-04", "2026-08-05",
                                "2026-08-06", "2026-08-07")


def test_a_fully_missing_interior_day_is_found():
    """The exact PC-off pattern: Tuesday stored nothing, Wednesday did."""
    rows = _rows([(_MON, 375), (_WED, 375), (_THU, 375)])
    out = missing_spot_days(rows, floor=_MON, judge_until=_THU)
    assert _TUE in out, (
        "a session that stored ZERO candles produces no aggregation row, so the "
        "incomplete-day repair can never see it — it must be derived from the "
        "trading calendar instead")


def test_a_complete_window_reports_no_holes():
    rows = _rows([(_MON, 375), (_TUE, 375), (_WED, 375)])
    assert missing_spot_days(rows, floor=_MON, judge_until=_WED) == []


def test_several_consecutive_missing_days_are_all_found():
    """A long weekend away: Tue-Thu all missed."""
    rows = _rows([(_MON, 375), (_FRI, 375)])
    out = missing_spot_days(rows, floor=_MON, judge_until=_FRI)
    assert out == [_TUE, _WED, _THU]


def test_weekends_and_holidays_are_not_holes():
    """Derived from the trading calendar, so a Saturday is not a gap."""
    out = missing_spot_days([], floor="2026-08-08", judge_until="2026-08-09")
    assert out == [], "reported a weekend as a missing session"


def test_a_partial_day_is_not_reported_as_MISSING():
    """A short day IS stored, so it belongs to the incomplete-day repair, not here.
    Reporting it twice would double-count the repair window."""
    rows = _rows([(_MON, 375), (_TUE, 12), (_WED, 375)])
    assert missing_spot_days(rows, floor=_MON, judge_until=_WED) == []


def test_the_result_is_sorted_and_deduped():
    rows = _rows([(_WED, 375)])
    out = missing_spot_days(rows, floor=_MON, judge_until=_WED)
    assert out == sorted(set(out))


def test_an_empty_window_is_safe():
    assert missing_spot_days([], floor="2026-08-07", judge_until=None) == []


# --- the planner must actually consult it ----------------------------------

def test_the_catch_up_planner_uses_the_hole_finder():
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "backend" / "app" / "data_hygiene.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "compute_catch_up_plan")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "missing_spot_days" in called, (
        "compute_catch_up_plan still only appends forward and repairs SHORT days, "
        "so a wholly-missed interior session stays invisible")


# --- the AUTOMATIC triggers must see it too ---------------------------------

def test_the_autoupdate_planner_also_repairs_missing_sessions():
    """startup / OAuth-connect / 18:00 all route through compute_hygiene_plan.

    Its spot gate is a PERCENTAGE over ~9 months with a 95% "verified" threshold,
    so 1-8 wholly-missed sessions out of ~185 still score 96-99.5% and emit NO
    action. That is precisely the operator's pattern, and it meant no automatic
    trigger could ever repair it.
    """
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "backend" / "app" / "data_hygiene.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "compute_hygiene_plan")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "missing_spot_days" in called, (
        "the auto-update plan repairs SHORT days but not ABSENT ones, so a "
        "session missed while the PC was off is never fetched automatically")
    assert "incomplete_spot_days" in called, "the short-day repair must remain"


# --- it must not over-fire --------------------------------------------------

def test_an_empty_warehouse_is_not_all_holes():
    """No stored rows means no INTERIOR — that is forward-append territory.

    Claiming every calendar day is missing would make an empty or newly-seeded
    warehouse demand a full re-fetch on every single sync.
    """
    assert missing_spot_days([], floor=_MON, judge_until=_FRI) == []


def test_days_after_the_last_stored_one_are_not_holes():
    """Those are the forward-append gap, already handled — double-counting them
    would widen the repair window for no reason."""
    rows = _rows([(_MON, 375), (_TUE, 375)])
    assert missing_spot_days(rows, floor=_MON, judge_until=_FRI) == []


def test_days_before_the_first_stored_one_are_pre_history():
    rows = _rows([(_THU, 375), (_FRI, 375)])
    assert missing_spot_days(rows, floor=_MON, judge_until=_FRI) == []
