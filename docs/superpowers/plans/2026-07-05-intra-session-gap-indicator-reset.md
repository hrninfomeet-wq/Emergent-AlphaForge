# Intra-session Gap-aware Indicator Warm-up Reset — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the whole-frame spot indicators (ATR/EMA/RSI/ADX/…) re-warm at each intra-session warehouse gap so no strategy fires on indicators smeared across a time discontinuity, with byte-identical output on gap-free windows.

**Architecture:** Add a per-bar `gap_before` boolean column (True only for a >1-minute jump *within* the same IST session; overnight boundaries are never flagged). A `_reset_on_gap(df, fn)` wrapper applies each whole-frame indicator per gap-bounded contiguous slice and reassembles; a fast-path (`if not gap_before.any(): return fn(df)`) makes gap-free windows execute the existing code path bit-for-bit. The change lives entirely in the enrichment layer (`indicators.py` + its memoized mirror `indicator_groups.py`); `run_backtest` is untouched because post-gap NaN warm-up is the same condition strategies already tolerate at frame start.

**Tech Stack:** Python 3.12, pandas 2.3.2 (host) / 3.0.3 (`.venv`), numpy, pytest.

**Reference spec:** `docs/superpowers/specs/2026-07-05-intra-session-gap-indicator-reset-design.md`

**Test command (from worktree root):** `python -m pytest tests/<file> -q`

**Byte-parity guardrail (must stay green after every task):**
`python -m pytest tests/test_indicator_equivalence.py -q` → `7 passed`

---

## File structure

- **Modify** `backend/app/indicators.py` — add `MAX_CONTIGUOUS_GAP_MS`, `gap_before_mask`, `_reset_on_gap`; add the `gap_before` column and wrap the whole-frame indicators inside `precompute_all_indicators`.
- **Modify** `backend/app/indicator_groups.py` — import the two new helpers; add a first `gap_before` group; wrap each whole-frame `_compute_*` identically so the memoized optimizer path stays byte-identical.
- **Create** `tests/test_gap_reset.py` — unit tests for the helpers + end-to-end precompute detection, parity, and reset-across-gap tests, plus a precompute-vs-groups equality test on a *gapped* frame.
- **Modify** `tests/test_indicator_equivalence.py` — add `gap_before` to the `test_expected_columns_present` list (documentation of the new column).
- **Modify** `docs/OPTIMIZER_VERDICT_2026-07.md`, `CHANGELOG.md`, `docs/HANDOFF.md` — flip edge case #2 to FIXED and record the change.

**Whole-frame indicators to wrap (reset per segment):** `ema9`, `ema21`, `ema50`, `rsi`, `macd`, `atr`, `adx`, `chop`, `atr_avg`, `fvg`, swing points, velocity, variance_ratio, squeeze, supertrend, candle_geometry.
**Left unchanged (per-session `groupby` / per-row):** `session_date`/`ist_time`, `vwap`, `vwap_sigma`, `nr7`, `cpr`, `orb_width`, `tod_tradeable`, `regime`.

---

## Task 1: Gap-detection + reset-wrapper helpers (pure, no wiring)

**Files:**
- Modify: `backend/app/indicators.py` (add helpers near the top, after imports)
- Test: `tests/test_gap_reset.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gap_reset.py`:

```python
# tests/test_gap_reset.py — intra-session gap detection + warm-up reset.
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pandas as pd

from app.indicators import (
    MAX_CONTIGUOUS_GAP_MS, gap_before_mask, _reset_on_gap,
    atr, ema, velocity_accel, candle_geometry,
)
from tests._adaptive_testutil import make_ohlc, make_sessions


def _drop_mid_session(df, start_i, count):
    """Delete `count` contiguous rows starting at positional start_i, creating an
    intra-session hole; return a fresh 0..N-1 indexed frame."""
    keep = [i for i in range(len(df)) if not (start_i <= i < start_i + count)]
    return df.iloc[keep].reset_index(drop=True)


def test_gap_constant_is_one_minute():
    assert MAX_CONTIGUOUS_GAP_MS == 60_000


def test_gap_mask_all_false_on_contiguous_session():
    df = make_ohlc([100 + (i % 7) for i in range(60)])
    m = gap_before_mask(df)
    assert m.dtype == bool and len(m) == len(df)
    assert not m.any()


def test_gap_mask_flags_only_first_post_gap_bar():
    df = make_ohlc([100 + (i % 7) for i in range(60)])
    gapped = _drop_mid_session(df, 30, 5)          # remove positions 30..34
    m = gap_before_mask(gapped).to_numpy()
    assert m[30] == True                           # first bar after the hole
    assert m.sum() == 1                            # exactly one boundary


def test_gap_mask_ignores_overnight_boundary():
    df = make_sessions([[100 + (i % 5) for i in range(30)],
                        [200 + (i % 5) for i in range(30)]])
    m = gap_before_mask(df).to_numpy()
    assert not m.any()                             # cross-date boundary is NOT a gap


def test_reset_on_gap_fastpath_is_identity_series():
    df = make_ohlc([100 + (i % 7) for i in range(60)])
    df["gap_before"] = gap_before_mask(df)
    out = _reset_on_gap(df, lambda d: atr(d, 14))
    pd.testing.assert_series_equal(out, atr(df, 14))


def test_reset_on_gap_segments_reset_series():
    df = make_ohlc([100 + (i % 7) * 1.3 for i in range(60)])
    gapped = _drop_mid_session(df, 30, 5)          # segments [0:30] and [30:55]
    gapped["gap_before"] = gap_before_mask(gapped)
    out = _reset_on_gap(gapped, lambda d: atr(d, 14))
    pd.testing.assert_series_equal(out.iloc[0:30], atr(gapped.iloc[0:30], 14))
    pd.testing.assert_series_equal(out.iloc[30:55], atr(gapped.iloc[30:55], 14))
    assert out.iloc[30:30 + 13].isna().all()       # RESET: post-gap warm-up NaN again


def test_reset_on_gap_tuple_and_dict_shapes():
    df = make_ohlc([100 + (i % 7) * 1.1 for i in range(60)])
    gapped = _drop_mid_session(df, 30, 5)
    gapped["gap_before"] = gap_before_mask(gapped)
    vz, az = _reset_on_gap(gapped, lambda d: velocity_accel(d["close"], 2, 60))
    assert len(vz) == len(gapped) and len(az) == len(gapped)
    geo = _reset_on_gap(gapped, lambda d: candle_geometry(d))
    assert set(geo) >= {"body_frac", "inside_bar", "close_z"}
    assert all(len(s) == len(gapped) for s in geo.values())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_gap_reset.py -q`
Expected: FAIL at import — `ImportError: cannot import name 'MAX_CONTIGUOUS_GAP_MS'`.

- [ ] **Step 3: Implement the helpers**

In `backend/app/indicators.py`, immediately after the existing imports (after the
`from app.vol_seasonality import attach_tod_tradeable` line, before `def ema`), add:

```python
# --- Intra-session gap handling -------------------------------------------
# Warehouse candles are 1-minute and minute-aligned, so contiguous bars are
# exactly 60_000 ms apart. A larger delta WITHIN the same IST session is a hole
# (partial-day gap / half-day boundary). Overnight (cross-date) boundaries are a
# different IST date and are intentionally NOT flagged — the whole-frame EWM/
# rolling indicators are designed to carry across them.
MAX_CONTIGUOUS_GAP_MS = 60_000


def gap_before_mask(df: pd.DataFrame) -> pd.Series:
    """Per-bar bool: True where this bar is >1 min after the previous bar AND in
    the same IST session (calendar date). First bar is False. Derived from `ts`
    only, so it is independent of any pre-existing `session_date` column."""
    ts = df["ts"].to_numpy(dtype="int64")
    n = len(ts)
    out = np.zeros(n, dtype=bool)
    if n >= 2:
        ist_date = (pd.to_datetime(df["ts"], unit="ms", utc=True)
                    .dt.tz_convert("Asia/Kolkata").dt.normalize().to_numpy())
        delta = ts[1:] - ts[:-1]
        same_session = ist_date[1:] == ist_date[:-1]
        out[1:] = (delta > MAX_CONTIGUOUS_GAP_MS) & same_session
    return pd.Series(out, index=df.index)


def _reset_on_gap(df: pd.DataFrame, fn, *, mask_col: str = "gap_before"):
    """Apply `fn` to each gap-bounded contiguous slice of `df` and reassemble.

    `fn(sub_df)` returns a Series, a tuple[Series, ...], or a dict[str, Series].
    Fast-path: when there is no intra-session gap, return `fn(df)` unchanged so
    gap-free windows are byte-identical to the pre-change computation. When gaps
    exist, split at each gap boundary into contiguous positional slices, apply
    `fn` per slice (each re-warms from its first row), and concatenate the parts
    back in order (slices keep the original index, so the result realigns).
    """
    gb = df[mask_col].to_numpy(dtype=bool)
    if not gb.any():
        return fn(df)
    cuts = np.flatnonzero(gb).tolist()
    starts = [0, *cuts]
    ends = [*cuts, len(gb)]
    parts = [fn(df.iloc[s:e]) for s, e in zip(starts, ends)]
    first = parts[0]
    if isinstance(first, tuple):
        return tuple(pd.concat([p[k] for p in parts]) for k in range(len(first)))
    if isinstance(first, dict):
        return {key: pd.concat([p[key] for p in parts]) for key in first}
    return pd.concat(parts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_gap_reset.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Confirm the byte-parity guardrail still passes**

Run: `python -m pytest tests/test_indicator_equivalence.py -q`
Expected: PASS (7 passed) — no wiring yet, so nothing changed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/indicators.py tests/test_gap_reset.py
git commit -m "feat(indicators): add intra-session gap_before_mask + _reset_on_gap helpers"
```

---

## Task 2: Add the `gap_before` column to both enrichment paths

**Files:**
- Modify: `backend/app/indicators.py` — `precompute_all_indicators` (first line of body)
- Modify: `backend/app/indicator_groups.py` — new group + registry entry
- Modify: `tests/test_gap_reset.py` — add detection tests
- Modify: `tests/test_indicator_equivalence.py` — add `gap_before` to expected columns

- [ ] **Step 1: Write the failing detection tests**

Append to `tests/test_gap_reset.py`:

```python
def test_precompute_adds_gap_before_all_false_on_clean_frame():
    from app.indicators import precompute_all_indicators
    df = make_sessions([[100 + (i % 9) for i in range(80)],
                        [110 + (i % 9) for i in range(80)]])
    enr = precompute_all_indicators(df.copy(), {})
    assert "gap_before" in enr.columns
    assert enr["gap_before"].dtype == bool
    assert not enr["gap_before"].any()


def test_precompute_flags_intra_session_gap():
    from app.indicators import precompute_all_indicators
    df = make_ohlc([100 + (i % 9) * 0.7 for i in range(80)])   # single session
    gapped = _drop_mid_session(df, 40, 6)
    enr = precompute_all_indicators(gapped.copy(), {})
    assert enr["gap_before"].sum() == 1
    assert bool(enr["gap_before"].iloc[40]) is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_gap_reset.py -k "gap_before" -q`
Expected: FAIL — `assert 'gap_before' in enr.columns` fails (column not added yet).

- [ ] **Step 3: Add the column in `precompute_all_indicators`**

In `backend/app/indicators.py`, in `precompute_all_indicators`, insert the
`gap_before` assignment as the FIRST statement after `df = df.copy()`:

```python
    p = params or {}
    df = df.copy()
    df["gap_before"] = gap_before_mask(df)
    df["ema9"] = ema(df["close"], int(p.get("ema_fast", 9)))
```

(Only the `df["gap_before"] = gap_before_mask(df)` line is new; the surrounding
lines are shown for placement. Do NOT wrap indicators yet — that is Task 3.)

- [ ] **Step 4: Add the mirror group in `indicator_groups.py`**

In `backend/app/indicator_groups.py`, update the import block to pull the two
helpers, add a compute fn, and register it FIRST.

Extend the `from app.indicators import (...)` list with:

```python
    gap_before_mask,
    _reset_on_gap,
```

Add this compute fn (place it just before `_compute_ema`):

```python
def _compute_gap_before(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    # Param-independent; must run FIRST so the wrapped groups can read the mask.
    return {"gap_before": gap_before_mask(df)}
```

Add this as the FIRST entry in the `GROUPS` list (before the `ema` group):

```python
GROUPS = [
    IndicatorGroup("gap_before", (), _compute_gap_before),
    IndicatorGroup("ema", ("ema_fast", "ema_slow"), _compute_ema),
    # ... rest unchanged ...
```

- [ ] **Step 5: Document the new column in the equivalence test**

In `tests/test_indicator_equivalence.py`, `test_expected_columns_present`, add
`"gap_before"` to the tuple of asserted columns:

```python
    for col in ("gap_before", "ema9", "ema21", "rsi", "macd_hist", "atr",
                "atr_avg", "adx", "chop", "vwap", "session_date", "ist_time",
                "regime", "squeeze_on", "supertrend", "st_dir", "tod_tradeable",
                "cpr_tc", "cpr_bc", "day_type", "nr7", "fvg",
                "orb_width_pct_partial", "orb_width_pct_prior"):
        assert col in enr.columns, f"missing {col}"
```

- [ ] **Step 6: Run the detection tests + the byte-parity guardrail**

Run: `python -m pytest tests/test_gap_reset.py tests/test_indicator_equivalence.py -q`
Expected: PASS. The equivalence full-frame `assert_frame_equal` still holds because
`gap_before` is added at the identical position (first appended column) in BOTH
paths and is all-False on the gap-free fixture.

- [ ] **Step 7: Commit**

```bash
git add backend/app/indicators.py backend/app/indicator_groups.py tests/test_gap_reset.py tests/test_indicator_equivalence.py
git commit -m "feat(indicators): emit gap_before column in both enrichment paths"
```

---

## Task 3: Wrap the whole-frame indicators with `_reset_on_gap` (the reset)

**Files:**
- Modify: `backend/app/indicators.py` — wrap whole-frame indicator lines in `precompute_all_indicators`
- Modify: `backend/app/indicator_groups.py` — wrap the matching `_compute_*` fns
- Modify: `tests/test_gap_reset.py` — parity + reset + groups-on-gap tests

- [ ] **Step 1: Write the failing parity + reset tests**

Append to `tests/test_gap_reset.py`:

```python
def test_precompute_byte_identical_on_gap_free_window():
    from app.indicators import precompute_all_indicators, ema, rsi, atr, adx, supertrend
    df = make_sessions([[100 + (i % 11) - (i % 4) * 0.5 for i in range(90)],
                        [105 + (i % 11) - (i % 4) * 0.5 for i in range(90)]])
    enr = precompute_all_indicators(df.copy(), {})
    base = df.copy()
    pd.testing.assert_series_equal(enr["ema9"], ema(base["close"], 9), check_names=False)
    pd.testing.assert_series_equal(enr["rsi"], rsi(base["close"], 14), check_names=False)
    pd.testing.assert_series_equal(enr["atr"], atr(base, 14), check_names=False)
    pd.testing.assert_series_equal(enr["adx"], adx(base, 14), check_names=False)
    st, st_dir = supertrend(base, 10, 3.0)
    pd.testing.assert_series_equal(enr["supertrend"], st, check_names=False)
    pd.testing.assert_series_equal(enr["st_dir"], st_dir, check_names=False)


def test_precompute_resets_indicators_across_gap():
    from app.indicators import precompute_all_indicators, atr, ema
    df = make_ohlc([100 + (i % 11) * 1.2 - (i % 4) * 0.6 for i in range(120)])
    gapped = _drop_mid_session(df, 60, 8)
    enr = precompute_all_indicators(gapped.copy(), {})
    boundary = int(np.flatnonzero(enr["gap_before"].to_numpy())[0])
    pre = gapped.iloc[:boundary]
    post = gapped.iloc[boundary:]
    # post-gap segment re-warms independently
    pd.testing.assert_series_equal(
        enr["atr"].iloc[boundary:].reset_index(drop=True),
        atr(post, 14).reset_index(drop=True), check_names=False)
    pd.testing.assert_series_equal(
        enr["ema9"].iloc[boundary:].reset_index(drop=True),
        ema(post["close"], 9).reset_index(drop=True), check_names=False)
    # pre-gap segment untouched (no forward leakage)
    pd.testing.assert_series_equal(
        enr["atr"].iloc[:boundary].reset_index(drop=True),
        atr(pre, 14).reset_index(drop=True), check_names=False)
    # RESET proof: atr is NaN again right after the gap
    assert enr["atr"].iloc[boundary:boundary + 13].isna().all()


def test_precompute_matches_groups_on_gapped_frame():
    from app.indicators import precompute_all_indicators
    from app.regime import classify_regime_series
    from app.indicator_groups import run_all_groups
    df = make_ohlc([100 + (i % 13) * 1.1 - (i % 5) * 0.5 for i in range(150)])
    gapped = _drop_mid_session(df, 70, 10)
    ref = precompute_all_indicators(gapped.copy(), {})
    ref["regime"] = classify_regime_series(ref)
    new = run_all_groups(gapped.copy(), {})
    pd.testing.assert_frame_equal(new, ref, check_like=True, check_dtype=True)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_gap_reset.py -k "reset or gapped_frame" -q`
Expected: FAIL — `test_precompute_resets_indicators_across_gap` fails
(`enr["atr"].iloc[boundary:boundary+13]` is not all-NaN because the indicators
still smear across the gap).

- [ ] **Step 3: Wrap the whole-frame indicators in `precompute_all_indicators`**

In `backend/app/indicators.py`, replace the whole-frame indicator lines with
`_reset_on_gap`-wrapped versions. The block from `df["ema9"]` through `df["chop"]`
becomes:

```python
    df["gap_before"] = gap_before_mask(df)
    df["ema9"] = _reset_on_gap(df, lambda d: ema(d["close"], int(p.get("ema_fast", 9))))
    df["ema21"] = _reset_on_gap(df, lambda d: ema(d["close"], int(p.get("ema_slow", 21))))
    df["ema50"] = _reset_on_gap(df, lambda d: ema(d["close"], 50))
    df["rsi"] = _reset_on_gap(df, lambda d: rsi(d["close"], int(p.get("rsi_length", 14))))
    macd_line, signal_line, hist = _reset_on_gap(df, lambda d: macd(
        d["close"],
        int(p.get("macd_fast", 12)),
        int(p.get("macd_slow", 26)),
        int(p.get("macd_signal", 9)),
    ))
    df["macd_line"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_hist"] = hist
    df["atr"] = _reset_on_gap(df, lambda d: atr(d, int(p.get("atr_length", 14))))
    df["adx"] = _reset_on_gap(df, lambda d: adx(d, int(p.get("adx_length", 14))))
    df["chop"] = _reset_on_gap(df, lambda d: choppiness_index(d, int(p.get("chop_length", 14))))
```

Leave the `dt` / `session_date` / `ist_time` / `vwap` block UNCHANGED. Then wrap
`atr_avg`, `fvg`, swing points:

```python
    df["atr_avg"] = _reset_on_gap(df, lambda d: d["atr"].rolling(100, min_periods=20).mean())
    df["fvg"] = _reset_on_gap(df, lambda d: detect_fvg(d))

    def _swing_cols(d):
        o = detect_swing_points(d, lookback=int(p.get("swing_lookback", 5)))
        return {"is_swing_high": o["is_swing_high"], "is_swing_low": o["is_swing_low"]}
    _sw = _reset_on_gap(df, _swing_cols)
    df["is_swing_high"] = _sw["is_swing_high"]
    df["is_swing_low"] = _sw["is_swing_low"]
```

(This replaces the old `df = detect_swing_points(df, lookback=...)` reassignment;
it assigns the same two columns in the same order — the discarded full-frame copy
was redundant, and the memoized path already extracts exactly these two columns.)

Then wrap velocity / variance_ratio / squeeze / supertrend:

```python
    df["vel_z"], df["accel_z"] = _reset_on_gap(df, lambda d: velocity_accel(
        d["close"], int(p.get("vel_n", 2)), int(p.get("vel_z_window", 60))))
    df["vr"], df["regime_score"] = _reset_on_gap(df, lambda d: variance_ratio(
        d["close"], int(p.get("vr_q", 4)), int(p.get("vr_lookback", 90)), float(p.get("vr_scale", 0.5))))
    on, fire, mom = _reset_on_gap(df, lambda d: squeeze(
        d, int(p.get("bb_len", 20)), float(p.get("bb_mult", 2.0)),
        int(p.get("kc_len", 20)), float(p.get("kc_atr_mult", 1.5)), int(p.get("sqz_mom_len", 20))))
    df["squeeze_on"], df["squeeze_fire"], df["sqz_mom"] = on, fire, mom
    df["supertrend"], df["st_dir"] = _reset_on_gap(df, lambda d: supertrend(
        d, int(p.get("st_period", 10)), float(p.get("st_mult", 3.0))))
```

Leave `vwap_sigma_bands`, `nr7`, `cpr_levels`, `_compute_orb_width`,
`attach_tod_tradeable` UNCHANGED (per-session). Finally wrap candle_geometry:

```python
    for _gname, _gser in _reset_on_gap(df, lambda d: candle_geometry(d)).items():
        df[_gname] = _gser
```

- [ ] **Step 4: Mirror the wrapping in `indicator_groups.py`**

Replace each whole-frame `_compute_*` body with the `_reset_on_gap`-wrapped form.
Leave `_compute_time`, `_compute_vwap`, `_compute_vwap_sigma`, `_compute_nr7`,
`_compute_cpr`, `_compute_orb_width`, `_compute_tod_tradeable`, `_compute_regime`
UNCHANGED.

```python
def _compute_ema(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {
        "ema9": _reset_on_gap(df, lambda d: ema(d["close"], int(p.get("ema_fast", 9)))),
        "ema21": _reset_on_gap(df, lambda d: ema(d["close"], int(p.get("ema_slow", 21)))),
    }


def _compute_ema50(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"ema50": _reset_on_gap(df, lambda d: ema(d["close"], 50))}


def _compute_rsi(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"rsi": _reset_on_gap(df, lambda d: rsi(d["close"], int(p.get("rsi_length", 14))))}


def _compute_macd(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    macd_line, signal_line, hist = _reset_on_gap(df, lambda d: macd(
        d["close"],
        int(p.get("macd_fast", 12)),
        int(p.get("macd_slow", 26)),
        int(p.get("macd_signal", 9)),
    ))
    return {"macd_line": macd_line, "macd_signal": signal_line, "macd_hist": hist}


def _compute_atr(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"atr": _reset_on_gap(df, lambda d: atr(d, int(p.get("atr_length", 14))))}


def _compute_adx(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"adx": _reset_on_gap(df, lambda d: adx(d, int(p.get("adx_length", 14))))}


def _compute_chop(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"chop": _reset_on_gap(df, lambda d: choppiness_index(d, int(p.get("chop_length", 14))))}


def _compute_atr_avg(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"atr_avg": _reset_on_gap(df, lambda d: d["atr"].rolling(100, min_periods=20).mean())}


def _compute_fvg(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return {"fvg": _reset_on_gap(df, lambda d: detect_fvg(d))}


def _compute_swing(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    def _cols(d):
        o = detect_swing_points(d, lookback=int(p.get("swing_lookback", 5)))
        return {"is_swing_high": o["is_swing_high"], "is_swing_low": o["is_swing_low"]}
    return _reset_on_gap(df, _cols)


def _compute_velocity(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    vel_z, accel_z = _reset_on_gap(df, lambda d: velocity_accel(
        d["close"], int(p.get("vel_n", 2)), int(p.get("vel_z_window", 60))))
    return {"vel_z": vel_z, "accel_z": accel_z}


def _compute_variance_ratio(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    vr, regime_score = _reset_on_gap(df, lambda d: variance_ratio(
        d["close"], int(p.get("vr_q", 4)), int(p.get("vr_lookback", 90)), float(p.get("vr_scale", 0.5))))
    return {"vr": vr, "regime_score": regime_score}


def _compute_squeeze(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    on, fire, mom = _reset_on_gap(df, lambda d: squeeze(
        d, int(p.get("bb_len", 20)), float(p.get("bb_mult", 2.0)),
        int(p.get("kc_len", 20)), float(p.get("kc_atr_mult", 1.5)), int(p.get("sqz_mom_len", 20))))
    return {"squeeze_on": on, "squeeze_fire": fire, "sqz_mom": mom}


def _compute_supertrend(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    st, st_dir = _reset_on_gap(df, lambda d: supertrend(
        d, int(p.get("st_period", 10)), float(p.get("st_mult", 3.0))))
    return {"supertrend": st, "st_dir": st_dir}


def _compute_geometry(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    return _reset_on_gap(df, lambda d: candle_geometry(d))
```

- [ ] **Step 5: Run the new tests**

Run: `python -m pytest tests/test_gap_reset.py -q`
Expected: PASS (all tests, including reset-across-gap and precompute-vs-groups-on-gap).

- [ ] **Step 6: Run the byte-parity guardrail + adjacent indicator suites**

Run: `python -m pytest tests/test_indicator_equivalence.py tests/test_indicators.py tests/test_adaptive_indicators.py tests/test_precompute_toolkit.py -q`
Expected: PASS. `test_indicator_equivalence.py` stays `7 passed` (fast-path makes
the gap-free fixture bit-identical; both paths carry `gap_before`).

- [ ] **Step 7: Commit**

```bash
git add backend/app/indicators.py backend/app/indicator_groups.py tests/test_gap_reset.py
git commit -m "feat(indicators): reset indicator warm-up across intra-session gaps"
```

---

## Task 4: Update tracking docs

**Files:**
- Modify: `docs/OPTIMIZER_VERDICT_2026-07.md` (edge case #2 row)
- Modify: `CHANGELOG.md`
- Modify: `docs/HANDOFF.md`

- [ ] **Step 1: Flip edge case #2 to FIXED**

In `docs/OPTIMIZER_VERDICT_2026-07.md`, edit the row `| 2 | **Warehouse mid-window
gap → indicators** …` — change the Verdict/Action cells from `SILENT-BUG` /
`**DEFERRED** …` to:

```
| SILENT-BUG | **FIXED** 2026-07-05 — intra-session `gap_before` flag (>1 min within a session; overnight boundaries excluded) + `_reset_on_gap` wrapper re-warms the whole-frame indicators per gap-bounded segment. No-gap fast-path keeps gap-free windows byte-identical (equivalence + parity tests). Spec/plan in `docs/superpowers/`. |
```

- [ ] **Step 2: Add a CHANGELOG entry**

Read the current top version in `CHANGELOG.md` and add a new entry above it
following the existing format, e.g.:

```markdown
## [Unreleased] — intra-session gap indicator reset
### Fixed
- Backtest/paper/live indicators no longer smear rolling/EWM state across an
  intra-session warehouse gap. New per-bar `gap_before` flag (>1 min jump within
  a session) drives a per-segment warm-up reset of the whole-frame indicators
  (ATR/EMA/RSI/ADX/MACD/…); overnight boundaries are unaffected. Gap-free windows
  are byte-identical (a no-gap fast-path preserves the exact prior computation).
  Ref: `docs/OPTIMIZER_VERDICT_2026-07.md` edge case #2.
```

- [ ] **Step 3: Note it in HANDOFF**

Add a one-line entry to the current-state section of `docs/HANDOFF.md` pointing at
the spec/plan and noting the enrichment layer now re-warms across intra-session
gaps.

- [ ] **Step 4: Commit**

```bash
git add docs/OPTIMIZER_VERDICT_2026-07.md CHANGELOG.md docs/HANDOFF.md
git commit -m "docs: mark optimizer-verdict edge case #2 (intra-session gap) FIXED"
```

---

## Task 5: Final verification sweep

- [ ] **Step 1: Run the full gap + indicator + backtest-relevant suites**

Run:
```bash
python -m pytest tests/test_gap_reset.py tests/test_indicator_equivalence.py \
  tests/test_indicators.py tests/test_adaptive_indicators.py \
  tests/test_precompute_toolkit.py tests/test_merged_params_indicator_flow.py \
  tests/test_optimizer_indicator_keys.py tests/test_session_precompute_parity.py \
  tests/test_strategy_gap.py -q
```
Expected: all PASS. (`test_strategy_gap.py` is an existing unrelated strategy test —
run it to confirm no accidental collision with the new gap concept.)

- [ ] **Step 2: Sanity-check a real backtest path import**

Run: `python -c "import sys; sys.path.insert(0,'backend'); from app.runtime import precompute_all_indicators; from app.indicator_groups import run_all_groups; print('imports ok')"`
Expected: `imports ok` (no import-time errors from the edits).

- [ ] **Step 3: Confirm working tree is clean and review the diff**

Run: `git status --short && git log --oneline -5`
Expected: clean tree; the four feature/doc commits present.

- [ ] **Step 4 (verification-before-completion):** Only after Steps 1–3 show real
passing output, report completion with the actual test counts. If anything fails,
stop and debug — do not claim success.

---

## Self-review notes

- **Spec coverage:** detection (§Design.1) → Task 1/2; `_reset_on_gap` fast-path
  (§Design.2) → Task 1; wrap-list (§Design.3) → Task 3; groups mirror (§Design.4)
  → Task 2/3; no `run_backtest` change (§Design.5) → honored (no backtest edits);
  tests (§Testing 1–4) → Task 1/3 (parity, reset, equivalence, column guard).
- **Column-set guard (§Testing 4):** confirmed no test pins the exact column set —
  `test_expected_columns_present` is a subset (`in`) check; `assert_frame_equal`
  in the equivalence test is symmetric across both patched paths.
- **Type consistency:** `gap_before_mask` / `_reset_on_gap` signatures are used
  identically in every task; `_reset_on_gap` returns match each helper's shape
  (Series / tuple / dict) as wrapped.
