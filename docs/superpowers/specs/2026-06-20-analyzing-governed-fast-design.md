# Governed + Fast Analyzing Stage — Design

**Goal:** The optimizer's "Analyzing" stage (option re-rank + survival + heatmap/robustness) must (1) **never silently run past a configurable wall-clock budget** (default 30 min), always surfacing live progress + ETA; and (2) run **much faster** via a vectorized option-exit walk and an opt-in, deterministic parallel sweep — with **byte-identical results** as a hard, tested invariant for every speedup.

**Why:** A confluence_scalper option_rerank ran **~10.7 h, silently** — root cause: the stage is O(K candidates × trades × per-trade option walk), sequential, single-core, with a `.iterrows()`-based inner loop, **no time budget, no progress, no ETA**. (Job `7a322591`: K=150, 7,689 trades/candidate → ~1.15M pairings.)

**Tech stack:** FastAPI + Optuna + pandas/numpy; `parallel_eval.py` fork pool; pytest. Branch: `feat/analyzing-governed-fast`.

**Backbone invariant (non-negotiable):** the deployability gate (option-rerank ranking + survival verdicts) is the most correctness-critical, least-reversible path. Therefore: **every performance change ships with a host test proving its output is byte-identical to the current sequential implementation**; governance additions are **inert** (zero behavioral change) unless the budget is actually hit. A speedup that can't be proven identical does not ship.

---

## Phasing (each phase independently shippable + parity-gated)

- **P1 — Governance** (additive; no result change unless budget hit). Ships first → the "never again" guarantee holds even before any speed work.
- **P2 — Vectorize `_walk_option_exit`** (byte-identical; speeds *every* option backtest app-wide).
- **P3 — Parallelize the rerank + survival loops** (opt-in, deterministic, byte-identical when complete).

---

## P1 — Governance: budget + ETA + heartbeat

### Config
Add to `OptimizerStartReq` (`schemas.py`): `analyze_budget_sec: int = 1800` (30 min; 0 = unlimited). Frontend (`Optimizer.jsx`): an "Analyzing time budget (min)" input in the option-execution panel, default 30, wired to the payload (`analyze_budget_sec = minutes*60`).

### A deadline that all analyzing sub-loops respect
At the `status="analyzing"` transition (`optimizer.py:1102`), stamp `analyze_started_monotonic = time.monotonic()` and read `analyze_budget_sec` from payload. Define a helper `_analyze_over_budget() -> bool` (`elapsed >= budget and budget > 0`). Check it at the top of **each candidate iteration** in BOTH the rerank loop (`:751`) and the survival loop (`:1162`), and before the heatmap/robustness passes (`:1261`). On over-budget: **break** the loop, keep everything computed so far, and record `analyze_budget_hit: true`, `analyzed_candidates: <i>/<K>`. The downstream best-pick runs on whatever completed (the candidates are processed in spot-objective order, so the partial set is the most-promising prefix — deterministic and sensible).

### Live per-candidate progress + ETA (always on)
Replace the once-only `rerank_progress` writes with a per-candidate update (throttled to ~once/sec or every candidate, whichever is cheaper): `{stage, done, total, elapsed_sec, per_item_sec, eta_sec}`. `per_item_sec` = EWMA of completed candidate wall-times (calibrated from the first candidate onward — this is the "self-measuring" ETA). The frontend `CurrentJobView` renders `Analyzing N/K · ETA mm:ss` under the status. So the user *sees* a 10h ETA within the first ~minute.

### Trade-count fail-fast note
When the best spot candidate's `trade_count` exceeds a soft threshold (e.g. 2000), include `analyze_warning: "hyper-active strategy (N trades) — option re-rank is expensive"` in the progress so it's visible immediately.

### Heartbeat logging
`log.info` the rerank/survival progress every ~10 candidates (logs are currently silent through the whole stage).

### Inertness
With `analyze_budget_sec=0` (or a budget never hit), behavior is **byte-identical to today** — the only additions are progress writes (which don't affect results). Test: a job that completes within budget produces the same `best_*`/`rerank`/`survival` as before.

## P2 — Vectorize `_walk_option_exit` (byte-identical)

`_walk_option_exit` (`option_backtest.py:107`) currently: per trade, boolean-filters the contract's candle slice by `[entry_ts, backstop_ts]`, **re-sorts** (already sorted in `candles_by_key`), and walks with **`.iterrows()`**. Replace with a numpy path:
1. The contract slice is already sorted by `ts` (built in `simulate_paired_option_trades:315`); drop the redundant `.sort_values`.
2. Bound the window with `np.searchsorted` on the `ts` array (O(log n)) instead of a boolean scan.
3. Extract `high/low/open/close/ts` as numpy arrays for the window.
4. **No-overlay path** (the common case: plain target/stop): vectorize the first-crossing — `np.argmax(low <= stop)` and `np.argmax(high >= target)`, take the earlier bar; stop-first if both hit in the same bar (mirror `intrabar_exit`'s `stop_first=True`). Return that bar's fill.
5. **Overlay path** (trailing/breakeven, `exit_cfg.enabled`): the effective stop depends on the running-max-through-prior-bar, so compute `running_max = np.maximum.accumulate(high)` shifted by one bar, derive the per-bar `eff_stop` vectorially via `effective_premium_stop`'s formula, then first-crossing as above; preserve the gap-below-fills-at-open rule (`stop_fill_price`) and the breakeven-vs-trail exit-reason attribution.

**Also (B2):** build `candles_by_key` **once** in `_option_rerank` and pass it into each `simulate_paired_option_trades` call (today each of the K calls re-groups the full candles_df — `option_backtest.py:327`). Add an optional `candles_by_key=` param to `simulate_paired_option_trades`; when provided, skip the internal groupby.

**Parity (the gate):** a host test feeds a battery of synthetic option-candle slices + trades (target-hit, stop-hit, both-in-one-bar, gap-down, no-hit→signal-exit, with and without overlay/trailing/breakeven) through BOTH the old iterrows implementation (kept as `_walk_option_exit_ref` in the test) and the new vectorized one, asserting **identical** `{exit_ts, exit_price, exit_reason}` for every case. This is the safety net; it must pass before P2 ships.

## P3 — Parallelize rerank + survival (opt-in, deterministic)

Reuse the `parallel_eval.py` fork-pool pattern. The K candidate sims (rerank) and K survival evals are independent and **deterministic** (seeded monte-carlo; fixed candidate set), so a complete parallel run is byte-identical to sequential — *stronger* than the trial parallelism (which perturbs the TPE trajectory).

### Design
- Gate behind a worker count (reuse `opt_workers`, or a dedicated `analyze_workers`; default 1 = **exact current sequential path**, untouched). Confirm which during implementation; default-1 must be byte-identical.
- Pre-load the shared read-only data in the PARENT before forking: `candles_df`, `contracts`, the raw frame (`_RAW_DF` already a parallel_eval global), the indicator cache. Workers inherit via copy-on-write (no pickling of the big frames — pass only per-candidate picklables: merged params, the candidate's resolved per-trade contract keys/expiries). NO motor/DB handle ever crosses into a worker (candles are pre-loaded → workers are DB-free → fork-safe).
- Top-level worker fns (picklable), mirroring `_worker_evaluate`: one for the rerank sim (returns option metrics for a candidate), one for the survival eval (returns the survival verdict). Each re-derives the strategy from the registry by id and re-enriches from the inherited cache.
- Collect results, **re-sort into the deterministic order** (by candidate index), then run the existing ranking/survivor-selection logic unchanged. Order-independence is the key correctness property: the final ranking/verdicts must not depend on completion order.
- **Budget interaction (P1):** with workers, check `_analyze_over_budget()` at batch boundaries; on hit, stop submitting, drain in-flight, return best of completed. (When the budget IS hit under parallelism the completed *set* is timing-dependent — acceptable: it's the exceptional partial path, flagged. When the budget is NOT hit, the result is deterministic + parity-identical.)
- **Memory/OOM guard:** forking W workers off a parent holding a multi-GB `candles_df` is the real risk (memory note: backend OOM-recycles on heavy runs). Mitigations: COW-share the candles (never per-worker copy); clamp `analyze_workers` to `min(requested, cpu-1, AF_ANALYZE_WORKERS env)`; and a conservative default. The vectorized walk (P2) also cuts each worker's working set + runtime, compounding safety.

**Parity (the gate):** a host test runs the rerank+survival on a fixture with `workers=1` and `workers=4` and asserts **identical** ranked option-pnl + survival verdicts (order-normalized). Parallel that can't prove identity does not ship.

## Components / files
- `backend/app/schemas.py` — `analyze_budget_sec`, (P3) `analyze_workers`.
- `backend/app/optimizer.py` — deadline helper + per-candidate budget/progress in the rerank (`:751`) + survival (`:1162`) loops + pre-heatmap check; (P3) parallel dispatch.
- `backend/app/option_backtest.py` — vectorized `_walk_option_exit`; `candles_by_key` param on `simulate_paired_option_trades`.
- `backend/app/parallel_eval.py` (or new `parallel_rerank.py`) — (P3) worker fns + candles/contracts globals.
- `frontend/src/pages/Optimizer.jsx` — analyzing-budget input + the live `Analyzing N/K · ETA` readout in `CurrentJobView`.
- Tests: `tests/test_walk_option_exit_parity.py` (P2), `tests/test_analyze_budget.py` (P1 pure helper), `tests/test_rerank_parallel_parity.py` (P3).

## Testing
- **Host (pytest, no server import):** P2 walk parity battery; the `_analyze_over_budget` pure helper; the ETA/EWMA helper; P3 sequential-vs-parallel parity on a fixture. (optimizer.py itself isn't host-importable → its wiring is verified on the stack.)
- **Running stack (controller):** rebuild; (P1) start an option_rerank that would exceed 30 min on a high-trade strategy → confirm it **stops at ~30 min**, returns a partial best flagged `analyze_budget_hit`, and the UI showed a live ETA; a run that fits the budget completes with results identical to a pre-change run (spot-check `best_option_pnl_value`/ranking). (P2) a before/after timing on the same job shows a large speedup with identical `rerank.ranked` option-pnl. (P3) `analyze_workers=4` vs `1` → identical ranking + survivors, faster wall-clock, no OOM.

## Risks / verify-items
1. **Vectorization correctness (P2):** the overlay (trailing/breakeven) is the subtle case — the parity battery must cover it exhaustively; if a case can't be made identical, keep the iterrows path for overlay-on and vectorize only overlay-off (still the common case). VERIFY.
2. **Parallel determinism (P3):** must prove order-independent identical verdicts; default workers=1 must be the untouched path. VERIFY.
3. **OOM under parallel + large candles:** clamp workers, COW-share, conservative default; the running-stack test must watch memory. VERIFY.
4. **Budget partial-result semantics:** a partial best must be clearly flagged in the result + UI so it's never mistaken for a full evaluation.
5. No paper/live impact — optimizer only.
