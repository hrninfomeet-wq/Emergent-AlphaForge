"""A live close must record what the exchange actually took.

`live/live_friction_profile.py::live_charges` computes the full statutory stack
(STT, exchange txn, SEBI, GST, stamp) and has ZERO callers repo-wide.
`total_charges` was never written to a live trade — yet
`routers/live_broker.py:806` PROJECTS that field, so the trade-stats route reads
something that does not exist. Paper and backtest both apply the charge model
(`live_friction.close_economics`, `option_backtest`); live did not, so the three
were never measuring the same quantity.

Convention deliberately preserved: `realized_pnl` stays GROSS.
`close_economics` records the project's own decision — "realized stays GROSS
(kill-switch / caps / analytics semantics unchanged)" — and the day-stop sums that
field. Making the loss cap net-of-charges is a policy change for the operator to
make explicitly, not a side effect. Charges are reported in their own fields.

Parity is by SHARED CODE: this calls the same `option_costs.round_trip_charges`
that paper's close path uses, so the two cannot drift.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.close_loop import close_live_trade  # noqa: E402
from app.option_costs import CostConfig, round_trip_charges  # noqa: E402


def _db(doc):
    class _Col:
        def __init__(self): self.updated = {}
        async def find_one(self, flt, proj=None): return dict(doc)
        async def update_one(self, flt, upd):
            self.updated = upd.get("$set", {})
            class _R: modified_count = 1
            return _R()
    class _DB:
        def __init__(self): self.live_trades = _Col()
    return _DB()


_TRADE = {"norenordno": "N1", "status": "OPEN", "quantity": 325,
          "entry_price": 34.15, "entry_fill_price": 34.00}


def _close(**kw):
    db = _db(_TRADE)
    asyncio.run(close_live_trade(db, norenordno="N1", exit_price=33.45,
                                 exit_reason="stop", **kw))
    return db.live_trades.updated


def test_total_charges_is_written():
    out = _close()
    assert "total_charges" in out, (
        "live_broker's trade-stats route projects total_charges — it must exist")
    assert out["total_charges"] > 0


def test_charges_match_the_shared_model_paper_uses():
    """Parity by shared code, not by a re-implementation that can drift."""
    expected = round_trip_charges(
        entry_premium=34.00, exit_premium=33.45, quantity=325,
        cfg=CostConfig(enabled=True))["total_charges"]
    out = _close()
    assert out["total_charges"] == round(float(expected), 2)


def test_charges_are_computed_on_the_true_fill_not_the_reference():
    """STT is a turnover tax — it must be levied on prices actually transacted."""
    out = _close()
    on_ref = round_trip_charges(entry_premium=34.15, exit_premium=33.45,
                                quantity=325, cfg=CostConfig(enabled=True))
    assert out["total_charges"] != round(float(on_ref["total_charges"]), 2)


def test_net_realized_is_gross_minus_charges():
    out = _close()
    assert out["net_realized_pnl"] == round(
        out["realized_pnl"] - out["total_charges"], 2)


def test_realized_pnl_stays_GROSS():
    """The day-stop sums this field; its meaning must not shift silently."""
    out = _close()
    assert out["realized_pnl"] == -178.75, (
        "realized_pnl must remain the gross premium move — the loss cap's basis "
        "is a policy decision, not a side effect of adding charges")
    assert out["net_realized_pnl"] < out["realized_pnl"]


def test_no_charges_claimed_without_a_usable_price():
    """Never fabricate: an unresolvable exit leaves the number absent."""
    db = _db({"norenordno": "N1", "status": "OPEN", "quantity": 325,
              "entry_price": 34.15})
    asyncio.run(close_live_trade(db, norenordno="N1", exit_price=None,
                                 exit_reason="never_filled"))
    out = db.live_trades.updated
    assert "total_charges" not in out and "net_realized_pnl" not in out
    assert out["status"] == "CLOSED" and out["exit_reason"] == "never_filled"


def test_zero_quantity_claims_no_charges():
    db = _db({"norenordno": "N1", "status": "OPEN", "quantity": 0,
              "entry_price": 34.15, "entry_fill_price": 34.00})
    asyncio.run(close_live_trade(db, norenordno="N1", exit_price=33.45,
                                 exit_reason="stop"))
    assert db.live_trades.updated.get("total_charges") in (None, 0.0)
