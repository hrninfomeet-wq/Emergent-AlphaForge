# tests/test_premium_momentum_recovery.py
"""Track B Task 7 — recovery rehydrates entered premium-momentum positions
with the PERSISTED lock state (entry premium, deployment exit plan) instead of
the generic 50%-catastrophe default, and closes locks whose position is gone
from the broker book (done_for_day='exited_while_down').

CONTAINER test (imports app.runtime -> motor): pytest.importorskip below skips
the WHOLE MODULE at collection time in a motor-less Python (a pytestmark alone
cannot stop the module-level app.runtime import from erroring the collection).
Fakes follow the repo's in-memory async-collection pattern; premium_locks
reuses _FakeLocks from tests/test_premium_lock_store.py (+ the `$ne` operator
the recovery scan uses).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

pytest.importorskip(
    "motor",
    reason="imports app.runtime (motor) — runs in the backend container",
)

import app.runtime as rt  # noqa: E402
from app.runtime import live_startup_recovery, rehydrate_premium_momentum  # noqa: E402
from tests.test_premium_lock_store import _FakeLocks  # noqa: E402


def run(c):
    return asyncio.run(c)


def _today_ist() -> str:
    return (datetime.now(timezone.utc)
            + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


class _Locks(_FakeLocks):
    """_FakeLocks + the `$ne` operator the recovery scan query uses."""

    def _matches(self, d, q):
        rest = {}
        for k, v in q.items():
            if isinstance(v, dict) and "$ne" in v:
                if d.get(k) == v["$ne"]:
                    return False
            else:
                rest[k] = v
        return super()._matches(d, rest)


class _Deployments:
    def __init__(self, docs):
        self.docs = list(docs)

    async def find_one(self, q, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return dict(d)
        return None


class _DB:
    def __init__(self, locks, deployments):
        self.premium_locks = locks
        self.strategy_deployments = _Deployments(deployments)
        self.live_orders = object()  # LiveEngine wiring only — never queried


class _Reg:
    """Fake monitor registry: snapshot() feeds the already-watched guard;
    register() records the call and adds the entry to the snapshot (like the
    real registry). `preloaded` simulates entries from a prior recovery run
    or a fresh arm. `events` (optional shared list) records ordering."""

    def __init__(self, preloaded=None, events=None):
        self.items = [dict(e) for e in (preloaded or [])]
        self.calls = []
        self._events = events

    def snapshot(self):
        return [dict(e) for e in self.items]

    def register(self, **kw):
        self.calls.append(kw)
        self.items.append({"id": str(kw.get("key")), "tsym": str(kw.get("tsym"))})
        if self._events is not None:
            self._events.append(("premium_register", kw.get("tsym")))
        return kw


# Review Finding 1: the lock's persisted contract carries the UPSTOX symbol
# (spaced format) while the broker book + order book use the NOREN tsym —
# DIFFERENT strings on purpose so any direct match is caught by these tests.
CE = {"trading_symbol": "NIFTY 24000 CE 10 JUL 26", "exch": "NFO",
      "instrument_key": "NSE_FO|1001"}
PE = {"trading_symbol": "NIFTY 24000 PE 10 JUL 26", "exch": "NFO",
      "instrument_key": "NSE_FO|1002"}
NOREN_CE = "NIFTY10JUL26C24000"
NOREN_PE = "NIFTY10JUL26P24000"


def _entered_lock(dep_id, side, contract, ordno, entry):
    return {
        "deployment_id": dep_id, "session_date": _today_ist(),
        "done_for_day": False, "triggered_side": side, side: dict(contract),
        "entered_norenordno": ordno, "entry_premium": entry,
    }


def test_reattaches_with_persisted_entry_and_stepped_xy_trail():
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D1", "ce", CE, "N1", 115.0)))
    db = _DB(locks, [{
        "id": "D1",
        "params": {"stop_pct": 30.0, "target_pct": 60.0},
        "risk": {"exit_controls": {"mode": "stepped_xy", "x": 20.0, "y": 10.0}},
    }])
    reg = _Reg()
    # float-form netqty string: Noren can return "65.00" — must parse, not error
    book = {NOREN_CE: {"tsym": NOREN_CE, "netqty": "65.00",
                       "exch": "NFO", "lp": "118.0"}}
    out = run(rehydrate_premium_momentum(db, reg, book,
                                         noren_tsym_by_ordno={"N1": NOREN_CE}))
    assert out == {"reattached": 1, "closed": 0, "skipped": 0, "errors": 0}
    assert len(reg.calls) == 1
    kw = reg.calls[0]
    assert kw["key"] == "N1"                      # keyed by the entry norenordno
    assert kw["tsym"] == NOREN_CE                 # the NOREN symbol, never Upstox
    assert kw["qty"] == 65
    assert kw["entry_price"] == 115.0             # PERSISTED entry, NOT a 50% default
    assert kw["source"] == "auto_live"
    assert kw["deployment_id"] == "D1"
    state = kw["state"]
    assert state["entry"] == 115.0
    assert state["stop_level"] == pytest.approx(80.5)     # 115 − 30%
    assert state["target_level"] == pytest.approx(184.0)  # 115 + 60%
    assert state["mode"] == "stepped_xy"          # trail from risk.exit_controls
    assert state["trail"]["x"] == 20.0
    assert state["trail"]["y"] == 10.0


def test_dead_lock_marked_done_and_not_registered():
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D2", "pe", PE, "N2", 90.0)))
    db = _DB(locks, [{"id": "D2", "params": {}, "risk": {}}])
    reg = _Reg()
    # position GONE from book — but its ordno RESOLVES via the order book,
    # so "gone" is a trusted determination (unresolved would SKIP instead).
    out = run(rehydrate_premium_momentum(db, reg, {},
                                         noren_tsym_by_ordno={"N2": NOREN_PE}))
    assert out == {"reattached": 0, "closed": 1, "skipped": 0, "errors": 0}
    assert reg.calls == []
    doc = run(locks.find_one({"deployment_id": "D2",
                              "session_date": _today_ist()}))
    assert doc["done_for_day"] is True
    assert doc["done_reason"] == "exited_while_down"


def test_missing_entry_premium_left_to_generic_rehydrate():
    """No persisted entry premium -> neither register nor mark_done (the
    generic 50%-default rehydrate owns that position)."""
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D3", "ce", CE, "N3", None)))
    db = _DB(locks, [{"id": "D3", "params": {}, "risk": {}}])
    reg = _Reg()
    book = {NOREN_CE: {"tsym": NOREN_CE, "netqty": 65}}
    out = run(rehydrate_premium_momentum(db, reg, book,
                                         noren_tsym_by_ordno={"N3": NOREN_CE}))
    assert out == {"reattached": 0, "closed": 0, "skipped": 0, "errors": 0}
    assert reg.calls == []
    doc = run(locks.find_one({"deployment_id": "D3",
                              "session_date": _today_ist()}))
    assert doc["done_for_day"] is False


# --- re-run safety: already-watched positions are NEVER re-registered --------

def test_already_watched_tsym_skipped_never_double_registered():
    """Run 1's premium book read failed but the GENERIC rehydrate guarded the
    tsym (key=tsym, catastrophe stop). The supervisor retry must NOT register
    the same tsym a second time under key=norenordno — two registry entries on
    one position means two independent stop evaluations -> two full-qty square
    orders on a fast gap (naked short) + a double-journaled close."""
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D1", "ce", CE, "N1", 115.0)))
    db = _DB(locks, [{"id": "D1", "params": {"stop_pct": 30.0}, "risk": {}}])
    reg = _Reg(preloaded=[{"id": NOREN_CE,
                           "tsym": NOREN_CE}])  # generic entry (Noren-keyed)
    book = {NOREN_CE: {"tsym": NOREN_CE, "netqty": "65"}}
    out = run(rehydrate_premium_momentum(db, reg, book,
                                         noren_tsym_by_ordno={"N1": NOREN_CE}))
    assert out == {"reattached": 0, "closed": 0, "skipped": 1, "errors": 0}
    assert reg.calls == []
    # the lock is NOT closed either — the position is alive and guarded
    doc = run(locks.find_one({"deployment_id": "D1",
                              "session_date": _today_ist()}))
    assert doc["done_for_day"] is False


def test_already_watched_norenordno_skipped_mid_square_not_clobbered():
    """A forced re-run (daily OAuth fires maybe_run_live_recovery(force=True))
    while the premium entry is mid-square must NOT re-register: register()
    REPLACES the entry, resetting squaring/square_ordno and re-arming a second
    exit while the first still rests at the broker."""
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D1", "ce", CE, "N1", 115.0)))
    db = _DB(locks, [{"id": "D1", "params": {"stop_pct": 30.0}, "risk": {}}])
    reg = _Reg(preloaded=[{"id": "N1", "tsym": NOREN_CE,
                           "squaring": True}])  # run 1's own entry, mid-square
    book = {NOREN_CE: {"tsym": NOREN_CE, "netqty": "65"}}
    out = run(rehydrate_premium_momentum(db, reg, book,
                                         noren_tsym_by_ordno={"N1": NOREN_CE}))
    assert out == {"reattached": 0, "closed": 0, "skipped": 1, "errors": 0}
    assert reg.calls == []


# --- live_startup_recovery step-2 wiring (book read, filtering, ordering) ----

class _Client:
    def __init__(self, book, orders=None):
        self._book = book
        self._orders = orders

    async def position_book(self):
        if isinstance(self._book, Exception):
            raise self._book
        return self._book

    async def order_book(self):
        if self._orders is None:
            # Default: derive a same-tsym order row per position row, plus the
            # well-known test ordnos (N1/N2) mapped to the Noren symbols.
            rows = [{"norenordno": "N1", "tsym": NOREN_CE},
                    {"norenordno": "N2", "tsym": NOREN_PE}]
            if isinstance(self._book, list):
                for p_ in self._book:
                    if p_.get("tsym"):
                        rows.append({"norenordno": "N1", "tsym": p_["tsym"]})
            return rows
        if isinstance(self._orders, Exception):
            raise self._orders
        return self._orders


def _wire(monkeypatch, *, book, db, reg, events):
    """Stub every step of live_startup_recovery around step 2 so the tests
    exercise the REAL inline wiring (book read -> netqty filter -> premium
    rehydrate -> generic rehydrate ordering)."""

    async def _factory():
        return _Client(book)

    class _Eng:
        def __init__(self, **kw):
            pass

        async def resume_pending(self):
            return {"adopted": 0, "needs_submit": 0}

    async def _generic_rehydrate():
        events.append(("generic_rehydrate",))
        return 0

    async def _reconcile(_db, _client):
        return {"status": "ok"}

    monkeypatch.setattr(rt, "_live_guard_client_factory", _factory)
    monkeypatch.setattr(rt, "get_db", lambda: db)
    monkeypatch.setattr(rt, "get_live_monitor_registry", lambda: reg)
    monkeypatch.setattr(rt.live_position_guard, "rehydrate_from_broker",
                        _generic_rehydrate)
    monkeypatch.setattr("app.live.engine.LiveEngine", _Eng)
    monkeypatch.setattr("app.live.reboot_reconcile.reconcile_on_startup",
                        _reconcile)


def test_startup_recovery_premium_runs_before_generic_with_persisted_state(monkeypatch):
    """The commit's headline safety argument: the premium rehydrate runs BEFORE
    the generic guard rehydrate, so the premium position gets its persisted
    plan and the generic step (which skips watched tsyms) leaves it alone."""
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D1", "ce", CE, "N1", 115.0)))
    db = _DB(locks, [{"id": "D1", "params": {"stop_pct": 30.0}, "risk": {}}])
    events = []
    reg = _Reg(events=events)
    book = [{"tsym": CE["trading_symbol"], "netqty": "65.00", "exch": "NFO"}]
    _wire(monkeypatch, book=book, db=db, reg=reg, events=events)
    assert run(live_startup_recovery()) is True
    assert events == [("premium_register", CE["trading_symbol"]),
                      ("generic_rehydrate",)]
    assert reg.calls[0]["entry_price"] == 115.0
    assert reg.calls[0]["qty"] == 65  # "65.00" parsed via _parse_netqty


def test_startup_recovery_flat_netqty_filtered_and_dead_lock_closed(monkeypatch):
    """A netqty==0 row is filtered out of the held map, so a lock whose
    position went flat is closed (exited_while_down) and never registered."""
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D1", "ce", CE, "N1", 115.0)))
    db = _DB(locks, [{"id": "D1", "params": {}, "risk": {}}])
    events = []
    reg = _Reg(events=events)
    book = [{"tsym": CE["trading_symbol"], "netqty": "0.00", "exch": "NFO"}]
    _wire(monkeypatch, book=book, db=db, reg=reg, events=events)
    run(live_startup_recovery())
    assert reg.calls == []
    doc = run(locks.find_one({"deployment_id": "D1",
                              "session_date": _today_ist()}))
    assert doc["done_for_day"] is True
    assert doc["done_reason"] == "exited_while_down"


@pytest.mark.parametrize("book", [[], {"stat": "Not_Ok", "emsg": "err"}],
                         ids=["empty_list", "non_list_payload"])
def test_startup_recovery_unknown_book_skips_premium_no_lock_closed(monkeypatch, book):
    """Empty/non-list position book == UNKNOWN (transient): the premium step
    is skipped entirely — no register AND no lock closed."""
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D1", "ce", CE, "N1", 115.0)))
    db = _DB(locks, [{"id": "D1", "params": {}, "risk": {}}])
    events = []
    reg = _Reg(events=events)
    _wire(monkeypatch, book=book, db=db, reg=reg, events=events)
    run(live_startup_recovery())
    assert reg.calls == []
    doc = run(locks.find_one({"deployment_id": "D1",
                              "session_date": _today_ist()}))
    assert doc["done_for_day"] is False


def test_startup_recovery_book_read_failure_is_incomplete(monkeypatch):
    """A failed position-book read skips the premium step (no lock closed)
    AND returns complete=False so the supervisor retries."""
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D1", "ce", CE, "N1", 115.0)))
    db = _DB(locks, [{"id": "D1", "params": {}, "risk": {}}])
    events = []
    reg = _Reg(events=events)
    _wire(monkeypatch, book=RuntimeError("network down"), db=db, reg=reg,
          events=events)
    assert run(live_startup_recovery()) is False
    assert reg.calls == []
    doc = run(locks.find_one({"deployment_id": "D1",
                              "session_date": _today_ist()}))
    assert doc["done_for_day"] is False


def test_startup_recovery_premium_errors_make_run_incomplete(monkeypatch):
    """errors>0 from the premium rehydrate -> complete=False (the position
    would ride at the generic catastrophe stop with its persisted plan lost;
    never latch a green 'recovered' over that). Retry is safe: the already-
    watched guard skips every lock that DID attach."""
    db = _DB(_Locks(), [])
    events = []
    reg = _Reg(events=events)
    book = [{"tsym": CE["trading_symbol"], "netqty": "65", "exch": "NFO"}]
    _wire(monkeypatch, book=book, db=db, reg=reg, events=events)

    async def _failing_rehydrate(_db, _reg, _held, **_kw):
        return {"reattached": 0, "closed": 0, "skipped": 0, "errors": 1}

    monkeypatch.setattr(rt, "rehydrate_premium_momentum", _failing_rehydrate)
    assert run(live_startup_recovery()) is False


# ====================== 5B B7: per-leg (both-mode) rehydration =================

def _both_lock(dep_id="D1", **extra):
    """A both-mode lock: entries live in per-leg fields; the legacy
    entered_norenordno is deliberately None (review C2's flagged blind spot —
    the old $ne:None selection skipped these entirely)."""
    return {
        "deployment_id": dep_id, "session_date": _today_ist(),
        "done_for_day": False, "triggered_side": None,
        "entered_norenordno": None, "entry_premium": None,
        "ce": dict(CE), "pe": dict(PE),
        **extra,
    }


_B7_DEP = [{"id": "D1",
            "params": {"leg_mode": "both", "stop_pct": 30.0, "target_pct": 60.0,
                       "lazy_enabled": True, "lazy_momentum_pct": 10.0,
                       "lazy_stop_pct": 12.0, "exit_time": "14:30"},
            "risk": {}}]


def test_b7_both_mode_two_legs_rehydrated_with_own_orders():
    locks = _Locks()
    run(locks.insert_one(_both_lock(
        ce_triggered=True, ce_entered_norenordno="N1", ce_entry_premium=115.0,
        pe_triggered=True, pe_entered_norenordno="N2", pe_entry_premium=95.0)))
    db = _DB(locks, list(_B7_DEP))
    reg = _Reg()
    book = {NOREN_CE: {"tsym": NOREN_CE, "netqty": "65",
                       "exch": "NFO", "lp": "118.0"},
            NOREN_PE: {"tsym": NOREN_PE, "netqty": "65",
                       "exch": "NFO", "lp": "96.0"}}
    out = run(rehydrate_premium_momentum(
        db, reg, book, noren_tsym_by_ordno={"N1": NOREN_CE, "N2": NOREN_PE}))
    assert out == {"reattached": 2, "closed": 0, "skipped": 0, "errors": 0}
    keys = sorted(c["key"] for c in reg.calls)
    assert keys == ["N1", "N2"], "each leg registers under its OWN norenordno"
    assert sorted(c["tsym"] for c in reg.calls) == sorted([NOREN_CE, NOREN_PE])
    for c in reg.calls:
        assert c["square_at_ist"] == "14:30", "exit_time must rehydrate too (B5)"


def test_b7_one_leg_gone_marks_only_that_leg_and_not_whole_doc():
    locks = _Locks()
    run(locks.insert_one(_both_lock(
        ce_triggered=True, ce_entered_norenordno="N1", ce_entry_premium=115.0,
        pe_triggered=True, pe_entered_norenordno="N2", pe_entry_premium=95.0)))
    db = _DB(locks, list(_B7_DEP))
    reg = _Reg()
    # CE position gone from the broker book; PE still open. Both ordnos
    # RESOLVE via the order book, so gone is a trusted determination.
    book = {NOREN_PE: {"tsym": NOREN_PE, "netqty": "65",
                       "exch": "NFO", "lp": "96.0"}}
    out = run(rehydrate_premium_momentum(
        db, reg, book, noren_tsym_by_ordno={"N1": NOREN_CE, "N2": NOREN_PE}))
    assert out["closed"] == 1 and out["reattached"] == 1
    doc = locks.docs[0]
    assert doc.get("ce_exited") is True, "only the GONE leg is marked exited"
    assert doc["done_for_day"] is False, \
        "whole-doc done must NOT fire while the sibling leg is still open"


def test_b7_lazy_leg_rehydrates_with_lazy_params():
    locks = _Locks()
    run(locks.insert_one(_both_lock(
        ce_triggered=True, ce_entered_norenordno="N1", ce_entry_premium=115.0,
        ce_exited=True,
        lazy_armed_pe=True, lpe_instrument_key="NSE_FO|2002",
        lpe_entered_norenordno="N3", lpe_entry_premium=88.0)))
    # NOTE deliberately NO lpe_tsym: production never writes it (review
    # Finding 2) — the Noren symbol comes ONLY from the order-book join.
    db = _DB(locks, list(_B7_DEP))
    reg = _Reg()
    book = {"NIFTY10JUL26P23900N": {"tsym": "NIFTY10JUL26P23900N", "netqty": "65",
                                    "exch": "NFO", "lp": "92.0"}}
    out = run(rehydrate_premium_momentum(
        db, reg, book, noren_tsym_by_ordno={"N3": "NIFTY10JUL26P23900N"}))
    assert out["reattached"] == 1 and out["errors"] == 0
    kw = reg.calls[0]
    assert kw["key"] == "N3" and kw["tsym"] == "NIFTY10JUL26P23900N"
    assert kw["entry_price"] == 88.0, "lazy leg rehydrates with ITS persisted entry"
    # reviewer minor: pin that the LAZY stop actually reached the monitor state
    assert kw["state"].get("stop_level") == 88.0 * (1 - 12.0 / 100.0),         "lazy_stop_pct (12%) must drive the rehydrated monitor state"


def test_b7_legacy_single_leg_docs_rehydrate_byte_identically():
    """The pre-5B doc shape must flow through the EXACT legacy path (whole-doc
    exited_while_down close when gone; same registration otherwise) — pinned by
    the pre-existing tests above; this adds the mixed-population case."""
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D1", "ce", CE, "N1", 115.0)))
    run(locks.insert_one(_both_lock("D2",
        pe_triggered=True, pe_entered_norenordno="N9", pe_entry_premium=95.0)))
    db = _DB(locks, [{"id": "D1", "params": {"stop_pct": 30.0}, "risk": {}},
                     {"id": "D2", "params": {"leg_mode": "both", "stop_pct": 30.0},
                      "risk": {}}])
    reg = _Reg()
    book = {NOREN_CE: {"tsym": NOREN_CE, "netqty": "65",
                       "exch": "NFO", "lp": "118.0"},
            NOREN_PE: {"tsym": NOREN_PE, "netqty": "65",
                       "exch": "NFO", "lp": "96.0"}}
    out = run(rehydrate_premium_momentum(
        db, reg, book, noren_tsym_by_ordno={"N1": NOREN_CE, "N9": NOREN_PE}))
    assert out["reattached"] == 2 and out["errors"] == 0
    assert sorted(c["key"] for c in reg.calls) == ["N1", "N9"]



def test_b7_unresolved_ordno_skips_never_falsely_exits():
    """Review Finding 1's safety rule: a leg whose norenordno does NOT resolve
    through the order-book join must be SKIPPED (left to the generic
    rehydrate) — never matched by the (Upstox) contract symbol and NEVER
    marked exited/done on an unresolvable symbol. Before the fix, the Upstox
    trading_symbol was matched against the Noren-keyed book, every open leg
    read \"gone\", and a session with real money open was falsely finalized."""
    locks = _Locks()
    run(locks.insert_one(_both_lock(
        ce_triggered=True, ce_entered_norenordno="N1", ce_entry_premium=115.0)))
    db = _DB(locks, list(_B7_DEP))
    reg = _Reg()
    # Position IS open at the broker (Noren-keyed) — but the join map is empty
    # (order-book read failed): resolution is impossible.
    book = {NOREN_CE: {"tsym": NOREN_CE, "netqty": "65"}}
    out = run(rehydrate_premium_momentum(db, reg, book, noren_tsym_by_ordno={}))
    assert out["reattached"] == 0 and out["closed"] == 0 and out["errors"] == 0
    assert reg.calls == []
    doc = locks.docs[0]
    assert "ce_exited" not in doc, "unresolvable symbol must NEVER mark a leg exited"
    assert doc["done_for_day"] is False
