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
    sig = validate_signal(strat.evaluate(row, row, _params(strat), {}))
    assert sig.direction == "NONE" and sig.blockers


def test_zero_atr_is_treated_as_warmup_not_a_divide_by_zero(strat):
    row = _row(close=24500, vwap=24400, atr=0.0)
    sig = validate_signal(strat.evaluate(row, row, _params(strat), {}))
    assert sig.direction == "NONE" and sig.blockers


# ----------------------------------------------------------------- routing --

def test_momentum_family_buys_the_breakout_side(strat):
    p = _params(strat, entry_family=MOMENTUM, band_atr_mult=1.0)
    up = _row(close=24530, vwap=24500, atr=20.0)     # +1.5 ATR above VWAP
    dn = _row(close=24470, vwap=24500, atr=20.0)
    assert validate_signal(strat.evaluate(up, up, p, {})).direction == "CE"
    assert validate_signal(strat.evaluate(dn, dn, p, {})).direction == "PE"


def test_fade_family_is_the_exact_inverse_of_momentum(strat):
    up = _row(close=24530, vwap=24500, atr=20.0)
    mom = strat.evaluate(up, up, _params(strat, entry_family=MOMENTUM), {})
    fade = strat.evaluate(up, up, _params(strat, entry_family=FADE), {})
    assert mom.direction == "CE" and fade.direction == "PE"


def test_inside_the_band_nothing_fires(strat):
    p = _params(strat, entry_family=MOMENTUM, band_atr_mult=1.0)
    flat = _row(close=24505, vwap=24500, atr=20.0)   # +0.25 ATR, inside band
    assert validate_signal(strat.evaluate(flat, flat, p, {})).direction == "NONE"


def test_expansion_family_requires_volatility_expansion(strat):
    p = _params(strat, entry_family=EXPANSION, expansion_ratio=1.5,
                band_atr_mult=1.0)
    # decisive bullish bar, but ATR is NOT expanded -> no trade
    quiet = _row(close=24540, vwap=24500, atr=20.0, atr_avg=20.0,
                 open_=24521, high=24540, low=24520)
    assert validate_signal(strat.evaluate(quiet, quiet, p, {})).direction == "NONE"
    # same bar with ATR 2x its average -> fires
    loud = _row(close=24540, vwap=24500, atr=40.0, atr_avg=20.0,
                open_=24521, high=24540, low=24520)
    assert validate_signal(strat.evaluate(loud, loud, p, {})).direction == "CE"


def test_weekday_mask_blocks_excluded_days(strat):
    # 2026-08-10 is a Monday (bit 0). Mask 0b11110 excludes Monday.
    row = _row(close=24530, vwap=24500, atr=20.0, session_date="2026-08-10")
    blocked = validate_signal(strat.evaluate(row, row, _params(strat, weekday_mask=0b11110), {}))
    allowed = validate_signal(strat.evaluate(row, row, _params(strat, weekday_mask=0b11111), {}))
    assert blocked.direction == "NONE" and blocked.blockers
    assert allowed.direction == "CE"


# ------------------------------------------------------------------- exits --

def test_stop_respects_the_basis_point_floor_when_atr_is_tiny(strat):
    """The tick study showed backtest/live diverge on stops inside ~10 NIFTY
    points. min_stop_bps enforces that floor in a scale-free way."""
    p = _params(strat, entry_family=MOMENTUM, stop_atr_mult=0.5,
                min_stop_bps=4.0, band_atr_mult=1.0)
    row = _row(close=24000, vwap=23990, atr=2.0)     # 0.5*ATR = 1pt, floor = 9.6pt
    sig = validate_signal(strat.evaluate(row, row, p, {}))
    assert sig.direction == "CE"
    assert sig.spot_stop_pts == pytest.approx(24000 * 4.0 / 10_000)


def test_target_always_exceeds_stop(strat):
    p = _params(strat, entry_family=MOMENTUM, stop_atr_mult=6.0,
                target_atr_mult=1.0, band_atr_mult=1.0)
    row = _row(close=24500, vwap=24400, atr=20.0)
    sig = validate_signal(strat.evaluate(row, row, p, {}))
    assert sig.direction == "CE"
    assert sig.spot_target_pts > sig.spot_stop_pts


def test_signal_numerics_are_always_finite(strat):
    """validate_signal is the shared runtime gate for backtest/paper/live;
    a NaN would bypass thresholds silently."""
    for family in (MOMENTUM, FADE, EXPANSION):
        row = _row(close=24540, vwap=24500, atr=40.0, atr_avg=20.0,
                   open_=24521, high=24540, low=24520)
        sig = validate_signal(strat.evaluate(row, row, _params(strat, entry_family=family), {}))
        for f in ("spot_target_pts", "spot_stop_pts", "time_stop_minutes"):
            v = getattr(sig, f)
            assert v is None or math.isfinite(float(v))


# ------------------------------------------------------- THE key property --

@pytest.mark.parametrize("family", [MOMENTUM, FADE, EXPANSION])
def test_identical_params_transfer_across_index_scales(strat, family):
    """SENSEX trades at ~3.3x NIFTY's level. With every threshold in ATR units,
    the SAME parameter set must produce the SAME direction and the SAME
    RELATIVE exit geometry on both. This is the trap that killed earlier
    absolute-point strategies."""
    p = _params(strat, entry_family=family, expansion_ratio=1.5)
    scale = 80_000 / 24_000

    nifty = _row(close=24_000 * 1.002, vwap=24_000, atr=24_000 * 0.001,
                 atr_avg=24_000 * 0.0005, open_=24_000 * 1.0012,
                 high=24_000 * 1.002, low=24_000 * 1.0011)
    sensex = _row(close=24_000 * 1.002 * scale, vwap=24_000 * scale,
                  atr=24_000 * 0.001 * scale, atr_avg=24_000 * 0.0005 * scale,
                  open_=24_000 * 1.0012 * scale, high=24_000 * 1.002 * scale,
                  low=24_000 * 1.0011 * scale)

    sn = validate_signal(strat.evaluate(nifty, nifty, p, {}))
    ss = validate_signal(strat.evaluate(sensex, sensex, p, {}))

    assert sn.direction == ss.direction != "NONE"
    assert sn.score == ss.score
    # exits must scale linearly with the index level
    assert ss.spot_stop_pts == pytest.approx(sn.spot_stop_pts * scale, rel=1e-6)
    assert ss.spot_target_pts == pytest.approx(sn.spot_target_pts * scale, rel=1e-6)
