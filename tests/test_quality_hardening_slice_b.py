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
