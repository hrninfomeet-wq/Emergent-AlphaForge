"""Tests for the slippage model (slice 7, part A)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, time, timedelta, timezone

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.slippage import (  # noqa: E402
    SlippageConfig,
    apply_slippage,
    estimate_slippage_per_side,
    is_expiry_tail,
    slippage_bucket,
)


IST = timezone(timedelta(hours=5, minutes=30))


def ist_to_ms(year, month, day, hh, mm) -> int:
    dt = datetime(year, month, day, hh, mm, tzinfo=IST)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


# ---- bucket classification --------------------------------------------------


def test_slippage_bucket_atm_variants():
    assert slippage_bucket("atm") == "atm"
    assert slippage_bucket("ATM") == "atm"
    assert slippage_bucket("Atm") == "atm"


def test_slippage_bucket_otm_distances():
    assert slippage_bucket("otm1") == "otm1"
    assert slippage_bucket("OTM1") == "otm1"
    assert slippage_bucket("otm2") == "otm2_plus"
    assert slippage_bucket("otm5") == "otm2_plus"


def test_slippage_bucket_itm_distances():
    assert slippage_bucket("itm1") == "itm1"
    assert slippage_bucket("itm3") == "itm2_plus"


def test_slippage_bucket_unknown_falls_back_to_atm():
    assert slippage_bucket("") == "atm"
    assert slippage_bucket("garbage") == "atm"
    assert slippage_bucket(None) == "atm"


# ---- per-side slippage estimate --------------------------------------------


def test_atm_default_is_half_point_per_side():
    res = estimate_slippage_per_side(
        moneyness="atm", ts_ms=ist_to_ms(2026, 5, 27, 11, 30), expiry_iso="2026-06-02"
    )
    assert res["pts"] == 0.5
    assert res["bucket"] == "atm"
    assert res["tail_multiplier_applied"] is False


def test_otm1_default_is_one_point():
    res = estimate_slippage_per_side(
        moneyness="otm1", ts_ms=ist_to_ms(2026, 5, 27, 11, 30), expiry_iso="2026-06-02"
    )
    assert res["pts"] == 1.0


def test_otm2_default_is_two_points():
    res = estimate_slippage_per_side(
        moneyness="otm2", ts_ms=ist_to_ms(2026, 5, 27, 11, 30), expiry_iso="2026-06-02"
    )
    assert res["pts"] == 2.0
    assert res["bucket"] == "otm2_plus"


def test_user_can_override_atm_to_zero():
    cfg = SlippageConfig.from_dict({"atm_pts": 0})
    res = estimate_slippage_per_side(
        moneyness="atm", ts_ms=ist_to_ms(2026, 5, 27, 11, 30), expiry_iso="2026-06-02", cfg=cfg
    )
    assert res["pts"] == 0.0


# ---- expiry-day tail multiplier --------------------------------------------


def test_expiry_tail_doubles_slippage_when_in_window():
    """ATM 0.5 pts becomes 1.0 in last 30 min of expiry day."""
    expiry = "2026-05-27"
    res = estimate_slippage_per_side(
        moneyness="atm", ts_ms=ist_to_ms(2026, 5, 27, 15, 10), expiry_iso=expiry
    )
    assert res["tail_multiplier_applied"] is True
    assert res["pts"] == 1.0


def test_expiry_tail_does_not_apply_before_15_00():
    expiry = "2026-05-27"
    res = estimate_slippage_per_side(
        moneyness="atm", ts_ms=ist_to_ms(2026, 5, 27, 14, 59), expiry_iso=expiry
    )
    assert res["tail_multiplier_applied"] is False
    assert res["pts"] == 0.5


def test_expiry_tail_does_not_apply_on_other_days():
    """Same wall-clock 15:10 IST but on a non-expiry day -> no multiplier."""
    expiry = "2026-05-27"
    other_day = ist_to_ms(2026, 5, 26, 15, 10)
    res = estimate_slippage_per_side(moneyness="atm", ts_ms=other_day, expiry_iso=expiry)
    assert res["tail_multiplier_applied"] is False


def test_expiry_tail_no_expiry_iso_means_no_multiplier():
    res = estimate_slippage_per_side(
        moneyness="atm", ts_ms=ist_to_ms(2026, 5, 27, 15, 25), expiry_iso=None
    )
    assert res["tail_multiplier_applied"] is False


# ---- apply_slippage -------------------------------------------------------


def test_apply_slippage_buy_costs_more():
    """Buying options pays MORE than mid - price moves up."""
    assert apply_slippage(fill_price=100.0, side="BUY", pts=0.5) == 100.5
    assert apply_slippage(fill_price=100.0, side="buy", pts=2.0) == 102.0


def test_apply_slippage_sell_receives_less():
    """Selling options receives LESS than mid - price moves down."""
    assert apply_slippage(fill_price=100.0, side="SELL", pts=0.5) == 99.5
    assert apply_slippage(fill_price=100.0, side="sell", pts=2.0) == 98.0


def test_apply_slippage_zero_pts_is_passthrough():
    assert apply_slippage(fill_price=42.7, side="BUY", pts=0) == 42.7
    assert apply_slippage(fill_price=42.7, side="SELL", pts=0) == 42.7


# ---- config round-trip ----------------------------------------------------


def test_config_from_dict_keeps_unspecified_defaults():
    cfg = SlippageConfig.from_dict({"atm_pts": 0.25})
    assert cfg.atm_pts == 0.25
    assert cfg.otm1_pts == 1.0  # default
    assert cfg.expiry_tail_multiplier == 2.0  # default


def test_config_to_dict_round_trip():
    cfg = SlippageConfig(atm_pts=0.3, otm1_pts=1.5)
    cfg2 = SlippageConfig.from_dict(cfg.to_dict())
    assert cfg2.atm_pts == 0.3
    assert cfg2.otm1_pts == 1.5


def test_is_expiry_tail_explicit_helper():
    expiry = "2026-05-27"
    cfg = SlippageConfig()
    assert is_expiry_tail(ts_ms=ist_to_ms(2026, 5, 27, 15, 0), expiry_iso=expiry, cfg=cfg)
    assert not is_expiry_tail(ts_ms=ist_to_ms(2026, 5, 27, 14, 59), expiry_iso=expiry, cfg=cfg)
    assert not is_expiry_tail(ts_ms=ist_to_ms(2026, 5, 26, 15, 30), expiry_iso=expiry, cfg=cfg)
