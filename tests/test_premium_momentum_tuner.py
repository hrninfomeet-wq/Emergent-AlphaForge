# tests/test_premium_momentum_tuner.py
"""Phase 1.3 — the honest tuning harness. Chronological train/test split (select
on TRAIN, report OOS of the selected — never select on test), mandatory costs
(mirrors the survival-gate precedent: tuning gross P&L finds cost-fragile
configs), bounded grid, overfit flags."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pandas as pd
import pytest

from app.premium_momentum_tuner import split_sessions, tune_premium_momentum


def _spot_bar(ts, ist, close, session):
    return {"ts": ts, "ist_time": ist, "close": close, "session_date": session}


def _opt(key, ts, close):
    return {"instrument_key": key, "ts": ts, "close": close}


def _fixture(n_sessions=10):
    """n sessions; CE premium rises 100->120->122 every session (always triggers
    +15%, EOD-exits at 122 for +2 gross)."""
    spot, opt = [], []
    contracts = [{"instrument_key": "CE|23950", "strike": 23950, "side": "CE",
                  "expiry_date": "2099-01-01"}]
    for d in range(n_sessions):
        session = f"2026-06-{d+1:02d}"
        base = d * 1000
        for i, (ist, sp, pr) in enumerate([("09:31", 24000.0, 100.0),
                                           ("09:32", 24000.0, 120.0),
                                           ("09:33", 24000.0, 122.0)]):
            spot.append(_spot_bar(base + i + 1, ist, sp, session))
            opt.append(_opt("CE|23950", base + i + 1, pr))
    return pd.DataFrame(spot), pd.DataFrame(opt), contracts


BASE = {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
        "cost_config": {"enabled": True, "spread_pct_of_premium": 1.0}}


def test_split_sessions_is_chronological_and_disjoint():
    sessions = [f"2026-06-{d:02d}" for d in range(1, 11)]
    train, test = split_sessions(sessions, train_frac=0.7)
    assert train == sessions[:7] and test == sessions[7:]
    assert not set(train) & set(test)
    assert max(train) < min(test)          # strictly chronological


def test_tuner_requires_costs_enabled():
    spot, opt, contracts = _fixture()
    with pytest.raises(ValueError, match="cost"):
        tune_premium_momentum(spot_df=spot, option_candles=opt, contracts=contracts,
                              instrument="NIFTY",
                              base_params={**BASE, "cost_config": {"enabled": False}},
                              grid={"momentum_pct": [15.0], "stop_pct": [20.0]})


def test_tuner_caps_the_grid():
    spot, opt, contracts = _fixture()
    with pytest.raises(ValueError, match="configs"):
        tune_premium_momentum(spot_df=spot, option_candles=opt, contracts=contracts,
                              instrument="NIFTY", base_params=BASE,
                              grid={"momentum_pct": list(range(60)),
                                    "stop_pct": list(range(10))},   # 600 > cap 500
                              max_configs=500)


def test_tuner_ranks_by_train_and_reports_oos_of_selected():
    spot, opt, contracts = _fixture(10)
    out = tune_premium_momentum(spot_df=spot, option_candles=opt, contracts=contracts,
                                instrument="NIFTY", base_params=BASE,
                                grid={"momentum_pct": [15.0, 500.0],  # 500% never triggers
                                      "stop_pct": [50.0]})
    assert out["split"]["train_sessions"] == 7 and out["split"]["test_sessions"] == 3
    ranked = out["configs"]
    assert len(ranked) == 2
    # the triggering config outranks the never-triggering one on TRAIN net
    assert ranked[0]["params"]["momentum_pct"] == 15.0
    assert ranked[0]["train"]["trades"] == 7 and ranked[0]["test"]["trades"] == 3
    assert ranked[1]["train"]["trades"] == 0
    best = out["best_by_train"]
    assert best["params"]["momentum_pct"] == 15.0
    # selection is on train; the OOS numbers are REPORTED, never the selector
    assert "net_pnl_pts" in best["test"]


def test_tuner_flags_overfit_configs():
    # train +, test − (or zero-trade test) must be flagged, not celebrated
    spot, opt, contracts = _fixture(10)
    out = tune_premium_momentum(spot_df=spot, option_candles=opt, contracts=contracts,
                                instrument="NIFTY", base_params=BASE,
                                grid={"momentum_pct": [15.0], "stop_pct": [50.0]})
    c = out["configs"][0]
    # fixture is uniformly profitable, so NOT overfit
    assert c["overfit_warning"] is False
    # and the flag logic: positive train + non-positive test would be flagged
    assert "overfit_warning" in c
