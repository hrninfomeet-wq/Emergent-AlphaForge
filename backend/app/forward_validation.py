"""Pre-registered forward-validation policy for a Rs 200,000 account.

The statistical unit is the trading session, not individual intraday trades.
Signals within a day are correlated, so uncertainty and annual impairment risk
use moving blocks of consecutive daily P&L.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, Iterable, List, Optional

import numpy as np


POLICY = {
    "capital": 200_000.0,
    "ruin_floor": 100_000.0,
    # The user supplied a 25% *monthly* tolerance.  We also keep the whole-
    # forward-record peak drawdown below the same ceiling; that is deliberately
    # stricter and prevents a sequence of individually tolerable months from
    # compounding into an unacceptable impairment.
    "max_monthly_drawdown_pct": 25.0,
    "max_drawdown_pct": 25.0,
    "max_annual_ruin_upper_pct": 30.0,
    "min_plumbing_sessions": 10,
    "min_plumbing_trades": 20,
    "min_forward_sessions": 60,
    "min_forward_trades": 120,
    "min_positive_ten_session_blocks": 4,
    "min_option_coverage": 0.95,
    "block_length": 5,
    "annual_sessions": 252,
}


def _moving_block_sample(
    values: List[float], length: int, block_length: int, rng: random.Random
) -> List[float]:
    if not values or length <= 0:
        return []
    n = len(values)
    block = max(1, min(int(block_length), n))
    out: List[float] = []
    while len(out) < length:
        start = rng.randrange(n)
        out.extend(values[(start + j) % n] for j in range(block))
    return out[:length]


def _wilson_upper(events: int, total: int, z: float = 1.6448536269514722) -> float:
    """One-sided 95% Wilson upper bound for a binomial probability."""
    if total <= 0:
        return 1.0
    p = events / total
    den = 1 + (z * z / total)
    centre = p + z * z / (2 * total)
    radius = z * math.sqrt((p * (1 - p) / total) + z * z / (4 * total * total))
    return min(1.0, (centre + radius) / den)


def block_bootstrap_evidence(
    daily_pnl: Iterable[float],
    *,
    capital: float = POLICY["capital"],
    ruin_floor: float = POLICY["ruin_floor"],
    paths: int = 2_000,
    seed: int = 20260720,
    block_length: int = POLICY["block_length"],
    annual_sessions: int = POLICY["annual_sessions"],
) -> Dict[str, Any]:
    """Daily-mean CI plus 252-session impairment probability and upper bound."""
    values = [float(v) for v in daily_pnl]
    if not values:
        return {
            "sample_sessions": 0, "daily_mean": 0.0,
            "daily_mean_ci95": [None, None],
            "annual_ruin_probability": None,
            "annual_ruin_upper95": None,
        }
    rng = random.Random(seed)
    n_paths = max(200, int(paths))
    boot_means = []
    ruin_events = 0
    for _ in range(n_paths):
        same_horizon = _moving_block_sample(values, len(values), block_length, rng)
        boot_means.append(float(np.mean(same_horizon)))
        annual = _moving_block_sample(values, annual_sessions, block_length, rng)
        equity = float(capital)
        ruined = False
        for pnl in annual:
            equity += pnl
            if equity <= ruin_floor:
                ruined = True
                break
        ruin_events += int(ruined)
    lower, upper = np.percentile(np.asarray(boot_means), [2.5, 97.5])
    observed = ruin_events / n_paths
    return {
        "sample_sessions": len(values),
        "block_length": min(max(1, int(block_length)), len(values)),
        "paths": n_paths,
        "annual_sessions": int(annual_sessions),
        "capital": float(capital),
        "ruin_floor": float(ruin_floor),
        "daily_mean": round(float(np.mean(values)), 2),
        "daily_mean_ci95": [round(float(lower), 2), round(float(upper), 2)],
        "annual_ruin_probability": round(observed, 4),
        "annual_ruin_upper95": round(_wilson_upper(ruin_events, n_paths), 4),
    }


def max_drawdown_pct(daily_pnl: Iterable[float], capital: float) -> float:
    equity = float(capital)
    peak = equity
    worst = 0.0
    for pnl in daily_pnl:
        equity += float(pnl)
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100)
    return round(worst, 3)


def max_calendar_month_drawdown_pct(
    daily_pnl: Iterable[float],
    session_dates: Optional[Iterable[str]],
    capital: float,
) -> float:
    """Worst within-calendar-month equity drawdown.

    Month boundaries reset the local high-water mark but not account equity.
    When dates are unavailable (pure unit calls or legacy callers), treat the
    supplied record as one month so the check still fails closed rather than
    disappearing.
    """
    values = [float(v) for v in daily_pnl]
    dates = list(session_dates or [])
    if len(dates) != len(values):
        dates = ["record"] * len(values)
    equity = float(capital)
    month = None
    month_peak = equity
    worst = 0.0
    for day, pnl in zip(dates, values):
        current_month = str(day)[:7] if len(str(day)) >= 7 else "record"
        if current_month != month:
            month = current_month
            month_peak = equity
        equity += pnl
        month_peak = max(month_peak, equity)
        if month_peak > 0:
            worst = max(worst, (month_peak - equity) / month_peak * 100)
    return round(worst, 3)


def evaluate_forward_promotion(
    *,
    daily_pnl: Iterable[float],
    complete_sessions: int,
    closed_trades: int,
    option_coverage: float,
    eod_violation_count: int,
    capital_enforced: bool,
    config_hash: str,
    lots: int,
    session_dates: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    values = [float(v) for v in daily_pnl]
    stats = block_bootstrap_evidence(values)
    ten_session_blocks = [sum(values[i:i + 10]) for i in range(0, len(values), 10) if len(values[i:i + 10]) == 10]
    positive_blocks = sum(1 for value in ten_session_blocks if value > 0)
    dd_pct = max_drawdown_pct(values, POLICY["capital"])
    monthly_dd_pct = max_calendar_month_drawdown_pct(
        values, session_dates, POLICY["capital"])

    plumbing_checks = {
        "sessions": complete_sessions >= POLICY["min_plumbing_sessions"],
        "trades": closed_trades >= POLICY["min_plumbing_trades"],
        "capital_enforced": bool(capital_enforced),
        "one_lot": int(lots or 0) == 1,
        "config_frozen": bool(config_hash),
        "no_eod_violations": int(eod_violation_count or 0) == 0,
    }
    promotion_checks = {
        **plumbing_checks,
        "forward_sessions": complete_sessions >= POLICY["min_forward_sessions"],
        "forward_trades": closed_trades >= POLICY["min_forward_trades"],
        "positive_daily_mean_ci": (
            stats["daily_mean_ci95"][0] is not None and stats["daily_mean_ci95"][0] > 0
        ),
        "positive_ten_session_blocks": positive_blocks >= POLICY["min_positive_ten_session_blocks"],
        "option_coverage": float(option_coverage or 0) >= POLICY["min_option_coverage"],
        "drawdown": dd_pct <= POLICY["max_drawdown_pct"],
        "monthly_drawdown": monthly_dd_pct <= POLICY["max_monthly_drawdown_pct"],
        "annual_ruin": (
            stats["annual_ruin_upper95"] is not None
            and stats["annual_ruin_upper95"] < POLICY["max_annual_ruin_upper_pct"] / 100
        ),
    }
    failed = [name for name, passed in promotion_checks.items() if not passed]
    return {
        "phase": "promotion_ready" if not failed else (
            "plumbing_ready" if all(plumbing_checks.values()) else "collecting"
        ),
        "promotion_allowed": not failed,
        "policy": dict(POLICY),
        "plumbing_checks": plumbing_checks,
        "promotion_checks": promotion_checks,
        "failed_checks": failed,
        "statistics": {
            **stats,
            "max_drawdown_pct": dd_pct,
            "max_monthly_drawdown_pct": monthly_dd_pct,
            "ten_session_blocks": len(ten_session_blocks),
            "positive_ten_session_blocks": positive_blocks,
            "option_coverage": round(float(option_coverage or 0), 4),
            "eod_violation_count": int(eod_violation_count or 0),
            "lots": int(lots or 0),
        },
    }
