# Project Overview

_What AlphaForge is, what it can do, and how far each part has been proven. This page is meant to
change slowly. The current state (git, the test baseline, the live posture, the next gate) is in
[`HANDOFF.md`](HANDOFF.md) §2 and is not repeated here. Updated 2026-10-06; state facts below are
as of 2026-09-30 unless a date says otherwise._

## What AlphaForge is

**AlphaForge Trading Lab** is a local-first research, paper and live-trading lab for Indian index
options. It runs on the operator's own Windows PC in Docker Compose: a React frontend, a FastAPI
backend (every route under `/api`) and MongoDB, with every port bound to `127.0.0.1`.

- **Instruments.** Research and live work is on **NIFTY** and **SENSEX** weekly options.
  **BANKNIFTY** is supported, but its weekly options ended in November 2024, so only monthlies
  exist for it.
- **Long premium only.** Strategies buy calls or puts (premium momentum can hold both legs). The
  executor refuses any entry that is not a BUY. There is no option-selling or spread engine; the
  short-side research question is closed (see below).
- **Two brokers, two jobs.** **Upstox** supplies market data: historical candles, the live
  WebSocket and the contract universe. **Flattrade** (Noren / PiConnect OMS) is the live broker:
  static IP required, daily OAuth, limit and SL-limit orders only.
- **Not a profit machine.** No strategy in the repo has a demonstrated after-cost edge. The app is
  the research, forward-testing and execution stack for finding out, with the evidence kept honest.

## The pipeline: research → paper → live

| Stage | What happens | Page (route) |
|---|---|---|
| **1. Warehouse** | 1-minute spot candles for the indices and `INDIAVIX` (`candles_1m`), ATM-band option candles (`options_1m`) and the contract universe (`option_contracts`). Holiday-aware NSE calendar and completeness model. Data Hygiene check / fill; automatic catch-up on startup, on Upstox connect and daily at ~18:00 IST. During the session the live tick roller writes today's spot bars, and candle recovery fills spot gaps from REST (there is no same-day source for option candles). | Data Warehouse (`/warehouse`) |
| **2. Backtest** | Spot backtests, paired real-option-candle backtests and premium-native (option-only) runs. Rupee-first metrics with statutory costs and slippage, an optional exit/risk overlay (trailing stop, breakeven, per-day caps), and an advisory trust scorecard. Every run is saved to the Backtest run journal. | Backtest Lab (`/backtest`) |
| **3. Optimize** | Bayesian (Optuna TPE), Grid or Genetic (CMA-ES) search, single-shot or walk-forward (re-optimizes per train window and stitches the out-of-sample trades). Optional option-premium re-rank of the top candidates. The result is saved as a preset: strategy params plus the option execution policy. | Optimizer (`/optimizer`), Saved Presets (`/presets`) |
| **4. Deploy** | A deployment is an immutable snapshot built from a Strategy Library entry, a saved preset or a saved backtest run. Its strategy source SHA is pinned at creation. The evaluator runs it on each CLOSED 1-minute bar and journals every signal, clean or blocked, with its lifecycle. | Deploy Strategies (`/live`), Signal Journal (`/journal`) |
| **5. Paper** | The deploy wizard's default mode (the wizard offers paper or `signal_only`; live is enabled later, per deployment). Paper deployments auto-trade every clean signal at the real option premium (never the spot level). The paper exit monitor fires stops and targets on ticks, and the 15:00 IST square-off closes the rest. Forward metrics accumulate against the pre-registered promotion policy ([`forward-validation-policy.md`](forward-validation-policy.md)). `signal_only` mode journals without trading. | Paper Trading (`/paper`), Strategy Library (`/strategies`) |
| **6. Live** | Only when the operator enables it on a deployment: real Flattrade orders through one executor, within per-deployment and account caps, managed by the in-process software exit guard. The Live Deployments pane holds, tightens caps, flattens or stops each deployment. | Live Broker (`/live-trading`) |

Other pages: Dashboard (`/`), Pre-Trade Checklist (`/checklist`), Premium Momentum
(`/premium-momentum`). How to use each page: [`USER_MANUAL.md`](USER_MANUAL.md). The deployment
model in depth: [`STRATEGY_DEPLOYMENTS.md`](STRATEGY_DEPLOYMENTS.md).

## Current status: proven vs capability only

"Built and tested" means covered by the local pytest suite (there is no CI). It does not mean the
code has run against a live market, and it says nothing about profitability.

| Area | Status |
|---|---|
| **Strategy edge** | **None demonstrated.** Every edge hunt has failed a holdout or been killed earlier: the optimizer's winners, the premium-momentum family, pooled regime routing, intraday ATM option buying (no edge on either index), short-side verticals (negative in 24 of 24 cells), SENSEX VWAP (no variant survives a four-quarter split). These are closed questions; see HANDOFF §5.2 before re-running any of them. |
| **Backtest numbers** | Read [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) before trusting one. Every paired-option backtest saved before 2026-07-30 is wrong; historical option candles are research-grade, not point-in-time certified ([`option-data-provenance.md`](option-data-provenance.md)). |
| **Optimizer** | Works, but it ranks trials on a spot-points proxy. Only the top-K finalists are re-scored on real option rupees (HANDOFF §2.5 T16). Treat a winner as a hypothesis for a paper cohort, not as evidence. |
| **Warehouse and candle recovery** | Verified against the real database (HANDOFF §2.2). |
| **Paper trading** | Auto-trades in market sessions. No strategy has passed the forward-validation promotion policy. |
| **Live order path** | Has placed real orders: `live_trades` holds 13 rows from 2026-08-04 to 2026-09-16, all CLOSED (checked 2026-09-30), with fill-based P&L since 2026-08-14. |
| **Live controls built 2026-09-26 → 09-30** | Reconcile from the trade book, restart attribution, the transmit fence, tighten caps, flatten, the trading clock and the status surfaces are unit- and mutation-tested but had **not run in a market session** as of 2026-09-30. Neither had premium-momentum multi-leg on the live guard. HANDOFF §2.2 lists what has and has not run. |
| **Broker OCO backstop** | **Off by default** (`LIVE_BROKER_OCO_ENABLED`, made opt-in by `f2aa106` on 2026-09-09) after its stop leg reached the order book on 2026-09-03 and was rejected. There is **no PC-down net**. |
| **Premium momentum, SENSEX VWAP and other plugins** | Capabilities you can backtest, paper-trade and deploy; none has a demonstrated edge. |
| **Always-on hosting** | Not built. The PC is the runtime, and exits are protected only while it runs. A VPS design exists ([`durable-static-ip-deployment.md`](durable-static-ip-deployment.md)). |

The next gate, which is market-session validation of the live controls on the static IP, and its
checklist are in HANDOFF §2.3 and [`LIVE_VALIDATION_PLAN_2026-08.md`](LIVE_VALIDATION_PLAN_2026-08.md).

## The safety model, in brief

The full model is [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) §E; the non-negotiable rules are
HANDOFF §4.1–§4.2.

- **One real-order chokepoint.** Every real order goes through `live/executor`. Signal-only and
  paper deployments never touch the broker.
- **Entries need all of these:** `deployment.mode == "live"`, a connected (stored and unexpired)
  Flattrade session, a time before the 15:00 IST entry cutoff, headroom under the deployment's and
  the account's caps, and the `LIVE_AUTOPLACE_ARMED=1` environment gate. Without that gate the
  executor dry-runs and sends nothing. Enabling live on a deployment is the operator's act alone
  (`POST /deployments/{id}/live/enable`).
- **No ARM ceremony and no research-qualification gate.** Both were removed on explicit operator
  instruction (v0.56.0); do not reintroduce them. An unvalidated deployment goes live only with the
  audited unvalidated-live consent. The data-integrity gate, which blocks a NEW live activation
  when today's candles cannot be verified, is a different thing and is intentional.
- **Exits are the software guard's job.** The in-process guard applies the strategy's premium stop
  and target plus the exit-controls overlay, using the same decider as the backtest and paper
  (`exit_controls.effective_premium_stop`). It is woken by ticks and always transmits its exits for
  positions it owns. It runs only while the backend runs, and the broker OCO is off, so nothing
  protects an open position while the PC or the backend is down.
- **Holding versus demoting.** Any move out of ACTIVE (Pause on a card, Stop, a drift pause, the
  kill switch) demotes a live deployment to paper, so a later Resume can never silently re-authorize
  real money. To stop new live entries but stay live, use the hold (Pause in the Live Deployments
  pane). Caps can only be tightened in place; raising one needs Disable then re-Enable.
- **Fail closed.** The source-drift check runs on every evaluation: an unpinned deployment pauses as
  `strategy_source_never_pinned`, an unreadable strategy file as `strategy_source_unreadable` and a
  changed one as `strategy_source_drift`. A failed broker read is UNKNOWN, never flat. An exposure
  that cannot be measured blocks entries rather than reading as zero.
- **Kill switch.** It latches the account and halts the engine first, then disables every live
  deployment and flattens the whole broker book.
- **A rebuild pauses the guard.** A position open across a backend restart comes back at the
  default catastrophe stop, not its strategy levels. Check for open live positions before
  rebuilding (HANDOFF §3).
- **The Flattrade MCP shares the broker session.** Never call its `login` / `logout`, and never
  place, modify or cancel orders through it ([`flattrade-mcp-integration.md`](flattrade-mcp-integration.md)).

## Architecture

The architecture is a single local stack. The React (CRA + craco) frontend is served by nginx on
`:3000`. The FastAPI backend on `:8001` mounts its routers under `/api`, reads Upstox over REST and
the V3 WebSocket, and talks to Flattrade for execution. MongoDB holds the warehouse, every saved run
and preset, deployments, signals, and the paper and live journals. Backend code is baked into the
Docker image (only `backend/app/strategies/plugins` is bind-mounted for code), so a code change needs
`docker compose up -d --build`; a plain restart runs the old code. The module map, data flow, Mongo
collections and the live gate chain are in [`ARCHITECTURE.md`](ARCHITECTURE.md); the routes are in
[`API_REFERENCE.md`](API_REFERENCE.md).

## Where to go next

1. [`../README.md`](../README.md): quick start, test commands, documentation index.
2. [`HANDOFF.md`](HANDOFF.md): the entry point. Current state, the traps most likely to cost money,
   run and test, and conventions.
3. [`AGENT_TODO.md`](AGENT_TODO.md): the live work board.
4. [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md): deep onboarding, including the live safety model (§E).
5. [`../CHANGELOG.md`](../CHANGELOG.md) and [`../learning_log.md`](../learning_log.md): what shipped and
   what was learned, newest first.
