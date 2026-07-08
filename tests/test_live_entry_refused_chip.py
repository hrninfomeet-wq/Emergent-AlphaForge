"""String-pins for the 'entry refused' surfacing (backlog item #2 half B).

auto_live writes signals.live_trade_error / signals.live_intended when an armed
deployment refuses/blocks a live entry (e.g. stale premium, throttle, broker
reject). These were WRITE-ONLY — nothing read them, so an armed deployment that
silently never placed had no on-screen reason. This pins the end-to-end wiring:

  backend  _live_status_payload → last_entry {error, intended, at, signal_id}
  frontend LiveDeploymentStrip  → red "entry refused: <reason>" chip

Host-only (reads source files); no import of motor-backed modules.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY_SRC = (ROOT / "backend/app/routers/deployments.py").read_text(encoding="utf-8")
AUTO_LIVE_SRC = (ROOT / "backend/app/auto_live.py").read_text(encoding="utf-8")
STRIP_SRC = (ROOT / "frontend/src/components/live/LiveDeploymentStrip.jsx").read_text(encoding="utf-8")


# --- auto_live still WRITES the fields the surfacing reads --------------------

def test_auto_live_writes_live_trade_error_and_intended():
    assert "live_trade_error" in AUTO_LIVE_SRC
    assert "live_intended" in AUTO_LIVE_SRC


# --- backend live-status payload READS them into last_entry ------------------

def test_live_status_payload_surfaces_last_entry():
    assert "last_entry" in DEPLOY_SRC
    # it queries the signals collection for this deployment's latest live outcome
    assert "db.signals.find_one" in DEPLOY_SRC
    assert "live_trade_error" in DEPLOY_SRC
    assert "live_intended" in DEPLOY_SRC


# --- frontend strip RENDERS the entry-refused chip ---------------------------

def test_strip_renders_entry_refused_chip():
    assert "last_entry" in STRIP_SRC
    assert "entry refused" in STRIP_SRC
    assert 'data-testid="live-entry-refused"' in STRIP_SRC
    # a human label mapper (not the raw snake_case reason)
    assert "entryErrorLabel" in STRIP_SRC
    # the specific stale-premium reason gets a friendly label
    assert "live_entry_premium_unavailable_or_stale" in STRIP_SRC
    assert "no fresh premium" in STRIP_SRC
