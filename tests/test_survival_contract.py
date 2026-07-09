import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from tests.contract_corpus import backend_api_text
from app.survival_validate import validate_survival_request


def test_survival_config_in_schema_corpus():
    src = backend_api_text()
    assert "class SurvivalConfigReq" in src
    assert "survival_config" in src
    assert "min_equity" in src and "max_drawdown_pct" in src and "max_ror_pct" in src


def test_optimize_start_validates_survival():
    src = backend_api_text()
    assert "survival_config" in src
    assert "costs_enabled" in src
    assert "option_rerank" in src


def _req(**kw):
    base = dict(enabled=True, evaluation_mode="option_rerank",
                costs_enabled=True, capital=200_000, ruin_floor=0.0,
                max_drawdown_pct=35.0, max_ror_pct=5.0)
    base.update(kw)
    return base


def test_survival_ok_when_all_requirements_met():
    assert validate_survival_request(**_req()) is None


def test_survival_requires_option_rerank():
    # option execution in the optimizer IS option_rerank mode — that's the gate.
    msg = validate_survival_request(**_req(evaluation_mode="spot"))
    assert msg and "option_rerank" in msg


def test_survival_requires_costs_enabled():
    msg = validate_survival_request(**_req(costs_enabled=False))
    assert msg and "costs" in msg.lower()


def test_survival_costs_message_names_the_option_cost_flag():
    # O3: the message must point at option_config.cost_config (the flag that actually
    # governs the survival curve), not the spot costs_enabled — else the user re-checks
    # the wrong switch and the gate can still judge a gross option curve.
    msg = validate_survival_request(**_req(costs_enabled=False))
    assert "option_config.cost_config.enabled" in msg


def test_survival_rejects_ruin_floor_ge_capital():
    msg = validate_survival_request(**_req(ruin_floor=200_000))
    assert msg and "ruin_floor" in msg
