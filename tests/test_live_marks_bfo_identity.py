"""SENSEX (BFO) positions must be tick-marked, exactly like NIFTY (NFO) ones.

2026-09-16, live, real money: a 5-lot SENSEX 17 SEP 74300 PE was open for 104
seconds and its Day P&L crawled while the NIFTY/SENSEX index strip on the same
page updated instantly. The Upstox WS subscription was fine (`BSE_FO|862237` was
in the set) and the guard's own tick lookup was fine (`premium_tick_key` maps
BFO -> BSE_FO by token), so exits were never at risk.

The DISPLAY path was the broken one. It resolves a contract by IDENTITY rather
than token — deliberately, because exchange tokens are recycled across expiries —
and `parse_position_identity` understood only the NFO symbol conventions:

    dname  "NIFTY 15SEP26 23350 CE"   <- DDMONYY contiguous, with a year
    tsym   "NIFTY15SEP26C23350"       <- DDMONYY + C/P + strike

Flattrade's BFO symbols look nothing like either:

    dname  "SENSEX 17 SEP 74300 PE"   <- spaced day/month, NO YEAR AT ALL
    tsym   "SENSEX2691774300PE"       <- YY + M + DD + strike + CE/PE

So every SENSEX position parsed to None, resolve_tick_key returned None, and
mark_positions fell through to mark_source="broker" — pricing a real-money
position off the 15s REST book while NIFTY positions marked on the tick.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live_marks import (  # noqa: E402
    build_contract_index,
    mark_positions,
    parse_position_identity,
    resolve_tick_key,
)

# The real position, verbatim from the broker book on 2026-09-16.
SENSEX_PE = {
    "dname": "SENSEX 17 SEP 74300 PE", "tsym": "SENSEX2691774300PE",
    "exch": "BFO", "token": "862237", "netqty": "-100",
    "lp": "295.55", "urmtom": "0.00", "rpnl": "822.00",
    "prcftr": "1.000000", "mult": "1",
}
NIFTY_CE = {
    "dname": "NIFTY 15SEP26 23350 CE", "tsym": "NIFTY15SEP26C23350",
    "exch": "NFO", "token": "46985", "netqty": "65",
    "lp": "32.30", "urmtom": "0.00", "rpnl": "0.00",
    "prcftr": "1.000000", "mult": "1",
}


def test_a_sensex_position_parses_to_an_identity():
    """THE regression. Returned None before the BFO branch existed."""
    assert parse_position_identity(SENSEX_PE) == ("SENSEX", "2026-09-17", 74300.0, "PE")


def test_the_nfo_conventions_are_untouched():
    """The BFO branch is tried LAST, so NIFTY parsing must be byte-identical."""
    assert parse_position_identity(NIFTY_CE) == ("NIFTY", "2026-09-15", 23350.0, "CE")
    assert parse_position_identity({"dname": "", "tsym": "NIFTY01SEP26C23950"}) == (
        "NIFTY", "2026-09-01", 23950.0, "CE")
    assert parse_position_identity({"dname": "", "tsym": "NIFTY18AUG26P24300"}) == (
        "NIFTY", "2026-08-18", 24300.0, "PE")


def test_a_sensex_position_resolves_to_its_real_instrument_key():
    """Parsing is not enough — it has to land on the RIGHT contract. `BSE_FO|862237`
    is the live key for this contract, confirmed against option_contracts."""
    index = build_contract_index([
        {"instrument_key": "BSE_FO|862237", "underlying": "SENSEX",
         "expiry_date": "2026-09-17", "strike": 74300, "side": "PE"},
        # A decoy at the same strike/side, different expiry. A token-based join
        # (or a year-guessing dname parse) would be at risk of picking this.
        {"instrument_key": "BSE_FO|999999", "underlying": "SENSEX",
         "expiry_date": "2026-09-10", "strike": 74300, "side": "PE"},
    ])
    assert resolve_tick_key(SENSEX_PE, index) == "BSE_FO|862237"


def test_an_open_sensex_position_marks_on_the_tick_not_the_broker():
    """End-to-end: mark_source flips broker -> tick, and the contract joins
    tick_keys so the stream actually subscribes to it."""
    index = build_contract_index([
        {"instrument_key": "BSE_FO|862237", "underlying": "SENSEX",
         "expiry_date": "2026-09-17", "strike": 74300, "side": "PE"},
    ])
    now = int(time.time() * 1000)
    out = mark_positions(
        [SENSEX_PE], contract_index=index,
        tick_lookup=lambda k: {"last_price": 300.0, "ingest_ts": now - 150},
        now_ms=now, max_age_ms=2000,
    )
    row = out["positions"][0]
    assert row["mark_source"] == "tick", "SENSEX still priced off the broker book"
    assert row["tick_key"] == "BSE_FO|862237"
    assert row["lp"] == 300.0
    assert out["tick_keys"] == ["BSE_FO|862237"], (
        "the contract is not in tick_keys, so the stream would never wake on it")


def test_a_stale_tick_still_falls_back_to_the_broker():
    """The fix must not defeat the staleness guard: a tick older than max_age_ms
    is not a live mark, and the row must say so."""
    index = build_contract_index([
        {"instrument_key": "BSE_FO|862237", "underlying": "SENSEX",
         "expiry_date": "2026-09-17", "strike": 74300, "side": "PE"},
    ])
    now = int(time.time() * 1000)
    out = mark_positions(
        [SENSEX_PE], contract_index=index,
        tick_lookup=lambda k: {"last_price": 300.0, "ingest_ts": now - 60_000},
        now_ms=now, max_age_ms=2000,
    )
    assert out["positions"][0]["mark_source"] == "broker"


def test_impossible_and_unknown_symbols_are_refused_not_guessed():
    """A wrong parse marks a position against ANOTHER contract's premium, so the
    parser must fail closed."""
    assert parse_position_identity({"dname": "", "tsym": "SENSEX2623174300PE"}) is None  # 31 Feb
    assert parse_position_identity({"dname": "", "tsym": "NOTASYMBOL"}) is None
    assert parse_position_identity({"dname": "", "tsym": ""}) is None


def test_the_bfo_month_codes_cover_october_to_december():
    """Months 10-12 are O/N/D in the BFO weekly symbol, not 10/11/12 — a digits-only
    parser would silently mis-date every Q4 contract."""
    assert parse_position_identity({"dname": "", "tsym": "SENSEX26O0874300PE"}) == (
        "SENSEX", "2026-10-08", 74300.0, "PE")
    assert parse_position_identity({"dname": "", "tsym": "SENSEX26N1274300CE"}) == (
        "SENSEX", "2026-11-12", 74300.0, "CE")
    assert parse_position_identity({"dname": "", "tsym": "SENSEX26D3174300CE"}) == (
        "SENSEX", "2026-12-31", 74300.0, "CE")


# --------------------------------------------------------------------------- #
# The Live Deployments pane must show live P&L (2026-09-16)
#
# Reported alongside the SENSEX marking bug: "the Live Deployments pane shows no
# live P&L at all". It was literally true — LiveRow rendered today's CUMULATIVE
# realised P&L and an open COUNT, and nothing else. A deployment sitting on an
# open position displayed no mark-to-market whatsoever.
# --------------------------------------------------------------------------- #

def _fe(rel: str) -> str:
    return (ROOT / "frontend" / "src" / rel).read_text(encoding="utf-8")


STRIP = "components/live/LiveDeploymentStrip.jsx"


def test_the_strip_consumes_the_marked_book():
    src = _fe(STRIP)
    assert "deployLive: liveStatuses, positions" in src, (
        "the strip never reads the marked position book, so it cannot show live P&L")
    assert "mtmByDeployment" in src


def test_the_row_renders_a_live_mtm_figure():
    src = _fe(STRIP)
    assert 'data-testid="live-deploy-mtm"' in src, "no live MTM element on the row"
    assert "liveMtm" in src


def test_mtm_is_attributed_by_trading_symbol_not_guessed():
    """The broker book carries no deployment_id. The only honest link is the tsym
    the guard recorded for that deployment; anything else invents attribution."""
    src = _fe(STRIP)
    block = src[src.index("const mtmByDeployment"):src.index("// Aggregate today's realized")]
    assert "open_positions" in block and "byTsym.get" in block, block[:300]


def test_an_unguarded_broker_position_is_not_folded_into_a_deployment():
    """A position the guard does not own must not be silently added to someone's
    MTM — the alert rail exists to surface it."""
    src = _fe(STRIP)
    block = src[src.index("const mtmByDeployment"):src.index("// Aggregate today's realized")]
    assert "if (!row) continue;" in block, (
        "positions are attributed without checking the guard owns them")


def test_a_broker_priced_row_is_labelled_not_passed_off_as_live():
    src = _fe(STRIP)
    block = src[src.index("const mtmByDeployment"):src.index("// Aggregate today's realized")]
    assert 'mark_source || "") !== "tick"' in block, (
        "mark_source is not checked — a 15s REST price would render as a live mark")
    assert "(broker)" in src, "no visible marker distinguishing a broker-priced MTM"
