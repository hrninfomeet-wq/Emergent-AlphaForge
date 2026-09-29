"""CONFIRMED signals nothing will ever act on must reach a terminal state.

Verified against the code (2026-09-29): a CONFIRMED signal is acted on ONLY inside the
evaluator pass for its own bar (`evaluate_active_deployments` routes that pass's fresh
results to the paper / live sink seconds after the bar closes). No route calls the
claim helpers (`claim_signal_for_paper_trade` has one caller, paper_auto), a refused sink
releases its claim but nothing retries, and a signal-only deployment has no sink at all.
So a CONFIRMED signal older than its pass reads "awaiting approval" for good.

`expire_unactioned_signals` moves such a signal CONFIRMED -> AUDITED
(`unactioned_bar_passed`). These tests EXECUTE it against an in-memory collection that
honours `$lt` / `$exists` / conditional replace, and pin what it must NEVER touch.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.signal_lifecycle import (  # noqa: E402
    UNACTIONED_AFTER_MINUTES, UNACTIONED_REASON, create_signal_doc,
    expire_unactioned_signals, transition_signal)

NOW = datetime(2026, 9, 29, 9, 30, tzinfo=timezone.utc)          # 15:00 IST


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _get(row: Dict[str, Any], dotted: str) -> Any:
    cur: Any = row
    for part in dotted.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def _match(row: Dict[str, Any], query: Dict[str, Any], honour_exists: bool = True) -> bool:
    for k, v in (query or {}).items():
        if k == "$or":
            if not any(_match(row, sub, honour_exists) for sub in v):
                return False
            continue
        rv = _get(row, k)
        if isinstance(v, dict) and "$lt" in v:
            if rv is None or not rv < v["$lt"]:
                return False
        elif isinstance(v, dict) and "$exists" in v:
            # honour_exists=False models a write that CANNOT guard on the absence of a
            # claim: the read-time pre-check must then be the only thing protecting it.
            if honour_exists and (k in row) != bool(v["$exists"]):
                return False
        elif rv != v:
            return False
    return True


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length=None):
        rows = self._rows if length is None else self._rows[: int(length)]
        return [dict(r) for r in rows]


class _Signals:
    def __init__(self, rows=None):
        self.rows: List[Dict[str, Any]] = [dict(r) for r in (rows or [])]
        self.fail_replace = False
        self.honour_exists = True
        self.before_replace = None      # a racing writer lands here
        self.queries: List[Dict[str, Any]] = []

    def find(self, query=None, projection=None):
        self.queries.append(query)
        return _Cur([r for r in self.rows if _match(r, query or {})])

    async def replace_one(self, query, doc, upsert=False):
        if self.fail_replace:
            raise RuntimeError("replace boom")
        if self.before_replace:
            self.before_replace(self.rows)
        for i, r in enumerate(self.rows):
            if _match(r, query, self.honour_exists):
                self.rows[i] = dict(doc)
                return type("Res", (), {"matched_count": 1})()
        return type("Res", (), {"matched_count": 0})()


class _DB:
    def __init__(self, rows=None):
        self.signals = _Signals(rows)


def _confirmed(sig_id="s1", *, bar_age_min=60, deployment_id="D1", **extra):
    """The REAL signal-doc shape: the bar is `candle_ts` + `context.candle.ts`.
    (`bar_ts` belongs to the evaluation AUDIT record; no real signal carries it —
    fixtures that set it hid a sweep that moved nothing on the real database.)"""
    sig = create_signal_doc(instrument="NIFTY", direction="CE", strategy_id="s",
                            entry_price=25000.0, confidence=70)
    sig["id"] = sig_id
    sig["deployment_id"] = deployment_id
    bar = _ms(NOW - timedelta(minutes=bar_age_min))
    sig["candle_ts"] = bar
    sig["context"] = {"candle": {"ts": bar, "ist_time": "14:00"}}
    sig["blocked"] = False
    sig = transition_signal(sig, "FORMING", reason="t")
    sig = transition_signal(sig, "CONFIRMED", reason="passed")
    sig.update(extra)
    return sig


def _run(db, **kw):
    return asyncio.run(expire_unactioned_signals(db, now_utc=NOW, **kw))


def _state(db, sig_id="s1"):
    return next(r for r in db.signals.rows if r["id"] == sig_id)


# --------------------------------------------------------------------------- #
# what it moves
# --------------------------------------------------------------------------- #

def test_an_old_unactioned_signal_becomes_audited_with_the_reason():
    db = _DB([_confirmed(bar_age_min=60)])
    assert _run(db) == 1
    s = _state(db)
    assert s["state"] == "AUDITED" and s["audited_at"] == NOW.isoformat()
    last = s["events"][-1]
    assert (last["from_state"], last["to_state"], last["reason"]) == (
        "CONFIRMED", "AUDITED", UNACTIONED_REASON)
    assert last["snapshot"]["expired_after_minutes"] == UNACTIONED_AFTER_MINUTES


def test_a_signal_from_an_earlier_day_moves():
    db = _DB([_confirmed(bar_age_min=60 * 24 * 5)])
    assert _run(db) == 1 and _state(db)["state"] == "AUDITED"


def test_a_refused_signal_with_its_error_field_still_moves_and_keeps_the_error():
    """A refused sink released its claim; nothing retries. The refusal is evidence and
    must survive the transition."""
    db = _DB([_confirmed(live_trade_error="live_entry_premium_unavailable_or_stale",
                         paper_trade_skip="capital_gate")])
    assert _run(db) == 1
    s = _state(db)
    assert s["state"] == "AUDITED"
    assert s["live_trade_error"] == "live_entry_premium_unavailable_or_stale"
    assert s["paper_trade_skip"] == "capital_gate"


def test_it_is_idempotent():
    db = _DB([_confirmed()])
    assert _run(db) == 1
    assert _run(db) == 0


# --------------------------------------------------------------------------- #
# what it must NEVER touch
# --------------------------------------------------------------------------- #

def test_a_signal_the_evaluator_may_still_route_is_never_touched():
    """The bar just closed: the pass that routes it is running right now."""
    for age in (0, 1, 5, 14):
        db = _DB([_confirmed(bar_age_min=age)])
        assert _run(db) == 0, f"expired a {age}-minute-old signal"
        assert _state(db)["state"] == "CONFIRMED"


def test_the_cushion_boundary_is_exact():
    just_inside = _DB([_confirmed(bar_age_min=UNACTIONED_AFTER_MINUTES - 0.01)])
    just_past = _DB([_confirmed(bar_age_min=UNACTIONED_AFTER_MINUTES + 0.01)])
    assert _run(just_inside) == 0
    assert _run(just_past) == 1


def test_a_claimed_signal_is_left_alone_a_sink_is_mid_flight_or_crashed():
    """paper_trade_claim: a sink claimed it. If it crashed between the trade insert and
    the signal write, the trade may exist — expiring the signal would lie about it."""
    db = _DB([_confirmed(paper_trade_claim={"source": "auto_live", "at": "x"})])
    assert _run(db) == 0 and _state(db)["state"] == "CONFIRMED"


def test_a_signal_linked_to_a_trade_is_left_alone():
    for field in ("paper_trade_id", "live_trade_id"):
        db = _DB([_confirmed(**{field: "t1"})])
        assert _run(db) == 0, field
        assert _state(db)["state"] == "CONFIRMED"


def test_only_confirmed_signals_are_considered():
    db = _DB([_confirmed("a")])
    for i, st in enumerate(("WATCHING", "FORMING", "TRIGGERED", "ACTIVE", "EXITED",
                            "SKIPPED", "AUDITED")):
        s = _confirmed(f"x{i}")
        s["state"] = st
        db.signals.rows.append(s)
    assert _run(db) == 1
    assert {r["id"]: r["state"] for r in db.signals.rows if r["id"] != "a"} == {
        "x0": "WATCHING", "x1": "FORMING", "x2": "TRIGGERED", "x3": "ACTIVE",
        "x4": "EXITED", "x5": "SKIPPED", "x6": "AUDITED"}


def test_a_blocked_signal_and_a_manual_signal_are_ignored():
    blocked = _confirmed("b", blocked=True)
    manual = _confirmed("m")
    del manual["deployment_id"]
    db = _DB([blocked, manual])
    assert _run(db) == 0
    assert all(r["state"] == "CONFIRMED" for r in db.signals.rows)


def test_a_signal_whose_bar_time_is_unreadable_is_skipped_never_expired():
    junk = _confirmed("j")
    junk["candle_ts"] = "garbage"
    junk["context"] = {"candle": {"ts": "garbage"}}
    missing = _confirmed("k")
    del missing["candle_ts"]
    missing["context"] = {}
    db = _DB([junk, missing])
    assert _run(db) == 0


def test_the_real_signal_shape_moves_by_candle_ts():
    """A CONFIRMED signal exactly as the evaluator writes it (sampled from the real
    database 2026-09-29): `candle_ts` and `context.candle.ts`, `bar_ts` absent."""
    sig = _confirmed("real", bar_age_min=90)
    assert "bar_ts" not in sig
    db = _DB([sig])
    assert _run(db) == 1 and _state(db, "real")["state"] == "AUDITED"


def test_only_the_nested_candle_time_is_enough():
    sig = _confirmed("nested", bar_age_min=90)
    del sig["candle_ts"]
    db = _DB([sig])
    assert _run(db) == 1


def test_a_legacy_bar_ts_still_reads():
    sig = _confirmed("legacy", bar_age_min=90)
    del sig["candle_ts"]
    sig["context"] = {}
    sig["bar_ts"] = _ms(NOW - timedelta(minutes=90))
    db = _DB([sig])
    assert _run(db) == 1


def test_a_racing_sink_claim_is_never_overwritten():
    """The write is conditional on the ABSENCE of a claim: if a sink claims the signal
    between the read and the write, the replace must not land."""
    db = _DB([_confirmed()])

    def racer(rows):
        rows[0]["paper_trade_claim"] = {"source": "auto_paper", "at": "now"}

    db.signals.before_replace = racer
    assert _run(db) == 0
    s = _state(db)
    assert s["state"] == "CONFIRMED" and s["paper_trade_claim"]["source"] == "auto_paper"


def test_a_racing_state_change_is_never_overwritten():
    db = _DB([_confirmed()])

    def racer(rows):
        rows[0]["state"] = "TRIGGERED"

    db.signals.before_replace = racer
    assert _run(db) == 0 and _state(db)["state"] == "TRIGGERED"


def test_the_read_time_check_alone_protects_claimed_and_linked_signals():
    """Belt: with a write that could NOT guard on absence, a claimed / trade-linked
    signal must still be skipped by the pre-check."""
    for field, val in (("paper_trade_claim", {"source": "auto_live", "at": "x"}),
                       ("paper_trade_id", "t1"), ("live_trade_id", "t2")):
        db = _DB([_confirmed(**{field: val})])
        db.signals.honour_exists = False
        assert _run(db) == 0, field
        assert _state(db)["state"] == "CONFIRMED"


def test_the_write_condition_alone_protects_a_signal_claimed_or_linked_mid_sweep():
    """Braces: a claim / trade link that lands AFTER the read must stop the write."""
    for field, val in (("paper_trade_claim", {"source": "auto_paper", "at": "now"}),
                       ("paper_trade_id", "t1"), ("live_trade_id", "t2")):
        db = _DB([_confirmed()])
        db.signals.before_replace = lambda rows, f=field, v=val: rows[0].__setitem__(f, v)
        assert _run(db) == 0, field
        s = _state(db)
        assert s["state"] == "CONFIRMED" and s[field] == val


def test_the_reason_is_the_documented_literal():
    db = _DB([_confirmed()])
    _run(db)
    assert _state(db)["events"][-1]["reason"] == "unactioned_bar_passed"


# --------------------------------------------------------------------------- #
# it never raises into the sweep it rides on
# --------------------------------------------------------------------------- #

def test_a_failing_write_is_swallowed_and_counted_as_not_moved():
    db = _DB([_confirmed()])
    db.signals.fail_replace = True
    assert _run(db) == 0
    assert _state(db)["state"] == "CONFIRMED"


def test_one_bad_document_does_not_stop_the_rest():
    bad = _confirmed("bad")
    bad["events"] = 5                      # list(5) -> TypeError inside transition_signal
    good = _confirmed("good")
    db = _DB([bad, good])
    assert _run(db) == 1                   # only `good` moved
    assert _state(db, "good")["state"] == "AUDITED"
    assert _state(db, "bad")["state"] == "CONFIRMED"


def test_a_db_without_a_signals_collection_or_none_is_a_quiet_zero():
    assert asyncio.run(expire_unactioned_signals(None, now_utc=NOW)) == 0
    assert asyncio.run(expire_unactioned_signals(object(), now_utc=NOW)) == 0


def test_the_query_asks_only_for_confirmed_signals_older_than_the_cutoff():
    db = _DB([])
    _run(db)
    q = db.signals.queries[0]
    assert q["state"] == "CONFIRMED"
    cutoff = _ms(NOW - timedelta(minutes=UNACTIONED_AFTER_MINUTES))
    assert {"candle_ts": {"$lt": cutoff}} in q["$or"]


# --------------------------------------------------------------------------- #
# wired into the sweeps that already exist (no new timer)
# --------------------------------------------------------------------------- #

def test_the_15_00_sweep_in_the_evaluator_loop_runs_the_expiry(monkeypatch):
    """Drive one real scheduler cycle (the pattern of test_runtime_scheduled_squareoff)."""
    from app import runtime

    calls = []

    class _Friday1500:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 14, 9, 30, tzinfo=timezone.utc)

    class _Candles:
        async def find_one(self, *a, **k):
            return None

    class _Db:
        candles_1m = _Candles()

    sleeps = 0

    async def _sleep(_t):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError

    async def _squareoff(*a, **k):
        return []

    async def _expire(db, **kw):
        calls.append((db, kw))
        return 3

    import app.signal_lifecycle as sl
    monkeypatch.setattr(runtime, "datetime", _Friday1500)
    monkeypatch.setattr(runtime, "_evaluator_wait", _sleep)
    monkeypatch.setattr(runtime, "get_db", lambda: _Db())
    monkeypatch.setattr(runtime, "is_square_off_due", lambda _now: True)
    monkeypatch.setattr(runtime, "square_off_open_paper_trades", _squareoff)
    monkeypatch.setattr(runtime.upstox_stream_manager, "latest_tick_map", lambda: {})
    monkeypatch.setattr(sl, "expire_unactioned_signals", _expire)
    try:
        asyncio.run(runtime._deployment_evaluator_loop())
    except asyncio.CancelledError:
        pass
    assert len(calls) == 1, "the once-a-day sweep did not run the signal expiry"
    assert calls[0][1]["now_utc"] == datetime(2026, 8, 14, 9, 30, tzinfo=timezone.utc)


def test_the_boot_reconcile_also_runs_the_expiry():
    """server.py's startup is monolithic; pin that the boot reconcile calls the helper
    inside a try/except so a failure can never break startup."""
    src = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    i = src.index("expire_unactioned_signals")
    block = src[max(0, i - 300): i + 400]
    assert "try:" in block and "except Exception" in block
    assert "await expire_unactioned_signals(db)" in block
