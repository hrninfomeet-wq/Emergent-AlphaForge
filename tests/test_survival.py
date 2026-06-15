import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.survival import SurvivalConfig, calmar, CALMAR_DD_FLOOR_PCT, _finite


def test_survival_config_from_dict_defaults_and_overrides():
    assert SurvivalConfig.from_dict(None).enabled is False
    cfg = SurvivalConfig.from_dict({"enabled": True, "max_drawdown_pct": 30,
                                    "objective": "net_inr", "min_oos_folds": "majority"})
    assert cfg.enabled is True
    assert cfg.max_drawdown_pct == 30.0
    assert cfg.objective == "net_inr"
    assert cfg.min_oos_folds == "majority"
    assert SurvivalConfig.from_dict({"objective": "bogus"}).objective == "calmar"


def test_calmar_floors_denominator_at_meaningful_dd():
    assert calmar(150.0, -30.0) == 5.0
    assert calmar(150.0, -0.5) == 150.0 / CALMAR_DD_FLOOR_PCT
    assert calmar(150.0, 0.0) == 150.0 / CALMAR_DD_FLOOR_PCT
    assert calmar(-40.0, -20.0) < 0


def test_finite_drops_nonfinite_and_nonnumeric():
    assert _finite([1.0, None, float("nan"), float("inf"), float("-inf"), "abc", 2]) == [1.0, 2.0]
    assert _finite([]) == []


from app.survival import monte_carlo_risk_of_ruin


def test_ror_zero_when_only_gains():
    r = monte_carlo_risk_of_ruin([100.0] * 200, capital=200_000, ruin_floor=0,
                                 n_paths=2000, seed=1)
    assert r["ror_pct"] == 0.0
    assert r["ror_ci_high"] >= 0.0
    assert r["n_days"] == 200


def test_ror_high_when_capital_tiny_vs_swings():
    r = monte_carlo_risk_of_ruin([-50.0, 60.0] * 100, capital=40, ruin_floor=0,
                                 n_paths=4000, seed=1)
    assert r["ror_pct"] > 50.0


def test_ror_is_reproducible_with_seed():
    a = monte_carlo_risk_of_ruin([-10, 12, -8, 15] * 50, 1000, 0, n_paths=3000, seed=7)
    b = monte_carlo_risk_of_ruin([-10, 12, -8, 15] * 50, 1000, 0, n_paths=3000, seed=7)
    assert a["ror_pct"] == b["ror_pct"]


def test_ror_empty_series_is_insufficient_and_max_risk():
    r = monte_carlo_risk_of_ruin([], capital=200_000, ruin_floor=0)
    assert r["n_days"] == 0
    assert r["ror_pct"] == 100.0


def test_ror_drops_nonfinite_days():
    r = monte_carlo_risk_of_ruin([float("nan"), 10.0, float("inf"), -5.0], 1000, 0,
                                 n_paths=500, seed=1)
    assert r["n_days"] == 2


from app.survival import survival_verdict, daily_from_curve, MIN_TRADES_FOR_RUIN


def _curve(equity_points):
    out = []
    prev = equity_points[0][1]
    for ts, eq in equity_points:
        out.append({"ts": ts, "equity_value": eq, "pnl_value": eq - prev,
                    "drawdown_value": 0.0, "drawdown_pct": 0.0})
        prev = eq
    return out


def _portfolio(curve, max_dd_pct, total_return_pct, capital=200_000):
    return {"starting_capital": capital, "curve": curve,
            "max_drawdown_pct": max_dd_pct, "total_return_pct": total_return_pct}


def _cfg(**kw):
    from app.survival import SurvivalConfig
    base = dict(enabled=True, min_equity=0.0, max_drawdown_pct=35.0, max_ror_pct=5.0)
    base.update(kw)
    return SurvivalConfig.from_dict(base)


def test_verdict_rejects_account_that_went_negative_PRIMARY_floor():
    curve = _curve([(1, 200_000), (2, 80_000), (3, -49_130), (4, 50_000)])
    trade_pnls = [120_000, -129_130, 99_130] * 40
    port = _portfolio(curve, max_dd_pct=-30.0, total_return_pct=10.0)
    v = survival_verdict(portfolio=port, trade_pnls=trade_pnls, cfg=_cfg(),
                         coverage={"spot_trade_count": 120, "paired_trade_count": 120},
                         capital=200_000)
    assert v["survived"] is False
    assert v["reason"] == "equity_floor"


def test_verdict_drawdown_sign_regression():
    curve = _curve([(1, 200_000), (2, 350_000), (3, 210_000)])
    trade_pnls = [150_000, -140_000] * 60
    port = _portfolio(curve, max_dd_pct=-40.0, total_return_pct=5.0)
    v = survival_verdict(portfolio=port, trade_pnls=trade_pnls, cfg=_cfg(),
                         coverage={"spot_trade_count": 120, "paired_trade_count": 120},
                         capital=200_000)
    assert v["survived"] is False
    assert v["reason"] == "max_drawdown"


def test_verdict_survives_clean_run():
    curve = _curve([(1, 200_000), (2, 230_000), (3, 290_000), (4, 312_000)])
    trade_pnls = [800.0] * 150
    port = _portfolio(curve, max_dd_pct=-12.0, total_return_pct=56.0)
    v = survival_verdict(portfolio=port, trade_pnls=trade_pnls, cfg=_cfg(),
                         coverage={"spot_trade_count": 160, "paired_trade_count": 150},
                         capital=200_000)
    assert v["survived"] is True
    assert v["calmar"] > 0


def test_verdict_fails_low_coverage_hard():
    curve = _curve([(1, 200_000), (2, 260_000)])
    port = _portfolio(curve, max_dd_pct=-5.0, total_return_pct=30.0)
    v = survival_verdict(portfolio=port, trade_pnls=[1000.0] * 150, cfg=_cfg(),
                         coverage={"spot_trade_count": 300, "paired_trade_count": 150},
                         capital=200_000)
    assert v["survived"] is False
    assert v["reason"] == "low_coverage"


def test_verdict_fails_insufficient_sample():
    curve = _curve([(1, 200_000), (2, 260_000)])
    port = _portfolio(curve, max_dd_pct=-5.0, total_return_pct=30.0)
    v = survival_verdict(portfolio=port, trade_pnls=[1000.0] * 10, cfg=_cfg(),
                         coverage={"spot_trade_count": 10, "paired_trade_count": 10},
                         capital=200_000)
    assert v["survived"] is False
    assert v["insufficient_sample"] is True


def test_verdict_rejects_nan_drawdown():
    # A NaN max_dd_pct must NOT slip through: abs(nan) > cap is False, so without
    # the non-finite guard a blown account could read survived=True.
    curve = _curve([(1, 200_000), (2, 240_000)])
    v = survival_verdict(portfolio=_portfolio(curve, max_dd_pct=float("nan"), total_return_pct=20.0),
                         trade_pnls=[1000.0] * 150, cfg=_cfg(),
                         coverage={"spot_trade_count": 150, "paired_trade_count": 150},
                         capital=200_000)
    assert v["survived"] is False
    assert v["reason"] == "non_finite_metrics"


def test_verdict_fails_empty_trades():
    port = _portfolio([], max_dd_pct=0.0, total_return_pct=0.0)
    v = survival_verdict(portfolio=port, trade_pnls=[], cfg=_cfg(),
                         coverage={"spot_trade_count": 0, "paired_trade_count": 0},
                         capital=200_000)
    assert v["survived"] is False
    assert v["reason"] == "no_trades"


def test_daily_from_curve_buckets_by_ist_date():
    day1a = 1_700_000_000_000
    day1b = day1a + 60_000
    day2 = day1a + 24 * 3600 * 1000
    curve = [{"ts": day1a, "pnl_value": 100.0}, {"ts": day1b, "pnl_value": -40.0},
             {"ts": day2, "pnl_value": 25.0}]
    daily = daily_from_curve(curve)
    assert sorted(daily) == sorted([60.0, 25.0])


from app.survival import oos_fold_index_ranges


def test_oos_fold_index_ranges_three_folds():
    # 900 rows, 3 folds, 60% train -> each fold is 300 rows, OOS tail = 120 rows.
    ranges = oos_fold_index_ranges(900, n_folds=3, train_pct=0.6)
    assert len(ranges) == 3
    assert ranges[0] == (1, 180, 300)      # (fold_no, oos_start, oos_end)
    assert ranges[1] == (2, 480, 600)
    assert ranges[2] == (3, 780, 900)


def test_oos_fold_index_ranges_skips_too_small():
    assert oos_fold_index_ranges(100, n_folds=3, train_pct=0.6) == []   # folds < 100 rows
    assert oos_fold_index_ranges(150, n_folds=3) == []                  # < 200 total guard
    # train < 50 guard (mirrors walk_forward): 600 rows / 3 folds = 200/fold;
    # train_pct=0.2 -> train_end=40 < 50 -> every fold skipped.
    assert oos_fold_index_ranges(600, n_folds=3, train_pct=0.2) == []


def test_cap_skips_excluded_from_coverage_denominator():
    from app.survival import survival_verdict, SurvivalConfig
    cfg = SurvivalConfig(enabled=True)
    port = {"max_drawdown_pct": -5.0, "total_return_pct": 10.0,
            "curve": [{"equity_value": 210000, "ts": 1}]}
    # 10 spot, 6 paired, 4 skipped_by_cap -> eligible = 10-4 = 6, ratio 6/6 = 1.0 (NOT low coverage)
    verdict = survival_verdict(
        portfolio=port, trade_pnls=[100.0] * 6, cfg=cfg,
        coverage={"spot_trade_count": 10, "paired_trade_count": 6, "skipped_by_cap": 4},
        capital=200000.0)
    assert verdict["low_coverage"] is False
