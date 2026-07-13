"""Item #10 — authored→deployed pipeline (audit S1/S18/S19).

CONTAINER tests.
  • S1  complete_structured no longer defaults max_tokens=4000 (which silently
        re-introduced the Gemini output truncation the 8192 fix solved).
  • S19 build_arm_advisories surfaces NON-blocking live-arm warnings from forward
        (paper) evidence — the arm route had no performance gate.
  • S18 GET /strategies/{id}/pipeline aggregates the stage state across collections.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.ai.llm_client import complete_structured  # noqa: E402
from app.forward_metrics import build_arm_advisories  # noqa: E402
from app.routers import strategies_admin as sa  # noqa: E402


def _run(c):
    return asyncio.run(c)


# --- S1: max_tokens default lifted --------------------------------------------
# Historical fix: the wrapper default was silently OVERRIDING the per-backend default
# and re-introducing the Gemini truncation bug (a 4096 default here made every caller
# that didn't pass max_tokens hit MAX_TOKENS). The invariant is "wrapper default >=
# per-backend default", not "wrapper default == 8192" — the latter re-broke the bug
# once we discovered gemini-2.5-pro's thinking tokens draw from the same budget and
# needed a much higher ceiling (see test_gemini_token_budget.py for the current
# minimums).

def test_complete_structured_default_is_at_least_per_backend_default():
    from app.ai import _gemini, _anthropic
    default = inspect.signature(complete_structured).parameters["max_tokens"].default
    assert default >= _gemini.DEFAULT_MAX_TOKENS, (
        f"wrapper max_tokens default {default} is BELOW _gemini.DEFAULT_MAX_TOKENS "
        f"({_gemini.DEFAULT_MAX_TOKENS}) — this re-introduces the Gemini truncation "
        "bug on every caller that doesn't override."
    )
    assert default >= _anthropic.DEFAULT_MAX_TOKENS


# --- S19: live-arm advisories from forward evidence ---------------------------

def test_advisory_when_no_forward_evidence():
    adv = build_arm_advisories(None)
    assert any(a["id"] == "no_forward_evidence" for a in adv)


def test_advisory_flags_nonpositive_forward_pnl():
    fwd = {"total_pnl": -1500.0, "trade_count": 12, "win_rate": 41.0,
           "session_completeness": {"complete_session_count": 15},
           "library_gate": {"min_complete_sessions": 10}}
    adv = build_arm_advisories(fwd)
    ids = {a["id"]: a for a in adv}
    assert "nonpositive_forward_pnl" in ids
    assert ids["nonpositive_forward_pnl"]["severity"] == "danger"
    assert "thin_sessions" not in ids           # 15 >= 10


def test_advisory_flags_thin_sessions():
    fwd = {"total_pnl": 5000.0, "trade_count": 8, "win_rate": 60.0,
           "session_completeness": {"complete_session_count": 3},
           "library_gate": {"min_complete_sessions": 10}}
    adv = build_arm_advisories(fwd)
    assert any(a["id"] == "thin_sessions" for a in adv)


def test_no_advisory_when_healthy_record():
    fwd = {"total_pnl": 42000.0, "trade_count": 50, "win_rate": 58.0,
           "session_completeness": {"complete_session_count": 20},
           "library_gate": {"min_complete_sessions": 10}}
    assert build_arm_advisories(fwd) == []


# --- S18: per-strategy pipeline endpoint --------------------------------------

def _get(d, path):
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _match(d, q):
    import re
    for k, v in q.items():
        actual = _get(d, k)
        if isinstance(v, dict) and "$exists" in v:
            if (actual is not None) != v["$exists"]:
                return False
        elif isinstance(v, dict) and "$regex" in v:
            flags = re.IGNORECASE if "i" in v.get("$options", "") else 0
            if actual is None or not re.search(v["$regex"], str(actual), flags):
                return False
        elif actual != v:
            return False
    return True


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    async def count_documents(self, q):
        return sum(1 for d in self.docs if _match(d, q))

    async def find_one(self, q, proj=None, sort=None):
        rows = [d for d in self.docs if _match(d, q)]
        if sort:
            f, direction = sort[0]
            rows.sort(key=lambda d: d.get(f) or "", reverse=direction < 0)
        return dict(rows[0]) if rows else None


class _DB:
    def __init__(self, **colls):
        for k, v in colls.items():
            setattr(self, k, _Coll(v))


def test_pipeline_aggregates_stages():
    sid = "confluence"
    db = _DB(
        backtest_runs=[{"strategy_id": sid, "created_at": "2026-07-01"},
                       {"strategy_id": sid, "created_at": "2026-07-05"},
                       {"strategy_id": "other", "created_at": "2026-07-09"}],
        optimization_jobs=[{"strategy_id": sid, "created_at": "2026-07-03"}],
        presets=[{"config": {"strategy_id": sid}, "saved_at": "2026-07-06"}],
        strategy_deployments=[
            {"strategy_id": sid, "mode": "paper", "created_at": "2026-07-07"},
            {"strategy_id": sid, "mode": "paper", "created_at": "2026-07-08",
             "risk": {"live": {"armed": False}}},   # armed once, now disarmed
            {"strategy_id": sid, "mode": "shadow", "created_at": "2026-07-06"},
        ],
    )
    reg = types.SimpleNamespace(get=lambda s: object(), origin_of=lambda s: None)
    with patch.object(sa, "_db", lambda: db), patch.object(sa, "get_registry", lambda: reg):
        out = _run(sa.strategy_pipeline(sid))
    assert out["backtests"]["count"] == 2
    assert out["backtests"]["latest"] == "2026-07-05"
    assert out["optimizations"]["count"] == 1
    assert out["presets"]["count"] == 1
    assert out["deployments"]["count"] == 3
    assert out["paper_deployments"]["count"] == 2   # lower-case "paper", NOT "shadow"
    assert out["live_ever_count"] == 1 and out["live_armed_count"] == 0
    assert out["stages"] == {
        "authored": True, "backtested": True, "optimized": True,
        "preset_saved": True, "paper": True, "live": True,
    }


def test_pipeline_empty_strategy_stages_false():
    reg = types.SimpleNamespace(get=lambda s: object(), origin_of=lambda s: None)
    db = _DB(backtest_runs=[], optimization_jobs=[], presets=[], strategy_deployments=[])
    with patch.object(sa, "_db", lambda: db), patch.object(sa, "get_registry", lambda: reg):
        out = _run(sa.strategy_pipeline("fresh"))
    assert out["stages"]["backtested"] is False and out["stages"]["live"] is False
    assert out["stages"]["authored"] is True


def test_pipeline_404_for_unknown_strategy():
    from fastapi import HTTPException
    reg = types.SimpleNamespace(get=lambda s: None, origin_of=lambda s: None)
    with patch.object(sa, "get_registry", lambda: reg):
        with pytest.raises(HTTPException) as ei:
            _run(sa.strategy_pipeline("ghost"))
    assert ei.value.status_code == 404
