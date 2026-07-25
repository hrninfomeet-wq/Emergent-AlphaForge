"""Tests for the read-only market-analysis assembly + route.

The pure primitives have their own suite (test_market_analysis.py). These cover
the ASSEMBLY contract the cockpit depends on:
  - the payload shape (keys the UI reads) is always present, even when every
    upstream stage fails;
  - honest degradation: a missing stage yields nulls + a `warnings` code, never
    a fabricated number;
  - PCR/max-pain are suppressed (with a warning) when the tick stream carries no
    open interest — reporting a PCR computed from zeros would be a lie;
  - the ~8s cache is single-flight (a second call inside the TTL does not rebuild).
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import market_analysis_build as mab  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class _AggCursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length=None):
        return list(self._rows)


class _FindCursor(_AggCursor):
    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self


class _Candles:
    def __init__(self, daily_rows=None, vix_rows=None):
        self._daily = daily_rows or []
        self._vix = vix_rows or []

    def aggregate(self, pipeline):
        return _AggCursor(self._daily)

    def find(self, query, projection=None):
        return _FindCursor(self._vix)


class _DB:
    def __init__(self, daily_rows=None, vix_rows=None):
        self.candles_1m = _Candles(daily_rows, vix_rows)


PAYLOAD_KEYS = {"instrument", "as_of", "spot", "structure", "trend", "levels",
                "options", "warnings"}
OPTION_KEYS = {"pcr_oi", "max_pain", "iv_rank_30d", "iv_rank_source",
               "atm_straddle", "implied_move_pct", "net_delta_rupee",
               "net_theta_rupee", "expiry", "chain"}


def _patch_stages(monkeypatch, *, intraday=None, chain=None, spot=None, oi=True):
    """Stub the three I/O stages so the assembly is host-testable."""
    async def _fake_intraday(db, instrument):
        return intraday if intraday is not None else (None, [])
    async def _fake_chain(db, instrument):
        return (chain or []), spot, "2026-07-28", oi
    monkeypatch.setattr(mab, "_intraday_closes_and_frame", _fake_intraday)
    monkeypatch.setattr(mab, "_option_chain", _fake_chain)


def test_payload_shape_is_complete_even_when_everything_fails(monkeypatch):
    """Total upstream failure must still return the full key set + warnings."""
    async def _boom(*a, **k):
        raise RuntimeError("upstream down")
    monkeypatch.setattr(mab, "_intraday_closes_and_frame", _boom)
    monkeypatch.setattr(mab, "_option_chain", _boom)
    monkeypatch.setattr(mab, "_daily_closes", _boom)
    monkeypatch.setattr(mab, "_vix_series", _boom)

    out = run(mab.build_market_analysis(_DB(), "NIFTY"))
    assert PAYLOAD_KEYS.issubset(out.keys())
    assert OPTION_KEYS.issubset(out["options"].keys())
    assert out["instrument"] == "NIFTY"
    assert out["structure"] is None and out["levels"] is None
    assert out["options"]["pcr_oi"] is None
    # honest: it SAYS it failed rather than reporting zeros
    for code in ("intraday_unavailable", "daily_unavailable",
                 "option_chain_unavailable", "iv_unavailable"):
        assert code in out["warnings"], code


def test_multi_timeframe_trend_from_daily_closes(monkeypatch):
    _patch_stages(monkeypatch)
    rising = [{"close": 100 + i, "ts": i} for i in range(80)]
    monkeypatch.setattr(mab, "_daily_closes",
                        lambda db, inst: _coro([r["close"] for r in rising]))
    monkeypatch.setattr(mab, "_vix_series", lambda db: _coro((None, [])))

    out = run(mab.build_market_analysis(_DB(), "NIFTY"))
    assert out["trend"]["daily"] == "up"
    assert out["trend"]["weekly"] == "up"
    assert out["trend"]["monthly"] == "up"


def test_pcr_and_maxpain_suppressed_when_no_open_interest(monkeypatch):
    """Ticks without open_interest (stream not in full mode) must NOT yield a
    PCR computed from zeros — suppress + warn."""
    chain = [{"strike": 24200, "ce_ltp": 78.2, "pe_ltp": 92.4, "ce_oi": None, "pe_oi": None}]
    _patch_stages(monkeypatch, chain=chain, spot=24187.7, oi=False)
    monkeypatch.setattr(mab, "_daily_closes", lambda db, inst: _coro([]))
    monkeypatch.setattr(mab, "_vix_series", lambda db: _coro((None, [])))

    out = run(mab.build_market_analysis(_DB(), "NIFTY"))
    assert out["options"]["pcr_oi"] is None
    assert out["options"]["max_pain"] is None
    assert "option_oi_unavailable" in out["warnings"]
    # the straddle is still computable from LTPs alone
    assert out["options"]["atm_straddle"] == pytest.approx(170.6)


def test_pcr_and_maxpain_computed_when_oi_present(monkeypatch):
    chain = [
        {"strike": 24100, "ce_ltp": 168.4, "pe_ltp": 41.0, "ce_oi": 120000, "pe_oi": 290000},
        {"strike": 24200, "ce_ltp": 78.2, "pe_ltp": 92.4, "ce_oi": 260000, "pe_oi": 240000},
    ]
    _patch_stages(monkeypatch, chain=chain, spot=24187.7, oi=True)
    monkeypatch.setattr(mab, "_daily_closes", lambda db, inst: _coro([]))
    monkeypatch.setattr(mab, "_vix_series", lambda db: _coro((None, [])))

    out = run(mab.build_market_analysis(_DB(), "NIFTY"))
    assert out["options"]["pcr_oi"] == pytest.approx((290000 + 240000) / (120000 + 260000))
    assert out["options"]["max_pain"] in (24100.0, 24200.0)
    assert out["options"]["expiry"] == "2026-07-28"


def test_iv_rank_uses_vix_proxy_and_flags_it(monkeypatch):
    """There is no stored ATM-IV history, so IV rank must come from the VIX
    percentile AND declare that provenance (never pass it off as ATM IV)."""
    _patch_stages(monkeypatch)
    monkeypatch.setattr(mab, "_daily_closes", lambda db, inst: _coro([]))
    monkeypatch.setattr(mab, "_vix_series",
                        lambda db: _coro((11.8, [10, 11, 12, 13, 14, 15, 16, 18, 20, 22])))

    out = run(mab.build_market_analysis(_DB(), "NIFTY"))
    assert out["options"]["iv_rank_source"] == "vix_proxy"
    assert 0.0 <= out["options"]["iv_rank_30d"] <= 1.0
    assert "iv_history_thin" in out["warnings"]


def test_cache_is_single_flight_within_ttl(monkeypatch):
    """A second call inside the TTL returns the cached payload without rebuilding."""
    calls = {"n": 0}

    async def _fake_build(db, instrument):
        calls["n"] += 1
        return {"instrument": instrument, "warnings": [], "options": {}}

    monkeypatch.setattr(mab, "build_market_analysis", _fake_build)
    mab._CACHE.clear()
    db = _DB()
    a = run(mab.cached_market_analysis(db, "NIFTY"))
    b = run(mab.cached_market_analysis(db, "NIFTY"))
    assert calls["n"] == 1
    assert "_cached_at" not in a and "_cached_at" not in b
    mab._CACHE.clear()


def _coro(value):
    async def _inner():
        return value
    return _inner()
