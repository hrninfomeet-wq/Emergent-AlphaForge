# Stage 5 UI implementation checkpoint

Implementation checked 2026-11-05 against `analysis-contract.md`; root owns backend,
probability evidence, journal durability and final browser acceptance.

## Implemented files

- `frontend/src/lib/chartAnalysisSettings.js`: independent concept toggles,
  forecast opt-in and strategy selection; missing legacy settings default off.
- `frontend/src/lib/chartAnalysisApi.js`: catalogue GET and observe POST with only
  instrument key, timeframe, strategy ID and forecast flag. No client candles.
- `frontend/src/components/charts/useChartAnalysis.js`: identity-masked catalogue
  and observe lifecycle; five-second polling after completion, one request in
  flight, abort/disposal guards, hidden-document pause, capped retry delay,
  retained explicitly stale data and unique current-strategy markers capped at 100.
- `ChartAnalysisPanel.jsx` and `chartAnalysis.css`: independent controls with
  availability reasons; forecast labelled model probability rather than accuracy;
  next-close zone/target, sample size, reasons, logging state, baseline and scores.
- `ChartAnalysisOverlays.jsx`: separate read-only zones primitive, price lines and
  installed Lightweight Charts marker plugin. Generated objects never enter user
  drawing persistence or execution handles. Layout cleanup detaches generated
  layers before passive chart destruction. Quote-only candle changes retain them.

Minimal wiring edits: `ChartTile.jsx` mounts controls next to indicators and owns
the per-tile analysis hook; `ChartCanvas.jsx` passes candle runtime to generated
layers; `chartLayouts.js` validates and persists independent per-tile settings
while reading legacy envelopes. `ChartTerminal.jsx` was not changed.

Forecast is off in every default tile. A persisted opt-in reopens the card after
reload; closing the analysis panel sets the effective forecast request to false
while preserving the user's saved opt-in. Overlay/strategy requests can continue
when their visible chart remains enabled. All polling pauses while the document
is hidden. Existing indicator and backend math is unchanged.

## Evidence

Confirmed: seven focused React/Jest suites pass, 24 tests, with this command from
`frontend` and `CI=true`:

```text
corepack yarn test --watchAll=false --runInBand --runTestsByPath src/components/charts/useChartAnalysis.test.jsx src/components/charts/ChartAnalysisPanel.test.jsx src/components/charts/ChartAnalysisOverlays.test.jsx src/lib/chartLayouts.test.js src/lib/chartAnalysisApi.test.js src/components/charts/ChartCanvas.test.jsx src/pages/ChartTerminal.test.jsx
```

New tests cover disabled observe work, single-flight polling, abort and late
response isolation, stale retention, wrong-instrument refusal, marker deduplication
and strategy reset, hidden-tab pause/resume, independent controls/unavailability,
forecast wording/missing scores, generated-layer cleanup, strict API payload and
legacy/independent settings round-trip. Existing canvas and terminal suites pass.

## Remaining acceptance / risks

Critical risk: a backend score displayed as probability or a historical pivot
displayed before it was knowable can mislead trading decisions. UI consumes the
root contract and labels hypothetical markers/research; root must verify causal
timestamps and forecast logging/scoring before exposure.

Not done here: production build, rendered primitive geometry/fullscreen browser
check, backend end-to-end forecast journal/restart, real strategy signals or data
session acceptance. Renderer only maps markers to exactly loaded candle times;
zones whose endpoints have no chart coordinates are skipped. It creates no new
chart time points or synthetic candles. Root must review rendering of these
bounded zones against actual backend payloads.

Catalogue failure requires changing instrument/timeframe to retry; observe errors
retry automatically with bounded backoff. This is stated in the error message.
The card shows aggregate scores, not a journal browser or accuracy guarantee.

Core lesson: execution isolation must be expressed in ownership, not just labels.
Confirmed approach: separate primitive/marker cleanup and strict API payload;
reuse existing tile identity and persistence validation. Dead ends avoided:
writing generated overlays as drawings, reusing mutable order price handles, or
presenting cockpit confidence as calibrated prediction accuracy.
