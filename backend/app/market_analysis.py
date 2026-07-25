"""Pure market-analysis primitives.

These functions compose the deterministic market-analysis payload used by the
live/paper trading UI (trend/structure classification, option-chain reads such
as PCR / max-pain / ATM straddle, IV-rank, and S/R level partitioning). Every
function here is a pure function of its inputs: no I/O, no DB, no network, no
FastAPI, no imports from other app modules. That makes the whole module
host-importable and directly unit-testable without booting the app.

Every function is defensive: malformed, missing, or empty input never raises —
it returns the documented "no data" sentinel (None / "flat" / empty list) so
callers can render a graceful fallback instead of crashing a request.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(value: Any, default: float = 0.0) -> float:
    """Coerce to float, returning `default` for None/blank/non-numeric input.

    The option chain is assembled from broker/warehouse rows, so a single
    malformed field (e.g. an OI of "" or "n/a") must degrade to 0 rather than
    raise out of an endpoint that is rendering a live risk surface.
    """
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out and out not in (float("inf"), float("-inf")) else default


def classify_trend(closes: List[float], *, flat_pct: float = 0.0015) -> str:
    """Classify a closes series as "up", "down", or "flat".

    Combines net return over the window with the majority direction of
    bar-to-bar moves so a single big outlier bar can't flip the label against
    the grain of the series. Fewer than 3 usable points is always "flat".
    """
    xs = [float(c) for c in (closes or []) if c is not None]
    if len(xs) < 3:
        return "flat"
    ret = (xs[-1] - xs[0]) / xs[0] if xs[0] else 0.0
    ups = sum(1 for a, b in zip(xs, xs[1:]) if b > a)
    downs = sum(1 for a, b in zip(xs, xs[1:]) if b < a)
    if ret > flat_pct and ups >= downs:
        return "up"
    if ret < -flat_pct and downs >= ups:
        return "down"
    return "flat"


def classify_structure(*, closes: List[float], adx: Optional[float]) -> Dict[str, Any]:
    """Classify market structure/regime from closes + ADX.

    Returns a dict with keys: label, kind, regime_bucket, buckets, confidence,
    why. `kind` distinguishes trending/range/choppy/transitional; the 5-bucket
    `regime_bucket` (0..4) maps direction x trend-strength onto a bearish ->
    strong-uptrend spectrum for downstream UI coloring.
    """
    direction = classify_trend(closes)

    if adx is None:
        kind = "transitional"
    elif adx >= 25 and direction != "flat":
        kind = "trending"
    elif adx >= 25:
        # Strong ADX but no net direction over the window: the move reversed
        # inside it. Calling that "trending" would contradict the flat label
        # the UI shows next to it, so report it as transitional.
        kind = "transitional"
    elif adx < 20 and direction == "flat":
        kind = "choppy"
    elif adx < 20:
        kind = "range"
    else:
        kind = "transitional"

    trending = kind == "trending"
    if direction == "down" and trending:
        regime_bucket = 0
        label = "BEARISH"
    elif direction == "down":
        regime_bucket = 1
        label = "DOWNTREND"
    elif direction == "flat":
        regime_bucket = 2
        label = "CONSOLIDATION" if kind == "range" else "CHOPPY"
    elif direction == "up" and not trending:
        regime_bucket = 3
        label = "UPTREND"
    else:
        regime_bucket = 4
        label = "STRONG UPTREND"

    xs = [float(c) for c in (closes or []) if c is not None]
    if len(xs) >= 3 and xs[0]:
        total_return = (xs[-1] - xs[0]) / xs[0]
    else:
        total_return = 0.0

    adx_component = 0.5 * min(adx or 0, 40) / 40
    ret_component = 0.5 * min(abs(total_return) / 0.01, 1.0)
    confidence = max(0.0, min(1.0, adx_component + ret_component))
    confidence = round(confidence, 2)

    adx_txt = "n/a" if adx is None else f"{float(adx):.1f}"
    why = f"direction={direction} · ADX={adx_txt} · net {total_return * 100:.2f}%"

    return {
        "label": label,
        "kind": kind,
        "regime_bucket": regime_bucket,
        "buckets": 5,
        "confidence": confidence,
        "why": why,
    }


def put_call_ratio(chain: List[Dict[str, Any]]) -> Optional[float]:
    """Total PE OI / total CE OI across an option-chain snapshot.

    Missing/None OI fields are treated as 0. Returns None (never raises) when
    the chain is empty or total CE OI is 0.
    """
    total_ce = 0.0
    total_pe = 0.0
    for row in chain or []:
        row = row or {}
        total_ce += _f(row.get("ce_oi"))
        total_pe += _f(row.get("pe_oi"))
    if total_ce == 0:
        return None
    return total_pe / total_ce


def max_pain(chain: List[Dict[str, Any]]) -> Optional[float]:
    """Strike minimizing total option-writer payout (classic max-pain).

    For each candidate strike K present in the chain, sums
    ce_oi*max(0, K-strike) + pe_oi*max(0, strike-K) across all rows, and
    returns the K with the lowest total (ties broken by the lowest strike).
    Returns None for an empty chain.
    """
    rows = [r for r in (chain or []) if r]
    if not rows:
        return None
    strikes = sorted({_f(r["strike"]) for r in rows if r.get("strike") is not None})
    if not strikes:
        return None

    best_strike: Optional[float] = None
    best_payout: Optional[float] = None
    for k in strikes:
        payout = 0.0
        for row in rows:
            strike = row.get("strike")
            if strike is None:
                continue
            strike = _f(strike)
            ce_oi = _f(row.get("ce_oi"))
            pe_oi = _f(row.get("pe_oi"))
            payout += ce_oi * max(0.0, k - strike)
            payout += pe_oi * max(0.0, strike - k)
        if best_payout is None or payout < best_payout:
            best_payout = payout
            best_strike = k
    return best_strike


def atm_straddle(chain: List[Dict[str, Any]], *, spot: float) -> Optional[Dict[str, Any]]:
    """ATM straddle premium and implied move %, from the strike nearest spot.

    Returns {"strike", "straddle", "implied_move_pct"}, or None when the
    chain is empty or the nearest-strike row is missing either LTP.
    """
    rows = [r for r in (chain or []) if r and r.get("strike") is not None]
    if not rows:
        return None
    spot_f = _f(spot)
    row = min(rows, key=lambda r: abs(_f(r["strike"]) - spot_f))
    ce_ltp = row.get("ce_ltp")
    pe_ltp = row.get("pe_ltp")
    if ce_ltp is None or pe_ltp is None:
        return None
    straddle = _f(ce_ltp) + _f(pe_ltp)
    implied_move_pct = round(straddle / spot_f * 100, 2) if spot_f else None
    return {
        "strike": _f(row["strike"]),
        "straddle": straddle,
        "implied_move_pct": implied_move_pct,
    }


def iv_rank(
    *,
    current_iv: Optional[float],
    iv_history: Optional[List[float]],
    vix: Optional[float] = None,
    vix_history: Optional[List[float]] = None,
    min_points: int = 8,
) -> Dict[str, Any]:
    """Percentile rank of current IV (or a VIX proxy) within its history.

    Prefers the option chain's own ATM-IV history; falls back to India-VIX
    when the IV history is too thin (fewer than `min_points`); reports
    "unavailable" rather than guessing when neither has enough data.
    """
    iv_hist = iv_history or []
    if current_iv is not None and len(iv_hist) >= min_points:
        rank = sum(1 for v in iv_hist if v <= current_iv) / len(iv_hist)
        return {"rank": round(rank, 3), "source": "atm_iv", "warning": None}

    vix_hist = vix_history or []
    if vix is not None and len(vix_hist) >= min_points:
        rank = sum(1 for v in vix_hist if v <= vix) / len(vix_hist)
        return {"rank": round(rank, 3), "source": "vix_proxy", "warning": "iv_history_thin"}

    return {"rank": None, "source": "unavailable", "warning": "iv_unavailable"}


def levels_from_sr(
    sr: Dict[str, List[float]], *, spot: float, pivot: Optional[float] = None
) -> Dict[str, Any]:
    """Partition context_signals.recent_sr_levels() output around spot.

    `sr` is the {"resistance": [...], "support": [...]} shape produced by
    context_signals.recent_sr_levels. Both lists are pooled and re-split
    strictly by position relative to spot (a "support" level can be above
    spot and vice versa depending on how it was tagged upstream), then each
    side is sorted nearest-first and capped at 3.
    """
    sr = sr or {}
    all_levels: List[float] = []
    for key in ("resistance", "support"):
        for v in sr.get(key) or []:
            if v is None:
                continue
            all_levels.append(_f(v))

    spot = _f(spot)
    supports = sorted((v for v in all_levels if v < spot), reverse=True)[:3]
    resistances = sorted((v for v in all_levels if v > spot))[:3]

    nearest_wall: Optional[Dict[str, Any]] = None
    s0 = supports[0] if supports else None
    r0 = resistances[0] if resistances else None
    if s0 is not None and r0 is not None:
        if abs(spot - s0) <= abs(r0 - spot):
            nearest_wall = {"side": "S", "price": s0}
        else:
            nearest_wall = {"side": "R", "price": r0}
    elif s0 is not None:
        nearest_wall = {"side": "S", "price": s0}
    elif r0 is not None:
        nearest_wall = {"side": "R", "price": r0}

    position_in_range: Optional[float] = None
    if s0 is not None and r0 is not None and r0 > s0:
        pos = (spot - s0) / (r0 - s0)
        position_in_range = round(max(0.0, min(1.0, pos)), 3)

    return {
        "spot": spot,
        "pivot": pivot,
        "supports": supports,
        "resistances": resistances,
        "nearest_wall": nearest_wall,
        "position_in_range": position_in_range,
    }
