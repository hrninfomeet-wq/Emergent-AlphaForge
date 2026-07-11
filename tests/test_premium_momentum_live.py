# tests/test_premium_momentum_live.py
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.premium_momentum_live import evaluate_premium_momentum_bar, _ist_hhmm
from tests.test_premium_lock_store import _FakeLocks   # reuse the fake collection


def run(c):
    return asyncio.run(c)


_CONTRACTS = [
    {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14", "lot_size": 65},
    {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14", "lot_size": 65},
]


def _tickmap(d):
    # latest_tick_map shape: {instrument_key: {"last_price": x, "ts": epoch_ms}}
    def lookup():
        return d
    return lookup


def _dep(params=None):
    return {"id": "D1", "strategy_id": "premium_momentum",
            "params": {"reference_time": "09:31", "moneyness": "itm1",
                       "side": "first_to_trigger", "momentum_pct": 15.0,
                       "stop_pct": 20.0, "late_lock_cutoff": "10:15",
                       **(params or {})}}


# candle_ts constants: real epoch-ms for 2026-07-10 09:29 / 09:31 IST
# (09:31 IST == 04:01 UTC), computed via zoneinfo — no hand-baked literals.
_IST = ZoneInfo("Asia/Kolkata")
TS_0929 = int(datetime(2026, 7, 10, 9, 29, tzinfo=_IST).timestamp() * 1000)
TS_0931 = int(datetime(2026, 7, 10, 9, 31, tzinfo=_IST).timestamp() * 1000)
# self-check: a wrong constant fails loudly at import time
assert _ist_hhmm(TS_0929) == "09:29"
assert _ist_hhmm(TS_0931) == "09:31"


def test_holding_before_reference_time():
    locks = _FakeLocks()
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0929, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=TS_0929 / 1000 + 60,
    ))
    assert out["outcome"] == "pre_reference"
    assert locks.docs == []                       # nothing persisted yet


def test_lock_and_ref_capture_at_reference_bar():
    locks = _FakeLocks()
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out["outcome"] == "monitoring"
    doc = locks.docs[0]
    assert doc["ce"]["strike"] == 23950 and doc["pe"]["strike"] == 24050
    assert doc["ce_ref_premium"] == 100.0 and doc["pe_ref_premium"] == 110.0


def test_stale_tick_holds_never_captures():
    locks = _FakeLocks()
    stale = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 - 10 * 60_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 - 10 * 60_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(stale), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out["outcome"] == "awaiting_ref"       # strikes locked, refs NOT captured
    doc = locks.docs[0]
    assert "ce_ref_premium" not in doc


def test_late_lock_cutoff_marks_done():
    locks = _FakeLocks()
    ts_1016 = TS_0931 + 45 * 60_000               # 10:16 IST
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=ts_1016, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=ts_1016 / 1000 + 60,
    ))
    assert out["outcome"] == "done"
    assert locks.docs[0]["done_reason"] == "no_lock"


def test_trigger_first_to_cross_uses_locked_contract():
    locks = _FakeLocks()
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    run(evaluate_premium_momentum_bar(          # bar 1: lock + refs
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    ts2 = TS_0931 + 60_000
    ticks2 = {"CE|23950": {"last_price": 116.0, "ts": ts2 + 55_000},   # +16% > 15%
              "PE|24050": {"last_price": 111.0, "ts": ts2 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=ts2, spot_close=26000.0,        # spot moved FAR — lock must hold
        contracts=_CONTRACTS, latest_tick_map=_tickmap(ticks2), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "triggered"
    assert out["direction"] == "CE"
    assert out["contract"]["instrument_key"] == "CE|23950"   # from LOCK, not spot 26000
    assert out["ref_premium"] == 100.0 and out["premium_now"] == 116.0
    # latch is NOT set here — the evaluator sets it only after the signal journals
    assert locks.docs[0]["triggered_side"] is None


def test_no_refire_when_done_or_triggered():
    locks = _FakeLocks()
    run(locks.insert_one({"deployment_id": "D1", "session_date": "2026-07-10",
                          "triggered_side": "CE", "done_for_day": False,
                          "entered_norenordno": "N1"}))
    ts2 = TS_0931 + 120_000
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "holding_position"
