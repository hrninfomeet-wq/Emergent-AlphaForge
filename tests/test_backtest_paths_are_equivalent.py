"""The sync and async backtest endpoints must produce the same shape of truth.

User-reported (2026-07-30): "Backtest failed: name 'df_enriched' is not defined".

TWO defects, one cause — I fixed a handler without noticing there are two.

1. **The NameError.** A `sed -i .../g` added `context_df=df_enriched` to BOTH
   `_run_paired_option_backtest` call sites. In `backtest_run` that local exists;
   in `run_backtest_job` the enriched frame is a local named `de` INSIDE the
   `_compute()` closure and is never returned. Every async run raised NameError.

2. **The worse one.** `POST /backtest/start` -> `run_backtest_job` is what the
   Backtest Lab actually calls (`api.startBacktest`). The premium walk-forward and
   option-based significance added on 2026-07-29 were applied ONLY to the sync
   `/backtest/run` handler, which is the path my own verification scripts used.
   So the user's UI runs still carried the defect I had reported as fixed —
   confirmed on their saved run `... 00:16:06`: 3 folds, every fold 0 trades,
   `divergence_warning: false`, `significance: INSUFFICIENT "0 trades"`.

The cure is not another parallel edit. Both handlers now call ONE finalizer,
`resolve_wf_and_significance`, so a family-routing decision cannot be applied to
one path and not the other.
"""
from __future__ import annotations

import inspect
import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.routers.research import (  # noqa: E402
    backtest_run,
    resolve_wf_and_significance,
    run_backtest_job,
)

SPOT_METRICS = {"trade_count": 40, "win_rate": 55.0, "profit_factor": 1.4}
SPOT_WF = {"folds": [{"fold": 1}], "is_vs_oos": {"divergence_warning": False},
           "stitched_oos_equity": [], "stitched_oos_trade_count": 12}


def _premium_option_result(n=6):
    import pandas as pd
    trades = []
    for i in range(n):
        ts = int(pd.Timestamp(f"2026-01-{i + 1:02d} 10:00", tz="Asia/Kolkata").timestamp() * 1000)
        trades.append({"status": "PAIRED", "direction": "CE",
                       "option_entry_ts": ts, "option_exit_ts": ts + 3600_000,
                       "option_pnl_value": 100.0 if i % 2 else -50.0,
                       "option_pnl_pts": 1.0})
    return {"dispatch": "premium_trigger_config",
            "metrics": {"paired_trade_count": n, "win_rate": 50.0},
            "trades": trades}


# --------------------------------------------------------------------------- #
# the NameError
# --------------------------------------------------------------------------- #

def test_the_async_job_has_no_undefined_names():
    """Asserted with pyflakes rather than by banning a string: `df_enriched` is a
    perfectly correct reference now that `_compute` returns the frame, so the
    property under test is 'no undefined name', not 'this identifier is absent'.
    A string ban would have forced the wrong fix (deleting the argument)."""
    import subprocess
    import sys as _sys

    path = os.path.join(os.path.dirname(__file__), "..", "backend", "app",
                        "routers", "research.py")
    out = subprocess.run([_sys.executable, "-m", "pyflakes", os.path.abspath(path)],
                         capture_output=True, text=True, timeout=120)
    undefined = [ln for ln in (out.stdout or "").splitlines() if "undefined name" in ln]
    assert not undefined, "\n".join(undefined)


def test_the_async_job_still_forwards_a_context_frame():
    """The fix must not be 'delete the argument' — that would silently drop the
    market-context annotation (regime/VIX) on the path the UI actually uses."""
    src = inspect.getsource(run_backtest_job)
    assert "context_df=" in src


def test_the_enriched_frame_is_returned_from_the_compute_thread():
    """It is built inside a closure; without returning it there is nothing to
    forward."""
    src = inspect.getsource(run_backtest_job)
    i = src.index("def _compute()")
    body = src[i:i + 1400]
    assert "return r, w, rd" in body and "de" in body


# --------------------------------------------------------------------------- #
# one finalizer, both paths
# --------------------------------------------------------------------------- #

def test_both_handlers_use_the_shared_finalizer():
    """THE structural fix. Two copies of the family routing is exactly how the UI
    path kept a defect that the sync path had already had fixed."""
    for fn in (backtest_run, run_backtest_job):
        assert "resolve_wf_and_significance(" in inspect.getsource(fn), fn.__name__


def test_neither_handler_still_routes_the_family_inline():
    for fn in (backtest_run, run_backtest_job):
        src = inspect.getsource(fn)
        assert "premium_walk_forward(" not in src, (
            f"{fn.__name__} routes inline again — it will drift from the other")


# --------------------------------------------------------------------------- #
# the finalizer's behaviour
# --------------------------------------------------------------------------- #

def test_a_premium_run_gets_the_option_walkforward():
    wf, _sig = resolve_wf_and_significance(
        option_result=_premium_option_result(30), spot_wf=SPOT_WF,
        spot_metrics={"trade_count": 0, "win_rate": 0.0, "profit_factor": None},
        n_folds=3, train_pct=0.6)
    assert wf is not SPOT_WF
    assert wf["measured"] is True
    assert wf["stitched_oos_trade_count"] > 0


def test_a_premium_run_gets_significance_from_the_option_trades():
    """It was badging every premium run on the empty spot metrics: "0 trades"."""
    _wf, sig = resolve_wf_and_significance(
        option_result=_premium_option_result(30), spot_wf=SPOT_WF,
        spot_metrics={"trade_count": 0, "win_rate": 0.0, "profit_factor": None},
        n_folds=3, train_pct=0.6)
    assert sig.get("badge") != "INSUFFICIENT"
    # NB assert on the COUNT, not on the absence of the substring "0 trades" —
    # "30 trades" contains it, which is how this test first failed against
    # correct code.
    assert "30 trades" in str(sig.get("note", ""))
    assert sig["ci95_win_rate"] != [0, 0]


def test_a_premium_run_with_no_trades_is_honestly_unmeasured():
    wf, _sig = resolve_wf_and_significance(
        option_result={"dispatch": "premium_trigger_config", "metrics": {}, "trades": []},
        spot_wf=SPOT_WF, spot_metrics={"trade_count": 0, "win_rate": 0.0},
        n_folds=3, train_pct=0.6)
    assert wf["measured"] is False
    assert wf["is_vs_oos"]["divergence_warning"] is not False


def test_an_ordinary_run_keeps_its_spot_walkforward_untouched():
    wf, sig = resolve_wf_and_significance(
        option_result={"enabled": True, "metrics": {}, "trades": []},
        spot_wf=SPOT_WF, spot_metrics=SPOT_METRICS, n_folds=3, train_pct=0.6)
    assert wf is SPOT_WF
    assert sig["note"].endswith("40 trades") or "40" in str(sig)


def test_no_option_result_at_all_is_safe():
    wf, sig = resolve_wf_and_significance(
        option_result=None, spot_wf=SPOT_WF, spot_metrics=SPOT_METRICS,
        n_folds=3, train_pct=0.6)
    assert wf is SPOT_WF
    assert sig is not None


def test_a_spot_run_with_walkforward_off_stays_none():
    wf, _sig = resolve_wf_and_significance(
        option_result=None, spot_wf=None, spot_metrics=SPOT_METRICS,
        n_folds=3, train_pct=0.6)
    assert wf is None


def test_async_insufficient_candles_preserves_the_same_audit_detail(monkeypatch):
    """The UI polls the async job, so its terminal error must be as useful as
    the synchronous endpoint's 400 rather than discarding the audit it just ran."""
    import pandas as pd
    import app.routers.research as research

    class _DB:
        def __init__(self):
            self.patch = None
            self.backtest_runs = self

        async def update_one(self, _query, patch):
            self.patch = patch

    audit = {
        "after": {"complete_days": 1, "expected_days": 3},
        "fill": {"status": "skipped", "reason": "upstox_not_connected"},
    }
    db = _DB()
    req = type("Req", (), {"strategy_id": "known", "instrument": "NIFTY",
                            "start_ts": 1, "end_ts": 2})()
    strategy = object()

    monkeypatch.setattr(research, "get_db", lambda: db)
    monkeypatch.setattr(research, "get_registry", lambda: type("R", (), {
        "get": staticmethod(lambda _id: strategy),
    })())

    async def _audit(_req):
        return audit

    async def _empty(*_args, **_kwargs):
        return pd.DataFrame()

    monkeypatch.setattr(research, "_audit_and_fill_backtest_data", _audit)
    monkeypatch.setattr(research, "load_candles_df", _empty)

    asyncio.run(run_backtest_job("run-1", req))

    stored = db.patch["$set"]
    assert stored["status"] == "failed"
    assert stored["data_audit"] == audit
    assert "Audit: 1/3 complete days" in stored["error"]
    assert "fill skipped (upstox_not_connected)" in stored["error"]
