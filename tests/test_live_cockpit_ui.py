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


# --------------------------------------------------------------------------- #
# Phase 2 — market analysis + holdings wiring
# --------------------------------------------------------------------------- #

def test_api_exposes_analysis_and_holdings_helpers():
    api = _src("lib/api.js")
    assert "marketAnalysis" in api and '"/market/analysis"' in api
    assert "liveBrokerHoldings" in api and '"/live-broker/holdings"' in api


def test_provider_polls_analysis_and_holdings():
    prov = _src("components/live/LiveDataProvider.jsx")
    for needle in ("marketAnalysis", "holdings", "ANALYSIS_MS", "HOLDINGS_MS"):
        assert needle in prov, needle
    # a failed analysis read must NOT trip the money-degraded banner
    i = prov.index("const moneyErrors")
    block = prov[i:i + 400]
    assert "marketAnalysis" not in block


def test_cockpit_feeds_analysis_and_merges_greeks():
    ck = _src("components/live/LiveCockpit.jsx")
    assert "MarketPulse analysis={analysis}" in ck
    assert "MarketAnalysis analysis={analysis}" in ck
    # net greeks come from the already-polled slice, not a duplicate broker read
    assert "net_delta_rupees_per_point" in ck and "net_theta_rupees_per_day" in ck
    assert "holdings={holdings}" in ck


def test_analysis_panels_render_real_fields():
    pulse = _src("components/live/cockpit/MarketPulse.jsx")
    for needle in ("regime_bucket", "confidence", "position_in_range", "intraday", "monthly"):
        assert needle in pulse, needle
    ma = _src("components/live/cockpit/MarketAnalysis.jsx")
    for needle in ("pcr_oi", "max_pain", "iv_rank_30d", "iv_rank_source", "atm_straddle", "chain"):
        assert needle in ma, needle
    # honest degradation must be surfaced, not hidden
    assert "option_oi_unavailable" in ma and "vix_proxy" in ma


# --------------------------------------------------------------------------- #
# Real-money safety fixes (audit items 2-4, 2026-07-25)
# --------------------------------------------------------------------------- #

def test_gtt_cancel_requires_confirmation():
    """Item 2: cancelling a resting OCO/GTT removes the only PC-down protection,
    so it must be a two-step action that names the symbol + consequence."""
    src = _src("components/live/GttBook.jsx")
    assert "gtt-cancel-arm" in src and "gtt-cancel-confirm" in src
    assert "confirming" in src
    assert "gtt-cancel-warning" in src
    # the warning must state the software-guard-only consequence
    assert "only while this app is running" in src
    # arming must NOT call the API directly
    i = src.index('data-testid="gtt-cancel-arm"')
    assert "handleCancel" not in src[i - 400:i]


def test_guard_flags_rehydrated_default_stops():
    """Item 3: positions re-attached after a restart carry DEEP-DEFAULT stops —
    the backend emits source/rehydrated_count precisely so the UI can say so."""
    panel = _src("components/live/GuardPanel.jsx")
    assert '"rehydrated"' in panel
    assert "rehydrated_count" in panel
    assert "guard-rehydrated-badge" in panel and "guard-rehydrated-count" in panel
    assert "DEFAULT STOP" in panel
    # and it is surfaced at the cockpit level, not only inside the panel
    rail = _src("components/live/cockpit/AlertRail.jsx")
    assert "rehydrated-positions-banner" in rail
    ck = _src("components/live/LiveCockpit.jsx")
    assert "rehydratedPositions" in ck


def test_overall_settings_fails_closed_on_unreadable_config():
    """Item 4: a failed (non-404) GET must not present defaults as the saved
    config, and Save must be blocked so it cannot overwrite the real controls."""
    src = _src("components/live/OverallSettingsPanel.jsx")
    assert "loadFailed" in src
    assert "overall-load-failed" in src
    # a genuine 404 still means "nothing saved yet" and stays editable
    assert "status === 404" in src or "=== 404" in src
    # Save is gated both in the button and inside the handler
    assert "saveBusy || loadFailed" in src
    assert "if (loadFailed)" in src
