"""Offline read-only chart fixtures: no real credentials, HTTP, Mongo or orders."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import chart_data as cd


def ts(iso):
    return int(datetime.fromisoformat(iso).timestamp())


NOW = ts("2026-11-01T12:00:00+05:30")
OPEN = ts("2026-10-30T09:15:00+05:30")
EQUITY = "NSE_EQ|INE000000001"
BSE_EQUITY = "BSE_EQ|INE000000002"


def run(coro):
    return asyncio.run(coro)


def master_row(key=EQUITY, typ="EQ", symbol="ALPHA", **kwargs):
    segment = key.split("|")[0]
    return {"instrument_key": key, "segment": segment, "exchange": segment[:3],
            "instrument_type": typ, "trading_symbol": symbol, "name": symbol + " LIMITED",
            "exchange_token": "1001", "lot_size": 1, "tick_size": 5, **kwargs}


def saved_row(stamp, o=100, h=104, l=98, c=102, volume=100):
    return dict(ts=stamp * 1000, open=o, high=h, low=l, close=c, volume=volume)


def candle(stamp, o=100, h=104, l=98, c=102, volume=100):
    iso = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    return [iso, o, h, l, c, volume, 0]


def payload(rows):
    return {"status": "success", "data": {"candles": rows}}


class Fixture:
    def __init__(self, *, rows=(), master=None, broker=None, now=NOW):
        self.rows = list(rows)
        self.master = [master_row()] if master is None else master
        self.broker = broker
        self.now = now
        self.calls, self.warehouse_calls = [], []
        self.master_calls = 0
        self.service = cd.ChartDataService(master_loader=self.load_master,
            authenticated_get=self.get, warehouse_loader=self.warehouse, clock=lambda: self.now)

    async def load_master(self):
        self.master_calls += 1
        if isinstance(self.master, Exception):
            raise self.master
        return self.master

    async def get(self, url):
        self.calls.append(url)
        if isinstance(self.broker, Exception):
            raise self.broker
        if callable(self.broker):
            return self.broker(url)
        return payload(self.broker or [])

    async def warehouse(self, symbol, start, end):
        self.warehouse_calls.append((symbol, start, end))
        return pd.DataFrame([r for r in self.rows if start <= r["ts"] <= end])


@pytest.mark.parametrize(("timeframe", "input_time", "expected"), [
    ("1m", "2026-10-30T09:16:59+05:30", "2026-10-30T09:16:00+05:30"),
    ("3m", "2026-10-30T09:17:59+05:30", "2026-10-30T09:15:00+05:30"),
    ("5m", "2026-10-30T09:21:00+05:30", "2026-10-30T09:20:00+05:30"),
    ("15m", "2026-10-30T09:29:59+05:30", "2026-10-30T09:15:00+05:30"),
    ("30m", "2026-10-30T10:14:59+05:30", "2026-10-30T09:45:00+05:30"),
    ("1h", "2026-10-30T10:14:59+05:30", "2026-10-30T09:15:00+05:30"),
    ("1d", "2026-10-30T23:59:59+05:30", "2026-10-30T00:00:00+05:30"),
    ("1w", "2026-11-01T23:59:59+05:30", "2026-10-26T00:00:00+05:30"),
    ("1w", "2026-11-02T00:00:00+05:30", "2026-11-02T00:00:00+05:30"),
])
def test_bucket_alignment_from_independent_calendar_arithmetic(timeframe, input_time, expected):
    assert cd.chart_bucket_time(ts(input_time), timeframe) == ts(expected)


@pytest.mark.parametrize("timeframe", ["1m", "3m", "5m", "15m", "30m", "1h"])
def test_bucket_alignment_matches_independent_pandas_resample(timeframe):
    dates = pd.date_range("2026-10-30 09:15", periods=375, freq="min", tz="Asia/Kolkata")
    frame = pd.DataFrame({"stamp": [int(d.timestamp()) for d in dates]}, index=dates)
    rule = f"{cd.TIMEFRAME_SECONDS[timeframe] // 60}min"
    for expected_bucket, group in frame.resample(rule, origin="start_day", offset="9h15min"):
        assert {cd.chart_bucket_time(s, timeframe) for s in group.stamp} == {int(expected_bucket.timestamp())}


def test_saved_aliases_are_network_independent_and_copies():
    fixture = Fixture(master=RuntimeError("network down"))
    first = run(fixture.service.resolve("nifty"))
    first["symbol"] = "mutated"
    assert run(fixture.service.resolve("NSE_INDEX|Nifty 50"))["symbol"] == "NIFTY"
    assert run(fixture.service.resolve("BANKNIFTY"))["warehouse_symbol"] == "BANKNIFTY"
    assert run(fixture.service.resolve("SENSEX"))["exchange"] == "BSE"
    assert fixture.master_calls == 0
    assert fixture.calls == []


def test_catalogue_covers_cash_groups_indices_and_current_derivative_identity():
    expiry = ts("2026-11-26T23:59:59+05:30") * 1000
    rows = [master_row(), master_row(BSE_EQUITY, "A", "BETA"),
            master_row("BSE_EQ|INE000000003", "X", "SMALL"),
            master_row("NSE_EQ|INE000000004", "BE", "DELIVERY"),
            master_row("NSE_INDEX|Nifty IT", "INDEX", "NIFTY IT"),
            master_row("BSE_INDEX|BANKEX", "INDEX", "BANKEX"),
            master_row("NSE_FO|123", "CE", "ALPHA 100 CE", expiry=expiry, strike_price=100, underlying_symbol="ALPHA"),
            master_row("BSE_FO|124", "FUT", "BETA FUT", expiry=expiry),
            master_row("BSE_FO|125", "PE", "BETA 100 PE", expiry=expiry, strike_price=100),
            master_row("NSE_FO|999", "CE", "OLD", expiry=ts("2026-10-29T23:59:59+05:30") * 1000, strike_price=100),
            master_row("NCD_FO|1", "CE", "CURRENCY", expiry=expiry, strike_price=100),
            master_row("MCX_FO|1", "FUT", "GOLD", expiry=expiry),
            master_row("NSE_EQ|DEBT", "SG", "STATE BOND"),
            master_row("BSE_EQ|DEBT", "F", "BOND"),
            master_row("NSE_EQ|WRONG", "EQ", "WRONG EXCHANGE", exchange="BSE")]
    fixture = Fixture(master=rows)
    result = run(fixture.service.search(limit=100))
    keys = {item["instrument_key"] for item in result["items"]}
    assert keys == {r["instrument_key"] for r in rows[:9]} | set(cd._saved_instruments())
    assert result["warnings"] == []
    option = run(fixture.service.resolve("NSE_FO|123"))
    assert {k: option[k] for k in ("kind", "expiry", "strike", "option_type", "exchange_token", "underlying")} == {
        "kind": "option", "expiry": "2026-11-26", "strike": 100, "option_type": "CE", "exchange_token": "1001", "underlying": "ALPHA"}
    assert "tick_size" not in option  # undocumented JSON units must not escape as rupees
    assert run(fixture.service.resolve("BSE_FO|124"))["kind"] == "future"
    assert run(fixture.service.resolve("BSE_INDEX|BANKEX"))["volume_available"] is False
    assert fixture.master_calls == 1


def test_catalogue_refresh_backoff_fallback_and_expiry_rechecked():
    now = ts("2026-11-02T23:59:50+05:30")
    expiry = ts("2026-11-02T23:59:59+05:30") * 1000
    fixture = Fixture(now=now, master=[master_row("NSE_FO|1", "CE", "TODAY", expiry=expiry, strike_price=100)])
    assert run(fixture.service.resolve("NSE_FO|1"))["expiry"] == "2026-11-02"
    fixture.now += 20
    with pytest.raises(ValueError, match="expired"):
        run(fixture.service.resolve("NSE_FO|1"))
    assert not run(fixture.service.search("TODAY"))["items"]
    fixture.now += cd.CATALOG_TTL
    fixture.master = RuntimeError("private backend message")
    result = run(fixture.service.search())
    assert result["warnings"] and len(result["items"]) == 3
    run(fixture.service.search())
    assert fixture.master_calls == 2
    assert "private" not in str(result)


def test_search_tokens_exact_symbol_priority_and_limits():
    fixture = Fixture(master=[master_row(), master_row("BSE_EQ|BETA", "X", "ALPHA BETA")])
    assert run(fixture.service.search("alpha", 1))["items"][0]["instrument_key"] == EQUITY
    assert run(fixture.service.search("alpha bse"))["items"][0]["instrument_key"] == "BSE_EQ|BETA"
    assert run(fixture.service.search("no such equity"))["items"] == []
    for limit in (0, -1, 101, True, 1.5):
        with pytest.raises(ValueError):
            run(fixture.service.search(limit=limit))


def test_warehouse_survives_broker_failure_with_valid_sorted_unique_ohlc():
    rows = [saved_row(OPEN + 60, 102, 110, 101, 107), saved_row(OPEN),
            saved_row(OPEN + 60, 102, 111, 100, 108),
            saved_row(OPEN + 120, 108, 112, 103, 109), saved_row(OPEN + 300, 109, 115, 105, 113),
            saved_row(OPEN + 360, h=float("nan")), saved_row(OPEN + 420, h=90)]
    fixture = Fixture(rows=rows, broker=RuntimeError("Bearer do-not-expose-this"))
    result = run(fixture.service.history("NIFTY", "5m", 20))
    assert result["bars"] == [
        dict(time=OPEN, open=100, high=112, low=98, close=109, volume=None),
        dict(time=OPEN + 300, open=109, high=115, low=105, close=113, volume=None)]
    assert result["source"] == "warehouse"
    assert result["volume_available"] is False
    assert any("Upstox" in w for w in result["warnings"])
    assert any("Invalid saved" in w for w in result["warnings"])
    assert "do-not-expose" not in str(result)
    assert len(fixture.calls) == 1


def test_exclusive_history_pagination_never_repeats_the_boundary():
    fixture = Fixture(rows=[saved_row(OPEN + i * 60) for i in range(7)], broker=RuntimeError("offline"))
    first = run(fixture.service.history("NIFTY", limit=3))
    assert [b["time"] for b in first["bars"]] == [OPEN + i * 60 for i in (4, 5, 6)]
    second = run(fixture.service.history("NIFTY", limit=3, before=first["next_before"]))
    assert [b["time"] for b in second["bars"]] == [OPEN + i * 60 for i in (1, 2, 3)]
    third = run(fixture.service.history("NIFTY", limit=3, before=second["next_before"]))
    assert [b["time"] for b in third["bars"]] == [OPEN]
    assert all(b["time"] < first["next_before"] for b in second["bars"])
    assert fixture.warehouse_calls[1][2] == first["next_before"] * 1000 - 1


@pytest.mark.parametrize(("timeframe", "native_path", "stamp"), [
    ("1m", "/minutes/1/", OPEN), ("3m", "/minutes/3/", OPEN),
    ("5m", "/minutes/5/", OPEN), ("15m", "/minutes/15/", OPEN),
    ("30m", "/minutes/30/", OPEN), ("1h", "/hours/1/", OPEN),
    ("1d", "/days/1/", ts("2026-10-30T00:00:00+05:30")),
    ("1w", "/days/1/", ts("2026-10-26T00:00:00+05:30")),
])
def test_generic_key_history_native_intervals_and_volume(timeframe, native_path, stamp):
    fixture = Fixture(broker=[candle(OPEN, volume=1234)])
    result = run(fixture.service.history(EQUITY, timeframe, 10))
    assert result["bars"] == [dict(time=stamp, open=100, high=104, low=98, close=102, volume=1234)]
    assert result["source"] == "upstox"
    assert result["volume_available"] is True
    assert any("NSE_EQ%7CINE000000001" + native_path in call for call in fixture.calls)
    assert not fixture.warehouse_calls
    assert len(fixture.calls) <= cd.MAX_HISTORY_REQUESTS + 1


def test_daily_and_weekly_combine_today_with_prior_days_without_double_volume():
    now = ts("2026-10-30T14:00:00+05:30")
    monday = ts("2026-10-26T00:00:00+05:30")
    def broker(url):
        if "/intraday/" in url:
            return payload([candle(OPEN, 110, 117, 109, 115, 50)])
        return payload([candle(monday + 3 * cd.DAY, 105, 113, 104, 110, 40),
                        candle(monday, 100, 107, 95, 105, 10),
                        candle(monday, 100, 108, 94, 105, 20)])
    fixture = Fixture(broker=broker, now=now)
    result = run(fixture.service.history(EQUITY, "1w", 5))
    assert result["bars"] == [dict(time=monday, open=100, high=117, low=94, close=115, volume=110)]


def test_saved_finalized_buckets_win_conflicts_and_rest_fills_gaps():
    fixture = Fixture(rows=[saved_row(OPEN, 100, 104, 98, 102)],
                      broker=[candle(OPEN, 99, 115, 96, 112, 200), candle(OPEN + 300)])
    result = run(fixture.service.history("NIFTY", "5m", 2))
    assert result["bars"] == [dict(time=OPEN, open=100, high=104, low=98, close=102, volume=None),
                              dict(time=OPEN + 300, open=100, high=104, low=98, close=102, volume=None)]
    assert result["source"] == "warehouse+upstox"
    assert any("take precedence" in w for w in result["warnings"])


def test_rest_reconciles_only_the_currently_forming_saved_bucket():
    fixture = Fixture(now=OPEN + 180, rows=[saved_row(OPEN, 100, 104, 98, 102)],
                      broker=[candle(OPEN, 99, 115, 96, 112, 200)])
    result = run(fixture.service.history("NIFTY", "5m", 2))
    assert result["bars"] == [dict(time=OPEN, open=99, high=115, low=96, close=112, volume=None)]
    assert any("forming bucket" in w for w in result["warnings"])


def test_off_hours_saved_candles_preserve_special_sessions_with_warning():
    evening = ts("2026-10-30T18:30:00+05:30")
    fixture = Fixture(rows=[saved_row(evening)], broker=RuntimeError("offline"))
    result = run(fixture.service.history("NIFTY"))
    assert result["bars"][0]["time"] == evening
    assert any("special sessions or feed artifacts" in w for w in result["warnings"])


def test_unaligned_native_bars_and_invalid_timestamps_are_not_relabelled():
    fixture = Fixture(broker=[candle(OPEN + 60), ["2026-10-30T09:15:00", 100, 104, 98, 102, 5],
                              candle(OPEN, h=90), candle(OPEN, c=float("inf")),
                              candle(OPEN + 300, volume=None), ["invalid"]])
    result = run(fixture.service.history(EQUITY, "5m", 10))
    assert [b["time"] for b in result["bars"]] == [OPEN + 300]
    assert result["bars"][0]["volume"] is None
    assert any("misaligned" in w for w in result["warnings"])


def test_historical_ranges_response_size_and_warehouse_reads_are_bounded():
    fixture = Fixture()
    run(fixture.service.history("NIFTY", "1m", cd.MAX_HISTORY_BARS))
    historical = [url for url in fixture.calls if "/intraday/" not in url]
    assert len(historical) == cd.MAX_HISTORY_REQUESTS
    for url in historical:
        end, start = (datetime.fromisoformat(v) for v in url.rsplit("/", 2)[1:])
        assert timedelta(0) <= end - start < timedelta(days=28)
    _, start, end = fixture.warehouse_calls[0]
    assert end - start < cd.MAX_WAREHOUSE_DAYS * cd.DAY * 1000
    with pytest.raises(ValueError, match="memory"):
        cd._broker_bars(payload([[]] * (cd.MAX_RESPONSE_ROWS + 1)), "1m", True, NOW)


def test_cache_is_bounded_detached_and_ttl_does_not_forge_asof():
    fixture = Fixture(broker=[candle(OPEN)])
    first = run(fixture.service.history(EQUITY, limit=1))
    calls = len(fixture.calls)
    first["bars"][0]["close"] = -100
    fixture.now += 5
    second = run(fixture.service.history(EQUITY, limit=1))
    assert second["bars"][0]["close"] == 102
    assert second["as_of_ms"] == NOW * 1000
    assert len(fixture.calls) == calls
    fixture.now += 11
    assert run(fixture.service.history(EQUITY, limit=1))["as_of_ms"] == fixture.now * 1000
    assert len(fixture.calls) > calls
    for i in range(cd.HISTORY_CACHE_SIZE + 2):
        run(fixture.service.history(EQUITY, limit=1, before=OPEN - 60 * i))
    assert len(fixture.service._history_cache) == cd.HISTORY_CACHE_SIZE


@pytest.mark.parametrize("kwargs", [dict(timeframe="2d"), dict(limit=0), dict(limit=2001),
                                   dict(limit=True), dict(before=0), dict(before=float("inf")),
                                   dict(before=OPEN + .5)])
def test_bad_requests_fail_before_any_io(kwargs):
    fixture = Fixture()
    with pytest.raises(ValueError):
        run(fixture.service.history(EQUITY, **kwargs))
    assert fixture.master_calls == 0 and fixture.calls == [] and fixture.warehouse_calls == []


def test_empty_or_malformed_broker_response_does_not_fabricate_prices():
    fixture = Fixture(broker=lambda _: {"status": "error", "data": {"candles": []}})
    result = run(fixture.service.history(EQUITY))
    assert result["bars"] == [] and result["source"] == "unavailable"
    assert result["next_before"] is None and result["has_more"] is False
    assert any("unavailable" in w for w in result["warnings"])


def test_before_precedes_provider_history_floor_without_invalid_request():
    fixture = Fixture()
    result = run(fixture.service.history(EQUITY, before=ts("1999-01-01T00:00:00+05:30")))
    assert not result["bars"] and not result["has_more"]
    assert not fixture.calls


def test_default_warehouse_reads_latest_saved_data_even_months_after_disconnect(monkeypatch):
    from types import SimpleNamespace
    from app import db, warehouse
    old = ts("2026-06-01T18:30:00+05:30")
    seed = [dict(instrument="NIFTY", **saved_row(old + i * 60)) for i in range(4)]
    observed = []

    class Collection:
        async def find_one(self, query, projection, sort):
            observed.append(("latest", query, sort))
            rows = [r for r in seed if r["ts"] <= query["ts"]["$lte"]]
            return max(rows, key=lambda r: r["ts"]) if rows else None

        def find(self, query, projection):
            observed.append(("range", query))
            lower, upper = query["ts"]["$gte"], query["ts"]["$lte"]
            selected = [r for r in seed if lower <= r["ts"] <= upper]

            class Cursor:
                def sort(self, field, order):
                    assert (field, order) == ("ts", 1)
                    return self

                async def to_list(self, length):
                    assert length == 200000
                    return selected
            return Cursor()

    fake_db = SimpleNamespace(candles_1m=Collection())
    monkeypatch.setattr(db, "get_db", lambda: fake_db)
    monkeypatch.setattr(warehouse, "get_db", lambda: fake_db)
    async def offline(url):
        raise RuntimeError("expired")
    service = cd.ChartDataService(authenticated_get=offline, clock=lambda: NOW)
    first = run(service.history("NIFTY", limit=2))
    assert [b["time"] for b in first["bars"]] == [old + 120, old + 180]
    assert first["source"] == "warehouse"
    assert any("predate" in w for w in first["warnings"])
    second = run(service.history("NIFTY", limit=2, before=first["next_before"]))
    assert [b["time"] for b in second["bars"]] == [old, old + 60]
    for kind, query, *rest in observed:
        if kind == "range":
            assert query["ts"]["$lte"] - query["ts"]["$gte"] < cd.MAX_WAREHOUSE_DAYS * cd.DAY * 1000


def test_duplicate_master_identities_cannot_resolve_a_reused_token():
    expiry = ts("2026-11-26T23:59:59+05:30") * 1000
    first = master_row("NSE_FO|1", "CE", "ALPHA CE", expiry=expiry, strike_price=100)
    second = {**first, "strike_price": 200, "trading_symbol": "ALPHA OTHER CE"}
    fixture = Fixture(master=[master_row(), first, second])
    result = run(fixture.service.search())
    assert any("Ambiguous" in w for w in result["warnings"])
    with pytest.raises(ValueError, match="Unknown"):
        run(fixture.service.resolve("NSE_FO|1"))


def test_peek_history_is_detached_includes_expired_entries_and_never_does_io():
    fixture = Fixture(rows=[saved_row(OPEN)], broker=RuntimeError("offline"))
    assert fixture.service.peek_history("NIFTY") is None
    assert fixture.calls == [] and fixture.warehouse_calls == []
    result = run(fixture.service.history("NIFTY"))
    counts = (len(fixture.calls), len(fixture.warehouse_calls), fixture.master_calls)
    fixture.now += 60
    cached = fixture.service.peek_history("NSE_INDEX|Nifty 50")
    assert cached == result
    cached["bars"][0]["close"] = -1
    assert fixture.service.peek_history("nifty")["bars"][0]["close"] == 102
    assert counts == (len(fixture.calls), len(fixture.warehouse_calls), fixture.master_calls)


def test_history_completion_watermark_distinguishes_same_millisecond_fetches():
    fixture = Fixture(rows=[saved_row(OPEN)], broker=RuntimeError("offline"))
    first = run(fixture.service.history("NIFTY", limit=1))
    second = run(fixture.service.history("NIFTY", limit=2))
    assert second["as_of_ms"] == first["as_of_ms"] + 1
