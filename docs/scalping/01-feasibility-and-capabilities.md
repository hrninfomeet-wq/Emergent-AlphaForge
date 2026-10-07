# 01 — Feasibility assessment and verified order-capability comparison

_Scalper lab, NIFTY (NFO) and SENSEX (BFO) intraday option buying. Written 2026-10-07. Every material
conclusion carries a label: **Confirmed** (read in code / primary source / measured in our data),
**Likely** (strong indirect evidence), **Assumption** (inference — consequence stated). Web sources were
accessed 2026-10-06/07. Repo paths are relative to the repo root._

## 0. Bottom line

| Question | Answer | Label |
|---|---|---|
| Who executes, who supplies data? | **Flattrade** (Noren / PiConnect OMS) executes; **Upstox** supplies market data (WebSocket V3 + REST). Upstox never places an order. | Confirmed (`docs/HANDOFF.md` §1, `backend/app/live/flattrade_client.py`, `backend/app/upstox_stream.py`) |
| Does the warehouse hold 1-minute data? | Yes: `candles_1m` (index OHLC, **volume always 0**) and `options_1m` (OHLCV + OI, **no bid/ask**), 2024-11-25 → 2026-10-05. | Confirmed (DB) |
| Are historical option *quotes* available? | Not in the warehouse. They exist only where the app happened to be running: 6 sessions of 1 Hz full-depth ticks (`tick_archive`), 11 sessions of 60-s REST chain snapshots with bid/ask (`chain_snapshots`), ~25 sessions of last-traded-price ticks (June–July). | Confirmed (DB) |
| Does the feed carry every exchange tick? | **No.** Upstox `full` mode is a ~1 Hz snapshot: index inter-arrival p50 1,046 ms; ATM option quote p50 650–1,050 ms; Upstox staff state tick-by-tick is not offered on the WebSocket. Nothing finer than one second is observable. | Confirmed (measured M1; Upstox Community 2024-07-18) |
| Can the existing app trade sub-minute today? | **No.** Entries fire only on a closed 1-minute bar (one signal per deployment per bar); fills are learned by polling the position book every 1.5 s; the Noren order-update WebSocket is unwired and uses an outdated login frame. | Confirmed (`backend/app/deployment_evaluator.py`, `backend/app/live/live_position_guard.py:64`, `backend/app/live/flattrade_client.py:578-615`) |
| Are CO / BO available for NIFTY but not SENSEX? | **Through the Flattrade API, CO and BO are unavailable on BOTH** (broker API policy, not an exchange rule). LMT and SL-LMT work on both. | Confirmed (Flattrade Kosh 2026-01-08; KB 2025-12-27; live BFO LMT fills) |
| Is there any broker- or exchange-held protection usable for a long option? | **No usable one on this account.** A resting SL-LMT SELL against a long option was margin-rejected live (needs naked-short margin); the broker OCO fired at placement and is off by default. Protection is software-only, and only while the app runs. | Confirmed (`docs/STRATEGY_DEPLOYMENTS.md:492-494`, `CHANGELOG.md:1172-1199`) |
| Is a sub-minute option-buying scalp viable on the evidence? | **No evidence supports it.** Every pre-registered hypothesis failed (doc 02); the stage-2 replay of the remaining spec failed its gate (doc 05). | Confirmed for the samples available |

**Blocking capabilities, and what was done about each:**

| Capability needed | State found | Resolution in this work |
|---|---|---|
| Sub-minute decision loop | none | Built: `backend/app/scalping/` (event-driven engine on the 1-s grid) — paper/replay only |
| Historical quotes for sub-minute testing | 6 depth sessions, expiring under a 30-day TTL | **Preserved** 3.2 M ticks into `tick_archive` (no TTL) on 2026-10-06 before the first session expired; daily archive job added (default on) |
| Replay with an execution model | none (`tick_replay.py` is a latency harness) | Built: `sim_broker.py` + `replay.py` |
| Order-update stream (fills pushed) | unwired, wrong auth frame | **Not built** — required before any live scalper (doc 04 §9) |
| Exchange LPP band at order time | only on Flattrade depth WebSocket (`le`/`ue`), not subscribed | Approximated (± max(40 %, ₹20) of last price); live adapter must subscribe (doc 04 §9) |
| Broker-held protection | none usable | Cannot be resolved on this account; software protection + operator escalation |

## 1. Application architecture as inspected

* **Stack:** FastAPI (`backend/`), React (`frontend/`), MongoDB, Docker Compose bound to 127.0.0.1. Confirmed.
* **Signal → order (existing):** `deployment_evaluator` on the closed 1-minute bar → `auto_live` → `live/executor.py` (the single real-order chokepoint) → `live/live_position_guard.py` registers on **ACK**, not on fill (the journal row is written OPEN at the reference LTP on broker acceptance). Confirmed (`auto_live.py:620-658`).
* **Entry pricing (existing):** LMT at reference LTP × (1 + min(0.5 %, 5 %)), `ret=DAY`, product NRML; the reference LTP may be up to **120 s** old; no spread or depth check. Confirmed (`order_builder.py:48,262-275`, `option_premium.py:129`).
* **Exits (existing):** in-process software guard: tick-woken fast pass (0.2 s floor) for premium stop/target; a slow 1.5 s cycle reads the broker position book, re-prices exits (1/2/4 % bands every 4 s), handles EOD (15:00) and finalises after two flat reads. Confirmed.
* **Defect found and reproduced (live path, not part of the scalper):** while a held contract keeps ticking at ~1 Hz the guard's slow cycle never runs, and after the cached book is 5 s old the fast pass stands down too. A stop breach was not acted on for the 5 s of continuous ticks in the reproduction. Confirmed (adversarial verifier, scratch reproduction against the real `LivePositionGuard`). Spun off as a separate task; it affects every live deployment.
* **Kill switch (existing):** latches, halts the engine, demotes live deployments, cancels working orders, flattens every open symbol in the account with marketable limits and a re-price ladder. Confirmed (`routers/live_broker.py:2491-2674`, `live/kill_switch.py`).
* **Rate control (existing):** a 9/s token bucket on entries only; cancels and exits bypass it; **no per-minute budget** against Flattrade's 40 order calls/min. Confirmed (`live/safety.py:161-233`).
* **Strategies:** 20 registered, all evaluated on closed 1-minute bars; `confirmation_mode="tick"` is accepted at creation but the evaluator refuses it. Confirmed.

## 2. Data: warehouse and feed

| Source | Instruments | Fields | Granularity / clock | Coverage | Label |
|---|---|---|---|---|---|
| `candles_1m` | NIFTY, BANKNIFTY, SENSEX, INDIAVIX | OHLC, volume (0 for indices) | 1 min, `ts` = bar open (epoch ms) | 2024-11-25 → today | Confirmed |
| `options_1m` | ATM band (every strike the day's range touched ± 1 step) | OHLCV, OI — **no bid/ask** | 1 min | 2024-11-25 → 2026-10-05 (no same-day option bars) | Confirmed |
| `chain_snapshots` | full chain, nearest expiry, NIFTY/BANKNIFTY/SENSEX | LTP, bid/ask + qty, OI, IV, Greeks | 60 s REST | 11 sessions 2026-08-27 → 10-06 (only when the app ran) | Confirmed |
| `tick_archive` (new) | NIFTY/SENSEX index + ATM±3 options | LTP, LTQ, best bid/ask + qty, **5-level depth**, OI, `ts` (exchange ltt), `received_ts` (Upstox frame clock), `ingest_ts` (local clock, from 09-15 on) | ~1 Hz snapshots | 6 sessions: 09-07 (full), 09-09, 09-15, 09-16, 09-29 (full), 10-06 (partial) | Confirmed |
| `ticks` (ltpc era) | index + options | LTP only | ~1 Hz | ~25 sessions 2026-06-11 → 07-17 (string `stored_at`, so never expired) | Confirmed |

Feed facts (M1, Confirmed): index updates p50 1,046 ms / p90 1,060 ms; NIFTY index exchange-time → local ingest ~1.0–2.2 s (the index print is ~1 s stale on arrival; the options-implied synthetic forward leads it by ~1 s); option quote ts → ingest p50 150–500 ms. The Upstox stream re-subscribes by **restarting** whenever the ATM band moves (13 restarts in 30 minutes on 2026-10-06), although Upstox V3 supports `sub`/`unsub` on an open socket — each restart costs up to 1 s of data. Confirmed. `persist_ticks` upserts on (key, ltt, session), so quote-only updates sharing a last-trade time overwrite each other — at 1 Hz this loses little but the tape is not a complete quote history. Confirmed (code).

**What the data can and cannot test (Confirmed):**
* 1-minute bars cannot order entry, target and stop inside a minute; every retail platform reviewed (AlgoTest, Tradetron, Quantman) backtests options on 1-minute bars and resolves same-bar ambiguity with heuristics. We did not.
* 1 Hz snapshots can test decisions made at 1-s resolution with executable bid/ask and depth — on 6 sessions only.
* Nothing available can test sub-second queue position, true fill probability of passive orders, or the exchange LPP reference (a 30-s average trade price).

## 3. Verified order-capability comparison (Flattrade API, 2026-10)

| Capability | NIFTY / NFO | SENSEX / BFO | Who holds it | Evidence | Label |
|---|---|---|---|---|---|
| LMT | Yes | Yes | Exchange order book | PDF `prctyp` LMT/SL-LMT (`docs/Resources/flattrade-pi-api/endpoints/04-place-order.md:29`); live BFO fills 2026-09-03 | Confirmed |
| MKT | **No (API)** | **No (API)** | — | Flattrade: "Only limit or SL-Limit orders are allowed" (Kosh 2026-01-08); NSE FAQ: algo market orders not permitted | Confirmed |
| SL-LMT (`trgprc`) | Accepted by API | Accepted by API | Exchange (validated after trigger) | PDF; **but a resting SELL stop against a long option is margin-rejected on this account** (live 2026-06-24) | Confirmed |
| SL-MKT | No | No | — | Flattrade policy | Confirmed |
| Cover order (prd H: entry + stop, optional trail; **no target**) | Not via API | Not via API | Broker | PDF `blprc`/`trailprc`; API ban (Kosh 2026-01-08, KB 2025-12-27, Flattrade MCP instructions) | Confirmed |
| Bracket order (prd B: target + stop + trail) | Not via API | Not via API | Broker | Same | Confirmed |
| Native target / trailing stop | Only inside CO/BO → unavailable | same | — | — | Confirmed |
| GTT / OCO (alert-based) | API exists; NRML only | API exists; NRML only | **Broker server** (not exchange), fires an LMT on LTP trigger | `16-place-gtt-order.md:23-28`; OCO fired at placement 2026-09-03 and its x/y leg pairing appears inverted; `LIVE_BROKER_OCO_ENABLED` off | Confirmed (BFO), Likely (NFO) |
| Linked cancellation of two resting exits | None (only GTT/OCO) | None | Client must cancel the sibling | Assumption: two resting SELLs on one long can both fill → short | Assumption (risk) |
| Modify order | Yes (prc, qty = **total**, trgprc, ret) | Yes | Exchange (LPP re-checked on modify) | `05-modify-order.md:19-30`; repo `modify_order` sends an incomplete payload (unused) | Confirmed |
| Order-update push | WebSocket `t:"o"` → `om` events (Fill/Rejected/Canceled) | same | Broker | `49-websocket-order-update…md`; repo client unwired | Confirmed |
| IOC retention | Documented (`DAY/EOS/IOC`); repo allows DAY only | same | Exchange | Not tested live | Likely permitted, repo blocks |
| Rate limits | Order APIs **10/s and 40/min per key**; all APIs 40/s, 200/min; shared with the Flattrade MCP | same key | Broker | `57-api-rate-limits.md` | Confirmed (whether modify/cancel count toward 40/min: **unresolved**) |
| SEBI/NSE algo threshold | ≤ 10 orders/s per exchange, broker calendar second; below it a generic algo ID, no client registration | same (BSE assumed equal) | Exchange / broker | NSE/INVG/67858 (2025-05-05) B.2–B.5 | Confirmed (NSE), Likely (BSE) |
| Limit price protection (LPP) | ref ≤ ₹50: ±₹20; > ₹50: ±40 %; ref = 30-s avg trade price; applies to modify and post-trigger SL | BSE: 40 %, min ₹20 (from 2025-12-01) | Exchange rejects | NSE/FAOP/54242; BSE 20251128-56; live BFO LPP reject 2026-09-03 | Confirmed |
| Tick size | ₹0.05 | ₹0.05 | Exchange | broker `ti` field | Likely (read `ti` at runtime) |
| Lot size | **65** (from Jan-2026 series, NSE/FAOP/70616) | **20** | Exchange | primary (NSE); SENSEX from repo + secondary | Confirmed / Likely |
| Freeze quantity | **3,510 (54 lots) from 2026-10-05** (NSE/FAOP/76693) — repo still 1,800 in two places | 1,000 (50 lots) | Exchange | primary circular (NSE) | Confirmed / Likely |
| Trading hours | 09:15–15:40 (since 2026-08-03; index frozen 15:15–15:35 in the closing auction) | 09:15–15:40 | Exchange | NSE/FAOP/74467 | Confirmed (NSE), Likely (BSE) |
| Weekly expiry | Tuesday | Thursday | Exchange | NSE/FAOP/68747; BSE 20250617-11 | Confirmed |
| Automated API trading requirements | Static IP (one primary + optional secondary, change ≤ once a week), daily OAuth/2FA session, one API key, no market orders, ≤ 10 OPS | same | Broker + exchange | NSE/INVG/67858 A.1–A.8; Flattrade Kosh 2026-01-08 | Confirmed |
| Order-to-trade ratio | option orders within ±40 % of LTP or ±₹20 exempt from 2026-04-06 | same | Exchange surveillance | SEBI 2026-02-04; NSE/SURV/73597; BSE 20260406-8 | Confirmed |

**Ordinary order types vs broker products.** LMT/SL-LMT are exchange order types and are available on both segments. CO/BO are broker *products* that bundle legs; Flattrade blocks them for API orders on every segment. The absence of CO/BO on SENSEX therefore says nothing about SENSEX limit orders, which work (live fills recorded). The repo comment that "BSE/BFO blocks them entirely" (`backend/app/live/flattrade_symbol.py:78`) has no source and is moot for the API. Confirmed.

## 4. Where protection lives, and what survives a failure

| Protection | Holder | Survives app crash? | Survives connection loss? | Survives PC power-off? |
|---|---|---|---|---|
| Software stop / target / trail / time exit (scalper engine and existing guard) | AlphaForge process | No | No (cannot send) | No |
| Kill switch | AlphaForge process + operator | No | No | No |
| Resting SL-LMT SELL | Exchange | — | — | — **margin-rejected on this account: unusable** |
| GTT / OCO | Flattrade server | Yes (in principle) | Yes | Yes — **but it fired at placement; off by default; NRML only** |
| Broker RMS auto square-off (MIS/intraday product) | Flattrade | Yes | Yes | Yes — Assumption: applies to MIS (`I`) only, near the close; the existing live path uses NRML (`M`), which is NOT auto-squared |
| Operator manual action (broker terminal / app) | Human | Yes | Yes (another device) | Yes |

**Consequence (Confirmed for the current account):** if the app or its connection fails while a scalp is open, nothing automatic closes it. The only remaining protections are the broker's intraday auto square-off (only if the product is MIS — an Assumption to confirm with Flattrade) and the operator. Doc 04 sets the escalation procedure accordingly.

## 5. Competitor practice (documented vs inferred)

Documented (AlgoTest, Tradetron, Quantman, Quantiply; accessed 2026-10-06):
* Monitoring on LTP continuously (AlgoTest "LTP mode"; candle-close mode checks at the 59th second); Tradetron checks once a minute by default, optionally per second.
* Stops are either a resting SL-L at the broker (AlgoTest, Quantman: "few sec" after entry) or held in the platform's software and fired as a limit order (Tradetron). Skipped SL-L orders are chased: AlgoTest re-prices at LTP + buffer and (pre-2026) converted to market after 1–20 s; after the April-2026 market-order ban it cancels after ~50 s and raises an error.
* Trailing: "every X move, trail by Y" steps; pushed to the resting broker order only at a trail frequency (AlgoTest caps 15 modifications per leg).
* Backtests: 1-minute OHLC everywhere; same-bar stop/target resolved by heuristics (AlgoTest: close decides / nearer-to-open wins — one heuristic favours the strategy); AlgoTest documents real losses up to 2.5× the backtested overall stop.
* After 2026-04-01 market orders are gone; Quantiply states its users cannot trade through Flattrade from 2026-04-01 (third-party platforms disallowed on API V2).

Inferred (not documented): continuous LTP monitoring is a tick-driven loop on a WebSocket feed; none documents a protection that survives the platform being down; none documents bid-side (executable) stop evaluation.

Adopted here: software stops evaluated on the **bid** (the executable side for a seller), cancel-confirm before re-pricing, an exit ladder that never gives up, a per-minute order budget with an exit reserve, and stop-first resolution of same-snapshot events.

## 6. Costs (current schedule; Confirmed against primary circulars)

STT on option sale **0.15 %** of premium from 2026-04-01 (Finance Act 2026; NSE/FATAX/73524, BSE 20260331-7); NSE options transaction **0.03553 %** (₹3,552.99 + ₹0.01 IPFT per crore, NSE/FA/73061, from 2026-03-01); BSE SENSEX options **0.0325 %**; SEBI ₹10/crore; stamp 0.003 % on buys; GST 18 % on (exchange + SEBI); brokerage ₹0. Round trip at an unchanged price: **0.2371 % of premium (NSE), 0.2299 % (BSE)**.

| Contract | Premium | Charges / round trip | In premium points |
|---|---|---|---|
| NIFTY × 65 | 50 / 100 / 200 | ₹7.71 / ₹15.41 / ₹30.82 | 0.12 / 0.24 / 0.47 |
| SENSEX × 20 | 100 / 300 / 600 | ₹4.60 / ₹13.80 / ₹27.59 | 0.23 / 0.69 / 1.38 |

**App-wide drift (Confirmed, not changed here):** `backend/app/option_costs.py` still uses STT 0.10 % and NSE 0.03503 %, so every backtest/paper/live charge in the app is **~21.6 % too low**; backtest and paper also charge SENSEX at the NSE rate. The app's "1 % spread" is applied round trip, while the measured ATM spread is ~0.23 % of mid round trip. Changing shared cost constants reprices every saved result, so it awaits operator approval (doc 06). The scalper uses the current schedule through `backend/app/scalping/costs.py`.

## 7. Unresolved points and the safe way to settle each (no real orders)

| # | Question | Safe resolution |
|---|---|---|
| U1 | Products/price types enabled per exchange on this account (MIS on BFO?) | One read-only `UserDetails` call from AlphaForge's own client (not the MCP) |
| U2 | Do Flattrade's 40 order calls/min include modify/cancel? rolling or calendar minute? | Written question to Flattrade support |
| U3 | Is IOC accepted for NFO/BFO options through the API? | Support ticket (cannot be probed without an order) |
| U4 | Is the exchange LPP band available before sending? | Subscribe read-only to the Flattrade depth WebSocket (`t:"d"`) and read `le`/`ue` |
| U5 | Does the corrected order-update WebSocket (`t:"a"`, `accesstoken`, then `t:"o"`) work? | Connect from the static IP outside market hours — read-only |
| U6 | Does Flattrade's intraday RMS auto-square MIS option positions, and when? | Support ticket; check the RMS policy page |
| U7 | SENSEX 0DTE microstructure (spread, depth, quote rate) | Run the app (recording on) through a Thursday session — nothing recorded yet |
| U8 | 2026 holiday calendar gaps (2026-10-20, 11-10, 11-24 look missing — three NIFTY expiry Tuesdays) | Check the official NSE 2026 list (spun-off task) |
