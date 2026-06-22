"""Tests for backend/app/live/margin.py — margin pre-check verdict (L3.2).

Coverage:
  required_premium:
    - finite positive inputs → expected value with default buffer
    - ref_ltp zero / negative / NaN / inf / bool → None
    - lot_size zero / negative / bool → None

  parse_cash:
    - Noren string "16552.95" → float
    - integer cash (numeric, not string) → float
    - missing key / None value → None
    - unparseable strings: "abc", "", "nan", "inf" → None
    - negative cash "-100" → None
    - non-dict limits → None

  check_margin:
    - sufficient: cash "16552.95", premium 7800 → ok True
    - insufficient: cash "5000", premium 7800 → ok False
    - exact boundary: cash == premium → ok True (>= rule)
    - limits not a dict (None / list / "x") → ok False
    - missing cash key {} → ok False
    - cash "abc" / "" / None / "-100" / "nan" / "inf" → ok False
    - premium_required None / 0 / negative / nan / inf → ok False

  margin_verdict:
    - returns correct dict shape {"check":"margin","ok":bool,"detail":str}
    - sufficient scenario → ok True
    - insufficient scenario → ok False
    - ref_ltp non-finite → ok False (fail-closed, required_premium=None path)
    - lot_size non-finite → ok False
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest
from app.live.margin import check_margin, margin_verdict, parse_cash, required_premium


# ===========================================================================
# required_premium
# ===========================================================================

class TestRequiredPremium:
    def test_finite_inputs_default_buffer(self):
        result = required_premium(120.0, 65)
        assert result == pytest.approx(120.0 * 65 * 1.05)

    def test_finite_inputs_custom_buffer(self):
        result = required_premium(100.0, 50, buffer=1.10)
        assert result == pytest.approx(100.0 * 50 * 1.10)

    def test_integer_ref_ltp(self):
        result = required_premium(120, 65)
        assert result == pytest.approx(120 * 65 * 1.05)

    # ref_ltp failures
    def test_ref_ltp_zero(self):
        assert required_premium(0, 65) is None

    def test_ref_ltp_negative(self):
        assert required_premium(-10.0, 65) is None

    def test_ref_ltp_nan(self):
        assert required_premium(float("nan"), 65) is None

    def test_ref_ltp_inf(self):
        assert required_premium(float("inf"), 65) is None

    def test_ref_ltp_bool_true(self):
        # bool subclasses int; must be rejected
        assert required_premium(True, 65) is None

    def test_ref_ltp_bool_false(self):
        assert required_premium(False, 65) is None

    def test_ref_ltp_none(self):
        assert required_premium(None, 65) is None

    def test_ref_ltp_string(self):
        assert required_premium("120", 65) is None

    # lot_size failures
    def test_lot_size_zero(self):
        assert required_premium(120.0, 0) is None

    def test_lot_size_negative(self):
        assert required_premium(120.0, -65) is None

    def test_lot_size_bool(self):
        assert required_premium(120.0, True) is None

    def test_lot_size_none(self):
        assert required_premium(120.0, None) is None


# ===========================================================================
# parse_cash
# ===========================================================================

class TestParseCash:
    def test_noren_string_cash(self):
        assert parse_cash({"cash": "16552.95"}) == pytest.approx(16552.95)

    def test_numeric_cash(self):
        # Noren usually sends strings, but a float/int should still parse.
        assert parse_cash({"cash": 16552.95}) == pytest.approx(16552.95)

    def test_integer_cash(self):
        assert parse_cash({"cash": 5000}) == pytest.approx(5000.0)

    def test_zero_cash_is_valid(self):
        # Zero is >= 0, not negative — should parse (no funds, but parseable).
        result = parse_cash({"cash": "0.00"})
        assert result == pytest.approx(0.0)

    def test_missing_cash_key(self):
        assert parse_cash({}) is None

    def test_cash_none_value(self):
        assert parse_cash({"cash": None}) is None

    def test_cash_unparseable_string(self):
        assert parse_cash({"cash": "abc"}) is None

    def test_cash_empty_string(self):
        assert parse_cash({"cash": ""}) is None

    def test_cash_nan_string(self):
        assert parse_cash({"cash": "nan"}) is None

    def test_cash_inf_string(self):
        assert parse_cash({"cash": "inf"}) is None

    def test_cash_negative(self):
        assert parse_cash({"cash": "-100"}) is None

    def test_cash_negative_float(self):
        assert parse_cash({"cash": -1.0}) is None

    # limits not a dict
    def test_limits_none(self):
        assert parse_cash(None) is None

    def test_limits_list(self):
        assert parse_cash([{"cash": "5000"}]) is None

    def test_limits_string(self):
        assert parse_cash("x") is None

    def test_limits_int(self):
        assert parse_cash(42) is None


# ===========================================================================
# check_margin
# ===========================================================================

class TestCheckMargin:
    # --- Allow paths ---

    def test_sufficient_noren_string_cash(self):
        ok, detail = check_margin({"cash": "16552.95"}, premium_required=7800.0)
        assert ok is True
        assert "16552.95" in detail or "16552" in detail
        assert "7800" in detail

    def test_exact_boundary_allow(self):
        """cash == premium_required → allowed (>= rule)."""
        ok, detail = check_margin({"cash": "7800.00"}, premium_required=7800.0)
        assert ok is True

    def test_cash_just_above_required(self):
        ok, _ = check_margin({"cash": "7800.01"}, premium_required=7800.0)
        assert ok is True

    # --- Block paths: insufficient cash ---

    def test_insufficient_cash(self):
        ok, detail = check_margin({"cash": "5000"}, premium_required=7800.0)
        assert ok is False
        assert "5000" in detail or "insufficient" in detail.lower()

    def test_cash_just_below_required(self):
        ok, _ = check_margin({"cash": "7799.99"}, premium_required=7800.0)
        assert ok is False

    # --- Block paths: garbage limits ---

    def test_limits_none(self):
        ok, detail = check_margin(None, premium_required=7800.0)
        assert ok is False
        assert detail  # non-empty reason

    def test_limits_list(self):
        ok, _ = check_margin([{"cash": "16000"}], premium_required=7800.0)
        assert ok is False

    def test_limits_string(self):
        ok, _ = check_margin("x", premium_required=7800.0)
        assert ok is False

    def test_limits_missing_cash_key(self):
        ok, _ = check_margin({}, premium_required=7800.0)
        assert ok is False

    def test_limits_cash_none_value(self):
        ok, _ = check_margin({"cash": None}, premium_required=7800.0)
        assert ok is False

    def test_limits_cash_string_abc(self):
        ok, _ = check_margin({"cash": "abc"}, premium_required=7800.0)
        assert ok is False

    def test_limits_cash_empty_string(self):
        ok, _ = check_margin({"cash": ""}, premium_required=7800.0)
        assert ok is False

    def test_limits_cash_negative(self):
        ok, _ = check_margin({"cash": "-100"}, premium_required=7800.0)
        assert ok is False

    def test_limits_cash_nan_string(self):
        ok, _ = check_margin({"cash": "nan"}, premium_required=7800.0)
        assert ok is False

    def test_limits_cash_inf_string(self):
        ok, _ = check_margin({"cash": "inf"}, premium_required=7800.0)
        assert ok is False

    # --- Block paths: garbage premium_required ---

    def test_premium_required_none(self):
        ok, _ = check_margin({"cash": "16552.95"}, premium_required=None)
        assert ok is False

    def test_premium_required_zero(self):
        ok, _ = check_margin({"cash": "16552.95"}, premium_required=0)
        assert ok is False

    def test_premium_required_negative(self):
        ok, _ = check_margin({"cash": "16552.95"}, premium_required=-100.0)
        assert ok is False

    def test_premium_required_nan(self):
        ok, _ = check_margin({"cash": "16552.95"}, premium_required=float("nan"))
        assert ok is False

    def test_premium_required_inf(self):
        ok, _ = check_margin({"cash": "16552.95"}, premium_required=float("inf"))
        assert ok is False

    # --- Detail string content ---

    def test_detail_is_non_empty_on_block(self):
        _, detail = check_margin({}, premium_required=7800.0)
        assert isinstance(detail, str) and len(detail) > 0

    def test_detail_is_non_empty_on_allow(self):
        _, detail = check_margin({"cash": "16552.95"}, premium_required=7800.0)
        assert isinstance(detail, str) and len(detail) > 0


# ===========================================================================
# margin_verdict
# ===========================================================================

class TestMarginVerdict:
    def _check_shape(self, v: dict) -> None:
        assert isinstance(v, dict)
        assert v["check"] == "margin"
        assert isinstance(v["ok"], bool)
        assert isinstance(v["detail"], str) and len(v["detail"]) > 0

    def test_sufficient_returns_ok_true(self):
        v = margin_verdict({"cash": "16552.95"}, ref_ltp=120.0, lot_size=65)
        self._check_shape(v)
        assert v["ok"] is True

    def test_insufficient_returns_ok_false(self):
        # ref_ltp=120, lot_size=65, buffer=1.05 → required ≈ 8190 > 5000
        v = margin_verdict({"cash": "5000"}, ref_ltp=120.0, lot_size=65)
        self._check_shape(v)
        assert v["ok"] is False

    def test_ref_ltp_nan_fail_closed(self):
        v = margin_verdict({"cash": "16552.95"}, ref_ltp=float("nan"), lot_size=65)
        self._check_shape(v)
        assert v["ok"] is False

    def test_ref_ltp_zero_fail_closed(self):
        v = margin_verdict({"cash": "16552.95"}, ref_ltp=0, lot_size=65)
        self._check_shape(v)
        assert v["ok"] is False

    def test_ref_ltp_inf_fail_closed(self):
        v = margin_verdict({"cash": "16552.95"}, ref_ltp=float("inf"), lot_size=65)
        self._check_shape(v)
        assert v["ok"] is False

    def test_ref_ltp_negative_fail_closed(self):
        v = margin_verdict({"cash": "16552.95"}, ref_ltp=-10.0, lot_size=65)
        self._check_shape(v)
        assert v["ok"] is False

    def test_lot_size_zero_fail_closed(self):
        v = margin_verdict({"cash": "16552.95"}, ref_ltp=120.0, lot_size=0)
        self._check_shape(v)
        assert v["ok"] is False

    def test_lot_size_negative_fail_closed(self):
        v = margin_verdict({"cash": "16552.95"}, ref_ltp=120.0, lot_size=-65)
        self._check_shape(v)
        assert v["ok"] is False

    def test_limits_garbage_fail_closed(self):
        v = margin_verdict(None, ref_ltp=120.0, lot_size=65)
        self._check_shape(v)
        assert v["ok"] is False

    def test_custom_buffer_applied(self):
        # buffer=1.0 → required = 120*65*1.0 = 7800; cash "7800" is exact boundary
        v = margin_verdict({"cash": "7800"}, ref_ltp=120.0, lot_size=65, buffer=1.0)
        self._check_shape(v)
        assert v["ok"] is True

    def test_verdict_dict_keys(self):
        v = margin_verdict({"cash": "16552.95"}, ref_ltp=120.0, lot_size=65)
        assert set(v.keys()) == {"check", "ok", "detail"}
