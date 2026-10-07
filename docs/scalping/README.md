# Sub-minute option-buying scalping — NIFTY (NFO) and SENSEX (BFO)

_Work done 2026-10-06 → 10-07. Status: **research capability, paper/replay only. Recommendation: NO-GO for real
money.** Real-money execution is not implemented and is structurally unreachable from this code._

## The answer in one paragraph

Flattrade executes and Upstox supplies data. The Upstox feed is a ~1 Hz snapshot, not every tick. Flattrade's API
allows only LMT and SL-LMT on **both** NIFTY and SENSEX — cover and bracket orders are unavailable on both via the
API, and a resting stop for a long option is margin-rejected on this account, so all protection is software that
stops when the app stops. Measured round-trip friction is ~0.47 % (NIFTY) / ~0.46 % (SENSEX) of premium
(spread ~0.23 % + statutory ~0.23 % at the 2026 STT of 0.15 %), 25 % / 34 % of a typical one-minute move. Five
sub-minute hypothesis families were tested on executable 1 Hz quotes; the two pre-registered ones were killed on a
22-session holdout, and the remaining NIFTY spec (N1) was killed by its pre-registered replay gate (−₹15.5 per
trade over 39 trades once an adversarial review had corrected an optimistic fill model; the correction alone moved
the result by ~₹1,350 — more than any claimed edge). SENSEX
expiry day — the only untested SENSEX regime — has never been recorded. Two fully specified, pre-registered specs
(N1 NIFTY, S1-E SENSEX) are implemented in a deterministic engine with a fault-tested order lifecycle, a recorded-
tape replay, and an env-gated paper runner, so the hypotheses can be falsified forward at zero risk.

## Deliverables

| # | Deliverable | Document |
|---|---|---|
| 1 | Feasibility assessment and verified order-capability comparison | [01-feasibility-and-capabilities.md](01-feasibility-and-capabilities.md) |
| — | Measurements, NIFTY vs SENSEX differences, every hypothesis tested | [02-measurements.md](02-measurements.md) |
| 2 | Separate NIFTY and SENSEX strategy specifications | [03-strategy-specs.md](03-strategy-specs.md) |
| 3 | Execution and protection design, failure handling | [04-execution-and-protection.md](04-execution-and-protection.md) |
| 4 | Implementation (paper/replay), configuration, tests | this page, below |
| 5 | Validation: observed vs simulated vs untested | [05-validation.md](05-validation.md) |
| 6 | Go/no-go, blockers, readiness criteria | [06-go-no-go.md](06-go-no-go.md) |
| — | Pre-registrations (written before each run) and raw results | [prereg/](prereg/), [results/](results/) |

## Checkpoints (dependency order)

| CP | Step | Completion check | Status |
|---|---|---|---|
| 0 | Preserve the only depth recordings before the 30-day TTL deletes them | `tick_archive` count = source count (3,201,938) | done 2026-10-06 23:35 IST |
| 1 | Inspect app, data path, broker capabilities, regulation, competitors — six bounded research agents, each followed by an adversarial verifier for the money-critical three | every claim cited (path:line or dated URL) and labelled; verifier corrections folded in (doc 01) | done |
| 2 | Measure microstructure and the null baseline (M1, M2) | per-session tables reproducible from `backend/scripts/scalping/research/` | done |
| 3 | Screen sub-minute hypotheses (M3, M5, M6); pre-register the survivors; run each once on the holdout (M4, M7) | pre-registration file timestamped before each run | done — both killed |
| 4 | Specs for the remaining hypotheses with kill criteria (doc 03) | every threshold has a unit and a provenance tag | done |
| 5 | Engine + simulator + replay + paper runner, tests first for every safety invariant | 62 scalping tests; full suite green (6,665 / 0 failed) | done |
| 6 | Stage-2 replay against a pre-registered gate | `prereg/PREREG_STAGE2_replay.md` → `results/stage2_replay.json` | done — **N1 KILLED** (after the simulator correction), S1-E untestable |
| 7 | Adversarial code review of the new package (3 lenses, each finding independently verified) | 17 findings, all reproduced, all fixed test-first or recorded; an exit livelock found while verifying (doc 05) | done |
| 8 | Live paper (stage 3) | pre-registered sample reached | **not started — operator decision** |

Delegation record: research agents and code reviewers received a bounded task, required evidence (path:line or
dated source), read-only rules (no broker calls, no Flattrade MCP, no repo edits) and an acceptance test; their
output was reviewed and, for money-critical claims, re-verified by an independent agent before use. Strategy,
execution and capital-risk decisions were made by the lead.

## Implementation

| Path | What |
|---|---|
| `backend/app/scalping/config.py` | `NIFTY_N1`, `SENSEX_S1E` with units + provenance; `validate()` refuses any mode but paper/replay |
| `backend/app/scalping/costs.py` | 2026 statutory schedule on top of `app.option_costs.round_trip_charges` |
| `backend/app/scalping/market.py` | 1-s market state: quotes, freshness, synthetic forward, z-scores |
| `backend/app/scalping/signals.py` | the two pre-registered entry rules |
| `backend/app/scalping/engine.py` | deterministic order/position state machine, invariants I1–I10 |
| `backend/app/scalping/sim_broker.py` | execution model: latency, depth, partial fills, LPP, faults |
| `backend/app/scalping/replay.py` | recorded-tape replay (same step as the paper loop) |
| `backend/app/scalping/recorder.py` | `tick_archive` preservation (boot + daily 15:50 IST) |
| `backend/app/scalping/paper_runner.py`, `journal.py` | live-tick paper loop + Mongo journal |
| `backend/app/routers/scalping.py` | `GET /api/scalping/status`, `/config`, `/paper/trades?date=`, `/events?date=`; `POST /api/scalping/paper/kill` |
| `backend/server.py` | starts the archive loop and (if enabled) the paper runner; mounts the router |
| `backend/scripts/scalping/run_replay.py` | stage-2 replay runner |
| `backend/scripts/scalping/research/` | measurement scripts M1–M7 (read-only Mongo) |
| `tests/test_scalping_engine.py`, `tests/test_scalping_sim_costs_runner.py`, `tests/test_scalping_review_regressions.py` | 79 tests |

**Configuration (environment, `backend/.env`; a change needs `docker compose up -d --build backend`, never a plain
restart — and only when no live position is open, see `docs/HANDOFF.md` §3):**

| Variable | Default | Effect |
|---|---|---|
| `SCALP_TAPE_ARCHIVE` | on | copy full-depth NIFTY/SENSEX index + option ticks out of the 30-day-TTL `ticks` into `tick_archive` |
| `SCALP_PAPER_ENABLED` | **off** | run the selected specs on live ticks with simulated fills; journals to `scalp_events`, `scalp_paper_trades` |
| `SCALP_PAPER_STRATEGIES` | `scalp_sensex_s1e_expiry_joint_impulse` | comma-separated preset ids for the paper runner (N1 is killed, so it is not in the default) |

**Run the replay (host, read-only):**

```bash
./.venv/Scripts/python.exe backend/scripts/scalping/run_replay.py
```

**Collections added:** `tick_archive` (no TTL, ~3.2 M docs on 2026-10-06), `scalp_events`, `scalp_paper_trades`,
`scalp_engine_state`. None has a TTL or a BSON date field.
