# Multiple indicators and drawing tools — 2026-11-02

## Completed and deployed

Confirmed: frontend-only rollout via `docker compose build frontend` and
`docker compose up -d --no-deps frontend`. Backend and Mongo container IDs/start
times stayed unchanged. No installs, broker session changes or trading actions.
Current Flattrade session is expired; book reads return 400, so no backend
recreation was attempted. Guard/conditional lists are empty, LIVE_OFFLINE with
zero live deployments armed; unreadable broker books are not proof of flatness.

Frontend captured source:
`26cf5bd797238884a64db38765ec293d8de781f5afeab64751dd1f6917795656`, matching
the host capture. Backend retains `5acdeeaff66e...`. The existing build badge
therefore reports source mismatch; this is expected after this frontend-only
change. Do not restart the trading backend solely to clear that metadata badge.

Checks completed: 22 Node model/indicator/config checks; 22 React chart checks;
CI=true build; Docker production build; `git diff --check` (existing CRLF notices).
The installed drawing library's own parser independently accepted all seven
new drawing kinds after app-schema normalization.

Chrome: added EMA50 alongside EMA20, changed its width, verified distinct lines
and legends. Added arrow, ray, ruler and vertical line; changed selected arrow
color/width and vertical line to dashed. Reload retained both EMAs, their widths/
colors, all four drawings, and the ruler's price/time measurement. No console
errors. The demo annotations remain saved on NIFTY; use Hide drawings or select
an annotation and Delete selected drawing to remove it. Prior count was zero.
New drawing defaults are per chart instance; individual drawing styles and
indicator instances persist. Saved legacy drawing schema and contract keys stay v1.

Screenshot: `C:/Users/haroo/AppData/Local/Temp/alphaforge-chart-evidence/ema-crossover-drawing-tools.jpg`.
Chart tab left open. Upstox token is also expired; saved history was used and live
data still requires operator reauthorization. Market acceptance from the initial
feature remains pending and is not claimed by these presentation-only checks.

The following source-work notes are historical.

User requested EMA20/EMA50 and multiple instances of other indicators, arrows,
rays, ruler, vertical/horizontal lines, color and width controls. Authorized
implementation, tests and browser verification; no broker/order changes.

Root owns implementation after indicator agent hit a usage limit before writing
source. No agent progress was assumed. Existing dirty worktree is preserved.

Source completed, verification in progress:
- Indicator configs retain `id` as type and add `instanceId`; distinct series ids
  and oscillator panes, independent parameters/color/width and add/remove UI.
- Versioned local browser storage for indicator settings, malformed data preserved.
- Drawing toolbar adds ray/horizontal ray, vertical line, arrow/up/down markers,
  date-and-price ruler. Existing v1 semantic drawing envelope expanded additively.
- Style controls apply to selected drawings or new drawings. New rows retain
  plugin default endpoint/ruler fields; saved legacy rows are not restyled.
- ChartCanvas applies changes to existing indicator series options (title/color/
  width), without recreating independent peers.

Critical compatibility check: Node round-trip through the installed drawing
library's real `parseDrawings` passed for all seven added kinds. Pure saved-model
tests preserve original anchors, style and derivative identity. Tests in progress;
one new EMA comparison needed floating-point tolerance (165.50000000000006 versus
the independently derived 165.5), not a calculation change.

Next: finish React/Node tests, CI build, read current broker/guard/conditional
state before any app rebuild, deploy safely, Chrome multi-EMA and drawing
style/save/reload checks. Prior rollout evidence remains in implementation-checkpoint.md.
