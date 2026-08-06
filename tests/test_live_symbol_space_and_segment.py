"""A live trade must carry the symbols and exchange the broker actually uses.

Two defects with ONE root cause: the executor knows the Noren `tsym` and `exch`
(it builds the OrderIntent from them) and neither ever reached the live_trades
document.

1. `live_blotter` joined `live_trades.trading_symbol` — an UPSTOX symbol — against
   a NOREN-keyed position book. The lookup could never match, so every live trade
   rendered as not-held with no broker LTP and no broker P&L. This is the same
   symbol-space confusion that has produced real bugs here before (recovery had to
   resolve leg symbols through the order book for exactly this reason).

2. Statutory charges were computed with the NSE exchange-transaction rate for
   every trade. BSE (SENSEX / BFO) is 0.000325 against NSE's 0.0003503 — the flat
   shared default over-charges SENSEX by ~7.7% on that component.

Storing `noren_tsym` and `exch` at insert fixes both, and lets the dead
`live/live_friction_profile.py` be deleted rather than left as a second charge
implementation waiting to be wired to the wrong caller.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.live_blotter import build_live_blotter  # noqa: E402
from app.option_costs import (  # noqa: E402
    EXCHANGE_TXN_RATE_BFO, cost_config_for_exchange, round_trip_charges,
)

_NOREN = "NIFTY07AUG26C24600"
_UPSTOX = "NIFTY 24600 CE 07 AUG 26"


def _trade(**over):
    t = {"norenordno": "N1", "status": "OPEN", "quantity": 65,
         "entry_price": 34.0, "trading_symbol": _UPSTOX,
         "noren_tsym": _NOREN, "exch": "NFO",
         "deployment_id": "dep1", "created_at": "2026-08-07T04:00:00+00:00"}
    t.update(over)
    return t


def _position():
    return {"tsym": _NOREN, "exch": "NFO", "netqty": "65",
            "lp": "36.0", "urmtom": "130.0"}


# --- 1. the symbol-space join ----------------------------------------------

def test_blotter_matches_the_broker_position_via_the_noren_symbol():
    rows = build_live_blotter([_trade()], [_position()], {"dep1": {}})
    assert rows and rows[0]["at_broker"] is True, (
        "the blotter must join on the NOREN symbol — matching an Upstox symbol "
        "against a Noren-keyed position book can never succeed")
    assert rows[0]["ltp"] == 36.0


def test_blotter_does_not_match_on_the_upstox_symbol():
    """Guard against the old behaviour silently coming back."""
    t = _trade()
    t["noren_tsym"] = "SOMETHINGELSE26C1"
    rows = build_live_blotter([t], [_position()], {"dep1": {}})
    assert rows[0]["at_broker"] is False, (
        "matched a position it should not have — the Upstox trading_symbol must "
        "not be used against the Noren book")


def test_legacy_rows_without_a_noren_symbol_still_render():
    """No crash for trades journalled before this field existed."""
    t = _trade()
    t.pop("noren_tsym")
    rows = build_live_blotter([t], [_position()], {"dep1": {}})
    assert rows and rows[0]["at_broker"] is False


# --- 2. segment-aware charges ----------------------------------------------

def test_sensex_uses_the_bse_exchange_rate():
    cfg = cost_config_for_exchange("BFO")
    assert cfg.exchange_txn_rate == EXCHANGE_TXN_RATE_BFO
    assert cfg.enabled is True


def test_nifty_uses_the_nse_exchange_rate():
    nfo = cost_config_for_exchange("NFO")
    bfo = cost_config_for_exchange("BFO")
    assert nfo.exchange_txn_rate != bfo.exchange_txn_rate
    assert nfo.exchange_txn_rate > bfo.exchange_txn_rate


def test_unknown_or_missing_exchange_defaults_to_nse():
    for value in (None, "", "XXX"):
        assert (cost_config_for_exchange(value).exchange_txn_rate
                == cost_config_for_exchange("NFO").exchange_txn_rate)


def test_the_bse_rate_actually_changes_the_total():
    nfo = round_trip_charges(entry_premium=100.0, exit_premium=110.0,
                             quantity=20, cfg=cost_config_for_exchange("NFO"))
    bfo = round_trip_charges(entry_premium=100.0, exit_premium=110.0,
                             quantity=20, cfg=cost_config_for_exchange("BFO"))
    assert bfo["total_charges"] < nfo["total_charges"], (
        "BSE's lower exchange rate must reduce the total, or the segment "
        "distinction is decorative")


def test_the_dead_duplicate_charge_module_is_gone():
    """A second, unwired charge implementation is a trap: a future caller wires
    the wrong one and live silently diverges from paper."""
    assert not (Path(__file__).resolve().parents[1]
                / "backend" / "app" / "live" / "live_friction_profile.py").exists()
