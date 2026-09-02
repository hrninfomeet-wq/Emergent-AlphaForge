"""Realistic Indian intraday cost model (spot-mode proxy).

In SPOT backtest mode (validating signals on the underlying), total round-trip
friction — the bid/ask crossed twice, plus brokerage, STT, exchange charges, GST
and stamp duty — is modelled as ONE flat deduction in index points per completed
trade. That is a deliberate simplification: the spot leg is a signal-quality
proxy, not a tradable instrument. The real rupee schedule lives in
`app.option_costs`, which is what prices the option leg.

WHY THIS IS A PER-INSTRUMENT TABLE
----------------------------------
Friction is fundamentally a PERCENTAGE of price, but this model is denominated in
index POINTS — so the constant must be re-derived for every instrument or it
silently means something different on each one.

That is exactly what went wrong. The previous version special-cased BANKNIFTY and
returned NIFTY's 1.5 points for everything else, so SENSEX — the LARGEST
underlying in the warehouse — was charged the smallest index's friction:

    NIFTY      1.5 pts @ ~24,468  = 0.00613% of index
    BANKNIFTY  4.0 pts @ ~55,734  = 0.00718% of index
    SENSEX     1.5 pts @ ~80,176  = 0.00187% of index   <-- 3.3x too cheap

The optimizer's SEARCH phase ranks candidates on this spot P&L, so under-charging
SENSEX made sub-noise stops look profitable. Measured on the 2026-09-01
`explosive_reversal` SENSEX job: the search converged on stops of 5-9 index points
(~0.3x of a single 1-minute bar's range), reported +9,085 spot points, and every
one of its 50 re-ranked candidates lost money once real option premiums were
applied — the best at -Rs 932,976. Re-priced correctly, all 50 are losses in the
spot phase too, which is where they should have been rejected.

The rates are NOT equal across instruments and should not be forced equal:
BANKNIFTY genuinely carries a wider relative spread than NIFTY. What the table
must guarantee is that no instrument sits at a wildly different rate from the
others by accident.

RATES ARE ESTIMATES AND CHANGE. These are operator-tunable proxies, not
statutory truth; `app.option_costs` carries the real, citation-backed schedule.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: Bump when the schedule below changes in a way that makes new results
#: non-comparable to stored ones. v1 = the pre-2026-09-01 table that charged
#: SENSEX NIFTY's 1.5 points; v2 = SENSEX priced at its own scale.
COST_MODEL_VERSION = 2

#: Round-trip spot friction in INDEX POINTS, per instrument.
#:
#: SENSEX is derived, not guessed: 1.5 pts is 0.00613% of NIFTY's ~24,468, and the
#: same fraction of SENSEX's ~80,176 is 4.9 points. That is the CONSERVATIVE
#: floor — `deployment_preflight` records BSE weeklies as "generally lower depth
#: than NIFTY weekly", and at BANKNIFTY's (wider) rate the figure would be ~5.75.
#: Raise it to 5.75 if live fills say the depth penalty is real; do not lower it.
SPOT_ROUND_TRIP_PTS_BY_INSTRUMENT = {
    "NIFTY": 1.5,
    "BANKNIFTY": 4.0,     # higher absolute pts for a larger underlying
    "SENSEX": 4.9,
}

# Back-compat aliases. Kept because they are the names the module has always
# exported; the table above is the single definition.
SPOT_ROUND_TRIP_PTS = SPOT_ROUND_TRIP_PTS_BY_INSTRUMENT["NIFTY"]
SPOT_ROUND_TRIP_PTS_BANKNIFTY = SPOT_ROUND_TRIP_PTS_BY_INSTRUMENT["BANKNIFTY"]

#: What an unpriced instrument is charged. A cost model must fail CLOSED — the
#: old fallthrough returned the CHEAPEST schedule to anything it did not
#: recognise, which is how SENSEX went 3.3x under-charged for its whole history.
#: Over-charging an unknown instrument produces a pessimistic backtest; the
#: reverse produces a strategy that only works in the simulator.
_UNKNOWN_INSTRUMENT_PTS = max(SPOT_ROUND_TRIP_PTS_BY_INSTRUMENT.values())

_warned: set[str] = set()


def cost_in_points(instrument: str) -> float:
    """Round-trip spot friction, in index points, for *instrument*.

    An unrecognised instrument is charged the most expensive known schedule and
    warned about ONCE — never silently given the cheapest.
    """
    key = str(instrument or "").strip().upper()
    known = SPOT_ROUND_TRIP_PTS_BY_INSTRUMENT.get(key)
    if known is not None:
        return known
    if key not in _warned:
        _warned.add(key)
        log.warning(
            "No spot round-trip cost is defined for instrument %r — charging the "
            "most expensive known schedule (%.2f pts). Add it to "
            "SPOT_ROUND_TRIP_PTS_BY_INSTRUMENT, derived from its index level, or "
            "every backtest on it is priced on another instrument's scale.",
            instrument, _UNKNOWN_INSTRUMENT_PTS,
        )
    return _UNKNOWN_INSTRUMENT_PTS


def apply_round_trip_cost(gross_pts: float, instrument: str, enabled: bool = True) -> float:
    """Deduct one round trip of friction from a trade's gross point P&L."""
    if not enabled:
        return gross_pts
    return gross_pts - cost_in_points(instrument)


def cost_model_meta(instrument: str, enabled: bool = True) -> dict:
    """Provenance stamp for a run, so stored results stay comparable.

    A SENSEX backtest saved before v2 and one saved after are NOT comparable —
    the second charges 3.3x the friction. Recording the schedule alongside the
    numbers is what lets a reader tell them apart instead of reading a 3.3x cost
    change as a strategy regression.
    """
    return {
        "version": COST_MODEL_VERSION,
        "instrument": str(instrument or "").strip().upper(),
        "round_trip_pts": cost_in_points(instrument) if enabled else 0.0,
        "enabled": bool(enabled),
    }
