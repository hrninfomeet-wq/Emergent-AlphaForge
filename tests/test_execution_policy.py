"""Sim↔live execution-policy PARITY tests (app/execution_policy.py).

These are golden invariants, not unit tests of one function: they replay the
same inputs through the BACKTEST path and the LIVE path and assert identical
outcomes. If any of these fail, live trades exit under different rules than
the backtests that justified deploying them — the worst silent bug this app
can have. Found and fixed at extraction time (2026-06-13): both live tick
deciders checked the TARGET first while the whole sim stack is pessimistic
STOP-FIRST.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.execution_policy import (  # noqa: E402
    resolve_premium_levels,
    spot_mirror_exit_reason,
    spot_mirror_levels,
    tick_exit_reason,
)
from app.exit_engine import intrabar_exit  # noqa: E402
from app.option_backtest import _resolve_option_levels  # noqa: E402
from app.paper_auto import compute_auto_risk_levels, compute_spot_exit_levels, spot_exit_reason  # noqa: E402
from app.paper_trading import risk_exit_reason  # noqa: E402


# ---------------------------------------------------------------------------
# Premium LEVELS: sim resolver ≡ policy ≡ live resolver
# ---------------------------------------------------------------------------

LEVEL_CASES = [
    # (entry, target_pts, stop_pts, target_pct, stop_pct)
    (150.0, 40, 30, None, None),
    (150.0, None, None, 65.0, 25.0),
    (150.0, 40, None, 65.0, 25.0),   # pts beat pct per leg
    (150.0, None, 30, None, None),   # stop only
    (150.0, 40, None, None, None),   # target only
    (150.0, 0, 0, 0, 0),             # zeros = unset
    (7.0, None, 30, None, None),     # stop would go negative -> floor
]


def test_sim_resolver_equals_policy_floor_zero():
    for entry, tp, sp, tpct, spct in LEVEL_CASES:
        sim = _resolve_option_levels(entry, target_pts=tp, stop_pts=sp, target_pct=tpct, stop_pct=spct)
        stop, target = resolve_premium_levels(
            entry, target_pts=tp, stop_pts=sp, target_pct=tpct, stop_pct=spct, stop_floor=0.0)
        assert sim["target_level"] == target, (entry, tp, tpct)
        assert sim["stop_level"] == stop, (entry, sp, spct)


def test_live_resolver_equals_policy_floor_tick():
    # Deployment-fallback inputs only (no strategy hints) — the live resolver
    # must produce exactly the policy levels with the Rs 0.05 floor + 2dp.
    for entry, tp, sp, tpct, spct in LEVEL_CASES:
        dep = {"auto_paper_target_pts": tp, "auto_paper_stop_pts": sp,
               "auto_paper_target_pct": tpct, "auto_paper_stop_pct": spct}
        live_stop, live_target = compute_auto_risk_levels(entry, None, dep)
        stop, target = resolve_premium_levels(
            entry, target_pts=tp, stop_pts=sp, target_pct=tpct, stop_pct=spct,
            stop_floor=0.05, ndigits=2)
        assert live_target == target, (entry, tp, tpct)
        assert live_stop == stop, (entry, sp, spct)


def test_sim_and_live_levels_agree_outside_the_floor_zone():
    # Same inputs through both paths: identical except the documented floor
    # (sim 0.0 vs live 0.05) and live 2dp rounding.
    entry, tp, sp = 150.0, 40, 30
    sim = _resolve_option_levels(entry, target_pts=tp, stop_pts=sp, target_pct=None, stop_pct=None)
    live_stop, live_target = compute_auto_risk_levels(
        entry, None, {"auto_paper_target_pts": tp, "auto_paper_stop_pts": sp})
    assert live_target == round(sim["target_level"], 2)
    assert live_stop == round(sim["stop_level"], 2)


# ---------------------------------------------------------------------------
# Premium DECISION: live tick ≡ degenerate sim bar
# ---------------------------------------------------------------------------

def _sim_reason(price, stop, target, is_long=True):
    _level, reason = intrabar_exit(high=price, low=price, stop=stop, target=target, is_long=is_long)
    return {"STOP": "stop_hit", "TARGET": "target_hit", None: None}[reason]


def test_live_premium_decision_equals_sim_for_price_grid():
    stop, target = 120.0, 190.0
    for price in (50.0, 119.99, 120.0, 120.01, 150.0, 189.99, 190.0, 250.0):
        trade = {"risk": {"stop_price": stop, "target_price": target}}
        assert risk_exit_reason(trade, price) == _sim_reason(price, stop, target), price


def test_live_premium_decision_is_stop_first_in_degenerate_config():
    # stop == target == price: both satisfied by one tick. The sim books the
    # pessimistic STOP; live must match (the old code booked target_hit).
    trade = {"risk": {"stop_price": 150.0, "target_price": 150.0}}
    assert risk_exit_reason(trade, 150.0) == "stop_hit"
    assert _sim_reason(150.0, 150.0, 150.0) == "stop_hit"


def test_live_premium_decision_handles_missing_levels():
    assert risk_exit_reason({"risk": {}}, 100.0) is None
    assert risk_exit_reason({"risk": {"stop_price": "", "target_price": None}}, 100.0) is None
    assert risk_exit_reason({"risk": {"stop_price": 90.0}}, 80.0) == "stop_hit"
    assert risk_exit_reason({"risk": {"target_price": 110.0}}, 120.0) == "target_hit"


# ---------------------------------------------------------------------------
# Spot-mirror LEVELS: live ≡ the backtest spot engine's formulas
# ---------------------------------------------------------------------------

def test_spot_mirror_levels_match_backtest_formulas_both_directions():
    entry, tgt, stp = 23500.0, 161.36, 96.30
    # backtest.py: CE -> stop = entry - stp, target = entry + tgt; PE mirrored.
    ce = spot_mirror_levels("CE", entry, target_pts=tgt, stop_pts=stp)
    assert ce["spot_target"] == round(entry + tgt, 2)
    assert ce["spot_stop"] == round(entry - stp, 2)
    pe = spot_mirror_levels("PE", entry, target_pts=tgt, stop_pts=stp)
    assert pe["spot_target"] == round(entry - tgt, 2)
    assert pe["spot_stop"] == round(entry + stp, 2)


def test_compute_spot_exit_levels_delegates_same_math():
    sig = {"risk_hints": {"spot_target_pts": 161.36, "spot_stop_pts": 96.30},
           "entry_price": 23500.0, "direction": "PE", "instrument": "NIFTY"}
    doc = compute_spot_exit_levels(sig)
    assert doc["spot_target"] == round(23500.0 - 161.36, 2)
    assert doc["spot_stop"] == round(23500.0 + 96.30, 2)


# ---------------------------------------------------------------------------
# Spot-mirror DECISION: live tick ≡ degenerate sim bar (is_long = CE)
# ---------------------------------------------------------------------------

def _sim_spot_reason(direction, price, spot_stop, spot_target):
    _level, reason = intrabar_exit(
        high=price, low=price, stop=spot_stop, target=spot_target,
        is_long=(direction == "CE"))
    return {"STOP": "spot_stop_hit", "TARGET": "spot_target_hit", None: None}[reason]


def test_live_spot_decision_equals_sim_for_both_directions():
    ce = {"direction": "CE", "spot_target": 23661.36, "spot_stop": 23403.70}
    pe = {"direction": "PE", "spot_target": 23338.64, "spot_stop": 23596.30}
    for price in (23300.0, 23403.70, 23500.0, 23596.30, 23661.36, 23700.0):
        assert spot_exit_reason(ce, price) == _sim_spot_reason("CE", price, ce["spot_stop"], ce["spot_target"]), ("CE", price)
        assert spot_exit_reason(pe, price) == _sim_spot_reason("PE", price, pe["spot_stop"], pe["spot_target"]), ("PE", price)


def test_live_spot_decision_is_stop_first_in_degenerate_config():
    levels = {"direction": "CE", "spot_target": 23500.0, "spot_stop": 23500.0}
    assert spot_exit_reason(levels, 23500.0) == "spot_stop_hit"


def test_policy_edge_inputs():
    assert tick_exit_reason("garbage", stop=1, target=2) is None
    assert tick_exit_reason(100.0, stop=None, target=None) is None
    assert spot_mirror_exit_reason("XX", 100.0, spot_target=90.0, spot_stop=110.0) in (
        "spot_target_hit", "spot_stop_hit", None)  # unknown direction -> short semantics, defined behavior
    assert spot_mirror_levels("CE", 100.0)["spot_target"] is None
