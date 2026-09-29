"""Behaviour of frontend/src/lib/liveTimelineView.js, EXECUTED through node.

The "Today's timeline" panel renders whatever formatTimeline returns: IST wall-clock
times, the label/detail text, and the gaps footnote. A failed fetch must never read as
an empty day.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "liveTimelineView.js")

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


def test_times_are_shown_in_ist_not_utc_or_local():
    out = _run_js("""
      return [M.istClock("2026-06-25T04:00:00+00:00"),      // 09:30 IST
              M.istClock("2026-06-25T18:29:59+00:00"),      // 23:59:59 IST
              M.istClock("2026-06-25T18:30:00+00:00"),      // 00:00:00 IST next day
              M.istClock("2026-06-25T09:31:07+05:30")];     // already IST-offset input
    """)
    assert out == ["09:30:00", "23:59:59", "00:00:00", "09:31:07"]


def test_an_unreadable_time_is_null_and_renders_as_a_dash():
    out = _run_js("""
      const r = M.formatTimeline({events: [{ts: "garbage", kind: "signal", label: "x", detail: "y"},
                                           {kind: "signal", label: "no ts"}]});
      return [M.istClock("nope"), M.istClock(null), M.istClock(12345), M.istClock(undefined),
              r.rows.map((x) => x.time)];
    """)
    assert out[:4] == [None, None, None, None]
    assert out[4] == ["—", "—"]


def test_rows_keep_the_servers_order_and_carry_label_detail_and_time():
    out = _run_js("""
      return M.formatTimeline({date: "2026-06-25", events: [
        {ts: "2026-06-25T04:00:00+00:00", kind: "signal", label: "CE signal", detail: "NIFTY"},
        {ts: "2026-06-25T04:00:02+00:00", kind: "refused", label: "Entry refused", detail: "max_concurrent"},
        {ts: "2026-06-25T05:00:00+00:00", kind: "exit", label: "Exit X", detail: "P&L —"},
      ], gaps: []});
    """)
    assert out["date"] == "2026-06-25" and out["empty"] is False and out["unavailable"] is False
    assert [(r["time"], r["label"], r["detail"]) for r in out["rows"]] == [
        ("09:30:00", "CE signal", "NIFTY"),
        ("09:30:02", "Entry refused", "max_concurrent"),
        ("10:30:00", "Exit X", "P&L —"),
    ]
    assert len({r["key"] for r in out["rows"]}) == 3


def test_a_refusal_and_a_disable_read_as_danger_a_hold_as_warn_the_rest_quiet():
    out = _run_js("""
      const tone = (kind) => M.formatTimeline({events: [{ts: "2026-06-25T04:00:00Z", kind}]}).rows[0].tone;
      return ["refused", "disabled", "hold", "caps", "entry", "exit", "signal", "order", "state",
              "intended", "unheard-of"].map(tone);
    """)
    assert out == ["danger", "danger", "warn", "warn", "default", "default", "default",
                   "dim", "dim", "dim", "dim"]


def test_gaps_are_kept_in_order_and_blank_or_non_string_ones_are_dropped():
    out = _run_js("""
      return M.formatTimeline({events: [], gaps: ["first gap", "", "   ", 5, null, "second gap"]}).gaps;
    """)
    assert out == ["first gap", "second gap"]


def test_an_empty_day_is_empty_but_still_carries_its_gaps():
    out = _run_js("""
      return M.formatTimeline({date: "2026-06-25", events: [], gaps: ["not recorded: X"]});
    """)
    assert out["empty"] is True and out["unavailable"] is False
    assert out["gaps"] == ["not recorded: X"] and out["rows"] == []


def test_a_response_that_is_not_an_object_is_unavailable_never_an_empty_day():
    out = _run_js("""
      return [null, undefined, "boom", 7, []].map((r) => {
        const v = M.formatTimeline(r);
        return [v.unavailable, v.empty, v.rows.length, v.date];
      });
    """)
    assert out == [[True, False, 0, None]] * 5


def test_malformed_events_do_not_break_the_list():
    out = _run_js("""
      const v = M.formatTimeline({events: [null, "x", 3, {ts: "2026-06-25T04:00:00Z", kind: "entry",
                                                       label: null, detail: undefined}]});
      return v.rows;
    """)
    assert len(out) == 1
    assert out[0]["label"] == "" and out[0]["detail"] == "" and out[0]["time"] == "09:30:00"
