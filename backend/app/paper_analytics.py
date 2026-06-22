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


_EXIT_BUCKETS = ("target", "stop", "eod", "manual", "other")


def normalize_exit_reason(reason: Any) -> str:
    # Precedence: target > manual > eod > stop(not time_stop) > other.
    # `manual` is checked BEFORE `eod` because "manual_square_off" contains both
    # "manual" and "square" and is a user-initiated close, not End-of-day.
    # "time_stop" is a time exit, not a price stop, so it is carved out of `stop`.
    r = str(reason or "").lower()
    if "target" in r:
        return "target"
    if "manual" in r:
        return "manual"
    if "eod" in r or "square" in r or "expiry" in r:
        return "eod"
    if "stop" in r and r != "time_stop":
        return "stop"
    return "other"


def exit_reason_query(bucket: str):
    """Mongo condition selecting CLOSED trades whose exit_reason is in `bucket`.

    Buckets mirror normalize_exit_reason's precedence (target > manual > eod >
    stop(not time_stop) > other) by excluding every higher-precedence substring,
    so each raw value matches exactly one bucket query. Returns None for an
    unknown/empty bucket (interpreted by the caller as "no filter").
    """
    def R(pat: str) -> Dict[str, Any]:
        return {"exit_reason": {"$regex": pat, "$options": "i"}}

    def notR(pat: str) -> Dict[str, Any]:
        return {"exit_reason": {"$not": {"$regex": pat, "$options": "i"}}}

    target = R("target")
    manual = {"$and": [R("manual"), notR("target")]}
    eod = {"$and": [R("eod|square|expiry"), notR("target|manual")]}
    stop = {"$and": [
        R("stop"),
        {"exit_reason": {"$ne": "time_stop"}},
        notR("target|manual|eod|square|expiry"),
    ]}
    other = {"$and": [
        {"exit_reason": {"$exists": True, "$ne": None}},
        {"$nor": [target, manual, eod, stop]},
    ]}
    return {"target": target, "manual": manual, "eod": eod, "stop": stop, "other": other}.get(bucket)


def merge_conditions(q: Dict[str, Any], extra: list) -> Dict[str, Any]:
    """Append `extra` conditions to q's `$and` list without clobbering top-level
    keys. No-op when `extra` is empty."""
    if not extra:
        return q
    existing = q.get("$and")
    q["$and"] = (list(existing) if existing else []) + list(extra)
    return q


def _r_multiple(trade: Dict[str, Any], running_pnl: float) -> Optional[float]:
    """Realized/unrealized P&L as a multiple of the trade's initial ₹ risk.
    None when risk wasn't recorded (fixed-lots / legacy trades)."""
    try:
        ra = float(trade.get("risk_amount"))
    except (TypeError, ValueError):
        return None
    if ra <= 0:
        return None
    return round(running_pnl / ra, 2)


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
        "r_multiple": _r_multiple(trade, running),
        "spark": downsample(series, spark_points),
        "duration_s": duration_s,
        "sl": risk.get("stop_price"),
        "tp": risk.get("target_price"),
    }


# ---------------------------------------------------------------------------
# Task 2: period P&L, equity curve, exposure, account roll-up
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def period_pnl(closed_trades: List[Dict[str, Any]], *, now_ms: Optional[int] = None) -> Dict[str, Any]:
    now_ms = now_ms if now_ms is not None else _now_ms()
    today = _ist_day(now_ms)
    now_dt = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc) + IST_OFFSET
    week_start = (now_dt - timedelta(days=now_dt.weekday())).strftime("%Y-%m-%d")
    month_start = now_dt.strftime("%Y-%m-01")
    out = {"today": 0.0, "week": 0.0, "month": 0.0, "lifetime": 0.0}
    wins = losses = 0
    gross_win = gross_loss = 0.0
    for t in closed_trades:
        if str(t.get("status") or "").upper() != "CLOSED":
            continue
        pnl = _f(t.get("realized_pnl"))
        day = _ist_day(t.get("closed_at") or t.get("updated_at"))
        if day is None:
            continue
        out["lifetime"] += pnl
        if day == today:
            out["today"] += pnl
        if day >= week_start:
            out["week"] += pnl
        if day >= month_start:
            out["month"] += pnl
        if pnl > 0:
            wins += 1
            gross_win += pnl
        elif pnl < 0:
            losses += 1
            gross_loss += abs(pnl)
    decided = wins + losses
    out = {k: round(v, 2) for k, v in out.items()}
    out["win_rate"] = round(wins / decided * 100, 1) if decided else None
    out["profit_factor"] = (round(gross_win / gross_loss, 2) if gross_loss > 0
                            else (None if gross_win == 0 else float("inf")))
    out["closed_count"] = decided
    return out


def build_equity_curve(closed_trades: List[Dict[str, Any]],
                       starting_capital: float = DEFAULT_CAPITAL) -> Dict[str, Any]:
    """Realized rupee equity stepped per IST close-day. Mirrors
    portfolio.build_rupee_equity_curve but keyed on realized_pnl/closed_at."""
    daily: Dict[str, float] = {}
    for t in closed_trades:
        if str(t.get("status") or "").upper() != "CLOSED":
            continue
        day = _ist_day(t.get("closed_at") or t.get("updated_at"))
        if day is None:
            continue
        daily[day] = daily.get(day, 0.0) + _f(t.get("realized_pnl"))
    equity = float(starting_capital)
    peak = float(starting_capital)
    max_dd = 0.0
    max_dd_pct = 0.0
    curve: List[Dict[str, Any]] = []
    for day in sorted(daily.keys()):
        equity += daily[day]
        peak = max(peak, equity)
        dd = equity - peak
        max_dd = min(max_dd, dd)
        if peak > 0:
            max_dd_pct = min(max_dd_pct, dd / peak * 100.0)
        curve.append({"day": day, "equity_value": round(equity, 2),
                      "pnl_value": round(daily[day], 2),
                      "drawdown_value": round(dd, 2)})
    net = round(equity - starting_capital, 2)
    return {
        "starting_capital": round(float(starting_capital), 2),
        "account_value_realized": round(equity, 2),
        "net_pnl": net,
        "total_return_pct": round(net / starting_capital * 100, 3) if starting_capital > 0 else 0.0,
        "max_drawdown_value": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 3),
        "curve": curve,
    }


def exposure(open_trades: List[Dict[str, Any]],
             starting_capital: float = DEFAULT_CAPITAL) -> Dict[str, Any]:
    by_instr: Dict[str, float] = {}
    deployed = 0.0
    for t in open_trades:
        cost = _f(t.get("entry_price")) * _f(t.get("quantity"))
        deployed += cost
        key = str(t.get("instrument") or "—")
        by_instr[key] = round(by_instr.get(key, 0.0) + cost, 2)
    return {
        "deployed_capital": round(deployed, 2),
        "deployed_pct": round(deployed / starting_capital * 100, 2) if starting_capital > 0 else 0.0,
        "by_instrument": by_instr,
    }


def build_account_analytics(closed_trades: List[Dict[str, Any]],
                            open_trades: List[Dict[str, Any]],
                            *, starting_capital: float = DEFAULT_CAPITAL,
                            now_ms: Optional[int] = None) -> Dict[str, Any]:
    eq = build_equity_curve(closed_trades, starting_capital)
    open_pnl = round(sum(_f(t.get("unrealized_pnl")) for t in open_trades), 2)
    exp = exposure(open_trades, starting_capital)
    return {
        "starting_capital": eq["starting_capital"],
        "account_value_realized": eq["account_value_realized"],
        "account_value_mtm": round(eq["account_value_realized"] + open_pnl, 2),
        "open_pnl": open_pnl,
        "open_count": len(open_trades),
        "deployed_capital": exp["deployed_capital"],
        "net_pnl": eq["net_pnl"],
        "total_return_pct": eq["total_return_pct"],
        "max_drawdown_value": eq["max_drawdown_value"],
        "max_drawdown_pct": eq["max_drawdown_pct"],
        "equity_curve": eq["curve"],
        "period_pnl": period_pnl(closed_trades, now_ms=now_ms),
        "exposure": exp,
    }


# ---------------------------------------------------------------------------
# Task 3: per-strategy attribution + contribution
# ---------------------------------------------------------------------------

def per_strategy_stats(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        sid = str(t.get("strategy_id") or "—")
        g = groups.setdefault(sid, {
            "strategy_id": sid, "deployment_id": t.get("deployment_id"),
            "net_pnl": 0.0, "closed_trades": 0, "open_count": 0, "open_mtm": 0.0,
            "_wins": 0, "_losses": 0, "_gw": 0.0, "_gl": 0.0, "_hold_s": 0.0,
            "_r_sum": 0.0, "_r_n": 0, "_exit": {b: 0 for b in _EXIT_BUCKETS}, "_exit_n": 0,
        })
        status = str(t.get("status") or "").upper()
        if status == "OPEN":
            g["open_count"] += 1
            g["open_mtm"] += _f(t.get("unrealized_pnl"))
        elif status == "CLOSED":
            pnl = _f(t.get("realized_pnl"))
            g["net_pnl"] += pnl
            g["closed_trades"] += 1
            if pnl > 0:
                g["_wins"] += 1
                g["_gw"] += pnl
            elif pnl < 0:
                g["_losses"] += 1
                g["_gl"] += abs(pnl)
            start = _to_ms(t.get("created_at"))
            end = _to_ms(t.get("closed_at"))
            if start is not None and end is not None:
                g["_hold_s"] += max(0, (end - start) / 1000)
            try:
                ra = float(t.get("risk_amount"))
            except (TypeError, ValueError):
                ra = 0.0
            if ra > 0:
                g["_r_sum"] += pnl / ra
                g["_r_n"] += 1
            g["_exit"][normalize_exit_reason(t.get("exit_reason"))] += 1
            g["_exit_n"] += 1
    total_net = sum(g["net_pnl"] for g in groups.values()) or 0.0
    out: List[Dict[str, Any]] = []
    for g in groups.values():
        decided = g["_wins"] + g["_losses"]
        net = round(g["net_pnl"], 2)
        out.append({
            "strategy_id": g["strategy_id"],
            "deployment_id": g["deployment_id"],
            "net_pnl": net,
            "closed_trades": g["closed_trades"],
            "open_count": g["open_count"],
            "open_mtm": round(g["open_mtm"], 2),
            "win_rate": round(g["_wins"] / decided * 100, 1) if decided else None,
            "profit_factor": (round(g["_gw"] / g["_gl"], 2) if g["_gl"] > 0
                              else (None if g["_gw"] == 0 else float("inf"))),
            "expectancy": round(net / g["closed_trades"], 2) if g["closed_trades"] else None,
            "avg_hold_s": int(g["_hold_s"] / g["closed_trades"]) if g["closed_trades"] else None,
            "contribution_pct": round(net / total_net * 100, 1) if total_net else None,
            "avg_r": round(g["_r_sum"] / g["_r_n"], 2) if g["_r_n"] else None,
            "exit_mix": ({b: round(g["_exit"][b] / g["_exit_n"] * 100) for b in _EXIT_BUCKETS}
                         if g["_exit_n"] else {b: 0 for b in _EXIT_BUCKETS}),
        })
    return sorted(out, key=lambda s: s["net_pnl"], reverse=True)


# ---------------------------------------------------------------------------
# Phase 2 Task 3: forward-vs-backtest drift combiner
# ---------------------------------------------------------------------------

def drift_compare(live: Optional[Dict[str, Any]],
                  baseline: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Forward-vs-backtest drift state. live={win_rate,avg,visible} (session-gated
    forward metrics); baseline={win_rate,avg,params_match} (pinned option-₹ evidence)."""
    if not baseline or not baseline.get("params_match") or baseline.get("win_rate") is None:
        return {"state": "no_baseline"}
    if not live or not live.get("visible"):
        return {"state": "insufficient_sample",
                "base_win_rate": baseline.get("win_rate"),
                "base_avg": (round(float(baseline["avg"]), 2) if baseline.get("avg") is not None else None)}
    lw, bw = live.get("win_rate"), baseline.get("win_rate")
    la, ba = live.get("avg"), baseline.get("avg")
    return {
        "state": "ok",
        "live_win_rate": lw, "base_win_rate": bw,
        "win_rate_delta": round(lw - bw, 1) if lw is not None and bw is not None else None,
        "live_avg": round(la, 2) if la is not None else None,
        "base_avg": round(ba, 2) if ba is not None else None,
        "avg_delta": round(la - ba, 2) if la is not None and ba is not None else None,
    }
