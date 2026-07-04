"""Paper↔Live parity: caps enforcement + basket overall-controls for paper.

Covers the 2026-07-04 parity slice:
  * risk.lots_override beats the pinned sizing replay (fixed lots per signal)
  * risk.max_concurrent blocks a new auto paper trade at the cap
  * paper_overall_controls squares the whole basket on an overall SL breach,
    reusing the PURE live evaluator, and treats a partial mark as stale (no-op)
  * the 'paper' scope is registered for the overall-settings routes and the
    LiveExitMonitor wiring carries the overall hook (string pins)
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.paper_auto import resolve_deployment_lots, auto_paper_trade_for_signal  # noqa: E402
import app.paper_overall_controls as poc  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# lots_override
# ---------------------------------------------------------------------------

def test_lots_override_beats_pinned_sizing():
    risk = {
        "lots_override": 3,
        "sizing": {"lots": 1, "sizing_config": {"enabled": True, "mode": "premium_at_risk",
                                                "risk_amount": 1000}},
        "default_lots": 1,
    }
    lots, audit = resolve_deployment_lots(risk, 100.0, {"lot_size": 75}, 90.0)
    assert lots == 3
    assert audit["sizing_mode"] == "lots_override"


def test_no_override_falls_through_to_pin():
    risk = {"sizing": {"lots": 2}, "default_lots": 1}
    lots, audit = resolve_deployment_lots(risk, 100.0, {"lot_size": 75}, None)
    assert lots == 2
    assert audit["sizing_mode"] == "fixed_lots"


# ---------------------------------------------------------------------------
# max_concurrent (fake db)
# ---------------------------------------------------------------------------

class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *_a, **_k):
        return self

    async def to_list(self, length=None):
        return list(self._rows)


class _Coll:
    def __init__(self, rows=(), open_count=0):
        self._rows = rows
        self._open_count = open_count

    def find(self, *_a, **_k):
        return _Cursor(self._rows)

    async def count_documents(self, _q):
        return self._open_count


class _Db:
    def __init__(self, open_count):
        self.paper_trades = _Coll(rows=[], open_count=open_count)


def test_max_concurrent_blocks_at_cap():
    deployment = {"id": "dep1", "mode": "paper", "risk": {"auto_paper": True, "max_concurrent": 1}}
    signal = {"id": "sig1", "state": "CONFIRMED"}
    res = asyncio.run(auto_paper_trade_for_signal(_Db(open_count=1), deployment, signal))
    assert res["created"] is False
    assert res["reason"] == "max_concurrent:1/1"


def test_max_concurrent_allows_below_cap():
    deployment = {"id": "dep1", "mode": "paper", "risk": {"auto_paper": True, "max_concurrent": 2}}
    signal = {"id": "sig1", "state": "CONFIRMED"}  # no option_contract → next gate fires
    res = asyncio.run(auto_paper_trade_for_signal(_Db(open_count=1), deployment, signal))
    assert res["reason"] == "no_option_contract"


# ---------------------------------------------------------------------------
# paper overall controls (fake db + injected store; square-off monkeypatched)
# ---------------------------------------------------------------------------

class _Store:
    def __init__(self, cfg):
        self._cfg = cfg

    async def get_config(self):
        return self._cfg

SL_CFG = {
    "sl": {"enabled": True, "mode": "mtm", "value": 1000},
    "target": {"enabled": False, "mode": "mtm", "value": 0},
    "trailing": {"mode": "none"},
    "reentry": {"enabled": False},
}

OPEN_ROWS = [
    {"id": "t1", "status": "OPEN", "entry_price": 100.0, "quantity": 75,
     "instrument_key": "K1"},
    {"id": "t2", "status": "OPEN", "entry_price": 50.0, "quantity": 75,
     "instrument_key": "K2"},
]


class _OpenDb:
    def __init__(self, rows):
        self.paper_trades = _Coll(rows=rows)


def _tick(price_by_key):
    def lookup(key):
        p = price_by_key.get(key)
        return {"last_price": p} if p is not None else None
    return lookup


def test_overall_sl_squares_the_basket(monkeypatch):
    poc._reset()
    squared = {}

    async def fake_squareoff(db, *, latest_tick_lookup=None, reason=None):
        squared["reason"] = reason
        return [{"closed": True}, {"closed": True}]

    import app.paper_squareoff as ps
    monkeypatch.setattr(ps, "square_off_open_paper_trades", fake_squareoff)

    # entry basket premium = 100*75 + 50*75 = 11250; marks drop both legs hard:
    # mtm = (85-100)*75 + (40-50)*75 = -1125 - 750 = -1875 ≤ -1000 → overall_sl
    res = asyncio.run(poc.check_paper_overall_controls(
        _OpenDb(OPEN_ROWS),
        latest_tick_lookup=_tick({"K1": 85.0, "K2": 40.0}),
        store_factory=lambda: _Store(SL_CFG),
    ))
    assert res["exit"] is True
    assert res["reason"] == "overall_sl"
    assert squared["reason"] == "overall_sl"


def test_partial_mark_is_stale_no_exit(monkeypatch):
    poc._reset()

    async def boom(*_a, **_k):  # must never be called
        raise AssertionError("square-off must not run on a stale mark")

    import app.paper_squareoff as ps
    monkeypatch.setattr(ps, "square_off_open_paper_trades", boom)

    res = asyncio.run(poc.check_paper_overall_controls(
        _OpenDb(OPEN_ROWS),
        latest_tick_lookup=_tick({"K1": 10.0}),  # K2 has NO mark → basket stale
        store_factory=lambda: _Store(SL_CFG),
    ))
    assert res["exit"] is False


def test_disabled_rules_reset_and_noop():
    poc._reset()
    cfg = {"sl": {"enabled": False}, "target": {"enabled": False},
           "trailing": {"mode": "none"}, "reentry": {"enabled": False}}
    res = asyncio.run(poc.check_paper_overall_controls(
        _OpenDb(OPEN_ROWS), store_factory=lambda: _Store(cfg),
    ))
    assert res == {"exit": False, "reason": "disabled"}


# ---------------------------------------------------------------------------
# wiring pins
# ---------------------------------------------------------------------------

def test_paper_scope_registered_for_overall_settings():
    src = (ROOT / "backend/app/routers/live_broker.py").read_text(encoding="utf-8")
    assert '_OVERALL_SCOPES = ("overall", "broker_level", "paper")' in src


def test_exit_monitor_carries_overall_hook():
    src = (ROOT / "backend/app/runtime.py").read_text(encoding="utf-8")
    assert "overall_fn=check_paper_overall_controls" in src
    mon = (ROOT / "backend/app/live_exit_monitor.py").read_text(encoding="utf-8")
    assert "self._overall_fn = overall_fn" in mon
