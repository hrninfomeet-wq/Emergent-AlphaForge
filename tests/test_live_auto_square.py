"""TDD tests for backend/app/live/auto_square.py (Task L3.3).

The time-cap primitives (deadline_iso / is_due / SQUARE_HORIZON_SEC) were removed
with the manual 10-minute auto-square timer (see docs/superpowers/specs/
2026-07-09-remove-manual-livetest-10min-timer-design.md); only the square executor
and the SL-LMT backstop builder remain.

Coverage
--------
build_sl_backstop_intent:
  - trgprc == stop_trigger
  - prc == max(0.05, round(stop_trigger - 0.05, 2))
  - prc <= trgprc (protective invariant holds)
  - prc > 0 (always)
  - prctyp == "SL-LMT"
  - trantype == "S" (SELL — long option exit)
  - stop_trigger near 0.05 → prc clamped to 0.05, still <= trgprc
  - [FIX2] stop_trigger == 0.05 → returns None (at tick floor, can't build protective stop)
  - [FIX2] stop_trigger < 0.05 (e.g. 0.04, 0.0, negative) → returns None
  - [FIX2] stop_trigger nan/inf → returns None
  - [FIX2] stop_trigger None → returns None (no raise)
  - [FIX2] stop_trigger "abc" (non-numeric string) → returns None (no raise)
  - [FIX2] stop_trigger 84.0 (120*0.7) → valid intent (trgprc 84.0, prc 83.95)
  - [FIX2] stop_trigger 0.10 → valid intent (prc 0.05)

square_position (MockNoren, injected time):
  - long netqty 65 → SELL 65 marketable-limit (correct direction)
  - short netqty -65 → BUY 65 marketable-limit (correct direction)
  - correct prc formula from lp (SELL: lp*(1-eff/100), BUY: lp*(1+eff/100))
  - filled 0 + working order → cancel called, squared=True via 'cancel'
  - partial fill (working_norenordno set + netqty 30) → cancels remainder THEN sells 30
  - lp missing → squared=False, reason='unpriced' (surfaced, not silently skipped)
  - lp == 0 → squared=False, reason='unpriced'
  - lp == NaN → squared=False, reason='unpriced'
  - scripted reject on first place → retry → second success → squared=True
  - scripted reject twice → squared=False, failures populated, no raise
  - wrong-direction guard: long position always produces trantype='S', never 'B'
  - never raises even when place_order raises
"""
from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.mock_noren import MockNoren
from app.live.auto_square import (
    build_sl_backstop_intent,
    reprice_exit_leg,
    square_position,
)
from app.live.order_builder import round_to_tick
from app.live.idempotency import new_client_order_id
from app.live.broker_protocol import OrderResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    """Run a coroutine synchronously (no event-loop fixture needed)."""
    return asyncio.run(coro)


def _position(
    netqty,
    lp,
    tsym="NIFTY2662221000CE",
    exch="NFO",
    working_norenordno=None,
):
    pos: Dict[str, Any] = {
        "tsym": tsym,
        "exch": exch,
        "netqty": netqty,
        "lp": lp,
    }
    if working_norenordno is not None:
        pos["working_norenordno"] = working_norenordno
    return pos


# ---------------------------------------------------------------------------
# build_sl_backstop_intent
# ---------------------------------------------------------------------------

class TestBuildSlBackstopIntent:
    def _make(self, stop_trigger, **kw):
        defaults = dict(
            exch="NFO",
            tsym="NIFTY2662221000CE",
            qty=65,
            stop_trigger=stop_trigger,
            client_order_id=new_client_order_id(),
        )
        defaults.update(kw)
        return build_sl_backstop_intent(**defaults)

    def test_trgprc_equals_stop_trigger(self):
        intent = self._make(100.0)
        assert intent.trgprc == 100.0

    def test_prc_below_trgprc(self):
        intent = self._make(100.0)
        assert intent.prc <= intent.trgprc

    def test_prc_greater_than_zero(self):
        intent = self._make(100.0)
        assert intent.prc > 0

    def test_prc_formula_normal(self):
        """prc = max(0.05, round(stop_trigger - 0.05, 2))"""
        intent = self._make(100.0)
        expected = max(0.05, round(100.0 - 0.05, 2))
        assert intent.prc == expected

    def test_prctyp_is_sl_lmt(self):
        intent = self._make(100.0)
        assert intent.prctyp == "SL-LMT"

    def test_trantype_is_sell(self):
        """Backstop is always a SELL — protective exit for a LONG option."""
        intent = self._make(100.0)
        assert intent.trantype == "S"

    def test_near_zero_stop_trigger_at_tick_floor_returns_none(self):
        """stop_trigger=0.05 (at the tick floor) → None.

        [FIX2] A stop_trigger AT 0.05 cannot build a protective stop because
        prc = max(0.05, 0.00) = 0.05 == trgprc (no headroom).  The function
        now returns None instead of asserting, so the caller falls back to the
        time-square hard cap.
        """
        result = self._make(0.05)
        assert result is None, (
            "Expected None for stop_trigger=0.05 (tick floor), but got an intent"
        )

    def test_very_small_stop_trigger_clamps_prc(self):
        """stop_trigger=0.10 → prc = max(0.05, 0.05) = 0.05; still <= trgprc.

        stop_trigger=0.10 is > 0.05 (valid), so an intent IS returned.
        """
        intent = self._make(0.10)
        assert intent is not None, "Expected a valid intent for stop_trigger=0.10"
        assert intent.prc == 0.05  # max(0.05, round(0.10-0.05,2)) = 0.05
        assert intent.prc <= intent.trgprc  # 0.05 <= 0.10 ✓

    def test_protective_invariant_holds_across_range(self):
        # 0.05 now returns None (at tick floor); start range from 0.10
        for trigger in [0.10, 1.0, 50.0, 500.0, 9999.0]:
            intent = self._make(trigger)
            assert intent is not None, f"Expected intent for trigger={trigger}"
            assert intent.prc <= intent.trgprc, f"failed at trigger={trigger}"
            assert intent.prc > 0, f"failed at trigger={trigger}"

    def test_prd_is_intraday(self):
        intent = self._make(100.0)
        assert intent.prd == "I"

    def test_ret_is_day(self):
        intent = self._make(100.0)
        assert intent.ret == "DAY"

    # --- FIX 2 new tests: fail-soft on invalid stop_trigger ---

    def test_stop_trigger_at_tick_floor_returns_none(self):
        """[FIX2] stop_trigger == 0.05 → None (can't build a stop AT the tick floor)."""
        result = self._make(0.05)
        assert result is None, (
            f"Expected None for stop_trigger=0.05 (at tick floor), got {result}"
        )

    def test_stop_trigger_below_tick_floor_returns_none(self):
        """[FIX2] stop_trigger == 0.04 → None (deep-OTM real market data, not a bug)."""
        result = self._make(0.04)
        assert result is None

    def test_stop_trigger_zero_returns_none(self):
        """[FIX2] stop_trigger == 0.0 → None (no raise)."""
        result = self._make(0.0)
        assert result is None

    def test_stop_trigger_negative_returns_none(self):
        """[FIX2] stop_trigger < 0 → None (no raise)."""
        result = self._make(-5.0)
        assert result is None

    def test_stop_trigger_nan_returns_none(self):
        """[FIX2] stop_trigger = NaN → None (no raise, no AssertionError)."""
        import math
        result = self._make(math.nan)
        assert result is None

    def test_stop_trigger_inf_returns_none(self):
        """[FIX2] stop_trigger = inf → None (no raise, no garbage intent)."""
        import math
        result = self._make(math.inf)
        assert result is None

    def test_stop_trigger_none_returns_none(self):
        """[FIX2] stop_trigger = None → None (no raise, no TypeError)."""
        result = self._make(None)
        assert result is None

    def test_stop_trigger_nonnumeric_string_returns_none(self):
        """[FIX2] stop_trigger = 'abc' → None (non-numeric string, no raise)."""
        result = self._make("abc")
        assert result is None

    def test_stop_trigger_84_returns_valid_intent(self):
        """[FIX2] stop_trigger = 120*0.7 = 84.0 → valid SL-LMT (trgprc=84.0, prc=83.95)."""
        intent = self._make(84.0)
        assert intent is not None, "Expected a valid OrderIntent for stop_trigger=84.0"
        assert intent.trgprc == 84.0
        assert intent.prc == 83.95
        assert intent.prctyp == "SL-LMT"
        assert intent.trantype == "S"

    def test_stop_trigger_010_returns_valid_intent(self):
        """[FIX2] stop_trigger = 0.10 → valid intent (prc = 0.05 <= trgprc = 0.10)."""
        intent = self._make(0.10)
        assert intent is not None, "Expected a valid OrderIntent for stop_trigger=0.10"
        assert intent.trgprc == 0.10
        assert intent.prc == 0.05
        assert intent.prc <= intent.trgprc


# ---------------------------------------------------------------------------
# square_position — direction + price
# ---------------------------------------------------------------------------

class TestSquarePositionDirectionAndPrice:
    def test_long_position_sells(self):
        """Long netqty 65 → SELL 65 marketable."""
        client = MockNoren()
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is True
        assert result["via"] == "exit_order"
        # Inspect the order placed in MockNoren
        orders = list(client._orders.values())
        assert len(orders) == 1
        assert orders[0]["trantype"] == "S"
        assert orders[0]["qty"] == 65

    def test_short_position_buys(self):
        """Short netqty -65 → BUY 65 marketable."""
        client = MockNoren()
        pos = _position(netqty=-65, lp=200.0)
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is True
        orders = list(client._orders.values())
        assert len(orders) == 1
        assert orders[0]["trantype"] == "B"
        assert orders[0]["qty"] == 65

    def test_sell_price_formula(self):
        """SELL price = tick-aligned(lp * (1 - band_pct/100), mode=down)."""
        client = MockNoren()
        lp = 200.0
        band = 1.0
        expected_prc = round_to_tick(lp * (1 - band / 100), 0.05, mode="down")
        pos = _position(netqty=65, lp=lp)
        run(square_position(client, pos, reason="test", band_pct=band))
        orders = list(client._orders.values())
        assert orders[0]["prc"] == expected_prc
        # Must be an exact 0.05 multiple
        assert round(expected_prc / 0.05) * 0.05 == pytest.approx(expected_prc)

    def test_buy_price_formula(self):
        """BUY price = tick-aligned(lp * (1 + band_pct/100), mode=up)."""
        client = MockNoren()
        lp = 200.0
        band = 1.0
        expected_prc = round_to_tick(lp * (1 + band / 100), 0.05, mode="up")
        pos = _position(netqty=-65, lp=lp)
        run(square_position(client, pos, reason="test", band_pct=band))
        orders = list(client._orders.values())
        assert orders[0]["prc"] == expected_prc
        # Must be an exact 0.05 multiple
        assert round(expected_prc / 0.05) * 0.05 == pytest.approx(expected_prc)

    def test_long_never_produces_buy(self):
        """Critical: a LONG position MUST NEVER produce a BUY intent (that grows the position)."""
        client = MockNoren()
        pos = _position(netqty=65, lp=100.0)
        run(square_position(client, pos, reason="test"))
        for order in client._orders.values():
            assert order["trantype"] != "B", (
                "BUY intent issued for a LONG position — this would grow, not close!"
            )

    def test_short_never_produces_sell(self):
        """Critical: a SHORT position MUST NEVER produce a SELL intent."""
        client = MockNoren()
        pos = _position(netqty=-65, lp=100.0)
        run(square_position(client, pos, reason="test"))
        for order in client._orders.values():
            assert order["trantype"] != "S", (
                "SELL intent issued for a SHORT position — this would grow, not close!"
            )


# ---------------------------------------------------------------------------
# square_position — exit product (NRML vs MIS) must MATCH the open position
# ---------------------------------------------------------------------------
# Real-money blocker: deployed live ENTRIES move to NRML (prd="M"). The single
# exit path (square_position) hardcoded prd="I" (MIS). Exiting an NRML long with
# an MIS sell can be rejected or open a NEW MIS short, leaving the NRML long open.
# The exit product MUST equal the position's own product.
# ---------------------------------------------------------------------------

class TestSquarePositionExitProduct:
    def test_nrml_position_exits_in_nrml(self):
        """A position carrying prd='M' (NRML) → the placed exit order's prd is 'M'."""
        client = MockNoren()
        pos = _position(netqty=65, lp=200.0)
        pos["prd"] = "M"  # NRML position
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is True
        assert result["via"] == "exit_order"
        orders = list(client._orders.values())
        assert len(orders) == 1
        assert orders[0]["prd"] == "M", (
            f"NRML position exited in product {orders[0]['prd']!r}, expected 'M' — "
            "an MIS sell on an NRML long can be rejected or open a new MIS short"
        )

    def test_position_without_prd_falls_back_to_mis(self):
        """A position with NO prd key → the placed exit order's prd is 'I' (MIS fallback).

        Guarantees the existing prd-less _position fixtures stay green.
        """
        client = MockNoren()
        pos = _position(netqty=65, lp=200.0)  # no 'prd' key
        assert "prd" not in pos
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is True
        orders = list(client._orders.values())
        assert len(orders) == 1
        assert orders[0]["prd"] == "I", (
            f"prd-less position exited in product {orders[0]['prd']!r}, expected 'I' fallback"
        )


# ---------------------------------------------------------------------------
# square_position — FRESH netqty re-confirm (B4 #2: never square a stale netqty)
# ---------------------------------------------------------------------------
# The guard reads the position book once per cycle. If the resting OCO fires
# between that read and the square, squaring the stale netqty would place a
# SECOND sell → naked short. So square_position MUST re-confirm the position is
# still non-flat from a FRESH position_book() read immediately before placing,
# and abort if flat. An EMPTY book (broker hiccup) is "unknown" — NOT flat — and
# must fall through to the existing place path unchanged.
# ---------------------------------------------------------------------------

class TestSquarePositionFreshNetqtyReconfirm:
    def test_fresh_book_reports_flat_aborts_with_already_flat(self):
        """A NON-EMPTY fresh book showing this tsym netqty 0 → no order placed,
        returns via=='already_flat'."""
        client = MockNoren()
        # The fresh re-read now reports this exact tsym as FLAT in a non-empty book.
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "0", "lp": "200.0"}
        ])
        pos = _position(netqty=65, lp=200.0)  # stale read still says long 65
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is True
        assert result["via"] == "already_flat"
        assert result["norenordno"] is None
        assert result["reason"] == "deadline"
        assert result["failures"] == []
        # CRITICAL: NO order was placed (a second sell would be a naked short).
        assert len(client._orders) == 0

    def test_fresh_book_flat_via_float_form_netqty(self):
        """netqty '0.0' (float-form string) in a non-empty book also reads as flat."""
        client = MockNoren()
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "0.0", "lp": "200.0"}
        ])
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="deadline"))
        assert result["via"] == "already_flat"
        assert len(client._orders) == 0

    def test_fresh_book_row_missing_netqty_treated_as_flat(self):
        """A matching row with NO netqty key → absent → treated as flat (no order)."""
        client = MockNoren()
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "lp": "200.0"}
        ])
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="deadline"))
        assert result["via"] == "already_flat"
        assert len(client._orders) == 0

    def test_fresh_book_still_nonflat_squares_normally(self):
        """A NON-EMPTY fresh book that STILL shows the tsym non-flat → squares
        normally (existing behavior: a real exit order is placed)."""
        client = MockNoren()
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "65", "lp": "200.0"}
        ])
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is True
        assert result["via"] == "exit_order"
        orders = [o for o in client._orders.values() if o["trantype"] == "S"]
        assert len(orders) == 1
        assert orders[0]["qty"] == 65

    def test_fresh_book_empty_is_unknown_not_flat_squares(self):
        """An EMPTY fresh book (broker Not_Ok / hiccup) is 'unknown', NOT flat —
        must fall through and square (the default MockNoren has [] book; this is
        exactly the existing ~87-test path)."""
        client = MockNoren()  # empty position book
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is True
        assert result["via"] == "exit_order"
        assert len([o for o in client._orders.values() if o["trantype"] == "S"]) == 1

    def test_square_refuses_when_cancel_confirm_read_fails(self):
        """Fail-CLOSED: when the order-book read that CONFIRMS the resting SL was
        cancelled raises (expired token), square_position must NOT place a new
        exit (a resting SL + a fresh exit = naked short / margin reject). It
        returns squared=False so the caller keeps retrying — the position keeps
        its existing SL, so it is not left unprotected."""
        client = MockNoren()
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "65", "lp": "200.0"}
        ])
        client.script_read_error("order_book", "Session Expired : Invalid Session Key")
        pos = _position(netqty=65, lp=200.0, working_norenordno="W1")
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is False
        assert result["reason"] == "cancel_unconfirmed"
        # No SELL exit order was placed (would-be naked short refused).
        assert [o for o in client._orders.values() if o["trantype"] == "S"] == []

    def test_square_refuses_when_discovery_read_fails_with_empty_seeds(self):
        """Fail-CLOSED even with NO seed working-order: if the DISCOVERY order-book
        read raises (expired token), we cannot confirm there are no untracked
        resting orders → refuse the exit (squared=False), never place a possibly
        naked exit. (Guards the empty-seed_ids path that would otherwise short-
        circuit to cleared=True.)"""
        client = MockNoren()
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "65", "lp": "200.0"}
        ])
        client.script_read_error("order_book", "Session Expired : Invalid Session Key")
        pos = _position(netqty=65, lp=200.0)  # NO working_norenordno → empty seed_ids
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is False
        assert result["reason"] == "cancel_unconfirmed"
        assert [o for o in client._orders.values() if o["trantype"] == "S"] == []

    def test_fresh_book_other_tsym_only_squares_this_one(self):
        """A non-empty book that contains OTHER scrips but not this tsym → this
        tsym is absent → treated as flat (no order). (No silent square of a
        position the broker no longer reports.)"""
        client = MockNoren()
        client.set_position_book([
            {"tsym": "SOMEOTHER25CE", "exch": "NFO", "netqty": "30", "lp": "50.0"}
        ])
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="deadline"))
        assert result["via"] == "already_flat"
        assert len(client._orders) == 0

    def test_position_book_raising_is_unknown_falls_through(self):
        """If position_book() RAISES, treat as 'unknown' and fall through to the
        existing place path (which itself validates) — never a false already-flat."""
        class _RaisingBookClient(MockNoren):
            async def position_book(self):
                raise RuntimeError("broker book unavailable")
        client = _RaisingBookClient()
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is True
        assert result["via"] == "exit_order"
        assert len([o for o in client._orders.values() if o["trantype"] == "S"]) == 1


# ---------------------------------------------------------------------------
# square_position — unfilled entry (netqty == 0)
# ---------------------------------------------------------------------------

class TestSquarePositionUnfilledEntry:
    def test_zero_netqty_with_working_order_cancels_and_returns_squared(self):
        """Entry never filled: cancel the working order, no exit order, squared=True."""
        client = MockNoren()
        # Pre-load a working order for cancel_order to find
        placed = run(client.place_order(
            __import__("app.live.broker_protocol", fromlist=["OrderIntent"]).OrderIntent(
                client_order_id="cid1",
                trantype="B",
                prctyp="LMT",
                exch="NFO",
                tsym="NIFTY2662221000CE",
                qty=65,
                prc=200.0,
            )
        ))
        norenordno = placed.norenordno
        pos = _position(netqty=0, lp=200.0, working_norenordno=norenordno)
        result = run(square_position(client, pos, reason="cancel_only"))
        assert result["squared"] is True
        assert result["via"] == "cancel"
        assert result["note"] == "no position"
        # Confirm cancel was called
        assert client._orders[norenordno]["status"] == "CANCELED"
        # No new exit order should have been placed (still just the one original order)
        assert len(client._orders) == 1


# ---------------------------------------------------------------------------
# square_position — partial fill (working_norenordno + netqty > 0)
# ---------------------------------------------------------------------------

class TestSquarePositionPartialFill:
    def test_partial_fill_cancels_remainder_then_exits(self):
        """Partial: working_norenordno present + netqty 30 → cancel then SELL 30."""
        from app.live.broker_protocol import OrderIntent as OI
        client = MockNoren()
        # Place a "working" order (the unfilled remainder)
        placed = run(client.place_order(OI(
            client_order_id="cid-partial",
            trantype="B",
            prctyp="LMT",
            exch="NFO",
            tsym="NIFTY2662221000CE",
            qty=65,
            prc=200.0,
        )))
        norenordno = placed.norenordno
        pos = _position(netqty=30, lp=200.0, working_norenordno=norenordno)
        result = run(square_position(client, pos, reason="partial"))
        # Cancel happened
        assert client._orders[norenordno]["status"] == "CANCELED"
        # Exit order placed
        assert result["squared"] is True
        exit_orders = [o for n, o in client._orders.items() if n != norenordno]
        assert len(exit_orders) == 1
        assert exit_orders[0]["trantype"] == "S"
        assert exit_orders[0]["qty"] == 30


# ---------------------------------------------------------------------------
# square_position — unpriced (bad lp) → squared=False surfaced
# ---------------------------------------------------------------------------

class TestSquarePositionUnpriced:
    def _run_bad_lp(self, lp_value):
        client = MockNoren()
        pos = _position(netqty=65, lp=lp_value)
        result = run(square_position(client, pos, reason="test"))
        return result, client

    def test_lp_missing_returns_unpriced(self):
        client = MockNoren()
        pos = {"tsym": "X", "exch": "NFO", "netqty": 65}  # no 'lp' key
        result = run(square_position(client, pos, reason="test"))
        assert result["squared"] is False
        assert result["reason"] == "unpriced"
        # No exit order should have been placed
        assert len(client._orders) == 0

    def test_lp_zero_returns_unpriced(self):
        result, client = self._run_bad_lp(0)
        assert result["squared"] is False
        assert result["reason"] == "unpriced"
        assert len(client._orders) == 0

    def test_lp_nan_returns_unpriced(self):
        result, client = self._run_bad_lp(float("nan"))
        assert result["squared"] is False
        assert result["reason"] == "unpriced"
        assert len(client._orders) == 0

    def test_lp_negative_returns_unpriced(self):
        result, client = self._run_bad_lp(-10.0)
        assert result["squared"] is False
        assert result["reason"] == "unpriced"
        assert len(client._orders) == 0

    def test_lp_none_returns_unpriced(self):
        result, client = self._run_bad_lp(None)
        assert result["squared"] is False
        assert result["reason"] == "unpriced"

    def test_lp_string_non_numeric_returns_unpriced(self):
        result, client = self._run_bad_lp("bad")
        assert result["squared"] is False
        assert result["reason"] == "unpriced"


# ---------------------------------------------------------------------------
# square_position — retry-once logic
# ---------------------------------------------------------------------------

class TestSquarePositionRetry:
    def test_first_reject_then_success_squared_true(self):
        """Single scripted reject → retry → success → squared=True."""
        client = MockNoren()
        client.script_reject("RMS limit exceeded")
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is True
        assert result["via"] == "exit_order"
        assert result["norenordno"] is not None
        # The first attempt was recorded in failures
        assert len(result["failures"]) == 1
        assert "RMS limit exceeded" in result["failures"][0]

    def test_two_rejects_squared_false_no_raise(self):
        """Two consecutive scripted rejects → squared=False, failures=[...], no raise.

        MockNoren has a single-slot reject queue so we use DoubleRejectClient which
        correctly queues two consecutive rejections (FIFO).
        """
        client = DoubleRejectClient(["RMS limit exceeded", "Insufficient margin"])
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="deadline"))
        assert result["squared"] is False
        assert len(result["failures"]) == 2
        assert any("RMS limit exceeded" in f for f in result["failures"])
        assert any("Insufficient margin" in f for f in result["failures"])

    def test_two_rejects_no_raise(self):
        """Confirmed: square_position NEVER raises even on two rejects."""
        client = DoubleRejectClient(["err1", "err2"])
        pos = _position(netqty=65, lp=200.0)
        # Should not raise
        result = run(square_position(client, pos, reason="test"))
        assert isinstance(result, dict)

    def test_squared_true_only_when_exit_accepted(self):
        """squared=True MUST mean an exit order was actually accepted by the broker."""
        client = MockNoren()
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="test"))
        if result["squared"] and result["via"] == "exit_order":
            # There must be an order in the book
            assert result["norenordno"] is not None
            assert result["norenordno"] in client._orders
        elif result["squared"] and result["via"] == "cancel":
            # No position was open; squared by cancel only
            assert result["norenordno"] is None


# ---------------------------------------------------------------------------
# square_position — adversarial: exception from place_order
# ---------------------------------------------------------------------------

class DoubleRejectClient:
    """A mock client whose place_order rejects the first N calls, then succeeds.

    Used when we need more than one scripted reject (MockNoren has only a
    single-slot _next_reject_reason, so the second script_reject call
    overwrites the first; this helper uses a proper queue).
    """

    def __init__(self, reject_reasons: list[str]) -> None:
        self._rejects = list(reject_reasons)  # consumed FIFO
        self.placed: list = []

    async def cancel_order(self, norenordno: str):
        from app.live.broker_protocol import OrderResult
        return OrderResult(ok=True, norenordno=norenordno)

    async def place_order(self, intent):
        from app.live.broker_protocol import OrderResult
        if self._rejects:
            reason = self._rejects.pop(0)
            return OrderResult(ok=False, rejreason=reason)
        # Accept and return a fake norenordno
        norenordno = f"FAKE{len(self.placed) + 1}"
        self.placed.append(intent)
        return OrderResult(ok=True, norenordno=norenordno)


class RaisingClient:
    """A mock client whose place_order always raises."""

    async def cancel_order(self, norenordno: str):
        from app.live.broker_protocol import OrderResult
        return OrderResult(ok=True, norenordno=norenordno)

    async def place_order(self, intent):
        raise RuntimeError("broker socket closed")


class TestSquarePositionNeverRaises:
    def test_place_order_exception_does_not_propagate(self):
        """If place_order raises, square_position must NOT raise — returns squared=False."""
        client = RaisingClient()
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="test"))
        assert isinstance(result, dict)
        assert result["squared"] is False
        assert len(result["failures"]) >= 1
        # The exception message should be captured
        assert any("broker socket closed" in f for f in result["failures"])


# ---------------------------------------------------------------------------
# square_position — result structure completeness
# ---------------------------------------------------------------------------

class TestSquarePositionResultStructure:
    def _required_keys(self):
        return {"squared", "via", "norenordno", "reason", "note", "failures"}

    def test_success_result_has_all_keys(self):
        client = MockNoren()
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="test"))
        assert self._required_keys().issubset(result.keys())

    def test_unpriced_result_has_all_keys(self):
        client = MockNoren()
        pos = _position(netqty=65, lp=None)
        result = run(square_position(client, pos, reason="test"))
        assert self._required_keys().issubset(result.keys())

    def test_double_reject_result_has_all_keys(self):
        client = DoubleRejectClient(["e1", "e2"])
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="test"))
        assert self._required_keys().issubset(result.keys())


# ---------------------------------------------------------------------------
# build_sl_backstop_intent — tick alignment (new)
# ---------------------------------------------------------------------------

class TestBuildSlBackstopIntentTickAlignment:
    """build_sl_backstop_intent must produce tick-aligned prices."""

    def _make(self, stop_trigger, tick=0.05, **kw):
        defaults = dict(
            exch="NFO",
            tsym="NIFTY2662221000CE",
            qty=65,
            stop_trigger=stop_trigger,
            client_order_id=new_client_order_id(),
            tick=tick,
        )
        defaults.update(kw)
        return build_sl_backstop_intent(**defaults)

    def test_trgprc_is_005_multiple(self):
        """trgprc must be a 0.05 multiple (nearest rounding)."""
        intent = self._make(100.03)  # raw not on tick
        assert intent is not None
        assert abs(round(intent.trgprc / 0.05) * 0.05 - intent.trgprc) < 1e-9, (
            f"trgprc={intent.trgprc!r} is not a 0.05 multiple"
        )

    def test_prc_is_005_multiple(self):
        """prc must be a 0.05 multiple (down rounding)."""
        intent = self._make(100.03)
        assert intent is not None
        assert abs(round(intent.prc / 0.05) * 0.05 - intent.prc) < 1e-9, (
            f"prc={intent.prc!r} is not a 0.05 multiple"
        )

    def test_protective_invariant_prc_le_trgprc(self):
        """prc <= trgprc must hold after tick rounding."""
        for trigger in [0.10, 1.0, 84.0, 100.03, 250.07, 9999.01]:
            intent = self._make(trigger)
            if intent is not None:
                assert intent.prc <= intent.trgprc, (
                    f"prc={intent.prc!r} > trgprc={intent.trgprc!r} for trigger={trigger!r}"
                )

    def test_prc_gt_zero(self):
        """prc must always be > 0."""
        intent = self._make(100.0)
        assert intent is not None
        assert intent.prc > 0

    def test_default_tick_005_when_not_supplied(self):
        """Calling without tick kwarg (old callers) still works — defaults to 0.05."""
        # Call without tick= kwarg to verify backward compat
        intent = build_sl_backstop_intent(
            exch="NFO",
            tsym="NIFTY2662221000CE",
            qty=65,
            stop_trigger=100.03,
            client_order_id=new_client_order_id(),
        )
        assert intent is not None
        assert abs(round(intent.trgprc / 0.05) * 0.05 - intent.trgprc) < 1e-9


# ---------------------------------------------------------------------------
# TICK ROUNDING — _marketable_prc + square_position exit prices (critical safety)
# ---------------------------------------------------------------------------
# Regression tests for: auto_square._marketable_prc used round(ref*(1±eff/100), 2)
# NOT tick-aligned, so the broker rejected exit orders with
# "Price 53.61 is not a multiple of tick size 0.05" and the position stayed open.
# ---------------------------------------------------------------------------

from app.live.auto_square import _marketable_prc


def _is_tick_aligned(prc: float, tick: float = 0.05) -> bool:
    from decimal import Decimal
    return (Decimal(str(prc)) % Decimal(str(tick))) == 0


class TestMarketablePrcTickAlignment:
    """_marketable_prc must return exact 0.05 multiples."""

    def test_sell_ref_53_90_is_tick_aligned(self):
        """Reproduction: ref=53.90, SELL → prc is 0.05 multiple and <= ref."""
        prc = _marketable_prc(53.90, "S", 1.0)
        assert _is_tick_aligned(prc), f"SELL prc {prc} is not a 0.05 multiple"
        assert prc <= 53.90, f"SELL prc {prc} > ref 53.90 (not marketable)"

    def test_buy_ref_53_90_is_tick_aligned(self):
        """BUY exit → prc is 0.05 multiple and >= ref."""
        prc = _marketable_prc(53.90, "B", 1.0)
        assert _is_tick_aligned(prc), f"BUY prc {prc} is not a 0.05 multiple"
        assert prc >= 53.90, f"BUY prc {prc} < ref 53.90 (not marketable)"

    def test_sell_various_refs_all_tick_aligned(self):
        """Sweep: SELL exit is tick-aligned for a range of ref prices."""
        refs = [53.61, 53.90, 100.33, 200.77, 75.12, 120.48, 1.03, 9999.97]
        for ref in refs:
            prc = _marketable_prc(ref, "S", 1.0)
            assert _is_tick_aligned(prc), (
                f"ref={ref}: SELL prc {prc} is not a 0.05 multiple"
            )
            assert prc <= ref, f"ref={ref}: SELL prc {prc} > ref (not marketable)"

    def test_buy_various_refs_all_tick_aligned(self):
        """Sweep: BUY exit is tick-aligned for a range of ref prices."""
        refs = [53.61, 53.90, 100.33, 200.77, 75.12, 120.48, 1.03, 9999.97]
        for ref in refs:
            prc = _marketable_prc(ref, "B", 1.0)
            assert _is_tick_aligned(prc), (
                f"ref={ref}: BUY prc {prc} is not a 0.05 multiple"
            )
            assert prc >= ref, f"ref={ref}: BUY prc {prc} < ref (not marketable)"

    def test_invalid_tick_falls_back_to_005(self):
        """tick <= 0 is guarded and falls back to 0.05."""
        prc = _marketable_prc(100.33, "S", 1.0, tick=0.0)
        assert _is_tick_aligned(prc, 0.05), (
            f"Fallback tick=0.0: SELL prc {prc} is not a 0.05 multiple"
        )


class TestSquarePositionTickAlignment:
    """square_position placed exit prices must be 0.05-aligned (live executor path)."""

    def test_sell_exit_53_90_tick_aligned(self):
        """Live-failure reproduction: ref=53.90, SELL exit → placed prc is 0.05 multiple.

        The broker rejected the order when prc was not a tick multiple.
        After the fix, the placed order price must satisfy: round(prc/0.05)*0.05 == prc.
        """
        client = MockNoren()
        pos = _position(netqty=65, lp=53.90)
        run(square_position(client, pos, reason="deadline", band_pct=1.0))
        orders = list(client._orders.values())
        assert len(orders) == 1
        prc = orders[0]["prc"]
        assert _is_tick_aligned(prc), (
            f"CRITICAL: SELL exit prc {prc} is not a 0.05 multiple — broker rejects!"
        )
        assert prc <= 53.90, f"SELL exit prc {prc} > ref 53.90 (not marketable)"

    def test_buy_exit_53_90_tick_aligned(self):
        """BUY-to-close exit → placed prc is 0.05 multiple and >= ref."""
        client = MockNoren()
        pos = _position(netqty=-65, lp=53.90)
        run(square_position(client, pos, reason="deadline", band_pct=1.0))
        orders = list(client._orders.values())
        assert len(orders) == 1
        prc = orders[0]["prc"]
        assert _is_tick_aligned(prc), (
            f"CRITICAL: BUY exit prc {prc} is not a 0.05 multiple — broker rejects!"
        )
        assert prc >= 53.90, f"BUY exit prc {prc} < ref 53.90 (not marketable)"

    def test_sell_exit_various_refs_tick_aligned(self):
        """Sweep: square_position SELL exit is tick-aligned for various refs."""
        refs = [53.61, 53.90, 100.33, 200.77, 75.12]
        for ref in refs:
            client = MockNoren()
            pos = _position(netqty=65, lp=ref)
            run(square_position(client, pos, reason="test"))
            prc = list(client._orders.values())[0]["prc"]
            assert _is_tick_aligned(prc), (
                f"ref={ref}: square_position SELL prc {prc} is not a 0.05 multiple"
            )

    def test_buy_exit_various_refs_tick_aligned(self):
        """Sweep: square_position BUY exit is tick-aligned for various refs."""
        refs = [53.61, 53.90, 100.33, 200.77, 75.12]
        for ref in refs:
            client = MockNoren()
            pos = _position(netqty=-65, lp=ref)
            run(square_position(client, pos, reason="test"))
            prc = list(client._orders.values())[0]["prc"]
            assert _is_tick_aligned(prc), (
                f"ref={ref}: square_position BUY prc {prc} is not a 0.05 multiple"
            )


# ---------------------------------------------------------------------------
# P1.4 — margin-safe square-off: cancel ALL working orders for the scrip
# (incl. a resting SL discovered in the book) + confirm terminal before exit.
# ---------------------------------------------------------------------------

from app.live.broker_protocol import OrderIntent as _OI  # noqa: E402
from app.live.kill_switch import TERMINAL as _TERMINAL  # noqa: E402


class MarginAwareMockNoren(MockNoren):
    """A MockNoren that rejects a SELL when another working SELL exists for the
    same tsym — simulating the broker's naked-short margin reject. This is the
    exact failure mode of the ₹2.16L bug: a resting SL sell + a square-off sell.
    """

    async def place_order(self, intent):
        if intent.trantype == "S":
            for o in self._orders.values():
                if (
                    str(o.get("tsym")) == str(intent.tsym)
                    and o.get("trantype") == "S"
                    and str(o.get("status", "")).strip().upper() not in _TERMINAL
                ):
                    from app.live.broker_protocol import OrderResult
                    return OrderResult(
                        ok=False,
                        rejreason="Margin shortfall: naked short — cancel resting SL first",
                    )
        return await super().place_order(intent)


def _place_open(client, *, trantype, tsym="NIFTY2662221000CE", qty=65, prc=200.0,
                prctyp="LMT", trgprc=None):
    """Place an OPEN order in the mock book and return its norenordno."""
    res = run(client.place_order(_OI(
        client_order_id=f"seed-{trantype}-{prc}",
        trantype=trantype, prctyp=prctyp, exch="NFO", tsym=tsym,
        qty=qty, prc=prc, trgprc=trgprc,
    )))
    return res.norenordno


class TestSquarePositionMarginSafe:
    def test_cancels_resting_sl_before_exit_no_margin_reject(self):
        """THE ₹2.16L BUG: a resting SL sell must be cancelled BEFORE the exit
        sell, or the broker rejects it as a naked short. square_position must
        discover + cancel the resting SL, then place the exit successfully."""
        client = MarginAwareMockNoren()
        sl_id = _place_open(client, trantype="S", prctyp="SL-LMT", prc=150.0, trgprc=155.0)
        pos = _position(netqty=65, lp=200.0)  # long 65, no working id passed
        result = run(square_position(client, pos, reason="kill"))
        # resting SL was cancelled
        assert client._orders[sl_id]["status"] == "CANCELED"
        # exit succeeded (would have been margin-rejected if the SL were still working)
        assert result["squared"] is True
        assert result["via"] == "exit_order"
        # exactly one new working SELL exit exists
        new_sells = [o for n, o in client._orders.items()
                     if n != sl_id and o["trantype"] == "S"
                     and str(o["status"]).upper() not in _TERMINAL]
        assert len(new_sells) == 1
        assert new_sells[0]["qty"] == 65

    def test_discovers_all_working_orders_for_scrip(self):
        """An entry remainder AND a resting SL — both for the scrip — are
        cancelled even though only one id is passed."""
        client = MockNoren()
        entry_id = _place_open(client, trantype="B", prc=200.0)
        sl_id = _place_open(client, trantype="S", prctyp="SL-LMT", prc=150.0, trgprc=155.0)
        pos = _position(netqty=65, lp=200.0, working_norenordno=entry_id)
        result = run(square_position(client, pos, reason="kill"))
        assert client._orders[entry_id]["status"] == "CANCELED"
        assert client._orders[sl_id]["status"] == "CANCELED"
        assert result["squared"] is True

    def test_working_norenordnos_list_is_honored(self):
        """The position may carry an explicit list of resting orders to cancel."""
        client = MockNoren()
        a = _place_open(client, trantype="B", prc=200.0)
        b = _place_open(client, trantype="S", prctyp="SL-LMT", prc=150.0, trgprc=155.0)
        pos = _position(netqty=65, lp=200.0)
        pos["working_norenordnos"] = [a, b]
        result = run(square_position(client, pos, reason="kill"))
        assert client._orders[a]["status"] == "CANCELED"
        assert client._orders[b]["status"] == "CANCELED"
        assert result["squared"] is True

    def test_does_not_strip_protection_when_unpriced(self):
        """If lp is unpriced we CANNOT place an exit — so we must NOT cancel the
        protective SL (don't leave the position naked AND unexited)."""
        client = MockNoren()
        sl_id = _place_open(client, trantype="S", prctyp="SL-LMT", prc=150.0, trgprc=155.0)
        pos = _position(netqty=65, lp=None)  # unpriced
        result = run(square_position(client, pos, reason="kill"))
        assert result["squared"] is False
        assert result["reason"] == "unpriced"
        # SL must still be working — protection preserved
        assert client._orders[sl_id]["status"] == "OPEN"


class StubbornCancelClient:
    """cancel_order returns ok but NEVER actually clears the order (the broker
    keeps it working). order_book keeps reporting it non-terminal. Models a
    cancel that won't take — square_position must refuse to place the exit."""

    def __init__(self, tsym):
        from app.live.broker_protocol import OrderResult  # noqa
        self._tsym = tsym
        self._book = [{"norenordno": "STUCK1", "tsym": tsym, "status": "OPEN",
                       "trantype": "S"}]
        self.placed = []

    async def cancel_order(self, norenordno):
        from app.live.broker_protocol import OrderResult
        return OrderResult(ok=True, norenordno=norenordno)  # ack but never clears

    async def order_book(self):
        return list(self._book)

    async def place_order(self, intent):
        from app.live.broker_protocol import OrderResult
        self.placed.append(intent)
        return OrderResult(ok=True, norenordno="EXIT1")


class TestSquarePositionCancelUnconfirmed:
    def test_unconfirmed_cancel_refuses_to_place_exit(self):
        """A working order that survives all cancel passes → squared=False,
        reason 'cancel_unconfirmed', and NO exit order placed (margin-safe)."""
        tsym = "NIFTY2662221000CE"
        client = StubbornCancelClient(tsym)
        pos = _position(netqty=65, lp=200.0, tsym=tsym)
        result = run(square_position(client, pos, reason="kill"))
        assert result["squared"] is False
        assert result["reason"] == "cancel_unconfirmed"
        assert "STUCK1" in result["failures"]
        # CRITICAL: no exit order was placed into a guaranteed margin reject
        assert client.placed == []

    def test_unconfirmed_cancel_on_unfilled_entry(self):
        """netqty==0 path: an un-cancellable working entry also surfaces
        cancel_unconfirmed rather than falsely reporting squared via cancel."""
        tsym = "NIFTY2662221000CE"
        client = StubbornCancelClient(tsym)
        pos = _position(netqty=0, lp=200.0, tsym=tsym)
        result = run(square_position(client, pos, reason="kill"))
        assert result["squared"] is False
        assert result["reason"] == "cancel_unconfirmed"


class LaggyCancelClient(MockNoren):
    """A MockNoren whose cancel only takes effect on the 2nd attempt per order —
    models broker eventual consistency. square_position's 2-pass confirm loop
    must absorb this and still clear the book before placing the exit."""

    def __init__(self):
        super().__init__()
        self._cancel_attempts = {}

    async def cancel_order(self, norenordno):
        from app.live.broker_protocol import OrderResult
        n = self._cancel_attempts.get(norenordno, 0) + 1
        self._cancel_attempts[norenordno] = n
        if n >= 2 and norenordno in self._orders:
            self._orders[norenordno]["status"] = "CANCELED"
            return OrderResult(ok=True, norenordno=norenordno)
        return OrderResult(ok=True, norenordno=norenordno)  # ack but not yet cleared


class TestSquarePositionDiscoveryScoping:
    def test_does_not_cancel_other_scrips_working_orders(self):
        """Discovery is scoped to the position's tsym — a working order for a
        DIFFERENT scrip must be left untouched."""
        client = MockNoren()
        mine = _place_open(client, trantype="S", tsym="NIFTY2662221000CE",
                           prctyp="SL-LMT", prc=150.0, trgprc=155.0)
        other = _place_open(client, trantype="S", tsym="BANKNIFTY2662250000CE",
                            prctyp="SL-LMT", prc=150.0, trgprc=155.0)
        pos = _position(netqty=65, lp=200.0, tsym="NIFTY2662221000CE")
        result = run(square_position(client, pos, reason="kill"))
        assert result["squared"] is True
        assert client._orders[mine]["status"] == "CANCELED"
        # the unrelated scrip's resting order is NOT cancelled
        assert client._orders[other]["status"] == "OPEN"

    def test_lagging_cancel_clears_on_second_pass(self):
        """A cancel that only takes effect on retry is absorbed by the 2-pass
        confirm loop; the exit is still placed after the book is clear."""
        client = LaggyCancelClient()
        sl = _place_open(client, trantype="S", prctyp="SL-LMT", prc=150.0, trgprc=155.0)
        pos = _position(netqty=65, lp=200.0)
        result = run(square_position(client, pos, reason="kill"))
        assert client._orders[sl]["status"] == "CANCELED"
        assert result["squared"] is True
        assert result["via"] == "exit_order"


# ---------------------------------------------------------------------------
# square_position — depth-aware square price via GetQuotes (Task C3)
# ---------------------------------------------------------------------------
# The disaster square prices its marketable limit off position["lp"] (a possibly
# stale mark). When a contract `token` is available, refresh the reference price
# from a FRESH GetQuotes so the exit actually clears. The refresh is GATED on
# position.get("token"): token-less positions (the existing ~94 fixtures) are
# byte-identical. The refresh composes with the B4 netqty re-confirm — it runs
# AFTER the fresh position_book() re-confirm passes (still non-flat), BEFORE the
# marketable-limit price is computed.
# ---------------------------------------------------------------------------

def _position_with_token(netqty, lp, token="999", exch="NFO",
                         tsym="NIFTY2662221000CE"):
    return {
        "tsym": tsym,
        "exch": exch,
        "netqty": netqty,
        "lp": lp,
        "token": token,
    }


class TestSquarePositionDepthAwarePrice:
    def test_token_refreshes_ref_price_from_get_quotes(self):
        """A position carrying a token + a fresh book that STILL shows it non-flat
        → the placed exit price is computed off the GetQuotes lp (98.5), NOT the
        stale position lp (100)."""
        client = MockNoren()
        client.set_quotes({"stat": "Ok", "lp": "98.5"})
        # B4 re-confirm: a non-empty book that still shows this tsym non-flat.
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "65", "lp": "100"}
        ])
        pos = _position_with_token(netqty="65", lp=100, token="999")
        result = run(square_position(client, pos, reason="deadline", band_pct=1.0))
        assert result["squared"] is True
        assert result["via"] == "exit_order"
        orders = [o for o in client._orders.values() if o["trantype"] == "S"]
        assert len(orders) == 1
        # SELL marketable from fresh ref 98.5, NOT stale 100.
        expected = round_to_tick(98.5 * (1 - 1.0 / 100), 0.05, mode="down")
        stale = round_to_tick(100 * (1 - 1.0 / 100), 0.05, mode="down")
        assert orders[0]["prc"] == expected
        assert orders[0]["prc"] != stale

    def test_no_token_prices_off_lp_unchanged(self):
        """A position WITHOUT a token → priced off lp (existing behavior). Even if
        the client has quotes loaded, no token means no refresh."""
        client = MockNoren()
        client.set_quotes({"stat": "Ok", "lp": "98.5"})  # present but must be ignored
        pos = _position(netqty=65, lp=200.0)  # no token key
        assert "token" not in pos
        result = run(square_position(client, pos, reason="deadline", band_pct=1.0))
        assert result["squared"] is True
        orders = list(client._orders.values())
        assert len(orders) == 1
        # Priced off stale lp 200.0 — the GetQuotes 98.5 was NOT consulted.
        assert orders[0]["prc"] == round_to_tick(200.0 * (1 - 1.0 / 100), 0.05, mode="down")

    def test_get_quotes_raising_falls_back_to_lp(self):
        """A token is present but get_quotes RAISES → fall back to position lp."""
        class _RaisingQuotesClient(MockNoren):
            async def get_quotes(self, exch, token):
                raise RuntimeError("quotes unavailable")
        client = _RaisingQuotesClient()
        pos = _position_with_token(netqty="65", lp=100, token="999")
        result = run(square_position(client, pos, reason="deadline", band_pct=1.0))
        assert result["squared"] is True
        orders = list(client._orders.values())
        assert len(orders) == 1
        assert orders[0]["prc"] == round_to_tick(100 * (1 - 1.0 / 100), 0.05, mode="down")

    def test_get_quotes_empty_falls_back_to_lp(self):
        """get_quotes returns {} (broker Not_Ok) → no usable lp → fall back."""
        client = MockNoren()
        client.set_quotes({})  # empty
        pos = _position_with_token(netqty="65", lp=100, token="999")
        result = run(square_position(client, pos, reason="deadline", band_pct=1.0))
        assert result["squared"] is True
        orders = list(client._orders.values())
        assert len(orders) == 1
        assert orders[0]["prc"] == round_to_tick(100 * (1 - 1.0 / 100), 0.05, mode="down")

    def test_get_quotes_nonpositive_lp_falls_back_to_lp(self):
        """get_quotes returns a non-positive / non-finite lp → fall back to position lp."""
        client = MockNoren()
        client.set_quotes({"stat": "Ok", "lp": "0"})  # zero → unusable
        pos = _position_with_token(netqty="65", lp=100, token="999")
        result = run(square_position(client, pos, reason="deadline", band_pct=1.0))
        assert result["squared"] is True
        orders = list(client._orders.values())
        assert orders[0]["prc"] == round_to_tick(100 * (1 - 1.0 / 100), 0.05, mode="down")

    def test_get_quotes_passed_position_exch_and_token(self):
        """The refresh queries get_quotes with the position's exch + token."""
        seen = {}
        class _RecordingClient(MockNoren):
            async def get_quotes(self, exch, token):
                seen["exch"] = exch
                seen["token"] = token
                return {"stat": "Ok", "lp": "98.5"}
        client = _RecordingClient()
        pos = _position_with_token(netqty="65", lp=100, token="T42", exch="BFO")
        run(square_position(client, pos, reason="deadline"))
        assert seen == {"exch": "BFO", "token": "T42"}

    def test_token_refresh_skipped_when_already_flat(self):
        """If the B4 re-confirm reports already-flat, NO order is placed regardless
        of the token/quotes (the refresh must compose AFTER the re-confirm)."""
        client = MockNoren()
        client.set_quotes({"stat": "Ok", "lp": "98.5"})
        client.set_position_book([
            {"tsym": "NIFTY2662221000CE", "exch": "NFO", "netqty": "0", "lp": "100"}
        ])
        pos = _position_with_token(netqty="65", lp=100, token="999")
        result = run(square_position(client, pos, reason="deadline"))
        assert result["via"] == "already_flat"
        assert len(client._orders) == 0


# ---------------------------------------------------------------------------
# reprice_exit_leg — Layer 2 over-sell-safe widening re-price primitive.
# ---------------------------------------------------------------------------
_RTSYM = "NIFTY2662221000CE"


class _AlwaysReject(MockNoren):
    async def place_order(self, intent):
        return OrderResult(ok=False, rejreason="RMS reject", raw={})


class _CancelNoop(MockNoren):
    """cancel_order reports ok but does NOT mark the order terminal → the confirm
    re-fetch still sees it working → cancel_unconfirmed."""
    async def cancel_order(self, ordno):
        return OrderResult(ok=True, norenordno=ordno)


class _BlipConfirmClient(MockNoren):
    """order_book returns [] on the CONFIRM re-fetch (a throttled-broker Not_Ok blip →
    _cancel_all_working_for_scrip optimistically reports cleared) but the prior order
    RECOVERS visible-OPEN on the subsequent _order_row read. cancel_order does not mark
    the order terminal (the cancel didn't take). Exercises the BUG-1 double-sell race."""

    def __init__(self, prior):
        super().__init__()
        self._prior = prior
        self._ob = 0

    async def cancel_order(self, ordno):
        return OrderResult(ok=True, norenordno=ordno)

    async def order_book(self):
        self._ob += 1
        # 1 = discovery, 2 = confirm pass (BLIP → []), 3+ = _order_row (recover).
        return [] if self._ob == 2 else [dict(self._prior)]


class TestRepriceExitLeg:
    def _client(self, *, prev=None, book=None, quote=None, cls=MockNoren):
        cl = cls()
        if prev is not None:
            cl._orders[prev["norenordno"]] = prev
        cl.set_position_book(book if book is not None else [])
        cl.set_quotes(quote or {"lp": "170", "bp1": "168", "sp1": "172", "lc": "150", "uc": "190"})
        return cl

    def _pos(self, netqty="20", lp="170", token="999"):
        return {"tsym": _RTSYM, "exch": "NFO", "netqty": netqty, "lp": lp, "prd": "I", "token": token}

    def _placed(self, cl, exclude="PREV1"):
        return [o for o in cl._orders.values() if o["norenordno"] != exclude]

    def test_unpriced_no_cancel_no_place(self):
        """No usable anchor (no quote bid AND no lp) → unpriced; nothing cancelled/placed."""
        cl = MockNoren()
        cl.set_quotes({})  # no bp1
        cl._orders["PREV1"] = {"norenordno": "PREV1", "tsym": _RTSYM, "status": "OPEN", "fillshares": "0"}
        pos = {"tsym": _RTSYM, "exch": "NFO", "netqty": "20", "lp": None, "prd": "I", "token": None}
        r = run(reprice_exit_leg(cl, pos, band_pct=2.0, prev_ordno="PREV1", prev_qty=20, reason="stop_reprice"))
        assert r["squared"] is False and r["reason"] == "unpriced"
        assert cl._orders["PREV1"]["status"] == "OPEN"  # NOT cancelled
        assert len(cl._orders) == 1                     # nothing placed

    def test_over_sell_safe_sizes_to_confirmed_remaining(self):
        """prev_qty 20, prior order filled 10, book shows 10 → places exactly 10 (not 20)."""
        prev = {"norenordno": "PREV1", "tsym": _RTSYM, "status": "OPEN", "fillshares": "10", "qty": 20}
        cl = self._client(prev=prev, book=[{"tsym": _RTSYM, "exch": "NFO", "netqty": "10", "lp": "170"}])
        r = run(reprice_exit_leg(cl, self._pos(), band_pct=2.0, prev_ordno="PREV1", prev_qty=20, reason="stop_reprice"))
        assert r["squared"] is True and r["via"] == "exit_order" and r["qty"] == 10
        placed = self._placed(cl)
        assert len(placed) == 1 and placed[0]["qty"] == 10 and placed[0]["trantype"] == "S"
        assert cl._orders["PREV1"]["status"] == "CANCELED"  # prior exit cancelled first

    def test_book_floors_remaining_below_fillshares_math(self):
        """The KNOWN book floors the remaining — never sell more than the account holds
        (book 5 < prev_qty-filled 10 → place 5)."""
        prev = {"norenordno": "PREV1", "tsym": _RTSYM, "status": "OPEN", "fillshares": "10", "qty": 20}
        cl = self._client(prev=prev, book=[{"tsym": _RTSYM, "exch": "NFO", "netqty": "5", "lp": "170"}])
        r = run(reprice_exit_leg(cl, self._pos(), band_pct=2.0, prev_ordno="PREV1", prev_qty=20, reason="stop_reprice"))
        assert r["qty"] == 5

    def test_book_unknown_sizes_off_fillshares(self):
        """filled readable + position_book UNKNOWN (empty) → size off fillshares."""
        prev = {"norenordno": "PREV1", "tsym": _RTSYM, "status": "OPEN", "fillshares": "5", "qty": 20}
        cl = self._client(prev=prev, book=[])  # empty == UNKNOWN
        r = run(reprice_exit_leg(cl, self._pos(), band_pct=2.0, prev_ordno="PREV1", prev_qty=20, reason="stop_reprice"))
        assert r["squared"] is True and r["qty"] == 15  # 20 - 5

    def test_already_flat_when_fully_filled(self):
        """prior order fully filled + book flat → already_flat, nothing placed."""
        prev = {"norenordno": "PREV1", "tsym": _RTSYM, "status": "OPEN", "fillshares": "20", "qty": 20}
        cl = self._client(prev=prev, book=[{"tsym": _RTSYM, "exch": "NFO", "netqty": "0", "lp": "170"}])
        r = run(reprice_exit_leg(cl, self._pos(), band_pct=2.0, prev_ordno="PREV1", prev_qty=20, reason="stop_reprice"))
        assert r["squared"] is True and r["via"] == "already_flat" and r["remaining"] == 0
        assert self._placed(cl) == []

    def test_cancel_unconfirmed_when_fillshares_unreadable(self):
        """prior order absent from the book (fillshares unreadable) → cancel_unconfirmed,
        places nothing (cannot size safely)."""
        cl = self._client(prev=None, book=[{"tsym": _RTSYM, "exch": "NFO", "netqty": "20", "lp": "170"}])
        r = run(reprice_exit_leg(cl, self._pos(), band_pct=2.0, prev_ordno="PREV_GONE", prev_qty=20, reason="stop_reprice"))
        assert r["squared"] is False and r["reason"] == "cancel_unconfirmed"
        assert len(cl._orders) == 0  # nothing placed

    def test_cancel_unconfirmed_when_prior_exit_wont_die(self):
        """The prior exit can't be confirmed terminal → cancel_unconfirmed, no place."""
        cl = self._client(prev={"norenordno": "PREV1", "tsym": _RTSYM, "status": "OPEN", "fillshares": "0"},
                          book=[{"tsym": _RTSYM, "exch": "NFO", "netqty": "20", "lp": "170"}], cls=_CancelNoop)
        r = run(reprice_exit_leg(cl, self._pos(), band_pct=2.0, prev_ordno="PREV1", prev_qty=20, reason="stop_reprice"))
        assert r["squared"] is False and r["reason"] == "cancel_unconfirmed"
        assert len(cl._orders) == 1  # only the (uncancellable) prior order; nothing new

    def test_successful_place_bid_anchored_clamped(self):
        """A clean re-price places the full remaining at a bid-anchored, lc-clamped price."""
        prev = {"norenordno": "PREV1", "tsym": _RTSYM, "status": "OPEN", "fillshares": "0", "qty": 20}
        cl = self._client(prev=prev, book=[{"tsym": _RTSYM, "exch": "NFO", "netqty": "20", "lp": "170"}])
        r = run(reprice_exit_leg(cl, self._pos(), band_pct=4.0, prev_ordno="PREV1", prev_qty=20, reason="stop_reprice"))
        assert r["squared"] is True and r["via"] == "exit_order" and r["qty"] == 20
        placed = self._placed(cl)
        # SELL through bid 168 * (1 - 4%) = 161.28, clamped >= lc 150, tick-rounded.
        assert len(placed) == 1
        assert 150.0 <= placed[0]["prc"] <= 168.0 and placed[0]["trantype"] == "S"

    def test_reject_twice_returns_failures(self):
        """Two rejected place attempts → squared False with both reasons; no over-place."""
        cl = self._client(prev={"norenordno": "PREV1", "tsym": _RTSYM, "status": "OPEN", "fillshares": "0", "qty": 20},
                          book=[{"tsym": _RTSYM, "exch": "NFO", "netqty": "20", "lp": "170"}], cls=_AlwaysReject)
        r = run(reprice_exit_leg(cl, self._pos(), band_pct=2.0, prev_ordno="PREV1", prev_qty=20, reason="stop_reprice"))
        assert r["squared"] is False and len(r["failures"]) == 2

    def test_double_sell_guard_when_cancel_confirm_blips(self):
        """BUG-1 regression: the cancel-confirm order_book BLIPS ([] on the confirm read)
        so _cancel_all_working_for_scrip optimistically reports cleared, but the prior
        exit recovers OPEN on the next read. The primitive MUST require the prior order
        be TERMINAL before placing → cancel_unconfirmed, NEVER stack a 2nd resting SELL
        (a naked options short)."""
        prior = {"norenordno": "PREV1", "tsym": _RTSYM, "status": "OPEN", "fillshares": "0", "qty": 20}
        cl = _BlipConfirmClient(prior)
        cl.set_position_book([{"tsym": _RTSYM, "exch": "NFO", "netqty": "20", "lp": "170"}])
        cl.set_quotes({"lp": "170", "bp1": "168", "lc": "150"})
        r = run(reprice_exit_leg(cl, self._pos(), band_pct=2.0, prev_ordno="PREV1", prev_qty=20, reason="stop_reprice"))
        assert r["squared"] is False and r["reason"] == "cancel_unconfirmed"
        assert len(cl._orders) == 0, "NO new exit may be placed while the prior may still rest"

    def test_short_position_reprice_is_a_buy(self):
        """Direction invariant: a SHORT position (netqty<0) re-prices as a BUY-to-close,
        ask-anchored (sp1, +band, uc-clamped)."""
        prev = {"norenordno": "PREV1", "tsym": _RTSYM, "status": "OPEN", "fillshares": "0", "qty": 20}
        cl = self._client(prev=prev, book=[{"tsym": _RTSYM, "exch": "NFO", "netqty": "-20", "lp": "170"}])
        pos = {"tsym": _RTSYM, "exch": "NFO", "netqty": "-20", "lp": "170", "prd": "I", "token": "999"}
        r = run(reprice_exit_leg(cl, pos, band_pct=2.0, prev_ordno="PREV1", prev_qty=20, reason="x_reprice"))
        assert r["squared"] is True and r["via"] == "exit_order" and r["qty"] == 20
        placed = self._placed(cl)
        assert len(placed) == 1 and placed[0]["trantype"] == "B"  # BUY to close a short
        assert 172.0 <= placed[0]["prc"] <= 190.0                 # ask-anchored, uc-clamped
