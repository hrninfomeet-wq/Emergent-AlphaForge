# Scenario-Adaptive Option-Buying Framework — Design Spec

**Date:** 2026-06-18
**Branch:** `feat/scenario-adaptive-framework` (off `feat/backtest-exit-controls` tip ce752f6)
**Status:** Design — pending adversarial audit + user review

---

## 0. Goal, evidence, and non-goals

**Goal.** Re-orient the strategy methodology toward profitable option **buying** by *reading the market scenario and routing the right behavior + right-sized exit to it* — built as a **general, reusable framework any strategy (current or future) can adopt**, NOT a single tuned strategy.

**Why (evidence, this session, NIFTY solid window 2025-10→2026-06).** The prior method (pick a technical signal → tune params → optimize spot metrics) is structurally mismatched to option buying:
- Moves are abundant (median capturable from 09:30 = 154pts; 80% of sessions ≥100pts) — the problem was never "no moves."
- Naive opening-drive follow-through is a **coin flip** (54% continue, MFE/MAE 1.01) — applying *one* behavior to all days is why every momentum strategy fails.
- **The edge is conditional on the opening-range width (known by ~09:45):** NARROW/quiet open → drive **continues** (62%, MFE/MAE 1.89, trend-follow with a let-run target); WIDE/volatile open → drive **fades** (45%, MFE/MAE 0.72, fade back toward the OPEN, which it reaches 62% of the time). Stable across 3 sub-periods.
- Regime-routing turns the coin-flip into a **positive spot edge** (+10/+20/+33 pts/trade at 50/80/120-pt stops).

**Honest caveat (the gate exists because of this).** The validated edge is **+EV in spot points**; +33 spot pts ≈ +16 premium pts on an ATM option *before* costs — so it is **marginal-to-positive in option-₹, not proven.** Therefore the first build is a **gated proof** evaluated as an option buyer net-of-cost; nothing generalizes until that survives.

**Non-goals.** No broker orders (paper only). No new regime/CPR/option-pricing primitives (reuse). No multi-instrument tuning, no live/paper wiring, and no per-trial option optimizer in the proof — all deferred behind the gate.

## 1. Architecture — four thin, independently-testable layers

1. **Classification** — `app/scenario_classifier.py` (PURE): `classify_scenario(regime, orb_width_pct, day_type, nr7, atr_ratio, vix_bucket, *, thresholds) -> str` in `{TREND_CONTINUATION, VOLATILE_FADE, CHOP, NONE}`. It **re-combines pre-computed columns only** — never re-derives adx/atr/chop/regime. Thresholds default to the CPR 30th/70th-percentile semantics, overridable via kwargs (instrument portability).
2. **Feature** — extend `indicators.precompute_all_indicators()` with two CAUSAL columns: `orb_width_pct_prior` (prior completed session, `shift(1)`, always available) and `orb_width_pct_partial` (current session, `None` until ≥N bars — copies the `opening_range_adaptive._opening_range` no-look-ahead guard). Computed exactly like `cpr_width_pct` (groupby `session_date` → per-session value → join back), normalized `100*(orb_hi-orb_lo)/cpr_p` so it is **scale-free / instrument-portable** like CPR width. The scenario's `or_minutes` is its OWN param, independent of any strategy's ORB window. *(Goes in `indicator_groups.py` as a new param-independent group, since `_compute_orb_for_session` in backtest.py is gated to `opening_range_breakout`.)*
3. **Routing** — `app/strategies/scenario_routing_base.py`: `ScenarioRoutedStrategyBase(StrategyBase)`, mirroring `AdaptiveStrategyBase`. `__init_subclass__` merges params AND **validates at class-definition time** that every key a strategy's `_core_signal` can return is in its declared `scenarios_traded` (no runtime KeyError). `_core_signal -> (direction, score, reasons, blockers, scenario_key)`; `evaluate()` routes `scenario_key` → the exit plan and attaches `scenario` onto the `Signal`.
4. **Exit dispatch** — `app/scenarios.py`: `exit_plan(scenario, ctx) -> {target_pts | level_target, stop_pts, trail_cfg, mode}`. Single source of exit semantics, with the discovered edge baked as **optimizable defaults** (not hard-coded): `TREND_CONTINUATION` = let-run (wide target ~90-200pts + trailing); `VOLATILE_FADE` = fade-to-OPEN (absolute `level_target`); `CHOP` = small scalp.

## 2. Signal / Trade context plumbing (backward-compatible)

Extend `Signal` (base.py:15-24) with optional `scenario: Optional[str]=None`, `spot_target_level: Optional[float]=None`, `exit_mode: Optional[str]=None`. Extend `Trade` (backtest.py:22-49) with `scenario: str=''` and `spot_target_level: Optional[float]=None`, snapshotted at entry next to the existing `regime`/`ist_time`. All optional → no migration, existing strategies unchanged. Scenario is an immutable string carried read-only Signal→Trade; **nothing is written into `ctx_global`** (preserves the in-place ctx-reuse invariant the loop documents, and the T9 micro-opt).

## 3. Level-based exit primitive (the ONE genuinely new piece of infra)

**Confirmed:** the spot loop (backtest.py:140-156) is 100% delta-based (entry±pts); there is no absolute-price target path, so `VOLATILE_FADE`-to-OPEN cannot be expressed today. Add `level_exit_decision(level_target, is_long, high, low, base_stop, ...)` (in `exit_controls.py` or a small `exit_controls_level.py`) that **delegates to the existing `exit_engine.intrabar_exit()`** with `target=level_target` (absolute) and the stop still a delta — so the stop-first pessimistic rule is the SAME audited code for delta- and level-targets (no fill-rule fork). Wire a **parallel branch** into the backtest loop: when `Trade.spot_target_level is not None`, resolve the target as the absolute level instead of `entry+tgt_pts`.

**Option pairing needs NO engine change for the proof:** `VOLATILE_FADE` uses `exit_mode="spot_exit"`, so the option mirrors the spot trade's exit timestamp/price — i.e. when the spot trade exits at the open level, the paired option exits with it. *(VERIFY in P3: confirm `simulate_paired_option_trades` spot_exit path keys off the spot trade's exit_ts, not a recomputed pts target.)*

## 4. Optimizer re-orientation to OPTION-₹ — DESIGNED, DEFERRED to post-proof (P5)

The headline correctness bug (verified): `_objective_value` (optimizer.py:100-146) scores only spot metrics; `net_pnl_inr` is a naive `pts×lot` proxy, not premium; real option-rupee exists only post-hoc in the stage-2 rerank. So a search tunes for spot while deployment picks on option-₹ — the documented misalignment + the prior failure mode.

**The proof does NOT need this** — it validates via the **existing** `evaluation_mode=option_rerank` + survival flow (search spot, rerank+survive on real paired-option ₹), exactly the gauntlet the prior edge-hunt used. The full fix (per-trial option scoring) is **P5, only if the proof survives**: `_objective_value_option` (`net_inr_option`/`calmar_option`), `_option_paired_eval` + `_preload_option_context` (load contracts/candles ONCE/job, cache), gated by a new opt-in `option_optimize_full` (default off → spot path byte-identical), a coverage-floor `_DISQUALIFY`, and `survival.objective="net_inr_option"` + optional `min_option_pnl_inr`.

## 5. Validation chain (reuse, do not rebuild)

The proof must pass, in order: (i) net-of-cost paired-option backtest (`simulate_paired_option_trades`, costs on); (ii) per-fold OOS rupee **survival** gate; (iii) **WFO** OOS stitch (option-aware); (iv) `deployment_quality.evaluate_source_quality` (deflated-Sharpe, ruin, coverage, the spot↔option correlation/`objective_misalignment` warning shipped this session). All already exist.

## 6. The proof + the HARD generalization gate

**First build = `opening_range_regime_router` (ORR)** as an option buyer, end-to-end. **GATE (binary):** ORR is "viable" iff it yields ≥1 OOS option-rupee **survivor** with positive net option-₹ AND clears `deployment_quality`, on NIFTY 2025-26, costs on. **If it fails → STOP**: do not build P5 (optimizer deepening) and do not generalize the routing base. This encodes the user's "do not generalize before the edge survives costs" as a process gate, not a comment. *(Layers 1c/1d/§3 ship behind ORR's usage only until the gate clears.)*

## 7. Phasing

- **P1 — Classification + feature (pure, no behavior change):** `scenario_classifier.py` + `orb_width_pct_prior/partial` columns + unit tests (classification table, look-ahead guard, precondition-vs-`classify_regime_series`). Ships dark; no strategy uses it.
- **P2 — Plumbing + routing base + exit dispatch:** optional Signal/Trade fields, `scenario_routing_base.py` (with `scenarios_traded` validation), `scenarios.py` `exit_plan` defaults. Backward-compatible.
- **P3 — Level-exit primitive:** `level_exit_decision` (delegating to `intrabar_exit`) + the parallel level-target branch + `spot_target_level` plumbing + a **parity regression test** (identical fill bar/level across spot, option-mirror). Unblocks `VOLATILE_FADE`.
- **P4 — ORR proof + GATE:** `opening_range_regime_router.py`; run via `evaluation_mode=option_rerank` + survival on NIFTY 2025-26 costs-on; apply the binary gate. Fail → STOP + report.
- **P5 (only if P4 survives) — Optimizer option-₹ deepening** (§4).
- **P6 (only if P4 survives) — Generalize:** offer `ScenarioRoutedStrategyBase` to other strategies; live/paper spot-level parity; multi-instrument threshold portability.

## 8. Reuse map (anti-duplication contract)

`regime.classify_regime_series` (regime input, read-only) · `cpr.cpr_levels` (day_type, cpr_width_pct, cpr_p denominator, 30/70 pctile thresholds) · `indicators.nr7` (causal, secondary filter) · `opening_range_adaptive._opening_range` (no-look-ahead pattern to copy) · `exit_engine.intrabar_exit` (the ONE fill rule level_exit delegates to) · `option_backtest.simulate_paired_option_trades`/`_walk_option_exit`/coverage (option-₹ truth) · `survival` (OOS gate) · `wfo`/`walkforward` (option OOS) · `deployment_quality` (trust verdict + correlation warning) · `adaptive_base` (template for the routing base) · `schemas.OptimizerStartReq`/`SurvivalConfigReq` (already plumb evaluation_mode/option_config/survival). **Do not duplicate any of these.**

## 9. Risks & mitigations

- **Look-ahead on orb_width** → ship `orb_width_pct_prior` (causal `shift(1)`) as the default classifier input; `_partial` returns `None` until ≥N bars; a test asserts no future bar feeds the current classification.
- **The spot edge dies to option costs** (the headline risk) → that is exactly what the P4 gate measures; STOP if it fails.
- **Exit-rule drift** (spot vs option vs live) → `level_exit_decision` delegates to `intrabar_exit`; a parity regression test asserts identical fill bar/level.
- **Threshold fragility / instrument portability** → thresholds parametrized; `orb_width` normalized by `cpr_p` (scale-free).
- **Scenario-key KeyError** → validated at class-definition time + defensively in `evaluate()`.
- **Duplication/drift** → classifier is PURE over pre-computed columns; a precondition test pins its inputs to `classify_regime_series` output.
- **Overfitting the scenario thresholds** → WFO OOS + survival + deflated-Sharpe; the edge must survive, not just fit.
- **Over-generalization** → §6 hard gate.

## 10. Testing

- `tests/test_scenario_classifier.py` — classification truth table + the look-ahead guard + a precondition test (inputs match `classify_regime_series`). Host-TDD (pure module).
- `tests/test_scenario_adaptive_exits.py` — `exit_plan` per-scenario outputs + the level-exit fill parity vs `intrabar_exit`. Host-TDD.
- The ORR option-rupee survival is verified on the **running stack** (P4), not host (it needs option data + the optimizer/survival flow).
- Host tests never import `server`/`optimizer`/`runtime`/`paper_auto`; run from repo root `python -m pytest tests/...`.

## 11. Verify during implementation (resolve before the dependent step)

1. **(P3, load-bearing)** Confirm `simulate_paired_option_trades` `spot_exit` keys the option exit off the spot trade's `exit_ts`/price (so a spot level-exit propagates to the option) — if it instead recomputes a pts target, `VOLATILE_FADE` option pairing needs a level→pts bridge.
2. **(P1)** Pin the exact `atr_ratio` column the classifier consumes (precomputed vs derived) so it does not secretly re-derive ATR.
3. **(P1/P2)** Confirm `vix_bucket` is available as a per-bar row column at `evaluate()` time (not an async context call) for the hot loop.
4. **(P4/P5)** Confirm the capital figure for any `calmar_option`/RoR threads from `option_config.sizing_config.capital`.
