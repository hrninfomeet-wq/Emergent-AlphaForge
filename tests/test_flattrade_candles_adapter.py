"""The TPSeries -> warehouse adapter, including the session-clipping trap.

Verified live on 2026-08-15: for a 09:15-10:15 window TPSeries returned 61 rows,
one of them OUTSIDE the session (a 09:14 pre-open marker with O=H=L=C).
`candle_gap` ignores unexpected timestamps when ASSESSING, but
`persist_candles_df` would happily WRITE them — so the adapter must clip, or every
fallback fetch quietly pollutes `candles_1m` with rows no session model accounts
for.

Source agreement was measured over 60 common NIFTY minutes on 2026-08-14:
high/low/close identical on every bar; `open` differs on 2/60, max 1.85 points.
That is why Upstox stays primary and this is only a fallback.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.flattrade_candles import NOREN_INDEX_TOKENS, make_tpseries_fetcher  # noqa: E402

MIN_MS = 60_000
OPEN_0814 = 1786679100000          # 2026-08-14 09:15 IST


class _Client:
    def __init__(self, rows=None, boom=False):
        self.rows, self.boom, self.calls = rows or [], boom, []

    async def time_price_series(self, *, exch, token, start_ms, end_ms, interval="1"):
        self.calls.append({"exch": exch, "token": token,
                           "start_ms": start_ms, "end_ms": end_ms})
        if self.boom:
            raise RuntimeError("flattrade down")
        return self.rows


def _row(ts, o=100.0, h=101.0, low=99.0, c=100.5, v=0):
    return {"ts": ts, "open": o, "high": h, "low": low, "close": c, "volume": v}


def _fetch(client, inst="NIFTY", lo=OPEN_0814, hi=OPEN_0814 + 3_600_000):
    async def factory():
        return client
    return asyncio.run(make_tpseries_fetcher(factory)(inst, lo, hi))


# --------------------------------------------------------------------------
# Token mapping
# --------------------------------------------------------------------------

def test_the_index_tokens_are_the_ones_resolved_live():
    """SearchScrip on 2026-08-15 returned these, all instname UNDIND."""
    assert NOREN_INDEX_TOKENS["NIFTY"] == ("NSE", "26000")
    assert NOREN_INDEX_TOKENS["BANKNIFTY"] == ("NSE", "26009")
    assert NOREN_INDEX_TOKENS["SENSEX"] == ("BSE", "1")


def test_the_mapped_token_is_what_gets_requested():
    c = _Client([_row(OPEN_0814)])
    _fetch(c, "SENSEX")
    assert c.calls[0]["exch"] == "BSE" and c.calls[0]["token"] == "1"


def test_an_unmapped_instrument_yields_empty_without_calling_the_broker():
    """An option contract has no index token; it must degrade, not raise."""
    c = _Client([_row(OPEN_0814)])
    df = _fetch(c, "NIFTY 24300 PE 18 AUG 26")
    assert df.empty and c.calls == []


def test_no_client_yields_empty_rather_than_raising():
    async def factory():
        return None
    df = asyncio.run(make_tpseries_fetcher(factory)("NIFTY", OPEN_0814,
                                                    OPEN_0814 + 60_000))
    assert df.empty


# --------------------------------------------------------------------------
# Session clipping — the live-observed trap
# --------------------------------------------------------------------------

def test_a_pre_open_marker_bar_is_CLIPPED():
    """The real 09:14 row TPSeries returned must never reach the warehouse."""
    rows = [_row(OPEN_0814 - MIN_MS), _row(OPEN_0814), _row(OPEN_0814 + MIN_MS)]
    df = _fetch(_Client(rows))
    assert len(df) == 2
    assert (OPEN_0814 - MIN_MS) not in set(df["ts"])


def test_a_post_close_bar_is_clipped():
    close_ts = OPEN_0814 + 374 * MIN_MS               # 15:29, last valid
    rows = [_row(close_ts), _row(close_ts + MIN_MS)]
    df = _fetch(_Client(rows))
    assert set(df["ts"]) == {close_ts}


def test_every_in_session_bar_survives():
    rows = [_row(OPEN_0814 + i * MIN_MS) for i in range(375)]
    df = _fetch(_Client(rows))
    assert len(df) == 375


def test_bars_spanning_two_days_are_each_clipped_to_their_own_session():
    prev_open = OPEN_0814 - 86_400_000
    rows = [_row(prev_open - MIN_MS), _row(prev_open),
            _row(OPEN_0814 - MIN_MS), _row(OPEN_0814)]
    df = _fetch(_Client(rows), lo=prev_open - MIN_MS, hi=OPEN_0814 + MIN_MS)
    assert set(df["ts"]) == {prev_open, OPEN_0814}


# --------------------------------------------------------------------------
# Warehouse shape
# --------------------------------------------------------------------------

def test_the_frame_carries_the_columns_persist_expects():
    df = _fetch(_Client([_row(OPEN_0814, o=1.0, h=2.0, low=0.5, c=1.5, v=7)]))
    assert set(df.columns) >= {"instrument", "ts", "datetime", "open", "high",
                               "low", "close", "volume"}
    r = df.iloc[0]
    assert r["instrument"] == "NIFTY"
    assert (r["open"], r["high"], r["low"], r["close"]) == (1.0, 2.0, 0.5, 1.5)
    assert r["volume"] == 7.0


def test_the_datetime_column_is_ist_iso():
    df = _fetch(_Client([_row(OPEN_0814)]))
    assert df.iloc[0]["datetime"].startswith("2026-08-14T09:15:00+05:30")


def test_an_empty_response_yields_an_empty_frame():
    assert _fetch(_Client([])).empty


def test_an_all_out_of_session_response_yields_an_empty_frame():
    assert _fetch(_Client([_row(OPEN_0814 - MIN_MS)])).empty


def test_a_broker_error_propagates_so_recovery_can_report_it():
    """recover_day catches this and records it; swallowing it here would make a
    broker outage look like 'no data available'."""
    import pytest
    with pytest.raises(RuntimeError):
        _fetch(_Client(boom=True))
