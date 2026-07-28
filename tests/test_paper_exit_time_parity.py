"""Step 7: close the two paper/live asymmetries a config would otherwise expose.

Both were flagged during the Phase 1 research fan-out and both are cases where a
strategy's own configuration promises something LIVE honours and PAPER silently
ignores — the worst shape for a paper-then-live workflow, because paper is
supposed to be the rehearsal.

**Risk #2 — `exit_time` / `square_at_ist`.** The evaluator stamps
`risk_hints.square_at_ist` from a premium-native strategy's `exit_time`. It is
consumed ONLY by `live/live_position_guard.py`; grep found zero paper readers. So
a config promising "square at 14:30" squares at 14:30 live and rides to the 15:00
EOD sweep on paper. A user validating on paper would measure a different strategy
from the one they later deploy.

`paper_auto.mark_open_deployment_trades` already honours
`risk_hints.time_stop_minutes` in exactly this way, so the mechanism is
established — the field was simply never read.

**Risk #3 — sizing replay.** `dispatch_full_backtest` returned
`"sizing_config": None`, and `deployment_sizing_from_source` reads that key to pin
a deployment's sizing. So deploying from a premium-native backtest silently fell
back to `default_lots` instead of replaying the config's own `lots` — the
deployment traded a different size than the backtest that justified it.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

IST = timezone(timedelta(hours=5, minutes=30))


class _Coll:
    def __init__(self, rows=None):
        self.rows: List[Dict[str, Any]] = list(rows or [])

    def find(self, query=None, projection=None):
        rows = [r for r in self.rows
                if all(r.get(k) == v for k, v in (query or {}).items()
                       if not isinstance(v, dict))]
        class _Cur:
            def __init__(self, rs): self._rs = rs
            async def to_list(self, length=None): return [dict(r) for r in self._rs]
        return _Cur(rows)

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in (query or {}).items()):
                return dict(r)
        return None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in (query or {}).items()):
                r.update(update.get("$set", {}))
                return type("R", (), {"matched_count": 1})()
        return type("R", (), {"matched_count": 0})()

    async def replace_one(self, query, doc, upsert=False):
        for i, r in enumerate(self.rows):
            if all(r.get(k) == v for k, v in (query or {}).items()):
                self.rows[i] = dict(doc)
                return type("R", (), {"matched_count": 1})()
        return type("R", (), {"matched_count": 0})()


class _DB:
    def __init__(self, trades):
        self.paper_trades = _Coll(trades)
        self.signals = _Coll()
        self.strategy_deployments = _Coll()


def _trade(square_at_ist: Optional[str], *, key="NSE_FO|K1"):
    hints: Dict[str, Any] = {}
    if square_at_ist:
        hints["square_at_ist"] = square_at_ist
    return {
        "id": "t1", "deployment_id": "dep-1", "status": "OPEN",
        "instrument_key": key, "entry_price": 100.0, "lots": 1, "lot_size": 75,
        "direction": "CE", "quantity": 75,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "risk_hints": hints,
    }


def _run(db, *, now_ist_hhmm: str):
    """Drive the real marker with a fresh tick and a pinned IST clock."""
    from app import paper_auto

    # pin the IST wall clock the square_at_ist comparison uses
    real = paper_auto._now_utc
    h, m = (int(x) for x in now_ist_hhmm.split(":"))
    fake_ist = datetime.now(IST).replace(hour=h, minute=m, second=0, microsecond=0)
    fake_utc = fake_ist.astimezone(timezone.utc)
    paper_auto._now_utc = lambda: fake_utc

    def _lookup(_key):
        # ts is epoch MILLIseconds (what the marker's freshness gate compares
        # against) and is pinned to the faked clock, else the tick reads as
        # ancient, option_price is None, and no exit branch can run at all.
        return {"last_price": 120.0, "ts": int(fake_utc.timestamp() * 1000)}
    try:
        return asyncio.run(paper_auto.mark_open_deployment_trades(
            db, latest_tick_lookup=_lookup))
    finally:
        paper_auto._now_utc = real


# --------------------------------------------------------------------------- #
# risk #2 — paper must honour the config's exit time
# --------------------------------------------------------------------------- #

def test_paper_squares_a_trade_when_its_exit_time_passes():
    """THE parity gap. Live squares at 14:30; paper rode to the 15:00 sweep."""
    db = _DB([_trade("14:30")])
    _run(db, now_ist_hhmm="14:31")
    row = db.paper_trades.rows[0]
    assert str(row.get("status")).upper() == "CLOSED", (
        "paper ignored the strategy's own exit_time")
    assert "exit_time" in str(row.get("exit_reason") or "").lower()


def test_paper_leaves_the_trade_open_before_its_exit_time():
    db = _DB([_trade("14:30")])
    _run(db, now_ist_hhmm="14:29")
    assert str(db.paper_trades.rows[0].get("status")).upper() == "OPEN"


def test_a_trade_with_no_exit_time_is_untouched():
    """Byte-identical for every ordinary paper trade."""
    db = _DB([_trade(None)])
    _run(db, now_ist_hhmm="14:31")
    assert str(db.paper_trades.rows[0].get("status")).upper() == "OPEN"


def test_a_malformed_exit_time_does_not_close_or_crash():
    """Fail OPEN on a bad value: squaring a position because a string could not
    be parsed would be worse than ignoring it, and the live guard normalises
    HH:MM rather than guessing."""
    db = _DB([_trade("not-a-time")])
    _run(db, now_ist_hhmm="14:31")
    assert str(db.paper_trades.rows[0].get("status")).upper() == "OPEN"


# --------------------------------------------------------------------------- #
# risk #3 — the config's lots must be replayable by a deployment
# --------------------------------------------------------------------------- #

def test_premium_backtest_emits_a_sizing_config_carrying_the_configured_lots():
    from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario
    from app.premium_trigger_dispatch import dispatch_full_backtest

    spot, opt, contracts = _simple_ce_wins_scenario()
    out = dispatch_full_backtest(
        strategy_id="anything", merged_params={
            "reference_time": "09:31", "moneyness": "itm1", "side": "ce",
            "momentum_pct": 15.0, "lots": 3},
        spot_df=spot.copy(), option_candles=opt.copy(), contracts=list(contracts),
        instrument="NIFTY")
    sizing = (out or {}).get("sizing_config")
    assert sizing, "no sizing_config => a deployment silently falls back to default_lots"
    assert sizing.get("mode") == "fixed_lots"
    assert int(sizing.get("fixed_lots")) == 3


def test_a_deployment_can_pin_that_sizing():
    from app.strategy_deployments import deployment_sizing_from_source

    pin = deployment_sizing_from_source("backtest_run", {
        "option_backtest": {
            "sizing_config": {"mode": "fixed_lots", "fixed_lots": 3, "enabled": True},
            "request": {"lots": 3},
        }})
    assert pin and pin.get("sizing_config"), "the pin must survive"
    assert int(pin["sizing_config"]["fixed_lots"]) == 3
