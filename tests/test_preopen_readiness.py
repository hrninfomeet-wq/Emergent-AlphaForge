"""Say whether the day can trade BEFORE the bell, not after it fails.

On 2026-08-04 the candle roller started at 10:26 IST — 71 minutes after the open —
because the roller, stream and exit monitor cannot start until a human completes
the daily Upstox OAuth. Nothing runs before the market opens: the only warehouse
job is `daily_autoupdate_loop` at 18:00, i.e. after the close. Every readiness
signal the system needs already exists, but only ever runs when someone clicks.

For an unattended bot the failure is silent and total: it is structurally dead
from 09:15 until a human happens to open the browser, and nothing says so.

This is a pure evaluator so the policy is testable without a clock, a database or
a broker. It REPORTS; it never trades, never fetches and never blocks anything —
an operator-facing readiness verdict, not a new gate.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.preopen_readiness import evaluate_preopen_readiness  # noqa: E402

_OK_UPSTOX = {"connected": True, "expired": False}
_OK_FLAT = {"connected": True, "expired": False}


def _ev(**kw):
    base = dict(
        is_trading_day=True,
        upstox_status=_OK_UPSTOX,
        flattrade_status=_OK_FLAT,
        live_deployment_count=0,
        warehouse_plan={"summary": {"total_actions": 0}},
    )
    base.update(kw)
    return evaluate_preopen_readiness(**base)


def test_a_ready_day_reports_ready():
    out = _ev()
    assert out["ready"] is True
    assert out["blockers"] == []


def test_a_missing_upstox_token_is_the_headline_blocker():
    """This is the 71-minute dead zone: no token, no roller, no bars, no signals."""
    out = _ev(upstox_status={"connected": False})
    assert out["ready"] is False
    assert any(b["id"] == "upstox_not_connected" for b in out["blockers"])


def test_an_expired_upstox_token_blocks_too():
    out = _ev(upstox_status={"connected": True, "expired": True})
    assert out["ready"] is False
    assert any(b["id"] == "upstox_token_expired" for b in out["blockers"])


def test_flattrade_is_only_required_when_something_is_live():
    """A paper-only day does not need the broker — demanding it would cry wolf."""
    assert _ev(flattrade_status={"connected": False}, live_deployment_count=0)["ready"] is True
    out = _ev(flattrade_status={"connected": False}, live_deployment_count=1)
    assert out["ready"] is False
    assert any(b["id"] == "flattrade_not_connected" for b in out["blockers"])


def test_a_non_trading_day_is_not_a_failure():
    """A holiday is not unreadiness — reporting red every Sunday trains people to
    ignore the signal."""
    out = _ev(is_trading_day=False, upstox_status={"connected": False})
    assert out["ready"] is True
    assert out["blockers"] == []
    assert out["reason"] == "not_a_trading_day"


def test_outstanding_warehouse_work_warns_but_does_not_block():
    """Missing candles degrade a backtest; they do not stop the day trading."""
    out = _ev(warehouse_plan={"summary": {"total_actions": 4}})
    assert out["ready"] is True, "a data gap must not be reported as cannot-trade"
    assert any(w["id"] == "warehouse_actions_pending" for w in out["warnings"])


def test_blockers_carry_an_actionable_message():
    out = _ev(upstox_status={"connected": False})
    b = out["blockers"][0]
    assert b.get("detail"), "a blocker with no instruction wastes the early warning"
    assert "upstox" in b["detail"].lower()


def test_every_check_is_reported_even_when_ready():
    """The operator should see WHAT was verified, not just a green light."""
    out = _ev()
    for key in ("upstox", "flattrade", "warehouse", "trading_day"):
        assert key in out["checks"]


def test_the_verdict_never_trades_or_fetches():
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "backend" / "app"
           / "preopen_readiness.py").read_text(encoding="utf-8")
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)}
    for forbidden in ("place_order", "execute_hygiene_plan", "square_position",
                      "insert_one", "update_one"):
        assert forbidden not in called, f"the readiness evaluator must not call {forbidden}"


# --- the loop must be MOUNTED and must run BEFORE the open ------------------

def _src(rel):
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")


def test_the_readiness_loop_is_actually_started():
    runtime, server = _src("backend/app/runtime.py"), _src("backend/server.py")
    assert "async def _preopen_readiness_loop" in runtime
    assert "_preopen_readiness_loop," in server, "not imported by server.py"
    assert 'asyncio.create_task(_preopen_readiness_loop(), name="preopen-readiness")' in server, (
        "the readiness check is defined but never started — the day would still "
        "begin with zero verification")


def test_it_runs_before_the_0915_open():
    """08:45 IST: after an overnight refresh settles, with time to act."""
    runtime = _src("backend/app/runtime.py")
    assert "_PREOPEN_HOUR_IST = 8" in runtime
    assert "_PREOPEN_MINUTE_IST = 45" in runtime


def test_the_loop_never_trades_fetches_or_arms():
    """A pre-open task must REPORT. It is not a new gate and must not act."""
    import ast
    runtime = _src("backend/app/runtime.py")
    fn = next(n for n in ast.walk(ast.parse(runtime))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_preopen_readiness")
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(fn) if isinstance(n, ast.Call)}
    for forbidden in ("place_order", "_autoupdate_execute_plan", "execute_hygiene_plan",
                      "square_position", "guardrail_tick", "_set_deployment_status"):
        assert forbidden not in called, (
            f"the pre-open check called {forbidden} — it must report, not act")
    assert "evaluate_preopen_readiness" in called
