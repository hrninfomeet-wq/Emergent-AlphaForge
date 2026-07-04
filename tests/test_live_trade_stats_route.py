"""Wiring pins for the live trade-statistics surface (item: store + display
live trade history for analysis).

The heavy lifting reuses paper_analytics (period_pnl / per_strategy_stats),
which have their own behavioral suites — these pins hold the wiring together:
route present and reusing the shared aggregators, api.js helper present, the
dashboard renders the card, and the live_trades docs carry the fields the
aggregators key on (guaranteed by the close-loop's own suite).
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
BROKER_SRC = (ROOT / "backend/app/routers/live_broker.py").read_text(encoding="utf-8")
API_SRC = (ROOT / "frontend/src/lib/api.js").read_text(encoding="utf-8")
DASH_SRC = (ROOT / "frontend/src/components/live/LiveDashboard.jsx").read_text(encoding="utf-8")
CARD_SRC = (ROOT / "frontend/src/components/live/LiveTradeStats.jsx").read_text(encoding="utf-8")


def test_route_exists_and_reuses_paper_aggregators():
    assert '@api.get("/live-broker/trade-stats")' in BROKER_SRC
    assert "paper_analytics.period_pnl(closed)" in BROKER_SRC
    assert "paper_analytics.per_strategy_stats(rows)" in BROKER_SRC


def test_route_reads_the_close_loop_journal_fields():
    i = BROKER_SRC.index('"/live-broker/trade-stats"')
    block = BROKER_SRC[i:i + 2200]
    for field in ('"realized_pnl"', '"closed_at"', '"strategy_id"', '"deployment_id"'):
        assert field in block, f"{field} not projected from live_trades"


def test_frontend_helper_and_card_wired():
    assert '"/live-broker/trade-stats"' in API_SRC
    assert "liveTradeStats" in API_SRC
    assert "<LiveTradeStats />" in DASH_SRC
    assert 'data-testid="live-trade-stats"' in CARD_SRC
    # analysis essentials on the card
    for needle in ("Win rate", "Profit factor", "Lifetime", "Expectancy"):
        assert needle in CARD_SRC


def test_trade_history_route_returns_full_close_fields():
    assert '@api.get("/live-broker/trade-history")' in BROKER_SRC
    i = BROKER_SRC.index('"/live-broker/trade-history"')
    block = BROKER_SRC[i:i + 2400]
    assert 'sort("created_at", -1)' in block
    assert "count_documents" in block  # pagination total
    assert '"/live-broker/trade-history"' in API_SRC
    assert 'data-testid="live-trade-history"' in CARD_SRC
    # never fabricate: the card renders None P&L/exit as "—"
    assert 't.realized_pnl != null' in CARD_SRC


def test_charges_surfaces_are_wired():
    from pathlib import Path
    blotter = (ROOT / "frontend/src/components/paper/TradeBlotter.jsx").read_text(encoding="utf-8")
    stats = (ROOT / "frontend/src/components/paper/StrategyStatsTable.jsx").read_text(encoding="utf-8")
    bt = (ROOT / "frontend/src/pages/BacktestLab.jsx").read_text(encoding="utf-8")
    pa = (ROOT / "backend/app/paper_analytics.py").read_text(encoding="utf-8")
    assert "<H right>Charges</H>" in blotter
    assert "total_charges" in stats
    assert '{ key: "opt_charges", label: "Charges ₹"' in bt
    assert 'g["total_charges"] += _f(t.get("total_charges"))' in pa
