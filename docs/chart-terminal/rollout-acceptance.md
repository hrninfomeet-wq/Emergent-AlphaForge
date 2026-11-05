# Local terminal rollout, November 5, 2026

Running screen: http://localhost:3000/charts. Release code: `cac2f8f`. The
frontend, backend and conditional watchdog use the approved chart upgrade plus
the previously deployed local overlays. Mongo remained running throughout.

- The user confirmed no current open Flattrade positions or pending orders.
  Before recreation, the app reported zero guarded positions and conditional
  orders, no active live deployment, and LIVE_OFFLINE. Its running token was
  reported expired at initial preflight. After the rollout, authenticated books
  returned zero net positions and only terminal order statuses. No broker order
  or broker login/logout was used for verification.
- The clean feature release passed 160 chart/conditional Python checks, 109 chart
  frontend checks, and the production build. The staged release context retained
  19 frontend runtime modules checked byte-for-byte against served source maps
  and 19 backend overlays checked against running code, including the reviewed
  executor delta. Staged regressions passed. The corrected stream configuration
  passed all 109 frontend checks and an isolated nginx forwarding check.
- Deployed API, frontend and current Compose settings were compared. Environment
  values and mount targets matched for all three app services. Final build
  fingerprint displayed in Chrome and read from the backend image:
  `7b4a3a4945db0afba1d50db62a0b3bf5392a2c8baa489543a0d296966a3ebb1a`.
  Manifest metadata records the full release revision and dirty/overlay provenance.
- In production Chrome, keeping Live Trading open filled the HTTP/1.1 pool on
  port 8001. The initial upgrade timed out loading expiries. Releasing the
  watchlist stream made the same request succeed. The final build moves only
  chart, watchlist and option-chain event streams to read-only nginx routes on
  port 3000. With both tabs and the watchlist open, REST requests and streams
  coexisted. NIFTY loaded 41 of 123 nearest-expiry strikes (Nov 9), SENSEX 41 of
  182 (Nov 5). Selecting the 24,550 NIFTY CE opened that exact premium chart.
  Its default PAPER ticket exposed market, limit and stop-limit types with
  Instant Order off. No entry, edit, cancellation or live toggle was exercised.
- NIFTY history showed 600 candles and a daily points/% change. Full screen kept
  symbol search visible. The production research panel exposed separate concept
  toggles, both supported strategy adapters and disabled true volume imbalance.
  The post-close option chain explicitly reported disconnected stream status and
  displayed REST snapshots. Real moving data was sampled earlier during the
  trading session through the committed service, as documented in option-chain
  acceptance. Intraday moving values in the newly deployed Chrome page still
  require a market session.
- A temporary watchlist instrument was added, renamed and read back at revision
  2. Its stable ID, key and edited label survived an actual backend recreation.
  Running `start-app.bat --no-browser` then passed and reused the healthy guard.
  Removing just that ID persisted at revision 3 and restored the originally
  empty list. The final backend and Mongo health checks passed, and the watchdog
  was running. The chart and its research panel can be opened without orders.

## Recovery

Standard Compose service image tags point at the verified release. Additional
images are tagged `alphaforge-chart-release-backend:cac2f8f` and
`alphaforge-chart-release-frontend:cac2f8f`. Previous running images were
retained as `alphaforge-before-chart-backend:20261105`,
`alphaforge-before-chart-frontend:20261105` and
`alphaforge-before-chart-conditional-watchdog:20261105`.
Before a rollback, repeat HANDOFF's current broker/guard/conditional order
checks. After establishing flat exposure, retag the three previous images under
the corresponding `emergent-alphaforge-<service>:latest` tags, then recreate
only backend, conditional-watchdog and frontend with Compose's `--no-build
--no-deps` flags. Preserve Mongo. Compare health, image IDs and paper state.
