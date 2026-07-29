"""Trade quality must show the largest capital a single trade would have needed.

User request (2026-07-30): "add a card for Max Capital Deployed for trade ... if
there are 116 trades then the maximum Buy Rs should be shown. This would tell how
much maximum capital would be required for the trade to be taken in real market."

This is a real funding question, not a cosmetic one: net P&L and return % say
nothing about the cash that has to be free at the worst moment, and a strategy
whose peak single-trade outlay exceeds the account cannot be traded at all
regardless of how good its backtest looks.

Definition matches the Trades table's existing "Buy Rs" column exactly
(`tradeBuyValue`): entry premium x quantity + round-trip charges. Reusing that
helper rather than recomputing keeps the card and the column from ever
disagreeing — a user WILL cross-check the card against the biggest row.

Points-mode (spot-only) runs have no premium outlay to report, so the value is
null and the card renders as a dash rather than a misleading 0.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "backtestMetrics.js")
_OVERVIEW = os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                         "components", "backtest", "PerformanceOverview.jsx")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


def _run_js(body: str):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    src = (f"import * as M from {url!r};\n"
           f"const out = (() => {{ {body} }})();\n"
           "process.stdout.write(JSON.stringify(out));\n")
    p = subprocess.run(["node", "--input-type=module", "-e", src],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(f"node failed:\n{p.stderr}")
    return json.loads(p.stdout)


def _opt(entry, qty, charges, pnl=0.0):
    return {"status": "PAIRED", "direction": "CE",
            "option_entry_ts": 1700000000000, "option_exit_ts": 1700001800000,
            "entry_option_price": entry, "exit_option_price": entry + 10,
            "quantity": qty, "total_charges": charges,
            "option_pnl_value": pnl, "option_pnl_pts": 10.0}


PREMIUM = {
    "metrics": {}, "trades": [], "equity_curve": [],
    "option_backtest": {
        "enabled": True, "dispatch": "premium_trigger_config",
        "metrics": {"paired_trade_count": 3, "win_rate": 66.7},
        "portfolio": {"starting_capital": 200000.0, "ending_equity": 205000.0,
                      "net_pnl_value": 5000.0, "max_drawdown_value": -1000.0,
                      "sharpe_daily": 1.0,
                      "curve": [{"ts": 1700000000000, "equity_value": 205000.0,
                                 "drawdown_value": 0.0}]},
        # peak outlay is the middle row: 300 * 130 + 60 = 39,060
        "trades": [_opt(100.0, 130, 40.0, 500.0),
                   _opt(300.0, 130, 60.0, 3000.0),
                   _opt(150.0, 130, 50.0, 1500.0)],
    },
}

SPOT = {
    "metrics": {"trade_count": 2, "total_pnl_pts": 30.0},
    "trades": [{"direction": "CE", "entry_ts": 1, "exit_ts": 2, "pnl_pts": 30.0}],
    "equity_curve": [], "option_backtest": None,
}


def _j(o):
    return json.dumps(o)


def test_the_max_is_the_largest_single_trade_buy_value():
    k = _run_js(f"return M.computeKeyMetrics({_j(PREMIUM)});")
    assert k["maxBuyValue"] == pytest.approx(300.0 * 130 + 60.0)


def test_it_matches_the_trades_table_Buy_column_exactly():
    """The user will cross-check this card against the biggest row. It must be
    the same number, from the same helper."""
    rows = _run_js(
        f"const ob={_j(PREMIUM)}.option_backtest;"
        f"return ob.trades.map(t => M.tradeBuyValue(t));")
    k = _run_js(f"return M.computeKeyMetrics({_j(PREMIUM)});")
    assert k["maxBuyValue"] == max(rows)


def test_charges_are_included_because_they_must_be_funded_too():
    k = _run_js(f"return M.computeKeyMetrics({_j(PREMIUM)});")
    assert k["maxBuyValue"] > 300.0 * 130


def test_points_mode_reports_null_not_zero():
    """A spot-only run has no premium outlay; 0 would read as 'costs nothing'."""
    k = _run_js(f"return M.computeKeyMetrics({_j(SPOT)});")
    assert k["maxBuyValue"] is None


def test_a_run_with_no_trades_is_null():
    empty = json.loads(_j(PREMIUM))
    empty["option_backtest"]["trades"] = []
    k = _run_js(f"return M.computeKeyMetrics({_j(empty)});")
    assert k["maxBuyValue"] is None


def test_unpaired_rows_are_excluded():
    mixed = json.loads(_j(PREMIUM))
    mixed["option_backtest"]["trades"].append(
        {"status": "UNPAIRED", "entry_option_price": 9999.0, "quantity": 130})
    k = _run_js(f"return M.computeKeyMetrics({_j(mixed)});")
    assert k["maxBuyValue"] == pytest.approx(300.0 * 130 + 60.0)


def test_malformed_rows_do_not_poison_the_max():
    """A NaN would propagate through Math.max and blank the card."""
    bad = json.loads(_j(PREMIUM))
    bad["option_backtest"]["trades"].append(
        {"status": "PAIRED", "entry_option_price": None, "quantity": None})
    k = _run_js(f"return M.computeKeyMetrics({_j(bad)});")
    assert k["maxBuyValue"] == pytest.approx(300.0 * 130 + 60.0)


# --------------------------------------------------------------------------- #
# the card is actually rendered
# --------------------------------------------------------------------------- #

def _overview():
    with open(_OVERVIEW, encoding="utf-8") as fh:
        return fh.read()


def test_the_card_exists_in_the_trade_quality_block():
    src = _overview()
    assert "maxBuyValue" in src, "computed but never displayed"
    i = src.index("Trade quality")
    assert "maxBuyValue" in src[i:i + 3000], (
        "the card must live in the Trade-quality block the user named")


def test_the_card_is_labelled_for_what_it_answers():
    src = _overview()
    low = src.lower()
    assert "max capital" in low or "capital / trade" in low
