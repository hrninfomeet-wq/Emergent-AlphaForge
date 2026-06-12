import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.paper_trading import close_trade, mark_trade_to_market, paper_trade_from_signal  # noqa: E402
from app.signal_lifecycle import SignalStateError, create_signal_doc, transition_signal  # noqa: E402


def test_signal_lifecycle_persists_transition_history():
    signal = create_signal_doc(
        instrument="NIFTY",
        direction="LONG",
        strategy_id="confluence_scalper",
        entry_price=23950,
        confidence=72,
        reasons=["trend", "volume"],
        created_at="2026-05-26T10:00:00+00:00",
    )

    assert signal["state"] == "WATCHING"
    moved = transition_signal(signal, "FORMING", reason="setup forming", at="2026-05-26T10:01:00+00:00")
    moved = transition_signal(moved, "CONFIRMED", reason="bar close", at="2026-05-26T10:02:00+00:00")

    assert moved["state"] == "CONFIRMED"
    assert [event["to_state"] for event in moved["events"]] == ["WATCHING", "FORMING", "CONFIRMED"]
    assert moved["events"][-1]["reason"] == "bar close"


def test_signal_lifecycle_blocks_invalid_transition():
    signal = create_signal_doc(
        instrument="NIFTY",
        direction="LONG",
        strategy_id="test",
        entry_price=100,
        confidence=50,
    )

    try:
        transition_signal(signal, "EXITED", reason="cannot exit before active")
    except SignalStateError as exc:
        assert "Invalid signal transition" in str(exc)
    else:
        raise AssertionError("invalid transition did not raise")


def test_paper_trade_marks_and_closes_buy_option_pnl():
    signal = create_signal_doc(
        instrument="NIFTY",
        direction="LONG",
        strategy_id="test",
        entry_price=24000,
        confidence=70,
        option_contract={
            "instrument_key": "NSE_FO|123",
            "trading_symbol": "NIFTY 24000 CE",
            "lot_size": 50,
        },
        created_at="2026-05-26T10:00:00+00:00",
    )
    trade = paper_trade_from_signal(
        signal,
        lots=2,
        entry_price=120.0,
        at="2026-05-26T10:01:00+00:00",
    )

    assert trade["status"] == "OPEN"
    assert trade["quantity"] == 100
    assert trade["entry_value"] == 12000.0

    marked = mark_trade_to_market(trade, last_price=135.5, at="2026-05-26T10:05:00+00:00")
    assert marked["unrealized_pnl"] == 1550.0
    assert marked["last_price"] == 135.5

    closed = close_trade(marked, exit_price=110.0, reason="manual exit", at="2026-05-26T10:10:00+00:00")
    assert closed["status"] == "CLOSED"
    assert closed["realized_pnl"] == -1000.0
    assert closed["exit_reason"] == "manual exit"


def test_paper_trade_auto_closes_on_target_and_stop():
    signal = create_signal_doc(
        instrument="NIFTY",
        direction="LONG",
        strategy_id="risk_test",
        entry_price=24000,
        confidence=70,
        option_contract={"trading_symbol": "NIFTY 24000 CE", "lot_size": 50},
    )
    target_trade = paper_trade_from_signal(
        signal,
        lots=1,
        entry_price=100,
        stop_price=80,
        target_price=130,
        at="2026-05-26T10:00:00+00:00",
    )

    target_closed = mark_trade_to_market(
        target_trade,
        last_price=131,
        auto_close_on_risk=True,
        at="2026-05-26T10:05:00+00:00",
    )

    assert target_closed["status"] == "CLOSED"
    assert target_closed["exit_reason"] == "target_hit"
    assert target_closed["realized_pnl"] == 1550.0
    assert target_closed["risk"]["target_price"] == 130

    stop_trade = paper_trade_from_signal(
        signal,
        lots=1,
        entry_price=100,
        stop_price=80,
        target_price=130,
        at="2026-05-26T10:00:00+00:00",
    )
    stop_closed = mark_trade_to_market(
        stop_trade,
        last_price=79,
        auto_close_on_risk=True,
        at="2026-05-26T10:06:00+00:00",
    )

    assert stop_closed["status"] == "CLOSED"
    assert stop_closed["exit_reason"] == "stop_hit"
    assert stop_closed["realized_pnl"] == -1050.0


def test_backend_exposes_signal_and_paper_routes():
    """Forward-surfaces contract after the 2026-06-12 overhaul: the enriched
    ledger / purge / overview routes exist, and the retired manual flows
    (research-signal create, transitions, approve/skip/mark-blocked, manual
    deploy-to-paper) stay gone."""
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")

    for needle in (
        '@api.get("/signals")',
        '@api.get("/signals/enriched")',
        '@api.post("/signals/purge")',
        '@api.get("/deployments/overview")',
        '@api.get("/paper/trades")',
        '@api.post("/paper/trades/purge")',
        '@api.post("/paper/trades/{trade_id}/mark")',
        '@api.post("/paper/trades/{trade_id}/close")',
    ):
        assert needle in server

    for retired in (
        '@api.post("/signals")',
        '@api.post("/signals/{signal_id}/transition")',
        '@api.post("/signals/{signal_id}/approve")',
        '@api.post("/signals/{signal_id}/skip")',
        '@api.post("/signals/{signal_id}/mark-blocked")',
        '@api.post("/signals/{signal_id}/paper")',
    ):
        assert retired not in server, f"retired route resurfaced: {retired}"


def test_frontend_exposes_live_and_paper_operational_views():
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    live = (ROOT / "frontend" / "src" / "pages" / "LiveSignals.jsx").read_text(encoding="utf-8")
    paper = (ROOT / "frontend" / "src" / "pages" / "PaperTrading.jsx").read_text(encoding="utf-8")
    ledger = (ROOT / "frontend" / "src" / "pages" / "SignalJournal.jsx").read_text(encoding="utf-8")

    for needle in ("listSignals", "listSignalsEnriched", "purgeSignals", "deploymentsOverview",
                   "listPaperTrades", "purgePaperTrades", "markPaperTrade", "closePaperTrade"):
        assert needle in api
    # The Deployments command center (rebuilt Live page, 2026-06-12).
    for needle in ("deployments-page", "deployment-card", "open-deploy-wizard", "undeploy-button"):
        assert needle in live
    for needle in ("paper-trading-journal", "paper-trade-table", "mark-paper-trade", "close-paper-trade", "risk-badge"):
        assert needle in paper
    # The Signals ledger (rebuilt /journal page, forward-surfaces R3, 2026-06-12).
    # Built on the enriched endpoint, with server-side filter/sort/CSV and the
    # signals-purge deletion toolkit.
    assert "listSignalsEnriched" in ledger and "purgeSignals" in ledger
    for needle in ("signals-ledger-page", "signals-ledger-table", "signals-ledger-row",
                   "ledger-deployment-filter", "ledger-state-filter", "ledger-clean-filter",
                   "ledger-export-csv", "ledger-delete-selected", "ledger-delete-older",
                   "ledger-purge-deployment", "ledger-next-page"):
        assert needle in ledger
