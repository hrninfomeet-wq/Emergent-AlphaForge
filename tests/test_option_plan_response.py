import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.option_plan_response import compact_option_plan_for_response  # noqa: E402


def test_compact_option_plan_for_response_keeps_coverage_but_removes_large_date_maps():
    plan = {
        "summary": {"planned_contracts": 1, "missing_data_contracts": 1},
        "items": [
            {
                "instrument_key": "NSE_FO|1",
                "trading_symbol": "NIFTY 24000 CE",
                "selected_dates": ["2026-05-20", "2026-05-21"],
                "fetch_dates": ["2026-05-21"],
                "selected_date_counts": {
                    "2026-05-20": {"expected": 375, "stored": 375},
                    "2026-05-21": {"expected": 375, "stored": 100},
                },
                "expected_candles": 750,
                "stored_selected_date_candles": 475,
                "stored_candles": 900,
                "coverage_pct": 63.33,
                "needs_fetch": True,
            }
        ],
        "missing": [{"spot_ts": 1}],
    }

    result = compact_option_plan_for_response(plan)
    item = result["items"][0]

    assert "selected_dates" not in item
    assert "fetch_dates" not in item
    assert "selected_date_counts" not in item
    assert item["selected_date_count"] == 2
    assert item["fetch_date_count"] == 1
    assert item["first_selected_date"] == "2026-05-20"
    assert item["last_selected_date"] == "2026-05-21"
    assert item["first_fetch_date"] == "2026-05-21"
    assert item["last_fetch_date"] == "2026-05-21"
    assert result["missing_count"] == 1
    assert result["missing"] == []
    assert result["summary"]["planned_coverage_pct"] == 63.33
    assert result["summary"]["stored_selected_date_candles"] == 475


def test_backend_and_frontend_default_option_planning_to_atm_only():
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    assert 'Field(default_factory=lambda: ["atm"])' in server
    assert 'moneyness: ["atm"]' in warehouse


def test_frontend_explains_planned_coverage_and_raw_option_audit_scope():
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    assert "option-warehouse-planned-coverage" in warehouse
    assert "Planned coverage" in warehouse
    assert "Raw universe audit" in warehouse
    assert "planner-selected" in warehouse
