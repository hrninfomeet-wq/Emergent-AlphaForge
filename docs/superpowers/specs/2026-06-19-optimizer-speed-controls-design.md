# Optimizer Speed Controls — Design

**Goal:** Make the optimizer fast and self-sizing from the UI: (1) expose the existing multi-core `opt_workers` knob as a form control, and (2) add a convergence **early-stop, default ON**, so `n_trials` is a CEILING — the search builds cumulatively and stops once the best objective stops improving (a 2000-trial ceiling won't run 2000 if it converges in a few hundred).

**Tech stack:** FastAPI + Optuna (backend), React (`Optimizer.jsx`), pytest host tests. Branch: `feat/optimizer-speed-controls`.

**Out of scope (dropped by user):** the pre-flight "recommended trials" pop-up.

---

## Confirmed facts (verified)
- `opt_workers` is fully wired on `main`: `effective_workers()` clamps to `min(requested, cpu-1)` (and the optional `AF_OPT_WORKERS` env ceiling, currently unset). Container has 16 CPUs. Bayesian-only. So a UI value flows straight through — no backend change for Piece 1.
- The optimizer's trial loop has THREE paths, all tracking `completed` and `best_so_far["trial_num"]`:
  - **grid** `optimizer.py:951-969` (`for params in combos[completed:]`)
  - **bayesian sequential** `:981-1014` (`for i in range(completed, n_trials)`, `study.optimize(n_trials=1)`)
  - **bayesian parallel** `:1016-1052` (`while completed < n_trials`, batch `B=min(workers, remaining)`, `study.ask()×B → parallel_backtest → study.tell`)
- There is NO early-stop today; `early_stop=false` must stay byte-identical to current behaviour.

## Piece 1 — Parallel-workers control (frontend only)
`Optimizer.jsx`: a number input (or 1–15 select) **"Parallel workers"**, default 1, wired into the start payload as `opt_workers`. Label: **"experimental · non-deterministic (±0.5–2% run-to-run) · higher = more RAM."** Show/enable only for `method === "bayesian"` (the knob is bayesian-only; grid ignores it). Backend already honors it.

## Piece 2 — Convergence early-stop (default ON)

### Pure decision module — `backend/app/early_stop.py` (host-TDD)
Two pure functions (no I/O, host-testable):
- `is_significant_improvement(new_value: float, anchor_value: float, min_delta: float) -> bool` — True when `new_value > anchor_value + abs(anchor_value)*min_delta` (relative threshold; handles `anchor_value == -inf` on the first trial → always True). Guards NaN.
- `should_early_stop(*, completed: int, last_improve_trial: int, warmup: int, patience: int) -> bool` — True when `completed >= warmup` AND `(completed - last_improve_trial) >= patience` AND `patience >= 1`.

### Wiring into `optimizer.py` (all three loops)
Add, alongside the existing `best_so_far` tracking, two counters initialized at study creation/resume:
```
anchor_value = best_so_far["value"]          # value at last SIGNIFICANT improvement
last_improve_trial = best_so_far["trial_num"] # trial index of that improvement (or 0 if none)
```
After each trial (grid/seq) or each batch (parallel), once `best_so_far` is updated:
```
if is_significant_improvement(best_so_far["value"], anchor_value, min_delta):
    anchor_value = best_so_far["value"]; last_improve_trial = completed
if early_stop and should_early_stop(completed=completed, last_improve_trial=last_improve_trial, warmup=warmup, patience=patience):
    early_stopped = True
    break   # leave the loop; all completed trials + best_so_far are kept
```
For the parallel batch path, `last_improve_trial` uses `completed` (end-of-batch count) — patience is measured in completed trials, which is correct and consistent across paths. After the loop, record on the job: `early_stopped: bool`, `stopped_at_trial: completed`, `trials_ceiling: n_trials`. The downstream `analyzing` stage (option-rerank + survival) runs on whatever trials completed — UNCHANGED.

### Config (payload + schema) — `OptimizerStartReq`
- `early_stop: bool = True`
- `early_stop_warmup: int = 200` (min trials before any stop)
- `early_stop_patience: int = 200` (no-improvement window)
- `early_stop_min_delta: float = 0.001` (0.1% relative; a gain smaller than this doesn't reset the patience clock)
The optimizer reads these from `payload` with the same defaults. `early_stop=false` ⇒ the `should_early_stop` branch never fires ⇒ byte-identical to today.

### Frontend control — `Optimizer.jsx`
An **"Auto-stop when converged"** toggle (default ON) with a short hint ("n_trials becomes a ceiling; stops when the best plateaus"). Advanced (optional, collapsed): patience number. Wire `early_stop` (+ patience if exposed) into the payload. Keep the form usable if the user leaves advanced untouched (defaults apply).

## Data flow
`Optimizer.jsx` form → start payload (`opt_workers`, `early_stop`, `early_stop_*`) → `OptimizerStartReq` → optimizer loop reads them → early-stop breaks the trial loop when converged → job records `early_stopped`/`stopped_at_trial` → results view shows the (possibly shortened) run.

## Error handling / edge cases
- `early_stop=false` → no behavior change (regression-pinned by the running-stack check).
- `patience < 1` or `warmup < 0` → `should_early_stop` returns False (never stops) — defensive.
- Resume path (`optimizer.py:896-913`): re-derive `anchor_value`/`last_improve_trial` from the restored `best_so_far` so a resumed run still early-stops correctly.
- Pause/cancel unaffected (separate flags, checked first in each loop).
- Multi-core non-determinism unchanged; early-stop just reads the running best.

## Testing
- **Host (pytest, no server import):** `tests/test_early_stop.py` — `is_significant_improvement` (first-trial -inf anchor, relative threshold, NaN, equal/just-below/just-above) and `should_early_stop` (before warmup, within patience, at patience boundary, patience<1). Pure, fast.
- **Running stack (controller):** rebuild backend. Start a SHORT bayesian optimization with `early_stop=true, early_stop_warmup=10, early_stop_patience=10, n_trials=500` on a quick strategy/window → confirm `n_trials_completed < 500` AND `early_stopped=true` AND `stopped_at_trial` recorded. Start the same with `early_stop=false` → runs the full ceiling (or until cancelled). Start one with `opt_workers=4` → confirms parallel path + early-stop coexist. Frontend: rebuild, confirm the two controls render and the payload carries `opt_workers`/`early_stop`.

## Verify-items (resolve during implementation)
1. In the parallel branch, confirm `best_so_far["trial_num"]` is set to a sensible completed-count when the best updates (line ~1048) so `last_improve_trial` math is correct; if it stores the Optuna trial number instead, normalize to `completed`.
2. Confirm the three payload knobs reach the loop scope (read near `n_trials = int(payload.get("n_trials", 200))`, `optimizer.py:789`).
3. Confirm `early_stopped`/`stopped_at_trial` are persisted on the finished job doc and don't break the existing results serialization.

## Risks
- **Premature stop**: a stochastic search can plateau then jump; conservative defaults (warmup 200 / patience 200) make this unlikely, and the user explicitly accepts the trade (don't burn ~1500 trials for a rare late jump). Tunable + switch-off-able.
- No broker/paper impact — optimizer only.
