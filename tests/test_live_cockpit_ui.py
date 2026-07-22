"""Source-contract pins for the Live Cockpit (2026-07-22 redesign, Phase 1).

The cockpit is a re-organisation of the Live Trading terminal into an always-on
core + config drawer + tabbed account panel, reusing existing components. These
pins hold the wiring together (the components have their own behavioural suites);
they also guard that the consent flow (H8) and the safety surfaces survived the
move.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (ROOT / "frontend" / "src" / rel).read_text(encoding="utf-8")


def test_cockpit_mounted_on_live_trading():
    page = _src("pages/LiveTrading.jsx")
    assert "LiveCockpit" in page
    assert "LiveDataProvider" in page and "LiveErrorBoundary" in page


def test_command_bar_has_market_status_and_brokers():
    cb = _src("components/live/cockpit/CommandBar.jsx")
    assert "MARKET OPEN" in cb and "MARKET CLOSED" in cb
    assert "BrokerConnect" in cb
    assert "onConfigure" in cb  # opens the config drawer


def test_broker_connect_wires_reconnect_and_disconnect():
    bc = _src("components/live/cockpit/BrokerConnect.jsx")
    # both brokers, both actions, all via existing endpoints (no MCP)
    for needle in ("upstoxAuthStart", "disconnectUpstox",
                   "flattradeAuthStart", "disconnectFlattrade",
                   "Upstox", "Flattrade", "Login to"):
        assert needle in bc, needle


def test_account_tabs_present_and_wired():
    acct = _src("components/live/cockpit/AccountTabs.jsx")
    for needle in ("Funds & Margin", "Holdings", "Order book", "Trade book",
                   "deriveCash", "OrdersBlotter", "LiveTradeStats"):
        assert needle in acct, needle


def test_config_drawer_hosts_deployment_backstop_controls():
    drawer = _src("components/live/cockpit/ConfigDrawer.jsx")
    for needle in ("LiveDeploymentStrip", "GttBook", "OverallSettingsPanel"):
        assert needle in drawer, needle


def test_cockpit_keeps_always_on_core_and_alerts():
    ck = _src("components/live/LiveCockpit.jsx")
    for needle in ("KillSwitchPanel", "QuickTrade", "PositionsBlotter",
                   "RiskKpis", "DeploymentSummary", "AccountTabs", "AlertRail",
                   "MarketPulse", "MarketAnalysis"):
        assert needle in ck, needle


def test_helpers_extracted_verbatim():
    helpers = _src("components/live/liveHelpers.js")
    for needle in ("deriveDayPnl", "deriveCash", "PositionsBlotter",
                   "OrdersBlotter", "ReconcileChip", "fmtMismatch"):
        assert needle in helpers, needle
