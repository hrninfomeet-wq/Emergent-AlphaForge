"""Risk caps must be evaluated on a TIMER, not only when a new signal arrives.

`check_account_caps` and `check_live_caps` have exactly one caller each —
`auto_live`, the new-entry path. Neither background loop evaluates them. So a
held position running against the account all afternoon tripped nothing: the stop
could only fire on the arrival of a new signal, which is precisely what does not
happen while a position is being held. An unattended bot could bleed past its
configured limits indefinitely.

The decision is extracted as a PURE function so the supervision policy is
testable without a loop, a clock or a database — the loop that drives it stays
thin and does only I/O.

Scope note: supervision HALTS and PAUSES (blocks new entries). It never squares.
Flattening a position is the guard's job and a separate operator decision; a
timer must not acquire the power to place real orders.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_deploy_governor import evaluate_risk_supervision  # noqa: E402

_IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)      # 11:30 IST
TODAY = NOW.astimezone(_IST).date().isoformat()


def _row(dep, *, status="OPEN", unreal=0.0, real=0.0, mark_age_s=1.0):
    return {
        "deployment_id": dep,
        "status": status,
        "lots": 1,
        "realized_pnl": real,
        "unrealized_pnl": unreal,
        "created_at": NOW.isoformat(),
        "closed_at": NOW.isoformat() if status == "CLOSED" else None,
        "marked_at": (NOW - timedelta(seconds=mark_age_s)).isoformat(),
    }


def _dep(dep_id, cap):
    return {"id": dep_id, "status": "ACTIVE", "mode": "live",
            "risk": {"live": {"daily_loss_cap": cap}}}


ACCT = {"daily_loss_limit": 10000, "max_open_positions": 5}


def test_a_held_loser_halts_the_account_with_no_new_signal():
    """The whole point: nothing arrives, and the cap still fires."""
    out = evaluate_risk_supervision(
        rows=[_row("dep1", unreal=-12000)], deployments=[_dep("dep1", 50000)],
        account_config=ACCT, today=TODAY, now_utc=NOW)
    assert out["halt_account"] is True
    assert out["mtm"] == -12000


def test_a_per_deployment_cap_pauses_only_that_deployment():
    out = evaluate_risk_supervision(
        rows=[_row("dep1", unreal=-6000), _row("dep2", unreal=-100)],
        deployments=[_dep("dep1", 5000), _dep("dep2", 5000)],
        account_config=ACCT, today=TODAY, now_utc=NOW)
    assert out["pause_deployment_ids"] == ["dep1"]
    assert out["halt_account"] is False   # -6100 is inside the 10000 account limit


def test_within_limits_does_nothing():
    out = evaluate_risk_supervision(
        rows=[_row("dep1", unreal=-100)], deployments=[_dep("dep1", 5000)],
        account_config=ACCT, today=TODAY, now_utc=NOW)
    assert out["halt_account"] is False
    assert out["pause_deployment_ids"] == []


def test_realized_and_unrealized_are_combined():
    out = evaluate_risk_supervision(
        rows=[_row("dep1", status="CLOSED", real=-7000),
              _row("dep1", unreal=-4000)],
        deployments=[_dep("dep1", 50000)],
        account_config=ACCT, today=TODAY, now_utc=NOW)
    assert out["mtm"] == -11000
    assert out["halt_account"] is True


def test_a_stale_mark_never_halts_on_a_guess():
    """Unknown exposure must not trip a sticky latch that needs a human to clear."""
    out = evaluate_risk_supervision(
        rows=[_row("dep1", unreal=-99999, mark_age_s=3600)],
        deployments=[_dep("dep1", 5000)],
        account_config=ACCT, today=TODAY, now_utc=NOW)
    assert out["exposure_unknown"] is True
    assert out["halt_account"] is False, (
        "halting on an unknown number would let a liveness defect latch the desk")
    assert out["pause_deployment_ids"] == []


def test_supervision_never_squares():
    """A timer must not acquire the power to place real orders."""
    out = evaluate_risk_supervision(
        rows=[_row("dep1", unreal=-99999)], deployments=[_dep("dep1", 100)],
        account_config=ACCT, today=TODAY, now_utc=NOW)
    assert "square" not in out
    assert set(out) <= {"halt_account", "pause_deployment_ids", "mtm",
                        "open_count", "exposure_unknown", "reasons"}


def test_paused_and_non_live_deployments_are_skipped():
    deps = [dict(_dep("dep1", 100), status="PAUSED"),
            dict(_dep("dep2", 100), mode="paper")]
    out = evaluate_risk_supervision(
        rows=[_row("dep1", unreal=-9000), _row("dep2", unreal=-9000)],
        deployments=deps, account_config=ACCT, today=TODAY, now_utc=NOW)
    assert out["pause_deployment_ids"] == []


def test_yesterdays_rows_do_not_count_today():
    old = _row("dep1", unreal=-50000)
    old["created_at"] = (NOW - timedelta(days=3)).isoformat()
    out = evaluate_risk_supervision(
        rows=[old], deployments=[_dep("dep1", 5000)],
        account_config=ACCT, today=TODAY, now_utc=NOW)
    assert out["halt_account"] is False
    assert out["pause_deployment_ids"] == []


# --- the loop must be MOUNTED, not merely defined ---------------------------

def _src(rel):
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")


def test_the_supervisor_loop_is_actually_started():
    """A loop nobody starts supervises nothing — the on_expire lesson, re-pinned."""
    runtime = _src("backend/app/runtime.py")
    server = _src("backend/server.py")
    assert "async def _risk_supervisor_loop" in runtime
    assert "_risk_supervisor_loop," in server, "not imported by server.py"
    assert 'asyncio.create_task(_risk_supervisor_loop(), name="risk-supervisor")' in server, (
        "the risk supervisor is defined but never started — caps would still be "
        "evaluated only when a new signal arrives")


def test_the_loop_never_places_or_squares_an_order():
    """A timer must not acquire the power to move real money.

    Checked by AST over the function's CALLS, not by grepping source text — the
    docstring legitimately contains the words "flatten"/"square" while explaining
    that it does neither, and a substring check flags that as a violation.
    """
    import ast
    tree = ast.parse(_src("backend/app/runtime.py"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef)
              and n.name == "_risk_supervisor_loop")
    called = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            called.add(f.attr if isinstance(f, ast.Attribute)
                       else getattr(f, "id", ""))
    forbidden = {"place_order", "square_position", "panic_squareoff",
                 "square_off_open_paper_trades", "cancel_order", "flatten_all"}
    assert not (called & forbidden), (
        f"the risk supervisor calls {called & forbidden} — supervision blocks "
        f"entries, it does not liquidate")
    # It SHOULD reach the halt/pause levers.
    assert "guardrail_tick" in called
    assert "_set_deployment_status" in called


def test_the_loop_is_market_hours_gated():
    runtime = _src("backend/app/runtime.py")
    body = runtime[runtime.index("async def _risk_supervisor_loop"):
                   runtime.index("def _live_engine_for_supervision")]
    assert "is_trading_day" in body and "_time(9, 15)" in body and "_time(15, 30)" in body
