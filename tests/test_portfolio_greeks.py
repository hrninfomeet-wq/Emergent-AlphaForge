import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.portfolio_greeks import compute_portfolio_greeks


def _mk_quote(lp=None, bp1=None, sp1=None, sptprc=None, und_tk=None, und_exch=None):
    q = {}
    if lp is not None: q["lp"] = str(lp)
    if bp1 is not None: q["bp1"] = str(bp1)
    if sp1 is not None: q["sp1"] = str(sp1)
    if sptprc is not None: q["sptprc"] = str(sptprc)
    if und_tk is not None: q["und_tk"] = str(und_tk)
    if und_exch is not None: q["und_exch"] = str(und_exch)
    return q


def _run(coro):
    return asyncio.run(coro)


def test_aggregates_net_delta_and_theta():
    positions = [{"tsym": "NIFTY25000CE", "exch": "NFO", "position": {"netqty": 65}}]

    async def quote(exch, token):
        return _mk_quote(bp1=99.5, sp1=100.5, sptprc=25000.0)

    async def resolve(tsym, exch):
        return (25000.0, (date.today().isoformat()), True, "TKN1")  # expiry today → TTE floor

    out = _run(compute_portfolio_greeks(
        positions, get_quote_fn=quote, resolve_contract_fn=resolve, today=date.today()))
    assert out["n_computed"] == 1 and out["n_skipped"] == 0
    assert out["net_delta_rupees_per_point"] != 0.0
    assert out["net_theta_rupees_per_day"] < 0.0  # long option bleeds theta


def test_skips_unresolvable_contract():
    positions = [{"tsym": "X", "exch": "NFO", "position": {"netqty": 65}}]

    async def quote(exch, token):
        return _mk_quote(lp=100.0, sptprc=25000.0)

    async def resolve(tsym, exch):
        return None

    out = _run(compute_portfolio_greeks(
        positions, get_quote_fn=quote, resolve_contract_fn=resolve, today=date.today()))
    assert out["n_computed"] == 0 and out["n_skipped"] == 1


def test_underlying_quote_fallback_for_spot():
    # option quote lacks sptprc but carries und_tk/und_exch → second quote gives spot
    from datetime import timedelta
    exp = (date.today() + timedelta(days=7)).isoformat()
    positions = [{"tsym": "NIFTY25000CE", "exch": "NFO", "position": {"netqty": 65}}]
    calls = {"n": 0}

    async def quote(exch, token):
        calls["n"] += 1
        if token == "TKN1":
            return _mk_quote(bp1=99.5, sp1=100.5, und_tk="26000", und_exch="NSE")
        return _mk_quote(lp=25000.0)  # underlying

    async def resolve(tsym, exch):
        return (25000.0, exp, True, "TKN1")

    out = _run(compute_portfolio_greeks(
        positions, get_quote_fn=quote, resolve_contract_fn=resolve, today=date.today()))
    assert out["n_computed"] == 1 and calls["n"] == 2


def test_empty_positions_returns_zeros():
    async def quote(exch, token): return {}
    async def resolve(tsym, exch): return None
    out = _run(compute_portfolio_greeks(
        [], get_quote_fn=quote, resolve_contract_fn=resolve, today=date.today()))
    assert out == {"net_delta_rupees_per_point": 0.0, "net_theta_rupees_per_day": 0.0,
                   "n_computed": 0, "n_skipped": 0, "positions": []}
