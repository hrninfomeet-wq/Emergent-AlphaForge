"""WIRING pins for the status-indicator fixes (2026-09-29).

Every decision these surfaces make is tested by EXECUTING its pure module through node
(test_market_feed_health, test_greeks_view, test_preopen_readiness_view, test_api_health,
test_overview_open_view, test_signal_display, test_persisted_reason_dates,
test_live_trade_view). The JSX only renders what those modules return; these pins hold the
components to them, so a component cannot quietly go back to painting its own — wrong —
verdict beside a perfectly tested module nobody calls.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FE = ROOT / "frontend" / "src"


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def _imports(text: str, name: str, module: str) -> bool:
    """`name` is imported (by name) from `module`."""
    for m in re.finditer(r'import\s*\{([^}]*)\}\s*from\s*"([^"]+)"', text):
        if m.group(2) == module and name in {n.strip() for n in m.group(1).split(",")}:
            return True
    return False


def test_market_header_takes_its_verdict_from_the_health_module():
    src = _src("components/MarketHeader.jsx")
    assert _imports(src, "deriveFeedIndicator", "@/lib/marketFeedHealth")
    assert "deriveFeedIndicator({" in src
    # the old verdict: "the stream task exists" == live
    assert "liveTickMode" not in src
    assert 'source_mode === "live_ticks" ||' not in src
    # "live ticks" is claimed by the module only; the component keeps the words for docs
    assert "live ticks" in src


def test_layout_footer_is_driven_by_the_health_poll_not_hard_coded():
    src = _src("components/Layout.jsx")
    assert _imports(src, "nextApiHealth", "@/lib/apiHealth")
    assert _imports(src, "describeApiHealth", "@/lib/apiHealth")
    assert "api.health()" in src and "<ApiStatus />" in src
    # the old footer: a constant green dot beside a constant claim
    assert '<span className="w-2 h-2 rounded-full bg-emerald-500"></span>' not in src
    assert "<span>local API live</span>" not in src
    api = _src("lib/api.js")
    assert '"/health"' in api and "getPreopenReadiness" in api
    assert '"/live-broker/preopen-readiness"' in api


def test_greeks_card_takes_its_claims_from_the_view_module():
    src = _src("components/live/GreeksCard.jsx")
    assert _imports(src, "greeksView", "@/lib/greeksView")
    assert "greeksView(greeks, errors?.greeks)" in src
    assert "No open live positions." not in src


def test_cockpit_feeds_the_preopen_verdict_through_its_view_to_the_alert_rail():
    ck = _src("components/live/LiveCockpit.jsx")
    assert _imports(ck, "preopenView", "@/lib/preopenReadinessView")
    assert "preopen={preopenBanner}" in ck
    # broker chips decide "since resolved"; unknown (no status yet) must stay null
    assert "flattradeConnected: status ? brokerConnection.connected : null" in ck
    rail = _src("components/live/cockpit/AlertRail.jsx")
    assert 'data-testid="preopen-readiness-banner"' in rail
    prov = _src("components/live/LiveDataProvider.jsx")
    assert "api.getPreopenReadiness()" in prov
    # a failed verdict read is not a money slice
    i = prov.index("const moneyErrors")
    assert "preopen" not in prov[i:i + 400]


def test_deployment_cards_show_carried_and_other_book_rows_and_pause_dates():
    src = _src("pages/LiveSignals.jsx")
    assert _imports(src, "openNotes", "@/lib/overviewOpenView")
    assert _imports(src, "headerOtherBookNote", "@/lib/overviewOpenView")
    assert _imports(src, "pauseReasonView", "@/lib/deploymentState")
    assert "openNotes(t, d.mode)" in src and "pauseReasonView(d)" in src
    for rel in ("components/live/cockpit/DeploymentSummary.jsx",
                "components/paper/DeploymentControlStrip.jsx"):
        text = _src(rel)
        assert _imports(text, "pauseReasonView", "@/lib/deploymentState"), rel
        assert "pauseReasonView(dep)" in text, rel


def test_the_overview_projection_carries_the_dates_of_the_persisted_reasons():
    src = (ROOT / "backend" / "app" / "routers" / "deployments.py").read_text(encoding="utf-8")
    i = src.index("async def deployments_overview")
    body = src[i:i + 12000]
    assert '"kill_switch_paused_at", "drift_detected_at"' in body


def test_signal_journal_labels_its_state_chip_through_the_display_module():
    src = _src("pages/SignalJournal.jsx")
    assert _imports(src, "signalDisplay", "@/lib/signalDisplay")
    assert "signalDisplay(s)" in src and "{view.label}" in src
    # the old chip printed the raw state
    assert "{s.state}</span></td>" not in src
    route = (ROOT / "backend" / "app" / "routers" / "journals.py").read_text(encoding="utf-8")
    assert '"live_trade_error", "live_trade_id", "paper_trade_claim"' in route


def test_live_trade_stats_tab_labels_open_rows_through_the_view_module():
    src = _src("components/live/LiveTradeStats.jsx")
    assert _imports(src, "tradeStatusChip", "@/lib/liveTradeView")
    assert _imports(src, "openCountView", "@/lib/liveTradeView")
    # the old chip: green whenever the journal says OPEN
    assert 'String(t.status).toUpperCase() === "OPEN" ? "border-emerald-500/40' not in src


def test_safety_latch_banner_always_renders_a_provenance_line():
    src = _src("components/live/SafetyLatchBanner.jsx")
    assert _imports(src, "stopWhen", "@/lib/liveStopState")
    assert "stopWhen(stop.at)" in src
    # the old banner hid the line when the time was missing
    assert "{when && (" not in src
