"""Flattrade TPSeries — the fallback historical source when Upstox is unavailable.

Decoded spec endpoint #29 (`docs/Resources/flattrade-pi-api/endpoints/
29-get-time-price-data.md`). Unlike Upstox's intraday endpoint — which takes NO
date arguments and returns today wholesale — TPSeries accepts explicit `st`/`et`
epoch-second bounds, so a gap can be requested precisely.

Two details in the vendor sample bite if ignored, and both are pinned here:

* the sample returns ``"time":"02-06-2020 15:46:23"`` — DASHES where the field
  table says slashes, and seconds that are NOT on a minute boundary. An unaligned
  ts writes rows that never match the `candles_1m` minute grid, so the gap would
  stay open while appearing to have been filled;
* rows arrive NEWEST FIRST.

Response parsing is tested against the documented shape. The live call itself was
exercised separately (see the commit message) — after the jData regression, the
standing lesson is that a transport contract cannot be validated against a mock
that shares the implementation's assumption.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.broker_protocol import BrokerReadError  # noqa: E402
from app.live.flattrade_client import (  # noqa: E402
    FlattradeClient,
    _parse_noren_time_ms,
)

# 2026-08-14 09:15:00 IST
OPEN_MS = 1786679100000


class _C(FlattradeClient):
    def __init__(self, response):
        self._jKey, self._uid, self._actid = "K", "FZ00000", "FZ00000"
        self._response = response
        self.sent = None

    async def _post(self, route, jdata):
        self.sent = (route, jdata)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _row(time_str, o=100.0, h=101.0, low=99.0, c=100.5, v="0"):
    return {"stat": "Ok", "time": time_str, "into": str(o), "inth": str(h),
            "intl": str(low), "intc": str(c), "intvwap": "0.00", "intv": v,
            "intoi": "0", "v": "980515", "oi": "128702"}


def _call(client, **kw):
    kw.setdefault("exch", "NSE")
    kw.setdefault("token", "26000")
    kw.setdefault("start_ms", OPEN_MS)
    kw.setdefault("end_ms", OPEN_MS + 3_600_000)
    return asyncio.run(client.time_price_series(**kw))


# --------------------------------------------------------------------------
# The timestamp parser — where the traps are
# --------------------------------------------------------------------------

def test_the_vendor_sample_format_with_DASHES_parses():
    assert _parse_noren_time_ms("02-06-2020 15:46:23") is not None


def test_the_documented_format_with_SLASHES_also_parses():
    a = _parse_noren_time_ms("14/08/2026 09:15:00")
    b = _parse_noren_time_ms("14-08-2026 09:15:00")
    assert a == b == OPEN_MS


def test_unaligned_seconds_are_FLOORED_to_the_minute():
    """15:46:23 must become 15:46:00 — otherwise the row never matches the grid."""
    aligned = _parse_noren_time_ms("14-08-2026 09:15:00")
    unaligned = _parse_noren_time_ms("14-08-2026 09:15:47")
    assert aligned == unaligned == OPEN_MS
    assert unaligned % 60_000 == 0


def test_the_time_is_read_as_IST_not_UTC():
    assert _parse_noren_time_ms("14-08-2026 09:15:00") == OPEN_MS


def test_junk_times_return_none_rather_than_raising():
    for bad in (None, "", "  ", "not a time", "2026-08-14T09:15:00", 42, {}):
        assert _parse_noren_time_ms(bad) is None


# --------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------

def test_the_request_carries_epoch_SECONDS_and_a_1_minute_interval():
    c = _C([])
    _call(c, start_ms=OPEN_MS, end_ms=OPEN_MS + 60_000)
    route, jd = c.sent
    assert route == "TPSeries"
    assert jd["st"] == str(OPEN_MS // 1000)
    assert jd["et"] == str((OPEN_MS + 60_000) // 1000)
    assert jd["intrv"] == "1"
    assert jd["exch"] == "NSE" and jd["token"] == "26000"
    assert jd["uid"] == "FZ00000"


def test_the_token_is_sent_as_a_string():
    """Noren rejects a numeric token; the index tokens are 26000 / 26009 / 1."""
    c = _C([])
    _call(c, token=26000)
    assert c.sent[1]["token"] == "26000"


# --------------------------------------------------------------------------
# The response
# --------------------------------------------------------------------------

def test_rows_are_normalised_and_sorted_ASCENDING():
    """The vendor returns newest-first."""
    c = _C([_row("14-08-2026 09:17:00", c=3.0),
            _row("14-08-2026 09:16:00", c=2.0),
            _row("14-08-2026 09:15:00", c=1.0)])
    rows = _call(c)
    assert [r["close"] for r in rows] == [1.0, 2.0, 3.0]
    assert [r["ts"] for r in rows] == [OPEN_MS, OPEN_MS + 60_000, OPEN_MS + 120_000]


def test_ohlc_maps_from_the_noren_field_names():
    c = _C([_row("14-08-2026 09:15:00", o=10.0, h=12.0, low=9.0, c=11.0, v="55")])
    r = _call(c)[0]
    assert (r["open"], r["high"], r["low"], r["close"]) == (10.0, 12.0, 9.0, 11.0)
    assert r["volume"] == 55.0


def test_an_empty_window_returns_empty_not_an_error():
    assert _call(_C([])) == []


def test_a_no_data_response_is_empty_not_an_error():
    c = _C({"stat": "Not_Ok", "emsg": 'Error Occurred : 5 "no data"'})
    assert _call(c) == []


def test_a_real_failure_RAISES_rather_than_looking_empty():
    """An unreadable window must never be mistaken for 'no candles here'."""
    c = _C({"stat": "Not_Ok", "emsg": "Session Expired : Invalid Session Key"})
    with pytest.raises(BrokerReadError):
        _call(c)


def test_an_unexpected_shape_raises():
    with pytest.raises(BrokerReadError):
        _call(_C("a string"))


def test_rows_with_unparseable_times_are_skipped_not_fatal():
    c = _C([_row("garbage"), _row("14-08-2026 09:15:00")])
    rows = _call(c)
    assert len(rows) == 1 and rows[0]["ts"] == OPEN_MS


def test_rows_with_junk_prices_are_skipped():
    bad = _row("14-08-2026 09:16:00")
    bad["intc"] = "abc"
    c = _C([bad, _row("14-08-2026 09:15:00")])
    assert len(_call(c)) == 1


def test_non_finite_prices_are_skipped():
    bad = _row("14-08-2026 09:16:00")
    bad["inth"] = "NaN"
    c = _C([bad, _row("14-08-2026 09:15:00")])
    assert len(_call(c)) == 1


def test_a_single_dict_success_row_is_accepted():
    """Some Noren routes return one dict where a list is documented."""
    c = _C(_row("14-08-2026 09:15:00"))
    assert len(_call(c)) == 1
