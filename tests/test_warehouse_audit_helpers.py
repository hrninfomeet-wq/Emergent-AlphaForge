import sys
import types
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

motor_module = types.ModuleType("motor")
motor_asyncio_module = types.ModuleType("motor.motor_asyncio")


class DummyMotorClient:
    pass


motor_asyncio_module.AsyncIOMotorClient = DummyMotorClient
sys.modules.setdefault("motor", motor_module)
sys.modules.setdefault("motor.motor_asyncio", motor_asyncio_module)
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))

from app import warehouse  # noqa: E402


def test_iter_weekday_dates_skips_weekends():
    days = warehouse._iter_weekday_dates(date(2026, 5, 22), date(2026, 5, 26))

    assert days == ["2026-05-22", "2026-05-25", "2026-05-26"]


def test_ist_day_bounds_cover_exact_calendar_day_in_utc_ms():
    start_ms, end_ms = warehouse._ist_day_bounds_ms("2026-05-18")

    assert start_ms == 1779042600000
    assert end_ms == 1779128999999


def test_summarize_audit_days_classifies_data_trust_failures():
    result = warehouse.summarize_audit_days(
        instrument="NIFTY",
        expected_dates=["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21"],
        stored_counts={"2026-05-18": 375, "2026-05-19": 200, "2026-05-20": 375},
        stored_hashes={"2026-05-18": "ok", "2026-05-19": "short", "2026-05-20": "stored"},
        computed_hashes={"2026-05-18": "ok", "2026-05-19": "short", "2026-05-20": "computed"},
    )

    days = {day["date"]: day for day in result["days"]}
    summary = result["summary"]

    assert days["2026-05-18"]["status"] == "ok"
    assert days["2026-05-19"]["status"] == "incomplete"
    assert days["2026-05-20"]["status"] == "hash_mismatch"
    assert days["2026-05-21"]["status"] == "missing"
    assert summary["complete"] is False
    assert summary["complete_days"] == 1
    assert summary["incomplete_days"] == 1
    assert summary["hash_mismatch_days"] == 1
    assert summary["missing_days"] == 1
