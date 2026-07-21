"""Entry-time capital constraint for paper deployments.

Why: a paper deployment used to open every confirmed signal independently, so a
run "sized" off Rs 2L could hold Rs 30L+ of concurrent premium — paper results
were not evidence the strategy is tradable in a real account. This module makes
a paper deployment behave like a demat account of configurable size: before a
trade opens, its premium outlay must fit inside the available capital,
otherwise the signal is skipped AND journaled (never a silent drop).

Two independent layers, each optional and each off by default so existing
deployments keep their current behavior:

- Per-deployment: ``deployment.risk.capital = {"amount": float, "basis":
  "fixed" | "cumulative"}`` — set from the deploy wizard or the Paper caps
  editor. This is the default allocation model: each deployment is gated
  against its own configured capital, which is also the scope of the Paper
  page's required_capital metric, so the "never exceeds configured capital"
  invariant is checkable per deployment.
- Account-wide: the pre-existing ``app_settings`` key ``paper_account`` gains
  ``enforce_capital`` (bool) + ``capital_basis``. When enabled, the SUM of open
  premium across ALL paper deployments is additionally gated against
  ``starting_capital`` — the shared-demat view for running several
  deployments against one account.

Basis semantics (both computed off realized P&L of CLOSED trades, keyed by
closed_at in IST — the exact bucketing paper_analytics uses):

- fixed: buying power never grows with profits (no compounding — sizes off the
  configured amount forever) but losses DO debit it: an account that lost
  Rs 50k cannot redeploy that Rs 50k.
      base = amount + min(0, R_day, R_week, R_month, R_year, R_total)
  The min over the analytics window granularities makes the Paper page metric
  provably bounded: required_capital(bucket) = max(deployed − realized within
  bucket) ≤ amount for EVERY bucket granularity, because each entry is gated to
  deployed_after ≤ amount + R_g for each window g, and a close event can never
  push (deployed − cum) up (a long option's loss is capped at the premium paid).
- cumulative: base = amount + R_total — profits compound, losses debit.
  required_capital may exceed ``amount`` for a bucket only when funded by
  profits banked BEFORE that bucket started (the account genuinely held that
  equity — self-funded, not phantom capital).

available = base − committed, committed = Σ entry_price × quantity over OPEN
trades — the same unrounded per-trade outlay formula paper_analytics'
exposure()/required_capital sweep uses (NOT the 2dp-rounded stored entry_value).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.paper_analytics import _bucket_key, _f, _ist_day

CAPITAL_BASES = ("fixed", "cumulative")
_EPS = 1e-6

# Projection shared by both gate queries — everything the arithmetic needs.
_TRADE_PROJECTION = {"_id": 0, "status": 1, "entry_price": 1, "quantity": 1,
                     "realized_pnl": 1, "closed_at": 1, "updated_at": 1}


def parse_capital_config(value: Any) -> Optional[Dict[str, Any]]:
    """Normalize a {"amount", "basis"} capital config; None when absent or
    unusable — a missing/broken config must mean "no gate" (legacy behavior),
    never a crash or an accidental block."""
    if not isinstance(value, dict):
        return None
    try:
        amount = float(value.get("amount"))
    except (TypeError, ValueError):
        return None
    if not amount > 0:
        return None
    basis = str(value.get("basis") or "fixed").lower()
    if basis not in CAPITAL_BASES:
        basis = "fixed"
    return {"amount": amount, "basis": basis}


def committed_exposure(trades: List[Dict[str, Any]]) -> float:
    """Premium outlay currently deployed in OPEN trades."""
    return sum(
        _f(t.get("entry_price")) * _f(t.get("quantity"))
        for t in trades
        if str(t.get("status") or "").upper() == "OPEN"
    )


def realized_windows(trades: List[Dict[str, Any]], now: Any) -> Dict[str, float]:
    """Realized P&L of CLOSED trades summed over the current IST day / week /
    month / year windows (keyed by closed_at, same bucketing as the analytics
    sweep) plus the all-time total."""
    today = _ist_day(now)
    out = {"day": 0.0, "week": 0.0, "month": 0.0, "year": 0.0, "total": 0.0}
    for t in trades:
        if str(t.get("status") or "").upper() != "CLOSED":
            continue
        pnl = _f(t.get("realized_pnl"))
        out["total"] += pnl
        day = _ist_day(t.get("closed_at") or t.get("updated_at"))
        if day is None or today is None:
            continue
        for period in ("day", "week", "month", "year"):
            if _bucket_key(day, period) == _bucket_key(today, period):
                out[period] += pnl
    return out


def evaluate_capital_gate(
    cfg: Dict[str, Any],
    trades: List[Dict[str, Any]],
    new_exposure: float,
    *,
    scope: str,
    now: Any = None,
) -> Dict[str, Any]:
    """Pure verdict: does a trade costing ``new_exposure`` rupees of premium fit
    the capital config given the deployment's (or account's) trade history?"""
    if isinstance(now, datetime):
        now = now.isoformat()
    now = now or datetime.now(timezone.utc).isoformat()
    committed = committed_exposure(trades)
    r = realized_windows(trades, now)
    if cfg["basis"] == "cumulative":
        base = cfg["amount"] + r["total"]
    else:
        base = cfg["amount"] + min(
            0.0, r["day"], r["week"], r["month"], r["year"], r["total"])
    available = base - committed
    need = float(new_exposure)
    out = {
        "allowed": need <= available + _EPS,
        "scope": scope,
        "basis": cfg["basis"],
        "capital": round(cfg["amount"], 2),
        "committed": round(committed, 2),
        "available": round(available, 2),
        "need": round(need, 2),
    }
    if not out["allowed"]:
        out["reason"] = (
            f"capital_gate:{scope}:need=₹{out['need']:,.0f} "
            f"available=₹{out['available']:,.0f} "
            f"(open=₹{out['committed']:,.0f} of ₹{out['capital']:,.0f}, {cfg['basis']})"
        )
    return out


async def load_account_capital_config(db: Any) -> Optional[Dict[str, Any]]:
    """The opt-in account-wide ceiling: paper_account.starting_capital enforced
    only when enforce_capital is truthy. getattr-guarded so test fakes without
    an app_settings collection behave as 'not configured' (motor always exposes
    the attribute)."""
    coll = getattr(db, "app_settings", None)
    if coll is None:
        return None
    doc = await coll.find_one({"key": "paper_account"}, {"_id": 0})
    if not doc or not doc.get("enforce_capital"):
        return None
    return parse_capital_config({
        "amount": doc.get("starting_capital"),
        "basis": doc.get("capital_basis") or "fixed",
    })


async def check_capital_gate(
    db: Any,
    deployment: Dict[str, Any],
    *,
    new_exposure: float,
    now: Any = None,
) -> Dict[str, Any]:
    """Both layers, cheapest-first: the per-deployment gate scans only this
    deployment's trades; the account-wide gate (opt-in) scans all paper trades.
    First failed layer wins the verdict; no config anywhere → allowed."""
    dep_cfg = parse_capital_config((deployment.get("risk") or {}).get("capital"))
    acct_cfg = await load_account_capital_config(db)
    if dep_cfg is None and acct_cfg is None:
        return {"allowed": True, "checks": []}
    checks: List[Dict[str, Any]] = []
    if dep_cfg is not None:
        rows = await db.paper_trades.find(
            {"deployment_id": str(deployment.get("id") or "")}, _TRADE_PROJECTION,
        ).to_list(length=None)
        verdict = evaluate_capital_gate(
            dep_cfg, rows, new_exposure, scope="deployment", now=now)
        if not verdict["allowed"]:
            return verdict
        checks.append(verdict)
    if acct_cfg is not None:
        rows = await db.paper_trades.find({}, _TRADE_PROJECTION).to_list(length=None)
        verdict = evaluate_capital_gate(
            acct_cfg, rows, new_exposure, scope="account", now=now)
        if not verdict["allowed"]:
            return verdict
        checks.append(verdict)
    return {"allowed": True, "checks": checks}
