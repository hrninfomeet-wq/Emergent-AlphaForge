"""A stale mark is UNKNOWN, never zero.

Once the guard persists a real mark (`on_mark`), `_open_unrealized_today` finally
has a number to sum. But the guard only marks while it is running and the broker
book is readable — exactly the conditions that fail during a token expiry, a
network outage or a crashed guard task.

If a stale mark kept being summed as its last value (or an absent one as 0.0), the
day-stop would silently go back to being blind at precisely the moment risk is
least observable. The existing `account_exposure_invalid` path already models
"I don't know the number": refuse the entry, do NOT pause, do NOT escalate a data
defect into an account-wide halt.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_deploy_governor import open_unrealized_today  # noqa: E402

_IST = timezone(timedelta(hours=5, minutes=30))


def _today():
    return datetime.now(_IST).strftime("%Y-%m-%d")


def _row(pnl, *, age_s=0.0, status="OPEN"):
    now = datetime.now(timezone.utc)
    return {
        "status": status,
        "created_at": now.isoformat(),
        "unrealized_pnl": pnl,
        "marked_at": (now - timedelta(seconds=age_s)).isoformat(),
    }


def test_a_fresh_mark_is_summed():
    total, unknown = open_unrealized_today([_row(-3250.0, age_s=2)], _today())
    assert total == -3250.0
    assert unknown is False


def test_a_stale_mark_is_unknown_not_its_last_value():
    total, unknown = open_unrealized_today([_row(-3250.0, age_s=600)], _today())
    assert unknown is True, (
        "a mark that stopped updating must read as UNKNOWN — summing its last "
        "value pretends the guard is still watching")


def test_an_unmarked_open_trade_is_unknown_not_zero():
    """The pre-fix state: unrealized_pnl 0.0 with no mark at all."""
    row = _row(0.0)
    row.pop("marked_at")
    total, unknown = open_unrealized_today([row], _today())
    assert unknown is True, (
        "an OPEN trade that was never marked must not be summed as a 0.0 loss — "
        "that is exactly the blindness this fixes")


def test_closed_trades_need_no_mark():
    """Only OPEN trades carry unrealized risk; a CLOSED row is settled."""
    row = _row(0.0, status="CLOSED")
    row.pop("marked_at")
    total, unknown = open_unrealized_today([row], _today())
    assert unknown is False and total == 0.0


def test_several_fresh_marks_sum():
    total, unknown = open_unrealized_today(
        [_row(-1000.0, age_s=1), _row(-250.0, age_s=3)], _today())
    assert total == -1250.0 and unknown is False


def test_one_stale_mark_taints_the_whole_account():
    """Partial knowledge of account exposure is not knowledge."""
    total, unknown = open_unrealized_today(
        [_row(-1000.0, age_s=1), _row(-250.0, age_s=999)], _today())
    assert unknown is True


# --- the whole point: an open loss must actually trip the cap ---------------

def test_a_held_losing_position_now_trips_the_daily_loss_cap():
    """End-to-end proof of the gap this closes.

    Before position marking, `unrealized_pnl` stayed 0.0 for the life of a live
    trade, so a held position running against the account could never breach
    `daily_loss_cap` — the stop could only ever fire on CLOSED trades.
    """
    import asyncio
    from app.live_deploy_governor import check_live_caps

    now = datetime.now(timezone.utc)

    class _DB:
        class live_trades:
            @staticmethod
            def find(_q, *a, **k):
                class _C:
                    async def to_list(self, length=None):
                        return [{
                            "deployment_id": "dep1", "status": "OPEN", "lots": 1,
                            "realized_pnl": 0.0, "unrealized_pnl": -5001.0,
                            "created_at": now.isoformat(),
                            "marked_at": now.isoformat(),   # guard is watching
                        }]
                return _C()

    dep = {"id": "dep1", "risk": {"live": {"daily_loss_cap": 5000}}}
    out = asyncio.run(check_live_caps(_DB(), dep, capped_lots=1, now_utc=now))
    assert out["allow"] is False
    assert out["reason"] == "daily_loss_cap"
    assert out["pause"] is True


def test_a_stale_mark_refuses_without_pausing():
    """Liveness defect != measured breach: refuse the entry, do not halt."""
    import asyncio
    from app.live_deploy_governor import check_live_caps

    now = datetime.now(timezone.utc)

    class _DB:
        class live_trades:
            @staticmethod
            def find(_q, *a, **k):
                class _C:
                    async def to_list(self, length=None):
                        return [{
                            "deployment_id": "dep1", "status": "OPEN", "lots": 1,
                            "realized_pnl": 0.0, "unrealized_pnl": -5001.0,
                            "created_at": now.isoformat(),
                            "marked_at": (now - timedelta(hours=1)).isoformat(),
                        }]
                return _C()

    dep = {"id": "dep1", "risk": {"live": {"daily_loss_cap": 5000}}}
    out = asyncio.run(check_live_caps(_DB(), dep, capped_lots=1, now_utc=now))
    assert out["allow"] is False
    assert out["reason"] == "exposure_unknown"
    assert out["pause"] is False
