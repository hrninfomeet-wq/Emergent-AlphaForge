"""Tests for app.live.arm_state.compute_arm_state — the single live-execution verdict."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.arm_state import compute_arm_state  # noqa: E402


def _s(**kw):
    base = dict(mode_doc={"mode": "PAPER", "single_shot_consumed": False},
                connected=True, autoplace_armed=False, guard_armed=False,
                armed_deployment_count=0)
    base.update(kw)
    return compute_arm_state(**base)


def test_safe_when_nothing_armed():
    s = _s()
    assert s["verdict"] == "SAFE"
    assert s["would_transmit_entry"] is False
    assert s["would_transmit_exit"] is False


def test_manual_live_test_armed_transmits_entry():
    s = _s(mode_doc={"mode": "LIVE_TEST", "single_shot_consumed": False})
    assert s["verdict"] == "LIVE"
    assert s["would_transmit_entry"] is True  # manual Place transmits regardless of the auto env gate


def test_manual_live_test_consumed_is_safe():
    s = _s(mode_doc={"mode": "LIVE_TEST", "single_shot_consumed": True})
    assert s["verdict"] == "SAFE"
    assert s["would_transmit_entry"] is False


def test_deployment_armed_but_autoplace_off_is_dry_run():
    s = _s(armed_deployment_count=2, autoplace_armed=False)
    assert s["verdict"] == "DRY_RUN"
    assert s["would_transmit_entry"] is False
    assert any("dry-run" in r for r in s["reasons"])


def test_deployment_armed_and_autoplace_on_transmits_entry():
    s = _s(armed_deployment_count=1, autoplace_armed=True)
    assert s["verdict"] == "LIVE"
    assert s["would_transmit_entry"] is True


def test_not_connected_never_transmits_even_when_armed():
    s = _s(mode_doc={"mode": "LIVE_TEST", "single_shot_consumed": False},
           armed_deployment_count=3, autoplace_armed=True, guard_armed=True, connected=False)
    assert s["would_transmit_entry"] is False
    assert s["would_transmit_exit"] is False
    # still surfaces that deployments are armed (dry-run, blocked by connectivity)
    assert s["verdict"] in ("DRY_RUN", "SAFE")


def test_guard_armed_transmits_exit():
    assert _s(guard_armed=True)["would_transmit_exit"] is True
    assert _s(guard_armed=False)["would_transmit_exit"] is False


def test_malformed_mode_doc_is_safe():
    assert compute_arm_state(mode_doc=None, connected=True, autoplace_armed=False,
                             guard_armed=False, armed_deployment_count=0)["verdict"] == "SAFE"
