"""Behaviour of frontend/src/lib/liveDeploymentView.js, EXECUTED through node.

Every decision the Live Deployments pane shows lives in that module; the JSX only
renders it. Grepping JSX cannot prove any of this (the project's own rule, and the
lesson of two tautological tests before it).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "liveDeploymentView.js")

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


def _gov(**kw):
    g = {"caps": {"lots": 2, "max_lots_per_day": 6, "max_concurrent": 2,
                  "daily_loss_cap": 3000},
         "consumed": {"lots_today": 2, "concurrent_now": 1, "realized_today": 0,
                      "open_unrealized": 0, "day_pnl": 0, "exposure_unknown": False},
         "binding": None, "error": None}
    g.update(kw)
    return g


def _status(gov=None, **kw):
    s = {"governor": gov if gov is not None else _gov()}
    s.update(kw)
    return s


# --------------------------------------------------------------------------- #
# Day Stop: worst deployment, never pooled
# --------------------------------------------------------------------------- #

def test_a_95_percent_and_a_0_percent_deployment_read_as_95_not_48():
    """THE handoff case. Caps are enforced per deployment; pooling one at 95% of its
    cap with one at 0% gave 47.5% and a calm card."""
    out = _run_js("""
      const deps = [{id: "a", mode: "live", name: "A"}, {id: "b", mode: "live", name: "B"}];
      const g = (pnl) => ({governor: {caps: {daily_loss_cap: 1000}, binding: null,
                           consumed: {day_pnl: pnl, exposure_unknown: false}}});
      return M.worstDayStop(deps, {a: g(-950), b: g(0)});
    """)
    assert out["worst"]["id"] == "a"
    assert abs(out["worst"]["ratio"] - 0.95) < 1e-9
    assert out["tone"] == "danger"


def test_the_day_stop_counts_an_open_loser():
    """Realized 0, open −₹3,200: a realized-only card read "₹0 used"."""
    out = _run_js("""
      return M.worstDayStop([{id: "a", mode: "live"}],
        {a: {governor: {caps: {daily_loss_cap: 3000}, binding: null,
             consumed: {realized_today: 0, open_unrealized: -3200, day_pnl: -3200,
                        exposure_unknown: false}}}});
    """)
    assert out["worst"]["used"] == 3200 and out["tone"] == "danger"


def test_an_unknown_exposure_is_a_warning_not_a_zero():
    out = _run_js("""
      return M.worstDayStop([{id: "a", mode: "live"}],
        {a: {governor: {caps: {daily_loss_cap: 3000}, binding: null,
             consumed: {day_pnl: null, exposure_unknown: true}}}});
    """)
    assert out["worst"] is None and out["anyUnknown"] is True
    assert out["rows"][0]["used"] is None and out["tone"] == "warn"


def test_no_live_deployment_and_no_cap_are_distinguished():
    out = _run_js("""
      return [M.worstDayStop([{id: "a", mode: "paper"}], {}),
              M.worstDayStop([{id: "a", mode: "live"}],
                {a: {governor: {caps: {}, consumed: {day_pnl: 0}, binding: null}}})];
    """)
    assert out[0]["any"] is False
    assert out[1]["any"] is True and out[1]["anyCap"] is False


# --------------------------------------------------------------------------- #
# Headroom
# --------------------------------------------------------------------------- #

def test_headroom_uses_the_governors_loss_not_realized_only():
    out = _run_js("""
      return M.capHeadroom(M.readGovernor({governor: {
        caps: {max_lots_per_day: 6, max_concurrent: 2, daily_loss_cap: 3000},
        consumed: {lots_today: 4, concurrent_now: 1, realized_today: 0,
                   day_pnl: -1500, exposure_unknown: false}, binding: null}}));
    """)
    loss = [r for r in out if r["key"] == "loss"][0]
    assert loss["used"] == 1500 and loss["max"] == 3000 and loss["ratio"] == 0.5


def test_an_unset_cap_is_not_a_limit():
    out = _run_js("""
      return M.capHeadroom(M.readGovernor({governor: {
        caps: {max_concurrent: 2}, consumed: {lots_today: 3, concurrent_now: 0,
        day_pnl: 0, exposure_unknown: false}, binding: null}}));
    """)
    lots = [r for r in out if r["key"] == "lots"][0]
    assert lots["configured"] is False and lots["max"] is None and lots["ratio"] is None
    assert lots["used"] == 3


def test_unknown_loss_is_null_never_zero():
    out = _run_js("""
      return M.capHeadroom(M.readGovernor({governor: {
        caps: {daily_loss_cap: 3000}, consumed: {day_pnl: null, exposure_unknown: true},
        binding: null}}));
    """)
    loss = [r for r in out if r["key"] == "loss"][0]
    assert loss["unknown"] is True and loss["used"] is None and loss["ratio"] is None


# --------------------------------------------------------------------------- #
# Binding constraint
# --------------------------------------------------------------------------- #

def test_a_missing_governor_never_reads_as_can_trade():
    out = _run_js("return [M.bindingView(M.readGovernor({})), "
                  "M.bindingView(M.readGovernor({governor: {error: 'describe_failed:X'}}))];")
    assert all(v["canTrade"] is False for v in out)


def test_nothing_binding_is_can_trade():
    out = _run_js("return M.bindingView(M.readGovernor({governor: {binding: null}}));")
    assert out["canTrade"] is True


@pytest.mark.parametrize("reason,fragment", [
    ("not_connected", "not connected"),
    ("after_entry_cutoff", "15:00"),
    ("account_exposure_unavailable:mongo down", "unreadable"),
    ("max_concurrent", "concurrent"),
    ("daily_loss_cap", "loss cap"),
    ("some_new_reason", "some new reason"),
])
def test_binding_reasons_read_as_words(reason, fragment):
    out = _run_js(f"return M.bindingView(M.readGovernor({{governor: {{binding: "
                  f"{{layer: 'deployment', reason: {json.dumps(reason)}, pause: false}}}}}}));")
    assert out["canTrade"] is False and fragment in out["text"]


def test_a_pausing_breach_is_danger():
    out = _run_js("return M.bindingView(M.readGovernor({governor: {binding: "
                  "{layer: 'deployment', reason: 'daily_loss_cap', pause: true}}}));")
    assert out["tone"] == "danger"


# --------------------------------------------------------------------------- #
# Row order
# --------------------------------------------------------------------------- #

def test_rows_sort_open_then_held_then_idle_with_not_live_apart():
    out = _run_js("""
      const deps = [
        {id: "idle", mode: "live"}, {id: "paper", mode: "paper"},
        {id: "held", mode: "live"}, {id: "open", mode: "live"},
        {id: "stale-open", mode: "live"},
      ];
      const st = {
        idle: {governor: {consumed: {concurrent_now: 0}}},
        held: {live_paused: true, governor: {consumed: {concurrent_now: 0}}},
        open: {open_positions: [{tsym: "X"}], governor: {consumed: {concurrent_now: 1}}},
        "stale-open": {open_positions: [], governor: {consumed: {concurrent_now: 1}}},
      };
      const r = M.sortDeploymentRows(deps, st);
      return {live: r.live.map(d => d.id), notLive: r.notLive.map(d => d.id)};
    """)
    assert out["live"] == ["open", "stale-open", "held", "idle"]
    assert out["notLive"] == ["paper"]


# --------------------------------------------------------------------------- #
# Open positions, intended entry, refusal chip, guard health
# --------------------------------------------------------------------------- #

def test_open_positions_show_room_to_the_stop():
    out = _run_js("""
      return M.openPositionRows(
        [{tsym: "X", qty: 60, entry_price: 367.22, stop_level: 183.61,
          target_level: null, seen_filled: true}],
        [{tsym: "X", lp: "300.00", mark_source: "tick"}]);
    """)[0]
    assert out["ltp"] == 300 and abs(out["distToStopPts"] - 116.39) < 1e-9
    assert out["markStale"] is False and out["target"] is None


def test_an_unmarked_position_is_flagged_not_priced():
    out = _run_js("return M.openPositionRows([{tsym: 'X', stop_level: 100}], []);")[0]
    assert out["ltp"] is None and out["distToStopPts"] is None and out["markStale"] is True


def test_both_intended_shapes_read_as_one_line():
    out = _run_js("""
      return [M.describeIntended({would_send: false, ref_ltp: 120.5, lots: 3}),
              M.describeIntended({ref_premium: 98.1, premium_at_entry: 101.0}),
              M.describeIntended(null)];
    """)
    assert "3 lots" in out[0] and "dry-run" in out[0]
    assert "ref ₹98.10" in out[1] and out[2] is None


def test_a_refusal_from_a_previous_session_is_marked_stale():
    out = _run_js("""
      const now = Date.parse("2026-09-29T05:00:00Z");
      return [M.entryRefusalView({error: "x", at: "2026-09-29T04:30:00Z"}, now),
              M.entryRefusalView({error: "x", at: "2026-09-16T06:49:03Z"}, now),
              M.entryRefusalView({error: "x"}, now),
              M.entryRefusalView({error: null}, now)];
    """)
    assert out[0]["stale"] is False
    assert out[1]["stale"] is True and out[1]["dateLabel"] == "2026-09-16"
    assert out[2]["stale"] is True
    assert out[3] is None


def test_the_ist_day_boundary_decides_staleness():
    """20:00Z on the 28th is 01:30 IST on the 29th — the same IST day as now."""
    out = _run_js("""
      return M.entryRefusalView({error: "x", at: "2026-09-28T20:00:00Z"},
                                Date.parse("2026-09-29T05:00:00Z"));
    """)
    assert out["stale"] is False


@pytest.mark.parametrize("state,tone", [
    ("watching", "success"), ("idle", "default"), ("blind", "danger"),
    ("stalled", "danger"), ("not_running", "danger"),
])
def test_guard_health_tones(state, tone):
    out = _run_js(f"return M.guardHealthView({{health: {{state: {json.dumps(state)}, "
                  f"label: 'L', reason: 'r'}}}});")
    assert out["tone"] == tone


def test_a_guard_without_health_is_unknown_never_armed():
    """The old indicator read ARMED from a constant; a missing health must not."""
    out = _run_js("return M.guardHealthView({armed: true});")
    assert out["label"] == "UNKNOWN" and out["tone"] == "warn"


# --------------------------------------------------------------------------- #
# Execution strip legs
# --------------------------------------------------------------------------- #

def test_an_unusable_session_is_blocked_not_dry_run():
    """"dry-run" is a gate choosing not to send. With no usable broker session
    nothing CAN be sent — including the guard's exits — and it must say so."""
    out = _run_js("""
      return M.executionLegs({connected: false, session_expired: true,
                              would_transmit_entry: false, would_transmit_exit: false});
    """)
    assert out["entries"]["text"].startswith("BLOCKED")
    assert out["squares"]["text"].startswith("BLOCKED")
    assert out["sessionExpired"] is True


def test_a_connected_session_keeps_transmit_and_dry_run():
    out = _run_js("""
      return [M.executionLegs({connected: true, would_transmit_entry: true,
                               would_transmit_exit: true}),
              M.executionLegs({connected: true, would_transmit_entry: false,
                               would_transmit_exit: true}),
              M.executionLegs({would_transmit_entry: false})];
    """)
    assert out[0]["entries"]["text"] == "TRANSMIT"
    assert out[1]["entries"]["text"] == "dry-run" and out[1]["squares"]["text"] == "TRANSMIT"
    # an older payload without `connected` or the exit field keeps the old reading
    assert out[2]["squares"]["text"] == "TRANSMIT"


# --------------------------------------------------------------------------- #
# Exit reports — never "flattened"
# --------------------------------------------------------------------------- #

def test_a_submitted_exit_is_not_called_flat():
    out = _run_js("return M.summarizeExitReport({exit_submitted_tsyms: ['X'], "
                  "flat_confirmation_pending_tsyms: ['X']});")
    assert out["ok"] is True and "awaiting fill confirmation" in out["message"]
    assert "flattened" not in out["message"].lower()


@pytest.mark.parametrize("key", ["failed_tsyms", "deferred_tsyms", "skipped_shared_tsyms",
                                 "unguarded_open_tsyms"])
def test_any_problem_makes_the_report_not_ok(key):
    out = _run_js(f"return M.summarizeExitReport({{exit_submitted_tsyms: ['A'], {key}: ['B']}});")
    assert out["ok"] is False and out["tone"] == "danger" and "B" in out["message"]


def test_an_empty_report_says_nothing_was_open():
    out = _run_js("return [M.summarizeExitReport({}), M.summarizeExitReport(null)];")
    assert out[0]["ok"] is True and "nothing" in out[0]["message"]
    assert out[1]["ok"] is False
