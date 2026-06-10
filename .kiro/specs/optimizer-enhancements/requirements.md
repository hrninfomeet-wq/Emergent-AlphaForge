# Requirements Document

## Introduction

This spec defines a set of enhancements to the existing Auto-Optimizer in the AlphaForge trading research application. The Auto-Optimizer runs Optuna-based Bayesian (TPE), Grid, and Genetic (CMA-ES) parameter searches over trading-strategy parameters and reports the best parameters, a robustness score, parameter importance, a 2D heatmap, and a saved "best" backtest run. The relevant code spans `backend/app/optimizer.py`, the `/api/optimize/start` route with the `OptimizerStartReq` model in `backend/server.py`, and the `frontend/src/pages/Optimizer.jsx` page.

The enhancements address four areas:

1. Make the optimizer guard rails optional through a single UI toggle (default ON), so the optimizer purely maximizes the selected objective when the operator chooses, without silently overriding it.
2. Raise the trial-budget ceiling exposed in the UI from 1000 up to the backend-supported maximum of 5000, with validation and an overfitting caution note.
3. Persist the Optimization Setup panel configuration across navigation using localStorage, mirroring the existing BacktestLab pattern.
4. Document parallel/concurrent trial execution as a deferred future enhancement that is explicitly out of scope for the current work.

The Auto-Optimizer is a research tool. It does not place orders and is independent of the project-wide constraint that paper, forward, and live signal flows are never blocked by trading rules. That constraint is noted here only for context and does not impose requirements on this spec.

A guiding principle agreed with the operator: the optimizer's job is to maximize the selected objective. Profitability is what matters, not balanced CE/PE trade counts. Strategy signal logic must remain unchanged; these requirements affect only optimizer scoring behavior and UI.

## Glossary

- **Auto_Optimizer**: The backend optimization engine in `backend/app/optimizer.py` that runs parameter searches and produces best parameters, robustness, importance, heatmap, and a saved best backtest run.
- **Optimization_Setup_Panel**: The configuration panel on the Optimizer page (`frontend/src/pages/Optimizer.jsx`) where the operator sets instrument, mode, strategy, method, objective, trial budget, costs, guards, and other run settings.
- **Guard_Rail**: An optimizer scoring rule that disqualifies a degenerate trial by returning the `_DISQUALIFY` sentinel (-1e9) from `_objective_value()` in `backend/app/optimizer.py`, steering the search away from that parameter set.
- **Min_Trades_Floor**: The `min_trades` guard rail, a statistical-significance floor (default 10) that disqualifies any trial whose trade count is below the floor.
- **CE_PE_Direction_Share**: The `min_direction_share` guard rail, a one-sided guard requiring the minority option side (CE versus PE) to hold at least a configured share of trades; a value of 0 disables the guard. Surfaced in the UI as `min_direction_pct` (percent, 0–50).
- **Objective**: The metric the Auto_Optimizer maximizes, one of `sharpe`, `profit_factor`, `total_pnl_pts`, `net_pnl_inr`, `win_rate`, `neg_max_dd`, or `risk_adjusted`.
- **Trial_Budget**: The number of optimization trials to run, represented by `n_trials` on `OptimizerStartReq` and the "Trial budget" control in the UI.
- **Bayesian_Method**: Optuna TPE sampler search (`method = "bayesian"`).
- **Grid_Method**: Cartesian-product grid search with uniform sub-sampling (`method = "grid"`).
- **Genetic_Method**: Optuna CMA-ES sampler search (`method = "genetic"`).
- **Walk_Forward_OOS**: Out-of-sample walk-forward evaluation that splits data into sequential in-sample and out-of-sample windows to detect overfitting.
- **Robustness_Perturbation**: The analysis that perturbs each numeric best-parameter by ±10% and ±20%, re-evaluates, and reports the fraction of perturbations that remain acceptable as a 0–100 robustness score.
- **Setup_Config**: The serializable set of operator-modified Optimization_Setup_Panel fields (instrument, mode, strategy, method, objective, n_trials, costs, guard toggle and values, optimize_indicator_periods, param_overrides, date window, and name).

## Requirements

### Requirement 1: Optional Guard Rails via UI Toggle

**User Story:** As a strategy researcher, I want a single toggle to turn the optimizer guard rails on or off, so that I can let the optimizer purely maximize my selected objective when I judge a profitable one-sided or low-trade-count strategy acceptable.

#### Acceptance Criteria

1. THE Optimization_Setup_Panel SHALL display a single Guard_Rail toggle control whose default state is ON.
2. WHILE the Guard_Rail toggle is ON, THE Auto_Optimizer SHALL apply the Min_Trades_Floor, which is operator-configurable as an integer from 0 to 10000 with a default value of 10.
3. WHILE the Guard_Rail toggle is ON, THE Optimization_Setup_Panel SHALL expose the CE_PE_Direction_Share as a separate optional sub-control accepting a percentage from 0 to 100 with a default value of 0.
4. WHERE the CE_PE_Direction_Share value is 0, THE Auto_Optimizer SHALL apply no one-sided CE versus PE disqualification.
5. WHILE the Guard_Rail toggle is OFF, THE Auto_Optimizer SHALL score each trial solely by the selected Objective with no Min_Trades_Floor disqualification and no CE_PE_Direction_Share disqualification.
6. IF a trial produces zero trades, THEN THE Auto_Optimizer SHALL score that trial below every trial that produced at least one trade, such that a zero-trade trial is never selected as the best solution, regardless of the Guard_Rail toggle state.
7. WHEN the operator starts an optimization with the Guard_Rail toggle OFF, THE Auto_Optimizer SHALL submit a Min_Trades_Floor of 0 and a CE_PE_Direction_Share of 0 in the start request.
8. WHEN the operator starts an optimization with the Guard_Rail toggle ON, THE Auto_Optimizer SHALL submit the operator-configured Min_Trades_Floor and CE_PE_Direction_Share values in the start request.
9. THE Auto_Optimizer SHALL leave strategy signal-generation logic unchanged, applying guard behavior only within trial scoring.
10. THE Optimization_Setup_Panel SHALL retain and display the Walk_Forward_OOS and Robustness_Perturbation safeguards as the primary overfitting controls independent of the Guard_Rail toggle state.
11. IF the Guard_Rail toggle is ON AND a trial's executed trade count is below the Min_Trades_Floor, THEN THE Auto_Optimizer SHALL disqualify that trial by scoring it below every trial whose trade count meets or exceeds the Min_Trades_Floor, such that the disqualified trial is never selected as the best solution.
12. IF the Guard_Rail toggle is ON AND the CE_PE_Direction_Share value is greater than 0 AND either the CE-side or PE-side share of a trial's trades is below the CE_PE_Direction_Share percentage, THEN THE Auto_Optimizer SHALL disqualify that trial by scoring it below every trial that satisfies the CE_PE_Direction_Share threshold, such that the disqualified trial is never selected as the best solution.

### Requirement 2: Relaxed Trial-Budget Limit

**User Story:** As a strategy researcher, I want to set the trial budget up to the backend maximum from the UI, so that I can run larger searches over large parameter spaces without editing the request manually.

#### Acceptance Criteria

1. THE Optimization_Setup_Panel SHALL provide a Trial_Budget input control that accepts any integer value from 10 to 5000 inclusive.
2. WHEN the operator submits a Trial_Budget that is an integer within the range 10 to 5000 inclusive, THE Auto_Optimizer SHALL accept the request and queue the optimization job.
3. IF the operator submits a Trial_Budget below 10 or above 5000, THEN THE Auto_Optimizer SHALL reject the request with a validation error indicating the allowed range is 10 to 5000.
4. IF the operator submits a Trial_Budget below 10 or above 5000, THEN THE Auto_Optimizer SHALL leave the optimization queue unchanged and SHALL NOT queue the optimization job.
5. IF the operator enters a Trial_Budget that is empty or not a whole integer, THEN THE Optimization_Setup_Panel SHALL reject the entry with a validation error indicating that a whole integer from 10 to 5000 is required and SHALL block submission of the request.
6. THE Optimization_Setup_Panel SHALL display a caution note stating that a higher Trial_Budget increases overfitting risk.
7. THE Optimization_Setup_Panel SHALL display guidance that the marginal benefit of additional trials decreases for small parameter search spaces and that the Trial_Budget should be scaled to the parameter search-space size.

### Requirement 3: Persist Optimization Setup Across Navigation

**User Story:** As a strategy researcher, I want my Optimization Setup configuration to be remembered when I leave the Optimizer page and return, so that I do not have to re-enter my settings after visiting other pages.

#### Acceptance Criteria

1. WHEN the operator modifies any Setup_Config field in the Optimization_Setup_Panel, THE Optimization_Setup_Panel SHALL write the complete current Setup_Config to a namespaced localStorage key dedicated to the Optimizer page within 1 second of the modification.
2. WHEN the Optimizer page mounts and a valid stored Setup_Config exists under the namespaced localStorage key, THE Optimization_Setup_Panel SHALL re-hydrate every persisted Setup_Config field from the stored value before the operator can start an optimization run.
3. THE Optimization_Setup_Panel SHALL persist the instrument, mode, strategy, method, objective, n_trials, costs, Guard_Rail toggle state, Min_Trades_Floor value, CE_PE_Direction_Share value, optimize_indicator_periods, param_overrides, date window, and name fields.
4. IF the stored Setup_Config is missing, empty, or cannot be parsed as a valid Setup_Config, THEN THE Optimization_Setup_Panel SHALL initialize with its default Setup_Config, SHALL overwrite the unparsable stored value with that default Setup_Config, and SHALL NOT surface a parse error to the operator.
5. WHEN the Optimizer page mounts with no stored Setup_Config, THE Optimization_Setup_Panel SHALL present a fresh setup populated with default values that permit starting a new optimization run without further required input.
6. THE Optimization_Setup_Panel SHALL exclude transient run state (run status, progress percentage, in-progress trial counters, and run results) from the persisted Setup_Config so that a new optimization run can be started immediately on mount.
7. IF writing the Setup_Config to localStorage fails because storage is unavailable or the storage quota is exceeded, THEN THE Optimization_Setup_Panel SHALL continue operating using the in-memory Setup_Config and SHALL NOT surface a storage error to the operator.

### Requirement 4: Parallel/Concurrent Trial Execution — REMOVED FROM PLAN

**Status:** Permanently removed. After careful evaluation (documented in HANDOFF.md and the session that produced chk_000031), this was determined to be the wrong approach for this project:

- For Bayesian TPE (the recommended method), parallelism degrades sample efficiency — each trial learns from the last; batching produces worse suggestions.
- More trials raises overfitting risk; "faster so I can run more trials" can actively hurt profitability.
- True process parallelism (Windows `spawn`) conflicts with the Pause/Resume/crash-resume design built in Slice 3 and adds Windows-specific complexity (strategy registry rebuild per worker, no shared Mongo client, per-worker DataFrame copies).
- The actual bottleneck (per-bar DataFrame access) was solved by a non-parallel approach: pre-materializing rows as dict records (~8.8x speedup, Slice 1), plus `detect_fvg` vectorization. These deliver most of the speed benefit cleanly.

**Better alternatives that were implemented or remain available:**
- Slice 1 (done): dict records ~8.8x; vectorized `detect_fvg`.
- Future if needed: split signal-generation from trade-simulation so threshold/exit sweeps reuse pre-computed bar signals; memoize duplicate param evaluations; multi-fidelity pruning (Optuna Hyperband); numba JIT the hot loop (user granted dependency permission). Scope any of these to a focused slice only if profiling shows a genuine bottleneck.
- If grid sweeps specifically need speed: a narrow per-method parallelism for grid/random (not Bayesian) could be added in isolation, but is not prioritized.
