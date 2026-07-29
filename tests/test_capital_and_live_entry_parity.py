"""Two practical honesty gaps from the 2026-07-30 audit.

**A. Capital feasibility is never checked.** The audited premium run reported
+197% on ₹200,000 while its largest SINGLE leg needed ₹167,229 — 83.6% of the
account. With `leg_mode: both` two primaries can be open at once plus a lazy leg,
so peak concurrent requirement is a multiple of that. Nothing anywhere compares
required capital to available capital; the equity curve simply compounds. A
return computed on a base that could not have funded the trades is not a return.

The honest measure is PEAK CONCURRENT outlay — a sweep line over open positions —
not the max of individual trades, because overlapping legs must be funded
simultaneously.

**B. The backtest takes entries live refuses.** `deployment_evaluator`
`_is_blocked_by_window` blocks every bar at/after `BLOCK_CLOSE_FROM = 14:50`, and
it runs BEFORE the Track B branch, so it applies to premium deployments too. The
premium backtest honoured only the strategy's own `entry_cutoff` (15:09 in the
audited config) and took 4 of 116 entries at/after 14:50 — trades live could never
have made. The optimizer already defaults `trade_window_end` to 14:50 for exactly
this reason, so the backtest disagreed with both live AND the optimizer.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "backtestMetrics.js")
_OVERVIEW = os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                         "components", "backtest", "PerformanceOverview.jsx")


# --------------------------------------------------------------------------- #
# B. live entry-window parity
# --------------------------------------------------------------------------- #

def test_the_live_block_constant_is_shared_not_retyped():
    """A second copy of 14:50 would drift from the evaluator's."""
    from app.deployment_evaluator import BLOCK_CLOSE_FROM
    from app.premium_trigger_dispatch import LIVE_ENTRY_BLOCK_FROM

    assert LIVE_ENTRY_BLOCK_FROM == BLOCK_CLOSE_FROM.strftime("%H:%M")


def test_a_later_entry_cutoff_is_clamped_to_the_live_block():
    from app.premium_trigger_dispatch import clamp_entry_cutoff_to_live

    got, clamped = clamp_entry_cutoff_to_live("15:09")
    assert got == "14:50" and clamped is True


def test_an_absent_entry_cutoff_still_gets_the_live_block():
    """Otherwise the default premium run keeps taking 15:0x entries."""
    from app.premium_trigger_dispatch import clamp_entry_cutoff_to_live

    got, clamped = clamp_entry_cutoff_to_live(None)
    assert got == "14:50" and clamped is True


def test_an_earlier_cutoff_is_respected_and_not_reported_as_clamped():
    from app.premium_trigger_dispatch import clamp_entry_cutoff_to_live

    got, clamped = clamp_entry_cutoff_to_live("13:30")
    assert got == "13:30" and clamped is False


def test_a_malformed_cutoff_falls_back_to_the_live_block():
    from app.premium_trigger_dispatch import clamp_entry_cutoff_to_live

    got, _ = clamp_entry_cutoff_to_live("not-a-time")
    assert got == "14:50"


def test_the_engine_params_carry_the_clamped_cutoff():
    from app.premium_trigger_config import PremiumTriggerConfig
    from app.premium_trigger_dispatch import build_engine_params

    params = {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
              "momentum_pct": 15.0, "lots": 1, "entry_cutoff": "15:09"}
    core = {k: v for k, v in params.items()
            if k in PremiumTriggerConfig.model_fields and v is not None}
    ep = build_engine_params(PremiumTriggerConfig(**core), params)
    assert ep["entry_cutoff"] == "14:50", "the backtest still takes live-blocked entries"


def test_the_clamp_is_disclosed_on_the_result():
    from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario
    from app.premium_trigger_dispatch import dispatch_full_backtest

    spot, opt, contracts = _simple_ce_wins_scenario()
    out = dispatch_full_backtest(
        strategy_id="probe",
        merged_params={"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
                       "momentum_pct": 15.0, "lots": 1, "entry_cutoff": "15:09"},
        spot_df=spot.copy(), option_candles=opt.copy(),
        contracts=list(contracts), instrument="NIFTY")
    assert out["entry_cutoff_clamped"] is True
    assert out["engine_params"]["entry_cutoff"] == "14:50"


# --------------------------------------------------------------------------- #
# A. peak CONCURRENT capital
# --------------------------------------------------------------------------- #

pytestmark_node = pytest.mark.skipif(shutil.which("node") is None,
                                     reason="node required")


def _run_js(body):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    src = (f"import * as M from {url!r};\n"
           f"const out = (() => {{ {body} }})();\n"
           "process.stdout.write(JSON.stringify(out));\n")
    p = subprocess.run(["node", "--input-type=module", "-e", src],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


def _t(entry_ts, exit_ts, premium, qty=100, charges=0.0, pnl=10.0):
    return {"status": "PAIRED", "direction": "CE",
            "option_entry_ts": entry_ts, "option_exit_ts": exit_ts,
            "entry_option_price": premium, "exit_option_price": premium + 1,
            "quantity": qty, "total_charges": charges,
            "option_pnl_value": pnl, "option_pnl_pts": 1.0}


def _result(trades):
    return {"metrics": {}, "trades": [], "equity_curve": [],
            "option_backtest": {
                "enabled": True, "dispatch": "premium_trigger_config",
                "metrics": {"paired_trade_count": len(trades), "win_rate": 50.0},
                "portfolio": {"starting_capital": 200000.0, "ending_equity": 210000.0,
                              "net_pnl_value": 10000.0, "max_drawdown_value": -500.0,
                              "sharpe_daily": 1.0,
                              "curve": [{"ts": 1, "equity_value": 210000.0,
                                         "drawdown_value": 0.0}]},
                "trades": trades}}


@pytestmark_node
def test_two_overlapping_legs_must_be_funded_together():
    """THE point. leg_mode='both' opens CE and PE at once."""
    res = _result([_t(1000, 5000, 100.0), _t(2000, 6000, 100.0)])
    k = _run_js(f"return M.computeKeyMetrics({json.dumps(res)});")
    assert k["peakConcurrentCapital"] == pytest.approx(2 * 100.0 * 100)


@pytestmark_node
def test_sequential_legs_do_not_stack():
    res = _result([_t(1000, 2000, 100.0), _t(3000, 4000, 100.0)])
    k = _run_js(f"return M.computeKeyMetrics({json.dumps(res)});")
    assert k["peakConcurrentCapital"] == pytest.approx(100.0 * 100)


@pytestmark_node
def test_peak_concurrent_is_never_below_the_single_trade_max():
    res = _result([_t(1000, 2000, 500.0), _t(3000, 4000, 100.0)])
    k = _run_js(f"return M.computeKeyMetrics({json.dumps(res)});")
    assert k["peakConcurrentCapital"] >= k["maxBuyValue"]


@pytestmark_node
def test_it_is_null_in_points_mode():
    spot = {"metrics": {"trade_count": 1, "total_pnl_pts": 5.0},
            "trades": [{"pnl_pts": 5.0}], "equity_curve": [], "option_backtest": None}
    k = _run_js(f"return M.computeKeyMetrics({json.dumps(spot)});")
    assert k["peakConcurrentCapital"] is None


@pytestmark_node
def test_a_run_needing_more_than_the_account_is_flagged():
    res = _result([_t(1000, 5000, 1500.0), _t(2000, 6000, 1500.0)])   # 300k > 200k
    k = _run_js(f"return M.computeKeyMetrics({json.dumps(res)});")
    assert k["capitalShortfall"] is True


@pytestmark_node
def test_a_fundable_run_is_not_flagged():
    res = _result([_t(1000, 2000, 100.0)])
    k = _run_js(f"return M.computeKeyMetrics({json.dumps(res)});")
    assert k["capitalShortfall"] is False


def test_the_overview_shows_peak_concurrent_capital_and_the_warning():
    with open(_OVERVIEW, encoding="utf-8") as fh:
        src = fh.read()
    assert "peakConcurrentCapital" in src
    assert "capitalShortfall" in src
