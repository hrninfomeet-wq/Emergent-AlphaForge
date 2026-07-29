"""Context Edge must show real buckets for a premium-native run, not UNKNOWN.

User-reported (2026-07-30): the "Context Edge — where this strategy works" panel
showed a single UNKNOWN bucket across all four dimensions (regime / time of day /
DTE / VIX) for `algotest_option_buy_nifty`, while ordinary strategies like
`confluence_scalper` populate it normally.

Cause: `_adapt_premium_trades_to_paired` set `"context": {}` on every reshaped
trade, and `build_context_breakdown` buckets a missing key as "UNKNOWN". The
premium engine simply never annotated context — it was documented as an accepted
gap in the module docstring.

What is honestly recoverable, and what is not:

  * **time_of_day** — derivable from the trade's own entry timestamp. Always.
  * **dte**         — derivable from the entry session date and the contract
                      expiries the dispatch already receives. Always.
  * **regime**      — NOT derivable inside the dispatch: its `spot_df` comes from
                      `load_candles_df` (raw OHLCV), with no indicator columns.
                      It has to be supplied by the caller, which holds the
                      enriched frame.
  * **vix_bucket**  — NOT derivable either; India VIX lives in Mongo and the
                      dispatch is pure. Supplied by the caller too.

So the dispatch fills what it can from its own inputs and accepts the other two
as optional injections. Partial truth beats a uniform UNKNOWN, and nothing is
fabricated: a dimension with no source stays UNKNOWN rather than being guessed.

Bucketing goes through the SAME `build_trade_context` the ordinary path uses, so
the two families cannot drift into different bucket vocabularies.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd  # noqa: E402

from app.premium_trigger_dispatch import dispatch_full_backtest  # noqa: E402


def _scenario():
    from test_premium_trigger_dispatch_parity import _simple_ce_wins_scenario
    return _simple_ce_wins_scenario()


def _run(**kw):
    spot, opt, contracts = _scenario()
    return dispatch_full_backtest(
        strategy_id="probe",
        merged_params={"reference_time": "09:31", "moneyness": "itm1",
                       "side": "ce", "momentum_pct": 15.0, "lots": 1},
        spot_df=spot.copy(), option_candles=opt.copy(),
        contracts=list(contracts), instrument="NIFTY", **kw)


def _paired(out):
    return [t for t in out["trades"] if t["status"] == "PAIRED"]


# --------------------------------------------------------------------------- #
# what the dispatch can always derive on its own
# --------------------------------------------------------------------------- #

def test_time_of_day_is_populated_from_the_entry_timestamp():
    """THE reported symptom for this dimension: a single UNKNOWN bucket."""
    out = _run()
    trades = _paired(out)
    assert trades, "scenario produced no trades"
    for t in trades:
        assert t["context"].get("time_of_day") not in (None, "", "UNKNOWN")


def test_the_time_of_day_breakdown_has_a_real_bucket():
    cb = _run()["context_breakdown"]
    assert set(cb["time_of_day"]) != {"UNKNOWN"}


def test_dte_is_populated_from_the_contract_expiries():
    out = _run()
    for t in _paired(out):
        assert t["context"].get("dte") is not None


def test_the_dte_breakdown_has_a_real_bucket():
    cb = _run()["context_breakdown"]
    assert set(cb["dte"]) != {"UNKNOWN"}


# --------------------------------------------------------------------------- #
# what must be injected — and must NOT be invented when absent
# --------------------------------------------------------------------------- #

def test_regime_stays_unknown_when_no_context_frame_is_supplied():
    """The dispatch has raw OHLCV only. Guessing a regime would be worse than
    saying UNKNOWN — the whole panel is for deciding where to switch a strategy
    off."""
    cb = _run()["context_breakdown"]
    assert set(cb["regime"]) == {"UNKNOWN"}


def test_regime_is_populated_when_the_caller_supplies_the_enriched_frame():
    spot, _opt, _c = _scenario()
    regime_by_ts = {int(ts): "TREND" for ts in spot["ts"].tolist()}
    out = _run(regime_by_ts=regime_by_ts)
    trades = _paired(out)
    assert trades
    for t in trades:
        assert t["context"]["regime"] == "TREND"
    assert set(out["context_breakdown"]["regime"]) == {"TREND"}


def test_vix_stays_unknown_without_rows():
    cb = _run()["context_breakdown"]
    assert set(cb["vix_bucket"]) == {"UNKNOWN"}


def test_vix_is_populated_from_supplied_candles():
    spot, _opt, _c = _scenario()
    ts0 = int(spot["ts"].min())
    vix_rows = [{"ts": ts0 - 60_000, "close": 12.0}]
    out = _run(vix_rows=vix_rows)
    for t in _paired(out):
        assert t["context"]["vix"] is not None
        assert t["context"]["vix_bucket"] != "UNKNOWN"


# --------------------------------------------------------------------------- #
# safety: this must not disturb anything else
# --------------------------------------------------------------------------- #

def test_pnl_and_trade_count_are_unchanged_by_context_annotation():
    """Context is descriptive metadata. If adding it moves a P&L figure, the
    annotation is touching execution — which it must never do."""
    plain = _run()
    annotated = _run(regime_by_ts={}, vix_rows=[])
    assert plain["metrics"] == annotated["metrics"]
    assert [t["option_pnl_value"] for t in _paired(plain)] == \
           [t["option_pnl_value"] for t in _paired(annotated)]


def test_a_missing_or_malformed_regime_map_does_not_crash():
    for bad in (None, {}, {"nonsense": "X"}):
        out = _run(regime_by_ts=bad)
        assert out is not None


def test_malformed_vix_rows_do_not_crash():
    for bad in (None, [], [{"ts": "junk"}], [{}]):
        out = _run(vix_rows=bad)
        assert out is not None


def test_context_uses_the_shared_builder_vocabulary():
    """Same bucket names as the ordinary path, so the panel cannot show two
    different vocabularies depending on strategy family."""
    from app.market_context import build_trade_context

    out = _run()
    keys = set(build_trade_context(ts_ms=0).keys())
    for t in _paired(out):
        assert set(t["context"].keys()) == keys


def test_the_runtime_passes_context_sources_for_premium_runs():
    """Source contract — the injection points must actually be wired, or regime
    and VIX silently stay UNKNOWN in the real app."""
    import inspect
    from app import runtime

    src = inspect.getsource(runtime._run_paired_option_backtest)
    i = src.index("dispatch_full_backtest(")
    call = src[i:i + 600]
    assert "regime_by_ts=" in call
    assert "vix_rows=" in call
