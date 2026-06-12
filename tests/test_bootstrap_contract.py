from pathlib import Path
import py_compile
import subprocess
from tests.contract_corpus import backend_api_text


ROOT = Path(__file__).resolve().parents[1]


def test_backend_server_is_syntax_valid():
    backend = ROOT / "backend"
    for f in [backend / "server.py", backend / "app" / "schemas.py",
              backend / "app" / "runtime.py", *sorted((backend / "app" / "routers").glob("*.py"))]:
        py_compile.compile(str(f), doraise=True)


def test_backend_server_imports_pandas_for_upstox_ingest():
    server = backend_api_text()

    assert "import pandas as pd" in server
    assert 'pd.to_datetime(df["ts"]' in server


def test_local_setup_contract_files_exist():
    required = [
        ROOT / "backend" / ".env.example",
        ROOT / "frontend" / ".env.example",
        ROOT / "frontend" / "yarn.lock",
        ROOT / "start-app.bat",
        ROOT / "docs" / "STARTUP_MANUAL.md",
    ]

    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]

    assert missing == []


def test_windows_startup_assistant_documents_safe_startup_flow():
    launcher = (ROOT / "start-app.bat").read_text(encoding="utf-8")
    manual = (ROOT / "docs" / "STARTUP_MANUAL.md").read_text(encoding="utf-8")

    for phrase in (
        "docker compose up -d --build",
        "docker info",
        "backend\\.env",
        "http://localhost:8001/api/health",
        "mongo_data",
        "--check-only",
    ):
        assert phrase in launcher

    for phrase in (
        "Manual Docker Startup",
        "Do not delete the Docker volume",
        "Do not print `backend\\.env`",
        "docker compose down -v",
        "Invoke-RestMethod http://localhost:8001/api/health",
    ):
        assert phrase in manual


def test_requirements_include_imported_runtime_dependencies():
    requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")

    for package in ("httpx", "yfinance"):
        assert package in requirements


def test_requirements_do_not_include_emergent_only_packages():
    requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")

    assert "emergentintegrations" not in requirements


def test_env_examples_are_not_gitignored():
    for path in ("backend/.env.example", "frontend/.env.example"):
        result = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=ROOT,
            check=False,
        )

        assert result.returncode == 1


def test_docker_compose_uses_current_schema():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert not compose.lstrip().startswith("version:")


def test_backend_container_loads_local_env_file():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "env_file:" in compose
    assert "./backend/.env" in compose


def test_frontend_status_copy_matches_current_phase():
    dashboard = (ROOT / "frontend" / "src" / "pages" / "Dashboard.jsx").read_text(encoding="utf-8")
    layout = (ROOT / "frontend" / "src" / "components" / "Layout.jsx").read_text(encoding="utf-8")

    assert "phase 2 · V1 build" not in layout
    assert 'value="V1"' not in dashboard
    assert "Phase 3 — Auto-Optimizer" in dashboard
    assert "status: \"done\"" in dashboard


def test_dashboard_warehouse_health_banner_present():
    """The Dashboard must surface the warehouse-health banner (quality-hardening
    Slice A): a 'can I trust today's data?' strip with a lazy band-coverage
    Check button (the plan costs ~5s, so it must not auto-run on mount)."""
    dashboard = (ROOT / "frontend" / "src" / "pages" / "Dashboard.jsx").read_text(encoding="utf-8")
    banner = (ROOT / "frontend" / "src" / "components" / "WarehouseHealthBanner.jsx").read_text(encoding="utf-8")

    assert "WarehouseHealthBanner" in dashboard
    assert "warehouse-health-banner" in banner
    # Lazy band-coverage check behind a button (no plan fetch on mount).
    assert "warehouse-health-check" in banner
    assert "dataHygienePlan" in banner
    # The cheap status signals the strip aggregates.
    assert "autoUpdateStatus" in banner
    assert "upstoxStreamStatus" in banner


def test_frontend_exposes_theme_selector_and_tokens():
    app = (ROOT / "frontend" / "src" / "App.js").read_text(encoding="utf-8")
    layout = (ROOT / "frontend" / "src" / "components" / "Layout.jsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
    theme = ROOT / "frontend" / "src" / "lib" / "theme.jsx"

    assert theme.exists()
    theme_text = theme.read_text(encoding="utf-8")
    assert "ThemeProvider" in app
    assert "useTheme" in layout
    assert 'data-testid="theme-select"' in layout
    for value in ("system", "black", "white"):
        assert f'value="{value}"' in layout
        assert value in theme_text
    assert '[data-theme="light"]' in css
    assert '[data-theme="dark"]' in css
    assert "color-scheme: light" in css
    assert "color-scheme: dark" in css


def test_handoff_is_local_first_not_emergent_only():
    handoff = (ROOT / "docs" / "HANDOFF.md").read_text(encoding="utf-8")

    assert "Docker Compose" in handoff or "docker compose" in handoff
    assert "sudo supervisorctl restart backend" not in handoff
    assert "integration_playbook_expert_v2" not in handoff


def test_project_docs_and_user_manual_are_current():
    required = [
        ROOT / "docs" / "PROJECT_OVERVIEW.md",
        ROOT / "docs" / "USER_MANUAL.md",
        ROOT / "docs" / "ARCHITECTURE.md",
        ROOT / "docs" / "HANDOFF.md",
    ]
    assert [p.name for p in required if not p.exists()] == []

    overview = (ROOT / "docs" / "PROJECT_OVERVIEW.md").read_text(encoding="utf-8")
    manual = (ROOT / "docs" / "USER_MANUAL.md").read_text(encoding="utf-8")
    handoff = (ROOT / "docs" / "HANDOFF.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    # Both PROJECT_OVERVIEW and HANDOFF must cover the project's core topical areas.
    # Section titles evolve as the project evolves; the assertions below check topical
    # coverage rather than exact headings.
    for text in (overview, handoff):
        for phrase in (
            "Status",  # status section in some form
            "Option",  # option workflow / planner
            "Upstox",  # broker integration
            "Architecture",  # architecture pointer / module map
        ):
            assert phrase in text

    for phrase in (
        "Theme",
        "Data Warehouse",
        "Backtest Lab",
        "Option Data Planner",
        "User Manual",
    ):
        assert phrase in manual

    assert "docs/PROJECT_OVERVIEW.md" in readme
    assert "docs/USER_MANUAL.md" in readme


def test_frontend_exposes_upstox_warehouse_controls():
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    for method in ("upstoxStatus", "startUpstoxAuth", "disconnectUpstox", "ingestUpstox", "marketQuote"):
        assert method in api

    for testid in (
        "upstox-panel",
        "upstox-connect-button",
        "upstox-ingest-button",
        "upstox-live-quote-button",
        "upstox-live-quote-card",
    ):
        assert testid in warehouse

    assert "upstox-chunk-guidance" in warehouse
    assert "Live market snapshot" in warehouse
    assert "Auto is recommended" in warehouse
    assert "chunk_days: upstoxForm.chunk_days === \"\" ? null" in warehouse


def test_backend_upstox_index_ingest_supports_auto_chunk_guidance():
    server = backend_api_text()
    chunking = ROOT / "backend" / "app" / "chunking.py"

    assert chunking.exists()
    assert "chunk_guidance_for_index" in chunking.read_text(encoding="utf-8")
    assert "chunk_days: Optional[int] = None" in server
    assert "chunk_guidance_for_index" in server
    assert '"chunk_guidance": guidance' in server


def test_backend_exposes_data_trust_routes_and_backtest_audit():
    server = backend_api_text()

    assert 'audit_integrity' in server
    assert '@api.get("/warehouse/audit/{instrument}")' in server
    assert '@api.delete("/warehouse/data/{instrument}")' in server
    assert '"data_audit"' in server


def test_backend_exposes_read_only_upstox_option_contract_routes():
    server = backend_api_text()
    client = (ROOT / "backend" / "app" / "upstox_client.py").read_text(encoding="utf-8")

    assert "fetch_option_contracts" in client
    assert "normalize_option_contract" in client
    assert '@api.get("/upstox/options/contracts/{instrument}")' in server
    assert '@api.get("/upstox/expired-options/contracts/{instrument}")' in server
    assert '@api.post("/upstox/options/contracts/{instrument}/sync")' in server
    assert '@api.get("/options/contracts/{instrument}")' in server


def test_backend_declares_phase4_option_and_tick_indexes():
    db = (ROOT / "backend" / "app" / "db.py").read_text(encoding="utf-8")

    for collection in (
        "option_contracts",
        "options_1m",
        "ticks",
        "chain_snapshots",
        "paper_trades",
    ):
        assert f"db.{collection}.create_index" in db


def test_backend_exposes_option_candle_ingest_routes():
    server = backend_api_text()
    client = (ROOT / "backend" / "app" / "upstox_client.py").read_text(encoding="utf-8")

    assert "fetch_historical_1m_for_key_chunked" in client
    assert '@api.post("/upstox/options/candles/ingest")' in server
    assert '@api.get("/options/candles")' in server


def test_backend_exposes_option_warehouse_preview_and_fetch_routes():
    server = backend_api_text()
    planner = ROOT / "backend" / "app" / "option_data_planner.py"

    assert planner.exists()
    assert "build_option_warehouse_plan" in planner.read_text(encoding="utf-8")
    assert '@api.post("/upstox/options/warehouse/preview")' in server
    assert '@api.post("/upstox/options/warehouse/fetch")' in server
    assert "OptionWarehousePlanReq" in server
    assert "sample_interval_minutes" in server
    assert "Fixed expiry date is required" in server


def test_backend_exposes_optional_paired_option_backtest():
    server = backend_api_text()
    option_backtest = (ROOT / "backend" / "app" / "option_backtest.py")

    assert option_backtest.exists()
    assert "OptionBacktestReq" in server
    assert "option_backtest" in server
    assert "simulate_paired_option_trades" in server
    assert "auto_fetch" in server
    assert "fetch_historical_1m_for_key_chunked" in server
    assert "_resolve_option_expiry_by_trade" in server
    assert "expiry_by_trade" in server


def test_frontend_exposes_data_trust_controls():
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")
    backtest = (ROOT / "frontend" / "src" / "pages" / "BacktestLab.jsx").read_text(encoding="utf-8")

    for method in ("auditWarehouse", "clearWarehouseData"):
        assert method in api

    for testid in (
        "warehouse-audit-panel",
        "warehouse-audit-button",
        "warehouse-clear-button",
        "data-audit-card",
    ):
        assert testid in warehouse + backtest


def test_frontend_exposes_option_backtest_controls_and_results():
    backtest = (ROOT / "frontend" / "src" / "pages" / "BacktestLab.jsx").read_text(encoding="utf-8")

    assert "option_backtest" in backtest
    for testid in (
        "option-backtest-panel",
        "option-backtest-switch",
        "option-expiry-input",
        "option-moneyness-select",
        "option-lots-input",
        "option-auto-fetch-switch",
        "option-backtest-card",
        "option-pairing-coverage",
        "option-auto-fetch-status",
    ):
        assert testid in backtest


def test_frontend_exposes_option_warehouse_planner_controls():
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    for method in ("previewOptionWarehouse", "fetchOptionWarehouse"):
        assert method in api

    for testid in (
        "option-warehouse-panel",
        "option-warehouse-instrument",
        "option-warehouse-from-date",
        "option-warehouse-to-date",
        "option-warehouse-preview-button",
        "option-warehouse-fetch-button",
        "option-warehouse-plan-table",
        "option-warehouse-fetch-summary",
        "option-warehouse-chunk-guidance",
        "option-warehouse-help",
    ):
        assert testid in warehouse

    for copy in (
        "Next available uses stored contract expiries",
        "Fixed date forces one expiry",
        "Sample every N minutes",
        "Use 1 for final strategy prep",
        "Historical expiry changes are handled only when those old contracts are stored",
    ):
        assert copy in warehouse


def test_frontend_exposes_expired_option_contract_backfill_controls():
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    assert "backfillExpiredOptionContracts" in api
    assert "/upstox/expired-options/contracts/" in api

    for testid in (
        "expired-contract-backfill-panel",
        "expired-contract-instrument",
        "expired-contract-from-date",
        "expired-contract-to-date",
        "expired-contract-max-expiries",
        "expired-contract-confirm-large",
        "expired-contract-backfill-button",
        "expired-contract-backfill-summary",
    ):
        assert testid in warehouse

    for copy in (
        "Backfill expired option contracts",
        "metadata only",
        "required before old option candles can be planned reliably",
    ):
        assert copy in warehouse
