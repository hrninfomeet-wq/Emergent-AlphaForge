"""Source contract: the safety latch must always have a visible way back.

The defect this guards (found 2026-07-27, Phase 0.1): `blocked_until_reset` halts
every new live entry and never self-clears, and `POST
/live-broker/safety-config/reset-latch` existed with **zero** frontend callers —
so a halted operator's only exit was a raw API call. The banner is easy to
un-mount by accident during a layout refactor (exactly how `ExecutionStateStrip`
was silently dropped during the cockpit rewrite), and nothing would fail.

These are grep-the-source tests, matching the project's existing frontend-contract
pattern: they assert WIRING, not rendering.
"""
from __future__ import annotations

import os

ROOT = os.path.join(os.path.dirname(__file__), "..", "frontend", "src")


def _read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_api_client_exposes_the_reset_latch_call():
    src = _read("lib", "api.js")
    assert "resetSafetyLatch" in src
    assert "/live-broker/safety-config/reset-latch" in src, (
        "the reset endpoint path must be wired; the latch has no other exit")


def test_banner_component_exists_and_calls_reset():
    src = _read("components", "live", "SafetyLatchBanner.jsx")
    assert "api.resetSafetyLatch" in src
    assert "api.getSafetyConfig" in src, "the banner must read the live latch state"
    assert "blocked_until_reset" in src


def test_banner_is_actually_MOUNTED_in_the_cockpit():
    """A component nobody renders is the same as no component at all."""
    src = _read("components", "live", "LiveCockpit.jsx")
    assert "SafetyLatchBanner" in src, "the banner import/mount was lost"
    assert "<SafetyLatchBanner" in src, (
        "SafetyLatchBanner is imported but never rendered — this is exactly how "
        "ExecutionStateStrip was silently dropped in the cockpit rewrite")


def test_banner_surfaces_why_and_when_not_just_that():
    """'Trading halted' with no cause or time cannot be triaged."""
    src = _read("components", "live", "SafetyLatchBanner.jsx")
    assert "latched_reason" in src
    assert "latched_at" in src


def test_reset_is_two_step_not_one_click():
    """Clearing the latch re-authorises REAL-MONEY entries. It must not be a
    single stray click."""
    src = _read("components", "live", "SafetyLatchBanner.jsx")
    assert "safety-latch-reset-confirm" in src
    assert "confirming" in src


def test_a_failed_poll_never_renders_a_reassuring_not_halted_state():
    """If the config read fails we must keep the last-known value, never fall
    back to 'no latch' — that would hide an active lockout."""
    src = _read("components", "live", "SafetyLatchBanner.jsx")
    # the catch block must not reset state to a cleared/unknown-but-safe value
    assert "setCfg(null)" not in src, (
        "clearing state on a failed poll would render the banner invisible while "
        "the desk is still halted")
