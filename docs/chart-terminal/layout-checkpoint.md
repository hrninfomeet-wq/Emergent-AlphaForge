# Multi-chart layout checkpoint

Phase 1 isolated paper acceptance passed before this work. Phase 2 source and isolated visual/restart acceptance are complete. No broker orders or deployment were performed.

- Grouped picker offers 1, 2, 3, 4 and 6 tiles, including equal and hero variants. Six configurations, active tile and synchronization toggles persist in a validated v1 browser envelope. Invalid or future envelopes are preserved; invalid new settings cannot overwrite valid saved state.
- Each mounted tile owns chart data; only tiles omitted by the chosen layout unmount and close their data subscription; visible-layout tiles stay mounted while scrolling to prevent height jumps. Only the active tile polls positions. A single shared trading hook and panel follow the active instrument; active data is tagged by tile, instrument and timeframe before it can mark orders.
- Crosshair synchronization uses public chart methods; source time maps to a target candle's interval, and relayed events are suppressed. Symbol sync applies the active instrument immediately and on later selections.
- The focused layout, active-context, chart canvas, data and trading suites passed: 33 tests. Isolated fixture production build compiled with backend URL http://localhost:8002, output C:/Users/haroo/AppData/Local/Temp/alphaforge-terminal-build.
- Remaining gate: root to check every layout bounding box, reload persistence, crosshair behavior and active trading context in isolated browser fixture. Live market and real broker acceptance are not claimed.

2026-11-04 root acceptance: 17 variants had expected tile counts and nonempty,
nonoverlapping bounds in Chrome. Restart of isolated server plus reload retained
six-grid, selected tile/instrument, individual 1m/5m timeframes and sync flags.
Maximize retains search, layout selection and the shared order confirmation panel;
Restore and browser-denied fullscreen fallback/Esc are covered. Synthetic RELIANCE
search updated only active tile 5. Dense layouts retain scrolling on small screens.
Final build compiled; latest selected frontend run 29 passed and backend 33 passed.
No production rollout or live-market acceptance is claimed.
