"""Black-Scholes option pricing + Greeks + implied-vol solve (pure, no I/O).

Flattrade exposes no market IV (GetOptionChain has none; GetOptionGreek consumes
volatility as an input), so IV is solved from the live premium and the Greeks are
derived from the same model. All functions are pure + synchronous (no broker),
fully unit-testable. norm.cdf via math.erf — no scipy.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

RISK_FREE_RATE = 0.065          # India ~1y T-bill; module default
IV_MIN = 0.01                   # 1%   solver clamp floor
IV_MAX = 5.0                    # 500% solver clamp ceiling
INTRADAY_FLOOR_DAYS = 0.25      # floor TTE so 0DTE never divides by zero
_LOW_VEGA = 1e-4                # vega below this → IV unreliable (deep ITM/OTM)
_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _d1_d2(spot: float, strike: float, t: float, rate: float, vol: float) -> Tuple[float, float]:
    vsqrt = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t) / vsqrt
    return d1, d1 - vsqrt


def bs_price(spot, strike, t, rate, vol, is_call) -> float:
    d1, d2 = _d1_d2(spot, strike, t, rate, vol)
    disc = math.exp(-rate * t)
    if is_call:
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_delta(spot, strike, t, rate, vol, is_call) -> float:
    d1, _ = _d1_d2(spot, strike, t, rate, vol)
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


def bs_gamma(spot, strike, t, rate, vol) -> float:
    d1, _ = _d1_d2(spot, strike, t, rate, vol)
    return _norm_pdf(d1) / (spot * vol * math.sqrt(t))


def bs_vega(spot, strike, t, rate, vol) -> float:
    """∂Price/∂vol per 1.0 (100%) change in vol."""
    d1, _ = _d1_d2(spot, strike, t, rate, vol)
    return spot * _norm_pdf(d1) * math.sqrt(t)


def bs_theta_per_year(spot, strike, t, rate, vol, is_call) -> float:
    d1, d2 = _d1_d2(spot, strike, t, rate, vol)
    disc = math.exp(-rate * t)
    term1 = -(spot * _norm_pdf(d1) * vol) / (2.0 * math.sqrt(t))
    if is_call:
        return term1 - rate * strike * disc * _norm_cdf(d2)
    return term1 + rate * strike * disc * _norm_cdf(-d2)


def _intrinsic(spot, strike, t, rate, is_call) -> float:
    disc = math.exp(-rate * t)
    return max(spot - strike * disc, 0.0) if is_call else max(strike * disc - spot, 0.0)


def implied_vol(premium, spot, strike, t, rate, is_call) -> Tuple[Optional[float], str]:
    """Solve IV from a market premium → (iv|None, confidence).

    Newton on vega with a bisection fallback, clamped to [IV_MIN, IV_MAX].
    (None, "none") when premium <= 0 / below intrinsic / unsolvable.
    confidence == "low" when vega at the solution is tiny (deep ITM/OTM).
    """
    if not (premium > 0.0 and spot > 0.0 and strike > 0.0 and t > 0.0):
        return None, "none"
    if premium < _intrinsic(spot, strike, t, rate, is_call) - 1e-6:
        return None, "none"

    vol = 0.3
    for _ in range(50):
        diff = bs_price(spot, strike, t, rate, vol, is_call) - premium
        # Converge in PRICE to the numerical fixpoint. A coarse 1e-6 price break
        # leaves a large IV error on thin-vega strikes (a 7-day deep-wing option
        # has vega ~1e-4, so a 1e-6 price slack is ~5e-3 of vol — bigger than the
        # 2e-3 round-trip tolerance). 1e-9 lands IV to ~1e-6 there. (Real-money:
        # see tests/test_greeks.py::test_iv_roundtrip.)
        if abs(diff) < 1e-9:
            break
        vega = bs_vega(spot, strike, t, rate, vol)
        if vega < _LOW_VEGA:
            break
        vol -= diff / vega
        if vol <= IV_MIN or vol >= IV_MAX:
            vol = min(max(vol, IV_MIN), IV_MAX)
            break

    if abs(bs_price(spot, strike, t, rate, vol, is_call) - premium) > 1e-3:
        lo, hi = IV_MIN, IV_MAX
        plo = bs_price(spot, strike, t, rate, lo, is_call) - premium
        for _ in range(100):
            mid = 0.5 * (lo + hi)
            pmid = bs_price(spot, strike, t, rate, mid, is_call) - premium
            vol = mid
            if abs(pmid) < 1e-6:
                break
            if (plo < 0.0) == (pmid < 0.0):
                lo, plo = mid, pmid
            else:
                hi = mid

    vol = min(max(vol, IV_MIN), IV_MAX)
    # Reject a premium the model can't reproduce within [IV_MIN, IV_MAX] (stale /
    # crossed / above-no-arb-max quote): a clamped non-solution must NOT be returned
    # as a confident IV — it would silently corrupt the net Greeks. Skip instead.
    if abs(bs_price(spot, strike, t, rate, vol, is_call) - premium) > max(1e-2, 1e-3 * premium):
        return None, "none"
    conf = "low" if bs_vega(spot, strike, t, rate, vol) < _LOW_VEGA else "ok"
    return vol, conf


def compute_greeks(spot, strike, t_years, premium, is_call, rate: float = RISK_FREE_RATE) -> Optional[dict]:
    """IV-from-premium → {iv, delta, gamma, theta_per_day, vega, confidence} or None."""
    if not (spot > 0.0 and strike > 0.0 and t_years > 0.0 and premium > 0.0):
        return None
    iv, conf = implied_vol(premium, spot, strike, t_years, rate, is_call)
    if iv is None:
        return None
    return {
        "iv": iv,
        "delta": bs_delta(spot, strike, t_years, rate, iv, is_call),
        "gamma": bs_gamma(spot, strike, t_years, rate, iv),
        "theta_per_day": bs_theta_per_year(spot, strike, t_years, rate, iv, is_call) / 365.0,
        "vega": bs_vega(spot, strike, t_years, rate, iv),
        "confidence": conf,
    }
