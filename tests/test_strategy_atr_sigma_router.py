"""Contract + behaviour tests for the ATR Sigma Router plugin.

The load-bearing property is SCALE-FREENESS: every threshold is expressed in
ATR (or basis points of spot), so one parameter set must behave identically on
NIFTY (~24,500) and SENSEX (~80,000). The project has been bitten before by
absolute-point parameters that could not transfer between indices.
"""
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategies.base import validate_signal  # noqa: E402
from app.strategies.plugins.atr_sigma_router import ATRSigmaRouter  # noqa: E402

MOMENTUM, FADE, EXPANSION = 0, 1, 2


def _row(*, close, vwap, atr, atr_avg=None, ema9=None, ema21=None,
         open_=None, high=None, low=None, session_date="2026-08-10",
         ist_time="11:00"):
    """One enriched bar. Defaults keep every non-tested gate passing."""
    atr_avg = atr if atr_avg is None else atr_avg
    open_ = close if open_ is None else open_
    hi = max(close, open_) if high is None else high
    lo = min(close, open_) if low is None else low
    # EMA stack defaults agree with whichever side of VWAP the close sits on,
    # so the trend filter never accidentally vetoes an unrelated assertion.
    if ema9 is None:
        ema9 = vwap + (1.0 if close >= vwap else -1.0)
    if ema21 is None:
        ema21 = vwap
    return pd.Series({
        "open": open_, "high": hi, "low": lo, "close": close,
        "vwap": vwap, "atr": atr, "atr_avg": atr_avg,
        "ema9": ema9, "ema21": ema21,
        "session_date": session_date, "ist_time": ist_time,
    })


def _flat(row):
    """A previous bar that qualifies for NOTHING: sitting exactly on VWAP (inside
    any band) with ATR at its average (no expansion). The strategy is
    EDGE-triggered — it fires on the bar a setup first appears — so a `prev` that
    already qualifies correctly suppresses the signal. Passing `row` as its own
    `prev` therefore tests nothing.
    """
    vwap = float(row["vwap"])
    atr = float(row["atr"])
    return _row(close=vwap, vwap=vwap, atr=atr, atr_avg=atr,
                open_=vwap, high=vwap + atr * 0.1, low=vwap - atr * 0.1,
                session_date=str(row["session_date"]), ist_time=str(row["ist_time"]))


def _params(strategy, **over):
    p = strategy.default_params()
    p.update(over)
    return p


@pytest.fixture
def strat():
    return ATRSigmaRouter()


# ---------------------------------------------------------------- contract --

def test_plugin_metadata_contract(strat):
    meta = strat.meta()
    assert meta["id"] == "atr_sigma_router"
    assert meta["is_builtin"] is False, "custom plugins must not claim builtin"
    assert "1m" in meta["supported_timeframes"], "1m is required for deployment"
    assert meta["origin"] == "custom"


def test_every_schema_entry_is_optimizer_searchable(strat):
    """optimizer.py only searches int/float/bool and needs min/max on numerics.
    A string param would be silently dropped from the search space."""
    for name, info in strat.parameter_schema.items():
        assert info["type"] in ("int", "float", "bool"), f"{name} is not searchable"
        assert "default" in info, f"{name} has no default"
        if info["type"] in ("int", "float"):
            assert "min" in info and "max" in info, f"{name} lacks bounds"
            assert info["min"] <= info["default"] <= info["max"], f"{name} default out of bounds"


def test_entry_family_bounds_cover_exactly_the_three_families(strat):
    fam = strat.parameter_schema["entry_family"]
    assert (fam["min"], fam["max"]) == (0, 2)


# ------------------------------------------------------------------ guards --

def test_warmup_returns_none_with_blocker(strat):
    row = _row(close=24500, vwap=24500, atr=float("nan"))
    sig = validate_signal(strat.evaluate(row, _flat(row), _params(strat), {}))
    assert sig.direction == "NONE" and sig.blockers


def test_zero_atr_is_treated_as_warmup_not_a_divide_by_zero(strat):
    row = _row(close=24500, vwap=24400, atr=0.0)
    sig = validate_signal(strat.evaluate(row, _flat(row), _params(strat), {}))
    assert sig.direction == "NONE" and sig.blockers


# ----------------------------------------------------------------- routing --

def test_momentum_family_buys_the_breakout_side(strat):
    p = _params(strat, entry_family=MOMENTUM, band_atr_mult=1.0)
    up = _row(close=24530, vwap=24500, atr=20.0)     # +1.5 ATR above VWAP
    dn = _row(close=24470, vwap=24500, atr=20.0)
    assert validate_signal(strat.evaluate(up, _flat(up), p, {})).direction == "CE"
    assert validate_signal(strat.evaluate(dn, _flat(dn), p, {})).direction == "PE"


def test_fade_family_is_the_exact_inverse_of_momentum(strat):
    up = _row(close=24530, vwap=24500, atr=20.0)
    mom = strat.evaluate(up, _flat(up), _params(strat, entry_family=MOMENTUM), {})
    fade = strat.evaluate(up, _flat(up), _params(strat, entry_family=FADE), {})
    assert mom.direction == "CE" and fade.direction == "PE"


def test_inside_the_band_nothing_fires(strat):
    p = _params(strat, entry_family=MOMENTUM, band_atr_mult=1.0)
    flat = _row(close=24505, vwap=24500, atr=20.0)   # +0.25 ATR, inside band
    assert validate_signal(strat.evaluate(flat, _flat(flat), p, {})).direction == "NONE"


def test_expansion_family_requires_volatility_expansion(strat):
    p = _params(strat, entry_family=EXPANSION, expansion_ratio=1.5,
                band_atr_mult=1.0)
    # decisive bullish bar, but ATR is NOT expanded -> no trade
    quiet = _row(close=24540, vwap=24500, atr=20.0, atr_avg=20.0,
                 open_=24521, high=24540, low=24520)
    assert validate_signal(strat.evaluate(quiet, _flat(quiet), p, {})).direction == "NONE"
    # same bar with ATR 2x its average -> fires
    loud = _row(close=24560, vwap=24500, atr=40.0, atr_avg=20.0,
                open_=24541, high=24560, low=24540)   # dev 60 > band 40
    assert validate_signal(strat.evaluate(loud, _flat(loud), p, {})).direction == "CE"


def test_weekday_mask_blocks_excluded_days(strat):
    # 2026-08-10 is a Monday (bit 0). Mask 0b11110 excludes Monday.
    row = _row(close=24530, vwap=24500, atr=20.0, session_date="2026-08-10")
    blocked = validate_signal(strat.evaluate(row, _flat(row), _params(strat, weekday_mask=0b11110), {}))
    allowed = validate_signal(strat.evaluate(row, _flat(row), _params(strat, weekday_mask=0b11111), {}))
    assert blocked.direction == "NONE" and blocked.blockers
    assert allowed.direction == "CE"


# ------------------------------------------------------------------- exits --

def test_stop_respects_the_basis_point_floor_when_atr_is_tiny(strat):
    """The tick study showed backtest/live diverge on stops inside ~10 NIFTY
    points. min_stop_bps enforces that floor in a scale-free way."""
    p = _params(strat, entry_family=MOMENTUM, stop_atr_mult=0.5,
                min_stop_bps=4.0, band_atr_mult=1.0)
    row = _row(close=24000, vwap=23990, atr=2.0)     # 0.5*ATR = 1pt, floor = 9.6pt
    sig = validate_signal(strat.evaluate(row, _flat(row), p, {}))
    assert sig.direction == "CE"
    assert sig.spot_stop_pts == pytest.approx(24000 * 4.0 / 10_000)


def test_target_always_exceeds_stop(strat):
    p = _params(strat, entry_family=MOMENTUM, stop_atr_mult=6.0,
                target_atr_mult=1.0, band_atr_mult=1.0)
    row = _row(close=24500, vwap=24400, atr=20.0)
    sig = validate_signal(strat.evaluate(row, _flat(row), p, {}))
    assert sig.direction == "CE"
    assert sig.spot_target_pts > sig.spot_stop_pts


def test_signal_numerics_are_always_finite(strat):
    """validate_signal is the shared runtime gate for backtest/paper/live;
    a NaN would bypass thresholds silently."""
    for family in (MOMENTUM, FADE, EXPANSION):
        row = _row(close=24540, vwap=24500, atr=40.0, atr_avg=20.0,
                   open_=24521, high=24540, low=24520)
        sig = validate_signal(strat.evaluate(row, _flat(row), _params(strat, entry_family=family), {}))
        for f in ("spot_target_pts", "spot_stop_pts", "time_stop_minutes"):
            v = getattr(sig, f)
            assert v is None or math.isfinite(float(v))


# ------------------------------------------------------- THE key property --

# ------------------------------------------- regressions from the 2026-08-19 audit --

def test_live_lookback_covers_the_whole_session(strat):
    """session_vwap is grouped over ONLY the rows in the live window, so the VWAP
    anchor is the window start. At 200 bars the window stops reaching 09:15 after
    12:34 and the anchor error reached 2.12 ATR by 14:49 — larger than the default
    entry band, which silently flips momentum/fade signals."""
    assert strat.live_lookback_bars >= 400
    assert strat.meta()["live_lookback_bars"] == strat.live_lookback_bars


def test_edge_triggered_not_level_triggered(strat):
    """Backtest takes one trade then blocks on position-open + cooldown, but the
    live evaluator applies NEITHER. Level-triggering would emit on every bar the
    close sits outside the band — unbounded live exposure."""
    p = _params(strat, entry_family=MOMENTUM, band_atr_mult=1.0)
    bar = _row(close=24530, vwap=24500, atr=20.0)
    assert validate_signal(strat.evaluate(bar, _flat(bar), p, {})).direction == "CE"
    # same setup still active on the prior bar -> suppressed
    again = validate_signal(strat.evaluate(bar, bar, p, {}))
    assert again.direction == "NONE" and again.blockers == ["setup already active"]


def test_signal_threshold_is_enforced_by_the_strategy(strat):
    """The backtest engine gates on signal_threshold generically; the live
    evaluator does not. A tuned threshold must mean the same thing on both."""
    bar = _row(close=24530, vwap=24500, atr=20.0)
    assert validate_signal(strat.evaluate(bar, _flat(bar), _params(strat, signal_threshold=40), {})).direction == "CE"
    blocked = validate_signal(strat.evaluate(bar, _flat(bar), _params(strat, signal_threshold=90), {}))
    assert blocked.direction == "NONE" and blocked.blockers


def test_nan_atr_avg_falls_back_rather_than_poisoning_the_score(strat):
    """`atr_avg and atr/atr_avg or 1.0` looked like a guard but bool(nan) is True,
    so NaN propagated instead of falling back to 1.0."""
    p = _params(strat, entry_family=MOMENTUM, band_atr_mult=1.0)
    bar = _row(close=24530, vwap=24500, atr=20.0, atr_avg=float("nan"))
    sig = validate_signal(strat.evaluate(bar, _flat(bar), p, {}))
    assert sig.direction == "CE"
    assert math.isfinite(sig.score) and sig.score >= 60
    assert not any("nan" in r.lower() for r in sig.reasons)


# ------------------------------------------------- scale-freeness, done properly --

# The two legs share ATR-RELATIVE geometry but straddle any plausible ABSOLUTE
# threshold: deviation is 1.5 ATR on both, yet 15 index points on NIFTY versus 50
# on SENSEX. A hardcoded ~30-point band answers them differently; a ratio rule
# cannot. The earlier version of this test scaled one bar shape by a constant, so
# an absolute threshold was crossed at BOTH levels and the assertions passed for
# implementations carrying exactly the trap this plugin exists to avoid.
_SCALE = 80_000 / 24_000


def _leg(level, atr, *, dev_atr=1.5, expansion=2.0):
    vwap = level
    close = vwap + dev_atr * atr
    return _row(close=close, vwap=vwap, atr=atr, atr_avg=atr / expansion,
                open_=close - atr * 0.5, high=close, low=close - atr * 0.6)


def _nifty():
    return _leg(24_000.0, 10.0)          # dev =  15 index points


def _sensex():
    return _leg(80_000.0, 10.0 * _SCALE)  # dev =  50 index points, same 1.5 ATR


@pytest.mark.parametrize("family", [MOMENTUM, FADE, EXPANSION])
def test_identical_params_transfer_across_index_scales(strat, family):
    p = _params(strat, entry_family=family, expansion_ratio=1.5,
                stop_atr_mult=0.5, min_stop_bps=4.0)
    n, s = _nifty(), _sensex()
    sn = validate_signal(strat.evaluate(n, _flat(n), p, {}))
    ss = validate_signal(strat.evaluate(s, _flat(s), p, {}))

    assert sn.direction == ss.direction != "NONE"
    assert sn.score == ss.score
    assert ss.spot_stop_pts == pytest.approx(sn.spot_stop_pts * _SCALE, rel=1e-4)
    assert ss.spot_target_pts == pytest.approx(sn.spot_target_pts * _SCALE, rel=1e-4)
    # and the bps floor must be the binding constraint here, or the assertion
    # above never exercises the one genuinely spot-proportional knob.
    # The floor is a fraction of CLOSE, not of the VWAP level.
    assert sn.spot_stop_pts == pytest.approx(float(n["close"]) * 4.0 / 10_000, rel=1e-4)
    assert sn.spot_stop_pts > 0.5 * 10.0, "ATR term won; floor never exercised"


# Three ways to reintroduce the absolute-point trap. The transfer fixture above
# is only meaningful if it REJECTS all of them — an earlier version scaled one bar
# shape by a constant, so every mutant crossed its threshold at both index levels
# and passed. Each mutant here must fail on at least one transfer assertion.

class _MutantAbsoluteBand(ATRSigmaRouter):
    def _route(self, family, row, params, dev, band, atr):
        return super()._route(family, row, params, dev, 30.0, atr)


class _MutantAbsoluteStopFloor(ATRSigmaRouter):
    @staticmethod
    def _exits(close, atr, params):
        stop = max(float(params["stop_atr_mult"]) * atr, 9.8)   # fixed points
        return round(stop, 6), round(max(float(params["target_atr_mult"]) * atr,
                                         stop * 1.2), 6)


class _MutantAbsoluteScore(ATRSigmaRouter):
    def evaluate(self, row, prev, params, ctx):
        sig = super().evaluate(row, prev, params, ctx)
        if sig.direction in ("CE", "PE"):
            dev = abs(float(row["close"]) - float(row["vwap"]))
            sig.score = min(100, 60 + 5 * sum(1 for x in (20.0, 40.0) if dev >= x))
        return sig


def _transfer_holds(strategy, family):
    """The transfer assertions, run against an arbitrary implementation."""
    p = _params(strategy, entry_family=family, expansion_ratio=1.5,
                stop_atr_mult=0.5, min_stop_bps=4.5)
    n, s = _nifty(), _sensex()
    a = strategy.evaluate(n, _flat(n), p, {})
    b = strategy.evaluate(s, _flat(s), p, {})
    if a.direction != b.direction or a.direction == "NONE" or a.score != b.score:
        return False
    return (b.spot_stop_pts == pytest.approx(a.spot_stop_pts * _SCALE, rel=1e-4)
            and b.spot_target_pts == pytest.approx(a.spot_target_pts * _SCALE, rel=1e-4))


@pytest.mark.parametrize("mutant", [_MutantAbsoluteBand, _MutantAbsoluteStopFloor,
                                    _MutantAbsoluteScore],
                         ids=["band", "stop_floor", "score"])
@pytest.mark.parametrize("family", [MOMENTUM, FADE, EXPANSION])
def test_transfer_fixture_rejects_absolute_point_mutants(strat, mutant, family):
    assert _transfer_holds(strat, family), "the real implementation must transfer"
    assert not _transfer_holds(mutant(), family), (
        f"{mutant.__name__} reintroduces an absolute-point threshold and still "
        f"passes the transfer assertions on family={family} — the fixture is vacuous")


def test_edge_trigger_judges_prev_on_its_own_volatility(strat):
    """A rising ATR must not re-open the re-fire hole: prev was evaluated with the
    CURRENT bar's band, so bar1 (dev 30, atr 20) got re-tested against bar2's
    band of 40 and read as 'not active', firing twice."""
    p = _params(strat, entry_family=MOMENTUM, band_atr_mult=1.0)
    bar1 = _row(close=24530, vwap=24500, atr=20.0)      # dev 30 > band 20 -> active
    bar2 = _row(close=24550, vwap=24500, atr=40.0)      # dev 50 > band 40 -> active
    assert validate_signal(strat.evaluate(bar1, _flat(bar1), p, {})).direction == "CE"
    second = validate_signal(strat.evaluate(bar2, bar1, p, {}))
    assert second.direction == "NONE", "re-fired on a rising ATR"


def test_expansion_first_ignition_bar_is_not_suppressed(strat):
    """prev was scored as atr_CURRENT / atr_avg_PREV, back-dating today's
    expansion onto a quiet previous bar and suppressing the real ignition."""
    p = _params(strat, entry_family=EXPANSION, expansion_ratio=1.5)
    quiet = _row(close=24510, vwap=24500, atr=20.0, atr_avg=20.0,
                 open_=24501, high=24510, low=24500)     # own ratio 1.0, not an ignition
    ignite = _row(close=24560, vwap=24500, atr=40.0, atr_avg=20.0,
                  open_=24541, high=24560, low=24540)    # own ratio 2.0, dev 60 > band 40
    assert validate_signal(strat.evaluate(ignite, quiet, p, {})).direction == "CE"


def test_expansion_survives_a_prev_bar_missing_high_low(strat):
    """_route now also runs on prev; a bare row["high"] subscript raised KeyError
    out of evaluate()."""
    p = _params(strat, entry_family=EXPANSION, expansion_ratio=1.5)
    cur = _row(close=24560, vwap=24500, atr=40.0, atr_avg=20.0,
               open_=24541, high=24560, low=24540)
    bare = pd.Series({"close": 24500, "vwap": 24500, "atr": 40.0, "atr_avg": 20.0,
                      "session_date": "2026-08-10", "ist_time": "11:00"})
    assert validate_signal(strat.evaluate(cur, bare, p, {})).direction == "CE"


def test_infinite_high_cannot_emit_a_confident_signal(strat):
    """span = inf slipped past `if span <= 0`; position underflowed to 0.0 and
    emitted a confident PE."""
    p = _params(strat, entry_family=EXPANSION, expansion_ratio=1.5)
    bad = _row(close=24560, vwap=24500, atr=40.0, atr_avg=20.0,
               open_=24541, high=float("inf"), low=24540)
    assert validate_signal(strat.evaluate(bad, _flat(bad), p, {})).direction == "NONE"


def test_searchable_ranges_cannot_disable_the_safety_floor_or_no_op(strat):
    """min_stop_bps must not reach below the measured 4 bps ambiguity floor, and
    signal_threshold must not span values that are all no-op or all-blocking."""
    assert strat.parameter_schema["min_stop_bps"]["min"] >= 4.0
    thr = strat.parameter_schema["signal_threshold"]
    # Only the harmful end is trimmed: >85 blocks every trade. The 40-59 no-op
    # band stays so saved presets remain deployable — two real ones carried 54
    # and 50 and were rejected outright when the minimum was raised to 60.
    assert thr["max"] <= 85
    assert thr["min"] <= 50, "raising this minimum breaks previously-saved presets"


def test_expansion_reason_is_not_duplicated(strat):
    p = _params(strat, entry_family=EXPANSION, expansion_ratio=1.5)
    loud = _row(close=24580, vwap=24500, atr=60.0, atr_avg=20.0,
                open_=24561, high=24580, low=24560)      # ratio 3.0, dev 80 > band 60
    sig = validate_signal(strat.evaluate(loud, _flat(loud), p, {}))
    atr_reasons = [r for r in sig.reasons if "its average" in r]
    assert len(atr_reasons) == len(set(atr_reasons)) == 1


def test_band_atr_mult_is_live_for_every_family(strat):
    """It was read by MOMENTUM/FADE only, so a third of optimizer trials burned a
    continuous dimension producing one identical output for EXPANSION."""
    bar = _row(close=24560, vwap=24500, atr=40.0, atr_avg=20.0,
               open_=24541, high=24560, low=24540)
    for family in (MOMENTUM, FADE, EXPANSION):
        outs = set()
        for mult in (0.25, 1.0, 2.0, 4.0):
            p = _params(strat, entry_family=family, band_atr_mult=mult,
                        expansion_ratio=1.5)
            s = validate_signal(strat.evaluate(bar, _flat(bar), p, {}))
            outs.add((s.direction, s.score))
        assert len(outs) > 1, f"band_atr_mult is a dead dimension for family={family}"


def test_bps_floor_actually_clears_ten_nifty_points(strat):
    """4.0 bps on NIFTY 24,500 is 9.8 pts — under the >=10 the tick study
    requires. The schema minimum must deliver what the docstring claims."""
    p = _params(strat, entry_family=MOMENTUM,
                min_stop_bps=strat.parameter_schema["min_stop_bps"]["min"],
                stop_atr_mult=0.5, band_atr_mult=1.0)
    bar = _row(close=24500, vwap=24450, atr=25.0)
    sig = validate_signal(strat.evaluate(bar, _flat(bar), p, {}))
    assert sig.direction == "CE"
    assert sig.spot_stop_pts >= 10.0
