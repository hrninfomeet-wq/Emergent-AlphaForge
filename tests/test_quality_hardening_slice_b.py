"""Contract tests for quality-hardening Slice B — research analytics.

These are pure string-asserts on frontend source (no server import, no motor):
they pin the client-side analytics surfaces added in Slice B so a future
refactor cannot silently drop them. Each item is added in its own commit:
  1. MAE/MFE distribution card        (Backtest Lab results)
  2. Monte Carlo card                 (Backtest Lab results)
  3. Run comparison view              (Backtest Run Journal)
  4. Volatility audit panel           (Data Warehouse)
  5. risk_hints in the Signals Ledger detail row
"""
from pathlib import Path
from tests.contract_corpus import backend_api_text
from tests.contract_corpus import warehouse_page_text

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"


def _read(*parts):
    return (FRONTEND.joinpath(*parts)).read_text(encoding="utf-8")


def test_backtest_lab_exposes_mae_mfe_distribution_card():
    lab = _read("pages", "BacktestLab.jsx")
    # The card is wired into the results view and renders both histograms.
    assert "MaeMfeCard" in lab
    for needle in ("mae-mfe-card", "mfe-histogram", "mae-histogram", "mae-mfe-hint"):
        assert needle in lab, f"missing MAE/MFE surface: {needle}"
    # Client-side math only — the excursions come from the trade docs, not a
    # new backend field. Option-leg excursions are preferred when paired.
    assert "option_mfe_pts" in lab and "option_mae_pts" in lab
    assert "mfe_pts" in lab and "mae_pts" in lab


def test_backtest_lab_exposes_monte_carlo_card():
    lab = _read("pages", "BacktestLab.jsx")
    assert "MonteCarloCard" in lab
    for needle in ("monte-carlo-card", "mc-drawdown-block", "mc-ending-block",
                   "mc-pneg-block", "monte-carlo-hint"):
        assert needle in lab, f"missing Monte Carlo surface: {needle}"
    # Bootstrap with replacement over the per-trade P&L, capped at 1,000 trades,
    # 1,000 runs — all client-side, no backend field.
    assert "slice(0, 1000)" in lab
    assert "RUNS = 1000" in lab
    assert "option_pnl_value" in lab and "pnl_pts" in lab


def test_backtest_run_journal_exposes_run_comparison():
    journal = _read("components", "BacktestRunJournal.jsx")
    comp = _read("components", "RunComparison.jsx")
    # The journal offers a two-run compare and renders the comparison component.
    assert "RunComparison" in journal
    assert "journal-compare-button" in journal
    assert "selected.size === 2" in journal
    # The comparison view: params diff, metric table, overlaid equity curves.
    for needle in ("run-comparison-panel", "comparison-params-table",
                   "comparison-metric-table", "comparison-equity-overlay",
                   "comparison-param-diff"):
        assert needle in comp, f"missing comparison surface: {needle}"
    # Built on the existing per-run endpoint — no backend change.
    assert "getBacktestRun" in journal


def test_data_warehouse_exposes_volatility_audit_panel():
    server = backend_api_text()
    api = _read("lib", "api.js")
    warehouse = warehouse_page_text()
    # Endpoint already exists (no backend change needed) and api.js calls it.
    assert '@api.post("/volatility/audit")' in server
    assert "volatilityAudit" in api
    # The read-only panel: spike count, spike share, top-10 spike bars table.
    assert "VolatilityAuditPanel" in warehouse
    for needle in ("volatility-audit-panel", "volatility-run-button",
                   "volatility-summary", "volatility-spikes-table",
                   "volatility-spike-row"):
        assert needle in warehouse, f"missing volatility surface: {needle}"


def test_signals_ledger_detail_shows_risk_hints():
    ledger = _read("pages", "SignalJournal.jsx")
    # The enriched row carries risk_hints; the detail row renders them next to
    # the entry triggers (spot pts / premium % / time stop).
    assert "ledger-risk-hints" in ledger
    assert "risk_hints" in ledger
    for needle in ("spot_target_pts", "spot_stop_pts", "target_pct", "stop_pct", "time_stop_minutes"):
        assert needle in ledger, f"missing risk hint key: {needle}"
