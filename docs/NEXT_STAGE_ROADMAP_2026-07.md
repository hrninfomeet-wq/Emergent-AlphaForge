# AlphaForge next-stage roadmap — 2026-07-31

## Decision

The next stage is **capability validation and evidence integrity**, followed by a
decision-focused Dashboard. It is worth implementing because it turns already-built
capability into trustworthy, repeatable decisions. Adding more strategy families or
stocks now is not the bottleneck and would increase multiple-testing and maintenance risk.

This roadmap keeps three programs separate:

1. **Product capability** — can the app perform the workflow correctly and clearly?
2. **Edge research** — does one frozen hypothesis survive untouched evidence after costs?
3. **Execution validation** — does paper/live behavior reconcile with the broker and safety
   model?

A pass in one program never substitutes for a pass in another. Operator-directed promotion
remains policy: a deterministic, runtime-competent strategy with finite configuration,
metrics and signals may be promoted with the required acknowledgements. Evidence warnings
remain advisory; live-consent and capital/broker safety gates remain mandatory.

## Verified snapshot

Snapshot values below were read from the running local app/Mongo on 2026-07-31. They are
diagnostic evidence, not permanent product constants.

- Backend and Mongo reported healthy; frontend, backend and Mongo containers were running
  on loopback-bound ports.
- The warehouse held 622,519 spot/VIX one-minute candles and 7,447,828 option one-minute
  candles.
- The app loaded 13 strategies with zero load failures and held 103 saved backtests plus
  23 optimization jobs.
- There were 1,525 paper trades, 23 deployments and zero live trades. Deployment state was
  2 active paper, 3 paused paper, 13 archived paper and 5 archived shadow; there was no live
  deployment.
- At the check, the market was closed, so the Upstox stream and live candle roller were
  correctly stopped. Market-hours behavior is not proved by this after-hours check.
- `GET /api/dashboard/summary` returned 62,924 bytes in 3,087 ms because its projection
  removes only top-level trade/equity fields and still returns nested
  `option_backtest.trades`/`equity_curve` data.
- The Dashboard reads `latest.metrics` directly. That is wrong for premium-native runs,
  whose authoritative result is in the dispatch-aware `option_backtest` envelope.
- The existing Warehouse page already uses `lightweight-charts` and supports stored OHLC
  selection. The Upstox manager already has a bounded tick fan-out, but the Dashboard has
  no live-bar stream and the manager does not yet expose safe dynamic subscription ownership
  for arbitrary chart instruments.
- The capability manifest truthfully enables VIX history and rejects historical Greeks,
  depth and tick order flow. Option candles contain OI, but OI is not yet exposed as a
  validated historical strategy feature.

## Ordered roadmap

### Gate A — market-hours paper/read-only validation

**Purpose:** establish that the already-built feed, candle roller, analysis panels, paper
engine and safety readbacks work together during an NSE session.

**Do:** run the existing market-hours validation in PAPER + READ-ONLY posture. Do not enable
live. Record feed state, candle rollover, analysis degradation/recovery, paper signals,
EOD behavior and reconciliation.

**Done when:** the checklist has timestamped evidence for every item, no fabricated/stale
market state is presented, paper orders remain internal, and every unresolved defect has a
reproduction.

**Stop rule:** any unexplained order-like broker activity, stale authorization, non-finite
value or reconciliation mismatch stops the session. This gate cannot be completed while the
market is closed.

### Stage 1 — close truth and optimizer integrity gaps

This is the next implementation milestone.

1. Close the eight confirmed MED optimizer findings (#14, #17, #20, #23, #25, #26, #29,
   #30) independently, with one pre-fix-red regression per finding and focused/full-suite
   checkpoints. Keep disputed #31 separate.
2. Bound the Dashboard summary projection so nested premium trades/equity arrays are not
   returned.
3. Route Latest Backtest KPIs through the same dispatch-aware result selector used by the
   trustworthy backtest/journal surfaces.
4. Remove stale phase labels, roadmap text and the yfinance-only description from the
   Dashboard/nav. Add the missing boot warning and clearer static-IP/operator guidance from
   capability Phase 2.
5. Add spot-data preflight and async backtest error parity so the main workflow fails with
   an actionable explanation.

**Done when:** every defect has mutation/red proof, `/api/dashboard/summary` omits all nested
trade/equity payloads, premium and ordinary Latest Backtest cards match their authoritative
result envelopes, frontend build/browser checks pass, and the full host plus container
route/Motor suites are green.

### Stage 2 — Dashboard v2, built as a decision surface

The Dashboard should not duplicate the Live Trading cockpit. Its job is to answer:

- Is the research/runtime data trustworthy and fresh?
- What experiment or deployment needs attention next?
- What is active, paused, stale, collecting or blocked?
- Which action continues the workflow without hiding evidence quality?

Recommended first layout:

1. **System strip:** market state, Upstox feed, Flattrade session read-only status, database,
   last candle and staleness.
2. **Research queue:** latest authoritative result, evidence label (selection/validation/
   holdout/forward), integrity warnings and the next allowed workflow action.
3. **Deployment queue:** paper/shadow/live counts, active configuration hash, forward-cohort
   progress and unresolved safety/readback items.
4. **Live market card:** a compact chart plus current analysis summary, explicitly separate
   from trade controls.
5. **Action queue:** ingest gap, rerun invalid legacy result, continue optimization, start a
   paper cohort, or open the Live cockpit.

**Done when:** every card derives from runtime state, premium-native and ordinary results
share one KPI adapter, unavailable values are explicit, and the page contains no hardcoded
project-phase truth.

### Stage 2a — transient live index chart

The chart is feasible without persisting chart-only data.

**Smallest useful slice:** NIFTY/BANKNIFTY/SENSEX selector, one-minute candles, current-day
bootstrap from Upstox Intraday Candle Data V3, live tick-to-OHLC aggregation into a bounded
60-bar in-memory ring, SSE updates, feed/stale state, and a clear `transient live view`
label. Reuse `lightweight-charts`.

**Done when:** synthetic ticks crossing minute boundaries produce exact OHLC, the SSE emits
only the selected instrument, switching selection closes the prior stream, reconnect
bootstrap does not duplicate bars, stale periods do not fabricate candles, memory remains
bounded, Warehouse behavior is unchanged, and frontend build/browser tests pass.

**Not in the first slice:** drawing-tool parity with Upstox/TradingView, arbitrary options,
multi-timeframe history, indicators, order entry or persistent chart history.

### Stage 2b — arbitrary option chart, only after 2a is stable

Add a contract selector and implement owner-scoped Upstox `sub`/`unsub` changes on the one
shared WebSocket. Reference-count subscriptions so one browser cannot remove instruments
needed by the candle roller, analysis or execution evaluator.

**Done when:** two concurrent consumers can select/switch/close independently; required
engine subscriptions remain; broker feed limits are enforced; and reconnect restores the
union of owned subscriptions.

### Stage 3 — reproducible experiment ledger

Freeze before execution:

- hypothesis ID and written market mechanism;
- strategy source hash and exact parameters;
- instrument/session universe and data-quality exclusions;
- warehouse snapshot/manifest hash;
- friction and sizing model;
- train, validation and one-use holdout boundaries;
- optimization method/trial budget and objective;
- kill criteria and allowed decision paths.

Editing any frozen field creates a new experiment ID. Results must display `selection`,
`validation`, `holdout` or `forward`; generic `OOS` is insufficient.

**Done when:** the ledger ID alone reproduces the source hash, data manifest, candidate
ranking and decision, and a used holdout fingerprint cannot be silently reused as untouched.

### Stage 4 — real LLM authoring acceptance test

Run one ordinary and one premium-trigger plain-English strategy through author → install →
backtest → optimize → paper using real model calls. Use synthetic or selection data; do not
spend a holdout merely to prove plumbing.

**Done when:** generated sources are deterministic under the canonical smoke input,
backtest/optimizer dispatch is correct, saved parameters round-trip exactly, and paper
evaluation uses the same configuration hash.

### Stage 5 — edge research, only if the parked decision is explicitly lifted

Run one pre-registered hypothesis, not a generated strategy family. Selection occurs only
on train/validation; the finalist receives one untouched holdout run.

**Kill before holdout when:** after-cost validation is non-positive, the tuned candidate
does not beat its untuned baseline, or the result depends on one narrow regime/index.

**Kill at holdout when:** net result is negative at declared friction, the lower confidence
bound is non-positive, the result collapses at the next cost tier, or performance is
concentrated in one block/index. Do not retune on that holdout.

Only a survivor starts a frozen one-lot paper cohort. This process searches for evidence;
it does not promise profitability.

### Stage 6 — live-capital validation, operational only

This remains blocked on a Flattrade-registered static IP and user-controlled live
activation. Prepare the scripted readback harness now; execute the one-lot sequence only
after paper qualification and operational readiness.

Any order-protection, reconciliation, cap, OCO or EOD failure returns the deployment to
paper and blocks scaling. A successful live cohort validates execution reliability, not
strategy edge.

## Deliberately deferred

- **More index-option strategy plugins:** the current 13 are enough to exercise the
  pipeline; another plugin does not fix the evidence bottleneck.
- **Stocks and stock/F&O screeners:** this is a new universe/data/corporate-action/fee/
  liquidity/execution program, not a selector addition. Revisit only after the index
  experiment and forward loops are reproducible.
- **Historical OI/Greeks/depth/order-flow strategies:** OI needs a quality and
  point-in-time-causality audit before exposure; the other historical surfaces were not
  recorded.
- **Short premium and defined-risk spreads:** first requires first-class multileg state,
  margin replay, recovery/atomicity and a wider multi-expiry dataset. Naked short options
  are outside the current risk posture.
- **A TradingView clone:** expensive, unrelated to edge, and already served better by
  specialist charting products. Build the compact situational-awareness chart only.

## Orchestration prompts and review gates

These prompts keep delegated work bounded. The primary reviewer owns trading-risk and
cross-component decisions.

1. **Market validation assistant:** `Execute docs/phase5b-market-validation-runbook.md in
   PAPER + READ-ONLY mode. Do not call any broker login/logout/order mutation. Return one
   evidence row per checklist item with timestamp, observed value and reproduction for any
   failure. Stop on unexplained broker activity or non-finite/reconciliation failure.`
2. **Optimizer assistant:** `Take exactly one confirmed MED finding from
   BACKTEST_INTEGRITY_AUDIT.md section 5. Reproduce it with the smallest deterministic test,
   prove the test fails on pre-fix behavior, implement the smallest fix, and run focused
   regressions. Do not touch live behavior or combine findings.`
3. **Dashboard backend assistant:** `Bound /api/dashboard/summary and introduce one shared,
   dispatch-aware KPI selector. Add ordinary and premium-native regression cases, including
   nested-array exclusion and a zero-filled spot stub. Return payload size/shape evidence.`
4. **Dashboard frontend assistant:** `Replace static phase truth with runtime-derived system,
   research and deployment status. Reuse the shared KPI selector. Preserve Live Trading as
   the order-control cockpit. Build and browser-test unavailable, stale, ordinary and
   premium-native states.`
5. **Live-chart assistant:** `Implement only the index/1m/60-bar transient slice. Bootstrap
   current-day candles, aggregate synthetic and live ticks into bounded OHLC, expose one
   selected-instrument SSE, and add cleanup/reconnect/stale tests. No order controls,
   persistence, arbitrary options or drawing tools.`
6. **Experiment-ledger assistant:** `Specify and implement immutable experiment and data
   manifests with source/config/data hashes, named evidence stages and holdout-use audit.
   Prove that changing any frozen input creates a new identity and that deterministic replay
   returns the same ranking and decision.`

**Review rule:** accept a work unit only when its binary gate is demonstrated by output or
artifact. Request revision for missing pre-fix proof, generic `OOS`, headline-only P&L,
non-runtime dashboard truth, cross-job/shared-stream ownership ambiguity, or any change that
weakens live consent and safety gates.

## Stop point

Do not implement the whole roadmap as one branch. Complete and review Gate A, then Stage 1,
then the Stage 2a chart slice. Re-evaluate priority after each green milestone. Do not start
edge research, stock expansion or live activation without a new explicit user decision.
