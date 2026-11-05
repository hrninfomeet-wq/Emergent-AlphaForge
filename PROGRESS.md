# AlphaForge chart terminal upgrade

Read this first on every resumed session, then docs/HANDOFF.md and the active item
checkpoint. This is the user-requested execution ledger; docs/AGENT_TODO.md remains
the project-wide work board. Existing completed chart feature: Lightweight Charts,
indicator instances, drawings and daily points/% header.

**Done — 2026-11-05:** items 1–5 implemented and deployed to
[the local chart screen](http://localhost:3000/charts). Source release `cac2f8f`.
Item 6 is complete in [FEATURE_PROPOSALS.md](FEATURE_PROPOSALS.md), with six
ranked proposals for review only. See [rollout checks](docs/chart-terminal/rollout-acceptance.md)
and [research acceptance](docs/chart-terminal/analysis-acceptance.md).

**Next / how to resume:** no build item is pending. Use the normal launcher or
open the existing Charts page. During a market session compare one moving chart
and option chain in this deployed browser against your broker screen. The real
order lifecycle remains exclusively the user's test. For any future rollback
use the guarded procedure in the rollout checks. Earlier phase sections below
are the chronological record, not open work. No remote push.

**Market-session follow-up:** Nov 5, 11:06–11:07 IST. The committed option-chain
parser/stream emitted moving quotes for the nearest NIFTY and SENSEX chains from
existing WS tick-map reads; see [option-chain acceptance](docs/chart-terminal/option-chain-acceptance.md).
A NIFTY one-minute prediction was committed before the real target close and
scored from warehouse data in an isolated journal; see the research acceptance.
No new broker socket or subscription was opened for these samples.

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

## Done in source and isolated paper acceptance — 1. Chart trading + blotter

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

Current item: **4, option chain**. Items 1–3 passed source and isolated browser checks. Phase 1 acceptance passed on the isolated
8002 server in Chrome; production 3000/8001 has not been restarted or changed.

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
Continue item 4 from its scoped checkpoint; earlier items are committed and verified in isolation. Update this file before starting every
item and after substantial sub-steps; update learning_log.md each execution.

## Verification and commits

2026-11-03 checkpoint: phase 1 source exists, not deployed. Shared conditional
engine supports chart preview/arm, paper market/limit/stop-limit and amendments;
frontend ticket, confirmations, Instant opt-in, lines and blotter are integrated.
Root fixed ambiguous modify retries, late fill acknowledgement, cancel admission,
stop-limit trigger persistence and pending-entry stop bounds. These require the
additional regression pass, frontend build and isolated browser sequence, now
completed: 41 focused pytest checks, 23 React checks and production build pass.
Chrome synthetic acceptance: 65 units filled at 200, fresh mark 201 -> P&L 65;
drag 198 stop to 199 produced confirmation, server remained 198 before approval,
then same order edit_version advanced to 1 with stop 199 in chart and blotter.
A separate resting limit 199 was canceled from its line; API CANCELED/active=false,
zero active line handles and empty active blotter confirmed. No real broker
action or production service restart. Backend sub-step commit: 9894d7d.

Completed delegated work: phase1_regressions lifecycle/adapter and UI tests;
phase1_browser_fixture synthetic acceptance server. Root reviewed and corrected
the test clock: advance between cycles, never make synthetic quotes newer than
the engine timestamp. Root corrected close confirmation to SELL/estimated exit
limit and restored chart scrolling when a dragged line disappears during a fill.
Preserve unrelated dirty files. Initial checkpoint commit: 2c3314c.

Resume fixture: `.venv/Scripts/python tests/manual/chart_terminal_fixture.py
--build C:/Users/haroo/AppData/Local/Temp/alphaforge-terminal-build` on 8002.
Frontend fixture build uses only per-command REACT_APP_BACKEND_URL and BUILD_PATH.
Fixture is memory-only, synthetic quotes, forbidden live gateway. Normal app
rollout and market-hours acceptance remain separate.

### 2026-11-04 resume — item 2 final gate

Recovered source and checkpoints; phase 1 commits remain 9894d7d/398a955.
Item 2 grouped picker has 17 variants. Previous Chrome pass found no overlapping
or empty tile slots in any variant; 1m crosshair at 18:54 mapped to the containing
5m candle at 18:50, and symbol sync/active chart changes were observed. Shared SSE
test run on resume: 28 Python tests passed; five focused frontend suites: 26 passed.
Root removed offscreen unmounting after observing placeholder height shrinkage;
only tiles omitted by the selected layout unmount. Compact multi-chart styling
added. User additionally requested full-layout maximize with stock search kept
available: implemented on the entire terminal root, including confirmation panel,
with a focused fullscreen test. Final fixture build/browser and restart persistence
check pending. Production services remain unchanged. Later items not started.

Item 2 final review: root Chrome loop checked all 17 picker variants, expected
counts and nonempty/nonoverlapping bounds. Maximized search changed only tile 5
with symbol sync off; other symbols/timeframes stayed unchanged. Added fallback
window maximize for denied Fullscreen API, Esc/Restore, compact tools disclosure,
and kept the shared confirmation panel in the maximized root. Individual canvas
fullscreen is suppressed in the terminal to prevent hidden trade confirmations.
Review reproduced a failed channel stranded behind healthy SSE siblings; added
per-channel capped retry, recovery resync and cancellation tests. Root rerun:
33 selected Python tests and 29 selected React tests passed. Fixed synchronized
cursor clearing on mouse leave with a regression. Final fixture rebuild/restart
check still pending; do not begin item 3 until it passes.

### Item 2 complete / item 3 starting — 2026-11-04

Final isolated production build compiled. After stopping and starting the fixture
server, Chrome reload retained six-grid, active tile 5, chart 2 at 5m, other charts
at 1m, independent selected instrument and sync flags. Full-layout maximize and
Restore were observed. A synthetic RELIANCE search changed active tile 5 only;
other five symbols remained BANKNIFTY. Six canvases rendered without overlap.
On small displays dense row/column variants scroll to preserve readable charts.
The Fullscreen API can be rejected by browser focus/permissions; the tested
window-maximize fallback and Esc remain available. No production restart.

Item 3 plan (cost: redone work):
1. Backend persisted watchlist with validated instrument identity and revision
   checks; pass add/rename/reorder/delete reload from a fresh store instance.
2. Left panel and cached fresh quote polling using existing feed ownership; pass
   edits save on server, stale/offline values are marked and click loads active tile.
3. Root integration/browser acceptance plus isolated persistent Mongo restart;
   production start-app.bat acceptance deferred to final rollout exposure check.
Delegation: backend agent owns service/router/tests; UI agent owns component/API/
unit tests. Root owns integration, fixture, review, persistence and commits.
Do not start option-chain work before this gate. No broker writes.

Item 3 checkpoint: commit 0548405 contains completed layouts. Backend/UI/fixture
agents are implementing isolated pieces; root integrated the future left panel
and scoped watchlist router. Backend uses one batch quote lease on existing
Upstox manager (preserves execution/chart ownership and tape limits), revisioned
Mongo preferences; UI defaults blank aliases. Root review caught alias contract
mismatch, quote-map O(n²) copying and overly frequent revision reads; agent is
correcting these before acceptance. Fixture uses only a dedicated test database,
never production alphaforge, for actual restart persistence. No production restart.

Item 3 backend acceptance: root reran 41 selected Python tests (watchlist/store,
shared manager leases, multistream and chart routes), all passed. Review fixed
initially unavailable symbols remaining stuck; periodic re-resolution covers both
mixed and all-unavailable lists, releases lease before reconnect. UI integration
17 selected tests passed; UI action/boundary checks and Chrome restart gate remain.
Isolated fixture supports explicit --watchlist-db
alphaforge_chart_terminal_fixture_20261104; default remains memory. Production
DB name is rejected, and app.runtime is never imported. Fresh Mongo client/store
read matched the seeded empty document. User database was not touched.

### Item 3 completed — 2026-11-04

Root recovered UI-agent files after its usage limit. CI build compiled; 20 selected
frontend checks passed. Chrome add/rename/reorder/delete/load-active-chart passed.
Actual fixture process stop/start with dedicated Mongo retained revision 7,
Market focus, ordered labels [NIFTY, Reliance focus], and identical response digest.
See docs/chart-terminal/watchlist-ui-checkpoint.md for evidence and screenshot.
Production start-app.bat rollout/real-session checks remain pending, not claimed.

Next item 4 plan: verify existing option data/official fields and expiry handling;
reuse read-only source with truthful absent values, then build chain panel with
expiry/ATM/CE-PE data and chart or ticket handoff. Pass mocked NIFTY/SENSEX nearest
expiry plus live-update reconciliation, then read-only real API if session allows.
Do not start item 5 until item 4 checks pass. No real orders, no second order path.

Item 4 checkpoint: stage3 UI commit eda1107. Official option-chain research saved
in docs/chart-terminal/option-chain-research.md; root independently checked REST
field docs. Exchange timestamp absent, IVunit andpreviousOIbaseline undocumented:
showreceivedtime andrawvendorIV; do not mergeWSIVwithRESTIV. Backendagent builds
validated cachedchain +bounded existingquote leases; UIagent buildsexpiry/ATM/
CE-PEdialog; root integratesactivechart handoff and will ownfixture/realreadchecks.
No order placement from chain. No production restart. Work not yet verified.

### Local main integration checkpoint — 2026-11-04

User requested integration after implementation and testing. Verified exact commit
eda1107 in detached clean worktree at %TEMP%/alphaforge-main-eda1107: 41 selected
Python tests, 20 selected React tests, and CI production build passed. Build only
reports existing bundle size/caniuse warnings. Advanced local main from 24e78a1
to eda1107 after checking ancestry and that main is not checked out elsewhere.
origin/main remains ff9cbe3; no push or production rollout. Dirty feature workspace
and unrelated changes preserved. This integrates completed items 1–3 only.

Item 4 source recovered; backend and UI review/test agents are finishing their
isolated files. Root owns real read-only data checks, fixture acceptance and final
integration. Items 5–6 are not started. Next gate: option-chain identity/freshness,
mock tests, read-only NIFTY/SENSEX coverage and isolated browser handoff. Do not
mark live market updates accepted outside a live market observation.

### Item 4 implemented and integrated — 2026-11-05

Local main now 68d5acb (items 1–4); remote origin/main unchanged. Option-chain
source, committed-tree tests/build, real read-only Upstox response replay and
isolated Chrome acceptance passed. See docs/chart-terminal/option-chain-acceptance.md
for exact evidence and remaining market-hours/rollout limits. Synthetic browser
checks include quote changes, expiry/window selection, ATM centering without
scroll jumps, active-chart handoff and subscription cleanup. No production restart.

Item 5 plan (critical: analysis must not reach execution; wrong wiring moves money):
1. Inventory existing concept/strategy evaluators and define a read-only adapter.
   Pass when every preview entry point has no order/deployment action dependency.
2. Independent persisted overlay controls; pass separate toggles/reload and chart
   render checks without changing existing backtester or strategy logic.
3. Candle-close probabilistic research view with durable predictions and later
   no-change-baseline scoring; pass no look-ahead, deduplication and scoring tests.
4. Strategy selection and chart markers; pass known synthetic signal and no-order
   checks plus isolated browser acceptance. Then integrate tested feature to main.
Item 6 follows: ranked FEATURE_PROPOSALS.md only, with value/effort/risk.

Item 5 backend sub-step: pure concept and reviewed-strategy analysis, isolated
research API and immutable prediction journal implemented. Root review and an
independent reproduction exposed cached unfinished candles becoming final solely
by wall time; history now records request-start time, and research requires that
watermark to prove candle completion. Duplicate/late database acknowledgements
cannot certify a late forecast; Mongo server time decides durable timeliness.
Session bounds reuse the existing segment/calendar rules. Targeted unit checks
passed. UI and isolated virtual-session fixture in progress. Do not integrate
item 5 into main until final build, durable Mongo and browser checks pass.

Item 5 progress: a35c0bd is the backend sub-step. Root independently tested a
real write/duplicate/settlement in the allowlisted fixture Mongo; a fresh client
read the scored original. The synthetic example had model error 0.112 points
versus 0.000 for no change; this is a scoring check, not model validation.
Added bounded data-only settlement for pending predictions after closing a tab,
segment-correct derivatives, multi-timeframe structural overlays and independent
BOS/CHoCH. No change to backtester, optimizer, presets or deploy validation.
Recovered UI/fixture agent files after usage exhaustion. Isolated final browser
and clean build are next; main remains at completed item 4 until acceptance.
