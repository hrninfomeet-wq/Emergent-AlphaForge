import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live import greeks as g

# Param combos where the forward premium carries ZERO recoverable vol-information
# in float64, so no solver can round-trip IV to abs=2e-3. All are the 7-day (T=0.02)
# 12%-vol deep wing: the deep-OTM call/put underflows to price == 0.0 exactly, and
# the deep-ITM put/call equals intrinsic to float precision (excess time-value 0.0,
# vega ~1e-15..1e-19). Proven by hand — see the agent's report. These are xfailed,
# NOT removed and NOT covered by loosening the tolerance for the other 86 cases.
_IV_DEAD_WING = {
    (0.85, 0.02, 0.12, True),   # deep-OTM call → price == 0.0
    (1.15, 0.02, 0.12, False),  # deep-OTM put  → price == 0.0
    (0.85, 0.02, 0.12, False),  # deep-ITM put  → premium == intrinsic (no time value)
    (1.15, 0.02, 0.12, True),   # deep-ITM call → premium == intrinsic (no time value)
}


def test_put_call_parity():
    S, K, T, r, vol = 100.0, 100.0, 0.5, 0.065, 0.25
    c = g.bs_price(S, K, T, r, vol, True)
    p = g.bs_price(S, K, T, r, vol, False)
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-9)


def test_delta_bounds_and_gamma_vega_positive():
    S, K, T, r, vol = 100.0, 95.0, 0.25, 0.065, 0.3
    assert 0.0 <= g.bs_delta(S, K, T, r, vol, True) <= 1.0
    assert -1.0 <= g.bs_delta(S, K, T, r, vol, False) <= 0.0
    assert g.bs_gamma(S, K, T, r, vol) > 0.0
    assert g.bs_vega(S, K, T, r, vol) > 0.0


def test_long_option_theta_per_day_negative():
    out = g.compute_greeks(100.0, 100.0, 0.1, 5.0, True)
    assert out is not None and out["theta_per_day"] < 0.0


@pytest.mark.parametrize("moneyness", [0.85, 0.95, 1.0, 1.05, 1.15])
@pytest.mark.parametrize("T", [0.02, 0.1, 0.5])
@pytest.mark.parametrize("vol0", [0.12, 0.25, 0.6])
@pytest.mark.parametrize("is_call", [True, False])
def test_iv_roundtrip(moneyness, T, vol0, is_call):
    if (moneyness, T, vol0, is_call) in _IV_DEAD_WING:
        pytest.xfail(
            "Zero recoverable IV information: the forward premium underflows to 0.0 "
            "(deep-OTM) or equals intrinsic to float precision (deep-ITM) for this "
            "7-day 12%-vol deep-wing strike; vega ~1e-15..1e-19."
        )
    S, r = 100.0, 0.065
    K = S / moneyness
    price = g.bs_price(S, K, T, r, vol0, is_call)
    iv, conf = g.implied_vol(price, S, K, T, r, is_call)
    assert iv is not None
    assert iv == pytest.approx(vol0, abs=2e-3)


def test_sub_intrinsic_premium_unsolvable():
    # call worth >= S - K*e^{-rT}; a premium below intrinsic has no IV
    S, K, T, r = 120.0, 100.0, 0.5, 0.065
    intrinsic = S - K * math.exp(-r * T)
    iv, conf = g.implied_vol(intrinsic - 1.0, S, K, T, r, True)
    assert iv is None and conf == "none"


def test_non_positive_inputs_return_none():
    assert g.compute_greeks(0.0, 100.0, 0.1, 5.0, True) is None
    assert g.compute_greeks(100.0, 100.0, 0.0, 5.0, True) is None
    assert g.compute_greeks(100.0, 100.0, 0.1, 0.0, True) is None


def test_deep_itm_low_confidence():
    # deep ITM call: tiny vega → low-confidence but Δ/Θ still returned
    out = g.compute_greeks(200.0, 50.0, 0.05, 150.1, True)
    assert out is None or out["confidence"] in ("low", "ok")


def test_premium_above_no_arb_max_is_unsolvable():
    # A premium above the max achievable price (here even above spot for a call)
    # has no valid IV; the solver must reject it, not clamp to IV_MAX with conf "ok".
    iv, conf = g.implied_vol(26000.0, 25000.0, 25000.0, 30 / 365.0, 0.065, True)
    assert iv is None and conf == "none"


def test_garbage_premium_skips_in_compute_greeks():
    assert g.compute_greeks(25000.0, 25000.0, 30 / 365.0, 26000.0, True) is None
