# Local Setup

Updated: 2026-07-13 (Phase 4 handoff — AI-key setup added, sync-from-GitHub flow validated)

Two options: **Docker Compose (recommended)** or **Native (Python + Node + local MongoDB)**.

## TL;DR — sync from GitHub and run

```bash
git clone https://github.com/hrninfomeet-wq/Emergent-AlphaForge.git
cd Emergent-AlphaForge

# 1) copy env templates
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env

# 2) generate a Fernet key for backend/.env (required for Upstox token encryption)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# paste into backend/.env as FERNET_KEY=...

# 3) paste your Upstox client id/secret and (optional) AI keys into backend/.env
#    (see backend/.env.example for every key that matters — grouped by purpose)

# 4) launch
docker compose up -d --build

# 5) open the app
# Frontend:  http://localhost:3000
# Backend:   http://localhost:8001/api/health
```

Everything else on this page is expanded detail on those five steps.

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

# 4. Edit backend/.env — fill in the sections you need:
#    Upstox (required for warehouse data ingest):
#      UPSTOX_CLIENT_ID=...
#      UPSTOX_CLIENT_SECRET=...
#
#    AI wizard (required for Strategy Library's "Check Feasibility" + AI Generate):
#      GEMINI_API_KEY=...   (get one free at https://aistudio.google.com/apikey)
#      # optional: ANTHROPIC_API_KEY=... for the Claude authoring path
#
#    Live trading stays OFF by default (LIVE_AUTOPLACE_ARMED=0). Leave it at 0 until
#    you're actively trading live. (v0.56.0: LIVE_GUARD_ARMED is gone — the exit guard
#    always transmits; going live is per-deployment via the app's Deploy-to-Live.)

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

- Windows: double-click `start-app.bat`
- Mac/Linux: `./start.sh`

The Windows launcher checks Docker Desktop, validates env files without printing secrets, starts the stack sequentially, waits for health, and then opens the browser. See `docs/STARTUP_MANUAL.md`.

### Upgrade (pull latest, rebuild)

```bash
git pull
docker compose up -d --build
```

Backend code is baked into the image, so **you must rebuild after backend edits or a fresh `git pull`** — a plain `docker compose up -d` will restart the old image.

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
copy .env.example .env                   # then edit — see backend/.env.example

# Frontend
cd ..\frontend
yarn install
copy .env.example .env

# Make sure MongoDB is running
# Linux: sudo systemctl start mongod
# Mac:   brew services start mongodb-community
# Windows: start the MongoDB service from Services panel

# NATIVE: change MONGO_URL in backend/.env to `mongodb://localhost:27017`
# (Docker Compose uses `mongodb://mongo:27017`.)

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

## AI Wizard Setup (Strategy Library — Check Feasibility / AI Generate)

As of Phase 4 (2026-07-13), the AI authoring wizard **accepts premium-native rules** (option-premium momentum triggers, locked strikes, stepped premium trails) and **maps session-level gates** (entry/exit time, EOD square-off, re-entry cutoff, day caps, position size) to their existing deployment-layer configuration — no more blanket rejects on the AlgoTest-style Configurable Contingency Breakout blueprint.

To use it, add at least one AI provider key to `backend/.env`:

```env
# Gemini (free tier available — get a key at https://aistudio.google.com/apikey)
GEMINI_API_KEY=your_key_here

# Optional — Anthropic (Claude), preferred if both are set
ANTHROPIC_API_KEY=your_key_here
```

Also as of Phase 4, the Gemini token-budget cutoff bug is fixed: `_gemini.DEFAULT_MAX_TOKENS` is now 32,768 (previously 8,192 was consumed by gemini-2.5-pro thinking tokens on non-trivial descriptions, leaving the JSON cut off mid-string). No user-facing action needed — just pull, rebuild, and long strategy descriptions no longer error.

Restart backend after editing `.env`:

```bash
docker compose restart backend
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
# Expect: items array with 12 strategies (11 deletable plugins + confluence_scalper,
# including premium_momentum)

# AI provider status
curl http://localhost:8001/api/strategies/author/providers
# Expect: providers list with at least one `configured: true`

# Upstox connection (if OAuth is configured)
curl http://localhost:8001/api/upstox/status

# Live candle roller (during market hours)
curl http://localhost:8001/api/live-candles/status

# Backend tests (host-safe / pure only — motor tests run inside the backend container)
# Needs a host Python env with pymongo/motor/pandas/pytest installed — the repo ships a
# root-level `.venv` with these already (create your own with
# `pip install -r backend/requirements.txt` if it's missing). A bare system Python
# without this WILL fail to collect ~25 test files with "No module named 'pymongo'" —
# that's an environment gap, not a code failure.
.venv\Scripts\python.exe -m pytest tests -q   # Windows; .venv/bin/python -m pytest tests -q on Mac/Linux
# Expect: ~3300+ tests pass (as of 2026-07-14)

# Frontend build
cd frontend
yarn build
```

---

## First Steps in the UI

1. Open Dashboard. Check status cards.
2. Open Data Warehouse. If Upstox is connected, run Data Hygiene plan + execute to bring the warehouse current.
3. Open Backtest Lab. Pick a strategy and run a 6-month backtest with walk-forward enabled.
4. Open Optimizer. Run Bayesian search with the `risk_adjusted` objective.
5. Apply best params as a Preset.
6. Open Live Signals. Create a deployment from the Preset (mode `shadow` first).
7. Wait for signals during market hours, or click Evaluate-now to dry-run.

### Trying the AI wizard (Phase 4 sanity check)

1. Open Strategy Library.
2. Click **AI Author** → paste the AlgoTest "Configurable Contingency Breakout (NF CE PE EXP2 Base)" blueprint from `docs/superpowers/specs/2026-07-13-...md`.
3. Click **Check Feasibility**. You should see an ADVISE (not REJECT) with rules mapping to `premium_trigger_config` (shipped) and `deployment_layer` (existing deployment config), plus `lazy_leg_contingency` marked as backtest-only Phase-5 future work.
4. If it still says REJECT: check `docker compose logs backend` for `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` errors and confirm `curl http://localhost:8001/api/strategies/author/providers` shows at least one configured.

---

## Upstox OAuth Setup

The backend has full OAuth and historical/live data integration. To use real Upstox data:

1. Register an Upstox API app and get `CLIENT_ID` + `CLIENT_SECRET` at https://developer.upstox.com/.
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
docker cp alphaforge_mongo:/tmp/backup.gz ./alphaforge_backup_2026_07_13.gz

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

### AI wizard says "no AI provider configured"
- At least one of `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` must be set in `backend/.env`.
- After editing `.env`, restart backend: `docker compose restart backend`.
- Verify: `curl http://localhost:8001/api/strategies/author/providers` should show at least one with `configured: true`.

### AI "response was cut off at the N-token limit" (historic — fixed in Phase 4)
- Symptom: `AI generation failed: The AI (gemini-2.5-pro) response was cut off at the 8000-token limit`.
- Root cause was `_gemini.DEFAULT_MAX_TOKENS=8192` + a `max_tokens=8000` hard cap in `py_author.py`; gemini-2.5-pro's thinking tokens consumed the whole budget on any non-trivial rule set.
- Fix: on Phase 4+ builds this can no longer happen for reasonable descriptions. If you still hit it on a truly gigantic description, the error is now actionable ("shorten / split the description and retry").

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
