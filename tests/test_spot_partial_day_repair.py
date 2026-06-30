"""F2 — under-captured spot day detection + catch-up repair, and F3 — VIX in
the auto-update loop. From the Data Warehouse review (2026-06-13).

The bug F2 fixes: a trading day captured only partially (PC off mid-session →
only the live roller's morning stored) sits at/below the last-stored-date
high-water mark, so the incremental catch-up declared "up to date" and never
re-fetched the full session from Upstox. The 2026-06-12 255/375 heatmap-amber
case.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from datetime import date, timedelta  # noqa: E402

from app.data_hygiene import (  # noqa: E402
    incomplete_spot_days,
    vix_topup_from_date,
    SPOT_INCOMPLETE_TOLERANCE,
    SPOT_REPAIR_LOOKBACK_DAYS,
)
from app.nse_calendar import trading_days_in_range  # noqa: E402
from tests.contract_corpus import backend_api_text


# ---------------------------------------------------------------------------
# incomplete_spot_days — pure detector
# ---------------------------------------------------------------------------

def test_detects_materially_partial_regular_session():
    rows = [
        {"date": "2026-06-11", "count": 375},   # full
        {"date": "2026-06-12", "count": 255},   # PC off after 13:30 -> partial
    ]
    out = incomplete_spot_days(rows, judge_until="2026-06-12")
    assert [d["date"] for d in out] == ["2026-06-12"]
    assert out[0]["count"] == 255 and out[0]["expected"] == 375


def test_muhurat_short_session_is_not_flagged():
    # 2025-10-21 Diwali Muhurat: expected_candle_count == 60, stored 60 -> complete.
    rows = [{"date": "2025-10-21", "count": 60}]
    assert incomplete_spot_days(rows, judge_until="2025-12-31") == []


def test_weekend_stray_ticks_not_flagged():
    # Non-trading days have expected_candle_count 0 -> never flagged, even with a
    # few stray live-roller ticks.
    rows = [{"date": "2026-05-30", "count": 5}, {"date": "2026-05-31", "count": 11}]
    assert incomplete_spot_days(rows, judge_until="2026-06-12") == []


def test_tolerance_ignores_a_couple_missing_prints():
    rows = [{"date": "2026-06-11", "count": 375 - (SPOT_INCOMPLETE_TOLERANCE - 1)}]
    assert incomplete_spot_days(rows, judge_until="2026-06-12") == []
    rows2 = [{"date": "2026-06-11", "count": 375 - (SPOT_INCOMPLETE_TOLERANCE + 5)}]
    assert [d["date"] for d in incomplete_spot_days(rows2, judge_until="2026-06-12")] == ["2026-06-11"]


def test_in_progress_day_not_judged():
    rows = [{"date": "2026-06-13", "count": 100}]
    assert incomplete_spot_days(rows, judge_until="2026-06-12") == []


def test_zero_count_day_skipped():
    rows = [{"date": "2026-06-12", "count": 0}]
    assert incomplete_spot_days(rows, judge_until="2026-06-12") == []


# ---------------------------------------------------------------------------
# vix_topup_from_date — fill mid-window HOLES, not just append forward
# ---------------------------------------------------------------------------

def _vix_full_rows(judge: str, lookback: int = SPOT_REPAIR_LOOKBACK_DAYS):
    """A complete set of VIX day-rows over the whole repair window [judge-lookback, judge]."""
    floor = (date.fromisoformat(judge) - timedelta(days=lookback)).isoformat()
    return [{"date": d, "count": 375} for d in trading_days_in_range(floor, judge)]


def test_vix_topup_no_holes_returns_forward_from():
    rows = _vix_full_rows("2026-06-25")
    assert vix_topup_from_date(rows, forward_from="2026-06-26", judge_until="2026-06-25") == "2026-06-26"


def test_vix_topup_pulls_back_to_missing_hole():
    # A VIX day missed while later days were fetched -> forward append can't fill it.
    rows = _vix_full_rows("2026-06-25")
    hole = rows[len(rows) // 2]["date"]
    rows2 = [r for r in rows if r["date"] != hole]
    assert vix_topup_from_date(rows2, forward_from="2026-06-26", judge_until="2026-06-25") == hole


def test_vix_topup_pulls_back_to_short_day():
    rows = _vix_full_rows("2026-06-25")
    short_day = rows[-1]["date"]
    rows2 = [({"date": r["date"], "count": 50} if r["date"] == short_day else r) for r in rows]
    assert vix_topup_from_date(rows2, forward_from="2026-06-26", judge_until="2026-06-25") == short_day


def test_vix_topup_no_judge_is_safe():
    assert vix_topup_from_date([], forward_from="2026-06-26", judge_until=None) == "2026-06-26"


# ---------------------------------------------------------------------------
# Source pins — the AUTO-UPDATE path repairs partial days + VIX holes too
# (tests never import runtime: motor absent)
# ---------------------------------------------------------------------------

def test_hygiene_plan_also_repairs_incomplete_days():
    dh = (ROOT / "backend" / "app" / "data_hygiene.py").read_text(encoding="utf-8")
    plan = dh[dh.index("async def compute_hygiene_plan"):dh.index("SPOT_INCOMPLETE_TOLERANCE = ")]
    # the daily auto-update runs compute_hygiene_plan, so it must repair partial
    # sessions too (not just the manual catch-up).
    assert "incomplete_spot_days(" in plan
    assert "under-captured" in plan
    assert "SPOT_REPAIR_LOOKBACK_DAYS" in plan  # bounded churn guard wired in


def test_vix_topup_uses_hole_aware_start():
    rt = (ROOT / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")
    vix = rt[rt.index("async def _topup_vix"):rt.index("async def _trigger_autoupdate")]
    assert "vix_topup_from_date(" in vix  # forward-only append replaced with hole-aware start


# ---------------------------------------------------------------------------
# Contract pins (source text — tests never import server/runtime: motor absent)
# ---------------------------------------------------------------------------

def test_catch_up_plan_pulls_back_to_incomplete_days():
    dh = (ROOT / "backend" / "app" / "data_hygiene.py").read_text(encoding="utf-8")
    plan = dh[dh.index("async def compute_catch_up_plan"):]
    assert "incomplete_spot_days(" in plan
    assert "incomplete_days" in plan
    assert "SPOT_REPAIR_LOOKBACK_DAYS" in plan  # churn guard wired in


def test_vix_is_in_sync_and_daily_loop():
    server = backend_api_text()
    # Sync now route tops up VIX (best-effort) and reports it.
    assert "_topup_vix()" in server
    assert '"vix": vix_result' in server
    # Daily loop receives the VIX top-up as its pre_run side-task.
    srv = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    assert "pre_run_fn=_topup_vix" in srv
    au = (ROOT / "backend" / "app" / "warehouse_autoupdate.py").read_text(encoding="utf-8")
    assert "pre_run_fn" in au and "await pre_run_fn()" in au


def test_planner_relabeled_as_manual_not_band():
    panel = (ROOT / "frontend" / "src" / "components" / "warehouse" / "OptionPlannerPanel.jsx").read_text(encoding="utf-8")
    assert "option-warehouse-philosophy-note" in panel
    assert "daily ATM band" in panel
