"""String-pins for the Live-page failure-visibility work (backlog item #4).

A failing poll keeps the last-good value on screen, so without explicit signals a
FROZEN number reads as live, a 422 white-screens the page (losing the kill switch),
and the reconcile chip prints "[object Object]". These pins lock the wiring:
  - usePoll stamps lastSuccess; the provider exposes health/lastSuccess.
  - a LiveErrorBoundary keeps the kill switch reachable on a render crash.
  - LiveDashboard shows a degraded banner + as-of and formats reconcile mismatches.
  - KillSwitchPanel ALWAYS renders (never self-unmounts) with an UNKNOWN state.
  - error catches route through getApiErrorMessage (never render raw 422 arrays).
  - Stop-ALL states its true blast radius and renders the response summary.

Host-only (reads frontend source).
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


USEPOLL = _read("frontend/src/hooks/usePoll.js")
PROVIDER = _read("frontend/src/components/live/LiveDataProvider.jsx")
BOUNDARY = _read("frontend/src/components/live/LiveErrorBoundary.jsx")
LIVE_PAGE = _read("frontend/src/pages/LiveTrading.jsx")
DASH = _read("frontend/src/components/live/LiveDashboard.jsx")
KILL = _read("frontend/src/components/live/KillSwitchPanel.jsx")
TICKET = _read("frontend/src/components/live/LiveOrderTicket.jsx")
STRIP = _read("frontend/src/components/live/LiveDeploymentStrip.jsx")


def test_usepoll_stamps_and_returns_last_success():
    assert "lastSuccess" in USEPOLL
    assert "setLastSuccess(Date.now())" in USEPOLL
    assert "return { data, error, loading, lastSuccess, refetch }" in USEPOLL


def test_provider_exposes_health_and_last_success():
    assert "health" in PROVIDER
    assert "degraded" in PROVIDER
    assert "errorSlices" in PROVIDER
    assert "lastSuccess:" in PROVIDER  # exposed in the value object


def test_error_boundary_keeps_kill_switch():
    assert "getDerivedStateFromError" in BOUNDARY
    assert "KillSwitchPanel" in BOUNDARY
    assert "Reload Live page" in BOUNDARY
    # and the route wraps the dashboard in it (inside the provider so context works)
    assert "LiveErrorBoundary" in LIVE_PAGE
    assert "LiveDataProvider" in LIVE_PAGE


def test_dashboard_degraded_banner_and_as_of():
    assert 'data-testid="live-degraded-banner"' in DASH
    assert 'data-testid="live-hero-asof"' in DASH
    assert "health" in DASH
    assert "lastSuccess" in DASH
    assert "STALE" in DASH


def test_dashboard_reconcile_chip_formats_mismatches():
    # must NOT join objects directly (that renders [object Object])
    assert "fmtMismatch" in DASH
    assert "m?.detail?.tsym" in DASH


def test_kill_switch_always_renders_and_handles_unknown():
    # the self-unmount `if (!visible && !result) return null;` must be gone
    assert "return null" not in KILL
    assert "brokerUnknown" in KILL
    assert "broker state UNKNOWN" in KILL
    assert "getApiErrorMessage" in KILL
    assert "already_running" in KILL


def test_order_ticket_uses_safe_error_formatter_and_clamps_band():
    assert "getApiErrorMessage" in TICKET
    # the raw-detail pattern that rendered objects into JSX must be gone from the
    # error setters (getApiErrorMessage replaces it)
    assert "e?.response?.data?.detail ?? e?.message ?? \"Preview failed\"" not in TICKET
    assert "e?.response?.data?.detail ?? e?.message ?? \"Place failed\"" not in TICKET
    # band_pct is clamped (NaN/blank can no longer post null and 422)
    assert "Number.isFinite(b) && b >= 0 ? b : 5" in TICKET


def test_stop_all_states_true_blast_radius_and_summary():
    assert "EVERY open PAPER trade" in STRIP
    assert "pauses EVERY active deployment" in STRIP
    # renders the backend response summary, not a generic toast
    assert "squared_off_count" in STRIP
    assert "paused_deployment_ids" in STRIP
    assert "disarmed_live_deployment_ids" in STRIP
