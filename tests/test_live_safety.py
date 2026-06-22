"""Tests for backend/app/live/safety.py — pre-trade safety engine (L1.1).

Coverage strategy: every fail-closed branch must have at least one test that
confirms it blocks (returns allowed=False with a non-empty reason), and every
allow path must have a test that confirms allowed=True and reason is None.

Fail-closed branches tested:
  check_fat_finger:
    - cap is None                     → block (no cap configured)
    - lots <= 0 (zero)                → block
    - lots <= 0 (negative)            → block
    - lots > cap                      → block
    - lots == cap                     → ALLOW (boundary)
    - lots < cap                      → ALLOW

  check_price_band:
    - ref_ltp is None                 → block (no reference)
    - ref_ltp == 0                    → block (zero/stale reference)
    - ref_ltp < 0                     → block (negative stale reference)
    - price <= 0                      → block
    - deviation > pct                 → block (out-of-band high)
    - deviation > pct (below ref)     → block (out-of-band low)
    - deviation == pct (exact edge)   → ALLOW (boundary at limit)
    - deviation < pct                 → ALLOW

  validate_jdata:
    - prctyp "MKT" (market)           → block
    - prctyp "IOC"                    → block
    - prctyp "CO"                     → block
    - prd "D" (delivery)              → block
    - ret "IOC"                       → block
    - SL-LMT without trgprc           → block
    - qty == 0                        → block
    - qty not multiple of lot_size    → block
    - qty negative                    → block
    - prc <= 0                        → block
    - valid LMT                       → ALLOW
    - valid SL-LMT (with trgprc)      → ALLOW
    - prd "M" (NRML)                  → ALLOW

  RateThrottle:
    - cancel always returns True even when bucket exhausted
    - entries allowed up to max_per_sec within 1 second
    - (max_per_sec + 1)th entry within 1 second → blocked
    - bucket refills after 1 second
    - cancel does NOT consume a token (bucket still full for next entry)
"""
import sys
from pathlib import Path

# Ensure backend package is importable without installing
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest
from app.live.broker_protocol import OrderIntent
from app.live.safety import (
    RateThrottle,
    check_fat_finger,
    check_price_band,
    validate_jdata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent(
    *,
    prctyp: str = "LMT",
    prd: str = "I",
    ret: str = "DAY",
    qty: int = 65,
    prc: float = 150.0,
    trgprc: float | None = None,
) -> OrderIntent:
    return OrderIntent(
        client_order_id="cid-test",
        trantype="B",
        prctyp=prctyp,
        exch="NFO",
        tsym="NIFTY25000CE",
        qty=qty,
        prc=prc,
        prd=prd,
        ret=ret,
        trgprc=trgprc,
    )


# ---------------------------------------------------------------------------
# check_fat_finger
# ---------------------------------------------------------------------------

class TestCheckFatFinger:
    def test_no_cap_blocks(self):
        allowed, reason = check_fat_finger(1, None)
        assert not allowed
        assert reason and "cap" in reason.lower()

    def test_zero_lots_blocks(self):
        allowed, reason = check_fat_finger(0, 10)
        assert not allowed
        assert reason

    def test_negative_lots_blocks(self):
        allowed, reason = check_fat_finger(-1, 10)
        assert not allowed
        assert reason

    def test_over_cap_blocks(self):
        allowed, reason = check_fat_finger(11, 10)
        assert not allowed
        assert reason and "cap" in reason.lower()

    def test_at_cap_allows(self):
        allowed, reason = check_fat_finger(10, 10)
        assert allowed
        assert reason is None

    def test_under_cap_allows(self):
        allowed, reason = check_fat_finger(5, 10)
        assert allowed
        assert reason is None

    def test_lot_1_cap_1_allows(self):
        # Minimum valid case
        allowed, reason = check_fat_finger(1, 1)
        assert allowed
        assert reason is None

    def test_float_lots_over_cap_blocks(self):
        # Float lots that exceed integer cap
        allowed, reason = check_fat_finger(10.5, 10)
        assert not allowed

    def test_float_lots_under_cap_allows(self):
        allowed, reason = check_fat_finger(9.9, 10)
        assert allowed


# ---------------------------------------------------------------------------
# check_price_band
# ---------------------------------------------------------------------------

class TestCheckPriceBand:
    def test_ref_ltp_none_blocks(self):
        allowed, reason = check_price_band(100.0, None, 2.0)
        assert not allowed
        assert reason

    def test_ref_ltp_zero_blocks(self):
        allowed, reason = check_price_band(100.0, 0.0, 2.0)
        assert not allowed
        assert reason

    def test_ref_ltp_negative_blocks(self):
        allowed, reason = check_price_band(100.0, -50.0, 2.0)
        assert not allowed
        assert reason

    def test_price_zero_blocks(self):
        allowed, reason = check_price_band(0.0, 100.0, 2.0)
        assert not allowed
        assert reason

    def test_price_negative_blocks(self):
        allowed, reason = check_price_band(-10.0, 100.0, 2.0)
        assert not allowed
        assert reason

    def test_out_of_band_high_blocks(self):
        # price is 5% above ref, band is 2%
        allowed, reason = check_price_band(105.0, 100.0, 2.0)
        assert not allowed
        assert reason and "%" in reason

    def test_out_of_band_low_blocks(self):
        # price is 5% below ref, band is 2%
        allowed, reason = check_price_band(95.0, 100.0, 2.0)
        assert not allowed
        assert reason and "%" in reason

    def test_exact_band_edge_allows(self):
        # deviation == pct exactly → allowed (not strictly greater)
        allowed, reason = check_price_band(102.0, 100.0, 2.0)
        assert allowed
        assert reason is None

    def test_within_band_allows(self):
        allowed, reason = check_price_band(101.0, 100.0, 2.0)
        assert allowed
        assert reason is None

    def test_exact_reference_allows(self):
        allowed, reason = check_price_band(100.0, 100.0, 0.5)
        assert allowed
        assert reason is None

    def test_just_over_band_blocks(self):
        # 2.01% deviation vs 2.0% band
        allowed, reason = check_price_band(102.01, 100.0, 2.0)
        assert not allowed

    def test_small_price_large_deviation_blocks(self):
        # Even tiny premium prices checked correctly
        allowed, reason = check_price_band(1.5, 1.0, 2.0)
        # deviation = 50% >> 2%
        assert not allowed

    def test_small_price_within_band_allows(self):
        allowed, reason = check_price_band(1.01, 1.0, 2.0)
        assert allowed


# ---------------------------------------------------------------------------
# validate_jdata
# ---------------------------------------------------------------------------

class TestValidateJdata:
    LOT_SIZE = 65  # NIFTY standard

    # --- prctyp rejections ---

    def test_market_order_blocked(self):
        allowed, reason = validate_jdata(_intent(prctyp="MKT"), lot_size=self.LOT_SIZE)
        assert not allowed
        assert reason and "prctyp" in reason.lower()

    def test_ioc_blocked(self):
        allowed, reason = validate_jdata(_intent(prctyp="IOC"), lot_size=self.LOT_SIZE)
        assert not allowed

    def test_co_blocked(self):
        allowed, reason = validate_jdata(_intent(prctyp="CO"), lot_size=self.LOT_SIZE)
        assert not allowed

    def test_bo_blocked(self):
        allowed, reason = validate_jdata(_intent(prctyp="BO"), lot_size=self.LOT_SIZE)
        assert not allowed

    # --- prd rejections ---

    def test_delivery_prd_blocked(self):
        allowed, reason = validate_jdata(_intent(prd="D"), lot_size=self.LOT_SIZE)
        assert not allowed
        assert reason and "prd" in reason.lower()

    def test_cnc_prd_blocked(self):
        allowed, reason = validate_jdata(_intent(prd="CNC"), lot_size=self.LOT_SIZE)
        assert not allowed

    # --- ret rejections ---

    def test_ret_ioc_blocked(self):
        # "IOC" as ret (immediate-or-cancel session) — not in ALLOWED_RET
        allowed, reason = validate_jdata(_intent(ret="IOC"), lot_size=self.LOT_SIZE)
        assert not allowed
        assert reason and "ret" in reason.lower()

    def test_ret_eod_blocked(self):
        allowed, reason = validate_jdata(_intent(ret="EOS"), lot_size=self.LOT_SIZE)
        assert not allowed

    # --- SL-LMT requires trgprc ---

    def test_sl_lmt_without_trgprc_blocked(self):
        allowed, reason = validate_jdata(
            _intent(prctyp="SL-LMT", trgprc=None, prc=150.0),
            lot_size=self.LOT_SIZE,
        )
        assert not allowed
        assert reason and "trgprc" in reason.lower()

    def test_sl_lmt_with_trgprc_allowed(self):
        allowed, reason = validate_jdata(
            _intent(prctyp="SL-LMT", trgprc=148.0, prc=147.0),
            lot_size=self.LOT_SIZE,
        )
        assert allowed
        assert reason is None

    # --- qty checks ---

    def test_qty_zero_blocked(self):
        allowed, reason = validate_jdata(_intent(qty=0), lot_size=self.LOT_SIZE)
        assert not allowed
        assert reason and "qty" in reason.lower()

    def test_qty_negative_blocked(self):
        allowed, reason = validate_jdata(_intent(qty=-65), lot_size=self.LOT_SIZE)
        assert not allowed

    def test_qty_not_multiple_blocked(self):
        # 66 is not a multiple of 65
        allowed, reason = validate_jdata(_intent(qty=66), lot_size=self.LOT_SIZE)
        assert not allowed
        assert reason and "lot_size" in reason.lower()

    def test_qty_one_lot_allowed(self):
        allowed, reason = validate_jdata(_intent(qty=65), lot_size=self.LOT_SIZE)
        assert allowed
        assert reason is None

    def test_qty_two_lots_allowed(self):
        allowed, reason = validate_jdata(_intent(qty=130), lot_size=self.LOT_SIZE)
        assert allowed

    def test_qty_banknifty_lot_size(self):
        # BANKNIFTY lot_size=30; qty=30 → ok; qty=31 → fail
        allowed, reason = validate_jdata(_intent(qty=30), lot_size=30)
        assert allowed
        allowed2, reason2 = validate_jdata(_intent(qty=31), lot_size=30)
        assert not allowed2

    # --- prc checks ---

    def test_prc_zero_blocked(self):
        allowed, reason = validate_jdata(_intent(prc=0.0), lot_size=self.LOT_SIZE)
        assert not allowed
        assert reason and "prc" in reason.lower()

    def test_prc_negative_blocked(self):
        allowed, reason = validate_jdata(_intent(prc=-1.0), lot_size=self.LOT_SIZE)
        assert not allowed

    # --- valid intent paths ---

    def test_valid_lmt_allowed(self):
        allowed, reason = validate_jdata(
            _intent(prctyp="LMT", prd="I", ret="DAY", qty=65, prc=150.0),
            lot_size=65,
        )
        assert allowed
        assert reason is None

    def test_valid_nrml_prd_allowed(self):
        # prd="M" is NRML (allowed)
        allowed, reason = validate_jdata(
            _intent(prctyp="LMT", prd="M"),
            lot_size=self.LOT_SIZE,
        )
        assert allowed

    def test_sensex_lot_size_20(self):
        allowed, reason = validate_jdata(_intent(qty=20), lot_size=20)
        assert allowed
        bad, _ = validate_jdata(_intent(qty=21), lot_size=20)
        assert not bad


# ---------------------------------------------------------------------------
# RateThrottle
# ---------------------------------------------------------------------------

class TestRateThrottle:
    """Token-bucket throttle — deterministic via injected `now`."""

    def test_cancel_always_allowed_when_fresh(self):
        t = RateThrottle(max_per_sec=9)
        assert t.allow(is_cancel=True, now=0.0) is True

    def test_cancel_always_allowed_when_bucket_exhausted(self):
        """Cancels bypass the bucket completely — even when entries are blocked."""
        t = RateThrottle(max_per_sec=3)
        # Exhaust the bucket with 3 entries
        assert t.allow(is_cancel=False, now=0.0) is True
        assert t.allow(is_cancel=False, now=0.0) is True
        assert t.allow(is_cancel=False, now=0.0) is True
        # 4th entry blocked
        assert t.allow(is_cancel=False, now=0.0) is False
        # Cancel still goes through
        assert t.allow(is_cancel=True, now=0.0) is True
        assert t.allow(is_cancel=True, now=0.0) is True  # repeatedly

    def test_entries_allowed_up_to_limit(self):
        """Exactly max_per_sec entries within 1 second should all pass."""
        max_n = 5
        t = RateThrottle(max_per_sec=max_n)
        for i in range(max_n):
            assert t.allow(is_cancel=False, now=0.0) is True, f"entry {i+1} should be allowed"

    def test_entry_over_limit_blocked(self):
        """The (max_per_sec + 1)th entry within 1 second must be blocked."""
        max_n = 5
        t = RateThrottle(max_per_sec=max_n)
        for _ in range(max_n):
            t.allow(is_cancel=False, now=0.0)
        assert t.allow(is_cancel=False, now=0.0) is False

    def test_bucket_refills_after_one_second(self):
        """After 1 second, the bucket should be full again."""
        max_n = 3
        t = RateThrottle(max_per_sec=max_n)
        # Exhaust at t=0
        for _ in range(max_n):
            t.allow(is_cancel=False, now=0.0)
        assert t.allow(is_cancel=False, now=0.0) is False
        # After 1 full second the bucket refills to max
        for i in range(max_n):
            assert t.allow(is_cancel=False, now=1.0) is True, f"entry {i+1} after refill should be allowed"

    def test_partial_refill(self):
        """After 0.5 seconds, half-bucket tokens available (floor)."""
        max_n = 4  # half = 2
        t = RateThrottle(max_per_sec=max_n)
        # Exhaust at t=0
        for _ in range(max_n):
            t.allow(is_cancel=False, now=0.0)
        # 0.5s later → 4*0.5=2 tokens replenished
        assert t.allow(is_cancel=False, now=0.5) is True
        assert t.allow(is_cancel=False, now=0.5) is True
        # 3rd should fail (only 2 tokens available)
        assert t.allow(is_cancel=False, now=0.5) is False

    def test_cancel_does_not_consume_token(self):
        """A cancel call must not decrease entry budget for subsequent entries."""
        max_n = 2
        t = RateThrottle(max_per_sec=max_n)
        # Make a cancel call (should have zero impact on bucket)
        t.allow(is_cancel=True, now=0.0)
        # Both entry slots should still be available
        assert t.allow(is_cancel=False, now=0.0) is True
        assert t.allow(is_cancel=False, now=0.0) is True
        # Now exhausted
        assert t.allow(is_cancel=False, now=0.0) is False

    def test_default_max_per_sec_is_9(self):
        """Default constructor enforces exactly 9 orders/second (under SEBI 10 limit)."""
        t = RateThrottle()
        count = 0
        for _ in range(20):
            if t.allow(is_cancel=False, now=0.0):
                count += 1
        assert count == 9

    def test_max_per_sec_1_allows_one_then_blocks(self):
        t = RateThrottle(max_per_sec=1)
        assert t.allow(is_cancel=False, now=0.0) is True
        assert t.allow(is_cancel=False, now=0.0) is False
        # Refill after 1 second
        assert t.allow(is_cancel=False, now=1.0) is True

    def test_invalid_max_per_sec_raises(self):
        with pytest.raises(ValueError):
            RateThrottle(max_per_sec=0)

    def test_multiple_seconds_accumulate_capped_at_max(self):
        """Tokens don't accumulate beyond max even after long idle period."""
        max_n = 3
        t = RateThrottle(max_per_sec=max_n)
        # 10 seconds idle — still capped at 3
        for i in range(max_n):
            assert t.allow(is_cancel=False, now=10.0) is True
        assert t.allow(is_cancel=False, now=10.0) is False

    def test_rapid_successive_calls_progressive_refill(self):
        """Calls at t=0, t=0.1, t=0.2 ... refill proportionally each call."""
        t = RateThrottle(max_per_sec=10)
        # Exhaust at t=0
        for _ in range(10):
            t.allow(is_cancel=False, now=0.0)
        assert t.allow(is_cancel=False, now=0.0) is False
        # At t=0.1 → 10*0.1=1 token → one entry allowed
        assert t.allow(is_cancel=False, now=0.1) is True
        assert t.allow(is_cancel=False, now=0.1) is False
