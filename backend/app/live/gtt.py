"""GTT / OCO-GTT intent builders — the NRML disaster backstop.

WHAT THIS IS
------------
A GTT (Good-Till-Triggered) is a resting trigger that lives on the BROKER'S
server, NOT in the live order book — it blocks NO margin and only fires a real
order once its trigger price is hit.  That property makes it the perfect
"PC-DIED" catastrophe net: if the machine running AlphaForge dies mid-session,
a GTT/OCO already parked at the broker will still stop-out (and/or take-profit)
the position without any local process alive.

Because it blocks no margin and never sits in the order book until triggered, it
is IMMUNE to the naked-short margin trap that a resting SL-LMT would create on a
short option.

NRML-ONLY — HARD INVARIANT
--------------------------
This backstop is built ONLY for carry-forward / overnight option positions
(Noren prd == "M", i.e. NRML/CNC).  MIS (prd == "I") is intraday and is
auto-squared by the exchange at close, so a resting GTT for it is both
unnecessary and dangerous (it could fire next session against a flat book).
Every builder below FAILS CLOSED (returns None) for any prd != "M".

SCHEMA SOURCE — Flattrade PiConnect API docs (verified 2026-06-25)
------------------------------------------------------------------
The exact request schema is now CONFIRMED against the official PDF documentation
(``docs/Resources/pi _ API Documentation ...pdf``, chapters 1.13–1.20):

  POST {host}/PlaceGTTOrder    single-leg GTT
      jdata: uid, actid, exch, tsym, ai_t, validity(GTT), d(trigger vs LTP),
             trantype(B/S), prctyp(LMT), prd(C/M/H), ret, qty, prc, dscqty, remarks
  POST {host}/PlaceOCOOrder    two-leg OCO (one-cancels-other)
      jdata: uid, ai_t(LMT_BOS_O), validity(GTT), exch, tsym, remarks,
             oivariable[{d,var_name:x},{d,var_name:y}],
             place_order_params(leg1), place_order_params_leg2(leg2)
  POST {host}/CancelGTTOrder / CancelOCOOrder    {uid, al_id}
  POST {host}/GetPendingGTTOrder                 {uid} -> list (the GTT book)

``ai_t`` DIRECTION — confirm-by-readback (real-money critical)
-------------------------------------------------------------
The docs enumerate the *base* alert types (LTP / ATP / Perc. Change via
GetEnabledAlertTypes) and show ``LMT_BOS_O`` verbatim for OCO, but they do NOT
document the direction suffix (``LTP_A`` "above" vs ``LTP_B`` "below") nor the
x/y -> leg pairing.  Therefore:

  * OCO ``ai_t`` defaults to the DOCUMENTED literal ``LMT_BOS_O`` (not a guess).
  * Single-GTT ``ai_t`` is a REQUIRED caller argument — no guessed default. Use
    the LTP_ABOVE / LTP_BELOW constants and CONFIRM the mapping by reading one
    real GTT back via GetPendingGTTOrder before depending on it.

All builders are PURE and STATELESS: no time.time(), no I/O, no network. The
client (FlattradeClient) injects identity (uid/actid) at transmit time.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from app.live.order_builder import round_to_tick

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The ONLY Noren product code GTT is built for: M == NRML / CNC (carry-forward).
_NRML_PRD = "M"

# Valid trade directions (Noren trantype).
_TRANTYPES = ("B", "S")

# Retention types accepted by the GTT/OCO order params (docs: DAY / EOS / IOC).
_RET_TYPES = ("DAY", "EOS", "IOC")

# DOCUMENTED OCO alert type (PiConnect docs ch.1.18 PlaceOCOOrder example,
# verbatim: "ai_t": "LMT_BOS_O").  One bracket pair; first leg to trigger
# cancels the other.
AI_T_OCO = "LMT_BOS_O"

# Single-GTT / alert base type LTP with the (inferred, confirm-by-readback)
# direction suffix.  Exposed so callers choose direction EXPLICITLY rather than
# the builder guessing it.
LTP_ABOVE = "LTP_A"   # fire when LTP rises to/above d  (e.g. a take-profit)
LTP_BELOW = "LTP_B"   # fire when LTP falls to/below d  (e.g. a stop-loss)


# ---------------------------------------------------------------------------
# Internal fail-closed validators
# ---------------------------------------------------------------------------

def _is_pos_int(x: Any) -> bool:
    """True iff x is a plain positive int (bool excluded — True/False are footguns)."""
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def _is_finite_pos_number(x: Any) -> bool:
    """True iff x is a real finite strictly-positive number (int/float, not bool)."""
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and math.isfinite(x)
        and x > 0
    )


def _nrml_ok(prd: Any) -> bool:
    """NRML-only guard — fail CLOSED on anything but the exact NRML code 'M'."""
    return prd == _NRML_PRD


def _str_field_ok(x: Any) -> bool:
    """A required string field (exch/tsym/ai_t): must be a non-empty string."""
    return isinstance(x, str) and x.strip() != ""


def _fmt_price(price: Any, tick: float) -> Optional[str]:
    """Tick-round a price and format it as a Noren jdata string, failing CLOSED.

    Returns the nearest-tick-rounded price formatted to 2 decimals (Noren jdata
    fields are strings, e.g. "40.05"), or None if the input is not a finite
    positive number OR if rounding collapses it to a non-positive value.
    Sub-tick prices are ROUNDED (not rejected); garbage (NaN/inf/<=0/str/None/
    bool) is rejected.
    """
    if not _is_finite_pos_number(price):
        return None
    rounded = round_to_tick(float(price), tick, mode="nearest")
    if not (math.isfinite(rounded) and rounded > 0):
        return None
    return f"{rounded:.2f}"


# ---------------------------------------------------------------------------
# 1. Single-trigger GTT — a resting SL (or TP) on a position
# ---------------------------------------------------------------------------

def build_gtt_intent(
    *,
    exch: str,
    tsym: str,
    qty: int,
    trantype: str,
    ai_t: str,
    d_trigger: float,
    prc_limit: float,
    prd: str,
    ret: str = "DAY",
    tick: float = 0.05,
    dscqty: int = 0,
    remarks: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build the jdata for a single-trigger GTT (a resting stop/target on a position).

    Maps to the documented PlaceGTTOrder schema. ``d_trigger`` -> ``d`` (the
    price compared with LTP) and ``prc_limit`` -> ``prc`` (the resulting order's
    limit price).  Identity (uid/actid) is NOT included here — the client injects
    it at transmit time.

    NRML-ONLY: returns None for any ``prd`` != "M".

    ``ai_t`` is REQUIRED (no guessed default): pass LTP_BELOW for a protective
    stop on a long option, LTP_ABOVE for a target.  Confirm the direction by
    reading one GTT back via GetPendingGTTOrder before depending on it.

    Validates fail-closed:
    - prd must be exactly "M"
    - exch / tsym / ai_t must be non-empty strings
    - qty must be a positive int; dscqty a non-negative int
    - trantype must be "B" or "S"; ret one of DAY/EOS/IOC
    - d_trigger / prc_limit must be finite positive numbers; each is then
      tick-rounded to the nearest valid tick multiple (sub-tick is rounded, NOT
      rejected; NaN/inf/<=0/str/None/bool are rejected)

    Returns a well-formed jdata dict (or None on ANY validation failure).
    """
    if not _nrml_ok(prd):
        return None
    if not _str_field_ok(exch) or not _str_field_ok(tsym) or not _str_field_ok(ai_t):
        return None
    if not _is_pos_int(qty):
        return None
    if not (isinstance(dscqty, int) and not isinstance(dscqty, bool) and dscqty >= 0):
        return None
    if trantype not in _TRANTYPES:
        return None
    if ret not in _RET_TYPES:
        return None

    d = _fmt_price(d_trigger, tick)
    prc = _fmt_price(prc_limit, tick)
    if d is None or prc is None:
        return None

    return {
        "ai_t": ai_t,           # alert type (direction) — caller-chosen
        "validity": "GTT",      # rests at broker; blocks no margin
        "exch": exch,
        "tsym": tsym,
        "d": d,                 # price compared with LTP (the trigger)
        "trantype": trantype,
        "prctyp": "LMT",
        "prd": _NRML_PRD,       # PINNED — never anything but NRML
        "ret": ret,
        "qty": str(qty),
        "prc": prc,             # resulting order's limit price
        "dscqty": str(dscqty),
        "remarks": remarks or "",
    }


# ---------------------------------------------------------------------------
# 2. OCO-GTT — two-leg (stop-loss + target); first to trigger cancels the other
# ---------------------------------------------------------------------------

def _oco_leg(
    *, exch: str, tsym: str, trantype: str, prc: str, qty: int, ret: str,
    ordersource: str, remarks: Optional[str],
) -> Dict[str, Any]:
    """Build one place_order_params leg for an OCO (identity injected by client)."""
    leg: Dict[str, Any] = {
        "tsym": tsym,
        "exch": exch,
        "trantype": trantype,
        "prctyp": "LMT",
        "prd": _NRML_PRD,
        "ret": ret,
        "ordersource": ordersource,
        "qty": str(qty),
        "prc": prc,
    }
    if remarks:
        leg["remarks"] = remarks
    return leg


def build_oco_intent(
    *,
    exch: str,
    tsym: str,
    qty: int,
    prd: str,
    sl_trigger: float,
    sl_limit: float,
    tp_trigger: float,
    tp_limit: float,
    trantype: str = "S",
    ai_t: str = AI_T_OCO,
    ret: str = "DAY",
    ordersource: str = "API",
    tick: float = 0.05,
    remarks: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build the jdata for a two-leg OCO-GTT (stop-loss leg + target leg).

    Maps to the documented PlaceOCOOrder schema:
        oivariable = [{d: sl_trigger, var_name: "x"}, {d: tp_trigger, var_name: "y"}]
        place_order_params       = leg1 (the SL leg, paired with x)
        place_order_params_leg2  = leg2 (the TP leg, paired with y)

    Both legs rest at the broker; the FIRST to trigger cancels the other.  This
    is the full PC-DIED net for a long NRML option: it stops the loss AND books
    the target without any local process alive.  Both legs are SELL legs by
    default (protective exit on a long option).  Identity (uid/actid) is injected
    by the client at transmit time.

    NRML-ONLY: returns None for any ``prd`` != "M".

    Validates fail-closed (same price rules as build_gtt_intent), applied to ALL
    FOUR prices: any garbage on ANY leg rejects the WHOLE OCO (returns None).
    """
    if not _nrml_ok(prd):
        return None
    if not _str_field_ok(exch) or not _str_field_ok(tsym) or not _str_field_ok(ai_t):
        return None
    if not _is_pos_int(qty):
        return None
    if trantype not in _TRANTYPES:
        return None
    if ret not in _RET_TYPES:
        return None

    sl_trig = _fmt_price(sl_trigger, tick)
    sl_lim = _fmt_price(sl_limit, tick)
    tp_trig = _fmt_price(tp_trigger, tick)
    tp_lim = _fmt_price(tp_limit, tick)
    if sl_trig is None or sl_lim is None or tp_trig is None or tp_lim is None:
        return None

    return {
        "ai_t": ai_t,           # DOCUMENTED OCO bracket type (LMT_BOS_O)
        "validity": "GTT",      # OCO rests at broker as a GTT pair; no margin
        "exch": exch,
        "tsym": tsym,
        "remarks": remarks or "",
        "oivariable": [
            {"d": sl_trig, "var_name": "x"},   # x -> leg1 (stop-loss trigger)
            {"d": tp_trig, "var_name": "y"},   # y -> leg2 (target trigger)
        ],
        "place_order_params": _oco_leg(
            exch=exch, tsym=tsym, trantype=trantype, prc=sl_lim, qty=qty,
            ret=ret, ordersource=ordersource, remarks=remarks,
        ),
        "place_order_params_leg2": _oco_leg(
            exch=exch, tsym=tsym, trantype=trantype, prc=tp_lim, qty=qty,
            ret=ret, ordersource=ordersource, remarks=remarks,
        ),
    }


# ---------------------------------------------------------------------------
# 3. Cancel a GTT / OCO by its broker alert id
# ---------------------------------------------------------------------------

def cancel_gtt_jdata(al_id: Any) -> Dict[str, Any]:
    """Build the cancel payload for a GTT/OCO identified by its broker alert id.

    The alert id (``al_id``) is the handle the broker returns when a GTT/OCO is
    placed (CancelGTTOrder / CancelOCOOrder both take ``{uid, al_id}``; the uid is
    injected by the client).  Coerces an int id to its string form (Noren jdata
    fields are strings).  Fails CLOSED: an empty / None / blank id raises
    ValueError rather than emit a payload that would cancel "nothing".
    """
    if al_id is None:
        raise ValueError("al_id is required to cancel a GTT/OCO")
    s = str(al_id).strip()
    if s == "":
        raise ValueError("al_id must be a non-empty alert id")
    return {"al_id": s}
