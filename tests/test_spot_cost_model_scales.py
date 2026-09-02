"""The spot round-trip cost must scale with the instrument's point scale.

THE BUG THIS PINS
-----------------
`cost_in_points` special-cased BANKNIFTY and returned NIFTY's 1.5 points for
EVERYTHING else — so SENSEX, the largest underlying in the warehouse, was charged
the smallest index's friction:

    NIFTY      1.5 pts @ ~24,468  = 0.00613% of index
    BANKNIFTY  4.0 pts @ ~55,734  = 0.00718% of index   ("higher absolute pts
                                                          for larger underlying")
    SENSEX     1.5 pts @ ~80,176  = 0.00187% of index   <-- 3.3x too cheap

The optimizer's SEARCH phase scores candidates on this spot P&L. Under-charging
SENSEX by 3.3x made sub-noise stops (5-9 index points, ~0.3x of one 1-minute bar
range) look profitable: the 2026-09-01 `explosive_reversal` SENSEX job reported
+9,085 spot points across 2,884 trades and every one of its 50 re-ranked
candidates turned out to be NEGATIVE once real option premiums were applied
(best -Rs 932,976). Re-priced at a scaled cost, all 50 flip to a loss in the spot
phase too — which is where the search would have rejected them.

The deeper defect was the SILENT FALLTHROUGH: an instrument absent from the table
got the cheapest schedule with no signal. That is a cost model failing OPEN.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.costs import (  # noqa: E402
    COST_MODEL_VERSION,
    SPOT_ROUND_TRIP_PTS_BY_INSTRUMENT,
    apply_round_trip_cost,
    cost_in_points,
)

# Median closes measured over the full warehouse on 2026-09-01.
MEDIAN_LEVEL = {"NIFTY": 24468.0, "BANKNIFTY": 55734.0, "SENSEX": 80176.0}


# --- existing instruments must not move (protects every stored result) -------

def test_nifty_cost_is_unchanged():
    """NIFTY's 49 optimizer jobs and 85 backtest runs must stay reproducible."""
    assert cost_in_points("NIFTY") == 1.5


def test_banknifty_cost_is_unchanged():
    assert cost_in_points("BANKNIFTY") == 4.0


# --- SENSEX is the fix ------------------------------------------------------

def test_sensex_no_longer_charged_niftys_cost():
    assert cost_in_points("SENSEX") != 1.5


def test_sensex_cost_is_scale_consistent_with_nifty():
    """SENSEX must pay at least NIFTY's RATE. BSE weeklies are documented as
    lower-depth than NIFTY weeklies (deployment_preflight.py), so its true rate
    is if anything higher — but it can never be lower."""
    nifty_rate = 1.5 / MEDIAN_LEVEL["NIFTY"]
    sensex_rate = cost_in_points("SENSEX") / MEDIAN_LEVEL["SENSEX"]
    assert sensex_rate >= nifty_rate * 0.98
    # ...and not absurdly above the widest known schedule either.
    bn_rate = 4.0 / MEDIAN_LEVEL["BANKNIFTY"]
    assert sensex_rate <= bn_rate * 1.10


def test_every_instrument_pays_a_comparable_fraction_of_index():
    """The invariant the old table violated: friction is a % of price, so no
    instrument may sit at a wildly different rate from the others."""
    rates = {k: cost_in_points(k) / MEDIAN_LEVEL[k] for k in MEDIAN_LEVEL}
    assert max(rates.values()) / min(rates.values()) < 1.5, rates


# --- the fallthrough must fail CLOSED, not open -----------------------------

def test_unknown_instrument_does_not_silently_get_the_cheapest():
    """The original defect in one line: anything not BANKNIFTY got 1.5."""
    assert cost_in_points("MIDCPNIFTY") != 1.5


def test_unknown_instrument_charges_the_most_expensive_known_schedule():
    assert cost_in_points("MIDCPNIFTY") == max(SPOT_ROUND_TRIP_PTS_BY_INSTRUMENT.values())


def test_unknown_instrument_warns(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        cost_in_points("SOMETHING_NEW")
    assert any("SOMETHING_NEW" in r.message or "SOMETHING_NEW" in str(r.args)
               for r in caplog.records), "an unpriced instrument must not be silent"


def test_none_and_empty_do_not_raise():
    for bad in (None, "", "   "):
        assert cost_in_points(bad) > 0


def test_instrument_name_is_case_insensitive():
    assert cost_in_points("sensex") == cost_in_points("SENSEX")
    assert cost_in_points(" NIFTY ") == cost_in_points("NIFTY")


# --- the deduction itself ---------------------------------------------------

def test_cost_is_deducted_from_gross():
    assert apply_round_trip_cost(100.0, "NIFTY", True) == pytest.approx(98.5)
    assert apply_round_trip_cost(100.0, "SENSEX", True) == pytest.approx(
        100.0 - cost_in_points("SENSEX"))


def test_disabled_costs_still_deduct_nothing():
    assert apply_round_trip_cost(100.0, "SENSEX", False) == 100.0
    assert apply_round_trip_cost(-7.0, "SENSEX", False) == -7.0


def test_a_loss_gets_larger_not_smaller():
    """Sign convention: cost always makes the result worse."""
    assert apply_round_trip_cost(-10.0, "SENSEX", True) < -10.0


# --- provenance -------------------------------------------------------------

def test_cost_model_version_is_exposed_and_bumped():
    """Stored SENSEX results from before this fix are not comparable to new
    ones. Runs must record which schedule produced their numbers."""
    assert isinstance(COST_MODEL_VERSION, int)
    assert COST_MODEL_VERSION >= 2


def test_table_covers_every_supported_underlying():
    from app.instruments import UNDERLYING_META
    assert set(UNDERLYING_META) <= set(SPOT_ROUND_TRIP_PTS_BY_INSTRUMENT)


# --- the economic regression this exists to prevent -------------------------

def test_scaled_cost_rejects_the_degenerate_sensex_basin():
    """The real candidates from optimizer job 486f062f (explosive_reversal /
    SENSEX / 2026-09-01). Each reported a PROFIT in spot points under the 1.5-pt
    schedule and a large LOSS in real option rupees. Under the scaled schedule
    the spot phase rejects them, which is the whole point of the fix.

    (trades, net_pnl_pts_at_1.5)
    """
    candidates = [
        (4753, 10745.0), (4469, 9247.0), (4326, 11077.0), (4189, 10615.0),
        (3861, 10017.0), (2884, 9085.0),
    ]
    sensex = cost_in_points("SENSEX")
    for n, net_at_1_5 in candidates:
        gross = net_at_1_5 + 1.5 * n          # undo the old charge
        rescored = gross - sensex * n          # apply the scaled one
        assert rescored < 0, (
            f"{n} trades: {net_at_1_5:+.0f} pts at 1.5 -> {rescored:+.0f} pts at "
            f"{sensex} — must be a loss, else the search still prefers sub-noise stops")
