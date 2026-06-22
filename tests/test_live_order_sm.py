"""TDD tests for app.live.order_sm — L2.1.

Covers:
  - Happy-path lifecycle transitions
  - TRIGGER_PENDING (stop order) path
  - Partial fills accumulating correctly
  - Idempotence: duplicate event does not double-count
  - Out-of-order: lower-rank event after higher-rank must not regress state
  - Double-fill replay: cumulative max, not additive
  - Reject classification: transient vs terminal, unknown/missing rejreason
  - Unknown status: no-op, no crash
  - Non-numeric fillshares / avgprc: no crash, values preserved
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest

from app.live.order_sm import (
    STATE_RANK,
    TERMINAL,
    apply_om,
    classify_reject,
    map_status,
)


# ---------------------------------------------------------------------------
# Fixtures — base order docs and om event builders
# ---------------------------------------------------------------------------

def make_doc(state: str = "SUBMITTED", qty: int = 65, fillshares: int = 0,
             avgprc: float | None = None, norenordno: str | None = "ORD001") -> dict:
    """Minimal order_doc for testing."""
    doc: dict = {
        "client_order_id": "cid-test-001",
        "norenordno": norenordno,
        "state": state,
        "qty": qty,
        "fillshares": fillshares,
    }
    if avgprc is not None:
        doc["avgprc"] = avgprc
    return doc


def make_om(status: str, fillshares: int = 0, avgprc: float | None = None,
            qty: int = 65, rejreason: str | None = None,
            norenordno: str = "ORD001") -> dict:
    """Minimal Noren om event for testing."""
    om: dict = {
        "norenordno": norenordno,
        "status": status,
        "fillshares": str(fillshares),
        "qty": str(qty),
    }
    if avgprc is not None:
        om["avgprc"] = str(avgprc)
    if rejreason is not None:
        om["rejreason"] = rejreason
    return om


# ===========================================================================
# 1. Metadata / constants
# ===========================================================================

def test_state_rank_values():
    assert STATE_RANK["INTENT"] < STATE_RANK["SUBMITTED"] < STATE_RANK["ACKED"]
    assert STATE_RANK["ACKED"] < STATE_RANK["OPEN"]
    assert STATE_RANK["OPEN"] == STATE_RANK["TRIGGER_PENDING"]
    assert STATE_RANK["OPEN"] < STATE_RANK["PARTIAL"]
    assert STATE_RANK["PARTIAL"] < STATE_RANK["COMPLETE"]
    assert STATE_RANK["COMPLETE"] == STATE_RANK["REJECTED"] == STATE_RANK["CANCELED"]


def test_terminal_set():
    assert TERMINAL == {"COMPLETE", "REJECTED", "CANCELED"}


# ===========================================================================
# 2. map_status
# ===========================================================================

class TestMapStatus:
    def test_pending_to_submitted(self):
        assert map_status({"status": "PENDING"}) == "SUBMITTED"

    def test_new_to_acked(self):
        assert map_status({"status": "NEW"}) == "ACKED"

    def test_open_to_open(self):
        assert map_status({"status": "OPEN", "fillshares": "0", "qty": "65"}) == "OPEN"

    def test_open_with_partial_fill_to_partial(self):
        # OPEN event but fillshares > 0 and < qty → PARTIAL
        assert map_status({"status": "OPEN", "fillshares": "25", "qty": "65"}) == "PARTIAL"

    def test_open_fully_filled_stays_open_mapping(self):
        # If fillshares == qty on an OPEN event, keep OPEN (COMPLETE arrives separately)
        assert map_status({"status": "OPEN", "fillshares": "65", "qty": "65"}) == "OPEN"

    def test_trigger_pending(self):
        assert map_status({"status": "TRIGGER_PENDING"}) == "TRIGGER_PENDING"

    def test_complete(self):
        assert map_status({"status": "COMPLETE"}) == "COMPLETE"

    def test_rejected(self):
        assert map_status({"status": "REJECTED"}) == "REJECTED"

    def test_canceled(self):
        assert map_status({"status": "CANCELED"}) == "CANCELED"

    def test_partially_filled(self):
        assert map_status({"status": "PARTIALLY_FILLED"}) == "PARTIAL"

    def test_partial_direct(self):
        assert map_status({"status": "PARTIAL"}) == "PARTIAL"

    def test_unknown_status_returns_current(self):
        assert map_status({"status": "WHATEVER"}, current_state="OPEN") == "OPEN"

    def test_missing_status_returns_current(self):
        assert map_status({}, current_state="ACKED") == "ACKED"

    def test_none_status_returns_current(self):
        assert map_status({"status": None}, current_state="SUBMITTED") == "SUBMITTED"

    def test_case_insensitive(self):
        # Noren might send lowercase
        assert map_status({"status": "complete"}) == "COMPLETE"
        assert map_status({"status": "pending"}) == "SUBMITTED"


# ===========================================================================
# 3. classify_reject
# ===========================================================================

class TestClassifyReject:
    # Transient cases
    def test_session_expired(self):
        assert classify_reject("Session Expired") == "transient"

    def test_token_invalid(self):
        assert classify_reject("Invalid token, please re-login") == "transient"

    def test_timeout(self):
        assert classify_reject("Order submission timeout") == "transient"

    def test_throttle(self):
        assert classify_reject("Throttle limit exceeded") == "transient"

    def test_too_many(self):
        assert classify_reject("Too many requests") == "transient"

    def test_rate_limit(self):
        assert classify_reject("Rate limit hit, retry after 1s") == "transient"

    def test_connection_error(self):
        assert classify_reject("Connection reset by peer") == "transient"

    def test_try_again(self):
        assert classify_reject("Please try again later") == "transient"

    # Terminal cases
    def test_rms_margin(self):
        assert classify_reject("RMS:Margin shortfall") == "terminal"

    def test_insufficient_funds(self):
        assert classify_reject("Insufficient funds") == "terminal"

    def test_invalid_order_type(self):
        assert classify_reject("Order type not allowed") == "terminal"

    def test_lot_size(self):
        assert classify_reject("Invalid lot size") == "terminal"

    def test_symbol_not_found(self):
        assert classify_reject("Symbol not found in exchange") == "terminal"

    def test_price_band(self):
        assert classify_reject("Price outside trading band") == "terminal"

    # Edge cases — fail-safe
    def test_none_rejreason_is_terminal(self):
        assert classify_reject(None) == "terminal"

    def test_empty_string_is_terminal(self):
        assert classify_reject("") == "terminal"

    def test_unknown_reason_is_terminal(self):
        assert classify_reject("XYZZY broker error 42") == "terminal"


# ===========================================================================
# 4. apply_om — happy-path lifecycle
# ===========================================================================

class TestApplyOmHappyPath:
    """SUBMITTED → ACKED → OPEN → COMPLETE (regular limit order, no partials)."""

    def test_submitted_to_acked(self):
        doc = make_doc(state="SUBMITTED", fillshares=0)
        om = make_om("NEW")
        result = apply_om(doc, om)
        assert result["state"] == "ACKED"
        assert result["fillshares"] == 0

    def test_acked_to_open(self):
        doc = make_doc(state="ACKED", fillshares=0)
        om = make_om("OPEN", fillshares=0)
        result = apply_om(doc, om)
        assert result["state"] == "OPEN"
        assert result["fillshares"] == 0

    def test_open_to_complete(self):
        doc = make_doc(state="OPEN", fillshares=0)
        om = make_om("COMPLETE", fillshares=65, avgprc=150.0)
        result = apply_om(doc, om)
        assert result["state"] == "COMPLETE"
        assert result["fillshares"] == 65
        assert result["avgprc"] == 150.0

    def test_full_happy_path_chain(self):
        doc = make_doc(state="SUBMITTED", fillshares=0)
        doc = apply_om(doc, make_om("NEW"))
        assert doc["state"] == "ACKED"
        doc = apply_om(doc, make_om("OPEN", fillshares=0))
        assert doc["state"] == "OPEN"
        doc = apply_om(doc, make_om("COMPLETE", fillshares=65, avgprc=150.0))
        assert doc["state"] == "COMPLETE"
        assert doc["fillshares"] == 65
        assert doc["avgprc"] == 150.0

    def test_input_doc_not_mutated(self):
        doc = make_doc(state="OPEN", fillshares=0)
        original = copy.deepcopy(doc)
        apply_om(doc, make_om("COMPLETE", fillshares=65, avgprc=150.0))
        assert doc == original  # input must NOT be mutated


# ===========================================================================
# 5. apply_om — TRIGGER_PENDING (stop order) path
# ===========================================================================

class TestApplyOmStopOrder:
    """SUBMITTED → ACKED → TRIGGER_PENDING → OPEN → COMPLETE."""

    def test_acked_to_trigger_pending(self):
        doc = make_doc(state="ACKED")
        result = apply_om(doc, make_om("TRIGGER_PENDING"))
        assert result["state"] == "TRIGGER_PENDING"

    def test_trigger_pending_to_open(self):
        doc = make_doc(state="TRIGGER_PENDING")
        result = apply_om(doc, make_om("OPEN", fillshares=0))
        # TRIGGER_PENDING and OPEN share the same rank (3), so state stays
        # at whichever came first — in this case TRIGGER_PENDING is already set
        # and OPEN has equal rank, so state should be unchanged (no regress)
        # Actually by design equal-rank keeps current: TRIGGER_PENDING stays.
        # But logically after triggering we expect OPEN — let's check the contract:
        # "If new_state rank == current rank, keep as-is" per the spec.
        # So TRIGGER_PENDING stays. The next meaningful event is PARTIAL or COMPLETE.
        assert result["state"] == "TRIGGER_PENDING"

    def test_trigger_pending_to_complete(self):
        doc = make_doc(state="TRIGGER_PENDING", fillshares=0)
        result = apply_om(doc, make_om("COMPLETE", fillshares=65, avgprc=200.0))
        assert result["state"] == "COMPLETE"
        assert result["fillshares"] == 65
        assert result["avgprc"] == 200.0

    def test_full_stop_order_chain(self):
        doc = make_doc(state="SUBMITTED")
        doc = apply_om(doc, make_om("NEW"))
        assert doc["state"] == "ACKED"
        doc = apply_om(doc, make_om("TRIGGER_PENDING"))
        assert doc["state"] == "TRIGGER_PENDING"
        doc = apply_om(doc, make_om("COMPLETE", fillshares=65, avgprc=200.0))
        assert doc["state"] == "COMPLETE"
        assert doc["fillshares"] == 65


# ===========================================================================
# 6. apply_om — partial fills accumulating
# ===========================================================================

class TestApplyOmPartialFills:
    """OPEN → PARTIAL (25) → PARTIAL (50) → COMPLETE (65)."""

    def test_first_partial_25(self):
        doc = make_doc(state="OPEN", fillshares=0)
        om = make_om("OPEN", fillshares=25, avgprc=148.0, qty=65)
        result = apply_om(doc, om)
        assert result["state"] == "PARTIAL"
        assert result["fillshares"] == 25
        assert result["avgprc"] == 148.0

    def test_second_partial_50(self):
        doc = make_doc(state="PARTIAL", fillshares=25, avgprc=148.0)
        om = make_om("OPEN", fillshares=50, avgprc=149.5, qty=65)
        result = apply_om(doc, om)
        assert result["state"] == "PARTIAL"
        assert result["fillshares"] == 50
        assert result["avgprc"] == 149.5

    def test_complete_from_partial(self):
        doc = make_doc(state="PARTIAL", fillshares=50, avgprc=149.5)
        om = make_om("COMPLETE", fillshares=65, avgprc=150.0)
        result = apply_om(doc, om)
        assert result["state"] == "COMPLETE"
        assert result["fillshares"] == 65
        assert result["avgprc"] == 150.0

    def test_fills_monotonic_through_chain(self):
        doc = make_doc(state="OPEN", fillshares=0)
        doc = apply_om(doc, make_om("OPEN", fillshares=25, avgprc=148.0, qty=65))
        assert doc["fillshares"] == 25
        doc = apply_om(doc, make_om("OPEN", fillshares=50, avgprc=149.5, qty=65))
        assert doc["fillshares"] == 50
        doc = apply_om(doc, make_om("COMPLETE", fillshares=65, avgprc=150.0))
        assert doc["fillshares"] == 65
        assert doc["state"] == "COMPLETE"


# ===========================================================================
# 7. ADVERSARIAL: duplicate event idempotence
# ===========================================================================

class TestDuplicateEventIdempotence:
    """Applying the same event twice must be a no-op (idempotent)."""

    def test_duplicate_complete_does_not_double_fills(self):
        """KEY TEST: two COMPLETE events both reporting fillshares=65 → stays 65."""
        doc = make_doc(state="OPEN", fillshares=0)
        complete_om = make_om("COMPLETE", fillshares=65, avgprc=150.0)

        doc = apply_om(doc, complete_om)
        assert doc["state"] == "COMPLETE"
        assert doc["fillshares"] == 65

        # Apply the EXACT same om again — fillshares must NOT double to 130
        doc2 = apply_om(doc, complete_om)
        assert doc2["state"] == "COMPLETE"
        assert doc2["fillshares"] == 65  # NOT 130
        assert doc2["avgprc"] == 150.0

    def test_duplicate_partial_is_idempotent(self):
        doc = make_doc(state="PARTIAL", fillshares=25, avgprc=148.0)
        om = make_om("OPEN", fillshares=25, avgprc=148.0, qty=65)
        doc2 = apply_om(doc, om)
        assert doc2["fillshares"] == 25  # no duplication
        assert doc2["state"] == "PARTIAL"

    def test_duplicate_ack_after_open_is_noop(self):
        doc = make_doc(state="OPEN", fillshares=0)
        ack_om = make_om("NEW")  # stale ACK replayed
        result = apply_om(doc, ack_om)
        assert result["state"] == "OPEN"  # no regression to ACKED


# ===========================================================================
# 8. ADVERSARIAL: out-of-order events
# ===========================================================================

class TestOutOfOrderEvents:
    """A late/replayed lower-rank event must not regress state."""

    def test_stale_open_after_complete_no_regress(self):
        """KEY TEST: COMPLETE arrives, then stale OPEN → state stays COMPLETE."""
        doc = make_doc(state="OPEN", fillshares=0)
        doc = apply_om(doc, make_om("COMPLETE", fillshares=65, avgprc=150.0))
        assert doc["state"] == "COMPLETE"
        assert doc["fillshares"] == 65

        # Stale OPEN arrives out-of-order
        stale_open = make_om("OPEN", fillshares=0)
        doc2 = apply_om(doc, stale_open)
        assert doc2["state"] == "COMPLETE"       # no regression
        assert doc2["fillshares"] == 65          # no downgrade to 0

    def test_stale_acked_after_partial_no_regress(self):
        doc = make_doc(state="PARTIAL", fillshares=25, avgprc=148.0)
        stale_ack = make_om("NEW")
        result = apply_om(doc, stale_ack)
        assert result["state"] == "PARTIAL"       # no regression to ACKED
        assert result["fillshares"] == 25

    def test_stale_submitted_after_complete_no_regress(self):
        doc = make_doc(state="COMPLETE", fillshares=65, avgprc=150.0)
        stale_pending = make_om("PENDING")
        result = apply_om(doc, stale_pending)
        assert result["state"] == "COMPLETE"
        assert result["fillshares"] == 65

    def test_terminal_complete_ignores_all_lower_rank(self):
        for stale_status in ("PENDING", "NEW", "OPEN", "PARTIALLY_FILLED"):
            doc = make_doc(state="COMPLETE", fillshares=65, avgprc=150.0)
            result = apply_om(doc, make_om(stale_status, fillshares=0))
            assert result["state"] == "COMPLETE", (
                f"Expected COMPLETE to survive stale {stale_status}"
            )


# ===========================================================================
# 9. ADVERSARIAL: double-fill replay (cumulative max)
# ===========================================================================

class TestDoubleFillReplay:
    """Two events both reporting cumulative fillshares=50 → fillshares=50, NOT 100."""

    def test_two_events_same_cumulative_fill(self):
        doc = make_doc(state="OPEN", fillshares=0)
        om_a = make_om("OPEN", fillshares=50, avgprc=148.0, qty=65)
        om_b = make_om("OPEN", fillshares=50, avgprc=148.0, qty=65)  # duplicate

        doc = apply_om(doc, om_a)
        assert doc["fillshares"] == 50

        doc = apply_om(doc, om_b)
        assert doc["fillshares"] == 50  # NOT 100

    def test_fill_never_decreases(self):
        """fillshares is monotonically non-decreasing (old cumulative < new is fine)."""
        doc = make_doc(state="PARTIAL", fillshares=50, avgprc=149.0)
        # Broker sends a stale/duplicate event with lower cumulative — ignore
        om_stale = make_om("PARTIALLY_FILLED", fillshares=25, avgprc=148.0)
        result = apply_om(doc, om_stale)
        assert result["fillshares"] == 50   # NOT reduced to 25
        assert result["avgprc"] == 149.0    # NOT overwritten with stale avgprc

    def test_fill_increases_correctly(self):
        doc = make_doc(state="PARTIAL", fillshares=25, avgprc=148.0)
        om = make_om("PARTIALLY_FILLED", fillshares=50, avgprc=149.0)
        result = apply_om(doc, om)
        assert result["fillshares"] == 50
        assert result["avgprc"] == 149.0


# ===========================================================================
# 10. REJECTED orders
# ===========================================================================

class TestRejectedOrders:
    def test_rejected_session_expired_transient(self):
        doc = make_doc(state="SUBMITTED")
        om = make_om("REJECTED", rejreason="Session Expired")
        result = apply_om(doc, om)
        assert result["state"] == "REJECTED"
        assert result["rejreason"] == "Session Expired"
        assert result["reject_class"] == "transient"

    def test_rejected_rms_margin_terminal(self):
        doc = make_doc(state="SUBMITTED")
        om = make_om("REJECTED", rejreason="RMS:Margin shortfall")
        result = apply_om(doc, om)
        assert result["state"] == "REJECTED"
        assert result["rejreason"] == "RMS:Margin shortfall"
        assert result["reject_class"] == "terminal"

    def test_rejected_missing_rejreason_defaults_terminal(self):
        """No rejreason → reject_class must be "terminal" (fail-safe)."""
        doc = make_doc(state="SUBMITTED")
        om = make_om("REJECTED")  # no rejreason kwarg → not in dict
        result = apply_om(doc, om)
        assert result["state"] == "REJECTED"
        assert result["reject_class"] == "terminal"

    def test_rejected_none_rejreason_defaults_terminal(self):
        doc = make_doc(state="SUBMITTED")
        om = make_om("REJECTED", rejreason=None)
        result = apply_om(doc, om)
        assert result["state"] == "REJECTED"
        assert result["reject_class"] == "terminal"

    def test_rejected_is_terminal_cannot_leave(self):
        doc = make_doc(state="REJECTED")
        doc["rejreason"] = "Session Expired"
        doc["reject_class"] = "transient"
        # Even if we retry and get a COMPLETE (shouldn't happen, but belt+suspenders)
        result = apply_om(doc, make_om("COMPLETE", fillshares=65, avgprc=150.0))
        assert result["state"] == "REJECTED"

    def test_canceled_is_terminal(self):
        doc = make_doc(state="OPEN")
        result = apply_om(doc, make_om("CANCELED"))
        assert result["state"] == "CANCELED"
        # Then try to re-open
        result2 = apply_om(result, make_om("OPEN"))
        assert result2["state"] == "CANCELED"


# ===========================================================================
# 11. Edge cases — robustness / no-crash
# ===========================================================================

class TestEdgeCases:
    def test_unknown_status_no_crash_state_unchanged(self):
        doc = make_doc(state="OPEN")
        om = {"status": "BOGUS_STATUS_XYZ", "fillshares": "0", "qty": "65"}
        result = apply_om(doc, om)
        assert result["state"] == "OPEN"

    def test_missing_status_no_crash(self):
        doc = make_doc(state="ACKED")
        result = apply_om(doc, {})
        assert result["state"] == "ACKED"

    def test_non_numeric_fillshares_no_crash(self):
        doc = make_doc(state="OPEN", fillshares=0)
        om = {"status": "OPEN", "fillshares": "NOT_A_NUMBER", "qty": "65"}
        result = apply_om(doc, om)
        assert result["state"] == "OPEN"
        assert result["fillshares"] == 0  # unchanged

    def test_non_numeric_avgprc_no_crash(self):
        doc = make_doc(state="OPEN", fillshares=0)
        om = {"status": "COMPLETE", "fillshares": "65", "avgprc": "N/A", "qty": "65"}
        result = apply_om(doc, om)
        assert result["state"] == "COMPLETE"
        assert result["fillshares"] == 65

    def test_none_fillshares_treated_as_zero(self):
        doc = make_doc(state="OPEN", fillshares=0)
        om = {"status": "OPEN", "fillshares": None, "qty": "65"}
        result = apply_om(doc, om)
        assert result["fillshares"] == 0

    def test_empty_string_fillshares_treated_as_zero(self):
        doc = make_doc(state="OPEN", fillshares=0)
        om = {"status": "OPEN", "fillshares": "", "qty": "65"}
        result = apply_om(doc, om)
        assert result["fillshares"] == 0

    def test_avgprc_not_updated_when_fills_unchanged(self):
        """avgprc must NOT be overwritten if fillshares didn't increase."""
        doc = make_doc(state="PARTIAL", fillshares=50, avgprc=149.0)
        # Same cumulative fills, different avgprc (stale/duplicate)
        om = {"status": "PARTIALLY_FILLED", "fillshares": "50", "avgprc": "148.0", "qty": "65"}
        result = apply_om(doc, om)
        assert result["avgprc"] == 149.0  # NOT overwritten with 148.0

    def test_norenordno_propagated_when_missing_in_doc(self):
        doc = make_doc(state="SUBMITTED", norenordno=None)
        om = make_om("NEW", norenordno="ORD999")
        result = apply_om(doc, om)
        assert result["norenordno"] == "ORD999"

    def test_norenordno_not_overwritten_when_already_set(self):
        doc = make_doc(state="SUBMITTED", norenordno="ORD001")
        om = make_om("NEW", norenordno="ORD999")
        result = apply_om(doc, om)
        assert result["norenordno"] == "ORD001"  # preserved

    def test_extra_doc_keys_preserved(self):
        doc = make_doc(state="OPEN")
        doc["deployment_id"] = "dep-123"
        doc["ts_intent"] = "2026-06-22T10:00:00Z"
        result = apply_om(doc, make_om("COMPLETE", fillshares=65, avgprc=150.0))
        assert result["deployment_id"] == "dep-123"
        assert result["ts_intent"] == "2026-06-22T10:00:00Z"

    def test_apply_om_returns_new_dict(self):
        doc = make_doc(state="OPEN")
        result = apply_om(doc, make_om("COMPLETE", fillshares=65, avgprc=150.0))
        assert result is not doc  # must be a different object

    def test_complete_already_terminal_accepts_higher_cumulative_fills(self):
        """COMPLETE + higher fillshares from replay: fills update but state stays."""
        doc = make_doc(state="COMPLETE", fillshares=60, avgprc=150.0)
        om = make_om("COMPLETE", fillshares=65, avgprc=151.0)
        result = apply_om(doc, om)
        assert result["state"] == "COMPLETE"
        assert result["fillshares"] == 65     # updated (was 60, now 65)
        assert result["avgprc"] == 151.0      # updated because fills increased


# ===========================================================================
# 12. STATE_RANK completeness — every ORDER_STATES member must have a rank
# ===========================================================================

def test_all_order_states_have_rank():
    from app.live.broker_protocol import ORDER_STATES
    for s in ORDER_STATES:
        assert s in STATE_RANK, f"Missing STATE_RANK entry for {s!r}"
