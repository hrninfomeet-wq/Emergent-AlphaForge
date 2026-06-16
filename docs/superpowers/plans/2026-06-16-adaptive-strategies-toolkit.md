# Adaptive Strategies — Plan 1: Shared Toolkit Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared "measured-edge" indicator toolkit + `AdaptiveStrategyBase` scaffolding + optimizer recompute-wiring that the 5 new strategies depend on — pure, causal, host-testable, and additive (zero behavior change for the existing 7 strategies).

**Architecture:** New pure functions in `app/indicators.py` (velocity/accel, variance-ratio, squeeze, supertrend, VWAP σ-bands, NR7) wired into `precompute_all_indicators`, plus two new modules `app/cpr.py` (daily Central-Pivot-Range + pivots + day-type) and `app/vol_seasonality.py` (trailing intraday time-gate), plus `app/strategies/adaptive_base.py` (the shared time-gate/Speed-confirm/ATR-exit scaffolding). New indicator-period params are registered in `optimizer.py`'s `INDICATOR_PARAM_KEYS` so tuning them actually recomputes the enriched frame.

**Tech Stack:** Python 3, pandas, numpy. Tests via `pytest` (host-safe — NO `motor`/`optuna`/`server.py` imports). Source-of-truth contract: `app/strategies/base.py` (`StrategyBase`/`Signal`), `app/indicators.py` (`precompute_all_indicators`).

---

> **Plan 1 of 4** (per the spec's phasing). Plan 1 = this toolkit foundation (independently testable). Plan 2 = the 3 core strategies (SEB/ARS/ORF). Plan 3 = Phase B (GAP + XRS engine touch). Plan 4 = Phase C (self-improving layers). Spec: [docs/superpowers/specs/2026-06-16-adaptive-options-strategies-design.md](../specs/2026-06-16-adaptive-options-strategies-design.md).
>
> **Causality is the #1 correctness rule** (spec §12): every function uses trailing windows / prior sessions only — no centered windows, no future peeking. Each task includes a look-ahead regression where applicable: a value computed at bar `i` on the full series must equal the value computed on the series truncated at `i`.
>
> **Conventions:** branch `feat/adaptive-strategies` (stacked on `feat/exit-risk-controls`). Commit after every green task. `core.autocrlf=true` → harmless CRLF warnings.

---

### Task 0: Branch + shared test util

**Files:**
- Create: `tests/_adaptive_testutil.py`

- [ ] **Step 1: Create the branch**

Run:
```bash
git checkout -b feat/adaptive-strategies
```
Expected: `Switched to a new branch 'feat/adaptive-strategies'`

- [ ] **Step 2: Write the shared OHLC test helper**

Create `tests/_adaptive_testutil.py`:
```python
"""Shared synthetic-OHLC builders for adaptive-toolkit tests (host-safe)."""
import numpy as np
import pandas as pd

IST = "Asia/Kolkata"


def make_ohlc(closes, *, start="2025-01-01 09:15", high_pad=0.5, low_pad=0.5, volume=0.0):
    """1m OHLC frame from a close path. ts is epoch-ms (UTC). One continuous run."""
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.date_range(start=start, periods=n, freq="1min", tz=IST)
    ts = (idx.asi8 // 1_000_000).astype("int64")  # UTC epoch-ms; asi8 avoids deprecated .view
    return pd.DataFrame({
        "ts": ts,
        "open": closes,
        "high": closes + high_pad,
        "low": closes - low_pad,
        "close": closes,
        "volume": np.full(n, float(volume)),
    })


def make_sessions(per_session_closes, *, start_date="2025-01-01"):
    """Stack multiple trading sessions (each a list of closes) into one frame
    with correct ist session_date boundaries. Returns the frame WITH a
    `session_date` column already set (skips the precompute step)."""
    frames = []
    day = pd.Timestamp(start_date)
    for closes in per_session_closes:
        f = make_ohlc(closes, start=f"{day.date()} 09:15")
        f["session_date"] = str(day.date())
        frames.append(f)
        day += pd.Timedelta(days=1)
    return pd.concat(frames, ignore_index=True)
```

- [ ] **Step 3: Commit**

```bash
git add tests/_adaptive_testutil.py
git commit -m "test(adaptive): shared synthetic-OHLC builders"
```

---

### Task 1: Velocity / Acceleration + Variance Ratio

**Files:**
- Modify: `backend/app/indicators.py` (add two functions after `choppiness_index`)
- Test: `tests/test_adaptive_indicators.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_adaptive_indicators.py`:
```python
import numpy as np
import pandas as pd
import pytest
from app.indicators import velocity_accel, variance_ratio
from tests._adaptive_testutil import make_ohlc


def test_velocity_sign_tracks_direction():
    df = make_ohlc(list(range(100, 160)))  # steady uptrend
    vel_z, accel_z = velocity_accel(df["close"], vel_n=2, z_window=20)
    assert vel_z.iloc[-1] is not None and not pd.isna(vel_z.iloc[-1])
    assert vel_z.iloc[-1] > 0  # rising price -> positive velocity z


def test_velocity_accel_is_causal():
    closes = list(np.cumsum(np.sin(np.arange(120) / 5.0)) + 100)
    df = make_ohlc(closes)
    full, _ = velocity_accel(df["close"], vel_n=2, z_window=30)
    cut, _ = velocity_accel(df["close"].iloc[:80], vel_n=2, z_window=30)
    assert full.iloc[79] == pytest.approx(cut.iloc[79], rel=1e-9, nan_ok=True)


def test_variance_ratio_trend_gt_1_revert_lt_1():
    trend = make_ohlc(list(range(100, 250)))                      # pure trend
    vr_t, score_t = variance_ratio(trend["close"], q=4, lookback=60)
    assert vr_t.iloc[-1] > 1.0 and score_t.iloc[-1] > 0
    osc = make_ohlc([100 + (3 if i % 2 else -3) for i in range(150)])  # zig-zag revert
    vr_r, score_r = variance_ratio(osc["close"], q=4, lookback=60)
    assert vr_r.iloc[-1] < 1.0 and score_r.iloc[-1] < 0


def test_variance_ratio_is_causal():
    closes = list(np.cumsum(np.random.default_rng(0).standard_normal(200)) + 100)
    df = make_ohlc(closes)
    full, _ = variance_ratio(df["close"], q=4, lookback=60)
    cut, _ = variance_ratio(df["close"].iloc[:150], q=4, lookback=60)
    assert full.iloc[149] == pytest.approx(cut.iloc[149], rel=1e-9, nan_ok=True)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_adaptive_indicators.py -q`
Expected: FAIL — `ImportError: cannot import name 'velocity_accel'`.

- [ ] **Step 3: Implement in `backend/app/indicators.py`**

Add after `choppiness_index` (around line 73):
```python
def velocity_accel(close: pd.Series, vel_n: int = 2, z_window: int = 60):
    """Z-scored velocity (n-bar return) and acceleration (its change). Causal:
    trailing rolling stats only. Returns (vel_z, accel_z)."""
    def _z(s: pd.Series) -> pd.Series:
        mu = s.rolling(z_window, min_periods=max(2, z_window // 2)).mean()
        sd = s.rolling(z_window, min_periods=max(2, z_window // 2)).std(ddof=0)
        out = (s - mu) / sd.replace(0, np.nan)
        return out.replace([np.inf, -np.inf], np.nan)
    vel = close.diff(vel_n)
    accel = vel.diff()
    return _z(vel), _z(accel)


def variance_ratio(close: pd.Series, q: int = 4, lookback: int = 90, scale: float = 0.5):
    """Lo-MacKinlay variance ratio over a trailing window: VR>1 trend, <1
    mean-revert, ~1 random walk. regime_score = clip((VR-1)/scale, -1, 1).
    Causal. Returns (vr, regime_score)."""
    logp = np.log(close.clip(lower=1e-9))
    r1 = logp.diff()
    rq = logp.diff(q)
    var1 = r1.rolling(lookback, min_periods=max(q + 2, lookback // 2)).var(ddof=1)
    varq = rq.rolling(lookback, min_periods=max(q + 2, lookback // 2)).var(ddof=1)
    vr = varq / (q * var1.replace(0, np.nan))
    vr = vr.replace([np.inf, -np.inf], np.nan)
    regime_score = ((vr - 1.0) / max(scale, 1e-6)).clip(-1.0, 1.0)
    return vr, regime_score
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_adaptive_indicators.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/indicators.py tests/test_adaptive_indicators.py
git commit -m "feat(indicators): velocity/acceleration + variance-ratio (causal)"
```

---

### Task 2: Bollinger + Keltner + Squeeze

**Files:**
- Modify: `backend/app/indicators.py`
- Test: `tests/test_adaptive_indicators.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_adaptive_indicators.py`)

```python
from app.indicators import bollinger, keltner, squeeze


def test_squeeze_on_during_compression_then_fires():
    flat = [100 + 0.05 * np.sin(i) for i in range(60)]   # tight range -> squeeze on
    burst = list(np.linspace(100, 130, 30))              # expansion -> fires
    df = make_ohlc(flat + burst, high_pad=0.2, low_pad=0.2)
    on, fire, mom = squeeze(df, bb_len=20, bb_mult=2.0, kc_len=20, kc_atr_mult=1.5, mom_len=20)
    assert on.iloc[40]            # compressed during the flat stretch
    assert fire.iloc[60:75].any() # fires at/after the expansion onset
    assert mom.iloc[75] > 0       # up-expansion -> positive momentum


def test_squeeze_fire_is_single_bar_edge():
    df = make_ohlc([100 + 0.05 * np.sin(i) for i in range(40)] + list(range(100, 140)))
    on, fire, _ = squeeze(df)
    # fire only where prior bar was on and this bar is off
    expected = on.shift(1).fillna(False) & (~on)
    assert (fire == expected).all()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_adaptive_indicators.py -q`
Expected: FAIL — `cannot import name 'bollinger'`.

- [ ] **Step 3: Implement in `backend/app/indicators.py`**

```python
def bollinger(close: pd.Series, length: int = 20, mult: float = 2.0):
    mid = close.rolling(length).mean()
    sd = close.rolling(length).std(ddof=0)
    return mid + mult * sd, mid - mult * sd, mid


def keltner(df: pd.DataFrame, length: int = 20, atr_mult: float = 1.5):
    mid = ema(df["close"], length)
    a = atr(df, length)
    return mid + atr_mult * a, mid - atr_mult * a


def squeeze(df: pd.DataFrame, bb_len: int = 20, bb_mult: float = 2.0,
            kc_len: int = 20, kc_atr_mult: float = 1.5, mom_len: int = 20):
    """TTM-style squeeze. on = Bollinger inside Keltner (compression);
    fire = released this bar (single-bar edge); mom = close minus the
    Donchian-mid/SMA midline (sign+slope of expansion). Causal."""
    bb_u, bb_l, _ = bollinger(df["close"], bb_len, bb_mult)
    kc_u, kc_l = keltner(df, kc_len, kc_atr_mult)
    on = (bb_l > kc_l) & (bb_u < kc_u)
    on = on.fillna(False)
    fire = on.shift(1).fillna(False) & (~on)
    hh = df["high"].rolling(mom_len).max()
    ll = df["low"].rolling(mom_len).min()
    midline = (((hh + ll) / 2) + df["close"].rolling(mom_len).mean()) / 2
    mom = df["close"] - midline
    return on, fire, mom
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_adaptive_indicators.py -q`
Expected: all passed (6 total in file).

- [ ] **Step 5: Commit**

```bash
git add backend/app/indicators.py tests/test_adaptive_indicators.py
git commit -m "feat(indicators): bollinger/keltner/squeeze (compression->expansion)"
```

---

### Task 3: Supertrend

**Files:**
- Modify: `backend/app/indicators.py`
- Test: `tests/test_adaptive_indicators.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
from app.indicators import supertrend


def test_supertrend_dir_flips_with_trend():
    df = make_ohlc(list(range(100, 160)) + list(range(160, 100, -1)))
    st, d = supertrend(df, period=10, mult=3.0)
    assert d.iloc[40] == 1     # uptrend -> long
    assert d.iloc[-1] == -1    # downtrend -> short


def test_supertrend_is_causal():
    closes = list(np.cumsum(np.random.default_rng(1).standard_normal(150)) + 100)
    df = make_ohlc(closes)
    st_full, d_full = supertrend(df, period=10, mult=3.0)
    st_cut, d_cut = supertrend(df.iloc[:120], period=10, mult=3.0)
    assert int(d_full.iloc[119]) == int(d_cut.iloc[119])
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_adaptive_indicators.py -k supertrend -q`
Expected: FAIL — `cannot import name 'supertrend'`.

- [ ] **Step 3: Implement in `backend/app/indicators.py`**

```python
def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0):
    """ATR-banded trailing trend with a path-dependent flip. Causal (bar i uses
    only finalized bands through i-1). Returns (supertrend_line, st_dir ±1)."""
    hl2 = (df["high"] + df["low"]) / 2.0
    a = atr(df, period)
    upper = (hl2 + mult * a).to_numpy()
    lower = (hl2 - mult * a).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    f_up = upper.copy()
    f_lo = lower.copy()
    st = np.full(n, np.nan)
    d = np.ones(n, dtype=int)
    for i in range(1, n):
        f_up[i] = upper[i] if (np.isnan(f_up[i - 1]) or upper[i] < f_up[i - 1] or close[i - 1] > f_up[i - 1]) else f_up[i - 1]
        f_lo[i] = lower[i] if (np.isnan(f_lo[i - 1]) or lower[i] > f_lo[i - 1] or close[i - 1] < f_lo[i - 1]) else f_lo[i - 1]
        if close[i] > f_up[i - 1]:
            d[i] = 1
        elif close[i] < f_lo[i - 1]:
            d[i] = -1
        else:
            d[i] = d[i - 1]
        st[i] = f_lo[i] if d[i] == 1 else f_up[i]
    return pd.Series(st, index=df.index), pd.Series(d, index=df.index)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_adaptive_indicators.py -k supertrend -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/indicators.py tests/test_adaptive_indicators.py
git commit -m "feat(indicators): supertrend (ATR-trail flip, causal)"
```

---

### Task 4: VWAP σ-bands + NR7

**Files:**
- Modify: `backend/app/indicators.py`
- Test: `tests/test_adaptive_indicators.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
from app.indicators import vwap_sigma_bands, nr7
from tests._adaptive_testutil import make_sessions
from app.indicators import session_vwap


def test_vwap_sigma_bands_widen_with_dispersion():
    df = make_sessions([[100, 101, 99, 103, 97, 105, 95]])  # one volatile session
    df["vwap"] = session_vwap(df)  # price-based (no volume) fallback
    sigma, u1, u2, l1, l2 = vwap_sigma_bands(df)
    assert (u2 >= u1).all() and (l2 <= l1).all()
    assert sigma.iloc[-1] > 0


def test_nr7_flags_session_after_narrow_day():
    wide = list(range(100, 130))                      # big range
    narrow = [110 + 0.1 * (i % 2) for i in range(30)] # tiny range (NR)
    after = list(range(110, 140))
    df = make_sessions([wide, wide, wide, wide, wide, wide, narrow, after])
    flag = nr7(df)
    last_date = df["session_date"].iloc[-1]
    assert flag[df["session_date"] == last_date].iloc[0]  # day after the NR is flagged


def test_nr7_does_not_use_today_full_range():
    # today's flag must equal the prior session's NR status, independent of how
    # today's later bars extend the range -> causal.
    df = make_sessions([[100]*30, [100]*30, [100]*30, [100]*30, [100]*30,
                        [100]*30, [100 + 0.1*(i%2) for i in range(30)], list(range(100, 200))])
    full = nr7(df)
    cut = nr7(df.iloc[: len(df) - 50])  # truncate today's tail
    last_date = df["session_date"].iloc[-1]
    a = full[df["session_date"] == last_date].iloc[0]
    b = cut[cut.index][df["session_date"].iloc[: len(cut)] == last_date]
    assert bool(a) == bool(b.iloc[0])
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_adaptive_indicators.py -k "vwap_sigma or nr7" -q`
Expected: FAIL — `cannot import name 'vwap_sigma_bands'`.

- [ ] **Step 3: Implement in `backend/app/indicators.py`**

```python
def vwap_sigma_bands(df: pd.DataFrame):
    """Price-based standard-deviation bands around session VWAP (matches
    session_vwap's volume-zero fallback). Requires `vwap` + `session_date`
    columns. Per-session expanding sigma (causal). Returns
    (sigma, u1, u2, l1, l2)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    dev2 = (typical - df["vwap"]) ** 2
    sigma = pd.Series(index=df.index, dtype="float64")
    for _, g in df.groupby("session_date", sort=False):
        sigma.loc[g.index] = np.sqrt(dev2.loc[g.index].expanding().mean())
    return sigma, df["vwap"] + sigma, df["vwap"] + 2 * sigma, df["vwap"] - sigma, df["vwap"] - 2 * sigma


def nr7(df: pd.DataFrame) -> pd.Series:
    """Per-bar flag: the PRIOR completed session's range was the narrowest of
    its preceding 7 sessions (Crabel NR7 -> today may expand). Causal: today's
    own (incomplete) range is never used. Requires `session_date`."""
    g = df.groupby("session_date", sort=False)
    rng = (g["high"].max() - g["low"].min()).sort_index()
    is_nr7 = rng <= rng.rolling(7, min_periods=2).min()
    prior = is_nr7.shift(1).fillna(False)
    return df["session_date"].map(prior).fillna(False).astype(bool)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_adaptive_indicators.py -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/indicators.py tests/test_adaptive_indicators.py
git commit -m "feat(indicators): price-based VWAP sigma-bands + NR7 (causal)"
```

---

### Task 5: CPR daily levels (`app/cpr.py`)

**Files:**
- Create: `backend/app/cpr.py`
- Test: `tests/test_cpr.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cpr.py`:
```python
import numpy as np
import pandas as pd
import pytest
from app.cpr import cpr_levels
from tests._adaptive_testutil import make_sessions


def test_cpr_formula_and_tc_ge_bc():
    # session 1 H/L/C known -> session 2 CPR derived from it
    df = make_sessions([[100, 110, 90, 105], [106, 107, 104, 106]])
    out = cpr_levels(df)
    s2 = df["session_date"].iloc[-1]
    row = out[df["session_date"] == s2].iloc[0]
    H, L, C = 110.5, 89.5, 105.0   # high_pad/low_pad 0.5 from make_ohlc
    P = (H + L + C) / 3
    assert row["cpr_p"] == pytest.approx(P, rel=1e-6)
    assert row["cpr_tc"] >= row["cpr_bc"]
    assert row["cpr_width_pct"] == pytest.approx((row["cpr_tc"] - row["cpr_bc"]) / P * 100, rel=1e-6)


def test_cpr_day_type_narrow_is_trend():
    # 8 wide sessions then a very narrow CPR -> next session tagged TREND
    wide = [[100, 130, 70, 100]] * 8
    narrow_src = [[100, 100.5, 99.5, 100]]   # tiny prior-day range -> narrow CPR
    after = [[100, 101, 99, 100]]
    df = make_sessions(wide + narrow_src + after)
    out = cpr_levels(df, narrow_pctile=40, wide_pctile=60, pctile_window=10)
    last = df["session_date"].iloc[-1]
    assert out[df["session_date"] == last]["day_type"].iloc[0] == "TREND"


def test_cpr_first_session_has_no_levels():
    df = make_sessions([[100, 110, 90, 105]])
    out = cpr_levels(df)
    assert out["cpr_p"].isna().all()  # no prior day -> NaN, never look-ahead
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_cpr.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.cpr'`.

- [ ] **Step 3: Implement `backend/app/cpr.py`**

```python
"""Central Pivot Range + floor pivots + day-type, computed once per session
from the PRIOR completed session's OHLC. Width day-type uses a rolling
percentile (scale-free -> portable NIFTY<->SENSEX). Pure + causal."""
from __future__ import annotations
import numpy as np
import pandas as pd

_LEVEL_COLS = ["cpr_p", "cpr_tc", "cpr_bc", "cpr_width_pct", "day_type", "R1", "S1", "R2", "S2"]


def cpr_levels(df: pd.DataFrame, narrow_pctile: float = 30.0, wide_pctile: float = 70.0,
               pctile_window: int = 20) -> pd.DataFrame:
    """Attach CPR + pivots + day_type per bar (keyed by `session_date`).
    Requires a `session_date` column. Returns a frame with `_LEVEL_COLS`
    aligned to df's index."""
    g = df.groupby("session_date", sort=False)
    sess = pd.DataFrame({
        "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
    }).sort_index()
    ph, pl, pc = sess["high"].shift(1), sess["low"].shift(1), sess["close"].shift(1)
    P = (ph + pl + pc) / 3.0
    BC = (ph + pl) / 2.0
    TC = 2.0 * P - BC
    tc = pd.concat([TC, BC], axis=1).max(axis=1)
    bc = pd.concat([TC, BC], axis=1).min(axis=1)
    width = (tc - bc) / P * 100.0
    lo = width.rolling(pctile_window, min_periods=3).quantile(narrow_pctile / 100.0)
    hi = width.rolling(pctile_window, min_periods=3).quantile(wide_pctile / 100.0)
    day_type = pd.Series("NEUTRAL", index=width.index)
    day_type[width <= lo] = "TREND"
    day_type[width >= hi] = "RANGE"
    day_type[width.isna()] = "NEUTRAL"
    sess_levels = pd.DataFrame({
        "cpr_p": P, "cpr_tc": tc, "cpr_bc": bc, "cpr_width_pct": width, "day_type": day_type,
        "R1": 2 * P - pl, "S1": 2 * P - ph, "R2": P + (ph - pl), "S2": P - (ph - pl),
    })
    joined = df[["session_date"]].join(sess_levels, on="session_date")
    return joined[_LEVEL_COLS]
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_cpr.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/cpr.py tests/test_cpr.py
git commit -m "feat(cpr): central pivot range + day-type (rolling-percentile width)"
```

---

### Task 6: Intraday vol-seasonality time-gate (`app/vol_seasonality.py`)

**Files:**
- Create: `backend/app/vol_seasonality.py`
- Test: `tests/test_vol_seasonality.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vol_seasonality.py`:
```python
import numpy as np
import pandas as pd
import pytest
from app.vol_seasonality import tod_bucket, attach_tod_tradeable


def test_tod_bucket_5min():
    assert tod_bucket("09:15", 5) == tod_bucket("09:19", 5)
    assert tod_bucket("09:20", 5) != tod_bucket("09:15", 5)


def test_dead_bucket_blocked_live_bucket_open():
    # Build 10 sessions: one bucket always wide, one always flat.
    rows = []
    for d in range(10):
        for b in range(6):
            wide = b == 0
            rng = 10.0 if wide else 0.1
            rows.append({"session_date": f"2025-01-0{d+1}", "ist_time": f"09:{15+b*5:02d}",
                         "high": 100 + rng, "low": 100 - rng, "atr": 5.0})
    df = pd.DataFrame(rows)
    out = attach_tod_tradeable(df, lookback_sessions=5, min_atr_frac=0.6)
    df = df.assign(tradeable=out)
    last = df[df["session_date"] == "2025-01-10"]
    assert last[last["ist_time"] == "09:15"]["tradeable"].iloc[0]      # wide bucket -> tradeable
    assert not last[last["ist_time"] == "09:20"]["tradeable"].iloc[0]  # flat bucket -> blocked


def test_cold_start_defaults_tradeable():
    df = pd.DataFrame([{"session_date": "2025-01-01", "ist_time": "09:15",
                        "high": 100.1, "low": 99.9, "atr": 5.0}])
    out = attach_tod_tradeable(df, lookback_sessions=5)
    assert bool(out[0]) is True  # no history -> do not block
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_vol_seasonality.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.vol_seasonality'`.

- [ ] **Step 3: Implement `backend/app/vol_seasonality.py`**

```python
"""Trailing intraday vol-seasonality time-gate. For each 5-min IST bucket,
estimate mean ATR-normalized range over the PRIOR `lookback_sessions` sessions;
gate entries to buckets whose conditional range clears the theta hurdle. Causal
(shift(1) -> prior sessions only). Phase-A trailing form; the WFO train-estimate
is Phase C."""
from __future__ import annotations
import numpy as np
import pandas as pd


def tod_bucket(ist_time: str, minutes: int = 5) -> int:
    h, m = int(ist_time[:2]), int(ist_time[3:5])
    return (h * 60 + m) // minutes


def attach_tod_tradeable(df: pd.DataFrame, lookback_sessions: int = 20,
                         min_atr_frac: float = 0.6, bucket_min: int = 5) -> np.ndarray:
    """Return a boolean array aligned to df: True where the bar's time-bucket has
    historically (prior sessions) cleared the range hurdle. Requires
    `session_date`, `ist_time`, `atr`. Cold-start (no history) -> True."""
    d = df.copy()
    d["_bucket"] = d["ist_time"].map(lambda t: tod_bucket(t, bucket_min))
    d["_rng_atr"] = (d["high"] - d["low"]) / d["atr"].replace(0, np.nan)
    per = (d.groupby(["session_date", "_bucket"])["_rng_atr"].mean()
             .reset_index().sort_values("session_date"))
    per["edge"] = (per.groupby("_bucket")["_rng_atr"]
                      .transform(lambda s: s.shift(1).rolling(lookback_sessions, min_periods=2).mean()))
    per["tradeable"] = (per["edge"] >= min_atr_frac) | per["edge"].isna()  # cold start -> True
    key = per.set_index(["session_date", "_bucket"])["tradeable"]
    pairs = list(zip(d["session_date"], d["_bucket"]))
    return np.array([bool(key.get(p, True)) for p in pairs], dtype=bool)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_vol_seasonality.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/vol_seasonality.py tests/test_vol_seasonality.py
git commit -m "feat(vol-seasonality): trailing intraday time-gate (causal)"
```

---

### Task 7: Wire toolkit columns into `precompute_all_indicators`

**Files:**
- Modify: `backend/app/indicators.py` (`precompute_all_indicators`, after line ~164)
- Test: `tests/test_precompute_toolkit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_precompute_toolkit.py`:
```python
import numpy as np
import pandas as pd
from app.indicators import precompute_all_indicators
from tests._adaptive_testutil import make_ohlc

NEW_COLS = ["vel_z", "accel_z", "vr", "regime_score", "squeeze_on", "squeeze_fire",
            "sqz_mom", "supertrend", "st_dir", "vwap_sigma", "vwap_u1", "vwap_u2",
            "vwap_l1", "vwap_l2", "nr7", "cpr_p", "cpr_tc", "cpr_bc", "cpr_width_pct",
            "day_type", "tod_tradeable"]


def test_precompute_adds_all_toolkit_columns():
    df = make_ohlc(list(np.cumsum(np.random.default_rng(2).standard_normal(400)) + 100))
    out = precompute_all_indicators(df)
    for c in NEW_COLS:
        assert c in out.columns, f"missing {c}"
    # existing columns still present (no regression)
    for c in ["ema9", "rsi", "vwap", "atr", "fvg", "is_swing_high"]:
        assert c in out.columns


def test_precompute_period_params_change_columns():
    df = make_ohlc(list(np.cumsum(np.random.default_rng(3).standard_normal(400)) + 100))
    a = precompute_all_indicators(df, {"vr_q": 4})
    b = precompute_all_indicators(df, {"vr_q": 12})
    assert not a["vr"].equals(b["vr"])  # vr_q actually flows through
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_precompute_toolkit.py -q`
Expected: FAIL — `assert 'vel_z' in out.columns`.

- [ ] **Step 3: Implement — append to `precompute_all_indicators` before `return df`**

In `backend/app/indicators.py`, add at the top (with the other imports):
```python
from app.cpr import cpr_levels
from app.vol_seasonality import attach_tod_tradeable
```
Then insert before `return df` (after the `detect_swing_points` line):
```python
    # --- adaptive toolkit columns (Plan 1) ---
    df["vel_z"], df["accel_z"] = velocity_accel(
        df["close"], int(p.get("vel_n", 2)), int(p.get("vel_z_window", 60)))
    df["vr"], df["regime_score"] = variance_ratio(
        df["close"], int(p.get("vr_q", 4)), int(p.get("vr_lookback", 90)), float(p.get("vr_scale", 0.5)))
    on, fire, mom = squeeze(
        df, int(p.get("bb_len", 20)), float(p.get("bb_mult", 2.0)),
        int(p.get("kc_len", 20)), float(p.get("kc_atr_mult", 1.5)), int(p.get("sqz_mom_len", 20)))
    df["squeeze_on"], df["squeeze_fire"], df["sqz_mom"] = on, fire, mom
    df["supertrend"], df["st_dir"] = supertrend(
        df, int(p.get("st_period", 10)), float(p.get("st_mult", 3.0)))
    sigma, u1, u2, l1, l2 = vwap_sigma_bands(df)
    df["vwap_sigma"], df["vwap_u1"], df["vwap_u2"], df["vwap_l1"], df["vwap_l2"] = sigma, u1, u2, l1, l2
    df["nr7"] = nr7(df)
    cpr = cpr_levels(
        df, float(p.get("cpr_narrow_pctile", 30.0)), float(p.get("cpr_wide_pctile", 70.0)),
        int(p.get("cpr_pctile_window", 20)))
    for c in cpr.columns:
        df[c] = cpr[c]
    df["tod_tradeable"] = attach_tod_tradeable(
        df, int(p.get("tod_lookback_sessions", 20)), float(p.get("tod_min_atr_frac", 0.6)))
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_precompute_toolkit.py -q`
Expected: 2 passed.

- [ ] **Step 5: Run the full existing suite (no regression)**

Run: `python -m pytest tests -q`
Expected: all green (toolkit columns are additive; existing strategies ignore them).

- [ ] **Step 6: Commit**

```bash
git add backend/app/indicators.py tests/test_precompute_toolkit.py
git commit -m "feat(indicators): wire adaptive toolkit columns into precompute"
```

---

### Task 8: Register new period params in the optimizer's recompute key

**Files:**
- Modify: `backend/app/optimizer.py:52-56` (`INDICATOR_PARAM_KEYS`)
- Test: `tests/test_optimizer_indicator_keys.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_optimizer_indicator_keys.py`. NOTE: `optimizer.py` imports
`optuna`, which is **absent on the host** (it's `py_compile`-only — see HANDOFF),
so we must NOT `import app.optimizer` in a host test. Assert on the source text
instead — the same contract-corpus pattern the repo already uses for
import-unsafe modules. The *behavioral* proof that these params recompute the
frame is Task 7's `test_precompute_period_params_change_columns` (import-safe,
no optuna).
```python
import pathlib

SRC = pathlib.Path("backend/app/optimizer.py").read_text(encoding="utf-8")
NEW = ["vel_n", "vel_z_window", "vr_q", "vr_lookback", "vr_scale", "bb_len", "bb_mult",
       "kc_len", "kc_atr_mult", "sqz_mom_len", "st_period", "st_mult",
       "cpr_narrow_pctile", "cpr_wide_pctile", "cpr_pctile_window",
       "tod_lookback_sessions", "tod_min_atr_frac"]


def _keys_tuple_text() -> str:
    i = SRC.index("INDICATOR_PARAM_KEYS")
    return SRC[i:SRC.index(")", i)]  # text of the tuple literal


def test_new_period_params_registered_in_keys_tuple():
    block = _keys_tuple_text()
    for k in NEW:
        assert f'"{k}"' in block, f"{k} not registered in INDICATOR_PARAM_KEYS"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_optimizer_indicator_keys.py -q`
Expected: FAIL — AssertionError `vel_n not registered in INDICATOR_PARAM_KEYS`.

- [ ] **Step 3: Implement — extend `INDICATOR_PARAM_KEYS` in `backend/app/optimizer.py`**

Replace the tuple at lines 52-56 with:
```python
INDICATOR_PARAM_KEYS = (
    "ema_fast", "ema_slow", "rsi_length",
    "macd_fast", "macd_slow", "macd_signal",
    "atr_length", "adx_length", "chop_length", "swing_lookback",
    # adaptive toolkit (Plan 1) — every param precompute_all_indicators reads
    "vel_n", "vel_z_window", "vr_q", "vr_lookback", "vr_scale",
    "bb_len", "bb_mult", "kc_len", "kc_atr_mult", "sqz_mom_len",
    "st_period", "st_mult",
    "cpr_narrow_pctile", "cpr_wide_pctile", "cpr_pctile_window",
    "tod_lookback_sessions", "tod_min_atr_frac",
)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_optimizer_indicator_keys.py -q`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/optimizer.py tests/test_optimizer_indicator_keys.py
git commit -m "feat(optimizer): register adaptive toolkit periods for frame recompute"
```

---

### Task 9: `AdaptiveStrategyBase` scaffolding

**Files:**
- Create: `backend/app/strategies/adaptive_base.py`
- Test: `tests/test_adaptive_base.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_adaptive_base.py`:
```python
import pandas as pd
import pytest
from app.strategies.adaptive_base import AdaptiveStrategyBase, BASE_PARAMS


class _Mom(AdaptiveStrategyBase):
    id = "_test_mom"
    name = "t"
    extra_params = {"foo": {"type": "int", "min": 1, "max": 9, "default": 3}}

    def _core_signal(self, row, prev, params, ctx):
        return ("CE", 70, ["x"], [], "momentum")


def _row(**kw):
    base = {"atr": 10.0, "accel_z": 1.5, "ist_time": "10:00", "tod_tradeable": True}
    base.update(kw)
    return pd.Series(base)


def test_merges_base_and_extra_params():
    s = _Mom()
    assert "k_acc" in s.parameter_schema and "foo" in s.parameter_schema


def test_momentum_speed_gate_blocks_weak_accel():
    s = _Mom()
    p = s.default_params()
    ok = s.evaluate(_row(accel_z=1.5), None, p, {})
    assert ok.direction == "CE" and ok.spot_target_pts == pytest.approx(p["t_atr"] * 10.0, rel=1e-6)
    blocked = s.evaluate(_row(accel_z=0.1), None, p, {})
    assert blocked.direction == "NONE" and "speed gate" in blocked.blockers


def test_time_gate_blocks_after_cutoff_and_dead_bucket():
    s = _Mom()
    p = s.default_params()
    assert s.evaluate(_row(ist_time="14:30"), None, p, {}).direction == "NONE"
    assert s.evaluate(_row(tod_tradeable=False), None, p, {}).direction == "NONE"


def test_reversion_speed_gate_allows_turning_accel():
    class _Rev(_Mom):
        id = "_test_rev"
        def _core_signal(self, row, prev, params, ctx):
            return ("CE", 70, [], [], "reversion")
    s = _Rev()
    p = s.default_params()
    # reversion CE allowed when accel not strongly negative (>= -k_acc_fade)
    assert s.evaluate(_row(accel_z=-0.1), None, p, {}).direction == "CE"
    assert s.evaluate(_row(accel_z=-2.0), None, p, {}).direction == "NONE"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_adaptive_base.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.strategies.adaptive_base'`.

- [ ] **Step 3: Implement `backend/app/strategies/adaptive_base.py`**

```python
"""Shared scaffolding for the adaptive strategy slate: time-gate + mode-aware
Speed confirm + ATR-relative exits. Concrete strategies override `_core_signal`
and set `extra_params`. Trusted core infra (versioned with the app), like
indicators.py / context_signals.py."""
from __future__ import annotations
from typing import Any, Dict, List, Tuple
import pandas as pd
from app.strategies.base import StrategyBase, Signal

BASE_PARAMS: Dict[str, Any] = {
    "k_acc": {"type": "float", "min": 0.0, "max": 2.0, "default": 0.5},
    "k_acc_fade": {"type": "float", "min": 0.0, "max": 2.0, "default": 0.5},
    "t_atr": {"type": "float", "min": 0.5, "max": 6.0, "default": 1.5},
    "s_atr": {"type": "float", "min": 0.3, "max": 3.0, "default": 0.8},
    "time_stop_min": {"type": "int", "min": 2, "max": 60, "default": 12},
    "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 55},
    "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 5},
    "entry_cutoff_hhmm": {"type": "str", "default": "14:00"},
    "use_time_gate": {"type": "bool", "default": True},
}


class AdaptiveStrategyBase(StrategyBase):
    supported_instruments = ["NIFTY", "SENSEX"]
    supported_modes = ["SCALP", "INTRADAY"]
    supported_timeframes = ["1m", "3m", "5m"]
    extra_params: Dict[str, Any] = {}

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        cls.parameter_schema = {**BASE_PARAMS, **getattr(cls, "extra_params", {})}

    def _core_signal(self, row, prev, params, ctx) -> Tuple[str, int, List[str], List[str], str]:
        """Return (direction, score, reasons, blockers, mode∈{momentum,reversion})."""
        raise NotImplementedError

    def _time_ok(self, row: pd.Series, params: Dict[str, Any]) -> bool:
        if not params.get("use_time_gate", True):
            return True
        t = str(row.get("ist_time") or "")
        if t and t >= str(params.get("entry_cutoff_hhmm", "14:00")):
            return False
        tg = row.get("tod_tradeable")
        return True if tg is None else bool(tg)

    def _speed_ok(self, direction: str, mode: str, row: pd.Series, params: Dict[str, Any]) -> bool:
        az = row.get("accel_z")
        if az is None or pd.isna(az):
            return False
        az = float(az)
        if mode == "momentum":
            k = float(params.get("k_acc", 0.5))
            return az >= k if direction == "CE" else az <= -k
        kf = float(params.get("k_acc_fade", 0.5))
        return az >= -kf if direction == "CE" else az <= kf

    def evaluate(self, row, prev, params, ctx) -> Signal:
        if pd.isna(row.get("atr")) or pd.isna(row.get("accel_z")):
            return Signal(direction="NONE", blockers=["warming up"])
        if not self._time_ok(row, params):
            return Signal(direction="NONE", blockers=["time gate"])
        direction, score, reasons, blockers, mode = self._core_signal(row, prev, params, ctx)
        if direction not in ("CE", "PE"):
            return Signal(direction="NONE", score=int(score or 0), reasons=reasons or [], blockers=blockers or [])
        if not self._speed_ok(direction, mode, row, params):
            return Signal(direction="NONE", score=int(score), reasons=reasons or [],
                          blockers=list(blockers or []) + ["speed gate"])
        atr = float(row["atr"])
        return Signal(
            direction=direction, score=int(score), reasons=reasons or [], blockers=list(blockers or []),
            spot_target_pts=round(float(params["t_atr"]) * atr, 2),
            spot_stop_pts=round(float(params["s_atr"]) * atr, 2),
            time_stop_minutes=int(params["time_stop_min"]),
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_adaptive_base.py -q`
Expected: 4 passed.

> Note: `AdaptiveStrategyBase` lives OUTSIDE `builtin/`/`plugins/`, so `auto_discover` never instantiates it directly (it only scans those two packages). Concrete strategies in `builtin/` (Plan 2) subclass it.

- [ ] **Step 5: Run the full suite + commit**

Run: `python -m pytest tests -q`
Expected: all green.
```bash
git add backend/app/strategies/adaptive_base.py tests/test_adaptive_base.py
git commit -m "feat(strategies): AdaptiveStrategyBase (time-gate + speed-confirm + ATR exits)"
```

---

### Task 10: Foundation verification in the running stack

**Files:** none (verification only)

- [ ] **Step 1: Full host suite**

Run: `python -m pytest tests -q`
Expected: all pass (612 prior + new toolkit tests), no failures.

- [ ] **Step 2: Rebuild backend + smoke the enriched frame**

Run:
```bash
docker compose up -d --build backend
docker compose ps
```
Expected: `alphaforge_backend` healthy.

- [ ] **Step 3: Confirm columns on a real warehouse frame**

In a backend shell (`docker compose exec backend python`), run:
```python
from app.warehouse import load_candles_df
from app.indicators import precompute_all_indicators
df = load_candles_df("NIFTY", limit=2000)
out = precompute_all_indicators(df)
print([c for c in ["vr","regime_score","squeeze_fire","st_dir","vwap_u2","nr7","cpr_width_pct","day_type","tod_tradeable"] if c in out.columns])
print(out[["vr","regime_score","day_type"]].tail(3).to_dict())
```
Expected: all listed columns present; `vr`/`regime_score` finite on recent bars; `day_type` ∈ {TREND,RANGE,NEUTRAL}. Repeat with `"SENSEX"` to confirm portability (no errors, finite values).

- [ ] **Step 4: Commit a short note (optional) + STOP**

Foundation complete. Proceed to **Plan 2 (the 3 core strategies)**.

---

## Notes / watch-items (carry into Plan 2)
- **Perf:** the toolkit adds O(n) work (one supertrend loop + a few per-session groupbys) to every `precompute_all_indicators` call, including the existing strategies and the optimizer's per-trial frames. The optimizer caches enriched frames per indicator-period combo (`_MAX_ENRICHED_CACHE`), so signal-threshold-only sweeps reuse them. If a profiler later shows this as hot, gate the toolkit block behind a `params`-supplied flag set only for adaptive strategies — do NOT prematurely optimize.
- **`squeeze` momentum** uses the vectorized `close − Donchian/SMA midline` (LazyBear without the linreg smoothing) for speed; the sign/slope that SEB needs is preserved. The linreg smoothing is an optional Plan-2+ refinement if signal quality needs it.
- **`day_type` rolling percentile** includes the current session's (pre-open-known) width — causal because the width is derived from the prior session. Verified by `test_cpr_first_session_has_no_levels` + the narrow-day test.

## Self-Review (done)
- **Spec coverage:** toolkit per-bar columns (§6 table) → Tasks 1-4,7; `cpr.py` (§6) → Task 5; `vol_seasonality.py` Phase-A trailing form (§6, §14) → Task 6; `AdaptiveStrategyBase` + mode-aware Speed confirm (§5, §4 P1) → Task 9; optimizer `INDICATOR_PARAM_KEYS` wiring (§6) → Task 8; causality/look-ahead (§12) → look-ahead tests in Tasks 1,3,4; NIFTY↔SENSEX portability (§11) → Task 10 Step 3. The 5 strategies + XRS engine touch + Phase-C artifacts are intentionally deferred to Plans 2-4.
- **Placeholders:** none — every step has runnable code/commands.
- **Type consistency:** function names (`velocity_accel`, `variance_ratio`, `squeeze`, `supertrend`, `vwap_sigma_bands`, `nr7`, `cpr_levels`, `attach_tod_tradeable`, `AdaptiveStrategyBase`) are used identically in their tests, in the precompute wiring (Task 7), and in the optimizer key list (Task 8). Column names match between producers and `NEW_COLS` in Task 7.
