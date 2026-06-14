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
