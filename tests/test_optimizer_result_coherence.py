"""The optimizer's displayed result, saved result and preset must be ONE thing.

All verified on a real job (`fd40ecff…`, confluence + option_rerank) on 2026-07-30.

**1. The UI showed one config and Save-as-Preset saved another.**
```
best_so_far.params  ema_fast/ema_slow/signal_threshold = 6/43/74   Sharpe 1.499
best_params         ema_fast/ema_slow/signal_threshold = 5/57/79   Sharpe 1.168
```
`finished["best_params"]` is built from the LOCAL `best_so_far`, which Stage 2
correctly reassigns when the option re-rank promotes a different candidate — but
`finished` carries no `best_so_far` key, so the PERSISTED field stays frozen at the
last `_flush_trial_log` from the trial loop. `Optimizer.jsx:1426` renders that
stale field (and Job History's "Best" column reads `best_so_far?.value`), while
`research.py:707` applies `best_params`.

**2. Three stages, two entry windows.** `_survival_eval_oos` threads
`trade_window_start/end` (optimizer.py:880-884) and the trial evaluators do too,
but `_option_rerank`'s own spot re-run does not — its signature does not even
accept them — and neither does `_save_best_as_backtest`. `run_backtest`'s default
is 15:00 while `OptimizerStartReq` ships 14:50. Measured on the same winning
params: 14:50 -> 222 spot / net ₹142,636; 15:00 -> 227 spot / net ₹134,865, a
**5.4%** difference. So Stage 2 ranked on a trade set Stage 1 never scored, the
promoted/saved run is a third thing again, and the number the job reports matches
the 15:00 run.

**3. `best_value` holds a rupee amount while `objective` says "sharpe"** (measured
`best_value = 134864.61`). Nothing names the unit, so the field cannot be read
safely by any consumer.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import optimizer  # noqa: E402
from app.preset_execution import execution_from_option_config  # noqa: E402
from app.schemas import OptimizerStartReq  # noqa: E402

_BACKTEST_LAB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                             "pages", "BacktestLab.jsx")


# --------------------------------------------------------------------------- #
# 1. one result, not two
# --------------------------------------------------------------------------- #

def test_the_finish_payload_refreshes_the_persisted_best_so_far():
    """THE fix. Without this key the DB keeps the Stage-1 winner while
    best_params holds the Stage-2 one."""
    src = inspect.getsource(optimizer.run_optimization)
    i = src.index("finished = {")
    block = src[i:i + 2600]
    assert '"best_so_far"' in block, (
        "the finish payload must re-persist best_so_far, or the UI shows a "
        "different config than Save-as-Preset writes")


def test_best_so_far_and_best_params_come_from_the_same_object():
    """Asserted structurally: both must read the same local, so they cannot
    diverge again."""
    src = inspect.getsource(optimizer.run_optimization)
    i = src.index("finished = {")
    block = src[i:i + 2600]
    assert 'best_params": best_so_far["params"]' in block.replace("'", '"')


def test_the_optimizer_exposes_what_best_value_measures():
    """A field that holds a Sharpe on one job and rupees on another is unusable
    without a unit."""
    src = inspect.getsource(optimizer.run_optimization)
    i = src.index("finished = {")
    block = src[i:i + 2600]
    assert '"best_value_metric"' in block


# --------------------------------------------------------------------------- #
# 2. one entry window through every stage
# --------------------------------------------------------------------------- #

def test_the_optimizer_default_window_is_still_the_live_one():
    """Guard the premise — if this changes the rest is meaningless."""
    r = OptimizerStartReq(strategy_id="s")
    assert r.trade_window_start == "09:25" and r.trade_window_end == "14:50"


def test_option_rerank_accepts_the_trade_window():
    sig = inspect.signature(optimizer._option_rerank)
    assert "trade_window_start" in sig.parameters
    assert "trade_window_end" in sig.parameters


def test_option_rerank_threads_the_window_into_its_spot_rerun():
    """Stage 2 re-runs run_backtest to get each candidate's trades. Without the
    window it ranks on a 15:00 trade set Stage 1 never scored.

    Asserted as "builds the _tw dict AND splats it into run_backtest" — the same
    shape `_survival_eval_oos` uses — rather than looking for a literal kwarg,
    which the splat form does not contain."""
    src = inspect.getsource(optimizer._option_rerank)
    assert "_tw = (" in src and '"trade_window_start": trade_window_start' in src
    i = src.index("run_backtest,")
    assert "**_tw" in src[i:i + 400], "the window dict is built but never passed"


def test_the_rerank_call_site_passes_the_window():
    src = inspect.getsource(optimizer.run_optimization)
    i = src.index("_option_rerank(")
    call = src[i:i + 600]
    assert "trade_window_start=" in call and "trade_window_end=" in call


def test_save_best_as_backtest_accepts_and_uses_the_window():
    sig = inspect.signature(optimizer._save_best_as_backtest)
    assert "trade_window_start" in sig.parameters
    assert "trade_window_end" in sig.parameters
    src = inspect.getsource(optimizer._save_best_as_backtest)
    # Both the spot re-run AND the walk-forward must receive it. Counted as
    # `**_tw` splats, which is the form actually used.
    assert src.count("**_tw") >= 2, (
        "run_backtest and walk_forward must both receive the window "
        f"(found {src.count('**_tw')} splat(s))")


def test_the_saved_run_config_records_the_window():
    """BacktestLab rehydrates `r.config?.trade_window_end || "15:00"`, so an
    absent key silently becomes a different window on reopen."""
    src = inspect.getsource(optimizer._save_best_as_backtest)
    i = src.index('"config"')
    block = src[i:i + 1200]
    assert "trade_window_start" in block and "trade_window_end" in block


# --------------------------------------------------------------------------- #
# 3. the preset carries the window out to the Backtest Lab
# --------------------------------------------------------------------------- #

def test_execution_block_carries_the_validated_window():
    ex = execution_from_option_config({
        "moneyness": "atm", "lots": 5, "exit_mode": "option_levels",
        "trade_window_start": "09:25", "trade_window_end": "14:50"})
    assert ex["trade_window_start"] == "09:25"
    assert ex["trade_window_end"] == "14:50"


def test_execution_block_omits_the_window_when_unknown():
    """Absent must stay distinguishable from a guessed default."""
    ex = execution_from_option_config({"moneyness": "atm", "lots": 5})
    assert "trade_window_end" not in ex


def test_execution_block_keeps_spread_min_pts():
    """It was dropped, silently losing a configured floor."""
    ex = execution_from_option_config({
        "moneyness": "atm", "lots": 1,
        "cost_config": {"enabled": True, "brokerage_per_order": 0,
                        "spread_pct_of_premium": 0.5, "spread_min_pts": 2.5}})
    assert ex["cost_config"]["spread_min_pts"] == 2.5


def test_apply_as_preset_puts_the_window_in_the_execution_block():
    from app.routers import research
    src = inspect.getsource(research.apply_opt_as_preset)
    i = src.index("_oc = dict(")
    block = src[i:i + 700]
    assert "trade_window_start" in block and "trade_window_end" in block


def test_the_backtest_lab_restores_the_window_from_a_preset():
    with open(_BACKTEST_LAB, encoding="utf-8") as fh:
        src = fh.read()
    i = src.index("ex.moneyness")          # the preset-execution apply block
    block = src[max(0, i - 900):i + 1500]
    assert "trade_window_end" in block, (
        "loading an optimizer preset must restore the window it was scored on")


# --------------------------------------------------------------------------- #
# regression safety
# --------------------------------------------------------------------------- #

def test_a_spot_only_option_config_still_returns_none():
    assert execution_from_option_config(None) is None
    assert execution_from_option_config({}) is None


def test_existing_execution_fields_are_unchanged():
    ex = execution_from_option_config({
        "moneyness": "itm1", "dte_filter": [0, 1], "exit_mode": "spot_exit",
        "lots": 3, "option_target_pct": 28, "option_stop_pct": 18})
    assert ex["moneyness"] == "itm1" and ex["lots"] == 3
    assert ex["dte_filter"] == [0, 1] and ex["exit_mode"] == "spot_exit"
    assert ex["option_target_pct"] == 28 and ex["option_stop_pct"] == 18
