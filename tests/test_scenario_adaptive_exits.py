import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.scenarios import exit_plan
from app.exit_controls_level import level_exit_decision
from app.exit_engine import intrabar_exit

def test_trend_continuation_is_let_run_target_no_level():
    p = exit_plan("TREND_CONTINUATION", {"atr": 40.0, "open": 24000.0}, params={})
    assert p["spot_target_level"] is None
    assert p["spot_target_pts"] >= 90 and p["spot_stop_pts"] > 0 and p["trail"] is True

def test_volatile_fade_targets_the_open_level():
    p = exit_plan("VOLATILE_FADE", {"atr": 40.0, "open": 24000.0}, params={})
    assert p["spot_target_level"] == 24000.0 and p["trail"] is False

def test_chop_is_small_scalp():
    p = exit_plan("CHOP", {"atr": 40.0, "open": 24000.0}, params={})
    assert p["spot_target_level"] is None and p["spot_target_pts"] < 90

def test_none_returns_no_trade_plan():
    assert exit_plan("NONE", {"atr": 40.0, "open": 24000.0}, params={}) is None

def test_unknown_scenario_returns_none():
    assert exit_plan("SOMETHING_NEW", {"atr": 40.0, "open": 24000.0}, params={}) is None

def test_params_override_drives_magnitudes():
    # The P4 optimizer tunes these magnitudes; the dispatcher MUST honor them.
    p = exit_plan("TREND_CONTINUATION", {"atr": 10.0, "open": 24000.0},
                  params={"trend_target_atr": 6.0, "trend_stop_atr": 1.0})
    assert p["spot_target_pts"] == 60.0 and p["spot_stop_pts"] == 10.0

def test_volatile_fade_missing_open_yields_none_level():
    p = exit_plan("VOLATILE_FADE", {"atr": 40.0}, params={})  # no "open" in ctx
    assert p["spot_target_level"] is None and p["trail"] is False


# --------------------------------------------------------------------------
# (a) Delegation / parity: level_exit_decision is a thin wrapper around
# intrabar_exit and must return EXACTLY what intrabar_exit returns.
# --------------------------------------------------------------------------
def test_level_exit_delegates_long_target_hit():
    bar = dict(high=135.0, low=99.0)
    got = level_exit_decision(stop=95.0, level_target=130.0, is_long=True, **bar)
    want = intrabar_exit(stop=95.0, target=130.0, is_long=True, **bar)
    assert got == want == (130.0, "TARGET")


def test_level_exit_delegates_long_stop_hit():
    bar = dict(high=101.0, low=90.0)
    got = level_exit_decision(stop=95.0, level_target=130.0, is_long=True, **bar)
    want = intrabar_exit(stop=95.0, target=130.0, is_long=True, **bar)
    assert got == want == (95.0, "STOP")


def test_level_exit_delegates_long_no_hit():
    bar = dict(high=120.0, low=98.0)
    got = level_exit_decision(stop=95.0, level_target=130.0, is_long=True, **bar)
    want = intrabar_exit(stop=95.0, target=130.0, is_long=True, **bar)
    assert got == want == (None, None)


def test_level_exit_delegates_short_target_hit():
    bar = dict(high=101.0, low=68.0)
    got = level_exit_decision(stop=105.0, level_target=70.0, is_long=False, **bar)
    want = intrabar_exit(stop=105.0, target=70.0, is_long=False, **bar)
    assert got == want == (70.0, "TARGET")


def test_level_exit_delegates_short_stop_hit():
    bar = dict(high=106.0, low=99.0)
    got = level_exit_decision(stop=105.0, level_target=70.0, is_long=False, **bar)
    want = intrabar_exit(stop=105.0, target=70.0, is_long=False, **bar)
    assert got == want == (105.0, "STOP")


def test_level_exit_delegates_short_no_hit():
    bar = dict(high=102.0, low=80.0)
    got = level_exit_decision(stop=105.0, level_target=70.0, is_long=False, **bar)
    want = intrabar_exit(stop=105.0, target=70.0, is_long=False, **bar)
    assert got == want == (None, None)


# --------------------------------------------------------------------------
# (b) Points-vs-level equivalence: a points-target tgt_p resolved to an
# ABSOLUTE target == entry +/- tgt_p must produce the IDENTICAL fill as a
# level-target set to that same absolute price. This is the core claim that
# lets the backtest swap `target = entry +/- pts` for `target = level`.
# --------------------------------------------------------------------------
def test_points_vs_level_equivalence_long():
    entry, tgt_p, stop = 100.0, 30.0, 95.0
    target_from_pts = entry + tgt_p   # 130
    level_target = 130.0
    bar = dict(high=135.0, low=99.0)
    from_pts = intrabar_exit(stop=stop, target=target_from_pts, is_long=True, **bar)
    from_level = level_exit_decision(stop=stop, level_target=level_target, is_long=True, **bar)
    assert from_pts == from_level == (130.0, "TARGET")


def test_points_vs_level_equivalence_short():
    entry, tgt_p, stop = 100.0, 30.0, 105.0
    target_from_pts = entry - tgt_p   # 70
    level_target = 70.0
    bar = dict(high=101.0, low=68.0)
    from_pts = intrabar_exit(stop=stop, target=target_from_pts, is_long=False, **bar)
    from_level = level_exit_decision(stop=stop, level_target=level_target, is_long=False, **bar)
    assert from_pts == from_level == (70.0, "TARGET")


# --------------------------------------------------------------------------
# (c) Stop-first still holds for level targets: a bar that touches BOTH the
# stop and the level must fill at the STOP (stop_first=True default), and the
# points-expression and level-expression must agree.
# --------------------------------------------------------------------------
def test_stop_first_holds_for_level_long():
    entry, tgt_p, stop = 100.0, 30.0, 95.0
    bar = dict(high=135.0, low=90.0)  # hits stop (90<=95) AND target (135>=130)
    from_pts = intrabar_exit(stop=stop, target=entry + tgt_p, is_long=True, **bar)
    from_level = level_exit_decision(stop=stop, level_target=130.0, is_long=True, **bar)
    assert from_pts == from_level == (95.0, "STOP")


def test_stop_first_holds_for_level_short():
    entry, tgt_p, stop = 100.0, 30.0, 105.0
    bar = dict(high=106.0, low=68.0)  # hits stop (106>=105) AND target (68<=70)
    from_pts = intrabar_exit(stop=stop, target=entry - tgt_p, is_long=False, **bar)
    from_level = level_exit_decision(stop=stop, level_target=70.0, is_long=False, **bar)
    assert from_pts == from_level == (105.0, "STOP")


# --------------------------------------------------------------------------
# (d) Integration: the new backtest branch actually REPLACES the points-target
# with the absolute level end-to-end. We craft prices so that WITHOUT the level
# branch the trade would never hit the points-target (130) on the trigger bar,
# but WITH it the trade exits at the level (110) on that bar.
# --------------------------------------------------------------------------
def test_backtest_branch_replaces_points_with_level():
    import numpy as np
    import pandas as pd
    from app.backtest import run_backtest
    from app.strategies.base import StrategyBase, Signal

    LEVEL = 110.0  # below the points-target (entry 100 + 30 = 130), reachable below

    class _LevelStub(StrategyBase):
        id = "_level_stub"

        def __init__(self):
            self._fired = False

        def evaluate(self, row, prev, params, ctx):
            # Fire CE exactly once, on the first eligible (in-window) bar.
            if not self._fired:
                self._fired = True
                return Signal(direction="CE", score=70, spot_target_level=LEVEL)
            return Signal(direction="NONE")

    # Price path: entry bar closes at 100. The NEXT bar's high reaches 115 --
    # enough to hit the level (110) but NOT the points-target (130) and the low
    # stays above the stop (entry 100 - 15 = 85). So WITHOUT the level branch
    # this bar produces no exit; WITH it the trade fills at exactly 110 (TARGET).
    n = 60
    closes = np.full(n, 100.0)
    df = pd.DataFrame({
        "ts": np.arange(n, dtype="int64") * 60_000,
        "open": closes.copy(),
        "high": closes + 0.5,
        "low": closes - 0.5,
        "close": closes.copy(),
        "volume": np.zeros(n),
    })
    # Strategy fires on bar index 1 (first in-window bar of the loop, which
    # starts at i=1); entry_price = close of bar 1 = 100.0.
    # Make bar index 2 reach exactly the level high without hitting points-target.
    df.loc[2, "high"] = 115.0   # >= 110 level, < 130 points-target
    df.loc[2, "low"] = 99.0     # > 85 stop -> no stop hit
    # In-window times so entries are allowed (09:25 <= ist < 15:00).
    df["ist_time"] = ["09:%02d" % min(25 + i, 59) for i in range(n)]
    df["datetime"] = df["ist_time"]

    res = run_backtest(df, _LevelStub(), params={}, costs_enabled=False)
    trades = res["trades"]
    assert len(trades) >= 1
    t = trades[0]
    assert t["direction"] == "CE"
    assert t["entry_price"] == 100.0
    # The load-bearing assertion: exit is the LEVEL (110), not entry+points (130),
    # and not entry/EOD. Reason is TARGET because the level was hit intrabar.
    assert t["exit_reason"] == "TARGET"
    assert t["exit_price"] == LEVEL
    # Sanity: 110 is strictly below the points-target 130, proving the branch
    # did NOT use the default points target.
    assert t["exit_price"] < 100.0 + 30.0
