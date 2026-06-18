"""Host tests for the Opening-Range Regime Router (ORR) — the proof strategy for
the scenario-adaptive framework. Covers (1) registry discovery + schema, (2) the
deterministic `_route` direction matrix (the heart of ORR), and (3) an end-to-end
smoke through the REAL enrich -> classify -> route -> exit_plan -> level pipeline.

Host-safe: only base/registry/backtest/indicators/regime/classifier — no
server/optimizer/runtime/paper_auto imports.

NOTE on the load-bearing fade-level assertion: `run_backtest` intentionally DROPS
`spot_target_level` from the serialized trade dict (`_clean_trade_dict` pops it as
internal bookkeeping; the characterization test even asserts it never leaks). So
the "VOLATILE_FADE carries the fade-to-open level" invariant is proven at the
`Signal` level via `strategy.evaluate(...)` on a row drawn from the REAL enriched
frame — that exercises the same classify -> route -> exit_plan path end-to-end.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pandas as pd
import pytest

from app.indicators import precompute_all_indicators
from app.regime import classify_regime_series
from app.scenario_classifier import classify_scenario
from app.strategies.base import get_registry
from app.strategies.builtin.opening_range_regime_router import OpeningRangeRegimeRouter
from app.backtest import run_backtest
from tests._adaptive_testutil import make_ohlc


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def registry():
    reg = get_registry()
    reg.auto_discover()
    return reg


def _build_session(day, kind, base, drift, *, pad=0.02):
    """One 200-bar session. The first `or_minutes` (default 30) bars define the
    opening range; we control its high-low so the session classifies NARROW
    (-> TREND_CONTINUATION) or WIDE (-> VOLATILE_FADE). `pad` is the OHLC wick so
    a flat opening has a tiny (~0.04 pt) range, well under the 0.30% narrow_thr.
    `drift` sets the post-opening direction (the opening drive sign)."""
    closes = np.full(200, float(base), dtype=float)
    if kind == "narrow":
        closes[:30] = base  # flat open -> range ~ 2*pad -> ~0.04% of pivot
    else:  # wide: a clear swing > 0.60 pts in the first 30 bars
        closes[:30] = base + np.concatenate(
            [np.linspace(0, 0.6, 15), np.linspace(0.6, 0, 15)])
    closes[30:] = closes[29] + np.linspace(0, drift, 170)
    f = make_ohlc(closes, start=f"{day} 09:15", high_pad=pad, low_pad=pad)
    f["session_date"] = day
    return f


# 5 sessions: s0 is the warm-up (cpr_p NaN -> orb width NaN -> no trades), then
# alternating NARROW (trend) / WIDE (fade) with up/down drives so BOTH scenarios
# fire and both drive signs are exercised.
_DAYS = ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]
_SPECS = [
    ("narrow", 100, 0.0),   # warm-up session (no defined prior pivot)
    ("narrow", 100, 6.0),   # NARROW, drive UP   -> TREND_CONTINUATION / CE
    ("wide", 100, 8.0),     # WIDE,   drive UP   -> VOLATILE_FADE / PE
    ("narrow", 100, -6.0),  # NARROW, drive DOWN -> TREND_CONTINUATION / PE
    ("wide", 100, -8.0),    # WIDE,   drive DOWN -> VOLATILE_FADE / CE
]


@pytest.fixture(scope="module")
def enriched_df():
    frames = [_build_session(d, k, b, dr) for d, (k, b, dr) in zip(_DAYS, _SPECS)]
    df = pd.concat(frames, ignore_index=True)
    enr = precompute_all_indicators(df)
    enr["regime"] = classify_regime_series(enr)
    return enr


def _ctx_row(close, open_px, *, orb_w, session="2025-01-08"):
    """A (row, ctx) pair: history holds the session's first-bar open=`open_px`."""
    hist = pd.DataFrame({"session_date": [session] * 3,
                         "open": [open_px, open_px + 0.5, open_px + 1.0]})
    row = pd.Series({
        "orb_width_pct_partial": orb_w, "close": close, "regime": "TREND",
        "atr": 2.0, "atr_avg": 2.0, "session_date": session, "ist_time": "10:00",
    })
    return row, {"history_df": hist, "i": 2}


# --------------------------------------------------------------------------- #
# 1. Registry discovery + schema
# --------------------------------------------------------------------------- #
def test_registry_discovers_orr(registry):
    strat = registry.get("opening_range_regime_router")
    assert strat is not None, "ORR not discovered into the registry"
    assert isinstance(strat, OpeningRangeRegimeRouter)
    assert strat.id == "opening_range_regime_router"
    assert strat.name and strat.version and strat.description
    assert strat.scenarios_traded == ("TREND_CONTINUATION", "VOLATILE_FADE")


def test_no_discovery_errors(registry):
    # The framework must discover ORR with zero import/instantiation errors.
    assert "opening_range_regime_router" not in getattr(registry, "_errors", {})
    assert not getattr(registry, "_errors", {}), \
        f"strategy discovery had errors: {getattr(registry, '_errors', {})}"


def test_parameter_schema_has_routing_and_exit_params(registry):
    schema = registry.get("opening_range_regime_router").parameter_schema
    # routing params come from ROUTING_BASE_PARAMS (set by the base __init_subclass__)
    for k in ("or_minutes", "narrow_thr", "wide_thr"):
        assert k in schema, f"missing routing param {k}"
    # exit magnitudes come from ORR.extra_params
    for k in ("trend_target_atr", "trend_stop_atr", "fade_stop_atr"):
        assert k in schema, f"missing exit-magnitude param {k}"


# --------------------------------------------------------------------------- #
# 2. _route direction matrix (deterministic — the heart of ORR)
# --------------------------------------------------------------------------- #
def test_route_trend_continuation_drive_up():
    orr = OpeningRangeRegimeRouter()
    row, ctx = _ctx_row(close=103.0, open_px=100.0, orb_w=0.20)  # close > open
    out = orr._route(row, None, orr.default_params(), ctx, "TREND_CONTINUATION")
    assert out[0] == "CE" and out[1] == 60 and out[3] == []


def test_route_trend_continuation_drive_down():
    orr = OpeningRangeRegimeRouter()
    row, ctx = _ctx_row(close=97.0, open_px=100.0, orb_w=0.20)  # close < open
    out = orr._route(row, None, orr.default_params(), ctx, "TREND_CONTINUATION")
    assert out[0] == "PE"


def test_route_volatile_fade_drive_up_enters_opposite():
    orr = OpeningRangeRegimeRouter()
    row, ctx = _ctx_row(close=103.0, open_px=100.0, orb_w=0.80)  # drive up -> fade PE
    out = orr._route(row, None, orr.default_params(), ctx, "VOLATILE_FADE")
    assert out[0] == "PE"


def test_route_volatile_fade_drive_down_enters_opposite():
    orr = OpeningRangeRegimeRouter()
    row, ctx = _ctx_row(close=97.0, open_px=100.0, orb_w=0.80)  # drive down -> fade CE
    out = orr._route(row, None, orr.default_params(), ctx, "VOLATILE_FADE")
    assert out[0] == "CE"


def test_route_warmup_blocks_on_nan_width():
    orr = OpeningRangeRegimeRouter()
    row, ctx = _ctx_row(close=103.0, open_px=100.0, orb_w=np.nan)
    assert orr._route(row, None, orr.default_params(), ctx, "TREND_CONTINUATION") == \
        ("NONE", 0, [], ["warming up"])


def test_route_no_session_open():
    orr = OpeningRangeRegimeRouter()
    row, _ = _ctx_row(close=103.0, open_px=100.0, orb_w=0.20)
    # empty/mismatched history -> _session_open returns None
    bad_ctx = {"history_df": pd.DataFrame({"session_date": [], "open": []}), "i": 0}
    out = orr._route(row, None, orr.default_params(), bad_ctx, "TREND_CONTINUATION")
    assert out[0] == "NONE" and out[3] == ["no session open"]


# --------------------------------------------------------------------------- #
# 3. End-to-end smoke through the REAL pipeline
# --------------------------------------------------------------------------- #
def test_end_to_end_backtest_scenarios_and_fade_level(enriched_df, registry):
    orr = registry.get("opening_range_regime_router")
    res = run_backtest(enriched_df.copy(), orr, orr.default_params())
    trades = res["trades"]

    # The synthetic frame is designed to fire trades in BOTH routed scenarios.
    assert trades, "ORR produced zero trades on the synthetic frame"
    scen = {t["scenario"] for t in trades}
    # Every trade carries a routed scenario (ORR only emits CE/PE under one).
    assert scen <= {"TREND_CONTINUATION", "VOLATILE_FADE", ""}
    assert "VOLATILE_FADE" in scen, "no VOLATILE_FADE trade fired — adjust synthetic widths"
    assert "TREND_CONTINUATION" in scen, "no TREND_CONTINUATION trade fired"

    # spot_target_level is internal bookkeeping and is intentionally NOT serialized
    # (see _clean_trade_dict); confirm that contract holds here too.
    for t in trades:
        assert "spot_target_level" not in t

    # --- load-bearing: classify -> route -> exit_plan -> level, end-to-end ---
    # Pull a real bar from the enriched frame that classifies as VOLATILE_FADE and
    # drive `evaluate` through the WHOLE strategy; assert the fade carries the
    # session-open level as its absolute target (this is what the level-exit uses).
    sig = _first_signal_for_scenario(enriched_df, orr, "VOLATILE_FADE")
    assert sig is not None, "could not synthesize a VOLATILE_FADE signal from enriched frame"
    assert sig.scenario == "VOLATILE_FADE"
    assert sig.direction in ("CE", "PE")
    assert sig.spot_target_level is not None, "VOLATILE_FADE must carry a fade-to-open level"
    assert sig.spot_target_pts is None  # fade exits to an absolute level, not an ATR multiple
    assert sig.exit_mode == "spot_exit"

    # And the trend scenario must carry an ATR-target (pts), not a level.
    sig_t = _first_signal_for_scenario(enriched_df, orr, "TREND_CONTINUATION")
    assert sig_t is not None and sig_t.scenario == "TREND_CONTINUATION"
    assert sig_t.spot_target_level is None and sig_t.spot_target_pts is not None


def _first_signal_for_scenario(enriched_df, orr, want):
    """Walk the enriched frame, find the first bar whose width classifies as
    `want` (before the 14:00 entry cutoff), and return ORR's full Signal there."""
    params = orr.default_params()
    df = enriched_df.reset_index(drop=True)
    for i in range(len(df)):
        row = df.iloc[i]
        t = str(row.get("ist_time") or "")
        if t >= str(params["entry_cutoff_hhmm"]):
            continue
        scen = classify_scenario(
            regime=row.get("regime"), orb_width_pct=row.get("orb_width_pct_partial"),
            day_type=row.get("day_type"), nr7=row.get("nr7"),
            atr_ratio=orr._atr_ratio(row),
            narrow_thr=float(params["narrow_thr"]), wide_thr=float(params["wide_thr"]))
        if scen != want:
            continue
        sig = orr.evaluate(row, df.iloc[i - 1] if i else None, params,
                           {"history_df": df, "i": i})
        if sig.direction in ("CE", "PE"):
            return sig
    return None
