"""Manual mark/close safety (review Item 5).

The auto path is race-safe and premium-forced; the MANUAL /paper/trades mark &
close routes were not — they accepted any operator price (a fat-fingered spot
level booked) and used an unconditional replace_one (a late write could clobber
an auto-close). These tests pin the pure premium-sanity helper and assert the
routes enforce the OPEN-status guard + sanity check (with an override escape).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.paper_trading import premium_sanity_error  # noqa: E402
from tests.contract_corpus import backend_api_text  # noqa: E402


def _trade(entry=150.0, last=None):
    t = {"entry_price": entry}
    if last is not None:
        t["last_price"] = last
    return t


# ---- premium sanity (pure) -------------------------------------------------


def test_normal_and_sharp_real_moves_pass():
    assert premium_sanity_error(_trade(entry=150.0, last=180.0), 200.0) is None
    # A sharp but real move from a tiny premium must NOT be over-guarded.
    assert premium_sanity_error(_trade(entry=0.5, last=0.5), 8.0) is None
    # SENSEX-scale premium doubling is fine.
    assert premium_sanity_error(_trade(entry=800.0, last=800.0), 1600.0) is None


def test_fat_fingered_spot_level_is_flagged():
    assert premium_sanity_error(_trade(entry=150.0, last=150.0), 23950.0) is not None
    assert premium_sanity_error(_trade(entry=0.5, last=0.5), 23950.0) is not None
    assert premium_sanity_error(_trade(entry=800.0, last=800.0), 81000.0) is not None


def test_non_positive_price_is_rejected():
    assert premium_sanity_error(_trade(), 0) is not None
    assert premium_sanity_error(_trade(), -5) is not None


def test_reference_is_last_price_then_entry():
    # No last mark -> reference is entry; a spot-scale value is flagged.
    assert premium_sanity_error({"entry_price": 100.0}, 9000.0) is not None
    # A recent mark raises the reference, so the same value is within bounds.
    assert premium_sanity_error({"entry_price": 100.0, "last_price": 1000.0}, 9000.0) is None


# ---- route guards (contract text) ------------------------------------------


def test_manual_routes_enforce_open_status_sanity_and_override():
    src = backend_api_text()
    # OPEN-status guard + conditional replace on both manual routes.
    assert "_require_open" in src
    assert '"id": trade_id, "status": "OPEN"' in src
    assert "closed concurrently" in src
    # Premium sanity wired in, with an explicit operator override escape hatch.
    assert "premium_sanity_error" in src
    assert "override_sanity" in src
    assert "implausible_premium" in src
