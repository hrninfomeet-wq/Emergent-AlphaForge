# Project Overview

Updated: 2026-07-21

## What AlphaForge Is

AlphaForge is a local-first research, forward-testing, and supervised live-execution terminal for Indian index options on NIFTY 50, BANKNIFTY, and SENSEX. It stores market data on disk, audits coverage, runs backtests, optimizes strategy parameters (including honest walk-forward optimization), and runs strategies against live 1-minute closes. Deployed strategies run independently and concurrently: paper mode auto-trades at real option premiums, signal-only mode journals, and a user-confirmed live deployment can route long-option orders through the guarded Flattrade executor when the master transmit gate is on.

It is not a guaranteed-profit system. It is the disciplined research and execution-prep stack a serious systematic options trader would build for themselves.

## Project Objective

End-to-end quant workflow:

1. Ingest clean index and option data into a local warehouse with integrity audits.
2. Build and tune strategies in a research lab with realistic costs and walk-forward validation.
3. Auto-optimize parameters with multiple search methods, robustness scoring, and an honest walk-forward (OOS) mode.
4. Forward-test the optimized strategy on live 1-minute closes with full audit trail.
5. Paper-trade signals automatically on clean signals (paper mode), or journal-only (signal_only mode) — multiple strategies concurrently and independently.
6. Review forward profitability per deployment before trusting a strategy with capital.

## Status Snapshot (2026-07-21)

| Area | Status |
|---|---|
| Local Docker stack | Working on Windows: MongoDB, FastAPI, React/nginx |
| Index data warehouse | NIFTY/BANKNIFTY/SENSEX 1m candles, ~100% coverage 2024-11-27 → today |
| Option warehouse | NIFTY ~1.46M / BANKNIFTY ~1.69M / SENSEX ~2.21M ATM CE/PE candles (OI populated) |
| NSE holiday calendar | 2024–2026 with Budget Saturdays + shifted-expiry exceptions; holiday-calendar modal |
| Data Hygiene (UI + backend) | Hero panel: check (plan ~6s) + fill (dependency-ordered execute) |
| Warehouse auto-update | On startup, OAuth-connect, and daily 18:00 IST |
| Option coverage page load | Cache-backed (~200ms, was ~8s) |
| Persistent background jobs | Progress survives navigation (global JobsProvider) |
| Point-in-time lookup | Spot + ATM CE/PE at any date/time, warehouse-only |
| Candlestick chart | Per-index 1m/5m/15m/1h/1d, OHLC legend, date/time locator, gap banner |
| Live tick → 1m OHLC roller | Running, closes Upstox same-day historical gap |
| Strategy plugin system | Built-in + drop-in `.py` plugins |
| Backtest + walk-forward | Complete; statistical significance and regime detector wired |
| Optimizer | Bayesian, Grid, CMA-ES; robustness, importance, heatmap; optional guard rails; indicator-period search; net-rupee objective; **option-aware two-stage re-rank**; **pause/resume/crash-resume**; ~8.8x faster loop |
| Walk-forward optimization (WFO) | **Honest OOS mode** (`POST /api/optimize/wfo`): per-window re-optimization, stitched OOS equity, WF efficiency / consistency / param stability |
| Option preflight | Pre-run would-pair coverage check (+ optional ingest) before a backtest |
| Slippage + volatility | Expiry-tail slippage + post-hoc detector |
| Rupee cost + sizing + context | Option cost model (%-spread), premium-at-risk sizing, regime/time/DTE/India-VIX tagging; INDIAVIX ingest in Data Hygiene scope |
| Strategy Deployments | 1m_close evaluator running, scheduler ON, drift detection ON |
| Auto paper trading | Paper-mode deployments auto-trade clean signals at real option premium (`risk.auto_paper`, default ON for new deployments); per-minute live marker fires stop/target/spot-mirror exits |
| Deployments command center | Per-strategy cards + 3-step wizard + undeploy; approval flow retired 2026-06-12; deployments independent |
| Auto square-off | 15:00 IST every market day, override per deployment |
| Quality gates + readiness | In the deploy wizard: validation evidence + warning acknowledgment |
| OAuth token-expiry countdown | In the global top bar |
| Forward metrics aggregation | Session-gated deployment metrics in Strategy Library; low-sample deployments shown with an amber badge instead of hidden |
| Per-deployment kill switches | Complete (max consecutive losses / daily loss cutoff / max open trades) |
| Phase 5 probability engine | Deferred until ≥6 months forward signal history |
| Phase 6 swing extension | Not started |

Verification baseline: 3,524 backend tests pass, 4 expected failures, 0 unexpected
failures; the optimized frontend build completes successfully (2026-07-21).

## Capabilities Summary

### Data warehouse

- Index 1-minute candles for NIFTY, BANKNIFTY, SENSEX in `candles_1m`, audited per day in `integrity_hashes` (holiday-aware via `nse_calendar`).
- Option 1-minute candles in `options_1m` with OI preserved.
- Option contract metadata in `option_contracts` with strike, side, expiry date, lot size from Upstox.
- Background ingest jobs for large 12–18 month index ranges, tracked by the global JobsProvider so progress survives navigation.
- Background option fetch jobs that fetch only the selected contract dates rather than the full date range per contract.
- Option Data Planner with preview, ATM-only default, configurable OTM/ITM, expiry mode, sample interval, and max-contract guard.
- Option Coverage Heatmap (served from `option_coverage_cache` for fast loads) and per-index candlestick chart with gap detection.
- **Data Hygiene** workflow (UI hero panel + backend): diffs the desired warehouse against current state and submits dependency-ordered fetches (~6s plan).
- **Automatic warehouse catch-up** on startup, OAuth-connect, and daily 18:00 IST (gated on Upstox connected; toggleable).
- **Point-in-time lookup**: spot + ATM CE/PE at a chosen date/time, read only from the warehouse, for cross-checking against a broker terminal.
- NSE holiday calendar with Budget Saturday and shifted-expiry support, surfaced as a modal.

### Research

- 6 built-in strategies plus drop-in `.py` plugins.
- Backtest with realistic Indian intraday costs.
- Walk-forward IS/OOS with divergence flag.
- Statistical significance (Wilson 95% CI).
- Regime detector (ADX + Choppiness + ATR expansion).
- Pre-trade checklist with 3 profiles and live signal-pass counter.
- Optimizer (Bayesian / Grid / CMA-ES) with robustness scoring, parameter importance, top-N alternatives. Plus: optional guard rails (min_trades + CE/PE share), indicator-period search, `net_pnl_inr` objective, an **option-aware two-stage re-rank** that selects params by real paired-option net rupee, and **pause / resume / crash-resume** with persisted progress. Backtest hot loop is ~8.8x faster (dict records).
- **Honest walk-forward optimization** (`app/wfo.py`, run type "Walk-forward" in the Optimizer): rolling or anchored trading-day windows, per-window Optuna re-optimization on the train slice only, each window's best scored on its unseen test slice, stitched into one OOS equity curve. Reports walk-forward efficiency (OOS pnl/day ÷ IS pnl/day; ≥0.7 strong, <0.4 likely overfit), OOS consistency, and per-parameter stability. Deployable params come from the most recent train window; Save-as-Preset / deploy flows work unchanged. v1 evaluates on spot — run the final preset through an option re-rank or option backtest for rupee realism.
- Pre-run **option-data preflight** (`POST /api/backtest/option-preflight`) reports would-pair coverage and can ingest missing option data before a backtest.
- Rupee option cost model (brokerage + statutory + %-of-premium spread), premium-at-risk sizing, DTE filter, and market-context + India-VIX tagging — a shared decision engine so backtest, paper, and live agree.
- Slippage model (ATM 0.5pt, OTM1/ITM1 1pt, OTM2+/ITM2+ 2pt, expiry-day 30-min 2x) wired into paired option backtests.
- Post-hoc volatility detector (5-min realized vs 30-day rolling baseline, spike threshold 2.5x).

### Forward testing

- Strategy Deployments persisted in `strategy_deployments`, created from immutable snapshots of compatible Strategy Library entries, saved presets, or saved backtest runs.
- Strategy source SHA pinned at creation; auto-pause on drift.
- Pre-flight check (spot coverage, upcoming expiries, active vs expired contracts, Upstox token state).
- Deployment quality warnings (missing walk-forward, divergence, low trade count, weak Sharpe, large drawdown) with required user acknowledgment.
- 1m_close evaluator runs every minute boundary +10s during NSE market hours.
- Time-of-day blocks (09:15–09:25 and 14:50–15:30 IST) and expiry-day cutoff at 15:00 IST.
- DTE filter (default `[0..6]`) and `option_no_data` blockers.
- Concurrency rule: keep highest-score per `(instrument, candle_ts)`.
- Audit trail invariants: `bar_ts`, `decision_ts`, `strategy_id`, `strategy_version`, `strategy_hash`, `pretrade_settings_snapshot`, `regime`, `option_contract`, `tracked_for_pnl`, `blockers[]`.
- Per-deployment kill switches under `deployment.risk`: `max_consecutive_losses` and `daily_loss_cutoff_pct` auto-PAUSE; `max_open_paper_trades` soft-BLOCKs new signals until trades close. Paper deployments only.
- Forward metrics per deployment: win-rate, avg P&L, total P&L, profit factor, and excluded incomplete-session trades. Strategy Library shows full metrics after 10 complete sessions and shows earlier results under an amber "low sample" badge (n/10 sessions) — preliminary, not evidence.

### Paper trading — automatic

> 2026-06-12: the approval flow was retired and deployments became fully independent (modes: `signal_only` | `paper`). Approve/skip bullets below were removed; see CHANGELOG 0.17.x and `docs/HANDOFF.md`.

- **Auto paper trading** (paper mode, `risk.auto_paper`, default ON for new deployments): every clean CONFIRMED signal opens a paper trade automatically — no clicking. The hook runs after the concurrency rule, with an atomic per-signal claim so the auto path and the approve route can never double-trade one signal.
- **Entry is always real option premium**: live WS tick first, else a stored `options_1m` candle at most 5 minutes old, never the spot index level. No resolvable premium means no trade plus a journaled `paper_trade_error` (the signal stays approvable).
- **Exits mirror the backtest**: strategy `risk_hints` win over the deployment-level fallbacks (`auto_paper_target_pts`/`auto_paper_stop_pts` in ₹ of premium, then `auto_paper_target_pct`/`auto_paper_stop_pct` in %). Built-in strategies define spot-point exits, so auto trades carry direction-aware spot-mirror levels — when the index hits the strategy's target/stop, the option closes at its current premium (`spot_target_hit`/`spot_stop_hit`). A background marker checks open trades every minute during market hours; anything left closes at the 15:00 IST square-off.
- Pending Approval panel still shows CONFIRMED signals (shadow/recommendation deployments, paper deployments with auto-paper off, or auto-trade refusals) with Approve / Skip / Mark Blocked.
- Approve resolves premium the same way and creates the paper trade when `deployment.mode == "paper"`, with lot size from `option_contracts.lot_size` and `lots` from `deployment.risk.default_lots` (default 1). Premium unavailable → HTTP 409 and the signal stays CONFIRMED.
- Auto square-off at 15:00 IST every market day. `risk.allow_overnight=true` opts out per deployment.

### Live data

- Upstox V3 read-only WebSocket market-data stream auto-starts on backend boot.
- Live tick → 1m OHLC roller subscribes to the broadcast and persists rolled bars into `candles_1m` so the evaluator fires on intraday bars.
- Market header prefers fresh ticks and falls back to REST quotes when ticks are stale or absent.

## Architecture

The system is a React + FastAPI + MongoDB stack running locally via Docker Compose. The backend exposes REST under `/api`, talks to Upstox over REST and a V3 WebSocket, and persists everything to MongoDB. The frontend is a single-page app with theme tokens and shadcn/ui components. See `docs/ARCHITECTURE.md` for the full module map, data flow diagram, and collection list.

## Key Workflows

### 1. Refresh the warehouse

Day-to-day this is automatic: the warehouse catches up to yesterday's close on backend startup, on Upstox OAuth-connect, and daily at 18:00 IST (today's bars come from the live roller). To do it manually:

1. Open Data Warehouse, confirm Upstox is connected (top-bar token countdown is green).
2. In the **Data Hygiene** panel, click **Check warehouse** (runs the plan, ~6s) to see the per-instrument spot/contracts/option-candle diff.
3. Click **Fill gaps** to submit the fetches in dependency order (spot → contracts → option_candles). Progress shows in the panel and the top bar and survives navigation.
4. Re-run **Check warehouse** to confirm gaps closed.
5. Verify with the candlestick chart (gap banner) and the point-in-time lookup.

### 2. Build and tune a strategy

1. Pick a strategy in Backtest Lab. Add params, costs, walk-forward.
2. Save the result. Open Optimizer with the same strategy + window.
3. Run Bayesian search with risk-adjusted objective. Review robustness and alternatives. Use evaluation mode "Option re-rank" before trusting a result for option buying.
4. For deployment candidates, re-run as Run type **"Walk-forward (honest OOS)"** and check WF efficiency (≥0.7), OOS consistency, and param stability before believing the edge.
5. Apply best params as a Preset.

### 3. Deploy for forward testing

1. From Live Signals, click Create Deployment.
2. Choose source: a compatible Strategy Library entry or a saved Preset (the API also accepts a saved Backtest Run).
3. The Pre-flight badge highlights data realism warnings.
4. The Quality badge surfaces walk-forward, trade-count, Sharpe, and drawdown warnings.
5. Tick the acknowledgment checkbox if any warnings are present (HTTP 400 otherwise).
6. Choose mode (`signal_only` or `paper`), DTE filter, lots/capital, and `allow_overnight` if desired. In paper mode, auto-paper is on by default; optional fallback exits apply only when the strategy provides no risk hints. Kill-switch fields are also set here.
7. Save. The deployment is `ACTIVE`. The scheduler picks it up on the next minute boundary +10s during market hours.

### 4. Let deployments run

1. With auto-paper ON (paper mode), clean signals appear in the Signal Journal already ACTIVE with a `paper_trade_id`, and the trade shows in Paper Trading at a realistic premium entry. Nothing to click.
2. Signal-only deployments journal without opening a trade. Refused paper/live entries remain visible with an auditable blocker/error reason.
3. To use real money, enable the existing deployment on Live Trading, configure positive caps within account ceilings, and complete the typed confirmation. Failed or missing forward evidence additionally requires the explicit unvalidated-live consent; the evidence decision is persisted.

### 5. Review forward performance

1. Inspect signals per deployment via `GET /api/deployments/{id}/signals`.
2. Inspect paper trades via `GET /api/paper/trades`. Exit reasons tell the story: `spot_target_hit` / `spot_stop_hit` / `stop_hit` / `target_hit` / `auto_square_off_15_00_IST`.
3. Strategy Library shows deployment forward metrics: full metrics after 10 complete sessions, earlier results under an amber "low sample" badge. The same data is at `GET /api/deployments/metrics?include_ineligible=1`.

## What Is Not Done

- Phase 4b forward-testing stack is **complete** (incl. per-deployment kill switches), plus the 2026-06-11 auto-paper-trading extension.
- **Optimizer follow-ups (recommended next):** after A/B-validating the option re-rank advantage, retire the legacy spot-only evaluation path; then build a **live risk engine** (position sizing, daily loss cutoff, regime gating — applied only to live real-money; paper/forward stay tagged-not-blocked). Honest walk-forward shipped 2026-06-10; a possible WFO v2 would evaluate OOS windows option-aware.
- **Auto-paper validation:** the auto-trade path passed tests and review, but its first true end-to-end exercise is the next live market session. Forward P&L starts clean from deployments created after 2026-06-11 — trades created by the old spot-entry approve bug are still in history and should not be trusted.
- Deferred optimizer items: full per-trial option-aware evaluation + parallelism (grid/random only), loop speedups (split signal-gen from trade-sim, memoization, multi-fidelity pruners, numba JIT).
- **Data fallback:** integrate Flattrade/Fyers historical-option APIs to fill the residual ~5-8% option-data gaps Upstox lacks (TradingView is not viable for option premium).
- **Optional warehouse extras** (not blocking): option price sanity check (intrinsic floor / impossible jumps), `mongodump` backup button, OI staleness check.
- Phase 5 — probability engine (Kaplan–Meier survival), meta-model, Kelly sizing, Telegram alerts. Deferred until ≥6 months forward history exists.
- Phase 6 — swing/positional extension on 1H/1D timeframes. Not started.
- Online hosting / always-on uptime. Local PC is the runtime.
- Automated broker entry exists only for a user-confirmed `mode=live` deployment with `LIVE_AUTOPLACE_ARMED=1`; it has not yet completed the market-hours validation runbook and must not be described as capital-ready merely because it is implemented.

## Recommendations And Tips Discovered During Development

These are the practical lessons. Treat them as project conventions.

### Project-wide

- Trust data first. A polished UI is meaningless without clean candles, correct expiries, and realistic costs.
- Build small verified slices. Each slice gets backend tests, a UI surface, a live verification, and a commit before the next slice starts.
- Use stored option contract metadata for expiry resolution. Never hardcode weekday rules.
- Lot size always comes from `option_contracts.lot_size` (Upstox-supplied). It changes; do not pin it.
- Walk-forward acceptance: warn but never block. The user makes a conscious choice via the ack checkbox.
- Manual approval gate before any paper trade or recommendation. No auto-execution.
- The post-hoc volatility detector replaces an event calendar that we cannot reliably maintain.
- Paper-trade entries are option premium, never spot. The 2026-06-11 review found (and fixed) the approve flow opening trades at the spot index level while all marking used premium — any new code path that opens an option position must resolve a real premium or refuse.

### Data ingestion

- Upstox returns `400 Invalid date range` on 30-day chunks crossing Feb→Mar. Use `chunk_days=7` for spot ingest.
- Upstox historical endpoint returns empty for the same trading day. The live tick → 1m roller is what closes that gap.
- Expired option candles use `/v2/expired-instruments/historical-candle/{expired_key}/1minute/{to}/{from}`. Sending expired keys to the normal V3 endpoint returns `UDAPI1021`.
- `GLOBAL_INDICATOR|USDINR` is rejected by REST quote endpoint but works on the WebSocket. Keep market header per-tile error tolerant.
- Run option fetches month-by-month with `Sample = 1`, ATM only, both CE and PE, `Missing only` enabled, `Max contracts = 500`. Expand to OTM1/ITM1 only when the strategy needs them.

### Calendar handling

- NIFTY weekly expiry day rotated: Thu (until 2024-08) → Wed (2024-09 to 2025-03) → Tue (2025-04+).
- BANKNIFTY weekly options were discontinued in November 2024. Only monthly expiries are available since.
- SENSEX weekly Friday expiry can shift to Wednesday when Thursday is a holiday — example: 2026-01-15 BMC/Maharashtra civic elections shifted SENSEX expiry to 2026-01-14. The `SHIFTED_EXPIRY_DAYS` set in `nse_calendar.py` tracks these.
- 2025-02-01 and 2026-02-01 are Budget Saturday trading sessions.
- 2025-10-21 captured 60 candles from the limited Diwali Muhurat session. The audit recognizes this.
- 4 days have off-by-1 candle counts due to single-minute Upstox glitches. They are treated as complete.

### Forward testing

- The contract picker filters `expiry_date >= today`. Without it, live signals can resolve to long-expired contracts. The 2026-05-28 bug was caused by missing this filter; the blocker name is `option_contract_no_active_expiry`.
- Strategy source SHA is pinned on every new deployment. Pre-slice-8 deployments without a pinned SHA continue to operate (legacy compat).
- The unique partial index `signals_deployment_bar_unique` over `(deployment_id, candle_ts)` prevents duplicate journaling without affecting manual research signals.
- Time-of-day blocks (09:15–09:25 and 14:50–15:30 IST) and expiry-day cutoff (15:00 IST) are non-negotiable defaults.
- WS subscribed instruments are captured at connect time. A subscription change in code does not propagate to a running stream — restart it.

### UI

- The dark theme is the default; the white theme exists for readability. Theme tokens are CSS variables, not per-panel hex codes.
- All interactive controls carry `data-testid` attributes (kebab-case, role-based, not appearance-based).
- The market header prefers fresh ticks and falls back gracefully when a single tile fails.

## Verification

```bash
python -m pytest tests -q
cd frontend
npm run build
cd ..
docker compose up -d --build
docker compose ps
```

Health checks:

- `GET /api/health` → `{db: "ok"}`
- `GET /api/upstox/status` → connected
- `GET /api/live-candles/status` → running during market hours

## Where To Go Next

If you are a new agent picking this up:

1. Read `docs/HANDOFF.md` (the entry point — it defines the read order).
2. Read `docs/DEVELOPER_GUIDE.md` for deep onboarding (run/build/test, safety model, warehouse model, gotchas).
3. Read `docs/ARCHITECTURE.md` for the module map.
4. For what changed recently and what is in flight, read `../CHANGELOG.md` — it is the versioned record of every slice.
