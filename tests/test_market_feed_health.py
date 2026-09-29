"""Behaviour of frontend/src/lib/marketFeedHealth.js, EXECUTED through node.

The global market header painted a green "LIVE TICKS · Upstox WebSocket" whenever the
stream TASK existed — which it does through every backoff-and-retry after the socket
drops. The verdict now comes from the stream's own health facts. Grepping the JSX
cannot prove that; this runs the decision.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "marketFeedHealth.js")

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


def _derive(stream, snapshot="{source_mode: 'live_ticks'}", loading="false", error="''"):
    return _run_js(f"""
      return M.deriveFeedIndicator({{loading: {loading}, error: {error},
        snapshot: {snapshot}, streamStatus: {stream}, quotesText: "6/8 quotes"}});
    """)


HEALTHY = "{running: true, connected: true, last_tick_age_s: 2.1, last_error: null, reconnect_count: 0}"


def test_a_connected_socket_with_a_fresh_tick_is_the_only_green():
    out = _derive(HEALTHY)
    assert out["text"] == "live ticks" and out["tone"] == "ok" and out["live"] is True
    assert out["detail"] == "Upstox WebSocket"


def test_a_task_that_exists_but_is_retrying_is_amber_not_live_ticks():
    """THE defect. running=true, connected=false: the loop is in backoff. The old
    `snapshot.source_mode === live_ticks || stream.running` read green."""
    out = _derive("{running: true, connected: false, last_tick_age_s: 400, "
                  "last_error: 'HTTP 401 on reauth', reconnect_count: 3}")
    assert out["text"] == "reconnecting" and out["tone"] == "warn" and out["live"] is False
    assert "401 on reauth" in out["detail"]
    assert "3 reconnects" in out["title"]


def test_a_retrying_stream_is_amber_even_when_the_snapshot_still_says_live_ticks():
    """The snapshot's source_mode is a 120-second freshness window: it can still read
    live_ticks for two minutes after the socket died. The stream status wins."""
    out = _derive("{running: true, connected: false, last_error: 'boom', reconnect_count: 1}",
                  snapshot="{source_mode: 'live_ticks'}")
    assert out["live"] is False and out["tone"] == "warn"


def test_the_first_connection_attempt_is_not_an_error():
    out = _derive("{running: true, connected: false, last_error: null, reconnect_count: 0}")
    assert out["text"] == "connecting" and out["tone"] == "neutral" and out["live"] is False


def test_an_open_socket_that_has_gone_silent_is_stale_amber():
    out = _derive("{running: true, connected: true, last_tick_age_s: 95, reconnect_count: 0}")
    assert out["text"] == "stale 1m 35s" and out["tone"] == "warn" and out["live"] is False
    assert "last tick 1m 35s ago" in out["detail"]


def test_the_staleness_threshold_is_thirty_seconds_inclusive():
    fresh = _derive("{running: true, connected: true, last_tick_age_s: 30}")
    stale = _derive("{running: true, connected: true, last_tick_age_s: 30.1}")
    assert fresh["live"] is True
    assert stale["live"] is False and stale["tone"] == "warn"


def test_an_open_socket_that_never_delivered_a_tick_is_not_live():
    """last_tick_age_s is None for "no tick yet". null must never coerce to 0 == fresh."""
    for age in ("null", "undefined"):
        out = _derive("{running: true, connected: true, last_tick_age_s: %s}" % age)
        assert out["text"] == "no ticks" and out["tone"] == "warn" and out["live"] is False


def test_a_status_that_does_not_say_connected_cannot_produce_live_ticks():
    """An old backend / a shape change: `connected` absent. Unknown is never green."""
    out = _derive("{running: true, last_tick_age_s: 1}")
    assert out["text"] == "unverified" and out["tone"] == "warn" and out["live"] is False


def test_fresh_ticks_that_are_not_the_headers_instruments_do_not_claim_live_quotes():
    out = _derive(HEALTHY, snapshot="{source_mode: 'api_fallback'}")
    assert out["live"] is False and out["tone"] == "neutral"
    assert out["text"] == "6/8 quotes" and "from API" in out["detail"]


def test_no_stream_is_the_neutral_api_fallback_not_green():
    out = _derive("{running: false, connected: false}", snapshot="{source_mode: 'api_fallback'}")
    assert out["text"] == "6/8 quotes" and out["detail"] == "API fallback"
    assert out["tone"] == "neutral" and out["live"] is False


def test_a_stopped_stream_is_not_live_even_if_a_recent_tick_still_feeds_the_snapshot():
    out = _derive("{running: false, connected: false, last_tick_age_s: 10}",
                  snapshot="{source_mode: 'live_ticks'}")
    assert out["live"] is False and out["tone"] == "neutral"


def test_an_unreadable_stream_status_is_unknown_never_live():
    for stream in ("null", "undefined"):
        out = _derive(stream, snapshot="{source_mode: 'live_ticks'}")
        assert out["live"] is False and out["tone"] == "neutral"
        assert out["detail"] == "stream status unavailable"


def test_the_market_header_api_failing_is_offline_red_and_beats_everything():
    out = _derive(HEALTHY, error="'Network Error'")
    assert out["text"] == "offline" and out["tone"] == "bad" and out["live"] is False
    assert out["title"] == "Network Error"


def test_loading_is_neutral():
    out = _derive(HEALTHY, loading="true")
    assert out["text"] == "loading" and out["tone"] == "neutral" and out["live"] is False


def test_tone_classes_and_age_formatting():
    out = _run_js("""
      return {ok: M.feedToneClass("ok"), warn: M.feedToneClass("warn"),
              bad: M.feedToneClass("bad"), neutral: M.feedToneClass("neutral"),
              odd: M.feedToneClass("???"),
              ages: [M.fmtAge(5), M.fmtAge(65), M.fmtAge(3725), M.fmtAge(-1),
                     M.fmtAge(NaN), M.fmtAge(null)],
              thr: M.TICK_STALE_S};
    """)
    assert out["ok"] == "text-emerald-400" and out["warn"] == "text-amber-400"
    assert out["bad"] == "text-red-400" and out["neutral"] == "text-dimmer"
    assert out["odd"] == "text-dimmer"
    assert out["ages"][:3] == ["5s", "1m 05s", "1h 02m"]
    # fmtAge(null): Number(null) is 0 -> "0s" would read as fresh; must be unknown
    assert out["ages"][3:] == ["—", "—", "—"]
    assert out["thr"] == 30
