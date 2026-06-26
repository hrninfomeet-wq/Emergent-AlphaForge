"""Tests for app.live.live_blotter.build_live_blotter — the deployment-attributed
live blotter join (live_trades ⨝ broker position book)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.live_blotter import build_live_blotter  # noqa: E402


def _trade(**kw):
    base = dict(
        id="t1", created_at="2026-06-25T04:00:00+00:00", deployment_id="dep1",
        strategy_id="orb", instrument="NIFTY", trading_symbol="NIFTY24JUN24000CE",
        direction="LONG", lots=2, quantity=150, entry_price=120.0, norenordno="N1",
    )
    base.update(kw)
    return base


def _pos(tsym, *, netqty="150", lp="135", urmtom="2250", rpnl="0"):
    return {"tsym": tsym, "netqty": netqty, "lp": lp, "urmtom": urmtom, "rpnl": rpnl}


DEPS = {"dep1": {"id": "dep1", "name": "ORB · NIFTY", "strategy_id": "orb", "instrument": "NIFTY"}}


def test_held_position_carries_broker_pnl_and_attribution():
    rows = build_live_blotter([_trade()], [_pos("NIFTY24JUN24000CE")], DEPS)
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "LIVE"
    assert r["at_broker"] is True
    assert r["deployment_name"] == "ORB · NIFTY"
    assert r["strategy_id"] == "orb"
    assert r["ltp"] == 135.0
    assert r["pnl"] == 2250.0  # urmtom + rpnl
    assert r["entry_price"] == 120.0


def test_trade_not_at_broker_is_flat_with_null_pnl():
    # tsym absent from the broker book (squared/unfilled) → FLAT, no fabricated P&L.
    rows = build_live_blotter([_trade()], [], DEPS)
    assert rows[0]["status"] == "FLAT"
    assert rows[0]["at_broker"] is False
    assert rows[0]["pnl"] is None
    assert rows[0]["ltp"] is None
    # attribution is still present even with no broker connection
    assert rows[0]["deployment_name"] == "ORB · NIFTY"


def test_flat_broker_position_netqty_zero_is_flat():
    rows = build_live_blotter([_trade()], [_pos("NIFTY24JUN24000CE", netqty="0")], DEPS)
    assert rows[0]["status"] == "FLAT"
    assert rows[0]["pnl"] is None


def test_pnl_attributed_to_newest_row_only_for_same_tsym():
    # Two OPEN journal rows on the same tsym (re-entry, no close-loop). The single
    # aggregated broker position must be attributed to the NEWEST row only, so the
    # P&L column sums to the broker total (no double-count).
    older = _trade(id="old", created_at="2026-06-25T03:00:00+00:00")
    newer = _trade(id="new", created_at="2026-06-25T05:00:00+00:00")
    rows = build_live_blotter([older, newer], [_pos("NIFTY24JUN24000CE", urmtom="2250")], DEPS)
    by_id = {r["id"]: r for r in rows}
    assert by_id["new"]["status"] == "LIVE"
    assert by_id["new"]["pnl"] == 2250.0
    assert by_id["old"]["status"] == "FLAT"
    assert by_id["old"]["pnl"] is None
    total = sum(r["pnl"] or 0 for r in rows)
    assert total == 2250.0  # exactly the broker total, not doubled


def test_closed_trade_surfaces_journaled_realized_pnl():
    # Close-loop journaled a squared trade (no longer at broker) → CLOSED with the
    # persisted realized P&L + exit mark (not FLAT/null).
    closed = _trade(status="CLOSED", realized_pnl=1950.0, exit_price=133.0)
    rows = build_live_blotter([closed], [], DEPS)
    r = rows[0]
    assert r["status"] == "CLOSED"
    assert r["at_broker"] is False
    assert r["pnl"] == 1950.0   # journaled realized P&L
    assert r["ltp"] == 133.0    # journaled exit mark


def test_closed_journal_row_still_open_at_broker_prefers_live_mtm():
    # Race: journal says CLOSED but the broker still reports the position → the
    # live broker MTM wins (the truth), not the stale journal close.
    closed = _trade(status="CLOSED", realized_pnl=1950.0, exit_price=133.0)
    rows = build_live_blotter([closed], [_pos("NIFTY24JUN24000CE")], DEPS)
    assert rows[0]["status"] == "LIVE"
    assert rows[0]["pnl"] == 2250.0   # broker urmtom+rpnl, not the journal number


def test_rows_sorted_newest_first():
    a = _trade(id="a", created_at="2026-06-25T01:00:00+00:00", trading_symbol="X")
    b = _trade(id="b", created_at="2026-06-25T09:00:00+00:00", trading_symbol="Y")
    rows = build_live_blotter([a, b], [], DEPS)
    assert [r["id"] for r in rows] == ["b", "a"]


def test_deployment_label_falls_back_when_name_absent():
    deps = {"dep1": {"id": "dep1", "strategy_id": "orb", "instrument": "NIFTY"}}
    rows = build_live_blotter([_trade()], [], deps)
    assert rows[0]["deployment_name"] == "orb · NIFTY"


def test_unknown_deployment_id_does_not_crash():
    # dep doc missing → label has no name and no dep.instrument, so it falls
    # through to the trade's strategy_id alone (the trade's own instrument is not
    # consulted by the label helper, which reads only the deployment doc).
    rows = build_live_blotter([_trade(deployment_id="ghost")], [], {})
    assert rows[0]["deployment_name"] == "orb"
    assert rows[0]["status"] == "FLAT"


def test_malformed_numeric_fields_are_tolerated():
    bad_pos = {"tsym": "NIFTY24JUN24000CE", "netqty": "150", "lp": "n/a", "urmtom": None, "rpnl": "x"}
    rows = build_live_blotter([_trade()], [bad_pos], DEPS)
    r = rows[0]
    assert r["at_broker"] is True   # netqty parsed to 150
    assert r["ltp"] is None         # "n/a" → None
    assert r["pnl"] is None         # neither urmtom nor rpnl parse


def test_oco_error_passthrough_when_set():
    # A filled entry whose resting OCO failed to place carries oco_error on the
    # journal doc → the blotter row surfaces it so the operator knows the position
    # is software-guard-only (no PC-down broker backstop).
    rows = build_live_blotter(
        [_trade(oco_error="no_broker_backstop")], [_pos("NIFTY24JUN24000CE")], DEPS
    )
    assert rows[0]["oco_error"] == "no_broker_backstop"


def test_oco_error_is_none_when_absent():
    # No oco_error on the journal doc (OCO placed fine, or field absent) → None.
    rows = build_live_blotter([_trade()], [_pos("NIFTY24JUN24000CE")], DEPS)
    assert rows[0]["oco_error"] is None
