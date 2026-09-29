"""Behaviour of frontend/src/lib/liveTradeView.js, EXECUTED through node.

The Live Trade Statistics tab rendered `status: "OPEN"` from the raw journal as a green
chip. An OPEN row is the journal's word — green is now reserved for a row the app's own
guard is still marking; a stale / never-marked one is amber "unverified", and a row with
no verification field at all (an older backend) is never green.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib", "liveTradeView.js")

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


def _chip(row: str):
    return _run_js(f"return M.tradeStatusChip({row});")


def test_an_open_row_the_guard_is_marking_is_the_only_green():
    c = _chip("{status: 'OPEN', open_state: 'verified', mark_age_s: 3, carried: false}")
    assert c["label"] == "OPEN" and c["tone"] == "live"
    assert "guard is marking" in c["title"] and "3s ago" in c["title"]


def test_an_open_row_with_a_stale_mark_is_amber_unverified_not_green():
    """THE residual defect: OPEN in the journal, but nobody has marked it for hours."""
    c = _chip("{status: 'OPEN', open_state: 'unverified', mark_age_s: 10800}")
    assert c["label"] == "OPEN · unverified" and c["tone"] == "unverified"
    assert "3 h ago" in c["title"] and "Check the broker position book" in c["title"]


def test_an_open_row_that_was_never_marked_says_never_marked():
    for age in ("null", "undefined"):
        c = _chip("{status: 'OPEN', open_state: 'unverified', mark_age_s: %s}" % age)
        assert c["tone"] == "unverified" and "never marked" in c["title"]


def test_an_open_row_from_an_older_backend_is_never_green():
    """No open_state field: unknown, so not vouched for."""
    c = _chip("{status: 'OPEN'}")
    assert c["tone"] == "unverified" and c["label"] == "OPEN · unverified"


def test_carried_rows_carry_their_entry_day_in_the_label():
    v = _chip("{status: 'OPEN', open_state: 'verified', mark_age_s: 2, carried: true, "
              "entry_day_ist: '2026-09-25'}")
    assert v["label"] == "OPEN · carried 25 Sep" and v["tone"] == "live"
    assert "earlier day" in v["title"]
    u = _chip("{status: 'OPEN', open_state: 'unverified', mark_age_s: 900000, carried: true, "
              "entry_day_ist: '2026-09-16'}")
    assert u["label"] == "OPEN · unverified · carried 16 Sep" and u["tone"] == "unverified"


def test_a_carried_row_with_an_unreadable_entry_day_still_says_carried():
    c = _chip("{status: 'OPEN', open_state: 'verified', carried: true, entry_day_ist: null}")
    assert c["label"] == "OPEN · carried"
    assert _chip("{status: 'OPEN', open_state: 'verified', carried: true, "
                 "entry_day_ist: '2026-13-40'}")["label"] == "OPEN · carried"


def test_closed_and_other_statuses_are_unchanged():
    assert _chip("{status: 'CLOSED'}") == {"label": "CLOSED", "tone": "closed", "title": ""}
    assert _chip("{status: 'closed'}")["tone"] == "closed"
    assert _chip("{status: 'REJECTED'}") == {"label": "REJECTED", "tone": "other", "title": ""}
    assert _chip("{}")["label"] == "—" and _chip("null")["label"] == "—"


def test_open_state_is_only_believed_when_it_says_verified():
    for state in ("'weird'", "true", "1", "'VERIFIED'"):
        c = _chip("{status: 'OPEN', open_state: %s}" % state)
        assert c["tone"] == "unverified", state


def test_mark_age_formatting_never_invents_freshness():
    out = _run_js("""
      return [M.markAge(3), M.markAge(89), M.markAge(90), M.markAge(5399), M.markAge(5400),
              M.markAge(172799), M.markAge(172800), M.markAge(null), M.markAge(undefined),
              M.markAge(""), M.markAge("x"), M.markAge(-5), M.markAge(NaN)];
    """)
    assert out == ["3s ago", "89s ago", "2 min ago", "90 min ago", "2 h ago", "48 h ago",
                   "2 d ago", "never marked", "never marked", "never marked",
                   "never marked", "never marked", "never marked"]


def test_per_strategy_open_cell_shows_unverified_and_carried_counts():
    out = _run_js("""
      return [M.openCountView({open_count: 0}),
              M.openCountView({open_count: 2, open_unverified: 0, open_carried: 0}),
              M.openCountView({open_count: 2, open_unverified: 1}),
              M.openCountView({open_count: 3, open_unverified: 2, open_carried: 1,
                               open_carried_oldest: "2026-09-16"}),
              M.openCountView({open_count: 1, open_carried: 1}),
              M.openCountView({}), M.openCountView(null)];
    """)
    assert out[0] == {"text": "0", "tone": "plain", "title": ""}
    assert out[1]["text"] == "2" and out[1]["tone"] == "plain"
    assert out[2]["text"] == "2 (1 unverified)" and out[2]["tone"] == "warn"
    assert out[3]["text"] == "3 (2 unverified, 1 carried)"
    assert "oldest 16 Sep" in out[3]["title"] and "not vouched for" in out[3]["title"]
    assert out[4]["text"] == "1 (1 carried)" and out[4]["tone"] == "warn"
    assert out[5]["text"] == "0" and out[6]["text"] == "0"


def test_chip_classes_reserve_green_for_the_verified_state():
    out = _run_js("""
      return ["live", "unverified", "closed", "other", "?"].map(M.tradeChipClass);
    """)
    assert "emerald" in out[0]
    assert "emerald" not in out[1] and "amber" in out[1]
    assert all("emerald" not in c for c in out[2:])
