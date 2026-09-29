"""Behaviour of frontend/src/lib/lastEvaluated.js, EXECUTED through node.

The Deployments card printed "Last evaluated 15:29 IST" with no date, so a
deployment idle for DAYS read as current beside its green ACTIVE chip. The label
now carries the date whenever the last evaluation was not TODAY (IST). Grepping
the JSX cannot prove that (the project's own rule) — this runs the decision.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "lastEvaluated.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


def _run_js(body: str, env=None):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    source = (
        f"import * as M from {url!r};\n"
        f"const out = await (async () => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out === undefined ? null : out));\n"
    )
    proc = subprocess.run(["node", "--input-type=module", "-e", source],
                          capture_output=True, text=True, timeout=60,
                          env={**os.environ, **(env or {})})
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


# "now" = Tue 29 Sep 2026 15:30 IST (10:00Z)
NOW = "Date.UTC(2026, 8, 29, 10, 0)"


def test_an_evaluation_from_today_shows_only_the_time_and_is_not_flagged():
    out = _run_js(f"return M.formatLastEvaluated(Date.UTC(2026, 8, 29, 9, 59), {NOW});")
    assert out == {"label": "Last evaluated 15:29 IST", "today": True}


def test_an_evaluation_from_an_earlier_day_carries_the_date_and_is_flagged():
    """THE case: last evaluated Friday 15:29, looked at on Tuesday."""
    out = _run_js(f"return M.formatLastEvaluated(Date.UTC(2026, 8, 25, 9, 59), {NOW});")
    assert out == {"label": "Last evaluated Fri 25 Sep 15:29 IST", "today": False}


def test_minutes_and_hours_are_zero_padded():
    out = _run_js(f"return M.formatLastEvaluated(Date.UTC(2026, 8, 29, 3, 35), {NOW});")
    assert out["label"] == "Last evaluated 09:05 IST"


def test_the_day_is_the_IST_day_not_the_UTC_day():
    # 17:00Z on the 29th is 22:30 IST on the 29th; "now" is 01:30 IST on the 30th.
    # Same UTC date, DIFFERENT IST date -> not today.
    out = _run_js("return M.formatLastEvaluated(Date.UTC(2026, 8, 29, 17, 0), "
                  "Date.UTC(2026, 8, 29, 20, 0));")
    assert out == {"label": "Last evaluated Tue 29 Sep 22:30 IST", "today": False}
    # 18:45Z on the 29th is 00:15 IST on the 30th; "now" is 06:30 IST on the 30th.
    # Different UTC date, SAME IST date -> today.
    out = _run_js("return M.formatLastEvaluated(Date.UTC(2026, 8, 29, 18, 45), "
                  "Date.UTC(2026, 8, 30, 1, 0));")
    assert out == {"label": "Last evaluated 00:15 IST", "today": True}


def test_the_viewers_own_timezone_never_moves_the_label():
    body = (f"return [M.formatLastEvaluated(Date.UTC(2026, 8, 25, 9, 59), {NOW}),"
            f" M.formatLastEvaluated(Date.UTC(2026, 8, 29, 9, 59), {NOW})];")
    outs = [_run_js(body, env={"TZ": tz})
            for tz in ("UTC", "America/Los_Angeles", "Asia/Tokyo", "Asia/Kolkata")]
    assert all(o == outs[0] for o in outs)
    assert outs[0][0]["label"] == "Last evaluated Fri 25 Sep 15:29 IST"


def test_a_different_year_is_spelled_out():
    out = _run_js(f"return M.formatLastEvaluated(Date.UTC(2025, 11, 31, 9, 59), {NOW});")
    assert out["label"] == "Last evaluated Wed 31 Dec 2025 15:29 IST"
    assert out["today"] is False


def test_a_numeric_string_timestamp_is_accepted():
    out = _run_js(f"return M.formatLastEvaluated(String(Date.UTC(2026, 8, 29, 9, 59)), {NOW});")
    assert out["label"] == "Last evaluated 15:29 IST"


@pytest.mark.parametrize("bad", ["null", "undefined", '""', "0", "-5", '"abc"', "NaN", "Infinity"])
def test_nothing_to_show_is_null_never_a_made_up_time(bad):
    assert _run_js(f"return M.formatLastEvaluated({bad}, {NOW});") is None


def test_an_unusable_now_falls_back_to_showing_the_date():
    """If "now" cannot be read, hiding the date is the dangerous default."""
    out = _run_js('return M.formatLastEvaluated(Date.UTC(2026, 8, 29, 9, 59), "abc");')
    assert out == {"label": "Last evaluated Tue 29 Sep 15:29 IST", "today": False}
