"""Parity battery pinning the CURRENT behaviour of ``_walk_option_exit``.

This is the GATE for the Phase-2 vectorization (Task 6 of the
analyzing-governed-fast plan). ``_ref`` below is a VERBATIM frozen copy of the
production ``app.option_backtest._walk_option_exit`` (plus its local helper
``_breakeven_binding``) as it stands today. The frozen ref shares the audited
helper functions with production (``effective_premium_stop`` / ``stop_fill_price``
from ``app.exit_controls``; ``intrabar_exit`` from ``app.exit_engine``), so the
ref encodes only the WALK structure, not a re-implementation of the deciders.

Each case asserts ``app.option_backtest._walk_option_exit(...) == _ref(...)``
field-for-field on ``exit_ts`` / ``exit_price`` / ``exit_reason``. They are
identical today, so the suite is green now and becomes a regression oracle:
when production is vectorized, any byte-level drift fails here. A few cases ALSO
pin the absolute expected dict so the battery catches a ref+prod that were ever
wrong together.

Host-safe: ``app.option_backtest`` pulls only pandas/numpy/exit_engine/
exit_controls/execution_policy (and sibling pure modules) -- no server/optimizer.
"""
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.option_backtest import _walk_option_exit  # noqa: E402
from app.exit_controls import (  # noqa: E402
    ExitControlsConfig,
    effective_premium_stop,
    stop_fill_price,
    EXIT_TRAIL_STOP,
    EXIT_BREAKEVEN_STOP,
)
from app.exit_engine import intrabar_exit  # noqa: E402


# --------------------------------------------------------------------------- #
# FROZEN ORACLE -- verbatim copy of the CURRENT _walk_option_exit body and its
# local helper _breakeven_binding. DO NOT "improve" these; they must stay valid
# (== production) only while production is unchanged, then diverge-detect once
# production is vectorized.
# --------------------------------------------------------------------------- #
def _ref(
    rows: pd.DataFrame,
    *,
    entry_ts: int,
    backstop_ts: int,
    entry_price: float,
    target_level: Optional[float],
    stop_level: Optional[float],
    exit_cfg: Optional[ExitControlsConfig] = None,
) -> Dict[str, Any]:
    forward = rows[(rows["ts"] > entry_ts) & (rows["ts"] <= backstop_ts)].sort_values("ts")
    last_close = entry_price
    last_ts = entry_ts
    running_max = float(entry_price)
    use_overlay = exit_cfg is not None and exit_cfg.enabled
    for _, bar in forward.iterrows():
        bar_ts = int(bar["ts"])
        high = float(bar.get("high", bar.get("close", entry_price)))
        low = float(bar.get("low", bar.get("close", entry_price)))
        bar_open = bar.get("open")
        last_close = float(bar.get("close", last_close))
        last_ts = bar_ts
        eff_stop = (effective_premium_stop(entry=entry_price, running_max=running_max,
                                           base_stop=stop_level, cfg=exit_cfg)
                    if use_overlay else stop_level)
        level, reason = intrabar_exit(
            high=high, low=low, stop=eff_stop, target=target_level, is_long=True,
        )
        if level is not None:
            if reason == "STOP":
                fill = stop_fill_price(level, reason, bar_open) if use_overlay else level
                exit_reason = "OPTION_STOP"
                if use_overlay and stop_level is not None and eff_stop is not None and eff_stop > float(stop_level):
                    exit_reason = (EXIT_BREAKEVEN_STOP
                                   if _ref_breakeven_binding(entry_price, running_max, eff_stop, exit_cfg)
                                   else EXIT_TRAIL_STOP)
                elif use_overlay and stop_level is None and eff_stop is not None:
                    exit_reason = EXIT_TRAIL_STOP
                return {"exit_ts": bar_ts, "exit_price": fill, "exit_reason": exit_reason}
            return {"exit_ts": bar_ts, "exit_price": level, "exit_reason": "OPTION_TARGET"}
        running_max = max(running_max, high)
    return {"exit_ts": last_ts, "exit_price": last_close, "exit_reason": "OPTION_SIGNAL_EXIT"}


def _ref_breakeven_binding(entry, running_max, eff_stop, cfg) -> bool:
    e = float(entry)
    if not (cfg.be_trigger and cfg.be_trigger > 0):
        return False
    if cfg.unit == "pts":
        be_level = e + (cfg.be_lock or 0.0)
        trig = e + cfg.be_trigger
    else:
        be_level = e * (1.0 + (cfg.be_lock or 0.0))
        trig = e * (1.0 + cfg.be_trigger)
    return float(running_max) >= trig and abs(be_level - float(eff_stop)) < 1e-9


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _mk(rows) -> pd.DataFrame:
    """Candle frame with the columns _walk_option_exit reads: ts/open/high/low/close."""
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])


def _trail_cfg(*, activation=0.10, distance=0.25, unit="pct") -> ExitControlsConfig:
    return ExitControlsConfig.from_dict(
        {"enabled": True, "unit": unit, "trailing": {"activation": activation, "distance": distance}}
    )


def _be_cfg(*, trigger=0.20, lock=0.0, unit="pct") -> ExitControlsConfig:
    return ExitControlsConfig.from_dict(
        {"enabled": True, "unit": unit, "breakeven": {"trigger": trigger, "lock": lock}}
    )


def _both_cfg(*, be_trigger=0.20, be_lock=0.0, activation=0.10, distance=0.25, unit="pct") -> ExitControlsConfig:
    return ExitControlsConfig.from_dict(
        {"enabled": True, "unit": unit,
         "breakeven": {"trigger": be_trigger, "lock": be_lock},
         "trailing": {"activation": activation, "distance": distance}}
    )


def _disabled_but_present_cfg() -> ExitControlsConfig:
    # enabled=False -> use_overlay False even though a cfg object is passed.
    return ExitControlsConfig.from_dict(
        {"enabled": False, "unit": "pct", "trailing": {"activation": 0.10, "distance": 0.25}}
    )


# The full param surface the production function reads.
_FIELDS = ("exit_ts", "exit_price", "exit_reason")


def _assert_parity(rows, **kw):
    """Field-for-field parity of production vs the frozen ref. Returns the
    production result so a caller can additionally pin absolute values."""
    prod = _walk_option_exit(rows, **kw)
    ref = _ref(rows, **kw)
    assert set(prod) == set(ref) == set(_FIELDS), (prod, ref)
    for f in _FIELDS:
        assert prod[f] == ref[f], f"field {f!r}: prod={prod[f]!r} ref={ref[f]!r}"
    return prod


# =========================================================================== #
# BATTERY
# =========================================================================== #

# --- overlay OFF (exit_cfg=None) ------------------------------------------- #

def test_target_only_hit_overlay_off():
    # high 130 >= target 120 on bar ts2; no stop set.
    rows = _mk([
        {"ts": 1, "open": 100, "high": 108, "low": 96, "close": 105},
        {"ts": 2, "open": 105, "high": 130, "low": 102, "close": 122},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=120.0, stop_level=None)
    assert out == {"exit_ts": 2, "exit_price": 120.0, "exit_reason": "OPTION_TARGET"}


def test_stop_only_hit_overlay_off():
    # low 78 <= stop 80 on bar ts2; no target set. Fills AT the stop level (overlay off).
    rows = _mk([
        {"ts": 1, "open": 100, "high": 104, "low": 95, "close": 98},
        {"ts": 2, "open": 96, "high": 99, "low": 78, "close": 82},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=None, stop_level=80.0)
    assert out == {"exit_ts": 2, "exit_price": 80.0, "exit_reason": "OPTION_STOP"}


def test_same_bar_stop_and_target_resolves_stop_first_overlay_off():
    # ts1: high 130 >= target 120 AND low 70 <= stop 80 -> intrabar_exit(stop_first=True)
    # must pick STOP. Fills at the stop LEVEL (overlay off; no gap-fill path).
    rows = _mk([
        {"ts": 1, "open": 100, "high": 130, "low": 70, "close": 95},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=1, entry_price=100.0,
                         target_level=120.0, stop_level=80.0)
    assert out == {"exit_ts": 1, "exit_price": 80.0, "exit_reason": "OPTION_STOP"}


def test_gap_down_through_stop_overlay_off_fills_at_stop_level():
    # bar opens 60 (< stop 80) and low 55 <= stop. Overlay OFF -> NO gap-fill, fills
    # at the stop level 80 (stop_fill_price is only called on the overlay path).
    rows = _mk([
        {"ts": 1, "open": 60, "high": 62, "low": 55, "close": 58},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=1, entry_price=100.0,
                         target_level=None, stop_level=80.0)
    assert out == {"exit_ts": 1, "exit_price": 80.0, "exit_reason": "OPTION_STOP"}


def test_no_hit_falls_through_to_signal_exit_overlay_off():
    # Neither level touched -> OPTION_SIGNAL_EXIT at the LAST bar's close/ts.
    rows = _mk([
        {"ts": 1, "open": 100, "high": 110, "low": 92, "close": 105},
        {"ts": 2, "open": 105, "high": 115, "low": 96, "close": 112},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=200.0, stop_level=50.0)
    assert out == {"exit_ts": 2, "exit_price": 112.0, "exit_reason": "OPTION_SIGNAL_EXIT"}


def test_empty_forward_window_returns_entry_as_signal_exit():
    # All bars are at/under entry_ts (filtered out by ts > entry_ts) -> the loop
    # never runs and the fall-through returns last_ts=entry_ts, last_close=entry_price.
    rows = _mk([
        {"ts": -5, "open": 100, "high": 110, "low": 90, "close": 105},
        {"ts": 0, "open": 100, "high": 130, "low": 70, "close": 105},  # ts==entry_ts excluded
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=10, entry_price=100.0,
                         target_level=120.0, stop_level=80.0)
    assert out == {"exit_ts": 0, "exit_price": 100.0, "exit_reason": "OPTION_SIGNAL_EXIT"}


def test_fully_empty_frame_returns_entry_as_signal_exit():
    rows = _mk([])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=10, entry_price=42.5,
                         target_level=80.0, stop_level=20.0)
    assert out == {"exit_ts": 0, "exit_price": 42.5, "exit_reason": "OPTION_SIGNAL_EXIT"}


def test_backstop_excludes_late_bars_overlay_off():
    # A stop-crossing bar exists at ts3 but backstop_ts=2 excludes it -> signal exit
    # at the last IN-WINDOW bar (ts2). Pins the (ts > entry) & (ts <= backstop) filter.
    rows = _mk([
        {"ts": 1, "open": 100, "high": 108, "low": 96, "close": 104},
        {"ts": 2, "open": 104, "high": 112, "low": 98, "close": 109},
        {"ts": 3, "open": 109, "high": 110, "low": 60, "close": 70},  # would stop, but excluded
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=None, stop_level=80.0)
    assert out == {"exit_ts": 2, "exit_price": 109.0, "exit_reason": "OPTION_SIGNAL_EXIT"}


def test_unsorted_rows_are_sorted_before_walk_overlay_off():
    # Rows supplied out of ts order; the walk sorts, so the EARLIEST crossing wins.
    # ts1 crosses target 120 (high 125); ts2 also would but ts1 is first chronologically.
    rows = _mk([
        {"ts": 2, "open": 122, "high": 140, "low": 118, "close": 130},
        {"ts": 1, "open": 100, "high": 125, "low": 99, "close": 121},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=120.0, stop_level=None)
    assert out == {"exit_ts": 1, "exit_price": 120.0, "exit_reason": "OPTION_TARGET"}


def test_no_levels_at_all_overlay_off_signal_exit():
    # target AND stop both None, overlay off -> no exit can fire -> signal exit at last bar.
    rows = _mk([
        {"ts": 1, "open": 100, "high": 110, "low": 95, "close": 108},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=1, entry_price=100.0,
                         target_level=None, stop_level=None)
    assert out == {"exit_ts": 1, "exit_price": 108.0, "exit_reason": "OPTION_SIGNAL_EXIT"}


# --- overlay OFF via a present-but-disabled cfg ---------------------------- #

def test_disabled_cfg_object_behaves_like_overlay_off():
    # exit_cfg present but enabled=False -> use_overlay False; identical to None.
    rows = _mk([
        {"ts": 1, "open": 100, "high": 104, "low": 78, "close": 82},  # stop 80 hit, fills at level
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=1, entry_price=100.0,
                         target_level=None, stop_level=80.0, exit_cfg=_disabled_but_present_cfg())
    assert out == {"exit_ts": 1, "exit_price": 80.0, "exit_reason": "OPTION_STOP"}


# --- overlay ON, trailing -------------------------------------------------- #

def test_overlay_trailing_ratchet_tagged_trail_no_base_stop():
    # No base stop; trail distance 0.25. Peak 200 reached at ts2 (running_max uses the
    # PRIOR-bar peak, so ts2 itself isn't stopped). ts3 trail = 200*0.75 = 150; low 140
    # crosses -> EXIT_TRAIL_STOP, fills at the trail level 150 (no gap, open 150 == level).
    cfg = _trail_cfg(activation=0.10, distance=0.25)
    rows = _mk([
        {"ts": 1, "open": 100, "high": 120, "low": 100, "close": 120},
        {"ts": 2, "open": 120, "high": 200, "low": 118, "close": 190},
        {"ts": 3, "open": 150, "high": 152, "low": 140, "close": 145},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=3, entry_price=100.0,
                         target_level=None, stop_level=None, exit_cfg=cfg)
    assert out == {"exit_ts": 3, "exit_price": 150.0, "exit_reason": "OPTION_TRAIL_STOP"}


def test_overlay_trailing_gap_fills_at_open():
    # Peak 200 at ts1. ts2 OPENS 130 < trail 150 -> overlay gap-fill at the OPEN (130),
    # not the trail level. Reason still EXIT_TRAIL_STOP (no base stop).
    cfg = _trail_cfg(activation=0.10, distance=0.25)
    rows = _mk([
        {"ts": 1, "open": 100, "high": 200, "low": 100, "close": 200},
        {"ts": 2, "open": 130, "high": 131, "low": 120, "close": 125},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=None, stop_level=None, exit_cfg=cfg)
    assert out == {"exit_ts": 2, "exit_price": 130.0, "exit_reason": "OPTION_TRAIL_STOP"}


def test_overlay_trailing_ratchets_above_base_stop_tagged_trail():
    # Base stop 80 + trail. Trail (150 from peak 200) ratchets ABOVE base -> tagged TRAIL,
    # fills at the trail level 150 (open 151 not below level -> no gap).
    cfg = _trail_cfg(activation=0.10, distance=0.25)
    rows = _mk([
        {"ts": 1, "open": 100, "high": 200, "low": 100, "close": 200},
        {"ts": 2, "open": 151, "high": 152, "low": 149, "close": 150},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=None, stop_level=80.0, exit_cfg=cfg)
    assert out == {"exit_ts": 2, "exit_price": 150.0, "exit_reason": "OPTION_TRAIL_STOP"}


def test_overlay_trailing_not_yet_activated_uses_base_stop_tagged_option_stop():
    # Trail activation 0.50 -> needs running_max >= 150 to arm. Premium never exceeds 120,
    # so the trail never arms; the base stop 80 fires -> plain OPTION_STOP (no overlay tag).
    cfg = _trail_cfg(activation=0.50, distance=0.25)
    rows = _mk([
        {"ts": 1, "open": 100, "high": 118, "low": 100, "close": 110},  # peak 118 < 150 (not armed)
        {"ts": 2, "open": 110, "high": 112, "low": 75, "close": 80},     # base stop 80; low 75 <= 80
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=None, stop_level=80.0, exit_cfg=cfg)
    assert out == {"exit_ts": 2, "exit_price": 80.0, "exit_reason": "OPTION_STOP"}


# --- overlay ON, breakeven ------------------------------------------------- #

def test_overlay_breakeven_binds_tagged_breakeven():
    # Base stop 80, BE trigger 0.20 lock 0.0. ts1 peak 130 (check at ts2 uses prior rm=130).
    # rm 130 >= trigger 120 -> BE stop = entry 100. ts2 low 95 <= 100 -> EXIT_BREAKEVEN_STOP.
    cfg = _be_cfg(trigger=0.20, lock=0.0)
    rows = _mk([
        {"ts": 1, "open": 100, "high": 130, "low": 110, "close": 125},
        {"ts": 2, "open": 105, "high": 106, "low": 95, "close": 100},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=None, stop_level=80.0, exit_cfg=cfg)
    assert out == {"exit_ts": 2, "exit_price": 100.0, "exit_reason": "OPTION_BREAKEVEN_STOP"}


def test_overlay_breakeven_with_positive_lock_tagged_breakeven():
    # BE trigger 0.20, lock 0.10 -> once rm >= 120, stop ratchets to entry*1.10 = 110.
    # ts2 low 105 <= 110 -> EXIT_BREAKEVEN_STOP at level 110 (open 108 < 110 -> gap fill 108).
    cfg = _be_cfg(trigger=0.20, lock=0.10)
    rows = _mk([
        {"ts": 1, "open": 100, "high": 130, "low": 100, "close": 125},
        {"ts": 2, "open": 108, "high": 112, "low": 105, "close": 109},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=None, stop_level=80.0, exit_cfg=cfg)
    # gap: open 108 < be level 110 -> fills at 108; reason still BREAKEVEN.
    assert out == {"exit_ts": 2, "exit_price": 108.0, "exit_reason": "OPTION_BREAKEVEN_STOP"}


def test_overlay_breakeven_not_triggered_uses_base_stop_tagged_option_stop():
    # BE trigger 0.50 -> needs rm >= 150. Premium peaks at 120 (never arms BE). Base stop 80
    # fires -> plain OPTION_STOP (eff_stop == base -> not > base -> no overlay tag).
    cfg = _be_cfg(trigger=0.50, lock=0.0)
    rows = _mk([
        {"ts": 1, "open": 100, "high": 120, "low": 100, "close": 115},  # peak 120 < 150
        {"ts": 2, "open": 110, "high": 112, "low": 78, "close": 82},     # base stop 80; low 78 <= 80
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=None, stop_level=80.0, exit_cfg=cfg)
    assert out == {"exit_ts": 2, "exit_price": 80.0, "exit_reason": "OPTION_STOP"}


# --- overlay ON, neither ratchet nor breakeven binds ----------------------- #

def test_overlay_enabled_but_no_be_no_trail_uses_base_stop():
    # enabled=True but BOTH be_trigger and trail_distance are 0 -> effective_premium_stop
    # returns the base stop unchanged -> plain OPTION_STOP.
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct"})
    rows = _mk([
        {"ts": 1, "open": 100, "high": 110, "low": 78, "close": 82},  # base stop 80, low 78 <= 80
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=1, entry_price=100.0,
                         target_level=None, stop_level=80.0, exit_cfg=cfg)
    assert out == {"exit_ts": 1, "exit_price": 80.0, "exit_reason": "OPTION_STOP"}


# --- overlay ON, target still wins (overlay only governs the STOP) --------- #

def test_overlay_on_target_hit_still_tagged_target():
    # With overlay armed, a TARGET crossing on a bar that does NOT cross the (ratcheted)
    # stop still returns OPTION_TARGET at the target level. Pins that overlay tagging is
    # stop-path only.
    cfg = _trail_cfg(activation=0.10, distance=0.25)
    rows = _mk([
        {"ts": 1, "open": 100, "high": 118, "low": 100, "close": 115},  # arm-ish; no stop cross
        {"ts": 2, "open": 116, "high": 205, "low": 114, "close": 200},  # target 200 hit; low 114 not under trail
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=200.0, stop_level=80.0, exit_cfg=cfg)
    assert out == {"exit_ts": 2, "exit_price": 200.0, "exit_reason": "OPTION_TARGET"}


def test_overlay_on_same_bar_stop_first_with_gap_open():
    # Overlay ON, base stop 80 (no ratchet armed because rm stays at entry 100 on the first
    # bar). ts1: high 130 >= target 120 AND low 70 <= base stop 80 -> STOP first. open 75 < 80
    # -> overlay gap-fill at OPEN 75. eff_stop == base (not > base) -> plain OPTION_STOP.
    cfg = _trail_cfg(activation=0.10, distance=0.25)
    rows = _mk([
        {"ts": 1, "open": 75, "high": 130, "low": 70, "close": 90},
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=1, entry_price=100.0,
                         target_level=120.0, stop_level=80.0, exit_cfg=cfg)
    assert out == {"exit_ts": 1, "exit_price": 75.0, "exit_reason": "OPTION_STOP"}


# --- pts-unit overlay (exercises the other branch of effective_premium_stop) #

def test_overlay_trailing_pts_unit_tagged_trail():
    # unit=pts: trail distance is ABSOLUTE premium points. Peak 200, distance 50 -> trail 150.
    cfg = _trail_cfg(activation=10.0, distance=50.0, unit="pts")
    rows = _mk([
        {"ts": 1, "open": 100, "high": 200, "low": 100, "close": 200},
        {"ts": 2, "open": 151, "high": 152, "low": 148, "close": 150},  # trail 150; low 148 <= 150
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=None, stop_level=None, exit_cfg=cfg)
    assert out == {"exit_ts": 2, "exit_price": 150.0, "exit_reason": "OPTION_TRAIL_STOP"}


# --- target/stop=None permutations under overlay --------------------------- #

@pytest.mark.parametrize("target_level,stop_level", [
    (None, None),
    (120.0, None),
    (None, 80.0),
    (120.0, 80.0),
])
def test_level_none_permutations_overlay_off(target_level, stop_level):
    rows = _mk([
        {"ts": 1, "open": 100, "high": 109, "low": 92, "close": 104},
        {"ts": 2, "open": 104, "high": 113, "low": 88, "close": 110},
    ])
    # Whatever the production does, the frozen ref must agree exactly.
    _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                   target_level=target_level, stop_level=stop_level)


@pytest.mark.parametrize("target_level,stop_level", [
    (None, None),
    (120.0, None),
    (None, 80.0),
    (120.0, 80.0),
])
def test_level_none_permutations_overlay_trailing(target_level, stop_level):
    cfg = _trail_cfg(activation=0.10, distance=0.25)
    rows = _mk([
        {"ts": 1, "open": 100, "high": 200, "low": 100, "close": 200},  # peak 200
        {"ts": 2, "open": 151, "high": 152, "low": 140, "close": 145},  # trail 150; low 140 <= 150
    ])
    _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                   target_level=target_level, stop_level=stop_level, exit_cfg=cfg)


# --- breakeven + trailing together (attribution: BE binds when eff_stop is the BE level) #

def test_overlay_breakeven_and_trailing_breakeven_wins_attribution():
    # Both armed. After peak 130: BE level = entry 100 (trigger 0.20 met), trail level =
    # 130*0.75 = 97.5. eff_stop = max(base 80, 100, 97.5) = 100 == BE level -> BREAKEVEN tag.
    cfg = _both_cfg(be_trigger=0.20, be_lock=0.0, activation=0.10, distance=0.25)
    rows = _mk([
        {"ts": 1, "open": 100, "high": 130, "low": 100, "close": 128},
        {"ts": 2, "open": 105, "high": 106, "low": 95, "close": 100},  # eff_stop 100; low 95 <= 100
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=None, stop_level=80.0, exit_cfg=cfg)
    assert out == {"exit_ts": 2, "exit_price": 100.0, "exit_reason": "OPTION_BREAKEVEN_STOP"}


def test_overlay_breakeven_and_trailing_trail_wins_attribution():
    # Both armed but the trail ratchets ABOVE the BE level. Peak 300: BE level = 100,
    # trail = 300*0.75 = 225. eff_stop = max(80, 100, 225) = 225 != BE level -> TRAIL tag.
    cfg = _both_cfg(be_trigger=0.20, be_lock=0.0, activation=0.10, distance=0.25)
    rows = _mk([
        {"ts": 1, "open": 100, "high": 300, "low": 100, "close": 290},
        {"ts": 2, "open": 226, "high": 227, "low": 220, "close": 224},  # trail 225; low 220 <= 225
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                         target_level=None, stop_level=80.0, exit_cfg=cfg)
    assert out == {"exit_ts": 2, "exit_price": 225.0, "exit_reason": "OPTION_TRAIL_STOP"}


def test_overlay_trail_with_no_base_stop_first_bar_peak_not_self_stopped():
    # Look-ahead safety: the bar that PRINTS the peak must not be stopped within itself,
    # because running_max is the peak THROUGH the PRIOR bar. Single bar, no prior -> the
    # trail is seeded at entry; with activation unmet it can't fire on bar 1 -> signal exit.
    cfg = _trail_cfg(activation=0.90, distance=0.25)  # needs rm >= 190 to arm
    rows = _mk([
        {"ts": 1, "open": 100, "high": 200, "low": 130, "close": 195},  # prints 200 but rm(prior)=100
    ])
    out = _assert_parity(rows, entry_ts=0, backstop_ts=1, entry_price=100.0,
                         target_level=None, stop_level=None, exit_cfg=cfg)
    assert out == {"exit_ts": 1, "exit_price": 195.0, "exit_reason": "OPTION_SIGNAL_EXIT"}
