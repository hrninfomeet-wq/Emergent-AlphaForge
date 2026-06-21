"""Pure paper-trade analytics (no DB access). The router supplies trade dicts.

Builds per-trade P&L series + MFE/MAE/running, downsampled sparklines, period
P&L, a rupee equity curve from a configurable starting capital, exposure, and
per-strategy attribution. Mirrors the equity math in app/portfolio.py but keyed
on paper trades' realized_pnl / closed_at."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

IST_OFFSET = timedelta(hours=5, minutes=30)
DEFAULT_CAPITAL = 200_000.0


def _to_ms(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _ist_day(value: Any) -> Optional[str]:
    ms = _to_ms(value)
    if ms is None:
        return None
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc) + IST_OFFSET
    return dt.strftime("%Y-%m-%d")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def pnl_series(trade: Dict[str, Any]) -> List[Dict[str, Any]]:
    """[{t (ms), pnl}] over the trade's life from events[]. OPEN=0, MARK=
    unrealized_pnl, CLOSE=realized_pnl. Falls back to entry->now/exit when no
    events are present."""
    events = trade.get("events") or []
    out: List[Dict[str, Any]] = []
    for e in events:
        t = _to_ms(e.get("at"))
        et = str(e.get("type") or "").upper()
        if et == "OPEN":
            pnl = 0.0
        elif et == "MARK":
            pnl = _f(e.get("unrealized_pnl"))
        elif et == "CLOSE":
            pnl = _f(e.get("realized_pnl"))
        else:
            continue
        if t is not None:
            out.append({"t": t, "pnl": round(pnl, 2)})
    if out:
        return out
    start = _to_ms(trade.get("created_at"))
    end = _to_ms(trade.get("closed_at")) or _to_ms(trade.get("updated_at")) or start
    end_pnl = (_f(trade.get("realized_pnl")) if str(trade.get("status")).upper() == "CLOSED"
               else _f(trade.get("unrealized_pnl")))
    series = []
    if start is not None:
        series.append({"t": start, "pnl": 0.0})
    if end is not None:
        series.append({"t": end, "pnl": round(end_pnl, 2)})
    return series


def downsample(points: List[Dict[str, Any]], n: int = 30) -> List[Dict[str, Any]]:
    """Stride-downsample to <= n points, always preserving the first, last, and
    the global max & min P&L points so the sparkline shape (and MFE/MAE) reads
    true."""
    if len(points) <= n:
        return points
    # Anchor the mandatory special points first.
    vals = [p["pnl"] for p in points]
    special = {0, len(points) - 1, vals.index(max(vals)), vals.index(min(vals))}
    # Fill remaining budget with evenly-spaced stride indices (excluding specials).
    budget = n - len(special)
    if budget > 0:
        step = (len(points) - 1) / (n - 1)
        for i in range(n):
            idx = round(i * step)
            if idx not in special:
                special.add(idx)
                budget -= 1
                if budget == 0:
                    break
    return [points[i] for i in sorted(special)]


def per_trade_analytics(trade: Dict[str, Any], *, now_ms: Optional[int] = None,
                        spark_points: int = 30) -> Dict[str, Any]:
    """Compact per-trade analytics for a blotter row. Prefers stored
    mfe_value/mae_value (set by the live marker) and falls back to the events
    series."""
    series = pnl_series(trade)
    vals = [p["pnl"] for p in series] or [0.0]
    is_closed = str(trade.get("status") or "").upper() == "CLOSED"
    if is_closed:
        running = _f(trade.get("realized_pnl"))
    elif trade.get("unrealized_pnl") is not None:
        running = _f(trade.get("unrealized_pnl"))
    else:
        # Fall back to the last point in the events-derived series.
        running = series[-1]["pnl"] if series else 0.0
    mfe = trade.get("mfe_value")
    mae = trade.get("mae_value")
    mfe_value = _f(mfe) if mfe is not None else max(vals)
    mae_value = _f(mae) if mae is not None else min(vals)
    risk = trade.get("risk") or {}
    start = _to_ms(trade.get("created_at"))
    end = _to_ms(trade.get("closed_at")) or (now_ms if now_ms is not None
                                             else int(datetime.now(timezone.utc).timestamp() * 1000))
    duration_s = max(0, int((end - start) / 1000)) if start is not None else 0
    return {
        "mfe_value": round(mfe_value, 2),
        "mae_value": round(mae_value, 2),
        "running_pnl": round(running, 2),
        "spark": downsample(series, spark_points),
        "duration_s": duration_s,
        "sl": risk.get("stop_price"),
        "tp": risk.get("target_price"),
    }
