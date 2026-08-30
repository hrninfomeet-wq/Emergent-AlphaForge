"""The Backtest Lab action buttons must report the run they belong to.

CONFIRMED findings, both verified against the stored corpus (114 saved runs).

1. `Trades.csv` exported `result.trades` verbatim:

       exportCsv(result?.trades || [], ...)

   while the Trades pane renders `displayTrades(result)` joined to
   `option_backtest.trades`. So the download disagreed with the screen in two
   ways. For an option run (100/114 stored) every option column was absent --
   including `opt_pnl_value`, the actual rupee P&L: the reported
   fibonacci_pullback run shows Rs 808,311.75 on screen and exported only spot
   points. For a premium-native run (22/114) `result.trades` is EMPTY BY
   CONSTRUCTION, so a run showing 51 trades and Rs 4.9M downloaded a file
   containing the string "(empty)".

2. `Save as preset` read sizing from `run.config.option_backtest` -- the request
   echo -- instead of `run.option_backtest`, the resolved policy the sim actually
   executed and which the optimizer rewrites. An optimizer-sized run that traded
   100 lots produced a 5-lot preset; one that traded 2 lots produced a 5-lot
   preset. Deploy-from-run already read the resolved block
   (`deployment_sizing_from_source`), so the two buttons disagreed on size for
   the same run.

`Config` / `Result` / `Deploy` were verified correct and are pinned here too, so
a future change cannot quietly regress them.

BEHAVIOURAL tests through node against the real modules -- the bug was precisely
that a plausible-looking field read returns the wrong (or an empty) list.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

_HERE = os.path.dirname(__file__)
_LIB = os.path.join(_HERE, "..", "frontend", "src", "lib")
_LAB = os.path.join(_HERE, "..", "frontend", "src", "pages", "BacktestLab.jsx")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


def _url(name):
    return "file:///" + os.path.abspath(os.path.join(_LIB, name)).replace("\\", "/")


def _node(src):
    p = subprocess.run(["node", "--input-type=module", "-e", src],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


def _export_csv(run, tmp_path):
    """Return the text `exportTradesCsv` would download for `run`.

    exports.js imports through the "@/lib/..." alias that vite resolves and node
    does not, so the import specifier is rewritten to a real path. The function
    body under test is untouched.
    """
    src = open(os.path.join(_LIB, "exports.js"), encoding="utf-8").read()
    src = src.replace('from "@/lib/backtestMetrics"', "from %r" % _url("backtestMetrics.js"))
    shim = tmp_path / "exports_under_test.mjs"
    shim.write_text(src, encoding="utf-8")
    shim_url = "file:///" + str(shim).replace("\\", "/")

    return _node(
        "let csv = null;\n"
        "globalThis.Blob = class { constructor(parts){ csv = parts.join(''); } };\n"
        "globalThis.URL = { createObjectURL: () => 'blob:x', revokeObjectURL(){} };\n"
        "globalThis.document = { createElement: () => ({ click(){} }),"
        " body: { appendChild(){}, removeChild(){} } };\n"
        "const E = await import(%r);\n"
        "E.exportTradesCsv(%s);\n"
        "process.stdout.write(JSON.stringify(csv));\n" % (shim_url, json.dumps(run))
    )


def _pane_rows(run):
    """Row count the Trades pane renders -- the contract the CSV must match."""
    return _node(
        "const M = await import(%r);\n"
        "const r = %s;\n"
        "process.stdout.write(JSON.stringify("
        "M.joinOptionLegs(M.displayTrades(r), r.option_backtest).length));\n"
        % (_url("backtestMetrics.js"), json.dumps(run))
    )


def _rows(csv_text):
    lines = [ln for ln in csv_text.split("\n") if ln]
    return lines[0].split(","), lines[1:]


# --- fixtures shaped exactly like the stored envelopes -----------------------

def _option_leg(i, pnl_value, lots=5, qty=375, status="PAIRED"):
    return {
        "index_trade_id": i, "status": status, "side": "CE", "strike": 23600 + i,
        "trading_symbol": "NIFTY %d CE" % (23600 + i), "lots": lots, "quantity": qty,
        "entry_option_price": 100.0, "exit_option_price": 120.0,
        "total_charges": 50.0, "option_pnl_value": pnl_value,
        "option_pnl_pts": 20.0, "option_exit_reason": "SPOT_EXIT",
    }


SPOT_RUN = {
    "id": "spot0001", "name": "spot run", "strategy_id": "fibonacci_pullback",
    "trades": [
        {"direction": "CE", "entry_ts": 1, "exit_ts": 2, "entry_price": 100.0,
         "exit_price": 110.0, "exit_reason": "TARGET", "score": 60,
         "pnl_pts": 10.0, "pnl_pct": 0.1, "vix": 14.5, "regime": "TREND"},
        {"direction": "PE", "entry_ts": 3, "exit_ts": 4, "entry_price": 200.0,
         "exit_price": 190.0, "exit_reason": "STOP", "score": 55,
         "pnl_pts": -10.0, "pnl_pct": -0.05, "vix": 15.0, "regime": "MIXED"},
    ],
    "metrics": {"total_pnl_pts": 0.0},
    "option_backtest": {
        "enabled": True,
        "trades": [_option_leg(0, 7500.0), _option_leg(1, -2500.0)],
        "portfolio": {"net_pnl_value": 5000.0},
        "sizing_config": {"mode": "premium_at_risk", "enabled": False, "capital": 200000},
        "request": {"lots": 5},
    },
    "config": {"option_backtest": {"enabled": True, "moneyness": "atm", "lots": 5,
                                   "sizing_config": {"capital": 200000}}},
}

# A premium-native run: spot `trades` empty by construction, everything real in
# option_backtest, and the optimizer pinned a fixed size the request never held.
PREMIUM_RUN = {
    "id": "prem0001", "name": "premium run", "strategy_id": "algotest_option_buy_nifty",
    "trades": [], "metrics": {},
    "option_backtest": {
        "enabled": True, "dispatch": "premium_trigger_config",
        "trades": [_option_leg(0, 90000.0, lots=100, qty=6500),
                   _option_leg(1, -15000.0, lots=100, qty=6500)],
        "portfolio": {"net_pnl_value": 75000.0},
        "metrics": {"paired_trade_count": 2},
        "sizing_config": {"mode": "fixed_lots", "fixed_lots": 100, "enabled": True},
        "request": {"lots": 5},
    },
    "config": {"option_backtest": {"enabled": True, "moneyness": "atm", "lots": 5}},
}


# --- Trades.csv --------------------------------------------------------------

def test_csv_row_count_matches_the_pane_for_a_premium_native_run(tmp_path):
    """The regression that produced a literal "(empty)" download."""
    csv = _export_csv(PREMIUM_RUN, tmp_path)
    assert csv != "(empty)"
    _, rows = _rows(csv)
    assert _pane_rows(PREMIUM_RUN) == 2
    assert len(rows) == 2


def test_csv_row_count_matches_the_pane_for_a_spot_run(tmp_path):
    csv = _export_csv(SPOT_RUN, tmp_path)
    _, rows = _rows(csv)
    assert len(rows) == _pane_rows(SPOT_RUN) == 2


@pytest.mark.parametrize("run,expected", [("SPOT_RUN", 5000.0), ("PREMIUM_RUN", 75000.0)])
def test_csv_rupee_pnl_column_reconciles_to_the_portfolio(run, expected, tmp_path):
    """`opt_pnl_value` is the money; its sum must equal the run's net P&L."""
    csv = _export_csv(globals()[run], tmp_path)
    header, rows = _rows(csv)
    assert "opt_pnl_value" in header, header
    col = header.index("opt_pnl_value")
    total = sum(float(r.split(",")[col]) for r in rows)
    assert total == pytest.approx(expected)


def test_csv_preserves_every_previously_exported_spot_column(tmp_path):
    """The fix must add option columns without dropping the raw diagnostics."""
    header, _ = _rows(_export_csv(SPOT_RUN, tmp_path))
    for key in SPOT_RUN["trades"][0]:
        assert key in header, "%s was dropped from the export" % key


def test_csv_option_columns_join_by_id_not_position(tmp_path):
    """A premium-native run filters to PAIRED legs; position-joining would then
    slide every later row onto the wrong option."""
    run = json.loads(json.dumps(PREMIUM_RUN))
    # An unpaired leg at the FRONT: paired positions become 0,1 while ids are 1,2.
    run["option_backtest"]["trades"].insert(0, _option_leg(0, 0.0, status="NO_DATA"))
    run["option_backtest"]["trades"][1]["index_trade_id"] = 1
    run["option_backtest"]["trades"][2]["index_trade_id"] = 2
    header, rows = _rows(_export_csv(run, tmp_path))
    col = header.index("opt_pnl_value")
    assert [float(r.split(",")[col]) for r in rows] == [90000.0, -15000.0]


def test_csv_labels_premium_prices_as_premium_not_spot(tmp_path):
    """A premium-native run has no index leg; its prices ARE premium."""
    header, _ = _rows(_export_csv(PREMIUM_RUN, tmp_path))
    assert "entry_premium" in header and "exit_premium" in header
    assert "entry_price" not in header

    spot_header, _ = _rows(_export_csv(SPOT_RUN, tmp_path))
    assert "entry_price" in spot_header and "entry_premium" not in spot_header


def test_csv_omits_option_columns_when_option_execution_was_off(tmp_path):
    run = json.loads(json.dumps(SPOT_RUN))
    run["option_backtest"] = {"enabled": False, "trades": []}
    header, rows = _rows(_export_csv(run, tmp_path))
    assert not [h for h in header if h.startswith("opt_")]
    assert len(rows) == 2


# --- Save as preset ----------------------------------------------------------

def _build_execution(run):
    """Run the real buildExecutionFromRun, lifted out of the component."""
    src = open(_LAB, encoding="utf-8").read()
    m = re.search(r"  const buildExecutionFromRun = \(run\) => \{.*?\n  \};\n", src, re.S)
    assert m, "buildExecutionFromRun not found -- did it move?"
    body = m.group(0).strip().replace("const buildExecutionFromRun", "const build", 1)
    return _node(
        "const parseDteFilter = (v) => (Array.isArray(v) ? v : (v == null ? null : [v]));\n"
        + body
        + "\nprocess.stdout.write(JSON.stringify(build(%s) ?? null));\n" % json.dumps(run)
    )


def test_preset_sizing_matches_the_lots_the_run_actually_traded():
    """The optimizer pinned 100 lots; the request still said 5."""
    ex = _build_execution(PREMIUM_RUN)
    assert {t["lots"] for t in PREMIUM_RUN["option_backtest"]["trades"]} == {100}
    assert ex["sizing_config"]["fixed_lots"] == 100
    assert ex["sizing_config"]["enabled"] is True
    assert ex["lots"] == 100, "preset would deploy the requested size, not the executed one"


def test_preset_keeps_requested_lots_when_sizing_was_not_pinned():
    """Sizing disabled -> the run really did trade its requested lots."""
    ex = _build_execution(SPOT_RUN)
    assert ex["lots"] == 5
    assert ex["sizing_config"]["enabled"] is False


# --- the buttons that were already correct -----------------------------------

def test_deploy_button_only_deep_links_and_never_places_an_order():
    """Deploy must stay a navigation into the wizard, not an action."""
    src = open(_LAB, encoding="utf-8").read()
    m = re.search(r"onDeploy=\{\(\) => ([^}]+)\}", src)
    assert m, "Deploy handler not found"
    handler = m.group(1)
    assert handler.startswith("navigate("), handler
    assert "/live?backtest=" in handler


def test_config_and_result_exports_read_the_canonical_envelopes():
    """Config must carry params_applied (the merged set the backend deploys
    from); Result must stay a lossless dump."""
    src = open(os.path.join(_LIB, "exports.js"), encoding="utf-8").read()
    cfg = re.search(r"exportBacktestConfig = \(result\) => \{.*?\n\};", src, re.S).group(0)
    assert "params_applied: result?.params_applied" in cfg
    assert "config: result?.config" in cfg
    res = re.search(r"exportBacktestResult = \(result\) => \{.*?\n\};", src, re.S).group(0)
    assert "exportJson(result," in res


# ---------------------------------------------------------------------------
# Round 2 — the pane/CSV changes requested after the first audit.
# ---------------------------------------------------------------------------

def _fmt(expr):
    """Evaluate an expression against the real fmt.js module."""
    return _node(
        "const F = await import(%r);\n"
        "process.stdout.write(JSON.stringify(%s));\n"
        % (_url("fmt.js"), expr)
    )


def test_trade_stamps_carry_the_year():
    """A multi-year backtest rendered "03 Nov" — Jan 2025 and Jan 2026 identical."""
    # 04:46 UTC == 10:16 IST on 2025-11-03
    assert _fmt("F.tsToDateTime(Date.UTC(2025,10,3,4,46,0))") == "03-Nov-25 10:16"


def test_signal_journal_stamp_is_untouched():
    """tsToTime is shared with another page; it must not have changed."""
    assert _fmt("F.tsToTime(Date.UTC(2025,10,3,4,46,0))") == "03 Nov 10:16"


def test_ist_date_does_not_roll_back_across_utc_midnight():
    """09:15 IST is 03:45 UTC the SAME day; a UTC-based date would be right, but
    00:30 IST is 19:00 UTC the PREVIOUS day — that is the one that bites."""
    assert _fmt("F.tsToIstDate(Date.UTC(2025,0,2,3,45,0))") == "2025-01-02"
    assert _fmt("F.tsToIstDate(Date.UTC(2025,0,1,19,0,0))") == "2025-01-02"


def test_join_carries_gross_option_points():
    """`Opt P&L pts` reads the engine's stored figure, not a UI recomputation."""
    out = _node(
        "const M = await import(%r);\n"
        "const r = %s;\n"
        "const rows = M.joinOptionLegs(M.displayTrades(r), r.option_backtest);\n"
        "process.stdout.write(JSON.stringify(rows.map(x => x.opt_pnl_pts)));\n"
        % (_url("backtestMetrics.js"), json.dumps(SPOT_RUN))
    )
    assert out == [20.0, 20.0]


def test_csv_includes_opt_pnl_pts(tmp_path):
    header, _ = _rows(_export_csv(SPOT_RUN, tmp_path))
    assert "opt_pnl_pts" in header


def _export_view(run, view, tmp_path):
    src = open(os.path.join(_LIB, "exports.js"), encoding="utf-8").read()
    src = src.replace('from "@/lib/backtestMetrics"', "from %r" % _url("backtestMetrics.js"))
    shim = tmp_path / "exports_view.mjs"
    shim.write_text(src, encoding="utf-8")
    return _node(
        "let csv=null;\n"
        "globalThis.Blob = class { constructor(p){ csv = p.join(''); } };\n"
        "globalThis.URL = { createObjectURL: () => 'blob:x', revokeObjectURL(){} };\n"
        "globalThis.document = { createElement: () => ({ click(){} }),"
        " body: { appendChild(){}, removeChild(){} } };\n"
        "const M = await import(%r);\n"
        "const E = await import(%r);\n"
        "const r = %s;\n"
        "const all = M.joinOptionLegs(M.displayTrades(r), r.option_backtest);\n"
        "const v = %s;\n"
        "E.exportTradesCsv(r, v ? { rows: all.filter((_,i)=> v.keep.includes(i)),"
        " filters: v.filters, total: all.length } : null);\n"
        "process.stdout.write(JSON.stringify(csv));\n"
        % (_url("backtestMetrics.js"),
           "file:///" + str(shim).replace("\\", "/"),
           json.dumps(run), json.dumps(view))
    )


def test_unfiltered_export_has_no_comment_preamble(tmp_path):
    """An unfiltered download must stay exactly as before — no leading '#' rows
    to break anyone's existing reader."""
    csv = _export_view(SPOT_RUN, None, tmp_path)
    assert not csv.startswith("#")
    assert csv.split("\n")[0].startswith("trade_no,")


def test_filtered_export_carries_only_additive_metrics(tmp_path):
    """Keep just the winning trade (index 0, +7500)."""
    csv = _export_view(SPOT_RUN, {"keep": [0], "filters": {"outcome": "win"}}, tmp_path)
    head = [l for l in csv.split("\n") if l.startswith("#")]
    blob = "\n".join(head)
    assert "net_pnl_inr: 7500" in blob
    assert "trades_in_view: 1 of 2" in blob
    assert "wins: 1  losses: 0" in blob
    # The unsafe ones must be named as absent, never printed as numbers.
    for banned in ("max_drawdown", "sharpe", "significance_ci"):
        assert banned + ":" not in blob
    assert "NOT recomputed" in blob
    # Data still parses: the header row follows the comments.
    body = [l for l in csv.split("\n") if l and not l.startswith("#")]
    assert body[0].startswith("trade_no,")
    assert len(body) == 2  # header + 1 row


def test_date_range_lands_in_the_filename(tmp_path):
    """Captured via the anchor's download attribute rather than the blob."""
    src = open(os.path.join(_LIB, "exports.js"), encoding="utf-8").read()
    src = src.replace('from "@/lib/backtestMetrics"', "from %r" % _url("backtestMetrics.js"))
    shim = tmp_path / "exports_fn.mjs"
    shim.write_text(src, encoding="utf-8")
    name = _node(
        "let fn=null;\n"
        "globalThis.Blob = class { constructor(p){} };\n"
        "globalThis.URL = { createObjectURL: () => 'blob:x', revokeObjectURL(){} };\n"
        "globalThis.document = { createElement: () => ({ set download(v){ fn=v; }, click(){} }),"
        " body: { appendChild(){}, removeChild(){} } };\n"
        "const M = await import(%r);\n"
        "const E = await import(%r);\n"
        "const r = %s;\n"
        "const all = M.joinOptionLegs(M.displayTrades(r), r.option_backtest);\n"
        "E.exportTradesCsv(r, { rows: all, filters: { date_from: '2025-01-01', date_to: '2025-06-30' }, total: all.length });\n"
        "process.stdout.write(JSON.stringify(fn));\n"
        % (_url("backtestMetrics.js"), "file:///" + str(shim).replace("\\", "/"), json.dumps(SPOT_RUN))
    )
    assert "2025-01-01_to_2025-06-30" in name


# --- pane wiring (source-level; the live DOM is verified separately) ---------

def _lab_src():
    return open(_LAB, encoding="utf-8").read()


def test_win_loss_filter_uses_option_money():
    """Spot and option legs disagree on 6-7% of trades across the corpus."""
    src = _lab_src()
    block = re.search(r"const filtered = useMemo\(\(\) => \{.*?\}, \[indexed", src, re.S).group(0)
    assert "opt_pnl_value" in block, "outcome filter still classifies by the spot leg"
    assert "optionEnabled" in block


def test_date_range_filters_on_entry_not_exit():
    src = _lab_src()
    block = re.search(r"const filtered = useMemo\(\(\) => \{.*?\}, \[indexed", src, re.S).group(0)
    assert "tsToIstDate(t.entry_ts) >= dateFrom" in block
    assert "tsToIstDate(t.exit_ts)" not in block


def test_redundant_columns_are_hidden_not_deleted():
    """Fields stay on the row (and in the CSV); only the rendered column goes."""
    src = _lab_src()
    block = re.search(r"const HIDDEN_COLUMN_KEYS = new Set\(\[.*?\]\);", src, re.S).group(0)
    assert '"score"' in block
    assert '"direction"' in block and '"opt_strike"' in block
    # ...and only while an Opt Leg column exists to replace them.
    assert "optionEnabled ?" in block
    # The keys must still be exported.
    exp = open(os.path.join(_LIB, "exports.js"), encoding="utf-8").read()
    assert '"direction"' in exp and '"score"' in exp and '"opt_strike"' in exp


def test_option_exit_columns_no_longer_share_one_label():
    src = _lab_src()
    labels = re.findall(r'\{ key: "opt_\w+", label: "([^"]+)"', src)
    assert len(labels) == len(set(labels)), f"duplicate option column labels: {labels}"
    assert "Opt Exit Reason" in labels
    assert "Opt P&L pts" in labels


# --- sparse option legs -----------------------------------------------------
# 16 stored runs have far FEWER option legs than spot trades (835 signals / 317
# legs on one) because a signal with no option data never produced a leg. Any
# positional fallback in the join stamps another trade's strike and rupee P&L
# onto those rows: 1,100 rows corpus-wide, and the export stopped reconciling
# (-104,324.65 against a true -63,181.45).

SPARSE_RUN = {
    "id": "sparse01", "name": "sparse legs", "strategy_id": "atr_sigma_router",
    "trades": [
        {"direction": "CE", "entry_ts": 1, "exit_ts": 2, "pnl_pts": 1.0},   # 0 -> leg
        {"direction": "PE", "entry_ts": 3, "exit_ts": 4, "pnl_pts": 2.0},   # 1 -> NO leg
        {"direction": "CE", "entry_ts": 5, "exit_ts": 6, "pnl_pts": 3.0},   # 2 -> leg
    ],
    "metrics": {"total_pnl_pts": 6.0},
    "option_backtest": {
        "enabled": True,
        # Only two legs, for spot trades 0 and 2. Position 1 in this array is
        # trade 2's leg — exactly what a positional join would mis-attach.
        "trades": [_option_leg(0, 1000.0), _option_leg(2, 4000.0)],
        "portfolio": {"net_pnl_value": 5000.0},
    },
    "config": {"option_backtest": {"enabled": True, "lots": 5}},
}


def test_trade_without_an_option_leg_stays_blank():
    """Never borrow a neighbouring trade's leg."""
    out = _node(
        "const M = await import(%r);\n"
        "const r = %s;\n"
        "const rows = M.joinOptionLegs(M.displayTrades(r), r.option_backtest);\n"
        "process.stdout.write(JSON.stringify(rows.map(x => "
        "({ pnl: x.opt_pnl_value, sym: x.opt_symbol, strike: x.opt_strike }))));\n"
        % (_url("backtestMetrics.js"), json.dumps(SPARSE_RUN))
    )
    assert out[0]["pnl"] == 1000.0
    assert out[1] == {"pnl": None, "sym": None, "strike": None}, \
        "trade 1 has no option leg and must not borrow one"
    assert out[2]["pnl"] == 4000.0


def test_sparse_run_csv_still_reconciles(tmp_path):
    """The money column must equal the run's net P&L, not an inflated sum."""
    header, rows = _rows(_export_csv(SPARSE_RUN, tmp_path))
    col = header.index("opt_pnl_value")
    total = sum(float(r.split(",")[col]) for r in rows if r.split(",")[col])
    assert len(rows) == 3
    assert total == pytest.approx(SPARSE_RUN["option_backtest"]["portfolio"]["net_pnl_value"])


def test_summary_does_not_count_legless_trades_as_losses(tmp_path):
    """`Number(null)` is 0 and `Number.isFinite(0)` is true, so mapping before
    discarding blanks turned every trade with no option fill into a zero-P&L
    loss — 733 losses / 12.22% win rate on a run whose true figures were
    212 / 32.48%."""
    run = json.loads(json.dumps(SPARSE_RUN))
    csv = _export_view(run, {"keep": [0, 1, 2], "filters": {"date_from": "2025-01-01"}}, tmp_path)
    head = "\n".join(l for l in csv.split("\n") if l.startswith("#"))
    # 3 rows, only 2 of which have an option fill (trade 1 has no leg).
    assert "trades_in_view: 3 of 3" in head
    assert "trades_with_option_fill: 2" in head
    assert "wins: 2  losses: 0" in head
    assert "win_rate_pct: 100" in head
    assert "net_pnl_inr: 5000" in head


def test_summary_omits_the_fill_line_when_every_trade_has_one():
    """No noise on the common dense-leg run."""
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        csv = _export_view(SPOT_RUN, {"keep": [0, 1], "filters": {"outcome": "win"}}, pathlib.Path(d))
    head = "\n".join(l for l in csv.split("\n") if l.startswith("#"))
    assert "trades_with_option_fill" not in head


def test_premium_native_csv_does_not_repeat_the_premium_columns(tmp_path):
    """A premium-native run has no spot leg, so entry_premium/exit_premium/
    pnl_premium_pts and opt_entry/opt_exit/opt_pnl_pts held byte-identical
    values. The pane drops the repeats; the CSV must agree."""
    header, _ = _rows(_export_csv(PREMIUM_RUN, tmp_path))
    for dup in ("opt_entry", "opt_exit", "opt_pnl_pts", "opt_pnl_pct"):
        assert dup not in header, f"{dup} duplicates a base premium column"
    for kept in ("entry_premium", "exit_premium", "pnl_premium_pts",
                 "opt_symbol", "opt_lots", "opt_qty", "opt_charges", "opt_pnl_value"):
        assert kept in header
    # ...but an ordinary option run still gets the full option block.
    spot_header, _ = _rows(_export_csv(SPOT_RUN, tmp_path))
    for k in ("opt_entry", "opt_exit", "opt_pnl_pts", "opt_pnl_pct"):
        assert k in spot_header


def test_comment_block_survives_a_run_name_containing_commas(tmp_path):
    """Run names routinely read "... net_pnl_inr, 404%" — a raw comma would
    split the comment line across spreadsheet columns."""
    run = json.loads(json.dumps(SPOT_RUN))
    run["name"] = "Optimized, fibonacci_pullback, net_pnl_inr, 404%"
    csv = _export_view(run, {"keep": [0], "filters": {"outcome": "win"}}, tmp_path)
    line = [l for l in csv.split("\n") if l.startswith("# run:")][0]
    assert "," not in line
    assert "404%" in line


def test_export_filtered_to_zero_rows_explains_itself(tmp_path):
    """A file containing only "(empty)" is the very failure this export was fixed
    for; a user who narrowed a date range too far cannot tell it from a broken
    download. Emit the summary plus the header row instead."""
    csv = _export_view(SPOT_RUN, {"keep": [], "filters": {"date_from": "2099-01-01"}}, tmp_path)
    assert csv != "(empty)"
    comments = [l for l in csv.split("\n") if l.startswith("#")]
    data = [l for l in csv.split("\n") if l and not l.startswith("#")]
    assert any("date_from=2099-01-01" in c for c in comments)
    assert any("trades_in_view: 0 of 2" in c for c in comments)
    assert len(data) == 1 and data[0].startswith("trade_no,")   # header only


def test_run_with_genuinely_no_trades_still_reports_empty(tmp_path):
    """Distinct from 'filtered to nothing': there is no column set to emit."""
    run = json.loads(json.dumps(SPOT_RUN))
    run["trades"] = []
    run["option_backtest"]["trades"] = []
    assert _export_csv(run, tmp_path) == "(empty)"


def test_pane_explains_an_empty_filtered_result():
    """Pre-existing behaviour, pinned: a date range can exclude every row, and
    headers over a blank body would read as "this run took no trades"."""
    src = open(_LAB, encoding="utf-8").read()
    assert "sorted.length === 0 &&" in src
    assert 'data-testid="trades-empty-filtered"' in src
    assert "No trades match the current filters." in src
