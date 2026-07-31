"""Deployment-wizard parameter validation must match the backend schema rules.

The backend deliberately accepts ``None`` when a strategy parameter's declared
default is ``None``.  The direct-deploy UI must not turn that executable config
into a promotion veto.  These tests execute the real frontend helper in Node;
source-string assertions cannot prove the validation behavior.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest


_LIB = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "src", "lib",
    "strategyParams.js",
)
_OPT_LIB = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "src", "lib",
    "optimizerCandidate.js",
)
_FRONTEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "src"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute the real frontend module",
)


def _run_js(body: str):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    src = (
        f"import * as M from {url!r};\n"
        f"const out = (() => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", src],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def _run_optimizer_js(body: str):
    url = "file:///" + os.path.abspath(_OPT_LIB).replace("\\", "/")
    src = (
        f"import * as M from {url!r};\n"
        f"const out = (() => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", src],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def test_nullable_defaults_are_valid_for_direct_strategy_promotion():
    schema = {
        "target_pct": {"type": "float", "default": None},
        "momentum_pts": {"type": "float", "default": None},
        "exit_time": {"type": "str", "default": None},
    }
    assert _run_js(
        f"return M.areStrategyParamsValid({json.dumps(schema)}, "
        f"{{target_pct:null, momentum_pts:null, exit_time:null}});"
    ) is True


def test_required_null_and_nonfinite_numeric_values_remain_invalid():
    schema = {
        "stop_pct": {"type": "float", "min": 10, "default": 20},
        "period": {"type": "int", "min": 2, "default": 10},
    }
    assert _run_js(
        f"return ["
        f"M.areStrategyParamsValid({json.dumps(schema)}, {{stop_pct:null, period:10}}),"
        f"M.areStrategyParamsValid({json.dumps(schema)}, {{stop_pct:20, period:Infinity}})"
        f"];"
    ) == [False, False]


def test_explicit_nullable_values_still_obey_type_and_range_rules():
    schema = {
        "target_pct": {"type": "float", "min": 10, "max": 40, "default": None},
        "exit_time": {"type": "str", "default": None},
    }
    assert _run_js(
        f"return ["
        f"M.areStrategyParamsValid({json.dumps(schema)}, {{target_pct:25, exit_time:'14:50'}}),"
        f"M.areStrategyParamsValid({json.dumps(schema)}, {{target_pct:5, exit_time:'14:50'}}),"
        f"M.areStrategyParamsValid({json.dumps(schema)}, {{target_pct:25, exit_time:''}})"
        f"];"
    ) == [True, False, False]


def test_clearing_a_nullable_input_preserves_null_not_an_empty_string():
    assert _run_js(
        "return ["
        "M.strategyParamInputValue('', {type:'float', default:null}),"
        "M.strategyParamInputValue('', {type:'str', default:null}),"
        "M.strategyParamInputValue('', {type:'float', default:20})"
        "];"
    ) == [None, None, ""]


def test_frontend_never_relabels_research_evidence_as_promotion_authority():
    paths = [
        os.path.join(_FRONTEND, "pages", "Optimizer.jsx"),
        os.path.join(_FRONTEND, "pages", "BacktestLab.jsx"),
        os.path.join(_FRONTEND, "pages", "StrategyLibrary.jsx"),
        os.path.join(_FRONTEND, "pages", "SavedPresets.jsx"),
    ]
    src = "\n".join(open(path, encoding="utf-8").read() for path in paths)
    for stale in (
        "forward validation decides promotion",
        "forward gates decide promotion",
        "cannot support promotion",
        "this is what makes a result deployable",
        "deployable only",
        "promotion ready",
    ):
        assert stale.lower() not in src.lower()
    assert "promotion remains available" in src
    assert "execution policy captured" in src


def test_optimizer_ui_recognizes_finite_zero_parameter_snapshot():
    assert _run_optimizer_js(
        "return M.hasFiniteOptimizerCandidate({status:'running', "
        "finite_candidate_available:true, best_params:{}, best_metrics:{sharpe:1.2}});"
    ) is True


def test_optimizer_ui_refuses_nested_nonfinite_snapshot():
    assert _run_optimizer_js(
        "return M.hasFiniteOptimizerCandidate({finite_candidate_available:true, "
        "best_params:{risk:{distance:Infinity}}, best_metrics:{sharpe:1.2}});"
    ) is False


def test_promotion_ui_keeps_all_saved_runs_and_acknowledgment_recovery_reachable():
    live = open(os.path.join(_FRONTEND, "pages", "LiveSignals.jsx"), encoding="utf-8").read()
    optimizer = open(os.path.join(_FRONTEND, "pages", "Optimizer.jsx"), encoding="utf-8").read()
    assert "listBacktestRuns(200)" in live
    assert "api.getBacktestRun(runId)" in live
    assert "acknowledgment_required" in live
    assert "setStep(3)" in live
    assert "LIVE REAL-MONEY" in live
    assert "from Strategy Library" in live
    assert "hasFiniteOptimizerCandidate(job)" in optimizer
