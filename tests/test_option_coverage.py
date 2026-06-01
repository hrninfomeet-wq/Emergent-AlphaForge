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


def test_background_jobs_tracked_globally_above_router():
    """Long-running warehouse jobs must be tracked in a provider mounted above
    the router so progress survives navigation."""
    app = (ROOT / "frontend" / "src" / "App.js").read_text(encoding="utf-8")
    jobs = (ROOT / "frontend" / "src" / "lib" / "jobs.jsx").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    # Provider wraps the app shell (which contains the router).
    assert "JobsProvider" in app
    # Jobs are persisted so a reload resumes polling.
    assert "localStorage" in jobs
    assert "upstox_ingest" in jobs and "option_fetch" in jobs
    # The page no longer owns the polling loop; it delegates to the provider.
    assert "useJobs" in warehouse
    assert "startJob" in warehouse
    assert "pollOptionFetchJob" not in warehouse
    assert "pollUpstoxIngestJob" not in warehouse


def test_data_hygiene_wired_into_warehouse_ui():
    """The Data Hygiene workflow must be surfaced in the UI and routed through
    the global job tracker."""
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    panel = (ROOT / "frontend" / "src" / "components" / "DataHygienePanel.jsx").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")
    jobs = (ROOT / "frontend" / "src" / "lib" / "jobs.jsx").read_text(encoding="utf-8")

    # API methods exist for the three hygiene endpoints.
    assert "dataHygienePlan" in api
    assert "dataHygieneExecute" in api
    assert "dataHygieneStatus" in api
    # Panel calls plan + execute and is mounted on the page.
    assert "dataHygienePlan" in panel
    assert "dataHygieneExecute" in panel
    assert "DataHygienePanel" in warehouse
    # Execute jobs are tracked via the global job batch tracker.
    assert "startHygieneBatch" in jobs


def test_warehouse_auto_update_wired_end_to_end():
    """Auto-update must be triggered on startup, on OAuth connect, and daily,
    with status/toggle routes and a UI surface."""
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    panel = (ROOT / "frontend" / "src" / "components" / "DataHygienePanel.jsx").read_text(encoding="utf-8")

    # Triggers: startup task, daily loop, and OAuth-connect.
    assert "warehouse-autoupdate-startup" in server
    assert "daily_autoupdate_loop" in server
    assert '_trigger_autoupdate("oauth_connect")' in server
    # Routes for status / toggle / manual run.
    assert '@api.get("/warehouse/auto-update/status")' in server
    assert '@api.post("/warehouse/auto-update/toggle")' in server
    # UI surface.
    assert "autoUpdateStatus" in api
    assert "auto-update-toggle" in panel


def test_warehouse_point_lookup_wired_end_to_end():
    """The spot + ATM CE/PE point-in-time lookup must be wired end to end."""
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    lookup = (ROOT / "frontend" / "src" / "components" / "WarehouseLookup.jsx").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    assert '@api.get("/warehouse/lookup")' in server
    assert "lookup_market_snapshot" in server
    assert "warehouseLookup" in api
    assert "warehouse-lookup-panel" in lookup
    assert "WarehouseLookup" in warehouse


def test_backtest_run_journal_moved_to_backtest_lab():
    """The Backtest Run Journal must live in the Backtest Lab; the Signal
    Journal page must no longer be the backtest-run table."""
    lab = (ROOT / "frontend" / "src" / "pages" / "BacktestLab.jsx").read_text(encoding="utf-8")
    journal_component = (ROOT / "frontend" / "src" / "components" / "BacktestRunJournal.jsx").read_text(encoding="utf-8")
    signal_journal = (ROOT / "frontend" / "src" / "pages" / "SignalJournal.jsx").read_text(encoding="utf-8")

    assert "BacktestRunJournal" in lab
    assert "backtest-run-journal" in journal_component
    # Signal Journal is now the deployment signal audit trail, not backtest runs.
    assert "Deployment Signal Journal" in signal_journal
    assert "listSignals" in signal_journal
    assert "listBacktestRuns" not in signal_journal


def test_oauth_token_expiry_countdown_present():
    """A token-expiry countdown must be surfaced in the global top bar and the
    Upstox panel."""
    layout = (ROOT / "frontend" / "src" / "components" / "Layout.jsx").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    assert "topbar-token-indicator" in layout
    assert "TokenExpiryIndicator" in layout
    assert "upstox-token-expiry-badge" in warehouse


def test_warehouse_candlestick_chart_wired_end_to_end():
    """The per-index candlestick chart + resample endpoint must be wired, with
    1m timeframe, an OHLC crosshair legend, and a date/time locator."""
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    chart = (ROOT / "frontend" / "src" / "components" / "WarehouseChart.jsx").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    assert '@api.get("/warehouse/ohlc/{instrument}")' in server
    assert "build_ohlc_response" in server
    assert "warehouseOhlc" in api
    assert "warehouse-chart-panel" in chart
    assert "WarehouseChart" in warehouse
    # 1m timeframe present.
    assert '"1m"' in chart
    # OHLC legend that follows the crosshair.
    assert "chart-ohlc-legend" in chart
    assert "subscribeCrosshairMove" in chart
    # Date/time locator with marker.
    assert "chart-locate-button" in chart
    assert "createSeriesMarkers" in chart


def test_warehouse_chart_has_ist_axis_theme_and_session_context():
    """The warehouse chart must make candle OHLC, chart theme, and IST session
    context explicit so stored data can be audited visually."""
    chart = (ROOT / "frontend" / "src" / "components" / "WarehouseChart.jsx").read_text(encoding="utf-8")

    # OHLC values are separately addressable in the overlay, not only the close.
    assert "chart-ohlc-open" in chart
    assert "chart-ohlc-high" in chart
    assert "chart-ohlc-low" in chart
    assert "chart-ohlc-close" in chart
    # The chart has local theme controls independent of the page theme.
    assert "chart-theme-system" in chart
    assert "chart-theme-dark" in chart
    assert "chart-theme-light" in chart
    # Time labels and session markers must be IST-aware.
    assert "tickMarkFormatter" in chart
    assert "axisLabel" in chart
    assert "buildSessionMarkers" in chart
    assert "chart-session-note" in chart
    # Slow full-history requests must not overwrite a newer timeframe selection.
    assert "loadSeqRef" in chart
