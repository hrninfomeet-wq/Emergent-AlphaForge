"""index_trade_id must reference the FULL spot trade list, even with a DTE filter.

Root cause (2026-07-18): `_run_paired_option_backtest` applies the DTE filter by
rebuilding `spot_trades` and the sim enumerates that FILTERED list, but the saved
run doc and the Trades pane join option legs to the FULL spot list by index. With
dte_filter=[0,1,2] active, 168 of 171 legs in a saved optimizer run rendered on
the wrong spot rows (a CE row showing another trade's 25850 PE leg). The fix
remaps each leg's index_trade_id back to its original full-list position.
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.runtime import _run_paired_option_backtest  # noqa: E402
from app.schemas import BacktestReq, OptionBacktestReq  # noqa: E402


def _ts(y, m, d, hh, mm):
    """Epoch ms for an IST wall time (IST = UTC+5:30)."""
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp() * 1000) - (5 * 3600 + 30 * 60) * 1000


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, length=None):
        return list(self._rows)


class _FakeColl:
    def __init__(self, rows):
        self._rows = rows

    def find(self, *args, **kwargs):
        return _FakeCursor(self._rows)


class _FakeDB:
    def __init__(self, contracts, option_candles):
        self.candles_1m = _FakeColl([])          # VIX join: no data, skipped
        self.option_contracts = _FakeColl(contracts)
        self.options_1m = _FakeColl(option_candles)


EXPIRY = "2026-01-13"  # Tuesday

# Trade 0 enters Mon 2026-01-12 (DTE 1, dropped by [0]); trades 1-2 enter on
# expiry day (DTE 0, kept). Trade 1 is a CE at 26108.2 (ATM 26100), trade 2 a
# PE at 25843.2 (ATM 25850) — mirroring the real-world repro.
SPOT_TRADES = [
    {"direction": "CE", "entry_price": 26050.0, "exit_price": 26060.0,
     "entry_ts": _ts(2026, 1, 12, 10, 0), "exit_ts": _ts(2026, 1, 12, 10, 25)},
    {"direction": "CE", "entry_price": 26108.2, "exit_price": 26160.5,
     "entry_ts": _ts(2026, 1, 13, 10, 0), "exit_ts": _ts(2026, 1, 13, 10, 25)},
    {"direction": "PE", "entry_price": 25843.2, "exit_price": 25800.0,
     "entry_ts": _ts(2026, 1, 13, 11, 0), "exit_ts": _ts(2026, 1, 13, 11, 25)},
]


def _contract(key, strike, side):
    return {"instrument_key": key, "trading_symbol": f"NIFTY {int(strike)} {side}",
            "underlying": "NIFTY", "expiry_date": EXPIRY, "strike": float(strike),
            "side": side, "lot_size": 65}


CONTRACTS = [
    _contract("NSE_FO|C26100", 26100, "CE"),
    _contract("NSE_FO|P26100", 26100, "PE"),
    _contract("NSE_FO|C25850", 25850, "CE"),
    _contract("NSE_FO|P25850", 25850, "PE"),
]


def _candles():
    rows = []
    for key, trade in (("NSE_FO|C26100", SPOT_TRADES[1]), ("NSE_FO|P25850", SPOT_TRADES[2])):
        for ts, px in ((trade["entry_ts"], 120.0), (trade["exit_ts"], 150.0)):
            rows.append({"instrument_key": key, "ts": ts, "open": px, "high": px,
                         "low": px, "close": px, "volume": 100})
    return rows


def _req(dte_filter):
    return BacktestReq(
        strategy_id="adaptive_regime_scalper", instrument="NIFTY",
        option_backtest=OptionBacktestReq(
            enabled=True, moneyness="atm", dte_filter=dte_filter, auto_fetch=False,
        ),
    )


def _run(monkeypatch, dte_filter):
    monkeypatch.setattr("app.runtime.get_db", lambda: _FakeDB(CONTRACTS, _candles()))
    return asyncio.run(_run_paired_option_backtest(_req(dte_filter), list(SPOT_TRADES)))


def test_dte_filtered_legs_keep_full_list_index(monkeypatch):
    result = _run(monkeypatch, [0])
    legs = result["trades"]
    by_id = {t["index_trade_id"]: t for t in legs}

    # Trade 0 was dropped by the DTE filter: no leg may claim its row.
    assert 0 not in by_id
    # The kept trades' legs sit at their ORIGINAL positions 1 and 2 (pre-fix
    # they came back as 0 and 1 and rendered on the wrong spot rows).
    assert set(by_id) == {1, 2}
    assert by_id[1]["status"] == "PAIRED"
    assert (by_id[1]["side"], by_id[1]["strike"]) == ("CE", 26100.0)
    assert by_id[2]["status"] == "PAIRED"
    assert (by_id[2]["side"], by_id[2]["strike"]) == ("PE", 25850.0)

    # Invariant the UI join relies on: every leg's signal fields match the spot
    # trade at index_trade_id in the FULL list.
    for leg in legs:
        spot = SPOT_TRADES[leg["index_trade_id"]]
        assert leg["signal_entry_ts"] == spot["entry_ts"]
        assert leg["direction"] == spot["direction"]

    stats = result["data"]["dte_filter"]
    assert stats["input_trades"] == 3
    assert stats["matched_trades"] == 2


def test_unfiltered_run_indexes_unchanged(monkeypatch):
    result = _run(monkeypatch, None)
    ids = sorted(t["index_trade_id"] for t in result["trades"])
    assert ids == [0, 1, 2]
    for leg in result["trades"]:
        assert leg["signal_entry_ts"] == SPOT_TRADES[leg["index_trade_id"]]["entry_ts"]
