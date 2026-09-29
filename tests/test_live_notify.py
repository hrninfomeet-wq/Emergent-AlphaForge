"""Behaviour of frontend/src/lib/liveNotify.js, EXECUTED through node.

The opt-in live alerts decide, from two consecutive snapshots of the live book,
whether a fill / exit / refusal / block / halt just happened. That decision lives in
this module (the hook only toasts); grepping JSX cannot prove any of it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib", "liveNotify.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")

# Shared JS helpers, prepended to every script: build a deployments roster + status
# map, snapshot it, and diff two of them.
_PRELUDE = """
const dep = (id, extra = {}) => ({id, name: id.toUpperCase(), mode: "live", ...extra});
const st = (o = {}) => ({
  open_positions: (o.open || []).map((x) => (typeof x === "string" ? {id: x, tsym: "T-" + x} : x)),
  today: {orders: o.orders ?? 0},
  last_entry: o.lastEntry ?? null,
  governor: o.noGov ? undefined : {
    error: o.govError ?? null,
    binding: o.binding ? {layer: "deployment", reason: o.binding} : null,
    account: {stops: {latched: !!o.latched, engine_halted: !!o.halted,
                      engine_halt_reason: o.haltReason ?? null}},
  },
});
const snap = (statuses, deps) => M.buildLiveSnapshot(
  statuses, deps || Object.keys(statuses).map((id) => dep(id)));
const diff = (a, b) => M.diffLiveEvents(a, b);
const kinds = (evs) => evs.map((e) => e.kind);
"""


def _run_js(body: str):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    source = (
        f"import * as M from {url!r};\n{_PRELUDE}\n"
        f"const out = await (async () => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out === undefined ? null : out));\n"
    )
    proc = subprocess.run(["node", "--input-type=module", "-e", source],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------- #
# Silence: the first look, and an unchanged look
# --------------------------------------------------------------------------- #

def test_the_first_snapshot_never_emits_anything():
    """Opening the page on a book that already holds positions, a refusal, a block and
    a latched account is not news."""
    out = _run_js("""
      const s = snap({a: st({open: ["p1", "p2"], orders: 3, binding: "max_concurrent",
                             lastEntry: {signal_id: "s1", error: "cap"}, latched: true})});
      return [diff(null, s), diff(s, null), diff(null, null)];
    """)
    assert out == [[], [], []]


def test_an_unchanged_snapshot_emits_nothing_however_much_is_going_on():
    out = _run_js("""
      const mk = () => snap({a: st({open: ["p1"], orders: 2, binding: "max_concurrent",
                                    lastEntry: {signal_id: "s1", error: "cap"}, latched: true,
                                    halted: true})});
      return diff(mk(), mk());
    """)
    assert out == []


def test_an_event_fires_once_not_on_every_poll_after_it():
    out = _run_js("""
      const s0 = snap({a: st({})});
      const s1 = snap({a: st({open: ["p1"]})});
      const s2 = snap({a: st({open: ["p1"]})});
      return [kinds(diff(s0, s1)), kinds(diff(s1, s2))];
    """)
    assert out == [["fill"], []]


# --------------------------------------------------------------------------- #
# fill / exit
# --------------------------------------------------------------------------- #

def test_a_new_open_position_is_a_fill():
    out = _run_js("""
      const evs = diff(snap({a: st({open: ["p1"]})}),
                       snap({a: st({open: ["p1", {id: "p2", tsym: "NIFTY26C24000"}], orders: 2})}));
      return evs;
    """)
    assert [e["kind"] for e in out] == ["fill"]
    assert out[0]["deploymentId"] == "a" and out[0]["name"] == "A"
    assert "NIFTY26C24000" in out[0]["body"] and "2 orders today" in out[0]["body"]
    assert out[0]["title"] == "Live fill — A"


def test_a_vanished_open_position_is_an_exit():
    out = _run_js("""
      return diff(snap({a: st({open: ["p1", {id: "p2", tsym: "NIFTY26C24000"}]})}),
                  snap({a: st({open: ["p1"]})}));
    """)
    assert [e["kind"] for e in out] == ["exit"]
    assert "NIFTY26C24000" in out[0]["body"]
    assert "no longer held" in out[0]["body"].lower()   # never claims a fill it cannot see


def test_one_position_swapped_for_another_is_an_exit_and_a_fill():
    out = _run_js("""
      return kinds(diff(snap({a: st({open: ["p1"]})}), snap({a: st({open: ["p2"]})})));
    """)
    assert sorted(out) == ["exit", "fill"]


def test_each_deployment_is_diffed_on_its_own():
    out = _run_js("""
      const evs = diff(snap({a: st({open: ["p1"]}), b: st({})}),
                       snap({a: st({open: ["p1"]}), b: st({open: ["q1"]})}));
      return evs.map((e) => [e.kind, e.deploymentId]);
    """)
    assert out == [["fill", "b"]]


def test_distinct_events_carry_distinct_keys():
    out = _run_js("""
      const evs = diff(snap({a: st({})}), snap({a: st({open: ["p1", "p2"]})}));
      return evs.map((e) => e.key);
    """)
    assert len(out) == 2 and out[0] != out[1]


# --------------------------------------------------------------------------- #
# refusal
# --------------------------------------------------------------------------- #

def test_a_new_refusal_is_reported_with_its_reason():
    out = _run_js("""
      return diff(snap({a: st({})}),
                  snap({a: st({lastEntry: {signal_id: "s1", error: "live_entry_premium_unavailable_or_stale"}})}));
    """)
    assert [e["kind"] for e in out] == ["refusal"]
    assert out[0]["body"] == "live entry premium unavailable or stale"
    assert out[0]["title"] == "Entry refused — A"


def test_a_refusal_on_a_later_signal_is_reported_again_but_the_same_one_is_not():
    out = _run_js("""
      const e = (id) => st({lastEntry: {signal_id: id, error: "max_concurrent"}});
      return [kinds(diff(snap({a: e("s1")}), snap({a: e("s2")}))),
              kinds(diff(snap({a: e("s1")}), snap({a: e("s1")})))];
    """)
    assert out == [["refusal"], []]


def test_an_error_landing_on_an_already_seen_signal_is_a_refusal():
    """The signal appeared first (intended-only), and the refusal was written later."""
    out = _run_js("""
      return kinds(diff(
        snap({a: st({lastEntry: {signal_id: "s1", error: null, intended: {lots: 1}}})}),
        snap({a: st({lastEntry: {signal_id: "s1", error: "no_option_contract"}})})));
    """)
    assert out == ["refusal"]


def test_a_changed_last_entry_without_an_error_is_not_a_refusal():
    out = _run_js("""
      return diff(
        snap({a: st({})}),
        snap({a: st({lastEntry: {signal_id: "s1", error: null, intended: {lots: 1}}})}));
    """)
    assert out == []


# --------------------------------------------------------------------------- #
# blocked
# --------------------------------------------------------------------------- #

def test_a_newly_binding_reason_is_a_block_named_in_plain_words():
    out = _run_js("""
      return diff(snap({a: st({})}), snap({a: st({binding: "after_entry_cutoff"})}));
    """)
    assert [e["kind"] for e in out] == ["blocked"]
    assert out[0]["body"] == "after the 15:00 IST entry cutoff"


def test_a_changed_binding_reason_reports_but_an_unchanged_or_cleared_one_does_not():
    out = _run_js("""
      const b = (r) => snap({a: st({binding: r})});
      return [kinds(diff(b("max_concurrent"), b("daily_loss_cap"))),
              kinds(diff(b("max_concurrent"), b("max_concurrent"))),
              kinds(diff(b("max_concurrent"), b(null)))];
    """)
    assert out == [["blocked"], [], []]


# --------------------------------------------------------------------------- #
# halt
# --------------------------------------------------------------------------- #

def test_the_latch_or_an_engine_halt_going_true_is_a_halt():
    out = _run_js("""
      const a = (o) => snap({a: st(o)});
      const latch = diff(a({}), a({latched: true}));
      const halt = diff(a({}), a({halted: true, haltReason: "recon_mismatch"}));
      const both = diff(a({}), a({latched: true, halted: true}));
      return [latch, halt, both];
    """)
    latch, halt, both = out
    assert [e["kind"] for e in latch] == ["halt"] and "latch" in latch[0]["body"]
    assert latch[0]["deploymentId"] is None
    assert [e["kind"] for e in halt] == ["halt"] and "engine halted: recon mismatch" in halt[0]["body"]
    assert len(both) == 1 and "latch" in both[0]["body"] and "engine halted" in both[0]["body"]


def test_a_halt_already_in_force_or_clearing_does_not_re_alert():
    out = _run_js("""
      const a = (o) => snap({a: st(o)});
      return [kinds(diff(a({latched: true}), a({latched: true}))),
              kinds(diff(a({latched: true}), a({}))),
              // latch already set, engine halts NOW -> only the new one
              diff(a({latched: true}), a({latched: true, halted: true})).map((e) => e.body),
              kinds(diff(a({halted: true}), a({halted: true}))),
              kinds(diff(a({halted: true}), a({})))];
    """)
    assert out[0] == [] and out[1] == []
    assert len(out[2]) == 1 and "engine halted" in out[2][0] and "latch" not in out[2][0]
    assert out[3] == [] and out[4] == []


def test_an_unknown_account_state_never_reads_as_a_fresh_halt():
    """A status fetch that failed for one poll must not, on recovery, look like the
    account just latched (hours ago)."""
    out = _run_js("""
      const known = snap({a: st({latched: true})});
      const unknown = snap({a: st({noGov: true})});
      const errored = snap({a: st({govError: "describe_failed:X", latched: true})});
      return [kinds(diff(unknown, known)), kinds(diff(known, unknown)),
              kinds(diff(errored, known))];
    """)
    assert out == [[], [], []]


def test_any_live_deployments_governor_can_report_the_account_halt():
    out = _run_js("""
      // whichever deployment's governor carries the stop, in either roster order
      const none = snap({a: st({}), b: st({})});
      return [kinds(diff(none, snap({a: st({}), b: st({latched: true})}))),
              kinds(diff(none, snap({a: st({latched: true}), b: st({})}))),
              kinds(diff(none, snap({a: st({halted: true}), b: st({})})))];
    """)
    assert out == [["halt"], ["halt"], ["halt"]]


# --------------------------------------------------------------------------- #
# Unknown deployments are never diffed
# --------------------------------------------------------------------------- #

def test_a_deployment_missing_from_either_snapshot_is_never_diffed():
    """A status that failed for one poll then came back must not read as 'every
    position just filled'; a deployment newly going live is not a fill either."""
    out = _run_js("""
      const gone = snap({a: st({open: ["p1"]})}, [dep("a"), dep("b")]);   // b has no status
      const back = snap({a: st({open: ["p1"]}), b: st({open: ["q1", "q2"], binding: "max_concurrent",
                        lastEntry: {signal_id: "s", error: "x"}})}, [dep("a"), dep("b")]);
      return [kinds(diff(gone, back)), kinds(diff(back, gone))];
    """)
    assert out == [[], []]


def test_only_live_deployments_with_a_status_are_in_the_snapshot():
    out = _run_js("""
      const s = M.buildLiveSnapshot(
        {a: st({open: ["p1"]}), p: st({open: ["p2"]}), n: null},
        [dep("a"), dep("p", {mode: "paper"}), dep("n"), dep("z"), {name: "no id", mode: "live"}]);
      return Object.keys(s.deployments);
    """)
    assert out == ["a"]


# --------------------------------------------------------------------------- #
# buildLiveSnapshot details
# --------------------------------------------------------------------------- #

def test_snapshot_reads_ids_orders_entry_key_binding_and_account():
    out = _run_js("""
      const s = snap({a: st({open: [{id: "b2", tsym: "X"}, {id: "a1", tsym: "Y"}], orders: 4,
                             lastEntry: {signal_id: "sig", error: "cap"}, binding: "live_paused",
                             halted: true, haltReason: "why"})});
      return s;
    """)
    d = out["deployments"]["a"]
    assert d["openIds"] == ["a1", "b2"]                       # sorted, id-keyed
    assert d["tsyms"] == {"a1": "Y", "b2": "X"}
    assert d["orders"] == 4
    assert d["lastEntryKey"] == "sig|cap" and d["lastEntryError"] == "cap"
    assert d["binding"] == "live_paused"
    assert out["account"] == {"known": True, "latched": False, "halted": True, "haltReason": "why"}


def test_a_position_without_an_id_falls_back_to_its_symbol_and_no_key_is_dropped():
    out = _run_js("""
      const s = snap({a: st({open: [{tsym: "ONLY-SYM"}, {id: null, tsym: null}, {id: ""}]})});
      return s.deployments.a.openIds;
    """)
    assert out == ["ONLY-SYM"]


def test_a_governor_with_an_error_contributes_no_binding_and_an_unknown_account():
    out = _run_js("""
      const s = snap({a: st({govError: "describe_failed:X", binding: "max_concurrent", latched: true})});
      return [s.deployments.a.binding, s.account.known, s.account.latched];
    """)
    assert out == [None, False, False]


def test_garbage_inputs_produce_an_empty_snapshot_not_an_exception():
    out = _run_js("""
      return [M.buildLiveSnapshot(null, null), M.buildLiveSnapshot("x", 5),
              M.buildLiveSnapshot({}, [null, undefined, {}])];
    """)
    for s in out:
        assert s["deployments"] == {} and s["account"]["known"] is False


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

OFF = {"enabled": False, "desktop": False, "sound": False}


def test_absent_or_malformed_storage_is_all_off():
    out = _run_js("""
      const bad = [null, undefined, "", "not json", "{", "[]", "[1,2]", "42", "true", "null",
                   '"str"', {}, 5];
      return bad.map((b) => M.parseNotifySettings(b));
    """)
    assert all(o == OFF for o in out)


def test_only_a_literal_true_turns_a_flag_on():
    out = _run_js("""
      return [
        M.parseNotifySettings('{"enabled": true, "desktop": true, "sound": true}'),
        M.parseNotifySettings('{"enabled": "yes", "desktop": 1, "sound": "true"}'),
        M.parseNotifySettings('{"enabled": true}'),
        M.parseNotifySettings('{"desktop": true}'),
      ];
    """)
    assert out[0] == {"enabled": True, "desktop": True, "sound": True}
    assert out[1] == OFF
    assert out[2] == {"enabled": True, "desktop": False, "sound": False}
    assert out[3] == {"enabled": False, "desktop": True, "sound": False}


def test_the_defaults_are_off_and_the_storage_key_is_versioned():
    out = _run_js("return [M.DEFAULT_NOTIFY_SETTINGS, M.NOTIFY_STORAGE_KEY];")
    assert out == [OFF, "af.liveNotify.v1"]


def test_settings_round_trip_and_never_serialise_a_truthy_non_boolean():
    out = _run_js("""
      const s = {enabled: true, desktop: false, sound: true};
      return [M.parseNotifySettings(M.serializeNotifySettings(s)),
              JSON.parse(M.serializeNotifySettings({enabled: 1, desktop: "x"})),
              JSON.parse(M.serializeNotifySettings(null))];
    """)
    assert out[0] == {"enabled": True, "desktop": False, "sound": True}
    assert out[1] == OFF and out[2] == OFF


def test_desktop_and_sound_mean_nothing_while_alerts_are_off():
    out = _run_js("""
      return [M.activeChannels({enabled: false, desktop: true, sound: true}),
              M.activeChannels({enabled: true, desktop: true, sound: false}),
              M.activeChannels({enabled: true, desktop: false, sound: true}),
              M.activeChannels(null)];
    """)
    assert out[0] == {"toast": False, "desktop": False, "sound": False}
    assert out[1] == {"toast": True, "desktop": True, "sound": False}
    assert out[2] == {"toast": True, "desktop": False, "sound": True}
    assert out[3] == {"toast": False, "desktop": False, "sound": False}


def test_only_a_granted_permission_turns_desktop_on_and_a_refusal_says_why():
    out = _run_js("""
      return [M.desktopPermissionOutcome(true, "granted"),
              M.desktopPermissionOutcome(true, "denied"),
              M.desktopPermissionOutcome(true, "default"),
              M.desktopPermissionOutcome(true, undefined),
              M.desktopPermissionOutcome(false, "granted")];
    """)
    assert out[0] == {"granted": True, "reason": None}
    assert out[1]["granted"] is False and "blocked" in out[1]["reason"]
    assert out[2]["granted"] is False and "dismissed" in out[2]["reason"]
    assert out[3]["granted"] is False
    assert out[4]["granted"] is False and "not support" in out[4]["reason"]
