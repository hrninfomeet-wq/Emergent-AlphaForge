"""The preflight must certify against the SAME lookup the backtest performs.

2026-07-30: for one identical config/window the preflight reported
`total_spot_trades 253, would_pair 253, coverage_pct 100.0, missing_candle 0`
while the real run paired 10/253. The preflight was RIGHT about availability —
the candles were there — but it queried `options_1m` directly and built its index
keyed by `canonical_instrument_key(instrument_key)`, so it never exercised
`build_candles_by_key` and was structurally incapable of detecting the NaN
`contract_key` collapse that invalidated the run.

That is the worst possible failure for a certification tool: the one check whose
job is "can you trust this backtest?" was blind to the bug that made the backtest
untrustworthy, and its green answer actively misdirected the user toward
re-fetching data they already had.

Indexing by canonical token was independently wrong: NSE reuses exchange tokens
across expiries, so one token's ts list mixed two different contracts and could
satisfy a trade from the wrong one.

Fix: ONE shared definition of a candle's contract identity
(`candle_contract_identity`) used by both, and the preflight now groups through
`build_candles_by_key` itself. The two cannot disagree by construction.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pandas as pd  # noqa: E402

from app.option_backtest import (  # noqa: E402
    build_candles_by_key,
    candle_contract_identity,
)

_BASE = dict(close=100.0, high=101.0, low=99.0, open=100.0, volume=1, oi=1,
             side="CE", strike=26200.0)


def _row(ik, expiry, ts, ck=None):
    r = dict(_BASE, instrument_key=ik, expiry_date=expiry, ts=ts)
    if ck is not None:
        r["contract_key"] = ck
    return r


# --------------------------------------------------------------------------- #
# one shared definition
# --------------------------------------------------------------------------- #

def test_identity_prefers_an_explicit_string_contract_key():
    assert candle_contract_identity("NSE_FO|1", "2026-01-06",
                                    "NSE_FO|1|2026-01-06") == "NSE_FO|1|2026-01-06"


def test_identity_ignores_nan_none_and_blank():
    """The exact values that produced the 'nan' bucket."""
    for bad in (float("nan"), None, "", "   "):
        assert candle_contract_identity("NSE_FO|40477", "2026-01-06", bad) == \
            "NSE_FO|40477|2026-01-06", f"{bad!r} was not rejected"


def test_identity_normalises_a_dated_metadata_key():
    assert candle_contract_identity("NSE_FO|40477|06-01-2026", "2026-01-06") == \
        "NSE_FO|40477|2026-01-06"


def test_the_grouper_and_the_helper_agree_on_a_mixed_frame():
    """THE invariant. Every identity the helper derives must be a real bucket in
    the grouper's output — otherwise the preflight can certify a lookup the sim
    will then fail."""
    rows = [_row("NSE_FO|40477", "2026-01-06", 1767239100000),
            _row("NSE_FO|40477", "2026-01-06", 1767239160000),
            _row("NSE_FO|57030", "2026-06-02", 1780026300000,
                 ck="NSE_FO|57030|2026-06-02")]
    keys = set(build_candles_by_key(pd.DataFrame(rows)))
    for r in rows:
        ident = candle_contract_identity(r["instrument_key"], r["expiry_date"],
                                        r.get("contract_key"))
        assert ident in keys, f"{ident} missing from the grouper's index"


def test_the_grouper_uses_the_shared_helper():
    """Source contract: two copies of this rule WILL drift."""
    src = inspect.getsource(build_candles_by_key)
    assert "candle_contract_identity" in src


# --------------------------------------------------------------------------- #
# the preflight actually exercises the grouping
# --------------------------------------------------------------------------- #

def _preflight_src():
    from app import runtime
    return inspect.getsource(runtime._option_preflight_report)


def test_the_preflight_groups_through_build_candles_by_key():
    assert "build_candles_by_key" in _preflight_src(), (
        "the preflight must exercise the real grouping, or it cannot detect the "
        "class of bug that made a 100%-coverage report wrong")


def test_the_preflight_no_longer_indexes_by_bare_canonical_token():
    """Token reuse means one token spans several contracts; a token-keyed ts
    list can satisfy a trade from the wrong expiry."""
    src = _preflight_src()
    assert "key_ts_index.setdefault(canonical_instrument_key" not in src


def test_the_preflight_requests_the_fields_the_identity_needs():
    """A projection of {instrument_key, ts} cannot build an identity at all."""
    src = _preflight_src()
    i = src.index("db.options_1m.find")
    call = src[i:i + 500]
    assert "expiry_date" in call and "contract_key" in call
