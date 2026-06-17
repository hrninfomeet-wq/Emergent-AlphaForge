# Optimizer/Backtest Speedup + Advisory Trust Warnings — Design Spec

**Date:** 2026-06-17
**Branch:** `feat/optimizer-speedup` (off `feat/backtest-exit-controls` tip)
**Status:** Design — pending adversarial audit + user review

---

## 1. Goal

Make optimizer and backtest trial runs **materially faster** and add **free trustworthiness signals** for option-buying — **without changing any engine result** (byte-identical) and **without blocking** any deploy (every quality signal is advisory). This is the speed/quality precursor to adaptive Plan 4.

## 2. Locked decisions (from brainstorming 2026-06-17)

1. **Start Phase 1** (measure + byte-identical safe wins + free warnings); spec covers Phase 1 + Phase 2.
2. **Objective misalignment → measure first.** Add a cheap in-job spot↔option-rupee correlation metric + advisory warning. Escalate to in-search option-awareness (a future phase) ONLY if measured correlation is weak.
3. **Trust fixes stay advisory.** Costs-off and thin-coverage become **loud warnings, never gates.** Permissive defaults preserved (honors "the app aids, never restricts"). Consistent with the Piece-3 flag-everywhere/never-block philosophy.

## 3. Verified problem (corrected; evidence-cited)

### Speed — the real bottleneck (an earlier analysis was wrong)
- `_build_param_space` (`optimizer.py:162-175`) **always** searches every numeric param a strategy declares in its own `parameter_schema`; the `optimize_indicator_periods` flag only *injects extra* catalog periods (`optimizer.py:177-190`). So any strategy that declares an indicator-period key searches it **by default**.
- Flagship `confluence_scalper` declares `ema_fast`/`ema_slow` (`confluence_scalper.py:16-17`); both are in `INDICATOR_PARAM_KEYS` (`_indicator_key`, `optimizer.py:144-146`). → on a **default** 2000-trial run, TPE varies the EMA periods nearly every trial → the 16-entry `enriched_cache` (`optimizer.py:809-820`) **thrashes** → `precompute_all_indicators` (~200-500ms, `indicators.py:244-296`) re-runs ~2000×. Precompute is ~half of per-trial cost and almost entirely **redundant**: only the EMA columns actually change; `rsi`/`macd`/`atr`/`adx`/`chop` are recomputed at unchanged params every trial.
- The rest of per-trial cost is the **sequential** bar loop in `run_backtest` (`backtest.py:116-222`) — GIL-bound, inherently sequential (position state carries bar-to-bar). Not vectorizable safely; only parallelism across trials helps it (a later phase).
- `strftime` for `session_date`/`ist_time` (`indicators.py:266,271`) is a known cheap hotspot (~30-40ms) replaceable byte-identically with vectorized datetime ops.

### Quality — the bigger story for option buying
- **Objective misalignment (critical).** Default optimize maximizes **spot index-point** metrics (`_objective_value`, `optimizer.py:96-141`). Option-buying **rupee P&L** is invisible to TPE — options are scored once, post-hoc, only if `evaluation_mode != "spot"` (`optimizer.py:783`, default `"spot"`), over the top-50-by-spot shortlist. An option-good/spot-mediocre config is structurally unreachable. Project SEB/ITM evidence hints spot↔option correlation may be weak. **Nothing measures that correlation today.**
- **Selection-bias deflation is evidence-gated.** `deflated_sharpe` exists and is correct (`deployment_quality.py:144`) but the selection-bias check only fires when `n_trials` evidence is supplied (`deployment_quality.py:290-297`; `:434` → `None` without evidence). The optimizer finalize already passes `n_trials` (Fix-A), but the standalone `GET /backtest/runs/{id}` path (Fix-C) calls `evaluate_source_quality(doc)` with **no** evidence → no deflation. Best-of-2000 inflates in-sample Sharpe; most run views never show the correction.
- **Optimistic option defaults.** `OptionBacktestReq.enabled=False` (`schemas.py:47`); option-leg costs and coverage are permissive/advisory → a lucky thin-pairing, zero-cost config can look profitable. (Per decision 2/3, these stay advisory — we surface them louder, we do not gate.)
- Option *realism* is acceptable: premiums come from **real stored 1m option candles**, so theta/IV are embedded between bars. Full Greeks/IV is out of scope.

## 4. Architecture overview

Two coordinated tracks, both landing on `feat/optimizer-speedup`:

- **Speed track:** dependency-keyed indicator memoization (replaces the monolithic recompute) + vectorized datetime + raw-candle LRU + per-bar micro-opts. All **byte-identical**, guarded by a regression harness.
- **Quality track:** in-job advisory signals (deflated-Sharpe everywhere `n_trials` is known; spot↔option correlation) + louder advisory warnings for costs-off / thin coverage. All **advisory** (no gating, no result change to the engine).

The **byte-identical regression harness is the linchpin** — it must exist and pass before any cache/vectorization refactor merges.

## 5. Phase 1 — Measure + free wins (all byte-identical / advisory)

### 5.1 Byte-identical regression harness (`tests/test_indicator_equivalence.py`)
- Pure host test. **Confirmed import-safe** (audit): `indicators.py`, `regime.py`, `cpr.py`, `vol_seasonality.py` import no forbidden module (server/optimizer/runtime/paper_auto). Loads a fixture candle frame (committed small NIFTY slice or synthetic OHLCV with `ts`).
- For every builtin strategy and a **representative param sweep** (defaults + a grid over each `INDICATOR_PARAM_KEYS` param actually in that strategy's schema, **including a sweep that varies `atr_length` while holding `tod_*`/regime params fixed** to catch the hidden `atr`-value edges below), assert the *new* enrichment path returns a DataFrame **exactly equal** (`pd.testing.assert_frame_equal`, including dtypes and NaN positions) to the *current* `precompute_all_indicators`.
- The harness must also exercise the **non-optimizer caller shapes**: the `deployment_evaluator.py:343` single-bar path and the `wfo.py:561` per-window path (both call `precompute_all_indicators`), not just the optimizer path.
- Written FIRST; initially asserts `precompute_all_indicators` equals itself (tautology that becomes meaningful once Phase 2 introduces the memoized path). It is the gate for 5.4 and **all of Phase 2** — must be green before any §6.1 code merges.

### 5.2 Latency instrumentation (`optimizer.py`, opt-in, off by default)
- Add lightweight `time.perf_counter()` accounting around `get_enriched` vs `run_backtest` inside the trial loop, accumulated per job and emitted to the job doc under a `timing` key (and `log.debug`). Guarded by an env/flag so it is **zero-cost when off** and never alters results.
- Goal: confirm the precompute-vs-backtest split for `confluence_scalper` and 2 others, and which indicator groups dominate — this **gates the scope** of Phase 2's memoization (if precompute proves <20% of per-trial cost for a strategy, descope its group work).

### 5.3 In-job advisory trust signals

**(a) Deflated-Sharpe wherever `n_trials` is knowable.** Today the `selection_bias` warning can only fire at optimizer finalize — `_save_best_as_backtest` never stores `n_trials` and `GET /backtest/runs/{id}` (`research.py:360-373`) calls `evaluate_source_quality(doc)` with **no** evidence, so saved/standalone run views never show the deflation. Two wiring steps (the quality engine already consumes the evidence unchanged at `deployment_quality.py:286-297`):
  1. Add an `n_trials` parameter to `_save_best_as_backtest` (`optimizer.py:444`) and persist `"n_trials"` into the saved `backtest_run` doc. The optimizer caller (`optimizer.py:~1136`) already has `n_trials` in scope; the `wfo.py` caller passes `None` (its behavior unchanged).
  2. `GET /backtest/runs/{id}` (`research.py:360-373`) calls `evaluate_source_quality(doc, evidence={"n_trials": doc.get("n_trials")})` **only when `n_trials` is present** (else unchanged). Advisory (`SEVERITY_WARNING`); must NOT change output for runs with strong OOS (the `strong_oos` suppression at `deployment_quality.py:293-296` still holds — verify on the running stack).

**(b) Spot↔option correlation (the "measure-first" instrument for decision 2).** No such metric exists today (`grep` confirms zero `spot_option_correlation`/`objective_misalignment`).
  1. Add a PURE helper `compute_spot_option_correlation(ranked) -> float | None` in `deployment_quality.py`: Pearson over each candidate's `spot_objective` vs `option_pnl_value`; returns `None` when `len(ranked) < 2` or either series has zero variance. Host-testable.
  2. Call it after the rank sort in `_option_rerank` (`optimizer.py:~754`, where `ranked` already carries `spot_objective` (:744) and `option_pnl_value` (:746)); store as `rerank_info["spot_option_correlation"]`. Computed/stored **only** when `evaluation_mode != "spot"` AND `ranked` is non-empty — so spot-mode runs stay byte-identical with no new keys (match the existing `rerank_info` gating at `optimizer.py:~1105`).
  3. Add an advisory warning id `objective_misalignment` (`SEVERITY_WARNING`) + a `QualityThresholds.min_spot_option_correlation` (default `0.3`), firing when the metric is not `None` and `< threshold`, with snapshot exposure. No gating. This is the signal that tells us whether in-search option-awareness (Phase 5) is worth pursuing.

### 5.4 Vectorize datetime columns (`indicators.py:265-271,266,271`)
- Replace `df["dt"].dt.strftime("%Y-%m-%d")` / `strftime("%H:%M")` with vectorized construction that yields **identical string values** (verified by 5.1). Keep `dt` as the tz-converted intermediate. Must match NaT handling exactly.

## 6. Phase 2 — Dependency-keyed memoization + micro-opts + louder advisories

> Gated by: 5.1 harness green + 5.2 measurement confirming precompute is a material per-trial cost (expected true for `confluence_scalper`).
>
> **Sequencing note (audit):** Phase 1 (§5.1-5.4) is **ship-ready independently** — it has no dependency on the §6.1 cache. The implementation plan will treat Phase 2 as a separate execution stage **gated on 5.2's measurement** (and may be split into its own plan/spec if measurement reshapes the §6.1 scope). We do not commit to the cache build until the measurement confirms the win.

### 6.1 Dependency-keyed indicator memoization (`indicators.py` internal; `optimizer.py`/`wfo.py` get_enriched)

> **Not a current bug:** today's monolithic cache keys the whole frame on the full `_indicator_key(merged)` (`optimizer.py:812`), so all groups recompute together and stay coherent. The hazards below exist ONLY for the proposed per-group split and are PREVENTED by encoding the true edges + the 5.1 harness.

Refactor `precompute_all_indicators` **internally** into an indicator-group registry; its **public signature stays unchanged** — all 6 callers (`optimizer.py:816`, `wfo.py:561`, `deployment_evaluator.py:343`, `runtime.py:650`, `research.py:173` & `:259`) keep calling `precompute_all_indicators(df, params)` and receive an identical frame. Each group declares `{param_keys, input_columns, output_columns, compute_fn}`, where `input_columns` lists EVERY raw/derived column it reads — this is how hidden value-level edges become explicit.

**Param-independent (computed ONCE per `get_enriched` window; the cache is job-local so the window is constant → no cross-window staleness):**

| group | output_columns | input_columns |
|---|---|---|
| time | `dt`,`session_date`,`ist_time` | `ts` |
| vwap | `vwap` | `session_date`,`high`,`low`,`close`,`volume` |
| ema50 | `ema50` | `close` |
| fvg | `fvg` | `high`,`low` |
| nr7 | `nr7` | `session_date`,`high`,`low` |
| cpr_levels | `cpr_p`,`cpr_tc`,`cpr_bc`,`cpr_width_pct`,`R1`,`S1`,`R2`,`S2` | `session_date`,`high`,`low`,`close` |
| vwap_sigma | `vwap_sigma`,`vwap_u1`,`vwap_u2`,`vwap_l1`,`vwap_l2` | `session_date`,`vwap`,`close` |

**Param-dependent (memoized by the group's FULL input-param set, including the transitive edges the audit surfaced):**

| group | param_keys | input_columns | note |
|---|---|---|---|
| ema | ema_fast,ema_slow | close | |
| rsi | rsi_length | close | |
| macd | macd_fast,macd_slow,macd_signal | close | |
| atr | atr_length | high,low,close | |
| atr_avg | **atr_length** | `atr` | rolling mean of `atr` -> keyed on atr_length |
| adx | adx_length | high,low,close | |
| chop | chop_length | high,low | |
| swing | swing_lookback | high,low | recompute on swing_lookback change |
| velocity | vel_n,vel_z_window | close | |
| variance_ratio | vr_q,vr_lookback,vr_scale | close | |
| squeeze | bb_len,bb_mult,kc_len,kc_atr_mult,sqz_mom_len | high,low,close | computes its OWN local ATR -- **no** atr_length edge (verified) |
| supertrend | st_period,st_mult | high,low,close | computes its OWN local ATR -- **no** atr_length edge (verified) |
| tod_tradeable | tod_lookback_sessions,tod_min_atr_frac,**atr_length** | session_date,high,low,**`atr`** | reads global `df['atr']` (vol_seasonality.py:23) -> atr_length edge |
| cpr_day_type | cpr_narrow_pctile,cpr_wide_pctile,cpr_pctile_window | `cpr_width_pct`,session_date | rolling percentile over session width |
| regime | adx_length,**atr_length**,chop_length | adx,`atr`,**`atr_avg`**,chop | reads `atr_avg` (regime.py:21) -> atr_length edge; ALWAYS assembled fresh, never cached as a stale standalone column |

- **`get_enriched(params)`** assembles the frame from the read-only param-independent base, reusing each param-dependent group's cached output columns when its `param_keys` are unchanged, else recomputing. For `confluence_scalper` (varies only ema_fast/ema_slow): only the **ema** group recomputes; `regime`/`tod`/`atr`/`rsi`/`macd`/`adx`/`chop`/`supertrend`/`squeeze` keep unchanged params -> reused.
- **Conservative fallback:** any group whose true edges are uncertain is keyed on the full `_indicator_key` (degrades to current monolithic behavior — never wrong).
- **Safe copy model (no "discipline"):** the param-independent base is computed once and treated **read-only**; each param-dependent group's `compute_fn` returns ONLY its own output columns; assembly copies them onto a fresh per-trial frame (column assignment, not recompute). No group mutates shared base columns — an owned-columns invariant the 5.1 harness verifies.
- **Memory bound (concrete):** per-group LRU of K (default 4) entries; one read-only base frame per job. For `confluence_scalper` only the ema-group cardinality grows; 5.2 asserts it stays small. Net resident memory ≈ current or lower (no 16× full-frame copies). Peak RSS on a heavy 12-month/high-freq run is a residual uncertainty to verify before trusting the memory claim.
- **Conditional skip (optional, user's "declared set + full fallback"):** a strategy MAY declare `required_indicators`; groups producing only non-required, non-regime columns may be skipped. Regime-required inputs (`adx`,`atr`,`atr_avg`,`chop`) are ALWAYS computed. Undeclared strategies compute the full set. Skipping is gated by the same 5.1 harness.
- **Performance (measured, not asserted):** a confluence cache-miss recomputes only the ema group instead of the full set, but **`regime` reassembly and the per-trial frame copy persist** — so the realistic per-trial saving is large but its exact factor is **confirmed by 5.2 before we claim it**, not assumed to be "a few ms".

### 6.2 Raw-candle in-process LRU (data layer)
- Memoize `load_candles_df(instrument,start_ts,end_ts)` raw output on an in-process LRU keyed by the exact tuple, so a backtest→optimize→WFO sequence on the same window loads once. Byte-identical; bounded; invalidation by key only (windows are immutable historical data).

### 6.3 Per-bar micro-opts (`backtest.py`) — bundle, byte-identical
- Hoist `ctx_global` out of the bar loop (build once, set only `i` per entry-eval bar); drop redundant `df.reset_index(drop=True)` when index is already default; replace Trade `__dict__` override storage with real dataclass fields. Each guarded by 5.1-style equality on trades/metrics. Expected small (~3-7% on run_backtest), but free.

### 6.4 Louder advisory realism warnings (NOT gates — decision 3)
- Surface costs-off and thin-coverage (paired-trade count / pairing ratio below a threshold) as prominent advisory warnings in the trust scorecard/quality output. Defaults stay permissive; nothing is rejected. Reuses existing `deployment_quality` warning machinery; no new gating path.

## 7. Correctness strategy (the linchpin)
- **No engine result changes.** Every speed item is proven byte-identical by the 5.1 harness (indicators) and trade/metric equality (backtest micro-opts) before merge.
- **All quality items are advisory.** They add warnings/metrics; they never alter trades, P&L, selection, or deploy gating.
- **Conservative fallback everywhere.** Any uncertain dependency → key on full `_indicator_key` (current behavior). Any undeclared `required_indicators` → full precompute.

## 8. Testing
- `tests/test_indicator_equivalence.py` (5.1) — the gate. Covers all builtin strategies × param sweep (incl. the `atr_length` sweep that exercises the `regime`/`tod` hidden edges) AND the `deployment_evaluator` single-bar + `wfo` per-window caller shapes.
- `compute_spot_option_correlation` (5.3b) — **pure, host-tested** in `deployment_quality` (Pearson, None on `<2`/zero-variance), alongside the existing host tests for `deflated_sharpe`/`expected_max_sharpe`.
- **Host-test boundary (explicit):** the pure helpers (`deployment_quality`, `indicators`, `regime`, `cpr`, `vol_seasonality`) are host-tested. The **call-site wiring** — `_save_best_as_backtest` persisting `n_trials`, `research.py` passing evidence, and `_option_rerank` storing the correlation — lives in `optimizer.py`/`research.py` (forbidden imports) and is verified on the **running stack**, not on host.
- Backtest micro-opt equality tests (trades + metrics identical pre/post).
- All tests run from repo root: `python -m pytest tests/...` — **must not** import `server.py`/`optimizer.py`/`runtime.py`/`paper_auto.py` on host.

## 9. Risks & mitigations
- **Silent numerical drift from the cache refactor** → 5.1 harness across all strategies + a param sweep that varies `atr_length` (catches the `regime`/`tod`/`atr_avg` value-level edges); conservative full-`_indicator_key` fallback for any uncertain group.
- **Mutation aliasing in shared base frame** → safe copy model (read-only base; each group returns only its own columns; assembly copies onto a fresh frame); owned-columns invariant asserted by the harness — not undefined "discipline".
- **Per-group cache unbounded growth → OOM** → concrete per-group LRU (K=4) + one read-only base frame per job; 5.2 asserts confluence ema-group cardinality stays small; **peak RSS on a heavy 12mo/high-freq run verified before trusting the memory claim**.
- **Refactor breaks a non-optimizer caller** → `precompute_all_indicators` public signature unchanged (registry internal); harness exercises all 6 caller shapes.
- **Measurement contradicts assumption** (precompute not dominant for some strategy) → 5.2 gates §6.1 scope per-strategy; descope rather than over-build.
- **§5.3 evidence wiring changes an existing scorecard** → verify on stack that `n_trials` evidence does not flip runs with strong OOS (the `strong_oos` suppression must still hold).

## 10. Out of scope (future phases, separate specs)
- **Phase 3:** parallelize option-rerank + survival folds (per-worker caches; byte-identical), early-stop (opt-in, off by default).
- **Phase 4:** WFO per-window parallelization; supertrend/vwap_sigma vectorization (measurement-gated).
- **Phase 5 (decision-gated):** in-search option-awareness (periodic option-rerank hint-trials or per-trial option objective) — ONLY if 5.3's correlation proves weak; parallel TPE trials (reproducibility tradeoff). Requires explicit user buy-in.
