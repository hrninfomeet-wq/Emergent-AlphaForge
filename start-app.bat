@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "CHECK_ONLY=0"
set "NO_BROWSER=0"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--help" goto help
if /I "%~1"=="/?" goto help
if /I "%~1"=="--check-only" (
  set "CHECK_ONLY=1"
  shift
  goto parse_args
)
if /I "%~1"=="--no-browser" (
  set "NO_BROWSER=1"
  shift
  goto parse_args
)
echo Unknown option: %~1
echo Run start-app.bat --help for usage.
exit /b 2

:args_done
echo.
echo ============================================================
echo  AlphaForge Trading Lab - local startup assistant
echo ============================================================
echo.
echo Project folder:
echo   %CD%
echo.
echo This launcher starts the local Docker stack:
echo   - MongoDB container: alphaforge_mongo
echo   - FastAPI backend:   http://localhost:8001
echo   - React frontend:    http://localhost:3000
echo.
echo Safety notes:
echo   - This script does not print or upload broker credentials.
echo   - It does not clear MongoDB data or place broker orders.
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

echo Step 1 of 6 - checking Docker command...
where docker >nul 2>nul
if errorlevel 1 (
  echo.
  echo ERROR: The docker command was not found.
  echo Install Docker Desktop for Windows, start it, then run this file again.
  echo Download: https://www.docker.com/products/docker-desktop/
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

echo Docker command found.
echo.

echo Step 2 of 6 - checking Docker Desktop engine...
call :EnsureDockerEngine
if errorlevel 1 exit /b 1
echo Docker engine is responding.
echo.

echo Step 3 of 6 - checking local environment files...
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
echo.

if "%BACKEND_ENV_CREATED%"=="1" (
  echo backend\.env was just created. Broker data features need these values later:
  echo   FERNET_KEY, UPSTOX_CLIENT_ID, UPSTOX_CLIENT_SECRET, UPSTOX_REDIRECT_URI
  echo The app can still start without Upstox, but OAuth and live data will be disabled.
  echo.
  set /p "OPEN_ENV=Open backend\.env in Notepad now? [Y/n] "
  if /I not "!OPEN_ENV!"=="N" (
    start /wait notepad "backend\.env"
  )
)

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

echo Current container status:
docker compose ps
echo.

if "%CHECK_ONLY%"=="1" (
  echo Check-only mode complete. No containers were started or rebuilt.
  exit /b 0
)

echo Step 5 of 6 - build and start containers.
echo Command to run:
echo   docker compose up -d --build
echo.
set /p "GO=Proceed with build/start now? [Y/n] "
if /I "%GO%"=="N" (
  echo Startup cancelled by user.
  exit /b 0
)

docker compose up -d --build
if errorlevel 1 (
  echo.
  echo ERROR: Docker Compose failed to start the stack.
  echo Useful diagnostics:
  echo   docker compose ps
  echo   docker compose logs --tail=100 backend
  echo   docker compose logs --tail=100 frontend
  pause
  exit /b 1
)
echo.

echo Step 6 of 6 - waiting for services to become ready...
call :WaitForBackend
set "BACKEND_READY=%ERRORLEVEL%"
call :WaitForFrontend
set "FRONTEND_READY=%ERRORLEVEL%"

echo.
echo Final container status:
docker compose ps
echo.

if "%BACKEND_READY%"=="0" (
  echo Backend health: ok
) else (
  echo Backend health: not ready yet.
  echo Check logs with: docker compose logs -f backend
)

if "%FRONTEND_READY%"=="0" (
  echo Frontend: responding
) else (
  echo Frontend: not responding yet.
  echo Check logs with: docker compose logs -f frontend
)

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
set /p "OPEN_APP=Open AlphaForge in your browser now? [Y/n] "
if /I not "%OPEN_APP%"=="N" (
  start "" "http://localhost:3000"
)

:done
echo.
echo Startup assistant finished.
pause
exit /b 0

:EnsureDockerEngine
docker info >nul 2>nul
if not errorlevel 1 exit /b 0

echo Docker is installed, but the engine is not responding.
echo Start Docker Desktop and wait until it says it is running.
echo.
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
  set /p "START_DOCKER=Start Docker Desktop now? [Y/n] "
  if /I not "!START_DOCKER!"=="N" (
    start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
  )
)

:docker_wait
echo.
set /p "RETRY=Press Enter after Docker Desktop is running, or type Q to quit: "
if /I "!RETRY!"=="Q" exit /b 1
docker info >nul 2>nul
if errorlevel 1 (
  echo Docker engine is still not ready.
  goto docker_wait
)
exit /b 0

:CheckEnvHints
set "FERNET_KEY_VALUE="
set "UPSTOX_CLIENT_ID_VALUE="
set "UPSTOX_CLIENT_SECRET_VALUE="
for /f "tokens=1,* delims==" %%A in ('findstr /B "FERNET_KEY=" "backend\.env" 2^>nul') do set "FERNET_KEY_VALUE=%%B"
for /f "tokens=1,* delims==" %%A in ('findstr /B "UPSTOX_CLIENT_ID=" "backend\.env" 2^>nul') do set "UPSTOX_CLIENT_ID_VALUE=%%B"
for /f "tokens=1,* delims==" %%A in ('findstr /B "UPSTOX_CLIENT_SECRET=" "backend\.env" 2^>nul') do set "UPSTOX_CLIENT_SECRET_VALUE=%%B"

if not defined FERNET_KEY_VALUE (
  echo WARNING: backend\.env has no FERNET_KEY value.
  echo Generate one before Upstox OAuth. The exact command is in docs\STARTUP_MANUAL.md.
)
if not defined UPSTOX_CLIENT_ID_VALUE (
  echo INFO: UPSTOX_CLIENT_ID is blank. Broker data will require OAuth setup later.
)
if not defined UPSTOX_CLIENT_SECRET_VALUE (
  echo INFO: UPSTOX_CLIENT_SECRET is blank. Broker data will require OAuth setup later.
)
exit /b 0

:WaitForBackend
for /L %%I in (1,1,60) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-RestMethod -Uri 'http://localhost:8001/api/health' -TimeoutSec 2; if ($r.db -eq 'ok') { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
  if not errorlevel 1 exit /b 0
  timeout /t 2 /nobreak >nul
)
exit /b 1

:WaitForFrontend
for /L %%I in (1,1,60) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:3000' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
  if not errorlevel 1 exit /b 0
  timeout /t 2 /nobreak >nul
)
exit /b 1

:help
echo AlphaForge startup assistant
echo.
echo Usage:
echo   start-app.bat
echo   start-app.bat --check-only
echo   start-app.bat --no-browser
echo.
echo Options:
echo   --check-only   Run prerequisite checks and exit before starting Docker.
echo   --no-browser   Start services without opening http://localhost:3000.
echo   --help         Show this help.
exit /b 0
