"""Tests for app.live.gtt — GTT / OCO-GTT intent builders (NRML disaster backstop).

TDD suite. The OCO-GTT is a PC-DIED catastrophe net for NRML/CNC option positions
ONLY (prd=="M"); it must NEVER be built for MIS (prd=="I"). It blocks no margin
(doesn't sit in the live order book until triggered) so it's immune to the
naked-short trap.

These builders are PURE jdata producers — Flattrade's exact GTT/OCO REST endpoint
is UNCONFIRMED, so there is NO wire call here.  See gtt.py module docstring TODO.

No conftest — backend must be put on sys.path explicitly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.gtt import (  # noqa: E402
    build_gtt_intent,
    build_oco_intent,
    cancel_gtt_jdata,
)


# ---------------------------------------------------------------------------
# Shared kwargs for a canonical NRML NIFTY CE long position (sell-stop backstop)
# ---------------------------------------------------------------------------
def _gtt_kwargs(**over):
    base = dict(
        exch="NFO",
        tsym="NIFTY26JUN26C25000",
        qty=65,
        trantype="S",          # protective sell on a long option
        trigger_price=40.07,    # off-tick on purpose → must round to 40.05
        limit_price=39.93,      # off-tick on purpose → must round to 39.95
        prd="M",                # NRML — the ONLY product GTT is built for
        remarks="cid-abc-123",
    )
    base.update(over)
    return base


def _oco_kwargs(**over):
    base = dict(
        exch="NFO",
        tsym="NIFTY26JUN26C25000",
        qty=65,
        prd="M",
        sl_trigger=40.07,
        sl_limit=39.93,
        tp_trigger=120.02,
        tp_limit=119.97,
        remarks="cid-oco-1",
    )
    base.update(over)
    return base


# ===========================================================================
# build_gtt_intent — single-trigger SL on a position
# ===========================================================================
def test_gtt_builds_for_nrml():
    g = build_gtt_intent(**_gtt_kwargs())
    assert g is not None
    assert isinstance(g, dict)


def test_gtt_tick_rounds_prices():
    g = build_gtt_intent(**_gtt_kwargs())
    # trigger 40.07 → 40.05, limit 39.93 → 39.95 (nearest tick of 0.05)
    assert float(g["trigger_price"]) == 40.05
    assert float(g["limit_price"]) == 39.95


def test_gtt_rounded_prices_are_tick_multiples():
    g = build_gtt_intent(**_gtt_kwargs(trigger_price=40.123, limit_price=39.871))
    for key in ("trigger_price", "limit_price"):
        # multiple-of-0.05 check (work in integer paise to avoid float noise)
        assert round(float(g[key]) / 0.05) == pytest.approx(float(g[key]) / 0.05, abs=1e-9)
        assert round(float(g[key]) * 100) % 5 == 0


def test_gtt_carries_core_fields():
    g = build_gtt_intent(**_gtt_kwargs())
    assert g["exch"] == "NFO"
    assert g["tsym"] == "NIFTY26JUN26C25000"
    assert g["trantype"] == "S"
    assert g["prd"] == "M"
    assert int(g["qty"]) == 65
    assert g["remarks"] == "cid-abc-123"


def test_gtt_has_single_alert_type_and_gtt_validity():
    g = build_gtt_intent(**_gtt_kwargs())
    # single-trigger alert type + GTT validity marker present
    assert g.get("ai_t") == "LMT_BOS_O"
    assert g.get("validity") == "GTT"


# --- NRML-only guard: MIS is REJECTED ---------------------------------------
@pytest.mark.parametrize("bad_prd", ["I", "i", "MIS", "", None, "C"])
def test_gtt_rejects_non_nrml(bad_prd):
    assert build_gtt_intent(**_gtt_kwargs(prd=bad_prd)) is None


# --- price validation: sub-tick handled (round), garbage REJECTED -----------
@pytest.mark.parametrize(
    "trigger,limit",
    [
        (float("nan"), 39.95),
        (float("inf"), 39.95),
        (40.05, float("-inf")),
        (-40.05, 39.95),       # negative price
        (0, 39.95),            # zero price
        (40.05, 0.0),
        ("40.05", 39.95),      # string
        (None, 39.95),
        (40.05, None),
    ],
)
def test_gtt_rejects_garbage_prices(trigger, limit):
    assert build_gtt_intent(**_gtt_kwargs(trigger_price=trigger, limit_price=limit)) is None


# --- qty validation ----------------------------------------------------------
@pytest.mark.parametrize("bad_qty", [0, -65, -1, 1.5, "65", None, True])
def test_gtt_rejects_bad_qty(bad_qty):
    assert build_gtt_intent(**_gtt_kwargs(qty=bad_qty)) is None


# --- trantype validation -----------------------------------------------------
@pytest.mark.parametrize("bad_side", ["X", "", None, "buy", "sell"])
def test_gtt_rejects_bad_trantype(bad_side):
    assert build_gtt_intent(**_gtt_kwargs(trantype=bad_side)) is None


def test_gtt_accepts_buy_and_sell():
    assert build_gtt_intent(**_gtt_kwargs(trantype="B")) is not None
    assert build_gtt_intent(**_gtt_kwargs(trantype="S")) is not None


# --- missing required field --------------------------------------------------
@pytest.mark.parametrize("bad_field", [{"exch": ""}, {"exch": None}, {"tsym": ""}, {"tsym": None}])
def test_gtt_rejects_missing_symbol_fields(bad_field):
    assert build_gtt_intent(**_gtt_kwargs(**bad_field)) is None


# ===========================================================================
# build_oco_intent — two-leg OCO (SL leg + TP leg; first cancels the other)
# ===========================================================================
def test_oco_builds_for_nrml():
    o = build_oco_intent(**_oco_kwargs())
    assert o is not None
    assert isinstance(o, dict)


def test_oco_has_both_legs():
    o = build_oco_intent(**_oco_kwargs())
    legs = o["legs"]
    assert isinstance(legs, list)
    assert len(legs) == 2
    kinds = {leg["kind"] for leg in legs}
    assert kinds == {"stoploss", "target"}


def test_oco_legs_are_tick_valid():
    o = build_oco_intent(**_oco_kwargs())
    for leg in o["legs"]:
        for key in ("trigger_price", "limit_price"):
            assert round(float(leg[key]) * 100) % 5 == 0


def test_oco_tick_rounds_each_leg():
    o = build_oco_intent(**_oco_kwargs())
    by_kind = {leg["kind"]: leg for leg in o["legs"]}
    # SL: 40.07 → 40.05, 39.93 → 39.95
    assert float(by_kind["stoploss"]["trigger_price"]) == 40.05
    assert float(by_kind["stoploss"]["limit_price"]) == 39.95
    # TP: 120.02 → 120.00, 119.97 → 119.95
    assert float(by_kind["target"]["trigger_price"]) == 120.00
    assert float(by_kind["target"]["limit_price"]) == 119.95


def test_oco_is_oco_validity():
    o = build_oco_intent(**_oco_kwargs())
    assert o.get("validity") == "OCO"
    assert o["prd"] == "M"
    assert int(o["qty"]) == 65


def test_oco_carries_core_fields():
    o = build_oco_intent(**_oco_kwargs())
    assert o["exch"] == "NFO"
    assert o["tsym"] == "NIFTY26JUN26C25000"
    assert o["remarks"] == "cid-oco-1"


# --- NRML-only guard ---------------------------------------------------------
@pytest.mark.parametrize("bad_prd", ["I", "MIS", "", None])
def test_oco_rejects_non_nrml(bad_prd):
    assert build_oco_intent(**_oco_kwargs(prd=bad_prd)) is None


# --- price validation: any garbage on any leg rejects whole OCO --------------
@pytest.mark.parametrize(
    "field,value",
    [
        ("sl_trigger", float("nan")),
        ("sl_limit", float("inf")),
        ("tp_trigger", -1.0),
        ("tp_limit", 0.0),
        ("sl_trigger", "40"),
        ("tp_limit", None),
    ],
)
def test_oco_rejects_garbage_leg_prices(field, value):
    assert build_oco_intent(**_oco_kwargs(**{field: value})) is None


# --- qty validation ----------------------------------------------------------
@pytest.mark.parametrize("bad_qty", [0, -65, 1.5, "65", None, True])
def test_oco_rejects_bad_qty(bad_qty):
    assert build_oco_intent(**_oco_kwargs(qty=bad_qty)) is None


# ===========================================================================
# cancel_gtt_jdata — cancel an existing GTT/OCO by alert id
# ===========================================================================
def test_cancel_payload_shape():
    p = cancel_gtt_jdata("25061900000123")
    assert isinstance(p, dict)
    assert p["al_id"] == "25061900000123"


def test_cancel_coerces_int_alert_id():
    p = cancel_gtt_jdata(25061900000123)
    assert p["al_id"] == "25061900000123"


@pytest.mark.parametrize("bad_id", ["", None])
def test_cancel_rejects_empty_alert_id(bad_id):
    with pytest.raises(ValueError):
        cancel_gtt_jdata(bad_id)
