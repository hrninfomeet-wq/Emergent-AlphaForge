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

from app.data_hygiene import incomplete_spot_days, SPOT_INCOMPLETE_TOLERANCE  # noqa: E402
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
