# Adaptive Options Strategies (5-strategy slate) — Design Spec

**Date:** 2026-06-16
**Status:** Approved design — framework (v2) + 5-strategy slate + shared toolkit locked with the user (2026-06-16). Pending user spec review; an adversarial design audit (the Piece-1/Piece-2 pattern) is recommended before build.
**Branch:** new `feat/adaptive-strategies`, stacked off `feat/exit-risk-controls` (Piece 2). Depends on Piece-1 (survival gate) + Piece-2 (exit/risk overlay) being in the chain.
**Scope:** Add **five** new regime-adaptive, survival-gated, intraday **option-buying** strategies for **NIFTY 50 + SENSEX** weekly options, plus a shared "measured-edge" toolkit, on the existing `StrategyBase` plugin contract. Strategies plug into the existing optimizer → option re-rank → survival gate → WFO → preset → deployment pipeline. **No engine rewrite** — only additive toolkit modules, one opt-in `ctx` field (XRS only), and the new strategy files. **No real broker orders, ever** (paper only). Design/spec only — implementation follows in a separate plan.

> Builds directly on the locked work: Piece-1 survivable-optimization (`survival.py`, OOS capital floor / RoR), Piece-2 exit/risk overlay (`exit_controls.py`: premium trailing/breakeven + daily caps), and the live-tick paper realism branch. The new strategies are designed to be **discovered, optimized, survival-gated, and deployed by machinery that already exists** — this spec adds *what to trade and how to measure its edge*, not new pipeline.

---

## 1. Problem

The app has 7 built-in strategies (VWAP pullback, confluence, ORB, Fibonacci, VWAP mean-reversion, explosive reversal, SMC sweep+FVG). They are competent but share three weaknesses the user has observed in practice:

1. **Overfitting / weak edge.** Many optimized results look good in-sample and fail OOS. The cause is consistent across them: **absolute, hand-tuned constants** (fixed point targets, fixed ADX/RSI thresholds, fixed time windows) that are tuned to one instrument and one volatility regime, plus entry logic that is not conditioned on whether a long-premium trade can actually clear its theta + cost hurdle.
2. **No regime adaptivity.** Each existing strategy runs one fixed entry model and uses the regime tag only as a *blocker*. None **switches** its model by regime, and none scales its exits/sizing with live volatility.
3. **Coverage gaps.** None keys on volatility *compression→expansion* (the option buyer's highest-EV event), daily *day-type* classification, or cross-index lead/lag, and none encodes the intraday options-buying timing structure (morning expansion vs midday theta grind).

The objective (locked with the user): strategies with a **measurable mathematical edge** that are **adaptive, self-improving, and tunable to present market conditions**, with **hard intraday risk management** (no overnight, EOD square-off), built to **survive the existing OOS survival gate** rather than to maximize in-sample points.

## 2. Goal

Ship a slate of **five** strategies, each a clean expression of **one** edge, all built on a shared framework that makes them adaptive and anti-overfit *by construction*:

| # | id | Name | Edge | Regime |
|---|---|---|---|---|
| 1 | `squeeze_expansion_breakout` | Squeeze Expansion Breakout (SEB) | variance-timing: long gamma into expansion | compression → expansion |
| 2 | `adaptive_regime_scalper` | Adaptive Regime Scalper (ARS) — *flagship* | direction-timing via measured regime (Variance Ratio soft-blend) | switches fade ↔ trend |
| 3 | `opening_range_adaptive` | Opening-Range Fade/Break (ORF) | trapped-liquidity + contraction-selectivity | opening session |
| 4 | `gap_fade` | Gap-Fade (GAP) | large-gap mean-reversion | open imbalance |
| 5 | `cross_index_rs` | Cross-Index Relative Strength (XRS) | NIFTY↔SENSEX lead/lag | cross-asset |

Every strategy: emits standard `Signal`s, exposes a small `parameter_schema` the optimizer tunes, uses **ATR/σ/percentile-relative thresholds only** (so one strategy serves NIFTY *and* SENSEX), and inherits the shared **edge & adaptivity framework** below. The slate is meant to be **optimized, survival-gated, then deployed together** for paper trading (independent deployments, no cross-strategy arbitration).

## 3. Decisions (locked with the user)

| Decision | Choice |
|---|---|
| Deliverable | **Design / spec only** this session; implementation in a later plan. |
| Slate | **All 5** (SEB, ARS, ORF, GAP, XRS). Room to add more later. |
| Instruments | **NIFTY 50 + SENSEX** weekly options (`supported_instruments = ["NIFTY","SENSEX"]`). Logic is instrument-agnostic; lot size & expiry always from contract metadata. |
| Style | Both **scalp & intraday** (`supported_modes = ["SCALP","INTRADAY"]`). 1-min base bars, like the existing engine. |
| Holding | **Intraday only. Hard EOD square-off. No overnight.** New-entry cutoff ~14:00 IST; engine square-off at 15:00 IST. |
| Adaptivity | **Regime-switching entries + volatility-adaptive exits/sizing + self-tuning (WFO) params.** |
| Framework | **v2 "measured-edge"** (§4): Movement×Direction×Speed; edge-decay exits; **Variance-Ratio** soft-blend regime + percentile/hysteresis; **walk-forward conditional ₹-expectancy** gate; survival-capped edge-proportional sizing. |
| Supertrend | Folded into the **ARS toolkit** as a trend trigger — *not* shipped as a standalone (overlaps confluence). |
| XRS engine change | One **opt-in, keyword-only** companion-index frame on `ctx`; the other 4 strategies + existing 7 are byte-identical. |
| Anti-overfit gate | A strategy is "real" only if it clears the **existing** survival gate on OOS + positive option-₹ on re-rank + robustness plateau + `min_trades` + balanced CE/PE. |

## 4. The edge & adaptivity framework (v2)

Every strategy inherits these five principles. They are the spine; the strategies are expressions of them.

**P1 — Buyer EV = Movement × Direction × Speed.** A long-premium intraday trade is +EV only if all three hold: enough **range** to clear theta+cost (*Movement*), a **forecastable sign** (*Direction*), and it happens **fast enough** to beat decay (*Speed*). Gamma pays on acceleration (|d²price|), so every entry carries a **Speed confirm** on z-scored acceleration *in the direction of the option being bought* — the variable P&L is actually a function of, which almost nobody trades directly. The confirm is **mode-aware** (set by each `_core_signal`): a **momentum** entry (SEB, ORF-break, XRS) requires `accel_z` strongly *in-direction* (≥ `k_acc`); a **reversion** entry (ARS-fade, GAP, ORF-fade) requires only that acceleration has **stopped working against you and is turning toward** the trade (`accel_z` crossing up through a small adverse band, parameter `k_acc_fade`) — so fades aren't suppressed for entering before momentum prints.

**P2 — The edge lives in the exit math.** Primary exit = **edge-decay** (exit when the signal that gave the edge dies), not a fixed target. ATR-scaled target/stop are **backstops**; breakeven + trailing come from the Piece-2 `exit_controls` overlay; a **time-stop** caps theta bleed on stalled trades. A merely-okay entry becomes +EV through asymmetric exits (quick T1 lock + breakeven-ratcheted runner for the fat tail).

**P3 — Adaptive without overfitting: measure the regime, don't threshold a proxy.** Replace absolute constants with **relative, self-normalizing** measures. The regime classifier is the **Variance Ratio** VR(q)=Var(q-bar ret)/(q·Var(1-bar ret)) — the Lo–MacKinlay efficiency statistic: VR>1 ⇒ trend, VR<1 ⇒ mean-revert, VR≈1 ⇒ random walk (stand aside). Used as a **continuous soft-blend score** with **hysteresis**, not a hard switch. All other thresholds are ATR/σ/percentile-relative → scale-free across NIFTY & SENSEX, far more stationary across regimes (anti-overfit).

**P4 — Theta-hurdle → walk-forward conditional ₹-expectancy gate.** Before any entry, require a positive expected option-₹ over the hold. The rigorous form (Phase C) estimates **E[option-₹ | regime×time-of-day×VIX×DTE]** on the **train** window (using the trade-context tags already recorded + the option-₹ re-rank) and only fires OOS where it's positive with margin — theta+spread+cost captured *empirically*, never approximated. Cold-start backstop = a simple ATR-projected-move vs (theta+cost) inequality.

**P5 — Self-improving + anti-overfit acceptance.** Self-improving = **WFO** re-optimizes few, monotonic, meaningful params on a rolling window so they track the present market; we design for parameter **plateaus, not peaks** (the existing robustness/heatmap analysis judges). A candidate is **only promoted** if it clears the existing **survival gate** (capital floor + RoR) on OOS folds, is **option-₹ positive** on re-rank, holds a robustness plateau, meets `min_trades`, and keeps a balanced CE/PE share. Overfit candidates fail by construction.

## 5. Architecture

Four additive layers + the unchanged plugin contract. Nothing here rewrites the engine.

```
A. Per-bar columns      → app/indicators.py :: precompute_all_indicators(df, params)   (single insertion point)
                          new pure fns: velocity/accel, variance_ratio, bollinger, keltner,
                          squeeze(on/fire/mom), supertrend, vwap_sigma_bands, nr7
B. Daily levels         → app/cpr.py  (Central Pivot Range + floor/Camarilla pivots, per session_date, prior-day RTH)
C. Estimated artifacts  → app/vol_seasonality.py (intraday time-gate, train-estimated)
                          app/edge_gate.py        (walk-forward conditional ₹-expectancy)   [Phase C]
                          edge-proportional sizing in app/portfolio.py (survival-capped)     [Phase C]
D. Engine touch (XRS)   → app/backtest.py: opt-in, keyword-only companion-index frame on ctx
Strategies              → app/strategies/adaptive_base.py (shared scaffolding) + 5 builtin/*.py
```

**Authoring contract (verified against `app/strategies/base.py`).** A strategy is a `StrategyBase` subclass in `backend/app/strategies/builtin/<id>.py`; it is **auto-discovered** at startup (only classes whose `__module__` equals the module are registered, so importing `StrategyBase`/`Signal` is safe and never double-registers). It sets `id, name, version, description, supported_instruments, supported_modes, supported_timeframes, parameter_schema` and implements `evaluate(row, prev, params, ctx) -> Signal`. `Signal` fields used: `direction` ("CE"/"PE"/"NONE"), `score` (int 0–100), `reasons`, `blockers`, `spot_target_pts`, `spot_stop_pts`, `time_stop_minutes`. The strategies need **no** changes to `base.py`, the routers, or the request schemas — the registry, `GET /api/strategies`, the backtest, and the optimizer pick them up automatically.

**Shared base — `app/strategies/adaptive_base.py`.** `AdaptiveStrategyBase(StrategyBase)` implements the framework scaffolding once so each strategy stays focused on its core signal:
- A common `parameter_schema` fragment: `k_acc, k_acc_fade, t_atr, s_atr, time_stop_min, signal_threshold, cooldown_bars, entry_cutoff_hhmm, use_time_gate`.
- `evaluate()` runs: warmup/NaN check → **time gate** (`tod_tradeable` and `ist_time < entry_cutoff_hhmm`) → call `self._core_signal(row, prev, params, ctx)` (returns strategy-specific direction/score/reasons/blockers **and a `mode` ∈ {momentum, reversion}**) → **mode-aware Speed confirm** (momentum: `accel_z` in-direction ≥ `k_acc`; reversion: `accel_z` turning toward the trade through the `k_acc_fade` band; else block) → attach **ATR exits** (`spot_target_pts = round(t_atr * row.atr)`, `spot_stop_pts = round(s_atr * row.atr)`) + `time_stop_minutes` → return `Signal`.
- Each concrete strategy overrides `_core_signal()` and extends `parameter_schema` with its own params. This centralizes the framework (one place to test the time/speed/exit scaffolding) and keeps each strategy a few dozen lines.

## 6. Shared toolkit — exact additions

All per-bar functions are **pure** (Series/DataFrame in, Series out), **causal** (trailing windows only — no centered/future peeking, mirroring `detect_swing_points`'s explicit look-ahead guard), and host-testable. Added to `app/indicators.py` and called inside `precompute_all_indicators`.

| Fn (new) | Output columns | Definition (causal) | Params (default) |
|---|---|---|---|
| `velocity_accel` | `vel_z`, `accel_z` | `vel = close.diff(vel_n)`; `vel_z = (vel − vel.rolling(W).mean())/vel.rolling(W).std()`; `accel = vel.diff()`; `accel_z` likewise | `vel_n`(2), `vel_z_window`(60) |
| `variance_ratio` | `vr`, `regime_score` | overlapping Lo–MacKinlay VR(q) over trailing `vr_lookback`; `regime_score = clip((vr−1)/vr_scale, −1, 1)` | `vr_q`(4), `vr_lookback`(90), `vr_scale`(0.5) |
| `bollinger` | `bb_u`, `bb_l`, `bb_mid` | `SMA(close,len) ± mult·rolling_std(close,len)` | `bb_len`(20), `bb_mult`(2.0) |
| `keltner` | `kc_u`, `kc_l` | `EMA(close,len) ± atr_mult·atr(df,len)` (reuses existing `atr`) | `kc_len`(20), `kc_atr_mult`(1.5) |
| `squeeze` | `squeeze_on`, `squeeze_fire`, `sqz_mom` | `on = (bb_l>kc_l)&(bb_u<kc_u)`; `fire = on.shift(1) & ~on`; `sqz_mom = linreg(close − ½(½(HH_n+LL_n)+SMA(close,n)), n)` (LazyBear) | uses bb/kc; `sqz_mom_len`(20) |
| `supertrend` | `supertrend`, `st_dir` | ATR-banded trailing flip on `hl2` | `st_period`(10), `st_mult`(3.0) |
| `vwap_sigma_bands` | `vwap_sigma`, `vwap_u1/u2`, `vwap_l1/l2` | per session: `sigma = sqrt(expanding mean((typical−vwap)²))` — **price-based** to match `session_vwap`'s volume-zero fallback; bands `vwap ± k·sigma` | — |
| `nr7` | `nr7` | per-session flag: the **prior completed** session's range was the narrowest of its preceding 7 sessions (Crabel contraction → today's expansion booster) | — |

**`app/cpr.py`** — `cpr_levels(df) -> df` attaches, per `session_date` from the **prior** session's RTH (09:15–15:30 IST) H/L/C:
`cpr_p=(H+L+C)/3`, `cpr_bc=(H+L)/2`, `cpr_tc=2·cpr_p−cpr_bc` (swap so TC≥BC), `cpr_width_pct=(cpr_tc−cpr_bc)/cpr_p·100`, floor `R1=2P−L, S1=2P−H, R2=P+(H−L), S2=P−(H−L)`, optional Camarilla `H3/L3/H4/L4`. `day_type` ∈ {TREND, RANGE, NEUTRAL} by **rolling percentile** of `cpr_width_pct` over the prior `cpr_pctile_window` sessions (`< cpr_narrow_pctile` ⇒ TREND-day, `> cpr_wide_pctile` ⇒ RANGE-day). Width-percentile (not absolute points) is what makes CPR portable NIFTY↔SENSEX. Holiday-aware via the existing `session_date` grouping / `nse_calendar`.

**`app/vol_seasonality.py`** — `build_tod_map(df_est, instrument, cfg) -> {bucket: tradeable}`: mean realized range (high−low, ATR-normalized) by 5-min IST bucket over the **estimation set**, with `tradeable = mean_range_bucket ≥ theta_hurdle` (or top-K buckets). Attaches `tod_tradeable` per bar by bucket. **Anti-leakage:** the estimation set is the **train/IS** window (WFO) or a trailing-N-session window — never the whole evaluated window. This *replaces the hardcoded 11:00–13:30 death-zone* with an empirically estimated, per-instrument, self-updating gate.

**`app/edge_gate.py` [Phase C]** — `fit_edge_table(train_trades) -> table` keyed by `(regime_score_bucket, tod_bucket, vix_bucket, dte)` → `{E_inr, n}`; `edge_ok(context, table, cfg) -> bool` allows an OOS entry only if `E_inr > margin and n ≥ min_n`, else falls back to the ATR-vs-cost backstop. Built on the existing trade-context tags + option-₹ re-rank; estimated on train, applied OOS (WFO-consistent).

**Edge-proportional sizing [Phase C]** — extend `portfolio.SizingConfig`: `lots = clip(base_lots · g(score/edge), 1, max_lots)`, then hard-capped by the existing `survival`/RoR gate. Default OFF ⇒ fixed `lots` (byte-identical).

**Optimizer wiring (important, prevents a silent no-op).** The new **period** params (`vel_n, vel_z_window, vr_q, vr_lookback, bb_len, kc_len, st_period, sqz_mom_len, cpr_pctile_window`) must be registered in `optimizer.py`'s `INDICATOR_PARAM_KEYS` / `INDICATOR_PARAM_CATALOG` so the enriched-frame cache (`_indicator_key`) **recomputes when they change** — otherwise tuning them silently does nothing (the exact class of bug the handoff notes was fixed for indicator periods).

## 7. The five strategies

Each below specifies only its `_core_signal` (the shared base supplies warmup/time-gate/Speed-confirm/ATR-exits/time-stop). `dir∈{CE,PE,NONE}`. All thresholds relative.

### 7.1 SEB — Squeeze Expansion Breakout (`squeeze_expansion_breakout`) — *Movement edge*
- **Hypothesis:** the highest-EV event for an option buyer is the low-vol→high-vol transition; buying convexity as a squeeze fires captures the expansion before theta matters.
- **Core:**
  ```
  if not row.squeeze_fire and coil_age(ctx) < min_coil_bars: return NONE
  if row.sqz_mom > 0 and row.close > row.vwap:  dir=CE
  elif row.sqz_mom < 0 and row.close < row.vwap: dir=PE
  score = base + f(coil_depth_sigma) + f(|accel_z|) + (nr7 ? +bonus : 0) + f(|sqz_mom| slope)
  ```
- **Edge-decay exit:** `sqz_mom` slope flips, or `squeeze_on` re-engages (re-compression), or `accel_z` crosses zero against the position. (Base supplies ATR backstop + `time_stop_min`.)
- **Params:** `bb_len, bb_mult, kc_len, kc_atr_mult, sqz_mom_len, min_coil_bars`(6) + base.
- **Portability:** coil depth in σ; everything ATR/σ-relative → identical NIFTY/SENSEX.

### 7.2 ARS — Adaptive Regime Scalper (`adaptive_regime_scalper`) — *Direction edge, flagship*
- **Hypothesis:** trade with the market's *measured* autocorrelation sign; soft-blend a trend module and a fade module by the Variance Ratio, biased by the CPR day-type.
- **Core:**
  ```
  rs = row.regime_score                      # VR-derived, [-1,1], hysteresis-smoothed
  w_trend = max(0, rs) * day_type_bias(row)  # day_type TREND lifts, RANGE damps
  w_fade  = max(0, -rs) * (1/day_type_bias)
  trend_sig: st_dir up & close reclaims cpr_tc/vwap  → CE ; mirror → PE
  fade_sig:  close ≤ vwap_l2 or near cpr_bc/S1 & reversal_candle → CE ; mirror at vwap_u2/cpr_tc/R1 → PE
  pick the higher of (w_trend·trend_score, w_fade·fade_score); STAND ASIDE if |rs| < dead_band and day_type==NEUTRAL
  ```
  (`reversal_candle`, S/R, divergence reused from `context_signals.py`.)
- **Edge-decay exit:** trend → `st_dir` flip or `rs` leaves the trend-hold band (hysteresis); fade → reverts to VWAP/Pivot or `rs` turns trend.
- **Params:** `vr_q, vr_lookback, vr_scale, dead_band`(0.15), `trend_hold_band, fade_hold_band, cpr_narrow_pctile`(30), `cpr_wide_pctile`(70), `st_period, st_mult` + base.
- **Portability:** VR is a ratio; CPR width is a percentile; bands are σ → fully scale-free.

### 7.3 ORF — Opening-Range Fade/Break (`opening_range_adaptive`) — *Direction + Speed*
- **Hypothesis:** the opening range is trapped-trader fuel; the *same* OR event is a breakout on a trend/NR7 day and a fade on a range day. Crabel: breakouts after contraction have outsized payoff but are rare → be selective.
- **Core (opening window only; OR = first `or_minutes` of the session, computed from `ctx.history_df`):**
  ```
  break (rs>0 or nr7 or day_type==TREND): close beyond OR±break_buffer_atr·atr & accel confirm
                                          → buy expansion; target = or_range·or_target_mult (ATR-capped)
  fade  (rs<0 or day_type==RANGE):        poke beyond OR then close back inside (failed breakout)
                                          → fade toward opposite OR edge; confluence: RSI extreme / round_level / vwap side
  ```
- **Edge-decay exit:** break → price re-enters the OR (failed) or `accel_z` dies; fade → opposite OR edge / VWAP. Hard: no ORF entries after the opening window.
- **Params:** `or_minutes`(15), `break_buffer_atr`(0.1), `or_target_mult`(1.0), `require_nr7_for_break`(false) + base.
- **Portability:** OR range in points but targets are OR-/ATR-relative.

### 7.4 GAP — Gap-Fade (`gap_fade`) — *Direction edge*
- **Hypothesis:** large/emotional opening gaps mean-revert (NIFTY/SENSEX gap most days; small/moderate gaps fill a majority of the time); fade the chasers, not breakaway gaps.
- **Core (after `confirm_hhmm`, e.g. 09:45):**
  ```
  gap_atr = (day_open − prev_close)/atr
  gap-up   gap_atr> g_min & rsi>rsi_ob & stalling & accel turning down → PE, target prev_close/vwap/cpr_p
  gap-down gap_atr<−g_min & rsi<rsi_os & accel turning up            → CE, target prev_close/vwap/cpr_p
  SKIP if gap is WITH a strong accelerating trend (rs same sign as gap & |accel_z| high)  # breakaway, don't fade
  ```
- **Edge-decay exit:** scale at gap-fill / VWAP / `cpr_p`; reverse-on-trend if `rs` flips to strong continuation.
- **Params:** `g_min_atr`(1.0), `rsi_ob`(70), `rsi_os`(30), `confirm_hhmm`(09:45), `fill_target`("prev_close") + base.
- **Portability:** gap measured in ATR units → scale-free.

### 7.5 XRS — Cross-Index Relative Strength (`cross_index_rs`) — *Direction edge*
- **Hypothesis:** NIFTY & SENSEX are highly correlated but lead/lag intraday; the leader pulls the laggard, and RS-divergence flags exhaustion.
- **Engine dependency:** reads `ctx["companion_df"]` — the *other* index's enriched frame aligned by `ts` (the one opt-in change, §8).
- **Core:**
  ```
  rs = ret_self(rs_window) − ret_companion(rs_window)        # z-scored over a trailing window
  self leading up (rs> +rs_z & accel_z_self>0 & accel_z_comp>0) → CE
  self leading down (rs< −rs_z & both accel<0)                   → PE
  RS-divergence (self new high, companion not) → exhaustion: damp score / block continuation
  continue only when self is in a momentum regime (regime_score>0)
  ```
- **Edge-decay exit:** RS converges (leadership lost) or `accel_z` dies.
- **Params:** `rs_window`(15), `rs_z`(1.0) + base.
- **Portability:** symmetric — runs on either index as "self", the other as companion.

## 8. The XRS engine touch (only non-additive change)

`backtest.py`'s per-bar loop builds `ctx`. Add an **opt-in, keyword-only** parameter to the backtest entry that, when a companion instrument + its enriched frame are supplied, attaches `ctx["companion_df"]` and `ctx["companion_i"]` (the companion bar at/just-before the current `ts`, as-of aligned, **no look-ahead**). Default absent ⇒ `ctx` is byte-identical for the other 4 strategies and the existing 7. The companion frame is loaded/enriched the same way as the primary (warehouse `candles_1m` for the other index over the same window). This mirrors the low-risk, default-None, keyword-only pattern used for the backtest-cancel and exit-overlay work. Optimizing XRS requires the companion warehouse data to be present (a precondition check + clear error, like the option-data preconditions).

## 9. Optimize → survival → WFO → deploy-together workflow

No new pipeline — the strategies flow through what exists:

1. **Backtest** each strategy (spot + option re-rank) to sanity-check signal counts and option-₹.
2. **Optimize** (`/optimize/start`, `evaluation_mode="option_rerank"`, `survival_config.enabled=true`, costs on): Optuna tunes the strategy's `parameter_schema`; top-K finalists re-ranked by **real option-₹**; the **survival gate** disqualifies any finalist that breaches the capital floor / DD% / RoR on **OOS folds**; with `search_exit_controls=true`, the Piece-2 overlay (trailing/breakeven + daily caps) is tuned per surviving finalist.
3. **WFO** (`/wfo/start`, `option_aware=true`): rolling re-optimization → stitched OOS net-₹ + per-window consistency. This is the **self-improving** loop — params track the present market. **Promote only WFO-positive, plateau-stable survivors.**
4. **Save preset** (`apply as preset`): `config.execution` already carries moneyness/DTE/exit_mode/costs + (Piece-2) `exit_controls`/`daily_caps`. The strategy's `strategy_id` + tuned `params` ride along.
5. **Deploy together:** create one **paper** deployment per surviving strategy (`auto_paper=true`, `allow_overnight=false`). They evaluate independently on the 1-min close + live-tick exit monitor; EOD square-off applies. The user reviews the forward-vs-backtest parity per strategy before any scaling.

**Anti-overfit acceptance criteria (a strategy ships only if ALL hold):**
- Survival verdict = **survivable** on the configured OOS folds (capital floor primary; DD%; RoR upper-CI).
- **Positive option-₹** on the re-rank and **positive stitched OOS** in WFO, with non-degenerate per-window consistency.
- A **robustness plateau** (neighbor-param configs are also positive — the existing heatmap/robustness view), not a lone peak.
- `min_trades` met and **balanced CE/PE** share (`min_direction_share`) — no one-sided or 3-trade fluke.
- Cleared the **₹-expectancy gate** (Phase C) / ATR-vs-cost backstop (Phase A).

## 10. Config & params surfacing

- **No new request-schema fields are required** for Phases A–B: strategies expose `parameter_schema` (read by `GET /api/strategies` and the optimizer); the option/exit/cap/cost config already exists on `OptionBacktestReq` / `option_config` / `DeploymentCreateReq.risk` (Piece-2). XRS adds only the internal opt-in `ctx` companion frame (§8) and an optimizer precondition.
- **Phase C** adds two opt-in config blocks (default OFF ⇒ unchanged): an `edge_gate` toggle (+ `min_n`, `margin`) and an `edge_sizing` toggle on `sizing_config`. These follow the Piece-1/2 "flag off ⇒ byte-identical" rule and get the same per-path validation pattern.
- Each strategy's `parameter_schema` keeps params **few, monotonic, and bounded** (the WFO/robustness requirement). Shared base params (`k_acc, t_atr, s_atr, time_stop_min, signal_threshold, cooldown_bars, entry_cutoff_hhmm`) are declared once.

## 11. NIFTY ↔ SENSEX portability (how every threshold is scale-free)

| Concern | Mechanism |
|---|---|
| Targets / stops | `k·ATR` (never fixed points) → auto-scale to each index's range |
| Regime | Variance Ratio (a ratio) + `regime_score` clip → unit-free |
| CPR "narrow/wide" | width **percentile** over prior sessions → unit-free |
| VWAP stretch | `k·σ` bands → in the instrument's own units |
| Gap size | `gap_atr` (ATR units) → unit-free |
| Velocity / accel | z-scored → unit-free |
| Lot size / expiry / tick | always from `option_contracts` metadata (locked rule) — SENSEX's different lot & weekly expiry handled by the existing contract layer, never weekday-hardcoded |

This is the entirety of the "SENSEX-specific" requirement: **nothing is in absolute points**, so one strategy body serves both. SENSEX's larger point scale and separate expiry are absorbed by ATR/percentile relativity + contract metadata.

## 12. Look-ahead, causality & determinism (correctness keystones)

- **Every new indicator is causal** — trailing windows only; no `center=True`; `squeeze_fire` uses `shift(1)`; `nr7` and CPR use only **completed prior** sessions; `vol_seasonality`/`edge_gate` estimate on **train**, apply OOS; XRS companion bar is **as-of** (≤ current `ts`). A dedicated look-ahead regression per primitive (mirroring the `detect_swing_points` guard) is mandatory.
- **Deterministic** given inputs; Optuna stays stochastic exactly as today.
- **Disabled = unchanged:** the new columns are additive; the existing 7 strategies don't read them and are unaffected; `edge_gate`/`edge_sizing`/XRS-companion default off/absent ⇒ byte-identical engine and pinned tests stay green.

## 13. Testing (host-safe, existing patterns; no `motor`/`optuna` import)

- **Toolkit units** (`test_adaptive_indicators.py`, `test_cpr.py`, `test_vol_seasonality.py`): correctness on crafted series; **look-ahead regression** per primitive; VR>1 on a synthetic trend / <1 on a mean-reverting series; squeeze on/fire transitions; supertrend flip; price-based vwap-σ with zero volume; nr7 selects the right session; CPR formula + width-percentile day_type + TC/BC swap; vol-seasonality train-only (no leakage).
- **Shared base** (`test_adaptive_base.py`): time-gate blocks outside `tod_tradeable`/after cutoff; Speed-confirm blocks weak `accel_z`; ATR exits computed from `row.atr`; EOD/time-stop attached.
- **Per-strategy** (`test_strategy_<id>.py`): each emits valid `Signal`s on synthetic regimes (SEB fires on a coil-release; ARS picks fade vs trend by `regime_score`; ORF break vs fade by day_type; GAP fades a large gap and skips a breakaway; XRS picks the leader); CE/PE symmetry; NONE when warming up.
- **Optimizer recompute** (`test_optimizer_indicator_keys.py`): changing `vr_q`/`bb_len`/etc. changes the cached enriched frame (proves the `INDICATOR_PARAM_KEYS` wiring — the silent-no-op guard).
- **XRS engine** (`test_backtest_companion_ctx.py`): companion frame present ⇒ `ctx.companion_df` as-of aligned; absent ⇒ `ctx` byte-identical (existing backtest tests green).
- **Phase C**: `edge_gate` fit-on-train/apply-OOS, cold-start backstop; survival-capped sizing never exceeds the RoR cap.
- **Contract corpus:** strategies are **not** corpus-pinned (the corpus asserts on routes/components, not strategy ids), so no corpus churn for the strategy files. Any Phase-C config fields added to `schemas.py` get the usual corpus assertion + per-path validation.
- `pytest -q` (currently 612) must stay green; new tests add to it.

## 14. Phasing & verification

- **Phase A — toolkit + 3 core strategies.** Per-bar columns + `cpr.py` + `vol_seasonality.py` (trailing-estimate form) + `adaptive_base.py`; SEB, ARS, ORF; optimizer `INDICATOR_PARAM_KEYS` wiring; ATR-vs-cost cold-start gate; fixed lots. Verify: each strategy optimizes under the survival gate on NIFTY **and** SENSEX, produces survivors with positive OOS option-₹, no look-ahead.
- **Phase B — GAP + XRS.** GAP (additive); XRS + the opt-in companion-frame engine touch + precondition. Verify: companion alignment causal; the other strategies byte-identical.
- **Phase C — self-improving layers.** `edge_gate.py` (walk-forward ₹-expectancy) + edge-proportional survival-capped sizing + train-window `vol_seasonality`. Verify: OOS gate improves survivor quality without leakage; sizing respects the RoR cap.
- Per phase: `pytest -q` green; `npm run build` clean; `docker compose up -d --build`; running-stack smoke — optimize one strategy end-to-end (option re-rank + survival), confirm survivors + WFO stitch, deploy as paper and watch one signal → auto paper trade → tick exit → EOD square-off.

## 15. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Overfitting** (the core problem) | self-normalizing/percentile/ATR-relative thresholds; few monotonic params; promotion only via survival + WFO-OOS + robustness plateau; ₹-expectancy gate trades only demonstrated-edge contexts |
| Index spot **volume** unreliable | `session_vwap` already falls back to a price-based cumulative mean; vwap-σ is **price-based** to match; no strategy depends on raw volume |
| `vol_seasonality` / `edge_gate` **in-window leakage** | estimate on train/IS or trailing-N only, apply OOS; enforced + unit-tested |
| Squeeze/Supertrend **whipsaw / theta bleed** | Speed confirm + time-stop + edge-decay exit + the morning-window time gate; survival gate kills configs that bleed |
| **CPR day boundaries / holidays** | prior **completed** RTH session via existing `session_date` grouping + `nse_calendar`; tested |
| XRS **engine touch** | keyword-only, default-absent, as-of aligned; existing tests pin byte-identical `ctx`; data precondition |
| Edge-proportional **sizing → ruin** (the user's −₹49k/−₹13-14k lesson) | OFF by default; hard-capped by the existing `survival`/RoR gate; tested against the cap |
| **Scope** (5 strategies + 3 estimated artifacts) | strict phasing (A/B/C); each strategy independently shippable; Phase C is opt-in |
| Design blind spots | run the **adversarial multi-agent design audit** (Piece-1/2 pattern) before build |

## 16. Out of scope / future

- **Real broker orders** — permanent: paper only.
- **Tick-native intra-bar entries** — entries stay 1-min-close (no repaint), matching the live realism design; ticks drive exits.
- **Options-chain / OI / PCR / max-pain signals** — `oi` exists on option candles but needs its own data-plumbing + design; a candidate future edge source, not this slate.
- **Cross-strategy arbitration / portfolio allocation** across the deployed strategies — they run independently now; a meta-allocator is a later piece (after ≥ forward history).
- **More strategies** (the user invited additions) — the toolkit makes a 6th/7th cheap; specced separately.
- **Kelly / capital-aware trials** beyond the survival-capped sizing here — Piece-1 Approach B / the deferred Phase-5 item.
