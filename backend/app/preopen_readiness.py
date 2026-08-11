"""Say whether the day can trade BEFORE the bell, not after it fails.

On 2026-08-04 the candle roller started at 10:26 IST — **71 minutes after the
open** — because the roller, stream and exit monitor cannot start until a human
completes the daily Upstox OAuth. Nothing runs before the market opens: the only
warehouse job is the 18:00 auto-update, i.e. after the close. Every readiness
signal already exists in the system; none of it ever ran early enough to help.

For an unattended bot that failure is silent and total. The system is
structurally dead from 09:15 until somebody happens to open a browser, and no
surface says so — the Live page simply shows no signals, which is exactly what a
quiet market looks like.

This module is a PURE verdict. It reports; it does not trade, fetch, write or
block. It is deliberately NOT a new gate: the standing project rule is that
arming gates are removed, not added, and an unready day must still be the
operator's call. Its only job is to make "not ready" visible at 08:45 instead of
discoverable at 10:26.

Blockers vs warnings is the load-bearing distinction:

* **blocker** — the day CANNOT trade as configured (no market data feed at all;
  no broker while a live deployment is armed).
* **warning** — the day can trade but something is degraded (outstanding
  warehouse work). Reporting a data gap as "cannot trade" would train the
  operator to ignore the signal, which costs more than the gap does.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _connected(status: Optional[Dict[str, Any]]) -> bool:
    return bool((status or {}).get("connected"))


def _expired(status: Optional[Dict[str, Any]]) -> bool:
    return bool((status or {}).get("expired"))


def evaluate_preopen_readiness(
    *,
    is_trading_day: bool,
    upstox_status: Optional[Dict[str, Any]],
    flattrade_status: Optional[Dict[str, Any]],
    live_deployment_count: int = 0,
    warehouse_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return ``{ready, blockers, warnings, checks, reason}`` for the coming session.

    Pure — no DB, no clock, no network. The caller gathers the inputs so this
    policy stays directly testable.
    """
    blockers: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    checks: Dict[str, Any] = {}

    checks["trading_day"] = bool(is_trading_day)
    if not is_trading_day:
        # A holiday is not unreadiness. Reporting red every Sunday would train the
        # operator to ignore the one morning it matters.
        return {
            "ready": True,
            "reason": "not_a_trading_day",
            "blockers": [],
            "warnings": [],
            "checks": checks,
        }

    # --- market data: the 71-minute dead zone ---------------------------------
    checks["upstox"] = {
        "connected": _connected(upstox_status),
        "expired": _expired(upstox_status),
    }
    if not _connected(upstox_status):
        blockers.append({
            "id": "upstox_not_connected",
            "detail": (
                "Upstox is not connected, so the live candle roller, tick stream "
                "and exit monitor cannot start. Complete the daily Upstox OAuth "
                "before 09:15 IST or the session produces no bars and no signals."
            ),
        })
    elif _expired(upstox_status):
        blockers.append({
            "id": "upstox_token_expired",
            "detail": (
                "The Upstox token has expired. Re-authorise before 09:15 IST — an "
                "expired token stops the roller exactly as a missing one does."
            ),
        })

    # --- broker: only load-bearing when something is actually live ------------
    checks["flattrade"] = {
        "connected": _connected(flattrade_status),
        "expired": _expired(flattrade_status),
        "required": int(live_deployment_count or 0) > 0,
    }
    if int(live_deployment_count or 0) > 0:
        if not _connected(flattrade_status):
            blockers.append({
                "id": "flattrade_not_connected",
                "detail": (
                    f"{live_deployment_count} deployment(s) are in LIVE mode but "
                    "Flattrade is not connected: entries cannot transmit and the "
                    "exit guard cannot read the position book."
                ),
            })
        elif _expired(flattrade_status):
            blockers.append({
                "id": "flattrade_token_expired",
                "detail": (
                    "The Flattrade session has expired while live deployments are "
                    "armed. Re-authorise before the open."
                ),
            })

    # --- warehouse: degraded, not fatal --------------------------------------
    pending = int(((warehouse_plan or {}).get("summary") or {}).get("total_actions") or 0)
    checks["warehouse"] = {"pending_actions": pending}
    if pending > 0:
        warnings.append({
            "id": "warehouse_actions_pending",
            "detail": (
                f"{pending} warehouse action(s) outstanding. Live trading is "
                "unaffected; backtests and option-band coverage may be stale."
            ),
        })

    return {
        "ready": not blockers,
        "reason": "ready" if not blockers else "blocked",
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
    }
