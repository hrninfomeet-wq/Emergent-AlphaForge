"""Tests for the screen CLI's pure helpers (`backend/scripts/screen_option_buying.py`).

The script's I/O needs a live warehouse, but its arithmetic does not, and the
arithmetic is where a silent error would corrupt every cell it prints. `pymongo`
is stubbed so the module imports on a host that has no driver installed.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _load_script_module():
    """Import the CLI with `pymongo` stubbed out ONLY if it is genuinely absent.

    The stub is installed into `sys.modules` permanently and never removed, so on
    a host that HAS the driver it would shadow the real one for every test that
    runs afterwards — `app.upstox_stream` imports `UpdateOne` from it and would
    fail with a bare `cannot import name ... (unknown location)`. Whether that
    happened depended purely on which test file pytest collected first, which is
    exactly the kind of order-dependent flake that is miserable to diagnose
    later. Prefer the real module whenever it can be imported.
    """
    if "pymongo" not in sys.modules:
        try:
            import pymongo  # noqa: F401
        except ImportError:
            stub = types.ModuleType("pymongo")
            stub.MongoClient = object      # never constructed in these tests
            sys.modules["pymongo"] = stub
    path = ROOT / "backend" / "scripts" / "screen_option_buying.py"
    spec = importlib.util.spec_from_file_location("screen_option_buying", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


screen = _load_script_module()


# ---------------------------------------------------------------------------
# ATM strike rounding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spot,step,expected", [
    (24_512.0, 50, 24_500),
    (24_526.0, 50, 24_550),
    (24_525.0, 50, 24_500),      # banker's rounding at the exact midpoint
    (81_240.0, 100, 81_200),
    (81_260.0, 100, 81_300),
])
def test_atm_strike_rounds_to_the_instrument_step(spot, step, expected):
    assert screen._atm_strike(spot, step) == expected


def test_nifty_and_sensex_use_different_steps():
    """The register's rule: never reuse a fixed threshold across the two indices."""
    from app.instruments import UNDERLYING_META
    assert UNDERLYING_META["NIFTY"]["strike_step"] == 50
    assert UNDERLYING_META["SENSEX"]["strike_step"] == 100


# ---------------------------------------------------------------------------
# IST conversion
# ---------------------------------------------------------------------------

def test_ist_helpers_convert_from_utc_epoch_ms():
    # 2026-08-20 09:15 IST == 03:45 UTC
    ts = int(datetime(2026, 8, 20, 3, 45, tzinfo=timezone.utc).timestamp() * 1000)
    assert screen._ist_date(ts) == "2026-08-20"
    assert screen._ist_hhmm(ts) == "09:15"


def test_ist_date_rolls_correctly_near_utc_midnight():
    """An 18:45 UTC bar is already the NEXT IST day — a classic off-by-one."""
    ts = int(datetime(2026, 8, 20, 18, 45, tzinfo=timezone.utc).timestamp() * 1000)
    assert screen._ist_date(ts) == "2026-08-21"


# ---------------------------------------------------------------------------
# Statutory charge percentage
# ---------------------------------------------------------------------------

def test_charges_pct_is_positive_and_segment_aware():
    """SENSEX (BFO) carries a lower exchange transaction rate than NIFTY (NFO).

    Charging the NSE rate on a SENSEX trade over-states that component; the
    screen must not do it.
    """
    nifty = screen.charges_pct_for("NIFTY", premium=140.0, lot_size=65)
    sensex = screen.charges_pct_for("SENSEX", premium=140.0, lot_size=20)
    assert nifty > 0 and sensex > 0
    assert sensex < nifty


def test_statutory_charges_are_premium_invariant_on_a_zero_brokerage_broker():
    """With Flattrade's Rs 0 F&O brokerage, EVERY charge is turnover-proportional.

    So the statutory drag is a flat ~0.186% of turnover whether the premium is
    Rs 10 or Rs 400. This matters for where the 0DTE penalty actually comes from:
    NOT from charges. It comes from theta and from the spread's points floor
    (see the two tests below). Getting this backwards would have the screen
    attribute the 0DTE bleed to the wrong term and "fix" it by tuning costs.
    """
    pcts = [screen.charges_pct_for("NIFTY", premium=p, lot_size=65)
            for p in (10.0, 36.6, 140.0, 400.0)]
    assert all(0.185 < p < 0.187 for p in pcts), pcts
    # Flat to within per-component rupee rounding, not economically varying.
    assert max(pcts) - min(pcts) < 0.001


def test_a_per_order_brokerage_would_make_cheap_premiums_uneconomic():
    """The counterfactual that justifies staying on a zero-brokerage broker.

    At Rs 20/order, a Rs 10 premium pays ~7.4% round-trip in brokerage alone —
    more than the entire measured 5-minute favourable move. This is a standing
    argument against ever moving this strategy family to a per-order broker.
    """
    from app.option_costs import CostConfig, round_trip_charges

    cfg = CostConfig(enabled=True, brokerage_per_order=20.0)
    qty = 65

    def pct(premium: float) -> float:
        ch = round_trip_charges(entry_premium=premium, exit_premium=premium,
                                quantity=qty, cfg=cfg)
        return 100.0 * ch["total_charges"] / (premium * qty)

    assert pct(10.0) > 7.0
    assert pct(400.0) < 0.5
    assert pct(10.0) > pct(400.0) * 10        # strongly premium-dependent


def test_the_spread_points_floor_is_what_punishes_cheap_premium():
    """The premium-DEPENDENT friction term, and the real 0DTE cost driver.

    A 0.5-point floor is 5% per side of a Rs 10 premium but only 0.36% of a
    Rs 140 one. Cheap 0DTE options are expensive to trade for this reason, not
    because statutory charges scale.
    """
    from app.option_costs import CostConfig, spread_pts_for_premium

    cfg = CostConfig(enabled=True, spread_pct_of_premium=1.0, spread_min_pts=0.5)
    cheap_pct = 100.0 * spread_pts_for_premium(10.0, cfg) / 10.0
    rich_pct = 100.0 * spread_pts_for_premium(140.0, cfg) / 140.0
    assert cheap_pct == pytest.approx(5.0)
    assert rich_pct == pytest.approx(1.0)     # the % term dominates once premium is large
    assert cheap_pct > rich_pct


def test_charges_pct_survives_a_degenerate_premium():
    assert screen.charges_pct_for("NIFTY", premium=0.0, lot_size=65) >= 0.0


# ---------------------------------------------------------------------------
# Defaults that encode a decision
# ---------------------------------------------------------------------------

def test_default_dte_excludes_0dte():
    """0DTE must be asked for explicitly — it is the worst measured day to buy."""
    parser_defaults = {a.dest: a.default for a in _build_parser()._actions}
    assert parser_defaults["dte"] == [1, 2, 3]


def test_default_entry_window_stops_before_the_live_cutoff():
    """14:48 is the last bar whose decision lands before the hard 14:50 live block."""
    parser_defaults = {a.dest: a.default for a in _build_parser()._actions}
    assert parser_defaults["entry_from"] == "09:25"
    assert parser_defaults["entry_to"] == "14:48"


def _build_parser():
    """Re-create the CLI parser without running main()."""
    import argparse
    from app.instruments import INSTRUMENT_KEYS
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="NIFTY", choices=sorted(INSTRUMENT_KEYS))
    ap.add_argument("--dte", nargs="*", type=int, default=[1, 2, 3])
    ap.add_argument("--entry-from", default="09:25")
    ap.add_argument("--entry-to", default="14:48")
    return ap


def test_the_shipped_parser_matches_this_expectation():
    """Guard against the CLI drifting away from the defaults asserted above.

    The window bounds used to be literals here ("09:25" / "14:48"). They are now
    read from `app.entry_window`, so the assertion moved from the source text to
    the resolved value — a literal check would have passed happily while the CLI
    screened a different window from the one live enforces, which is register
    item #6 wearing a different hat.
    """
    from app.entry_window import DEFAULT_ENTRY_END, DEFAULT_ENTRY_START

    source = (ROOT / "backend" / "scripts" / "screen_option_buying.py").read_text()
    assert 'default=[1, 2, 3]' in source
    assert '"09:25"' not in source, "the entry window must not be re-hardcoded"
    assert '"14:48"' not in source, "the 14:48 literal is superseded by DEFAULT_ENTRY_END"

    defaults = {a.dest: a.default for a in screen.build_arg_parser()._actions}
    assert defaults["entry_from"] == DEFAULT_ENTRY_START
    assert defaults["entry_to"] == DEFAULT_ENTRY_END


# ---------------------------------------------------------------------------
# Register item #7 — the entry window must constrain what is MEASURED
#
# `--entry-from` / `--entry-to` chose the session's ATM strike and nothing else.
# The option frame was then fetched for the WHOLE day, so 13.6% of measured
# entry bars sat outside the window (09:15-09:24 and 14:49-15:29) — and outside
# the window live will actually take. The unconditioned baseline barely moved
# (<= 0.010 MFE/MAE), which is exactly why it survived unnoticed; a CONDITIONED
# cell that fires at the open would have been scored on entries live refuses.
# ---------------------------------------------------------------------------

def test_entry_window_mask_selects_only_bars_inside_the_window():
    import pandas as pd
    frame = pd.DataFrame({"ist": ["09:14", "09:15", "09:25", "12:00",
                                  "14:49", "14:50", "15:29"]})
    mask = screen.entry_window_mask(frame, "09:25", "14:50")
    assert list(mask) == [False, False, True, True, True, False, False]


def test_the_window_is_half_open_like_every_other_window_in_the_app():
    """`backtest._in_window` is `start <= ist < end` and the live evaluator
    blocks at `>= end`. A screen using `<=` on the end would measure one bar the
    other two refuse — the same one-bar-at-a-time drift item #6 was about."""
    import pandas as pd
    frame = pd.DataFrame({"ist": ["14:49", "14:50"]})
    assert list(screen.entry_window_mask(frame, "09:25", "14:50")) == [True, False]


def test_the_screen_defaults_to_the_same_window_as_live_and_backtest():
    from app.entry_window import DEFAULT_ENTRY_END, DEFAULT_ENTRY_START

    parser = screen.build_arg_parser()
    defaults = {a.dest: a.default for a in parser._actions}
    assert defaults["entry_from"] == DEFAULT_ENTRY_START
    assert defaults["entry_to"] == DEFAULT_ENTRY_END


def test_a_frame_without_an_ist_column_is_refused_not_silently_unmasked():
    """Returning all-True on a missing column would restore the exact defect
    this fixes, invisibly."""
    import pandas as pd
    with pytest.raises(ValueError):
        screen.entry_window_mask(pd.DataFrame({"close": [1.0]}), "09:25", "14:50")
