"""Aggregate live option Greeks into a portfolio net-Δ / net-Θ summary.

Pure orchestration: the broker quote + contract resolution are injected (async),
so this is fully testable with no network. Math lives in app/live/greeks.py.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.live.greeks import INTRADAY_FLOOR_DAYS, RISK_FREE_RATE, compute_greeks


def _to_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _signed_netqty(pos: Dict[str, Any]) -> int:
    inner = pos.get("position") if isinstance(pos.get("position"), dict) else {}
    for src in (inner.get("netqty"), pos.get("netqty"), pos.get("qty")):
        n = _to_float(src)
        if n is not None:
            return int(n)
    return 0


def _premium_from_quote(q: Dict[str, Any]) -> Optional[float]:
    bp1, sp1 = _to_float(q.get("bp1")), _to_float(q.get("sp1"))
    if bp1 and sp1 and bp1 > 0.0 and sp1 > 0.0:
        return 0.5 * (bp1 + sp1)
    return _to_float(q.get("lp"))


async def _spot_from_quote(q: Dict[str, Any], get_quote_fn) -> Optional[float]:
    spot = _to_float(q.get("sptprc"))
    if spot is not None and spot > 0.0:
        return spot
    und_tk, und_exch = q.get("und_tk"), q.get("und_exch")
    if und_tk and und_exch:
        try:
            uq = await get_quote_fn(str(und_exch), str(und_tk))
        except Exception:
            uq = {}
        return _to_float((uq or {}).get("lp"))
    return None


async def compute_portfolio_greeks(
    positions: List[Dict[str, Any]],
    *,
    get_quote_fn: Callable[[str, str], Awaitable[Dict[str, Any]]],
    resolve_contract_fn: Callable[[str, str], Awaitable[Optional[Tuple[float, str, bool, str]]]],
    today: date,
    spot_fallback: Optional[float] = None,
    rate: float = RISK_FREE_RATE,
) -> Dict[str, Any]:
    net_delta = 0.0
    net_theta = 0.0
    n_computed = 0
    n_skipped = 0
    per_position: List[Dict[str, Any]] = []

    for pos in positions or []:
        tsym = str(pos.get("tsym") or "")
        exch = str(pos.get("exch") or "")
        netqty = _signed_netqty(pos)
        if not tsym or not exch or netqty == 0:
            n_skipped += 1
            continue

        try:
            contract = await resolve_contract_fn(tsym, exch)
        except Exception:
            contract = None
        if not contract:
            n_skipped += 1
            continue
        strike, expiry_iso, is_call, token = contract

        try:
            q = await get_quote_fn(exch, str(token))
        except Exception:
            q = {}
        q = q or {}
        premium = _premium_from_quote(q)
        spot = await _spot_from_quote(q, get_quote_fn)
        if spot is None:
            spot = spot_fallback
        if premium is None or spot is None:
            n_skipped += 1
            continue

        try:
            days = (date.fromisoformat(str(expiry_iso)) - today).days
        except (TypeError, ValueError):
            n_skipped += 1
            continue
        t_years = max(float(days), INTRADAY_FLOOR_DAYS) / 365.0

        g = compute_greeks(spot, strike, t_years, premium, is_call, rate=rate)
        if g is None:
            n_skipped += 1
            continue

        net_delta += g["delta"] * netqty
        net_theta += g["theta_per_day"] * netqty
        n_computed += 1
        per_position.append({
            "tsym": tsym, "netqty": netqty, "spot": spot, "premium": premium,
            "iv": g["iv"], "delta": g["delta"], "theta_per_day": g["theta_per_day"],
            "confidence": g["confidence"],
        })

    return {
        "net_delta_rupees_per_point": net_delta,
        "net_theta_rupees_per_day": net_theta,
        "n_computed": n_computed,
        "n_skipped": n_skipped,
        "positions": per_position,
    }
