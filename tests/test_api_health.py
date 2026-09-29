"""Behaviour of frontend/src/lib/apiHealth.js, EXECUTED through node.

The sidebar footer was a hard-coded green dot beside "local API live", so a crashed
backend container left every page saying the API was live. The dot now follows a real
/api/health poll; this pins what each poll history is allowed to show.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib", "apiHealth.js")

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


def _fold(*results, start=1_000_000, step=15_000):
    """Fold results (JS object literals) through nextApiHealth; return final state,
    its description at the last instant, and the list of statuses along the way."""
    lits = ", ".join(results)
    return _run_js(f"""
      let s = M.initialApiHealth();
      const seen = [];
      let t = {start};
      for (const r of [{lits}]) {{ s = M.nextApiHealth(s, r, t); seen.push(s.status); t += {step}; }}
      return {{state: s, seen, view: M.describeApiHealth(s, t - {step})}};
    """)


OK = "{ok: true}"
NO_ANSWER = "{ok: false, httpStatus: null, detail: 'timeout of 5000ms exceeded'}"
HTTP_503 = "{ok: false, httpStatus: 503, detail: 'mongo down'}"


def test_before_any_check_it_is_grey_checking_never_live():
    out = _run_js("return M.describeApiHealth(M.initialApiHealth(), 1e6);")
    assert out["tone"] == "neutral" and out["label"] == "checking API…"
    assert "live" not in out["label"]


def test_a_successful_check_is_green_local_api_live():
    out = _fold(OK)
    assert out["state"]["status"] == "up"
    assert out["view"] == {"tone": "ok", "label": "local API live",
                           "title": "The backend answered its last health check."}


def test_the_first_missed_check_is_amber_not_red():
    """A busy backend can time a ping out: one miss is 'not responding', not 'down'."""
    out = _fold(OK, NO_ANSWER)
    assert out["seen"] == ["up", "degraded"]
    assert out["view"]["tone"] == "warn" and out["view"]["label"] == "API not responding"


def test_two_consecutive_misses_are_red_with_the_last_ok_time():
    """THE defect: the backend is gone and the footer must stop saying live."""
    out = _fold(OK, NO_ANSWER, NO_ANSWER)
    assert out["seen"] == ["up", "degraded", "down"]
    assert out["state"]["failures"] == 2
    assert out["view"]["tone"] == "bad"
    # last OK at t=1_000_000; the last check ran 30s later
    assert out["view"]["label"] == "local API unreachable · last OK 30s ago"
    assert "timeout" in out["view"]["title"]


def test_a_success_after_an_outage_clears_it_completely():
    out = _fold(OK, NO_ANSWER, NO_ANSWER, NO_ANSWER, OK)
    assert out["seen"][-1] == "up" and out["state"]["failures"] == 0
    assert out["view"]["label"] == "local API live"


def test_an_outage_from_the_start_is_red_and_says_never():
    out = _fold(NO_ANSWER, NO_ANSWER)
    assert out["view"]["tone"] == "bad"
    assert out["view"]["label"] == "local API unreachable · last OK never"


def test_a_missed_check_between_successes_does_not_accumulate():
    out = _fold(OK, NO_ANSWER, OK, NO_ANSWER, OK, NO_ANSWER)
    assert out["seen"] == ["up", "degraded", "up", "degraded", "up", "degraded"]


def test_an_http_error_means_reachable_but_unhealthy_amber_never_red():
    """503 from /health: the API ANSWERED (so 'unreachable' would be false) but its
    database check failed (so 'live' would be false too)."""
    out = _fold(OK, HTTP_503, HTTP_503, HTTP_503)
    assert out["seen"] == ["up", "degraded", "degraded", "degraded"]
    assert out["state"]["failures"] == 0
    assert out["view"]["tone"] == "warn" and out["view"]["label"] == "API unhealthy"
    assert "mongo down" in out["view"]["title"]


def test_an_http_error_resets_the_no_answer_streak():
    """It ANSWERED, so it is reachable: the run of silent checks starts over, and one
    later silence is amber again, not red."""
    out = _fold(NO_ANSWER, NO_ANSWER, HTTP_503, NO_ANSWER)
    assert out["seen"] == ["degraded", "down", "degraded", "degraded"]
    assert out["state"]["failures"] == 1


def test_an_http_error_with_no_detail_still_names_the_status():
    out = _fold("{ok: false, httpStatus: 502}")
    assert "HTTP 502" in out["view"]["title"]


def test_a_garbage_previous_state_or_result_is_handled():
    out = _run_js("""
      return [M.nextApiHealth(null, {ok: true}, 5).status,
              M.nextApiHealth(undefined, null, 5).status,
              M.nextApiHealth({status: 'up', failures: 'x'}, {ok: false}, 5).failures,
              M.describeApiHealth(null, 5).tone,
              M.describeApiHealth({status: 'weird'}, 5).tone];
    """)
    assert out == ["up", "degraded", 1, "neutral", "neutral"]


def test_time_since_formatting():
    out = _run_js("""
      const now = 10_000_000;
      return [M.fmtSince(now - 5_000, now), M.fmtSince(now - 59_999, now),
              M.fmtSince(now - 60_000, now), M.fmtSince(now - 3_599_000, now),
              M.fmtSince(now - 3_600_000, now), M.fmtSince(now - 7_300_000, now),
              M.fmtSince(now + 9_000, now),
              M.fmtSince(null, now), M.fmtSince(undefined, now), M.fmtSince("", now),
              M.fmtSince("x", now)];
    """)
    assert out == ["5s ago", "59s ago", "1m ago", "59m ago", "1h ago", "2h ago",
                   "0s ago", "never", "never", "never", "never"]


def test_the_down_threshold_is_two_and_dot_classes_map_by_tone():
    out = _run_js("""
      return {n: M.DOWN_AFTER_FAILURES,
              dots: ["ok", "warn", "bad", "neutral", "?"].map(M.apiDotClass)};
    """)
    assert out["n"] == 2
    assert out["dots"] == ["bg-emerald-500", "bg-amber-400", "bg-red-500",
                           "bg-slate-500", "bg-slate-500"]
