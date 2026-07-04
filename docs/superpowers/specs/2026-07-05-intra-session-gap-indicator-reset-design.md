# Intra-session gap-aware indicator warm-up reset — design

**Date:** 2026-07-05
**Status:** approved (design), pending implementation plan
**Tracked from:** `docs/OPTIMIZER_VERDICT_2026-07.md` — research-path edge case #2 (SILENT-BUG, DEFERRED)

## Problem

`load_candles_df` (`backend/app/warehouse.py`) returns raw DB rows sorted by `ts`
with a positional `RangeIndex` and **no reindex to a complete minute grid**.
`run_backtest` (`backend/app/backtest.py`) iterates positionally
(`for i in range(1, len(df)): row = records[i]; prev = records[i-1]`).

A mid-session warehouse gap (a partial-day hole, or the boundary around a
half-day/closure that leaves a session partially present) makes the post-gap bar
**positionally adjacent** to the pre-gap bar. The whole-frame rolling/EWM
indicators in `precompute_all_indicators` (`backend/app/indicators.py`) then
compute **across the time discontinuity** — no `NaN` is produced and no warning
fires. This silently contaminates signals on bars adjacent to a gap.

Whole missing **days** are already caught by the day-level audit
`_audit_and_fill_backtest_data` (`backend/app/runtime.py`). Intra-session partial
gaps are not. This is a silent correctness bug affecting signal quality on
gap-adjacent bars.

### Critical nuance that constrains the fix

In `precompute_all_indicators`, indicators fall into two classes:

- **Whole-frame (carry state across ALL day boundaries by design):** `ema9/21/50`,
  `rsi`, `macd`, `atr`, `adx`, `chop`, `atr_avg`, `fvg`, swing points, `vel_z/accel_z`,
  `vr/regime_score`, squeeze, `supertrend`, and `candle_geometry`'s `close_z`/`inside_bar`.
  These call `.ewm()/.rolling()/.diff()/.shift()`/recursive loops over the **entire
  frame**, so they *already* smear across normal overnight boundaries — that is the
  existing, intended contract.
- **Per-session (reset daily via `groupby("session_date")`, or per-row):** `vwap`,
  `vwap_sigma`, `nr7`, `cpr`, `orb_width`, `tod_tradeable` (session-level
  aggregations), and `regime` (per-row over already-computed columns).

Because the whole-frame indicators already carry across overnight boundaries,
**"reset warm-up at *each* session boundary" is NOT viable** — it would change
those indicators at every overnight boundary and break byte-parity on any normal
multi-day window. The fix must key specifically on **abnormal intra-session
gaps**, never on normal overnight boundaries.

## Goals / non-goals

**Goals**
- Whole-frame indicators re-warm (produce `NaN` until warm-up completes) at each
  intra-session gap, so no strategy fires on indicators smeared across a hole.
- Byte-identical output on gap-free windows (existing indicator columns and
  resulting trades unchanged), proven by construction.
- Keep the optimizer/WFO memoized hot path (`indicator_groups.py`) byte-identical
  to the monolithic precompute (`tests/test_indicator_equivalence.py` stays green).

**Non-goals**
- Option-side gaps. Those are a pairing/coverage failure mode (edge case #1,
  already fixed via the exit-candle preflight), not indicator smearing.
- Reindexing the frame to a synthetic complete minute grid (would inject fake OHLC
  and change trade timing).
- Any change to `run_backtest`'s positional loop.

## Design

### 1. Gap detection — `gap_before` column

A per-bar boolean column, computed **first** in enrichment (before any whole-frame
indicator):

```
gap_before[i] = (ts[i] - ts[i-1] > MAX_CONTIGUOUS_GAP_MS) AND same_ist_date(i, i-1)
gap_before[0] = False
```

- `MAX_CONTIGUOUS_GAP_MS = 60_000` (1 minute). Warehouse candles are
  minute-aligned, so contiguous bars are exactly 60_000 ms apart; any larger
  within-session delta is a genuine hole. Named module constant for testability.
- `same_ist_date` uses the IST calendar date derived from `ts`. Cross-session
  (overnight, half-day → next-day) boundaries are a *different* IST date and are
  therefore **never** flagged — this preserves the existing overnight-carry
  contract.
- On a gap-free window, `gap_before` is all-`False`.

`gap_before` is exposed as a real column (not a throwaway local) because:
(a) the `indicator_groups.py` group path consumes it by reading `df["gap_before"]`,
and (b) it doubles as observability (which bars had gaps). It is additive and
all-`False` on gap-free windows, so it changes no existing value or trade.

### 2. Warm-up reset — `_reset_on_gap` wrapper (with fast-path)

A single helper in `indicators.py`, imported by `indicator_groups.py`:

```python
def _reset_on_gap(df, fn, *, mask_col="gap_before"):
    """Apply fn to each gap-bounded contiguous slice of df and reassemble.
    fn: (sub_df) -> Series | tuple[Series,...] | dict[str, Series].
    Fast-path: no gaps -> fn(df) unchanged (byte-identical to current)."""
    gb = df[mask_col].to_numpy()
    if not gb.any():
        return fn(df)                      # <-- construction guarantee of parity
    # split at gap starts into contiguous positional [start, end) slices,
    # apply fn per slice, reassemble by concatenation preserving the frame index
```

- **Fast-path is the parity guarantee.** When `gap_before` has no `True` values,
  the wrapper returns `fn(df)` — literally the current call. Gap-free windows
  execute the existing code path bit-for-bit; no `concat`, no reordering.
- **Gapped case** splits at gap boundaries (contiguous positional slices, because
  the frame is sorted by `ts`), applies `fn` to each slice (each re-warms from the
  slice's first row), and `pd.concat`s the parts back in order (slices retain the
  original index labels). Each segment's result equals the standalone computation
  on that slice — the definition of a correct warm-up reset.
- Handles the helpers' varied return shapes: `Series` (ema/rsi/atr/adx/chop/
  atr_avg/fvg), `tuple[Series,...]` (macd/velocity/variance_ratio/squeeze/
  supertrend), and `dict[str, Series]` (candle_geometry, swing points extracted as
  two columns).

### 3. What gets wrapped

Wrapped in `_reset_on_gap` (whole-frame, minute-level cross-bar):

`ema9`, `ema21`, `ema50`, `rsi`, `macd`, `atr`, `adx`, `chop`, `atr_avg`, `fvg`,
`is_swing_high`/`is_swing_low`, `vel_z`/`accel_z`, `vr`/`regime_score`,
`squeeze_on`/`squeeze_fire`/`sqz_mom`, `supertrend`/`st_dir`, and
`candle_geometry` (`body_frac`/`upper_wick_frac`/`lower_wick_frac`/`inside_bar`/
`close_z` — single-bar members are unaffected by segmentation and stay identical;
`inside_bar`/`close_z` correctly reset).

Left unchanged (per-session `groupby` or per-row): `session_date`/`ist_time`,
`vwap`, `vwap_sigma`, `nr7`, `cpr`, `orb_width`, `tod_tradeable`, `regime`.

`atr_avg` is wrapped even though it reads the `atr` column, because
`atr.rolling(100).mean()` would otherwise average across the gap; wrapping it makes
the rolling window see only its own segment's (already reset) `atr`.

### 4. `indicator_groups.py` mirror

- Add a param-independent `gap_before` group at the **front** of `GROUPS` so the
  mask column exists before any consumer runs.
- Each wrapped group's compute fn reads `df["gap_before"]` via the same
  `_reset_on_gap` wrapper, so it is byte-identical to precompute by construction.
- Both paths add `gap_before` identically → `test_indicator_equivalence.py` stays
  green.

### 5. No `run_backtest` change

The reset yields `NaN` indicators during each segment's re-warmup — the same
condition that already exists at the start of every frame (e.g. the first ~14 ATR
bars are `NaN`). Strategies already tolerate leading `NaN`s (`NaN` comparisons →
`False` → no signal fires), so post-gap bars are simply not traded on. Open-position
management across a gap is unaffected: OHLC is never `NaN`, so price-based
stop/target/time exits still work. The fix stays entirely in the enrichment layer.

### 6. Scope of effect

- `precompute_all_indicators` is called by the one-shot backtest/preflight
  (`runtime.py`, `routers/research.py`), the optimizer/WFO (via
  `indicator_groups.enrich_with_cache`), and paper/live deployments
  (`deployment_evaluator.py`). All inherit the fix.
- **Live/paper side effect (intended):** after a real-time data hiccup that leaves
  an intra-session hole, live signals self-suppress until re-warmed — a desirable
  safety property. Called out so the behavior change beyond backtests is explicit.

## Testing

New test module (host-runnable; no DB):

1. **Parity on a gap-free window.** Build a synthetic multi-day, fully-populated
   1-minute frame. Assert every existing indicator column from
   `precompute_all_indicators` is bit-identical to the pre-change reference
   (`assert_frame_equal` on the known indicator columns), and `gap_before` is
   all-`False`.
2. **Correctness across a synthetic gap.** Take the same frame, delete a contiguous
   run of minutes mid-session. Assert:
   - `gap_before` is `True` exactly at the first post-gap bar and `False` elsewhere;
   - each wrapped indicator on the post-gap segment equals the standalone helper
     computed on that segment's slice in isolation;
   - the pre-gap segment's wrapped-indicator values are unchanged vs. computing on
     the pre-gap slice alone (no leakage backward).
3. **Equivalence unchanged.** `tests/test_indicator_equivalence.py`
   (precompute vs. `enrich_with_cache`) stays green with `gap_before` present in
   both paths.
4. **Column-set guard.** Verify no existing test rigidly pins the exact column set
   of `precompute_all_indicators` output such that an additive `gap_before` breaks
   it; if one exists, update it deliberately.

## Risks

- **Byte-parity drift in `indicator_groups.py`.** Mitigated by the shared
  `_reset_on_gap` wrapper (identical call in both paths) and the existing
  equivalence test.
- **Return-shape coverage in `_reset_on_gap`.** The reassembly must handle Series,
  tuple, and dict returns; covered by the synthetic-gap test exercising each
  wrapped helper.
- **Perf on the optimizer hot path.** `gap_before.any()` is O(N) and the mask is a
  param-independent group cached once per job; the fast-path adds no per-trial work
  on gap-free windows.
