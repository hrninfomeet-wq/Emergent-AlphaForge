"""An accepted OCO is not a resting OCO.

2026-08-14, real 1-lot live trade. `PlaceOCOOrder` returned ok with
al_id 26081400000991, so the journal recorded `oco_al_id` set and
`oco_error: null` — i.e. "you have a broker backstop". The broker then REJECTED
the resting leg asynchronously:

    RED:Margin Shortfall:INR 101830.33 Available:INR 86932.68

The place-time code was correct — it only sets `oco_al_id` when `res["ok"]`. The
gap is that acceptance and survival are different facts, and nothing ever checked
the second one. The GTT book was empty while the journal still claimed a backstop.

This is structural on this account, not a tuning problem: a RESTING NRML sell is
margined by the broker as a potential naked short (it cannot know the long will
still exist when the trigger fires), so protecting a Rs 4,010 position demanded
Rs 1,01,830 of span margin. The software guard is the real protection; the UI must
say so.

The surfaces to light up ALREADY EXIST — `auto_live` writes
`oco_error="no_broker_backstop"` whenever `oco_al_id` is falsy, and
`AlertRail.jsx` renders "N live positions have no broker backstop
(software-guard-only)". They stayed dark only because `oco_al_id` was set. So the
fix is to tell the truth about the al_id, not to build a new surface.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.oco_verify import unbacked_norenordnos  # noqa: E402

_OK = {"norenordno": "N1", "status": "OPEN", "oco_al_id": "AL1"}


def _gtt(al_id, **over):
    row = {"al_id": al_id, "tsym": "NIFTY18AUG26P24300"}
    row.update(over)
    return row


def test_a_resting_oco_is_left_alone():
    assert unbacked_norenordnos([_OK], [_gtt("AL1")]) == []


def test_an_al_id_absent_from_the_book_is_unbacked():
    """THE 2026-08-14 case: accepted, then the leg was rejected for margin."""
    assert unbacked_norenordnos([_OK], []) == ["N1"]


def test_a_trade_with_no_al_id_is_not_reported_again():
    """auto_live already journalled no_broker_backstop for it — no churn."""
    t = {"norenordno": "N2", "status": "OPEN", "oco_al_id": None}
    assert unbacked_norenordnos([t], []) == []


def test_closed_trades_are_ignored():
    t = {"norenordno": "N3", "status": "CLOSED", "oco_al_id": "AL9"}
    assert unbacked_norenordnos([t], []) == []


def test_an_UNREADABLE_book_reports_nothing():
    """A failed GetPendingGTTOrder is UNKNOWN, not 'no backstop'. Clearing a real
    al_id on a transient read failure would lose the handle used to CANCEL it."""
    assert unbacked_norenordnos([_OK], None) == []


def test_alternate_id_field_names_still_match():
    """Noren wraps a stored GTT on read; do not miss a live backstop over a key name."""
    for key in ("al_id", "alert_id", "id"):
        assert unbacked_norenordnos([_OK], [{key: "AL1"}]) == [], key


def test_ids_compare_as_strings():
    assert unbacked_norenordnos([{"norenordno": "N1", "status": "OPEN",
                                  "oco_al_id": 12345}], [_gtt(12345)]) == []


def test_several_trades_are_partitioned_correctly():
    trades = [
        {"norenordno": "A", "status": "OPEN", "oco_al_id": "AL1"},   # resting
        {"norenordno": "B", "status": "OPEN", "oco_al_id": "AL2"},   # gone
        {"norenordno": "C", "status": "OPEN", "oco_al_id": None},    # already known
    ]
    assert unbacked_norenordnos(trades, [_gtt("AL1")]) == ["B"]


# --- the check must be MOUNTED, and must not cost a per-cycle broker call ---

def _runtime_src():
    from pathlib import Path
    return (Path(__file__).resolve().parents[1]
            / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")


def test_the_backstop_check_runs_in_the_supervisor():
    """A check nobody calls verifies nothing — the on_expire lesson, re-pinned."""
    import ast
    src = _runtime_src()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_risk_supervisor_loop")
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "unbacked_norenordnos" in called, (
        "the OCO survival check is defined but never runs — the journal would keep "
        "claiming a backstop that was rejected for margin")
    assert "gtt_book" in called


def test_it_does_not_run_inside_the_15s_guard_cycle():
    """Rate budget: the guard cycles ~1.5s. Polling the GTT book there would be
    ~40x the broker calls for a fact that changes rarely."""
    from pathlib import Path
    guard = (Path(__file__).resolve().parents[1] / "backend" / "app" / "live"
             / "live_position_guard.py").read_text(encoding="utf-8")
    assert "unbacked_norenordnos" not in guard
    assert "gtt_book" not in guard


def test_the_supervisor_never_places_or_cancels_an_order():
    """Unchanged invariant: supervision reports and blocks; it does not transact."""
    import ast
    src = _runtime_src()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_risk_supervisor_loop")
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(fn) if isinstance(n, ast.Call)}
    for forbidden in ("place_order", "place_oco", "cancel_order", "cancel_oco",
                      "square_position", "panic_squareoff"):
        assert forbidden not in called, f"supervisor called {forbidden}"
