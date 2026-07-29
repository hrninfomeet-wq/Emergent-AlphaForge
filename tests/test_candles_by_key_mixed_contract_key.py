"""A candle frame that MIXES contract_key presence must still group correctly.

CRITICAL, found 2026-07-30. A Confluence backtest paired **10 of 253** signals
and reported `no_candles_for_strike` for the other 243 — yet every one of those
243 had expiry-matched candles sitting in `options_1m` within the entry-age
window. The engine could not find data it already had.

`build_candles_by_key` did:

    explicit = candles["contract_key"] if "contract_key" in candles.columns else [None] * len
    ...
    str(ck) if ck else contract_identity_key(ik, expiry)

`contract_key` was added in v0.56.1 and exists on only ~2.3% of `options_1m`
docs (`db.py:72`: "historical rows created before v0.56.1 have no contract_key").
When a loaded frame mixes both shapes, pandas materialises the column and the
absent entries become **NaN** — and `bool(float("nan")) is True`, so `str(ck)`
won and every legacy row was keyed as the literal string `"nan"`.

Consequences that made it hard to spot:
  * it fails as MISSING_ENTRY_CANDLE, i.e. it looks like a DATA gap, so the user
    is sent to re-fetch candles that are already stored;
  * the preflight queries Mongo directly and never calls this function, so it
    cheerfully reported 100% coverage on the same config;
  * it worsens with every ingestion (more contract_key rows => more mixed
    frames) and then self-heals once every row has one.

Ordinary paired-option strategies only. The premium-native engine matches on
`canonical_instrument_key(instrument_key)` (`premium_momentum.py:71`) and never
reads `contract_key`, which is why premium runs paired 116/116 throughout.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pandas as pd  # noqa: E402

from app.option_backtest import build_candles_by_key  # noqa: E402

_BASE = dict(close=100.0, high=101.0, low=99.0, open=100.0, volume=1, oi=1,
             side="CE", strike=26200.0)


def _row(ik, expiry, ts, ck=None):
    r = dict(_BASE, instrument_key=ik, expiry_date=expiry, ts=ts)
    if ck is not None:
        r["contract_key"] = ck
    return r


LEGACY_A = _row("NSE_FO|40477", "2026-01-06", 1767239100000)
LEGACY_B = _row("NSE_FO|40477", "2026-01-06", 1767239160000)
TAGGED = _row("NSE_FO|57030", "2026-06-02", 1780026300000,
              ck="NSE_FO|57030|2026-06-02")


def test_a_frame_with_no_contract_key_still_works():
    """The historical case — must stay byte-identical."""
    keys = build_candles_by_key(pd.DataFrame([LEGACY_A, LEGACY_B]))
    assert "NSE_FO|40477|2026-01-06" in keys
    assert len(keys["NSE_FO|40477|2026-01-06"]) == 2


def test_a_frame_where_every_row_has_contract_key_still_works():
    keys = build_candles_by_key(pd.DataFrame([TAGGED]))
    assert "NSE_FO|57030|2026-06-02" in keys


def test_THE_BUG_a_mixed_frame_does_not_lose_the_legacy_rows():
    """One tagged row must not orphan every untagged row."""
    keys = build_candles_by_key(pd.DataFrame([LEGACY_A, LEGACY_B, TAGGED]))
    assert "NSE_FO|40477|2026-01-06" in keys, (
        "legacy candles vanished from the index — this is the 10-of-253 bug")
    assert len(keys["NSE_FO|40477|2026-01-06"]) == 2
    assert "NSE_FO|57030|2026-06-02" in keys


def test_no_bucket_is_ever_named_nan():
    """The exact fingerprint of the defect."""
    keys = build_candles_by_key(pd.DataFrame([LEGACY_A, LEGACY_B, TAGGED]))
    assert "nan" not in keys
    for k in keys:
        assert k == k and str(k).lower() != "nan"  # k == k catches float NaN keys


def test_an_explicit_contract_key_is_still_authoritative():
    """When present it must win — it is the immutable identity the writer chose,
    and it can legitimately differ from what we would derive."""
    odd = _row("NSE_FO|99999", "2026-06-02", 1780026300000,
               ck="NSE_FO|99999|2026-06-02")
    keys = build_candles_by_key(pd.DataFrame([odd]))
    assert "NSE_FO|99999|2026-06-02" in keys


def test_blank_and_whitespace_contract_keys_fall_back():
    """Empty string / whitespace are not identities."""
    for bad in ("", "   "):
        rows = pd.DataFrame([_row("NSE_FO|40477", "2026-01-06", 1767239100000, ck=bad)])
        keys = build_candles_by_key(rows)
        assert "NSE_FO|40477|2026-01-06" in keys, f"ck={bad!r} was not ignored"


def test_a_none_contract_key_in_a_mixed_frame_falls_back():
    """Explicit None (not just absent) must behave like absent."""
    rows = pd.DataFrame([
        _row("NSE_FO|40477", "2026-01-06", 1767239100000, ck=None),
        TAGGED,
    ])
    keys = build_candles_by_key(rows)
    assert "NSE_FO|40477|2026-01-06" in keys


def test_the_lookup_the_sim_performs_now_succeeds_on_a_mixed_frame():
    """End-to-end on the real lookup expression from
    simulate_paired_option_trades (option_backtest.py:660-663)."""
    from app.instruments import canonical_instrument_key, contract_identity_key

    keys = build_candles_by_key(pd.DataFrame([LEGACY_A, LEGACY_B, TAGGED]))
    dated_metadata_key = "NSE_FO|40477|06-01-2026"   # how option_contracts stores it
    identity = contract_identity_key(dated_metadata_key, "2026-01-06")
    rows = keys.get(identity)
    if rows is None:
        rows = keys.get(canonical_instrument_key(dated_metadata_key), pd.DataFrame())
    assert not rows.empty, "the sim would still report no_candles_for_strike"
    assert len(rows) == 2


def test_token_reuse_across_expiries_is_still_kept_separate():
    """NSE reuses tokens. Two expiries on one token must never cross-pair, and
    the fix must not weaken that."""
    june = _row("NSE_FO|40477", "2025-06-05", 1748576700000)
    jan = _row("NSE_FO|40477", "2026-01-06", 1767239100000)
    keys = build_candles_by_key(pd.DataFrame([june, jan, TAGGED]))
    assert "NSE_FO|40477|2025-06-05" in keys
    assert "NSE_FO|40477|2026-01-06" in keys
    assert len(keys["NSE_FO|40477|2025-06-05"]) == 1
    assert len(keys["NSE_FO|40477|2026-01-06"]) == 1
    # ambiguous canonical alias must NOT be exposed for a reused token
    assert "NSE_FO|40477" not in keys


def test_an_empty_frame_is_still_an_empty_dict():
    assert build_candles_by_key(pd.DataFrame()) == {}
    assert build_candles_by_key(None) == {}
