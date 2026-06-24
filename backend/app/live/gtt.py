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

PURE BUILDERS — NO WIRE CALL
----------------------------
Flattrade's exact GTT/OCO REST endpoint is UNCONFIRMED at build time: the public
PiConnect docs describe a direct ``/PlaceGTTOrder`` / ``/PlaceOCOOrder`` family,
but the pip-installed Noren/PiConnect wheel currently in this repo lacks GTT
routes.  So this module builds ONLY the pure ``jdata`` dicts and documents the
endpoint as a TODO.  It deliberately does NOT hardcode a transport.

TODO(endpoint): once confirmed against the live Flattrade/PiConnect account,
wire these jdata dicts to the real GTT endpoints.  Candidates per PiConnect docs:
    POST {host}/PlaceGTTOrder   (single trigger)
    POST {host}/PlaceOCOOrder   (two-leg OCO)
    POST {host}/CancelGTTOrder  (cancel by al_id)
The field names below (ai_t / validity / al_id) mirror the documented Noren GTT
schema so the wire mapping is a thin rename, not a redesign, when confirmed.

All builders are PURE and STATELESS: no time.time(), no I/O, no network.
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

# Single-trigger alert type for a resting limit-on-trigger order.
# (Noren GTT alert-type taxonomy: *_BOS_O = "Buy/Sell Order on trigger".)
_AI_T_SINGLE = "LMT_BOS_O"


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
    """A required string field (exch/tsym): must be a non-empty string."""
    return isinstance(x, str) and x.strip() != ""


def _round_tick_pos(price: Any, tick: float) -> Optional[float]:
    """Tick-round a price, failing CLOSED.

    Returns the nearest-tick-rounded price (a valid tick multiple), or None if
    the input is not a finite positive number OR if rounding collapses it to a
    non-positive value.  Sub-tick prices are ROUNDED (not rejected); garbage
    (NaN/inf/<=0/str/None/bool) is rejected.
    """
    if not _is_finite_pos_number(price):
        return None
    rounded = round_to_tick(float(price), tick, mode="nearest")
    if not (math.isfinite(rounded) and rounded > 0):
        return None
    return rounded


# ---------------------------------------------------------------------------
# 1. Single-trigger GTT — a resting SL (or BOS) on a position
# ---------------------------------------------------------------------------

def build_gtt_intent(
    *,
    exch: str,
    tsym: str,
    qty: int,
    trantype: str,
    trigger_price: float,
    limit_price: float,
    prd: str,
    tick: float = 0.05,
    remarks: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build the jdata for a single-trigger GTT (a resting stop on a position).

    NRML-ONLY: returns None for any ``prd`` != "M" (GTT is the carry-forward
    disaster backstop; MIS must NOT get one).

    Validates fail-closed:
    - prd must be exactly "M"
    - exch / tsym must be non-empty strings
    - qty must be a positive int
    - trantype must be "B" or "S"
    - trigger_price / limit_price must be finite positive numbers; each is then
      tick-rounded to the nearest valid tick multiple (sub-tick is rounded, NOT
      rejected; NaN/inf/<=0/str/None/bool are rejected)

    Returns a well-formed jdata dict (or None on ANY validation failure).
    """
    if not _nrml_ok(prd):
        return None
    if not _str_field_ok(exch) or not _str_field_ok(tsym):
        return None
    if not _is_pos_int(qty):
        return None
    if trantype not in _TRANTYPES:
        return None

    trig = _round_tick_pos(trigger_price, tick)
    lim = _round_tick_pos(limit_price, tick)
    if trig is None or lim is None:
        return None

    return {
        "ai_t": _AI_T_SINGLE,   # single-trigger alert type
        "validity": "GTT",      # rests at broker; blocks no margin
        "exch": exch,
        "tsym": tsym,
        "trantype": trantype,
        "prd": _NRML_PRD,       # PINNED — never anything but NRML
        "qty": str(qty),
        "prctyp": "LMT",
        "trigger_price": trig,
        "limit_price": lim,
        "remarks": remarks,
    }


# ---------------------------------------------------------------------------
# 2. OCO-GTT — two-leg (stop-loss + target); first to trigger cancels the other
# ---------------------------------------------------------------------------

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
    tick: float = 0.05,
    remarks: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build the jdata for a two-leg OCO-GTT (stop-loss leg + target leg).

    Both legs rest at the broker; the FIRST to trigger cancels the other.  This
    is the full PC-DIED net for a long NRML option: it stops the loss AND books
    the target without any local process alive.

    Both legs are SELL legs (protective exit on a long option) — the trantype is
    fixed to "S" on each leg.  (A short-option OCO is not built here: a resting
    BUY-to-cover GTT is fine margin-wise, but this backstop targets the
    option-BUYER long book, which is the only book AlphaForge carries overnight.)

    NRML-ONLY: returns None for any ``prd`` != "M".

    Validates fail-closed (same rules as build_gtt_intent), applied to BOTH legs:
    any garbage price on EITHER leg rejects the WHOLE OCO (returns None).
    """
    if not _nrml_ok(prd):
        return None
    if not _str_field_ok(exch) or not _str_field_ok(tsym):
        return None
    if not _is_pos_int(qty):
        return None

    sl_trig = _round_tick_pos(sl_trigger, tick)
    sl_lim = _round_tick_pos(sl_limit, tick)
    tp_trig = _round_tick_pos(tp_trigger, tick)
    tp_lim = _round_tick_pos(tp_limit, tick)
    if sl_trig is None or sl_lim is None or tp_trig is None or tp_lim is None:
        return None

    legs = [
        {
            "kind": "stoploss",
            "ai_t": _AI_T_SINGLE,
            "trantype": "S",
            "prctyp": "LMT",
            "trigger_price": sl_trig,
            "limit_price": sl_lim,
        },
        {
            "kind": "target",
            "ai_t": _AI_T_SINGLE,
            "trantype": "S",
            "prctyp": "LMT",
            "trigger_price": tp_trig,
            "limit_price": tp_lim,
        },
    ]

    return {
        "validity": "OCO",      # one-cancels-other; rests at broker, no margin
        "exch": exch,
        "tsym": tsym,
        "prd": _NRML_PRD,       # PINNED — never anything but NRML
        "qty": str(qty),
        "legs": legs,
        "remarks": remarks,
    }


# ---------------------------------------------------------------------------
# 3. Cancel a GTT / OCO by its broker alert id
# ---------------------------------------------------------------------------

def cancel_gtt_jdata(al_id: Any) -> Dict[str, Any]:
    """Build the cancel payload for a GTT/OCO identified by its broker alert id.

    The alert id (``al_id``) is the handle the broker returns when a GTT/OCO is
    placed.  Coerces an int id to its string form (Noren jdata fields are
    strings).  Fails CLOSED: an empty / None / blank id raises ValueError rather
    than emit a payload that would cancel "nothing" (or, worse, be misinterpreted
    broker-side).
    """
    if al_id is None:
        raise ValueError("al_id is required to cancel a GTT/OCO")
    s = str(al_id).strip()
    if s == "":
        raise ValueError("al_id must be a non-empty alert id")
    return {"al_id": s}
