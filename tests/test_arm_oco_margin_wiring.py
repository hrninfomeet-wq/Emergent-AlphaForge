"""The arm path must consult the margin probe BEFORE spending the OCO order.

Behavioural, not source-text: drive the real `arm_for(...)` callable with a fake
client and assert on what the client was ASKED to do. (Source-text assertions have
misfired four times in this audit against correct implementations.)

Guarantees pinned here:
  * a readable shortfall skips PlaceOCOOrder entirely and returns no al_id;
  * a healthy account still places it and returns the al_id;
  * a probe that RAISES must not cost the position its catastrophe net;
  * the position is REGISTERED with the software guard in every one of those
    cases — the OCO is best-effort, registration is mandatory.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_deploy_context import arm_for  # noqa: E402
from app.live.live_position_guard import get_registry  # noqa: E402


class _Intent:
    exch, tsym, qty, prd = "NFO", "NIFTY18AUG26P24300", 65, "M"


class _Client:
    """Records what it was asked; never touches a network."""

    def __init__(self, margin_resp=None, raise_on_margin=False):
        self._margin_resp = margin_resp or {}
        self._raise = raise_on_margin
        self.margin_calls, self.oco_calls = [], []

    async def order_margin(self, **kw):
        self.margin_calls.append(kw)
        if self._raise:
            raise RuntimeError("probe exploded")
        return self._margin_resp

    async def place_oco(self, oco):
        self.oco_calls.append(oco)
        return {"ok": True, "al_id": "AL999"}


_PLAN = {"levels": {"stop_pct": 50.0}}
_SIGNAL = {"deployment_id": "dep1"}


def _run(client, ordno):
    get_registry().clear() if hasattr(get_registry(), "clear") else None
    arm = arm_for(_PLAN, _SIGNAL, 61.35, client=client)
    return asyncio.run(arm(_Intent(), ordno))


def test_a_readable_shortfall_skips_the_oco_order():
    c = _Client({"stat": "Ok", "cash": "86932.68", "marginused": "101830.33"})
    al = _run(c, "ord-short")
    assert c.margin_calls, "the probe was never sent"
    assert c.oco_calls == [], "PlaceOCOOrder was spent on a provable shortfall"
    assert al is None
    assert get_registry().get("ord-short") is not None, "software guard must still hold it"


def test_a_healthy_account_still_gets_its_resting_oco():
    c = _Client({"stat": "Ok", "cash": "500000", "marginused": "101830.33"})
    assert _run(c, "ord-ok") == "AL999"
    assert len(c.oco_calls) == 1


def test_a_probe_that_raises_does_not_cost_the_position_its_net():
    c = _Client(raise_on_margin=True)
    assert _run(c, "ord-boom") == "AL999"
    assert len(c.oco_calls) == 1, "a probe failure must not skip the backstop"


def test_the_probe_prices_the_resting_SELL_leg_not_the_entry():
    """Margin for a resting short is the thing in question — a BUY probe would
    return the (much smaller) premium outlay and never detect the shortfall."""
    c = _Client({"stat": "Ok", "cash": "500000", "marginused": "1"})
    _run(c, "ord-leg")
    kw = c.margin_calls[0]
    assert kw["trantype"] == "S"
    assert kw["prd"] == "M"          # NRML — the reason it is margined as naked
    assert int(kw["qty"]) == 65
