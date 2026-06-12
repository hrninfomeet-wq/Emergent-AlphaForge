"""Tests for expired-vs-active endpoint routing in the Upstox client.

Root cause fixed 2026-06-12: contracts synced while ACTIVE keep
source="current_option_contract" forever, so after expiry the normal V3
endpoint was still used (UDAPI100011) and band backfills silently failed for
every weekly that was ever synced live. Routing now keys off the actual
expiry_date, and the expired endpoint key is synthesized for 2-part keys.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# upstox_client transitively imports app.db -> motor, which is not installed on
# the host (tests run without a DB by design). Stub just the motor import; the
# functions under test are pure and never touch the DB.
import types  # noqa: E402

if "motor.motor_asyncio" not in sys.modules:
    _motor = types.ModuleType("motor")
    _motor_asyncio = types.ModuleType("motor.motor_asyncio")
    _motor_asyncio.AsyncIOMotorClient = object
    _motor.motor_asyncio = _motor_asyncio
    sys.modules.setdefault("motor", _motor)
    sys.modules["motor.motor_asyncio"] = _motor_asyncio

from app.upstox_client import (  # noqa: E402
    _expired_endpoint_key,
    _is_expired_instrument_key,
)


def _ist_today():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def test_expired_source_flag_still_routes_expired():
    assert _is_expired_instrument_key(
        "NSE_FO|38201|07-10-2025", {"source": "expired_option_contract"}) is True


def test_three_part_dated_key_routes_expired():
    assert _is_expired_instrument_key("NSE_FO|38201|07-10-2025", None) is True


def test_active_sourced_contract_past_expiry_routes_expired():
    # The 2026-05-20 23550CE case: plain key, source=current, expiry long past.
    yesterday = (_ist_today() - timedelta(days=1)).isoformat()
    contract = {"source": "current_option_contract", "expiry_date": yesterday}
    assert _is_expired_instrument_key("NSE_FO|72141", contract) is True


def test_contract_expiring_today_or_later_stays_on_normal_endpoint():
    today = _ist_today().isoformat()
    future = (_ist_today() + timedelta(days=5)).isoformat()
    assert _is_expired_instrument_key("NSE_FO|72141", {"expiry_date": today}) is False
    assert _is_expired_instrument_key("NSE_FO|72141", {"expiry_date": future}) is False
    assert _is_expired_instrument_key("NSE_FO|72141", None) is False


def test_expired_endpoint_key_synthesized_for_plain_keys():
    key = _expired_endpoint_key("NSE_FO|72141", {"expiry_date": "2026-05-26"})
    assert key == "NSE_FO|72141|26-05-2026"


def test_expired_endpoint_key_passthrough_for_dated_keys():
    assert _expired_endpoint_key("NSE_FO|38201|07-10-2025", {"expiry_date": "2025-10-07"}) == "NSE_FO|38201|07-10-2025"


def test_expired_endpoint_key_garbage_expiry_left_unchanged():
    assert _expired_endpoint_key("NSE_FO|72141", {"expiry_date": "garbage"}) == "NSE_FO|72141"
