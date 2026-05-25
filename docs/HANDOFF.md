# Handoff Guide — Read This First

> For the next AI agent or developer picking up this project.

## TL;DR

This is a working V1 + V2 + V3 build of AlphaForge Trading Lab. **Phases 1-3 are complete and tested**. Phase 4 (Upstox Live + Options Backtest) is the next major piece. **Do not break existing functionality.** Add features incrementally.

## Critical Things You MUST Know

### 1. Environment Variables — DO NOT MODIFY

- `backend/.env`: `MONGO_URL` is pre-configured. **Never change it.**
- `frontend/.env`: `REACT_APP_BACKEND_URL` is pre-configured by Emergent's Kubernetes ingress. **Never change it.**
- All API routes MUST be prefixed with `/api` (kubernetes ingress rule).
- Backend binds to `0.0.0.0:8001`. Frontend dev server on `3000`. Don't change these ports.

### 2. Service Control

```bash
sudo supervisorctl restart backend     # after .env or new deps
sudo supervisorctl restart frontend
tail -n 50 /var/log/supervisor/backend.err.log
tail -n 50 /var/log/supervisor/frontend.err.log
```

Hot-reload is ON for both — only restart on dep/env changes.

### 3. Dependency Management

- Backend: `pip install <pkg>` then `pip freeze >> backend/requirements.txt` (never overwrite the whole file).
- Frontend: `cd frontend && yarn add <pkg>` (NEVER `npm install`).

### 4. Sanity Checks Before Every Major Edit

1. **Read `/app/plan.md`** — it's the source of truth for the build plan.
2. **Run `test_core.py`** if you touch indicators/backtest/regime: `cd backend && python test_core.py` should print `POC SUCCESS`.
3. **Lint**: `mcp_lint_python` for backend, `mcp_lint_javascript` for frontend before claiming a fix.
4. **No `toLocaleString()` in frontend** — the runtime container reports `POSIX` locale and crashes. Use `lib/fmt.js` helpers instead.
5. **TradingView Lightweight Charts v5** — `addSeries(CandlestickSeries, opts)` (NOT `addCandlestickSeries`). Explicit `localization: {locale: "en-US", timeFormatter, dateFormat}` required for the same POSIX reason.

## Project Status by Phase

### ✅ Phase 1 — POC
- File: `backend/test_core.py` (single-file E2E proof).
- Validates: yfinance ingestion → MongoDB → indicators → Confluence Scalper → backtest with realistic costs → walk-forward → equity curve → significance badge.
- **Run anytime**: `cd backend && python test_core.py`. Expect `POC SUCCESS` at the end.

### ✅ Phase 2 — V1 Lab
- 6 built-in strategies + custom plugin auto-discovery (`backend/app/strategies/plugins/`).
- Data Warehouse v2 with coverage heatmap.
- Pre-Trade Checklist (3 profiles).
- Multi-pane TradingView charts.
- Statistical significance + signal funnel.
- Tested 97.5% pass via testing_agent_v3.

### ✅ Phase 3 — Auto-Optimizer
- Optuna (TPE + CMA-ES) + Grid Search.
- Walk-forward, importance, heatmap, robustness, top-N alternatives.
- One-click "Save as Preset" → presets show in BacktestLab "Load preset" dropdown.
- "View Best in Lab" button auto-loads the best result as a full backtest.
- Stop button for in-flight jobs (graceful cancellation).
- 3 export buttons (Config JSON, Result JSON, Alts CSV).
- Tested 100% pass via testing_agent_v3.

### ⏳ Phase 4 — Upstox Live + Options Backtest (NEXT)

**This is the biggest remaining piece. Plan carefully.**

Required credentials (user has them ready):
- `UPSTOX_CLIENT_ID`
- `UPSTOX_CLIENT_SECRET`
- `UPSTOX_REDIRECT_URI` (must match the Upstox dashboard registration)

Deliverables:
1. **OAuth flow**: `/api/upstox/auth/start` → redirects to Upstox → `/api/upstox/auth/callback` → store token in `upstox_tokens` collection (encrypt at rest if possible).
2. **WebSocket tick stream**: subscribe to underlying + ATM±5 options dynamic universe. Use Upstox V3 protobuf format. Auto-reconnect with exponential backoff.
3. **Live Signal Engine**: per-tick evaluation with discipline filters (cooldown, bar-close gate, daily caps). Full Signal Lifecycle State Machine (WATCHING → FORMING → CONFIRMED → TRIGGERED → ACTIVE → EXITED → AUDITED). Each signal persisted with full state snapshot.
4. **Options Backtest** (paired INDEX+OPTION legs): real expired-option candles via Upstox V3 expired-instruments API. Two risk modes: `spot_points` (INDEX_ONLY exit) and `option_premium_pct` (OPTION_ONLY exit). Port the semantics from the user's original Node repo `pluginHistoricalBacktest.js`.
5. **Live Signal Console UI**: signal cards with full context (strike, entry, target1/2, stop, time stop, regime, VIX, news, invalidation, expected value).
6. **Paper Trading**: one-click "Take / Skip / Deploy to Paper" buttons; live P&L tracking.

**Reference repository**: <https://github.com/hrninfomeet-wq/project-deepseek-version> — the user's original Node.js+SQLite implementation has the full Upstox WS + paired-leg backtest semantics. Read the following files there before implementing:
- `server/upstox/client.js` — REST client with rate limiting needs improvement
- `server/upstox/oauth.js` — OAuth flow
- `server/upstox/websocket.js` — WS protobuf decode (lacks reconnect — add it)
- `server/live/liveSession.js` — paired leg state machine
- `server/paper/paperBroker.js` — paired INDEX+OPTION legs with strict TP/SL
- `server/research/pluginHistoricalBacktest.js` — paired leg historical backtest

Key decisions already made:
- Use **Upstox Python SDK** if available, otherwise raw HTTPS + websockets library.
- Tick stream stored in MongoDB time-series collection `ticks` (compressed).
- Per-tick evaluation with cooldowns (NOT 100s of signals/min).
- Bar-close confirmation gate is configurable per pre-trade profile.

Get the playbook before coding: `integration_playbook_expert_v2("Upstox V3 OAuth + WebSocket tick stream Python")`.

### ⏳ Phase 5 — Profitability Boosters

- **Probabilistic exit engine** (Kaplan-Meier survival analysis on historical similar setups) — the differentiator. Requires ≥6 months of signal history first.
- Meta-model (logistic regression or LightGBM) — which strategy works in which regime.
- Position sizing (Kelly fraction + equity-curve learning).
- Event calendar filter (RBI/FOMC/CPI auto-block).
- India VIX overlay (auto-adjust targets/stops by VIX percentile).
- What-If analysis engine ("if you'd taken every signal…").
- Optional Telegram bot for live signal push.

### ⏳ Phase 6 — Swing/Positional Extension

- Daily/weekly candle warehouse.
- Overnight risk model + gap handling.
- Swing-specific plugins (breakout-and-hold, weekly options).
- Multi-day position lifecycle.

### ✅ Phase 7 — Local Deployment Package (this commit)

- `docker-compose.yml` at repo root.
- `start.sh` (Mac/Linux) + `start.bat` (Windows).
- `backend/Dockerfile` + `frontend/Dockerfile`.
- `docs/LOCAL_SETUP.md` — step-by-step instructions.

## How to Add a Feature Safely

1. **Read `/app/plan.md`** + this file.
2. **Write a TODO** with `todo_write` listing the steps.
3. **Update `/app/plan.md`** if the scope changes meaningfully.
4. **For external integrations** (Upstox, Telegram, etc.) call `integration_playbook_expert_v2` FIRST — never code blind.
5. **For LLM features**: use `EMERGENT_LLM_KEY` via `emergent_integrations_manager` (OpenAI, Anthropic, Gemini supported).
6. **Add data-testid** to every interactive element (the testing agent relies on it).
7. **Call `testing_agent_v3`** after each phase — don't skip.
8. **Fix every issue** the testing agent reports, even low priority.
9. **Use `mcp_bulk_file_writer`** for batches of >3 files (huge token savings).
10. **Call `finish`** with an honest summary after each milestone.

## Known Gotchas

1. **yfinance 30-day cap on 1m data** — for longer history, switch to Upstox in Phase 4.
2. **NIFTY/SENSEX have zero volume** in yfinance — VWAP automatically falls back to typical-price MA.
3. **First few candles have NaN indicators** — every strategy must check and return `direction="NONE"` until warm-up.
4. **ORB strategy needs special context** — `_compute_orb_for_session()` pre-builds the opening range map; passed via `ctx` not parameters.
5. **Custom plugin failures** — captured in `StrategyRegistry._errors`. The StrategyLibrary UI shows them with the exception text.
6. **Optimizer importance fallback** — without scikit-learn, falls back to variance-based importance (works but less accurate). Already fixed by installing sklearn.
7. **Lightweight Charts v5 panes** — uses `chart.addSeries(SeriesType, opts, paneIndex)`. Pane heights via `chart.panes()[i].setHeight()`. Wrap in try/catch — pane API can be undefined in older builds.
8. **POSIX locale crash** — already handled via locale-safe formatters in `frontend/src/lib/fmt.js` + explicit `localization` in chart configs. **Don't introduce new `toLocaleString()` calls without testing.**
9. **MongoDB datetime serialization** — use `serialize_doc()` from `backend/app/db.py` to strip `_id` and convert datetimes before returning JSON. Already done in every route.
10. **Mongo `cancelled` flag** — `_is_cancelled()` queries Mongo every check (not in-memory). This is intentional for cross-process safety. Cost: ~1-2ms per check; only every 5 trials.

## Files You Should NEVER Touch Without Strong Reason

- `backend/.env`, `frontend/.env` — managed by Emergent
- `frontend/src/components/ui/*` — shadcn primitives, regenerate if needed via `npx shadcn-ui add ...`
- `backend/test_core.py` — golden POC. Re-run after backend changes to confirm nothing regressed.

## Quick Sanity Tests

```bash
# Backend health
curl -s http://localhost:8001/api/health
curl -s http://localhost:8001/api/strategies | python -m json.tool | head -50
curl -s http://localhost:8001/api/warehouse/coverage | python -m json.tool

# Frontend reachability (Emergent preview URL — see frontend/.env)
# Open browser → preview URL → check console for errors
```

## When Stuck (Use These Tools)

- `troubleshoot_agent` — call after 2 failed fix attempts. Read-only deep RCA.
- `integration_playbook_expert_v2` — for any third-party integration.
- `web_search_tool_v2` — for current docs / package versions (2026).

## Final Words

Be honest with the user. Show statistical significance badges, OOS divergence warnings, realistic cost models. The point of the app is to **invalidate bad strategy ideas fast**, not to produce vanity backtest charts. If you keep that principle, the user wins.
