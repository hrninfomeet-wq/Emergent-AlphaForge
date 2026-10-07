# 05 — Validation: what was observed, what was simulated, what is untested

_Acceptance criteria for every stage were written before that stage was run (`docs/scalping/prereg/`).
"Observed" = measured on recorded market data; "simulated" = the engine's own decisions with fills from the
execution model; "untested" = no data exists yet._

## Stage map

| Stage | Data | Status | Verdict |
|---|---|---|---|
| 1. Historical screen | 1-minute warehouse (prior work) + 1 Hz recorded ticks (M1–M7) | **done** | every hypothesis family failed (doc 02) |
| 2. Recorded-event replay with execution model | 6 recorded depth sessions, full engine + `SimBroker`, 4 execution scenarios | **done** (re-run 2026-10-07 after the review corrected the simulator) | **N1: KILLED** (base net expectancy ≤ 0) · **S1-E: not testable** (no SENSEX expiry day recorded) |
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
(`results/stage2_replay.json` holds every trade). Execution model (doc 04 / `sim_broker.py`, corrected after the
adversarial review): an order reaches the exchange 140 ms after the decision (measured PlaceOrder ack p50) and its
first match is the FIRST snapshot ingested after arrival; it walks the 5-level book taking 50 % of each level, and
each snapshot is consumed at most once; the remainder rests and fills only at its OWN limit when the opposite side
crosses it; no passive fills; LPP rejects against the current quote; cancel/fill race.

**N1 (NIFTY), all 6 sessions (DTE filter left 4 tradeable):**

| Scenario | Trades | Net ₹ | Net / trade ₹ | Median trade ₹ | Win rate | Max drawdown ₹ | Worst losing streak | Sessions + / traded | Charges ₹ | Unfilled entries |
|---|---|---|---|---|---|---|---|---|---|---|
| base | 39 | **−605** | **−15.5** | −36.4 | 44 % | −981 | 4 | **1 / 4** | 376 | 8 |
| latency ×3 (420 ms) | 39 | +846 | +21.7 | −9.3 | 49 % | −723 | 3 | 2 / 4 | 379 | 11 |
| thin book (20 % of depth) | 39 | −622 | −15.9 | −36.4 | 44 % | −996 | 4 | 1 / 4 | 376 | 8 |
| faults (3 % reject, 2 % indeterminate, 5 % dropped + 5 % duplicate events) | 39 | −640 | −16.4 | −33.5 | 44 % | −1,017 | 4 | 1 / 4 | 375 | 10 |

Per session (base): 09-07 −₹274 (14 trades), 09-15 −₹225 (4), 09-29 −₹318 (20, daily cap hit), 10-06 +₹211 (1);
09-09 and 09-16 were DTE 4 (filtered). Send → first fill p50 643 ms, p90 1,076 ms. Exits needed a second rung on
3 of 39 trades.

**Verdict against the pre-registered criteria:** base net expectancy −₹15.5/trade ≤ 0 → **KILL**. Criteria (1)
and (2) also fail; safety (4) passes in every scenario.

**The result swings with fill-model assumptions by more than any claimed edge.** Under the first, optimistic
simulator (a resting order filled at a better displayed price, a snapshot re-consumed every second, first match on
the last snapshot of the next second) the same rule on the same tape showed base **+₹745** over 38 trades. Correcting
the simulator moved it by ~₹1,350. Raising latency to 420 ms flips one session (09-29) from −₹318 to +₹1,145. A
result that sensitive to execution details is noise, not edge — and it is exactly why sub-minute rules cannot be
judged on 1-minute bars.

**S1-E (SENSEX, expiry day only):** 0 trades in every scenario — no SENSEX expiry day exists in the recorded data.
**Untestable.** The engine-check variant (same rules, all DTE, machinery only): 18 trades, −₹1,449,
−₹80.5/trade, max drawdown −₹1,775, losing streak 5–7.

**Safety (observed in simulation, all scenarios, both specs):** zero unintended shorts, zero positions open at a
session end, zero halts; under injected faults (2 rejects, 2 indeterminate submits, 16 dropped and 7 duplicated
order events for N1) the engine reconciled every discrepancy.

## Adversarial review of the implementation (2026-10-07)

Three independent reviewers (safety-invariant breaker, look-ahead/simulation realism, runtime blast radius)
reported 17 findings with reproduction scripts; independent verifiers confirmed 3 before a usage limit stopped the
rest, and the lead re-ran every remaining reproduction (all reproduced). All were fixed test-first
(`tests/test_scalping_review_regressions.py`, 16 tests, each failing before its fix).

| # | Finding | Severity | Fix |
|---|---|---|---|
| 1 | An order missing from ONE reconcile read (or a failed read) was declared never-accepted → a second SELL could be sent while the first was in flight → unintended short | critical (latent with the simulator; real with a broker) | I12: two consecutive successful reads ≥ 2 × ack timeout after sending; a failed read changes nothing |
| 2 | A refused or lost cancel was never re-sent → the exit rested at a stale price forever | critical (latent) | cancel re-armed with backoff (1, 2, 4 … 30 s) |
| 3 | An order found unchanged by a reconcile stayed "unknown" → after a restart the exit ladder never resumed | major | any order found by a successful read is known again |
| 4 | Paper kill dropped the cancels it generated → a working entry filled after the kill | major | the runner executes the kill's actions; snapshot right after |
| 5 | Paper restart wiped the day's loss pause, trade count and P&L on the first second | major | full day state restored (`restore_day_state`) |
| 6 | Any Mongo error outside the engine step silently killed the paper loop | major | every iteration guarded, logged, `status.last_error`; setup retried |
| 7 | Simulator LPP check used a stale cached quote → a sold-off exit was rejected forever | major | current reference quote |
| 8 | Simulator re-consumed the same snapshot every second; resting orders filled at better-than-limit prices; first match on the wrong snapshot | major (biased results optimistic) | per-order snapshot cursor; resting fills at own limit; ingest-ordered tick feed |
| 9 | `last_quote` leaked between round trips → phantom stale-feed exit priced off another contract | major | scoped to the trip's contract |
| 10 | A fill without a price was valued at ₹0 (crash / fabricated loss) | major (latent) | I11: unpriced fill ⇒ reconcile; only non-price exits until priced |
| 11 | COMPLETE without a parsable fill quantity was booked as unfilled while the broker held the long | major (latent) | I11: trip kept open, reconcile required |
| 12 | Open paper position at the session rollover was discarded silently | minor | journaled `paper_session_end_open_position` |
| 13–15 | status/events routes without supporting indexes; boot archive ran during market hours and unbounded on an empty archive | minor | indexes created at startup; boot catch-up deferred while open; scan bounded to 31 days |
| 16 | Synthetic forward re-selects the ATM strike each second (fake level jumps) | minor (5 of 245 triggers differ on 09-29) | documented, not changed |

**Found by the lead while verifying the fixes — exit livelock (I13).** When exchange latency exceeded the 1-s
exit re-price interval, every exit was cancelled before it reached the book; 49 of 300 harsh-fuzz seeds held a long
for over 5 minutes. The re-price clock now starts when the broker reports the order OPEN (fallback 3 × the wait) and
the wait grows per rung. After the fix: 0 of 300 seeds stuck, worst hold 93 s under 2.5 s latency, 25 % dropped
events and thin books. A mutation check confirms the regression test fails on the old timing.

## Failure-recovery testing

| Test | Where | Result |
|---|---|---|
| Invariants I1–I10, one scenario each (ack ≠ fill, timeouts, partial fills, cancel/fill race, unknown SELL blocks new SELL, negative position halts, single working exit, ladder never gives up + LPP floor, stop-first, trail, stale feed, indeterminate submit, ack timeout, reconcile mismatch needs 2 reads, restart → reconcile, kill, daily loss, loss streak, cutoffs, rate reserve, filters) | `tests/test_scalping_engine.py` | 30 pass |
| Review regressions I11–I13 + simulator realism + runner robustness (16, each failing before its fix) | `tests/test_scalping_review_regressions.py` | 16 pass |
| Seeded fault fuzz (12 seeds × 1,500 s) | same | pass: broker never short, divergence ≤ 20 s |
| Harsh fuzz (300 seeds × 2,000 s, 25 % dropped / 25 % duplicated events, 10 % indeterminate, latency to 2.5 s, cancel latency to 4 s, depth down to 2 %) | dev run (not in suite) | after all fixes: 15,597 trades, 0 shorts, 0 positions stuck > 300 s (worst hold 93 s), worst divergence 9 s, 0 halts |
| Price-jump fuzz (reviewer's, 40 seeds, sudden −45 % / +60 % moves) | dev run | 0 of 40 seeds stuck (16 of 40 before the LPP fix) |
| Costs vs the verified 2026 schedule (6 worked examples) | `tests/test_scalping_sim_costs_runner.py` | pass |
| Simulator: no fill on the decision quote, partial fills vs depth, LPP reject, cancel race both ways, no passive fills | same | pass |
| Replay determinism | same | pass |
| No import path from `app.scalping` to the broker | same (AST check) | pass |
| Full host suite after integration and the review fixes | `pytest tests/` | 6,682 passed, 4 xfailed, 0 failed |

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
