"""Item #10 frontend wiring (audit S17) — string-pins over JSX source (no JS test
runner; babel-parse is verified separately). HOST test (reads frontend/src)."""
from __future__ import annotations

from pathlib import Path

_FE = Path(__file__).resolve().parents[1] / "frontend" / "src"


def _src(rel: str) -> str:
    return (_FE / rel).read_text(encoding="utf-8")


def test_backtestlab_handles_strategy_deep_link():
    src = _src("pages/BacktestLab.jsx")
    assert 'searchParams.get("strategy")' in src
    assert 'strategy_id: strategyId' in src


def test_optimizer_handles_strategy_deep_link():
    src = _src("pages/Optimizer.jsx")
    assert 'get("strategy")' in src
    assert "strategy_id: sid" in src


def test_wizard_next_step_panel_and_ctas():
    src = _src("components/strategy/AuthoringWizard.jsx")
    # the toast no longer just vanishes — a next-step panel with CTAs is shown
    assert 'data-testid="author-next-steps"' in src
    assert "/backtest?strategy=" in src and "/optimizer?strategy=" in src
    assert "setInstalledId(res.strategy_id)" in src


def test_wizard_invalidates_stale_reject_lock():
    src = _src("components/strategy/AuthoringWizard.jsx")
    # editing the source clears the stale feasibility verdict so a fixed spec can install
    assert "useEffect(() => { setRuleSet(null); }, [aiSource]);" in src
