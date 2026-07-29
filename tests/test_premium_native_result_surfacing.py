"""A premium-native run must SHOW its trades and its real KPIs.

User-reported (2026-07-29): "If there is no trade list in the Trades pane then how
will the user know which trade was taken and what was the P&L?"

They were right, and the data was never missing — only mis-wired:

  * `BacktestLab.jsx` KPI grid read `result.metrics`, the SPOT metrics, which for
    a premium-native run is a zero-filled stub (`evaluate()` is an inert stub, so
    the spot path produces no trades). Trades / Win Rate / Profit Factor /
    Net P&L therefore rendered blank or zero.
  * The "Highest Acct Value" card DID populate, because it goes through
    `buildPerformanceSeries`, which already falls back to
    `option_backtest.portfolio`. That asymmetry was the whole visible symptom:
    one card read the right object, four read the wrong one.
  * `<TradesTable trades={result.trades} .../>` iterated the empty spot list and
    early-returned "No trades were taken", even though `option_backtest.trades`
    held every executed option trade with entry/exit/strike/side/P&L.

These are BEHAVIOURAL tests: they execute the real `backtestMetrics.js` through
node rather than grepping the source. A grep cannot tell a use from a definition
— that is exactly how an unbound name shipped to this user a day earlier.

Unit honesty matters here. Spot runs are in index POINTS; a premium-native run is
rupee-native (option premium x quantity). Relabelling is part of the fix — showing
a rupee figure under a "(pts)" heading would be the same units lie this audit
found in `optimizer.py:398`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "backtestMetrics.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is required to execute the real module")


def _run_js(body: str):
    """Import the REAL module in node and return the evaluated expression."""
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    src = (
        f"import * as M from {url!r};\n"
        f"const out = (() => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(["node", "--input-type=module", "-e", src],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


# A premium-native result envelope, shaped exactly like dispatch_full_backtest's.
PREMIUM = {
    "metrics": {"trade_count": 0, "win_rate": 0.0, "profit_factor": None,
                "total_pnl_pts": 0.0, "max_dd_pts": 0.0, "sharpe": None},
    "trades": [],
    "equity_curve": [],
    "option_backtest": {
        "enabled": True,
        "dispatch": "premium_trigger_config",
        "metrics": {"paired_trade_count": 2, "wins": 1, "losses": 1,
                    "win_rate": 50.0, "total_option_pnl_pts": 12.5,
                    "total_option_pnl_value": 8125.0},
        "portfolio": {"starting_capital": 200000.0, "ending_equity": 208125.0,
                      "net_pnl_value": 8125.0, "max_drawdown_value": -3000.0,
                      "sharpe_daily": 1.42, "total_return_pct": 4.06,
                      "curve": [{"ts": 1700000000000, "equity_value": 203000.0,
                                 "drawdown_value": 0.0},
                                {"ts": 1700003600000, "equity_value": 208125.0,
                                 "drawdown_value": 0.0}]},
        "trades": [
            {"index_trade_id": 0, "status": "PAIRED", "direction": "CE", "side": "CE",
             "strike": 24500.0, "instrument_key": "NSE_FO|1", "expiry_date": "2026-07-30",
             "option_entry_ts": 1700000000000, "option_exit_ts": 1700001800000,
             "entry_option_price": 100.0, "exit_option_price": 160.0,
             "option_pnl_pts": 60.0, "option_pnl_value": 3900.0,
             "option_exit_reason": "OPTION_TARGET", "lots": 1, "quantity": 65,
             "total_charges": 100.0},
            {"index_trade_id": 1, "status": "PAIRED", "direction": "PE", "side": "PE",
             "strike": 24400.0, "instrument_key": "NSE_FO|2", "expiry_date": "2026-07-30",
             "option_entry_ts": 1700002000000, "option_exit_ts": 1700003600000,
             "entry_option_price": 120.0, "exit_option_price": 72.5,
             "option_pnl_pts": -47.5, "option_pnl_value": -3087.5,
             "option_exit_reason": "OPTION_STOP", "lots": 1, "quantity": 65,
             "total_charges": 100.0},
        ],
    },
}

SPOT = {
    "metrics": {"trade_count": 7, "win_rate": 57.1, "profit_factor": 1.8,
                "total_pnl_pts": 210.5, "max_dd_pts": -80.0, "sharpe": 1.1},
    "trades": [{"direction": "CE", "entry_ts": 1, "exit_ts": 2, "pnl_pts": 30.0}],
    "equity_curve": [],
    "option_backtest": None,
}


def _j(obj):
    return json.dumps(obj)


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #

def test_a_premium_run_is_detected():
    assert _run_js(f"return M.isPremiumNative({_j(PREMIUM)});") is True


def test_an_ordinary_run_is_not():
    assert _run_js(f"return M.isPremiumNative({_j(SPOT)});") is False


def test_garbage_is_safe():
    assert _run_js("return [M.isPremiumNative(null), M.isPremiumNative({}), "
                   "M.isPremiumNative({option_backtest:{}})];") == [False, False, False]


# --------------------------------------------------------------------------- #
# THE reported symptom — the trade list
# --------------------------------------------------------------------------- #

def test_premium_trades_are_surfaced_for_the_table():
    rows = _run_js(f"return M.displayTrades({_j(PREMIUM)});")
    assert len(rows) == 2, "the user must see the trades that were actually taken"


def test_each_surfaced_trade_answers_which_and_how_much():
    rows = _run_js(f"return M.displayTrades({_j(PREMIUM)});")
    first = rows[0]
    assert first["direction"] == "CE"
    assert first["entry_ts"] == 1700000000000
    assert first["exit_ts"] == 1700001800000
    assert first["entry_price"] == 100.0
    assert first["exit_price"] == 160.0
    assert first["pnl_pts"] == 60.0
    assert first["exit_reason"] == "OPTION_TARGET"


def test_the_join_key_is_preserved_so_option_columns_still_attach():
    """TradesTable joins option legs by index_trade_id == row index."""
    rows = _run_js(f"return M.displayTrades({_j(PREMIUM)});")
    assert [r["index_trade_id"] for r in rows] == [0, 1]


def test_pnl_pct_is_derived_for_the_percent_column():
    rows = _run_js(f"return M.displayTrades({_j(PREMIUM)});")
    assert rows[0]["pnl_pct"] == pytest.approx(60.0)      # 100 -> 160
    assert rows[1]["pnl_pct"] == pytest.approx(-39.583, abs=1e-2)


def test_unpaired_rows_are_excluded():
    bad = json.loads(_j(PREMIUM))
    bad["option_backtest"]["trades"].append({"index_trade_id": 9, "status": "UNPAIRED"})
    rows = _run_js(f"return M.displayTrades({_j(bad)});")
    assert len(rows) == 2


def test_an_ordinary_run_still_returns_its_own_spot_trades():
    """Byte-identical for every existing strategy — this must not change what
    confluence/VWAP users already see."""
    rows = _run_js(f"return M.displayTrades({_j(SPOT)});")
    assert rows == SPOT["trades"]


# --------------------------------------------------------------------------- #
# the KPI grid
# --------------------------------------------------------------------------- #

def test_premium_kpis_come_from_the_option_envelope():
    k = _run_js(f"return M.resultKpis({_j(PREMIUM)});")
    assert k["tradeCount"] == 2, "the KPI read the empty SPOT metrics before"
    assert k["winRate"] == 50.0
    assert k["netPnl"] == 8125.0
    assert k["sharpe"] == 1.42


def test_premium_kpis_are_labelled_as_rupees_not_points():
    """A rupee figure under a '(pts)' heading is the same units lie this audit
    found elsewhere."""
    k = _run_js(f"return M.resultKpis({_j(PREMIUM)});")
    assert k["currency"] is True
    assert k["unit"] == "₹"


def test_profit_factor_is_computed_since_the_option_metrics_omit_it():
    """option_backtest.metrics has no profit_factor key at all, so the card was
    permanently blank. gross win 3900 / gross loss 3087.5 = 1.263."""
    k = _run_js(f"return M.resultKpis({_j(PREMIUM)});")
    assert k["profitFactor"] == pytest.approx(1.263, abs=1e-3)


def test_max_drawdown_comes_from_the_rupee_portfolio():
    k = _run_js(f"return M.resultKpis({_j(PREMIUM)});")
    assert abs(k["maxDd"]) == 3000.0


def test_ordinary_runs_keep_their_points_kpis_unchanged():
    k = _run_js(f"return M.resultKpis({_j(SPOT)});")
    assert k["currency"] is False
    assert k["unit"] == "pts"
    assert k["tradeCount"] == 7
    assert k["winRate"] == 57.1
    assert k["profitFactor"] == 1.8
    assert k["netPnl"] == 210.5
    assert k["sharpe"] == 1.1


def test_a_premium_run_with_no_trades_does_not_crash():
    empty = json.loads(_j(PREMIUM))
    empty["option_backtest"]["trades"] = []
    empty["option_backtest"]["metrics"] = {"paired_trade_count": 0, "win_rate": 0.0}
    k = _run_js(f"return M.resultKpis({_j(empty)});")
    assert k["tradeCount"] == 0
    assert k["profitFactor"] is None
