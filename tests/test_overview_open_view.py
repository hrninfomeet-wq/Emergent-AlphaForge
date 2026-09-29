"""Behaviour of frontend/src/lib/overviewOpenView.js, EXECUTED through node.

The card's OPEN accounting now reports carried rows and the OTHER book (a demoted
deployment's real-money live_trades) as separate lines. What each may say — and that
they are surfaced, never folded into the figures above them.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib", "overviewOpenView.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


def _run_js(body: str):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    source = (
        f"import * as M from {url!r};\n"
        f"const out = await (async () => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out === undefined ? null : out));\n"
    )
    proc = subprocess.run(["node", "--input-type=module", "-e", source],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def _notes(today: str, mode: str = "'paper'"):
    return _run_js(f"return M.openNotes({today}, {mode});")


def test_a_card_with_nothing_unusual_has_no_extra_lines():
    assert _notes("{open_trades: 1, open_carried: 0, other_book: null}") == []
    assert _notes("{}") == [] and _notes("null") == [] and _notes("undefined") == []


def test_carried_rows_get_a_warning_line_with_the_oldest_entry_date():
    n = _notes("{open_trades: 2, open_carried: 1, open_carried_oldest: '2026-09-25'}")
    assert len(n) == 1 and n[0]["id"] == "carried" and n[0]["tone"] == "warn"
    assert n[0]["text"] == "1 open trade carried from an earlier day (oldest entered Fri 25 Sep)"
    assert "not entered today" in n[0]["title"]


def test_carried_plural_and_missing_date():
    n = _notes("{open_carried: 3}")
    assert n[0]["text"] == "3 open trades carried from an earlier day"


def test_a_demoted_deployments_live_rows_are_a_danger_line_naming_real_money():
    """THE second defect: the still-OPEN real-money row must not vanish from the card."""
    n = _notes("{open_trades: 0, other_book: {book: 'live', open_trades: 1, open_carried: 1, "
               "oldest_open_entry_ist: '2026-09-16', realized_today: -300}}")
    assert len(n) == 1 and n[0]["id"] == "other-book-live" and n[0]["tone"] == "danger"
    assert n[0]["text"] == (
        "This deployment is now PAPER, but its live book still shows: "
        "1 OPEN LIVE trade (real money), oldest entered Wed 16 Sep; live realized today −₹300.")
    assert "NOT in the figures above" in n[0]["title"]


def test_the_live_line_uses_the_deployments_actual_mode():
    n = _notes("{other_book: {book: 'live', open_trades: 2, realized_today: 0}}", "'signal_only'")
    assert n[0]["text"].startswith("This deployment is now SIGNAL_ONLY")
    assert "2 OPEN LIVE trades (real money)" in n[0]["text"]
    assert "realized today" not in n[0]["text"]


def test_a_live_book_with_only_realized_pnl_is_still_reported():
    n = _notes("{other_book: {book: 'live', open_trades: 0, realized_today: 250.4}}")
    assert n[0]["tone"] == "danger" and "live realized today +₹250" in n[0]["text"]
    assert "OPEN LIVE" not in n[0]["text"]


def test_a_stray_paper_row_on_a_live_deployment_is_a_warning_not_danger():
    n = _notes("{other_book: {book: 'paper', open_trades: 1, oldest_open_entry_ist: '2026-09-25', "
               "realized_today: 0}}", "'live'")
    assert n[0]["id"] == "other-book-paper" and n[0]["tone"] == "warn"
    assert n[0]["text"] == "1 stray paper trade still OPEN on this live deployment (oldest entered Fri 25 Sep)"


def test_a_paper_book_with_nothing_open_says_nothing():
    assert _notes("{other_book: {book: 'paper', open_trades: 0, realized_today: 0}}", "'live'") == []


def test_both_lines_can_appear_together_in_a_stable_order():
    n = _notes("{open_carried: 1, open_carried_oldest: '2026-09-25', "
               "other_book: {book: 'live', open_trades: 1, realized_today: 0}}")
    assert [x["id"] for x in n] == ["carried", "other-book-live"]


def test_header_note_sums_the_live_book_under_non_live_deployments():
    n = _run_js("return M.headerOtherBookNote({other_book_live_open: 2, other_book_live_realized_today: -1200});")
    assert n["tone"] == "danger"
    assert n["text"] == "Live book under non-live deployments: 2 open live trades · −₹1,200 realized today"
    assert "Not included in Today MTM" in n["title"]


def test_header_note_is_absent_when_there_is_nothing():
    out = _run_js("""
      return [M.headerOtherBookNote({}), M.headerOtherBookNote(null),
              M.headerOtherBookNote({other_book_live_open: 0, other_book_live_realized_today: 0})];
    """)
    assert out == [None, None, None]


def test_header_note_with_only_open_or_only_realized():
    a = _run_js("return M.headerOtherBookNote({other_book_live_open: 1});")
    assert a["text"] == "Live book under non-live deployments: 1 open live trade"
    b = _run_js("return M.headerOtherBookNote({other_book_live_realized_today: 80});")
    assert b["text"] == "Live book under non-live deployments: +₹80 realized today"


def test_rupee_and_day_formatting_never_invent_a_number():
    out = _run_js("""
      return {r: [M.fmtRupees(-300), M.fmtRupees(40), M.fmtRupees(0), M.fmtRupees(null),
                  M.fmtRupees(undefined), M.fmtRupees(""), M.fmtRupees("x"), M.fmtRupees(NaN),
                  M.fmtRupees(1234567)],
              d: [M.fmtDay("2026-09-25"), M.fmtDay("bad"), M.fmtDay(null), M.fmtDay("2026-13-40")]};
    """)
    assert out["r"] == ["−₹300", "+₹40", "₹0", "—", "—", "—", "—", "—", "+₹12,34,567"]
    assert out["d"] == ["Fri 25 Sep", None, None, None]
