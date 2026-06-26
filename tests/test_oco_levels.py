"""Tests for app.live.oco_levels.compute_catastrophe_band (pure, no I/O).

The catastrophe-band OCO is a PC-down LAST-RESORT and must NEVER fire at the
same premium as the in-process software guard (which would risk a double-sell).
The catastrophe stop is therefore DERIVED strictly wider than the guard stop:
    eff_stop_pct = max(configured_or_default, guard_stop_pct + MIN_GAP_PP)

The headline invariant (looped over a grid below): the returned sl_trigger is
ALWAYS a strictly LOWER premium than the guard's own stop level
    entry * (1 - guard_stop_pct / 100)
for every input — so the resting OCO can never race the guard.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# Ensure backend/ is on sys.path (same pattern as all other test_live_*.py)
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.live.oco_levels import (  # noqa: E402
    DEFAULT_STOP_PCT,
    DEFAULT_TARGET_PCT,
    MIN_GAP_PP,
    compute_catastrophe_band,
)


def test_configured_below_guard_clamps_wider_than_guard():
    # configured 48 <= guard 50 → eff clamps to 50 + 15 = 65
    # sl_trigger ~= 100 * (1 - 0.65) = 35.0, STRICTLY BELOW the guard stop level 50.0
    band = compute_catastrophe_band(
        100.0, guard_stop_pct=50, stop_pct=48, target_pct=135
    )
    assert band is not None
    sl_trigger, sl_limit, tp_trigger, tp_limit = band

    guard_level = 100.0 * (1 - 50 / 100.0)  # 50.0
    assert sl_trigger == pytest.approx(35.0)
    assert sl_trigger < guard_level  # strictly wider than the guard

    assert tp_trigger == pytest.approx(235.0)

    # SELL legs are marketable-to-clear: limit sits BELOW its trigger
    assert sl_limit < sl_trigger
    assert tp_limit < tp_trigger

    # all tick(0.05)-rounded
    for v in band:
        assert round(v / 0.05) == pytest.approx(v / 0.05, abs=1e-9)


def test_configured_above_guard_gap_used_as_is():
    # 48 > 30 + 15 = 45 → eff = 48 → sl_trigger ~= 100 * (1 - 0.48) = 52.0
    band = compute_catastrophe_band(100.0, guard_stop_pct=30, stop_pct=48)
    assert band is not None
    sl_trigger, sl_limit, tp_trigger, tp_limit = band

    guard_level = 100.0 * (1 - 30 / 100.0)  # 70.0
    assert sl_trigger == pytest.approx(52.0)
    assert sl_trigger < guard_level  # still strictly wider than the guard


def test_strictly_wider_invariant_over_grid():
    """THE #1 real-money safety invariant: sl_trigger is ALWAYS a strictly
    lower premium than the guard's own stop level, for every input."""
    entry = 100.0
    for guard_stop_pct in (20, 30, 40, 50):
        for configured in (None, 45, 48, 50, 70):
            band = compute_catastrophe_band(
                entry, guard_stop_pct=guard_stop_pct, stop_pct=configured
            )
            assert band is not None, (guard_stop_pct, configured)
            sl_trigger = band[0]
            guard_level = entry * (1 - guard_stop_pct / 100.0)
            assert sl_trigger < guard_level, (
                f"guard_stop_pct={guard_stop_pct} configured={configured}: "
                f"sl_trigger={sl_trigger} NOT strictly below guard_level={guard_level}"
            )


def test_defaults_applied_when_none():
    assert DEFAULT_STOP_PCT == 50.0
    assert DEFAULT_TARGET_PCT == 135.0
    assert MIN_GAP_PP == 15.0

    # stop_pct=None → 50 floor; with a low guard the 50 floor dominates.
    band = compute_catastrophe_band(100.0, guard_stop_pct=10, stop_pct=None)
    assert band is not None
    sl_trigger, _sl_limit, tp_trigger, _tp_limit = band
    # eff = max(50, 10 + 15) = 50 → sl_trigger ~= 50.0
    assert sl_trigger == pytest.approx(50.0)
    # target_pct=None → 135 → tp_trigger ~= 235.0
    assert tp_trigger == pytest.approx(235.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 0.0, -5.0, None, "x"])
def test_non_finite_or_nonpositive_entry_returns_none(bad):
    assert compute_catastrophe_band(bad, guard_stop_pct=50) is None


# --------------------------------------------------------------------------- #
# POINTS-derived guard stops (the BLOCKER): when a deployment uses a pts stop the
# caller derives guard_stop_pct from the guard's resolved ABSOLUTE stop level. A
# deep pts stop (e.g. stop_pts=70 → guard stop level 30.0 → guard_stop_pct=70)
# must STILL produce an sl_trigger strictly BELOW the guard level — never the old
# 50%-default that would land ABOVE a deeper-than-50% guard stop and fire first.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("guard_stop_pct", [30, 50, 60, 70, 80])
def test_points_derived_guard_stop_band_strictly_wider(guard_stop_pct):
    """entry=100, so guard_stop_pct == stop_pts. Whenever a band is returned the
    sl_trigger must be a STRICTLY lower premium than the guard's own stop level
    entry*(1 - guard_stop_pct/100). This is the inverted-OCO BLOCKER."""
    entry = 100.0
    band = compute_catastrophe_band(entry, guard_stop_pct=guard_stop_pct)
    guard_level = entry * (1 - guard_stop_pct / 100.0)
    if band is not None:
        sl_trigger = band[0]
        assert sl_trigger < guard_level, (
            f"guard_stop_pct={guard_stop_pct}: sl_trigger={sl_trigger} "
            f"NOT strictly below guard_level={guard_level}"
        )


@pytest.mark.parametrize("guard_stop_pct", [81, 85, 90, 95, 120])
def test_no_safe_gap_returns_none(guard_stop_pct):
    """When guard_stop_pct + MIN_GAP_PP exceeds the ~95% premium floor there is no
    room for a safe catastrophe gap below the guard → GRACEFUL DEGRADE to None
    (the caller then leaves the position software-guard-only). The old silent
    clamp to 95% would have tied the OCO at-or-above the guard — that must be gone."""
    assert compute_catastrophe_band(100.0, guard_stop_pct=guard_stop_pct) is None


def test_just_within_safe_gap_returns_band():
    # 80 + 15 = 95 == cap → still room (boundary): a band IS returned, strictly wider.
    band = compute_catastrophe_band(100.0, guard_stop_pct=80)
    assert band is not None
    sl_trigger = band[0]
    assert sl_trigger < 100.0 * (1 - 80 / 100.0)  # < 20.0


@pytest.mark.parametrize("bad_target", [-5.0, 0.0, float("nan"), float("inf")])
def test_invalid_target_falls_back_to_default(bad_target):
    """A non-finite or <= 0 target_pct must fall back to DEFAULT_TARGET_PCT (135),
    never resting a negative/zero broker TP."""
    band = compute_catastrophe_band(100.0, guard_stop_pct=30, target_pct=bad_target)
    assert band is not None
    tp_trigger = band[2]
    # default 135 → tp_trigger ~= 235.0; and ALWAYS strictly above entry.
    assert tp_trigger == pytest.approx(235.0)
    assert tp_trigger > 100.0


def test_absurd_target_bounded_not_resting_nonsense():
    """A mistyped absurd target (e.g. 5000%) must not rest a nonsensical broker TP —
    it is capped to a sane max (or falls back to the default), never the raw 5000%."""
    band = compute_catastrophe_band(100.0, guard_stop_pct=30, target_pct=5000)
    assert band is not None
    tp_trigger = band[2]
    raw = 100.0 * (1 + 5000 / 100.0)  # 5100.0 — the nonsensical value
    assert tp_trigger < raw
