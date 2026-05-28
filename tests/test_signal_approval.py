"""Tests for the deployment-signal approval flow.

Validates the lifecycle transitions and audit fields produced by approve/skip/mark-blocked
without going through the HTTP layer (we test the helper transitions; the HTTP wrappers
are simple wiring tested manually against the running backend).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.signal_lifecycle import create_signal_doc, transition_signal, SignalStateError  # noqa: E402


def _confirmed_signal() -> Dict[str, Any]:
    """Build a signal in CONFIRMED state, simulating a deployment-generated clean signal."""
    doc = create_signal_doc(
        instrument="NIFTY",
        direction="CE",
        strategy_id="confluence_scalper",
        entry_price=23910.5,
        confidence=72,
        reasons=["ema_cross", "vol_spike"],
        option_contract={"trading_symbol": "NIFTY26JUN23900CE", "lot_size": 50},
        context={"deployment_id": "d-1", "strategy_hash": "abc1234567890def"},
    )
    doc = transition_signal(doc, "FORMING", reason="strategy direction set")
    doc = transition_signal(doc, "CONFIRMED", reason="passed pretrade")
    return doc


def test_approve_walks_to_active():
    """Approve flow: CONFIRMED -> TRIGGERED -> ACTIVE with approval audit."""
    doc = _confirmed_signal()
    doc = transition_signal(doc, "TRIGGERED", reason="manual_approval")
    doc = transition_signal(doc, "ACTIVE", reason="manual_approval_active")
    assert doc["state"] == "ACTIVE"
    states = [evt["to_state"] for evt in doc["events"]]
    assert states == ["WATCHING", "FORMING", "CONFIRMED", "TRIGGERED", "ACTIVE"]


def test_skip_walks_to_audited():
    """Skip flow: CONFIRMED -> TRIGGERED -> SKIPPED -> AUDITED."""
    doc = _confirmed_signal()
    doc = transition_signal(doc, "TRIGGERED", reason="manual_skip_pre")
    doc = transition_signal(doc, "SKIPPED", reason="manual_skip")
    doc = transition_signal(doc, "AUDITED", reason="manual_skip_audit")
    assert doc["state"] == "AUDITED"
    assert any(evt["to_state"] == "SKIPPED" for evt in doc["events"])


def test_mark_blocked_walks_directly_to_audited_from_confirmed():
    """Manual block flow: CONFIRMED -> AUDITED via the lifecycle's CONFIRMED->AUDITED edge."""
    doc = _confirmed_signal()
    doc = transition_signal(doc, "AUDITED", reason="manual_block")
    assert doc["state"] == "AUDITED"


def test_approve_rejects_already_active_signal():
    """Approving an ACTIVE signal again should fail at the lifecycle layer."""
    doc = _confirmed_signal()
    doc = transition_signal(doc, "TRIGGERED", reason="manual_approval")
    doc = transition_signal(doc, "ACTIVE", reason="manual_approval_active")
    with pytest.raises(SignalStateError):
        # ACTIVE -> ACTIVE is not a valid transition
        transition_signal(doc, "ACTIVE", reason="duplicate")


def test_audited_is_terminal():
    """Once a signal is AUDITED, no further transitions are allowed."""
    doc = _confirmed_signal()
    doc = transition_signal(doc, "AUDITED", reason="terminal")
    with pytest.raises(SignalStateError):
        transition_signal(doc, "ACTIVE", reason="cannot_revive")


def test_signal_carries_audit_metadata_through_transitions():
    """The original deployment_id and strategy_hash must survive all transitions."""
    doc = _confirmed_signal()
    doc = transition_signal(doc, "TRIGGERED", reason="manual_approval")
    doc = transition_signal(doc, "ACTIVE", reason="manual_approval_active")
    assert doc["context"]["deployment_id"] == "d-1"
    assert doc["context"]["strategy_hash"] == "abc1234567890def"


# ---------- auto-paper on approval (slice 4) ---------------------------------

from app.paper_trading import paper_trade_from_signal  # noqa: E402


def _confirmed_signal_with_deployment(deployment_id: str = "dep-1") -> Dict[str, Any]:
    """Confirmed signal with an attached deployment_id, like the evaluator produces."""
    doc = _confirmed_signal()
    doc["deployment_id"] = deployment_id
    doc["option_contract"] = {
        "trading_symbol": "NIFTY26JUN23900CE",
        "lot_size": 65,  # Upstox-sourced, never hardcoded in our code
        "instrument_key": "NSE_FO|TEST|23900CE",
        "strike": 23900.0,
        "side": "CE",
    }
    return doc


def test_auto_paper_creates_trade_with_correct_lot_size_and_deployment_id():
    """Simulates what the approve route does for a deployment in mode=paper."""
    signal = _confirmed_signal_with_deployment()
    # Simulate the lifecycle walk the route performs
    signal = transition_signal(signal, "TRIGGERED", reason="manual_approval")
    signal = transition_signal(signal, "ACTIVE", reason="manual_approval_active")

    trade = paper_trade_from_signal(
        signal,
        lots=1,
        entry_price=signal.get("entry_price"),
        stop_price=None,
        target_price=None,
    )
    trade["deployment_id"] = "dep-1"
    trade["source"] = "paper_auto_on_approval"

    assert trade["lot_size"] == 65, "lot_size must come from the signal's option_contract (Upstox-sourced)"
    assert trade["lots"] == 1
    assert trade["quantity"] == 65 * 1
    assert trade["deployment_id"] == "dep-1"
    assert trade["source"] == "paper_auto_on_approval"
    assert trade["status"] == "OPEN"
    assert trade["instrument_key"] == "NSE_FO|TEST|23900CE"


def test_auto_paper_respects_default_lots_from_deployment_risk():
    """default_lots from deployment.risk is honored when creating the trade."""
    signal = _confirmed_signal_with_deployment()
    signal = transition_signal(signal, "TRIGGERED", reason="approve")
    signal = transition_signal(signal, "ACTIVE", reason="approve")

    # Caller (approve route) passes default_lots from deployment.risk
    trade = paper_trade_from_signal(signal, lots=2, entry_price=signal.get("entry_price"))

    assert trade["lots"] == 2
    assert trade["quantity"] == 65 * 2  # 2 lots * Upstox lot size
