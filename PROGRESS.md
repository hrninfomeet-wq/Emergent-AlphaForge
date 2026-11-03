# AlphaForge chart terminal upgrade

Read this first on every resumed session, then docs/HANDOFF.md and the active item
checkpoint. This is the user-requested execution ledger; docs/AGENT_TODO.md remains
the project-wide work board. Existing completed chart feature: Lightweight Charts,
indicator instances, drawings and daily points/% header.

## Rules and current state

- Work branch: `codex/chart-terminal`. Large pre-existing dirty checkout contains
  unrelated research/trading work. Never reset/stash it or stage it wholesale.
- Never place/modify/cancel any REAL broker order during building/testing. Paper
  mode and mocked broker only. User alone tests real orders.
- Reuse Quick Trade/live-trading order service, existing kill switch and latch.
  No independent chart order engine. Paper default; explicit LIVE toggle and
  persistent indicator; confirmation before sends/edits/cancels. Instant mode
  off by default and live enable per session only.
- Retain Lightweight Charts. No backtester, optimizer, preset or deploy-validation
  changes. Upstox screenshots supply interaction/layout inspiration only.
- Finish and verify each numbered item before starting the next. Commit each
  completed item and substantial sub-step; explicitly stage only task changes.
- Current app last deployed source b570bc7c6a5f...; user confirmed flat for that
  rollout. Recheck exposure before any future backend recreation. No real-order
  testing or broker login/logout via MCP.

## Done

- Read project state and dirty-worktree inventory. PROGRESS.md did not exist.
- Defined ordered gates below; started phase 1 capability/code-path checks.

## In progress — 1. Chart trading + blotter

Critical part (cost of a wrong version: moves money): execution admission,
mode isolation, order ownership and modification/cancellation reuse. Root owns it.
First prove the existing paper/order path can support the acceptance sequence.
Then add chart UI/lines and confirm in an isolated paper fixture. Do not start 2
until the paper sequence passes.

Delegated prompts / review gate:
- `terminal_broker_capabilities`: verify broker order/modify/cancel/stream limits
  against official docs and decoded local references; write only
  docs/chart-terminal/upgrade-broker-capabilities.md. Done when every supported
  UI action has a named primary source or an explicit unknown. Root reviews.
- `terminal_order_path_audit`: trace existing conditional/manual/paper/kill/latch
  code without edits; propose the smallest reuse seam and mock tests. Done when
  the real paper execution gap and shared admission path are identified. Root
  independently reads money-path code before accepting the result.
- Root: implement shared-path extensions; isolated tests must prove no live
  broker call is reachable in paper mode and existing kill/latch is respected.
  Then wire chart ticket, draggable pending/SL/target lines and bottom blotter.

Pass/fail: paper order appears on chart and blotter; fresh marks update P&L;
dragged SL changes the same stored order; cancellation removes its active line;
blotter agrees. Unsupported market/native protection types are explicit in UI.

## Next — strictly sequential

2. Multi-chart layouts (wrong version: costs redone work). 1/2/3/4/6 chart variants,
   independent symbol/timeframe, optional crosshair/symbol sync, remembered layout.
   Pass when every variant renders without overlap and survives app restart.
3. Backend watchlist (wrong version: costs redone work). Add/remove/reorder/rename,
   LTP/% and load active chart; one list minimum. Pass when add/edit/delete/reorder
   survive restart through start-app.bat; no browser-only source of truth.
4. Option chain (wrong version: drives a trading/research decision). Reuse existing
   chain data; professional layout, expiry/ATM, CE/PE LTP/OI/change/volume/IV,
   chart or ticket action. Pass nearest NIFTY/SENSEX expiry loads and update
   handling is observed. Do not fabricate unavailable vendor fields.
5. Overlays/AI/signal preview (wrong version: drives a trading/research decision).
   Independent persisted overlay toggles; probability/zone/reasons each closed
   candle; durable predictions and no-change baseline scoring. Strategy signals
   without deployments or trading. Pass toggles/reload, logged/scored predictions
   and selected live preview signals. Disclose uncalibrated probabilities.
6. Review only (wrong version: costs redone work). Write FEATURE_PROPOSALS.md,
   ranked chart/app value/effort/risk. Pass review file exists and proposals have
   no implementation changes.

## Blockers / uncertainties

- Historical Quick Trade paper warning is superseded by isolated engine tests.
  Actual broker/live acceptance remains unverified and is operator-only.
- Chart entry inherits the existing long NIFTY/BANKNIFTY/SENSEX option scope.
  Other instruments remain chartable, with an explicit unavailable trading state.
- Flattrade live API supports limit / stop-limit, not MARKET. Paper MARKET is a
  simulation. Stop/target remain software unless existing verified broker-stop
  configuration permits a native stop. No new broker stream or order engine.
- No real-money acceptance may be claimed. Market-hours checks may require user
  follow-up; use explicit synthetic fixtures for deterministic acceptance now.

## How to resume

Read this file, current git diff and scoped checkpoint; preserve prior work.
Recover actual agent/file results rather than assuming a completion message.
Finish item 1's missing gate before item 2. Update this file before starting every
item and after substantial sub-steps; update learning_log.md each execution.

## Verification and commits

2026-11-03 checkpoint: phase 1 source exists, not deployed. Shared conditional
engine supports chart preview/arm, paper market/limit/stop-limit and amendments;
frontend ticket, confirmations, Instant opt-in, lines and blotter are integrated.
Root fixed ambiguous modify retries, late fill acknowledgement, cancel admission,
stop-limit trigger persistence and pending-entry stop bounds. These require the
additional regression pass, frontend build and isolated browser sequence before
phase 1 is complete. Focused current run: 38 pytest checks passed (conditional
adapters/lifecycle + chart lifecycle). No live broker action or service restart.

Active delegated bounded work: phase1_regressions owns only lifecycle/adapter
tests; phase1_browser_fixture owns only the synthetic acceptance server. Root
owns UI integration, independent review and final acceptance. Preserve all prior
unrelated dirty files. Initial checkpoint commit: 2c3314c.
