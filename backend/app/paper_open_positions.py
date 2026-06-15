"""Pure shaping of OPEN paper trades into a live open-positions view: unrealized
P&L computed from the latest tick at request time, with a persisted-mark fallback
when no fresh tick exists. No DB access — the router supplies the rows + lookup."""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional


def _live_price(tick_lookup, key: str) -> Optional[float]:
    if not key:
        return None
    tick = tick_lookup(key)
    if not tick or tick.get("last_price") in (None, ""):
        return None
    try:
        p = float(tick["last_price"])
    except (TypeError, ValueError):
        return None
    return p if p > 0 else None


def build_open_positions(
    trades: List[Dict[str, Any]],
    *,
    latest_tick_lookup: Callable[[str], Optional[Dict[str, Any]]],
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    open_mtm = 0.0
    for t in trades:
        key = str(t.get("instrument_key") or "")
        qty = float(t.get("quantity") or 0)
        entry = float(t.get("entry_price") or 0)
        live = _live_price(latest_tick_lookup, key)
        stale = live is None
        if live is not None:
            # live tick present -> P&L is live-computed (qty 0 -> 0, consistent with live_stale=False)
            unreal = round((live - entry) * qty, 2)
        else:
            unreal = round(float(t.get("unrealized_pnl") or 0), 2)
        premium = live if live is not None else (
            float(t.get("last_price") or t.get("entry_price") or 0) or None)
        stop = t.get("stop_price")
        target = t.get("target_price")
        items.append({
            "id": t.get("id"),
            "instrument_key": key,
            "deployment_name": t.get("deployment_name"),
            "direction": t.get("direction"),
            "entry_price": entry,
            "live_premium": premium,
            "live_stale": stale,
            "unrealized_pnl": unreal,
            "dist_to_stop": (round(float(premium) - float(stop), 2)
                             if premium is not None and stop is not None else None),
            "dist_to_target": (round(float(target) - float(premium), 2)
                               if premium is not None and target is not None else None),
            "created_at": t.get("created_at"),
            "mfe_pts": t.get("mfe_pts"), "mae_pts": t.get("mae_pts"),
        })
        open_mtm += unreal
    return {"items": items, "open_mtm": round(open_mtm, 2), "count": len(items)}
