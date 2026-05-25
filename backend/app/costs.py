"""Realistic Indian intraday cost model (spot-mode proxy)."""

# In SPOT backtest mode (validating signals on the underlying), we model total
# round-trip friction (slippage + brokerage proxy + STT + GST) as a constant
# deduction in instrument points. This is industry-standard practice.
SPOT_ROUND_TRIP_PTS = 1.5  # NIFTY/SENSEX
SPOT_ROUND_TRIP_PTS_BANKNIFTY = 4.0  # higher absolute pts for larger underlying


def cost_in_points(instrument: str) -> float:
    if instrument.upper() == "BANKNIFTY":
        return SPOT_ROUND_TRIP_PTS_BANKNIFTY
    return SPOT_ROUND_TRIP_PTS


def apply_round_trip_cost(gross_pts: float, instrument: str, enabled: bool = True) -> float:
    if not enabled:
        return gross_pts
    return gross_pts - cost_in_points(instrument)
