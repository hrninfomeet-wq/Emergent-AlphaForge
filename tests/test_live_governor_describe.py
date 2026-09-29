"""`describe_live_caps` — the governor's own answer to "why can't this trade?".

The Live Deployments pane must render enforcement, never re-derive it. The
handoff's first draft computed loss headroom from REALIZED P&L in JavaScript;
the governor gates on realized + OPEN-unrealized, so that figure under-reported
consumption exactly when a losing position was open — the moment it matters.

So describe runs the SAME steps the enforcing checks run (precheck → measure →
decide), and these tests pin that the two agree, case by case, including the
open-loser case where a realized-only computation diverges.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_deploy_governor import (  # noqa: E402
    check_account_caps,
    check_live_caps,
    describe_live_caps,
)
from tests.test_live_deploy_governor import (  # noqa: E402
    NOW_UTC,
    _DB,
    _TODAY_AT,
    _YEST_AT,
    _trade,
)

ACCOUNT = {"max_lots_per_order": 20, "max_open_positions": 5, "daily_loss_limit": 5000}


def _dep(mode="live", **caps):
    return {"id": "dep1", "mode": mode, "risk": {"live": caps}}


def _describe(dep, rows, *, account=ACCOUNT, connected=None, now=NOW_UTC):
    return asyncio.run(describe_live_caps(
        _DB(rows), dep, now_utc=now, account_config=account, connected=connected))


def _check(dep, rows, capped_lots):
    return asyncio.run(check_live_caps(_DB(rows), dep, capped_lots=capped_lots,
                                       now_utc=NOW_UTC))


# --------------------------------------------------------------------------- #
# Parity: describe's verdict IS check_live_caps' verdict
# --------------------------------------------------------------------------- #

CAPS = dict(lots=2, max_lots_per_day=6, max_concurrent=2, daily_loss_cap=3000.0)

PARITY_CASES = {
    "all clear": [],
    "open LOSER, realized zero": [   # a realized-only figure says 0 of 3000 used
        _trade(status="OPEN", unrealized_pnl=-3200.0)],
    "open loser + realized loss": [
        _trade(status="CLOSED", realized_pnl=-2000.0, closed_at=_TODAY_AT),
        _trade(status="OPEN", unrealized_pnl=-1100.0)],
    "stale mark": [_trade(status="OPEN", unrealized_pnl=-10.0, marked_at=None)],
    "lots exhausted": [_trade(status="CLOSED", lots=5, closed_at=_TODAY_AT)],
    "concurrency full": [_trade(status="OPEN", lots=1), _trade(status="OPEN", lots=1)],
    "stale OPEN row from yesterday": [_trade(status="OPEN", created_at=_YEST_AT)] * 2,
    "poisoned lots": [_trade(status="CLOSED", lots=float("nan"), closed_at=_TODAY_AT)],
}


@pytest.mark.parametrize("name", sorted(PARITY_CASES))
def test_describe_verdict_equals_the_enforcing_check(name):
    rows = PARITY_CASES[name]
    d = _describe(_dep(**CAPS), rows)
    assert d["error"] is None, d["error"]
    assert d["verdict"] == _check(_dep(**CAPS), rows, d["next_entry_lots"])


def test_an_open_loser_is_counted_in_loss_consumed():
    """THE case the handoff's first draft got wrong."""
    d = _describe(_dep(**CAPS), PARITY_CASES["open LOSER, realized zero"])
    c = d["consumed"]
    assert c["realized_today"] == 0.0
    assert c["open_unrealized"] == -3200.0
    assert c["day_pnl"] == -3200.0
    assert c["loss_headroom"] == -200.0          # breached: headroom <= 0
    assert d["verdict"]["reason"] == "daily_loss_cap"


@pytest.mark.parametrize("dep", [
    _dep(),                                              # live, no caps
    _dep(daily_loss_cap=float("nan"), max_concurrent=2),  # poisoned cap
    _dep(mode="paper"),                                  # paper, no caps
])
def test_fail_closed_prechecks_pass_through_as_themselves(dep):
    d = _describe(dep, [])
    assert d["verdict"] == _check(dep, [], 1)
    if dep["mode"] == "live" and not dep["risk"]["live"]:
        assert d["verdict"]["reason"] == "live_caps_missing"


def test_a_poisoned_lots_row_refuses_visibly_instead_of_raising():
    """It used to raise out of check_live_caps — fail-closed, but inexplicable."""
    rows = PARITY_CASES["poisoned lots"]
    assert _check(_dep(**CAPS), rows, 2)["reason"] == "lots_unmeasurable"
    d = _describe(_dep(**CAPS), rows)
    assert d["consumed"]["lots_today"] is None and d["error"] is None


# --------------------------------------------------------------------------- #
# Unknown is never zero
# --------------------------------------------------------------------------- #

def test_an_unknown_exposure_renders_as_unknown_not_zero():
    d = _describe(_dep(**CAPS), PARITY_CASES["stale mark"])
    c = d["consumed"]
    assert c["exposure_unknown"] is True
    assert c["open_unrealized"] is None and c["day_pnl"] is None
    assert c["loss_headroom"] is None
    assert d["verdict"]["reason"] == "exposure_unknown"


def test_an_unset_cap_is_null_not_zero():
    d = _describe(_dep(max_concurrent=2), [])
    assert d["caps"]["daily_loss_cap"] is None
    assert d["caps"]["max_lots_per_day"] is None
    assert d["consumed"]["loss_headroom"] is None


def test_stale_open_rows_are_surfaced():
    """They hold a concurrency slot but sit outside the loss sum — the 09-16
    stale doc held one of its deployment's two slots for eleven days."""
    d = _describe(_dep(**CAPS), PARITY_CASES["stale OPEN row from yesterday"])
    assert d["consumed"]["concurrent_now"] == 2
    assert d["consumed"]["open_rows_prior_days"] == 2
    assert d["verdict"]["reason"] == "max_concurrent"


def test_a_close_with_no_journalled_pnl_is_surfaced():
    rows = [_trade(status="CLOSED", realized_pnl=None, closed_at=_TODAY_AT)]
    d = _describe(_dep(**CAPS), rows)
    assert d["consumed"]["closed_today_null_realized"] == 1


# --------------------------------------------------------------------------- #
# The account layer
# --------------------------------------------------------------------------- #

def _acct_check(rows, cfg):
    return asyncio.run(check_account_caps(_DB(rows), config=cfg, engine=None,
                                          now_utc=NOW_UTC))


@pytest.mark.parametrize("rows,cfg", [
    ([], ACCOUNT),
    ([_trade(status="OPEN")] * 5, ACCOUNT),                       # max open
    ([_trade(status="OPEN", unrealized_pnl=-6000.0)], ACCOUNT),   # loss limit
    ([_trade(status="OPEN", marked_at=None)], ACCOUNT),           # unknown
    ([], dict(ACCOUNT, blocked_until_reset=True)),                # latched
])
def test_account_verdict_equals_the_enforcing_check(rows, cfg):
    d = asyncio.run(describe_live_caps(_DB(rows), _dep(**CAPS), now_utc=NOW_UTC,
                                       account_config=cfg))
    assert d["account_verdict"] == _acct_check(rows, cfg)


def test_describe_never_trips_the_account_latch():
    """A loss breach under the ENFORCING check (with an engine) halts the desk.
    describe runs the same decision and must never act on it."""
    calls = []

    class _Engine:
        async def guardrail_tick(self, *a):
            calls.append(a)

    rows = [_trade(status="OPEN", unrealized_pnl=-6000.0)]
    d = asyncio.run(describe_live_caps(_DB(rows), _dep(**CAPS), now_utc=NOW_UTC,
                                       account_config=ACCOUNT))
    assert d["account_verdict"]["reason"] == "account_broker_stop_loss"
    assert calls == []


def test_an_unreadable_account_config_fails_closed():
    d = _describe(_dep(**CAPS), [], account=None)
    assert d["account_verdict"]["reason"] == "account_config_unavailable"
    assert d["binding"]["layer"] == "account"


def test_an_account_ceiling_below_one_is_named():
    """build_live_deploy_context disables live entirely below 1 lot per order."""
    d = _describe(_dep(**CAPS), [], account=dict(ACCOUNT, max_lots_per_order=0))
    assert d["next_entry_lots"] is None
    assert d["binding"]["reason"] == "account_max_lots_per_order_unset"


# --------------------------------------------------------------------------- #
# Binding order: authorization → account → deployment (the entry path's own)
# --------------------------------------------------------------------------- #

def test_authorization_binds_first():
    d = _describe(_dep(**CAPS), PARITY_CASES["concurrency full"], connected=False)
    assert d["binding"] == {"layer": "authorization", "reason": "not_connected",
                            "pause": False}


def test_account_binds_before_deployment():
    rows = [_trade(status="OPEN")] * 5            # account full AND dep full
    d = _describe(_dep(**CAPS), rows, connected=True)
    assert d["binding"]["layer"] == "account"


def test_deployment_binds_when_nothing_above_refuses():
    d = _describe(_dep(**CAPS), PARITY_CASES["concurrency full"], connected=True)
    assert d["binding"] == {"layer": "deployment", "reason": "max_concurrent",
                            "pause": False}


def test_nothing_binds_when_everything_allows():
    d = _describe(_dep(**CAPS), [], connected=True)
    assert d["binding"] is None


def test_after_the_entry_cutoff_is_the_binding_reason():
    late = datetime(2026, 6, 25, 9, 45, tzinfo=timezone.utc)   # 15:15 IST
    d = _describe(_dep(**CAPS), [], connected=True, now=late)
    assert d["binding"]["reason"] == "after_entry_cutoff"


# --------------------------------------------------------------------------- #
# Never raises, never writes, never emits a NaN
# --------------------------------------------------------------------------- #

def test_it_never_raises_on_a_failing_database():
    class _Boom:
        class live_trades:
            @staticmethod
            def find(*a, **k):
                raise RuntimeError("mongo down")
    d = asyncio.run(describe_live_caps(_Boom(), _dep(**CAPS), now_utc=NOW_UTC,
                                       account_config=ACCOUNT))
    assert d["error"] is None or d["error"].startswith("describe_failed")
    assert d["account_verdict"]["reason"].startswith("account_exposure_unavailable")


def test_it_writes_nothing():
    class _NoWrites(_DB):
        def __init__(self, rows):
            super().__init__(rows)
            for name in ("update_one", "update_many", "insert_one", "replace_one"):
                setattr(self.live_trades, name, self._forbidden)

        @staticmethod
        async def _forbidden(*a, **k):
            raise AssertionError("describe wrote to the database")

    rows = [_trade(status="OPEN", unrealized_pnl=-6000.0)]
    d = asyncio.run(describe_live_caps(_NoWrites(rows), _dep(**CAPS), now_utc=NOW_UTC,
                                       account_config=ACCOUNT))
    assert d["error"] is None


def test_the_block_is_strict_json_even_with_poisoned_inputs():
    """The status route serializes with allow_nan=False: one NaN 500s the batch."""
    rows = [_trade(status="CLOSED", realized_pnl=float("nan"), closed_at=_TODAY_AT),
            _trade(status="OPEN", unrealized_pnl=float("inf"))]
    dep = _dep(daily_loss_cap=float("nan"), max_concurrent=float("inf"), lots="x")
    d = _describe(dep, rows, account=dict(ACCOUNT, daily_loss_limit=float("nan")))
    json.dumps(d, allow_nan=False)


def test_rows_passed_in_are_used_instead_of_a_read():
    """A batch shares ONE read; describe must not re-query per deployment."""
    class _Counting(_DB):
        reads = 0

        def __init__(self, rows):
            super().__init__(rows)
            orig = self.live_trades.find

            def find(q, p=None):
                _Counting.reads += 1
                return orig(q, p)
            self.live_trades.find = find

    rows = [_trade(status="OPEN")]
    asyncio.run(describe_live_caps(_Counting(rows), _dep(**CAPS), now_utc=NOW_UTC,
                                   rows=rows, account_rows=rows,
                                   account_config=ACCOUNT))
    assert _Counting.reads == 0
