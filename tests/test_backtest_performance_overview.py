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
    chart = _read("components", "backtest", "DualAxisChart.jsx")
    overview = _read("components", "backtest", "PerformanceOverview.jsx")
    assert "buildPerformanceSeries" in metrics and "computeKeyMetrics" in metrics
    assert "DualAxisChart" in chart
    assert "performance-overview" in overview and "perf-key-metrics" in overview


def test_two_separate_charts_with_named_axes():
    # The single dual-pane chart was split into two separate charts, each with
    # NAMED, vertically-oriented (text-up) left and right axis titles.
    overview = _read("components", "backtest", "PerformanceOverview.jsx")
    assert "chart-pnl-vs-value" in overview and "chart-account-drawdown" in overview
    # axis labels passed to the two charts
    for label in ('label: "Cumulative P&L"', '"Trade value"', 'label: "Account value"', 'label: "Drawdown"'):
        assert label in overview
    chart = _read("components", "backtest", "DualAxisChart.jsx")
    # vertical, text-up axis-title rendering
    assert "writingMode" in chart and "rotate(180deg)" in chart
    assert "AxisTitle" in chart


def test_chart_right_axis_is_per_trade_buy_value_not_index():
    # The user's definition: right axis = per-trade net buy value (premium × qty
    # + charges), NOT the index spot level. Sell − Buy must equal net P&L.
    metrics = _read("lib", "backtestMetrics.js")
    assert "tradeBuyValue" in metrics and "tradeSellValue" in metrics
    assert "entry_option_price" in metrics and "total_charges" in metrics
    overview = _read("components", "backtest", "PerformanceOverview.jsx")
    assert '"Trade value"' in overview


def test_account_value_low_high_cards():
    # Lowest/Highest account value live in the top KPI grid (with Trades / Win
    # Rate), not the Trade-quality block.
    page = _read("pages", "BacktestLab.jsx")
    assert "result-acct-low" in page and "result-acct-high" in page
    assert "Lowest Acct Value" in page and "Highest Acct Value" in page
    assert "buildPerformanceSeries" in page  # account range computed in ResultsView
    overview = _read("components", "backtest", "PerformanceOverview.jsx")
    assert "Lowest account value" not in overview  # moved out of Trade-quality


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


def test_backtest_chart_moved_below_overview_and_out_of_advanced():
    page = _read("pages", "BacktestLab.jsx")
    chart = _read("components", "backtest", "BacktestChart.jsx")
    # Dedicated chart exists and is placed after the overview, before Advanced.
    assert "<BacktestChart result={result} />" in page
    i_overview = page.index("<PerformanceOverview result={result} />")
    i_chart = page.index("<BacktestChart result={result} />")
    i_adv = page.index("<AdvancedAnalytics>")
    assert i_overview < i_chart < i_adv
    # The old MultiPaneChart is no longer in the results.
    assert "MultiPaneChart" not in page
    # Pro features: title, timeframe buttons, entry/exit markers, SL/target
    # price lines, and a date/time go-to.
    assert "backtest-chart-title" in chart
    assert "backtest-chart-tf-" in chart  # timeframe buttons (template testid)
    assert '"1m", "5m", "15m", "1h", "1d"' in chart  # TIMEFRAMES
    assert "createSeriesMarkers" in chart and "createPriceLine" in chart
    assert "spot_target_pts" in chart and "spot_stop_pts" in chart
    assert "backtest-chart-goto-date" in chart and "backtest-chart-trade-select" in chart
    # Markers carry the trade number (#N), gated by density so the dense
    # overview doesn't become an unreadable wall of labels.
    assert "labelMarkers" in chart and "`#${n} ${t.direction}`" in chart


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
    # The user explicitly declined a buy-and-hold benchmark. Each chart has
    # exactly TWO series (left + right) — no benchmark/buy-and-hold overlay.
    overview = _read("components", "backtest", "PerformanceOverview.jsx").lower()
    assert "benchmark" not in overview
    assert "buy-and-hold" not in overview
