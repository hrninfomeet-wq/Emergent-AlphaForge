# Handoff

Updated: 2026-06-13

This is the entry point for the next AI agent or developer. **Read this + `CHANGELOG.md` before editing.** The repository and `tests/` are the source of truth — not any prior chat. `CHANGELOG.md` holds the detailed, versioned history (currently 0.17.x → 0.36.x); this file is the current architectural + state overview.

---

## 1. What this is

AlphaForge Trading Lab — a local-first research & forward-testing terminal for **Indian index options** (NIFTF/BANKNIFTY/SENSEX). React + FastAPI + MongoDB in Docker, Upstox for data. It does: warehouse 1-minute spot + option candles → backtest/optimize strategies → save as presets → deploy for live **signal generation + auto paper trading**. **No real broker orders, ever** (manual gate is a hard, permanent requirement).

## 2. Status — current state (2026-06-13)

- **566 backend tests pass.** Frontend builds clean (1 pre-existing BacktestLab exhaustive-deps warning). `optimizer.py` is syntax-checked via `py_compile` only (optuna absent on host).
- **Unpushed: 10 commits** on `main` (origin at `1db8173`): the 4 chart commits (`44eae45`/`880cc05`/`696ceda`/`a145ce0`) + the **6-slice live-realism/gate-rigor hardening pass** (`4bc7df3` friction, `754ebb2` gate, `24347db` cockpit, `e914de4` exits, `6d4b8d3` manual-safety, `50a0062` chart+rerank). Push only on the user's explicit "push" (per-changeset approval).
- Most recent work: a multi-agent **app review** → a **6-slice hardening pass** (CHANGELOG 0.37.x; see it for the per-slice detail and the principle that each fix is a user choice, not a silent change). Before that: Data Warehouse overhaul (0.23–0.30), Backtest results redesign (0.31–0.36), execution-policy extraction (0.27), server.py split (0.28).
- **New since the review** (read CHANGELOG 0.37.x before touching these): `app/live_friction.py` (single fill-model, shared by `option_backtest` + the live close path; per-deployment `risk.friction`); `app/rerank_select.py` (pure re-rank shortlist, opt-in `rerank_diversity`); `deployment_quality` now takes `evidence=` (selection-bias deflated-Sharpe + option-rupee-OOS) + `QualityThresholds`; `nse_calendar.market_status`; closed paper trades carry `gross_realized_pnl`/`friction_cost`/`total_charges` + `exit_price_source`/`exit_price_stale`.
- Untracked local note files exist in the repo root ("Fable reply on progress.md", a docs note) — the user's, leave them.

## 3. Running the stack

| Service | Where | Notes |
|---|---|---|
| Frontend | `http://localhost:3000` | React + nginx, dark/light theme |
| Backend | `http://localhost:8001` | FastAPI, all routes under `/api` |
| MongoDB | container `alphaforge_mongo` | named volume `mongo_data` (NOT in the project folder / OneDrive) |
| Upstox | OAuth (daily expiry) + REST historical + V3 WebSocket | re-connect drives auto-update |

```bash
docker compose up -d --build            # launch / rebuild (or start.bat / start.sh)
python -m pytest tests -q               # 533 pass; tests NEVER import server.py (motor absent on host)
cd frontend && npm run build            # must compile clean before committing FE
```

Backend code is baked into the image — **rebuild the backend container after backend edits** (`docker compose up -d --build backend`). Frontend likewise.

## 4. Architecture

### Backend (split in Slice C / CHANGELOG 0.28.x — zero behavior change)
- `backend/server.py` — **203-line app factory only**: startup/shutdown + scheduler wiring, root/health, mounts the routers, CORS.
- `backend/app/schemas.py` — all Pydantic request models.
- `backend/app/runtime.py` — shared singletons (`upstox_stream_manager`, `live_candle_roller`), constants, and the route helpers (catch-up chain, hygiene submit, VIX top-up, option preview, etc.).
- `backend/app/routers/{research,warehouse,journals,deployments,broker}.py` — the 103 routes (each file: `api = APIRouter()`). Import DAG: **server → routers → runtime → app business modules** (no cycles; nothing imports server).
- Key business modules: `completeness.py` (band truth), `data_hygiene.py` (warehouse plan/ledger/catch-up), `execution_policy.py` (exit truth), `backtest.py` + `option_backtest.py` + `portfolio.py` (engines), `optimizer.py` + `wfo.py` + `walkforward.py`, `deployment_evaluator.py`, `paper_auto.py` + `paper_trading.py` + `paper_squareoff.py`, `warehouse_autoupdate.py`, `warehouse_ohlc.py`, `option_coverage_cache.py`, `nse_calendar.py`, `live_candle_roller.py`, `upstox_client.py` + `upstox_stream.py`, `instruments.py` (canonical keys), `slippage.py`, `volatility.py`, `vix.py`, `dte.py`, `market_context.py`.

### Frontend
- Pages: `pages/{Dashboard,BacktestLab,Optimizer,DataWarehouse,LiveSignals,SignalJournal,PaperTrading,StrategyLibrary,Checklist}.jsx`.
- `components/warehouse/*` — Data Warehouse split (UpstoxPanel, OptionPlannerPanel, ExpiredBackfillPanel, CoverageHeatmaps, DataTrustPanel, VolatilityAuditPanel, Disclosures, shared).
- `components/backtest/*` — Backtest results: `PerformanceOverview.jsx`, `DualAxisChart.jsx`, `MonthlyPnlCalendar.jsx`, `BacktestChart.jsx`.
- `lib/backtestMetrics.js` — pure client-side metrics/series for the backtest results.
- `lib/jobs.jsx` — global `JobsProvider` (background-job tracker, survives navigation; persists active run IDs to localStorage).
- `components/{Layout,DataHygienePanel,WarehouseChart,WarehouseLookup,HolidayCalendarDialog,BacktestRunJournal,MetricCard,TokenCountdown}.jsx`.

### Mongo collections
`candles_1m`, `options_1m`, `option_contracts`, `option_known_empty` (broker-empty ledger), `integrity_hashes`, `warehouse_runs`, `option_coverage_cache`, `data_hygiene_latest` (persisted plan), `backtest_runs`, `optimization_jobs`, `presets`, `pretrade_profiles`, `strategy_deployments`, `signals` (unique partial index `(deployment_id, candle_ts)`), `paper_trades`, `ticks`, `upstox_tokens`.

## 5. Data Warehouse — the philosophy (read before touching warehouse code)

- **Daily ATM-band completeness is the single definition of "complete"** (`app/completeness.py`). A day is option-complete when *every strike its spot low–high touched* (nearest `round_to_step` ±1 pad), for **both legs**, at the day's resolved (next-available) expiry, has candles. `band_completeness` (counts + `per_day` rows + `coverage_pct`), `missing_band_pairs` (exact fetch list). The old per-day/per-expiry presence check was the "verified-but-incomplete" bug.
- **Fetch is driven by the SAME band it's judged against** — `data_hygiene.build_band_fetch_plan` → `missing_band_pairs` → exact (day, expiry, side, strike) → contract → date tasks. Never re-derive a separate moneyness selection for the fetch (that was the 0.25.x bug).
- **Broker-empty ledger** (`option_known_empty`): some band strikes are genuinely unavailable at Upstox (late-listed strikes Upstox never archived — verified: one authoritative token each, 0 candles, no duplicate in the 61.7k-contract store). After a band fetch, `record_broker_empty_pairs` ledgers requested-but-still-absent pairs **whose task did not fail AND are before the latest closed session** (`grace_from` — Upstox publishes F&O history with a lag; never ledger a same-night session). Ledgered pairs are excluded from `missing_pairs`/coverage and shown as "broker-empty" so status reaches **verified** honestly. `retest_known_empty=True` forces a re-test.
- **Partial-day spot repair** (`incomplete_spot_days`): a trading day captured only partially (PC off mid-session → only the live roller's morning, e.g. 255/375) sits at the last-stored-date high-water mark, so plain catch-up never repaired it. Catch-up now detects closed days materially below the **calendar-expected** count (`nse_calendar.expected_candle_count`; Muhurat/weekend ticks never flagged) and re-fetches them, bounded by `SPOT_REPAIR_LOOKBACK_DAYS=21` (churn guard).
- **Sync** = `POST /api/warehouse/sync` (alias of `/data-hygiene/catch-up`): catch-up new sessions + band sweep for spot-current instruments + **VIX top-up** (`_topup_vix`). Auto-update (`warehouse_autoupdate.py`) runs on startup / OAuth-connect / daily 18:00 IST; VIX is folded into the daily loop via `pre_run_fn`.
- **Instant status**: `/api/data-hygiene/plan` persists its result to `data_hygiene_latest`; `/api/data-hygiene/latest` serves it so the page shows health on load. Rolling scope = `default_scope_start()` (9 months, floor 2024-11-27).
- **Canonical keys**: candles stored under 2-part `SEGMENT|TOKEN`; dated 3-part keys only in expired-endpoint URLs; all lookups canonicalize (`instruments.canonical_instrument_key`). **Expired routing keys off `expiry_date < today(IST)`**, not provenance → `/v2/expired-instruments/...`.
- Page (`DataWarehouse.jsx` + `components/warehouse/*`): status hero, **Sync now**, band-truth heatmaps with 8w/3m/All range chips, **Advanced tools** (collapsed: manual planner + expired backfill — pre-band moneyness selection, research only), **danger zone** (typed-confirmation clears), runs table with human labels.

## 6. Backtest results (CHANGELOG 0.31–0.36)

Engine: `backtest.py` (spot) + `option_backtest.py` (paired option execution, premium-accurate) + `portfolio.py` (rupee equity curve, sizing). All exit decisions go through `execution_policy` / `intrabar_exit` (stop-first).

Results UI (`BacktestLab.jsx` `ResultsView` → `components/backtest/*`, all client-side from the run doc):
- **KPI grid**: Trades, Win Rate, Profit Factor, Net P&L pts, Max DD pts, Sharpe, **Lowest/Highest account value** (min/max of the ₹ equity curve — surfaces e.g. the account briefly going negative).
- **`PerformanceOverview`**: ₹-first hero (Net ₹, Return on capital %, Ending equity, Max DD ₹/%, **Profit ÷ max DD**, annualized Sharpe). **Honest-metric rule: CAGR/Calmar are suppressed under a ~1-year window** (`years >= 1.0`) — annualizing a few months produced absurd 1900% vanity numbers; the span-independent Profit÷maxDD is the headline reward/risk. Trade-quality block (avg win/loss, payoff, expectancy ₹, streaks, drawdown duration + recovered).
- **`DualAxisChart` ×2** (named vertical axes, text-up): "Cumulative P&L vs trade value" (left = cum P&L, right = **per-trade net buy value** = entry premium×qty+charges — the user's definition, NOT index level) and "Account value & drawdown".
- **`MonthlyPnlCalendar`**: year×month net-P&L grid.
- **`BacktestChart`**: price chart with the strategy's trades — instrument title, **1m/5m/15m/1h/1d**, entry/exit markers with **#N trade-number labels (density-gated: shown when focused or ≤50 in view)**, focused-trade **Entry/Target/Stop/Exit** price lines (SL/target reconstructed from `spot_target_pts`/`spot_stop_pts`), **go-to date/time** locator + trade navigator, **full-screen maximize** (Fullscreen API). Trades table has **Lots (Qty) / Buy ₹ / Sell ₹** columns (Sell − Buy = net P&L).
- **Advanced analytics** (collapsed): data-audit, option pairing/execution, context breakdown (DTE/regime/time/VIX), MAE/MFE, Monte Carlo, walk-forward IS/OOS, signal funnel.
- `lib/backtestMetrics.js`: `buildPerformanceSeries`, `computeKeyMetrics`, `tradeBuyValue`/`tradeSellValue`, `monthlyPnl`.

## 7. Execution policy (CHANGELOG 0.27.x) — do not bypass

`app/execution_policy.py` is the **single source of exit semantics**, shared by sim and live: `resolve_premium_levels` (pts-over-pct, target above / stop below, floor — sim 0.0 / live ₹0.05), `tick_exit_reason` (a live tick is a degenerate bar routed through the backtest's own `intrabar_exit` → **stop-first**), `spot_mirror_levels` / `spot_mirror_exit_reason`. `tests/test_execution_policy.py` (11 golden tests) pins sim↔live parity — a real divergence (live deciders were target-first) was fixed here. Any new exit logic must route through this module.

## 8. Forward testing / deployments / paper

- Deployments (`strategy_deployments`) created only from saved presets or backtest runs; modes `signal_only | paper`; evaluate independently (no cross-strategy arbitration). Approval flow is **retired** — do not resurrect it.
- `deployment_evaluator.py` runs on a 1-minute-close scheduler in market hours. Paper deployments with `risk.auto_paper` (default ON) auto-open a paper trade per clean CONFIRMED signal at real option premium; a per-minute marker fires premium + spot-mirror exits; 15:00 IST square-off (`risk.allow_overnight` opts out).
- Forward metrics gated by ≥70%-covered 10:00–15:00 sessions; low-sample surfaces in Strategy Library under an amber badge. Strategy-source SHA pinned per deployment; evaluator auto-pauses on drift (re-pin via `POST /api/deployments/{id}/repin-source`).

## 9. Non-negotiable trading rules / conventions (locked by the user)

- **Premium-never-spot** fills; **lot size always from `option_contracts.lot_size`**; **OPEN trades never deletable**; **IST everywhere**.
- DTE default `[0..6]`; signal window 09:25–14:50; expiry-day cutoff 15:00 IST (from `option_contracts.expiry_date`, never weekday-hardcoded).
- Slippage: ATM 0.5pt / OTM1·ITM1 1pt / OTM2+·ITM2+ 2pt / expiry-day 30-min 2×.
- Hygiene scope: rolling 9 months (floor 2024-11-27), NIFTY+BANKNIFTY+SENSEX, daily ATM band. No event calendar (post-hoc volatility detector instead).
- **Never** commit `.env`, tokens, broker creds, or `memory/test_credentials.md`.

## 10. Testing approach

- `pytest -q` (533) must pass before any commit. **Tests never import `server.py`** (motor is absent on the host) — they string-assert on source via `tests/contract_corpus.py`: `backend_api_text()` (server + schemas + runtime + routers) and `warehouse_page_text()` (DataWarehouse page + `components/warehouse/*`). When you pin a route/testid, it can live in any router/component file.
- `FakeDB`/`FakeCollection` stubs for hygiene/evaluator tests; add `self.option_known_empty = FakeCollection()` when a new test exercises the ledger.

## 11. Working with the user / Kiro split / standing decisions

- **Division of labor**: complex / trading-critical work → the senior agent (this one). Well-bounded UI slices → **Opus 4.8 in the Kiro app** via ready-to-paste prompts. Kiro must **never** edit `deployment_evaluator.py`, `optimizer.py`, `wfo.py`, `paper_auto.py`. `.kiro/specs/forward-surfaces-overhaul/design.md` is the conventions bible (theme tokens, kebab-case testids, IST, contract tests in the same commit, pytest + npm build + docker rebuild + browser check per slice). Quality-hardening Slice C (server split) was done by the senior agent; that spec is fully delivered.
- **Standing decisions**: no Fyers/Flattrade integration (dropped); the "retire the legacy spot-only optimizer evaluation / flip rerank default" recommendation is **deferred** by the user ("I will retire the legacy later"); **per-changeset push approval** (commit freely, push only when the user says "push"); **batch docs** — one consolidated pass per session, important info only (the user explicitly wants tokens saved on doc churn).
- See the persistent memory (`alphaforge-operating-context`) for usage reality: PC rarely runs in market hours (research honesty > live-uptime features); auto-paper wanted; low-sample metrics visible not hidden.

## 12. Operational lessons (gotchas — read before related work)

**Upstox**: 30-day chunks crossing Feb→Mar give `400 Invalid date range` (use `chunk_days=7`); historical is empty for the in-progress day (the live roller closes it); F&O history publishes with a **lag** (don't trust same-night completeness → the ledger grace rule); expired options need `/v2/expired-instruments/...` (normal V3 returns `UDAPI100011`); `GLOBAL_INDICATOR|USDINR` REST quote 400s but works on WS; WS subscription set is fixed at connect (stop+restart to change).
**Contract correctness**: always filter `expiry_date >= today` when picking a live contract; `select_contract_for_signal` is exact-match-or-None (no nearest fallback — regression-pinned). Some Upstox expired strikes have outlier tokens with 0 candles and no alternative — genuinely broker-empty, not a remap bug (verified; do not "fix" by re-keying).
**Performance**: `options_1m` is 5M+ docs — never aggregate it on a page-load path; use `option_coverage_cache` / index-friendly groupings, no `$lookup`. Candlestick timeframes window intraday so requests stay ~100ms.
**Frontend**: long-job polling lives in `lib/jobs.jsx` (survives navigation). lightweight-charts — keep effect deps **stable** (data refs, not freshly-built objects) or it disposes+recreates and races autoSize ("Object is disposed"). **Do not shadow the global `window`** with a local variable (a `const window = useMemo(...)` crashed the chart's Fullscreen handler — `addEventListener is not a function`). CRA SPA client-navigation does NOT reload the JS bundle — after a rebuild, do a full reload (or check `curl localhost:3000 | grep main.*.js` for the new hash). The browser-screenshot tool intermittently times out on canvas-heavy pages (verify via DOM `find`/`read_page` + console instead).
**Git**: `core.autocrlf=true` → harmless CRLF warnings on commit.

## 13. Verification checklist

```bash
python -m pytest tests -q          # 533 pass
cd frontend && npm run build       # compiles (1 pre-existing exhaustive-deps warning in BacktestLab)
cd .. && docker compose up -d --build && docker compose ps
curl -s localhost:8001/api/health  # {"db":"ok"}
```
UI smoke: Data Warehouse hero + Sync now + band heatmaps; Backtest results — KPI grid incl. account-value range, the two charts with named axes, monthly calendar, BacktestChart (timeframes, trade focus → #N markers + Entry/Tgt/SL lines, go-to, maximize); no console errors.

## 14. What's next (open items)

- **Verify the 0.37.x hardening pass in the running stack**: `docker compose up -d --build` then a browser smoke — deploy a paper strategy with friction ON (check the journal's net vs gross), the Live Signals market badge/clock + last-evaluated, the deploy-gate selection-bias/option-OOS warnings, a manual close with a fat-fingered price (override prompt), and the chart's premium focus strip on an option-levels run. (Tests + FE build are green; the live/browser check was deferred.)
- **Surface the new paper-trade fields in the journal UI** (data already flows via `/paper/trades`): a gross-vs-net column + `friction_cost`/`total_charges`, and a "stale/estimated" badge from `exit_price_stale`. (Deferred FE follow-up to 0.37.x friction + exit-edge work.)
- **Dead-code cleanup**: the old self-referential option-coverage endpoint/cache/`api.optionCoverage` (the heatmap reads band truth; this path has zero frontend call sites) — safe to delete.
- After A/B-validating the option re-rank (now with the opt-in `rerank_diversity` shortlist), retire the legacy spot-only optimizer path (deferred by user), then a live-only risk engine.
- Optional warehouse extras: option-price sanity check, `mongodump` backup button.
- Backtest follow-ups (optional): a shared backend performance-metrics module so the optimizer/forward metrics reuse identical definitions; benchmark overlay was explicitly declined.
- Phase 5/6 (survival models, Kelly, swing) deferred until ≥6 months of forward history.
