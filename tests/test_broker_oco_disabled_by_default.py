"""The broker OCO is OFF unless the operator explicitly enables it.

2026-09-03, live, BFO SENSEX2690376800PE. Two entries filled at 144.55 and
146.72. One second after EACH fill the OCO's stop leg hit the order book and was
rejected by the exchange:

    SELL ORDER PRICE [49.70000000] IS BEYOND LPP LIMIT: [87.65000000]
    SELL ORDER PRICE [50.35000000] IS BEYOND LPP LIMIT: [89.15000000]

The price is not the defect — the TIMING is. A resting GTT/OCO never reaches the
order book; it lives in GetPendingGTTOrder until its trigger fires. These legs
reached the order book one second after the fill, carrying the broker's own
trigger note:

    LMT_BOS_O: oco:26090300039944: Ltp 144.85 is above 50.75

The stop trigger (50.75, i.e. 35% of entry premium: a 50% guard stop widened by
MIN_GAP_PP) fired on "LTP is ABOVE it" — a condition already true at placement.
``gtt.py`` documents the x/y -> leg pairing as UNCONFIRMED and asks for
confirm-by-readback; this is the readback, and it says the pairing is wrong.

Two consequences, both bad:
  * the PC-down catastrophe net never rested, while the journal recorded an
    al_id claiming it did (``oco_verify`` had already found a MARGIN route to
    the same false claim — this is a second, independent one);
  * a SELL limit at 49.70 into a 144.85 market is MARKETABLE. The LPP reject is
    the only reason the position was not flattened one second after entry.

So the OCO is disabled until the pairing is confirmed against a live readback.
The software exit guard is — and on 2026-09-03 actually was — the real
protection: it squared both lots at 09:39. Nothing here weakens that; the arm's
registration is MANDATORY in every case below.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest  # noqa: E402

from app.live_deploy_context import arm_for, _broker_oco_enabled  # noqa: E402
from app.live.live_position_guard import get_registry  # noqa: E402


class _Intent:
    exch, tsym, qty, prd = "BFO", "SENSEX2690376800PE", 40, "M"


class _Client:
    def __init__(self):
        self.margin_calls, self.oco_calls = [], []

    async def order_margin(self, **kw):
        self.margin_calls.append(kw)
        return {"stat": "Ok", "cash": "500000", "marginused": "1"}

    async def place_oco(self, oco):
        self.oco_calls.append(oco)
        return {"ok": True, "al_id": "AL999"}


_PLAN = {"levels": {"stop_pct": 50.0}}
_SIGNAL = {"deployment_id": "dep1"}


def _run(client, ordno, entry=145.10):
    return asyncio.run(arm_for(_PLAN, _SIGNAL, entry, client=client)(_Intent(), ordno))


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    monkeypatch.delenv("LIVE_BROKER_OCO_ENABLED", raising=False)


# --------------------------------------------------------------------------- #
# The flag itself
# --------------------------------------------------------------------------- #

def test_unset_means_disabled():
    assert _broker_oco_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_affirmative_values_enable_it(monkeypatch, value):
    monkeypatch.setenv("LIVE_BROKER_OCO_ENABLED", value)
    assert _broker_oco_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_everything_else_is_disabled(monkeypatch, value):
    monkeypatch.setenv("LIVE_BROKER_OCO_ENABLED", value)
    assert _broker_oco_enabled() is False


# --------------------------------------------------------------------------- #
# The arm path
# --------------------------------------------------------------------------- #

def test_no_oco_order_is_spent_when_disabled():
    c = _Client()
    assert _run(c, "ord-off") is None
    assert c.oco_calls == [], "PlaceOCOOrder was spent while the OCO is disabled"


def test_not_even_the_margin_probe_is_spent_when_disabled():
    """The probe exists to decide whether to place an OCO. With the OCO off it is
    a broker round-trip that can change nothing — and the Noren rate budget is
    shared with the Flattrade MCP."""
    c = _Client()
    _run(c, "ord-off-probe")
    assert c.margin_calls == []


def test_the_software_guard_still_registers_the_position():
    """The OCO is best-effort; registration is MANDATORY. Disabling one must
    never touch the other."""
    c = _Client()
    _run(c, "ord-off-reg")
    assert get_registry().get("ord-off-reg") is not None


def test_a_null_al_id_makes_auto_live_journal_no_broker_backstop():
    """auto_live writes oco_error='no_broker_backstop' whenever oco_al_id is
    falsy, and the Live cockpit's alert rail renders it. Returning None is what
    lights that up — the operator is TOLD there is no broker net, rather than
    being shown an al_id for one that never rested."""
    assert _run(_Client(), "ord-off-journal") is None


def test_enabling_the_flag_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setenv("LIVE_BROKER_OCO_ENABLED", "1")
    c = _Client()
    assert _run(c, "ord-on") == "AL999"
    assert len(c.oco_calls) == 1
    assert c.margin_calls, "the margin probe must still gate an enabled OCO"
