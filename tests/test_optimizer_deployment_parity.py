"""Item #9 — deployment parity pack (audit O6 trade_window + O9 preset provenance).

CONTAINER tests (research/optimizer import motor).
  • trade_window defaults to the LIVE-EFFECTIVE 09:25–14:50 and is threaded into the
    optimizer's backtests (trials, parallel workers) — the optimizer no longer
    rewards 14:50–15:00 entries live can never take.
  • apply_opt_as_preset carries the pretrade_profile and stamps the evaluation stage
    + terminal job status so a cancelled spot-only preset ≠ a survival-passed one.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.schemas import OptimizerStartReq, WfoStartReq  # noqa: E402
import app.optimizer as opt  # noqa: E402
import app.parallel_eval as pe  # noqa: E402
from app.routers import research  # noqa: E402


def _run(c):
    return asyncio.run(c)


# --- O6: schema default is the live-effective window --------------------------

def test_optimizer_schema_defaults_to_live_window():
    r = OptimizerStartReq(strategy_id="s")
    assert r.trade_window_start == "09:25"
    assert r.trade_window_end == "14:50"     # NOT run_backtest's 15:00 default


def test_wfo_schema_defaults_to_live_window():
    r = WfoStartReq(strategy_id="s")
    assert r.trade_window_start == "09:25" and r.trade_window_end == "14:50"


# --- O6: the window is threaded into the optimizer's backtests ----------------

class _Strat:
    id = "s"

    def merged_params(self, p):
        return dict(p)


def test_evaluate_threads_window_to_run_backtest():
    cap = {}

    def fake_rb(df, strat, merged, **kw):
        cap.update(kw)
        return {"metrics": {}, "trades": []}

    with patch.object(opt, "run_backtest", fake_rb):
        opt._evaluate(lambda m: "DF", _Strat(), {"a": 1}, "NIFTY", True, {}, "09:25", "14:50")
    assert cap["trade_window_start"] == "09:25" and cap["trade_window_end"] == "14:50"


def test_evaluate_omits_window_when_none():
    cap = {}

    def fake_rb(df, strat, merged, **kw):
        cap.update(kw)
        return {"metrics": {}, "trades": []}

    with patch.object(opt, "run_backtest", fake_rb):
        opt._evaluate(lambda m: "DF", _Strat(), {"a": 1}, "NIFTY", True, {})
    assert "trade_window_start" not in cap   # None → run_backtest's own default


def test_parallel_worker_threads_window():
    cap = {}

    def fake_rb(enr, strat, merged, **kw):
        cap.update(kw)
        return {"metrics": {}, "trades": []}

    with patch.object(pe, "run_backtest", fake_rb), \
         patch.object(pe, "enrich_with_cache", lambda f, m, c: f), \
         patch.object(pe, "get_registry", lambda: types.SimpleNamespace(get=lambda s: _Strat())):
        pe._worker_evaluate("s", {"a": 1}, None, "NIFTY", True, {},
                            pd.DataFrame({"close": [1]}), {}, "09:25", "14:50")
    assert cap["trade_window_end"] == "14:50"


# --- O9(a)+(c): preset carries profile + validation provenance ---------------

class _Col:
    def __init__(self, doc=None):
        self.doc = doc
        self.upserts = []

    async def find_one(self, q, proj=None):
        return dict(self.doc) if self.doc else None

    async def update_one(self, q, upd, upsert=False):
        self.upserts.append(upd["$set"])


class _DB:
    def __init__(self, job):
        self.optimization_jobs = _Col(job)
        self.presets = _Col()


def _job(**over):
    j = {
        "id": "J1", "instrument": "NIFTY", "strategy_id": "s", "method": "bayesian",
        "objective": "risk_adjusted", "status": "done", "best_params": {"a": 1},
        "best_value": 1.0, "best_metrics": {"sharpe": 1.0},
        "finite_candidate_available": True,
        "config": {"evaluation_mode": "spot"},
    }
    j.update(over)
    return j


def _apply(db):
    with patch.object(research, "get_db", lambda: db):
        return _run(research.apply_opt_as_preset("J1", name="P1"))


def test_preset_stamps_spot_only_and_carries_profile():
    db = _DB(_job(status="cancelled", config={
        "evaluation_mode": "spot", "pretrade_profile": "Aggressive",
        "trade_window_start": "09:25", "trade_window_end": "14:50"}))
    out = _apply(db)
    assert out["ok"]
    cfg = db.presets.upserts[0]["config"]
    assert cfg["validation"]["stage"] == "spot_only"
    assert cfg["validation"]["job_status"] == "cancelled"   # not masquerading as validated
    assert cfg["pretrade_profile"] == "Aggressive"          # not silently "Balanced"
    assert cfg["validation"]["trade_window_end"] == "14:50"


def test_preset_stamps_survival_passed():
    db = _DB(_job(
        config={"evaluation_mode": "option_rerank", "survival_config": {"enabled": True}},
        survival_summary={"survivors": 3, "capital": 50000}))
    _apply(db)
    v = db.presets.upserts[0]["config"]["validation"]
    assert v["stage"] == "survival_passed"
    assert v["survivors"] == 3 and v["survival_capital"] == 50000


def test_preset_option_ranked_when_no_survivors():
    db = _DB(_job(
        config={"evaluation_mode": "option_rerank", "survival_config": {"enabled": False}}))
    _apply(db)
    assert db.presets.upserts[0]["config"]["validation"]["stage"] == "option_ranked"


def test_running_job_with_finite_snapshot_can_be_saved():
    db = _DB(_job(status="running", best_params=None, best_so_far={
        "value": 1.2, "params": {"a": 2}, "metrics": {"sharpe": 1.2},
        "trial_num": 4, "finite_candidate_available": True,
    }))
    assert _apply(db)["ok"]
    cfg = db.presets.upserts[0]["config"]
    assert cfg["params"] == {"a": 2}
    assert cfg["validation"]["job_status"] == "running"


def test_empty_param_finite_candidate_is_saved():
    db = _DB(_job(best_params={}, best_metrics={"sharpe": 1.0}))
    assert _apply(db)["ok"]
    assert db.presets.upserts[0]["config"]["params"] == {}


def test_optimizer_preset_rejects_nonfinite_candidate_params():
    db = _DB(_job(best_params={"a": float("nan")}))
    with pytest.raises(HTTPException) as exc:
        _apply(db)
    assert exc.value.status_code == 400
    assert "finite" in str(exc.value.detail).lower()
    assert db.presets.upserts == []


def test_running_job_without_completed_finite_candidate_is_rejected():
    db = _DB(_job(
        status="running", best_params=None, best_value=None,
        best_metrics={}, finite_candidate_available=False,
        best_so_far={"value": None, "params": {}, "metrics": {}, "trial_num": -1},
    ))
    with pytest.raises(HTTPException) as exc:
        _apply(db)
    assert exc.value.status_code == 400
    assert "finite completed trial" in str(exc.value.detail).lower()
