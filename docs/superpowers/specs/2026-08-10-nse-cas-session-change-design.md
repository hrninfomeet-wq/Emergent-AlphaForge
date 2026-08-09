# NSE/BSE closing-auction session split — design

**Date:** 2026-08-10 · **Status:** implemented · **Suite:** 4445 passed, 4 xfailed

## 1. What changed in the market

SEBI's Closing Auction Session (CAS) framework took effect **2026-08-03** across NSE,
BSE and MSEI. It is the first change to the session's shape in the app's history.

| Segment | Before | From 2026-08-03 |
|---|---|---|
| Cash — F&O-eligible stocks | continuous 09:15–15:30 | continuous 09:15–**15:15**, then auction **15:15–15:35** |
| Cash — non-F&O stocks | 09:15–15:30 | unchanged |
| Equity derivatives (index + stock) | 09:15–15:30 | 09:15–**15:40** |
| Closing price, F&O stocks | VWAP 15:00–15:30 | auction equilibrium price |

CAS internals: 15:15–15:20 transition (no orders) → 15:20–15:25 order entry I
(market + limit) → 15:25–15:30 order entry II (limit only, random close 15:28–15:30)
→ 15:30–15:35 matching.

The popular framing — "trading extended to 3:40" — is misleading for this app. We
trade index options off spot signals, so our **signal source got 15 minutes shorter**
while only the tradeable instrument runs later.

## 2. The consequence that actually matters

Every NIFTY 50 / SENSEX constituent is F&O-eligible. During the auction none of them
trades, so the published index has nothing to recompute from and **freezes**.

Measured from Flattrade's own tick history for NIFTY on 2026-08-03:

```
15:14 IST  O=24577.35 H=24584.05 L=24569.00 C=24573.55  range=15.05
15:15 IST  O=24573.35 H=24573.35 L=24573.35 C=24573.35  range=0.00  <-- FROZEN
 ...        (14 consecutive identical zero-range bars)
15:28 IST  O=24573.35 H=24573.35 L=24573.35 C=24573.35  range=0.00  <-- FROZEN
15:29 IST  O=24774.30 H=24774.30 L=24774.30 C=24774.30  range=0.00  <-- +200.95 pts
```

Fed to a stateful indicator this is actively harmful: ATR and Bollinger width decay
toward zero across the flat bars, then every breakout / momentum gate fires on a
synthetic +0.82% gap. It recurs daily.

## 3. Design

### Principle: date-aware, never retroactive

Every rule keys off `CAS_EFFECTIVE_DATE = 2026-08-03`. Data before it must compute
**byte-identically** to before this change — roughly two years of warehouse history
and every backtest built on it depend on it. This is enforced by
`tests/test_cas_indicator_suppression.py::test_pre_cas_day_is_untouched`, not left to
convention.

### `backend/app/session_spec.py` — one source of truth

`session_spec(iso_date, segment) -> SessionSpec(open_min, close_min, cas_start_min,
cas_end_min, expected_candles)`.

| | pre 2026-08-03 | from 2026-08-03 |
|---|---|---|
| `spot` | 09:15–15:30, 375, no auction | 09:15–15:30, 375, auction 15:15–15:30 |
| `options` | 09:15–15:30, 375 | 09:15–**15:40**, **385** |

Spot keeps 375 bars: the index feed still publishes to 15:30, it just stops moving.
**Spot and options no longer share a day length** — that divergence is why a single
shared constant could not describe both, and why this module exists.

Muhurat and holiday handling delegate to `nse_calendar`. An unrecognised segment
falls back to `spot`, so a wrong guess narrows rather than widens a caller's window.

### Indicator suppression

`cas_window_mask(df)` mirrors the existing `gap_before_mask` and is applied at the
single `_reset_on_gap` choke point, so all ~25 indicators inherit it without touching
each compute function.

The mechanism deliberately differs from gap handling. A **gap** means bars are
*missing* → split the frame and re-warm each slice. The **auction** means bars are
*present but fake* → drop them from the input, then realign.

Realignment is type-directed, and this distinction is load-bearing:

* **numeric** columns are *state* (ema, rsi, atr, macd, supertrend) → hold the last
  real value, which is exactly "nothing happened".
* **bool / object** columns are per-bar *event* markers (`fvg`, swing points, `nr7`,
  `inside_bar`) → fill with the empty value. Forward-filling these would manufacture
  ~15 fresh signals every day.

### Deliberate asymmetry: indicators vs OHLC

Auction bars are excluded from **indicator input** but **retained in the candle
series**. The 15:29 bar carries the official closing price; dropping it would make
every daily close in the warehouse wrong. Indicators want them gone, the candle
series needs them.

### Segment awareness

`precompute_all_indicators(df, params, segment="spot")`. Every current caller feeds
the cash/index signal series, so the default is correct. Passing `"options"` disables
suppression — derivatives trade continuously through the cash auction, so their bars
are real. The parameter exists because a future option-frame caller silently freezing
indicators would be a quiet correctness bug in a money-handling path.

### Live guards

`live_position_guard`, `live_sl_monitor` and `live_exit_monitor` all bounded market
hours at 15:30. From 2026-08-03 that leaves an open option position unguarded for the
last ten minutes of its own tradeable session — precisely when the auction result is
repricing it. All three now bound on `session_spec(date, OPTIONS)`.

**No new entry gate was added.** `_is_blocked_by_window` already blocks entries from
14:50, well before the 15:15 auction, so a CAS entry block would be dead code in the
hot path.

### Coverage audit

A complete post-CAS contract-day is 385 bars. The audit compared against a flat 375,
so a day missing the entire 15:30–15:40 tape would still have reported 100% coverage —
a silent hole in a trust surface. `option_coverage` and `option_data_audit` now
resolve expected counts per date. The Mongo `complete_contracts` threshold became a
`$cond` on the date string.

Side effect, called out because it is a real behaviour change: `_weekday_counts` used
to expect 375 bars on market holidays. It now returns 0 for them.

## 4. Deliberately not done

* **`time_of_day_bucket`** — takes only `HH:MM` with no date, so a CAS bucket would
  retroactively relabel pre-2026-08-03 trades. Threading a date through every caller
  is not worth it for a window no trade lands in (entries stop at 14:50).
* **`nse_calendar.SESSION_CLOSE_MIN` / `REGULAR_SESSION_CANDLES`** — still correct for
  cash. Left alone with pointer comments.
* **`warehouse_ohlc` / `live_candle_roller` 15:30 bounds** — both are index-only paths;
  15:30 is right for spot.

## 5. Measured 2026-08-10 — verified against the live warehouse

**Upstox serves the full extended session.** 385 bars/contract, last bar 15:39, every
contract complete, on every post-CAS day for NIFTY, BANKNIFTY and SENSEX. The
15:30–15:40 tape is in our warehouse. No vendor escalation needed.

Flattrade's *historical* API does compress that window into its 15:29 bar — a
Flattrade-only artifact, irrelevant to our data path. Worth remembering if that API is
ever used for backfill.

Spot confirmed at 375 bars ending 15:29 with 14 frozen tail bars per post-CAS index
day, and both vendors agree on the values (24573.35 frozen → 24774.30 close). One
difference: our stored 15:29 bar has a **true range of 200.95** (Upstox captures the
transition inside the bar; Flattrade prints only the final value), which makes
suppression more consequential than the Flattrade sample suggested.

**INDIA VIX does not freeze** (0–1 flat bars) — it is derived from the option order
book, which keeps trading. It is correctly never masked: it lives in
`AUX_INSTRUMENT_KEYS`, is never a backtest instrument, and `indicators.py` has zero
VIX references, so no VIX-derived value passes through `_reset_on_gap`.

### Suppression verified on the real frame

`precompute_all_indicators` over stored NIFTY data for 2026-08-03 — 15 bars flagged on
03-Aug, 0 on the 30-Jul control:

| IST | ATR suppressed | ATR unsuppressed | |
|---|---|---|---|
| 15:14 | 9.689 | 9.689 | last real bar |
| 15:20 | 9.689 | 6.221 | decayed to 64% |
| 15:28 | 9.689 | 3.439 | **decayed to 35%** |
| 15:29 | 9.689 | 17.547 | **spiked to 181%** |

A **5.1× ATR swing in one minute**, all artifact.

### ★ It bleeds into the next morning

The auction window is untradeable, so distortion confined to it would be harmless.
It is not confined to it. `gap_before_mask` deliberately does not flag cross-date
boundaries — whole-frame EWM indicators are designed to carry across them — so the
poisoned state propagates into the next session's open.

Four consecutive post-CAS days (NIFTY, error = unsuppressed vs fixed):

| IST | 04-Aug | 05-Aug | 06-Aug | 07-Aug |
|---|---|---|---|---|
| 09:15 | **+57.2%** | −29.4% | **−38.7%** | −29.5% |
| 09:25 *(signal window opens)* | **+34.4%** | −19.8% | −20.0% | −15.2% |
| 09:40 | +17.5% | −9.5% | −8.6% | −7.1% |
| 10:30 | +0.6% | −0.3% | −0.4% | −0.3% |

**The real exposure was the first ~75 minutes of every trading day**, not the dead
auction window — the whole morning trend-development window the strategies trade.
Pinned by `test_artifact_does_not_bleed_into_the_next_session`, which asserts that
excluding the auction bars is exactly equivalent to their never having existed, plus
a vacuity guard proving the difference is real when the mask is off.

## 5a. End-to-end verification against the live warehouse (2026-08-10)

- `/api/options/coverage` — 375/contract on 2026-07-30..31, **385/contract from
  2026-08-03**, 100% and all-contracts-complete across NIFTY/BANKNIFTY/SENSEX.
- `/api/warehouse/audit/NIFTY` — 423 days, **0 missing, 0 hash mismatches**; every
  post-CAS day 375/375 with `hash_ok=True`.
- `market_status` serves `cas_start_ist: "15:15"` and `derivatives_close_ist: "15:40"`.
- All 5 post-CAS trading days (Aug 3–7) uniform: spot 375 with 14 frozen tail bars,
  options 385 ending 15:39. INDIA VIX 0–1 frozen bars, as expected.

**Resolved:** BANKNIFTY had no data at all for 2026-08-03 (failed ingest, not a CAS
effect). Operator re-synced 2026-08-10; now 375 spot bars / 14 frozen, 20 option
contracts at 385.

## 5b. Audit made calendar-aware (fixed on operator instruction)

`warehouse.summarize_audit_days` took a flat `expected_per_day=375` while
`warehouse.get_coverage` already resolved per-day counts, so
`/api/warehouse/audit/{instrument}` reported the 2025-10-21 Muhurat session as
**incomplete at 60/375** when 60 is exactly right.

It now resolves each date through `session_spec` (`expected_per_day=None` default;
pass an int to pin, pass `segment=OPTIONS` to audit option days at 385). Consequences
for correctness on an arbitrary user-chosen window:

* `expected_candles` is the **true sum** of per-day expectations, not
  `len(dates) × 375`. On the full NIFTY history that corrects 158,625 → **158,310**.
* `expected_per_day` is now `None` whenever a window mixes session lengths — a single
  number would be a lie there. Per-day values live in `days[].expected_candles`.
* Non-trading days inside a window report `not_a_session` and no longer count as a
  shortfall; `session_days` counts only days that expected candles, and `complete` is
  measured against that.
* Data stored on a day the exchange was closed reports `unexpected_session` — a real
  anomaly that flat-375 arithmetic silently classified as merely "incomplete".

Measured against the live warehouse: NIFTY incomplete days **5 → 4** (2025-10-21 now
reads 60/60 `ok`); the four genuine 374/375 days remain.

## 5c. Open finding — off-session rows reach the backtest, NOT fixed

`warehouse.load_candles_df` applies no session filter, while the chart's
`warehouse_ohlc._regular_session_rows` does. So stale-feed artifacts are hidden on the
chart but **fed to `precompute_all_indicators` as if they were market minutes**.

Measured across the whole spot warehouse: **143 rows out of ~632,000 (0.023%)**, none
on non-trading days. Concentrated on 2026-05-29, which carries bars out to **23:47**
(110 of the 143 across the three indices); 2026-06-01 has one at **00:09**.

Entries are safe — the 09:25–14:50 trade window blocks them — but session VWAP, daily
resampled OHLC and indicator state for those days are contaminated, and the chart and
the backtest disagree about what data exists. The fix is a session filter at
`load_candles_df` (segment-aware, so options keep their legitimate 15:30–15:40 bars),
with an explicit opt-out for repair tooling. **Left for the operator to call because it
changes historical backtest results for the affected days.**

## 6. Files

**New:** `backend/app/session_spec.py` · `backend/scripts/audit_cas_session_coverage.py` ·
`tests/test_session_spec.py` · `tests/test_cas_indicator_suppression.py` ·
`tests/test_option_coverage_cas.py` · `tests/test_live_guard_cas_window.py`

**Changed:** `indicators.py` · `indicator_groups.py` · `nse_calendar.py` ·
`option_coverage.py` · `option_data_audit.py` · `live_exit_monitor.py` ·
`live/live_sl_monitor.py` · `live/live_position_guard.py` · `CommandBar.jsx` ·
`LiveSignals.jsx` · `WarehouseChart.jsx` · `lib/time.js` · `STRATEGY_PLUGINS.md` ·
`BACKTEST_INTEGRITY_AUDIT.md` §8 · `CHANGELOG.md`
