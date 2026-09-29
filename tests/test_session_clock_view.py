"""frontend/src/lib/sessionClock.js, EXECUTED through node.

The countdown must follow the SERVER's instants: a browser clock ten minutes out
used to make every client-side clock ten minutes wrong. The anchor is a server
instant pinned to performance.now(), which no wall-clock skew can move.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib", "sessionClock.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")

# 2026-09-29 10:30 IST, and 15:00 IST, as epoch ms
T1030 = 1790658000000
T1500 = T1030 + 4.5 * 3600 * 1000


def _run_js(body: str):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    source = (f"import * as M from {url!r};\n"
              f"const out = await (async () => {{ {body} }})();\n"
              "process.stdout.write(JSON.stringify(out === undefined ? null : out));\n")
    proc = subprocess.run(["node", "--input-type=module", "-e", source],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def _session(**kw):
    s = {"phase": "session", "entry_cutoff_ist": "15:00",
         "next_event": {"kind": "entry_cutoff", "at_ms": T1500}}
    s.update(kw)
    return s


def test_the_countdown_ignores_a_skewed_browser_clock():
    """Only the server instant and the monotonic delta enter the maths — the
    browser's wall clock (here 10 minutes fast) never does."""
    out = _run_js(f"""
      const realDateNow = Date.now;
      Date.now = () => realDateNow() + 10 * 60 * 1000;   // a skewed PC clock
      const a = M.makeAnchor({T1030}, 1000);
      return M.sessionCountdown({json.dumps(_session())}, a, 1000 + 60 * 1000);
    """)
    assert out["text"] == "entries close 15:00 · 4h 29m"


def test_time_advances_from_the_monotonic_clock():
    out = _run_js(f"""
      const a = M.makeAnchor({T1030}, 500);
      return M.serverNowFrom(a, 500 + 1234);
    """)
    assert out == T1030 + 1234


def test_a_stale_anchor_stops_counting():
    out = _run_js(f"""
      const a = M.makeAnchor({T1030}, 0);
      return M.sessionCountdown({json.dumps(_session())}, a, M.ANCHOR_STALE_MS + 1);
    """)
    assert out["stale"] is True and "stale" in out["text"]


def test_crossing_the_boundary_asks_for_a_refetch():
    out = _run_js(f"""
      const a = M.makeAnchor({T1500} - 1000, 0);
      return M.sessionCountdown({json.dumps(_session())}, a, 2000);
    """)
    assert out["crossed"] is True


def test_the_last_quarter_hour_is_a_warning():
    out = _run_js(f"""
      const a = M.makeAnchor({T1500} - 10 * 60 * 1000, 0);
      return M.sessionCountdown({json.dumps(_session())}, a, 0);
    """)
    assert out["tone"] == "warn" and out["text"].endswith("10m 00s")


def test_a_holiday_names_itself_and_the_next_session():
    out = _run_js("""
      const a = M.makeAnchor(1790919000000, 0);
      return M.sessionCountdown({phase: "closed_day", day_kind: "holiday",
        holiday_label: "Gandhi Jayanti",
        next_event: {kind: "next_session_open", at_ms: 1791171900000}}, a, 0);
    """)
    assert "Gandhi Jayanti" in out["text"] and "2026-10-05 09:15" in out["text"]


def test_an_unknown_eod_is_said_not_guessed():
    out = _run_js(f"""
      const a = M.makeAnchor({T1500}, 0);
      return M.sessionCountdown({{phase: "after_cutoff", next_event: null}}, a, 0);
    """)
    assert "unknown" in out["text"]


def test_a_missing_session_is_a_warning():
    out = _run_js("return [M.sessionCountdown(null, null, 0), "
                  "M.sessionCountdown({error: 'x'}, {serverMs: 0, perf: 0}, 0)];")
    assert all(o["stale"] and o["tone"] == "warn" for o in out)


def test_a_deployments_earlier_entry_end_is_shown_only_when_earlier():
    out = _run_js("""
      const s = {entry_cutoff_ist: "15:00"};
      return [M.deploymentEntryEnd({effective_end: "14:50"}, s),
              M.deploymentEntryEnd({effective_end: "15:00"}, s),
              M.deploymentEntryEnd(null, s)];
    """)
    assert out == ["entries until 14:50", None, None]


def test_the_market_pill_defers_to_the_servers_holiday():
    out = _run_js("""
      return [M.marketPillOverride({is_trading_day: false, day_kind: "holiday",
                                    holiday_label: "Gandhi Jayanti"}),
              M.marketPillOverride({is_trading_day: true}),
              M.marketPillOverride(null),
              M.marketPillOverride({error: "x", is_trading_day: false})];
    """)
    assert out[0]["open"] is False and "HOLIDAY" in out[0]["label"]
    assert "Gandhi Jayanti" in out[0]["title"]
    assert out[1] is None and out[2] is None and out[3] is None
