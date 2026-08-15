# Mid-session backend startup — verified current behaviour

**Audited 2026-08-15 against the live database and running backend. Empirical, not inferred.**

## Ground truth: what 2026-08-14 actually left behind

The backend booted at ~09:49 IST on a live trading day. The database still shows it:

```
candles_1m, 2026-08-14   (a full spot session = 375 bars, 09:15 -> 15:29)
  NIFTY      340 bars   first 09:50:00   last 15:29:00   interior gaps: 0
  BANKNIFTY  375 bars   first 09:15:00   last 15:29:00
  SENSEX     375 bars   first 09:15:00   last 15:29:00
```

**NIFTY — the instrument that traded real money that day — is missing 09:15–09:49 (35 bars),
as a clean LEADING hole with zero interior gaps.** Nothing backfilled it. BANKNIFTY and SENSEX
show 375 because they were filled by other means (the ISO datetime format on those rows proves
they came from the historical ingest, not the live roller).

### The live-trading consequence, reproduced

Replaying `deployment_evaluator._load_recent_candles(lookback=200)` as of the first live bar:

```
evaluator lookback window at 09:50:
  oldest 2026-08-13 12:11:00
  newest 2026-08-14 09:50:00
  bars from TODAY:          1
  bars from PRIOR sessions: 199
```

The strategy formed its view from **199 previous-session bars and one bar of today**, and the
35 minutes of the actual open never existed at evaluation time. `gap_before`
(`indicators.py:55`) would not have flagged it: there is no interior gap to flag, and its
docstring states it "never flags a cross-date boundary" by design. So nothing marked the window
as anomalous. The first signals of the day fired into exactly this state.

## Requirement-by-requirement

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Detect gaps for every instrument/timeframe | **NOT MET** | `live_feed_health.py:20` `FRESH_THRESHOLD_SEC=120` measures only the AGE of the newest bar. A 10:31 boot reports `LIVE` within 2 min while 09:15–10:31 is absent. Freshness ≠ completeness; nothing counts bars or finds holes for the current day. |
| 2 | Backfill from historical API before trusting the WS | **NOT MET** | `server.py:236-244` auto-update comment: catches up "to YESTERDAY'S CLOSE … Today's intraday bars come from the live roller." `live_candle_roller.py` aggregates LIVE TICKS ONLY — it captures nothing from before it starts. The capability exists (`POST /warehouse/intraday-backfill/{instrument}`, `routers/warehouse.py:109-146` → `upstox_client.fetch_intraday_1m:385`) but has **ZERO callers** across backend/ and frontend/src. Manual-only. |
| 3 | Validate continuity/timestamps/tz/session/dupes/partials | **PARTIAL** | Idempotency is real (see #4). But there is no continuity or completeness validation of a recovered day, and **two datetime formats coexist in `candles_1m`**: roller writes `2026-08-14 09:50:00`, ingest writes `2026-08-14T09:15:00+05:30`. Counts: NIFTY 1,083 space / 159,056 ISO; BANKNIFTY 5,225 / 154,181; SENSEX 368 / 159,063. Lexically `"2026-08-14 09:50:00" < "2026-08-14T09:15:00+05:30"` is **true**, so the formats sort inverted. |
| 4 | Idempotent, chronological persistence | **MET** | `candles_1m` carries a **unique index on `(instrument, ts)`** (verified via `getIndexes()`), and every read path sorts by `ts`, not `datetime` — e.g. `deployment_evaluator._load_recent_candles:103-119`, `warehouse.py:314,403,475`. So the format split above is **latent, not currently mis-ordering**. Upserts on `ts` are idempotent. |
| 5 | Block strategy activation until data is complete and fresh | **NOT MET** | `_load_recent_candles` takes the last N bars with no completeness check. No data-completeness blocker exists among the evaluator's skip reasons. `preopen_readiness.py` computes a verdict but is PURE and fires only on an 08:45 IST timer (`runtime._preopen_readiness_loop`) — a 10:31 boot never runs it, and its verdict is advisory regardless. |

## Additional scoped finding — option bars

On 2026-08-14, **58 option contracts had ticks captured but only 3 produced 1-minute bars**
(vs 34 contracts with bars on 08-13, supplied by the 18:00 historical job). `live_candle_roller`
covers index spot only — its docstring scopes it to "NIFTY, BANKNIFTY, SENSEX".

This is **lower severity than it looks** for live trading: exits are driven by the broker
position book (`live_position_guard`), not option candles, so an option-bar gap cannot strand a
position. It does affect intraday option-premium reads and any paper/backtest parity for the day.

## Broker API limitation (documented in-source, and real)

`live_candle_roller.py:12-14`:
> "Upstox's 'historical candles' endpoint returns empty for the same trading day, so the
> bars-for-today gap was never closed."

This is why the tick→bar roller exists at all. The correct same-day source is the **separate**
Upstox V3 intraday endpoint `/v3/historical-candle/intraday/{key}/minutes/1`
(`upstox_client.fetch_intraday_1m:385`) — which the codebase already wraps and never calls
automatically. **WebSockets do not and cannot supply history**; that assumption is not made
anywhere in the current code, correctly.

## Reconciliation semantics (the hard part), verified

`warehouse.persist_candles_df:156-185` performs an **unconditional `$set` upsert keyed on
`(instrument, ts)`**. There is no merge and no "more complete bar wins" rule — **last writer
wins, unconditionally.** The roller uses the same persister (`_flush_bucket:265-292`).

Direction matters:

* **backfill overwrites a roller bar** — usually GOOD. The exchange-sourced bar is
  authoritative and replaces a bar assembled from ticks.
* **roller overwrites a backfill bar** — the dangerous direction, and it is reachable for the
  CURRENT, still-forming minute. Any backfill that fetches "today so far" receives a partial
  current minute; whichever of the two writes last wins, so a complete bar can be replaced by a
  partial one. **Mitigation for any implementation: never let backfill write the in-progress
  minute.**

### The late-tick path — latent, NOT active (I checked before claiming it)

`live_candle_roller._flush_bucket:289-291` deletes the bucket after a successful flush, and the
tick path at `:223-226` flushes the current bucket whenever `existing["ts"] != bucket_ts`. So a
tick arriving late for an already-flushed minute M would (a) prematurely flush the in-progress
M+1 bucket as a partial bar, and (b) create a degenerate M bucket seeded by that single tick
(`open=high=low=close`), which then overwrites the complete M bar. The module docstring's claim
that "the upsert guarantees idempotency" is true of the WRITE but not of the VALUE.

**However, this is not happening.** Across 20,433 real NIFTY ticks from 2026-08-14, ts went
backwards **zero** times, worst backward jump 0 ms. The reason is structural: the roller keys on
`tick.get("received_ts") or tick.get("ts")` (`:216`) and `received_ts` is present on every
persisted tick — local receive time is monotonic by construction, so exchange-side reordering
cannot reach the bucket logic. Report this as a latent hazard guarded by an undocumented
invariant, not as an active corruption.

## Rate limits and the fallback broker

* **Upstox**: documented limit 50 req/sec; the client sleeps 0.15 s between calls
  (`upstox_client.py:459-460, 596`) and retries a throttled call three times with backoff
  (`:211-240`), raising if still throttled. Comfortably under budget for a gap-fill.
* **Flattrade `TPSeries`** (decoded spec endpoint #29, `docs/Resources/flattrade-pi-api/
  endpoints/29-get-time-price-data.md`) DOES serve 1-minute candles, and takes explicit `st`/`et`
  epoch-second bounds — strictly better than Upstox's intraday endpoint for *gap-only* fetching,
  which returns the whole day. Caveats: it needs a Noren numeric `token` (not the Upstox
  instrument key), returns newest-first with a `DD/MM/CCYY hh:mm:ss` time format, and
  **`backend/app/live/flattrade_client.py` has no method for it** — the fallback is documented
  but unimplemented.

---

# Implementation (commit `46e934e`)

| Piece | File | Role |
|---|---|---|
| Completeness model | `backend/app/candle_gap.py` | PURE. `expected_minute_ts` / `missing_ranges` / `assess_completeness`. Derives from `ts` only. |
| Recovery executor | `backend/app/candle_recovery.py` | assess → fetch → persist → **re-assess**. Broker fallback via an injectable seam. |
| Fail-closed gate | `backend/app/live_data_gate.py` | Blocks the `mode → live` transition ONLY. |
| Startup | `backend/server.py` | `maybe_recover_candles(force=True, reason="startup")`, backgrounded. |
| Continuous | `runtime._live_feed_supervisor_loop` | Rate-limited retry; also covers mid-session WS dropouts. |
| Operator surface | `GET /api/live-feed/health` | `completeness` per instrument + `candle_recovery` status. |

## End-to-end evidence (real Mongo, scratch instrument, 2026-08-15)

```
STEP 1  seed the real 08-14 shape        340/375 (90.67%)  missing 09:15-09:49 (35)
STEP 2  gate BLOCKS                      ok=False  reason=incomplete_market_data
STEP 3  recovery closes the gap          status=recovered  340/375 -> 375/375
STEP 4  gate PASSES                      ok=True   reason=data_verified
STEP 5  idempotence                      already_complete  broker_calls=0  rows=375
STEP 6  in-progress minute               wrote 10:30=True   wrote 10:31=False
STEP 7  REAL warehouse (read-only)       08-13: 368/375 on ALL THREE instruments
```

## Residual risks and broker limitations no implementation removes

1. **Upstox intraday has no date parameters.** `/v3/historical-candle/intraday/{key}/minutes/1`
   returns "today" wholesale. A sub-range request is impossible, so gap-only fetching is achieved
   by the idempotent `(instrument, ts)` upsert. Cost is one call per instrument — acceptable.
2. **No same-day source for OPTION candles.** `fetch_intraday_1m` accepts only the three index
   keys; INDIAVIX and option contracts raise. On 2026-08-14, 58 option contracts had ticks but
   only 3 had bars. Live exits are unaffected (the guard marks from the **broker position book**,
   not option candles), but intraday option-premium reads and same-day paper/backtest parity are.
3. **Flattrade TPSeries is wired but not live-verified.** It serves 1-minute candles and accepts
   explicit `st`/`et` bounds — better than Upstox for gap-only fetching — but needs a Noren
   numeric token, and `flattrade_client.py` still has no method for it. The `fallback_fetch` seam
   and its tests exist; the concrete fetcher does not. **This is the main open item.**
4. **Recovery cannot invent data neither broker has.** An unresolved gap stays unresolved; the
   gate then keeps blocking new live activation, which is the intended failure mode.
5. **The `datetime` format split remains.** Latent only — every read path sorts by `ts` — but any
   NEW code that sorts or range-filters on the string will mis-order across the boundary.
6. **The late-tick hazard remains latent.** `live_candle_roller` would corrupt a bar on a
   genuinely out-of-order tick; it does not happen because the roller keys on `received_ts`
   (locally monotonic). That invariant is undocumented in the roller itself.
7. **The evaluator's new-bar trigger is hardcoded to NIFTY** (`runtime.py:962-967`). If NIFTY's
   bar fails to advance, NO deployment on ANY instrument is evaluated — including SENSEX ones.
   Out of scope here; worth its own fix.
