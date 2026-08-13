"""Equivalence guard for the option re-rank contract-index optimization.

The per-trade eligible-contract filter used to rebuild
``[c for c in contract_list if str(c.get("expiry_date","")) == str(rexp)]`` for
every trade of every candidate — O(trades x contracts), and the single largest
cost in the "Analyzing / option_rerank" stage (measured 3.03s per candidate on a
real SENSEX job: 15,208 contracts x 2,112 trades). It is now an index built once.

Speed is worthless if it moves a number, so these tests assert the INDEX returns
exactly what the comprehension returned — same rows, same order, same identity —
including the edge cases that make a naive dict wrong: contracts with a missing
or None expiry, and a lookup for an expiry no contract has.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.option_backtest import simulate_paired_option_trades  # noqa: E402


def _contracts():
    """Mixed universe: several expiries, plus rows the naive index gets wrong."""
    out = []
    for exp in ("2026-01-08", "2026-01-15", "2026-01-22"):
        for strike in (24000, 24100, 24200):
            for side in ("CE", "PE"):
                out.append({"instrument_key": f"NFO|{exp}|{strike}|{side}",
                            "underlying": "NIFTY", "expiry_date": exp,
                            "strike": strike, "side": side})
    out.append({"instrument_key": "NFO|noexp|24000|CE", "underlying": "NIFTY",
                "strike": 24000, "side": "CE"})              # key missing entirely
    out.append({"instrument_key": "NFO|nullexp|24000|PE", "underlying": "NIFTY",
                "expiry_date": None, "strike": 24000, "side": "PE"})   # explicit None
    return out


def _index(contract_list):
    """The mapping the production code builds."""
    idx = {}
    for c in contract_list:
        idx.setdefault(str(c.get("expiry_date", "")), []).append(c)
    return idx


def _legacy(contract_list, resolved_expiry):
    """The comprehension the index replaced — the reference implementation."""
    return [c for c in contract_list
            if str(c.get("expiry_date", "")) == str(resolved_expiry)]


def test_index_matches_the_comprehension_row_for_row():
    contracts = _contracts()
    idx = _index(contracts)
    # every real expiry, plus the falsy/absent cases and one that matches nothing
    for rexp in ("2026-01-08", "2026-01-15", "2026-01-22", "", "None", "2099-12-31"):
        expected = _legacy(contracts, rexp)
        actual = idx.get(str(rexp), [])
        assert actual == expected, f"expiry {rexp!r}: {len(actual)} vs {len(expected)}"
        # identity, not just equality — the sim reads these dicts directly
        assert all(a is e for a, e in zip(actual, expected)), f"expiry {rexp!r}: aliasing changed"


def test_index_preserves_caller_ordering():
    """`contracts` arrives sorted by (expiry_date, strike, side) and the sim's
    contract selection returns the FIRST match — so bucket order is load-bearing."""
    contracts = _contracts()
    idx = _index(contracts)
    for exp, bucket in idx.items():
        assert bucket == _legacy(contracts, exp)
        positions = [contracts.index(c) for c in bucket]
        assert positions == sorted(positions), f"expiry {exp!r} reordered"


def test_sim_accepts_a_shared_index_and_matches_building_its_own():
    """The optimizer passes a shared index so ~75 per-candidate sims don't each
    rebuild it. Passing it must not change the result vs letting the sim build one."""
    contracts = _contracts()
    trades = [{"direction": "CE", "entry_ts": 1767239100000, "exit_ts": 1767240000000,
               "entry_price": 24050.0, "exit_price": 24080.0,
               "entry_datetime": "2026-01-01 09:25", "exit_datetime": "2026-01-01 09:40"}]
    common = dict(spot_trades=trades, contracts=contracts, option_candles=None,
                  underlying="NIFTY", moneyness="atm", lots=1,
                  expiry_by_trade={0: "2026-01-08"})
    built = simulate_paired_option_trades(**common)
    shared = simulate_paired_option_trades(**common, contracts_by_expiry=_index(contracts))
    assert built["metrics"] == shared["metrics"]
    assert built["coverage"] == shared["coverage"]


def test_survival_gate_has_exactly_one_definition():
    """The survival gate runs in three places — the event loop's thread, and a
    worker process (per finalist and per exit-control grid point). They must all
    call the SAME `_survival_eval_oos_sync`; a second copy of floor/DD/RoR logic
    would be free to drift and silently change who is deployable."""
    src = (ROOT / "backend" / "app" / "optimizer.py").read_text(encoding="utf-8")
    assert "def _survival_eval_oos_sync(" in src
    # the async wrapper must DELEGATE, not re-implement
    assert "_survival_eval_oos_sync, strategy, df_enriched" in src
    assert "return _survival_eval_oos_sync(" in src        # the worker delegates too
    # The gate's arithmetic must exist in exactly TWO places, and they are the two
    # genuinely different strategy families — `_survival_eval_oos_premium_trigger`
    # (option-native, no spot signal to pair) and `_survival_eval_oos_sync`. If this
    # count ever rises, someone has copied the gate rather than called it.
    import re
    owners = []
    fn = None
    for line in src.splitlines():
        m = re.match(r"(?:async )?def (\w+)\(", line)
        if m:
            fn = m.group(1)
        if 'verdict["folds_ok"] = folds_ok' in line:
            owners.append(fn)
    assert owners == ["_survival_eval_oos_premium_trigger", "_survival_eval_oos_sync"], owners


def test_survival_worker_never_raises_so_the_caller_can_recompute():
    """A worker with no option universe must return None — the caller then
    recomputes that finalist in-process. Raising, or returning a not-survived
    verdict, would silently disqualify a finalist that actually survives."""
    import app.parallel_eval as pe
    from app.optimizer import survival_worker

    prev = (pe._SIM_RAW_DF, pe._SIM_CONTRACTS, pe._SIM_CANDLES_BY_KEY)
    try:
        pe._SIM_RAW_DF, pe._SIM_CONTRACTS, pe._SIM_CANDLES_BY_KEY = None, None, None
        assert survival_worker("confluence_scalper", {}, "NIFTY", True, {}, {},
                               None, 3, 0.6, "09:25", "14:50") is None
        # universe present but empty is equally unusable
        pe._SIM_CONTRACTS, pe._SIM_CANDLES_BY_KEY = [], {}
        assert survival_worker("confluence_scalper", {}, "NIFTY", True, {}, {},
                               None, 3, 0.6, "09:25", "14:50") is None
    finally:
        pe._SIM_RAW_DF, pe._SIM_CONTRACTS, pe._SIM_CANDLES_BY_KEY = prev


def test_contract_identity_key_memoization_is_transparent():
    """String inputs are memoized; datetime/date/NaN fall through uncached. All
    paths must agree with the uncached implementation."""
    from datetime import date, datetime as dt

    from app.instruments import contract_identity_key, _contract_identity_key_impl

    cases = [
        ("NFO|12345", "2026-01-08"),
        ("NFO|12345", "08-01-2026"),      # DD-MM-YYYY broker form
        ("NFO|12345|08-01-2026", None),   # expiry embedded in a dated 3-part key
        ("NFO|12345", None),
        ("NFO|12345", ""),
        ("", "2026-01-08"),
        ("NFO|12345", date(2026, 1, 8)),          # uncached path
        ("NFO|12345", dt(2026, 1, 8, 15, 30)),    # uncached path
        ("NFO|12345", float("nan")),              # uncached path (pandas mixed frame)
    ]
    for ik, exp in cases:
        assert contract_identity_key(ik, exp) == _contract_identity_key_impl(ik, exp), (ik, exp)
    # repeated calls stay stable (the cache is not poisoning results)
    for ik, exp in cases:
        assert contract_identity_key(ik, exp) == _contract_identity_key_impl(ik, exp), (ik, exp)
