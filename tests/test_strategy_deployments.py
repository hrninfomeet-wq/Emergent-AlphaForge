import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategy_deployments import build_deployment_doc  # noqa: E402
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
        '@api.post("/deployments/{deployment_id}/archive")',
        '@api.get("/deployments/{deployment_id}/signals")',
    ):
        assert needle in server
    assert "strategy_deployments.create_index" in db


def test_frontend_exposes_strategy_deployment_panel():
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    live = (ROOT / "frontend" / "src" / "pages" / "LiveSignals.jsx").read_text(encoding="utf-8")

    for needle in ("listDeployments", "deploymentsOverview", "createDeployment", "pauseDeployment", "resumeDeployment", "archiveDeployment"):
        assert needle in api
    # Deployments command center (2026-06-12): cards + 3-step deploy wizard + undeploy.
    for needle in ("deployments-page", "deployment-card", "open-deploy-wizard",
                   "wizard-preset-select", "wizard-mode-select", "undeploy-button"):
        assert needle in live
