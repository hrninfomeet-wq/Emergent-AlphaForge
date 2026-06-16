# Adaptive Strategies — Plan 2: Core Strategies (SEB, ARS, ORF) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the three core regime-adaptive intraday option-buying strategies — Squeeze Expansion Breakout (SEB), Adaptive Regime Scalper (ARS), Opening-Range Fade/Break (ORF) — as self-contained drop-in `builtin/` files that subclass `AdaptiveStrategyBase` and read the Plan-1 toolkit columns.

**Architecture:** Each strategy is one `StrategyBase`/`AdaptiveStrategyBase` subclass in `backend/app/strategies/builtin/<id>.py`, auto-discovered at startup. It implements only `_core_signal(row, prev, params, ctx) -> (direction, score, reasons, blockers, mode)`; the base supplies the warmup/time-gate/mode-aware-speed-confirm/ATR-exit scaffolding and merges `BASE_PARAMS` + the strategy's `extra_params` into `parameter_schema`. Strategies are thin decision files reading toolkit columns (`regime_score`, `st_dir`, `squeeze_*`, `vwap_*`, `cpr_*`, `day_type`, `nr7`, `accel_z`, …) produced by `precompute_all_indicators`.

**Tech Stack:** Python 3, pandas. Tests via `pytest` (host-safe — NO `motor`/`optuna`/`server.py`). Depends on Plan 1 (the toolkit foundation, already built + docker-validated).

---

> **Plan 2 of 4.** Builds on Plan 1 (toolkit). Plan 3 = GAP + XRS (+ the opt-in companion-frame engine touch). Plan 4 = Phase-C self-improving layers. Spec §7: [docs/superpowers/specs/2026-06-16-adaptive-options-strategies-design.md](../specs/2026-06-16-adaptive-options-strategies-design.md).
>
> **Conventions (carry from Plan 1):**
> - Branch `feat/adaptive-strategies` (already checked out). Commit after each green task.
> - **Every test file MUST start with the 4-line `sys.path` preamble** (the repo has no conftest.py):
>   ```python
>   import sys
>   from pathlib import Path
>   ROOT = Path(__file__).resolve().parents[1]
>   sys.path.insert(0, str(ROOT / "backend"))
>   ```
> - Strategy files live in `backend/app/strategies/builtin/` so `auto_discover` finds them. `supported_instruments = ["NIFTY", "SENSEX"]` (set on `AdaptiveStrategyBase`; inherited).
> - `_core_signal` returns `(direction, score, reasons, blockers, mode)` where `mode ∈ {"momentum","reversion"}`; the base's speed-gate uses `k_acc` for momentum and `k_acc_fade` for reversion. The base already attaches `spot_target_pts=round(t_atr*atr,2)`, `spot_stop_pts=round(s_atr*atr,2)`, `time_stop_minutes` — strategies do NOT set these.
> - Plan-1 review notes: SEB derives momentum slope from `sqz_mom` vs its prior bar (the column is close-minus-midline, not pre-smoothed); prefer `regime_score` (clipped) over raw `vr`.
> - `ctx` provides `history_df` (full enriched frame), `i` (current integer index), `instrument`. Strategies that need session/coil context read `ctx["history_df"]` up to `ctx["i"]` only (never future bars).

---

### Task 1: Squeeze Expansion Breakout (SEB)

**Files:**
- Create: `backend/app/strategies/builtin/squeeze_expansion_breakout.py`
- Test: `tests/test_strategy_seb.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_seb.py`:
```python
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.strategies.builtin.squeeze_expansion_breakout import SqueezeExpansionBreakout


def _row(**kw):
    base = {"atr": 10.0, "accel_z": 1.5, "ist_time": "10:00", "tod_tradeable": True,
            "squeeze_on": False, "squeeze_fire": True, "sqz_mom": 2.0,
            "vwap": 100.0, "close": 105.0, "nr7": False}
    base.update(kw)
    return pd.Series(base)


def test_seb_registers_and_merges_params():
    s = SqueezeExpansionBreakout()
    assert s.id == "squeeze_expansion_breakout"
    assert "k_acc" in s.parameter_schema and "min_coil_bars" in s.parameter_schema
    assert s.supported_instruments == ["NIFTY", "SENSEX"]


def test_seb_fire_up_emits_CE_with_atr_exits():
    s = SqueezeExpansionBreakout()
    p = s.default_params()
    sig = s.evaluate(_row(), _row(sqz_mom=1.0), p, {})
    assert sig.direction == "CE"
    assert sig.spot_target_pts == pytest.approx(p["t_atr"] * 10.0)
    assert sig.spot_stop_pts == pytest.approx(p["s_atr"] * 10.0)


def test_seb_fire_down_emits_PE():
    s = SqueezeExpansionBreakout()
    p = s.default_params()
    sig = s.evaluate(_row(sqz_mom=-2.0, close=95.0, accel_z=-1.5), _row(sqz_mom=-1.0), p, {})
    assert sig.direction == "PE"


def test_seb_no_fire_is_none():
    s = SqueezeExpansionBreakout()
    p = s.default_params()
    sig = s.evaluate(_row(squeeze_fire=False, squeeze_on=True), None, p, {})
    assert sig.direction == "NONE"


def test_seb_weak_accel_blocked_by_speed_gate():
    s = SqueezeExpansionBreakout()
    p = s.default_params()
    sig = s.evaluate(_row(accel_z=0.1), None, p, {})  # below k_acc -> base blocks
    assert sig.direction == "NONE" and "speed gate" in sig.blockers
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_strategy_seb.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.strategies.builtin.squeeze_expansion_breakout'`.

- [ ] **Step 3: Implement `backend/app/strategies/builtin/squeeze_expansion_breakout.py`**

```python
"""Squeeze Expansion Breakout (SEB) — variance-timing edge.

Buys the direction of a Bollinger-in-Keltner squeeze RELEASE (volatility
compression -> expansion) with an acceleration + VWAP confirm. Long-gamma
ignition: you owned cheap optionality through the coil and ride the expansion.
Built on AdaptiveStrategyBase (time-gate + speed-confirm + ATR exits).
"""
from __future__ import annotations
import pandas as pd
from app.strategies.adaptive_base import AdaptiveStrategyBase


class SqueezeExpansionBreakout(AdaptiveStrategyBase):
    id = "squeeze_expansion_breakout"
    name = "Squeeze Expansion Breakout"
    version = "1.0.0"
    description = ("Long-gamma ignition: buy the direction of a Bollinger-in-Keltner "
                   "squeeze release with acceleration + VWAP confirm. Variance-timing edge.")
    extra_params = {
        "min_coil_bars": {"type": "int", "min": 2, "max": 20, "default": 6},
        "bb_len": {"type": "int", "min": 10, "max": 30, "default": 20},
        "bb_mult": {"type": "float", "min": 1.5, "max": 2.5, "default": 2.0},
        "kc_len": {"type": "int", "min": 10, "max": 30, "default": 20},
        "kc_atr_mult": {"type": "float", "min": 1.0, "max": 2.0, "default": 1.5},
        "sqz_mom_len": {"type": "int", "min": 10, "max": 30, "default": 20},
    }

    def _core_signal(self, row, prev, params, ctx):
        for k in ("squeeze_on", "squeeze_fire", "sqz_mom", "vwap", "close"):
            if pd.isna(row.get(k)):
                return ("NONE", 0, [], ["warming up"], "momentum")
        coil = self._coil_age(ctx)
        fired = bool(row["squeeze_fire"]) or (
            coil >= int(params["min_coil_bars"]) and not bool(row["squeeze_on"]))
        if not fired:
            return ("NONE", 0, [], ["no squeeze fire"], "momentum")
        mom = float(row["sqz_mom"])
        mom_prev = float(prev.get("sqz_mom")) if (prev is not None and not pd.isna(prev.get("sqz_mom"))) else 0.0
        close, vwap = float(row["close"]), float(row["vwap"])
        score = 55
        reasons = ["squeeze fired"]
        if coil >= int(params["min_coil_bars"]):
            score += min(15, coil)
            reasons.append(f"coil={coil}")
        if bool(row.get("nr7")):
            score += 8
            reasons.append("NR7 prior day")
        if mom > 0 and close > vwap and mom >= mom_prev:
            return ("CE", min(100, score + 10), reasons + ["momentum up"], [], "momentum")
        if mom < 0 and close < vwap and mom <= mom_prev:
            return ("PE", min(100, score + 10), reasons + ["momentum down"], [], "momentum")
        return ("NONE", 0, reasons, ["fire without aligned direction"], "momentum")

    @staticmethod
    def _coil_age(ctx) -> int:
        hist = ctx.get("history_df") if ctx else None
        i = ctx.get("i") if ctx else None
        if hist is None or i is None or "squeeze_on" not in getattr(hist, "columns", []):
            return 0
        col = hist["squeeze_on"]
        coil, j = 0, int(i) - 1
        while j >= 0 and bool(col.iloc[j]):
            coil += 1
            j -= 1
        return coil
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_strategy_seb.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/strategies/builtin/squeeze_expansion_breakout.py tests/test_strategy_seb.py
git commit -m "feat(strategy): Squeeze Expansion Breakout (SEB) — variance-timing"
```

---

### Task 2: Adaptive Regime Scalper (ARS)

**Files:**
- Create: `backend/app/strategies/builtin/adaptive_regime_scalper.py`
- Test: `tests/test_strategy_ars.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_ars.py`:
```python
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.strategies.builtin.adaptive_regime_scalper import AdaptiveRegimeScalper


def _row(**kw):
    base = {"atr": 10.0, "accel_z": 1.5, "ist_time": "10:00", "tod_tradeable": True,
            "regime_score": 0.5, "st_dir": 1, "vwap": 100.0, "vwap_l2": 92.0, "vwap_u2": 108.0,
            "close": 105.0, "cpr_tc": 102.0, "cpr_bc": 98.0, "day_type": "TREND"}
    base.update(kw)
    return pd.Series(base)


def test_ars_registers_and_merges_params():
    s = AdaptiveRegimeScalper()
    assert s.id == "adaptive_regime_scalper"
    assert "k_acc" in s.parameter_schema and "dead_band" in s.parameter_schema


def test_ars_trend_regime_emits_CE():
    s = AdaptiveRegimeScalper()
    p = s.default_params()
    assert s.evaluate(_row(), None, p, {}).direction == "CE"  # VR>1 trend, ST up, reclaim


def test_ars_fade_regime_emits_CE_reversion():
    s = AdaptiveRegimeScalper()
    p = s.default_params()
    # VR<1 range day, price at -2sigma, accel turning (reversion gate allows az >= -k_acc_fade)
    sig = s.evaluate(_row(regime_score=-0.5, day_type="RANGE", st_dir=-1, close=90.0, accel_z=-0.1), None, p, {})
    assert sig.direction == "CE"


def test_ars_stand_aside_in_random_walk():
    s = AdaptiveRegimeScalper()
    p = s.default_params()
    assert s.evaluate(_row(regime_score=0.05, day_type="NEUTRAL", close=100.0), None, p, {}).direction == "NONE"


def test_ars_trend_down_emits_PE():
    s = AdaptiveRegimeScalper()
    p = s.default_params()
    sig = s.evaluate(_row(st_dir=-1, close=95.0, accel_z=-1.5), None, p, {})
    assert sig.direction == "PE"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_strategy_ars.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `backend/app/strategies/builtin/adaptive_regime_scalper.py`**

```python
"""Adaptive Regime Scalper (ARS) — direction-timing edge (flagship).

Soft-blends a trend module and a fade module by the Variance-Ratio regime score,
biased by the CPR day-type. Trend-ride when VR>1 (Supertrend + VWAP/CPR reclaim),
fade VWAP-2sigma/CPR edges when VR<1, stand aside near VR~1. Built on
AdaptiveStrategyBase.
"""
from __future__ import annotations
import pandas as pd
from app.strategies.adaptive_base import AdaptiveStrategyBase


class AdaptiveRegimeScalper(AdaptiveStrategyBase):
    id = "adaptive_regime_scalper"
    name = "Adaptive Regime Scalper"
    version = "1.0.0"
    description = ("Variance-Ratio soft-blend regime switch: trend-ride (Supertrend + "
                   "VWAP/CPR reclaim) when VR>1, fade VWAP-2sigma/CPR edges when VR<1, "
                   "biased by CPR day-type. Direction-timing edge.")
    extra_params = {
        "dead_band": {"type": "float", "min": 0.05, "max": 0.4, "default": 0.15},
        "vr_q": {"type": "int", "min": 2, "max": 10, "default": 4},
        "vr_lookback": {"type": "int", "min": 40, "max": 150, "default": 90},
        "st_period": {"type": "int", "min": 5, "max": 20, "default": 10},
        "st_mult": {"type": "float", "min": 1.5, "max": 4.0, "default": 3.0},
    }

    def _core_signal(self, row, prev, params, ctx):
        for k in ("regime_score", "st_dir", "vwap", "vwap_l2", "vwap_u2", "close", "cpr_tc", "cpr_bc"):
            if pd.isna(row.get(k)):
                return ("NONE", 0, [], ["warming up"], "momentum")
        rs = float(row["regime_score"])
        dt = str(row.get("day_type", "NEUTRAL"))
        if abs(rs) < float(params["dead_band"]) and dt == "NEUTRAL":
            return ("NONE", 0, [], ["random walk / stand aside"], "momentum")
        bias = 1.2 if dt == "TREND" else (0.8 if dt == "RANGE" else 1.0)
        w_trend = max(0.0, rs) * bias
        w_fade = max(0.0, -rs) / bias
        close, vwap = float(row["close"]), float(row["vwap"])
        st = int(row["st_dir"])
        cands = []  # (weighted_score, direction, mode, kind)
        if st > 0 and close > vwap and close > float(row["cpr_tc"]):
            cands.append((w_trend * 60.0, "CE", "momentum", "trend"))
        elif st < 0 and close < vwap and close < float(row["cpr_bc"]):
            cands.append((w_trend * 60.0, "PE", "momentum", "trend"))
        if close <= float(row["vwap_l2"]):
            cands.append((w_fade * 60.0, "CE", "reversion", "fade"))
        elif close >= float(row["vwap_u2"]):
            cands.append((w_fade * 60.0, "PE", "reversion", "fade"))
        cands = [c for c in cands if c[0] > 0]
        if not cands:
            return ("NONE", 0, [], ["no weighted setup"], "momentum")
        cands.sort(key=lambda c: c[0], reverse=True)
        wscore, direction, mode, kind = cands[0]
        score = int(min(100, 50 + wscore))
        return (direction, score, [f"{kind} rs={rs:.2f} day={dt}"], [], mode)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_strategy_ars.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/strategies/builtin/adaptive_regime_scalper.py tests/test_strategy_ars.py
git commit -m "feat(strategy): Adaptive Regime Scalper (ARS) — VR soft-blend regime switch"
```

---

### Task 3: Opening-Range Fade/Break (ORF)

**Files:**
- Create: `backend/app/strategies/builtin/opening_range_adaptive.py`
- Test: `tests/test_strategy_orf.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_orf.py`:
```python
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.strategies.builtin.opening_range_adaptive import OpeningRangeAdaptive


def _session_hist(n=26, sess="2025-01-02"):
    """n 1-min bars; first 15 form an OR of [100,105]; later bars break above to 109."""
    rows = []
    for k in range(n):
        mm = 15 + k
        ist = f"09:{mm:02d}" if mm < 60 else f"10:{mm - 60:02d}"
        cl = 102.0 if k < 16 else 109.0
        rows.append({"session_date": sess, "ist_time": ist,
                     "high": 105.0 if k < 16 else 109.5, "low": 100.0,
                     "close": cl, "atr": 3.0})
    return pd.DataFrame(rows)


def test_orf_registers():
    s = OpeningRangeAdaptive()
    assert s.id == "opening_range_adaptive"
    assert "or_minutes" in s.parameter_schema and "k_acc" in s.parameter_schema


def test_orf_breakout_up_on_trend_day_emits_CE():
    s = OpeningRangeAdaptive()
    p = s.default_params()
    hist = _session_hist()
    i = len(hist) - 1
    row = hist.iloc[i].copy()
    row["accel_z"] = 1.5
    row["tod_tradeable"] = True
    row["regime_score"] = 0.5
    row["day_type"] = "TREND"
    row["nr7"] = False
    ctx = {"i": i, "history_df": hist, "instrument": "NIFTY"}
    sig = s.evaluate(row, hist.iloc[i - 1], p, ctx)
    assert sig.direction == "CE"


def test_orf_outside_window_is_none():
    s = OpeningRangeAdaptive()
    p = s.default_params()
    hist = _session_hist()
    i = len(hist) - 1
    row = hist.iloc[i].copy()
    row["ist_time"] = "11:30"  # past the opening window
    row["accel_z"] = 1.5
    row["tod_tradeable"] = True
    row["regime_score"] = 0.5
    row["day_type"] = "TREND"
    ctx = {"i": i, "history_df": hist, "instrument": "NIFTY"}
    assert s.evaluate(row, hist.iloc[i - 1], p, ctx).direction == "NONE"


def test_orf_failed_break_fade_on_range_day_emits_PE():
    s = OpeningRangeAdaptive()
    p = s.default_params()
    # build a session that pokes above OR-high then closes back inside on a range day
    rows = []
    for k in range(20):
        mm = 15 + k
        ist = f"09:{mm:02d}" if mm < 60 else f"10:{mm - 60:02d}"
        rows.append({"session_date": "2025-01-03", "ist_time": ist,
                     "high": 105.0, "low": 100.0, "close": 102.0, "atr": 3.0})
    hist = pd.DataFrame(rows)
    i = len(hist) - 1
    prev = hist.iloc[i - 1].copy()
    prev["close"] = 106.0  # prior bar poked above OR-high (105)
    row = hist.iloc[i].copy()
    row["close"] = 104.0   # closed back inside -> failed breakout
    row["accel_z"] = -0.1  # reversion: turning, not strongly counter
    row["tod_tradeable"] = True
    row["regime_score"] = -0.5
    row["day_type"] = "RANGE"
    row["nr7"] = False
    ctx = {"i": i, "history_df": hist, "instrument": "NIFTY"}
    assert s.evaluate(row, prev, p, ctx).direction == "PE"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_strategy_orf.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `backend/app/strategies/builtin/opening_range_adaptive.py`**

```python
"""Opening-Range Fade/Break (ORF) — trapped-liquidity + contraction-selectivity.

Marks the first N-minute opening range, then routes the SAME event by regime:
breakout on trend / NR7 days, fade a FAILED breakout on range days. Opening
window only. Built on AdaptiveStrategyBase.
"""
from __future__ import annotations
import pandas as pd
from app.strategies.adaptive_base import AdaptiveStrategyBase


class OpeningRangeAdaptive(AdaptiveStrategyBase):
    id = "opening_range_adaptive"
    name = "Opening-Range Fade/Break"
    version = "1.0.0"
    description = ("First-N-min opening range: breakout on trend/NR7 days, fade failed "
                   "breakouts on range days. Trapped-liquidity + contraction-selectivity edge.")
    extra_params = {
        "or_minutes": {"type": "int", "min": 5, "max": 30, "default": 15},
        "break_buffer_atr": {"type": "float", "min": 0.0, "max": 0.5, "default": 0.1},
        "or_window_end_hhmm": {"type": "str", "default": "10:45"},
        "require_nr7_for_break": {"type": "bool", "default": False},
    }

    def _core_signal(self, row, prev, params, ctx):
        t = str(row.get("ist_time") or "")
        if not t or t > str(params["or_window_end_hhmm"]):
            return ("NONE", 0, [], ["outside opening window"], "momentum")
        if pd.isna(row.get("atr")):
            return ("NONE", 0, [], ["warming up"], "momentum")
        orr = self._opening_range(row, ctx, int(params["or_minutes"]))
        if orr is None:
            return ("NONE", 0, [], ["OR forming / not ready"], "momentum")
        or_hi, or_lo = orr
        buf = float(params["break_buffer_atr"]) * float(row["atr"])
        close = float(row["close"])
        rs = float(row.get("regime_score") or 0.0)
        dt = str(row.get("day_type", "NEUTRAL"))
        nr7 = bool(row.get("nr7"))
        prev_close = float(prev["close"]) if (prev is not None and not pd.isna(prev.get("close"))) else close
        trend_day = rs > 0 or dt == "TREND" or nr7
        range_day = rs < 0 or dt == "RANGE"
        if trend_day and (not params["require_nr7_for_break"] or nr7):
            if close > or_hi + buf:
                return ("CE", 65, [f"OR breakout up day={dt}"], [], "momentum")
            if close < or_lo - buf:
                return ("PE", 65, [f"OR breakout down day={dt}"], [], "momentum")
        if range_day:
            if prev_close > or_hi >= close:
                return ("PE", 60, ["failed up-break -> fade"], [], "reversion")
            if prev_close < or_lo <= close:
                return ("CE", 60, ["failed down-break -> fade"], [], "reversion")
        return ("NONE", 0, [], ["no OR setup"], "momentum")

    @staticmethod
    def _opening_range(row, ctx, or_minutes):
        hist = ctx.get("history_df") if ctx else None
        i = ctx.get("i") if ctx else None
        if hist is None or i is None or "session_date" not in getattr(hist, "columns", []):
            return None
        sess = row.get("session_date")
        upto = hist.iloc[: int(i) + 1]
        sess_bars = upto[upto["session_date"] == sess]
        if len(sess_bars) <= or_minutes:
            return None  # still forming the OR — do not trade yet
        or_bars = sess_bars.iloc[:or_minutes]
        return float(or_bars["high"].max()), float(or_bars["low"].min())
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_strategy_orf.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/strategies/builtin/opening_range_adaptive.py tests/test_strategy_orf.py
git commit -m "feat(strategy): Opening-Range Fade/Break (ORF) — trapped-liquidity"
```

---

### Task 4: Registration + integration verification

**Files:**
- Test: `tests/test_new_strategies_integration.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_new_strategies_integration.py`:
```python
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pandas as pd
from app.strategies.base import get_registry
from app.indicators import precompute_all_indicators
from tests._adaptive_testutil import make_sessions

NEW_IDS = ["squeeze_expansion_breakout", "adaptive_regime_scalper", "opening_range_adaptive"]


def test_new_strategies_auto_discovered():
    reg = get_registry()
    reg.auto_discover()
    for sid in NEW_IDS:
        s = reg.get(sid)
        assert s is not None, f"{sid} not auto-discovered"
        assert "NIFTY" in s.supported_instruments and "SENSEX" in s.supported_instruments


def test_strategies_run_over_enriched_frame_without_error():
    # a few synthetic sessions of varied price action -> enrich -> run each strategy on every bar
    rng = np.random.default_rng(7)
    sessions = []
    for _ in range(6):
        base = 100.0 + rng.standard_normal(80).cumsum()
        sessions.append(list(base))
    df = make_sessions(sessions)
    out = precompute_all_indicators(df)
    reg = get_registry()
    reg.auto_discover()
    for sid in NEW_IDS:
        s = reg.get(sid)
        p = s.default_params()
        n_sig = 0
        for i in range(len(out)):
            row = out.iloc[i]
            prev = out.iloc[i - 1] if i > 0 else None
            ctx = {"i": i, "history_df": out, "instrument": "NIFTY"}
            sig = s.evaluate(row, prev, p, ctx)
            assert sig.direction in ("CE", "PE", "NONE")
            if sig.direction in ("CE", "PE"):
                # ATR exits attached by the base
                assert sig.spot_target_pts is not None and sig.spot_stop_pts is not None
                n_sig += 1
        # no assertion on count (synthetic data may not trigger), but must not error
```

- [ ] **Step 2: Run to verify it fails or passes**

Run: `python -m pytest tests/test_new_strategies_integration.py -q`
(Will fail only if a strategy errors on a real enriched frame; otherwise passes once Tasks 1–3 are in.)

- [ ] **Step 3: Fix any integration error surfaced, then confirm**

Run: `python -m pytest tests/test_new_strategies_integration.py -q`
Expected: 2 passed.

- [ ] **Step 4: Full suite**

Run: `python -m pytest tests -q`
Expected: all pass (686 from Plan 1 + new strategy tests), 0 failed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_new_strategies_integration.py
git commit -m "test(strategies): auto-discovery + enriched-frame integration smoke"
```

- [ ] **Step 6: Docker smoke (running stack)**

Rebuild + confirm the registry exposes the 3 new strategies via the API:
```bash
docker compose up -d --build backend
curl -s localhost:8001/api/strategies | python -m json.tool | grep -E "squeeze_expansion_breakout|adaptive_regime_scalper|opening_range_adaptive"
```
Expected: all three ids appear. (Then a real backtest of each can be run from the Backtest Lab / API as the next phase.)

---

## Self-Review (done)
- **Spec coverage:** SEB (§7.1) → Task 1; ARS (§7.2) → Task 2; ORF (§7.3) → Task 3; auto-discovery/modularity (§5) + producer→consumer contract → Task 4. GAP/XRS (§7.4/7.5) are Plan 3; Phase-C (§9 estimated artifacts) is Plan 4.
- **Base-contract consistency:** every strategy returns the 5-tuple `(direction, score, reasons, blockers, mode)`; none sets `spot_target_pts`/`time_stop_minutes` (the base does). `extra_params` keys that are indicator periods (`bb_len`, `vr_q`, `st_period`, …) are already registered in `optimizer.INDICATOR_PARAM_KEYS` (Plan 1 Task 8), so the optimizer recomputes when it tunes them.
- **Placeholder scan:** none — every step has runnable code/commands.
- **Causality:** SEB coil + ORF opening-range read `ctx["history_df"]` only up to `ctx["i"]`; no future bars. ATR exits and the time/speed gates come from the Plan-1 base (already tested).
- **Host-safety:** strategy modules import only `pandas` + `app.strategies.adaptive_base`; the integration test's `auto_discover` imports the existing builtins (all host-safe). No `motor`/`optuna`.
