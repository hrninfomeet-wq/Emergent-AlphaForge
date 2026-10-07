"""Key-wide ORDER-API budget with an exit/cancel reserve (audit 2026-10-07 #3).

Flattrade's ORDER API limit is 10/s AND 40/min per API key
(docs/Resources/flattrade-pi-api/endpoints/57-api-rate-limits.md). The only
local limiter was `safety.RateThrottle` — a 9/s token bucket, on deployed
ENTRIES only, with no per-minute window at all. Cancels, modifies, exits and
GTT/OCO calls from the guard / kill switch / auto-square were never counted,
so entries could spend the whole 40/min and the broker would then refuse the
exit that protects a position.

`OrderRateBudget` counts EVERY order-API call key-wide (recorded at
`FlattradeClient._post`, the single choke point) in sliding 1 s / 60 s windows,
and refuses an ENTRY whenever taking it would leave less than the exit reserve
(plus headroom for traffic this process cannot see — the MCP shares the key).
Exits and cancels are never refused locally; they are counted.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.mock_noren import MockNoren  # noqa: E402
from app.live.order_budget import OrderRateBudget, budget_for  # noqa: E402
from tests.test_live_client import _client, _intent, _make_httpx_mock, run  # noqa: E402
from tests.test_live_executor import _GOOD_LIMITS, FakeEngine  # noqa: E402
from tests.test_live_executor_deployed import _place_deployed, _run  # noqa: E402


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _budget(clock):
    return OrderRateBudget(clock=clock)


# --- the budget itself ------------------------------------------------------

def test_entries_stop_short_of_the_per_minute_limit():
    """40/min key-wide minus the exit reserve and unseen-traffic headroom: entries
    get at most 24 of any rolling minute, so >= 16 stay free for exits."""
    clock = _Clock()
    b = _budget(clock)
    admitted = 0
    for _ in range(60):
        ok, _why = b.entry_allowed()
        if not ok:
            break
        b.record("PlaceOrder")
        admitted += 1
        clock.t += 1.1                     # stay clear of the per-second window
    assert admitted == 24, admitted
    assert b.limits()["per_min"] == 40
    assert b.limits()["per_min"] - admitted >= 16


def test_exits_and_cancels_count_against_the_window():
    clock = _Clock()
    b = _budget(clock)
    for route in ["CancelOrder"] * 12 + ["ModifyOrder"] * 6 + ["CancelOCOOrder"] * 6:
        b.record(route)
        clock.t += 1.1
    ok, why = b.entry_allowed()
    assert not ok and "per_min" in why


def test_exits_are_never_refused_locally():
    """Recording is unconditional — the budget is a gate for ENTRIES only."""
    clock = _Clock()
    b = _budget(clock)
    for _ in range(60):
        b.record("PlaceOrder")
    assert b.snapshot()["last_60s"] == 60


def test_non_order_routes_do_not_count():
    clock = _Clock()
    b = _budget(clock)
    for route in ("OrderBook", "PositionBook", "GetQuotes", "Limits", "TPSeries") * 20:
        b.record(route)
    assert b.snapshot()["last_60s"] == 0
    assert b.entry_allowed()[0] is True


def test_minute_window_slides():
    clock = _Clock()
    b = _budget(clock)
    for _ in range(24):
        b.record("PlaceOrder")
        clock.t += 1.1
    assert b.entry_allowed()[0] is False
    clock.t += 60.0
    assert b.entry_allowed()[0] is True


def test_per_second_is_a_sliding_window_not_a_bursty_bucket():
    """The token bucket admitted ~2x its rate across a second boundary. A sliding
    window admits at most 5 entries (10/s minus reserve/headroom) in ANY second."""
    clock = _Clock(1000.9)
    b = _budget(clock)
    for _ in range(5):
        assert b.entry_allowed()[0] is True
        b.record("PlaceOrder")
    clock.t = 1001.1                       # a new calendar second, same rolling one
    ok, why = b.entry_allowed()
    assert not ok and "per_sec" in why
    clock.t = 1002.0
    assert b.entry_allowed()[0] is True


def test_budget_is_shared_per_api_key():
    assert budget_for("ACCT1") is budget_for("ACCT1")
    assert budget_for("ACCT1") is not budget_for("ACCT2")


# --- recorded at the client's single REST choke point -----------------------

def test_flattrade_client_records_order_routes_key_wide():
    client = _client(uid="BUDGET-REC-1")
    before = client.order_budget().snapshot()["last_60s"]
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Ok", "norenordno": "1"})
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        run(client.place_order(_intent()))
        run(client.cancel_order("1"))
    mock_books, _ = _make_httpx_mock(200, [])
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_books):
        try:
            run(client.order_book())
        except Exception:
            pass
    assert client.order_budget().snapshot()["last_60s"] - before == 2
    # Same account, different session object → same budget.
    assert _client(uid="BUDGET-REC-1").order_budget() is client.order_budget()


# --- enforced at the entry chokepoint ---------------------------------------

class _BudgetedMock(MockNoren):
    def __init__(self, budget, **kw):
        super().__init__(**kw)
        self._budget = budget
        self.place_calls = 0

    def order_budget(self):
        return self._budget

    async def place_order(self, intent):
        self.place_calls += 1
        return await super().place_order(intent)


def test_executor_refuses_an_entry_that_would_eat_the_exit_reserve():
    clock = _Clock()
    b = _budget(clock)
    for _ in range(24):
        b.record("CancelOrder")
        clock.t += 1.1
    client = _BudgetedMock(b, limits_data=_GOOD_LIMITS)
    engine = FakeEngine()
    res = _run(_place_deployed(client=client, engine=engine, capped_lots=1,
                               autoplace_armed=True))
    assert client.place_calls == 0, "the entry was transmitted into the exit reserve"
    assert res["placed"] is False
    assert "order_budget" in res["reason"], res["reason"]
    assert engine.halt_calls == [], "a budget refusal is a skip, not a halt"


def test_executor_transmits_when_the_budget_has_room():
    b = _budget(_Clock())
    client = _BudgetedMock(b, limits_data=_GOOD_LIMITS)
    res = _run(_place_deployed(client=client, capped_lots=1, autoplace_armed=True))
    assert client.place_calls == 1
    assert res["placed"] is True, res
