import os
from pathlib import Path
import py_compile
import subprocess
import pytest
from tests.contract_corpus import backend_api_text
from tests.contract_corpus import warehouse_page_text


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


def test_startup_readiness_probe_uses_ipv4_loopback_not_localhost():
    """The step-6 readiness probe must dial 127.0.0.1, never localhost.

    docker-compose publishes the ports IPv4-only (127.0.0.1:8001, 127.0.0.1:3000)
    while Windows resolves `localhost` to ::1 first. Nothing listens on ::1, and
    that dead attempt stalls ~2s before .NET falls back to IPv4 — which silently
    blew the probe's own timeout on EVERY poll. Both wait loops then ran to
    exhaustion (~8.5 min) and reported "not ready" on a perfectly healthy stack.
    """
    launcher = (ROOT / "start-app.bat").read_text(encoding="utf-8")

    assert ":WaitForServices" in launcher
    assert "http://127.0.0.1:8001/api/health" in launcher
    assert "http://127.0.0.1:3000" in launcher

    # The echoed URLs stay human-friendly `localhost`; only the probes are pinned.
    for banned in (
        "Invoke-RestMethod -Uri 'http://localhost:",
        "Invoke-WebRequest -Uri 'http://localhost:",
    ):
        assert banned not in launcher, f"readiness probe regressed to localhost: {banned}"


def test_windows_launcher_default_success_path_is_non_interactive():
    """A double-click must need no keyboard input on the successful path."""
    launcher = (ROOT / "start-app.bat").read_text(encoding="utf-8")
    low = launcher.lower()

    assert "set /p" not in low
    assert "\ndocker compose up -d --build\n" in launcher
    assert 'start "" "http://localhost:3000"' in launcher

    success_tail = launcher[
        launcher.index('if "%NO_BROWSER%"'):
        launcher.rindex("\n:EnsureDockerEngine")
    ]
    assert "pause" not in success_tail.lower()


@pytest.mark.skipif(os.name != "nt", reason="executes the Windows batch launcher")
def test_windows_launcher_rejects_metacharacter_arguments_without_execution():
    result = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "call",
            "start-app.bat",
            "probe&echo INJECTION_MARKER",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 2
    assert "INJECTION_MARKER" not in result.stdout
    assert "INJECTION_MARKER" not in result.stderr


def test_windows_launcher_autostarts_docker_with_bounded_wait():
    launcher = (ROOT / "start-app.bat").read_text(encoding="utf-8")
    engine = launcher[
        launcher.rindex("\n:EnsureDockerEngine"):
        launcher.rindex("\n:CheckEnvHints")
    ]

    assert "%ProgramFiles%\\Docker\\Docker\\Docker Desktop.exe" in engine
    assert 'start "" "%DOCKER_DESKTOP_EXE%"' in engine
    assert engine.count("docker info") >= 2
    assert "AddSeconds(180)" in engine
    assert "timed out" in engine.lower()
    assert "exit /b 1" in engine
    assert "set /p" not in engine.lower()
    assert "goto docker_wait" not in engine.lower()

    launch_to_poll = engine[
        engine.index('start "" "%DOCKER_DESKTOP_EXE%"'):
        engine.index("echo Waiting up to 180 seconds for the Docker engine...")
    ]
    assert "if errorlevel 1" not in launch_to_poll.lower()


def test_windows_launcher_opens_browser_only_after_full_readiness():
    launcher = (ROOT / "start-app.bat").read_text(encoding="utf-8")
    up = launcher.index("\ndocker compose up -d --build\n")
    wait = launcher.index("call :WaitForServices", up)
    ready_gate = launcher.index('if not "%READY_CODE%"=="0"', wait)
    browser = launcher.index('start "" "http://localhost:3000"', ready_gate)

    assert up < wait < ready_gate < browser
    assert "exit /b 1" in launcher[ready_gate:browser]
    assert "$f=$true" in launcher
    assert ".StatusCode -ge 200" in launcher
    assert ".StatusCode -lt 400" in launcher
    assert ".StatusCode -lt 500" not in launcher


def test_windows_launcher_preserves_check_only_and_no_browser():
    launcher = (ROOT / "start-app.bat").read_text(encoding="utf-8")
    up = launcher.index("\ndocker compose up -d --build\n")
    check_only = launcher.index('if "%CHECK_ONLY%"=="1"')
    no_browser = launcher.index('if "%NO_BROWSER%"=="1"')
    browser = launcher.index('start "" "http://localhost:3000"')

    assert check_only < up
    assert 'exit /b 0' in launcher[check_only:up]
    assert up < no_browser < browser
    assert 'goto done' in launcher[no_browser:browser].lower()
    assert "exit before starting Docker." not in launcher
    assert "without starting Docker Desktop or containers." in launcher
    assert "--rebuild" in launcher


def test_windows_launcher_does_not_read_or_mutate_sensitive_state():
    launcher = (ROOT / "start-app.bat").read_text(encoding="utf-8")
    low = launcher.lower()

    for transient_secret_var in (
        "FERNET_KEY_VALUE",
        "UPSTOX_CLIENT_ID_VALUE",
        "UPSTOX_CLIENT_SECRET_VALUE",
    ):
        assert transient_secret_var not in launcher

    for required_safe_fragment in (
        'findstr /R /C:"^FERNET_KEY=..*" "backend\\.env" >nul 2>nul',
        'findstr /R /C:"^UPSTOX_CLIENT_ID=..*" "backend\\.env" >nul 2>nul',
        'findstr /R /C:"^UPSTOX_CLIENT_SECRET=..*" "backend\\.env" >nul 2>nul',
        "WARNING: LIVE_AUTOPLACE_ARMED is enabled in backend\\.env.",
    ):
        assert required_safe_fragment in launcher

    for banned in (
        "type backend\\.env",
        "docker compose down -v",
        "docker volume rm",
        "docker system prune",
        "mcp__flattrade",
        'set "live_autoplace_armed=',
        "echo live_autoplace_armed=0",
        "echo live_autoplace_armed=1",
    ):
        assert banned not in low


def _prepare_windows_launcher_sandbox(tmp_path):
    (tmp_path / "start-app.bat").write_text(
        (ROOT / "start-app.bat").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    fake_bin = tmp_path / "fake-bin"
    backend.mkdir()
    frontend.mkdir()
    fake_bin.mkdir()
    (backend / ".env.example").write_text(
        "FERNET_KEY=test-only\n"
        "UPSTOX_CLIENT_ID=test-only\n"
        "UPSTOX_CLIENT_SECRET=test-only\n"
        "LIVE_AUTOPLACE_ARMED=0\n",
        encoding="utf-8",
    )
    (frontend / ".env.example").write_text("REACT_APP_BACKEND_URL=\n", encoding="utf-8")
    stub_source = fake_bin / "Stub.cs"
    stub_exe = fake_bin / "stub.exe"
    stub_source.write_text(
        "using System;\n"
        "using System.IO;\n"
        "public static class Stub {\n"
        "  public static int Main(string[] args) {\n"
        "    var log = Environment.GetEnvironmentVariable(\"ALPHAFORGE_LAUNCHER_STUB_LOG\");\n"
        "    var exe = Path.GetFileNameWithoutExtension(Environment.GetCommandLineArgs()[0]);\n"
        "    var command = String.Join(\" \", args);\n"
        "    if (!String.IsNullOrEmpty(log)) {\n"
        "      File.AppendAllText(log, exe + \" \" + command + Environment.NewLine);\n"
        "    }\n"
        "    if (exe.Equals(\"powershell\", StringComparison.OrdinalIgnoreCase)) {\n"
        "      if (command.Contains(\"Get-Content\")) return 1;\n"
        "      if (Environment.GetEnvironmentVariable(\"ALPHAFORGE_STUB_BACKEND_RESTORING\") == \"1\""
        " && command.Contains(\"Invoke-RestMethod\") && !command.Contains(\"AddSeconds(180)\")) {\n"
        "        var state = Environment.GetEnvironmentVariable(\"ALPHAFORGE_STUB_PROBE_STATE\");\n"
        "        var count = File.Exists(state) ? Int32.Parse(File.ReadAllText(state)) : 0;\n"
        "        count += 1; File.WriteAllText(state, count.ToString());\n"
        "        if (count == 1) return 1;\n"
        "      }\n"
        "      if (Environment.GetEnvironmentVariable(\"ALPHAFORGE_STUB_SERVICES_COLD\") == \"1\""
        " && command.Contains(\"Invoke-RestMethod\") && !command.Contains(\"AddSeconds(180)\")) return 1;\n"
        "    }\n"
        "    if (exe.Equals(\"docker\", StringComparison.OrdinalIgnoreCase)"
        " && Environment.GetEnvironmentVariable(\"ALPHAFORGE_STUB_BACKEND_RESTORING\") == \"1\""
        " && command.Contains(\"compose ps --status running -q backend\")) Console.WriteLine(\"stub-backend\");\n"
        "    if (exe.Equals(\"docker\", StringComparison.OrdinalIgnoreCase) && args.Length > 0"
        " && args[0].Equals(\"info\", StringComparison.OrdinalIgnoreCase)"
        " && Environment.GetEnvironmentVariable(\"ALPHAFORGE_STUB_DOCKER_INFO_FAIL\") == \"1\") return 1;\n"
        "    if (exe.Equals(\"docker\", StringComparison.OrdinalIgnoreCase) && args.Length > 0"
        " && args[0].Equals(\"info\", StringComparison.OrdinalIgnoreCase)"
        " && Environment.GetEnvironmentVariable(\"ALPHAFORGE_STUB_DOCKER_INFO_FAIL_ONCE\") == \"1\") {\n"
        "      var state = Environment.GetEnvironmentVariable(\"ALPHAFORGE_STUB_DOCKER_INFO_STATE\");\n"
        "      var count = File.Exists(state) ? Int32.Parse(File.ReadAllText(state)) : 0;\n"
        "      count += 1; File.WriteAllText(state, count.ToString());\n"
        "      if (count == 1) return 1;\n"
        "    }\n"
        "    return 0;\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    compile_env = os.environ.copy()
    compile_env["ALPHAFORGE_STUB_SOURCE"] = str(stub_source)
    compile_env["ALPHAFORGE_STUB_EXE"] = str(stub_exe)
    compiled = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Add-Type -Path $env:ALPHAFORGE_STUB_SOURCE "
            "-OutputAssembly $env:ALPHAFORGE_STUB_EXE -OutputType ConsoleApplication",
        ],
        env=compile_env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    stub_bytes = stub_exe.read_bytes()
    (fake_bin / "docker.exe").write_bytes(stub_bytes)
    (fake_bin / "powershell.exe").write_bytes(stub_bytes)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    stub_log = tmp_path / "stub-calls.log"
    env["ALPHAFORGE_LAUNCHER_STUB_LOG"] = str(stub_log)
    return fake_bin, env, stub_log


def _run_sandboxed_launcher(tmp_path, env, *args):
    return subprocess.run(
        ["cmd.exe", "/d", "/c", "call", "start-app.bat", *args],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        input="",
        timeout=15,
        check=False,
    )


@pytest.mark.skipif(os.name != "nt", reason="executes the Windows batch launcher")
def test_windows_launcher_warm_and_cold_success_flows_use_safe_actions(tmp_path):
    """Exercise cmd.exe control flow without starting Docker, brokers, or a browser."""
    _fake_bin, env, stub_log = _prepare_windows_launcher_sandbox(tmp_path)
    result = _run_sandboxed_launcher(tmp_path, env, "--no-browser")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "AlphaForge startup complete." in result.stdout
    assert "Skipping rebuild to preserve the running guard." in result.stdout
    calls = stub_log.read_text(encoding="utf-8")
    assert "docker compose up -d --build" not in calls

    stub_log.unlink()
    env["ALPHAFORGE_STUB_SERVICES_COLD"] = "1"
    result = _run_sandboxed_launcher(tmp_path, env, "--no-browser")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "AlphaForge startup complete." in result.stdout
    calls = stub_log.read_text(encoding="utf-8")
    assert "docker compose up -d --build" in calls
    assert "powershell -NoProfile" in calls

    stub_log.unlink()
    env.pop("ALPHAFORGE_STUB_SERVICES_COLD")
    result = _run_sandboxed_launcher(tmp_path, env, "--rebuild", "--no-browser")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "--rebuild can recreate the backend" in result.stdout
    assert "docker compose up -d --build" in stub_log.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="executes the Windows batch launcher")
def test_windows_launcher_waits_for_a_restoring_backend_without_rebuilding_it(tmp_path):
    _fake_bin, env, stub_log = _prepare_windows_launcher_sandbox(tmp_path)
    env["ALPHAFORGE_STUB_BACKEND_RESTORING"] = "1"
    env["ALPHAFORGE_STUB_PROBE_STATE"] = str(tmp_path / "probe-state.txt")
    result = _run_sandboxed_launcher(tmp_path, env, "--no-browser")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Waiting without recreating it" in result.stdout
    assert "The running backend was not rebuilt." in result.stdout
    calls = stub_log.read_text(encoding="utf-8")
    assert "docker compose up" not in calls
    assert "AddSeconds(180)" in calls


@pytest.mark.skipif(os.name != "nt", reason="executes the Windows batch launcher")
def test_windows_launcher_continues_after_docker_desktop_launch_until_engine_is_ready(tmp_path):
    fake_bin, env, _stub_log = _prepare_windows_launcher_sandbox(tmp_path)
    desktop = tmp_path / "program-files" / "Docker" / "Docker" / "Docker Desktop.exe"
    desktop.parent.mkdir(parents=True)
    desktop.write_bytes((fake_bin / "stub.exe").read_bytes())
    env["ProgramFiles"] = str(tmp_path / "program-files")
    env["ALPHAFORGE_STUB_DOCKER_INFO_FAIL_ONCE"] = "1"
    env["ALPHAFORGE_STUB_DOCKER_INFO_STATE"] = str(tmp_path / "docker-info-state.txt")

    result = _run_sandboxed_launcher(tmp_path, env, "--no-browser")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Starting Docker Desktop automatically..." in result.stdout
    assert "Waiting up to 180 seconds for the Docker engine..." in result.stdout
    assert "Windows could not launch Docker Desktop" not in result.stdout
    assert "AlphaForge startup complete." in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="executes the Windows batch launcher")
def test_windows_check_only_does_not_start_docker_and_parses_quoted_arm_value(tmp_path):
    fake_bin, env, stub_log = _prepare_windows_launcher_sandbox(tmp_path)
    (fake_bin / "powershell.exe").unlink()
    (tmp_path / "backend" / ".env").write_text(
        "FERNET_KEY=test-only\n"
        "UPSTOX_CLIENT_ID=test-only\n"
        "UPSTOX_CLIENT_SECRET=test-only\n"
        "LIVE_AUTOPLACE_ARMED = \"1\"\n",
        encoding="utf-8",
    )
    (tmp_path / "frontend" / ".env").write_text("REACT_APP_BACKEND_URL=\n", encoding="utf-8")
    env["ALPHAFORGE_STUB_DOCKER_INFO_FAIL"] = "1"
    env["ProgramFiles"] = str(tmp_path / "missing-program-files")
    result = _run_sandboxed_launcher(tmp_path, env, "--check-only")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "LIVE_AUTOPLACE_ARMED is enabled" in result.stdout
    assert "Check-only mode will not start Docker Desktop." in result.stdout
    calls = stub_log.read_text(encoding="utf-8")
    assert "docker compose up" not in calls


def test_startup_manual_describes_one_click_and_failure_behavior():
    manual = (ROOT / "docs" / "STARTUP_MANUAL.md").read_text(encoding="utf-8")
    user_manual = (ROOT / "docs" / "USER_MANUAL.md").read_text(encoding="utf-8")
    low = manual.lower()

    for phrase in (
        "starts Docker Desktop automatically",
        "requires no confirmation on the success path",
        "opens the browser only after both services are ready",
        "exits with a non-zero status",
        "--check-only",
        "--no-browser",
        "--rebuild",
    ):
        assert phrase.lower() in low

    assert "start.bat" not in user_manual


def test_mongo_healthcheck_declares_start_period():
    """Without start_period Docker defers Mongo's FIRST probe by a full interval.

    backend gates on `condition: service_healthy`, so a Mongo that is actually
    ready in ~1-2s still held the whole stack back for the interval.
    """
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "start_period:" in compose


def test_build_contexts_exclude_host_artifacts():
    """frontend/Dockerfile runs `COPY . .` AFTER `yarn install`.

    Without a .dockerignore the host's Windows-built node_modules overwrites the
    clean --frozen-lockfile install inside the image — a correctness hazard, not
    just a slow build. Both ignore files also keep `.env` secrets out of layers.
    """
    for rel in ("frontend/.dockerignore", "backend/.dockerignore"):
        path = ROOT / rel
        assert path.exists(), f"missing {rel}"
        assert ".env" in path.read_text(encoding="utf-8"), f"{rel} must exclude .env"

    frontend_ignore = (ROOT / "frontend" / ".dockerignore").read_text(encoding="utf-8")
    assert "node_modules" in frontend_ignore


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
    for stale in ("P4a", "Upstox scaffold", "WS/live/options remain",
                  "yfinance with integrity checks"):
        assert stale not in dashboard


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
    # Topical coverage (case-insensitive), NOT exact headings — section titles evolve
    # as the docs are refreshed (e.g. HANDOFF now uses a "Current state" section and
    # links ARCHITECTURE.md). Check the topics are covered, however they're phrased.
    for text in (overview, handoff):
        low = text.lower()
        assert "status" in low or "current state" in low   # a status / current-state section
        for phrase in ("option", "upstox", "architecture"):
            assert phrase in low

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
    warehouse = warehouse_page_text()

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
    warehouse = warehouse_page_text()
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
    warehouse = warehouse_page_text()

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
    warehouse = warehouse_page_text()

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
