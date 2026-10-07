"""order_sm normalises Noren's status spelling drift (audit 2026-10-07 #6).

`map_status` matched only "REJECTED" / "CANCELED". The decoded OrderBook sample
spells a reject "REJECT" (docs/Resources/flattrade-pi-api/endpoints/10-order-book.md),
and "CANCELLED" (double-L) is the other drift `kill_switch.TERMINAL` already
absorbs. Either fell through to "unknown status → keep the current state", so a
REJECTED order stayed OPEN/SUBMITTED in the state machine (live engine and the
scalping engine both run `apply_om`) — never terminal, never released.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.order_sm import apply_om, map_status  # noqa: E402


@pytest.mark.parametrize("raw", ["REJECT", "Reject", " reject ", "REJECTED", "Rejected"])
def test_every_reject_spelling_maps_to_rejected(raw):
    assert map_status({"status": raw}, current_state="SUBMITTED") == "REJECTED"


@pytest.mark.parametrize("raw", ["CANCELLED", "Cancelled", "CANCELED", "canceled"])
def test_every_cancel_spelling_maps_to_canceled(raw):
    assert map_status({"status": raw}, current_state="OPEN") == "CANCELED"


def test_a_reject_spelled_REJECT_terminates_the_order_doc():
    doc = {"state": "OPEN", "norenordno": "N1", "qty": 65, "fillshares": 0}
    out = apply_om(doc, {"norenordno": "N1", "status": "REJECT",
                         "rejreason": "Insufficient funds"})
    assert out["state"] == "REJECTED"


def test_a_cancel_spelled_CANCELLED_terminates_the_order_doc():
    doc = {"state": "OPEN", "norenordno": "N1", "qty": 65, "fillshares": 0}
    out = apply_om(doc, {"norenordno": "N1", "status": "CANCELLED"})
    assert out["state"] == "CANCELED"


def test_a_genuinely_unknown_status_still_preserves_the_state():
    assert map_status({"status": "SOMETHING_NEW"}, current_state="OPEN") == "OPEN"
