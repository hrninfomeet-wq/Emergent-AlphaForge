"""Behaviour of frontend/src/lib/signalDisplay.js, EXECUTED through node.

A CONFIRMED signal is acted on only inside its own bar's evaluator pass; afterwards
nothing revisits it, so the amber CONFIRMED chip read as "awaiting action" for ever.
The ledger now labels it truthfully — and must never call a signal "not acted on" when
a trade may exist.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib", "signalDisplay.js")

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


# "now" = Tue 2026-09-29 15:00 IST (09:30Z)
NOW = "Date.UTC(2026, 8, 29, 9, 30)"


def _view(sig: str, now: str = NOW):
    return _run_js(f"return M.signalDisplay({sig}, {now});")


def _bar(minutes_ago: float) -> str:
    return f"{NOW} - {minutes_ago} * 60000"


# --------------------------------------------------------------------------- #
# CONFIRMED: pending only while its own pass runs
# --------------------------------------------------------------------------- #

def test_a_fresh_confirmed_signal_is_still_pending():
    v = _view(f"{{state: 'CONFIRMED', bar_ts: {_bar(1)}}}")
    assert v["label"] == "CONFIRMED" and v["tone"] == "pending" and v["expired"] is False


def test_a_confirmed_signal_past_its_pass_is_expired_not_pending():
    """THE defect: a CONFIRMED signal from an hour ago still read as awaiting action."""
    v = _view(f"{{state: 'CONFIRMED', bar_ts: {_bar(60)}}}")
    assert v["label"] == "EXPIRED" and v["tone"] == "expired" and v["expired"] is True
    assert "Not acted on" in v["note"] and "nothing revisits" in v["note"]
    assert "today 14:00 IST" in v["note"]


def test_a_confirmed_signal_from_an_earlier_day_is_expired_and_dated():
    v = _view(f"{{state: 'CONFIRMED', bar_ts: Date.UTC(2026, 8, 25, 4, 45)}}")
    assert v["label"] == "EXPIRED"
    assert "Fri 25 Sep 10:15 IST" in v["note"]


def test_the_expiry_boundary_is_fifteen_minutes_exactly():
    at = _view(f"{{state: 'CONFIRMED', bar_ts: {_bar(15)}}}")
    past = _view(f"{{state: 'CONFIRMED', bar_ts: {_bar(15.01)}}}")
    inside = _view(f"{{state: 'CONFIRMED', bar_ts: {_bar(14.99)}}}")
    assert inside["expired"] is False and at["expired"] is False
    assert past["expired"] is True


def test_the_threshold_matches_the_backend_sweep():
    """The display rule and signal_lifecycle.expire_unactioned_signals must agree."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.signal_lifecycle import UNACTIONED_AFTER_MINUTES
    out = _run_js("return M.EXPIRE_AFTER_MS;")
    assert out == UNACTIONED_AFTER_MINUTES * 60 * 1000


def test_the_day_boundary_is_IST_not_UTC():
    # Bar at 23:59 IST on the 28th (18:29Z), viewed 2 minutes later at 00:01 IST on the
    # 29th (18:31Z — still the 28th in UTC). Only 2 minutes old, but an EARLIER IST day.
    v = _view("{state: 'CONFIRMED', bar_ts: Date.UTC(2026, 8, 28, 18, 29, 0)}",
              "Date.UTC(2026, 8, 28, 18, 31, 0)")
    assert v["expired"] is True
    # Same instants but the bar at 23:50 IST viewed at 23:55 IST: same IST day, 5 minutes.
    same_day = _view("{state: 'CONFIRMED', bar_ts: Date.UTC(2026, 8, 28, 18, 20, 0)}",
                     "Date.UTC(2026, 8, 28, 18, 25, 0)")
    assert same_day["expired"] is False


def test_a_string_bar_ts_and_iso_bar_ts_are_read_too():
    for bar in ("'1790000000000'", "'2026-09-29T08:30:00+00:00'"):
        v = _view(f"{{state: 'CONFIRMED', bar_ts: {bar}}}")
        assert v["state"] == "CONFIRMED"
    assert _view("{state: 'CONFIRMED', bar_ts: '2026-09-29T08:30:00+00:00'}")["expired"] is True


def test_an_unreadable_bar_time_is_never_called_expired():
    for bar in ("null", "undefined", "'garbage'", "0", "''"):
        v = _view(f"{{state: 'CONFIRMED', bar_ts: {bar}}}")
        assert v["expired"] is False and v["label"] == "CONFIRMED"
        assert "bar time not recorded" in v["note"]


# --------------------------------------------------------------------------- #
# refusals, claims and trades
# --------------------------------------------------------------------------- #

def test_a_refused_signal_is_not_acted_on_immediately_even_when_fresh():
    """The sink refused and released its claim; nothing retries."""
    for field in ("live_trade_error", "paper_trade_error", "paper_trade_skip"):
        v = _view(f"{{state: 'CONFIRMED', bar_ts: {_bar(0.5)}, {field}: 'premium_stale'}}")
        assert v["label"] == "NOT ACTED ON" and v["expired"] is True, field
        assert v["note"] == "Not traded — refused: premium_stale"


def test_a_claimed_signal_with_no_trade_is_unverified_never_not_acted_on():
    """paper_trade_claim without a trade id = a crash between the trade insert and the
    signal write. A trade may exist: it must not be called unactioned."""
    v = _view(f"{{state: 'CONFIRMED', bar_ts: {_bar(600)}, "
              "paper_trade_claim: {source: 'auto_live', at: 'x'}}")
    assert v["label"] == "UNVERIFIED" and v["tone"] == "unverified" and v["expired"] is False
    assert "A trade may exist" in v["note"]


def test_a_signal_linked_to_a_trade_is_never_expired():
    for field in ("paper_trade_id", "live_trade_id"):
        v = _view(f"{{state: 'CONFIRMED', bar_ts: {_bar(600)}, {field}: 't1'}}")
        assert v["expired"] is False and v["label"] == "CONFIRMED"


# --------------------------------------------------------------------------- #
# every other state is untouched
# --------------------------------------------------------------------------- #

def test_other_states_pass_through_unchanged_however_old():
    for st in ("TRIGGERED", "ACTIVE", "EXITED", "AUDITED", "WATCHING", "FORMING", "SKIPPED"):
        v = _view(f"{{state: '{st}', bar_ts: {_bar(99999)}}}")
        assert v == {"state": st, "label": st, "tone": "normal", "expired": False, "note": None}


def test_lowercase_and_missing_state_are_handled():
    assert _view("{state: 'confirmed', bar_ts: %s}" % _bar(60))["label"] == "EXPIRED"
    assert _view("{}")["label"] == "—"
    assert _view("null")["label"] == "—"


def test_chip_classes():
    out = _run_js("""
      return [M.signalChipClass({tone: "expired"}, "F"),
              M.signalChipClass({tone: "unverified"}, "F"),
              M.signalChipClass({tone: "pending"}, "F"),
              M.signalChipClass({tone: "normal"}, "F"),
              M.signalChipClass(null, "F")];
    """)
    assert out[0] == "border-line text-dimmer"
    assert "rose" in out[1]
    assert out[2:] == ["F", "F", "F"]
