# 04 — Execution and protection design (with failure handling)

_Implemented in `backend/app/scalping/engine.py` (deterministic, no I/O) and exercised by
`tests/test_scalping_engine.py` (invariants I1–I10, a seeded 12-seed fault fuzz in the suite, and a 300-seed /
12,600-trade harsh-fault fuzz run during development: 0 shorts, worst engine/broker divergence 6 s, 0 halts).
The only execution adapter is the simulator; §9 lists what a real-money adapter would need._

## 1. Principles

1. **Deterministic, event-driven.** The engine is a pure state machine: market second, order events, submit/cancel
   results, reconcile reads, kill. Same inputs ⇒ same actions (pinned by a replay-determinism test).
2. **Broker truth wins; never guess.** Position = cumulative fills reported by the broker. An acknowledgement is not
   a fill. Any contradiction halts entries; an unresolved one halts the engine.
3. **Protection is software, so the app must stay up.** On this account nothing at the broker or exchange can hold
   a usable stop for a long option (doc 01 §4). The design therefore minimises time-at-risk (30-s holds), refuses
   to trade without fresh data, and escalates to the operator on anything it cannot resolve.

## 2. Order lifecycle

```
signal(t) ──filters──► INTENT ──place──► SUBMITTED (broker accepted; NOT a fill)
                                 │            │
                                 │      OPEN / PARTIAL ──fills──► position += Δfill
                                 │            │
          no result in 3 s ──► UNKNOWN ──► reconcile (order book + position)
                                              │
   entry timeout (2 s) ──► cancel ──► CANCELED (fills before the cancel landed stand)
   exit decision ──► cancel every working BUY ──► SELL LMT (rung k) ──► COMPLETE ──► trip closed, P&L booked
                                         └─ not filled in 1.5 s ─► cancel ─► CANCELED confirmed ─► SELL rung k+1
```

| Stage | Rule | Invariant |
|---|---|---|
| Signal | evaluated once per second on fresh data only | — |
| Submission | one entry order per round trip; unique client id carried in `remarks` | I4 |
| Acknowledgement | `SUBMITTED` only; position unchanged | I1 |
| Partial fill | the filled quantity is the position; it is protected (exited) like a full fill | I6 |
| Full fill | `first_fill_ms` starts the time exit | — |
| Protective order | none resting (unusable on this account); protection = the engine's exit loop | §3 |
| Exit | one working SELL at a time; sized from **confirmed** fills minus working SELL qty | I2, I3 |
| Cancellation | a cancel request is not a cancellation; only the broker's CANCELED event is | I3 |
| Reconciliation | routine every 10 s while anything is live; on demand after any unknown | I5 |

## 3. Exit logic (evaluated every second while holding, on the bid)

Order of evaluation, first match wins: halt/kill → forced flag → 15:00 → daily-loss pause → stale feed (no quote
for 5–6 s) → **stop** → trail → target → time. Stop is checked before target on the same snapshot (I7); a single
bid can never satisfy both, and the time exit can never pre-empt a stop.

Exit pricing: SELL LMT at `bid − ladder[k] × tick`, clamped to ≥ the exchange LPP floor + 1 tick; each rung waits
`exit_reprice_ms`, then cancel → confirmed CANCELED → next rung; the last rung repeats forever with a critical
alert (I8). With a stale feed the reference bid is cut 5 % before laddering (defensive).

## 4. Failure handling

| Failure | What the engine does | Residual risk |
|---|---|---|
| **Order rejected** (definite) | entry: trip closes, no position; exit: alert, next second places a new SELL | repeated LPP rejects in a crash → the ladder walks down to the LPP floor; still bounded by the band |
| **Unknown status** (transport error / no ack in 3 s / cancel unconfirmed in 3 s) | order marked UNKNOWN; entries blocked; reconcile requested every 5 s; **no new SELL while any SELL is unknown** | position may be held a few seconds longer than intended |
| **Duplicate / out-of-order events** | `apply_om`: rank never regresses, terminal sticky, fills = cumulative max | none observed in fuzz |
| **Lost events** | routine reconcile every 10 s; cancel-confirm timeout | divergence bounded (fuzz worst 6 s) |
| **Partial fills** | entry: remainder cancelled at timeout; exit: remaining confirmed qty re-offered | — |
| **Cancel/fill race** (entry remainder fills after the exit was sized) | exit sized from confirmed fills; the late remainder is exited by a second SELL; never oversold | tested explicitly |
| **Simultaneous target/stop** | impossible on one snapshot (one bid); stop has priority over every other reason | — |
| **Duplicate exits** | at most one working SELL; a second requires CANCELED or COMPLETE of the first | — |
| **Unintended short** | prevented by sizing (I2); if broker fills ever exceed confirmed long, the engine HALTS | requires a broker-side anomaly; operator action |
| **Stale quotes** | no entry if quote > 1.5–2 s old; holding and no quote for 5–6 s → protective exit | exit priced off the last bid −5 % |
| **Disconnection** (feed) | as stale quotes; engine keeps exiting with the last known bid | if the order path is also down, nothing can be sent (§6) |
| **Disconnection** (broker) | submits time out → UNKNOWN → reconcile cannot be read → entries blocked, alert every 5 s | position unprotected until the link returns (§6) |
| **Restart** | `restore()` puts the engine in RECONCILE_REQUIRED; nothing trades until the broker book agrees. (Paper: in-process fills cannot be reconciled, so an open paper trip is journaled `paper_restart_abandoned`.) | in live use, a restart mid-trade leaves the position unmanaged until boot completes |
| **Reconcile mismatch** | one mismatched read may be a race (order book and position book are separate reads): entries stay blocked; **two consecutive** mismatches HALT | a genuine mismatch halts; operator resolves |
| **Late fill on a closed trip** | HALT (never trade on it silently) | operator resolves |
| **Order-rate budget exhausted** | entries need 2 spare orders in the per-second and per-minute windows; exits are deferred to the next second, never dropped | exit delayed ≤ 1 s per deferral |

## 5. Kill switch, cutoffs, pauses, escalation

* **Kill** (`POST /api/scalping/paper/kill`, or `engine.kill`): cancel working entries, exit the position on the
  next second, HALT; never auto-resumes. Restart is an operator decision.
* **Cutoffs:** entries 09:20–14:45 (N1) / 09:20–14:30 (S1-E); forced exit 15:00 (the app's EOD square is also
  15:00; options trade to 15:40, the index freezes 15:15–15:35).
* **Pauses:** daily loss (realized net + open MTM at the bid) → no entries for the day and exit; loss streak →
  timed pause; trades/day cap.
* **Escalation ladder (paper today; the same for any future live adapter):**
  1. warning alert — an exit needed a second rung, or a reconcile mismatch was seen once;
  2. critical alert — the exit ladder reached its last rung, daily loss limit hit, no quote ever seen for a held
     contract;
  3. HALT — unintended short, two consecutive reconcile mismatches, late fill on a closed trip, paper step
     exception;
  4. operator — after a HALT, or whenever the broker cannot be read: check the broker book directly (Flattrade
     terminal), flatten by hand if needed, then decide whether to restart.

## 6. What protects a position when something fails — and the limits

| Scenario | Automatic protection left | Worst case |
|---|---|---|
| Feed stalls, app and broker up | engine's stale-feed exit after 5–6 s | exit at a stale-bid-minus-5 % limit; may need several rungs |
| Broker API down, app up | **none** (cannot send) | position held until the API returns; loss = the move over the outage |
| App crash / PC down | **none** on this account (no usable resting stop; OCO off; NRML not auto-squared) | unbounded until the operator acts; for a 1-lot NIFTY ATM scalp at ₹66 premium the full premium at risk is ₹4,290 |
| Exchange fast market | LPP band caps how far below the reference an exit can be priced | the stop is **not** a maximum loss: measured 60-s adverse moves reach −17 % (NIFTY p99) / −8 % (SENSEX p99) — 3.4× / 2× the configured stop |
| Halt / circuit on the contract | none | exit impossible until trading resumes |

**A configured stop is a trigger, not a guaranteed maximum loss.** Slippage past the stop (1 Hz observation, ≥ 1 s
to the exchange, LPP rejects, thin SENSEX touch), unfilled exits and outages can all exceed it. The worst loss per
round trip is the premium paid.

## 7. Latency — measured vs not

| Interval | Value | Status |
|---|---|---|
| Upstox index frame → local ingest | NIFTY index print ~1.0–2.2 s old on arrival | measured (M1) |
| Upstox option quote → local ingest | p50 150–500 ms, p90 230 ms–2.1 s | measured (M1) |
| Decision grid | 1 s (feed cadence) | by design |
| Order send → broker acknowledgement (PlaceOrder round trip) | **p50 139 ms, range 65–320 ms (n = 21 real orders)** | measured (`live_orders.ts_claim → ts_submitted`) |
| Acknowledgement → fill | **never recorded** — the app has no fill timestamps during a session | **not measured** (requires the order-update WebSocket) |
| Exit decision → exit fill | not measured | not measured |
| Paper/replay | every `place`, `ack`, `fill`, `cancel`, `exit_decision` event carries `ts_ms`, `sent_ms`, `quote_age_ms` → `scalp_events` | instrumented (simulated latency only) |

No speed claim is made beyond these numbers.

## 8. Recording for replay

* `tick_archive` (no TTL): full-depth NIFTY/SENSEX index + option ticks, copied out of the 30-day-TTL `ticks`
  collection at boot and daily at 15:50 IST (`SCALP_TAPE_ARCHIVE`, default on). 3,201,938 ticks preserved on
  2026-10-06, including the 09-07 session that would have expired on 10-07.
* `scalp_events` / `scalp_paper_trades` / `scalp_engine_state`: every decision, order event and closed trade from
  the paper runner, with timings.
* Limits: the app's tick upsert collapses quote-only updates that share a last-trade time; nothing records the
  Flattrade depth feed (LPP band, order counts) or futures. Adding NIFTY/SENSEX near-month futures to the
  subscription is recommended (doc 06).

## 9. Not built — prerequisites before any real-money scalper (each needs separate authorisation)

1. A Flattrade order-update WebSocket client using the current login (`t:"a"`, `accesstoken`) + `t:"o"` subscribe,
   feeding `engine.on_order_event` (the existing scaffold is unwired, uses the old frame and calls an async handler
   synchronously).
2. A live adapter mapping engine actions to the existing chokepoint (`live/executor.py`) with idempotent client ids
   (`remarks`), MIS vs NRML decided with Flattrade's auto-square policy confirmed (doc 01 U6), and any non-200
   PlaceOrder treated as UNKNOWN, never as a clean reject.
3. The Flattrade depth WebSocket for the real LPP band (`le`/`ue`) at order time.
4. A key-wide order budget shared with the rest of the app and the Flattrade MCP (40/min per key).
5. The existing live guard's starvation defect fixed (spun-off task) — the scalper would share the process.
6. An always-on host on the static IP (`docs/durable-static-ip-deployment.md`): a scalper with software-only
   protection cannot run from a PC that is often off.
