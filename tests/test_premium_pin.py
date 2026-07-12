# tests/test_premium_pin.py
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.premium_pin import premium_pin_keys
from tests.test_premium_lock_store import _FakeLocks


def run(c):
    return asyncio.run(c)


def test_pin_keys_are_todays_lock_keys(monkeypatch=None):
    locks = _FakeLocks()
    run(locks.insert_one({"deployment_id": "D1", "session_date": "2026-07-10",
                          "ce": {"instrument_key": "KC"}, "pe": {"instrument_key": "KP"}}))
    keys = run(premium_pin_keys(locks, now_session_date="2026-07-10"))
    assert sorted(keys) == ["KC", "KP"]


def test_pin_survives_lock_read_failure():
    class _Boom:
        def find(self, *a, **k):
            raise RuntimeError("db down")
    keys = run(premium_pin_keys(_Boom(), now_session_date="2026-07-10"))
    assert keys == []          # pin failure must NEVER break a stream restart


def test_auto_follow_unions_premium_pins_source_pin():
    # host string-pin: the union must sit in _auto_follow_option_stream AFTER the
    # cap (same as open paper keys) so pins are cap-exempt.
    src = (Path(__file__).resolve().parents[1] / "backend/app/runtime.py").read_text(encoding="utf-8")
    assert "premium_pin_keys" in src
    i_pin = src.index("premium_pin_keys")
    i_paper = src.index('db.paper_trades.distinct("instrument_key"')
    assert abs(i_pin - i_paper) < 2000   # same union block, not a distant stray
