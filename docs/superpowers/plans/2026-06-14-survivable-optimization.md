# Survivable Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the optimizer select strategies that survive hard constraints on the ₹ option-equity curve (absolute floor + drawdown-% cap + risk-of-ruin), evaluated per walk-forward OOS fold, ranked by a configurable Calmar/net-₹ objective — instead of maximizing a spot-points number that can bankrupt the account.

**Architecture:** Approach A — a new pure-Python `app/survival.py` computes the metrics + verdict; the gate is wired into the existing two-stage option re-rank in `optimizer.py` and the finalize block. Default-OFF (`survival_config.enabled=false`) ⇒ byte-identical to today. Validation lives in the `OptimizerStartReq` pydantic model + the `/optimize/start` route (both in the contract corpus).

**Tech Stack:** Python 3.12, FastAPI, Motor (async Mongo), Optuna; numpy for the Monte-Carlo; pytest (host-safe — no motor/optuna imports in `survival.py` or its tests). Frontend: React (CRA), axios.

**Spec:** [docs/superpowers/specs/2026-06-14-survivable-optimization-design.md](../specs/2026-06-14-survivable-optimization-design.md)

---

## File Structure

- **Create** `backend/app/survival.py` — pure-Python survival math + verdict (`SurvivalConfig`, `calmar`, `monte_carlo_risk_of_ruin`, `survival_verdict`, `daily_from_curve`). No motor/optuna. One responsibility: turn a ₹ equity curve + trade series + config into a survival verdict.
- **Create** `tests/test_survival.py` — host-safe unit tests for `survival.py`.
- **Modify** `backend/app/schemas.py` — add `SurvivalConfig` pydantic model + `survival_config` field on `OptimizerStartReq`.
- **Modify** `backend/app/routers/research.py` — `/optimize/start` validation (clear 400s) when survival is enabled.
- **Modify** `backend/app/optimizer.py` — capture `sim['portfolio']`/`sim['trades']`; add `_survival_eval_oos` helper (per-fold OOS ₹ pairing + verdict); wire the gate + zero-survivor block into the finalize stage.
- **Create** `tests/test_survival_contract.py` — contract asserts (schema fields, router validation strings).
- **Modify** `frontend/src/pages/Optimizer.jsx` — Survivability setup panel + results badges + return-vs-drawdown scatter.

---

## PHASE 1 — `app/survival.py` (pure module, fully TDD)

### Task 1: `SurvivalConfig` + `calmar`

**Files:**
- Create: `backend/app/survival.py`
- Test: `tests/test_survival.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_survival.py
from app.survival import SurvivalConfig, calmar, CALMAR_DD_FLOOR_PCT


def test_survival_config_from_dict_defaults_and_overrides():
    assert SurvivalConfig.from_dict(None).enabled is False
    cfg = SurvivalConfig.from_dict({"enabled": True, "max_drawdown_pct": 30,
                                    "objective": "net_inr", "min_oos_folds": "majority"})
    assert cfg.enabled is True
    assert cfg.max_drawdown_pct == 30.0
    assert cfg.objective == "net_inr"
    assert cfg.min_oos_folds == "majority"
    # bad objective falls back to default
    assert SurvivalConfig.from_dict({"objective": "bogus"}).objective == "calmar"


def test_calmar_floors_denominator_at_meaningful_dd():
    # dd_pct is NEGATIVE percent; magnitude used.
    assert calmar(150.0, -30.0) == 5.0                 # 150 / 30
    # near-zero DD does NOT explode: floored at CALMAR_DD_FLOOR_PCT (5%)
    assert calmar(150.0, -0.5) == 150.0 / CALMAR_DD_FLOOR_PCT
    assert calmar(150.0, 0.0) == 150.0 / CALMAR_DD_FLOOR_PCT
    # negative return ranks worst, stays negative
    assert calmar(-40.0, -20.0) < 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_survival.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.survival'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/survival.py
"""Survival metrics + verdict for capital-aware, risk-constrained optimization.

Pure-Python (no motor/optuna) so it is host-testable like app/rerank_select.py.
Consumes the rupee-equity outputs already produced by app/portfolio.py +
app/option_backtest.py; it NEVER changes their signatures.

The optimizer scores spot-index points, but ruin happens on the RUPEE option
equity curve. These helpers gate finalists on that curve: an absolute equity
floor (primary), a drawdown-% cap, and a Monte-Carlo risk-of-ruin — meant to be
applied OUT-OF-SAMPLE (per walk-forward fold) by the caller.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

# A tail statistic (ruin probability) needs more than the spot min_trades=10
# guard (which counts SPOT trades); this counts PAIRED rupee trades.
MIN_TRADES_FOR_RUIN = 100
# Below this paired/spot ratio the rupee curve is built on too small a subset
# (pairing fails on illiquid strikes during the violent moves that cause real
# ruin), so the verdict is unreliable -> HARD fail, not an advisory flag.
MIN_COVERAGE = 0.8
# Calmar denominator floor: a MEANINGFUL drawdown so a near-zero-DD fluke cannot
# explode the ratio. Percent units (dd_pct is like -12.0).
CALMAR_DD_FLOOR_PCT = 5.0

_IST = timedelta(hours=5, minutes=30)


@dataclass
class SurvivalConfig:
    enabled: bool = False
    min_equity: float = 0.0          # PRIMARY gate: reject if realized equity ever <= this
    max_drawdown_pct: float = 35.0   # reject if |peak DD%| exceeds this
    max_ror_pct: float = 5.0         # reject if RoR upper-CI exceeds this
    ruin_floor: float = 0.0          # RoR ruin level (rupees); validated 0 <= ruin_floor < capital
    objective: str = "calmar"        # "calmar" | "net_inr"
    min_oos_folds: str = "all"       # "all" | "majority"

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "SurvivalConfig":
        if not data:
            return cls()
        cfg = cls()
        if "enabled" in data:
            cfg.enabled = bool(data["enabled"])
        for k in ("min_equity", "max_drawdown_pct", "max_ror_pct", "ruin_floor"):
            if data.get(k) is not None:
                try:
                    setattr(cfg, k, float(data[k]))
                except (TypeError, ValueError):
                    pass
        if data.get("objective") in ("calmar", "net_inr"):
            cfg.objective = str(data["objective"])
        if data.get("min_oos_folds") in ("all", "majority"):
            cfg.min_oos_folds = str(data["min_oos_folds"])
        return cfg


def _finite(values: Sequence[Any]) -> List[float]:
    """Keep only finite floats (drops NaN/inf/None that would poison equity math)."""
    out: List[float] = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def calmar(return_pct: float, dd_pct: float) -> float:
    """Risk-adjusted return on the RUPEE equity curve: return% / |maxDD%|.

    Units are PERCENT (dd_pct is negative, e.g. -12.0). Denominator floored at
    CALMAR_DD_FLOOR_PCT so a near-zero-DD candidate doesn't get an infinite score.
    """
    denom = max(abs(float(dd_pct)), CALMAR_DD_FLOOR_PCT)
    return float(return_pct) / denom
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_survival.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/survival.py tests/test_survival.py
git commit -m "feat(survival): SurvivalConfig + calmar (floored denominator)"
```

---

### Task 2: `monte_carlo_risk_of_ruin` (per-day bootstrap, seeded, CI)

**Files:**
- Modify: `backend/app/survival.py`
- Test: `tests/test_survival.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_survival.py  (append)
from app.survival import monte_carlo_risk_of_ruin


def test_ror_zero_when_only_gains():
    r = monte_carlo_risk_of_ruin([100.0] * 200, capital=200_000, ruin_floor=0,
                                 n_paths=2000, seed=1)
    assert r["ror_pct"] == 0.0
    assert r["ror_ci_high"] >= 0.0
    assert r["n_days"] == 200


def test_ror_high_when_capital_tiny_vs_swings():
    # Capital barely above a single day's loss; ruin is near-certain.
    r = monte_carlo_risk_of_ruin([-50.0, 60.0] * 100, capital=40, ruin_floor=0,
                                 n_paths=4000, seed=1)
    assert r["ror_pct"] > 50.0


def test_ror_is_reproducible_with_seed():
    a = monte_carlo_risk_of_ruin([-10, 12, -8, 15] * 50, 1000, 0, n_paths=3000, seed=7)
    b = monte_carlo_risk_of_ruin([-10, 12, -8, 15] * 50, 1000, 0, n_paths=3000, seed=7)
    assert a["ror_pct"] == b["ror_pct"]


def test_ror_empty_series_is_insufficient_and_max_risk():
    r = monte_carlo_risk_of_ruin([], capital=200_000, ruin_floor=0)
    assert r["n_days"] == 0
    assert r["ror_pct"] == 100.0


def test_ror_drops_nonfinite_days():
    r = monte_carlo_risk_of_ruin([float("nan"), 10.0, float("inf"), -5.0], 1000, 0,
                                 n_paths=500, seed=1)
    assert r["n_days"] == 2  # nan + inf dropped
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_survival.py -k ror -q`
Expected: FAIL — `ImportError: cannot import name 'monte_carlo_risk_of_ruin'`

- [ ] **Step 3: Implement**

```python
# backend/app/survival.py  (append)
def monte_carlo_risk_of_ruin(
    daily_pnls: Sequence[Any],
    capital: float,
    ruin_floor: float = 0.0,
    n_paths: int = 10000,
    horizon: Optional[int] = None,
    seed: int = 42,
) -> Dict[str, Any]:
    """Estimate P(account ever falls to/through ruin_floor) by bootstrapping
    PER-DAY rupee P&L (preserves intraday loss clustering — a per-TRADE i.i.d.
    bootstrap understates ruin in the unsafe direction).

    Path 0 is the ACTUAL observed daily sequence so the realized worst path is
    always counted. Returns {ror_pct, ror_ci_high, n_days}. Seeded =>
    reproducible. Fully vectorized over (n_paths, horizon).
    """
    pnls = _finite(daily_pnls)
    n_days = len(pnls)
    if n_days == 0:
        return {"ror_pct": 100.0, "ror_ci_high": 100.0, "n_days": 0}
    h = int(horizon or n_days)
    rng = np.random.default_rng(seed)
    arr = np.asarray(pnls, dtype=float)
    samples = rng.choice(arr, size=(int(n_paths), h), replace=True)
    if h == n_days:
        samples[0, :] = arr  # seed path 0 with the real observed sequence
    equity = float(capital) + np.cumsum(samples, axis=1)
    min_equity = equity.min(axis=1)
    ruined = int(np.count_nonzero(min_equity <= float(ruin_floor)))
    p = ruined / float(n_paths)
    # Wald upper 95% bound — fail-closed: "can't prove safe" counts as unsafe.
    se = math.sqrt(max(p * (1.0 - p), 1e-9) / float(n_paths))
    ci_high = min(1.0, p + 1.96 * se)
    return {"ror_pct": round(p * 100.0, 3), "ror_ci_high": round(ci_high * 100.0, 3), "n_days": n_days}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_survival.py -k ror -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/survival.py tests/test_survival.py
git commit -m "feat(survival): seeded per-day-bootstrap Monte-Carlo risk-of-ruin with CI"
```

---

### Task 3: `daily_from_curve` + `survival_verdict` (the gate)

**Files:**
- Modify: `backend/app/survival.py`
- Test: `tests/test_survival.py`

- [ ] **Step 1: Write the failing test** — this is the safety-critical test, including the drawdown-SIGN regression that the audit flagged.

```python
# tests/test_survival.py  (append)
from app.survival import survival_verdict, daily_from_curve, MIN_TRADES_FOR_RUIN


def _curve(equity_points):
    # equity_points: list of (ts_ms, equity_value). pnl_value inferred as deltas.
    out = []
    prev = equity_points[0][1]
    for ts, eq in equity_points:
        out.append({"ts": ts, "equity_value": eq, "pnl_value": eq - prev,
                    "drawdown_value": 0.0, "drawdown_pct": 0.0})
        prev = eq
    return out


def _portfolio(curve, max_dd_pct, total_return_pct, capital=200_000):
    return {"starting_capital": capital, "curve": curve,
            "max_drawdown_pct": max_dd_pct, "total_return_pct": total_return_pct}


def _cfg(**kw):
    from app.survival import SurvivalConfig
    base = dict(enabled=True, min_equity=0.0, max_drawdown_pct=35.0, max_ror_pct=5.0)
    base.update(kw)
    return SurvivalConfig.from_dict(base)


def test_verdict_rejects_account_that_went_negative_PRIMARY_floor():
    # Equity dips to -49,130 (the user's bankrupt run). MUST be rejected even
    # though from-peak DD% alone might look survivable.
    curve = _curve([(1, 200_000), (2, 80_000), (3, -49_130), (4, 50_000)])
    trade_pnls = [120_000, -129_130, 99_130] * 40  # >=120 trades
    port = _portfolio(curve, max_dd_pct=-30.0, total_return_pct=10.0)
    v = survival_verdict(portfolio=port, trade_pnls=trade_pnls, cfg=_cfg(),
                         coverage={"spot_trade_count": 120, "paired_trade_count": 120},
                         capital=200_000)
    assert v["survived"] is False
    assert v["reason"] == "equity_floor"


def test_verdict_drawdown_sign_regression():
    # max_dd_pct is NEGATIVE (-40). A naive `dd <= 35` would PASS this. Magnitude
    # compare must REJECT (40% > 35% cap).
    curve = _curve([(1, 200_000), (2, 350_000), (3, 210_000)])  # never <= 0
    trade_pnls = [150_000, -140_000] * 60
    port = _portfolio(curve, max_dd_pct=-40.0, total_return_pct=5.0)
    v = survival_verdict(portfolio=port, trade_pnls=trade_pnls, cfg=_cfg(),
                         coverage={"spot_trade_count": 120, "paired_trade_count": 120},
                         capital=200_000)
    assert v["survived"] is False
    assert v["reason"] == "max_drawdown"


def test_verdict_survives_clean_run():
    curve = _curve([(1, 200_000), (2, 230_000), (3, 290_000), (4, 312_000)])
    trade_pnls = [800.0] * 150  # all small gains -> tiny DD, no ruin
    port = _portfolio(curve, max_dd_pct=-12.0, total_return_pct=56.0)
    v = survival_verdict(portfolio=port, trade_pnls=trade_pnls, cfg=_cfg(),
                         coverage={"spot_trade_count": 160, "paired_trade_count": 150},
                         capital=200_000)
    assert v["survived"] is True
    assert v["calmar"] > 0


def test_verdict_fails_low_coverage_hard():
    curve = _curve([(1, 200_000), (2, 260_000)])
    port = _portfolio(curve, max_dd_pct=-5.0, total_return_pct=30.0)
    v = survival_verdict(portfolio=port, trade_pnls=[1000.0] * 150, cfg=_cfg(),
                         coverage={"spot_trade_count": 300, "paired_trade_count": 150},  # 0.5
                         capital=200_000)
    assert v["survived"] is False
    assert v["reason"] == "low_coverage"


def test_verdict_fails_insufficient_sample():
    curve = _curve([(1, 200_000), (2, 260_000)])
    port = _portfolio(curve, max_dd_pct=-5.0, total_return_pct=30.0)
    v = survival_verdict(portfolio=port, trade_pnls=[1000.0] * 10, cfg=_cfg(),  # < 100
                         coverage={"spot_trade_count": 10, "paired_trade_count": 10},
                         capital=200_000)
    assert v["survived"] is False
    assert v["insufficient_sample"] is True


def test_verdict_fails_empty_trades():
    port = _portfolio([], max_dd_pct=0.0, total_return_pct=0.0)
    v = survival_verdict(portfolio=port, trade_pnls=[], cfg=_cfg(),
                         coverage={"spot_trade_count": 0, "paired_trade_count": 0},
                         capital=200_000)
    assert v["survived"] is False


def test_daily_from_curve_buckets_by_ist_date():
    # two trades same IST day, one next day
    day1a = 1_700_000_000_000
    day1b = day1a + 60_000
    day2 = day1a + 24 * 3600 * 1000
    curve = [{"ts": day1a, "pnl_value": 100.0}, {"ts": day1b, "pnl_value": -40.0},
             {"ts": day2, "pnl_value": 25.0}]
    daily = daily_from_curve(curve)
    assert sorted(daily) == sorted([60.0, 25.0])  # 100-40=60 ; 25
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_survival.py -k verdict -q`
Expected: FAIL — `ImportError: cannot import name 'survival_verdict'`

- [ ] **Step 3: Implement**

```python
# backend/app/survival.py  (append)
def daily_from_curve(curve: Sequence[Dict[str, Any]]) -> List[float]:
    """Bucket a rupee equity curve's per-trade pnl_value into per-IST-day totals."""
    by_day: Dict[str, float] = {}
    for pt in curve:
        try:
            ts = int(pt.get("ts"))
            pnl = float(pt.get("pnl_value", 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(pnl):
            continue
        d = (datetime.fromtimestamp(ts / 1000, tz=timezone.utc) + _IST).strftime("%Y-%m-%d")
        by_day[d] = by_day.get(d, 0.0) + pnl
    return list(by_day.values())


def survival_verdict(
    *,
    portfolio: Dict[str, Any],
    trade_pnls: Sequence[Any],
    cfg: SurvivalConfig,
    coverage: Dict[str, Any],
    capital: float,
    seed: int = 42,
) -> Dict[str, Any]:
    """Decide whether one finalist's RUPEE equity curve SURVIVES. Guards run
    first; then the gates in priority order: absolute floor -> DD% -> RoR.
    `survived` reflects SAFETY only; the caller additionally requires
    total_return_pct > 0 before promoting a survivor.
    """
    pnls = _finite(trade_pnls)
    n = len(pnls)
    spot_ct = int((coverage or {}).get("spot_trade_count", 0) or 0)
    paired_ct = int((coverage or {}).get("paired_trade_count", n) or 0)
    max_dd_pct = portfolio.get("max_drawdown_pct")
    total_return_pct = portfolio.get("total_return_pct")
    curve = portfolio.get("curve") or []

    base = {
        "survived": False, "calmar": None, "ror_pct": None, "ror_ci_high": None,
        "min_equity": None, "max_dd_pct": max_dd_pct,
        "total_return_pct": total_return_pct,
        "insufficient_sample": False, "low_coverage": False, "reason": None,
    }

    # --- Guards (fail-closed) ---
    if n == 0:
        return {**base, "insufficient_sample": True, "reason": "no_trades"}
    if max_dd_pct is None or total_return_pct is None or not math.isfinite(float(total_return_pct)):
        return {**base, "reason": "non_finite_metrics"}
    if spot_ct > 0 and (paired_ct / spot_ct) < MIN_COVERAGE:
        return {**base, "low_coverage": True, "reason": "low_coverage"}

    cal = calmar(float(total_return_pct), float(max_dd_pct))
    base["calmar"] = round(cal, 4)

    # min realized equity from the curve (the deterministic absolute floor)
    eqs = _finite([pt.get("equity_value") for pt in curve])
    min_equity = min(eqs) if eqs else float(capital)
    base["min_equity"] = round(min_equity, 2)

    # 1. PRIMARY — absolute equity floor
    if min_equity <= cfg.min_equity:
        return {**base, "reason": "equity_floor"}
    # 2. Drawdown-% cap (MAGNITUDE compare — max_dd_pct is negative)
    if abs(float(max_dd_pct)) > cfg.max_drawdown_pct:
        return {**base, "reason": "max_drawdown"}
    # 3. Risk-of-ruin (needs a tail-sized sample)
    if n < MIN_TRADES_FOR_RUIN:
        return {**base, "insufficient_sample": True, "reason": "insufficient_sample"}
    daily = daily_from_curve(curve)
    ror = monte_carlo_risk_of_ruin(daily, capital=capital, ruin_floor=cfg.ruin_floor, seed=seed)
    base["ror_pct"] = ror["ror_pct"]
    base["ror_ci_high"] = ror["ror_ci_high"]
    if ror["ror_ci_high"] > cfg.max_ror_pct:
        return {**base, "reason": "risk_of_ruin"}

    return {**base, "survived": True, "reason": "ok"}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_survival.py -q`
Expected: PASS (all ~13 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/survival.py tests/test_survival.py
git commit -m "feat(survival): survival_verdict gate (floor->DD->RoR) + daily_from_curve"
```

---

## PHASE 2 — Config schema + router validation

### Task 4: `SurvivalConfig` pydantic model + `OptimizerStartReq.survival_config`

**Files:**
- Modify: `backend/app/schemas.py:123-149` (OptimizerStartReq) — add the field + a model above it.
- Test: `tests/test_survival_contract.py`

- [ ] **Step 1: Write the failing contract test**

```python
# tests/test_survival_contract.py
from tests.contract_corpus import backend_api_text


def test_survival_config_in_schema_corpus():
    src = backend_api_text()
    assert "class SurvivalConfigReq" in src
    assert "survival_config" in src
    assert "min_equity" in src and "max_drawdown_pct" in src and "max_ror_pct" in src


def test_optimize_start_validates_survival():
    src = backend_api_text()
    # router enforces the hard requirements when survival is enabled
    assert "survival_config" in src
    assert "costs_enabled" in src       # gross-P&L survivors are forbidden
    assert "option_rerank" in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_survival_contract.py -q`
Expected: FAIL — `assert "class SurvivalConfigReq" in src`

- [ ] **Step 3: Implement schema** — insert directly above `class OptimizerStartReq` (schemas.py:123):

```python
# backend/app/schemas.py  (insert above OptimizerStartReq)
class SurvivalConfigReq(BaseModel):
    """Capital-aware survival constraints for the optimizer. Off by default ->
    optimizer behaves exactly as before. See app/survival.py for the gate."""
    enabled: bool = False
    min_equity: float = 0.0            # PRIMARY gate: reject if realized ₹ equity ever <= this
    max_drawdown_pct: float = 35.0     # reject if |peak DD%| exceeds this
    max_ror_pct: float = 5.0           # reject if risk-of-ruin upper-CI exceeds this
    ruin_floor: float = 0.0            # RoR ruin level (₹); 0 <= ruin_floor < capital
    objective: str = "calmar"          # "calmar" | "net_inr"
    min_oos_folds: str = "all"         # "all" | "majority"
```

Then add the field to `OptimizerStartReq` (after `option_config`, schemas.py:149):

```python
    survival_config: Optional[SurvivalConfigReq] = None
```

- [ ] **Step 4: Run the schema half to verify it passes**

Run: `python -m pytest tests/test_survival_contract.py::test_survival_config_in_schema_corpus -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py tests/test_survival_contract.py
git commit -m "feat(schemas): SurvivalConfigReq + OptimizerStartReq.survival_config"
```

---

### Task 5: `/optimize/start` validation (clear 400s)

**Files:**
- Modify: `backend/app/routers/research.py:417-429` (optimize_start)
- Test: `tests/test_survival_contract.py` (extend) + a unit test of the helper

- [ ] **Step 1: Write the failing test** — extract the validation into a pure helper so it is host-testable without motor.

```python
# tests/test_survival_contract.py  (append)
import pytest
from app.survival_validate import validate_survival_request


def _req(**kw):
    base = dict(enabled=True, evaluation_mode="option_rerank", option_config={"enabled": True},
                costs_enabled=True, capital=200_000, ruin_floor=0.0,
                max_drawdown_pct=35.0, max_ror_pct=5.0)
    base.update(kw)
    return base


def test_survival_ok_when_all_requirements_met():
    assert validate_survival_request(**_req()) is None  # None == valid


def test_survival_requires_option_rerank():
    msg = validate_survival_request(**_req(evaluation_mode="spot"))
    assert msg and "option_rerank" in msg


def test_survival_requires_option_execution():
    msg = validate_survival_request(**_req(option_config={"enabled": False}))
    assert msg and "option execution" in msg


def test_survival_requires_costs_enabled():
    msg = validate_survival_request(**_req(costs_enabled=False))
    assert msg and "costs" in msg.lower()


def test_survival_rejects_ruin_floor_ge_capital():
    msg = validate_survival_request(**_req(ruin_floor=200_000))
    assert msg and "ruin_floor" in msg
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_survival_contract.py -k survival_ -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.survival_validate'`

- [ ] **Step 3: Implement the pure validator**

```python
# backend/app/survival_validate.py
"""Pure validation for an optimizer request with survival_config enabled.
Returns an error string (for HTTP 400) or None when valid. Host-testable."""
from __future__ import annotations
from typing import Any, Dict, Optional


def validate_survival_request(
    *, enabled: bool, evaluation_mode: str, option_config: Optional[Dict[str, Any]],
    costs_enabled: bool, capital: float, ruin_floor: float,
    max_drawdown_pct: float, max_ror_pct: float,
) -> Optional[str]:
    if not enabled:
        return None
    if evaluation_mode != "option_rerank":
        return "Survival mode requires evaluation_mode='option_rerank' (the ₹ gate lives in the re-rank)."
    if not (option_config and option_config.get("enabled")):
        return "Survival mode requires option execution enabled (₹ equity is impossible spot-only)."
    if not costs_enabled:
        return "Survival mode requires costs_enabled=true (else risk-of-ruin/Calmar run on gross P&L)."
    if not (0.0 <= float(ruin_floor) < float(capital)):
        return f"ruin_floor must be 0 <= ruin_floor < capital ({capital})."
    if not (0 < float(max_drawdown_pct) <= 100):
        return "max_drawdown_pct must be in (0, 100]."
    if not (0 < float(max_ror_pct) <= 100):
        return "max_ror_pct must be in (0, 100]."
    return None
```

- [ ] **Step 4: Wire it into the route** — in `research.py` `optimize_start` (after the existing rerank_top_k check, ~research.py:428):

```python
# backend/app/routers/research.py  (inside optimize_start, after the rerank_top_k check)
    sc = req.survival_config
    if sc and sc.enabled:
        from app.survival_validate import validate_survival_request
        cap = float(((req.option_config or {}).get("sizing_config") or {}).get("capital", 200_000) or 200_000)
        err = validate_survival_request(
            enabled=True, evaluation_mode=req.evaluation_mode, option_config=req.option_config,
            costs_enabled=req.costs_enabled, capital=cap, ruin_floor=sc.ruin_floor,
            max_drawdown_pct=sc.max_drawdown_pct, max_ror_pct=sc.max_ror_pct,
        )
        if err:
            raise HTTPException(400, err)
```

> NOTE: confirm `req.costs_enabled` exists on `OptimizerStartReq`; if the field is named differently, adapt. Grep: `grep -n costs_enabled backend/app/schemas.py`.

- [ ] **Step 5: Run to verify it passes + commit**

Run: `python -m pytest tests/test_survival_contract.py -q`
Expected: PASS

```bash
git add backend/app/survival_validate.py backend/app/routers/research.py tests/test_survival_contract.py
git commit -m "feat(optimize): validate survival_config (option_rerank+exec+costs+ruin_floor) with 400s"
```

---

## PHASE 3 — Optimizer integration (gate + per-fold OOS + zero-survivor)

### Task 6: Per-candidate per-fold OOS survival helper

**Files:**
- Modify: `backend/app/optimizer.py` (add `_survival_eval_oos` near `_option_rerank`)
- Test: covered indirectly; add a focused unit test that exercises the fold-splitting math on a stub.

This helper mirrors `walk_forward`'s fold split (walkforward.py:27-37), runs the spot backtest on each OOS *test* slice, pairs options with the already-loaded candles, builds the ₹ portfolio per fold, and aggregates.

- [ ] **Step 1: Add the helper** (pure-ish; takes already-loaded `contracts`/`candles_df`):

```python
# backend/app/optimizer.py  (add above _option_rerank)
from app.survival import survival_verdict, SurvivalConfig  # top-of-file import

def _oos_test_slices(df: "pd.DataFrame", n_folds: int, train_pct: float):
    """Yield each walk-forward fold's OOS (test) slice — mirrors walk_forward."""
    if df.empty or len(df) < 200:
        return
    fold_size = len(df) // n_folds
    for k in range(n_folds):
        start = k * fold_size
        end = min((k + 1) * fold_size, len(df))
        if end - start < 100:
            continue
        slice_df = df.iloc[start:end].reset_index(drop=True)
        train_end = int(len(slice_df) * train_pct)
        test_df = slice_df.iloc[train_end:].reset_index(drop=True)
        if len(test_df) < 30:
            continue
        yield k + 1, test_df
```

- [ ] **Step 2: Add the survival evaluator** that pairs options per OOS slice and returns a verdict:

```python
# backend/app/optimizer.py  (add above _option_rerank)
async def _survival_eval_oos(
    db, strategy, df_enriched, merged_params, contracts, candles_df,
    instrument, costs, pretrade, option_cfg, sc: SurvivalConfig,
    n_folds: int = 3, train_pct: float = 0.6,
) -> Dict[str, Any]:
    """Run each OOS fold: spot backtest -> option pairing -> ₹ portfolio. Floor +
    DD% must hold in `min_oos_folds`; RoR runs on the stitched-OOS ₹ series."""
    moneyness = str(option_cfg.get("moneyness") or "atm")
    lots = int(option_cfg.get("lots") or 1)
    capital = float((option_cfg.get("sizing_config") or {}).get("capital", 200_000) or 200_000)
    fold_pass: List[bool] = []
    stitched_curve: List[Dict[str, Any]] = []
    stitched_pnls: List[float] = []
    spot_total = paired_total = 0
    for _fold, test_df in _oos_test_slices(df_enriched, n_folds, train_pct):
        res = await asyncio.to_thread(
            run_backtest, test_df, strategy, merged_params,
            instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade)
        spot_trades = res.get("trades", []) or []
        ebt = _resolve_expiry_by_trade(spot_trades, contracts, option_cfg.get("expiry_date"))
        sim = await asyncio.to_thread(
            simulate_paired_option_trades,
            spot_trades=spot_trades, contracts=contracts, option_candles=candles_df,
            underlying=instrument, moneyness=moneyness, lots=lots,
            entry_max_age_sec=int(option_cfg.get("entry_max_age_sec") or 120),
            exit_max_age_sec=int(option_cfg.get("exit_max_age_sec") or 180),
            expiry_by_trade=ebt, fixed_expiry_date=option_cfg.get("expiry_date"),
            exit_mode=option_cfg.get("exit_mode") or "spot_exit",
            option_target_pts=option_cfg.get("option_target_pts"),
            option_stop_pts=option_cfg.get("option_stop_pts"),
            option_target_pct=option_cfg.get("option_target_pct"),
            option_stop_pct=option_cfg.get("option_stop_pct"),
            cost_config=option_cfg.get("cost_config"), sizing_config=option_cfg.get("sizing_config"),
        )
        port = sim.get("portfolio") or {}
        cov = sim.get("coverage") or {}
        spot_total += int(cov.get("spot_trade_count", 0) or 0)
        paired_total += int(cov.get("paired_trade_count", 0) or 0)
        curve = port.get("curve") or []
        stitched_curve.extend(curve)
        stitched_pnls.extend(float(p["option_pnl_value"]) for p in sim.get("trades", [])
                             if p.get("status") == "PAIRED")
        # per-fold floor + DD checks
        eqs = [c.get("equity_value") for c in curve]
        floor_ok = (min(eqs) > sc.min_equity) if eqs else False
        dd = port.get("max_drawdown_pct")
        dd_ok = (dd is not None) and (abs(float(dd)) <= sc.max_drawdown_pct)
        fold_pass.append(bool(floor_ok and dd_ok))

    folds_ok = (all(fold_pass) if sc.min_oos_folds == "all"
                else sum(fold_pass) > len(fold_pass) / 2) if fold_pass else False
    # stitched-OOS portfolio for RoR + ranking metrics (reuse the rupee curve helper)
    from app.portfolio import build_rupee_equity_curve
    # stitched_curve is per-trade equity points across folds; rebuild a clean curve
    # by replaying the stitched ₹ trades to keep equity continuous from `capital`.
    stitched_port = build_rupee_equity_curve(
        [{"status": "PAIRED", "option_pnl_value": p, "option_exit_ts": c.get("ts")}
         for p, c in zip(stitched_pnls, stitched_curve)], capital=capital)
    verdict = survival_verdict(
        portfolio=stitched_port, trade_pnls=stitched_pnls, cfg=sc,
        coverage={"spot_trade_count": spot_total, "paired_trade_count": paired_total},
        capital=capital)
    verdict["folds_ok"] = folds_ok
    verdict["fold_pass"] = fold_pass
    # survived overall requires BOTH the stitched verdict AND the per-fold rule
    verdict["survived"] = bool(verdict["survived"] and folds_ok)
    return verdict
```

> NOTE: `build_rupee_equity_curve` sorts PAIRED trades by `option_exit_ts`; pass each stitched trade's exit ts so the stitched equity is chronological. Do NOT modify `build_rupee_equity_curve`.

- [ ] **Step 3: Smoke-test the fold splitter**

```python
# tests/test_survival_oos.py
import pandas as pd
from app.optimizer import _oos_test_slices


def test_oos_slices_yields_per_fold_tails():
    df = pd.DataFrame({"ts": range(900), "close": range(900)})
    slices = list(_oos_test_slices(df, n_folds=3, train_pct=0.6))
    assert len(slices) == 3
    for fold_no, test_df in slices:
        assert len(test_df) == 300 - int(300 * 0.6)  # 120-row OOS tail per fold
```

> This test imports `app.optimizer`, which imports motor/optuna — it must be MARKED to run only where those are installed (the container), not on the host. Add `pytestmark = pytest.mark.optimizer` and configure CI to skip `optimizer` on host. If the host lacks optuna, instead extract `_oos_test_slices` into `app/survival.py` (pure) and test it there. **Prefer the extraction** — keeps the splitter host-testable.

- [ ] **Step 4: Run (in container) / verify extraction host-side**

Run (host, if extracted): `python -m pytest tests/test_survival_oos.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/optimizer.py tests/test_survival_oos.py
git commit -m "feat(optimizer): per-fold OOS survival evaluator (_survival_eval_oos)"
```

---

### Task 7: Wire the gate + zero-survivor into the finalize block

**Files:**
- Modify: `backend/app/optimizer.py:884-947` (the rerank + best-promotion + save block)

- [ ] **Step 1: Read `SurvivalConfig` from the payload** — add near the other payload reads in `run_optimization` (optimizer.py:669-675):

```python
        survival = SurvivalConfig.from_dict(payload.get("survival_config"))
```

- [ ] **Step 2: After `_option_rerank` returns `ranked` (optimizer.py:898), gate each finalist when survival is on.** Replace the best-promotion block (optimizer.py:899-912) with:

```python
            survival_summary = None
            if survival.enabled and ranked:
                # Re-enrich per finalist (best indicator periods) and evaluate OOS survival.
                for r in ranked:
                    merged = strategy.merged_params(r["params"])
                    df_enr = get_enriched(merged)
                    v = await _survival_eval_oos(
                        get_db(), strategy, df_enr, merged, _rerank_contracts, _rerank_candles,
                        instrument, costs, pretrade, option_cfg, survival)
                    r["survival"] = v
                survivors = [r for r in ranked
                             if r.get("survival", {}).get("survived")
                             and (r["survival"].get("total_return_pct") or 0) > 0]
                key = (lambda r: r["survival"]["calmar"]) if survival.objective == "calmar" \
                    else (lambda r: r["option_pnl_value"])
                survivors.sort(key=lambda r: (key(r), r["option_pnl_value"]), reverse=True)
                if survivors:
                    best = survivors[0]
                    best_so_far = {"value": key(best), "params": best["params"],
                                   "metrics": {**(best.get("spot_metrics") or {}),
                                               "option_pnl_value": best["option_pnl_value"],
                                               **best["survival"]},
                                   "trial_num": -1}
                    survival_summary = {"survivors": len(survivors), "evaluated": len(ranked),
                                        "objective": survival.objective}
                else:
                    # Zero survivors: do NOT promote a disqualified candidate.
                    reasons = {}
                    for r in ranked:
                        rs = r.get("survival", {}).get("reason", "unknown")
                        reasons[rs] = reasons.get(rs, 0) + 1
                    best_so_far = {"value": -1e9, "params": {}, "metrics": {}, "trial_num": -1}
                    survival_summary = {"survivors": 0, "evaluated": len(ranked),
                                        "reason_counts": reasons,
                                        "suggestions": ["loosen max_drawdown_pct or max_ror_pct",
                                                        "widen parameter bounds / rerank_top_k",
                                                        "extend the date range for more OOS trades"]}
            elif ranked and ranked[0]["paired_trade_count"] > 0:
                best = ranked[0]
                best_so_far = {
                    "value": best["option_pnl_value"], "params": best["params"],
                    "metrics": {**(best.get("spot_metrics") or {}),
                                "option_pnl_value": best["option_pnl_value"],
                                "option_pnl_pts": best["option_pnl_pts"],
                                "option_win_rate": best["option_win_rate"],
                                "paired_trade_count": best["paired_trade_count"]},
                    "trial_num": -1}
```

> The `_rerank_contracts`/`_rerank_candles` are the contracts + candle frame loaded inside `_option_rerank`. **Refactor `_option_rerank` to also return them** (return `ranked, contracts, candles_df`) so the survival evaluator reuses the single load. Update the call at optimizer.py:894 to unpack the tuple. This avoids a second multi-million-row option-candle load.

- [ ] **Step 3: Add `survival_summary` to the rerank_info and the finished doc.** In `rerank_info` (optimizer.py:913) add `"survival_summary": survival_summary`. In `finished` (optimizer.py:953) add `"survival_summary": survival_summary`.

- [ ] **Step 4: Make `final_status` survival-aware** — replace optimizer.py:951:

```python
        if survival.enabled and survival_summary is not None and survival_summary.get("survivors") == 0:
            final_status = "done_no_survivor"
        else:
            final_status = "cancelled" if cancelled_flag and completed < n_trials else "done"
```

- [ ] **Step 5: Verify byte-identical when OFF + container test**

Run (container): `docker compose exec backend python -m pytest tests -q`
Then a manual optimizer run with `survival_config.enabled=false` must produce the same job doc shape as before (no `survival` keys populated). Spot-check via `GET /optimize/jobs/{id}`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/optimizer.py
git commit -m "feat(optimizer): survival gate + zero-survivor handling in finalize (default-off)"
```

---

## PHASE 4 — Frontend (Optimizer setup + results)

### Task 8: API passthrough + Survivability setup panel

**Files:**
- Modify: `frontend/src/lib/api.js` (ensure the optimize payload forwards `survival_config` — if it spreads the config object, no change needed; verify)
- Modify: `frontend/src/pages/Optimizer.jsx` (setup panel + state)

- [ ] **Step 1: Read the existing optimize setup panel** to follow its pattern.

Run: `grep -n "evaluation_mode\|rerank_top_k\|option_config\|startOptimize\|setConfig" frontend/src/pages/Optimizer.jsx`

- [ ] **Step 2: Add survival state + payload field.** Where the optimize config object is assembled, add:

```jsx
// in the config state default
survival_config: { enabled: false, min_equity: 0, max_drawdown_pct: 35,
                   max_ror_pct: 5, ruin_floor: 0, objective: "calmar", min_oos_folds: "all" },
```

And include `survival_config: config.survival_config` in the POST payload to `/optimize/start`.

- [ ] **Step 3: Add the Survivability panel** (mirror the existing option-execution panel styling). Render only when `evaluation_mode === "option_rerank"`:

```jsx
{config.evaluation_mode === "option_rerank" && (
  <div className="panel">
    <label><input type="checkbox"
      checked={config.survival_config.enabled}
      onChange={e => setSurvival({ enabled: e.target.checked })} /> Survival mode</label>
    {config.survival_config.enabled && (
      <>
        <p className="hint">Requires option execution + costs ON. Gates finalists on the
           ₹ equity curve, evaluated per walk-forward OOS fold.</p>
        <NumberField label="Equity floor (₹)" value={config.survival_config.min_equity}
          onChange={v => setSurvival({ min_equity: v })} />
        <NumberField label="Max drawdown %" value={config.survival_config.max_drawdown_pct}
          onChange={v => setSurvival({ max_drawdown_pct: v })} />
        <NumberField label="Max risk-of-ruin %" value={config.survival_config.max_ror_pct}
          onChange={v => setSurvival({ max_ror_pct: v })} />
        <SelectField label="Objective" value={config.survival_config.objective}
          options={[["calmar", "Risk-adjusted (Calmar)"], ["net_inr", "Total ₹"]]}
          onChange={v => setSurvival({ objective: v })} />
      </>
    )}
  </div>
)}
```

With a `setSurvival` helper: `const setSurvival = (patch) => setConfig(c => ({ ...c, survival_config: { ...c.survival_config, ...patch } }));` (use the existing field components; if `NumberField`/`SelectField` don't exist, use the same raw inputs the option panel uses).

- [ ] **Step 4: Manual verify** — toggle survival on with option execution OFF → the run should 400 with the validation message; with everything on, it should start.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Optimizer.jsx frontend/src/lib/api.js
git commit -m "feat(optimizer-ui): Survivability setup panel + survival_config payload"
```

---

### Task 9: Results — survival badges + return-vs-drawdown scatter

**Files:**
- Modify: `frontend/src/pages/Optimizer.jsx` (results/rerank table + a scatter)

- [ ] **Step 1: Read the rerank results renderer.**

Run: `grep -n "rerank\|ranked\|results\|recharts\|Scatter" frontend/src/pages/Optimizer.jsx`

- [ ] **Step 2: Render the survival verdict per finalist** in the rerank table (when `r.survival` present):

```jsx
{r.survival && (
  <span className={r.survival.survived ? "badge badge-green" : "badge badge-red"}>
    {r.survival.survived
      ? `Survived · Calmar ${r.survival.calmar} · DD ${Math.abs(r.survival.max_dd_pct)}% · RoR ${r.survival.ror_pct}%`
      : `Disqualified · ${r.survival.reason}`}
  </span>
)}
```

- [ ] **Step 3: Add the return-vs-drawdown scatter** (recharts is already a dependency — confirm with the grep above). Plot every finalist: x = `|max_dd_pct|`, y = `total_return_pct`, color = survived/disqualified, so the user can pick the knee:

```jsx
<ScatterChart width={460} height={300}>
  <XAxis type="number" dataKey="dd" name="Max DD %" />
  <YAxis type="number" dataKey="ret" name="Return %" />
  <Tooltip />
  <Scatter data={ranked.filter(r => r.survival).map(r => ({
    dd: Math.abs(r.survival.max_dd_pct || 0),
    ret: r.survival.total_return_pct || 0,
    fill: r.survival.survived ? "#22c55e" : "#ef4444",
  }))} />
</ScatterChart>
```

- [ ] **Step 4: Show the zero-survivor message** when `job.survival_summary?.survivors === 0`:

```jsx
{job.survival_summary?.survivors === 0 && (
  <div className="warn">No strategy survived your constraints.
    {job.survival_summary.suggestions?.map(s => <li key={s}>{s}</li>)}
  </div>
)}
```

- [ ] **Step 5: Build + manual verify + commit**

```bash
cd frontend && npm run build
```

Run an `option_rerank` optimization with survival on; confirm badges + scatter render and a 0-survivor run shows the honest message.

```bash
git add frontend/src/pages/Optimizer.jsx
git commit -m "feat(optimizer-ui): survival badges + return-vs-drawdown scatter + 0-survivor message"
```

---

## PHASE 5 — Integration verification

### Task 10: Full-stack smoke + regression

- [ ] **Step 1: Rebuild containers** (backend changed):

```bash
docker compose up -d --build
```

- [ ] **Step 2: Backend regression** — confirm nothing broke and the new tests pass in the container:

```bash
docker compose exec backend python -m pytest tests -q
```
Expected: all prior tests PASS + new survival tests PASS.

- [ ] **Step 3: OFF-path byte-identical check** — run an `option_rerank` optimization with `survival_config.enabled=false`; confirm the job doc has no populated `survival`/`survival_summary` and the chosen best matches the pre-change behavior.

- [ ] **Step 4: ON-path end-to-end** — run with survival ON over a date range known to contain a blow-up config; confirm (a) blow-up configs are `Disqualified · equity_floor`, (b) a survivor with `total_return_pct>0` is promoted, (c) a deliberately tight `max_ror_pct` yields `done_no_survivor` with suggestions.

- [ ] **Step 5: Update CHANGELOG + HANDOFF** and commit:

```bash
git add CHANGELOG.md docs/HANDOFF.md
git commit -m "docs: record survivable-optimization (piece 1) + verification"
```

---

## Self-Review Notes (author)

- **Spec coverage:** absolute floor (Task 3) ✓; DD% magnitude (Task 3) ✓; per-day-bootstrap RoR + CI fail-closed (Task 2) ✓; per-fold OOS (Task 6) ✓; configurable Calmar/net-₹ objective (Tasks 4,7) ✓; require `total_return_pct>0` to promote (Task 7) ✓; zero-survivor honest path (Task 7,9) ✓; validation 400s incl. costs (Task 5) ✓; reuse `sim['portfolio']`/`trades`, no signature change (Tasks 6,7) ✓; default-off byte-identical (Tasks 7,10) ✓; frontend setup+results+scatter (Tasks 8,9) ✓; determinism seed (Tasks 2,3) ✓.
- **Open implementation risks to watch:** (1) extract `_oos_test_slices` into `survival.py` if the host lacks optuna so it stays host-testable; (2) refactor `_option_rerank` to return its loaded `contracts`/`candles_df` so the survival evaluator reuses the single candle load (avoid a 2nd multi-million-row query); (3) confirm `OptimizerStartReq.costs_enabled` exists (grep) before wiring Task 5; (4) confirm recharts is imported in Optimizer.jsx before Task 9's scatter.
