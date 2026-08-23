# Intraday option-buying candidates — audit, constraints and two frozen specifications

**Date:** 2026-08-22 · **Scope:** NIFTY 50 and SENSEX only · **Status:** pre-registration.
No backtest has been run for this document. Nothing here is evidence of an edge.

Companion to [`OPTION_BUYING_MICROSTRUCTURE_2026-08.md`](OPTION_BUYING_MICROSTRUCTURE_2026-08.md)
(what the data says the buyer's payoff is), [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md)
(can a number be trusted) and [`forward-validation-policy.md`](forward-validation-policy.md)
(what promotion requires).

---

## 0. What this document is, and the one thing to read first

The request was to audit the application, verify current exchange and broker constraints,
define the 1-minute-versus-live gap, and design two intraday **option-buying** candidates —
one short-horizon scalp, one same-day intraday including 0DTE/1DTE variants.

The audit is complete and verified against source. The constraints are verified against
source plus current external sources. The two specifications are complete and frozen, and
candidate B ships as a registered research-only plugin
(`expiry_regime_trend_continuation`) so that its spec is actually falsifiable inside
Backtest Lab and the Optimizer. Registered is not validated: it has never been run.

**The results section is empty on purpose, and you should read §6 before §4.** This
repository has already measured the thing these strategies would try to exploit, and the
measurement says the unconditioned intraday option buyer loses before costs. Three
campaigns have failed a holdout. Both candidates below are therefore written as
*falsification tests* with pre-registered kill criteria, not as proposals expected to work.
The honest prior is that both fail.

Two further facts shape everything:

1. **Candidate A cannot be implemented today.** Its premise — that option-side volume and
   open interest carry directional information the 25-indicator spot library cannot see —
   requires option-side data to reach a strategy's `evaluate()`. It does not. The plumbing
   change is specified in §7.1 and is the single highest-value product change identified by
   this audit.
2. **A genuinely untouched holdout is now scarce.** Prior campaigns have consumed
   2025-01 → 2025-12 and 2026-01-01 → 2026-07-10. Only roughly 30 sessions remain never
   optimised against. That is below the 60-session promotion minimum, and it bounds what
   any campaign started today can prove. See §5.3.

---

## 1. Verified capability map

Measured from source at commit `6e6e1cc`. Sizes: backend 62,333 LOC, frontend 29,635 LOC,
tests 75,749 LOC across 354 test files.

### 1.1 Frontend pages (`frontend/src/pages/`)

| Page | LOC | What it does | Relevance here |
|---|---:|---|---|
| `BacktestLab.jsx` | 3,229 | Run/inspect backtests; spot + paired-option modes; Trades/KPI/Journal panes; read-only coverage Check → Ingest → recheck | Primary research surface |
| `Optimizer.jsx` | 2,560 | TPE / Grid / CMA-ES sweeps, walk-forward, re-rank, winners → preset | Parameter search |
| `LiveSignals.jsx` | 1,508 | Signal feed + cockpit | Forward observation |
| `PremiumMomentum.jsx` | 684 | Premium-native strategy surface (separate result envelope) | Not used by these candidates |
| `PaperTrading.jsx` | 632 | Paper positions, caps editor, square-off | Forward evidence |
| `SavedPresets.jsx` | 562 | Saved parameter sets → deployment | Freeze mechanism |
| `DataWarehouse.jsx` | 536 | Ingest/coverage/gap repair | Data validation |
| `SignalJournal.jsx` | 491 | Per-signal audit trail | Diagnosis |
| `StrategyLibrary.jsx` | 360 | Registry browser, lifecycle | Registration |
| `PreTradeChecklist.jsx` | 191 | Pre-trade profile filters | Gate config |
| `Dashboard.jsx` | 165 | Summary | — |
| `LiveTrading.jsx` | 23 | Thin shell over the live cockpit components | Deployment |

### 1.2 Backend routers (`backend/app/routers/`)

`live_broker.py` (2,861) · `deployments.py` (1,655) · `warehouse.py` (1,244) ·
`research.py` (884) · `strategies_admin.py` (577) · `journals.py` (567) · `broker.py` (405) ·
`premium_momentum_routes.py` (316). Every route is under `/api`.

### 1.3 Data warehouse — Mongo collections actually in use

| Collection | Role | Verified state |
|---|---|---|
| `candles_1m` | 1-minute spot/index OHLCV | The signal source for every strategy |
| `options_1m` | 1-minute option OHLCV **+ `volume` + `oi`** | `oi` is stored and read by **nothing** in the signal path |
| `option_contracts` | Contract master (strike, side, expiry, lot_size, `contract_key`) | 63,868 docs at last audit |
| `ticks` | Live tick capture | **30-day TTL** (`db.py:90`) — not a long-horizon replay source |
| `chain_snapshots` | Option-chain snapshots | Index exists, **no writer**. Count 0. |
| `backtest_runs`, `optimization_jobs`, `presets` | Research artefacts | — |
| `strategy_deployments`, `paper_trades`, `live_trades`, `signals`, `live_orders` | Execution | — |
| `premium_locks`, `integrity_hashes`, `pretrade_profiles` | Safety/provenance | — |

### 1.4 Indicator library — complete enumeration

`backend/app/indicators.py` (512 LOC), assembled by `precompute_all_indicators` and
memoised group-wise by `indicator_groups.py`. Every column a strategy can read:

**Trend/momentum:** `ema9`, `ema21`, `ema50`, `rsi`, `macd_line`, `macd_signal`, `macd_hist`,
`supertrend`, `st_dir`, `adx`
**Volatility/range:** `atr`, `atr_avg`, `chop`, `squeeze_on`, `squeeze_fire`, `sqz_mom`,
`nr7`, Bollinger/Keltner internals
**Session-anchored:** `vwap`, `vwap_sigma`, `vwap_u1`, `vwap_u2`, `vwap_l1`, `vwap_l2`,
`session_date`, `ist_time`
**Structure:** `is_swing_high`, `is_swing_low`, `fvg`, CPR levels (`cpr.py`), opening-range
width, Fibonacci helpers
**Adaptive:** `vel_z`, `accel_z`, `vr`, `regime_score`, `tod_tradeable`
**Candle geometry:** body/wick z-scores (`candle_geometry`)
**Hygiene:** `gap_before`, CAS mask

Optimizer-tunable indicator dimensions are limited to the ten in
`indicator_param_catalog.py`: `ema_fast`, `ema_slow`, `rsi_length`, `macd_fast`,
`macd_slow`, `macd_signal`, `atr_length`, `adx_length`, `chop_length`, `swing_lookback`.

> **The gap that matters for this brief.** Every indicator above is computed from **spot**
> OHLCV. There is **no** open-interest, implied-volatility, put-call-ratio, option-volume,
> bid-ask-spread, skew or greek feature anywhere in the strategy-facing set. The user's
> hypothesis about "option-chain structure, volume/OI" is currently **untestable in a
> strategy**, even though `options_1m.oi` exists in the warehouse. Verified by grep across
> `indicators.py`, `indicator_groups.py` and `features/`.

### 1.5 Strategy registry — 17 registered at audit time, all loading clean

*(18 after this change adds `expiry_regime_trend_continuation`, §4.2. The table below is the
registry as it stood when audited, which is the baseline the rest of this document reasons
against.)*

| id | ver | live lookback | instruments |
|---|---|---|---|
| `confluence_scalper` (built-in) | 1.0.0 | 400 | all |
| `adaptive_regime_scalper` | 1.0.0 | 400 | all |
| `algotest_option_buy_nifty` | 1.0.0 | — | NIFTY/BANKNIFTY/SENSEX |
| `atr_sigma_router` | 1.0.0 | 400 | NIFTY/BANKNIFTY/SENSEX |
| `dte_opening_shock_breakout` | 1.0.0 | 400 | NIFTY/SENSEX |
| `explosive_reversal`, `explosive_reversal_atr` | 1.0.0 | — | all |
| `fibonacci_pullback` | 1.0.0 | — | all |
| `gap_fade` | 1.0.0 | 400 | all |
| `opening_range_breakout`, `opening_range_regime_router` | 1.0.0 | 400 | all |
| `premium_momentum` (premium-native) | 1.0.0 | — | NIFTY only |
| `sensex_explosive_reversal` | 1.0.0 | — | SENSEX |
| `smc_liquidity_sweep_fvg` | 1.1.0 | — | all |
| `squeeze_expansion_breakout` | 1.0.0 | 400 | all |
| `vwap_mean_reversion`, `vwap_pullback_scalp` | 1.0.0 | 400 | all |

Nine of these carry `live_lookback_bars = 400` as the fix for the session-anchor defect
closed in `fc424a1`/`1cc6ce2`. **Any new strategy reading a session-anchored value must
declare 400.** At the 200-bar default the window stops reaching 09:15 after 12:34 and VWAP
drifted a measured 2.12 ATR by 14:49 — larger than a 1-ATR entry band, inverting signals for
~40% of the session.

Coverage note: `smc_liquidity_sweep_fvg` already implements sweep-of-extreme plus FVG
retrace, and `vwap_pullback_scalp` already implements VWAP-trend pullback scalping. Neither
candidate below duplicates them.

### 1.6 Backtest, optimizer, paper and live

**Backtest** (`backtest.py`, `option_backtest.py`, `exit_engine.py`) — the fill model is
genuinely conservative and I could not fault it:
- stop-first on a same-bar stop/target tie (`intrabar_exit(stop_first=True)`);
- a long stop that gaps below fills at the **bar open**, not the stop level
  (`stop_fill_price`);
- trailing ratchets off the running max **through the prior bar**, so the bar printing the
  peak cannot self-stop (look-ahead safe, `_walk_option_exit`);
- exit levels are the resting order's level, not the bar close.

**Costs** are two separable layers, applied as EITHER/OR to avoid double-counting:
`app.slippage` (per-side points by moneyness bucket, 2× in the expiry tail from 15:00) OR
`app.option_costs.spread_pct_of_premium` (%-of-premium with a points floor), plus statutory
`round_trip_charges`. `app.live_friction.fill_premium` is the single shared implementation
so sim and paper cannot disagree about a fill price.

**Optimizer** (`optimizer.py`) — Optuna TPE, Grid and CMA-ES; objectives `sharpe`,
`profit_factor`, `total_pnl_pts`, `net_pnl_inr`, `win_rate`, `neg_max_dd`; `min_trades` and
`min_direction_share` guards; walk-forward via `walkforward.py`/`wfo.py` with rolling
train/test folds and signed premium decay.

**Paper** (`paper_trading.py`, `paper_squareoff.py`, `paper_capital.py`) — scheduled 15:00
IST square-off, account-wide ₹2,00,000 capital gate, per-deployment cost schedule honoured,
`execution_realized_pnl` recorded top-of-book after charges.

**Live** (`deployment_evaluator.py`, `live/`) — see §3. Not used by this work.

### 1.7 Saved presets

`db.presets` + `SavedPresets.jsx`. A preset freezes strategy id, version, params, entry
window, option policy (moneyness, DTE filter, lots), cost config and exit/risk controls.
`forward_config_hash` covers strategy source + params + option policy + pre-trade profile +
sizing + friction + paper exit/risk controls; promotion recomputes it and requires an exact
match. This is the mechanism these specifications freeze into.

---

## 2. Verified market, contract and broker constraints

### 2.1 Expiry schedule — verified externally, and it has rotated twice

| | 2024Q4 | 2025Q1–Q2 | 2025Q3 | 2025Q4 → 2026 |
|---|---|---|---|---|
| NIFTY | Thu | Thu | Thu → **Tue** | **Tue** |
| SENSEX | Fri | **Tue** | Tue → **Thu** | **Thu** |

Current schedule confirmed against external sources: NIFTY weekly expiry moved to **Tuesday**
effective 2025-09-01 following SEBI's October 2024 circular; SENSEX weekly expiry is
**Thursday** effective 2025-09-04. Holiday rule: expiry shifts to the **previous** working
day. BANKNIFTY weeklies were discontinued (monthly only, last Tuesday) — irrelevant here but
it explains why the repo's BANKNIFTY paths differ.

The repo's own derivation from stored `expiry_date` values matches. A weekday-based DTE rule
reproduces the real expiry on only **233/424 NIFTY** and **238/426 SENSEX** sessions.

> **Binding rule for both specifications:** neither strategy derives DTE. DTE targeting
> belongs to the run's `dte_filter` and the deployment's option policy, which read real
> expiry metadata via `app/dte.py::compute_dte` (trading-day distance to the nearest
> upcoming stored expiry, holiday-aware through `nse_calendar`). Verified correct.

### 2.2 Contract specifications

| | NIFTY | SENSEX |
|---|---|---|
| Lot size | **65** (revised from 75, effective Jan 2026) | **20** |
| Strike step | 50 | 100 |
| Segment | NSE / NFO | BSE / BFO |
| Exchange txn rate | 0.0003503 | 0.000325 |
| Tick size | 0.05 | 0.05 |

Repo's `UNDERLYING_META` matches on all of the above and correctly marks `lot_size` as a
**last-resort fallback** — `resolve_lot_size()` reads the contract's own `lot_size` and warns
when it falls back. This is right: BANKNIFTY's stored lots really did change 35 → 30 mid-2025
while the static map still said 35, a 16.7% error in every rupee figure that trusted it.

### 2.3 Session times — the Closing Auction Session changed them three weeks ago

Verified externally and in `session_spec.py`. Effective **2026-08-03**:

- **Cash/index:** continuous trading ends **15:15**; closing auction 15:15 → 15:35.
  Because every NIFTY 50 / SENSEX constituent is F&O-eligible, none trades during the
  auction, so **index candles freeze from 15:15** (zero-range, identical OHLC) and then print
  the auction result as one large jump bar. Measured on NIFTY 2026-08-03: 14 flat bars at
  24573.35, then 24774.30 — a **+200.95 point (+0.82%) single-bar gap**.
- **Equity derivatives:** trade on to **15:40**.

So spot is 375 stored bars and options are 385 from that date: the two series no longer share
a day length. `session_spec(date, segment)` is the only correct source for session bounds and
`cas_window_mask` suppresses indicator computation across the frozen window.

*Minor discrepancy to confirm:* the repo sets `CAS_END_MIN = 15:30` while external sources
put the auction end at 15:35. Stored spot candles end at 15:29 regardless, so this does not
affect any current computation, but it should be reconciled before anything depends on the
auction-end boundary itself.

> **Consequence for both specifications:** any strategy holding into the close must square
> off before 15:15, not 15:30. A frozen index print is not a price you can exit against, and
> the auction jump bar is unhedgeable. Both specs use a 14:55 hard square-off.

### 2.4 Broker capability — Flattrade (Noren / PiConnect)

| Constraint | Value | Source |
|---|---|---|
| Allowed order types | **`LMT` and `SL-LMT` only** | `live/broker_protocol.py:52` — `ALLOWED_PRCTYP`; market/CO/BO/IOC blocked |
| SL-MKT | RMS-blocked for index options on both exchanges | `live/flattrade_symbol.py:79` |
| Entry pricing | Marketable limit, default **0.5% cross buffer** | `live/executor.py:229` `buffer_pct` |
| Rate budget | <10 orders/sec free tier; above needs the paid registered-algo tier (₹5,000+GST per exchange) — **never move onto it** | decoded API docs; `AGENTS.md` |
| Static IP | Required, registered per API key | decoded API docs |
| API key | One per account, one redirect URI, last-login-wins, shared with the Flattrade MCP | `docs/flattrade-mcp-integration.md` |
| Brokerage | ₹0 on F&O | `option_costs.DEFAULT_BROKERAGE_PER_ORDER` |

> **This is the single most important constraint for a scalping design.** There are **no
> market orders**. Every entry is a marketable limit that can miss, and every stop is an
> `SL-LMT` that can gap through its own limit and not fill. A strategy whose edge depends on
> immediate certain fills is not implementable on this broker.

### 2.5 Data feed — Upstox

- Streaming defaults to **Full** mode: five depth levels, OI, IV, greeks, timestamps
  (`upstox_stream.py`, `ALLOWED_STREAM_MODES`).
- Feed resolution is **1 tick/second** (exactly 60/min) — not every trade.
- Feed lag (received − exchange timestamp): **p50 1,026 ms, p90 1,135 ms**.
- Historical option candles come from `historical-candle-v3` and carry `oi` at index 6.

### 2.6 Historical data quality — the binding limits

From [`option-data-provenance.md`](option-data-provenance.md), verified against code:

- Option candles are **research triage only**, not point-in-time certified, and
  `promotion_allowed` is false for evidence derived from them.
- 8,714 two-part broker tokens map to more than one contract identity; 2,423 of those hold
  candles, covering 2,551,919 of 7,229,203 rows. A `SEGMENT|TOKEN` value is a live routing
  address, not a durable historical identity. New ingestion stamps
  `contract_key = canonical_token + expiry_date`; pairing refuses ambiguous aliases.
- Legacy rows carry no `first_ingested_at` / `retrieval_run_id`.
- **`chain_snapshots` is empty** — there is no historical option-chain structure to test.
- **`ticks` has a 30-day TTL** — there is no long-horizon quote tape to replay.

> Two of the user's stated hypotheses are therefore **not historically testable in this
> application today**: option-chain structure (no chain history at all) and anything
> requiring bid/ask or queue position beyond 30 days (TTL). Per-bar option `volume` and `oi`
> *are* available for the full history and are the one option-side signal that can be tested
> — once §7.1 is built.

---

## 3. The 1-minute backtest versus live execution gap

### 3.1 What the backtest consumes

| Input | Source | Note |
|---|---|---|
| Signal series | `candles_1m` 1-minute spot OHLCV, whole frame | Indicators computed over the **entire** frame |
| Option premium | `options_1m` 1-minute OHLCV for the selected contract | Selected by `select_contract_for_signal` (side- and ATM-exact) |
| Timestamps | `ts` = bar-start epoch ms; `bar_end_ts` = +60,000 | Decision is at bar close |
| Entry fill | Next bar's premium, net of slippage **or** %-spread | Never both |
| Exit fill | Resting-order level; stop gaps fill at bar open | Stop wins same-bar ties |
| Charges | `round_trip_charges` — statutory + brokerage | ~0.186% of turnover round-trip on Flattrade |
| Entry window | Default **09:25 → 15:00**, both overridable | `backtest.py:16-20` |

### 3.2 What live receives

| Input | Source | Note |
|---|---|---|
| Signal series | Last **N** bars only, `N = max(200, min(live_lookback_bars, 1000))` | `deployment_evaluator.py:538-542` |
| Minimum bars | 50 | `MIN_BARS_FOR_EVALUATION` |
| Quote | 1 tick/second Full feed; last-traded plus five depth levels | p50 1,026 ms stale |
| Option freshness | Contract must have data within **5 minutes** | `_has_recent_option_data(max_age_minutes=5)` |
| Data completeness | **100%** of closed minutes required — any hole blocks **entries** | `live_data_gate.REQUIRED_COVERAGE = 1.0`; exits are never blocked, so a stale feed cannot strand a position |
| Entry window | **Hardcoded 09:25 → 14:50** | `BLOCK_OPEN_UNTIL` / `BLOCK_CLOSE_FROM` |
| Expiry-day cutoff | No entries after 15:00 on the expiry day | `_is_blocked_by_expiry_day_cutoff` |
| Order type | `LMT` marketable at +0.5%, or `SL-LMT` | No market orders |
| EOD | 15:00 IST guard square + scheduled paper square-off | `EOD_SQUARE_IST` |

### 3.3 Enumerated divergences, quantified where the repo has measured them

| # | Source of divergence | Direction | Magnitude | Confidence |
|---|---|---|---|---|
| 1 | **Entry-window mismatch.** Backtest default ends 15:00; live blocks from 14:50. | Backtest optimistic | Every signal in those **10 minutes** is untradeable live | **Certain** — read from source |
| 2 | **Rolling-window indicators.** Backtest computes over the whole frame; live over N bars. | Either | At N=200 the window stops reaching 09:15 after 12:34; measured VWAP error **+17.02 pts = 2.12 ATR** at 14:49 | **Measured** (`fc424a1`) — mitigated by `live_lookback_bars=400`, which any new strategy must declare |
| 3 | **Intrabar path.** A 1-minute bar hides ~5× its range in oscillation (p50 4.97×, p90 7.55×). | Backtest optimistic | Bars where stop AND target both sit inside the range: **2.51%** at ±5 NIFTY pts, **0.16%** at ±10, **0.02%** at ±20 | **Measured** — bounded by keeping stops ≥ ~4 bps of spot |
| 4 | **Bar extremes vs ticks.** | Neutral | **0.0%** of bars had ticks outside the stored high/low — stops and targets trigger faithfully | **Measured** |
| 5 | **Feed latency.** | Backtest optimistic | p50 1,026 ms, p90 1,135 ms between exchange and decision | **Measured** |
| 6 | **Spread.** Modelled as %-of-premium with a points floor; **not** measured from real quotes. | Unknown | 1%/side is a *stress assumption*. Real ATM 1DTE spreads may be tighter; cheap 0DTE wider | **Assumption — the largest unquantified term** |
| 7 | **Slippage.** `SlippageConfig` points-per-bucket. | Unknown | ATM 0.5 pt/side, ITM1/OTM1 1.0, ITM2+/OTM2+ 2.0, ×2 in the expiry tail from 15:00. The ITM1-worse-than-ATM finding rests partly on this **assumption**, not a measurement | **Assumption** |
| 8 | **No market orders.** Entry is a marketable limit at +0.5%. | Backtest optimistic | Unmodelled: missed fills when price runs away; `SL-LMT` gapping through its limit | **Unmodelled** |
| 9 | **Partial fills.** | Backtest optimistic | Not modelled anywhere. At 1 lot the risk is a full miss, not a partial | **Unmodelled** |
| 10 | **Rejected orders.** | Backtest optimistic | Margin, RMS, rate-throttle rejections are not in the sim | **Unmodelled** |
| 11 | **Statutory charges.** | Neutral | ~0.186% of turnover round-trip, **premium-invariant** on zero brokerage (verified: 0.18579%–0.18615% from ₹10 to ₹400 premium) | **Verified in this session** |
| 12 | **CAS jump bar.** From 2026-08-03, index freezes 15:15 and prints one large auction bar. | Backtest catastrophic if unhandled | Measured **+200.95 pts (+0.82%)** in one bar on 2026-08-03 | **Measured** — both specs square off 14:55 |

### 3.4 How this gap is treated

It is **not** predicted. It is bounded conservatively and then measured forward:

- Stops are held **≥ 4 bps of spot** (≈10 NIFTY / ≈32 SENSEX points), which puts
  backtest/live agreement within 0.16% of bars (row 3) and keeps friction from dominating.
- Costs are **always on**, with the 1%/side spread as the base case and 0.5%/1.5%
  sensitivities reported alongside — a candidate that only survives at 0.5% is rejected.
- Rows 8–10 are **not modelled at all**, so the backtest is known to be optimistic by an
  unmeasured amount. The only instrument that measures it is a paper cohort reconciled
  fill-by-fill against modelled fills. That reconciliation is a promotion requirement (§5.5),
  not a nice-to-have.

---

## 4. The two candidates

Both are **NIFTY and SENSEX parameterised separately**. No threshold is shared across the two
indices: they differ in price scale (~24,500 vs ~81,000), strike step (50 vs 100), lot (65 vs
20), segment charge rate, expiry weekday and premium level. Every distance parameter below is
expressed in **basis points of spot** and resolved per index, which is the same scale-free
approach `atr_sigma_router` uses.

### 4.0 Design constraints both candidates inherit

| Constraint | Value | Why |
|---|---|---|
| Session window | Entries 09:25–14:48 | 09:25 is the hardcoded live open block; 14:48's decision lands at 14:49, before the 14:50 cutoff |
| Forced square-off | **14:55** | Before the 15:15 CAS freeze and the 15:00 guard square |
| `live_lookback_bars` | **400** | Both read session-anchored values (§1.5) |
| Moneyness | **ATM** | ITM1 measured worse (net −2.20% vs −1.48% NIFTY 1DTE); OTM1's pooled positive collapsed per-session (1.4% of sessions positive, t = −20.7) |
| Minimum stop | **≥ 4 bps of spot** | The intrabar-ambiguity floor (row 3 above) |
| Costs | Always on; 1%/side base, 0.5%/1.5% sensitivities | A candidate surviving only at 0.5% is rejected |
| DTE source | Run `dte_filter` / deployment option policy | Never derived in the strategy (§2.1) |
| Order types | Marketable `LMT` entry, `SL-LMT` backstop | Broker capability (§2.4) |

### 4.1 Candidate A — ATM Premium-Flow Scalp (short-horizon)

**Status: SPECIFIED, NOT IMPLEMENTABLE TODAY.** Requires §7.1.

#### Hypothesis A

> Directional conviction expresses in leveraged option flow before it is visible in the
> underlying's price series. When ATM **call** option volume and open-interest build
> accelerates relative to ATM **put** flow (or vice versa), the subsequent 10-minute move in
> that side's premium has MFE/MAE materially above the 0.90–0.95 unconditioned base rate.

**Why this and not another price pattern.** Five price-only hypotheses have already been
killed on this data (noise-band breakout, mean-reversion fade, compression→expansion, ITM1
pre-expiry, OTM1 convexity), and 17 registered strategies are all underlying-led. Every one
of the 25 indicators reads spot OHLCV. Option-side flow is the only information channel in
this warehouse that **nothing has looked at**, and `options_1m` carries per-bar `volume` and
`oi` for the full history. This is the cheapest genuinely new test available.

**Why 10 minutes and not 3.** Round-trip friction as a share of median MFE is 43–90% at 3
minutes and 32–67% at 5. At those horizons the strategy is a cost-payment machine regardless
of signal quality. Ten minutes is the shortest horizon where friction is not the dominant
term, and calling it a "scalp" at 3 minutes would be designing a known loser.

#### Falsification — pre-registered, decided before any run

| Test | Kill threshold |
|---|---|
| Screen, train slice | Conditioned ATM MFE/MAE **≤ 1.15** at 10 min → **REJECT, no plugin written** |
| Screen, train slice | Session-level t-stat on net% **≤ 2.0** → REJECT |
| Screen shape | CANDIDATE at exactly one horizon with NO_EDGE either side → treat as multiple-comparisons artefact, REJECT |
| Backtest, validation | Net expectancy ≤ 0 after costs, **or** ≤ `confluence_scalper` baseline → REJECT |
| Holdout | Any negative net → REJECT. One read only. |

#### Specification

| Field | NIFTY | SENSEX |
|---|---|---|
| Instrument | NIFTY 50 | SENSEX |
| DTE | **1–3** (0DTE excluded) | **1–3** |
| Contract | ATM, nearest upcoming expiry | ATM |
| Lots | 1 | 1 |
| Entry window | 09:25–14:48 IST | 09:25–14:48 IST |
| Liquidity filter | ATM bar volume ≥ 20-session causal median × 0.5 | same rule, own median |
| Max trades/session | **2** | 2 |
| Hold horizon (time stop) | 10 min | 10 min |
| Stop | max(4 bps of spot, 0.6 × ATR14) in spot terms | same formula |
| Target | 2.0 × stop | 2.0 × stop |
| Trailing | Breakeven at +1.0 × stop; then trail 1.0 × stop | same |
| Daily loss cap | ₹4,000 | ₹4,000 |
| Max concurrent | 1 | 1 |
| Forced square-off | 14:55 | 14:55 |

**Entry trigger.** On a closed 1-minute bar, with `flow_imbalance` defined as
`(ce_vol_z − pe_vol_z) + (ce_oi_delta_z − pe_oi_delta_z)` where each z-score is computed
against a **causal** 20-session rolling distribution for the same time-of-day bucket:

1. `flow_imbalance ≥ +1.5` → CE eligible; `≤ −1.5` → PE eligible.
2. **Confirmation:** the underlying's close must agree with the flow side relative to session
   VWAP (CE requires `close > vwap`, PE requires `close < vwap`). Flow without price
   agreement is not a signal.
3. **Confirmation:** `adx ≥ 20`, to exclude the chop where a 2:1 target cannot be reached.
4. Emit at most one signal per direction per 30-bar cooldown.

**No-trade conditions.** Any of: `flow_imbalance` unavailable or non-finite; ATM contract
absent or its last bar older than 5 minutes; bar volume below the liquidity floor; `atr` or
`vwap` NaN; DTE unresolved; the session's data-completeness gate has failed; two trades
already taken; daily loss cap hit; time ≥ 14:48.

**Data freshness and completeness.** Option contract data within 5 minutes
(`_has_recent_option_data`); 100% of closed spot minutes present (`REQUIRED_COVERAGE = 1.0`);
`live_lookback_bars = 400`.

**Degraded-condition behaviour.** Gaps → `gap_before` resets indicator state, and a gap in
the session blocks entries via the data gate. Stale quotes → the 5-minute freshness check
blocks entry; exits are never blocked. Broker/API failure → a lost ACK is INDETERMINATE, the
claim is retained and the engine halts (`be04cca`); no retry. Abnormal spread → if modelled
spread would exceed 3% of premium, skip. Volatility spike → no entry when
`atr > 2.5 × atr_avg`.

**Parameter budget — frozen, 6 dimensions.** `flow_z_threshold` {1.0, 1.5, 2.0},
`hold_minutes` {10, 15}, `stop_bps` {4, 6, 8}, `target_mult` {1.5, 2.0, 2.5},
`adx_min` {18, 20, 25}, `max_trades` {1, 2}. **324 combinations, per index.** Nothing else
may be tuned. Indicator periods are **pinned at defaults** — they are not part of the
hypothesis, and 10 of the 14 dimensions in a prior campaign were indicator periods that could
not move the objective.

### 4.2 Candidate B — Expiry-Regime Trend Continuation (same-day intraday)

**Status: SPECIFIED AND IMPLEMENTED** as
`backend/app/strategies/plugins/expiry_regime_trend_continuation.py` (34 tests). It
registers, loads, and is selectable in Backtest Lab and the Optimizer. Implemented ≠
validated: it has no screen result, no backtest and no paper cohort, and the verdict in
§6.2 is unchanged.

#### Hypothesis B

> Expiry-session price behaviour differs materially from an ordinary session, and the
> difference is **adverse to the buyer**. On the expiry session (0DTE), pin/gamma effects
> suppress sustained directional travel while theta is at its maximum; on 1DTE the same
> trend-continuation setup retains directional travel at materially lower carry. A trend-day
> continuation trade held 30–60 minutes therefore has materially better net expectancy on
> **1DTE** than the identical trade on **0DTE**.

This is deliberately structured so the interesting result is a **difference between two
arms**, not a single positive number. The 0DTE arm is **pre-registered as expected to fail**:
measured net cost of a 5-minute ATM hold is −4.43% NIFTY / −2.01% SENSEX at 0DTE against
−1.48% / −0.61% at 1DTE. If the 0DTE arm wins, the register's §3 is wrong and that is a
finding worth more than the strategy.

**Why 30–60 minutes.** Friction is 13–26% of median MFE at 30 minutes against 32–67% at 5.
Combined with the horizon-invariance of the MFE≥2×MAE share (35–37% at every horizon 3m→30m),
a longer hold does not buy a trendier market — it buys the *same* opportunity set at a third
of the friction. That is the only structural advantage available.

#### Falsification — pre-registered

| Test | Kill threshold |
|---|---|
| Screen, train | 1DTE conditioned MFE/MAE **≤ 1.10** at 30 min → REJECT |
| Screen, train | Session-level t-stat **≤ 2.0** → REJECT |
| Arm comparison | 1DTE net expectancy **not** > 0DTE net expectancy by ≥ 1.0 percentage point → hypothesis B is **disconfirmed**, report and stop |
| Backtest, validation | Net ≤ 0 after costs, or ≤ the both-sides baseline → REJECT |
| Holdout | Negative net → REJECT. One read. |
| Trade count | < 30 trades per index on validation → **UNPOWERED**, not a pass |

#### Specification

| Field | NIFTY | SENSEX |
|---|---|---|
| DTE | **1** primary · **0** as a separately-reported arm | same |
| Contract | ATM, nearest upcoming expiry | ATM |
| Lots | 1 | 1 |
| Entry window | 09:45–13:30 IST | 09:45–13:30 IST |
| Max trades/session | **1** | 1 |
| Hold | 60 min time stop (`hold_max_minutes`) | same |
| Stop | max(5 bps of spot, 0.8 × ATR14) | same formula |
| Target | 2.5 × stop | 2.5 × stop |
| Trailing | Breakeven at +1.2 × stop, then trail 1.2 × stop | same |
| Daily loss cap | ₹4,000 | ₹4,000 |
| Max concurrent | 1 | 1 |
| Forced square-off | 14:55 | 14:55 |

**Entry trigger.** Trend-day agreement on a closed bar at or after 09:45:

1. **Opening range** built from the exact 30 bars 09:15–09:44 IST (the same construction
   `dte_opening_shock_breakout` uses, which is already tested).
2. **Three-way agreement** required, all in the same direction:
   - close breaks the opening-range boundary;
   - close is on the same side of session VWAP;
   - close is on the same side of the **previous session's close**.
3. **Confirmation:** the breaking bar's range ≥ 1.2 × ATR14 and its close is in the top
   (CE) or bottom (PE) 35% of its own range.
4. At most **one** signal per session, the first that qualifies.

**No-trade conditions.** Opening range incomplete (fewer than 30 bars 09:15–09:44); prior
session close unavailable; DTE not in the arm's filter; `atr`, `vwap` or `adx` NaN; a signal
already taken; time outside 09:45–13:30; data gate failed; ATM contract stale > 5 min.

**Degraded-condition behaviour.** Identical to Candidate A, plus: on 0DTE the expiry-tail
slippage multiplier (2× from 15:00) never applies because the position is closed by 14:55,
and if the 0DTE arm ever needs to hold past 15:00 the trade is rejected rather than modelled.

**Parameter budget — frozen, 5 dimensions**, as implemented in the plugin's
`parameter_schema`:

| Dimension | Grid | Schema name |
|---|---|---|
| Stop floor (bps of spot) | {5, 7, 10} | `stop_bps` (min pinned at **4.0**, the ambiguity floor) |
| Target multiple | {2.0, 2.5, 3.0} | `target_mult` |
| Range expansion | {1.0, 1.2, 1.5} | `range_mult` |
| Entry cutoff | {12:30, 13:30} → {195, 255} | `entry_cutoff_minutes_after_open` |
| Max hold | {45, 60} | `hold_max_minutes` |

**108 combinations, per index, per DTE arm.** Opening range is pinned at 30 bars; indicator
periods are pinned at defaults; `signal_threshold` is pinned (the score is fixed at 65, so
any value ≤ 65 is behaviourally identical). `stop_atr_mult` is exposed at its 0.8 default
for sensitivity probing but is **not** part of the search budget.

**One honest deviation from the spec above.** The "30-minute minimum hold" is *not*
implemented. The `Signal` contract has no minimum-hold field, and adding one would mean
suppressing the stop-loss for the first 30 minutes — strictly worse risk for a
long-premium position. Early exit is therefore governed by the stop and target alone, and
`hold_max_minutes` caps the upper end. If a minimum hold turns out to matter, it is a
change to the exit engine, not to this plugin.

---

## 5. Research protocol — pre-registered before any run

### 5.1 Order of operations, and the gate that comes first

```
0. Rebuild            docker compose up -d --build backend   <- REQUIRED after a pull
1. Validate data      docker compose exec backend python scripts/screen_option_buying.py --validate-only
2. Screen             docker compose exec backend python scripts/screen_option_buying.py --instrument {NIFTY,SENSEX}
   └─ REJECT here kills the candidate. No optimizer trial. No backtest.
3. Plugin             candidate B: ALREADY WRITTEN (expiry_regime_trend_continuation)
                      candidate A: blocked on §7.1 and must not be written before it
4. Backtest, train    Backtest Lab, costs on
5. Optimize, train    Optimizer, frozen budget from §4, train slice only
6. Rank, validation   validation slice; finalists RECORDED before step 7
7. Holdout            ONE read, finalists only, with friction sensitivities
8. Paper forward      frozen cohort, one lot, fill reconciliation
```

> **Step 0 is not ceremony.** `backend/Dockerfile` bakes source in with `COPY . .`, and
> `docker-compose.yml` bind-mounts only `backend/app/strategies/plugins`. After a pull the
> new plugin is live in a running container, but `scripts/screen_option_buying.py` and
> `app/option_screen.py` are not present until the image is rebuilt — the run fails with
> `No such file or directory`, which looks like a bad checkout and is not one.

Step 2 is not optional and not a formality. `OPTION_BUYING_MICROSTRUCTURE_2026-08.md` closes
by saying the screen "kills candidates before a plugin is written", and the five scripts
behind it were thrown away. They are now shipped as `app/option_screen.py` (§7.3) so this
campaign and the next one measure the same thing the same way.

Candidate B's plugin exists ahead of its screen result, which is a deliberate departure
from that ordering and worth naming. The screen measures raw ATM premium excursions under a
mask; it does **not** execute a strategy. Without a registered plugin the candidate could
not reach Backtest Lab or the Optimizer *even after passing*, so the spec would not have
been falsifiable inside this application. Writing it costs one file and consumes no
holdout. The gate is unchanged: **a REJECT at step 2 retires the plugin unrun.**

### 5.2 What the screen must reproduce before its verdicts mean anything

The unconditioned ATM baseline must land near the recorded **0.90–0.95** MFE/MAE. If it does
not, the data has changed and *that* is the finding — the run stops being about strategies
until the discrepancy is explained. The script prints the baseline before any conditioned
cell for exactly this reason.

### 5.3 Splits — and the honest problem with them

| Slice | Window | Status |
|---|---|---|
| Train | 2024-11-25 → 2025-08-31 | Heavily used by prior campaigns |
| Validation | 2025-09-01 → 2025-12-31 | Used as the premium-momentum validation slice |
| **Prior holdout** | 2026-01-01 → 2026-07-10 | **Consumed** — read by the premium-momentum finalists |
| **Available fresh holdout** | 2026-07-11 → present (~30 sessions) | Never optimised against |

> **This is a material limitation and it cannot be engineered away.** Roughly 30 sessions is
> below the 60-session promotion minimum and well below the 120-trade minimum for strategies
> taking 1–2 trades a day. A campaign started today can produce a *screen verdict* and a
> *train/validation verdict*, but it **cannot produce a promotion-grade holdout result**. The
> remaining evidence must come forward, from paper, at roughly 20 sessions a month.
>
> `Split.holdout` in `app/option_screen.py` raises `HoldoutProtectionError` unless
> `unlock_holdout(reason=...)` is called with a written reason, so a holdout read has to be a
> deliberate, recorded act rather than an accident.

### 5.4 Metrics reported for every arm

Net expectancy per trade (₹ and % of premium); max drawdown (₹ and % of the ₹2,00,000
account); longest losing streak; win/loss distribution with median win and median loss;
MAE/MFE distribution per trade; trades per session; **sensitivity to ±1 step on every frozen
parameter** (a result that survives only at one grid point is rejected as overfit);
per-session medians and a session-level t-stat — never a pooled bar mean.

### 5.5 Paper-forward requirements

Per [`forward-validation-policy.md`](forward-validation-policy.md), unchanged and not
negotiable here: frozen `forward_config_hash`; exactly one lot; the account-wide ₹2,00,000
gate; costs on; ≥60 complete sessions (≥357/375 bars) **and** ≥120 closed trades; ≥95%
point-in-time option coverage; no overnight; lower block-bootstrap daily-mean CI > 0; ≥4/6
positive 10-session blocks; ≤25% monthly and whole-record drawdown; 252-session impairment
bound below 30%.

Additionally required by this document, because §3.3 rows 8–10 are unmodelled: **a
fill-by-fill reconciliation of modelled versus observed entry and exit prices**, reported as
a distribution, not an average. That distribution is the only real measurement of the
execution gap that exists.

---

## 6. Results and verdicts

### 6.1 Results

**None. No backtest, optimizer run or paper session was executed for this document.**

The environment this work was done in has no MongoDB, no Docker daemon and no warehouse data
— it is a fresh clone of the repository. The warehouse lives on the operator's machine. Every
number in this document is either read from source, measured in a prior recorded session, or
verified against an external source, and each is labelled as such.

What *is* new and verified in this session: statutory charges are **premium-invariant** at
~0.186% of turnover round-trip on Flattrade's zero brokerage (0.18579%–0.18615% across ₹10 →
₹400 premium). The 0DTE cost penalty therefore comes from **theta and the spread's points
floor**, not from charges — a ₹0.5 floor is 5.0% per side of a ₹10 premium against 1.0% of a
₹140 one. On a ₹20/order broker the same round trip would cost 7.4% at ₹10 premium, which is
a standing argument against ever moving this strategy family off a zero-brokerage broker.
Pinned in `tests/test_screen_option_buying_script.py`.

### 6.2 Verdicts

| Candidate | Verdict | Basis |
|---|---|---|
| **A — ATM Premium-Flow Scalp** | **RESEARCH-ONLY — premise CONFIRMED, build unblocked** | The blocking question is answered: OI is populated on **99.61% (NIFTY) / 99.86% (SENSEX)** of sampled option bars (measured 2026-08-23, §10). The data supports the hypothesis, so §7.1 is now justified work rather than a gamble. Still not implementable *today* — option-side features must reach `evaluate()` first — and still unscreened. |
| **B — Expiry-Regime Trend Continuation, 1DTE arm** | **RESEARCH-ONLY — implemented, cleared to screen** | Plugin registers and loads (34 tests). No screen, backtest or paper evidence exists. Screen first (§5.1 step 2); a REJECT retires it unrun. |
| **B — 0DTE arm** | **RESEARCH-ONLY, pre-registered as expected to FAIL** | Measured net −4.43% NIFTY / −2.01% SENSEX per 5-min ATM hold, ~3× the 1DTE bleed. Run it as the control arm, not as a hope. |

**Neither candidate is paper-ready, and neither is eligible for a live-readiness review.**
Paper-ready requires a surviving screen plus a positive cost-adjusted validation result;
live-readiness additionally requires the §5.5 cohort, which needs roughly three months of
forward sessions that do not exist yet.

### 6.3 The honest prior

The user asked for these hypotheses to be treated as hypotheses rather than established
edges. Applying that symmetrically to the design itself: this repository's own measurements
say the intraday ATM option buyer faces MFE/MAE of 0.90–0.95 before costs, that 35–37% of
bars carry the entire opportunity set at every horizon from 3 to 30 minutes, that 74% of the
intraday path is noise, and that five conditioned variants have already failed. Three full
campaigns have failed a holdout.

Both candidates below are more likely to fail than to succeed, and the specifications are
written so that failure is **cheap and fast** — the screen kills a candidate in minutes,
before a plugin exists. That is the point of the ordering in §5.1. A short-horizon scalping
strategy in particular is contraindicated by the measured friction profile; Candidate A is
the most defensible version of the request, not an endorsement of the premise.

---

## 7. Required product improvements

### 7.1 Option-side features must reach `evaluate()` — HIGH, and it blocks Candidate A

The warehouse stores `volume` and `oi` per option bar. `warehouse_lookup.py` surfaces `oi`.
**No strategy can read either**, because `build_eval_ctx` hands `evaluate()` a spot frame and
nothing else. This is the gap between what the data supports and what a strategy can express,
and it blocks the only genuinely untried hypothesis available.

Proposed shape, deliberately narrow:

- A new `app/features/option_flow.py` computing, per spot bar, from the ATM CE and PE
  contracts of the nearest upcoming expiry: `ce_volume`, `pe_volume`, `ce_oi`, `pe_oi`,
  `ce_oi_delta`, `pe_oi_delta`, and causal time-of-day z-scores of each.
- Delivered through `session_precompute`/`build_eval_ctx` as `ctx["option_flow"]`, so the
  `Signal` contract and every existing strategy are untouched.
- **The same builder must serve backtest and live.** The `exit_controls` defect
  (`20c9750`) and the `jData` encoding defect (`23d422b`) were both interface mismatches that
  a green suite missed because both sides of the test shared the implementation's assumption.
  This is an interface. Test it against the real other side at least once.
- Backfill caveat: `oi` is populated from `historical-candle-v3` index 6. The screen's
  `--validate-only` mode reports actual population; **verify it is non-trivially populated
  before building this.** If historical `oi` is mostly zero, Candidate A is dead on arrival
  and the build should not start.

### 7.2 The live entry window is hardcoded and diverges from the backtest — HIGH

`BLOCK_OPEN_UNTIL = 09:25` and `BLOCK_CLOSE_FROM = 14:50` are module constants in
`deployment_evaluator.py`, not per-deployment settings, while `backtest.py` defaults to
09:25–15:00 and is overridable. Consequences:

1. Any backtest run at defaults counts signals from **14:50–15:00** that live will refuse.
2. A strategy needing the opening 10 minutes cannot be deployed at all, however it backtests.
3. `dte_opening_shock_breakout`'s doc already has to warn operators to override the window
   by hand — a documented workaround for a missing feature.

Fix: make the window a deployment-level setting, defaulting to the current constants, and
have the backtest read the same resolved window so a saved run and its deployment cannot
disagree. Until then, **every backtest in this campaign must set `trade_window_end` to
14:50 explicitly**, and both specs above do.

### 7.3 The screening gate is now shipped — DONE in this change

`backend/app/option_screen.py` + `backend/scripts/screen_option_buying.py`, with 45 tests.
It enforces the three lessons that were previously carried only in prose: per-session
statistics with a session-level t-stat, causal-only conditioning thresholds, and a holdout
that raises unless deliberately unlocked with a written reason.

### 7.4 No historical option-chain data exists — MEDIUM

`chain_snapshots` has an index and no writer. The user's option-chain-structure hypothesis
cannot be tested at all, and cannot be tested *in future* either unless capture starts now.
If chain structure is to be a research avenue, begin capturing snapshots immediately —
history cannot be backfilled.

### 7.5 Tick retention cannot support quote replay — MEDIUM

`db.ticks` has a 30-day TTL. Validating modelled fills against a real bid/ask tape needs a
received-time-keyed tape spanning the cohort. Either extend retention for a capture window or
accept that fill validation comes only from live paper reconciliation (§5.5).

### 7.6 Spread and slippage are assumptions, not measurements — MEDIUM

`SlippageConfig`'s per-bucket points and the 1%/side spread are stress assumptions. The
ITM1-worse-than-ATM conclusion rests partly on the modelled 2× ITM1 slippage. With Full-feed
depth already streaming, capture observed ATM/ITM1/OTM1 bid-ask by DTE and time-of-day and
replace the assumption with a measurement.

### 7.7 Missing indicators, ranked by value to this brief

1. **Option flow** (§7.1) — blocks Candidate A.
2. **Relative-volume z-score by time-of-day bucket** on spot — both specs need a liquidity
   filter and there is no rel-vol feature; each strategy would hand-roll it.
3. **Realised-vs-implied spread** — needs IV history, which is streamed but not warehoused.
4. **Prior-session close as a first-class column** — Candidate B needs it and
   `dte_opening_shock_breakout` re-derives it from a 400-bar window. One shared, tested
   column would remove a repeated source of the exact window-reach bug `fc424a1` fixed.

### 7.8 Safety controls — no gap found, but `HANDOFF.md` is stale and says otherwise

An earlier revision of this document listed the `detect_drift` call-site bypass in
`deployment_evaluator.py` as an open safety gap, on the strength of the "Known open
(deliberate)" note in [`HANDOFF.md`](HANDOFF.md) §2.0e. **That was wrong. The defect is
closed**, by `6e6e1cc` — which is the head of `main` and the base of this branch.

Verified in the current source (`deployment_evaluator.py:443-460`): the call site no longer
pre-filters on `pinned_sha`, and the three failing states are journalled under distinct
reasons — `strategy_source_never_pinned`, `strategy_source_unreadable` and
`strategy_source_drift` — because "this was never verified" and "the file changed under a
running deployment" need different operator responses. A drifted **live** deployment is also
demoted to paper rather than merely paused, so a re-pin cannot silently resume real trading
against the changed code.

**The actionable item is documentation, not code.** `HANDOFF.md` is the designated entry
point for every new engineer and agent, and it currently describes a closed live-safety
defect as deliberately open. A reader who trusts it will either go hunting for a
non-existent bug or, worse, believe an unpinned deployment can still evaluate. Update that
§2.0e note to point at `6e6e1cc`.

**The generalisable lesson, and the reason this is in the report at all:** I propagated a
stale claim from a summary document into an audit finding without checking the code — in a
repository whose own handoff opens by saying "the repository and `tests/` are the source of
truth, not any prior chat." A summary is prior chat. Every other finding in §7 was read
from source; this one was not, and it was the only one that was wrong.

---

## 8. Remaining risks, assumptions and the exact evidence still needed

### 8.1 Assumptions this document makes

| # | Assumption | If wrong |
|---|---|---|
| 1 | Historical `oi` in `options_1m` is non-trivially populated | Candidate A is dead before it starts. **Check first** with `--validate-only` |
| 2 | The 0.90–0.95 baseline still reproduces on current data | Every relative verdict shifts; the discrepancy becomes the finding |
| 3 | 1%/side is a fair central spread estimate for ATM 1DTE | Both candidates' economics move materially (§7.6) |
| 4 | Option-flow imbalance is measurable from 1-minute bars | Candidate A needs sub-minute flow and the 30-day TTL cannot supply history |
| 5 | ~30 fresh sessions remain untouched | If prior campaigns touched 2026-07/08 too, there is **no** clean holdout and only forward paper can decide |
| 6 | CAS behaviour observed on 2026-08-03 is the steady state | Only ~15 sessions of post-CAS data exist; the freeze/jump pattern is not yet a large sample |

### 8.2 Risks

- **Overfitting to a thin holdout.** ~30 sessions with 1–2 trades a day is ~30–60 trades —
  half the promotion minimum. A positive result there is not evidence. It must be labelled
  UNPOWERED, and §4's kill criteria do that explicitly.
- **Multiple comparisons across arms.** Two candidates × two indices × two DTE arms × grid =
  many chances to find noise. `summarize_screen` flags a single surviving horizon as
  `fragile_single_horizon` for this reason.
- **Regime dependence.** Post-CAS sessions, the Jan-2026 lot change (75 → 65) and the
  2025 expiry-weekday rotations all mean the history is not one regime. A model fitted
  across them is fitted to an average that no longer exists.
- **Execution divergence remains unmeasured** (§3.3 rows 8–10). No amount of backtesting
  closes it.

### 8.3 Exact evidence still needed before any live consideration

1. `--validate-only` output for both indices: session completeness, contract coverage,
   `contract_key` coverage, and **actual `oi` population**.
2. A screen run reproducing the unconditioned baseline near 0.90–0.95 on both indices.
3. A screen verdict of `CANDIDATE` at two or more adjacent horizons for at least one arm.
4. Train + validation backtests with costs on, beating both a simple baseline and
   `confluence_scalper`, with ±1-step parameter sensitivity reported.
5. A single, recorded holdout read by named finalists, with 0.5%/1.0%/1.5% friction
   sensitivities.
6. A ≥60-session, ≥120-trade frozen one-lot paper cohort meeting every gate in §5.5.
7. The fill-by-fill modelled-versus-observed reconciliation distribution from that cohort.
8. Completion of [`LIVE_VALIDATION_PLAN_2026-08.md`](LIVE_VALIDATION_PLAN_2026-08.md) and a
   Flattrade-registered static IP.
9. A separate, explicit user authorisation. **Nothing in this document constitutes one.**

---

## 9. What was changed in the repository by this work

| File | Change |
|---|---|
| `backend/app/option_screen.py` | New. The shipped pre-plugin screen (pure, no DB). |
| `backend/scripts/screen_option_buying.py` | New. Read-only CLI: validate → split → baseline → conditions. |
| `tests/test_option_screen.py` | New. 36 tests, including mutation-verified block-boundary guards. |
| `tests/test_screen_option_buying_script.py` | New. 16 tests (pure helpers). |
| `tests/test_screen_option_buying_db_paths.py` | New. 16 tests driving the DB-touching functions against a strict fake Mongo. |
| `backend/app/strategies/plugins/expiry_regime_trend_continuation.py` | New. Candidate B as a registered research-only plugin. |
| `tests/test_strategy_expiry_regime_trend_continuation.py` | New. 39 tests, including look-ahead safety, fail-closed paths and the clamped entry cutoff. |
| `docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md` | This document. |

Every change is **additive** — eight new files, zero modifications to existing source.

### 9.1 Three defects the CLI's database path was hiding

The screen's pure helpers were tested from the start; its three DB-touching
functions — `validate_spot`, `validate_options`, `build_atm_series` — were not, because
this environment has no MongoDB. Driving them against a strict fake found three defects,
all of which would have surfaced on the operator's first real run:

| Defect | Why it mattered |
|---|---|
| `option_contracts` was queried on **`option_type`**; the field is **`side`** (`options_universe.py` normalises to it, `option_candles.py` stores it). | The query matched nothing, so the run printed *"no ATM option series could be built … this is a DATA finding, not a strategy one."* It would have **blamed the warehouse for a typo in the query** — the most expensive possible failure mode for a validation tool. A second instance survived in a projection and was caught only by a test asserting the string is absent repo-wide. |
| The OI estimate used `count_documents({"oi": {"$gt": 0}}, limit=200_000)` over a denominator of `min(total, 200_000)`. | It saturates: any warehouse holding 200k populated rows reads **~100%**, whether the true share is 3% or 100%. This is *the* number the whole candidate-A decision turns on. Replaced with a per-instrument `$sample` estimate that reports the share, the sample size and its scope. |
| The CLI handed `screen_condition` one frame stacking every session **and both option legs**, while that function's docstring said forward windows must not straddle sessions. | Every block boundary produced `horizon` bars whose "forward" excursion was measured against a different contract, silently corrupting every cell. Fixed by making `screen_condition` compute excursions **within contiguous blocks** (`group_by`, defaulting to `session_date`) — the contract is now enforced by the code instead of asserted in prose. |

**The generalisable point, and it is the same one as §7.8:** the third defect existed
because a contract was *documented* rather than *checked*, and the only caller broke it
immediately. This repository's own record already says it twice — the `jData` encoding and
the `exit_controls` schema were both green in CI and both wrong in production, because the
test shared the implementation's assumption. A docstring is not a test.

**Verification baseline (host, 2026-08-23):** `5,081 passed, 2 failed, 10 skipped,
4 xfailed` in 162s. The two failures are `test_premium_momentum_route.py`, which needs a
live MongoDB on `localhost:27017` and fails with `ServerSelectionTimeoutError` in any
environment without one; they are unrelated to this change. The host also needed
`pytest-asyncio`, `pydantic`, `fastapi`, `httpx`, `optuna`, `motor` and `yfinance`
installed before the suite would collect — all are already in
`backend/requirements.txt`.

### 9.2 The block-boundary fix was itself unguarded — found by mutating it

§9.1 records fixing the excursion window so it cannot cross a contract boundary,
and the commit message for it said "a docstring is not a test". Auditing that
commit the way this repo demands — *audit your own commits with the same
machinery you use on others'* — the fix turned out to be unguarded in exactly
that way.

Deleting the entire block loop, reverting to the original whole-frame behaviour,
left **all 29 tests in `option_screen.py`'s own test file passing.** The single
test that caught it lived in the CLI's test module, an end-to-end assertion two
layers away from the invariant. Refactor or delete that one test and the core
guarantee would have silently gone with it.

`tests/test_option_screen.py` now owns the invariant directly (36 tests), and the
guards are verified by killing three mutants rather than by inspection:

| Mutant | Behaviour it restores | Killed by |
|---|---|---|
| Block loop deleted | Windows measured over the whole frame | 3 tests |
| Run-detection disabled | Separated stretches of one key rejoined into a block | 4 tests |
| Default grouping removed | Whole frame instead of `session_date` | 3 tests |

**Worth stating plainly, because it is the third instance of one pattern in this
document:** §7.8 was a claim propagated from a summary without reading the code;
§9.1's third defect was a contract asserted in a docstring and broken by its only
caller; this was a fix believed correct because it was *written* correctly rather
than because anything would fail if it regressed. All three are the same error —
treating an assertion as evidence. A mutation is cheap and answers the question
directly: *if this were wrong, would anything go red?*

### 9.3 A ten-mutant sweep over every shipped invariant

§9.2 found one unguarded fix by mutating it. Rather than stop at the one that was
already suspected, the same treatment was applied to every load-bearing invariant
in this change — the ones whose silent regression would produce a strategy that
looks profitable in backtest and is not.

| Mutant | Restores | Result |
|---|---|---|
| `causal_session_stat` drops its `shift(1)` | Thresholds see their own session | KILLED |
| `Split.unlock_holdout` becomes a no-op | Holdout readable without a reason | KILLED |
| Session stats pool bars instead of collapsing | The redundancy hypothesis #5 died of | KILLED |
| Excursion window includes the entry bar | A bar claims its own high | KILLED |
| Stop uses `min` instead of `max` | Stops below the intrabar-ambiguity floor | KILLED |
| Opening range accepts any 30 bars | A rolling window rebuilds a false "open" | KILLED |
| Plugin takes the LAST qualifying bar | Look-ahead across the session | KILLED |
| Three-way agreement loses its prior-close leg | A weaker, already-covered condition | KILLED |
| Decisive-close confirmation removed | Breaks on indecisive bars | KILLED |
| **Hard 14:48 live cap removed** | Signals live will refuse | **SURVIVED → now killed** |

The survivor is worth recording precisely, because the test that should have
caught it looked correct. It asserted a 14:49 signal does not fire at cutoff
`333` — but 333 *equals* the cap, so `min(..., _LAST_ELIGIBLE_MIN)` was a no-op
in the only case exercised. The clamp only bites above the schema maximum, and
nothing tested that.

That is not a hypothetical path. Params reach a strategy as stored dicts from
saved presets and pinned deployment snapshots, and this repo has already shipped
`56bc3a9` for a schema narrowing that broke saved presets — stored params
outliving the range that produced them is a state this codebase has seen. Had one
leaked, the strategy would emit entries after 14:48 that the live evaluator
refuses: **precisely the backtest-counts-untradeable-signals divergence recorded
as row 1 of the parity register in §3.3.** The guard now covers out-of-schema
values in both directions, and all ten mutants die.

Candidate B's plugin is registered but **unrun and undeployed**; candidate A's was
deliberately not written (§5.1, §7.1). No deployment, preset, broker session or
live setting was created or altered. No order was placed, modified or cancelled. No live mode
was enabled, and no Flattrade MCP login/logout was called. The screen CLI opens one read-only
Mongo connection and writes nothing.

---

## 10. First real warehouse validation (2026-08-23)

`--validate-only` was run by the operator against the live warehouse. This is the
first section of this document containing measurements from the actual data
rather than from source or a prior register.

| | NIFTY | SENSEX |
|---|---|---|
| Spot sessions | 433 (2024-11-25 → 2026-08-21) | 433 (same span) |
| Complete (≥95% bars) | **433 / 433** | **433 / 433** |
| Option contracts | 22,345 | 37,574 |
| Expiries | 107 (→ 2031-06-24) | 108 (→ 2031-06-26) |
| Lot sizes present | **25, 65, 75** | **10, 20** |
| `contract_key` coverage | 61.39% | **6.61%** |
| **OI populated** | **99.61%** (19,922/20,000) | **99.86%** (19,971/20,000) |
| Chain snapshots | 0 | 0 |
| Ticks retained | 26,720,007 | (shared) |

**Four things this changes or confirms.**

1. **Candidate A's premise holds.** OI is populated on ~99.7% of bars. The §7.1
   feature build is justified. This was the single go/no-go and it passed.

2. **`contract_key` coverage is far worse than the provenance doc implied, and
   SENSEX is the severe case at 6.61%.** The screen originally fell back to a
   two-part `SEGMENT|TOKEN` lookup whenever the key was absent — which for SENSEX
   would have been the *usual* path, not the exception, on exactly the identifier
   the provenance audit found mapping to multiple contracts (8,714 tokens, 2,423
   holding candles). **Fixed by not trusting the token at all:** `options_1m`
   stores `underlying`/`expiry_date`/`strike`/`side` on every row and `db.py`
   indexes precisely that tuple, so the screen now asks for a contract by what it
   *is*. Token lookups survive only as a labelled `instrument_key_unverified`
   fallback, counted and reported per run. Low `contract_key` coverage is
   therefore no longer a blocker for this campaign.

3. **Three NIFTY lot regimes in one window (25 → 75 → 65).** A single lot number
   cannot size a run spanning 2024-11 → 2026-08; `resolve_lot_size` already warns
   on this, and any rupee figure must be read per-regime. Statutory charges are
   premium-invariant as a percentage (§6.1), so the screen's cost model is
   unaffected — but a P&L in rupees is not.

4. **Session coverage is perfect** — 433/433 complete on both indices, spanning
   the full history. Data completeness is not a constraint on this campaign; the
   constraints are the ones already recorded: no chain history, a 30-day tick TTL,
   and ~30 never-optimised sessions (2026-07-11 → 2026-08-21, consistent with the
   §5.3 estimate now that the data end date is confirmed as 2026-08-21).

**Reporting defect found by this run:** both indices printed an identical
7,967,661 "option candles" under a per-instrument heading. That was
`estimated_document_count()` — the whole collection. Now reported per instrument
alongside the global figure.
