import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategy_deployments import build_deployment_doc, deployment_sizing_from_source  # noqa: E402
from tests.contract_corpus import backend_api_text


def test_build_deployment_from_preset_freezes_auditable_config():
    preset = {
        "name": "nifty optimized preset",
        "config": {
            "instrument": "NIFTY",
            "strategy_id": "confluence_scalper",
            "params": {"ema_fast": 9, "ema_slow": 21},
            "mode": "SCALP",
        },
        "saved_at": "2026-05-26T10:00:00+00:00",
    }

    doc = build_deployment_doc(
        source_type="preset",
        source_doc=preset,
        name="NIFTY forward test",
        mode="shadow",  # legacy value: must map to signal_only
        confirmation_mode="1m_close",
        option_moneyness=["atm", "otm1"],
        pretrade_profile="Balanced",
        risk={"stop_price": 80, "target_price": 130},
        now="2026-05-26T11:00:00+00:00",
    )

    assert doc["source_type"] == "preset"
    assert doc["source_id"] == "nifty optimized preset"
    assert doc["strategy_id"] == "confluence_scalper"
    assert doc["instrument"] == "NIFTY"
    assert doc["params"] == {"ema_fast": 9, "ema_slow": 21}
    assert doc["confirmation_mode"] == "1m_close"
    assert doc["option_policy"]["moneyness"] == ["atm", "otm1"]
    assert doc["mode"] == "signal_only"  # legacy "shadow" mapped
    assert doc["manual_approval_required"] is False  # approval flow retired
    assert doc["status"] == "ACTIVE"


def test_build_deployment_from_backtest_uses_applied_params_and_metrics():
    run = {
        "id": "run-1",
        "name": "best backtest",
        "instrument": "BANKNIFTY",
        "strategy_id": "orb_breakout",
        "config": {
            "instrument": "BANKNIFTY",
            "strategy_id": "orb_breakout",
            "params": {"range_minutes": 15},
        },
        "params_applied": {"range_minutes": 15, "stop": 40},
        "metrics": {"total_pnl_pts": 120.5, "trade_count": 8},
    }

    doc = build_deployment_doc(
        source_type="backtest_run",
        source_doc=run,
        name="ORB forward",
        mode="recommendation",  # retired mode: must map to signal_only
        now="2026-05-26T11:00:00+00:00",
    )

    assert doc["source_type"] == "backtest_run"
    assert doc["source_id"] == "run-1"
    assert doc["mode"] == "signal_only"  # legacy "recommendation" mapped
    assert doc["params"] == {"range_minutes": 15, "stop": 40}
    assert doc["source_snapshot"]["metrics"]["trade_count"] == 8
    assert doc["option_policy"]["moneyness"] == ["atm"]


def test_build_deployment_rejects_unknown_mode():
    preset = {"name": "p", "config": {"instrument": "NIFTY", "strategy_id": "s", "params": {}}}
    try:
        build_deployment_doc(source_type="preset", source_doc=preset, name="x", mode="autopilot")
        assert False, "expected ValueError for unknown mode"
    except ValueError as e:
        assert "signal_only or paper" in str(e)


def test_backend_exposes_strategy_deployment_routes_and_index():
    server = backend_api_text()
    db = (ROOT / "backend" / "app" / "db.py").read_text(encoding="utf-8")

    for needle in (
        '@api.get("/deployments")',
        '@api.post("/deployments")',
        '@api.get("/deployments/{deployment_id}")',
        '@api.post("/deployments/{deployment_id}/pause")',
        '@api.post("/deployments/{deployment_id}/resume")',
        '@api.post("/deployments/{deployment_id}/stop")',
        '@api.post("/deployments/stop-all")',
        '@api.post("/deployments/{deployment_id}/archive")',
        '@api.get("/deployments/{deployment_id}/signals")',
        # Live control surface (strategy-deploy-to-live)
        '@api.post("/deployments/{deployment_id}/live/arm")',
        '@api.post("/deployments/{deployment_id}/live/disarm")',
        '@api.post("/deployments/{deployment_id}/live/stop")',
        '@api.get("/deployments/{deployment_id}/live/status")',
    ):
        assert needle in server
    assert "strategy_deployments.create_index" in db


def test_safety_config_body_exposes_max_lots_per_order():
    """The live safety-config PUT body carries the account lot-ceiling field
    (it flows through SafetyConfigStore.put_config, which validates it)."""
    server = backend_api_text()
    assert "max_lots_per_order" in server


def test_frontend_exposes_strategy_deployment_panel():
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    live = (ROOT / "frontend" / "src" / "pages" / "LiveSignals.jsx").read_text(encoding="utf-8")

    for needle in ("listDeployments", "deploymentsOverview", "createDeployment", "pauseDeployment", "resumeDeployment", "archiveDeployment"):
        assert needle in api
    # Deployments command center (2026-06-12): cards + 3-step deploy wizard + undeploy.
    for needle in ("deployments-page", "deployment-card", "open-deploy-wizard",
                   "wizard-preset-select", "wizard-mode-select", "undeploy-button"):
        assert needle in live


def test_deployment_sizing_from_backtest_run_extracts_policy():
    run = {
        "id": "run-1",
        "option_backtest": {
            "sizing_config": {"enabled": True, "mode": "premium_at_risk",
                              "capital": 200_000, "risk_per_trade_pct": 1.0, "max_lots": 10},
            "request": {"lots": 2},
        },
    }
    pin = deployment_sizing_from_source("backtest_run", run)
    assert pin is not None
    assert pin["sizing_config"]["enabled"] is True
    assert pin["sizing_config"]["mode"] == "premium_at_risk"
    assert pin["sizing_config"]["capital"] == 200_000
    assert pin["lots"] == 2
    assert pin["source_id"] == "run-1"


def test_deployment_sizing_from_preset_extracts_policy():
    preset = {"name": "p1", "config": {"execution": {
        "lots": 3,
        "sizing_config": {"enabled": False, "mode": "fixed_lots", "fixed_lots": 3, "max_lots": 10},
    }}}
    pin = deployment_sizing_from_source("preset", preset)
    assert pin is not None
    assert pin["sizing_config"]["enabled"] is False
    assert pin["lots"] == 3
    assert pin["source_id"] == "p1"


def test_deployment_sizing_none_when_preset_has_no_sizing_config():
    preset = {"name": "old", "config": {"execution": {"lots": 5}}}  # legacy preset
    assert deployment_sizing_from_source("preset", preset) is None


def test_deployment_sizing_none_for_spot_only_or_unknown():
    assert deployment_sizing_from_source("backtest_run", {"id": "r"}) is None
    assert deployment_sizing_from_source("weird", {}) is None


def test_deployment_sizing_defaults_lots_to_one_when_absent():
    run = {"id": "r2", "option_backtest": {
        "sizing_config": {"enabled": True, "mode": "premium_at_risk"}}}  # no request
    pin = deployment_sizing_from_source("backtest_run", run)
    assert pin is not None
    assert pin["lots"] == 1


def test_deployment_sizing_tolerates_non_numeric_preset_lots():
    preset = {"name": "p", "config": {"execution": {
        "lots": "abc",  # corrupted/hand-edited
        "sizing_config": {"enabled": False, "mode": "fixed_lots"}}}}
    pin = deployment_sizing_from_source("preset", preset)
    assert pin is not None
    assert pin["lots"] == 1


def test_build_deployment_pins_sizing_from_source():
    run = {
        "id": "run-9", "strategy_id": "s", "instrument": "NIFTY",
        "config": {"strategy_id": "s", "instrument": "NIFTY", "params": {}},
        "option_backtest": {
            "sizing_config": {"enabled": True, "mode": "premium_at_risk",
                              "capital": 200_000, "risk_per_trade_pct": 1.0, "max_lots": 10},
            "request": {"lots": 2},
        },
    }
    doc = build_deployment_doc(source_type="backtest_run", source_doc=run, name="d", mode="paper")
    assert doc["risk"]["sizing"]["sizing_config"]["enabled"] is True
    assert doc["risk"]["sizing"]["sizing_config"]["mode"] == "premium_at_risk"
    assert doc["risk"]["sizing"]["lots"] == 2
    assert doc["risk"]["sizing"]["source_id"] == "run-9"


def test_build_deployment_no_sizing_when_source_lacks_it():
    preset = {"name": "old", "config": {"instrument": "NIFTY", "strategy_id": "s",
              "params": {}, "execution": {"lots": 5}}}
    doc = build_deployment_doc(source_type="preset", source_doc=preset, name="d", mode="paper",
                               risk={"stop_price": 80})
    assert "sizing" not in doc["risk"]
    assert doc["risk"]["stop_price"] == 80      # caller-supplied risk key preserved
    assert "allow_overnight" in doc["risk"]     # always-present key intact
