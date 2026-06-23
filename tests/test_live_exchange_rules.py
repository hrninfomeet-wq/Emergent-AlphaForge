"""Tests for the exchange rules engine (flattrade_symbol.rules_for / market_allowed)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.flattrade_symbol import (  # noqa: E402
    EXCHANGE_RULES,
    market_allowed,
    rules_for,
)


class TestRulesFor:
    def test_nifty(self):
        r = rules_for("NIFTY")
        assert r["exch"] == "NFO"
        assert r["lot_size"] == 65
        assert r["freeze_qty"] == 1800
        assert r["tick"] == 0.05
        assert r["products"] == ["NRML", "MIS"]
        assert r["price_types"] == ["LIMIT", "MARKET", "SL-LMT"]
        assert r["expiry_cadence"] == "weekly_tue"

    def test_banknifty(self):
        r = rules_for("BANKNIFTY")
        assert r["exch"] == "NFO"
        assert r["lot_size"] == 30
        assert r["freeze_qty"] == 600
        assert r["expiry_cadence"] == "monthly_last_tue"

    def test_sensex(self):
        r = rules_for("SENSEX")
        assert r["exch"] == "BFO"
        assert r["lot_size"] == 20
        assert r["freeze_qty"] == 1000
        assert r["expiry_cadence"] == "weekly_thu"

    def test_lowercase_and_whitespace(self):
        assert rules_for("nifty")["exch"] == "NFO"
        assert rules_for("  sensex  ")["exch"] == "BFO"

    @pytest.mark.parametrize("bad", ["UNKNOWN", "", "  ", None, 123, ["NIFTY"], {}])
    def test_unknown_returns_none(self, bad):
        assert rules_for(bad) is None

    def test_co_bo_not_in_products(self):
        for u in ("NIFTY", "BANKNIFTY", "SENSEX"):
            prods = rules_for(u)["products"]
            assert "CO" not in prods
            assert "BO" not in prods

    def test_sl_mkt_not_in_price_types(self):
        for u in ("NIFTY", "BANKNIFTY", "SENSEX"):
            pts = rules_for(u)["price_types"]
            assert "SL-MKT" not in pts

    def test_copy_safety_top_level(self):
        r = rules_for("NIFTY")
        r["lot_size"] = 999
        assert EXCHANGE_RULES["NIFTY"]["lot_size"] == 65

    def test_copy_safety_nested_lists(self):
        r = rules_for("NIFTY")
        r["products"].append("CO")
        r["price_types"].append("SL-MKT")
        assert "CO" not in EXCHANGE_RULES["NIFTY"]["products"]
        assert "SL-MKT" not in EXCHANGE_RULES["NIFTY"]["price_types"]


class TestMarketAllowed:
    def test_market_allowed_when_in_price_types(self):
        assert market_allowed(rules_for("NIFTY")) is True
        assert market_allowed(rules_for("SENSEX")) is True

    def test_market_allowed_none_rules(self):
        assert market_allowed(None) is False

    def test_market_allowed_no_market_type(self):
        assert market_allowed({"price_types": ["LIMIT", "SL-LMT"]}) is False
