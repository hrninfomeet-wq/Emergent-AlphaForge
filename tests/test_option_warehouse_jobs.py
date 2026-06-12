import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.option_warehouse_jobs import compact_date_ranges, option_fetch_tasks_from_plan  # noqa: E402
from tests.contract_corpus import backend_api_text


def test_compact_date_ranges_groups_contiguous_calendar_dates():
    ranges = compact_date_ranges(["2026-05-01", "2026-05-02", "2026-05-04", "2026-05-06", "2026-05-05"])

    assert ranges == [
        {"from_date": "2026-05-01", "to_date": "2026-05-02"},
        {"from_date": "2026-05-04", "to_date": "2026-05-06"},
    ]


def test_option_fetch_tasks_use_missing_selected_dates_not_whole_window():
    plan = {
        "items": [
            {
                "instrument_key": "NSE_FO|1",
                "underlying": "NIFTY",
                "expiry_date": "2026-05-28",
                "strike": 25000,
                "side": "CE",
                "trading_symbol": "NIFTY CE",
                "lot_size": 65,
                "selected_dates": ["2026-05-20", "2026-05-21", "2026-05-24"],
                "fetch_dates": ["2026-05-20", "2026-05-21"],
                "needs_fetch": True,
            },
            {
                "instrument_key": "NSE_FO|2",
                "underlying": "NIFTY",
                "expiry_date": "2026-05-28",
                "strike": 24950,
                "side": "PE",
                "selected_dates": ["2026-05-20"],
                "fetch_dates": [],
                "needs_fetch": False,
            },
        ]
    }

    tasks = option_fetch_tasks_from_plan(plan, fetch_missing_only=True)

    assert tasks == [
        {
            "instrument_key": "NSE_FO|1",
            "from_date": "2026-05-20",
            "to_date": "2026-05-21",
            "contract": {
                "underlying": "NIFTY",
                "expiry_date": "2026-05-28",
                "strike": 25000,
                "side": "CE",
                "trading_symbol": "NIFTY CE",
                "lot_size": 65,
            },
        }
    ]


def test_backend_and_frontend_expose_background_option_fetch_jobs():
    server = backend_api_text()
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    assert '@api.post("/upstox/options/warehouse/fetch/jobs")' in server
    assert '@api.get("/upstox/options/warehouse/fetch/jobs/{run_id}")' in server
    assert "run_option_warehouse_fetch_job" in server
    assert "startOptionWarehouseFetchJob" in api
    assert "getOptionWarehouseFetchJob" in api
    assert "option-warehouse-fetch-progress" in warehouse
    assert "background" in warehouse.lower()
