"""Gap G1 — a position re-attached after a restart must still belong to its deployment.

Before this fix `rehydrate_from_broker` registered every recovered position keyed by
its tsym with no `deployment_id`. Three things keyed on those two fields then missed
it for the rest of the session:

  * the deployment's Flatten / Stop select registry entries by `deployment_id`, so
    they found nothing and reported the contract under `unguarded_open_tsyms`;
  * the guard's P&L marks are written to the journal row whose `norenordno` equals
    the entry key, so the OPEN row's `marked_at` went stale;
  * the guard's own confirmed-flat close journals by the same key, so after the
    guard squared the position the row stayed OPEN until the next restart.

Attribution must never be a guess: a contract whose open rows span deployments, or
whose held quantity exceeds what the rows ordered, stays unattributed — as before.
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.live_position_guard import (  # noqa: E402
    LiveMonitorRegistry, LivePositionGuard,
)
from app.live.ownership import (  # noqa: E402
    attribution_for, resolve_rehydrate_attribution,
)
from tests.test_live_position_guard import (  # noqa: E402
    _FakeClient, _NOW, _Recorder, _TSYM, _aw, _guard, _pos, run,
)

OTHER = "SENSEX26JUN76600PE"


def _row(ordno, dep, *, tsym=_TSYM, qty=20, status="OPEN", **kw):
    return {"norenordno": ordno, "deployment_id": dep, "noren_tsym": tsym,
            "quantity": qty, "status": status, **kw}


# --------------------------------------------------------------------------- #
# The pure resolver
# --------------------------------------------------------------------------- #

def test_one_open_row_attributes_its_order_and_deployment():
    att = resolve_rehydrate_attribution(live_trades=[_row("N1", "D1")])
    assert att == {_TSYM: {"norenordno": "N1", "deployment_id": "D1",
                           "quantity": 20.0, "rows": 1}}


def test_closed_rows_are_not_candidates():
    att = resolve_rehydrate_attribution(live_trades=[
        _row("OLD", "D2", status="CLOSED"), _row("N1", "D1")])
    assert att[_TSYM]["norenordno"] == "N1" and att[_TSYM]["deployment_id"] == "D1"


def test_rows_spanning_two_deployments_are_never_guessed():
    att = resolve_rehydrate_attribution(live_trades=[_row("N1", "D1"), _row("N2", "D2")])
    assert _TSYM not in att


def test_a_row_with_no_deployment_blocks_attribution():
    att = resolve_rehydrate_attribution(live_trades=[_row("N1", "D1"), _row("N2", "")])
    assert _TSYM not in att
    assert resolve_rehydrate_attribution(live_trades=[_row("N1", None)]) == {}


def test_two_rows_of_one_deployment_give_the_deployment_but_no_order():
    """One registry entry cannot be keyed by two order numbers."""
    att = resolve_rehydrate_attribution(live_trades=[_row("N1", "D1", qty=20),
                                                     _row("N2", "D1", qty=40)])
    assert att[_TSYM] == {"norenordno": None, "deployment_id": "D1",
                          "quantity": 60.0, "rows": 2}


def test_a_legacy_row_resolves_its_tsym_through_the_order_book():
    legacy = _row("N1", "D1", tsym="")
    assert resolve_rehydrate_attribution(live_trades=[legacy]) == {}
    att = resolve_rehydrate_attribution(
        live_trades=[legacy], order_book=[{"norenordno": "N1", "tsym": _TSYM}])
    assert att[_TSYM]["norenordno"] == "N1"


def test_the_rows_own_noren_tsym_beats_the_order_book():
    att = resolve_rehydrate_attribution(
        live_trades=[_row("N1", "D1")], order_book=[{"norenordno": "N1", "tsym": OTHER}])
    assert set(att) == {_TSYM}


@pytest.mark.parametrize("bad", [None, "", "abc", float("nan"), 0, -5])
def test_an_unreadable_quantity_is_unknown_not_zero(bad):
    att = resolve_rehydrate_attribution(live_trades=[_row("N1", "D1", qty=bad)])
    assert att[_TSYM]["quantity"] is None


def test_a_row_without_an_order_number_is_ignored():
    assert resolve_rehydrate_attribution(live_trades=[_row("", "D1")]) == {}


@pytest.mark.parametrize("netqty,ok", [(20, True), (10, True), (-20, True),
                                       (21, False), (0, False), ("x", False)])
def test_attribution_must_cover_the_whole_held_quantity(netqty, ok):
    att = {_TSYM: {"norenordno": "N1", "deployment_id": "D1", "quantity": 20.0}}
    assert (attribution_for(_TSYM, netqty, att) is not None) is ok


def test_attribution_with_an_unknown_quantity_is_declined():
    att = {_TSYM: {"norenordno": "N1", "deployment_id": "D1", "quantity": None}}
    assert attribution_for(_TSYM, 20, att) is None
    assert attribution_for(_TSYM, 20, None) is None
    assert attribution_for(OTHER, 20, att) is None


# --------------------------------------------------------------------------- #
# The guard's rehydrate
# --------------------------------------------------------------------------- #

ATT = {_TSYM: {"norenordno": "N1", "deployment_id": "D1", "quantity": 20.0, "rows": 1}}


def _rehydrate(reg, book, *, owned=frozenset({_TSYM}), attribution=ATT):
    return run(_guard(reg, _FakeClient(book), _Recorder())
               .rehydrate_from_broker(owned_tsyms=set(owned), attribution=attribution))


def test_an_attributed_position_is_keyed_like_its_original_arm():
    reg = LiveMonitorRegistry()
    assert _rehydrate(reg, [_pos(netqty=20, lp=250.0)]) == 1
    item = reg.get("N1")
    assert item is not None and reg.get(_TSYM) is None
    assert item["deployment_id"] == "D1" and item["tsym"] == _TSYM
    # Levels are NOT restored — still the recovery-time default, still flagged.
    assert item["source"] == "rehydrated" and item["entry_price_is_mark"] is True
    assert item["state"]["stop_level"] == 125.0


def test_attribution_never_widens_ownership():
    """The attribution map is not proof of ownership — the owned set still gates."""
    reg = LiveMonitorRegistry()
    assert _rehydrate(reg, [_pos(netqty=20)], owned=set()) == 0
    assert len(reg) == 0


def test_more_contracts_than_the_row_ordered_stay_unattributed():
    reg = LiveMonitorRegistry()
    assert _rehydrate(reg, [_pos(netqty=40)]) == 1
    item = reg.get(_TSYM)
    assert item is not None and item["deployment_id"] is None


def test_a_deployment_only_attribution_keeps_the_tsym_key():
    reg = LiveMonitorRegistry()
    _rehydrate(reg, [_pos(netqty=20)], attribution={
        _TSYM: {"norenordno": None, "deployment_id": "D1", "quantity": 60.0, "rows": 2}})
    item = reg.get(_TSYM)
    assert item is not None and item["deployment_id"] == "D1"


def test_an_order_already_guarded_under_another_symbol_is_not_attributed():
    from app.live.live_sl_monitor import build_monitor_state
    reg = LiveMonitorRegistry()
    reg.register(key="N1", tsym=OTHER, exch="BFO", qty=20, prd="I", entry_price=90.0,
                 state=build_monitor_state(90.0, stop_pct=30), source="auto_live",
                 deployment_id="D9")
    _rehydrate(reg, [_pos(netqty=20)])
    assert reg.get("N1")["tsym"] == OTHER, "clobbered the existing entry"
    item = reg.get(_TSYM)
    assert item is not None and item["deployment_id"] is None


def test_without_attribution_nothing_changes():
    reg = LiveMonitorRegistry()
    _rehydrate(reg, [_pos(netqty=20)], attribution=None)
    item = reg.get(_TSYM)
    assert item is not None and item["deployment_id"] is None


# --------------------------------------------------------------------------- #
# What the attribution is FOR — the guard's own writes land on the journal row
# --------------------------------------------------------------------------- #

class _Hooks:
    def __init__(self):
        self.marks, self.closes, self.expires = [], [], []

    async def on_mark(self, marks):
        self.marks.extend(marks)

    async def on_close(self, entry, exit_price, reason, result):
        self.closes.append((entry["id"], entry.get("deployment_id"), reason))

    async def on_expire(self, entry, reason):
        self.expires.append((entry["id"], reason))


def _hooked_guard(reg, client, hooks, rec=None):
    return LivePositionGuard(
        registry=reg, client_factory=lambda: _aw(client),
        square_fn=(rec or _Recorder()).square_fn, now_fn=lambda: _NOW,
        in_market_hours=lambda: True, on_mark=hooks.on_mark,
        on_close=hooks.on_close, on_expire=hooks.on_expire)


def test_marks_and_the_confirmed_flat_close_reach_the_journal_row():
    reg, hooks, rec = LiveMonitorRegistry(), _Hooks(), _Recorder()
    client = _FakeClient([_pos(netqty=20, lp=250.0)])
    g = _hooked_guard(reg, client, hooks, rec)
    run(g.rehydrate_from_broker(owned_tsyms={_TSYM}, attribution=ATT))
    run(g._cycle())
    assert hooks.marks and hooks.marks[-1]["norenordno"] == "N1"
    client.set([_pos(netqty=20, lp=100.0)])            # through the 125 default stop
    run(g._cycle())
    assert rec.squared, "the default stop did not fire"
    client.set([_pos(netqty=0, lp=100.0)])
    for _ in range(3):
        run(g._cycle())
    assert hooks.closes and hooks.closes[0][:2] == ("N1", "D1")
    assert len(reg) == 0


def test_a_position_that_goes_flat_before_the_first_cycle_is_not_never_filled():
    """With the real order number as the key, an age-out would journal a trade that
    DID fill as `never_filled`. The entry was adopted because the book showed it held."""
    reg, hooks = LiveMonitorRegistry(), _Hooks()
    client = _FakeClient([_pos(netqty=20, lp=250.0)])
    g = _hooked_guard(reg, client, hooks)
    run(g.rehydrate_from_broker(owned_tsyms={_TSYM}, attribution=ATT))
    client.set([_pos(netqty=0, lp=250.0)])
    for _ in range(45):                                  # past max_pending_misses=40
        run(g._cycle())
    assert hooks.expires == [], "a filled position was aged out as never_filled"
    assert hooks.closes == [], "closed outside the guard — the reconcile journals it"
    assert len(reg) == 0


# --------------------------------------------------------------------------- #
# The deployment's Flatten reaches it
# --------------------------------------------------------------------------- #

def test_flatten_reaches_a_restart_recovered_position(monkeypatch):
    from tests.test_live_controls import _flatten, _live_db, _open_market
    from tests.test_deployment_live_routes import _install

    db = _live_db()
    db.live_trades.rows.append(_row("N1", "dep-1", tsym="NIFTY25100CE", qty=65))
    reg = LiveMonitorRegistry()
    run(_guard(reg, _FakeClient([_pos(netqty=65, tsym="NIFTY25100CE")]), _Recorder())
        .rehydrate_from_broker(
            owned_tsyms={"NIFTY25100CE"},
            attribution=resolve_rehydrate_attribution(live_trades=db.live_trades.rows)))
    _r, squared = _install(monkeypatch, db, registry=reg)
    _open_market(monkeypatch)
    before = copy.deepcopy(db.strategy_deployments.rows[0])
    out = _flatten()
    assert squared == ["NIFTY25100CE"]
    assert out["unguarded_open_tsyms"] == [] and out["complete"] is True
    assert db.strategy_deployments.rows[0] == before


# --------------------------------------------------------------------------- #
# End to end through live_startup_recovery
# --------------------------------------------------------------------------- #

class _ProjColl:
    """Honours an INCLUSION projection — a field the runtime forgets to project must
    be missing here, exactly as it would be from Mongo."""

    def __init__(self, docs):
        self.docs = docs

    def find(self, q=None, proj=None):
        keep = [k for k, v in (proj or {}).items() if v and k != "_id"]

        async def _cursor():
            for d in self.docs:
                if q and not all(d.get(k) == v for k, v in q.items()
                                 if not isinstance(v, dict)):
                    continue
                yield {k: d[k] for k in keep if k in d} if keep else dict(d)
        return _cursor()


def test_startup_recovery_re_attaches_the_position_to_its_deployment(monkeypatch):
    pytest.importorskip("motor", reason="imports app.runtime (motor)")
    import app.runtime as rt
    from tests.test_premium_momentum_recovery import _Client, _DB, _Locks

    tsym = "NIFTY29DEC99C24000"                      # far future — never expired
    db = _DB(_Locks(), [])
    db.live_trades = _ProjColl([{
        "id": "t1", "norenordno": "N7", "cid": "c7", "status": "OPEN",
        "noren_tsym": tsym, "deployment_id": "dep-A", "quantity": 65,
        "trading_symbol": "NIFTY 24000 CE 29 DEC 99"}])
    db.live_orders = _ProjColl([{"norenordno": "N7", "client_order_id": "c7",
                                 "state": "SUBMITTED", "intent": {"tsym": tsym}}])
    book = [{"tsym": tsym, "exch": "NFO", "netqty": "65", "lp": "120"}]
    client = _Client(book, orders=[])                # no order-book help: the row's
    reg = LiveMonitorRegistry()                      # own noren_tsym must carry it

    async def _factory():
        return client

    class _Eng:
        def __init__(self, **kw):
            pass

        async def resume_pending(self):
            return {"adopted": 0, "needs_submit": 0}

    async def _reconcile(_db, _client):
        return {"status": "ok"}

    guard = LivePositionGuard(registry=reg, client_factory=_factory,
                              square_fn=_Recorder().square_fn, now_fn=lambda: _NOW)
    monkeypatch.setattr(rt, "_live_guard_client_factory", _factory)
    monkeypatch.setattr(rt, "get_db", lambda: db)
    monkeypatch.setattr(rt, "get_live_monitor_registry", lambda: reg)
    monkeypatch.setattr(rt, "live_position_guard", guard)
    monkeypatch.setattr("app.live.engine.LiveEngine", _Eng)
    monkeypatch.setattr("app.live.reboot_reconcile.reconcile_on_startup", _reconcile)

    assert asyncio.run(rt.live_startup_recovery()) is True
    item = reg.get("N7")
    assert item is not None, f"not keyed by its order: {[e['id'] for e in reg.snapshot()]}"
    assert item["deployment_id"] == "dep-A" and item["tsym"] == tsym
    assert item["source"] == "rehydrated"
