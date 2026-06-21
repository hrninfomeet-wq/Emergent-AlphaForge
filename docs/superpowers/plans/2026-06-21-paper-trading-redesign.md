# Paper Trading redesign — Phase 1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Paper Trading page into an analytics dashboard — configurable-capital account value + equity curve, period P&L, per-strategy stats, and a redesigned sortable blotter with per-trade Max/Min/Now P&L, a sparkline, SL/TP and duration — reusing existing backend data.

**Architecture:** Backend computes, frontend renders. A new pure `app/paper_analytics.py` module (host-tested) builds the equity curve, period P&L, exposure, per-strategy stats and per-trade analytics from existing `paper_trades` docs (incl. the per-minute `events[]` mark history). Thin FastAPI routes in `journals.py` expose it. The React page is split into focused components under `frontend/src/components/paper/` using recharts (already installed) + inline-SVG sparklines.

**Tech Stack:** Python/FastAPI + MongoDB (motor), pytest host tests; React 19 + Tailwind (custom dark tokens) + shadcn/Radix + recharts + lucide + sonner.

**Spec:** [docs/superpowers/specs/2026-06-21-paper-trading-redesign-design.md](../specs/2026-06-21-paper-trading-redesign-design.md)

---

## Confirmed environment facts (override any in-task "confirm at execution" notes)

- **FastAPI app:** `backend/server.py`, `app = FastAPI(...)`, router mounted with `app.include_router(api)` under prefix `/api`. TestClient import (tests add `ROOT/"backend"` to `sys.path`): `from server import app`.
- **`_TRADES_SORT_FIELDS`** is a `set` at `backend/app/runtime.py:897`: `{"updated_at","created_at","closed_at","realized_pnl","entry_price"}`. Add `"mfe_value"`, `"mae_value"`.
- **DB:** MongoDB via motor (`app/db.py` `get_db()`, `serialize_doc`), `MONGO_URL`/`DB_NAME` env. **No `conftest.py` / no test DB fixture exists.** Therefore: pure-function tasks (1–4) use pytest; DB/route tasks (6–8) verify against the running backend via `curl` (start it with the `run` skill) — do NOT write TestClient tests that require a live Mongo.
- **Theme tokens (frontend/src/index.css) — use these exact CSS vars in any inline style (recharts/SVG):**
  - Backgrounds: `var(--bg-0)` `var(--bg-1)` `var(--bg-2)` `var(--bg-3)`
  - Borders: `var(--border-1)` `var(--border-2)`
  - Text: `var(--text-1)` `var(--text-2)` `var(--text-3)`
  - Semantics: `var(--color-success)`=#2ED47A, `var(--color-danger)`=#FF5D5D, `var(--color-info)`=#5AA9FF, `var(--color-warning)`, `var(--color-focus)`
  - Tailwind classes already map to these (`bg-bg-1`, `border-line`, `text-dim`/`text-dimmer`, `text-success`/`text-danger`). In the component code below, replace any `var(--color-bg-2…)`→`var(--bg-2)`, `var(--color-line…)`→`var(--border-1)`, `var(--color-dimmer…)`→`var(--text-3)`.
- **Run the app (for verification):** use the `run` skill. Backend: `uvicorn server:app` from `backend/` (needs Mongo on `localhost:27017`); frontend: `cd frontend && yarn start`. `docker-compose.yml` exists if a full stack is preferred.

## Conventions & ground rules

- **Backend = TDD** (matches the 880-test host suite; see `tests/test_portfolio.py`). Run tests from repo root: `python -m pytest tests/<file>::<test> -v`.
- **Frontend has no jest/RTL suite in use** — do NOT invent one. Build components following existing patterns (Tailwind tokens `bg-bg-0/1/2/3`, `border-line`, `text-dim/dimmer`, `text-success/danger/info`, `font-mono tabular-nums`; `data-testid` hooks). Verify with the `run` skill (launch app) and the `verify` skill (observe behavior).
- **Commits:** the repo rule is "commit only when the user asks." Commit steps below are the intended units; honor the user's per-changeset approval cadence when executing.
- **Branch:** implement on a fresh branch off `main` (unrelated to `feat/option-aware-optimization`). Create it via `superpowers:using-git-worktrees` at execution start, or `git switch -c feat/paper-analytics-redesign main`.
- **Money/colour conventions:** ₹ via the new `fmtINR` (Indian grouping, no locale API); P&L always carries a sign (never colour-only); `font-mono tabular-nums` on numeric cells.

---

## File structure

**Backend**
- Create `backend/app/paper_analytics.py` — pure analytics (no DB): per-trade analytics, downsample, period P&L, equity curve, exposure, per-strategy stats, account roll-up.
- Modify `backend/app/paper_trading.py` — track `mfe_value`/`mae_value` on open marks + close.
- Modify `backend/app/runtime.py` — add `mfe_value`/`mae_value` to `_TRADES_SORT_FIELDS`.
- Modify `backend/app/routers/journals.py` — add `/paper/account-config` (GET/PUT), `/paper/analytics`, `/paper/strategy-stats`; extend `/paper/trades` with `include_analytics`.
- Create `tests/test_paper_analytics.py` — host tests for the pure module.
- Create `tests/test_paper_trading_mfe.py` — host tests for the mark/close MFE/MAE tracking.

**Frontend**
- Modify `frontend/src/lib/fmt.js` — add `fmtINR`, `fmtINRSigned`, `fmtDuration`.
- Modify `frontend/src/lib/api.js` — add `paperAnalytics`, `paperStrategyStats`, `getPaperAccountConfig`, `setPaperAccountConfig`; add `include_analytics` to `listPaperTrades`.
- Create `frontend/src/components/paper/TradeSparkline.jsx` — tiny inline-SVG P&L curve.
- Create `frontend/src/components/paper/AccountHero.jsx` — account value + equity curve + editable starting capital.
- Create `frontend/src/components/paper/PeriodPnlCards.jsx` — Today/Week/Month/Lifetime + win rate + PF.
- Create `frontend/src/components/paper/StrategyStatsTable.jsx` — per-strategy attribution + contribution.
- Create `frontend/src/components/paper/TradeBlotter.jsx` — flat sortable table with new columns.
- Create `frontend/src/components/paper/TradeDetailDrawer.jsx` — expandable per-trade detail.
- Create `frontend/src/components/paper/DeploymentControlStrip.jsx` — extracted from current page (logic unchanged).
- Create `frontend/src/components/paper/PnlCalendar.jsx` — extracted heat-grid + monthly bars.
- Modify `frontend/src/pages/PaperTrading.jsx` — orchestrate fetches + layout; feed-health chip.

---

## Task 1: paper_analytics — per-trade P&L series, downsample, per-trade analytics

**Files:**
- Create: `backend/app/paper_analytics.py`
- Test: `tests/test_paper_analytics.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for pure paper-trade analytics."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.paper_analytics import downsample, pnl_series, per_trade_analytics  # noqa: E402


def _trade(events, **kw):
    base = {
        "id": "t1", "created_at": "2026-06-20T04:48:00+00:00",
        "quantity": 75, "entry_price": 100.0,
        "risk": {"stop_price": 80.0, "target_price": 140.0},
        "status": "OPEN", "events": events,
    }
    base.update(kw)
    return base


def test_pnl_series_from_events():
    t = _trade([
        {"type": "OPEN", "at": "2026-06-20T04:48:00+00:00", "price": 100.0},
        {"type": "MARK", "at": "2026-06-20T04:49:00+00:00", "unrealized_pnl": 750.0},
        {"type": "MARK", "at": "2026-06-20T04:50:00+00:00", "unrealized_pnl": -300.0},
    ])
    s = pnl_series(t)
    assert [round(p["pnl"], 1) for p in s] == [0.0, 750.0, -300.0]


def test_per_trade_analytics_mfe_mae_running():
    t = _trade([
        {"type": "OPEN", "at": "2026-06-20T04:48:00+00:00", "price": 100.0},
        {"type": "MARK", "at": "2026-06-20T04:49:00+00:00", "unrealized_pnl": 750.0},
        {"type": "MARK", "at": "2026-06-20T04:50:00+00:00", "unrealized_pnl": -300.0},
        {"type": "MARK", "at": "2026-06-20T04:51:00+00:00", "unrealized_pnl": 525.0},
    ])
    a = per_trade_analytics(t)
    assert a["mfe_value"] == 750.0
    assert a["mae_value"] == -300.0
    assert a["running_pnl"] == 525.0
    assert a["sl"] == 80.0 and a["tp"] == 140.0
    assert a["duration_s"] >= 180
    assert len(a["spark"]) == 4


def test_per_trade_analytics_prefers_stored_mfe_value():
    t = _trade([], mfe_value=900.0, mae_value=-150.0, unrealized_pnl=400.0)
    a = per_trade_analytics(t)
    assert a["mfe_value"] == 900.0 and a["mae_value"] == -150.0


def test_closed_trade_uses_realized_for_running_and_endpoint():
    t = _trade(
        [
            {"type": "OPEN", "at": "2026-06-20T04:48:00+00:00", "price": 100.0},
            {"type": "MARK", "at": "2026-06-20T04:49:00+00:00", "unrealized_pnl": 600.0},
            {"type": "CLOSE", "at": "2026-06-20T05:00:00+00:00", "realized_pnl": 450.0},
        ],
        status="CLOSED", realized_pnl=450.0, closed_at="2026-06-20T05:00:00+00:00",
    )
    a = per_trade_analytics(t)
    assert a["running_pnl"] == 450.0
    assert a["spark"][-1]["pnl"] == 450.0


def test_downsample_keeps_endpoints_and_extremes():
    pts = [{"t": i, "pnl": v} for i, v in enumerate([0, 5, 9, 3, -7, 2, 8, 1, 4, 6, 0])]
    out = downsample(pts, n=5)
    assert len(out) == 5
    assert out[0] == pts[0] and out[-1] == pts[-1]
    vals = [p["pnl"] for p in out]
    assert 9 in vals and -7 in vals  # global max & min preserved


def test_downsample_passthrough_when_small():
    pts = [{"t": 0, "pnl": 1}, {"t": 1, "pnl": 2}]
    assert downsample(pts, n=30) == pts
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_paper_analytics.py -v`
Expected: FAIL (`ModuleNotFoundError: app.paper_analytics`).

- [ ] **Step 3: Implement the module**

```python
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
    keep_idx = {0, len(points) - 1}
    vals = [p["pnl"] for p in points]
    keep_idx.add(vals.index(max(vals)))
    keep_idx.add(vals.index(min(vals)))
    step = (len(points) - 1) / (n - 1)
    for i in range(n):
        keep_idx.add(round(i * step))
    return [points[i] for i in sorted(keep_idx)]


def per_trade_analytics(trade: Dict[str, Any], *, now_ms: Optional[int] = None,
                        spark_points: int = 30) -> Dict[str, Any]:
    """Compact per-trade analytics for a blotter row. Prefers stored
    mfe_value/mae_value (set by the live marker) and falls back to the events
    series."""
    series = pnl_series(trade)
    vals = [p["pnl"] for p in series] or [0.0]
    is_closed = str(trade.get("status") or "").upper() == "CLOSED"
    running = (_f(trade.get("realized_pnl")) if is_closed else _f(trade.get("unrealized_pnl")))
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
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_paper_analytics.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/paper_analytics.py tests/test_paper_analytics.py
git commit -m "feat(paper): per-trade analytics + sparkline downsample (pure, tested)"
```

---

## Task 2: paper_analytics — period P&L, equity curve, exposure, account roll-up

**Files:**
- Modify: `backend/app/paper_analytics.py`
- Test: `tests/test_paper_analytics.py`

- [ ] **Step 1: Add failing tests**

```python
from app.paper_analytics import (  # noqa: E402
    period_pnl, build_equity_curve, exposure, build_account_analytics,
)

_DAY = 86_400_000
_BASE = 1_750_000_000_000  # fixed ms; tests are deterministic


def _closed(pnl, closed_ms, instrument="NIFTY", entry=100.0, qty=75):
    return {"status": "CLOSED", "realized_pnl": pnl,
            "closed_at": datetime.fromtimestamp(closed_ms / 1000, tz=timezone.utc).isoformat(),
            "instrument": instrument, "entry_price": entry, "quantity": qty}


from datetime import datetime, timezone  # noqa: E402


def test_period_pnl_buckets_today_and_lifetime():
    now = _BASE
    rows = [_closed(1000, now - 1000), _closed(-200, now - _DAY * 3), _closed(500, now - _DAY * 40)]
    p = period_pnl(rows, now_ms=now)
    assert p["today"] == 1000.0
    assert p["lifetime"] == 1300.0
    assert p["win_rate"] == round(2 / 3 * 100, 1)
    assert p["profit_factor"] == round(1500 / 200, 2)


def test_build_equity_curve_from_capital():
    rows = [_closed(1000, _BASE - _DAY * 2), _closed(-500, _BASE - _DAY), _closed(2000, _BASE)]
    c = build_equity_curve(rows, starting_capital=200_000)
    assert c["starting_capital"] == 200_000
    assert c["account_value_realized"] == 202_500.0
    assert c["max_drawdown_value"] <= 0
    assert c["curve"][-1]["equity_value"] == 202_500.0


def test_exposure_pct_and_by_instrument():
    open_trades = [
        {"entry_price": 100.0, "quantity": 75, "instrument": "NIFTY"},
        {"entry_price": 50.0, "quantity": 30, "instrument": "BANKNIFTY"},
    ]
    e = exposure(open_trades, starting_capital=200_000)
    assert e["deployed_capital"] == 9000.0
    assert e["deployed_pct"] == round(9000 / 200_000 * 100, 2)
    assert e["by_instrument"]["NIFTY"] == 7500.0


def test_build_account_analytics_combines_realized_and_mtm():
    rows = [_closed(1000, _BASE)]
    open_trades = [{"entry_price": 100.0, "quantity": 75, "instrument": "NIFTY",
                    "unrealized_pnl": 300.0}]
    a = build_account_analytics(rows, open_trades, starting_capital=200_000, now_ms=_BASE)
    assert a["account_value_realized"] == 201_000.0
    assert a["open_pnl"] == 300.0
    assert a["account_value_mtm"] == 201_300.0
    assert a["deployed_capital"] == 7500.0
    assert "equity_curve" in a and "period_pnl" in a and "exposure" in a
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_paper_analytics.py -k "period or equity or exposure or account" -v`
Expected: FAIL (ImportError on the new names).

- [ ] **Step 3: Implement**

Append to `backend/app/paper_analytics.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_paper_analytics.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/paper_analytics.py tests/test_paper_analytics.py
git commit -m "feat(paper): period P&L, equity curve, exposure, account roll-up"
```

---

## Task 3: paper_analytics — per-strategy stats

**Files:**
- Modify: `backend/app/paper_analytics.py`
- Test: `tests/test_paper_analytics.py`

- [ ] **Step 1: Add failing test**

```python
from app.paper_analytics import per_strategy_stats  # noqa: E402


def test_per_strategy_stats_attribution_and_contribution():
    rows = [
        {"strategy_id": "orr", "deployment_id": "d1", "status": "CLOSED",
         "realized_pnl": 1000.0, "created_at": "2026-06-20T04:00:00+00:00",
         "closed_at": "2026-06-20T04:30:00+00:00"},
        {"strategy_id": "orr", "deployment_id": "d1", "status": "CLOSED",
         "realized_pnl": -200.0, "created_at": "2026-06-20T05:00:00+00:00",
         "closed_at": "2026-06-20T05:20:00+00:00"},
        {"strategy_id": "scalp", "deployment_id": "d2", "status": "OPEN",
         "unrealized_pnl": 300.0},
    ]
    stats = per_strategy_stats(rows)
    by = {s["strategy_id"]: s for s in stats}
    assert by["orr"]["net_pnl"] == 800.0
    assert by["orr"]["closed_trades"] == 2
    assert by["orr"]["win_rate"] == 50.0
    assert by["orr"]["profit_factor"] == 5.0
    assert by["scalp"]["open_count"] == 1
    assert by["scalp"]["open_mtm"] == 300.0
    assert by["orr"]["contribution_pct"] == 100.0  # only strat with realized net
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_paper_analytics.py -k strategy -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** — append to `backend/app/paper_analytics.py`:

```python
def per_strategy_stats(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        sid = str(t.get("strategy_id") or "—")
        g = groups.setdefault(sid, {
            "strategy_id": sid, "deployment_id": t.get("deployment_id"),
            "net_pnl": 0.0, "closed_trades": 0, "open_count": 0, "open_mtm": 0.0,
            "_wins": 0, "_losses": 0, "_gw": 0.0, "_gl": 0.0, "_hold_s": 0.0,
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
        })
    return sorted(out, key=lambda s: s["net_pnl"], reverse=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_paper_analytics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/paper_analytics.py tests/test_paper_analytics.py
git commit -m "feat(paper): per-strategy attribution + contribution stats"
```

---

## Task 4: Track MFE/MAE on the live marker + close

**Files:**
- Modify: `backend/app/paper_trading.py:135-149` (`_mark_open_trade`) and `:187-241` (`close_trade`)
- Test: `tests/test_paper_trading_mfe.py`

- [ ] **Step 1: Write failing test**

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.paper_trading import paper_trade_from_signal, mark_trade_to_market, close_trade  # noqa: E402


def _signal():
    return {"id": "s1", "instrument": "NIFTY", "direction": "CE", "strategy_id": "orr",
            "entry_price": 100.0, "option_contract": {"lot_size": 75, "instrument_key": "k"}}


def test_mark_tracks_running_mfe_mae():
    t = paper_trade_from_signal(_signal(), lots=1, entry_price=100.0)
    t = mark_trade_to_market(t, last_price=110.0)   # +750 (qty 75)
    t = mark_trade_to_market(t, last_price=96.0)    # -300
    t = mark_trade_to_market(t, last_price=107.0)   # +525
    assert t["mfe_value"] == 750.0
    assert t["mae_value"] == -300.0


def test_close_preserves_mfe_mae():
    t = paper_trade_from_signal(_signal(), lots=1, entry_price=100.0)
    t = mark_trade_to_market(t, last_price=120.0)   # +1500
    closed = close_trade(t, exit_price=108.0, reason="target")
    assert closed["mfe_value"] == 1500.0
    assert closed["status"] == "CLOSED"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_paper_trading_mfe.py -v`
Expected: FAIL (`KeyError: 'mfe_value'`).

- [ ] **Step 3: Implement** — in `_mark_open_trade`, after computing `unrealized_pnl`, add running extremes:

```python
    updated["last_price"] = price
    updated["unrealized_pnl"] = round((price - entry) * quantity, 2)
    prev_mfe = updated.get("mfe_value")
    prev_mae = updated.get("mae_value")
    updated["mfe_value"] = round(max(updated["unrealized_pnl"], prev_mfe if prev_mfe is not None else updated["unrealized_pnl"]), 2)
    updated["mae_value"] = round(min(updated["unrealized_pnl"], prev_mae if prev_mae is not None else updated["unrealized_pnl"]), 2)
    updated["updated_at"] = timestamp
```

In `close_trade`, ensure the fields survive (they are copied via `dict(trade)`; also fold the realized value so a never-marked trade still has bounds). After the `updated.update({...})` block add:

```python
    rp = updated.get("realized_pnl") or 0.0
    if updated.get("mfe_value") is None:
        updated["mfe_value"] = round(rp, 2)
    if updated.get("mae_value") is None:
        updated["mae_value"] = round(rp, 2)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_paper_trading_mfe.py tests/test_paper_analytics.py -v`
Expected: PASS. Also run the existing paper tests to confirm no regression: `python -m pytest tests/ -k paper -v`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/paper_trading.py tests/test_paper_trading_mfe.py
git commit -m "feat(paper): track running MFE/MAE on mark + close"
```

---

## Task 5: Add MFE/MAE to sortable trade fields

**Files:**
- Modify: `backend/app/runtime.py` (`_TRADES_SORT_FIELDS`)

- [ ] **Step 1: Locate the set**

Run: `python -m pytest -q` first to confirm a green baseline, then find the definition:
Run: `grep -n "_TRADES_SORT_FIELDS" backend/app/runtime.py`

- [ ] **Step 2: Add the fields**

Add `"mfe_value"` and `"mae_value"` to the `_TRADES_SORT_FIELDS` collection (keep existing entries: `created_at`, `realized_pnl`, `entry_price`, `updated_at`, `closed_at`).

- [ ] **Step 3: Verify import still loads**

Run: `python -c "import sys; sys.path.insert(0,'backend'); from app.runtime import _TRADES_SORT_FIELDS; print(sorted(_TRADES_SORT_FIELDS))"`
Expected: list includes `mfe_value` and `mae_value`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/runtime.py
git commit -m "feat(paper): allow sorting trades by MFE/MAE"
```

---

## Task 6: Account-config endpoints (configurable starting capital)

**Files:**
- Modify: `backend/app/routers/journals.py`
- Test: `tests/test_paper_account_config.py`

- [ ] **Step 1: Write failing test** (uses FastAPI TestClient against the app; mirrors existing router test style — confirm app import path with `grep -rn "FastAPI(" backend/app | head`; assume `from app.main import app`)

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient
from app.main import app  # adjust if app factory differs

client = TestClient(app)


def test_account_config_default_then_set():
    r = client.get("/api/paper/account-config")
    assert r.status_code == 200
    assert r.json()["starting_capital"] == 200000.0
    r2 = client.put("/api/paper/account-config", json={"starting_capital": 500000})
    assert r2.status_code == 200
    assert r2.json()["starting_capital"] == 500000.0
    assert client.get("/api/paper/account-config").json()["starting_capital"] == 500000.0
```

> If a test DB fixture is required, follow the pattern in the existing router tests (`grep -rln "TestClient" tests/`). If none exist for these routes, keep this as an integration smoke test run against the dev backend instead and verify manually in Step 4.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_paper_account_config.py -v`
Expected: FAIL (404 on the route).

- [ ] **Step 3: Implement** — add to `backend/app/routers/journals.py` (after the imports, add `from pydantic import BaseModel`, then the routes):

```python
from pydantic import BaseModel

_DEFAULT_STARTING_CAPITAL = 200_000.0


async def _get_starting_capital(db) -> float:
    doc = await db.app_settings.find_one({"key": "paper_account"}, {"_id": 0})
    if doc and doc.get("starting_capital") is not None:
        try:
            return float(doc["starting_capital"])
        except (TypeError, ValueError):
            pass
    return _DEFAULT_STARTING_CAPITAL


class AccountConfigReq(BaseModel):
    starting_capital: float


@api.get("/paper/account-config")
async def get_paper_account_config():
    return {"starting_capital": await _get_starting_capital(get_db())}


@api.put("/paper/account-config")
async def set_paper_account_config(req: AccountConfigReq):
    if req.starting_capital <= 0:
        raise HTTPException(400, "starting_capital must be > 0")
    db = get_db()
    await db.app_settings.update_one(
        {"key": "paper_account"},
        {"$set": {"key": "paper_account", "starting_capital": float(req.starting_capital)}},
        upsert=True,
    )
    return {"starting_capital": float(req.starting_capital)}
```

- [ ] **Step 4: Run / verify**

Run: `python -m pytest tests/test_paper_account_config.py -v` (or, if no test DB, start the backend via the `run` skill and `curl` GET/PUT/GET).
Expected: PASS / 200s with the persisted value.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/journals.py tests/test_paper_account_config.py
git commit -m "feat(paper): configurable starting capital (account-config GET/PUT)"
```

---

## Task 7: /paper/analytics and /paper/strategy-stats routes

**Files:**
- Modify: `backend/app/routers/journals.py`

- [ ] **Step 1: Implement the routes** (thin wrappers over the tested pure layer; the pure functions already have unit coverage from Tasks 2–3):

```python
from app import paper_analytics


@api.get("/paper/analytics")
async def paper_account_analytics():
    db = get_db()
    starting = await _get_starting_capital(db)
    closed = await db.paper_trades.find(
        {"status": "CLOSED"},
        {"_id": 0, "events": 0, "realized_pnl": 1, "closed_at": 1, "updated_at": 1,
         "instrument": 1, "entry_price": 1, "quantity": 1, "status": 1},
    ).to_list(length=100000)
    open_rows = await db.paper_trades.find({"status": "OPEN"}, {"_id": 0, "events": 0}).to_list(length=500)
    from app.runtime import upstox_stream_manager
    live = build_open_positions(open_rows, latest_tick_lookup=upstox_stream_manager.latest_tick_map().get)
    live_by_id = {p["id"]: p for p in live["items"]}
    for r in open_rows:
        lp = live_by_id.get(r.get("id"))
        if lp is not None:
            r["unrealized_pnl"] = lp["unrealized_pnl"]
    out = paper_analytics.build_account_analytics(closed, open_rows, starting_capital=starting)
    return serialize_doc(out)


@api.get("/paper/strategy-stats")
async def paper_strategy_stats():
    db = get_db()
    rows = await db.paper_trades.find(
        {}, {"_id": 0, "events": 0, "strategy_id": 1, "deployment_id": 1, "status": 1,
             "realized_pnl": 1, "unrealized_pnl": 1, "created_at": 1, "closed_at": 1},
    ).to_list(length=100000)
    dep_ids = sorted({str(r.get("deployment_id")) for r in rows if r.get("deployment_id")})
    names = {}
    if dep_ids:
        for d in await db.strategy_deployments.find({"id": {"$in": dep_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(length=len(dep_ids)):
            names[str(d["id"])] = str(d.get("name") or "")
    stats = paper_analytics.per_strategy_stats(rows)
    for s in stats:
        s["deployment_name"] = names.get(str(s.get("deployment_id") or ""), "")
    return serialize_doc({"items": stats, "count": len(stats)})
```

- [ ] **Step 2: Verify** — start backend (`run` skill) and:

Run: `curl -s localhost:8000/api/paper/analytics | python -m json.tool | head -40`
Run: `curl -s localhost:8000/api/paper/strategy-stats | python -m json.tool | head -40`
Expected: JSON with `account_value_realized`, `equity_curve`, `period_pnl`, `exposure`; and a per-strategy `items` array. (Port per your dev setup.)

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/journals.py
git commit -m "feat(paper): /paper/analytics and /paper/strategy-stats routes"
```

---

## Task 8: Extend /paper/trades with per-trade analytics

**Files:**
- Modify: `backend/app/routers/journals.py:213-265` (`list_paper_trades`)

- [ ] **Step 1: Add the opt-in param + per-row analytics**

Change the signature to add `include_analytics: bool = Query(False)`. When true, do NOT exclude `events` from the projection (so analytics can read them), compute per-trade analytics, then strip `events` before returning:

```python
    proj = {"_id": 0} if include_analytics else {"_id": 0, "events": 0}
    rows = await db.paper_trades.find(q, proj).sort(field, direction).skip(skip).limit(limit).to_list(length=limit)
    ...
    if include_analytics:
        from app import paper_analytics
        for r in rows:
            r["analytics"] = paper_analytics.per_trade_analytics(r)
            r.pop("events", None)
```

(Place the analytics loop after the `deployment_name` enrichment, before the CSV/return block. CSV export keeps `include_analytics=False`.)

- [ ] **Step 2: Verify**

Run: `curl -s "localhost:8000/api/paper/trades?include_analytics=true&limit=3" | python -m json.tool | head -60`
Expected: each item has an `analytics` block (`mfe_value`, `mae_value`, `running_pnl`, `spark`, `duration_s`, `sl`, `tp`) and no `events`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/journals.py
git commit -m "feat(paper): per-trade analytics on /paper/trades (opt-in)"
```

---

## Task 9: Frontend API client additions

**Files:**
- Modify: `frontend/src/lib/api.js:156-166`

- [ ] **Step 1: Add methods** (next to the existing paper methods):

```javascript
  paperAnalytics: () => apiClient.get("/paper/analytics").then((r) => r.data),
  paperStrategyStats: () => apiClient.get("/paper/strategy-stats").then((r) => r.data),
  getPaperAccountConfig: () => apiClient.get("/paper/account-config").then((r) => r.data),
  setPaperAccountConfig: (starting_capital) =>
    apiClient.put("/paper/account-config", { starting_capital }).then((r) => r.data),
```

And add `include_analytics` support to the existing `listPaperTrades` (it already spreads `params`, so callers can pass `{ include_analytics: true }` — no change needed; confirm by reading the method).

- [ ] **Step 2: Verify** — `cd frontend && yarn build` (or rely on the dev server hot-reload in Task 19). Expected: no syntax errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.js
git commit -m "feat(paper-ui): api client for analytics/strategy-stats/account-config"
```

---

## Task 10: Indian-currency + duration formatters

**Files:**
- Modify: `frontend/src/lib/fmt.js`

- [ ] **Step 1: Add `fmtINR`, `fmtINRSigned`, `fmtDuration`** (manual Indian grouping — NO `toLocaleString`, consistent with this file's existing policy):

```javascript
// Indian lakh/crore grouping (last 3 digits, then groups of 2). No locale API.
const groupIndian = (intStr) => {
  if (intStr.length <= 3) return intStr;
  const head = intStr.slice(0, intStr.length - 3);
  const tail = intStr.slice(-3);
  return head.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + tail;
};

export const fmtINR = (n, decimals = 0) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "–";
  const v = Number(n);
  const fixed = Math.abs(v).toFixed(decimals);
  const [int, dec] = fixed.split(".");
  const body = groupIndian(int) + (dec ? "." + dec : "");
  return `${v < 0 ? "−" : ""}₹${body}`;
};

export const fmtINRSigned = (n, decimals = 0) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "–";
  const v = Number(n);
  const fixed = Math.abs(v).toFixed(decimals);
  const [int, dec] = fixed.split(".");
  const body = groupIndian(int) + (dec ? "." + dec : "");
  return `${v < 0 ? "−" : "+"}₹${body}`;
};

export const fmtDuration = (seconds) => {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
};
```

- [ ] **Step 2: Verify** with a quick node check:

Run: `cd frontend && node -e "const f=require('@babel/register'); " 2>/dev/null; node --input-type=module -e "import('./src/lib/fmt.js').then(m=>console.log(m.fmtINR(214580), m.fmtINRSigned(-6100), m.fmtDuration(3845)))"`
Expected: `₹2,14,580 −₹6,100 1h 04m`. (If ESM import fails in node, verify visually once the page renders in Task 19.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/fmt.js
git commit -m "feat(paper-ui): Indian ₹ grouping + duration formatters"
```

---

## Task 11: TradeSparkline component (inline SVG)

**Files:**
- Create: `frontend/src/components/paper/TradeSparkline.jsx`

- [ ] **Step 1: Implement** (zero-baseline P&L curve; green if last >=0 else red; draws a faint zero line):

```jsx
// Compact P&L sparkline from analytics.spark = [{t, pnl}]. Pure SVG, no deps.
export default function TradeSparkline({ points, width = 72, height = 24 }) {
  if (!points || points.length < 2) {
    return <span className="text-dimmer text-[10px] font-mono">—</span>;
  }
  const xs = points.map((p) => p.t);
  const ys = points.map((p) => p.pnl);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys, 0), maxY = Math.max(...ys, 0);
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  const px = (x) => ((x - minX) / spanX) * width;
  const py = (y) => height - ((y - minY) / spanY) * height;
  const path = points.map((p, i) => `${i ? "L" : "M"}${px(p.t).toFixed(1)},${py(p.pnl).toFixed(1)}`).join(" ");
  const last = ys[ys.length - 1];
  const stroke = last >= 0 ? "var(--color-success)" : "var(--color-danger)";
  const zeroY = py(0).toFixed(1);
  return (
    <svg width={width} height={height} className="overflow-visible" preserveAspectRatio="none" data-testid="trade-sparkline" aria-hidden="true">
      <line x1="0" y1={zeroY} x2={width} y2={zeroY} stroke="var(--color-line)" strokeWidth="0.5" strokeDasharray="2,2" />
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.5" />
    </svg>
  );
}
```

> Note: confirm the CSS var names your theme exposes (`--color-success`/`--color-danger`/`--color-line`). The existing `PaperTrading.jsx` `CalendarHeatGrid` already uses `var(--color-success)` / `var(--color-danger)`, so reuse those; for the zero line, fall back to `currentColor` with `text-line` on the `<svg>` if `--color-line` is not defined.

- [ ] **Step 2: Verify** in Task 19 (rendered in blotter rows).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/paper/TradeSparkline.jsx
git commit -m "feat(paper-ui): TradeSparkline inline-SVG P&L curve"
```

---

## Task 12: AccountHero component (account value + equity curve + editable capital)

**Files:**
- Create: `frontend/src/components/paper/AccountHero.jsx`

- [ ] **Step 1: Implement** (recharts AreaChart; uses `fmtINR`/`fmtINRSigned`; an inline editable starting capital with confirm):

```jsx
import { useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { Pencil, Check, X } from "lucide-react";
import { fmtINR, fmtINRSigned, fmtPct } from "@/lib/fmt";

function Stat({ label, value, tone = null }) {
  const cls = tone == null ? "" : Number(tone) > 0 ? "text-success" : Number(tone) < 0 ? "text-danger" : "";
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`text-sm font-mono tabular-nums mt-0.5 ${cls}`}>{value}</div>
    </div>
  );
}

export default function AccountHero({ analytics, startingCapital, onSetCapital, busy }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(startingCapital ?? 200000));
  if (!analytics) return null;
  const a = analytics;
  const curve = (a.equity_curve || []).map((p) => ({ day: p.day, equity: p.equity_value }));
  const save = () => {
    const v = Number(draft);
    if (!Number.isFinite(v) || v <= 0) return;
    onSetCapital(v);
    setEditing(false);
  };
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="paper-account-hero">
      <div className="flex justify-between flex-wrap gap-3 items-start">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Account value (realized)</div>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-mono tabular-nums">{fmtINR(a.account_value_realized)}</span>
            <span className={`text-sm font-mono ${a.total_return_pct >= 0 ? "text-success" : "text-danger"}`}>{fmtPct(a.total_return_pct, 2)}</span>
          </div>
          <div className="text-[11px] text-dimmer flex items-center gap-1">
            start {editing ? (
              <span className="inline-flex items-center gap-1">
                <input value={draft} onChange={(e) => setDraft(e.target.value)} type="number"
                  className="h-6 w-24 bg-bg-2 border border-line rounded px-1 text-[11px]" data-testid="paper-capital-input" />
                <button onClick={save} disabled={busy} className="text-success" title="Save"><Check className="w-3.5 h-3.5" /></button>
                <button onClick={() => setEditing(false)} className="text-dimmer" title="Cancel"><X className="w-3.5 h-3.5" /></button>
              </span>
            ) : (
              <span className="inline-flex items-center gap-1">
                {fmtINR(a.starting_capital)}
                <button onClick={() => { setDraft(String(a.starting_capital)); setEditing(true); }} className="text-dimmer hover:text-foreground" title="Edit starting capital" data-testid="paper-capital-edit"><Pencil className="w-3 h-3" /></button>
              </span>
            )}
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-5 gap-y-2">
          <Stat label="Live MTM" value={fmtINR(a.account_value_mtm)} tone={a.account_value_mtm - a.starting_capital} />
          <Stat label="Deployed in market" value={fmtINR(a.deployed_capital)} />
          <Stat label="Open P&L" value={fmtINRSigned(a.open_pnl)} tone={a.open_pnl} />
          <Stat label="Max drawdown" value={fmtINR(a.max_drawdown_value)} tone={a.max_drawdown_value} />
        </div>
      </div>
      <div className="h-[150px] mt-2" data-testid="paper-equity-curve">
        {curve.length >= 2 ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={curve} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-success)" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="var(--color-success)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "var(--color-dimmer, #888)" }} width={48}
                tickFormatter={(v) => `₹${Math.round(v / 1000)}k`} />
              <Tooltip formatter={(v) => fmtINR(v)} labelFormatter={(l) => l}
                contentStyle={{ background: "var(--color-bg-2, #111)", border: "1px solid var(--color-line, #333)", fontSize: 11 }} />
              <Area type="monotone" dataKey="equity" stroke="var(--color-success)" strokeWidth={1.5} fill="url(#eq)" />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-[11px] text-dimmer font-mono pt-6">No closed trades yet — equity curve appears as trades close.</div>
        )}
      </div>
    </div>
  );
}
```

> Confirm CSS var names used by recharts inline styles against your theme (`--color-bg-2`, `--color-line`, `--color-dimmer`). If your tokens differ, substitute the hex the existing charts use (see `frontend/src/components/charts/`).

- [ ] **Step 2: Verify** in Task 19.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/paper/AccountHero.jsx
git commit -m "feat(paper-ui): AccountHero with equity curve + editable starting capital"
```

---

## Task 13: PeriodPnlCards component

**Files:**
- Create: `frontend/src/components/paper/PeriodPnlCards.jsx`

- [ ] **Step 1: Implement** (reuses the dark-token card style; sign-aware colours):

```jsx
import { fmtINRSigned, fmtPct, fmtNum } from "@/lib/fmt";

function Card({ label, value, tone = null }) {
  const cls = tone == null ? "" : Number(tone) > 0 ? "text-success" : Number(tone) < 0 ? "text-danger" : "";
  return (
    <div className="rounded-md border border-line bg-bg-2 p-2">
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`text-base font-mono tabular-nums mt-0.5 ${cls}`}>{value}</div>
    </div>
  );
}

export default function PeriodPnlCards({ period }) {
  if (!period) return null;
  const p = period;
  const pf = p.profit_factor;
  return (
    <div className="grid grid-cols-2 lg:grid-cols-6 gap-2" data-testid="paper-period-cards">
      <Card label="Today" value={fmtINRSigned(p.today)} tone={p.today} />
      <Card label="This week" value={fmtINRSigned(p.week)} tone={p.week} />
      <Card label="This month" value={fmtINRSigned(p.month)} tone={p.month} />
      <Card label="Lifetime" value={fmtINRSigned(p.lifetime)} tone={p.lifetime} />
      <Card label="Win rate" value={p.win_rate == null ? "—" : fmtPct(p.win_rate, 1)} />
      <Card label="Profit factor" value={pf == null ? "—" : (pf === Infinity || pf === "Infinity" ? "∞" : fmtNum(pf, 2))} />
    </div>
  );
}
```

- [ ] **Step 2: Verify** in Task 19.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/paper/PeriodPnlCards.jsx
git commit -m "feat(paper-ui): period P&L cards (today/week/month/lifetime)"
```

---

## Task 14: StrategyStatsTable component

**Files:**
- Create: `frontend/src/components/paper/StrategyStatsTable.jsx`

- [ ] **Step 1: Implement** (per-strategy rows; clicking a strategy filters the blotter via `onFilterStrategy`):

```jsx
import { fmtINRSigned, fmtPct, fmtNum } from "@/lib/fmt";
import { colorPnL } from "@/lib/fmt";

export default function StrategyStatsTable({ stats, onFilterStrategy }) {
  if (!stats || stats.length === 0) {
    return <div className="rounded-lg border border-line bg-bg-1 p-3 text-[11px] text-dimmer">No strategy activity yet.</div>;
  }
  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-x-auto" data-testid="paper-strategy-stats">
      <table className="w-full text-xs">
        <thead className="bg-bg-2 text-dim">
          <tr className="border-b border-line">
            <th className="text-left p-2">Strategy</th>
            <th className="text-right p-2">Net P&L</th>
            <th className="text-right p-2">Trades</th>
            <th className="text-right p-2">Win%</th>
            <th className="text-right p-2">PF</th>
            <th className="text-right p-2">Expectancy</th>
            <th className="text-right p-2">Open</th>
            <th className="text-right p-2">Contrib.</th>
          </tr>
        </thead>
        <tbody>
          {stats.map((s) => (
            <tr key={s.strategy_id + (s.deployment_id || "")} className="border-b border-line hover:bg-bg-2 cursor-pointer"
              onClick={() => onFilterStrategy?.(s.strategy_id)} data-testid="paper-strategy-row">
              <td className="p-2">
                <div className="font-medium truncate max-w-[200px]" title={s.deployment_name || s.strategy_id}>{s.deployment_name || s.strategy_id}</div>
                <div className="text-dimmer truncate max-w-[200px]">{s.strategy_id}</div>
              </td>
              <td className={`p-2 text-right font-mono tabular-nums ${colorPnL(s.net_pnl)}`}>{fmtINRSigned(s.net_pnl)}</td>
              <td className="p-2 text-right font-mono text-dim">{s.closed_trades}</td>
              <td className="p-2 text-right font-mono">{s.win_rate == null ? "—" : fmtPct(s.win_rate, 0)}</td>
              <td className="p-2 text-right font-mono">{s.profit_factor == null ? "—" : (s.profit_factor === "Infinity" || s.profit_factor === Infinity ? "∞" : fmtNum(s.profit_factor, 2))}</td>
              <td className={`p-2 text-right font-mono ${colorPnL(s.expectancy)}`}>{s.expectancy == null ? "—" : fmtINRSigned(s.expectancy)}</td>
              <td className="p-2 text-right font-mono text-dim">{s.open_count}{s.open_count ? ` · ${fmtINRSigned(s.open_mtm)}` : ""}</td>
              <td className="p-2 text-right font-mono text-dim">{s.contribution_pct == null ? "—" : `${fmtNum(s.contribution_pct, 0)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Verify** in Task 19.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/paper/StrategyStatsTable.jsx
git commit -m "feat(paper-ui): per-strategy stats table with contribution"
```

---

## Task 15: TradeDetailDrawer component

**Files:**
- Create: `frontend/src/components/paper/TradeDetailDrawer.jsx`

- [ ] **Step 1: Implement** (expand row: larger recharts P&L curve with SL/TP reference lines + friction breakdown). Rendered as a full-width row under the clicked trade:

```jsx
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtINR, fmtINRSigned, fmtNum } from "@/lib/fmt";

export default function TradeDetailDrawer({ trade }) {
  const a = trade.analytics || {};
  const qty = Number(trade.quantity || 0);
  // P&L curve in ₹; SL/TP are premium levels -> convert to ₹ vs entry for the reference lines.
  const entry = Number(trade.entry_price || 0);
  const slPnl = a.sl != null && qty ? (Number(a.sl) - entry) * qty : null;
  const tpPnl = a.tp != null && qty ? (Number(a.tp) - entry) * qty : null;
  const data = (a.spark || []).map((p) => ({ t: p.t, pnl: p.pnl }));
  return (
    <div className="bg-bg-0 border-t border-line p-3" data-testid="paper-trade-detail">
      <div className="grid lg:grid-cols-[2fr_1fr] gap-4">
        <div className="h-[160px]">
          {data.length >= 2 ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id={`pnl-${trade.id}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--color-info)" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="var(--color-info)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="t" hide />
                <YAxis tick={{ fontSize: 10 }} width={52} tickFormatter={(v) => fmtINR(v)} />
                <Tooltip formatter={(v) => fmtINRSigned(v)} labelFormatter={() => ""} />
                <ReferenceLine y={0} stroke="var(--color-line)" strokeDasharray="2,2" />
                {slPnl != null && <ReferenceLine y={slPnl} stroke="var(--color-danger)" strokeDasharray="3,3" label={{ value: "SL", fontSize: 10, fill: "var(--color-danger)" }} />}
                {tpPnl != null && <ReferenceLine y={tpPnl} stroke="var(--color-success)" strokeDasharray="3,3" label={{ value: "TP", fontSize: 10, fill: "var(--color-success)" }} />}
                <Area type="monotone" dataKey="pnl" stroke="var(--color-info)" strokeWidth={1.5} fill={`url(#pnl-${trade.id})`} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="text-[11px] text-dimmer font-mono pt-6">No intra-trade marks recorded.</div>
          )}
        </div>
        <div className="text-[11px] font-mono space-y-1">
          <Row k="Max P&L (MFE)" v={fmtINRSigned(a.mfe_value)} cls="text-success" />
          <Row k="Min P&L (MAE)" v={fmtINRSigned(a.mae_value)} cls="text-danger" />
          <Row k="Running P&L" v={fmtINRSigned(a.running_pnl)} />
          <Row k="Last SL / TP" v={`${a.sl ?? "—"} / ${a.tp ?? "—"}`} />
          {trade.friction_cost != null && Number(trade.friction_cost) !== 0 && (
            <>
              <Row k="Gross" v={fmtINR(trade.gross_realized_pnl)} />
              <Row k="Friction" v={`−${fmtINR(Math.abs(Number(trade.friction_cost)))}`} />
              <Row k="Charges" v={fmtINR(trade.total_charges)} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ k, v, cls = "" }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-dimmer">{k}</span>
      <span className={cls}>{v}</span>
    </div>
  );
}
```

- [ ] **Step 2: Verify** in Task 19.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/paper/TradeDetailDrawer.jsx
git commit -m "feat(paper-ui): per-trade detail drawer (P&L curve + SL/TP + friction)"
```

---

## Task 16: TradeBlotter component (flat, sortable, new columns)

**Files:**
- Create: `frontend/src/components/paper/TradeBlotter.jsx`

- [ ] **Step 1: Implement** the redesigned table. Date is a per-row column; the parent owns server filters/sort/paginate and passes them down. Rows expand into `TradeDetailDrawer`. Reuse the existing inline close controls for OPEN rows.

```jsx
import { Fragment, useState } from "react";
import { fmtINR, fmtINRSigned, fmtNum, fmtPct, fmtDuration, colorPnL } from "@/lib/fmt";
import TradeSparkline from "./TradeSparkline";
import TradeDetailDrawer from "./TradeDetailDrawer";
import { Zap } from "lucide-react";

const IST_OFFSET_MS = 330 * 60 * 1000;
const pad = (n) => String(n).padStart(2, "0");
const istParts = (iso) => {
  if (!iso) return null;
  const d = new Date(new Date(iso).getTime() + IST_OFFSET_MS);
  return { day: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`,
           time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` };
};

export default function TradeBlotter({ rows, sort, onToggleSort, onCloseAtMarket, busy }) {
  const [open, setOpen] = useState(() => new Set());
  const toggle = (id) => setOpen((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const mark = (col) => (sort === col ? " ▲" : sort === `-${col}` ? " ▼" : null);
  const H = ({ col, children, right }) => (
    <th className={`p-2 ${right ? "text-right" : "text-left"} ${col ? "cursor-pointer hover:text-foreground" : ""}`}
      onClick={col ? () => onToggleSort(col) : undefined}>{children}{col ? mark(col) : null}</th>
  );
  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-x-auto" data-testid="paper-trade-blotter">
      <table className="w-full text-xs" data-testid="paper-trade-table">
        <thead className="sticky top-0 bg-bg-2 z-10">
          <tr className="text-dim border-b border-line">
            <H col="created_at">Date / time</H>
            <H>Strategy / contract</H>
            <H right>Side</H>
            <H col="entry_price" right>Entry→Exit</H>
            <H right>Dur</H>
            <H right>SL / TP</H>
            <H col="mfe_value" right>Max</H>
            <H col="mae_value" right>Min</H>
            <H right>Now</H>
            <H right>P&L curve</H>
            <H col="realized_pnl" right>Net P&L</H>
            <H right>P&L%</H>
            <H right>Status</H>
            <H right>Actions</H>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan="14" className="p-6 text-center text-dimmer">No paper trades match these filters.</td></tr>
          )}
          {rows.map((t) => {
            const isOpen = String(t.status || "").toUpperCase() === "OPEN";
            const a = t.analytics || {};
            const entry = istParts(t.created_at);
            const exit = istParts(t.closed_at);
            const net = isOpen ? a.running_pnl : Number(t.realized_pnl || 0);
            const notional = Number(t.entry_price || 0) * Number(t.quantity || 0);
            const pct = notional ? (Number(net || 0) / notional) * 100 : null;
            return (
              <Fragment key={t.id}>
                <tr className="border-b border-line hover:bg-bg-2 cursor-pointer" onClick={() => toggle(t.id)} data-testid="paper-trade-row">
                  <td className="p-2 font-mono whitespace-nowrap">{entry ? entry.day : "—"}<div className="text-dimmer">{entry ? entry.time : ""}</div></td>
                  <td className="p-2"><div className="font-medium truncate max-w-[150px]" title={t.deployment_name}>{t.deployment_name || t.strategy_id}</div><div className="text-dimmer font-mono truncate max-w-[150px]">{t.trading_symbol || t.instrument}</div></td>
                  <td className="p-2 text-right"><span className={`font-mono ${t.direction === "CE" ? "text-emerald-400" : t.direction === "PE" ? "text-red-400" : "text-dim"}`}>{t.direction || "—"}</span></td>
                  <td className="p-2 text-right font-mono whitespace-nowrap">{fmtNum(t.entry_price)}→{t.exit_price != null ? fmtNum(t.exit_price) : (isOpen ? "live" : "—")}</td>
                  <td className="p-2 text-right font-mono text-dim">{fmtDuration(a.duration_s)}</td>
                  <td className="p-2 text-right font-mono text-dimmer whitespace-nowrap">{a.sl ?? "—"} / {a.tp ?? "—"}</td>
                  <td className="p-2 text-right font-mono text-success">{fmtINRSigned(a.mfe_value)}</td>
                  <td className="p-2 text-right font-mono text-danger">{fmtINRSigned(a.mae_value)}</td>
                  <td className={`p-2 text-right font-mono ${colorPnL(a.running_pnl)}`}>{fmtINRSigned(a.running_pnl)}</td>
                  <td className="p-2 text-right"><div className="flex justify-end"><TradeSparkline points={a.spark} /></div></td>
                  <td className={`p-2 text-right font-mono ${colorPnL(net)}`}>{fmtINRSigned(net)}</td>
                  <td className={`p-2 text-right font-mono ${colorPnL(pct)}`}>{pct == null ? "—" : fmtPct(pct, 1)}</td>
                  <td className="p-2 text-right"><span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${isOpen ? "border-emerald-500/40 text-emerald-300" : "border-line text-dim"}`}>{t.status}</span></td>
                  <td className="p-2 text-right" onClick={(e) => e.stopPropagation()}>
                    {isOpen && (
                      <button disabled={busy} onClick={() => onCloseAtMarket(t)} className="h-7 text-[11px] bg-bg-3 border border-line hover:bg-bg-2 px-2 rounded inline-flex items-center" data-testid="close-paper-trade" title="Close at last live mark">
                        <Zap className="w-3 h-3 mr-1" /> @ market
                      </button>
                    )}
                  </td>
                </tr>
                {open.has(t.id) && (
                  <tr><td colSpan="14" className="p-0"><TradeDetailDrawer trade={t} /></td></tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
```

> Keep the existing manual-price / selection / purge controls available — for Phase 1 they can stay on the parent page toolbar (Task 18). The inline `@market` close is preserved here; full manual-price fallback can move into the detail drawer in a follow-up.

- [ ] **Step 2: Verify** in Task 19.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/paper/TradeBlotter.jsx
git commit -m "feat(paper-ui): redesigned flat sortable trade blotter with per-trade analytics"
```

---

## Task 17: Extract DeploymentControlStrip + PnlCalendar

**Files:**
- Create: `frontend/src/components/paper/DeploymentControlStrip.jsx`
- Create: `frontend/src/components/paper/PnlCalendar.jsx`

- [ ] **Step 1: Move existing code** — cut the `DeploymentControlRow` + the Live Deployments strip JSX and handlers (`doPause/doResume/doStop/doStopAll`) from `PaperTrading.jsx` into `DeploymentControlStrip.jsx` (props: `deployments`, `perDeployOpen`, `busy`, and the action callbacks). Cut `CalendarHeatGrid` + the calendar card into `PnlCalendar.jsx` (props: `dayPnl`, plus a monthly-bar series). Behaviour is unchanged — pure extraction.

- [ ] **Step 2: Add monthly P&L bars to PnlCalendar** (recharts BarChart from the analytics `equity_curve` aggregated by month, or a `period_pnl`-derived monthly series passed in). Keep it small (height ~120px).

- [ ] **Step 3: Verify** the extracted pieces still render identically (Task 19).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/paper/DeploymentControlStrip.jsx frontend/src/components/paper/PnlCalendar.jsx
git commit -m "refactor(paper-ui): extract deployment strip + P&L calendar components"
```

---

## Task 18: Reassemble PaperTrading.jsx + feed-health chip

**Files:**
- Modify: `frontend/src/pages/PaperTrading.jsx`

- [ ] **Step 1: Rewire the page** to:
  1. Fetch `api.paperAnalytics()`, `api.paperStrategyStats()`, `api.getPaperAccountConfig()` alongside the existing trades fetch; call `api.listPaperTrades({ ...params, include_analytics: true })` for the blotter page.
  2. Render in order: `<AccountHero>`, `<PeriodPnlCards>`, `<StrategyStatsTable onFilterStrategy={...}>`, `<DeploymentControlStrip>`, `<PnlCalendar>` (collapsible), filters toolbar, `<TradeBlotter>`, pagination, footnote.
  3. Keep the existing 30s table refresh + 2s open-positions poll; refresh analytics on the 30s cadence (analytics is realized-based, so 30s is plenty).
  4. Wire `onSetCapital` → `api.setPaperAccountConfig(v)` → toast + refetch analytics.
  5. Add a **feed-health chip** near the page title using the existing `livePos` poll: green "Live" when `livePos.items` non-empty and not all stale; amber "Estimated / stale" otherwise (reuse the `live_stale` flags already surfaced).
  6. Keep CSV export, selection/purge toolkit, manual close-all.

- [ ] **Step 2: Confirm sort wiring** — `TradeBlotter`'s `onToggleSort(col)` maps to the existing server `sort` toggle logic; the new `mfe_value`/`mae_value` columns are now valid server sort fields (Task 5).

- [ ] **Step 3: Verify** in Task 19.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/PaperTrading.jsx
git commit -m "feat(paper-ui): assemble redesigned Paper Trading page + feed-health chip"
```

---

## Task 19: Full verification

- [ ] **Step 1: Backend green** — `python -m pytest tests/ -q`. Expected: all pass (existing 880 + new paper-analytics/mfe/account-config tests).

- [ ] **Step 2: Launch the app** via the `run` skill (backend + `cd frontend && yarn start`).

- [ ] **Step 3: Verify (use the `verify` skill)** on `/paper`:
  - Account hero shows account value, live MTM, deployed capital, open P&L, max drawdown; equity curve renders.
  - Edit starting capital → value persists + curve rebases; reload keeps it.
  - Period cards (today/week/month/lifetime + win rate + PF) populate.
  - Per-strategy table shows rows; clicking one filters the blotter.
  - Blotter: date per row; sort by date / entry / net P&L / Max / Min works; each row shows Max/Min/Now, sparkline, SL/TP, duration; row click expands the detail drawer with the bigger curve + SL/TP lines.
  - Feed-health chip reflects live vs estimated.
  - ₹ values show Indian grouping (e.g. ₹2,14,580); P&L carries +/− signs.

- [ ] **Step 4: Run `design:design-critique` and `design:accessibility-review`** on the rendered page; fix any high-priority findings (colour-only signals, contrast, focus states).

- [ ] **Step 5: Final commit** (any verification fixes).

```bash
git add -A
git commit -m "fix(paper-ui): verification + a11y polish"
```

---

## Self-review (author check)

- **Spec coverage:** req.1 infographics → AccountHero/equity/cards/strategy table/sparklines (T12-16); req.2 per-strategy → T3/T14; req.3 deployed capital + running P&L + ₹2L start + account value over days → T2/T6/T12; req.4 lifetime/weekly/monthly/daily → T2/T13; req.5 date-per-row + filter/sort → T16/T18; req.6 Min/Max/Now + duration + sparkline + SL/TP → T1/T4/T15/T16; req.7 extras (exposure, feed-health) → T2/T18; configurable capital → T6/T12. Phase-2 extras (drift, R-multiple, exit-reason) are intentionally deferred to a separate plan.
- **Placeholder scan:** no TBD/TODO; each code step has concrete code; verification steps give exact commands.
- **Type consistency:** `analytics` block shape (`mfe_value`, `mae_value`, `running_pnl`, `spark`, `duration_s`, `sl`, `tp`) is identical across T1, T8, T15, T16; account analytics keys (`account_value_realized`, `account_value_mtm`, `equity_curve`, `period_pnl`, `exposure`) identical across T2, T7, T12; `per_strategy_stats` keys identical across T3, T7, T14.
- **Assumptions to confirm at execution:** the FastAPI app import path for the TestClient (T6), exact theme CSS-var names for recharts inline styles (T12/T15), and the `_TRADES_SORT_FIELDS` container type (T5).

## Phase 2 (separate plan, later)

Forward-vs-backtest drift per strategy, expectancy/R-multiple columns in the blotter, exit-reason breakdown chart, and optional write-time downsampling/capping of `events[]`. Each gets its own spec→plan cycle.
