# Stage 5 UI/library inventory

Read-only inventory, 2026-11-05. Only this document changed. No implementation,
backtest, chart math, broker access or production mutation in this pass.

## Confirmed existing seams

| File / function | Current responsibility and constraint |
|---|---|
| `frontend/src/components/charts/ChartTile.jsx` / `ChartTile` | Owns tile data hook, local settings disclosure, indicator settings, chart props and active-tile payload. Best insertion point for independent analysis controls/hook. |
| `frontend/src/components/charts/useChartData.js` / `useChartData` | Identity-keyed bars, REST reconciliation, SSE, pagination, positions. Uses abort controllers, generation guard and immediate wrong-identity masking. Reuse its lifecycle pattern; leave market-data ownership unchanged. |
| `frontend/src/components/charts/ChartCanvas.jsx` / `ChartCanvas` | Creates chart/candle series, DrawingManager, runtime cleanup, indicator series effect, position price-line effect and crosshair syncing. Holds `{chart,candles}` as `orderRuntime`; independent visual components can receive this runtime without exposing drawing manager mutation. |
| `frontend/src/components/charts/ChartOrderLines.jsx` / `ChartOrderLines`, `orderLevels` | Dedicated ephemeral order handles with confirmation callback. Research overlays must have separate runtime/state and no `onRequestChange` connection. |
| `frontend/src/lib/chartIndicators.js` / `INDICATOR_DEFINITIONS`, `computeIndicators` | Display-only numeric studies. No FVG/order-block/liquidity/BOS/CHoCH overlay implementation found. Do not add a second structural algorithm here. |
| `frontend/src/lib/chartLayouts.js` / `defaultLayout`, `readLayout`, `saveLayout` | Per-tile instrument, timeframe and independent `configs`; localStorage layout envelope validates before write. Extend validation/defaulting for an optional independent analysis-settings field so existing layouts remain readable. |
| `frontend/src/lib/chartIndicatorConfig.js` / `readIndicatorSettings`, `saveIndicatorSettings` | Global legacy indicator seed and strict indicator-only schema. Structural overlay settings do not fit this schema; keep them separate. |
| `frontend/src/lib/chartModel.js` / `instrumentIdentity`, `drawingStorageKey`, `normalizeDrawings` | Full derivative identity and durable user drawing validation. Analysis must use full identity but never serialize generated shapes as user drawings. |
| `frontend/src/pages/ChartTerminal.jsx` / `changeTile`, `receiveData`, `validData` | Active-tile routing, layout save, fullscreen root and positions/ticket. `changeTile` syncs instrument only; independent overlay settings can remain per tile. Root can place an active-tile research card inside the fullscreen terminal. |

Confirmed against opened source and symbol searches: no `SmartMoney`/`currentSmartMoney`
frontend page or separate smart-money overlay component exists in this tree.
Existing structural computations are in `backend/app/features/structures.py`
(`compute_choch`, `compute_fvg_zones`, `compute_order_block`, swing/displacement
features). Backend owner must choose their canonical chart adapter and causal
availability semantics; UI inventory does not establish them.

## Installed chart capabilities

Confirmed against installed
`frontend/node_modules/lightweight-charts/dist/typings.d.ts`:
`createSeriesMarkers(series, markers, options)` returns a plugin with
`setMarkers` and `detach`; candle series supports `attachPrimitive` and
`detachPrimitive`. Primitive lifecycle includes `attached`, `detached`,
`updateAllViews`, `paneViews`, optional axis views and `autoscaleInfo`.

Chosen visual seam: one generated-zone primitive attached to the candle series
for bounded price/time rectangles and levels, plus one series-marker plugin for
strategy events. Reconcile data with stable IDs; detach both before chart removal.
Keep these read-only, outside DrawingManager and its persistence, and avoid
changing candle viewport/autoscale on each analysis update. Existing position
price lines remain owned by their current effect.

## Existing analysis and AI UI

Confirmed: `frontend/src/components/live/cockpit/MarketPulse.jsx` / `MarketPulse`
renders deterministic regime, trend and S/R. `MarketAnalysis.jsx` / `MarketAnalysis`
renders option analytics. `frontend/src/lib/marketAnalysisView.js` /
`analysisForInstrument` masks wrong-index payloads.
`LiveDataProvider.jsx` polls/streams `/market/analysis` for an index selection;
`frontend/src/lib/api.js` / `api.marketAnalysis` accepts an index symbol.
These are layout/provenance references, not an arbitrary-contract/timeframe
probability API. In particular, structure confidence cannot be relabeled as
probability of a future price event.

Confirmed: AI provider discovery, feasibility conversation and code/spec generation
are in `frontend/src/components/strategy/AuthoringWizard.jsx` via
`api.getAuthorProviders`, `authorConverse`, `authorFromSource`,
`authorPythonFromSource`. `StrategyLibrary.jsx` hosts strategy authoring.
No chart research-probability card or generic chart-analysis API client exists.
Do not mount strategy generation inside chart polling.

Confirmed: `frontend/src/pages/SignalJournal.jsx` reads
`api.listSignalsEnriched`; `frontend/src/lib/signalDisplay.js` distinguishes
journal lifecycle states. `LiveSignals.jsx` manages deployments. Those are
executed/journaled deployment signals; chart-generated hypothetical strategy
markers need explicit research provenance and must never imply fills.

## Proposed bounded implementation pieces

1. Root freezes a shared read-only response contract. Done when identity,
   timeframe, source/method version, evaluated-through candle, creation time,
   warnings/unavailability, zones/levels/events and research probability meaning
   are specified. Include each structure's first-known/confirmation time rather
   than relying only on its earlier pivot candle. This is the critical piece.
2. Add `frontend/src/lib/chartAnalysisApi.js` and
   `frontend/src/components/charts/useChartAnalysis.js`. Take full instrument
   identity, timeframe and enabled groups/strategy. Fetch only when enabled;
   abort/guard context changes like `useChartData`, reject identity mismatches,
   bound cadence and retain original timestamp on error. Done when race, disabled,
   stale-retention and wrong-contract tests pass. Endpoint names await root contract.
3. Add per-tile independent settings in `chartLayouts.js`, surfaced through a new
   `ChartAnalysisSettings.jsx` disclosure in `ChartTile`. Keep concept toggles,
   research-card visibility and strategy-marker visibility independently
   selectable. Persist settings only, not derived analyses. Done when legacy
   layouts load and changing one tile leaves its siblings unchanged after reload.
4. Add `ChartAnalysisOverlays.jsx` and a small primitive module receiving candle
   runtime plus validated visual data from `ChartCanvas`. Add markers using the
   installed marker plugin. Done when disable/change/unmount removes generated
   objects without removing user drawings, positions or order handles; scrolling
   and fullscreen preserve alignment.
5. Add a pure `ChartResearchCard.jsx` for the active tile inside terminal root;
   use the tile's matching analysis payload, not a second independent fetch.
   Show event definition, horizon, as-of/source, sample/calibration limits and
   unavailable state. Done when a contract switch cannot show the previous card
   and absent probabilities render unavailable, never an invented percentage.

Each piece is implementable by one UI assistant after root's contract; root reviews
its identity, cleanup and independent-setting tests before browser acceptance.
No further UI edits are authorized by this inventory.

## Risks and limits

Critical risk: an uncalibrated score labeled probability or a retrospectively
placed pivot marker shown before its confirmation can mislead a trading decision.
If wrong, apparent predictive accuracy is fabricated. Root must establish causal
timestamps and probability evidence before the UI displays those claims.

Assumption: the forthcoming backend returns bounded identity-keyed overlay data
and an explicit unavailable research result — not checked; proceeding as a design
proposal. If wrong, the hook/primitive payload adapter must change before UI work.
Browser performance and multi-tile backend cost are untested in this inventory.

Core lesson: reuse lifecycle and identity ownership while keeping generated
research separate from durable drawings and execution handles. Confirmed approach:
inspect installed chart typings rather than assume older marker APIs. Dead end to
avoid: reusing cockpit confidence as a forecast probability or treating deployment
journal signals as hypothetical chart strategy output.
