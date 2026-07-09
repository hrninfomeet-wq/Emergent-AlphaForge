"""Item #7 — optimizer survival-gate honesty (audit O3 / O5 route enforcement).

CONTAINER tests (research router imports motor). They exercise optimize_start's
validation, which raises BEFORE create_job:
  • O3  survival on ⇒ option costs (option_config.cost_config.enabled) REQUIRED.
        The gate must reject the GROSS-option-curve config, and must key on the
        OPTION cost flag, not the spot costs_enabled.
  • O5  search_exit_controls on ⇒ option_config.exit_mode='option_levels' REQUIRED
        (premium exit controls are a no-op under spot exit).
  • O2  ruin_floor is validated against the USER capital, not a phantom ₹200k.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers import research  # noqa: E402
from app.schemas import OptimizerStartReq, SurvivalConfigReq  # noqa: E402

_REG_OK = types.SimpleNamespace(get=lambda _s: True)  # any strategy_id resolves


def _run(coro):
    return asyncio.run(coro)


def _req(**kw):
    base = dict(strategy_id="X", method="bayesian", n_trials=200,
                evaluation_mode="option_rerank", rerank_top_k=50)
    base.update(kw)
    return OptimizerStartReq(**base)


def _survival(**kw):
    base = dict(enabled=True, max_drawdown_pct=35.0, max_ror_pct=5.0, ruin_floor=0.0)
    base.update(kw)
    return SurvivalConfigReq(**base)


async def _call(req):
    with patch.object(research, "get_registry", lambda: _REG_OK):
        return await research.optimize_start(req)


def _expect_400(req):
    with pytest.raises(HTTPException) as ei:
        _run(_call(req))
    assert ei.value.status_code == 400
    return ei.value


# --- O3: survival requires OPTION costs ---------------------------------------

def test_survival_rejects_when_option_costs_off():
    exc = _expect_400(_req(
        survival_config=_survival(),
        option_config={"exit_mode": "spot_exit", "cost_config": {"enabled": False}}))
    assert "option costs" in exc.detail.lower()


def test_survival_rejects_when_option_costs_missing():
    # No cost_config at all is the buildOptionConfig-with-costs-off case.
    _expect_400(_req(survival_config=_survival(), option_config={"exit_mode": "spot_exit"}))


def test_survival_spot_costs_on_but_option_costs_off_still_rejected():
    # The O3 bug: spot costs_enabled=True masked option costs being OFF.
    _expect_400(_req(
        costs_enabled=True, survival_config=_survival(),
        option_config={"exit_mode": "spot_exit", "cost_config": {"enabled": False}}))


def test_survival_passes_when_option_costs_on():
    req = _req(survival_config=_survival(),
               option_config={"exit_mode": "spot_exit", "cost_config": {"enabled": True},
                              "sizing_config": {"capital": 200000}})
    with patch.object(research, "get_registry", lambda: _REG_OK), \
         patch.object(research, "optimizer_create_job", AsyncMock(return_value="job123")):
        out = _run(research.optimize_start(req))
    assert out["job_id"] == "job123" and out["status"] == "queued"


# --- O2: capital drives the ruin-floor validation -----------------------------

def test_survival_ruin_floor_validated_against_user_capital():
    # ruin_floor 100k is fine under the 200k default but must FAIL under a 50k account.
    exc = _expect_400(_req(
        survival_config=_survival(ruin_floor=100_000),
        option_config={"cost_config": {"enabled": True}, "sizing_config": {"capital": 50_000}}))
    assert "ruin_floor" in exc.detail


# --- O5: exit-control search requires option-levels exit ----------------------

def test_exit_search_rejected_under_spot_exit():
    exc = _expect_400(_req(
        search_exit_controls=True,
        option_config={"exit_mode": "spot_exit", "cost_config": {"enabled": True}}))
    assert "exit_mode" in exc.detail and "option_levels" in exc.detail


def test_exit_search_passes_under_option_levels_exit():
    req = _req(search_exit_controls=True,
               option_config={"exit_mode": "option_levels", "cost_config": {"enabled": True}})
    with patch.object(research, "get_registry", lambda: _REG_OK), \
         patch.object(research, "optimizer_create_job", AsyncMock(return_value="jobX")):
        out = _run(research.optimize_start(req))
    assert out["job_id"] == "jobX"


def test_exit_search_off_ignores_exit_mode():
    # search off → exit_mode is irrelevant, no 400.
    req = _req(search_exit_controls=False,
               option_config={"exit_mode": "spot_exit", "cost_config": {"enabled": True}})
    with patch.object(research, "get_registry", lambda: _REG_OK), \
         patch.object(research, "optimizer_create_job", AsyncMock(return_value="jobY")):
        out = _run(research.optimize_start(req))
    assert out["job_id"] == "jobY"
