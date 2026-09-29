"""Persisted reasons must carry their date, EXECUTED through node.

`kill_switch_reason` / `drift_reason` are stored on the deployment and are NOT cleared
by a manual pause or resume; a live latch never self-clears. A bare "auto-paused:
max_consecutive_losses" or "halted because of broker_stop_loss" reads as this
morning's whatever its age. Covers lib/istWhen.js, deploymentState.pauseReasonView and
liveStopState.stopWhen.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")

# "now" = Tue 2026-09-29 15:00 IST (09:30Z)
NOW = "Date.UTC(2026, 8, 29, 9, 30)"


def _run_js(module: str, body: str):
    url = "file:///" + os.path.abspath(os.path.join(_LIB, module)).replace("\\", "/")
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


# --------------------------------------------------------------------------- #
# istWhen
# --------------------------------------------------------------------------- #

def test_a_time_today_says_today_and_an_earlier_day_carries_its_date():
    out = _run_js("istWhen.js", f"""
      const now = {NOW};
      return [M.formatIstWhen("2026-09-29T08:42:00+00:00", now),
              M.formatIstWhen("2026-09-25T08:42:00+00:00", now),
              M.formatIstWhen("2025-12-31T18:30:00+00:00", now)];
    """)
    assert out[0]["label"] == "today 14:12 IST" and out[0]["today"] is True
    assert out[1]["label"] == "Fri 25 Sep 14:12 IST" and out[1]["today"] is False
    # 2025-12-31T18:30Z is 00:00 IST on 1 Jan 2026 — the IST year, not the UTC year, so
    # it is THIS year and the year is not spelled out.
    assert out[2]["label"] == "Thu 1 Jan 00:00 IST"
    # a different year spells the year out
    far =_run_js("istWhen.js", f"return M.formatIstWhen('2024-03-05T04:30:00+00:00', {NOW});")
    assert far["label"] == "Tue 5 Mar 2024 10:00 IST"


def test_the_IST_day_not_the_UTC_day_decides_today():
    """20:00Z on the 28th is 01:30 IST on the 29th: that IS today."""
    out = _run_js("istWhen.js", f"return M.formatIstWhen('2026-09-28T20:00:00+00:00', {NOW});")
    assert out["today"] is True and out["label"] == "today 01:30 IST"


def test_unusable_values_are_null_never_a_made_up_time():
    out = _run_js("istWhen.js", f"""
      return [null, undefined, "", "junk", 0, -5, NaN, true, "0000-00-00"].map(
        (v) => M.formatIstWhen(v, {NOW}));
    """)
    assert out == [None] * 9


def test_to_epoch_ms_reads_iso_naive_and_numeric_forms():
    out = _run_js("istWhen.js", """
      return [M.toEpochMs("2026-09-29T09:30:00Z"),
              M.toEpochMs("2026-09-29T15:00:00+05:30"),
              M.toEpochMs("2026-09-29T09:30:00"),          // naive = UTC
              M.toEpochMs("1790000000000"), M.toEpochMs(1790000000000),
              M.toEpochMs("x")];
    """)
    utc = 1790674200000
    assert out[0] == utc and out[1] == utc and out[2] == utc
    assert out[3] == 1790000000000 and out[4] == 1790000000000 and out[5] is None


# --------------------------------------------------------------------------- #
# pauseReasonView
# --------------------------------------------------------------------------- #

def _pause(dep: str, now: str = NOW):
    return _run_js("deploymentState.js", f"return M.pauseReasonView({dep}, {now});")


def test_a_kill_switch_reason_carries_the_date_it_was_recorded():
    v = _pause("{kill_switch_reason: 'max_consecutive_losses', "
               "kill_switch_paused_at: '2026-09-25T08:42:00+00:00'}")
    assert v["kind"] == "kill_switch" and v["reason"] == "max_consecutive_losses"
    assert v["whenLabel"] == "Fri 25 Sep 14:12 IST" and v["dated"] is True
    assert v["text"] == "auto-paused (Fri 25 Sep 14:12 IST): max_consecutive_losses"
    assert v["stale"] is True and "paused by hand since" in v["title"]


def test_a_reason_from_today_is_not_stale():
    v = _pause("{kill_switch_reason: 'daily_loss_cutoff_pct', "
               "kill_switch_paused_at: '2026-09-29T08:42:00+00:00'}")
    assert v["stale"] is False and v["whenLabel"] == "today 14:12 IST"
    assert "paused by hand" not in v["title"]


def test_a_reason_with_no_recorded_date_says_so_and_is_stale():
    """Unknown is never made to look current."""
    v = _pause("{kill_switch_reason: 'max_consecutive_losses'}")
    assert v["dated"] is False and v["whenLabel"] == "date not recorded" and v["stale"] is True
    assert v["text"] == "auto-paused (date not recorded): max_consecutive_losses"


def test_the_newest_reason_wins_when_both_are_stored():
    """A fortnight-old kill-switch reason must not mask today's drift pause."""
    v = _pause("{kill_switch_reason: 'max_consecutive_losses', "
               "kill_switch_paused_at: '2026-09-12T08:42:00+00:00', "
               "drift_reason: 'strategy_source_drift', "
               "drift_detected_at: '2026-09-29T08:42:00+00:00'}")
    assert v["kind"] == "drift" and v["reason"] == "strategy_source_drift" and v["stale"] is False


def test_and_the_kill_switch_wins_when_it_is_the_newer_one():
    v = _pause("{kill_switch_reason: 'max_consecutive_losses', "
               "kill_switch_paused_at: '2026-09-29T08:42:00+00:00', "
               "drift_reason: 'strategy_source_drift', "
               "drift_detected_at: '2026-09-12T08:42:00+00:00'}")
    assert v["kind"] == "kill_switch"


def test_an_undated_reason_never_outranks_a_dated_one():
    v = _pause("{kill_switch_reason: 'undated', drift_reason: 'strategy_source_drift', "
               "drift_detected_at: '2026-09-01T08:42:00+00:00'}")
    assert v["kind"] == "drift"


def test_two_undated_reasons_keep_the_existing_kill_switch_precedence():
    v = _pause("{kill_switch_reason: 'k', drift_reason: 'd'}")
    assert v["kind"] == "kill_switch"


def test_no_reason_is_null():
    for dep in ("{}", "null", "undefined", "{kill_switch_reason: '', drift_reason: null}"):
        assert _pause(dep) is None


def test_the_paused_dots_tooltip_carries_the_reasons_date():
    out = _run_js("deploymentLiveness.js", """
      return [M.deploymentLiveness({status: 'PAUSED', kill_switch_reason: 'max_consecutive_losses',
                 kill_switch_paused_at: '2026-09-25T08:42:00+00:00'}, null).tooltip,
              M.deploymentLiveness({status: 'PAUSED'}, null).tooltip,
              M.deploymentLiveness({status: 'PAUSED', paused_reason: 'manual'}, null).tooltip,
              M.deploymentLiveness({status: 'ACTIVE'}, {state: 'LIVE', reason: 'ok'}).label];
    """)
    assert "max_consecutive_losses — recorded Fri 25 Sep" in out[0] and "14:12 IST" in out[0]
    assert out[1] == "Paused" and out[2] == "manual" and out[3] == "ACTIVE · LIVE"


def test_pause_reason_of_is_unchanged():
    out = _run_js("deploymentState.js", """
      return [M.pauseReasonOf({kill_switch_reason: 'k', drift_reason: 'd'}),
              M.pauseReasonOf({drift_reason: 'd'}), M.pauseReasonOf({})];
    """)
    assert out == ["k", "d", None]


# --------------------------------------------------------------------------- #
# stopWhen (the safety-latch banner's provenance line)
# --------------------------------------------------------------------------- #

def _stop(at: str, now: str = NOW):
    return _run_js("liveStopState.js", f"return M.stopWhen({at}, {now});")


def test_a_recorded_trip_time_is_dated_and_aged():
    out = _stop("'2026-09-25T08:42:00+00:00'")
    assert out == {"known": True, "text": "Fri 25 Sep 14:12 IST · 4 d ago"}


def test_ages_scale_from_minutes_to_days():
    assert _stop("'2026-09-29T09:29:40+00:00'")["text"].endswith("just now")
    assert _stop("'2026-09-29T09:00:00+00:00'")["text"].endswith("30 min ago")
    assert _stop("'2026-09-29T04:30:00+00:00'")["text"].endswith("5 h ago")


def test_a_latch_with_no_recorded_time_says_so_rather_than_saying_nothing():
    """An absent line reads as 'nothing to note'; a latch with no timestamp may be
    from an earlier session, and the banner must say it does not know."""
    for at in ("null", "undefined", "''"):
        out = _stop(at)
        assert out["known"] is False
        assert out["text"] == "trip time not recorded — this stop may date from an earlier session"


def test_an_unparseable_trip_time_is_reported_not_swallowed():
    out = _stop("'not-a-time'")
    assert out["known"] is False and "unreadable (not-a-time)" in out["text"]


def test_read_stop_state_is_unchanged_by_the_new_import():
    out = _run_js("liveStopState.js", """
      return [M.readStopState({blocked_until_reset: true, latched_reason: "broker_stop_loss",
                               latched_at: "2026-09-02T04:00:00Z"}),
              M.readStopState({}).stopped];
    """)
    assert out[0]["stopped"] is True and out[0]["reason"] == "broker_stop_loss"
    assert out[0]["at"] == "2026-09-02T04:00:00Z" and out[1] is False
