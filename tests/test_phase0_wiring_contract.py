"""Source contracts for Phase 0.2 / 0.3 / 0.4 — "built but unreachable" fixes.

Each of these backend capabilities was fully implemented and tested, and had
ZERO frontend callers (verified by grep, 2026-07-27):

  0.2  GET  /live-broker/recovery-status      — its docstring literally says it
       exists to drive a UI strip; nothing called it, so an operator could not
       tell whether overnight position recovery had completed.
  0.3  source_type="backtest_run"             — accepted by the deployment
       builder with the same validation parity as a preset, but the wizard only
       offered preset/library, forcing a save-a-preset detour on EVERY deploy.
  0.4  GET  /strategies/{id}/pipeline         — "Powers the StrategyCard stage
       chips"; no caller, so the Library stayed a flat list.

The failure mode these guard is silent un-wiring: a component that is imported
but never rendered, or an API method that loses its only call site during a
refactor. That is exactly how ExecutionStateStrip disappeared in the cockpit
rewrite, and nothing failed at the time.
"""
from __future__ import annotations

import os

ROOT = os.path.join(os.path.dirname(__file__), "..", "frontend", "src")


def _read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# 0.2 — overnight recovery visibility
# --------------------------------------------------------------------------- #

def test_recovery_status_api_and_component_exist():
    assert "getRecoveryStatus" in _read("lib", "api.js")
    assert "/live-broker/recovery-status" in _read("lib", "api.js")
    src = _read("components", "live", "RecoveryStatusBanner.jsx")
    assert "api.getRecoveryStatus" in src
    assert "succeeded" in src, "the banner must key off the recovery latch itself"


def test_recovery_banner_is_mounted():
    src = _read("components", "live", "LiveCockpit.jsx")
    assert "<RecoveryStatusBanner" in src, "imported-but-never-rendered is the bug"


def test_recovery_banner_severity_follows_exposure():
    """Unrecovered WITH open positions is the dangerous case and must read
    differently from an unrecovered but flat book."""
    src = _read("components", "live", "RecoveryStatusBanner.jsx")
    assert "openPositions" in src
    assert "danger" in src and "warning" in src


def test_recovery_banner_says_nothing_when_it_knows_nothing():
    """Before the first poll returns, and while disconnected, the component must
    not assert a state it cannot vouch for."""
    src = _read("components", "live", "RecoveryStatusBanner.jsx")
    assert "if (!state) return null" in src, (
        "must bail out while the recovery state is still unknown")
    assert "if (!connected" in src, (
        "must bail out when disconnected — the broker banner already covers that")


# --------------------------------------------------------------------------- #
# 0.3 — deploy straight from a backtest run
# --------------------------------------------------------------------------- #

def test_wizard_offers_backtest_run_as_a_source():
    src = _read("pages", "LiveSignals.jsx")
    assert '"backtest_run"' in src, "the third source must be offered in the picker"
    assert "wizard-backtest-select" in src, "a run selector must exist"
    assert "listBacktestRuns" in src, "the runs must actually be fetched"


def test_backtest_lab_can_deploy_without_saving_a_preset():
    src = _read("pages", "BacktestLab.jsx")
    assert "result-deploy" in src, "the results view needs a Deploy control"
    assert "/live?backtest=" in src, "Deploy must deep-link the run id"


def test_deploy_deeplink_is_consumed_and_guarded():
    """The wizard must only auto-open once, and only for a run that really
    exists — otherwise a stale/hand-typed id opens an unusable wizard."""
    src = _read("pages", "LiveSignals.jsx")
    assert 'searchParams.get("backtest")' in src
    assert "deepLinkRef" in src
    assert "backtestRuns.find" in src, "must reuse the run when it is already listed"
    assert "api.getBacktestRun(runId)" in src, "must verify older/deep-linked run ids before opening"


def test_saving_a_preset_is_no_longer_presented_as_the_only_route():
    """The preset button must stop being the primary CTA now that Deploy exists —
    otherwise the detour is merely optional-but-still-implied."""
    src = _read("pages", "BacktestLab.jsx")
    i_save = src.index("result-save-preset")
    i_deploy = src.index("result-deploy")
    assert i_save < i_deploy, "expected Save-as-preset then Deploy in the toolbar"
    assert "Not required in order to deploy" in src, (
        "the preset button's tooltip should say a preset is not required")


# --------------------------------------------------------------------------- #
# 0.4 — pipeline stage chips
# --------------------------------------------------------------------------- #

def test_pipeline_api_and_component_exist():
    assert "getStrategyPipeline" in _read("lib", "api.js")
    src = _read("components", "strategy", "PipelineChips.jsx")
    assert "api.getStrategyPipeline" in src
    assert "stages" in src


def test_pipeline_chips_are_mounted_on_the_card():
    src = _read("pages", "StrategyLibrary.jsx")
    assert "import PipelineChips" in src, "component imported"
    assert "<PipelineChips" in src, "component actually rendered"


def test_pipeline_chips_distinguish_ever_live_from_currently_live():
    """`live_ever_count` (configured for live at some point) and
    `live_armed_count` (live RIGHT NOW) mean very different things for a
    retire/delete decision; the chip must not blur them."""
    src = _read("components", "strategy", "PipelineChips.jsx")
    assert "live_armed_count" in src
    assert "live_ever_count" in src


def test_pipeline_chips_stay_silent_on_a_failed_read():
    """An all-dim row after a failed fetch would read as 'never tested'."""
    src = _read("components", "strategy", "PipelineChips.jsx")
    assert "failed" in src and "return null" in src
