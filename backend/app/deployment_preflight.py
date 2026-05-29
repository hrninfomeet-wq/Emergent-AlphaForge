"""Data realism pre-flight check for Strategy Deployments.

Per user spec (2026-05-27): informational, never blocks deployment creation.
Surfaces data-quality concerns so the user makes informed choices.

Checks performed for a given instrument:
  - Spot candle coverage in the last N trading days
  - Option contracts present for the next K upcoming expiries
  - Whether all "next" contracts are actually expired (a real bug we hit on 2026-05-28)
  - Known structural breaks for the instrument (NIFTY weekly day rotation,
    BANKNIFTY weekly discontinuation since 2024-11)
  - Upstox token connection state

Returns a status of "verified" / "warning" / "degraded" plus a list of check
records. The deployment creation flow displays these but does not refuse based
on the result.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.instruments import UNDERLYING_META

log = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)

# Status levels in order of severity
STATUS_VERIFIED = "verified"
STATUS_WARNING = "warning"
STATUS_DEGRADED = "degraded"
_STATUS_RANK = {STATUS_VERIFIED: 0, STATUS_WARNING: 1, STATUS_DEGRADED: 2}


def _today_ist_iso() -> str:
    return (datetime.now(timezone.utc) + IST_OFFSET).strftime("%Y-%m-%d")


def _aggregate_status(checks: List[Dict[str, Any]]) -> str:
    """Return the worst status across the check list."""
    worst = STATUS_VERIFIED
    for check in checks:
        status = str(check.get("status") or STATUS_VERIFIED)
        if _STATUS_RANK.get(status, 0) > _STATUS_RANK.get(worst, 0):
            worst = status
    return worst


def _structural_breaks_for(instrument: str) -> List[Dict[str, Any]]:
    """Hand-curated structural break warnings.

    Maintenance: review when broker/exchange announces changes. Last reviewed 2026-05-27.
    """
    notes: List[Dict[str, Any]] = []
    inst = instrument.upper()
    if inst == "BANKNIFTY":
        notes.append({
            "id": "banknifty_weekly_discontinued",
            "status": STATUS_WARNING,
            "label": "BANKNIFTY weekly options discontinued",
            "detail": "NSE/SEBI discontinued BANKNIFTY weekly options around 2024-11. "
                      "Only monthly expiries are tradable from that date forward. "
                      "DTE filters that assume weekly availability will silently fall back to monthly contracts.",
        })
    if inst == "NIFTY":
        notes.append({
            "id": "nifty_weekly_day_rotation",
            "status": STATUS_VERIFIED,
            "label": "NIFTY weekly expiry day rotation history",
            "detail": "NIFTY weekly expiry day rotated: Thursday until Aug 2024, Wednesday Sep 2024 - Mar 2025, Tuesday Apr 2025+. "
                      "The deployment evaluator reads expiry dates from option_contracts and is unaffected, "
                      "but historical backtests that hard-code a weekday will misalign.",
        })
    if inst == "SENSEX":
        notes.append({
            "id": "sensex_friday_expiry",
            "status": STATUS_VERIFIED,
            "label": "SENSEX weekly on BSE",
            "detail": "SENSEX weekly options trade on BSE with Friday expiry, generally lower depth than NIFTY weekly.",
        })
    return notes


async def _check_spot_coverage(db: Any, instrument: str, lookback_days: int) -> Dict[str, Any]:
    """How many of the last N trading days have ANY 1m candles for this instrument?

    A purely structural check - we count distinct session_dates with candles vs
    the expected count of weekday days in the window. Holidays will cause small
    expected mismatches; we treat coverage >= 80% as verified, 60-80% as warning,
    below 60% as degraded.
    """
    today_ist = _today_ist_iso()
    start_ist = (datetime.now(timezone.utc) + IST_OFFSET - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    cursor = db.candles_1m.find(
        {"instrument": instrument.upper()},
        {"_id": 0, "session_date": 1, "ts": 1},
    )
    rows = await cursor.to_list(length=None)
    distinct_dates = {
        r.get("session_date")
        for r in rows
        if r.get("session_date") and start_ist <= r.get("session_date") <= today_ist
    }

    # Expected = weekdays in window. Holidays are unknown so this is an upper bound.
    expected = 0
    cur = datetime.now(timezone.utc) + IST_OFFSET - timedelta(days=lookback_days)
    end = datetime.now(timezone.utc) + IST_OFFSET
    while cur.date() <= end.date():
        if cur.weekday() < 5:
            expected += 1
        cur += timedelta(days=1)

    found = len(distinct_dates)
    coverage_pct = round((found / max(1, expected)) * 100, 1)
    if coverage_pct >= 80:
        status = STATUS_VERIFIED
    elif coverage_pct >= 60:
        status = STATUS_WARNING
    else:
        status = STATUS_DEGRADED

    return {
        "id": "spot_coverage",
        "status": status,
        "label": f"Spot 1m coverage (last {lookback_days} days)",
        "detail": f"{found} of ~{expected} expected weekdays have candles ({coverage_pct}%).",
        "value": {
            "lookback_days": lookback_days,
            "expected_weekdays": expected,
            "found_dates": found,
            "coverage_pct": coverage_pct,
        },
    }


async def _check_option_contracts(db: Any, instrument: str, lookahead_expiries: int) -> Dict[str, Any]:
    """Are option contracts present in the warehouse for upcoming expiries?

    Warns if all the future-dated expiries we know about are too few or are stale.
    Critical because the evaluator picks contracts from option_contracts metadata
    and a missing future expiry means the strategy cannot fire on that DTE.
    """
    today = _today_ist_iso()
    expiries = await db.option_contracts.distinct(
        "expiry_date",
        {"underlying": instrument.upper(), "expiry_date": {"$gte": today}},
    )
    expiries = sorted(expiries)
    found = len(expiries)
    if found >= lookahead_expiries:
        status = STATUS_VERIFIED
        detail = f"{found} upcoming expiries known: {', '.join(expiries[:lookahead_expiries])}{'...' if found > lookahead_expiries else ''}"
    elif found > 0:
        status = STATUS_WARNING
        detail = (
            f"Only {found} upcoming expiries known (expected at least {lookahead_expiries}): "
            f"{', '.join(expiries)}. Sync expired-option contracts in Data Warehouse."
        )
    else:
        status = STATUS_DEGRADED
        detail = (
            f"No upcoming option contracts in store for {instrument}. "
            "Use Upstox option-contracts sync before creating a paper-mode deployment."
        )
    return {
        "id": "option_contracts_upcoming",
        "status": status,
        "label": f"Upcoming option expiries (>= today)",
        "detail": detail,
        "value": {
            "lookahead_expiries": lookahead_expiries,
            "found_count": found,
            "expiries": expiries[:lookahead_expiries],
        },
    }


async def _check_only_expired_contracts(db: Any, instrument: str) -> Dict[str, Any]:
    """Detect the exact bug we hit on 2026-05-28: contract picker resolved a
    live signal to an expired November-2024 contract because option_contracts
    contains both current and expired contracts and the picker doesn't filter.

    If the latest expiry across ALL option_contracts is in the past, this
    deployment cannot fire any meaningful live signal. Mark as degraded.
    """
    today = _today_ist_iso()
    all_expiries = await db.option_contracts.distinct(
        "expiry_date",
        {"underlying": instrument.upper()},
    )
    if not all_expiries:
        # Already covered by _check_option_contracts; don't double-warn here
        return {
            "id": "active_contracts_present",
            "status": STATUS_VERIFIED,
            "label": "Active option contracts present",
            "detail": "Skipped - no contracts at all (see Upcoming option expiries check).",
            "value": {"active_count": 0, "expired_count": 0},
        }
    active = [e for e in all_expiries if e and e >= today]
    expired = [e for e in all_expiries if e and e < today]
    if not active:
        status = STATUS_DEGRADED
        detail = (
            f"All {len(expired)} stored {instrument} contracts have expired (latest: {max(expired)}). "
            "The contract picker may resolve live signals to expired contracts. "
            "Sync current option contracts before activating this deployment."
        )
    else:
        status = STATUS_VERIFIED
        detail = f"{len(active)} active expiries vs {len(expired)} expired. Picker has live contracts to choose from."
    return {
        "id": "active_contracts_present",
        "status": status,
        "label": "Active vs expired option contracts",
        "detail": detail,
        "value": {"active_count": len(active), "expired_count": len(expired)},
    }


async def _check_upstox_token(db: Any) -> Dict[str, Any]:
    """Lightweight token state check using only the persisted token doc, no broker calls."""
    doc = await db.upstox_tokens.find_one({}, {"_id": 0})
    if not doc:
        return {
            "id": "upstox_token",
            "status": STATUS_DEGRADED,
            "label": "Upstox connection",
            "detail": "No Upstox token stored. OAuth must complete before live evaluation can run.",
            "value": {"connected": False, "expired": True},
        }
    expires_at = doc.get("expires_at")
    expired = bool(doc.get("expired") or False)
    if expires_at:
        try:
            expires_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            expired = expired or expires_dt <= datetime.now(timezone.utc)
        except Exception:
            pass
    if expired:
        return {
            "id": "upstox_token",
            "status": STATUS_WARNING,
            "label": "Upstox connection",
            "detail": f"Token expired (or expires very soon). Reconnect Upstox to keep live evaluation running.",
            "value": {"connected": False, "expired": True, "expires_at": str(expires_at)},
        }
    return {
        "id": "upstox_token",
        "status": STATUS_VERIFIED,
        "label": "Upstox connection",
        "detail": f"Token valid until {expires_at}.",
        "value": {"connected": True, "expired": False, "expires_at": str(expires_at)},
    }


async def compute_data_realism(
    db: Any,
    instrument: str,
    *,
    lookback_days: int = 30,
    lookahead_expiries: int = 4,
) -> Dict[str, Any]:
    """Compute a structured data realism report for a deployment instrument.

    Returns:
        {
            "instrument": "NIFTY",
            "computed_at": "...",
            "status": "verified" | "warning" | "degraded",
            "checks": [...],
            "structural_breaks": [...]
        }
    Never raises - failures inside individual checks become degraded check rows
    so the UI can show what failed without losing the rest of the report.
    """
    inst = instrument.upper()
    if inst not in UNDERLYING_META:
        return {
            "instrument": inst,
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "status": STATUS_DEGRADED,
            "checks": [{
                "id": "supported_instrument",
                "status": STATUS_DEGRADED,
                "label": "Supported instrument",
                "detail": f"{inst} is not in the supported list ({', '.join(UNDERLYING_META.keys())}).",
            }],
            "structural_breaks": [],
        }

    checks: List[Dict[str, Any]] = []
    for runner in (
        _check_spot_coverage(db, inst, lookback_days),
        _check_option_contracts(db, inst, lookahead_expiries),
        _check_only_expired_contracts(db, inst),
        _check_upstox_token(db),
    ):
        try:
            checks.append(await runner)
        except Exception as exc:
            log.exception("preflight check failed")
            checks.append({
                "id": "check_error",
                "status": STATUS_DEGRADED,
                "label": "Pre-flight check error",
                "detail": str(exc)[:200],
            })

    structural_breaks = _structural_breaks_for(inst)

    # Aggregate status spans both checks and structural breaks
    aggregate = _aggregate_status(checks + structural_breaks)

    return {
        "instrument": inst,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "lookahead_expiries": lookahead_expiries,
        "status": aggregate,
        "checks": checks,
        "structural_breaks": structural_breaks,
    }
