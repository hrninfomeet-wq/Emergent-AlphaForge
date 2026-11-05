# AlphaForge: proposed next improvements

Review only, 2026-11-05. For a personal terminal, the first improvement I would
approve is the cash exposure disclosure. The next is the ability to inspect an
actual forecast after it settles. Neither requires new trading authority. No item
on this list has been implemented as part of this review.

Effort is relative: small means reusing existing fields on one surface; medium
adds a read model or a cross-page UI; large needs instrument/time alignment,
recovery or lifecycle tests across services. Risk describes a wrong implementation.

| Rank | Proposal and user value | Effort | Risk | Observable acceptance |
|---|---|---|---|---|
| 1 | **Show rupees at risk in the chart's BUY confirmation and Instant Order review.** The shared preview already calculates planned stop loss and full premium exposure. Show both beside quantity and price so the user sees what a missed software stop can cost. | Small | High if a planned stop is labelled a maximum loss. | For 50 units capped at ₹100 with a ₹90 stop, the same server preview renders ₹500 planned loss and ₹5,000 premium exposure plus costs in both reviews. Unknown values stay unavailable. Opening either review sends no order. |
| 2 | **Open the forecast behind a score.** From a chart's AI view, drill into original issue time, source candle, target candle, probabilities, actual close and the no-change result. Compare forward performance by contract, timeframe and model version before treating a probability as useful. | Medium | Medium: selecting only scored winners or pooling different models would mislead. | A paged, read-only history includes pending, late and scored rows with the counts explained; one settled row reproduces both absolute errors. Viewing the list creates zero predictions. |
| 3 | **Put backtest trades on the exact chart they came from.** From a saved run or journal entry, open the option premium chart or spot chart as appropriate, with non-draggable entry/exit markers. This answers “what did the strategy actually see?” without changing backtest results. | Large | High: an expired strike mapped to today's contract, or a premium drawn on a spot axis, gives a convincing but false replay. | A known archived options trade resolves exchange, underlying, expiry, strike and side, and both marker prices match the stored fills. A missing contract or candle displays “history unavailable”; no fallback to the current expiry and no order handle. |
| 4 | **Explain feed gaps and recoveries across a workspace.** Extend the existing freshness badge with last exchange tick, receipt time, last reconciled candle, source and reconnect history. Useful when six charts and an option chain disagree or Flattrade is offline. | Medium | Medium: a fresh UI heartbeat can conceal stale market data. | In an isolated replay, stop one symbol and keep another moving. Only the affected symbol ages; after reconnect, compare its bars with authoritative history and verify subscription counts return to their starting values when views close. |
| 5 | **Save named workspaces on the backend with several watchlist tabs.** Keep the current browser-local layout as an offline fallback, and let the user open the same chart arrangement in another browser profile. | Medium | Medium: conflicting edits can overwrite a layout. Restoring LIVE or Instant settings would be unsafe. | Save two distinct workspaces; open them from a clean browser profile and get the same symbols, timeframes, studies and lists. Concurrent edits raise a conflict. Executable drafts, LIVE and Instant settings are never restored. |
| 6 | **Add strategy previews only as their required data becomes available.** Start with the presently disabled option-flow or premium strategy the user most wants to study; supply its exact timestamped inputs before adding it to the picker. | Large | High: a signal inferred from spot alone can look like a valid options signal. | Run one stored session through a read-only adapter and its reference evaluator using identical as-of inputs. Signal times agree; a missing or stale required input disables preview. No execution dependency is imported. |

## Why these were chosen

- The [conditional engine](backend/app/conditional/engine.py) produces both rupee
  exposure figures. [Quick Trade](frontend/src/components/live/ConditionalOrderPanel.jsx)
  shows them, while the [chart confirmation](frontend/src/components/charts/ChartTrading.jsx)
  presents order fields and warnings. Use the same server values in both places.
- The [prediction journal](backend/app/chart_research.py) saves individual
  predictions, but the [research panel](frontend/src/components/charts/ChartAnalysisPanel.jsx)
  presents the latest forecast and aggregate scores. Forward evidence matters
  more than adding another model before the first is measured.
- [Backtest Lab](frontend/src/pages/BacktestLab.jsx) distinguishes premium-native
  results from spot trades. Preserve this distinction using [option contract
  identity](backend/app/option_candles.py) and a separate read-only marker layer
  alongside [research overlays](frontend/src/components/charts/ChartAnalysisOverlays.jsx).
- [Chart data](frontend/src/components/charts/useChartData.js) already reconciles
  history and streamed updates. The [tick replay harness](backend/app/tick_replay.py)
  already reserves synthetic identities; use an isolated instance for outage
  tests. A production trading process is not the place to inject test market data.
- [Layout settings](frontend/src/lib/chartLayouts.js) currently live in browser
  storage. The [watchlist store](backend/app/chart_watchlist.py) already handles
  revision conflicts. Extend that pattern without persisting execution permissions.
- The [strategy capability catalogue](backend/app/chart_analysis.py) identifies
  unavailable inputs. Keep “unavailable” visible until an adapter proves parity.

**Defer:** a second order engine, a chart-library replacement, and predictions
that directly trigger orders. None addresses the measured gaps above; each would
raise the cost of verifying a personal trading screen.
