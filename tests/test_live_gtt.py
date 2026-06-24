"""Tests for app.live.gtt — GTT / OCO-GTT intent builders (NRML disaster backstop).

TDD suite, schema CONFIRMED against the official Flattrade PiConnect API docs
(PDF chapters 1.13–1.20, verified 2026-06-25):

  PlaceGTTOrder single-leg jdata: ai_t, validity(GTT), exch, tsym, d(trigger vs
      LTP), trantype, prctyp(LMT), prd, ret, qty, prc, dscqty, remarks
  PlaceOCOOrder jdata: ai_t(LMT_BOS_O), validity(GTT), exch, tsym, remarks,
      oivariable[{d,var_name:x},{d,var_name:y}], place_order_params(leg1),
      place_order_params_leg2(leg2)
  CancelGTTOrder/CancelOCOOrder: {uid, al_id}  (uid injected by client)

The OCO-GTT is a PC-DIED catastrophe net for NRML/CNC option positions ONLY
(prd=="M"); it must NEVER be built for MIS (prd=="I"). It blocks no margin
(doesn't sit in the live order book until triggered) so it's immune to the
naked-short trap.

These builders are PURE jdata producers — identity (uid/actid) is injected by
the client at transmit time, so it is intentionally absent here.

No conftest — backend must be put on sys.path explicitly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.gtt import (  # noqa: E402
    AI_T_OCO,
    LTP_ABOVE,
    LTP_BELOW,
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
        ai_t=LTP_BELOW,        # stop: fire when LTP falls to/below d
        d_trigger=40.07,       # off-tick on purpose → must round to 40.05
        prc_limit=39.93,       # off-tick on purpose → must round to 39.95
        prd="M",               # NRML — the ONLY product GTT is built for
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
    # d 40.07 → 40.05 (trigger), prc 39.93 → 39.95 (order limit) at tick 0.05
    assert float(g["d"]) == 40.05
    assert float(g["prc"]) == 39.95


def test_gtt_is_flat_documented_form():
    # REQUEST is the FLAT documented form (vision-verified catalog #16) — NOT the
    # wrapped oivariable/place_order_params shape that GetPendingGTTOrder RETURNS.
    g = build_gtt_intent(**_gtt_kwargs())
    assert "oivariable" not in g
    assert "place_order_params" not in g
    assert g["trantype"] == "S"
    assert g["prctyp"] == "LMT"
    assert g["prd"] == "M"
    assert int(g["qty"]) == 65
    assert g["dscqty"] == "0"


def test_gtt_prices_are_strings():
    # Noren jdata fields are strings.
    g = build_gtt_intent(**_gtt_kwargs())
    assert isinstance(g["d"], str)
    assert isinstance(g["prc"], str)
    assert isinstance(g["qty"], str)


def test_gtt_rounded_prices_are_tick_multiples():
    g = build_gtt_intent(**_gtt_kwargs(d_trigger=40.123, prc_limit=39.871))
    for val in (g["d"], g["prc"]):
        assert round(float(val) * 100) % 5 == 0


def test_gtt_carries_core_fields():
    g = build_gtt_intent(**_gtt_kwargs())
    assert g["exch"] == "NFO"
    assert g["tsym"] == "NIFTY26JUN26C25000"
    assert g["validity"] == "GTT"
    assert g["ret"] == "DAY"
    assert g["remarks"] == "cid-abc-123"
    assert int(g["qty"]) == 65


def test_gtt_ai_t_below_is_confirmed_value():
    # Locked to the broker's recorded value (live readback 2026-06-25).
    assert LTP_BELOW == "LTP_B_O"
    g = build_gtt_intent(**_gtt_kwargs(ai_t=LTP_BELOW))
    assert g["ai_t"] == "LTP_B_O"
    assert g["validity"] == "GTT"


def test_gtt_has_caller_alert_type():
    g = build_gtt_intent(**_gtt_kwargs(ai_t=LTP_ABOVE))
    assert g["ai_t"] == "LTP_A_O"


def test_gtt_does_not_embed_identity():
    # Identity (uid/actid) is injected by the client, not the pure builder.
    g = build_gtt_intent(**_gtt_kwargs())
    assert "uid" not in g
    assert "actid" not in g


# --- NRML-only guard: MIS is REJECTED ---------------------------------------
@pytest.mark.parametrize("bad_prd", ["I", "i", "MIS", "", None, "C"])
def test_gtt_rejects_non_nrml(bad_prd):
    assert build_gtt_intent(**_gtt_kwargs(prd=bad_prd)) is None


# --- ai_t is required (no guessed direction) --------------------------------
@pytest.mark.parametrize("bad_ai_t", ["", "   ", None, 123])
def test_gtt_rejects_missing_ai_t(bad_ai_t):
    assert build_gtt_intent(**_gtt_kwargs(ai_t=bad_ai_t)) is None


# --- ret validation ----------------------------------------------------------
@pytest.mark.parametrize("bad_ret", ["GTC", "", None, "day"])
def test_gtt_rejects_bad_ret(bad_ret):
    assert build_gtt_intent(**_gtt_kwargs(ret=bad_ret)) is None


# --- price validation: sub-tick handled (round), garbage REJECTED -----------
@pytest.mark.parametrize(
    "d_trigger,prc_limit",
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
def test_gtt_rejects_garbage_prices(d_trigger, prc_limit):
    assert build_gtt_intent(**_gtt_kwargs(d_trigger=d_trigger, prc_limit=prc_limit)) is None


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


def test_oco_has_documented_alert_type_and_validity():
    o = build_oco_intent(**_oco_kwargs())
    assert o["ai_t"] == AI_T_OCO == "LMT_BOS_O"
    assert o["validity"] == "GTT"


def test_oco_oivariable_pairs_x_sl_and_y_tp():
    o = build_oco_intent(**_oco_kwargs())
    oiv = {row["var_name"]: row["d"] for row in o["oivariable"]}
    assert set(oiv) == {"x", "y"}
    # x = SL trigger (40.07 → 40.05), y = TP trigger (120.02 → 120.00)
    assert float(oiv["x"]) == 40.05
    assert float(oiv["y"]) == 120.00


def test_oco_has_both_leg_param_blocks():
    o = build_oco_intent(**_oco_kwargs())
    leg1 = o["place_order_params"]
    leg2 = o["place_order_params_leg2"]
    # leg1 = SL leg → sells at sl_limit (39.93 → 39.95)
    assert float(leg1["prc"]) == 39.95
    # leg2 = TP leg → sells at tp_limit (119.97 → 119.95)
    assert float(leg2["prc"]) == 119.95
    # both protective SELL legs, NRML, limit
    for leg in (leg1, leg2):
        assert leg["trantype"] == "S"
        assert leg["prd"] == "M"
        assert leg["prctyp"] == "LMT"
        assert int(leg["qty"]) == 65


def test_oco_leg_prices_are_tick_valid_strings():
    o = build_oco_intent(**_oco_kwargs())
    prices = [
        o["place_order_params"]["prc"],
        o["place_order_params_leg2"]["prc"],
        o["oivariable"][0]["d"],
        o["oivariable"][1]["d"],
    ]
    for p in prices:
        assert isinstance(p, str)
        assert round(float(p) * 100) % 5 == 0


def test_oco_carries_core_fields():
    o = build_oco_intent(**_oco_kwargs())
    assert o["exch"] == "NFO"
    assert o["tsym"] == "NIFTY26JUN26C25000"
    assert o["remarks"] == "cid-oco-1"


def test_oco_does_not_embed_identity():
    o = build_oco_intent(**_oco_kwargs())
    assert "uid" not in o
    assert "actid" not in o
    assert "uid" not in o["place_order_params"]
    assert "actid" not in o["place_order_params_leg2"]


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
