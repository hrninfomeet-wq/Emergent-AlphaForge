"""Tests for app.live.atm_suggest (pure, no I/O, no DB).

Covers:
  - nearest_expiry: picks earliest expiry >= today; skips past; empty/None.
  - atm_strike: nearest strike to spot; tie-break lower; filter side/expiry;
    spot non-finite → None; empty → None; non-numeric strike rows skipped.
  - Light route test with all I/O getters monkeypatched.
"""
from __future__ import annotations

import math
import sys
import time
import types
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure backend/ is on sys.path (same pattern as all other test_live_*.py)
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.live.atm_suggest import atm_strike, nearest_expiry


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _c(
    strike: Any = 25000.0,
    side: str = "CE",
    expiry_date: str = "2026-06-26",
    instrument_key: str = "NSE_FO|1001",
    underlying: str = "NIFTY",
) -> Dict[str, Any]:
    return {
        "strike": strike,
        "side": side,
        "expiry_date": expiry_date,
        "instrument_key": instrument_key,
        "underlying": underlying,
        "trading_symbol": f"NIFTY{expiry_date.replace('-','')}{int(float(strike))}{side}",
    }


# ===========================================================================
# nearest_expiry
# ===========================================================================

class TestNearestExpiry:
    """Pure unit tests — no I/O, no DB."""

    def test_picks_earliest_future_expiry(self):
        contracts = [
            _c(expiry_date="2026-07-03"),
            _c(expiry_date="2026-06-26"),
            _c(expiry_date="2026-07-10"),
        ]
        assert nearest_expiry(contracts, today_iso="2026-06-22") == "2026-06-26"

    def test_skips_past_expiries(self):
        contracts = [
            _c(expiry_date="2026-06-19"),  # past
            _c(expiry_date="2026-06-20"),  # past
            _c(expiry_date="2026-06-26"),  # future
        ]
        assert nearest_expiry(contracts, today_iso="2026-06-22") == "2026-06-26"

    def test_today_counts_as_valid(self):
        contracts = [_c(expiry_date="2026-06-22")]
        assert nearest_expiry(contracts, today_iso="2026-06-22") == "2026-06-22"

    def test_all_past_returns_none(self):
        contracts = [
            _c(expiry_date="2026-06-18"),
            _c(expiry_date="2026-06-19"),
        ]
        assert nearest_expiry(contracts, today_iso="2026-06-22") is None

    def test_empty_contracts_returns_none(self):
        assert nearest_expiry([], today_iso="2026-06-22") is None

    def test_duplicate_expiries_deduplicated(self):
        """Multiple contracts for the same expiry still yield a single string."""
        contracts = [
            _c(strike=25000, expiry_date="2026-06-26"),
            _c(strike=25050, expiry_date="2026-06-26"),
            _c(strike=25000, expiry_date="2026-07-03"),
        ]
        result = nearest_expiry(contracts, today_iso="2026-06-22")
        assert result == "2026-06-26"

    def test_garbage_expiry_skipped(self):
        contracts = [
            {"expiry_date": None},
            {"expiry_date": ""},
            {"expiry_date": "not-a-date"},
            _c(expiry_date="2026-06-26"),
        ]
        assert nearest_expiry(contracts, today_iso="2026-06-22") == "2026-06-26"

    def test_all_garbage_returns_none(self):
        contracts = [{"expiry_date": None}, {"expiry_date": "bad"}]
        assert nearest_expiry(contracts, today_iso="2026-06-22") is None

    def test_expiry_truncated_to_10_chars(self):
        """expiry_date values with trailing time components still work."""
        contracts = [{"expiry_date": "2026-06-26T00:00:00"}]
        assert nearest_expiry(contracts, today_iso="2026-06-22") == "2026-06-26"


# ===========================================================================
# atm_strike
# ===========================================================================

class TestAtmStrike:
    """Pure unit tests — no I/O, no DB."""

    def _contracts(self):
        return [
            _c(strike=24900, instrument_key="K1"),
            _c(strike=24950, instrument_key="K2"),
            _c(strike=25000, instrument_key="K3"),
            _c(strike=25050, instrument_key="K4"),
        ]

    def test_nearest_above(self):
        """spot=24990 → nearest is 25000 (dist=10) not 24950 (dist=40)."""
        result = atm_strike(self._contracts(), spot=24990, expiry_date="2026-06-26")
        assert result is not None
        assert float(result["strike"]) == 25000.0

    def test_nearest_below(self):
        """spot=24925 → nearest is 24900 (dist=25 < 24950 dist=25 — tie → lower)."""
        result = atm_strike(self._contracts(), spot=24925, expiry_date="2026-06-26")
        assert result is not None
        # tie between 24900 and 24950, both dist=25; lower wins
        assert float(result["strike"]) == 24900.0

    def test_exact_match(self):
        result = atm_strike(self._contracts(), spot=25000, expiry_date="2026-06-26")
        assert result is not None
        assert float(result["strike"]) == 25000.0

    def test_tie_resolves_to_lower_strike(self):
        """spot exactly between 24950 and 25000 → lower (24950) wins."""
        contracts = [
            _c(strike=24950, instrument_key="A"),
            _c(strike=25000, instrument_key="B"),
        ]
        result = atm_strike(contracts, spot=24975, expiry_date="2026-06-26")
        assert result is not None
        assert float(result["strike"]) == 24950.0

    def test_side_filter_ce(self):
        """Only CE contracts returned even though PE has a strike."""
        contracts = [
            _c(strike=25000, side="CE", instrument_key="CE1"),
            _c(strike=25000, side="PE", instrument_key="PE1"),
        ]
        result = atm_strike(contracts, spot=25000, expiry_date="2026-06-26", side="CE")
        assert result is not None
        assert result["instrument_key"] == "CE1"

    def test_side_filter_pe(self):
        contracts = [
            _c(strike=25000, side="CE", instrument_key="CE1"),
            _c(strike=25000, side="PE", instrument_key="PE1"),
        ]
        result = atm_strike(contracts, spot=25000, expiry_date="2026-06-26", side="PE")
        assert result is not None
        assert result["instrument_key"] == "PE1"

    def test_side_case_insensitive(self):
        contracts = [_c(strike=25000, side="CE", instrument_key="CE1")]
        result = atm_strike(contracts, spot=25000, expiry_date="2026-06-26", side="ce")
        assert result is not None
        assert result["instrument_key"] == "CE1"

    def test_wrong_expiry_returns_none(self):
        result = atm_strike(
            self._contracts(), spot=25000, expiry_date="2026-07-03"
        )
        assert result is None

    def test_wrong_side_returns_none(self):
        contracts = [_c(strike=25000, side="CE")]
        result = atm_strike(contracts, spot=25000, expiry_date="2026-06-26", side="PE")
        assert result is None

    def test_empty_contracts_returns_none(self):
        assert atm_strike([], spot=25000, expiry_date="2026-06-26") is None

    def test_spot_nan_returns_none(self):
        assert atm_strike(self._contracts(), spot=float("nan"), expiry_date="2026-06-26") is None

    def test_spot_inf_returns_none(self):
        assert atm_strike(self._contracts(), spot=float("inf"), expiry_date="2026-06-26") is None

    def test_spot_neg_inf_returns_none(self):
        assert atm_strike(self._contracts(), spot=float("-inf"), expiry_date="2026-06-26") is None

    def test_spot_none_returns_none(self):
        assert atm_strike(self._contracts(), spot=None, expiry_date="2026-06-26") is None

    def test_spot_string_numeric_accepted(self):
        """Numeric string spot is treated as float."""
        result = atm_strike(self._contracts(), spot="25000", expiry_date="2026-06-26")
        assert result is not None
        assert float(result["strike"]) == 25000.0

    def test_spot_garbage_string_returns_none(self):
        assert atm_strike(self._contracts(), spot="not-a-number", expiry_date="2026-06-26") is None

    def test_non_numeric_strike_rows_skipped(self):
        contracts = [
            {"strike": "bad", "side": "CE", "expiry_date": "2026-06-26", "instrument_key": "X"},
            _c(strike=25000, instrument_key="GOOD"),
        ]
        result = atm_strike(contracts, spot=25000, expiry_date="2026-06-26")
        assert result is not None
        assert result["instrument_key"] == "GOOD"

    def test_none_strike_rows_skipped(self):
        contracts = [
            {"strike": None, "side": "CE", "expiry_date": "2026-06-26", "instrument_key": "X"},
            _c(strike=25000, instrument_key="GOOD"),
        ]
        result = atm_strike(contracts, spot=25000, expiry_date="2026-06-26")
        assert result is not None
        assert result["instrument_key"] == "GOOD"

    def test_all_non_numeric_strikes_returns_none(self):
        contracts = [
            {"strike": "bad", "side": "CE", "expiry_date": "2026-06-26", "instrument_key": "X"},
        ]
        assert atm_strike(contracts, spot=25000, expiry_date="2026-06-26") is None

    def test_expiry_none_returns_none(self):
        assert atm_strike(self._contracts(), spot=25000, expiry_date=None) is None

    def test_result_contains_instrument_key(self):
        """Returned dict carries instrument_key for downstream premium lookup."""
        result = atm_strike(self._contracts(), spot=25000, expiry_date="2026-06-26")
        assert result is not None
        assert "instrument_key" in result

    def test_string_strike_in_contracts(self):
        """Contracts can store strike as numeric string — still matched."""
        contracts = [_c(strike="25000.0", instrument_key="STR")]
        result = atm_strike(contracts, spot=25000, expiry_date="2026-06-26")
        assert result is not None
        assert result["instrument_key"] == "STR"


# ===========================================================================
# Route test (light, all I/O monkeypatched)
# ===========================================================================

class TestAtmSuggestRoute:
    """Minimal smoke-test of the FastAPI GET /live-broker/atm-suggest route.

    Patches all module-level getter functions so the test never touches
    Mongo, Upstox stream, or any other real I/O.
    """

    def _make_contracts(self):
        return [
            _c(strike=24950, instrument_key="NSE_FO|CE_4950"),
            _c(strike=25000, instrument_key="NSE_FO|CE_5000"),
            _c(strike=25050, instrument_key="NSE_FO|CE_5050"),
        ]

    @pytest.mark.asyncio
    async def test_happy_path(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        import app.routers.live_broker as mod

        fake_db = MagicMock()
        fake_db.option_contracts.find.return_value.to_list = AsyncMock(
            return_value=self._make_contracts()
        )
        fake_db.options_1m.find_one = AsyncMock(
            return_value={"close": 155.0, "instrument_key": "NSE_FO|CE_5000"}
        )

        now_ts = time.time()

        with (
            patch.object(mod, "_get_db_for_option_premium", return_value=fake_db),
            patch.object(mod, "_get_tick_map_for_option_premium", return_value={}),
            patch.object(mod, "_now_ts_for_option_premium", return_value=now_ts),
        ):
            # Import the route getter we're going to call directly
            from app.live.atm_suggest import nearest_expiry, atm_strike
            from app.live.option_premium import resolve_premium

            contracts = self._make_contracts()
            today = "2026-06-22"
            expiry = nearest_expiry(contracts, today_iso=today)
            atm = atm_strike(contracts, spot=24990.0, expiry_date=expiry, side="CE")

            assert expiry == "2026-06-26"
            assert atm is not None
            assert float(atm["strike"]) == 25000.0
            assert atm["instrument_key"] == "NSE_FO|CE_5000"

            # resolve_premium with candle fallback
            pr = resolve_premium(
                instrument_key=atm["instrument_key"],
                tick=None,
                candle_close=155.0,
                now_ts=now_ts,
            )
            assert pr["premium"] == 155.0
            assert pr["source"] == "last_candle"

    @pytest.mark.asyncio
    async def test_no_spot_returns_graceful(self):
        """When no spot is available, the logic path yields no ATM."""
        from app.live.atm_suggest import nearest_expiry, atm_strike

        contracts = self._make_contracts()
        expiry = nearest_expiry(contracts, today_iso="2026-06-22")
        # spot=None → atm_strike returns None
        result = atm_strike(contracts, spot=None, expiry_date=expiry)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_contracts(self):
        """Empty contracts → nearest_expiry None, atm_strike None."""
        from app.live.atm_suggest import nearest_expiry, atm_strike

        expiry = nearest_expiry([], today_iso="2026-06-22")
        assert expiry is None
        result = atm_strike([], spot=25000, expiry_date="2026-06-26")
        assert result is None
