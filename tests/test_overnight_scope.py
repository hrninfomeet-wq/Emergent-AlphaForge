"""`allow_overnight` is a preference about END-OF-DAY, not an exemption from stops.

The skip lives INSIDE `square_off_open_paper_trades`, which has six callers — so
a flag meaning "don't sweep me at 15:00" silently exempted positions from:

  * the paper OVERALL BASKET stop-loss (paper_overall_controls) — an account-level
    RISK control that simply did not fire;
  * Stop-ALL and per-deployment manual Stop (routers/deployments);
  * the manual square-off endpoint (routers/journals);
  * strategy retire/delete cleanup (routers/strategies_admin), which then left
    orphan OPEN trades behind a deleted strategy.

"Keep my position overnight" is not "ignore my stop loss".

The default is therefore IGNORE (square anyway) and the EOD paths opt IN. That way
a future caller added without thought gets the safe behaviour, and only the two
genuinely time-scheduled sweeps carry the exemption.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.paper_squareoff import square_off_open_paper_trades  # noqa: E402


class _Coll:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]
        self.replaced = []

    def find(self, q=None, proj=None):
        docs = [d for d in self.docs
                if all(d.get(k) == v for k, v in (q or {}).items()
                       if not isinstance(v, dict))]
        class _C:
            async def to_list(_s, length=None): return [dict(d) for d in docs]
        return _C()

    async def replace_one(self, flt, doc, upsert=False):
        self.replaced.append(doc)
        class _R: modified_count = 1
        return _R()


class _DB:
    def __init__(self, trades, deps):
        self.paper_trades = _Coll(trades)
        self.strategy_deployments = _Coll(deps)


def _db():
    return _DB(
        [{"id": "t1", "status": "OPEN", "deployment_id": "depN",
          "instrument_key": "NSE_FO|1", "created_at": "2026-08-04T05:00:00+00:00",
          "entry_price": 100.0, "quantity": 65, "lots": 1}],
        [{"id": "depN", "risk": {"allow_overnight": True}}])


def test_a_stop_squares_an_overnight_position():
    """The default path — a risk control or an explicit Stop — must not be opted out of."""
    db = _db()
    asyncio.run(square_off_open_paper_trades(db, reason="overall_sl"))
    assert db.paper_trades.replaced, (
        "an account-level stop skipped a position because the operator had asked "
        "to hold positions OVERNIGHT — two unrelated things")


def test_the_scheduled_eod_sweep_still_honours_it():
    """The one place the flag legitimately means something."""
    db = _db()
    out = asyncio.run(square_off_open_paper_trades(
        db, reason="auto_square_off_15_00_IST", honour_allow_overnight=True))
    assert db.paper_trades.replaced == []
    assert out and out[0].get("skipped") == "allow_overnight"


def test_a_normal_deployment_is_squared_either_way():
    """No over-fire: the flag only ever affects deployments that set it."""
    for honour in (False, True):
        db = _DB([{"id": "t1", "status": "OPEN", "deployment_id": "depPlain",
                   "instrument_key": "NSE_FO|1", "created_at": "2026-08-04T05:00:00+00:00",
                   "entry_price": 100.0, "quantity": 65, "lots": 1}],
                 [{"id": "depPlain", "risk": {}}])
        asyncio.run(square_off_open_paper_trades(
            db, reason="x", honour_allow_overnight=honour))
        assert len(db.paper_trades.replaced) == 1, honour


def test_the_risk_and_operator_call_sites_do_not_opt_in():
    """Source contract: only genuinely SCHEDULED sweeps may carry the exemption."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    must_not = [
        "backend/app/paper_overall_controls.py",   # basket stop = risk
        "backend/app/routers/deployments.py",      # Stop-ALL + manual Stop
        "backend/app/routers/journals.py",         # manual square-off
        "backend/app/routers/strategies_admin.py",  # retire/delete cleanup
    ]
    for rel in must_not:
        src = (root / rel).read_text(encoding="utf-8")
        assert "honour_allow_overnight" not in src, (
            f"{rel} opts out of squaring an overnight position — a stop, an "
            f"explicit operator Stop, or a delete must always square")


def test_the_scheduled_sweeps_DO_opt_in():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for rel in ("backend/app/runtime.py", "backend/server.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "honour_allow_overnight=True" in src, (
            f"{rel} is a scheduled EOD sweep and must still honour the operator's "
            f"overnight preference")
