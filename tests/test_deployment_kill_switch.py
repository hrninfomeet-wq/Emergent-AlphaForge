"""Tests for per-deployment kill switches (Phase 4b Slice 12)."""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.deployment_kill_switch import (  # noqa: E402
    check_deployment_kill_switches,
    daily_realized_summary,
    evaluate_kill_switches,
    kill_switches_configured,
    trailing_consecutive_losses,
)


def _closed(pnl: float, closed_at: str, entry_value: float = 10000.0) -> Dict[str, Any]:
    return {"status": "CLOSED", "realized_pnl": pnl, "closed_at": closed_at, "entry_value": entry_value}


# ---- trailing_consecutive_losses -------------------------------------------


def test_consecutive_losses_counts_trailing_run():
    trades = [
        _closed(100, "2026-05-10T05:00:00+00:00"),
        _closed(-50, "2026-05-11T05:00:00+00:00"),
        _closed(-75, "2026-05-12T05:00:00+00:00"),
        _closed(-20, "2026-05-13T05:00:00+00:00"),
    ]
    assert trailing_consecutive_losses(trades) == 3


def test_consecutive_losses_breaks_on_win_or_breakeven():
    trades = [
        _closed(-10, "2026-05-10T05:00:00+00:00"),
        _closed(0, "2026-05-11T05:00:00+00:00"),    # breakeven breaks streak
        _closed(-30, "2026-05-12T05:00:00+00:00"),
    ]
    assert trailing_consecutive_losses(trades) == 1


def test_consecutive_losses_empty():
    assert trailing_consecutive_losses([]) == 0


# ---- daily_realized_summary -------------------------------------------------


def test_daily_summary_only_counts_today_ist():
    trades = [
        _closed(-500, "2026-06-01T05:00:00+00:00", entry_value=10000),   # 2026-06-01 IST
        _closed(-300, "2026-06-01T06:00:00+00:00", entry_value=10000),   # 2026-06-01 IST
        _closed(1000, "2026-05-31T05:00:00+00:00", entry_value=10000),   # different day
    ]
    s = daily_realized_summary(trades, "2026-06-01")
    assert s["net"] == -800
    assert s["capital"] == 20000
    assert s["pct"] == -4.0
    assert s["count"] == 2


def test_daily_summary_handles_ist_date_boundary():
    # 2026-06-01T19:30Z == 2026-06-02 01:00 IST -> belongs to 2026-06-02.
    trades = [_closed(-100, "2026-06-01T19:30:00+00:00", entry_value=5000)]
    assert daily_realized_summary(trades, "2026-06-01")["count"] == 0
    assert daily_realized_summary(trades, "2026-06-02")["count"] == 1


# ---- evaluate_kill_switches (pure) -----------------------------------------


def test_max_consecutive_losses_pauses():
    d = evaluate_kill_switches(
        risk={"max_consecutive_losses": 3},
        consecutive_losses=3, daily_pct=0.0, daily_net=0.0, open_trade_count=0,
    )
    assert d["pause"] is True
    assert d["pause_switch"] == "max_consecutive_losses"


def test_below_consecutive_limit_does_not_pause():
    d = evaluate_kill_switches(
        risk={"max_consecutive_losses": 4},
        consecutive_losses=3, daily_pct=0.0, daily_net=0.0, open_trade_count=0,
    )
    assert d["pause"] is False


def test_daily_loss_cutoff_pauses_when_breached():
    d = evaluate_kill_switches(
        risk={"daily_loss_cutoff_pct": -3.0},
        consecutive_losses=0, daily_pct=-3.5, daily_net=-3500.0, open_trade_count=0,
    )
    assert d["pause"] is True
    assert d["pause_switch"] == "daily_loss_cutoff_pct"


def test_daily_loss_cutoff_not_breached():
    d = evaluate_kill_switches(
        risk={"daily_loss_cutoff_pct": -3.0},
        consecutive_losses=0, daily_pct=-1.0, daily_net=-1000.0, open_trade_count=0,
    )
    assert d["pause"] is False


def test_max_open_trades_blocks_but_does_not_pause():
    d = evaluate_kill_switches(
        risk={"max_open_paper_trades": 2},
        consecutive_losses=0, daily_pct=0.0, daily_net=0.0, open_trade_count=2,
    )
    assert d["pause"] is False
    assert d["block_reason"] is not None
    assert "max_open_paper_trades" in d["block_reason"]


def test_no_switches_configured_is_all_clear():
    d = evaluate_kill_switches(
        risk={}, consecutive_losses=99, daily_pct=-99.0, daily_net=-1.0, open_trade_count=99,
    )
    assert d["pause"] is False
    assert d["block_reason"] is None
    assert d["triggered"] == []


def test_consecutive_losses_takes_headline_when_both_pause():
    d = evaluate_kill_switches(
        risk={"max_consecutive_losses": 2, "daily_loss_cutoff_pct": -2.0},
        consecutive_losses=2, daily_pct=-5.0, daily_net=-5000.0, open_trade_count=0,
    )
    assert d["pause"] is True
    assert d["pause_switch"] == "max_consecutive_losses"
    assert len(d["triggered"]) == 2


def test_kill_switches_configured():
    assert kill_switches_configured({"max_consecutive_losses": 3}) is True
    assert kill_switches_configured({"daily_loss_cutoff_pct": -2.0}) is True
    assert kill_switches_configured({"max_open_paper_trades": 1}) is True
    assert kill_switches_configured({"daily_loss_cutoff_pct": 0}) is False
    assert kill_switches_configured({}) is False
    assert kill_switches_configured({"default_lots": 1}) is False


# ---- async wrapper ----------------------------------------------------------


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    async def to_list(self, length=None):
        return list(self._rows)


class _PaperColl:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection=None):
        rows = [r for r in self.rows if all(r.get(k) == v for k, v in query.items())]
        return _Cursor(rows)

    async def count_documents(self, query):
        return sum(1 for r in self.rows if all(r.get(k) == v for k, v in query.items()))


class _DB:
    def __init__(self, rows):
        self.paper_trades = _PaperColl(rows)


def test_wrapper_skips_non_paper_mode():
    db = _DB([])
    dep = {"id": "d1", "mode": "shadow", "risk": {"max_consecutive_losses": 1}}
    d = asyncio.run(check_deployment_kill_switches(db, dep, today_ist="2026-06-01"))
    assert d["pause"] is False
    assert d["triggered"] == []


def test_wrapper_pauses_on_consecutive_losses():
    rows = [
        {"deployment_id": "d1", "status": "CLOSED", "realized_pnl": -10, "closed_at": "2026-05-30T05:00:00+00:00", "entry_value": 1000},
        {"deployment_id": "d1", "status": "CLOSED", "realized_pnl": -20, "closed_at": "2026-05-31T05:00:00+00:00", "entry_value": 1000},
    ]
    db = _DB(rows)
    dep = {"id": "d1", "mode": "paper", "risk": {"max_consecutive_losses": 2}}
    d = asyncio.run(check_deployment_kill_switches(db, dep, today_ist="2026-06-01"))
    assert d["pause"] is True
    assert d["pause_switch"] == "max_consecutive_losses"
    assert d["inputs"]["consecutive_losses"] == 2


def test_wrapper_blocks_on_max_open():
    rows = [
        {"deployment_id": "d1", "status": "OPEN"},
        {"deployment_id": "d1", "status": "OPEN"},
    ]
    db = _DB(rows)
    dep = {"id": "d1", "mode": "paper", "risk": {"max_open_paper_trades": 2}}
    d = asyncio.run(check_deployment_kill_switches(db, dep, today_ist="2026-06-01"))
    assert d["pause"] is False
    assert d["block_reason"] is not None
    assert d["inputs"]["open_trade_count"] == 2


# ---- end-to-end wiring contract --------------------------------------------

from app.deployment_kill_switch import check_soft_daily_governor  # noqa: E402


class _GovCur:
    def __init__(self, docs): self._docs = docs
    def sort(self, *a, **k): return self
    async def to_list(self, length=None): return list(self._docs)


class _GovColl:
    def __init__(self, docs): self._docs = docs
    def find(self, flt, *a, **k):
        return _GovCur([d for d in self._docs if d.get("deployment_id") == flt.get("deployment_id")])


class _GovDB:
    def __init__(self, docs): self.paper_trades = _GovColl(docs)


def test_soft_governor_halts_on_max_trades_including_open():
    today = "2026-06-15"
    docs = [
        {"deployment_id": "d1", "status": "CLOSED", "created_at": "2026-06-15T04:00:00+00:00",
         "closed_at": "2026-06-15T05:00:00+00:00", "realized_pnl": -500.0},
        {"deployment_id": "d1", "status": "OPEN", "created_at": "2026-06-15T05:30:00+00:00"},
    ]
    dep = {"id": "d1", "mode": "paper", "risk": {"daily_caps": {"max_trades": 2}}}
    d = asyncio.run(check_soft_daily_governor(_GovDB(docs), dep, today_ist=today))
    assert d["halt"] and d["reason"] == "MAX_TRADES_HALT"


def test_soft_governor_halts_on_daily_loss():
    today = "2026-06-15"
    docs = [{"deployment_id": "d1", "status": "CLOSED", "created_at": "2026-06-15T04:00:00+00:00",
             "closed_at": "2026-06-15T05:00:00+00:00", "realized_pnl": -16000.0}]
    dep = {"id": "d1", "mode": "paper", "risk": {"daily_caps": {"loss": 15000}}}
    d = asyncio.run(check_soft_daily_governor(_GovDB(docs), dep, today_ist=today))
    assert d["halt"] and d["reason"] == "DAILY_LOSS_HALT"


def test_soft_governor_clear_when_no_caps_or_not_paper():
    today = "2026-06-15"
    docs = [{"deployment_id": "d1", "status": "CLOSED", "created_at": "2026-06-15T04:00:00+00:00",
             "closed_at": "2026-06-15T05:00:00+00:00", "realized_pnl": -99999.0}]
    # no daily_caps -> clear
    dep = {"id": "d1", "mode": "paper", "risk": {}}
    assert asyncio.run(check_soft_daily_governor(_GovDB(docs), dep, today_ist=today)) == {"halt": False, "reason": None}
    # signal_only mode -> clear even with caps
    dep2 = {"id": "d1", "mode": "signal_only", "risk": {"daily_caps": {"loss": 1}}}
    assert asyncio.run(check_soft_daily_governor(_GovDB(docs), dep2, today_ist=today)) == {"halt": False, "reason": None}


# ---- end-to-end wiring contract --------------------------------------------

from pathlib import Path  # noqa: E402
from tests.contract_corpus import backend_api_text

ROOT = Path(__file__).resolve().parents[1]


def test_kill_switch_wired_end_to_end():
    """Kill switches must be wired: evaluator import, create-request fields, and
    the deployment form inputs."""
    evaluator = (ROOT / "backend" / "app" / "deployment_evaluator.py").read_text(encoding="utf-8")
    server = backend_api_text()
    live = (ROOT / "frontend" / "src" / "pages" / "LiveSignals.jsx").read_text(encoding="utf-8")

    # Evaluator checks kill switches and can pause.
    assert "check_deployment_kill_switches" in evaluator
    assert "kill_switch_reason" in evaluator
    # Create request exposes the three switches.
    for field in ("max_consecutive_losses", "daily_loss_cutoff_pct", "max_open_paper_trades"):
        assert field in server
        assert field in live
    # UI surfaces the pause reason.
    assert "deployment-pause-reason" in live
