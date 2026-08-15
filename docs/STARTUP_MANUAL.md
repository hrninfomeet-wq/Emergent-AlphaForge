# AlphaForge Startup Manual

Updated: 2026-08-16

This manual explains how to start AlphaForge on a local PC, either with the one-click Windows launcher or with manual Docker commands. It is written for day-to-day use and for future AI/developer handoff.

AlphaForge is local-first:

- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8001/api`
- Backend health: `http://localhost:8001/api/health`
- MongoDB container: `alphaforge_mongo`
- MongoDB data volume: `mongo_data`

Do not delete the Docker volume unless you intentionally want to wipe the local warehouse.

## Recommended Windows Startup

Use the detailed launcher:

```bat
start-app.bat
```

You can double-click it from File Explorer or run it from Command Prompt/PowerShell inside the project root.

(The older `start.bat` wrapper was removed — `start-app.bat` is the only Windows launcher now.)

### What The Launcher Checks

1. Confirms it is running from the project root.
2. Confirms Docker and Docker Compose are installed.
3. Confirms the Docker Desktop engine is running. If needed, it starts Docker Desktop automatically and waits up to 180 seconds for the engine.
4. Creates `backend\.env` and `frontend\.env` from examples if missing.
5. Warns if `FERNET_KEY`, `UPSTOX_CLIENT_ID`, or `UPSTOX_CLIENT_SECRET` are blank. It never prints secret values. It also warns when `LIVE_AUTOPLACE_ARMED` is already enabled but never changes that setting.
6. Validates `docker-compose.yml`.
7. Reuses an already-healthy backend and frontend without rebuilding them. If the backend is healthy but the frontend is not, it rebuilds only the frontend. When the backend is not running, it runs `docker compose up -d --build` automatically. The default success path requires no confirmation on the success path.
8. Waits for the backend health response and a successful frontend HTTP response.
9. Prints useful URLs and log commands, then opens the browser only after both services are ready.

If Docker cannot start, Compose fails, or either service misses the readiness deadline, the launcher prints targeted recovery commands, does not open the browser, and exits with a non-zero status. On a double-clicked error path, it pauses so the warning remains visible. Containers that did start are left running for inspection; the launcher never deletes the `mongo_data` volume.

Starting the backend activates AlphaForge's existing recovery and position-guard behavior. Existing guarded live positions can still transmit safety exits. The launcher does not log in to a broker, change broker sessions, arm or disarm `LIVE_AUTOPLACE_ARMED`, or bypass any live-readiness gate.

### Launcher Options

```bat
start-app.bat --check-only
```

Runs prerequisite and environment checks without starting Docker Desktop and without starting or rebuilding containers. If the Docker engine is already running, it also prints current container status.

```bat
start-app.bat --no-browser
```

Starts the stack but does not open the frontend URL.

```bat
start-app.bat --rebuild
```

Forces a full rebuild even when the backend is already running. This can briefly interrupt AlphaForge's software position guard. Use it only after confirming there is no live broker exposure. Ordinary double-click startup waits for a running backend to become healthy without recreating it; if the deadline expires, it prints diagnostics and exits.

```bat
start-app.bat --help
```

Shows usage.

## Manual Docker Startup

Open PowerShell or Command Prompt:

```powershell
cd "<path-to-your-Emergent-AlphaForge-checkout>"
```

Check Docker:

```powershell
docker --version
docker compose version
docker info
```

If `docker info` fails, start Docker Desktop and wait until it says the engine is running.

Create local env files if missing:

```powershell
copy backend\.env.example backend\.env
copy frontend\.env.example frontend\.env
```

Edit `backend\.env` only in a local editor. Do not paste credentials into terminal output or chat. For Upstox OAuth and encrypted token storage, these values matter:

```text
FERNET_KEY=
UPSTOX_CLIENT_ID=
UPSTOX_CLIENT_SECRET=
UPSTOX_REDIRECT_URI=http://localhost:8001/api/upstox/auth/callback
FRONTEND_POST_AUTH_URL=http://localhost:3000/warehouse
```

Generate `FERNET_KEY` locally:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the generated value into `backend\.env`. Keep it stable. Changing it later can make already encrypted broker tokens unreadable.

Build and start:

```powershell
docker compose up -d --build
```

Check container status:

```powershell
docker compose ps
```

Expected services:

- `alphaforge_mongo` healthy
- `alphaforge_backend` healthy
- `alphaforge_frontend` running

Check backend health:

```powershell
Invoke-RestMethod http://localhost:8001/api/health
```

Expected:

```json
{"db":"ok"}
```

Open:

```text
http://localhost:3000
```

## After The App Opens

1. Open Data Warehouse.
2. Check the Upstox token badge. If expired, reconnect Upstox.
3. Click `Check warehouse` in Data Hygiene.
4. If actions are shown, click `Fill gaps`.
5. Run Data Trust Audit for the date range before serious backtesting.
6. Use Backtest Lab only after index and option coverage are trusted.

The warehouse also catches up automatically on backend startup, on Upstox OAuth connect, and daily at 18:00 IST when Upstox is connected.

## Stop, Restart, And Logs

Stop containers without deleting data:

```powershell
docker compose stop
```

Start existing containers again:

```powershell
docker compose start
```

Restart one service:

```powershell
docker compose restart backend
docker compose restart frontend
```

Rebuild after code changes:

```powershell
docker compose up -d --build
```

Read logs:

```powershell
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f mongo
```

Stop and remove containers while keeping the MongoDB data volume:

```powershell
docker compose down
```

Dangerous data wipe:

```powershell
docker compose down -v
```

Only run `docker compose down -v` if you intentionally want to delete the local MongoDB warehouse volume.

## Common Startup Problems

### Docker Desktop Is Not Running

Symptom:

```text
Cannot connect to the Docker daemon
```

The launcher normally starts Docker Desktop automatically and waits up to 180 seconds. If that wait fails:

1. Open Docker Desktop and review its status or Troubleshoot panel.
2. If Docker reports a WSL problem, run `wsl --status` in PowerShell.
3. Wait until Docker reports that the engine is running, then run `start-app.bat` again.

### Backend Health Is Not OK

Check:

```powershell
docker compose ps
docker compose logs --tail=120 backend
```

Common causes:

- MongoDB is still starting.
- `backend\.env` has invalid values.
- Port `8001` is already used by another process.

### Frontend Shows Network Error

Check:

```powershell
Invoke-RestMethod http://localhost:8001/api/health
type frontend\.env
```

`frontend\.env` should include:

```text
REACT_APP_BACKEND_URL=http://localhost:8001
```

Do not print `backend\.env` if it contains broker credentials.

### Upstox OAuth Fails

Check only the field names, not secret values:

- `FERNET_KEY` is populated.
- `UPSTOX_CLIENT_ID` is populated.
- `UPSTOX_CLIENT_SECRET` is populated.
- `UPSTOX_REDIRECT_URI=http://localhost:8001/api/upstox/auth/callback`
- The same redirect URL is configured in the Upstox developer app.

Then restart backend:

```powershell
docker compose restart backend
```

Reconnect Upstox from Data Warehouse.

### Ports Are Already In Use

Default ports:

- Frontend host port: `3000`
- Backend host port: `8001`
- Mongo host port: `27017`

Check listeners:

```powershell
netstat -ano | findstr ":3000 :8001 :27017"
```

If another app owns one of those ports, close that app or change the host-port mapping in `docker-compose.yml`.

## Verification Commands For Development

Run these before claiming the app baseline is green:

```powershell
.venv\Scripts\python.exe -m pytest tests -q   # needs pymongo/motor/pytest — see docs/LOCAL_SETUP.md
cd frontend
npm run build
cd ..
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://localhost:8001/api/health
```

Expected current baseline after this startup-manual slice:

- Backend tests pass.
- Frontend production build completes.
- Docker services are up.
- Backend health returns `{ "db": "ok" }`.

Frontend build may print existing React hook dependency warnings. Treat new warnings as regressions, but the current known warnings do not block startup.
