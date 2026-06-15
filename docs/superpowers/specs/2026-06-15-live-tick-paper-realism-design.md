# Live Tick-Driven Paper-Trading Realism — Design Spec

**Date:** 2026-06-15
**Status:** Approved design, pre-implementation
**Topic:** Make deployed-strategy signal generation + paper trading faithfully
reflect *live* market execution, so the Signal Journal and Paper Trade pages can be
trusted as forward-test evidence before any future broker-API deployment.

> **Vision alignment.** The app's purpose is to optimize a statistically-edged
> strategy (backtest + walk-forward + survival gate) and then *forward-test it
> honestly* in paper trading. Paper results are only trustworthy if they reproduce
> what real money would have done. This spec closes the gaps between the current
> live engine and that standard. No real broker orders — permanent for now.

---

## 1. Problem

The live engine is closer to "live" than it first appeared, but has two real gaps
and one fidelity hole:

1. **Exit granularity (the main gap).** Open paper trades are marked to the live
   premium **once per minute** ([runtime.py:184](../../../backend/app/runtime.py)).
   A stop breached at 10:32:15 isn't detected until ~10:33:10 and fills at a drifted
   premium — so paper exits are *better or worse* than a real tick-level stop, which
   destroys backtest↔paper↔live parity.
2. **Entry latency.** Evaluation fires at a fixed `minute+10s`
   ([runtime.py:156](../../../backend/app/runtime.py)); the just-closed bar is
   already in `candles_1m` ~1s after close (the roller flushes on the first tick of
   the new minute), so entries can fire ~2–3s after close instead of ~10s.
3. **Stale "live" data on the Paper page.** It refreshes every 30s
   ([PaperTrading.jsx:153](../../../frontend/src/pages/PaperTrading.jsx)), so open
   P&L looks frozen even though the marks update intraminute.

Already correct (do **not** change): spot signals are built from the live tick
stream via `live_candle_roller.py` and evaluated on **closed 1-minute bars** (matches
the backtest, no repainting); entries fill at the live in-memory tick.

## 2. Goal

Deliver **Option A** (chosen): real-time, candle-close entries with tick-driven
fills + **tick-level exit monitoring** + a **live Paper page** — faithful to the
1-minute backtested strategy, no repainting, no broker orders. Research basis: retail
platforms (Zerodha Streak) deliberately signal on OHLC close, not LTP, to avoid
intra-candle false signals; ticks are used for *fills and exits*, not entry
evaluation.

## 3. Decisions (locked)

| Decision | Choice |
|---|---|
| Signal trigger | Real-time **candle-close** entries (not tick-level entries) |
| Exit monitoring | **Fast poll ~1.5s** against the live premium tick |
| Entry latency | **Tighten to ~2–3s** after bar close (event/poll on new bar) |
| Paper page | **Live** open-positions (P&L, premium, MTM) ~2s; history ~30s |
| Broker orders | None — paper only, permanent for now |

## 4. Architecture — two small, isolated loops + a live view

Replace the single per-minute loop with **two background tasks** split by cadence,
plus a faster Paper-page poll. Each unit has one clear purpose and is independently
testable.

```
Bar-close evaluator (per new closed 1-min bar, ~2–3s after close)
  • detect a NEW closed bar in candles_1m  • evaluate_active_deployments
  • journal signals + auto-open paper trades (fill at LIVE tick + entry friction)
  • housekeeping (throttled): 15:00 square-off, option-stream auto-follow

Live exit monitor (every ~1.5s, market hours)            [NEW: app/live_exit_monitor.py]
  • mark all OPEN trades to the live premium; auto-close on stop/target/spot-mirror/
    time-stop at the breach premium (+ exit friction); staleness-guarded
  • relies on union-subscription coverage maintained by the auto-follow (§6.1)

Paper page (frontend)
  • OPEN positions poll ~2s (live P&L/premium/MTM/distance-to-stop)
  • closed history + stats poll ~30s
```

## 5. Components

### 5.1 Component A — `app/live_exit_monitor.py` (NEW; the core fidelity win)
A tiny isolated async loop. Every **~1.5s** during NSE market hours
(09:15–15:30 IST, weekdays):
1. Call the existing `mark_open_deployment_trades(db, latest_tick_lookup=...)` — it is
   idempotent, status-conditional, staleness-guarded, and already auto-closes on
   premium stop/target + spot-mirror exits. We change only the **cadence** (1.5s vs
   60s), not the logic. (Fresh premiums for every held contract are guaranteed by the
   union-subscription coverage in §6.1 — the monitor does not manage the stream.)
2. Log auto-closes; update a status snapshot (§7).

Decoupled from the evaluator: a slow strategy evaluation can never delay an exit, and
a stuck exit can never delay evaluation.

### 5.2 Component B — tighter bar evaluation (entries ~2–3s after close)
Replace the fixed `minute+10s` sleep in the evaluator loop with **poll-for-new-bar**:
~every 2s, read the latest `candles_1m.ts` for the tracked instrument; when it is
**newer than the last-evaluated bar ts**, run `evaluate_active_deployments`
immediately, then record the new bar ts. The existing `last_evaluated_ts`
idempotency guard ([deployment_evaluator.py:352](../../../backend/app/deployment_evaluator.py))
guarantees a bar is evaluated **once**. The minute-boundary timer is kept as a
**feed-gap fallback** (if no new bar appears, fall back to the old cadence so nothing
hangs). **No-repaint guarantee:** only bars the roller has already *flushed*
(completed minutes) are ever read — the forming bucket is never evaluated.

### 5.3 Component C — live Paper page
- **Split the refresh by weight.** OPEN positions poll a lightweight `status=OPEN`
  feed every **~2s**; closed history + stats stay at ~30s. So we never re-pull the
  whole journal every 2s.
- **Always-fresh P&L:** the open-positions feed computes `unrealized_pnl` from the
  **latest in-memory tick at request time** (so it is live even between the monitor's
  writes), falling back to the persisted mark when no fresh tick exists (flagged
  stale). Reuse the existing premium-mark math; do not duplicate it.
- **Surface, per open trade:** live premium (LTP), live unrealized ₹ P&L, **distance
  to stop and to target** (₹ and %), live MFE/MAE, age/time-in-trade, and an
  `exit_price_stale` indicator. **Header:** live Open MTM + realized-today + day total.
- Same feed can power the Live Signals cockpit's "Open MTM" tile later (out of scope).

## 6. Expert additions (high-value, in-scope)

### 6.1 Subscription coverage for held contracts (fidelity-critical)
The exit monitor can only fire a stop/target if the held option has a **fresh** tick
(120s staleness guard). The auto-follow subscribes only the ATM±3 band, so a trade
whose strike drifts out of that band would **stop receiving ticks → its stop could
blow past un-monitored**. Fix: **extend the existing option-stream auto-follow** (the
single owner of the subscription) to subscribe the **union** `{ATM±N baseline} ∪
{every OPEN trade's instrument_key}`, triggered (a) the moment a paper trade
auto-opens (so its contract is live before the next exit cycle) and (b) on the
periodic cadence — idempotent, restart-only-when-the-set-changes. The exit monitor
consumes this coverage; it never manages the stream itself. This guarantees every
live position is always markable — a non-negotiable for honest paper trading.

### 6.2 Tick-level time-stop + EOD parity
Enforce the strategy's `risk_hints.time_stop_minutes` in the fast loop (close at the
live premium when elapsed), so time-based exits match the backtest at tick latency,
not minute latency. The 15:00 IST auto-square-off stays.

### 6.3 Exit-fill friction parity
Live exits must apply the **same `apply_exit_friction`** model the backtest uses when
`risk.friction.enabled` (entry already does via `build_auto_trade`). Without it, paper
exit P&L diverges from the simulation the strategy was validated on.

### 6.4 Timeliness audit (in-time signals)
Each signal already records `bar_ts` and `decision_ts`. Surface the **bar→decision
latency** (and entry fill ts) on the Signal Journal / Paper page so the user can *see*
signals are timely — directly serving "correct and in-time recommendations."

### 6.5 Trust/health visibility
The user runs this live and must trust it. Expose `/api/live-exit-monitor/status`
(running, last_run_ts, open_trades_checked, auto_closes, last_error) alongside the
existing candle-roller/stream status, and surface a compact "live engine healthy"
indicator (exit monitor + roller + stream all green, last-evaluated age) on the Live
Signals cockpit.

## 7. Observability
- `/api/live-exit-monitor/status` endpoint + an in-process status dict (mirrors
  `LiveCandleRoller.status()`).
- Structured logs on auto-close (`id/exit_reason/premium`) and on subscription
  changes.

## 8. Error handling & safety
- Market-hours + weekday gating on both loops; quiet outside hours.
- Per-iteration try/except — a single bad trade/tick never crashes a loop.
- **Idempotent, status-conditional writes** — a concurrent manual close always wins
  (`replace_one({id, status:"OPEN"})`).
- **No fill on stale data** — a >120s-old premium is treated as absent (no mark, no
  stop fire); display flags it stale.
- **No real broker orders.** Paper only.
- Write volume (N open trades × ~40/min) is trivial for this single-user stack.

## 9. Testing
- **Host-safe unit tests** (fake db + fake tick lookup, the existing pattern):
  exit monitor fires stop/target at the correct breach premium; stale tick ⇒ no
  close; idempotency; time-stop closes at the right elapsed minute; new-bar detection
  evaluates a closed bar exactly once and never the forming bucket; subscription-set
  union includes every open contract; open-positions live-P&L computation.
- **Contract tests:** the `/api/live-exit-monitor/status` route + the open-positions
  feed shape; the frontend fast-poll wiring.
- **Running-stack verification:** open a paper trade, watch the Paper page P&L tick
  live (~2s); force a stop and confirm it closes within ~1.5s at the live premium
  (not at the next minute); confirm a drifted-strike trade stays monitored.

## 10. Out of scope / future
- **Tick-native entries** (intra-bar) — a different product needing tick-level
  strategies + a tick backtest; current strategies don't need it.
- **Forward-vs-backtest parity scorecard** — compare realized paper win-rate / avg
  ₹ / expectancy against the strategy's backtested distribution, to build confidence
  before broker deployment. High value, aligns with the vision, but its own feature.
- **Broker-API live execution** — only after Signal Journal + Paper results earn
  confidence. Its own project, with its own safety design.
