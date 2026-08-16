@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "CHECK_ONLY=0"
set "NO_BROWSER=0"
set "REBUILD=0"

:parse_args
set "ARG=%~1"
if not defined ARG goto args_done
setlocal EnableDelayedExpansion
if /I "!ARG!"=="--help" (
  endlocal
  goto help
)
if /I "!ARG!"=="/?" (
  endlocal
  goto help
)
if /I "!ARG!"=="--check-only" (
  endlocal
  set "CHECK_ONLY=1"
  shift
  goto parse_args
)
if /I "!ARG!"=="--no-browser" (
  endlocal
  set "NO_BROWSER=1"
  shift
  goto parse_args
)
if /I "!ARG!"=="--rebuild" (
  endlocal
  set "REBUILD=1"
  shift
  goto parse_args
)
endlocal
echo Unknown or unsafe option.
echo Run start-app.bat --help for usage.
exit /b 2

:args_done
echo.
echo ============================================================
echo  AlphaForge Trading Lab - local startup assistant
echo ============================================================
echo.
echo Project folder:
echo   "%CD%"
echo.
echo This launcher starts the local Docker stack:
echo   - MongoDB container: alphaforge_mongo
echo   - FastAPI backend:   http://localhost:8001
echo   - React frontend:    http://localhost:3000
echo.
echo Safety notes:
echo   - This script does not print or upload broker credentials.
echo   - It does not clear MongoDB data or change live-trading settings.
echo   - Starting the backend activates its live recovery and position guard.
echo     Existing guarded live positions may transmit exits by design.
echo   - Your warehouse data lives in the Docker volume named mongo_data.
echo.

if not exist "docker-compose.yml" (
  echo ERROR: docker-compose.yml was not found.
  echo Start this file from the Emergent-AlphaForge project root.
  pause
  exit /b 1
)

if not exist "backend\.env.example" (
  echo ERROR: backend\.env.example was not found.
  pause
  exit /b 1
)

if not exist "frontend\.env.example" (
  echo ERROR: frontend\.env.example was not found.
  pause
  exit /b 1
)

echo Step 1 of 6 - checking required commands...
where docker >nul 2>nul
if errorlevel 1 (
  echo.
  echo ERROR: The docker command was not found.
  echo Install Docker Desktop for Windows, start it, then run this file again.
  echo Download: https://www.docker.com/products/docker-desktop/
  pause
  exit /b 1
)

where powershell >nul 2>nul
if errorlevel 1 (
  echo.
  echo ERROR: Windows PowerShell was not found.
  echo AlphaForge uses PowerShell for non-evaluating environment checks and readiness probes.
  pause
  exit /b 1
)

docker compose version >nul 2>nul
if errorlevel 1 (
  echo.
  echo ERROR: Docker Compose v2 is not available.
  echo Update Docker Desktop, then run this file again.
  pause
  exit /b 1
)

echo Docker, Docker Compose, and PowerShell commands found.
echo.

echo Step 2 of 6 - checking local environment files...
set "BACKEND_ENV_CREATED=0"
set "FRONTEND_ENV_CREATED=0"

if not exist "backend\.env" (
  copy "backend\.env.example" "backend\.env" >nul
  set "BACKEND_ENV_CREATED=1"
  echo Created backend\.env from backend\.env.example.
) else (
  echo Found backend\.env.
)

if not exist "frontend\.env" (
  copy "frontend\.env.example" "frontend\.env" >nul
  set "FRONTEND_ENV_CREATED=1"
  echo Created frontend\.env from frontend\.env.example.
) else (
  echo Found frontend\.env.
)

call :CheckEnvHints
if errorlevel 1 (
  call :HoldForUser
  exit /b 1
)
echo.

if "%BACKEND_ENV_CREATED%"=="1" (
  echo backend\.env was just created. Broker data features need these values later:
  echo   FERNET_KEY, UPSTOX_CLIENT_ID, UPSTOX_CLIENT_SECRET, UPSTOX_REDIRECT_URI
  echo The app can still start without Upstox, but OAuth and live data will be disabled.
  echo Edit backend\.env later with a local text editor; startup will continue automatically.
)

echo Step 3 of 6 - checking Docker Desktop engine...
set "DOCKER_ENGINE_READY=0"
if "%CHECK_ONLY%"=="1" (
  docker info >nul 2>nul
  if errorlevel 1 (
    echo Docker engine is not responding. Check-only mode will not start Docker Desktop.
    echo Run start-app.bat without --check-only when you want the launcher to start it.
  ) else (
    set "DOCKER_ENGINE_READY=1"
    echo Docker engine is responding.
  )
) else (
  call :EnsureDockerEngine
  if errorlevel 1 (
    call :HoldForUser
    exit /b 1
  )
  set "DOCKER_ENGINE_READY=1"
  echo Docker engine is responding.
)
echo.

echo Step 4 of 6 - validating Docker Compose file...
docker compose config >nul
if errorlevel 1 (
  echo.
  echo ERROR: Docker Compose configuration is invalid.
  echo Review docker-compose.yml and the .env files, then retry.
  pause
  exit /b 1
)
echo Docker Compose configuration looks valid.
echo.

if "%DOCKER_ENGINE_READY%"=="1" (
  echo Current container status:
  docker compose ps
) else (
  echo Current container status was not queried because the Docker engine is offline.
)
echo.

if "%CHECK_ONLY%"=="1" (
  echo Check-only mode complete. No containers were started or rebuilt.
  exit /b 0
)

echo Step 5 of 6 - selecting a safe startup action...
call :ProbeServices

if "%REBUILD%"=="0" (
  if "%PROBE_CODE%"=="0" (
    echo Backend and frontend are already ready. Skipping rebuild to preserve the running guard.
    goto services_ready
  )

  if "%PROBE_CODE%"=="2" (
    echo Backend is healthy; starting or rebuilding only the frontend.
    echo Command to run:
    echo   docker compose up -d --build --no-deps frontend
    echo.
    docker compose up -d --build --no-deps frontend
    if errorlevel 1 (
      echo.
      echo ERROR: Docker Compose failed to start the frontend.
      echo The healthy backend was not recreated.
      echo Check logs with: docker compose logs --tail=100 frontend
      call :HoldForUser
      exit /b 1
    )
    goto wait_for_services
  )

  call :BackendContainerIsRunning
  if not errorlevel 1 (
    echo Backend container is running but still becoming healthy.
    echo Waiting without recreating it so the live guard process is not interrupted.
    call :WaitForBackend
    if errorlevel 1 (
      echo.
      echo ERROR: The running backend did not become healthy within 180 seconds.
      echo It was not recreated. Inspect: docker compose logs --tail=100 backend
      echo Use --rebuild only after confirming there is no live broker exposure.
      call :HoldForUser
      exit /b 1
    )

    call :ProbeServices
    if not errorlevel 1 (
      echo Backend and frontend are ready. The running backend was not rebuilt.
      goto services_ready
    )

    echo Backend is now healthy; starting or rebuilding only the frontend.
    echo Command to run:
    echo   docker compose up -d --build --no-deps frontend
    echo.
    docker compose up -d --build --no-deps frontend
    if errorlevel 1 (
      echo.
      echo ERROR: Docker Compose failed to start the frontend.
      echo The healthy backend was not recreated.
      echo Check logs with: docker compose logs --tail=100 frontend
      call :HoldForUser
      exit /b 1
    )
    goto wait_for_services
  )
)

if "%REBUILD%"=="1" (
  echo WARNING: --rebuild can recreate the backend and briefly interrupt its software guard.
  echo Use this option only after confirming there is no live broker exposure.
)
echo Building and starting the full stack.
echo Command to run:
echo   docker compose up -d --build
echo.
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo ERROR: Docker Compose failed to start the stack.
  echo Useful diagnostics:
  echo   docker compose ps
  echo   docker compose logs --tail=100 backend
  echo   docker compose logs --tail=100 frontend
  call :HoldForUser
  exit /b 1
)
echo.

:wait_for_services
echo Step 6 of 6 - waiting for services to become ready...
call :WaitForServices
if not "%READY_CODE%"=="0" (
  echo.
  if "%READY_CODE%"=="2" echo ERROR: Frontend did not become ready within 180 seconds.
  if "%READY_CODE%"=="3" echo ERROR: Backend did not become healthy within 180 seconds.
  if "%READY_CODE%"=="1" echo ERROR: Backend and frontend did not become ready within 180 seconds.
  echo.
  echo Current container status:
  docker compose ps
  echo.
  echo Useful diagnostics:
  echo   docker compose logs --tail=100 backend
  echo   docker compose logs --tail=100 frontend
  echo   docker compose logs --tail=100 mongo
  echo.
  echo The containers were left running so you can inspect and retry safely.
  call :HoldForUser
  exit /b 1
)

:services_ready
echo.
echo Final container status:
docker compose ps
echo.
echo Backend health: ok
echo Frontend: responding
echo.
echo Open these URLs:
echo   Frontend:       http://localhost:3000
echo   Backend health: http://localhost:8001/api/health
echo.
echo Common next steps:
echo   1. Data Warehouse - connect Upstox if the token expired.
echo   2. Data Warehouse - click Check warehouse, then Fill gaps if needed.
echo   3. Backtest Lab - run research only after the warehouse is trusted.
echo.
echo Useful commands:
echo   docker compose ps
echo   docker compose logs -f backend
echo   docker compose logs -f frontend
echo   docker compose stop
echo.

if "%NO_BROWSER%"=="1" goto done
echo Opening AlphaForge in your default browser...
start "" "http://localhost:3000"
if errorlevel 1 (
  echo.
  echo WARNING: AlphaForge is live, but Windows could not open the default browser.
  echo Open this URL manually: http://localhost:3000
  call :HoldForUser
  exit /b 1
)

:done
echo.
echo AlphaForge startup complete.
exit /b 0

:EnsureDockerEngine
docker info >nul 2>nul
if not errorlevel 1 exit /b 0

echo Docker is installed, but the engine is not responding.
set "DOCKER_DESKTOP_EXE=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
echo.
if not exist "%DOCKER_DESKTOP_EXE%" (
  echo ERROR: Docker Desktop could not be started automatically because this file was not found:
  echo   "%DOCKER_DESKTOP_EXE%"
  echo Install or repair Docker Desktop, then run start-app.bat again.
  echo Download: https://www.docker.com/products/docker-desktop/
  exit /b 1
)

echo Starting Docker Desktop automatically...
start "" "%DOCKER_DESKTOP_EXE%"

echo Waiting up to 180 seconds for the Docker engine...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=(Get-Date).AddSeconds(180); while((Get-Date) -lt $d){ docker info *> $null; if($LASTEXITCODE -eq 0){ exit 0 }; Start-Sleep -Seconds 2 }; exit 1"
if errorlevel 1 (
  echo.
  echo ERROR: Docker Desktop startup timed out after 180 seconds.
  echo Open Docker Desktop and review its status or Troubleshoot panel.
  echo If WSL reports an error, run: wsl --status
  echo After Docker reports Engine running, run start-app.bat again.
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo ERROR: Docker Desktop reported ready, but docker info still failed.
  echo Restart Docker Desktop and run start-app.bat again.
  exit /b 1
)
exit /b 0

:CheckEnvHints
findstr /R /C:"^FERNET_KEY=..*" "backend\.env" >nul 2>nul
if errorlevel 1 (
  echo WARNING: backend\.env has no FERNET_KEY value.
  echo Generate one before Upstox OAuth. The exact command is in docs\STARTUP_MANUAL.md.
)
findstr /R /C:"^UPSTOX_CLIENT_ID=..*" "backend\.env" >nul 2>nul
if errorlevel 1 (
  echo INFO: UPSTOX_CLIENT_ID is blank. Broker data will require OAuth setup later.
)
findstr /R /C:"^UPSTOX_CLIENT_SECRET=..*" "backend\.env" >nul 2>nul
if errorlevel 1 (
  echo INFO: UPSTOX_CLIENT_SECRET is blank. Broker data will require OAuth setup later.
)

powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "try{$lines=@(Get-Content -LiteralPath 'backend\.env' -ErrorAction Stop)}catch{exit 2}; $rows=@($lines | Where-Object { $_ -match '^\s*(?:export\s+)?LIVE_AUTOPLACE_ARMED\s*=' }); if($rows.Count -eq 0){exit 1}; $v=($rows[-1] -split '=',2)[1].Trim(); if($v.Length -gt 0 -and (($v[0] -eq [char]34) -or ($v[0] -eq [char]39))){$q=$v[0]; $end=$v.IndexOf($q,1); if($end -lt 1){exit 2}; $v=$v.Substring(1,$end-1)}else{$v=($v -split '\s+#',2)[0].Trim()}; if(@('1','true','yes','on') -contains $v.Trim().ToLowerInvariant()){exit 0}; exit 1"
set "LIVE_ARM_CHECK_CODE=%ERRORLEVEL%"
if "%LIVE_ARM_CHECK_CODE%"=="0" (
  echo.
  echo WARNING: LIVE_AUTOPLACE_ARMED is enabled in backend\.env.
  echo This launcher will not change it. Eligible live entries and guarded exits may transmit.
  exit /b 0
)
if "%LIVE_ARM_CHECK_CODE%"=="1" exit /b 0
echo ERROR: The launcher could not safely parse LIVE_AUTOPLACE_ARMED in backend\.env.
echo Correct that line before starting broker-capable services.
exit /b 1

:ProbeServices
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$b=$false; $f=$false; try{if((Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/health' -TimeoutSec 3).db -eq 'ok'){$b=$true}}catch{}; try{$r=Invoke-WebRequest -Uri 'http://127.0.0.1:3000' -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 400){$f=$true}}catch{}; if($b -and $f){exit 0}; if($b){exit 2}; if($f){exit 3}; exit 1"
set "PROBE_CODE=%ERRORLEVEL%"
exit /b %PROBE_CODE%

:BackendContainerIsRunning
for /f "delims=" %%I in ('docker compose ps --status running -q backend 2^>nul') do exit /b 0
exit /b 1

:WaitForBackend
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$d=(Get-Date).AddSeconds(180); while((Get-Date) -lt $d){ try{if((Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/health' -TimeoutSec 3).db -eq 'ok'){exit 0}}catch{}; Start-Sleep -Milliseconds 500}; exit 1"
exit /b %ERRORLEVEL%

:WaitForServices
rem Polls BOTH services from ONE PowerShell process and sets READY_CODE:
rem   0 = both ready, 2 = backend only, 3 = frontend only, 1 = neither.
rem
rem The URLs use 127.0.0.1, NOT localhost. docker-compose.yml publishes the
rem ports IPv4-only (127.0.0.1:8001, 127.0.0.1:3000) while Windows resolves
rem localhost to ::1 first. Nothing listens on ::1, and that dead IPv6 attempt
rem stalls ~2s before .NET falls back to IPv4 -- which blew the old -TimeoutSec 2
rem on EVERY poll, so the check could never pass. Both 60-iteration loops then
rem ran to exhaustion (~8.5 min) and reported "not ready" on a healthy stack.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=(Get-Date).AddSeconds(180); $b=$false; $f=$false; while((Get-Date) -lt $d){ if(-not $b){ try{ if((Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/health' -TimeoutSec 3).db -eq 'ok'){ $b=$true; Write-Host '  Backend ready.' } }catch{} }; if(-not $f){ try{ $r=Invoke-WebRequest -Uri 'http://127.0.0.1:3000' -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 400){ $f=$true; Write-Host '  Frontend ready.' } }catch{} }; if($b -and $f){ exit 0 }; Start-Sleep -Milliseconds 500 }; if($b){ exit 2 }; if($f){ exit 3 }; exit 1"
set "READY_CODE=%ERRORLEVEL%"
exit /b 0

:HoldForUser
echo.
echo Startup stopped. Review the message above, then press any key to close this window.
pause >nul
exit /b 0

:help
echo AlphaForge startup assistant
echo.
echo Usage:
echo   start-app.bat
echo   start-app.bat --check-only
echo   start-app.bat --no-browser
echo   start-app.bat --rebuild
echo.
echo Options:
echo   --check-only   Check prerequisites without starting Docker Desktop or containers.
echo   --no-browser   Start services without opening http://localhost:3000.
echo   --rebuild      Force a full rebuild; use only with no live broker exposure.
echo   --help         Show this help.
exit /b 0
