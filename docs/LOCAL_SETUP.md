# Local Setup (Run AlphaForge on Your PC)

Two options: **Docker Compose (recommended)** or **Native (Python + Node + local MongoDB)**.

## Option A — Docker Compose (Recommended)

### Prerequisites

- **Docker Desktop** (Windows / Mac) or **Docker Engine + Compose** (Linux)
  - Download: <https://www.docker.com/products/docker-desktop>
  - Verify: `docker --version` and `docker compose version`

### Steps

```bash
# 1. Clone the repo
git clone <your-repo-url>
cd alphaforge-trading-lab

# 2. Copy env templates (defaults work for local — no changes needed yet)
cp backend/.env.example backend/.env       # Linux/Mac
# Windows: copy backend\.env.example backend\.env
cp frontend/.env.example frontend/.env

# 3. Launch
docker compose up -d --build

# 4. Wait ~30 seconds for services to start, then open
# Frontend:  http://localhost:3000
# Backend:   http://localhost:8001/api/health
# MongoDB:   localhost:27017 (no auth in dev)
```

### Stop / Restart / Logs

```bash
docker compose stop                  # stop containers
docker compose start                 # start them back
docker compose restart backend       # restart one
docker compose down                  # stop + remove containers (data persists in volume)
docker compose down -v               # ALSO delete the MongoDB volume (wipes data!)

docker compose logs -f backend       # tail backend logs
docker compose logs -f frontend
docker compose logs -f mongo
```

### One-Command Launchers

- **Windows**: double-click `start.bat`
- **Mac/Linux**: `./start.sh`

Both scripts: copy env templates if missing → `docker compose up -d` → open browser.

### Upgrade (Pull New Code)

```bash
git pull
docker compose up -d --build
```

---

## Option B — Native (No Docker)

### Prerequisites

- **Python 3.11+** — <https://www.python.org/downloads/>
- **Node.js 18+** + **Yarn** — <https://nodejs.org/> and `npm install -g yarn`
- **MongoDB 6+** — <https://www.mongodb.com/try/download/community>
- **Git** — <https://git-scm.com/downloads>

### Steps

```bash
# 1. Clone
git clone <your-repo-url>
cd alphaforge-trading-lab

# 2. Backend setup
cd backend
python -m venv .venv
source .venv/bin/activate           # Linux/Mac
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # edit if MongoDB is not on localhost:27017

# 3. Frontend setup
cd ../frontend
yarn install
cp .env.example .env

# 4. Make sure MongoDB is running
# Linux: sudo systemctl start mongod
# Mac:   brew services start mongodb-community
# Windows: start "MongoDB" service from Services panel

# 5. Start backend (in one terminal)
cd ../backend
source .venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 8001 --reload

# 6. Start frontend (in another terminal)
cd frontend
yarn start

# Open http://localhost:3000
```

---

## First Steps in the UI

1. **Open Dashboard** → see system status
2. **Data Warehouse** → click "Ingest 7d" on NIFTY (and BANKNIFTY/SENSEX if you want)
3. **Backtest Lab** → pick a strategy → click "Run Backtest"
4. **Optimizer** → pick a strategy → click "Auto-Optimize" with n_trials=150
5. **Apply** the best params as a Preset → load it in Backtest Lab → verify
6. **Pre-Trade Checklist** → tune your filters per profile

## Troubleshooting

### "Cannot connect to MongoDB"
- Docker: `docker compose ps` should show `mongo` as healthy
- Native: `mongosh` should connect to `mongodb://localhost:27017`
- Verify `backend/.env` has `MONGO_URL=mongodb://localhost:27017` (native) or `MONGO_URL=mongodb://mongo:27017` (docker)

### "No strategies loaded"
- Restart backend: `docker compose restart backend` or `Ctrl+C` then re-run uvicorn
- Check logs: `docker compose logs backend` — look for `Strategy registered: ...`

### Frontend shows "Network Error"
- Verify `REACT_APP_BACKEND_URL=http://localhost:8001` in `frontend/.env`
- Verify backend health: `curl http://localhost:8001/api/health`

### yfinance "empty data returned"
- Indian market data needs market days. Try `days: 14` instead of 7.
- Outside Indian market hours yfinance may return only 1-2 days. Wait for next market session.

### Port already in use (3000 / 8001 / 27017)
- Docker: edit `docker-compose.yml` and remap left side, e.g. `"3001:3000"` then open `http://localhost:3001`.
- Native: stop whichever app is using those ports.

## Phase 4a (Upstox) Setup — Scaffolded, Not Fully Verified

The backend already contains OAuth and historical candle ingest scaffolding. To validate it locally, register an Upstox API app and add these values to `backend/.env`:

```
FERNET_KEY=generate_a_stable_fernet_key_first
UPSTOX_CLIENT_ID=your_client_id
UPSTOX_CLIENT_SECRET=your_secret
UPSTOX_REDIRECT_URI=http://localhost:8001/api/upstox/auth/callback
FRONTEND_POST_AUTH_URL=http://localhost:3000/warehouse
```

Generate `FERNET_KEY` with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then restart backend with `docker compose restart backend`. The remaining Phase 4 work is WebSocket streaming, live signal state, paper trading, and paired option backtesting.

## Backup / Restore Your Data

```bash
# Backup (Docker)
docker exec -t alphaforge_mongo mongodump --archive=/tmp/backup.gz --gzip
docker cp alphaforge_mongo:/tmp/backup.gz ./alphaforge_backup_$(date +%Y%m%d).gz

# Restore
docker cp ./alphaforge_backup_YYYYMMDD.gz alphaforge_mongo:/tmp/backup.gz
docker exec -t alphaforge_mongo mongorestore --archive=/tmp/backup.gz --gzip
```
