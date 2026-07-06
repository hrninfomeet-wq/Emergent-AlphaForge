"""Contract test: the optimizer's INDICATOR_PARAM_CATALOG is exposed on
GET /strategies so the Backtest Lab form can render + override indicator
periods without duplicating bounds in JS.

Pure + host-safe: imports app.optimizer directly (no DB/motor) and
string-asserts on the router source via the shared contract corpus, rather
than hitting the DB-backed route (see docs/HANDOFF.md section 3).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tests.contract_corpus import backend_api_text

from app.optimizer import INDICATOR_PARAM_CATALOG

API = backend_api_text()

EXPECTED_KEYS = {
    "ema_fast", "ema_slow", "rsi_length", "macd_fast", "macd_slow",
    "macd_signal", "atr_length", "adx_length", "chop_length", "swing_lookback",
}


def test_indicator_param_catalog_has_expected_keys():
    assert set(INDICATOR_PARAM_CATALOG.keys()) == EXPECTED_KEYS


def test_indicator_param_catalog_entries_are_well_formed():
    for key, defn in INDICATOR_PARAM_CATALOG.items():
        for field in ("type", "min", "max", "default"):
            assert field in defn, f"{key} missing {field!r}"
        assert defn["type"] == "int"
        assert defn["min"] < defn["max"]
        assert defn["min"] <= defn["default"] <= defn["max"]


def test_strategies_route_exposes_indicator_param_catalog():
    assert "indicator_param_catalog" in API
    assert "INDICATOR_PARAM_CATALOG" in API
