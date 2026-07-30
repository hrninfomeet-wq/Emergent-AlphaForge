"""The run journal must not render every premium-native run as a dud.

CONFIRMED finding. `BacktestRunJournal.jsx` reads only the SPOT metrics envelope:

    <td>{fmtInt(r.metrics?.trade_count)}</td>
    <td>{fmtPct(r.metrics?.win_rate)}</td>
    ...

For a premium-native run that envelope is a zero-filled stub by construction, so
every row reads Trades 0 / WR 0.00% / PF – / Net Pts +0.00 — **beside a green
SIGNIFICANT badge whose CI was computed from the 253 OPTION trades** (research.py
builds `significance` from the option metrics). Sorting the journal by Trades, PF
or Net Pts therefore ranks every premium run last regardless of performance.

`BacktestLab.jsx`'s "Load past run" label has the identical defect.

The detail view already solved this in `backtestMetrics.js` (`isPremiumNative` +
`resultKpis`); the journal was simply never given the same treatment. The data is
present — `GET /backtest/runs` projects out only `trades`, `equity_curve` and
`walkforward`, so `option_backtest.metrics` and `.portfolio` are both in the list
payload.

These are BEHAVIOURAL tests through node against the real module, because the
journal's bug is precisely that a plausible-looking field read returns zeros.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "backtestMetrics.js")
_JOURNAL = os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                        "components", "BacktestRunJournal.jsx")
_LAB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "pages",
                    "BacktestLab.jsx")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


def _run_js(body):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    src = (f"import * as M from {url!r};\n"
           f"const out = (() => {{ {body} }})();\n"
           "process.stdout.write(JSON.stringify(out));\n")
    p = subprocess.run(["node", "--input-type=module", "-e", src],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


# A LIST-payload row: no `trades`, no `equity_curve`, no `walkforward`.
PREMIUM_ROW = {
    "id": "abc12345", "name": "premium run", "created_at": "2026-07-30T00:00:00Z",
    "status": "done",
    "metrics": {"trade_count": 0, "win_rate": 0.0, "profit_factor": None,
                "total_pnl_pts": 0.0, "max_dd_pts": 0.0, "sharpe": None},
    "option_backtest": {
        "enabled": True, "dispatch": "premium_trigger_config",
        "metrics": {"paired_trade_count": 253, "win_rate": 46.2,
                    "total_option_pnl_value": 120000.0},
        "portfolio": {"starting_capital": 200000.0, "net_pnl_value": 120000.0,
                      "max_drawdown_value": -45000.0, "sharpe_daily": 1.31},
        "trades": [],
    },
}

ORDINARY_ROW = {
    "id": "def67890", "name": "confluence run", "created_at": "2026-07-29T00:00:00Z",
    "status": "done",
    "metrics": {"trade_count": 253, "win_rate": 45.4, "profit_factor": 1.19,
                "total_pnl_pts": 1457.13, "max_dd_pts": -683.43, "sharpe": 1.168},
    "option_backtest": None,
}


def _j(o):
    return json.dumps(o)


def test_a_premium_journal_row_reports_its_real_trade_count():
    """THE symptom: the row said 0 while the detail view said 253."""
    k = _run_js(f"return M.resultKpis({_j(PREMIUM_ROW)});")
    assert k["tradeCount"] == 253


def test_a_premium_journal_row_reports_its_real_win_rate():
    k = _run_js(f"return M.resultKpis({_j(PREMIUM_ROW)});")
    assert k["winRate"] == 46.2


def test_a_premium_journal_row_reports_rupee_pnl_and_says_so():
    """Rendering ₹120,000 under a 'Net Pts' heading would be a units lie."""
    k = _run_js(f"return M.resultKpis({_j(PREMIUM_ROW)});")
    assert k["netPnl"] == 120000.0
    assert k["currency"] is True and k["unit"] == "₹"


def test_a_premium_row_without_option_trades_still_reports_drawdown_and_sharpe():
    """The list payload has `portfolio` but an empty `trades` — the row must not
    depend on the per-trade array being present."""
    k = _run_js(f"return M.resultKpis({_j(PREMIUM_ROW)});")
    assert abs(k["maxDd"]) == 45000.0
    assert k["sharpe"] == 1.31


def test_an_ordinary_journal_row_is_unchanged():
    k = _run_js(f"return M.resultKpis({_j(ORDINARY_ROW)});")
    assert k["tradeCount"] == 253 and k["winRate"] == 45.4
    assert k["profitFactor"] == 1.19 and k["netPnl"] == 1457.13
    assert k["currency"] is False and k["unit"] == "pts"


def test_a_row_with_no_metrics_at_all_does_not_crash():
    k = _run_js('return M.resultKpis({"id":"x"});')
    assert k["tradeCount"] is None or k["tradeCount"] == 0


# --------------------------------------------------------------------------- #
# both surfaces must use it
# --------------------------------------------------------------------------- #

def _src(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_the_journal_uses_the_family_aware_kpis():
    src = _src(_JOURNAL)
    assert "resultKpis" in src, "the journal still reads the spot metrics stub"


def test_the_journal_no_longer_reads_the_raw_spot_metrics_for_those_columns():
    src = _src(_JOURNAL)
    for field in ("r.metrics?.trade_count", "r.metrics?.win_rate",
                  "r.metrics?.profit_factor", "r.metrics?.total_pnl_pts"):
        assert field not in src, f"{field} still rendered raw"


def test_the_journal_marks_the_unit_so_rupees_are_not_shown_as_points():
    """Anchored on the RENDER, not on the first `resultKpis` token — that is now
    the import line, and a window from there proves nothing."""
    src = _src(_JOURNAL)
    i = src.index("rk.netPnl")
    block = src[max(0, i - 600):i + 600]
    assert "rk.currency" in block and "₹" in block


def test_the_load_past_run_label_is_also_family_aware():
    """BacktestLab.jsx had the identical `WR {fmtPct(r.metrics?.win_rate)}` defect."""
    src = _src(_LAB)
    assert "WR {fmtPct(r.metrics?.win_rate)}" not in src
