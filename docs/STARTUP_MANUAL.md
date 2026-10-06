# AlphaForge Setup & Startup Manual

Updated: 2026-09-30

The single setup document: first-time install, the daily Windows start, the daily broker
logins, upgrades, backup/restore and troubleshooting. Written for the operator and for the next
developer/agent. The test suite and build gate live in [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md).

## At a glance

| Thing | Value |
|---|---|
| Frontend | `http://localhost:3000`. Browse via `localhost`: `CORS_ORIGINS` allows only `http://localhost:3000`, so `127.0.0.1:3000` gets CORS-blocked API calls |
| Backend API | `http://localhost:8001/api` (FastAPI). Health: `http://127.0.0.1:8001/api/health` → `{"db":"ok"}` (host probes dial 127.0.0.1: `localhost` → `::1` stalls ~2 s; the browser still uses `localhost:3000`) |
| Containers | `alphaforge_mongo` (`mongo:7`), `alphaforge_backend` (Python 3.11 image, uvicorn :8001), `alphaforge_frontend` (nginx :3000) |
| Port binding | Every port is published on **127.0.0.1 only** (`docker-compose.yml`). MongoDB has **no authentication**; loopback is its only protection |
| Data | Docker volume `mongo_data` (the whole warehouse, journals, deployments, broker tokens) |
| Code in the image | Backend code is **baked into the image**. Only `backend/app/strategies/plugins` is bind-mounted. A restart runs the old code; only a rebuild picks up changes |
| Host probes | Scripts and health checks from the host should dial `127.0.0.1`. `localhost` resolves to `::1` first on Windows and stalls ~2 s before falling back |
| Operator shell | Windows PowerShell 5.1. It has no `&&`. Run one command per line, or use `A; if ($?) { B }` |

Do not delete the Docker volume unless you intentionally want to wipe the local warehouse.

## First-time install (Windows, Docker)

Prerequisites: Docker Desktop, Git, and a host Python with the `cryptography` package for the
key-generation one-liner. The repo's root `.venv` (Python 3.12) has it.

1. Clone:

   ```powershell
   git clone https://github.com/hrninfomeet-wq/Emergent-AlphaForge.git
   cd Emergent-AlphaForge
   ```

2. Create the env files. `start-app.bat` also creates them from the templates if they are missing.
   Use `cp` instead of `copy` on Mac/Linux.

   ```powershell
   copy backend\.env.example backend\.env
   copy frontend\.env.example frontend\.env
   ```

3. Generate a `FERNET_KEY` and paste it into `backend\.env` (see *Manual Docker Startup* below).
   It encrypts the broker tokens stored in MongoDB.
4. Fill in `backend\.env` in a local editor. `backend/.env.example` groups every key by purpose.

   | Purpose | Keys | Needed for |
   |---|---|---|
   | Token encryption | `FERNET_KEY` | Any broker login |
   | Market data (Upstox) | `UPSTOX_CLIENT_ID`, `UPSTOX_CLIENT_SECRET` (app registered at https://developer.upstox.com/) | Warehouse ingest, live ticks, paper premiums |
   | AI authoring | `GEMINI_API_KEY` and/or `ANTHROPIC_API_KEY` | Strategy Library AI wizard (see *AI authoring keys*) |
   | Live execution (Flattrade) | `FLATTRADE_API_KEY`, `FLATTRADE_API_SECRET`, optionally `FLATTRADE_USER_ID`, `FLATTRADE_PRIMARY_IP` / `FLATTRADE_SECONDARY_IP` | Only when you trade live |
   | Entry master switch | `LIVE_AUTOPLACE_ARMED=0` | Leave at 0 until you are actively trading live. At 0, live-mode deployments dry-run their entries |
   | PC-down backstop | `LIVE_BROKER_OCO_ENABLED=0` | Off since 2026-09-03 (the OCO stop leg fired at placement). With it off there is **no PC-down net**. Read the template comment before changing it |

5. **Machine-specific mount.** `docker-compose.yml` binds `C:/Users/haroo/.flattrade` to
   `/host-flattrade`. That is the Flattrade MCP session directory that AlphaForge syncs its token
   into. On another machine, change that host path before the first start.
6. Start: double-click `start-app.bat` (next section), or run `docker compose up -d --build`.
   On Mac/Linux, run `./start.sh`. It copies missing env files, runs `docker compose up -d --build`,
   waits about 60 s for the health endpoint and then opens the browser.
7. Verify (from PowerShell; `curl` works the same on Mac/Linux):

   | Check | Expect |
   |---|---|
   | `Invoke-RestMethod http://localhost:8001/api/health` | `{"db":"ok"}` |
   | `GET /api/strategies` | every plugin listed. A plugin that failed to load shows `is_loaded: false` plus `error` |
   | `GET /api/strategies/author/providers` | at least one provider with `configured: true` (only if you set an AI key) |
   | `GET /api/upstox/status` | Upstox token state (after the Upstox login) |
   | `GET /api/live-candles/status` | live candle roller state (runs during market hours) |

8. First steps in the UI: Data Warehouse → connect Upstox → **Check warehouse** / **Fill gaps**
   → Backtest Lab → Optimizer → save a Preset → deploy it as **Signal only** or **Paper** first.
   See [`USER_MANUAL.md`](USER_MANUAL.md).

## Recommended Windows Startup

Use the detailed launcher:

```bat
start-app.bat
```

You can double-click it from File Explorer or run it from Command Prompt/PowerShell inside the
project root. PowerShell does not run programs from the current folder by name, so type
`.\start-app.bat` there.

(The older `start.bat` wrapper was removed. `start-app.bat` is the only Windows launcher now.)

### What The Launcher Checks

1. Confirms it is running from the project root.
2. Confirms Docker and Docker Compose are installed.
3. Confirms the Docker Desktop engine is running. If needed, it starts Docker Desktop automatically and waits up to 180 seconds for the engine.
4. Creates `backend\.env` and `frontend\.env` from examples if missing.
5. Warns if `FERNET_KEY`, `UPSTOX_CLIENT_ID`, or `UPSTOX_CLIENT_SECRET` are blank. It never prints secret values. It also warns when `LIVE_AUTOPLACE_ARMED` is already enabled but never changes that setting.
6. Validates `docker-compose.yml`.
7. Reuses an already-healthy backend and frontend without rebuilding them. If the backend is healthy but the frontend is not, it rebuilds only the frontend. When the backend is not running, it runs `docker compose up -d --build` automatically. The default success path requires no confirmation on the success path.
8. Waits for the backend health response and a successful frontend HTTP response (probing `127.0.0.1`).
9. Prints useful URLs and log commands, then opens the browser only after both services are ready.

If Docker cannot start, Compose fails, or either service misses the readiness deadline, the launcher prints targeted recovery commands, does not open the browser, and exits with a non-zero status. On a double-clicked error path, it pauses so the warning remains visible. Containers that did start are left running for inspection; the launcher never deletes the `mongo_data` volume.

Starting the backend activates AlphaForge's existing recovery and position-guard behavior. Existing guarded live positions can still transmit safety exits. The launcher does not log in to a broker, change broker sessions, arm or disarm `LIVE_AUTOPLACE_ARMED`, or bypass any live-readiness gate.

> **Step 7 means the launcher does not deploy new code.** If the backend is already running, a
> double-click reuses it even after a `git pull`. See *Upgrade after a pull or code change*.

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

## Daily broker logins

Both brokers need a fresh login every trading day. Log in from AlphaForge's own pages.

### Upstox (market data)

- Log in from **Data Warehouse** (the *Upstox Broker Data* panel at the top, **Connect Upstox** /
  **Reconnect**), or the **Upstox** chip in the Live Broker command bar.
  The flow redirects through `GET /api/upstox/auth/start` and returns to
  `/api/upstox/auth/callback`, which must match the redirect URL registered on your Upstox app.
- The token is stored encrypted in MongoDB. A countdown in the top bar and in the Upstox panel
  shows when it expires. Redo the login when fetches fail with auth errors.
- During market hours (09:15–15:30 IST on trading days), the backend's live-feed supervisor starts
  the Upstox stream and the 1-minute candle roller by itself once the token is valid. Logging in
  late needs no manual restart.

### Flattrade (live execution; only when trading live)

- Open **Live Broker** (`/live-trading`), click the **Flattrade** chip in the command bar, then
  **Login to Flattrade**. `GET /api/flattrade/auth/start` returns the Flattrade login URL. It
  answers 400 until `FLATTRADE_API_KEY` and `FLATTRADE_API_SECRET` are set.
- Flattrade redirects to `FLATTRADE_REDIRECT_URI`. The default is
  `http://127.0.0.1:8001/api/flattrade/auth/callback`, and it must equal the redirect URL
  registered on the API key. The callback does three things:
  1. saves the token;
  2. mirrors it into the Flattrade MCP session file;
  3. starts live recovery (re-attaches and reconciles positions carried over a restart).

  It then sends the browser to `FRONTEND_POST_AUTH_URL?flattrade_connected=1`. That is the
  same variable Upstox uses, and the template sets it to `/warehouse`. Go back to Live Broker and
  confirm the Flattrade chip shows ✓.
- Flattrade clears tokens around **06:00 IST** daily. A token issued before that time counts as
  expired. While it is expired, nothing reaches the broker: no entry, no guard exit, no kill
  switch. The Live Broker page shows a red "Flattrade session expired" banner.
- Orders go out only from the static IP registered on the API key. Flattrade accepts
  limit/SL-limit orders only.
- **Never use the Flattrade MCP's `login`/`logout` tools.** AlphaForge is the sole OAuth owner of
  the shared API key, and a second login invalidates AlphaForge's token. If the MCP is wedged on
  a stale session, run
  `.venv\Scripts\python.exe backend\scripts\resync_mcp_session.py --clean` after a valid
  AlphaForge login. See [`flattrade-mcp-integration.md`](flattrade-mcp-integration.md).

### Pre-open readiness check (08:45 IST)

The backend checks readiness at 08:45 IST every day and stores a verdict. The verdict is a
report only: it never blocks or trades.

- **Blockers:** Upstox not connected or its token expired. Flattrade not connected or its
  session expired, counted only while at least one ACTIVE deployment is in live mode.
- **Warnings:** warehouse actions pending.

The Live Broker alert rail shows the verdict when it has a blocker or warning (`GET /api/live-broker/preopen-readiness`).

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
7. If any deployment is in live mode: log in to Flattrade on Live Broker and read the execution
   strip and the alert rail before 09:15.

The warehouse also catches up automatically on backend startup, on Upstox OAuth connect, and daily at 18:00 IST when Upstox is connected.

## Upgrade after a pull or code change

```powershell
git pull
docker compose up -d --build
```

- `docker compose restart backend`, `docker compose start` and a double-click of `start-app.bat`
  against a healthy stack all keep running the **old image**. Only `--build` (or
  `start-app.bat --rebuild`) deploys changed backend code. The one exception is a plugin under
  `backend/app/strategies/plugins`, which is bind-mounted.
- Recreating the backend briefly stops the software exit guard. Do it with no live broker
  exposure.
- Check that the container really has the new code. From PowerShell:
  `docker exec alphaforge_backend grep -c <symbol> /app/app/<file>.py`. From Git Bash, prefix
  the same command with `MSYS_NO_PATHCONV=1`.
- The frontend bundle is baked in the same way. After a rebuild, hard-refresh the browser
  (Ctrl+Shift+R) if a page looks unchanged.

After editing `backend\.env`, recreate the backend with `docker compose up -d backend`. A
`restart` keeps the environment the container was created with (`backend/.env.example` says the
same: set the value, then recreate the container).

## Stop, Restart, And Logs

Stop containers without deleting data:

```powershell
docker compose stop
```

Start existing containers again:

```powershell
docker compose start
```

Restart one service (same image, same environment):

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

## Backup and restore

The warehouse, journals, deployments and encrypted broker tokens all live in the `mongo_data`
volume. Back it up before anything destructive.

```powershell
# Backup
docker exec -t alphaforge_mongo mongodump --archive=/tmp/backup.gz --gzip
docker cp alphaforge_mongo:/tmp/backup.gz ./alphaforge_backup_YYYYMMDD.gz

# Restore
docker cp ./alphaforge_backup_YYYYMMDD.gz alphaforge_mongo:/tmp/backup.gz
docker exec -t alphaforge_mongo mongorestore --archive=/tmp/backup.gz --gzip
```

A restore needs the same `FERNET_KEY`, or the stored broker tokens cannot be decrypted. Log in
again in that case.

## AI authoring keys

The Strategy Library AI wizard (Check Feasibility, AI Generate) needs at least one provider key in
`backend\.env`: `GEMINI_API_KEY` (free key at https://aistudio.google.com/apikey) and/or
`ANTHROPIC_API_KEY`. When both are set, Anthropic is used unless `AI_PROVIDER` is set to
`anthropic` or `gemini`. Model overrides are optional; the defaults live in
`backend/app/ai/llm_client.py`. Recreate the backend after editing, then check
`GET /api/strategies/author/providers` for `configured: true`.

## Native setup (no Docker)

Docker is the supported path; the launcher, the health probes and this manual assume it. A native
run needs the same versions the images use: Python 3.11, Node 20 with Yarn 1.22, MongoDB 7.

```powershell
# Backend (from backend/)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
#   in backend\.env: MONGO_URL=mongodb://127.0.0.1:27017   (compose overrides it to mongodb://mongo:27017)
uvicorn server:app --host 127.0.0.1 --port 8001 --reload

# Frontend (from frontend/, second terminal)
yarn install
yarn start        # craco dev server on :3000; frontend\.env needs REACT_APP_BACKEND_URL=http://localhost:8001
```

Bind the API to `127.0.0.1`: it has no authentication layer.

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

Also browse `http://localhost:3000`, not `127.0.0.1:3000`, because CORS allows only the
`localhost` origin.

Do not print `backend\.env` if it contains broker credentials.

### Upstox OAuth Fails

Check only the field names, not secret values:

- `FERNET_KEY` is populated.
- `UPSTOX_CLIENT_ID` is populated.
- `UPSTOX_CLIENT_SECRET` is populated.
- `UPSTOX_REDIRECT_URI=http://localhost:8001/api/upstox/auth/callback`
- The same redirect URL is configured in the Upstox developer app.

Then recreate the backend so it reads the edited file:

```powershell
docker compose up -d backend
```

Reconnect Upstox from Data Warehouse.

### Flattrade Login Fails

- A 400 saying "Flattrade credentials not configured" means `FLATTRADE_API_KEY` /
  `FLATTRADE_API_SECRET` are blank. Set them and recreate the backend.
- If you land on a page with `?flattrade_error=...`, read that error text. Common causes are a
  redirect URL that differs from the one registered on the key, and a stale session that needs
  a fresh login after 06:00 IST.
- Orders refused while the login works usually mean the machine is not sending from the
  registered static IP.

### Ports Are Already In Use

Default ports:

- Frontend host port: `3000`
- Backend host port: `8001`
- Mongo host port: `27017`

Check listeners:

```powershell
netstat -ano | findstr ":3000 :8001 :27017"
```

If another app owns one of those ports, close that app or change the host-port mapping in
`docker-compose.yml`. Keep the loopback prefix, for example `"127.0.0.1:3001:3000"`, never a bare
`"3001:3000"`. The API and MongoDB have no authentication, so they must not be exposed to the LAN.

### Code Change Does Not Show Up

Rebuild with `docker compose up -d --build`; restart is not rebuild. See *Upgrade after a pull or
code change*.

### No Strategies Loaded

Check `docker compose logs backend` for `Strategy registered: ...` lines. A plugin that failed to
import is listed by `GET /api/strategies` with `is_loaded: false` and its `error`.

### Same-Day Candles Missing

Upstox's historical API returns nothing for the current day; that is expected. Today's bars come
from the live candle roller, which the feed supervisor starts during market hours whenever the
Upstox token is valid. `GET /api/live-candles/status` shows it; `POST /api/live-candles/start`
starts it by hand.

### Upstox 400 "Invalid date range" on an index ingest

Leave **Chunk** on *Auto* in the Upstox panel. Auto requests index candles in 7-day calls
(`app/chunking.py`); a larger manual chunk (it accepts up to 30 days) can hit the Upstox
Feb→Mar boundary error. After a failure, retry with a manual chunk of 1–3 days.

### Deployment Errors Seen Right After Start

| Symptom | Meaning / fix |
|---|---|
| Deployment auto-paused with `strategy_source_drift` | The plugin file changed since the deployment was pinned. Use **Re-pin & resume** (`POST /api/deployments/{id}/repin-source`) if the edit was intentional, or create a new deployment |
| Deployment create returns 400 `acknowledgment_required` | The source has quality warnings. Tick the acknowledgment box, or send `acknowledged_warnings=true` |

## Verification Commands For Development

The full gates are in [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) §B, *The test pyramid*. There
is no CI; the local suite is the only evidence. In short:

```powershell
.venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider
cd frontend
$env:CI = "true"
npx --no-install craco build
cd ..
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://localhost:8001/api/health
```

- **Host test environment.** Use the repo's root `.venv` (Python 3.12 with pymongo, motor,
  pandas and pytest). Under a bare Python without those packages, many test files fail to collect
  with "No module named 'pymongo'", or the motor-dependent tests skip. That is an environment gap,
  not a code failure. `node` must be on `PATH`, or the frontend-logic tests skip.
- **Frontend build.** `CI=true` turns every ESLint warning into a build failure. A warning is a
  regression.
- **Expected.** The suite passes, the frontend build completes, the Docker services are up, and
  the backend health returns `{ "db": "ok" }`. HANDOFF §2 carries the current test count.
