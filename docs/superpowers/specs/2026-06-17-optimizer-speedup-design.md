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
- Pure host test (no server/optimizer/runtime import). Loads a fixture candle frame (committed small NIFTY slice or synthetic OHLCV with `ts`).
- For every builtin strategy and a **representative param sweep** (defaults + a grid over each `INDICATOR_PARAM_KEYS` param actually in that strategy's schema), assert the *new* enrichment path returns a DataFrame **exactly equal** (`pd.testing.assert_frame_equal`, including dtypes and NaN positions) to the *current* `precompute_all_indicators`.
- This test is written FIRST and initially asserts equality of `precompute_all_indicators` against itself (a tautology that becomes meaningful once Phase 2 introduces the memoized path). It is the gate for 5.4 and all of Phase 2.

### 5.2 Latency instrumentation (`optimizer.py`, opt-in, off by default)
- Add lightweight `time.perf_counter()` accounting around `get_enriched` vs `run_backtest` inside the trial loop, accumulated per job and emitted to the job doc under a `timing` key (and `log.debug`). Guarded by an env/flag so it is **zero-cost when off** and never alters results.
- Goal: confirm the precompute-vs-backtest split for `confluence_scalper` and 2 others, and which indicator groups dominate — this **gates the scope** of Phase 2's memoization (if precompute proves <20% of per-trial cost for a strategy, descope its group work).

### 5.3 In-job advisory trust signals
- **Deflated-Sharpe everywhere `n_trials` is knowable.** Persist `n_trials` on the saved best-run doc at optimizer finalize, and have `GET /backtest/runs/{id}` (`research.py`, Fix-C path) pass `evidence={"n_trials": doc.get("n_trials")}` when present so the existing `selection_bias` warning fires on the run view, not only in the optimizer job. No new thresholds; reuses `deployment_quality` as-is. Advisory (it is already a `SEVERITY_WARNING`).
- **Spot↔option correlation metric (option-rerank mode only).** When `evaluation_mode != "spot"` and the option rerank ran, compute Pearson correlation between each shortlisted candidate's spot objective value and its option net-₹ over the reranked top-K, store on the job (`spot_option_correlation`), and add an advisory warning (new id e.g. `objective_misalignment`) when correlation `< 0.3`. This is the "measure first" instrument for decision 2 — it tells us whether in-search option-awareness is worth a future phase. Pure addition; no gating.

### 5.4 Vectorize datetime columns (`indicators.py:265-271,266,271`)
- Replace `df["dt"].dt.strftime("%Y-%m-%d")` / `strftime("%H:%M")` with vectorized construction that yields **identical string values** (verified by 5.1). Keep `dt` as the tz-converted intermediate. Must match NaT handling exactly.

## 6. Phase 2 — Dependency-keyed memoization + micro-opts + louder advisories

> Gated by: 5.1 harness green + 5.2 measurement confirming precompute is a material per-trial cost (expected true for `confluence_scalper`).

### 6.1 Dependency-keyed indicator memoization (`indicators.py` + `optimizer.py` get_enriched)
- Refactor `precompute_all_indicators` into an **indicator-group registry**: each group declares `(param_keys, input_columns, output_columns, compute_fn)`. Groups:
  - **Param-independent (compute ONCE per (instrument,start,end)):** `dt`/`session_date`/`ist_time` (5.4), `vwap`, `ema50` (fixed), `fvg`, `nr7`, price-only CPR levels (`cpr_p`,`cpr_tc`,`cpr_bc`,`R1`,`S1`,`R2`,`S2`,`cpr_width_pct`), `vwap_sigma`/`vwap_u1`/`vwap_u2`/`vwap_l1`/`vwap_l2` (depend only on `vwap`).
  - **Param-dependent (memoize each group by ONLY its own param subset):** `ema9`/`ema21`←(ema_fast,ema_slow); `rsi`←rsi_length; `macd_*`←(macd_fast,slow,signal); `atr`←atr_length; `atr_avg`←(atr_length) [derives from `atr`]; `adx`←adx_length; `chop`←chop_length; swing pts←swing_lookback; `vel_z`/`accel_z`←(vel_n,vel_z_window); `vr`/`regime_score`←(vr_q,vr_lookback,vr_scale); squeeze←(bb_len,bb_mult,kc_len,kc_atr_mult,sqz_mom_len); supertrend/`st_dir`←(st_period,st_mult); `tod_tradeable`←(tod_lookback_sessions,tod_min_atr_frac); cpr `day_type`←(cpr_*_pctile,cpr_pctile_window) [derives from `cpr_width_pct`]; `regime`←(adx,atr,chop) [derives].
  - **True input columns must be derived by code inspection** (e.g. does `squeeze`/`supertrend`/`tod` internally read `atr`?). The registry encodes the real dependency edges; the 5.1 harness PROVES equivalence. Any group whose dependencies are uncertain is conservatively keyed on the full `_indicator_key` (degrades to current behavior, never wrong).
- `get_enriched(params)` assembles the enriched df by reusing every group whose params are unchanged and recomputing only changed groups. For `confluence_scalper` (varies only ema_fast/slow) this recomputes the EMA group + `regime` and reuses everything else → precompute drops from ~200-500ms to a few ms.
- **Memory:** per-group caches are bounded; the param-independent base is a single frame per window. Net memory ≈ current or lower (no 16× full-frame copies). Must not worsen the known OOM-on-heavy-runs behavior — bound caches and reuse the base frame by reference where safe (copy-on-write discipline so groups don't mutate shared columns).
- **Conditional skip (optional, user's "declared set + full fallback"):** a strategy MAY declare `required_indicators`; groups producing only non-required, non-regime columns may be skipped. Undeclared strategies compute the full set (zero risk). Regime-required columns (`adx`,`atr`,`atr_avg`,`chop`) are ALWAYS computed. Skipping is gated by the same 5.1 harness.

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
- `tests/test_indicator_equivalence.py` (5.1) — the gate.
- Unit tests for the in-job deflated-Sharpe wiring and the spot↔option correlation metric (pure functions where possible; the correlation computation extracted to a testable helper).
- Backtest micro-opt equality tests (trades + metrics identical pre/post).
- All tests host-runnable from repo root: `python -m pytest tests/...` — **must not** import `server.py`/`optimizer.py`/`runtime.py`/`paper_auto.py` on host (extract pure helpers; verify optimizer/runtime wiring on the running stack).

## 9. Risks & mitigations
- **Silent numerical drift from the cache refactor** → 5.1 harness across all strategies + a param sweep; conservative full-key fallback.
- **Mutation aliasing in shared base frame** → copy-on-write discipline; groups write only their own columns; harness catches any leak.
- **Measurement contradicts assumption** (precompute not dominant for some strategy) → 5.2 gates 6.1 scope per-strategy; descope rather than over-build.
- **OOM regression** → bounded per-group caches; single base frame per window; no 16× full-frame duplication.

## 10. Out of scope (future phases, separate specs)
- **Phase 3:** parallelize option-rerank + survival folds (per-worker caches; byte-identical), early-stop (opt-in, off by default).
- **Phase 4:** WFO per-window parallelization; supertrend/vwap_sigma vectorization (measurement-gated).
- **Phase 5 (decision-gated):** in-search option-awareness (periodic option-rerank hint-trials or per-trial option objective) — ONLY if 5.3's correlation proves weak; parallel TPE trials (reproducibility tradeoff). Requires explicit user buy-in.
