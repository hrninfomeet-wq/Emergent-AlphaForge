"""The live guard must honour `risk.exit_controls` — paper and the sim already do.

`resolve_live_exit_plan` (auto_live.py:215-216) passes the deployment's NESTED
`risk.exit_controls` block verbatim as `levels["trail"]`::

    {enabled, unit, breakeven:{trigger,lock}, trailing:{activation,distance}}

`build_monitor_state` expected a FLAT trail::

    {mode, trigger, lock_profit, step, raise_by, gap, x, y}

No `mode` key means `mode = str(trail.get("mode") or "none")` resolves to "none",
every trail field lands as None, and NO error is raised because "none" is a legal
mode. The overlay was discarded in total silence, leaving only the
`_GUARD_DEFAULT_STOP_PCT = 50.0` floor as protection.

Meanwhile exit_controls.py opens with "THE single source both the sim
(option_backtest) and the live mark (paper_auto / deployment_kill_switch) call, so
they can never drift" — and live was the one caller that never called it.

REAL MONEY, 2026-08-14, deployment 314fb7e8 (New_Confluence_NIFTY), with
exit_controls {enabled, unit:"pts", breakeven:{trigger:10, lock:8}}:

* PAPER, entry 84.6954 -> paper_trades.risk.stop_price 92.70 (= entry+8, the
  breakeven lock, ratcheted) -> realized +Rs 4,882.69.
* LIVE, NIFTY 24300 PE, ref 61.35 / fill 61.70, qty 65 -> trail dropped. The real
  27,173-tick path peaked at 72.00 at 10:33:28 IST (trigger 71.70), so the stop
  should have ratcheted to 69.70 and exited ~10:33:46 for +Rs 520 gross. It did
  not; the premium decayed to 36.30 by close (-Rs 1,651) and was closed by hand.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.auto_live import resolve_live_exit_plan  # noqa: E402
from app.live.live_sl_monitor import build_monitor_state, evaluate_exit  # noqa: E402

# The REAL deployment risk block and the REAL signal risk_hints.
DEPLOYMENT = {"risk": {
    "exit_controls": {"enabled": True, "unit": "pts",
                      "breakeven": {"trigger": 10, "lock": 8}, "trailing": None},
    "daily_caps": None, "default_lots": 10, "auto_paper": True,
}}
SIGNAL = {"risk_hints": {"target_pct": None, "stop_pct": None,
                         "spot_target_pts": 161.35554902405147,
                         "spot_stop_pts": 96.29802628652973,
                         "time_stop_minutes": None},
          "direction": "PE", "entry_price": 24323.75}

FILL, QTY = 61.70, 65


def _armed_state(entry=FILL):
    lv = resolve_live_exit_plan(SIGNAL, DEPLOYMENT)["levels"]
    return build_monitor_state(entry, stop_pct=lv.get("stop_pct"),
                               stop_pts=lv.get("stop_pts"),
                               target_pct=lv.get("target_pct"),
                               target_pts=lv.get("target_pts"),
                               trail=lv.get("trail"))


def _drive(state, path):
    """Feed a premium path; return (exit_result_or_None, final_state)."""
    for lp in path:
        res = evaluate_exit(state, lp)
        state = res["state"]
        if res["exit"]:
            return res, state
    return None, state


def test_the_deep_default_stop_is_still_seeded():
    """Guard the premise: no premium target, and the 50% floor is the base stop."""
    lv = resolve_live_exit_plan(SIGNAL, DEPLOYMENT)["levels"]
    assert lv["stop_pct"] == 50.0
    assert lv["target_pct"] is None and lv["target_pts"] is None
    st = _armed_state()
    assert st["initial_stop"] == 30.85          # 61.70 * 0.50
    assert st["target_level"] is None


def test_the_overlay_survives_the_arm():
    """The nested block must reach the state, not be silently dropped."""
    st = _armed_state()
    assert st.get("exit_controls"), "risk.exit_controls was discarded at arm time"
    assert st["exit_controls"]["unit"] == "pts"
    assert st["exit_controls"]["be_trigger"] == 10.0
    assert st["exit_controls"]["be_lock"] == 8.0


def test_the_2026_08_14_stop_ratchets_to_the_breakeven_lock():
    """THE regression. Peak 72.00 >= trigger 71.70 means stop must become 69.70.

    The ratchet consumes the PREVIOUS observation's peak, so it arms on the tick
    AFTER the peak crosses. That is not a lag to fix: paper_auto.py:874 reads the
    same `rmax_prev` "look-ahead parity with the sim", and matching it exactly is
    the entire point of this change."""
    st = _armed_state()
    assert st["stop_level"] == 30.85
    _, st = _drive(st, [61.70, 65.0, 71.55, 72.0, 71.9])
    assert st["stop_level"] == 69.70, (
        "stop stayed at {0} — the breakeven lock never armed".format(st["stop_level"]))


def test_the_position_then_exits_in_profit():
    st = _armed_state()
    res, st = _drive(st, [61.70, 72.0, 71.9, 69.25, 60.0, 36.30])
    assert res is not None, "no exit fired across the real path shape"
    assert st["stop_level"] == 69.70
    assert (69.70 - FILL) * QTY == pytest.approx(520.0)


def test_the_trigger_must_actually_be_reached():
    """72.00 armed it; 71.55 must not. Off-by-one here is a real-money error."""
    st = _armed_state()
    _, st = _drive(st, [61.70, 71.55, 71.6])
    assert st["stop_level"] == 30.85, "armed below the trigger"


def test_a_deployment_with_no_exit_controls_is_untouched():
    """Byte-identical for every deployment that configures no overlay."""
    st = build_monitor_state(FILL, stop_pct=50.0)
    assert "exit_controls" not in st, "an absent overlay must not add state"
    _, st = _drive(st, [61.70, 90.0, 120.0])
    assert st["stop_level"] == 30.85


def test_a_disabled_overlay_is_untouched():
    dep = {"risk": {"exit_controls": {"enabled": False, "unit": "pts",
                                      "breakeven": {"trigger": 10, "lock": 8}}}}
    lv = resolve_live_exit_plan(SIGNAL, dep)["levels"]
    st = build_monitor_state(FILL, stop_pct=lv["stop_pct"], trail=lv.get("trail"))
    assert "exit_controls" not in st
    _, st = _drive(st, [61.70, 120.0])
    assert st["stop_level"] == 30.85


def test_a_flat_trail_dict_still_works():
    """The other producers (stepped_xy rehydrate, manual single-shot) pass FLAT
    dicts. A flat dict has no `enabled` key, so it must not be read as an overlay."""
    st = build_monitor_state(100.0, stop_pct=50.0,
                             trail={"mode": "lock", "trigger": 110.0,
                                    "lock_profit": 5.0})
    assert "exit_controls" not in st
    _, st = _drive(st, [100.0, 110.0])
    assert st["stop_level"] == 105.0


def test_the_overlay_can_never_lower_a_live_stop():
    """_raise_stop is the sole writer and is a monotonic ratchet."""
    st = _armed_state()
    _, st = _drive(st, [61.70, 72.0, 71.9])
    assert st["stop_level"] == 69.70
    _, st = _drive(st, [70.0, 71.0])            # peak recedes; stop must hold
    assert st["stop_level"] == 69.70


def test_pct_unit_is_a_fraction_not_a_percent():
    """exit_controls.py:80 uses e * (1.0 + be_trigger), so 0.2 means +20%.
    The SENSEX deployment ships {unit:"pct", trigger:0.2, lock:0.05}."""
    dep = {"risk": {"exit_controls": {"enabled": True, "unit": "pct",
                                      "breakeven": {"trigger": 0.2, "lock": 0.05}}}}
    lv = resolve_live_exit_plan(SIGNAL, dep)["levels"]
    st = build_monitor_state(100.0, stop_pct=lv["stop_pct"], trail=lv.get("trail"))
    _, st = _drive(st, [100.0, 119.0, 119.5])
    assert st["stop_level"] == 50.0, "armed below a +20% trigger"
    _, st = _drive(st, [120.0, 119.0])
    assert st["stop_level"] == 105.0, "lock must be entry * 1.05"


def test_breakeven_and_trailing_compose():
    """The flat schema dispatches ONE mode and provably cannot express both.
    effective_premium_stop returns max(base, breakeven?, trailing?)."""
    dep = {"risk": {"exit_controls": {
        "enabled": True, "unit": "pts",
        "breakeven": {"trigger": 10, "lock": 8},
        "trailing": {"activation": 20, "distance": 5}}}}
    lv = resolve_live_exit_plan(SIGNAL, dep)["levels"]
    st = build_monitor_state(100.0, stop_pct=50.0, trail=lv.get("trail"))
    _, st = _drive(st, [100.0, 112.0, 111.0])   # breakeven only: 108
    assert st["stop_level"] == 108.0
    _, st = _drive(st, [130.0, 129.0])          # trailing now wins: 130 - 5
    assert st["stop_level"] == 125.0
