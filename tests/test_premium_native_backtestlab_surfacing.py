"""Backtest Lab surfacing for premium-native (premium_momentum) runs.

User-reported bug (2026-07-14 21:27 run): the run stored 127 PAIRED trades in
option_backtest.trades, but the UI looked completely dead — every main-view
pane (performance, chart, trade list) reads SPOT trades, which are structurally
empty for this strategy (evaluate() is a stub by design), and the only renderer
of option-native trades (OptionBacktestCard) lived inside the collapsed
Advanced-analytics section. The option-preflight endpoint likewise derived
needed contracts from spot trades -> a misleading permanent 0%.

These are host string-pins over source (the repo's standard for JSX/wiring
assertions — see tests/test_premium_momentum.py) pinning the three fixes:
 1. BacktestLab hoists OptionBacktestCard + an explainer banner into the main
    flow when result.option_backtest.dispatch == "premium_trigger_config".
 2. runtime._option_preflight_report has a premium-native branch reporting
    per-session locked-strike coverage (never the spot-derived 0%).
 3. runtime._run_paired_option_backtest threads the option form's lots +
    cost_config into the dispatch params (the plugin schema exposes neither,
    so the form is the user's only way to set them in the Backtest Lab).
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_backtestlab_hoists_option_card_for_premium_native_runs():
    page = (_ROOT / "frontend" / "src" / "pages" / "BacktestLab.jsx").read_text(encoding="utf-8")
    assert "premium-native-banner" in page
    # The banner + hoisted card are gated on the dispatch marker, not on
    # strategy name string-matching (the marker travels with the stored run).
    assert page.count('dispatch === "premium_trigger_config"') >= 2, (
        "expected the dispatch gate on both the hoisted card and the preflight toast"
    )
    # The hoist must appear BEFORE the AdvancedAnalytics section so the result
    # is visible without expanding anything.
    assert page.index("premium-native-banner") < page.index("<AdvancedAnalytics>")


def test_preflight_report_has_premium_native_branch():
    src = (_ROOT / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")
    i = src.index("async def _option_preflight_report")
    body = src[i:i + 6000]
    assert 'req.strategy_id == "premium_momentum"' in body
    assert "run_premium_momentum_backtest" in body
    # Panel-compat fields must be present in the premium report.
    for field in ('"total_spot_trades"', '"would_pair"', '"coverage_pct"', '"missing_candle"'):
        assert field in body, f"premium preflight report missing {field}"


def test_paired_backtest_threads_form_lots_and_costs_into_dispatch():
    src = (_ROOT / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")
    i = src.index("async def _run_paired_option_backtest")
    body = src[i:i + 7000]
    assert 'pm_params["lots"] = int(req.params.get("lots") or config.lots or 1)' in body
    assert 'pm_params["cost_config"] = config.cost_config' in body
    # Plugin schema defaults must be applied (a raw request with empty params
    # must behave like the UI's filled panel, not fail config validation and
    # silently fall through to the always-empty generic spot path).
    assert "merged_params(req.params)" in body
    # And the dispatch call must use the threaded params, not raw req.params.
    assert "merged_params=pm_params" in body
