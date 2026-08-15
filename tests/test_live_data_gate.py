"""The data-integrity gate blocks a NEW live activation — and nothing else.

The operator asked for it explicitly: "block new live strategy deployments when
required data cannot be verified, while clearly reporting the affected instruments,
intervals, and recovery status."

The scope tests here are the important ones. Two gates were removed from this
project on standing instruction and both judged the STRATEGY's permission to trade.
This one judges the DATA, and its blast radius must stay a single transition — a
gate that could block an exit would strand a real position and be strictly worse
than the gap it guards against.
"""
from __future__ import annotations

import asyncio
import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.candle_gap import MINUTE_MS  # noqa: E402
from app.live_data_gate import check_live_data_gate, evaluate_live_data_gate  # noqa: E402

OPEN_MS = 1786679100000            # 2026-08-14 09:15 IST
FULL = 375


def _ist(hh, mm):
    return OPEN_MS + ((hh * 60 + mm) - (9 * 60 + 15)) * MINUTE_MS


def _complete(inst="NIFTY"):
    return {inst: {"complete": True, "present": FULL, "expected": FULL,
                   "coverage_pct": 100.0, "ranges": []}}


def _incomplete(inst="NIFTY"):
    return {inst: {"complete": False, "present": 340, "expected": FULL,
                   "coverage_pct": 90.67, "ranges": [(_ist(9, 15), _ist(9, 49), 35)],
                   "ranges_truncated": False}}


class _DB:
    def __init__(self, ts_by_inst=None, boom=False):
        self.data, self.boom = ts_by_inst or {}, boom
        self.candles_1m = self._C(self)

    class _C:
        def __init__(self, db):
            self.db = db

        def find(self, q, proj=None):
            db, inst = self.db, q["instrument"]

            class _Cur:
                async def to_list(self, length=None):
                    if db.boom:
                        raise RuntimeError("mongo down")
                    return [{"ts": t} for t in db.data.get(inst, [])]
            return _Cur()


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------

def test_complete_data_passes():
    v = evaluate_live_data_gate(_complete())
    assert v["ok"] is True and v["reason"] == "data_verified"
    assert v["blocked_instruments"] == []


def test_incomplete_data_blocks_and_names_instrument_and_interval():
    v = evaluate_live_data_gate(_incomplete())
    assert v["ok"] is False and v["reason"] == "incomplete_market_data"
    assert v["blocked_instruments"] == ["NIFTY"]
    assert "NIFTY" in v["detail"] and "1m" in v["detail"]
    assert "340/375" in v["detail"] and "09:15-09:49 (35)" in v["detail"]


def test_the_detail_reports_recovery_status():
    rec = {"instruments": [{"instrument": "NIFTY", "status": "unresolved"}]}
    v = evaluate_live_data_gate(_incomplete(), recovery=rec)
    assert "NIFTY: unresolved" in v["detail"]


def test_recovery_not_attempted_is_stated_rather_than_implied():
    v = evaluate_live_data_gate(_incomplete())
    assert "not attempted" in v["detail"]


def test_only_the_incomplete_instruments_are_blocked():
    reports = {**_complete("BANKNIFTY"), **_incomplete("NIFTY")}
    v = evaluate_live_data_gate(reports)
    assert v["blocked_instruments"] == ["NIFTY"]


def test_a_truncated_range_list_is_marked_in_the_detail():
    r = _incomplete()
    r["NIFTY"]["ranges_truncated"] = True
    assert "…" in evaluate_live_data_gate(r)["detail"]


def test_junk_reports_do_not_crash_the_verdict():
    for bad in ({}, None, {"NIFTY": None}, {"NIFTY": "x"}):
        v = evaluate_live_data_gate(bad)
        assert isinstance(v["ok"], bool)


# --------------------------------------------------------------------------
# The DB-backed check
# --------------------------------------------------------------------------

def test_a_complete_day_passes_end_to_end():
    db = _DB({"NIFTY": [_ist(9, 15) + i * MINUTE_MS for i in range(FULL)]})
    v = asyncio.run(check_live_data_gate(db, ["NIFTY"], now_ms=_ist(16, 0)))
    assert v["ok"] is True


def test_the_real_2026_08_14_hole_blocks_end_to_end():
    db = _DB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})
    v = asyncio.run(check_live_data_gate(db, ["NIFTY"], now_ms=_ist(16, 0)))
    assert v["ok"] is False
    assert v["report"]["NIFTY"]["missing"] == 35


def test_an_unreadable_warehouse_fails_CLOSED():
    """Cannot verify IS the condition this gate exists for."""
    db = _DB(boom=True)
    v = asyncio.run(check_live_data_gate(db, ["NIFTY"], now_ms=_ist(16, 0)))
    assert v["ok"] is False
    assert v["report"]["NIFTY"]["reason"] == "unreadable"


def test_a_holiday_passes_without_touching_the_warehouse():
    """2026-08-15 is Independence Day. Nothing is expected, so there is nothing to
    verify — and a read error on such a day must not fail closed on an empty
    question. This is the bug the first draft had."""
    db = _DB(boom=True)
    ist_noon = 1786853700000 + 0          # 2026-08-15 ~09:15 IST + slack
    v = asyncio.run(check_live_data_gate(db, ["NIFTY"], now_ms=ist_noon))
    assert v["ok"] is True and v["reason"] == "no_candles_expected"


def test_pre_open_passes_without_touching_the_warehouse():
    db = _DB(boom=True)
    v = asyncio.run(check_live_data_gate(db, ["NIFTY"], now_ms=_ist(9, 0)))
    assert v["ok"] is True and v["reason"] == "no_candles_expected"


def test_a_healthy_mid_session_state_passes():
    now = _ist(11, 0) + 30_000
    have = [_ist(9, 15) + i * MINUTE_MS for i in range(105)]     # 09:15..10:59
    db = _DB({"NIFTY": have})
    v = asyncio.run(check_live_data_gate(db, ["NIFTY"], now_ms=now))
    assert v["ok"] is True


# --------------------------------------------------------------------------
# SCOPE — the tests that matter most
# --------------------------------------------------------------------------

def _module_calls(mod_name: str) -> set:
    import importlib
    import inspect
    mod = importlib.import_module(mod_name)
    tree = ast.parse(inspect.getsource(mod))
    return {n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)} | {
        n.func.attr for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


def test_no_exit_path_consults_the_gate():
    """Blocking an exit because data looks stale would STRAND a real position."""
    for mod in ("app.live.live_position_guard", "app.live.live_sl_monitor",
                "app.live_deploy_governor", "app.deployment_kill_switch",
                "app.paper_squareoff", "app.auto_live"):
        calls = _module_calls(mod)
        assert "check_live_data_gate" not in calls, mod
        assert "evaluate_live_data_gate" not in calls, mod


def test_the_gate_is_wired_only_to_the_live_enable_transition():
    import importlib
    import inspect
    mod = importlib.import_module("app.routers.deployments")
    tree = ast.parse(inspect.getsource(mod))
    users = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                    and n.func.id == "check_live_data_gate":
                users.add(fn.name)
    assert users == {"enable_deployment_live"}, users


def test_the_disable_path_is_never_gated():
    """Coming OFF live must always be possible, whatever the data says."""
    import importlib
    import inspect
    mod = importlib.import_module("app.routers.deployments")
    src = inspect.getsource(mod)
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                ("disable" in fn.name or "stop" in fn.name or "kill" in fn.name):
            body = ast.dump(fn)
            assert "check_live_data_gate" not in body, fn.name
