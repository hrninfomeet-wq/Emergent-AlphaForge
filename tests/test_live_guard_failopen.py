"""Item #6 — guard fail-open + gate-split (audit L20 / L21 / L13), adapted to
main's Layer-1 confirm-flat guard (entries are never removed before a square).

HOST tests (no motor):
  • L20  a non-confirming square keeps the entry watched and retries (bounded);
         on exhaustion the guard ESCALATES and STOPS re-issuing (square_stopped)
         but the entry STAYS registered — the OCO backstop + the confirmed-flat
         finalize remain. Dry-run keeps it visible but fires exactly once.
  • L21  a never-filled age-out cancels the carried resting OCO so it can't rest
         orphaned (a confirmed-flat drop cancels its OCO via _finalize_flat; a
         retry/exhaustion stop KEEPS the OCO as the deliberate backstop).
  • L13  SessionStore.arm pins the armed contract's tsym/exch.
  • gate-split  compute_arm_state flags autoplace-armed + guard-dry-run loudly.

Every guard here pins now_fn pre-EOD — on timer-less main the 15:00 IST EOD
square covers MANUAL positions too, so an unpinned clock would fire it in any
test run after 15:00 IST.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.arm_state import compute_arm_state  # noqa: E402
from app.live.live_position_guard import (  # noqa: E402
    LiveMonitorRegistry,
    LivePositionGuard,
)
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402
from app.live.session_store import SessionStore  # noqa: E402


def run(coro):
    return asyncio.run(coro)


async def _aw(v):
    return v


_TSYM = "SENSEX26JUN76500CE"
# 11:30 IST — safely before the 15:00 IST EOD square.
_NOW = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)


class _FakeClient:
    def __init__(self, positions):
        self._positions = positions

    def set(self, positions):
        self._positions = positions

    async def position_book(self):
        return list(self._positions)


class _OcoClient(_FakeClient):
    """position_book fake that also records cancel_oco calls."""

    def __init__(self, positions):
        super().__init__(positions)
        self.cancel_oco_calls = []

    async def cancel_oco(self, al_id):
        self.cancel_oco_calls.append(str(al_id))
        return {"ok": True}


class _ScriptedSquare:
    """square_fn returning a fixed result; counts calls."""

    def __init__(self, result):
        self._result = dict(result)
        self.calls = 0

    async def square_fn(self, client, position, *, reason):
        self.calls += 1
        return dict(self._result)


def _pos(netqty=20, lp=170.0, tsym=_TSYM):
    return {"tsym": tsym, "exch": "BFO", "netqty": str(netqty), "lp": str(lp), "urmtom": "0"}


def _registered(reg, *, oco_al_id=None, entry=250.0, stop_pct=30):
    state = build_monitor_state(entry, stop_pct=stop_pct)  # stop = 175
    reg.register(key="ORD1", tsym=_TSYM, exch="BFO", qty=20, prd="I",
                 entry_price=entry, state=state, oco_al_id=oco_al_id)
    return reg


# ---------------------------------------------------------------------------
# L20 — re-register on a non-confirming square
# ---------------------------------------------------------------------------

def test_confirmed_square_marks_squaring_and_finalizes_on_flat():
    # Layer-1 semantics: a broker-ACCEPTED square KEEPS the entry (squaring=True)
    # until the book confirms flat; only then is it finalized (dropped).
    reg = LiveMonitorRegistry()
    _registered(reg)
    client = _FakeClient([_pos(lp=170.0)])   # below stop → breach
    sq = _ScriptedSquare({"squared": True})
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, now_fn=lambda: _NOW)
    exits = run(g._cycle())
    assert sq.calls == 1
    assert len(exits) == 1
    assert len(reg) == 1                      # KEPT until confirmed flat
    assert reg.get("ORD1")["squaring"] is True
    client.set([_pos(netqty=0, lp=170.0)])    # broker confirms flat
    run(g._cycle())
    run(g._cycle())                           # 2nd consecutive flat read
    assert len(reg) == 0                      # finalized


def test_failed_square_keeps_watching_with_retry_counter():
    reg = LiveMonitorRegistry()
    _registered(reg)
    client = _FakeClient([_pos(lp=170.0)])
    sq = _ScriptedSquare({"squared": False})  # real failure (not dry_run)
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, now_fn=lambda: _NOW)
    exits = run(g._cycle())
    assert len(reg) == 1                       # KEPT under guard (never removed)
    assert reg.get("ORD1")["squaring"] is False  # failure → not squaring; retried
    assert reg.get("ORD1")["square_retries"] == 1
    assert len(exits) == 1                     # attempt recorded for diagnostics
    assert exits[0]["result"]["squared"] is False
    assert g.status()["exits"] == 0            # stat counts only ACCEPTED squares


def test_failed_square_retries_then_escalates_and_stops():
    # Exhaustion STOPS re-issuing (square_stopped) but KEEPS the entry registered:
    # the broker OCO remains the backstop and a confirmed flat still finalizes.
    reg = LiveMonitorRegistry()
    _registered(reg)
    client = _FakeClient([_pos(lp=170.0)])
    sq = _ScriptedSquare({"squared": False})
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, max_square_retries=2,
                          now_fn=lambda: _NOW)
    run(g._cycle())                            # retry 1 → kept
    assert reg.get("ORD1")["square_retries"] == 1 and len(reg) == 1
    run(g._cycle())                            # retry 2 == budget → ESCALATE + STOP
    assert reg.get("ORD1")["square_retries"] == 2 and len(reg) == 1
    assert reg.get("ORD1")["square_stopped"] is True
    assert g.status()["escalations"] == 1
    assert "square exhausted" in (g.status()["last_escalation"] or "")
    assert g.status()["stuck"] == 1            # surfaced, never silent
    run(g._cycle())                            # stopped → NOT re-issued
    assert sq.calls == 2
    assert len(reg) == 1                       # still watched (finalize still applies)
    # The position eventually reads flat (OCO fired / closed manually) → finalized.
    client.set([_pos(netqty=0, lp=170.0)])
    run(g._cycle()); run(g._cycle())
    assert len(reg) == 0


def test_contention_rereregisters_without_counting_toward_exhaustion():
    # Another exit path holds the per-tsym claim (item #3 mutex) → square_fn returns
    # squared=False, reason="exit_in_flight_elsewhere". The guard keeps watching +
    # retries but must NOT count this toward exhaustion (a legit concurrent exit
    # must never force-drop a still-open position).
    reg = LiveMonitorRegistry()
    _registered(reg)
    client = _FakeClient([_pos(lp=170.0)])
    sq = _ScriptedSquare({"squared": False, "reason": "exit_in_flight_elsewhere"})
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, max_square_retries=2,
                          now_fn=lambda: _NOW)
    for _ in range(5):                            # 5 cycles of pure contention
        run(g._cycle())
    assert len(reg) == 1                          # still watched
    assert reg.get("ORD1").get("square_retries", 0) == 0   # NOT counted
    assert g.status()["escalations"] == 0         # never escalated despite > max
    assert sq.calls == 5


def test_dry_run_square_keeps_visible_and_fires_once():
    reg = LiveMonitorRegistry()
    _registered(reg)
    client = _FakeClient([_pos(lp=170.0)])
    sq = _ScriptedSquare({"squared": False, "dry_run": True})
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, now_fn=lambda: _NOW)
    exits1 = run(g._cycle())
    assert sq.calls == 1
    assert len(reg) == 1                        # kept visible in guard_status
    assert reg.get("ORD1")["dry_run_exit_logged"] is True
    assert len(exits1) == 1                     # intent surfaced once
    exits2 = run(g._cycle())                    # next cycle: NOT re-fired
    assert sq.calls == 1                        # square_fn not called again
    assert exits2 == []
    assert len(reg) == 1                        # still watched (visible)


# ---------------------------------------------------------------------------
# Cross-path idempotency (review hardening): a re-added entry must not be squared
# AGAIN by a later path (overall-basket / EOD) in the SAME cycle, and a dry-run
# must not re-fire via those paths every cycle.
# ---------------------------------------------------------------------------

def test_same_cycle_second_square_is_noop():
    # Simulates the premium loop re-adding a failed entry, then the overall-basket
    # or EOD path processing it AGAIN in the same cycle: the 2nd call must no-op so
    # the retry budget burns ~1×/cycle, not 2-3×.
    reg = LiveMonitorRegistry()
    _registered(reg)
    client = _FakeClient([_pos(lp=170.0)])
    sq = _ScriptedSquare({"squared": False})   # real fail → re-adds
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, max_square_retries=5,
                          now_fn=lambda: _NOW)
    g._cycle_token = 7
    run(g._issue_square(client, reg.get("ORD1"), "software_stop", "stop", [], _NOW))
    assert sq.calls == 1 and reg.get("ORD1")["square_retries"] == 1
    # a SECOND path in the SAME cycle (overall/EOD) re-hits the failed entry:
    run(g._issue_square(client, reg.get("ORD1"), "eod_square", "eod_square", [], _NOW))
    assert sq.calls == 1                                 # skipped (already this cycle)
    assert reg.get("ORD1")["square_retries"] == 1        # budget not double-burned
    # NEXT cycle → eligible again
    g._cycle_token = 8
    run(g._issue_square(client, reg.get("ORD1"), "software_stop", "stop", [], _NOW))
    assert sq.calls == 2 and reg.get("ORD1")["square_retries"] == 2


def test_dry_run_not_refired_via_eod_cross_path():
    # A DEPLOYED dry-run position at/after EOD: the EOD path fires the dry-run once,
    # then the cross-path guard suppresses re-firing every subsequent cycle (the
    # per-position `continue` alone did not cover EOD / overall-basket).
    reg = LiveMonitorRegistry()
    state = build_monitor_state(250.0, stop_pct=30)
    reg.register(key="D1", tsym=_TSYM, exch="BFO", qty=20, prd="I",
                 entry_price=250.0, state=state, source="auto_live")
    client = _FakeClient([_pos(netqty=20, lp=240.0)])   # above stop (no premium exit)
    sq = _ScriptedSquare({"squared": False, "dry_run": True})
    now_eod = datetime(2026, 7, 9, 9, 35, tzinfo=timezone.utc)  # 15:05 IST
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, now_fn=lambda: now_eod)
    run(g._cycle())                                      # EOD dry-run fires once
    assert sq.calls == 1 and reg.get("D1")["dry_run_exit_logged"] is True
    run(g._cycle())                                      # cross-path guard: NOT re-fired
    run(g._cycle())
    assert sq.calls == 1
    assert len(reg) == 1                                 # still watched (visible)


def test_exits_stat_counts_only_real_squares():
    # A dry-run intent is recorded in the diagnostic list but must NOT inflate the
    # "exits" stat (which means "positions actually flattened").
    reg = LiveMonitorRegistry()
    _registered(reg)
    g = LivePositionGuard(
        registry=reg, client_factory=lambda: _aw(_FakeClient([_pos(lp=170.0)])),
        square_fn=_ScriptedSquare({"squared": False, "dry_run": True}).square_fn,
        now_fn=lambda: _NOW)
    run(g._cycle())
    assert g.status()["exits"] == 0                      # dry-run ≠ real flatten
    reg2 = LiveMonitorRegistry()
    _registered(reg2)
    g2 = LivePositionGuard(
        registry=reg2, client_factory=lambda: _aw(_FakeClient([_pos(lp=170.0)])),
        square_fn=_ScriptedSquare({"squared": True}).square_fn,
        now_fn=lambda: _NOW)
    run(g2._cycle())
    assert g2.status()["exits"] == 1                     # real square counted


# ---------------------------------------------------------------------------
# L21 — cancel the orphaned OCO on a NON-square drop; KEEP it on a retry drop
# ---------------------------------------------------------------------------

def test_flat_drop_cancels_orphaned_oco():
    # Closed-elsewhere: the confirmed-flat finalize (_finalize_flat) cancels the
    # orphaned OCO — same L21 outcome, owned by main's Layer-1 machinery.
    reg = LiveMonitorRegistry()
    _registered(reg, oco_al_id="OCO1")
    client = _OcoClient([_pos(netqty=20, lp=240.0)])  # live, above stop
    sq = _ScriptedSquare({"squared": True})
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, now_fn=lambda: _NOW)
    run(g._cycle())                              # seen_filled
    client.set([_pos(netqty=0, lp=240.0)])       # now flat (closed elsewhere)
    run(g._cycle())                              # flat_reads=1 (not yet dropped)
    assert client.cancel_oco_calls == []
    run(g._cycle())                              # flat_reads=2 → finalize + cancel OCO
    assert len(reg) == 0
    assert client.cancel_oco_calls == ["OCO1"]
    assert sq.calls == 0                          # never squared (closed elsewhere)


class _OcoOrderClient(_OcoClient):
    """_OcoClient that also records cancel_order calls (the age-out path now
    best-effort cancels the still-working ENTRY order before dropping)."""

    def __init__(self, positions):
        super().__init__(positions)
        self.cancel_order_calls = []

    async def cancel_order(self, norenordno):
        self.cancel_order_calls.append(str(norenordno))
        return {"stat": "Ok"}


def test_age_out_cancels_entry_order_and_orphaned_oco():
    # The book must be KNOWN (non-empty, other scrip) for misses to advance — an
    # empty book is UNKNOWN and never ages anything out. On age-out the guard now
    # cancels the possibly-still-working ENTRY order (its id IS the norenordno)
    # AND the resting OCO, so a late fill can't arrive unwatched with a live OCO.
    reg = LiveMonitorRegistry()
    _registered(reg, oco_al_id="OCO2")
    client = _OcoOrderClient([_pos(netqty=10, lp=50.0, tsym="SOMEOTHER")])
    sq = _ScriptedSquare({"squared": True})
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, max_pending_misses=2,
                          now_fn=lambda: _NOW)
    run(g._cycle())                              # misses=1
    assert client.cancel_oco_calls == []
    run(g._cycle())                              # misses=2 → age-out drop + cancels
    assert len(reg) == 0
    assert client.cancel_order_calls == ["ORD1"]  # entry order cancelled first
    assert client.cancel_oco_calls == ["OCO2"]


def test_age_out_never_advances_on_unknown_book():
    reg = LiveMonitorRegistry()
    _registered(reg, oco_al_id="OCO2")
    client = _OcoClient([])                       # EMPTY == UNKNOWN
    sq = _ScriptedSquare({"squared": True})
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, max_pending_misses=2,
                          now_fn=lambda: _NOW)
    for _ in range(6):                            # 3x the grace window
        run(g._cycle())
    assert len(reg) == 1                          # never aged out
    assert client.cancel_oco_calls == []


def test_retry_exhaustion_stop_keeps_oco_as_backstop():
    reg = LiveMonitorRegistry()
    _registered(reg, oco_al_id="OCO3")
    client = _OcoClient([_pos(lp=170.0)])        # breach; square keeps failing
    sq = _ScriptedSquare({"squared": False})
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, max_square_retries=1,
                          now_fn=lambda: _NOW)
    run(g._cycle())                              # retry 1 == budget → escalate + STOP
    assert len(reg) == 1                         # still registered (watched)
    assert reg.get("ORD1")["square_stopped"] is True
    run(g._cycle())                              # stopped → not re-issued
    assert sq.calls == 1
    assert client.cancel_oco_calls == []          # OCO deliberately kept (backstop)


# ---------------------------------------------------------------------------
# L13 — SessionStore pins the armed contract
# ---------------------------------------------------------------------------

class _FakeCol:
    def __init__(self):
        self.docs = {}

    async def find_one(self, q):
        _id = q.get("_id")
        return dict(self.docs[_id]) if _id in self.docs else None

    async def update_one(self, q, upd, upsert=False):
        _id = q.get("_id")
        if _id not in self.docs and upsert:
            self.docs[_id] = {}
        if _id in self.docs and "$set" in upd:
            self.docs[_id].update(upd["$set"])
        return type("R", (), {"matched_count": 1})()


def test_arm_pins_tsym_and_exch():
    ss = SessionStore(_FakeCol())
    run(ss.arm(entry_norenordno="E1", tsym="NIFTY26JUN24000CE", exch="NFO"))
    got = run(ss.get())
    assert got["tsym"] == "NIFTY26JUN24000CE"
    assert got["exch"] == "NFO"


def test_arm_without_tsym_is_none_for_legacy_compat():
    ss = SessionStore(_FakeCol())
    run(ss.arm(entry_norenordno="E1"))
    got = run(ss.get())
    assert got["tsym"] is None and got["exch"] is None


# ---------------------------------------------------------------------------
# L13 — _select_session_position targets THIS session's contract by tsym
# ---------------------------------------------------------------------------

from app.routers.live_broker import _select_session_position  # noqa: E402


def test_select_prefers_pinned_tsym_even_when_deployed_row_is_first():
    # The dangerous case: a co-existing DEPLOYED position is the FIRST open row.
    # The old "first non-zero" heuristic would flatten it; the fix picks OUR tsym.
    positions = [
        {"tsym": "DEPLOYED_CE", "exch": "NFO", "netqty": "50"},
        {"tsym": "MYSHOT_PE", "exch": "NFO", "netqty": "20"},
    ]
    got = _select_session_position(positions, {"tsym": "MYSHOT_PE", "exch": "NFO"})
    assert got is not None and got["tsym"] == "MYSHOT_PE"


def test_select_legacy_no_tsym_falls_back_to_first_open():
    positions = [
        {"tsym": "AAA", "exch": "NFO", "netqty": "0"},
        {"tsym": "BBB", "exch": "NFO", "netqty": "20"},
    ]
    got = _select_session_position(positions, {"tsym": None, "exch": None})
    assert got is not None and got["tsym"] == "BBB"


def test_select_pinned_contract_flat_returns_none():
    positions = [
        {"tsym": "DEPLOYED_CE", "exch": "NFO", "netqty": "50"},
        {"tsym": "MYSHOT_PE", "exch": "NFO", "netqty": "0"},   # our contract is flat
    ]
    got = _select_session_position(positions, {"tsym": "MYSHOT_PE", "exch": "NFO"})
    assert got is None            # ⇒ caller marks THIS session squared, leaves the deployed row


def test_select_exch_mismatch_still_matches_sole_tsym():
    # exch must never EXCLUDE the sole tsym match (broker exch-string quirk must
    # not fail-open into a false "flat").
    positions = [{"tsym": "MYSHOT_PE", "exch": "BFO", "netqty": "20"}]
    got = _select_session_position(positions, {"tsym": "MYSHOT_PE", "exch": "NFO"})
    assert got is not None and got["tsym"] == "MYSHOT_PE"


# ---------------------------------------------------------------------------
# gate-split — compute_arm_state flags real-entries + dry-run-exits loudly
# ---------------------------------------------------------------------------

def test_gate_split_cannot_occur_in_any_configuration():
    """Audit L20 regression pin, strengthened.

    L20 was the DANGEROUS SPLIT: LIVE_AUTOPLACE_ARMED=1 with LIVE_GUARD_ARMED=0 —
    real automated entries opening while the unattended guard only LOGGED its
    squares, leaving the broker OCO as the sole automated backstop.

    That gate is gone; the guard always transmits. So rather than asserting the
    warning fires correctly, we now assert the far stronger property: there is NO
    input combination that can produce the split at all. If someone ever re-adds an
    exit-side gate, this test fails.
    """
    for connected in (True, False):
        for autoplace in (True, False):
            for n in (0, 1, 5):
                st = compute_arm_state(
                    mode_doc={"mode": "PAPER"}, connected=connected,
                    autoplace_armed=autoplace, armed_deployment_count=n)
                assert st["exit_gap"] is False
                assert st["warning"] is None
                # The only thing that can stop an auto-square is broker reachability,
                # never a configuration flag.
                assert st["would_transmit_exit"] is bool(connected)
                # And entries must never transmit while exits cannot.
                if st["would_transmit_entry"]:
                    assert st["would_transmit_exit"] is True


# ---------------------------------------------------------------------------
# Review fixes — soft-failure classification, EOD bypass, accepted-square reset
# ---------------------------------------------------------------------------

def test_cancel_unconfirmed_is_soft_never_burns_budget():
    """cancel_unconfirmed placed NOTHING (unreadable order book / surviving
    working order) — ~40s of an order-book blip must not permanently stop the
    guard, so it never counts toward the retry budget (review fix)."""
    reg = LiveMonitorRegistry()
    _registered(reg)
    client = _FakeClient([_pos(lp=170.0)])
    sq = _ScriptedSquare({"squared": False, "reason": "cancel_unconfirmed"})
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, max_square_retries=2,
                          now_fn=lambda: _NOW)
    for _ in range(6):                            # 3x the budget
        run(g._cycle())
    assert len(reg) == 1                          # still watched
    assert reg.get("ORD1").get("square_retries", 0) == 0
    assert not reg.get("ORD1").get("square_stopped")
    assert g.status()["escalations"] == 0
    assert sq.calls == 6                          # kept retrying every cycle


def test_eod_still_attempts_a_square_stopped_entry():
    """square_stopped halts DISCRETIONARY squares, but the 15:00 EOD backstop must
    keep attempting — a no-OCO manual/rehydrated position has no other automated
    exit left (review fix: square_stopped must not gate the EOD square)."""
    from datetime import datetime, timezone
    reg = LiveMonitorRegistry()
    _registered(reg)
    entry = reg.get("ORD1")
    entry["seen_filled"] = True
    entry["square_stopped"] = True                # budget exhausted earlier
    entry["square_retries"] = 99
    client = _FakeClient([_pos(netqty=20, lp=240.0)])   # above stop (no breach)
    sq = _ScriptedSquare({"squared": False})
    now_eod = datetime(2026, 7, 9, 9, 35, tzinfo=timezone.utc)  # 15:05 IST
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, now_fn=lambda: now_eod)
    run(g._cycle())
    assert sq.calls == 1                          # EOD attempted despite stopped
    run(g._cycle())
    assert sq.calls == 2                          # and keeps attempting per cycle


def test_accepted_square_resets_retry_bookkeeping():
    """A broker-ACCEPTED square proves the path works — the failure counters from
    an earlier bad spell must not linger (review fix)."""
    reg = LiveMonitorRegistry()
    _registered(reg)
    entry = reg.get("ORD1")
    entry["square_retries"] = 7                   # earlier bad spell
    client = _FakeClient([_pos(lp=170.0)])        # breach
    sq = _ScriptedSquare({"squared": True, "norenordno": "EXIT1", "qty": 20})
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=sq.square_fn, now_fn=lambda: _NOW)
    run(g._cycle())
    e = reg.get("ORD1")
    assert e["squaring"] is True
    assert e["square_retries"] == 0               # reset on accept
    assert e.get("square_stopped") in (False, None)
    assert e["square_qty"] == 20                  # seeded from the executor's qty
