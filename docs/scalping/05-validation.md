# 05 — Validation: what was observed, what was simulated, what is untested

_Acceptance criteria for every stage were written before that stage was run (`docs/scalping/prereg/`).
"Observed" = measured on recorded market data; "simulated" = the engine's own decisions with fills from the
execution model; "untested" = no data exists yet._

## Stage map

| Stage | Data | Status | Verdict |
|---|---|---|---|
| 1. Historical screen | 1-minute warehouse (prior work) + 1 Hz recorded ticks (M1–M7) | **done** | every hypothesis family failed (doc 02) |
| 2. Recorded-event replay with execution model | 6 recorded depth sessions, full engine + `SimBroker`, 4 execution scenarios | **done** | **N1: FAIL** (not KILL) · **S1-E: not testable** (no SENSEX expiry day recorded) |
| 3. Live paper trading | live Upstox ticks, wall clock, same engine + simulator | **built, not run** (`SCALP_PAPER_ENABLED=0` by default) | criteria pre-registered (doc 03) |
| 4. Readiness review before any real-money pilot | stage-3 evidence + the prerequisites in doc 04 §9 | not reached | doc 06 |

## Stage 1 — historical screen (observed)

| Hypothesis | Sample | Pre-registered? | Result |
|---|---|---|---|
| Random entry (baseline) | discovery, ~24k entries/index | — | −0.23 % (NIFTY) / −0.21 % (SENSEX) gross per round trip; −0.47 / −0.46 % net |
| Index-impulse FADE (H1) | holdout, 22 sessions | yes | **KILLED**: 0/12 cells net-positive on either index |
| Index-impulse momentum | discovery + holdout | (mirror of H1) | NIFTY sign flips between samples; SENSEX negative in both |
| Synthetic-forward impulse | discovery | no | ≈ 0 |
| Touch order-book imbalance | discovery | no | no information (≈ baseline) |
| Passive inside-spread entry | discovery | no | worse than taking (adverse selection) |
| NIFTY → SENSEX lead–lag | discovery | no | real 1-s lag, ~0.1 % of premium vs ~0.46 % friction |
| Joint NIFTY+SENSEX impulse (S1) | holdout + discovery | yes | **KILLED**: holdout −0.50 %/trade, discovery −2.59 %, 3/18 cells positive |

## Stage 2 — recorded-event replay (simulated fills on observed quotes)

Pre-registration: `prereg/PREREG_STAGE2_replay.md`. Runner: `backend/scripts/scalping/run_replay.py`
(`results/stage2_replay.json` holds every trade). Execution model (doc 04 / `sim_broker.py`): order reaches the
exchange 140 ms after the decision (measured PlaceOrder ack p50) and can only match a LATER quote; walks the
5-level book taking 50 % of each level; no passive fills; LPP rejects; cancel/fill race.

**N1 (NIFTY), all 6 sessions (DTE filter left 4 tradeable):**

| Scenario | Trades | Net ₹ | Net / trade ₹ | Median trade ₹ | Win rate | Max drawdown ₹ | Worst losing streak | Sessions + / traded | Charges ₹ | Unfilled entries |
|---|---|---|---|---|---|---|---|---|---|---|
| base | 38 | **+745** | +19.6 | **−19.4** | 47 % | −717 | 4 | **1 / 4** | 373 | 19 |
| latency ×3 (420 ms) | 38 | +965 | +25.4 | −15.5 | 45 % | −678 | 4 | 1 / 4 | 366 | 23 |
| thin book (20 % of depth) | 38 | +739 | +19.4 | −19.4 | 47 % | −723 | 4 | 1 / 4 | 373 | 19 |
| faults (3 % reject, 2 % indeterminate, 5 % dropped + 5 % duplicate events) | 38 | +649 | +17.1 | −19.4 | 47 % | −691 | 4 | 1 / 4 | 369 | 18 |

Per session (base): 09-07 −₹197 (14 trades), 09-15 −₹164 (3), **09-29 +₹1,450 (20, daily cap hit)**, 10-06
−₹344 (1); 09-09 and 09-16 were DTE 4 (filtered). Entry fills averaged 0.27 points **below** the decision ask:
limit entries fill when the price comes back and miss trades that run away — a structural drag on any momentum
rule. Exit needed > 1 rung on 5/38 trades (max 3 rungs). Send→first fill p50 1,000 ms, p90 2,000 ms (the 1 Hz grid).

**Verdict against the pre-registered criteria:** (1) net/trade > 0 over ≥ 40 trades — **fails** (38 trades);
(2) ≥ 4/6 sessions net-positive — **fails** (1/4); (3) stress scenarios > 0 — passes; (4) safety — passes.
→ **FAIL** (not KILL: base expectancy is positive, but entirely from one session; the median trade loses).
That latency ×3 *improves* the result is itself a sign the outcome is noise-dominated, not latency-sensitive edge.

**S1-E (SENSEX, expiry day only):** 0 trades in every scenario — no SENSEX expiry day exists in the recorded
data. **Untestable.** The engine-check variant (same rules, all DTE, for machinery only): 18 trades, −₹1,723,
−₹96/trade, max drawdown −₹1,948, losing streak 5–7, entry fills 1.6 points below the decision ask.

**Safety (observed in simulation, all scenarios, both specs):** zero unintended shorts, zero positions open at a
session end, zero halts; under injected faults (2 rejects, 2 indeterminate submits, 16 dropped and 7 duplicated
order events for N1) the engine reconciled every discrepancy.

## Failure-recovery testing

| Test | Where | Result |
|---|---|---|
| Invariants I1–I10, one scenario each (ack ≠ fill, timeouts, partial fills, cancel/fill race, unknown SELL blocks new SELL, negative position halts, single working exit, ladder never gives up + LPP floor, stop-first, trail, stale feed, indeterminate submit, ack timeout, reconcile mismatch needs 2 reads, restart → reconcile, kill, daily loss, loss streak, cutoffs, rate reserve, filters) | `tests/test_scalping_engine.py` | 30 pass |
| Seeded fault fuzz (12 seeds × 1,500 s) | same | pass: broker never short, divergence ≤ 20 s |
| Harsh fuzz (300 seeds × 2,000 s, 25 % dropped / 25 % duplicated events, 10 % indeterminate, latency to 2.5 s, cancel latency to 4 s) | dev run (not in suite) | 12,621 trades, 0 shorts, worst divergence 6 s, 0 halts |
| Costs vs the verified 2026 schedule (6 worked examples) | `tests/test_scalping_sim_costs_runner.py` | pass |
| Simulator: no fill on the decision quote, partial fills vs depth, LPP reject, cancel race both ways, no passive fills | same | pass |
| Replay determinism | same | pass |
| No import path from `app.scalping` to the broker | same (AST check) | pass |
| Full host suite after integration | `pytest tests/` | 6,665 passed, 4 xfailed, 0 failed |

Two engine defects were found by these tests during development and fixed before any result was recorded: a
reconcile storm (archived orders re-read on reconcile re-triggered reconcile) and an over-strict divergence rule.

## Sample limitations (why none of this is conclusive in either direction)

* Discovery: 6 sessions, 2 full; NIFTY sessions skew to expiry days (3/6), SENSEX has none.
* Holdout: 22 sessions with LTP only; executable prices approximated as LTP ± half the measured spread; Upstox
  frame clock instead of the local clock.
* Every stage-1 family other than H1/S1 was evaluated only in-sample.
* Stage 2 re-uses the discovery sessions; it can kill a spec, it cannot validate one.
* The 1 Hz feed hides intra-second paths; queue position and passive fill probability are unobservable.
* Sessions available are those when the operator's PC happened to be on; their regime mix is not random.

## What would change the conclusion (and how to measure it)

1. **Forward data.** 20+ fresh NIFTY sessions and 12+ SENSEX expiry days recorded with depth (paper runner on).
2. **A faster, finer feed.** Measure the Flattrade depth WebSocket's update rate (read-only); if it is
   event-driven rather than 1 Hz, sub-second microstructure becomes testable.
3. **Futures in the tape.** NIFTY/SENSEX near-month futures lead both the index and (probably) option quotes.
4. **Real fill data.** Even paper cannot show queue position or fill probability; only a tiny, separately
   authorised live pilot can, and only after doc 04 §9.
