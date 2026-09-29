"""Behaviour of frontend/src/lib/preopenReadinessView.js, EXECUTED through node.

The 08:45 IST verdict is now shown on /live-trading. What it may claim: nothing unless
it holds a blocker or a warning; a previous day's verdict is muted, dated and labelled
NOT today's; and a morning blocker the broker chips now disprove is dropped.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "preopenReadinessView.js")

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


FT_EXPIRED = "{id: 'flattrade_token_expired', detail: 'The Flattrade session has expired.'}"
UP_MISSING = "{id: 'upstox_not_connected', detail: 'Upstox is not connected.'}"
WAREHOUSE = "{id: 'warehouse_actions_pending', detail: '3 warehouse action(s) outstanding.'}"


def _resp(*, blockers="[]", warnings="[]", is_today="true", days_ago=0,
          session_date="'2026-09-29'", reason="'blocked'", evaluated_at="'2026-09-29T03:15:00+00:00'"):
    return ("{verdict: {session_date: %s, evaluated_at: %s, ready: false, reason: %s,"
            " blockers: %s, warnings: %s}, today: '2026-09-29', is_today: %s, days_ago: %s}"
            % (session_date, evaluated_at, reason, blockers, warnings, is_today, days_ago))


def _view(resp, now="{}"):
    return _run_js(f"return M.preopenView({resp}, {now});")


# --------------------------------------------------------------------------- #
# when there is nothing to show
# --------------------------------------------------------------------------- #

def test_no_response_or_no_verdict_shows_nothing():
    for resp in ("null", "undefined", "{}", "{verdict: null, is_today: null}", "'x'"):
        assert _view(resp) is None


def test_a_clean_verdict_is_not_a_banner():
    assert _view(_resp(reason="'ready'")) is None


def test_a_holiday_is_never_a_banner():
    assert _view(_resp(blockers=f"[{FT_EXPIRED}]", reason="'not_a_trading_day'")) is None


# --------------------------------------------------------------------------- #
# today's verdict
# --------------------------------------------------------------------------- #

def test_todays_blocker_is_a_danger_banner_with_the_time_in_IST():
    v = _view(_resp(blockers=f"[{FT_EXPIRED}]"))
    assert v["tone"] == "danger" and v["isToday"] is True
    assert v["title"] == "Pre-open check (08:45 IST today): NOT READY"
    assert v["items"] == [{"id": "flattrade_token_expired", "kind": "blocker",
                           "label": "Flattrade session expired",
                           "detail": "The Flattrade session has expired."}]
    assert "not a live re-check" in v["sub"]


def test_todays_warnings_only_are_amber_not_danger():
    v = _view(_resp(warnings=f"[{WAREHOUSE}]", reason="'ready'"))
    assert v["tone"] == "warn" and v["title"].endswith("warnings")
    assert v["items"][0]["kind"] == "warning"


def test_blockers_are_listed_before_warnings():
    v = _view(_resp(blockers=f"[{FT_EXPIRED}]", warnings=f"[{WAREHOUSE}]"))
    assert [i["kind"] for i in v["items"]] == ["blocker", "warning"]


def test_an_unknown_blocker_id_is_shown_verbatim_not_hidden():
    v = _view(_resp(blockers="[{id: 'some_new_check', detail: 'hm'}]"))
    assert v["items"][0]["label"] == "some_new_check"


# --------------------------------------------------------------------------- #
# resolved since the morning check
# --------------------------------------------------------------------------- #

def test_a_flattrade_blocker_is_dropped_once_the_broker_chip_says_connected():
    """08:45 said expired; the operator logged in at 09:00. It must not linger."""
    assert _view(_resp(blockers=f"[{FT_EXPIRED}]"), "{flattradeConnected: true}") is None


def test_an_unknown_or_still_disconnected_broker_keeps_the_blocker():
    for now in ("{flattradeConnected: false}", "{flattradeConnected: null}", "{}",
                "{flattradeConnected: undefined}", "{upstoxConnected: true}"):
        v = _view(_resp(blockers=f"[{FT_EXPIRED}]"), now)
        assert v is not None and v["items"][0]["id"] == "flattrade_token_expired", now


def test_each_broker_only_resolves_its_own_blockers():
    v = _view(_resp(blockers=f"[{FT_EXPIRED}, {UP_MISSING}]"), "{flattradeConnected: true}")
    assert [i["id"] for i in v["items"]] == ["upstox_not_connected"]
    assert v["resolved"] == ["flattrade_token_expired"]
    assert "Since resolved: Flattrade session expired" in v["sub"]
    both = _view(_resp(blockers=f"[{FT_EXPIRED}, {UP_MISSING}]"),
                 "{flattradeConnected: true, upstoxConnected: true}")
    assert both is None


def test_a_resolved_blocker_leaves_the_warnings_showing_and_downgrades_the_tone():
    v = _view(_resp(blockers=f"[{FT_EXPIRED}]", warnings=f"[{WAREHOUSE}]"),
              "{flattradeConnected: true}")
    assert v["tone"] == "warn" and [i["id"] for i in v["items"]] == ["warehouse_actions_pending"]


def test_a_warehouse_warning_is_never_marked_resolved_by_a_broker():
    v = _view(_resp(warnings=f"[{WAREHOUSE}]"),
              "{flattradeConnected: true, upstoxConnected: true}")
    assert v is not None and v["items"][0]["id"] == "warehouse_actions_pending"


# --------------------------------------------------------------------------- #
# a previous day's verdict is never current
# --------------------------------------------------------------------------- #

def test_a_previous_days_verdict_is_muted_dated_and_labelled_not_today():
    v = _view(_resp(blockers=f"[{FT_EXPIRED}]", is_today="false", days_ago=4,
                    session_date="'2026-09-25'",
                    evaluated_at="'2026-09-25T03:15:00+00:00'"))
    assert v["tone"] == "muted" and v["isToday"] is False
    assert v["title"] == "Previous pre-open check — Fri 25 Sep (4 days ago), NOT today's"
    assert "No pre-open check has been recorded for today" in v["sub"]
    assert "not the current state" in v["sub"]


def test_yesterday_is_singular():
    v = _view(_resp(blockers=f"[{FT_EXPIRED}]", is_today="false", days_ago=1,
                    session_date="'2026-09-28'"))
    assert "(1 day ago)" in v["title"]


def test_a_previous_days_blocker_is_not_dropped_by_todays_broker_state():
    """Connected NOW says nothing about what was wrong on Friday: it stays (muted,
    dated) rather than silently vanishing or being 'resolved' by today's chip."""
    v = _view(_resp(blockers=f"[{FT_EXPIRED}]", is_today="false", days_ago=4,
                    session_date="'2026-09-25'"),
              "{flattradeConnected: true, upstoxConnected: true}")
    assert v is not None and v["tone"] == "muted" and v["resolved"] == []


def test_an_unknown_is_today_is_never_treated_as_today():
    for flag in ("null", "undefined", "false"):
        v = _view(_resp(blockers=f"[{FT_EXPIRED}]", is_today=flag, days_ago="null",
                        session_date="'bad'"))
        assert v["tone"] == "muted" and v["isToday"] is False
        assert "an earlier day" in v["title"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def test_date_and_time_formatting_is_IST_arithmetic():
    out = _run_js("""
      return {
        d: [M.fmtSessionDate("2026-09-25"), M.fmtSessionDate("2026-01-01"),
            M.fmtSessionDate("nope"), M.fmtSessionDate(null), M.fmtSessionDate("2026-13-40")],
        t: [M.fmtIstTime("2026-09-29T03:15:00+00:00"),
            M.fmtIstTime("2026-09-28T22:00:00+00:00"),
            M.fmtIstTime("garbage"), M.fmtIstTime(null)],
      };
    """)
    assert out["d"][:2] == ["Fri 25 Sep", "Thu 1 Jan"]
    # bad, null, and an impossible date that Date.UTC would silently roll over
    assert out["d"][2:] == [None, None, None]
    assert out["t"] == ["08:45", "03:30", None, None]


def test_upstox_connected_reads_the_feed_health_token_and_unknown_stays_null():
    out = _run_js("""
      return [M.upstoxConnectedFrom({token: {connected: true, expired: false}}),
              M.upstoxConnectedFrom({token: {connected: true, expired: true}}),
              M.upstoxConnectedFrom({token: {connected: false}}),
              M.upstoxConnectedFrom({token: null}),
              M.upstoxConnectedFrom(null), M.upstoxConnectedFrom(undefined),
              M.upstoxConnectedFrom({})];
    """)
    assert out == [True, False, False, None, None, None, None]


def test_tone_classes():
    out = _run_js("""
      return [M.preopenToneClass("danger"), M.preopenToneClass("warn"),
              M.preopenToneClass("muted")];
    """)
    assert "text-danger" in out[0] and "text-warning" in out[1] and "text-dim" in out[2]
    assert "border-danger" not in out[2] and "bg-danger" not in out[2]
