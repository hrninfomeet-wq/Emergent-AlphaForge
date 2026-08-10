"""A deployment must not vanish from every performance surface when it goes live.

The moment a deployment is promoted it disappears from the screens that say how it
is doing:

  * `GET /deployments/overview` — the command-centre card feed — aggregates
    `db.paper_trades` with no mode branch, so a live deployment holding real
    positions renders `open_trades = 0` and Rs 0 underneath the red
    "LIVE REAL-MONEY" badge the same renderer computes.
  * `forward_metrics._closed_trades` / `_open_trades` query `db.paper_trades`
    unconditionally, so both metric routes go permanently blank at exactly the
    moment they start mattering.

For an unattended bot that is the worst failure mode available: the screen you
glance at says nothing is happening while real money is at risk, and the true
numbers live on a different page that silently disagrees.

There is a second, subtler trap. `_promotion_trade_pnl` prefers
`execution_realized_pnl` and otherwise HAIRCUTS the trade to `min(realized, 0)`.
Only paper writes that field. Pointed at live trades unchanged, every live WINNER
would score Rs 0 while every loser counted in full — a live cohort would look
systematically catastrophic. A live `realized_pnl` is already broker-measured
(entry from `entry_fill_price`, exit from the fill), so it IS the executable
surface and must not be haircut.

The mode-aware pattern already existed three files away:
`deployment_evaluator.py:283` does `col = db.live_trades if mode == "live" else
db.paper_trades`. It was simply never applied to the aggregations.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.forward_metrics import (  # noqa: E402
    _closed_trades, _open_trades, _promotion_trade_pnl, trades_collection_for_mode,
)


class _Coll:
    def __init__(self, docs, name):
        self.docs = list(docs)
        self.name = name

    def find(self, q=None, proj=None):
        docs = [d for d in self.docs
                if all(d.get(k) == v for k, v in (q or {}).items())]
        class _C:
            def sort(_s, *a, **k): return _s
            async def to_list(_s, length=None): return [dict(d) for d in docs]
        return _C()


class _DB:
    def __init__(self, paper, live):
        self.paper_trades = _Coll(paper, "paper")
        self.live_trades = _Coll(live, "live")


_PAPER = [{"deployment_id": "d1", "status": "CLOSED", "realized_pnl": 10.0, "src": "paper"}]
_LIVE = [{"deployment_id": "d1", "status": "CLOSED", "realized_pnl": 99.0, "src": "live"}]
_OPEN_P = [{"deployment_id": "d1", "status": "OPEN", "src": "paper"}]
_OPEN_L = [{"deployment_id": "d1", "status": "OPEN", "src": "live"}]


# --- collection routing -----------------------------------------------------

def test_a_live_deployment_reads_live_trades():
    db = _DB(_PAPER, _LIVE)
    rows = asyncio.run(_closed_trades(db, "d1", mode="live"))
    assert rows and rows[0]["src"] == "live", (
        "a live deployment's metrics were read from paper_trades — it renders "
        "as dead while real money is at risk")


def test_a_paper_deployment_still_reads_paper_trades():
    db = _DB(_PAPER, _LIVE)
    rows = asyncio.run(_closed_trades(db, "d1", mode="paper"))
    assert rows and rows[0]["src"] == "paper"


def test_open_trades_route_by_mode_too():
    db = _DB(_OPEN_P, _OPEN_L)
    assert asyncio.run(_open_trades(db, "d1", mode="live"))[0]["src"] == "live"
    assert asyncio.run(_open_trades(db, "d1", mode="paper"))[0]["src"] == "paper"


def test_default_mode_is_paper_so_existing_callers_are_unchanged():
    db = _DB(_PAPER, _LIVE)
    assert asyncio.run(_closed_trades(db, "d1"))[0]["src"] == "paper"


def test_the_helper_mirrors_the_evaluator_pattern():
    db = _DB([], [])
    assert trades_collection_for_mode(db, "live") is db.live_trades
    assert trades_collection_for_mode(db, "LIVE") is db.live_trades
    for m in ("paper", "signal_only", "", None):
        assert trades_collection_for_mode(db, m) is db.paper_trades


# --- the haircut trap -------------------------------------------------------

def test_a_live_winner_is_not_haircut_to_zero():
    """`execution_realized_pnl` is paper-only; a live fill IS the executable surface."""
    t = {"realized_pnl": 250.0}          # no execution_realized_pnl, as live never writes it
    assert _promotion_trade_pnl(t, is_live=True) == 250.0, (
        "a live winning trade scored 0 — the cohort would look catastrophic")


def test_a_live_loser_still_counts_in_full():
    assert _promotion_trade_pnl({"realized_pnl": -180.0}, is_live=True) == -180.0


def test_paper_haircut_behaviour_is_unchanged():
    """No regression: an uncovered paper winner is still haircut to zero."""
    assert _promotion_trade_pnl({"realized_pnl": 250.0}) == 0.0
    assert _promotion_trade_pnl({"realized_pnl": -180.0}) == -180.0
    assert _promotion_trade_pnl({"realized_pnl": 250.0,
                                 "execution_realized_pnl": 240.0}) == 240.0


# --- the overview aggregation ----------------------------------------------

def test_the_overview_route_aggregates_both_collections():
    """Scoped to the overview function and checked via AST, not a substring.

    An earlier version of this test grepped for the literal "live_trades.aggregate"
    and failed against a correct implementation that binds the collection to a
    variable first — the same false-positive shape as grepping a docstring.
    """
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "backend" / "app" / "routers" / "deployments.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
               and "overview" in n.name), None)
    assert fn is not None, "could not locate the overview route"
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "paper_trades" in attrs
    assert "live_trades" in attrs, (
        "the command-centre card feed still reads paper_trades only, so a live "
        "deployment renders 0 trades and Rs 0 under its own LIVE badge")
