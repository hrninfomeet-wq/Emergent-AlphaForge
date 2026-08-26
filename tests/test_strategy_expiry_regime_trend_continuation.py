"""Tests for the `expiry_regime_trend_continuation` research plugin.

The properties pinned here are the ones this repo has already been burned by:
session anchors read through too small a live window (`fc424a1`), "cannot verify"
paths that fail OPEN instead of closed (`fa2b65d`), and look-ahead hidden inside a
whole-frame precompute.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategies.base import build_eval_ctx  # noqa: E402
from app.strategies.plugins.expiry_regime_trend_continuation import (  # noqa: E402
    _CLOSE_LOCATION_MIN,
    _CTX_KEY,
    _LAST_ELIGIBLE_MIN,
    ExpiryRegimeTrendContinuation,
    _close_location,
    _minutes_of,
)

STRAT = ExpiryRegimeTrendContinuation()
DEFAULTS = {k: v["default"] for k, v in STRAT.parameter_schema.items()}


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------

def _bar(date, hhmm, close, *, high=None, low=None, vwap=None, atr=2.0, open_=None):
    high = close + 0.5 if high is None else high
    low = close - 0.5 if low is None else low
    return {
        "session_date": date, "ist_time": hhmm,
        "open": close if open_ is None else open_,
        "high": high, "low": low, "close": close,
        "vwap": close if vwap is None else vwap, "atr": atr,
    }


def _prior_session(date="2026-08-20", close=100.0):
    """A minimal prior session that only has to supply a final close."""
    return [_bar(date, "15:29", close)]


def _opening_range(date, *, high=101.0, low=99.0):
    """The exact 30 bars 09:15-09:44, bounded by (low, high)."""
    rows = []
    for m in range(15, 45):
        rows.append(_bar(date, f"09:{m:02d}", 100.0, high=high, low=low))
    return rows


def _signal_bar(date, hhmm, *, close=105.0, atr=2.0, vwap=100.0, span=4.0,
                location=1.0):
    """A bar engineered to satisfy (or miss) the confirmation terms.

    `span` is the bar's own high-low range; `location` is where the close sits
    within it, measured from the CE side (1.0 = closed at the high).
    """
    low = close - span * location
    high = low + span
    return _bar(date, hhmm, close, high=high, low=low, vwap=vwap, atr=atr)


def _run(rows, params=None):
    """Precompute + evaluate every row; return the list of (index, Signal) fired."""
    df = pd.DataFrame(rows)
    p = {**DEFAULTS, **(params or {})}
    extras = STRAT.session_precompute(df, p)
    fired = []
    for i in range(len(df)):
        ctx = build_eval_ctx(
            history_df=df, i=i, instrument="NIFTY",
            session_date=str(df.iloc[i]["session_date"]), session_extras=extras,
        )
        prev = df.iloc[i - 1] if i > 0 else df.iloc[i]
        sig = STRAT.evaluate(df.iloc[i], prev, p, ctx)
        if sig.direction != "NONE":
            fired.append((i, sig))
    return fired


def _valid_ce_day(date="2026-08-21", signal_time="10:00"):
    """A session that should produce exactly one CE signal."""
    return (
        _prior_session(close=100.0)
        + _opening_range(date, high=101.0, low=99.0)
        + [_signal_bar(date, signal_time, close=105.0, vwap=100.0)]
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_minutes_of_parses_and_rejects():
    assert _minutes_of("09:15") == 555
    assert _minutes_of("14:48") == _LAST_ELIGIBLE_MIN
    for bad in (None, "", "9:15", "abcde", "xx:yy"):
        assert _minutes_of(bad) is None


def test_close_location_is_measured_from_the_signal_side():
    # Bar 10-14, closing at 13.
    assert _close_location(14.0, 10.0, 13.0, "CE") == pytest.approx(0.75)
    assert _close_location(14.0, 10.0, 13.0, "PE") == pytest.approx(0.25)


def test_close_location_none_on_a_zero_range_bar():
    """CAS-frozen index bars are exactly this shape and must not signal."""
    assert _close_location(10.0, 10.0, 10.0, "CE") is None


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

def test_a_clean_trend_day_fires_exactly_one_ce_signal():
    fired = _run(_valid_ce_day())
    assert len(fired) == 1
    i, sig = fired[0]
    assert sig.direction == "CE"
    assert sig.score == 65
    assert sig.spot_stop_pts > 0
    assert sig.spot_target_pts == pytest.approx(sig.spot_stop_pts * DEFAULTS["target_mult"])
    assert sig.time_stop_minutes == DEFAULTS["hold_max_minutes"]
    assert len(sig.reasons) == 4          # break + VWAP + prior close + bar quality


def test_a_clean_pe_day_fires_pe():
    date = "2026-08-21"
    rows = (
        _prior_session(close=100.0)
        + _opening_range(date, high=101.0, low=99.0)
        # Breaks BELOW the range low, below VWAP and below the prior close,
        # closing at the bar's low (location 0.0 from CE side == 1.0 from PE).
        + [_signal_bar(date, "10:00", close=95.0, vwap=100.0, location=0.0)]
    )
    fired = _run(rows)
    assert len(fired) == 1
    assert fired[0][1].direction == "PE"


def test_only_the_first_qualifying_bar_signals():
    date = "2026-08-21"
    rows = (
        _prior_session(close=100.0)
        + _opening_range(date, high=101.0, low=99.0)
        + [_signal_bar(date, "10:00", close=105.0, vwap=100.0),
           _signal_bar(date, "10:01", close=106.0, vwap=100.0),
           _signal_bar(date, "10:02", close=107.0, vwap=100.0)]
    )
    fired = _run(rows)
    assert len(fired) == 1
    assert fired[0][0] == 31              # prior bar + 30 opening bars -> index 31


# ---------------------------------------------------------------------------
# Three-way agreement — each leg must be independently necessary
# ---------------------------------------------------------------------------

def test_break_without_vwap_agreement_is_rejected():
    date = "2026-08-21"
    rows = (
        _prior_session(close=100.0)
        + _opening_range(date, high=101.0, low=99.0)
        # Breaks the range but closes BELOW VWAP.
        + [_signal_bar(date, "10:00", close=105.0, vwap=110.0)]
    )
    assert _run(rows) == []


def test_break_without_prior_close_agreement_is_rejected():
    date = "2026-08-21"
    rows = (
        _prior_session(close=200.0)      # prior close ABOVE the breakout close
        + _opening_range(date, high=101.0, low=99.0)
        + [_signal_bar(date, "10:00", close=105.0, vwap=100.0)]
    )
    assert _run(rows) == []


def test_no_break_is_rejected_even_with_everything_else_agreeing():
    date = "2026-08-21"
    rows = (
        _prior_session(close=99.0)
        + _opening_range(date, high=101.0, low=99.0)
        # Inside the opening range.
        + [_signal_bar(date, "10:00", close=100.5, vwap=99.5)]
    )
    assert _run(rows) == []


# ---------------------------------------------------------------------------
# Bar-quality confirmation
# ---------------------------------------------------------------------------

def test_a_narrow_break_bar_is_rejected():
    date = "2026-08-21"
    rows = (
        _prior_session(close=100.0)
        + _opening_range(date, high=101.0, low=99.0)
        # span 1.0 against ATR 2.0 and range_mult 1.2 -> needs >= 2.4
        + [_signal_bar(date, "10:00", close=105.0, vwap=100.0, span=1.0, atr=2.0)]
    )
    assert _run(rows) == []


def test_an_indecisive_close_is_rejected():
    date = "2026-08-21"
    rows = (
        _prior_session(close=100.0)
        + _opening_range(date, high=101.0, low=99.0)
        # Wide bar, but closes mid-range (location 0.5 < 0.65).
        + [_signal_bar(date, "10:00", close=105.0, vwap=100.0, span=4.0, location=0.5)]
    )
    assert _run(rows) == []


@pytest.mark.parametrize("location,fires", [
    (0.6875, True),    # 11/16 — just above the 0.65 gate
    (0.625, False),    # 5/8   — just below it
])
def test_the_close_location_gate_bites_at_the_right_place(location, fires):
    """Both values are exactly representable in binary.

    Testing the gate at exactly 0.65 would be measuring float rounding, not
    behaviour: 0.65 is not representable, so the reconstructed ratio lands a few
    ulps either side of it depending on how the bar was built.
    """
    date = "2026-08-21"
    rows = (
        _prior_session(close=100.0)
        + _opening_range(date, high=101.0, low=99.0)
        + [_signal_bar(date, "10:00", close=105.0, vwap=100.0, span=4.0,
                       location=location)]
    )
    assert (len(_run(rows)) == 1) is fires
    assert 0.625 < _CLOSE_LOCATION_MIN < 0.6875


# ---------------------------------------------------------------------------
# Time windows
# ---------------------------------------------------------------------------

def test_a_bar_before_0945_cannot_signal():
    date = "2026-08-21"
    rows = (
        _prior_session(close=100.0)
        + _opening_range(date, high=101.0, low=99.0)
        + [_signal_bar(date, "09:44", close=105.0, vwap=100.0)]
    )
    # 09:44 is inside the opening range itself and is never scanned.
    assert _run(rows) == []


def test_the_tunable_cutoff_shortens_the_window():
    rows = _valid_ce_day(signal_time="13:00")
    assert len(_run(rows)) == 1                                   # default 13:30
    # 09:15 + 180 = 12:15, so a 13:00 signal is now outside.
    assert _run(rows, {"entry_cutoff_minutes_after_open": 180}) == []


def test_the_cutoff_cannot_be_pushed_past_the_live_1450_block():
    """A parameter may shorten the entry window; it may never extend it.

    The live evaluator blocks entries from 14:50 and that constant is not
    per-deployment, so a signal after 14:48 would be untradeable — a backtest
    counting it would be measuring trades that cannot exist.
    """
    rows = _valid_ce_day(signal_time="14:49")
    # Even at the schema maximum (333 == 14:48), 14:49 must not fire.
    assert _run(rows, {"entry_cutoff_minutes_after_open": 333}) == []
    assert STRAT.parameter_schema["entry_cutoff_minutes_after_open"]["max"] == 333


@pytest.mark.parametrize("out_of_schema_cutoff", [400, 600, 10_000])
def test_an_out_of_schema_cutoff_is_still_clamped_to_1448(out_of_schema_cutoff):
    """The cap must hold for params that never passed schema validation.

    Found by mutation: removing `min(..., _LAST_ELIGIBLE_MIN)` from the clamp
    survived the whole suite, because the only test used cutoff 333 — which
    EQUALS the cap, making the min() a no-op. The clamp only bites on a value
    above the schema maximum, and nothing exercised that.

    It is not a hypothetical path. Params reach a strategy as stored dicts from
    saved presets and pinned deployment snapshots, and this repo has already
    shipped a fix (`56bc3a9`) for a schema narrowing that broke saved presets —
    i.e. stored params outliving the range that produced them is a real state.
    If one leaked through, the strategy would emit signals after 14:48 that the
    live evaluator refuses, which is exactly the backtest-counts-untradeable-
    signals divergence the parity register warns about.
    """
    rows = _valid_ce_day(signal_time="14:49")
    assert _run(rows, {"entry_cutoff_minutes_after_open": out_of_schema_cutoff}) == []


def test_an_out_of_schema_cutoff_still_admits_a_1448_signal():
    """Clamping must pin the boundary at 14:48, not collapse the window."""
    rows = _valid_ce_day(signal_time="14:48")
    assert len(_run(rows, {"entry_cutoff_minutes_after_open": 10_000})) == 1


def test_a_negative_cutoff_cannot_reopen_the_window():
    """The other end of the clamp: max(0, ...) must not wrap into a huge bound."""
    rows = _valid_ce_day(signal_time="10:00")
    assert _run(rows, {"entry_cutoff_minutes_after_open": -500}) == []


def test_1448_fires_at_the_widest_permitted_cutoff():
    """14:48 is reachable, but only when the cutoff is opened to its maximum.

    At the 13:30 default it is correctly outside the window — the parameter is
    what moves the boundary, and it tops out exactly at 14:48.
    """
    rows = _valid_ce_day(signal_time="14:48")
    assert _run(rows) == []                                        # 13:30 default
    assert len(_run(rows, {"entry_cutoff_minutes_after_open": 333})) == 1


# ---------------------------------------------------------------------------
# Fail-closed behaviour — every "cannot verify" path returns DENY
# ---------------------------------------------------------------------------

def test_missing_prior_session_close_fails_closed():
    date = "2026-08-21"
    rows = _opening_range(date, high=101.0, low=99.0) + [
        _signal_bar(date, "10:00", close=105.0, vwap=100.0)
    ]
    assert _run(rows) == []                 # no prior session at all


def test_an_incomplete_opening_range_fails_closed():
    date = "2026-08-21"
    partial = _opening_range(date)[:-1]      # 29 bars, not 30
    rows = _prior_session(close=100.0) + partial + [
        _signal_bar(date, "10:00", close=105.0, vwap=100.0)
    ]
    assert _run(rows) == []


def test_a_gapped_opening_range_fails_closed():
    """30 bars, but not the exact 09:15-09:44 labels.

    This is the live-window hazard: a rolling window that has lost 09:15 would
    otherwise rebuild a different 'opening' range and diverge from the backtest.
    """
    date = "2026-08-21"
    shifted = [_bar(date, f"09:{m:02d}", 100.0, high=101.0, low=99.0)
               for m in range(16, 46)]      # 09:16-09:45
    rows = _prior_session(close=100.0) + shifted + [
        _signal_bar(date, "10:00", close=105.0, vwap=100.0)
    ]
    assert _run(rows) == []


def test_a_nan_indicator_on_the_signal_bar_fails_closed():
    date = "2026-08-21"
    bad = _signal_bar(date, "10:00", close=105.0, vwap=100.0)
    bad["atr"] = float("nan")
    rows = _prior_session(close=100.0) + _opening_range(date) + [bad]
    assert _run(rows) == []


def test_a_zero_atr_fails_closed():
    date = "2026-08-21"
    rows = _prior_session(close=100.0) + _opening_range(date) + [
        _signal_bar(date, "10:00", close=105.0, vwap=100.0, atr=0.0)
    ]
    assert _run(rows) == []


def test_a_threshold_above_the_fixed_score_suppresses_everything():
    assert _run(_valid_ce_day(), {"signal_threshold": 90}) == []


def test_a_nonpositive_hold_is_refused():
    fired = _run(_valid_ce_day(), {"hold_max_minutes": 0})
    assert fired == []


# ---------------------------------------------------------------------------
# Stop sizing — the scale-free property the brief requires
# ---------------------------------------------------------------------------

def test_the_stop_is_the_larger_of_the_bps_floor_and_the_atr_term():
    date = "2026-08-21"
    # Quiet session: ATR 0.1 -> atr term 0.08; bps floor on close 105 -> 0.0525.
    quiet = _prior_session(close=100.0) + _opening_range(date) + [
        _signal_bar(date, "10:00", close=105.0, vwap=100.0, atr=0.1, span=1.0)
    ]
    sig = _run(quiet)[0][1]
    assert sig.spot_stop_pts == pytest.approx(0.08)          # ATR term wins

    # Volatile session: ATR 20 -> atr term 16; bps floor 0.0525.
    loud = _prior_session(close=100.0) + _opening_range(date) + [
        _signal_bar(date, "10:00", close=105.0, vwap=100.0, atr=20.0, span=40.0)
    ]
    assert _run(loud)[0][1].spot_stop_pts == pytest.approx(16.0)


def test_stop_scales_with_price_so_nifty_and_sensex_do_not_share_a_threshold():
    """The same bps parameter must produce a proportionally larger SENSEX stop.

    NIFTY ~24,500 and SENSEX ~81,000 do not share a point scale; the brief
    forbids reusing a fixed threshold across them.
    """
    def stop_at(price):
        date = "2026-08-21"
        rows = (
            [_bar("2026-08-20", "15:29", price * 0.95)]
            + [_bar(date, f"09:{m:02d}", price * 0.98,
                    high=price * 0.99, low=price * 0.97) for m in range(15, 45)]
            + [_signal_bar(date, "10:00", close=price, vwap=price * 0.98,
                           atr=0.0001, span=price * 0.01)]
        )
        return _run(rows)[0][1].spot_stop_pts

    nifty = stop_at(24_500.0)
    sensex = stop_at(81_000.0)
    assert sensex / nifty == pytest.approx(81_000.0 / 24_500.0, rel=1e-6)
    assert nifty == pytest.approx(24_500.0 * 5.0 / 10_000.0)     # 12.25 pts
    # Both clear the ~4 bps intrabar ambiguity floor (~10 NIFTY / ~32 SENSEX pts).
    assert nifty >= 24_500.0 * 4.0 / 10_000.0
    assert sensex >= 81_000.0 * 4.0 / 10_000.0


def test_the_bps_schema_minimum_sits_at_the_ambiguity_floor():
    """A search must not be able to tune the stop below where backtest and live
    stop agreeing about which level filled first."""
    assert STRAT.parameter_schema["stop_bps"]["min"] == 4.0


# ---------------------------------------------------------------------------
# Look-ahead safety — the whole-frame precompute must not see the future
# ---------------------------------------------------------------------------

def test_precompute_on_a_prefix_picks_the_same_bar_as_the_full_frame():
    """The property that makes the whole-frame scan legitimate.

    Evaluated over any prefix that reaches the signal bar, the chosen index must
    be identical to the one chosen with the rest of the session visible.
    """
    date = "2026-08-21"
    rows = (
        _prior_session(close=100.0)
        + _opening_range(date, high=101.0, low=99.0)
        + [_signal_bar(date, "10:00", close=105.0, vwap=100.0)]
        + [_signal_bar(date, f"10:{m:02d}", close=110.0, vwap=100.0)
           for m in range(1, 20)]
    )
    full = pd.DataFrame(rows)
    full_i = STRAT.session_precompute(full, DEFAULTS)[_CTX_KEY][date]["first_signal_i"]

    prefix = pd.DataFrame(rows[:32])       # ends exactly on the signal bar
    prefix_i = STRAT.session_precompute(prefix, DEFAULTS)[_CTX_KEY][date]["first_signal_i"]

    assert full_i == prefix_i == 31


def test_a_prefix_ending_before_the_signal_selects_nothing():
    date = "2026-08-21"
    rows = _prior_session(close=100.0) + _opening_range(date) + [
        _signal_bar(date, "10:00", close=105.0, vwap=100.0)
    ]
    prefix = pd.DataFrame(rows[:31])       # stops one bar short
    info = STRAT.session_precompute(prefix, DEFAULTS)[_CTX_KEY].get(date)
    assert info is not None
    assert info["first_signal_i"] is None


# ---------------------------------------------------------------------------
# Contract / registration
# ---------------------------------------------------------------------------

def test_the_live_window_covers_the_session_anchors():
    """400, for the reason nine shipped strategies had to be fixed in fc424a1."""
    assert STRAT.live_lookback_bars == 400


def test_scope_is_nifty_and_sensex_only():
    assert STRAT.supported_instruments == ["NIFTY", "SENSEX"]
    assert STRAT.supported_timeframes == ["1m"]


def test_dte_is_not_a_strategy_parameter():
    """DTE targeting belongs to the run's dte_filter / the option policy.

    Expiry weekdays rotated twice; a strategy deriving DTE would be silently
    wrong across 2024-2025.
    """
    keys = " ".join(STRAT.parameter_schema).lower()
    assert "dte" not in keys
    assert "expiry" not in keys
    assert "moneyness" not in keys


def test_the_plugin_is_discovered_by_the_registry():
    from app.strategies.base import get_registry

    registry = get_registry()
    registry.auto_discover()
    discovered = registry.get("expiry_regime_trend_continuation")
    assert discovered is not None
    assert discovered.version == "1.0.0"
    assert registry.origin_of("expiry_regime_trend_continuation") == "custom"


def test_every_schema_default_is_inside_its_own_bounds():
    for name, spec in STRAT.parameter_schema.items():
        assert spec["min"] <= spec["default"] <= spec["max"], name


# ---------------------------------------------------------------------------
# Multi-entry: max_trades_per_session + cooldown
#
# B originally locked onto the session's FIRST qualifying bar and ignored every
# later one, so the engine's `daily_caps.max_trades` could only ever cap a
# budget of 1. These pin the knob that replaces that behaviour. The default
# MUST stay 1 so the pre-registered single-entry spec is still reachable, and
# every later entry must still obey the cutoff and the per-session reset.
# ---------------------------------------------------------------------------

def _multi_session(date, times, *, close=105.0):
    """Opening range then a qualifying signal bar at each of `times`.

    `close` matters when two of these are chained: a session's last close
    becomes the next session's prior close, and entry needs close > prior close.
    """
    rows = _opening_range(date)
    for hhmm in times:
        rows.append(_signal_bar(date, hhmm, close=close))
    return rows


def test_default_max_trades_is_one_so_the_frozen_spec_is_unchanged():
    assert STRAT.parameter_schema["max_trades_per_session"]["default"] == 1
    rows = _prior_session() + _multi_session("2026-08-21", ["09:45", "10:30", "11:30"])
    assert len(_run(rows)) == 1


def test_max_trades_per_session_admits_exactly_that_many_entries():
    rows = _prior_session() + _multi_session("2026-08-21", ["09:45", "10:30", "11:30"])
    fired = _run(rows, {"max_trades_per_session": 3, "signal_cooldown_bars": 1})
    assert len(fired) == 3
    assert [s.direction for _, s in fired] == ["CE", "CE", "CE"]


def test_cap_is_a_ceiling_not_a_target():
    rows = _prior_session() + _multi_session(
        "2026-08-21", ["09:45", "10:00", "10:30", "11:00", "11:30"])
    fired = _run(rows, {"max_trades_per_session": 2, "signal_cooldown_bars": 1})
    assert len(fired) == 2


def test_cooldown_suppresses_a_qualifying_bar_that_is_too_close():
    # Two qualifying bars one minute apart; a 30-bar cooldown must drop the second.
    rows = _prior_session() + _multi_session("2026-08-21", ["09:45", "09:46"])
    fired = _run(rows, {"max_trades_per_session": 5, "signal_cooldown_bars": 30})
    assert len(fired) == 1
    # Widening the budget without the cooldown lets both through, which proves
    # the cooldown - not the cap - is what suppressed it above.
    assert len(_run(rows, {"max_trades_per_session": 5,
                           "signal_cooldown_bars": 1})) == 2


def test_later_entries_still_obey_the_entry_cutoff():
    rows = _prior_session() + _multi_session("2026-08-21", ["09:45", "14:00"])
    # cutoff 30 min after open = 09:45, so the 14:00 bar is out of window.
    fired = _run(rows, {"max_trades_per_session": 5, "signal_cooldown_bars": 1,
                        "entry_cutoff_minutes_after_open": 30})
    assert len(fired) == 1


def test_later_entries_never_fire_past_the_hard_1448_cap():
    rows = _prior_session() + _multi_session("2026-08-21", ["09:45", "14:49", "15:10"])
    fired = _run(rows, {"max_trades_per_session": 5, "signal_cooldown_bars": 1,
                        "entry_cutoff_minutes_after_open": 333})
    assert len(fired) == 1
    assert all(_minutes_of(rows[i]["ist_time"]) <= _LAST_ELIGIBLE_MIN for i, _ in fired)


def test_the_budget_resets_each_session():
    # Session 2 must close ABOVE session 1's last close, or its own entries fail
    # the prior-close term and the test would pass for the wrong reason.
    rows = (_prior_session()
            + _multi_session("2026-08-21", ["09:45", "10:30"], close=105.0)
            + _multi_session("2026-08-24", ["09:45", "10:30"], close=110.0))
    fired = _run(rows, {"max_trades_per_session": 2, "signal_cooldown_bars": 1})
    assert len(fired) == 4
    # Two in each session, not four in one.
    assert len({str(rows[i]["session_date"]) for i, _ in fired}) == 2


def _filled_session(date, signal_times, *, close=105.0, end_min=12 * 60):
    """Opening range then EVERY minute bar to `end_min`, qualifying only at
    `signal_times`. Filler bars close inside the opening range, so they fail the
    break term and are skipped — which makes row spacing equal minute spacing.
    """
    rows = _opening_range(date)
    want = set(signal_times)
    for minute in range(9 * 60 + 45, end_min):
        hhmm = f"{minute // 60:02d}:{minute % 60:02d}"
        if hhmm in want:
            rows.append(_signal_bar(date, hhmm, close=close))
        else:
            rows.append(_bar(date, hhmm, 100.0, high=100.5, low=99.5))
    return rows


def _run_raw(rows, params):
    """Like `_run` but passes `params` VERBATIM — no defaults merged in.

    `_run` merges DEFAULTS, so it can never exercise an absent key. A preset or
    deployment saved before a knob existed has exactly that shape.
    """
    df = pd.DataFrame(rows)
    extras = STRAT.session_precompute(df, params)
    fired = []
    for i in range(len(df)):
        ctx = build_eval_ctx(history_df=df, i=i, instrument="NIFTY",
                             session_date=str(df.iloc[i]["session_date"]),
                             session_extras=extras)
        prev = df.iloc[i - 1] if i > 0 else df.iloc[i]
        if STRAT.evaluate(df.iloc[i], prev, params, ctx).direction != "NONE":
            fired.append(i)
    return fired


def test_a_preset_saved_before_this_knob_existed_still_takes_one_trade():
    """The absent-key fallback must be the frozen spec's 1, not an open budget.

    Caught by mutation: changing the `params.get(...)` fallback from 1 to 99 left
    every other test in this module green, because they all merge DEFAULTS and
    so always supply the key. A stale preset would have silently taken 99 entries
    a session.
    """
    # Spaced with real filler bars so the DEFAULT cooldown cannot be what
    # suppresses the extra entries — otherwise this test passes for the wrong
    # reason and the mutant survives (it did, first time round).
    rows = _prior_session() + _filled_session("2026-08-21", ["09:45", "10:30", "11:30"])
    legacy = {k: v for k, v in DEFAULTS.items()
              if k not in ("max_trades_per_session", "signal_cooldown_bars")}
    assert "max_trades_per_session" not in legacy
    assert len(_run_raw(rows, legacy)) == 1
    # Control: the SAME frame with an explicit budget of 3 fires three times, so
    # the spacing genuinely is not the limiting factor above.
    assert len(_run_raw(rows, {**DEFAULTS, "max_trades_per_session": 3})) == 3


def test_multi_entry_precompute_is_still_look_ahead_safe():
    """Every admitted entry must be identical on a prefix ending at that entry.

    The single-entry version was safe because the scan stopped at the first
    match. With a budget > 1 the argument has to hold per entry, so it is
    re-pinned here rather than assumed to carry over.
    """
    date = "2026-08-21"
    rows = _prior_session() + _multi_session(date, ["09:45", "10:30", "11:30"])
    p = {**DEFAULTS, "max_trades_per_session": 3, "signal_cooldown_bars": 1}
    full = pd.DataFrame(rows)
    full_signals = STRAT.session_precompute(full, p)[_CTX_KEY][date]["signals"]
    assert len(full_signals) == 3

    for pos in sorted(full_signals):
        prefix = pd.DataFrame(rows[: pos + 1])
        prefix_signals = STRAT.session_precompute(prefix, p)[_CTX_KEY][date]["signals"]
        # The entries decided at or before `pos` are bit-identical, and no later
        # entry has been invented from data the prefix cannot see.
        expected = {k: v for k, v in full_signals.items() if k <= pos}
        assert prefix_signals == expected
