# Pre-registration — Stage 2 recorded-event replay (written 2026-10-07 ~08:00 IST, BEFORE the run)

**What runs.** The full engine (`backend/app/scalping`) with the two pre-registered specs exactly as configured in
`config.NIFTY_N1` and `config.SENSEX_S1E`, against every recorded full-depth session in `tick_archive`
(2026-09-07, -09, -15, -16, -29, 2026-10-06), through the deterministic `SimBroker`.
Runner: `backend/scripts/scalping/run_replay.py`.

**Execution scenarios** (`replay.stress_variants`):
`base` (140 ms latency = measured PlaceOrder ack p50; 50 % of displayed depth available; no passive fills),
`latency_x3` (420 ms), `thin_book` (20 % of displayed depth), `faults` (3 % rejects, 2 % indeterminate submits,
5 % dropped and 5 % duplicated order updates).

**What this sample can and cannot show.** These 6 sessions are the DISCOVERY sample of M1-M3 (NIFTY index-impulse
momentum was negative on them). Only 2 are full sessions. SENSEX has no expiry-day (DTE 0) session among them, so
S1-E cannot trade at all here — stage 2 can test its machinery only via an engine-check variant, not its edge.
A pass here is necessary, not sufficient: it only admits a spec to forward paper trading.

**PASS (N1, all required):**
1. `base`: net expectancy per trade > Rs 0 after statutory charges, over >= 40 trades;
2. >= 4 of the 6 sessions net-positive (sessions with >= 1 trade);
3. `latency_x3` and `thin_book`: net expectancy per trade > Rs 0;
4. `faults`: zero unintended shorts, zero positions open at session end, every halt explained.
**KILL (N1):** `base` net expectancy <= 0, or any safety violation in (4).
**S1-E:** reported as NOT TESTABLE on recorded data (no SENSEX 0DTE session); its engine-check variant must satisfy (4).
