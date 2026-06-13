"""Contract pins for the backtest results redesign (2026-06-13).

A decision-first Performance section: rupee hero, account-value + underlying
chart (algotest-style cumulative-P&L-vs-underlying, NOT a benchmark overlay),
a dedicated drawdown pane, and a tight high-value metrics block — with the
deep research cards collapsed into an Advanced analytics section.

These are string-asserts on the frontend source (the repo's frontend tests
are pytest text pins; no JS test runner is wired up).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FE = ROOT / "frontend" / "src"


def _read(*parts):
    return (FE.joinpath(*parts)).read_text(encoding="utf-8")


def test_performance_overview_components_exist():
    metrics = _read("lib", "backtestMetrics.js")
    chart = _read("components", "backtest", "EquityUnderlyingChart.jsx")
    overview = _read("components", "backtest", "PerformanceOverview.jsx")
    assert "buildPerformanceSeries" in metrics and "computeKeyMetrics" in metrics
    assert "equity-underlying-chart" in chart
    assert "performance-overview" in overview and "perf-key-metrics" in overview


def test_results_uses_overview_and_collapses_advanced():
    page = _read("pages", "BacktestLab.jsx")
    assert "<PerformanceOverview result={result} />" in page
    assert "backtest-advanced-analytics" in page and "backtest-advanced-toggle" in page
    # The deep cards still exist — just moved inside the collapsible section.
    for testid in ("data-audit-card", "option-backtest-card", "mae-mfe-card", "monte-carlo-card"):
        assert testid in page


def test_metrics_are_honest_not_vanity():
    metrics = _read("lib", "backtestMetrics.js")
    overview = _read("components", "backtest", "PerformanceOverview.jsx")
    # CAGR/Calmar must be suppressed under ~1 year (no 1900% vanity numbers).
    assert "years >= 1.0" in metrics
    # Span-independent reward/risk ratio is the hero, not annualized CAGR.
    assert "returnOverMaxDd" in metrics
    assert "Profit ÷ max DD" in overview
    # Decision-critical trade-quality metrics are present.
    for needle in ("avgWin", "avgLoss", "payoff", "expectancy", "maxWinStreak", "ddDurationDays"):
        assert needle in metrics


def test_underlying_is_context_not_a_benchmark():
    # The user explicitly declined a buy-and-hold benchmark; the underlying line
    # is context only. Pin that intent: the chart documents it AND there is no
    # benchmark/buy-and-hold SERIES (only equity, underlying, drawdown series).
    chart = _read("components", "backtest", "EquityUnderlyingChart.jsx")
    assert "context only" in chart.lower()
    assert "benchmark" not in chart.lower().replace("not a buy-and-hold benchmark", "")
    # exactly three data series — no extra benchmark/buy-hold overlay
    assert chart.count("chart.addSeries(") == 3
