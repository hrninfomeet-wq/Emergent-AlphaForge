"""Executes the screen CLI's DATABASE-touching functions against a fake Mongo.

`validate_spot`, `validate_options` and `build_atm_series` had never been run
when they were written — only their pure helpers were covered. That is the exact
shape of defect this repository keeps paying for: a query whose field name is
wrong returns nothing, and the script then reports "no ATM option series could be
built ... this is a DATA finding", blaming the warehouse for a typo.

The fake below mimics only the slice of the pymongo sync API these functions use,
and it is deliberately STRICT: unknown query operators raise rather than silently
matching nothing, because silently matching nothing is the failure being guarded
against.

Document shapes mirror the real writers:
  * `options_1m`      — app/option_candles.py (`underlying`, `side`, `contract_key`, `oi`)
  * `option_contracts`— app/options_universe.py normalises to `side`, never `option_type`
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

IST = timezone(timedelta(hours=5, minutes=30))


def _load():
    if "pymongo" not in sys.modules:
        stub = types.ModuleType("pymongo")
        stub.MongoClient = object
        sys.modules["pymongo"] = stub
    path = ROOT / "backend" / "scripts" / "screen_option_buying.py"
    spec = importlib.util.spec_from_file_location("screen_option_buying_db", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


screen = _load()


# ---------------------------------------------------------------------------
# A deliberately strict fake Mongo
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, field, direction=1):
        self._docs.sort(key=lambda d: d.get(field), reverse=(direction == -1))
        return self

    def limit(self, n):
        self._docs = self._docs[:int(n)]
        return self

    def __iter__(self):
        return iter(self._docs)


def _matches(doc, query):
    for key, want in (query or {}).items():
        have = doc.get(key)
        if isinstance(want, dict):
            for op, val in want.items():
                if op == "$gte":
                    if have is None or have < val:
                        return False
                elif op == "$lt":
                    if have is None or have >= val:
                        return False
                elif op == "$lte":
                    if have is None or have > val:
                        return False
                elif op == "$gt":
                    if have is None or have <= val:
                        return False
                else:
                    raise AssertionError(f"fake Mongo does not implement {op!r}")
        elif have != want:
            return False
    return True


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, query=None, projection=None):
        return FakeCursor([d for d in self.docs if _matches(d, query)])

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return dict(d)
        return None

    def estimated_document_count(self):
        return len(self.docs)

    def count_documents(self, query=None, limit=None):
        n = sum(1 for d in self.docs if _matches(d, query))
        return min(n, limit) if limit else n

    def aggregate(self, pipeline):
        """Supports the $match / $sample / $group shape _oi_population builds."""
        rows = list(self.docs)
        for stage in pipeline:
            if "$match" in stage:
                rows = [d for d in rows if _matches(d, stage["$match"])]
            elif "$sample" in stage:
                rows = rows[:int(stage["$sample"]["size"])]   # deterministic stand-in
            elif "$group" in stage:
                if not rows:
                    return []
                spec = stage["$group"]
                field = spec["with_oi"]["$sum"]["$cond"][0]["$gt"][0].lstrip("$")
                return [{
                    "_id": None,
                    "n": len(rows),
                    "with_oi": sum(1 for d in rows if (d.get(field) or 0) > 0),
                }]
            else:
                raise AssertionError(f"fake Mongo aggregate stage {stage!r}")
        return rows


class FakeDB:
    def __init__(self, **collections):
        for name in ("candles_1m", "options_1m", "option_contracts",
                     "chain_snapshots", "ticks"):
            setattr(self, name, FakeCollection(collections.get(name)))


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _ms(date: str, hhmm: str) -> int:
    h, m = hhmm.split(":")
    dt = datetime.strptime(date, "%Y-%m-%d").replace(
        hour=int(h), minute=int(m), tzinfo=IST)
    return int(dt.timestamp() * 1000)


def _spot_session(date, *, bars=375, close=24_512.0, start="09:15"):
    h0, m0 = (int(x) for x in start.split(":"))
    base = _ms(date, start)
    return [{"instrument": "NIFTY", "ts": base + i * 60_000, "close": close}
            for i in range(bars)]


def _option_bars(date, contract_key, *, n=60, oi=1_000, premium=140.0, start="09:15"):
    base = _ms(date, start)
    return [{
        "contract_key": contract_key, "instrument_key": "NSE_FO|1",
        "underlying": "NIFTY", "side": contract_key[-2:],
        "ts": base + i * 60_000,
        "open": premium, "high": premium + 1, "low": premium - 1,
        "close": premium, "volume": 500, "oi": oi,
    } for i in range(n)]


def _contracts(expiry="2026-08-25", strike=24_500):
    return [
        {"underlying": "NIFTY", "strike": strike, "side": "CE",
         "expiry_date": expiry, "instrument_key": "NSE_FO|1",
         "contract_key": "TOK|CE", "lot_size": 65},
        {"underlying": "NIFTY", "strike": strike, "side": "PE",
         "expiry_date": expiry, "instrument_key": "NSE_FO|2",
         "contract_key": "TOK|PE", "lot_size": 65},
    ]


# ---------------------------------------------------------------------------
# validate_spot
# ---------------------------------------------------------------------------

def test_validate_spot_counts_sessions_and_flags_partials():
    db = FakeDB(candles_1m=(_spot_session("2026-08-20", bars=375)
                            + _spot_session("2026-08-21", bars=100)))
    out = screen.validate_spot(db, "NIFTY")

    assert out["sessions"] == 2
    assert out["complete_sessions"] == 1
    assert out["partial_sessions"] == 1
    assert out["worst_partials"][0]["date"] == "2026-08-21"
    assert out["worst_partials"][0]["bars"] == 100
    assert out["first_session"] == "2026-08-20"
    assert out["last_session"] == "2026-08-21"


def test_validate_spot_reports_an_empty_warehouse_rather_than_crashing():
    out = screen.validate_spot(FakeDB(), "NIFTY")
    assert out["error"] == "no spot candles"
    assert out["sessions"] == 0


def test_validate_spot_uses_the_95_percent_completeness_bar():
    """357/375 is the forward-validation policy's own threshold."""
    db = FakeDB(candles_1m=_spot_session("2026-08-20", bars=357))
    assert screen.validate_spot(db, "NIFTY")["complete_sessions"] == 1
    db = FakeDB(candles_1m=_spot_session("2026-08-20", bars=356))
    assert screen.validate_spot(db, "NIFTY")["partial_sessions"] == 1


# ---------------------------------------------------------------------------
# validate_options + the OI estimate
# ---------------------------------------------------------------------------

def test_validate_options_reads_contracts_and_reports_oi():
    db = FakeDB(option_contracts=_contracts(),
                options_1m=_option_bars("2026-08-20", "TOK|CE", oi=5_000))
    out = screen.validate_options(db, "NIFTY")

    assert out["contracts"] == 2
    assert out["expiries"] == 1
    assert out["lot_sizes_seen"] == [65]
    assert out["contract_key_coverage_pct"] == 100.0
    assert out["oi_population"]["pct"] == 100.0
    assert out["oi_population"]["scope"] == "instrument"


def test_the_oi_estimate_reports_a_real_share_not_a_saturating_count():
    """The bug this replaced: count_documents(limit=N)/min(total,N) reads ~100%
    for any warehouse holding N populated rows, whatever the true share is."""
    bars = (_option_bars("2026-08-20", "TOK|CE", n=10, oi=1_000)
            + _option_bars("2026-08-21", "TOK|CE", n=90, oi=0))
    db = FakeDB(option_contracts=_contracts(), options_1m=bars)

    oi = screen.validate_options(db, "NIFTY")["oi_population"]
    assert oi["sampled"] == 100
    assert oi["with_oi"] == 10
    assert oi["pct"] == 10.0          # the truth, not 100.0


def test_a_fully_unpopulated_oi_column_reads_zero():
    """The go/no-go signal for candidate A. It must be able to say zero."""
    db = FakeDB(option_contracts=_contracts(),
                options_1m=_option_bars("2026-08-20", "TOK|CE", n=50, oi=0))
    assert screen.validate_options(db, "NIFTY")["oi_population"]["pct"] == 0.0


def test_oi_falls_back_and_says_so_when_rows_lack_the_underlying_field():
    bars = _option_bars("2026-08-20", "TOK|CE", n=40, oi=7)
    for b in bars:
        del b["underlying"]
    db = FakeDB(option_contracts=_contracts(), options_1m=bars)

    oi = screen.validate_options(db, "NIFTY")["oi_population"]
    assert oi["scope"] == "all-instruments"
    assert "underlying" in oi["note"]
    assert oi["pct"] == 100.0


def test_validate_options_says_so_when_no_contracts_match():
    out = screen.validate_options(FakeDB(), "NIFTY")
    assert out["contracts"] == 0
    assert "underlying" in out["note"]


def test_empty_options_collection_does_not_divide_by_zero():
    db = FakeDB(option_contracts=_contracts())
    oi = screen.validate_options(db, "NIFTY")["oi_population"]
    assert oi["pct"] is None
    assert oi["scope"] == "none"


# ---------------------------------------------------------------------------
# build_atm_series — the query that was wrong
# ---------------------------------------------------------------------------

def _full_db():
    return FakeDB(
        candles_1m=_spot_session("2026-08-20", bars=375, close=24_512.0),
        option_contracts=_contracts(expiry="2026-08-25", strike=24_500),
        options_1m=(_option_bars("2026-08-20", "TOK|CE", n=60)
                    + _option_bars("2026-08-20", "TOK|PE", n=60)),
    )


def test_build_atm_series_finds_both_legs():
    """Would have returned EMPTY under the `option_type` typo, and the CLI would
    have blamed the warehouse for it."""
    frame = screen.build_atm_series(
        _full_db(), "NIFTY", ["2026-08-20"], dte_filter=None,
        entry_from="09:25", entry_to="14:48")

    assert not frame.empty
    assert set(frame["side"]) == {"CE", "PE"}
    assert set(frame["strike"]) == {24_500}          # 24,512 -> nearest 50
    assert set(frame["session_date"]) == {"2026-08-20"}
    assert {"ts", "open", "high", "low", "close", "oi", "ist"} <= set(frame.columns)


def test_contracts_are_matched_on_side_not_option_type():
    """Pin the field name directly — the defect was a silent no-match."""
    db = _full_db()
    for c in db.option_contracts.docs:                # rename side -> option_type
        c["option_type"] = c.pop("side")
    frame = screen.build_atm_series(db, "NIFTY", ["2026-08-20"], dte_filter=None,
                                    entry_from="09:25", entry_to="14:48")
    assert frame.empty          # proves the query really keys on `side`

    source = (ROOT / "backend" / "scripts" / "screen_option_buying.py").read_text()
    assert '"side": side' in source
    assert '"option_type"' not in source


def test_dte_filter_excludes_a_session_outside_the_range():
    db = _full_db()          # 2026-08-20 session, 2026-08-25 expiry
    kept = screen.build_atm_series(db, "NIFTY", ["2026-08-20"], dte_filter=None,
                                   entry_from="09:25", entry_to="14:48")
    assert not kept.empty
    dropped = screen.build_atm_series(db, "NIFTY", ["2026-08-20"], dte_filter=[0],
                                      entry_from="09:25", entry_to="14:48")
    assert dropped.empty     # the session is not 0DTE against a 08-25 expiry


def test_a_thin_contract_is_skipped_rather_than_screened():
    db = FakeDB(
        candles_1m=_spot_session("2026-08-20"),
        option_contracts=_contracts(),
        options_1m=_option_bars("2026-08-20", "TOK|CE", n=5),   # < 30 bars
    )
    frame = screen.build_atm_series(db, "NIFTY", ["2026-08-20"], dte_filter=None,
                                    entry_from="09:25", entry_to="14:48")
    assert frame.empty


def test_an_empty_result_still_has_the_screen_columns():
    """So the caller's `.empty` branch and column access cannot raise."""
    frame = screen.build_atm_series(FakeDB(), "NIFTY", ["2026-08-20"],
                                    dte_filter=None, entry_from="09:25",
                                    entry_to="14:48")
    assert frame.empty
    assert {"session_date", "high", "low", "close", "side"} <= set(frame.columns)


def test_the_atm_strike_is_fixed_from_the_first_eligible_bar():
    """Re-selecting the strike intrabar would be a look-ahead."""
    rows = _spot_session("2026-08-20", bars=375, close=24_512.0)
    for r in rows[200:]:
        r["close"] = 24_890.0          # a big afternoon move
    db = FakeDB(candles_1m=rows, option_contracts=_contracts(),
                options_1m=_option_bars("2026-08-20", "TOK|CE", n=60)
                + _option_bars("2026-08-20", "TOK|PE", n=60))

    frame = screen.build_atm_series(db, "NIFTY", ["2026-08-20"], dte_filter=None,
                                    entry_from="09:25", entry_to="14:48")
    assert set(frame["strike"]) == {24_500}     # morning strike, not 24,900


# ---------------------------------------------------------------------------
# The screen consumes what the builder produces
# ---------------------------------------------------------------------------

def test_the_builder_output_screens_without_crossing_contracts():
    """End to end: build a stacked CE+PE frame and screen it blocked by leg."""
    from app.option_screen import screen_condition

    frame = screen.build_atm_series(_full_db(), "NIFTY", ["2026-08-20"],
                                    dte_filter=None, entry_from="09:25",
                                    entry_to="14:48")
    blocks = frame["session_date"].astype(str) + "|" + frame["side"].astype(str)
    cells = screen_condition(frame, label="atm", horizons=[5],
                             spread_pct_per_side=1.0, group_by=blocks)

    assert len(cells) == 1
    # 2 legs x 60 bars, minus the last 5 of EACH leg (a forward window may not
    # reach into the other contract): 120 - 10 = 110.
    assert cells[0].n_bars == 110
