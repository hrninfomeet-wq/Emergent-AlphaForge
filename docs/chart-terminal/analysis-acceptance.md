# Chart research acceptance — 2026-11-05

**Decision:** accept item 5 into local main for paper use and forward research.
This verifies the terminal workflow, not the predictive value of the model or a
live trading deployment. No production order, login, permission or rollout was
part of this acceptance.

## What the user can do

Open **Research overlays & forecast** on a chart. Support/resistance, fair value
gaps, order blocks, breaks of structure, changes of character, displacement,
rejection candles and premium/discount bands have separate switches. Settings
belong to the tile and survive a reload. True volume imbalance is disabled:
OHLCV cannot tell which trades lifted the offer or hit the bid.

The next-close forecast shows a direction distribution, price zone, target time,
number of samples and reasons. AlphaForge journals each displayed forecast
before its horizon closes. The chart compares its later error with the original
close, and reports probability scores and interval coverage. It uses historical
candle analogues, not a calibrated probability of profit. The reviewed Fibonacci
Pullback and SMC Liquidity Sweep + FVG adapters support one-minute NIFTY,
BANKNIFTY and SENSEX index charts. Strategies requiring the option-flow warehouse
or premium session engine are disabled with the missing input named.

## Evidence

- Clean detached checkout at `2b39f84`: all 160 selected backend chart and
  conditional-order checks passed; all 109 frontend checks in 18 chart suites
  passed. CI production build exited zero. Tests include the paper order path,
  contract identity, cache/fetch crossing a candle close, competing journal writes,
  target expiry, segment session boundaries, stale responses and strategy isolation.
- Chrome, isolated port 8002, actual compiled UI, FastAPI routes, real local
  Mongo restricted to the fixture database, and synthetic virtual exchange time.
  SENSEX one-minute: enable support/resistance, premium/discount, SMC preview
  and forecasting. Reload retained selections; order blocks stayed off. Turning
  support/resistance off left premium/discount on.
- Before the fixture advanced: zero scored and one pending SENSEX prediction.
  Advancing one candle produced a CE preview with “sweep low+reclaim” and
  displacement, one chart marker, a new logged forecast, one scored and one
  pending. The original forecast's error was 0.112 points vs 0.000 for no change.
  The next card showed 77 historical transitions. This is synthetic evidence.
- Closing the research panel stopped issuing forecasts. After a second virtual
  candle, the worker settled both remaining SENSEX/NIFTY fixture records without
  a forecasting client: collection totals moved from two scored/two pending to
  four scored/zero pending. The journal was also read from a fresh Mongo client.
- Stage 5 independent review's three reproductions are addressed: request-start
  watermark prevents cached partial candles from settling predictions; server-side
  insertion time and immutable duplicate handling prevent late acknowledgments
  from certifying predictions; session boundaries follow the instrument segment.
  See `tests/test_chart_research.py` and `tests/test_chart_analysis.py`.

## Reproduce and outstanding checks

Build the frontend with `CI=true`, `REACT_APP_BACKEND_URL=http://localhost:8002`
and a separate `BUILD_PATH`. Run `tests/manual/chart_terminal_fixture.py --build
<build path> --watchlist-db alphaforge_chart_terminal_fixture_20261104 --research`
with the repo virtualenv. Use **only** `POST /api/fixture/research/advance` on
port 8002 to close a virtual candle. The fixture cannot route to a live broker.

Real market-hours feed behavior, forecast calibration and broker acceptance have
not been established. If final history is missing a target candle it stays pending;
no invented outcome enters the score. The production service on port 8001 still
lacks the analysis catalogue, and an expired broker session cannot establish flat
exposure for a backend restart. Source integration does not restart the guard.
