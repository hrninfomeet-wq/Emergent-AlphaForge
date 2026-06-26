"""Tests for the ``product`` kwarg on ``order_builder.build_intent`` (Task A3).

A broker GTT/OCO only attaches to a CARRY product (NRML, prd="M"), not intraday
MIS (prd="I"). Deployed option-BUY entries must therefore be placed NRML so the
(later-task) resting OCO can protect them.

build_intent gains a keyword-only ``product`` (default "MIS"):
  - product="NRML" → intent.prd == "M"
  - product omitted → intent.prd == "I"  (back-compat default = MIS)
  - product="BOGUS" → (None, verdicts, None) with a failing verdict check=="product"
    (NOT a raised KeyError).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend package is importable without installing
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.order_builder import build_intent


# ---------------------------------------------------------------------------
# Shared fixtures (mirrors tests/test_live_order_builder.py)
# ---------------------------------------------------------------------------

_NIFTY_SCRIP = {
    "tsym": "NIFTY26JUN26C25000",
    "token": "1",
    "ls": "65",
    "symname": "NIFTY",
    "optt": "CE",
    "exd": "26-JUN-2026",
    "dname": "NIFTY 26JUN26 25000 CE",
    "ti": "0.05",
}

_CONTRACT = {
    "underlying": "NIFTY",
    "strike": 25000.0,
    "side": "CE",
    "expiry_date": "2026-06-26",
    "lot_size": 65,
}

_REF_LTP = 200.0
_BAND_PCT = 5.0
_FAT_FINGER_CAP = 10
_CID = "test-cid-product"


def _fake_search(exch: str, query: str):
    return [_NIFTY_SCRIP]


def _build(**kwargs):
    defaults = dict(
        contract=_CONTRACT,
        side="B",
        order_kind="entry",
        lots=2,
        ref_ltp=_REF_LTP,
        band_pct=_BAND_PCT,
        fat_finger_cap=_FAT_FINGER_CAP,
        levels={},
        client_order_id=_CID,
        search_fn=_fake_search,
    )
    defaults.update(kwargs)
    return build_intent(**defaults)


# ---------------------------------------------------------------------------
# (a) product="NRML" → prd == "M"
# ---------------------------------------------------------------------------

def test_product_nrml_sets_prd_m():
    intent, verdicts, lot_size = _build(product="NRML")
    assert intent is not None, f"expected intent, got None. verdicts={verdicts}"
    assert intent.prd == "M", f"expected prd='M' for NRML, got {intent.prd!r}"


# ---------------------------------------------------------------------------
# (b) default (omit product) → prd == "I"  (back-compat MIS)
# ---------------------------------------------------------------------------

def test_product_default_is_mis():
    intent, verdicts, lot_size = _build()
    assert intent is not None, f"expected intent, got None. verdicts={verdicts}"
    assert intent.prd == "I", f"expected default prd='I' (MIS), got {intent.prd!r}"


# ---------------------------------------------------------------------------
# (c) product="BOGUS" → (None, verdicts, None) with failing check=="product"
# ---------------------------------------------------------------------------

def test_product_bogus_fails_closed():
    intent, verdicts, lot_size = _build(product="BOGUS")
    assert intent is None, f"expected None intent for BOGUS product, got {intent!r}"
    assert lot_size is None, f"expected lot_size None on product failure, got {lot_size!r}"
    prod_v = next((v for v in verdicts if v["check"] == "product"), None)
    assert prod_v is not None, f"no 'product' verdict in list: {[v['check'] for v in verdicts]}"
    assert not prod_v["ok"], "expected the 'product' verdict to fail"
