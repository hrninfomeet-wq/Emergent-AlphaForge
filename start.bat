@echo off
REM AlphaForge launcher (Windows)
cd /d "%~dp0"

if not exist backend\.env (
  copy backend\.env.example backend\.env >nul
)
if not exist frontend\.env (
  copy frontend\.env.example frontend\.env >nul
)

echo ^>^> Building ^& starting AlphaForge (Docker Compose)...
docker compose up -d --build
if errorlevel 1 (
  echo Failed to start Docker Compose. Is Docker Desktop running?
  pause
  exit /b 1
)

echo.
echo Waiting for services to come up...
set COUNT=0
:wait
set /a COUNT+=1
if %COUNT% GTR 30 goto ready
curl -fsS http://localhost:8001/api/health >nul 2>&1
if errorlevel 1 (
  timeout /t 2 /nobreak >nul
  goto wait
)

:ready
echo Backend ready.
echo.
echo   Frontend:  http://localhost:3000
echo   Backend:   http://localhost:8001/api/health
echo.
start "" http://localhost:3000

echo Stop with:   docker compose stop
echo Logs with:   docker compose logs -f backend
echo.
pause
