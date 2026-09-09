"""An exit limit outside the exchange price band is an automatic reject.

2026-09-03, live: "SELL ORDER PRICE [49.70000000] IS BEYOND LPP LIMIT:
[87.65000000]". The order was the catastrophe OCO's stop leg — the OCO itself is
now OFF by default (see ``live_deploy_context._broker_oco_enabled``); no
REST-side price pre-check was kept, because GetQuotes cannot see the LPP band.
But the SAME defect class sits on the paths that actually flatten a position:

  * ``kill_switch._leg_price`` DOES clamp to the band — but then rounds the
    clamped price DOWN to the tick, which walks it back below ``lc`` whenever
    ``lc`` is not itself a tick multiple. The clamp then guarantees the very
    reject it exists to prevent.
  * ``auto_square._marketable_prc`` (the FIRST square — every strategy stop,
    spot-mirror, time-stop, overall-basket and EOD exit) had no band awareness
    at all, even though Step 3.6 already holds the GetQuotes payload that
    carries ``lc``/``uc``.

A square-off that the broker rejects leaves a live position open, so this is the
most safety-critical instance of the class, not the least.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.auto_square import square_position  # noqa: E402
from app.live.kill_switch import _leg_price  # noqa: E402
from app.live.mock_noren import MockNoren  # noqa: E402
from app.live.order_builder import round_to_tick  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def _pos(netqty="65", lp=100.0, token="999"):
    return {"tsym": "NIFTY2662221000CE", "exch": "NFO",
            "netqty": netqty, "lp": lp, "token": token}


def _sell_orders(client):
    return [o for o in client._orders.values() if o["trantype"] == "S"]


# --------------------------------------------------------------------------- #
# kill_switch._leg_price — the clamp must not round itself back out of band
# --------------------------------------------------------------------------- #

class TestLegPriceClampRespectsTheTick:
    def test_sell_clamped_to_a_non_tick_floor_stays_at_or_above_it(self):
        """lc=87.63 is not a 0.05 multiple. Rounding the clamped price DOWN lands
        on 87.60 — below the floor, and rejected for exactly the reason the
        clamp exists to avoid."""
        prc = _leg_price(65, 100.0, 50.0, 0.05, {"lc": "87.63"})
        assert prc is not None
        assert prc >= 87.63, f"{prc} is below the band floor it was clamped to"
        assert prc == 87.65

    def test_buy_clamped_to_a_non_tick_ceiling_stays_at_or_below_it(self):
        prc = _leg_price(-65, 100.0, 50.0, 0.05, {"uc": "112.37"})
        assert prc is not None
        assert prc <= 112.37, f"{prc} is above the band ceiling it was clamped to"
        assert prc == 112.35

    def test_a_tick_multiple_floor_is_unchanged(self):
        assert _leg_price(65, 100.0, 50.0, 0.05, {"lc": "87.65"}) == 87.65

    def test_an_in_band_price_is_untouched(self):
        # 100 * (1 - 1%) = 99.0, well inside [50, 150]
        assert _leg_price(65, 100.0, 1.0, 0.05, {"lc": "50", "uc": "150"}) == 99.00

    def test_no_band_in_the_quote_is_unchanged(self):
        assert _leg_price(65, 100.0, 1.0, 0.05, {}) == 99.00


# --------------------------------------------------------------------------- #
# square_position — the software square must respect the band too
# --------------------------------------------------------------------------- #

class TestSquarePositionClampsToTheBand:
    def test_a_sell_below_the_floor_is_lifted_to_it(self):
        """The marketable 1% cross lands at 99.00 but the exchange floor is
        99.50 — placing 99.00 is a guaranteed reject and the position stays
        open. Clamp to the lowest price the exchange will accept."""
        client = MockNoren()
        client.set_quotes({"stat": "Ok", "lp": "100", "lc": "99.50", "uc": "150"})
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "65", "lp": "100"}
        ])
        result = run(square_position(client, _pos(), reason="stop_hit", band_pct=1.0))
        assert result["squared"] is True
        orders = _sell_orders(client)
        assert len(orders) == 1
        assert orders[0]["prc"] == 99.50, "exit was priced outside the exchange band"

    def test_a_non_tick_floor_is_lifted_to_the_next_valid_tick(self):
        client = MockNoren()
        client.set_quotes({"stat": "Ok", "lp": "100", "lc": "99.53", "uc": "150"})
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "65", "lp": "100"}
        ])
        run(square_position(client, _pos(), reason="stop_hit", band_pct=1.0))
        prc = _sell_orders(client)[0]["prc"]
        assert prc >= 99.53 and prc == 99.55

    def test_a_buy_to_close_is_capped_at_the_ceiling(self):
        """A short position exits with a BUY; the cross pushes it UP, so the
        ceiling is the binding bound."""
        client = MockNoren()
        client.set_quotes({"stat": "Ok", "lp": "100", "lc": "50", "uc": "100.50"})
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "-65", "lp": "100"}
        ])
        result = run(square_position(client, _pos(netqty="-65"), reason="stop_hit",
                                     band_pct=1.0))
        assert result["squared"] is True
        buys = [o for o in client._orders.values() if o["trantype"] == "B"]
        assert len(buys) == 1
        assert buys[0]["prc"] == 100.50

    def test_an_in_band_exit_is_priced_exactly_as_before(self):
        client = MockNoren()
        client.set_quotes({"stat": "Ok", "lp": "100", "lc": "50", "uc": "150"})
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "65", "lp": "100"}
        ])
        run(square_position(client, _pos(), reason="stop_hit", band_pct=1.0))
        assert _sell_orders(client)[0]["prc"] == round_to_tick(
            100 * (1 - 1.0 / 100), 0.05, mode="down")

    def test_a_quote_without_band_fields_is_priced_exactly_as_before(self):
        client = MockNoren()
        client.set_quotes({"stat": "Ok", "lp": "100"})
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "65", "lp": "100"}
        ])
        run(square_position(client, _pos(), reason="stop_hit", band_pct=1.0))
        assert _sell_orders(client)[0]["prc"] == 99.00

    def test_a_token_less_position_is_byte_identical(self):
        """No token → no quote → no band. The ~94 token-less fixtures and the
        legacy/paper path must be untouched."""
        client = MockNoren()
        client.set_quotes({"stat": "Ok", "lp": "100", "lc": "99.50"})
        pos = {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "65", "lp": 200.0}
        run(square_position(client, pos, reason="stop_hit", band_pct=1.0))
        assert _sell_orders(client)[0]["prc"] == round_to_tick(
            200.0 * (1 - 1.0 / 100), 0.05, mode="down")


# ---------------------------------------------------------------------------
# A band on the WRONG SIDE of the market must not be honoured (2026-09-09)
#
# The clamp added above assumed lc < market < uc. It is not guaranteed: `ref`
# falls back to position["lp"] when the fresh quote's `lp` is unusable, while
# lc/uc are still read from that (different-vintage) quote payload. A floor at
# or ABOVE the market would lift a SELL exit out of the market — where it RESTS
# UNFILLED rather than crossing. That is the dangerous direction, because
# _square_position_impl reports squared=True on an ACCEPTED limit: a position
# that never flattened is reported flat. An out-of-band reject is loud and
# leaves the position visibly open. Prefer the reject.
# ---------------------------------------------------------------------------

class TestBandOnTheWrongSideIsIgnored:
    def test_sell_is_not_lifted_above_the_market_by_a_floor_above_ref(self):
        """lc ABOVE the mark: honouring it would price the exit through the top
        of the book and leave it resting."""
        client = MockNoren()
        # Fresh quote carries a floor ABOVE the last price — stale/nonsensical band.
        client.set_quotes({"stat": "Ok", "lp": "100", "lc": "150"})
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "65", "lp": "100"}
        ])
        run(square_position(client, _pos(), reason="stop_hit", band_pct=1.0))
        prc = _sell_orders(client)[0]["prc"]
        assert prc == 99.00, f"expected the unclamped marketable cross, got {prc}"
        assert prc < 100.0, "a SELL exit must still be priced THROUGH the market"

    def test_buy_is_not_pushed_below_the_market_by_a_ceiling_under_ref(self):
        """uc BELOW the mark, on a short position — mirror image."""
        client = MockNoren()
        client.set_quotes({"stat": "Ok", "lp": "100", "uc": "50"})
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "-65", "lp": "100"}
        ])
        run(square_position(client, _pos(netqty="-65"), reason="stop_hit", band_pct=1.0))
        buys = [o for o in client._orders.values() if o["trantype"] == "B"]
        assert len(buys) == 1
        assert buys[0]["prc"] == 101.00, buys[0]["prc"]
        assert buys[0]["prc"] > 100.0, "a BUY exit must still be priced THROUGH the market"

    def test_leg_price_ignores_a_floor_above_the_anchor(self):
        """kill_switch._leg_price carries the same guard against its own anchor."""
        prc = _leg_price(65, 100.0, 50.0, 0.05, {"lc": "150"})
        assert prc == round_to_tick(100.0 * 0.5, 0.05, mode="down"), prc
        assert prc < 100.0

    def test_leg_price_ignores_a_ceiling_below_the_anchor(self):
        prc = _leg_price(-65, 100.0, 50.0, 0.05, {"uc": "50"})
        assert prc == round_to_tick(100.0 * 1.5, 0.05, mode="up"), prc
        assert prc > 100.0

    def test_a_sane_band_still_clamps(self):
        """Regression floor: the wrong-side guard must not disable the real clamp."""
        prc = _leg_price(65, 100.0, 50.0, 0.05, {"lc": "87.63"})
        assert prc >= 87.63, f"{prc} fell back below the floor"
