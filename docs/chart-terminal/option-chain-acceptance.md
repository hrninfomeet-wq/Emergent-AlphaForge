# Option-chain acceptance checkpoint

Browser/API checks performed 2026-11-04; checkpoint integrated on 2026-11-05.
Local main advanced to 68d5acb after the gates below. No remote push or production
restart. All order testing was synthetic; no real orders were sent or changed.

## Confirmed checks

- Clean detached committed checkout: 51 selected Python tests passed. Six React
  suites passed 36 checks; the subsequent ATM-scroll regression brought the three
  changed suites to 21 passing checks. Final CI production build passed.
- Read-only existing Upstox session returned nearest NIFTY expiry 2026-11-09 with
  123 strikes and SENSEX expiry 2026-11-05 with 182 strikes. Raw responses replayed
  through the new service validated contract/strike/side/expiry identities.
- Isolated Chrome on port 8002: toolbar and watchlist entry points; NIFTY/SENSEX
  nearest synthetic expiries; expiry switch; highlighted and centered ATM; strike
  paging; null versus zero; option selection into the active candlestick chart.
- Forced synthetic LTP 321.25, OI 555 and volume 123 appeared through the real SSE
  route. SENSEX paging showed 26 strikes from 77,000 to 79,500. Manual scroll stayed
  at 1353 pixels across quote updates. Closing removed the option lease; only the
  two-symbol watchlist lease remained. Full-layout maximize retained the dialog.
- Preview: `%TEMP%/alphaforge-terminal-evidence/option-chain-checkpoint.png`.

## Limits

Real market-hours moving updates are not observed. REST retrieval time does not
prove quote freshness; spot/ATM use the REST snapshot. Per-field provenance marks
mixed REST/stream updates. IV units and previous-OI reference period are not
specified in the checked official docs, so no percent or day-OI claim is made.
No MCX chain; other catalogue-supported NSE/BSE underlyings have a generic route
but were not individually accepted against real APIs. Real trading is untested.

## Resume

Fixture: repo `.venv/Scripts/python.exe tests/manual/chart_terminal_fixture.py
--build C:/Users/haroo/AppData/Local/Temp/alphaforge-option-terminal-build-final
--watchlist-db alphaforge_chart_terminal_fixture_20261104`. Port 8002 only.
Production remains the earlier image. Next: item 5 overlays, logged/scored
research predictions and read-only strategy preview. Keep execution unreachable.
