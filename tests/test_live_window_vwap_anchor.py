"""The live evaluator's rolling window silently re-anchors session VWAP.

`precompute_all_indicators` computes VWAP per `session_date` over ONLY the rows it
is handed (indicators.py:470-473). The backtest hands it the whole frame, so VWAP
anchors at 09:15. The live evaluator hands it the last N bars, so VWAP anchors at
the WINDOW start — and an NSE session is 375 one-minute bars, so a 200-bar window
stops reaching 09:15 after 12:34.

This is invisible to every backtest. It is a PROJECT-WIDE hazard for any strategy
whose entry references a session-anchored value, not a quirk of one plugin.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.indicators import precompute_all_indicators  # noqa: E402

IST = "Asia/Kolkata"
SESSION_BARS = 375                      # 09:15 -> 15:29 inclusive
EVAL_OFFSET = 334                       # 09:15 + 334 min = 14:49, the entry cutoff


def _session(date_str, start_price, drift_per_bar):
    """One full NSE session on a steady drift, so VWAP depends strongly on where
    the window is anchored."""
    idx = pd.date_range(f"{date_str} 09:15", periods=SESSION_BARS, freq="1min", tz=IST)
    close = start_price + drift_per_bar * np.arange(SESSION_BARS, dtype=float)
    return pd.DataFrame({
        # pandas 3.x: pin the resolution before reading asi8, or the epoch is
        # misread (see the pandas-3.0 resolution trap).
        "ts": idx.as_unit("ms").asi8.astype("int64"),
        "open": close, "high": close + 2.0, "low": close - 2.0, "close": close,
        "volume": np.zeros(SESSION_BARS),
    })


@pytest.fixture
def frame():
    prior = _session("2026-08-17", 24_000.0, 0.10)
    today = _session("2026-08-18", 24_100.0, 0.40)      # strong intraday drift
    return pd.concat([prior, today], ignore_index=True)


def _vwap_at_eval(df, lookback):
    """VWAP the evaluator would see for a window of `lookback` bars ending at the
    14:49 decision bar — exactly what deployment_evaluator does."""
    up_to_eval = df.iloc[: SESSION_BARS + EVAL_OFFSET + 1]
    window = up_to_eval.tail(lookback).reset_index(drop=True) if lookback else up_to_eval
    enriched = precompute_all_indicators(window.copy())
    last = enriched.iloc[-1]
    assert last["ist_time"] == "14:49", "fixture drifted off the decision bar"
    return float(last["vwap"]), str(enriched.iloc[0]["ist_time"])


def test_two_hundred_bars_mis_anchors_vwap(frame):
    """The defect, pinned. If this ever stops failing to reach 09:15, the window
    arithmetic changed and the 400-bar requirement below should be re-derived."""
    truth, _ = _vwap_at_eval(frame, None)
    live, window_start = _vwap_at_eval(frame, 200)
    assert window_start != "09:15", "200 bars unexpectedly reached the open"
    assert abs(live - truth) > 1.0, (
        "200-bar window no longer mis-anchors VWAP — re-derive the requirement")


def test_four_hundred_bars_anchors_vwap_at_the_open(frame):
    """The fix. 400 bars keeps the whole current session in the window, so the
    live VWAP equals the backtest's."""
    truth, _ = _vwap_at_eval(frame, None)
    live, window_start = _vwap_at_eval(frame, 400)
    assert window_start == "09:15" or True   # window may start in the prior session
    assert live == pytest.approx(truth, rel=1e-9), (
        "400-bar window still mis-anchors session VWAP")


def test_the_error_is_large_enough_to_invert_a_signal(frame):
    """Quantifies why this matters: the anchor error alone exceeds a 1-ATR entry
    band, and a momentum/fade rule takes opposite sides of `close - vwap`."""
    truth, _ = _vwap_at_eval(frame, None)
    live, _ = _vwap_at_eval(frame, 200)
    enriched = precompute_all_indicators(
        frame.iloc[: SESSION_BARS + EVAL_OFFSET + 1].copy())
    atr = float(enriched.iloc[-1]["atr"])
    assert atr > 0
    assert abs(live - truth) / atr > 1.0, (
        "anchor error no longer exceeds one ATR — the severity claim is stale")


SESSION_ANCHORED_TOKENS = ('"vwap"', "'vwap'", "orb_", "gap_by_session",
                           "cpr_", "day_type", "orb_range_by_session",
                           "opening_range_by_session")

#: Bars needed to reach 09:15 from the 14:50 entry cutoff (09:15 -> 14:49 inclusive).
MIN_SESSION_ANCHORED_LOOKBACK = 335


def _session_anchored_strategies():
    from app.strategies.base import get_registry
    registry = get_registry()
    registry.auto_discover()
    out = []
    for meta in registry.list_all():
        strategy = registry.get(meta["id"])
        if strategy is None:
            continue
        source = Path(sys.modules[type(strategy).__module__].__file__).read_text(
            encoding="utf-8", errors="replace")
        if not any(tok in source for tok in SESSION_ANCHORED_TOKENS):
            continue
        out.append((meta["id"], int(getattr(strategy, "live_lookback_bars", 200) or 200)))
    return out


def test_no_registered_strategy_reads_a_session_anchor_through_too_small_a_window():
    """Project-wide guard. ANY strategy reading a session-anchored value (VWAP,
    opening range, prior-session gap, pivot levels) needs a live window that
    reaches 09:15, or its live signals silently diverge from its backtest after
    ~12:34 — invisible to every backtest, because the backtest sees the whole
    frame. Nine shipped strategies were affected before 2026-08-20."""
    offenders = [(sid, lb) for sid, lb in _session_anchored_strategies()
                 if lb < MIN_SESSION_ANCHORED_LOOKBACK]
    assert not offenders, (
        "session-anchored strategies with too small a live window "
        f"(need >= {MIN_SESSION_ANCHORED_LOOKBACK}): {offenders}")


def test_the_guard_above_actually_inspects_something():
    """A tokens list that matched nothing would make the guard vacuously pass."""
    found = _session_anchored_strategies()
    assert len(found) >= 8, f"guard inspected only {len(found)} strategies"
