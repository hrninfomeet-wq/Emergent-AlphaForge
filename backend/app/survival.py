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


def daily_from_curve(curve: Sequence[Dict[str, Any]]) -> List[float]:
    """Bucket a rupee equity curve's per-trade pnl_value into per-IST-day totals."""
    by_day: Dict[str, float] = {}
    for pt in curve:
        try:
            ts = int(pt.get("ts"))
            pnl = float(pt.get("pnl_value", 0.0))
        except (TypeError, ValueError, AttributeError):
            continue  # AttributeError: a non-dict curve entry (defensive)
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
    # max_dd_pct is None short-circuits before float(); a NaN max_dd_pct must be
    # rejected here — abs(nan) > cap is False, so it would silently slip the DD gate.
    if (max_dd_pct is None or total_return_pct is None
            or not math.isfinite(float(total_return_pct))
            or not math.isfinite(float(max_dd_pct))):
        return {**base, "reason": "non_finite_metrics"}
    if spot_ct > 0 and (paired_ct / spot_ct) < MIN_COVERAGE:
        return {**base, "low_coverage": True, "reason": "low_coverage"}

    cal = calmar(float(total_return_pct), float(max_dd_pct))
    base["calmar"] = round(cal, 4)

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
