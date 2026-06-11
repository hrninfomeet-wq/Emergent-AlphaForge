# Local Setup

Updated: 2026-05-29

Two options: **Docker Compose (recommended)** or **Native (Python + Node + local MongoDB)**.

## Option A — Docker Compose (Recommended)

### Prerequisites

- **Docker Desktop** (Windows / Mac) or **Docker Engine + Compose** (Linux)
- Verify: `docker --version` and `docker compose version`

### Steps

```bash
# 1. Clone the repo
git clone https://github.com/hrninfomeet-wq/Emergent-AlphaForge.git
cd Emergent-AlphaForge

# 2. Copy env templates
copy backend\.env.example backend\.env       # Windows cmd
# or: cp backend/.env.example backend/.env   # Mac/Linux
copy frontend\.env.example frontend\.env

# 3. Generate a stable FERNET_KEY for backend/.env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 4. Edit backend/.env and add Upstox credentials (optional, but required for live data):
#    UPSTOX_CLIENT_ID=...
#    UPSTOX_CLIENT_SECRET=...
#    UPSTOX_REDIRECT_URI=http://localhost:8001/api/upstox/auth/callback
#    FRONTEND_POST_AUTH_URL=http://localhost:3000/warehouse

# 5. Launch
docker compose up -d --build

# 6. Wait ~30 seconds, then open
# Frontend:  http://localhost:3000
# Backend:   http://localhost:8001/api/health
# MongoDB:   localhost:27017 (no auth in dev)
```

### Stop / Restart / Logs

```bash
docker compose stop                  # stop containers
docker compose start                 # start them back
docker compose restart backend       # restart one
docker compose down                  # stop + remove containers (data persists)
docker compose down -v               # ALSO delete the MongoDB volume (wipes data!)

docker compose logs -f backend       # tail backend logs
docker compose logs -f frontend
docker compose logs -f mongo
```

### One-Command Launchers

- Windows: double-click `start-app.bat` (recommended) or `start.bat` (compatibility wrapper)
- Mac/Linux: `./start.sh`

The Windows launcher checks Docker Desktop, validates env files without printing secrets, starts the stack sequentially, waits for health, and then opens the browser. See `docs/STARTUP_MANUAL.md`.

### Upgrade

```bash
git pull
docker compose up -d --build
```

---

## Option B — Native (No Docker)

### Prerequisites

- Python 3.11+
- Node.js 18+ + Yarn
- MongoDB 6+
- Git

### Steps

```bash
git clone https://github.com/hrninfomeet-wq/Emergent-AlphaForge.git
cd Emergent-AlphaForge

# Backend
cd backend
python -m venv .venv
.venv\Scripts\activate                  # Windows
# source .venv/bin/activate              # Mac/Linux
pip install -r requirements.txt
copy .env.example .env

# Frontend
cd ..\frontend
yarn install
copy .env.example .env

# Make sure MongoDB is running
# Linux: sudo systemctl start mongod
# Mac:   brew services start mongodb-community
# Windows: start the MongoDB service from Services panel

# Start backend
cd ..\backend
.venv\Scripts\activate
uvicorn server:app --host 0.0.0.0 --port 8001 --reload

# Start frontend in another terminal
cd frontend
yarn start

# Open http://localhost:3000
```

---

## Quick Verification Script

After the stack is up, run these checks:

```bash
# Backend health
curl http://localhost:8001/api/health
# Expect: {"db":"ok"}

# Strategies loaded
curl http://localhost:8001/api/strategies
# Expect: items array with 6 built-in strategies

# Upstox connection (if OAuth is configured)
curl http://localhost:8001/api/upstox/status

# Live candle roller (during market hours)
curl http://localhost:8001/api/live-candles/status

# Backend tests
python -m pytest tests -q
# Expect: 440 tests pass (as of 2026-06-12)

# Frontend build
cd frontend
npm run build
```

---

## First Steps in the UI

1. Open Dashboard. Check status cards.
2. Open Data Warehouse. If Upstox is connected, run Data Hygiene plan + execute to bring the warehouse current.
3. Open Backtest Lab. Pick a strategy and run a 6-month backtest with walk-forward enabled.
4. Open Optimizer. Run Bayesian search with risk_adjusted objective.
5. Apply best params as a Preset.
6. Open Live Signals. Create a deployment from the Preset (mode `shadow` first).
7. Wait for signals during market hours, or click Evaluate-now to dry-run.

---

## Phase 4 Upstox Setup

The backend has full OAuth and historical/live data integration. To use real Upstox data:

1. Register an Upstox API app and get `CLIENT_ID` + `CLIENT_SECRET`.
2. Add to `backend/.env`:
   ```
   FERNET_KEY=<generated_value>
   UPSTOX_CLIENT_ID=your_client_id
   UPSTOX_CLIENT_SECRET=your_secret
   UPSTOX_REDIRECT_URI=http://localhost:8001/api/upstox/auth/callback
   FRONTEND_POST_AUTH_URL=http://localhost:3000/warehouse
   ```
3. Restart backend: `docker compose restart backend`.
4. From the UI, go to Data Warehouse and click Connect Upstox.
5. Complete the OAuth flow in the popup.

The token is encrypted in MongoDB. Tokens expire — re-do OAuth when fetches start failing with auth errors.

---

## Backup / Restore Your Data

```bash
# Backup (Docker)
docker exec -t alphaforge_mongo mongodump --archive=/tmp/backup.gz --gzip
docker cp alphaforge_mongo:/tmp/backup.gz ./alphaforge_backup_2026_05_29.gz

# Restore
docker cp ./alphaforge_backup_YYYYMMDD.gz alphaforge_mongo:/tmp/backup.gz
docker exec -t alphaforge_mongo mongorestore --archive=/tmp/backup.gz --gzip
```

---

## Troubleshooting

### "Cannot connect to MongoDB"
- Docker: `docker compose ps` should show `mongo` as healthy.
- Native: `mongosh` should connect to `mongodb://localhost:27017`.
- Verify `backend/.env` has `MONGO_URL=mongodb://localhost:27017` (native) or `MONGO_URL=mongodb://mongo:27017` (docker).

### "No strategies loaded"
- Restart backend. Check logs for `Strategy registered: ...`.
- Failed plugins appear in `GET /api/strategies` with `is_loaded: false` + `error`.

### Frontend shows "Network Error"
- Verify `REACT_APP_BACKEND_URL=http://localhost:8001` in `frontend/.env`.
- Verify backend health: `curl http://localhost:8001/api/health`.

### Upstox 400 "Invalid date range"
- Spot ingest chunker is fixed at 7 days. If you call the synchronous `/upstox/warehouse/ingest` with a bigger custom `chunk_days`, you may hit the Feb→Mar boundary issue. Leave it Auto.

### Same-day historical returns empty
- Expected behavior from Upstox. The live tick → 1m roller (`POST /api/live-candles/start`) closes the gap during market hours.

### Live candle roller not running
- It auto-starts at backend boot after WS auto-start. If WS auto-start failed (Upstox token issue), the roller will not start. Re-do OAuth, then `POST /api/live-candles/start`.

### Port already in use (3000 / 8001 / 27017)
- Docker: edit `docker-compose.yml` and remap, e.g., `"3001:3000"`.
- Native: stop whichever app is using the port.

### Strategy Deployment auto-paused with `strategy_source_drift`
- The plugin .py file changed since the deployment was created. Create a new deployment to pin the new SHA.

### Deployment creation returns `acknowledgment_required` 400
- The source has quality warnings. Tick the acknowledgment checkbox in the UI, or set `acknowledged_warnings=true` in the request body.
