"""TDD tests for backend/app/live/reconcile.py.

Broker field contract (for L2.3 wiring):
  Broker order row:  {"norenordno": str, "status": str, ...}
  Broker position row: {"tsym": str, "netqty": str, ...}

Internal order doc:  {"norenordno": str | None, "state": str, ...}
Internal position:   {"tsym": str, "qty": int, ...}
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.reconcile import reconcile

# ---------------------------------------------------------------------------
# Working states — internal orders in these states must exist at the broker
# ---------------------------------------------------------------------------
WORKING_STATES = ("OPEN", "TRIGGER_PENDING", "SUBMITTED", "PARTIAL")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _int_order(norenordno, state, tsym="NIFTY25000CE"):
    return {"norenordno": norenordno, "state": state, "tsym": tsym}


def _broker_order(norenordno, status="OPEN"):
    return {"norenordno": norenordno, "status": status}


def _int_pos(tsym, qty):
    return {"tsym": tsym, "qty": qty}


def _broker_pos(tsym, netqty):
    return {"tsym": tsym, "netqty": str(netqty)}


# ---------------------------------------------------------------------------
# Test 1: clean match — ok=True, no mismatches
# ---------------------------------------------------------------------------

def test_clean_match_ok():
    """All internal working orders present at broker; positions match."""
    internal_orders = [_int_order("ORD1", "OPEN")]
    internal_positions = [_int_pos("NIFTY25000CE", 65)]
    broker_orders = [_broker_order("ORD1", "OPEN")]
    broker_positions = [_broker_pos("NIFTY25000CE", 65)]

    result = reconcile(internal_orders, internal_positions, broker_orders, broker_positions)

    assert result["ok"] is True
    assert result["mismatches"] == []


def test_empty_books_ok():
    """Both sides empty — perfect match."""
    result = reconcile([], [], [], [])
    assert result["ok"] is True
    assert result["mismatches"] == []


def test_completed_internal_order_not_flagged():
    """An internal COMPLETE order not at broker is fine — it's terminal."""
    internal_orders = [_int_order("ORD1", "COMPLETE")]
    result = reconcile(internal_orders, [], [], [])
    assert result["ok"] is True


def test_canceled_internal_order_not_flagged():
    """An internal CANCELED/REJECTED order not at broker is fine — terminal."""
    internal_orders = [
        _int_order("ORD1", "CANCELED"),
        _int_order("ORD2", "REJECTED"),
    ]
    result = reconcile(internal_orders, [], [], [])
    assert result["ok"] is True


def test_intent_state_no_norenordno_not_flagged():
    """An INTENT-state order with no norenordno hasn't been submitted yet — ok."""
    internal_orders = [{"state": "INTENT", "tsym": "NIFTY25000CE"}]
    result = reconcile(internal_orders, [], [], [])
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Test 2: internal OPEN order missing at broker → mismatch
# ---------------------------------------------------------------------------

def test_internal_open_order_missing_at_broker():
    """An internal OPEN order whose norenordno the broker doesn't know → mismatch."""
    internal_orders = [_int_order("ORD999", "OPEN")]
    result = reconcile(internal_orders, [], [], [])

    assert result["ok"] is False
    types = [m["type"] for m in result["mismatches"]]
    assert "internal_order_not_at_broker" in types


def test_internal_trigger_pending_missing_at_broker():
    """TRIGGER_PENDING is a working state — must be at broker."""
    internal_orders = [_int_order("ORD888", "TRIGGER_PENDING")]
    result = reconcile(internal_orders, [], [], [])

    assert result["ok"] is False
    assert any(m["type"] == "internal_order_not_at_broker" for m in result["mismatches"])


def test_internal_submitted_missing_at_broker():
    """SUBMITTED is a working state — must be at broker."""
    internal_orders = [_int_order("ORD777", "SUBMITTED")]
    result = reconcile(internal_orders, [], [], [])

    assert result["ok"] is False
    assert any(m["type"] == "internal_order_not_at_broker" for m in result["mismatches"])


def test_internal_partial_missing_at_broker():
    """PARTIAL is a working state — must be at broker."""
    internal_orders = [_int_order("ORD666", "PARTIAL")]
    result = reconcile(internal_orders, [], [], [])

    assert result["ok"] is False
    assert any(m["type"] == "internal_order_not_at_broker" for m in result["mismatches"])


def test_internal_open_broker_shows_canceled():
    """Internal thinks OPEN; broker shows CANCELED → mismatch (we think it's working but it isn't)."""
    internal_orders = [_int_order("ORD1", "OPEN")]
    broker_orders = [_broker_order("ORD1", "CANCELED")]

    result = reconcile(internal_orders, [], broker_orders, [])

    assert result["ok"] is False
    assert any(m["type"] == "internal_order_not_at_broker" for m in result["mismatches"])


def test_internal_open_broker_shows_rejected():
    """Internal thinks OPEN; broker shows REJECTED → mismatch."""
    internal_orders = [_int_order("ORD2", "OPEN")]
    broker_orders = [_broker_order("ORD2", "REJECTED")]

    result = reconcile(internal_orders, [], broker_orders, [])

    assert result["ok"] is False
    assert any(m["type"] == "internal_order_not_at_broker" for m in result["mismatches"])


# ---------------------------------------------------------------------------
# Test 3: broker has a working order we don't know → mismatch
# ---------------------------------------------------------------------------

def test_unknown_broker_order():
    """Broker has a working order with no matching internal record → mismatch."""
    broker_orders = [_broker_order("BROKER_ONLY_ORD", "OPEN")]
    result = reconcile([], [], broker_orders, [])

    assert result["ok"] is False
    types = [m["type"] for m in result["mismatches"]]
    assert "unknown_broker_order" in types


def test_broker_terminal_order_no_internal_not_flagged():
    """Broker has a COMPLETE/CANCELED order with no internal match — terminal, not flagged."""
    broker_orders = [_broker_order("GHOST1", "COMPLETE"), _broker_order("GHOST2", "CANCELED")]
    result = reconcile([], [], broker_orders, [])
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Test 4: position qty divergence → mismatch
# ---------------------------------------------------------------------------

def test_position_qty_mismatch():
    """Internal 65, broker 130 → mismatch with both quantities in detail."""
    internal_positions = [_int_pos("NIFTY25000CE", 65)]
    broker_positions = [_broker_pos("NIFTY25000CE", 130)]

    result = reconcile([], internal_positions, [], broker_positions)

    assert result["ok"] is False
    mismatches = result["mismatches"]
    assert any(m["type"] == "position_qty_mismatch" for m in mismatches)
    qty_mismatch = next(m for m in mismatches if m["type"] == "position_qty_mismatch")
    detail = qty_mismatch["detail"]
    assert "65" in str(detail) or 65 in detail.values() or 65 == detail.get("internal_qty")
    assert "130" in str(detail) or 130 in detail.values() or 130 == detail.get("broker_qty")


def test_position_qty_zero_mismatch():
    """Internal says qty=0 (flat) but broker still shows 65 → mismatch."""
    internal_positions = [_int_pos("NIFTY25000CE", 0)]
    broker_positions = [_broker_pos("NIFTY25000CE", 65)]

    result = reconcile([], internal_positions, [], broker_positions)

    assert result["ok"] is False
    assert any(m["type"] == "position_qty_mismatch" for m in result["mismatches"])


def test_position_qty_match_ok():
    """Matching quantities, even after string→int conversion."""
    internal_positions = [_int_pos("NIFTY25000CE", 65)]
    broker_positions = [_broker_pos("NIFTY25000CE", 65)]

    result = reconcile([], internal_positions, [], broker_positions)

    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Test 5: unknown broker position (net qty != 0, no internal record) → mismatch
# ---------------------------------------------------------------------------

def test_unknown_broker_position():
    """Broker has a non-zero position we have no internal record for → mismatch."""
    broker_positions = [_broker_pos("BANKNIFTY50000CE", 30)]
    result = reconcile([], [], [], broker_positions)

    assert result["ok"] is False
    types = [m["type"] for m in result["mismatches"]]
    assert "unknown_broker_position" in types


def test_unknown_broker_position_zero_netqty_ok():
    """Broker position with netqty=0 is flat — not a mismatch even without internal record."""
    broker_positions = [_broker_pos("BANKNIFTY50000CE", 0)]
    result = reconcile([], [], [], broker_positions)
    assert result["ok"] is True


def test_unknown_broker_position_negative_netqty_flagged():
    """Broker shows a short position (-30) we don't know — flagged."""
    broker_positions = [_broker_pos("SENSEX80000PE", -20)]
    result = reconcile([], [], [], broker_positions)

    assert result["ok"] is False
    assert any(m["type"] == "unknown_broker_position" for m in result["mismatches"])


# ---------------------------------------------------------------------------
# Test 6: multiple simultaneous mismatches all reported
# ---------------------------------------------------------------------------

def test_multiple_mismatches_all_reported():
    """Multiple divergences → all appear in mismatches list."""
    internal_orders = [_int_order("MISSING_ORD", "OPEN")]
    broker_orders = [_broker_order("ROGUE_ORD", "OPEN")]
    internal_positions = [_int_pos("NIFTY25000CE", 65)]
    broker_positions = [_broker_pos("NIFTY25000CE", 130)]

    result = reconcile(internal_orders, internal_positions, broker_orders, broker_positions)

    assert result["ok"] is False
    types = {m["type"] for m in result["mismatches"]}
    assert "internal_order_not_at_broker" in types
    assert "unknown_broker_order" in types
    assert "position_qty_mismatch" in types


# ---------------------------------------------------------------------------
# Test 7: robustness — missing / None keys don't crash
# ---------------------------------------------------------------------------

def test_missing_norenordno_internal_order_not_flagged():
    """Internal order with missing norenordno key and non-working state — no crash."""
    internal_orders = [{"state": "INTENT"}]  # no norenordno key at all
    result = reconcile(internal_orders, [], [], [])
    assert result["ok"] is True


def test_missing_netqty_broker_position_treated_as_zero():
    """Broker position with no netqty key → treated as 0 (flat), not an error."""
    broker_positions = [{"tsym": "NIFTY25000CE"}]  # no netqty key
    result = reconcile([], [], [], broker_positions)
    assert result["ok"] is True  # treated as 0 net qty, no internal record needed


def test_missing_status_broker_order_treated_as_working():
    """Broker order with no status key — fail toward mismatch: treat as unknown working order."""
    broker_orders = [{"norenordno": "GHOST99"}]  # no status
    result = reconcile([], [], broker_orders, [])
    # Fail-toward-mismatch: unknown broker working order
    assert result["ok"] is False
