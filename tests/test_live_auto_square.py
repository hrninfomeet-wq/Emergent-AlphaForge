"""TDD tests for backend/app/live/auto_square.py (Task L3.3).

Coverage
--------
deadline_iso:
  - fill "2026-06-22T10:00:00" + 600 s → "...10:10:00"
  - horizon clamped to 600 if a larger value is passed (e.g. 3600)
  - horizon < 600 respected (e.g. 300 s → 5 min)
  - timezone-aware fill time handled correctly
  - [FIX1] deadline_iso always emits a UTC-aware (+00:00) ISO string
  - [FIX1] IST-aware fill → UTC-normalized deadline (catastrophic pairing test)
  - [FIX1] is_due correctly orders aware-IST now vs UTC-aware deadline

is_due:
  - now before deadline → False
  - now exactly at deadline → True
  - now after deadline → True
  - unparseable deadline string → True (fail-safe square-now)
  - unparseable now string → True (fail-safe square-now)
  - both unparseable → True (fail-safe)
  - [FIX1] aware-UTC deadline vs aware-IST now that is genuinely past → True
  - [FIX1] aware-UTC deadline vs aware-IST now that is genuinely before → False
  - [FIX1] naive vs naive still compares correctly (back-compat)
  - [FIX1] aware now 30 min after deadline (both aware) → True

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
    SQUARE_HORIZON_SEC,
    _to_utc,
    deadline_iso,
    is_due,
    build_sl_backstop_intent,
    square_position,
)
from app.live.order_builder import round_to_tick
from app.live.idempotency import new_client_order_id


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
# deadline_iso
# ---------------------------------------------------------------------------

class TestDeadlineIso:
    def test_basic_10_minutes(self):
        dl = deadline_iso("2026-06-22T10:00:00")
        assert dl == "2026-06-22T10:10:00+00:00"

    def test_horizon_clamped_to_600(self):
        """Passing horizon_sec=3600 (1 hour) must be clamped to 600 s."""
        dl = deadline_iso("2026-06-22T10:00:00", horizon_sec=3600)
        # Clamped to 10:10:00, not 11:00:00
        assert "10:10:00" in dl
        assert "11:00:00" not in dl

    def test_horizon_below_600_respected(self):
        """horizon_sec=300 (5 min) should NOT be clamped."""
        dl = deadline_iso("2026-06-22T10:00:00", horizon_sec=300)
        assert "10:05:00" in dl

    def test_timezone_aware_fill_time(self):
        """Timezone-aware ISO strings are handled without error."""
        dl = deadline_iso("2026-06-22T10:00:00+05:30", horizon_sec=600)
        # Should be parseable and 10 minutes ahead
        from datetime import datetime
        dt = datetime.fromisoformat(dl)
        dt_fill = datetime.fromisoformat("2026-06-22T10:00:00+05:30")
        diff = (dt - dt_fill).total_seconds()
        assert diff == 600

    def test_square_horizon_sec_constant_is_600(self):
        assert SQUARE_HORIZON_SEC == 600

    # --- FIX 1 new tests ---

    def test_deadline_iso_always_emits_utc_aware_string(self):
        """[FIX1] deadline_iso must always return a UTC-aware (+00:00) ISO string,
        even when given a naive input."""
        dl = deadline_iso("2026-06-22T10:00:00")  # naive
        # Must be parseable and have tz offset
        from datetime import datetime
        dt = datetime.fromisoformat(dl)
        assert dt.tzinfo is not None, "deadline_iso returned a naive datetime string"
        from datetime import timezone as tz
        import datetime as _dt
        # Offset must be UTC (0)
        assert dt.utcoffset() == _dt.timedelta(0), (
            f"Expected UTC (+00:00) offset, got {dt.utcoffset()}"
        )

    def test_deadline_iso_ist_fill_normalizes_to_utc(self):
        """[FIX1] Catastrophic pairing: IST-aware fill → deadline must be UTC-normalized.
        Deadline from 10:00 IST (+05:30) = 04:30 UTC + 10min = 04:40 UTC."""
        dl = deadline_iso("2026-06-22T10:00:00+05:30", horizon_sec=600)
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(dl)
        # Must be UTC-aware
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0, (
            f"Expected UTC offset, got {dt.utcoffset()}"
        )
        # 10:00 IST = 04:30 UTC; + 10 min = 04:40 UTC
        assert dt.hour == 4 and dt.minute == 40, (
            f"Expected 04:40 UTC, got {dt.isoformat()}"
        )


# ---------------------------------------------------------------------------
# is_due
# ---------------------------------------------------------------------------

class TestIsDue:
    def test_before_deadline_returns_false(self):
        assert is_due("2026-06-22T10:10:00", "2026-06-22T10:05:00") is False

    def test_at_deadline_returns_true(self):
        assert is_due("2026-06-22T10:10:00", "2026-06-22T10:10:00") is True

    def test_after_deadline_returns_true(self):
        assert is_due("2026-06-22T10:10:00", "2026-06-22T10:15:00") is True

    def test_unparseable_deadline_returns_true(self):
        """Fail-safe: bad deadline string → square now."""
        assert is_due("not-a-date", "2026-06-22T10:05:00") is True

    def test_unparseable_now_returns_true(self):
        """Fail-safe: bad now string → square now."""
        assert is_due("2026-06-22T10:10:00", "garbage") is True

    def test_both_unparseable_returns_true(self):
        assert is_due("bad", "also-bad") is True

    def test_empty_strings_return_true(self):
        assert is_due("", "") is True

    def test_one_second_before_deadline_is_false(self):
        assert is_due("2026-06-22T10:10:00", "2026-06-22T10:09:59") is False

    def test_one_second_after_deadline_is_true(self):
        assert is_due("2026-06-22T10:10:00", "2026-06-22T10:10:01") is True

    # --- FIX 1 new tests: tz-aware vs tz-aware correct ordering ---

    def test_aware_ist_now_past_utc_deadline_returns_true(self):
        """[FIX1] Catastrophic pairing: UTC-aware deadline vs IST-aware now that IS past it.

        Deadline: 2026-06-22T04:40:00+00:00 (04:40 UTC)
        Now:      2026-06-22T10:15:00+05:30 (= 04:45 UTC — 5 min past deadline)
        Expected: True (position IS past deadline)
        """
        deadline = "2026-06-22T04:40:00+00:00"
        now_ist = "2026-06-22T10:15:00+05:30"   # 04:45 UTC — past deadline
        assert is_due(deadline, now_ist) is True, (
            "is_due returned False for an IST now that is genuinely past the UTC deadline"
        )

    def test_aware_ist_now_before_utc_deadline_returns_false(self):
        """[FIX1] IST-aware now that is genuinely BEFORE UTC deadline → False.

        Deadline: 2026-06-22T04:40:00+00:00 (04:40 UTC)
        Now:      2026-06-22T10:05:00+05:30 (= 04:35 UTC — 5 min before deadline)
        Expected: False
        """
        deadline = "2026-06-22T04:40:00+00:00"
        now_ist = "2026-06-22T10:05:00+05:30"   # 04:35 UTC — before deadline
        assert is_due(deadline, now_ist) is False, (
            "is_due returned True for an IST now that is genuinely before the UTC deadline"
        )

    def test_naive_vs_naive_ordering_preserved(self):
        """[FIX1] Naive-vs-naive still compares correctly (back-compat).
        Both assumed UTC → ordering is identical to before."""
        # Before deadline (same as existing test_before_deadline_returns_false but explicit)
        assert is_due("2026-06-22T10:10:00", "2026-06-22T10:09:00") is False
        assert is_due("2026-06-22T10:10:00", "2026-06-22T10:11:00") is True

    def test_aware_now_30min_after_aware_deadline_is_true(self):
        """[FIX1] Deadline 20min after an aware fill, now 30min after fill (both aware UTC) → True."""
        # Fill at 09:00 UTC; deadline = 09:10 UTC; now = 09:30 UTC → past deadline
        fill = "2026-06-22T09:00:00+00:00"
        dl = deadline_iso(fill, horizon_sec=600)  # 09:10 UTC
        now = "2026-06-22T09:30:00+00:00"         # 30 min after fill = past deadline
        assert is_due(dl, now) is True


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
