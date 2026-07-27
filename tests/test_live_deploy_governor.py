"""Tests for live deployment caps governor (check_live_caps).

TDD: these tests were written before the implementation.
FakeDB mirrors the live_trades collection (same shape as paper_trades).
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_deploy_governor import check_live_caps  # noqa: E402


# ---------------------------------------------------------------------------
# FakeDB harness (mirrors the paper_trades harness in test_deployment_kill_switch.py)
# ---------------------------------------------------------------------------

class _Cursor:
    def __init__(self, rows: List[Dict]):
        self._rows = list(rows)
        self.called = True

    def sort(self, *a, **k):
        return self

    async def to_list(self, length=None):
        return list(self._rows)


class _LiveColl:
    def __init__(self, rows: List[Dict]):
        self.rows = rows
        self.find_called = False

    def find(self, query, projection=None):
        self.find_called = True
        filtered = [
            r for r in self.rows
            if all(r.get(k) == v for k, v in query.items())
        ]
        return _Cursor(filtered)


class _DB:
    def __init__(self, rows: List[Dict]):
        self.live_trades = _LiveColl(rows)


# ---------------------------------------------------------------------------
# Helpers for building trade rows
# ---------------------------------------------------------------------------

def _trade(
    *,
    deployment_id: str = "dep1",
    status: str = "OPEN",
    lots: int = 1,
    realized_pnl: float = 0.0,
    unrealized_pnl: float = 0.0,
    created_at: str = "2026-06-25T04:00:00+00:00",  # IST 2026-06-25 09:30
    closed_at: Optional[str] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "deployment_id": deployment_id,
        "status": status,
        "lots": lots,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "created_at": created_at,
    }
    if closed_at is not None:
        row["closed_at"] = closed_at
    return row


# A fixed now_utc whose IST date is 2026-06-25
NOW_UTC = datetime(2026, 6, 25, 4, 0, 0, tzinfo=timezone.utc)  # IST = 09:30

# Trade entered today (IST 2026-06-25)
_TODAY_AT = "2026-06-25T04:00:00+00:00"  # UTC 04:00 = IST 09:30
# Trade entered yesterday (IST 2026-06-24)
_YEST_AT = "2026-06-24T04:00:00+00:00"   # UTC 04:00 = IST 09:30 on 24th

DEP = {"id": "dep1", "risk": {"live": {}}}


def _dep_with(**caps) -> Dict[str, Any]:
    return {"id": "dep1", "risk": {"live": caps}}


# ===========================================================================
# 1. No caps configured → allow True, DB NOT queried
# ===========================================================================

def test_no_caps_returns_allow_without_db_query():
    db = _DB([])
    dep = {"id": "dep1", "risk": {"live": {}}}
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result == {"allow": True, "reason": "ok", "pause": False}
    assert db.live_trades.find_called is False


def test_no_live_key_returns_allow_without_db_query():
    """If 'live' sub-key is absent entirely, treat as no caps."""
    db = _DB([])
    dep = {"id": "dep1", "risk": {}}
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result == {"allow": True, "reason": "ok", "pause": False}
    assert db.live_trades.find_called is False


# ===========================================================================
# 2. All-pass: caps set, all under limit
# ===========================================================================

def test_all_pass_when_under_all_limits():
    rows = [
        _trade(status="OPEN",   lots=1, unrealized_pnl=100, created_at=_TODAY_AT),
        _trade(status="CLOSED", lots=1, realized_pnl=200,
               created_at=_TODAY_AT, closed_at=_TODAY_AT),
    ]
    db = _DB(rows)
    dep = _dep_with(max_concurrent=5, max_lots_per_day=10, daily_loss_cap=5000)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result == {"allow": True, "reason": "ok", "pause": False}


# ===========================================================================
# 3. max_concurrent
# ===========================================================================

def test_max_concurrent_blocks_when_open_gte_limit():
    rows = [
        _trade(status="OPEN", created_at=_TODAY_AT),
        _trade(status="OPEN", created_at=_TODAY_AT),
    ]
    db = _DB(rows)
    dep = _dep_with(max_concurrent=2)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result["allow"] is False
    assert result["reason"] == "max_concurrent"
    assert result["pause"] is False


def test_max_concurrent_allows_when_open_below_limit():
    rows = [_trade(status="OPEN", created_at=_TODAY_AT)]
    db = _DB(rows)
    dep = _dep_with(max_concurrent=2)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result["allow"] is True


def test_max_concurrent_ignores_closed_trades():
    """CLOSED trades must not count toward max_concurrent."""
    rows = [
        _trade(status="CLOSED", created_at=_TODAY_AT),
        _trade(status="CLOSED", created_at=_TODAY_AT),
    ]
    db = _DB(rows)
    dep = _dep_with(max_concurrent=2)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result["allow"] is True


def test_max_concurrent_ignores_other_deployments():
    """Trades from a different deployment must not count."""
    rows = [
        _trade(deployment_id="other", status="OPEN", created_at=_TODAY_AT),
        _trade(deployment_id="other", status="OPEN", created_at=_TODAY_AT),
    ]
    # dep1's live_trades query will filter by deployment_id=dep1 → 0 open rows
    db = _DB(rows)
    dep = _dep_with(max_concurrent=2)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    # The FakeDB filters by deployment_id from the query, so dep1 sees 0 rows → allow
    assert result["allow"] is True


# ===========================================================================
# 4. max_lots_per_day
# ===========================================================================

def test_max_lots_per_day_blocks_when_lots_plus_capped_exceeds_limit():
    """lots_today=3, capped_lots=2, limit=4 → 3+2=5 > 4 → block."""
    rows = [
        _trade(lots=2, status="CLOSED", created_at=_TODAY_AT),
        _trade(lots=1, status="OPEN",   created_at=_TODAY_AT),
    ]
    db = _DB(rows)
    dep = _dep_with(max_lots_per_day=4)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=2, now_utc=NOW_UTC))
    assert result["allow"] is False
    assert result["reason"] == "max_lots_per_day"
    assert result["pause"] is False


def test_max_lots_per_day_allows_when_exactly_equal_to_limit():
    """lots_today=3, capped_lots=1, limit=4 → 3+1=4 == 4 → allow (not strictly >)."""
    rows = [_trade(lots=3, status="CLOSED", created_at=_TODAY_AT)]
    db = _DB(rows)
    dep = _dep_with(max_lots_per_day=4)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result["allow"] is True


def test_max_lots_per_day_ignores_yesterday_trades():
    """Lots from yesterday must not count toward today's limit."""
    rows = [_trade(lots=5, status="CLOSED", created_at=_YEST_AT)]
    db = _DB(rows)
    dep = _dep_with(max_lots_per_day=4)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result["allow"] is True


# ===========================================================================
# 5. daily_loss_cap
# ===========================================================================

def test_daily_loss_cap_realized_breach_pauses():
    """Realized loss today: −6000; cap = 5000 → 0 + (−6000) <= −5000 → pause.

    daily_realized_summary uses closed_at (not created_at) to attribute a trade
    to a calendar day, so CLOSED trades must carry closed_at.
    """
    rows = [
        _trade(status="CLOSED", realized_pnl=-6000, lots=1,
               created_at=_TODAY_AT, closed_at=_TODAY_AT),
    ]
    db = _DB(rows)
    dep = _dep_with(daily_loss_cap=5000)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result["allow"] is False
    assert result["reason"] == "daily_loss_cap"
    assert result["pause"] is True


def test_daily_loss_cap_open_unrealized_breach_pauses():
    """Realized=0, open unrealized=−5001 today → −5001 <= −5000 → pause."""
    rows = [
        _trade(status="OPEN", unrealized_pnl=-5001, lots=1, created_at=_TODAY_AT),
    ]
    db = _DB(rows)
    dep = _dep_with(daily_loss_cap=5000)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result["allow"] is False
    assert result["reason"] == "daily_loss_cap"
    assert result["pause"] is True


def test_daily_loss_cap_not_breached_allows():
    """Realized=−2000, open unrealized=−2000 today → −4000 > −5000 → allow."""
    rows = [
        _trade(status="CLOSED", realized_pnl=-2000, lots=1,
               created_at=_TODAY_AT, closed_at=_TODAY_AT),
        _trade(status="OPEN",   unrealized_pnl=-2000, lots=1, created_at=_TODAY_AT),
    ]
    db = _DB(rows)
    dep = _dep_with(daily_loss_cap=5000)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result["allow"] is True


def test_daily_loss_cap_wins_over_max_concurrent_block():
    """Loss breach must be headline even when max_concurrent would also block.

    Precedence: loss_cap > max_lots_per_day > max_concurrent.
    """
    rows = [
        _trade(status="OPEN", unrealized_pnl=-6000, lots=1, created_at=_TODAY_AT),
        _trade(status="OPEN", unrealized_pnl=0,     lots=1, created_at=_TODAY_AT),
    ]
    db = _DB(rows)
    dep = _dep_with(daily_loss_cap=5000, max_concurrent=2)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    # Both would fire; loss_cap must win as the reason
    assert result["allow"] is False
    assert result["reason"] == "daily_loss_cap"
    assert result["pause"] is True


def test_daily_loss_cap_open_unrealized_only_counts_today_open():
    """Open-unrealized from YESTERDAY must not be counted.

    Yesterday's OPEN trade has unrealized=-6000 but is excluded from today's
    open-unrealized sum because created_at is on a different IST day.
    Today's CLOSED trade contributes realized=-100 (closed_at required).
    Total = -100 > -5000 → allow.
    """
    rows = [
        # Yesterday's OPEN trade — excluded from today's unrealized sum
        _trade(status="OPEN", unrealized_pnl=-6000, lots=1, created_at=_YEST_AT),
        # Today's CLOSED trade — contributes to realized (closed_at required for daily_realized_summary)
        _trade(status="CLOSED", realized_pnl=-100, lots=1,
               created_at=_TODAY_AT, closed_at=_TODAY_AT),
    ]
    db = _DB(rows)
    dep = _dep_with(daily_loss_cap=5000)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    # realized_today=-100, open_unrealized_today=0 → total=-100 > -5000 → allow
    assert result["allow"] is True


# ===========================================================================
# 8. Poisoned daily_loss_cap (NaN / Infinity) — refuse, never silently uncapped
# ===========================================================================

def test_nan_daily_loss_cap_refuses_and_pauses():
    """json.loads accepts a NaN literal and every NaN comparison is False —
    without the guard a NaN cap silently disables the loss breaker
    (release-audit finding H2)."""
    db = _DB([])
    dep = _dep_with(daily_loss_cap=float("nan"), max_concurrent=2)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result == {"allow": False, "reason": "invalid_daily_loss_cap", "pause": True}


def test_infinite_daily_loss_cap_refuses_and_pauses():
    """An Infinity cap is an unbounded (= disabled) breaker: refuse it too."""
    db = _DB([])
    dep = _dep_with(daily_loss_cap=float("inf"), max_concurrent=2)
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result == {"allow": False, "reason": "invalid_daily_loss_cap", "pause": True}


def test_nan_only_cap_on_live_mode_hits_caps_missing_fail_closed():
    """If NaN is the ONLY configured cap, _live_caps_configured sees a cap-less
    doc; a live-mode deployment then refuses via the existing live_caps_missing
    fail-closed branch (never trades unbounded)."""
    db = _DB([])
    dep = {"id": "dep1", "mode": "live",
           "risk": {"live": {"daily_loss_cap": float("nan")}}}
    result = asyncio.run(check_live_caps(db, dep, capped_lots=1, now_utc=NOW_UTC))
    assert result == {"allow": False, "reason": "live_caps_missing", "pause": True}


# ===========================================================================
# C3 — ACCOUNT-GLOBAL caps.
#
# check_live_caps counts only the deployment's OWN trades, so exposure opened by
# a sibling deployment was invisible: five open positions or a large loss
# elsewhere could not block a new entry, and the account-level guardrails
# (max_open_positions / daily_loss_limit) had no production caller at all.
# ===========================================================================

from app.live_deploy_governor import check_account_caps  # noqa: E402


class _AcctDB:
    """live_trades across MANY deployments (the account view)."""
    def __init__(self, rows):
        self.live_trades = _LiveColl(rows)


_ACCT_CONFIG = {
    "daily_loss_limit": 5000,
    "profit_lock_target": 10000,
    "max_open_positions": 5,
    "max_lots_per_order": 20,
    "blocked_until_reset": False,
}


def test_account_caps_allow_when_account_is_clean():
    db = _AcctDB([_trade(deployment_id="dep1", status="OPEN", created_at=_TODAY_AT)])
    out = asyncio.run(check_account_caps(db, config=_ACCT_CONFIG, now_utc=NOW_UTC))
    assert out["allow"] is True


def test_account_open_positions_in_OTHER_deployments_block_a_new_entry():
    """THE C3 scenario: this deployment has none open, but the ACCOUNT is at the
    cap because of sibling deployments."""
    rows = [_trade(deployment_id=f"other{i}", status="OPEN", created_at=_TODAY_AT) for i in range(5)]
    db = _AcctDB(rows)
    out = asyncio.run(check_account_caps(db, config=_ACCT_CONFIG, now_utc=NOW_UTC))
    assert out["allow"] is False
    assert out["reason"] == "account_max_open_block"


def test_account_daily_loss_across_deployments_halts_entries():
    """Losses in sibling deployments count toward the ACCOUNT daily-loss limit."""
    rows = [
        _trade(deployment_id="other1", status="CLOSED", realized_pnl=-3000,
               created_at=_TODAY_AT, closed_at=_TODAY_AT),
        _trade(deployment_id="other2", status="CLOSED", realized_pnl=-2500,
               created_at=_TODAY_AT, closed_at=_TODAY_AT),
    ]
    db = _AcctDB(rows)
    out = asyncio.run(check_account_caps(db, config=_ACCT_CONFIG, now_utc=NOW_UTC))
    assert out["allow"] is False
    assert out["reason"] == "account_broker_stop_loss"
    assert out["pause"] is True


def test_account_loss_breach_trips_the_engine_latch():
    """A loss breach must HALT the account, not just refuse this one entry —
    this is guardrail_tick's first production caller."""
    calls = []

    class _Engine:
        async def guardrail_tick(self, mtm, open_count):
            calls.append((mtm, open_count))
            return "broker_stop_loss"

    rows = [_trade(deployment_id="o1", status="CLOSED", realized_pnl=-9000,
                   created_at=_TODAY_AT, closed_at=_TODAY_AT)]
    db = _AcctDB(rows)
    out = asyncio.run(check_account_caps(db, config=_ACCT_CONFIG, engine=_Engine(), now_utc=NOW_UTC))
    assert out["allow"] is False
    assert len(calls) == 1, "guardrail_tick was not invoked on an account loss breach"


def test_account_latch_blocks_every_entry():
    """Once the safety latch is tripped, nothing enters until an explicit reset."""
    db = _AcctDB([])
    cfg = {**_ACCT_CONFIG, "blocked_until_reset": True}
    out = asyncio.run(check_account_caps(db, config=cfg, now_utc=NOW_UTC))
    assert out["allow"] is False
    assert out["reason"] == "account_latched"


def test_account_caps_fail_closed_on_unreadable_trades():
    """If the account exposure cannot be read we must NOT trade blind."""
    class _Boom:
        def find(self, *a, **k):
            raise RuntimeError("mongo down")

    class _DB:
        live_trades = _Boom()

    out = asyncio.run(check_account_caps(_DB(), config=_ACCT_CONFIG, now_utc=NOW_UTC))
    assert out["allow"] is False
    assert "unavailable" in out["reason"]


def test_account_poisoned_pnl_refuses_entry_WITHOUT_latching_the_account():
    """A NaN in one journal row must not escalate into an account-wide halt.

    json.loads accepts NaN, so a single malformed live_trades row can make the
    account MTM non-finite. evaluate_guardrails' fail-safe maps that to
    "broker_stop_loss" — correct for "don't trade on unknown P&L", but if we
    forwarded it to guardrail_tick a DATA DEFECT would trip the sticky latch and
    halt the engine until a human reset it. Refuse the entry; do not stop the desk.
    """
    calls = []

    class _Engine:
        async def guardrail_tick(self, mtm, open_count):
            calls.append((mtm, open_count))
            return "broker_stop_loss"

    db = _AcctDB([_trade(deployment_id="o1", status="OPEN", created_at=_TODAY_AT,
                         unrealized_pnl=float("nan"))])
    out = asyncio.run(check_account_caps(db, config=_ACCT_CONFIG, engine=_Engine(),
                                         now_utc=NOW_UTC))
    assert out["allow"] is False
    assert "exposure_invalid" in out["reason"]
    assert out["pause"] is False
    assert calls == [], "a malformed row must never trip the account latch"
