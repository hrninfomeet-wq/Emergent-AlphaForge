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


def test_chart_right_axis_is_per_trade_buy_value_not_index():
    # The user's definition: right axis = per-trade net buy value (premium × qty
    # + charges), NOT the index spot level. Sell − Buy must equal net P&L.
    metrics = _read("lib", "backtestMetrics.js")
    assert "tradeBuyValue" in metrics and "tradeSellValue" in metrics
    assert "entry_option_price" in metrics and "total_charges" in metrics
    chart = _read("components", "backtest", "EquityUnderlyingChart.jsx")
    assert "Trade buy value" in chart  # default right-axis label
    # account value + drawdown are clubbed in the lower pane (4 series total).
    assert chart.count("chart.addSeries(") == 4


def test_monthly_pnl_calendar_present():
    cal = _read("components", "backtest", "MonthlyPnlCalendar.jsx")
    overview = _read("components", "backtest", "PerformanceOverview.jsx")
    assert "monthly-pnl-calendar" in cal
    assert "MonthlyPnlCalendar" in overview
    assert "monthlyPnl" in _read("lib", "backtestMetrics.js")


def test_recovered_is_explained():
    overview = _read("components", "backtest", "PerformanceOverview.jsx")
    # Self-explanatory wording + an explanatory tooltip (the user couldn't tell
    # what "recovered" meant).
    assert "recovered to new high" in overview and "not yet recovered" in overview
    assert "below a previous peak" in overview


def test_trades_table_has_lots_buy_sell_columns():
    page = _read("pages", "BacktestLab.jsx")
    assert 'label: "Lots (Qty)"' in page
    assert 'label: "Buy ₹"' in page and 'label: "Sell ₹"' in page
    assert "opt_buy_value" in page and "opt_sell_value" in page


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


def test_no_buy_and_hold_benchmark_series():
    # The user explicitly declined a buy-and-hold benchmark. The right-axis line
    # is per-trade buy value (or, spot-only, the index level) — never a
    # buy-and-hold equity series. Pin: no benchmark series in the chart.
    chart = _read("components", "backtest", "EquityUnderlyingChart.jsx").lower()
    assert "not a benchmark" in chart  # documented intent
    # the four series are cum P&L, buy value, account value, drawdown — no 5th.
    assert chart.count("chart.addseries(") == 4
