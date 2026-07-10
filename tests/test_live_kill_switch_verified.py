"""Tests for panic_squareoff_verified (app/live/kill_switch.py) — item B hardening.

Trading-critical invariants:
- flattening orders are exchange-aware marketable LIMITs (never MKT): priced
  through the fresh GetQuotes touch when a token exists, clamped inside the
  circuit band, aligned to the leg's own tick, in the position's own prd/exch;
- unfilled exits are re-priced through a BOUNDED widening-band loop via
  cancel + re-place; remaining qty is re-read from the broker's fillshares
  after the cancel so a race-window fill can never cause an over-sell;
- every leg gets an honest outcome (FILLED / PLACED_UNCONFIRMED / REJECTED
  with the broker's reason / UNPRICED) and the final position book decides
  all_flat — a partial flatten is loudly visible, never swallowed;
- the route returns the per-leg report + a truthful one-line message.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.kill_switch import (  # noqa: E402
    FLATTEN_BAND_SCHEDULE,
    LEG_FILLED,
    LEG_REJECTED,
    LEG_UNCONFIRMED,
    LEG_UNPRICED,
    _leg_price,
    panic_squareoff_verified,
)
from app.live.mock_noren import MockNoren  # noqa: E402


NOSLEEP = None


async def _nosleep(_secs: float) -> None:
    return None


def _run(coro):
    return asyncio.run(coro)


def _pos(tsym="NIFTY26JUN26C25000", netqty="65", lp="200.0", exch="NFO",
         prd="M", token=None, ti=None) -> Dict[str, Any]:
    row = {"tsym": tsym, "netqty": netqty, "lp": lp, "exch": exch, "prd": prd}
    if token is not None:
        row["token"] = token
    if ti is not None:
        row["ti"] = ti
    return row


class FillingClient(MockNoren):
    """MockNoren that fills the Nth accepted placement (1-based) COMPLETE and
    flattens the position book when it fills. Earlier placements stay OPEN so
    the re-price loop has something to chew on."""

    def __init__(self, *, fill_on_attempt: int = 1, partial_first: Optional[int] = None, **kw):
        super().__init__(**kw)
        self._fill_on = fill_on_attempt
        self._accepted = 0
        self._partial_first = partial_first

    async def place_order(self, intent):
        result = await super().place_order(intent)
        if not result.ok:
            return result
        self._accepted += 1
        order = self._orders[result.norenordno]
        if self._accepted == self._fill_on:
            order["status"] = "COMPLETE"
            order["fillshares"] = str(order["qty"])
            self._position_book_data = []
        elif self._partial_first is not None and self._accepted == 1:
            order["fillshares"] = str(self._partial_first)
        return result


class RejectingBookClient(MockNoren):
    """Accepts the placement but the order book later reports it REJECTED —
    the post-ack RMS/exchange reject path (rejreason lives on the book row)."""

    def __init__(self, *, rejreason: str, **kw):
        super().__init__(**kw)
        self._book_rejreason = rejreason

    async def place_order(self, intent):
        result = await super().place_order(intent)
        if result.ok:
            self._orders[result.norenordno]["status"] = "REJECT"  # doc sample spelling
            self._orders[result.norenordno]["rejreason"] = self._book_rejreason
        return result


class CancelFailsButFilledClient(MockNoren):
    """cancel_order fails; the book shows the order COMPLETE (the fill won the
    race). The loop must classify the leg FILLED and never double-place."""

    async def cancel_order(self, norenordno):
        if norenordno in self._orders and self._orders[norenordno]["status"] == "OPEN":
            self._orders[norenordno]["status"] = "COMPLETE"
            self._orders[norenordno]["fillshares"] = str(self._orders[norenordno]["qty"])
            from app.live.broker_protocol import OrderResult
            return OrderResult(ok=False, rejreason="Rejected : ORA:Order not found to Cancel",
                               raw={"stat": "Not_Ok"})
        return await super().cancel_order(norenordno)


# ---------- pure pricing --------------------------------------------------------

def test_leg_price_sells_through_the_bid_and_clamps_to_lower_circuit():
    quote = {"bp1": "200.0", "sp1": "201.0", "lc": "199.5", "uc": "260.0"}
    # 1% through the bid would be 198.0 — below the 199.5 circuit floor → clamp.
    prc = _leg_price(65, 210.0, 1.0, 0.05, quote)
    assert prc == pytest.approx(199.5)
    # Without the clamp binding (band 0.1% → 199.8) rounding is DOWN to tick.
    prc = _leg_price(65, 210.0, 0.1, 0.05, quote)
    assert prc == pytest.approx(199.80)


def test_leg_price_buys_through_the_ask_and_clamps_to_upper_circuit():
    quote = {"bp1": "200.0", "sp1": "201.0", "lc": "150.0", "uc": "202.0"}
    # 1% through the ask = 203.01 → capped at uc 202.0.
    prc = _leg_price(-65, 195.0, 1.0, 0.05, quote)
    assert prc == pytest.approx(202.0)


def test_leg_price_falls_back_to_ref_without_quote():
    assert _leg_price(65, 200.0, 1.0, 0.05, {}) == pytest.approx(198.0)
    assert _leg_price(-65, 200.0, 1.0, 0.05, {}) == pytest.approx(202.0)
    assert _leg_price(65, None, 1.0, 0.05, {}) is None


# ---------- happy path ----------------------------------------------------------

def test_all_legs_fill_first_pass_all_flat():
    cl = FillingClient(fill_on_attempt=1, position_book_data=[_pos()])
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    assert out["filled"] == 1
    assert out["flattened"] == 1
    assert out["legs"][0]["outcome"] == LEG_FILLED
    assert out["all_flat"] is True
    assert out["residual"] == []
    assert out["total"] is True
    # Exactly one exit order, SELL, LMT, in the position's own prd.
    book = _run(cl.order_book())
    assert len(book) == 1
    assert book[0]["trantype"] == "S" and book[0]["prctyp"] == "LMT"
    assert book[0]["prd"] == "M"


def test_position_read_failure_verdict_is_unknown_never_all_flat():
    """THE core fix: when the final position re-read RAISES (expired token), the
    kill verdict must be UNKNOWN (all_flat=None), NEVER a false ALL FLAT."""
    cl = MockNoren()
    cl.script_read_error("position_book", "Session Expired : Invalid Session Key")
    out = _run(panic_squareoff_verified(cl, [], [], sleep=_nosleep))
    assert out["all_flat"] is None          # UNKNOWN, not True
    assert out["total"] is False            # never a clean all-clear
    assert out["residual"] != []            # carries the re-check-failed marker


# ---------- reject-and-surface --------------------------------------------------

def test_immediate_reject_surfaces_reason_per_leg():
    cl = MockNoren(position_book_data=[_pos()])
    cl.script_reject("RMS:Rule: Check circuit limit including square off order exceeds")
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_REJECTED
    assert "RMS" in leg["reason"]
    assert out["flatten_failures"][0]["reason"] == leg["reason"]
    assert out["filled"] == 0
    assert out["all_flat"] is False          # position book still shows the leg
    assert out["residual"][0]["tsym"] == "NIFTY26JUN26C25000"
    assert out["total"] is False


def test_post_ack_book_reject_surfaces_rejreason():
    """ACCEPTED then REJECTED on the book (doc sample spelling 'REJECT') — the
    old executor counted this leg as flattened; the verified one must not."""
    cl = RejectingBookClient(rejreason="Insufficient funds",
                             position_book_data=[_pos()])
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_REJECTED
    assert leg["reason"] == "Insufficient funds"
    assert out["filled"] == 0
    assert out["flattened"] == 1  # historic meaning: broker ACCEPTED the order
    assert out["all_flat"] is False


# ---------- bounded re-price loop ------------------------------------------------

def test_reprice_loop_fills_on_second_pass():
    cl = FillingClient(fill_on_attempt=2, position_book_data=[_pos()])
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_FILLED
    assert len(leg["attempts"]) == 2
    # Second attempt crosses a wider band → strictly more aggressive SELL price.
    assert leg["attempts"][1]["prc"] < leg["attempts"][0]["prc"]
    assert leg["attempts"][1]["band_pct"] > leg["attempts"][0]["band_pct"]
    # First attempt was cancelled before the re-place.
    first_no = leg["attempts"][0]["norenordno"]
    book = {o["norenordno"]: o for o in _run(cl.order_book())}
    assert book[first_no]["status"] == "CANCELED"
    assert out["all_flat"] is True


def test_loop_is_bounded_and_leaves_final_exit_working():
    cl = MockNoren(position_book_data=[_pos()])  # orders never fill
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_UNCONFIRMED
    assert len(leg["attempts"]) == len(FLATTEN_BAND_SCHEDULE)
    assert out["pending"] == 1
    assert out["all_flat"] is False
    # The most aggressive exit is LEFT WORKING (resting exit beats no exit).
    last_no = leg["attempts"][-1]["norenordno"]
    book = {o["norenordno"]: o for o in _run(cl.order_book())}
    assert book[last_no]["status"] == "OPEN"
    # Earlier attempts were cancelled.
    for att in leg["attempts"][:-1]:
        assert book[att["norenordno"]]["status"] == "CANCELED"


def test_partial_fill_replaces_remaining_qty_only():
    cl = FillingClient(fill_on_attempt=2, partial_first=25,
                       position_book_data=[_pos(netqty="65")])
    out = _run(panic_squareoff_verified(cl, [], [_pos(netqty="65")], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_FILLED
    book = _run(cl.order_book())
    qtys = [o["qty"] for o in book]
    assert qtys == [65, 40]  # second placement covers only the unfilled 40


def test_cancel_raced_by_fill_never_double_places():
    cl = CancelFailsButFilledClient(position_book_data=[_pos()])
    # Empty the position fixture on the fill so all_flat reflects reality.
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_FILLED
    assert len([o for o in _run(cl.order_book()) if o["trantype"] == "S"]) == 1
    assert out["filled"] == 1


# ---------- pricing inputs -------------------------------------------------------

def test_quote_priced_leg_uses_touch_and_leg_tick():
    cl = FillingClient(
        fill_on_attempt=1,
        position_book_data=[_pos(token="43854", ti="0.05")],
        quotes_data={"stat": "Ok", "bp1": "190.0", "sp1": "191.0",
                     "lc": "20.0", "uc": "400.0"},
    )
    _run(panic_squareoff_verified(
        cl, [], [_pos(token="43854", ti="0.05", lp="200.0")], sleep=_nosleep))
    book = _run(cl.order_book())
    # Priced off the live bid (190 * 0.99 = 188.1), not the stale lp 200.
    assert float(book[0]["prc"]) == pytest.approx(188.10)


def test_freeze_qty_slicing_builds_child_legs():
    big = _pos(tsym="NIFTY26JUN26C25000", netqty="4000", lp="200.0")
    cl = MockNoren(position_book_data=[big])
    out = _run(panic_squareoff_verified(cl, [], [big], sleep=_nosleep))
    slices = [l for l in out["legs"]]
    assert [l["qty"] for l in slices] == [1800, 1800, 400]
    assert slices[0]["slice"] == "1/3" and slices[2]["slice"] == "3/3"


def test_unpriced_leg_without_lp_or_token_is_loud():
    bad = _pos(netqty="65", lp="0.00")
    cl = MockNoren(position_book_data=[bad])
    out = _run(panic_squareoff_verified(cl, [], [bad], sleep=_nosleep))
    assert out["legs"][0]["outcome"] == LEG_UNPRICED
    assert out["unpriced"] == [{"tsym": "NIFTY26JUN26C25000", "netqty": 65}]
    assert out["total"] is False


# ---------- frontend wiring (text contract, same idiom as
# test_deployment_kill_switch.py) --------------------------------------------------

def test_frontend_kill_switch_panel_is_wired():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    panel = (root / "frontend" / "src" / "components" / "live" /
             "KillSwitchPanel.jsx").read_text(encoding="utf-8")
    # Gated on the broker book (positions/working orders), not only the session.
    assert "isOpenPosition" in panel and "isWorkingOrder" in panel
    # Typed confirm step before firing.
    assert 'confirmText !== "KILL"' in panel
    assert "kill-switch-confirm-input" in panel
    # Per-leg outcome report + loud residual banner.
    assert "kill-switch-report" in panel
    assert "kill-switch-residual" in panel
    assert "PLACED_UNCONFIRMED" in panel
    dash = (root / "frontend" / "src" / "components" / "live" /
            "LiveDashboard.jsx").read_text(encoding="utf-8")
    assert "<KillSwitchPanel />" in dash
    # The old session-gated one-click kill button is gone from PositionMonitor.
    pm = (root / "frontend" / "src" / "components" / "live" /
          "PositionMonitor.jsx").read_text(encoding="utf-8")
    assert "liveKillSwitch" not in pm


def test_never_raises_on_client_explosions():
    class ExplodingClient(MockNoren):
        async def place_order(self, intent):
            raise RuntimeError("socket torn")

        async def position_book(self):
            raise RuntimeError("book unavailable")

    cl = ExplodingClient()
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == "FAILED"
    assert "socket torn" in leg["reason"]
    assert out["all_flat"] is None  # flat-check unavailable ≠ flat
