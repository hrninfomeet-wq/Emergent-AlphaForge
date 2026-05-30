import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.option_coverage import summarize_option_coverage  # noqa: E402


def test_summarize_option_coverage_groups_days_and_contract_counts():
    result = summarize_option_coverage([
        {
            "underlying": "NIFTY",
            "date": "2024-11-28",
            "candles": 750,
            "contracts": 2,
            "complete_contracts": 2,
            "instrument_keys": ["CE1", "PE1"],
        },
        {
            "underlying": "NIFTY",
            "date": "2024-11-29",
            "candles": 500,
            "contracts": 2,
            "complete_contracts": 1,
            "instrument_keys": ["CE2", "PE2"],
        },
    ])

    nifty = result["NIFTY"]
    assert nifty["total_candles"] == 1250
    assert nifty["contract_count"] == 4
    assert nifty["first_date"] == "2024-11-28"
    assert nifty["last_date"] == "2024-11-29"
    assert nifty["days"][0]["coverage_pct"] == 100.0
    assert nifty["days"][1]["coverage_pct"] == 66.67
    assert nifty["days"][1]["incomplete_contracts"] == 1


def test_backend_and_frontend_expose_option_coverage_heatmap():
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    assert '@api.get("/options/coverage")' in server
    assert "optionCoverage" in api
    assert "option-coverage-heatmap" in warehouse


def test_option_coverage_endpoint_is_cache_backed():
    """The coverage endpoint must read the precomputed cache, not the slow
    full-collection aggregation, on the page-load path."""
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    assert "get_option_coverage_cached" in server


def test_holiday_calendar_wired_end_to_end():
    """Holiday calendar endpoint + UI modal must be present and connected."""
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")
    dialog = (ROOT / "frontend" / "src" / "components" / "HolidayCalendarDialog.jsx").read_text(encoding="utf-8")

    assert '@api.get("/calendar/holidays")' in server
    assert "marketHolidays" in api
    assert "HolidayCalendarDialog" in warehouse
    assert "holiday-calendar-dialog" in dialog


def test_obsolete_yfinance_ingest_panel_removed():
    """The yfinance 7d/14d ingest panel was obsolete and must be gone."""
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")
    assert "yfinance" not in warehouse
    assert "Ingest 7d" not in warehouse


def test_emergent_badge_and_telemetry_removed():
    """The Made-with-Emergent badge, its loader script, and PostHog telemetry
    must be removed from the app shell."""
    index_html = (ROOT / "frontend" / "public" / "index.html").read_text(encoding="utf-8")
    assert "emergent-badge" not in index_html
    assert "emergent-main.js" not in index_html
    assert "posthog" not in index_html.lower()
    assert "AlphaForge" in index_html
