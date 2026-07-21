import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.forward_validation import (  # noqa: E402
    block_bootstrap_evidence, evaluate_forward_promotion,
    max_calendar_month_drawdown_pct, max_drawdown_pct,
)
from app.forward_metrics import (  # noqa: E402
    _policy_lots, _promotion_trade_pnl, _qualifying_account_capital,
    _trade_has_qualifying_account_capital,
)


def test_empty_or_short_record_fails_closed():
    out = evaluate_forward_promotion(
        daily_pnl=[], complete_sessions=0, closed_trades=0, option_coverage=0,
        eod_violation_count=1, capital_enforced=False, config_hash="", lots=10,
    )
    assert out["phase"] == "collecting"
    assert out["promotion_allowed"] is False
    assert "forward_sessions" in out["failed_checks"]
    assert out["statistics"]["annual_ruin_upper95"] is None


def test_profitable_sixty_session_one_lot_record_can_pass():
    daily = [1000.0] * 60
    out = evaluate_forward_promotion(
        daily_pnl=daily, complete_sessions=60, closed_trades=120,
        option_coverage=0.99, eod_violation_count=0, capital_enforced=True,
        config_hash="abc123", lots=1,
    )
    assert out["promotion_allowed"] is True
    assert out["phase"] == "promotion_ready"
    assert out["statistics"]["positive_ten_session_blocks"] == 6
    assert out["statistics"]["annual_ruin_upper95"] < 0.30


def test_individual_trade_count_cannot_rescue_negative_daily_blocks():
    daily = [2000.0] * 30 + [-3000.0] * 30
    out = evaluate_forward_promotion(
        daily_pnl=daily, complete_sessions=60, closed_trades=500,
        option_coverage=1.0, eod_violation_count=0, capital_enforced=True,
        config_hash="abc123", lots=1,
    )
    assert out["promotion_allowed"] is False
    assert "positive_daily_mean_ci" in out["failed_checks"]


def test_drawdown_is_measured_from_account_equity_peak():
    assert max_drawdown_pct([20_000, -55_000], 200_000) == 25.0
    assert max_calendar_month_drawdown_pct(
        [20_000, -55_000, 30_000, -60_000],
        ["2026-01-02", "2026-01-03", "2026-02-02", "2026-02-03"],
        200_000,
    ) == 30.769
    stats = block_bootstrap_evidence([100.0] * 60, paths=200)
    assert stats["sample_sessions"] == 60
    assert stats["daily_mean_ci95"][0] == 100.0


def test_promotion_requires_exact_fixed_two_lakh_account_contract():
    assert _qualifying_account_capital({"amount": 200_000, "basis": "fixed"})
    assert not _qualifying_account_capital(None)
    assert not _qualifying_account_capital({"amount": 200_000, "basis": "cumulative"})
    assert not _qualifying_account_capital({"amount": 1_000_000, "basis": "fixed"})
    assert not _qualifying_account_capital({"amount": 200_000})


def test_one_lot_gate_checks_configuration_and_every_observed_trade():
    assert _policy_lots(1, {1}) == 1
    assert _policy_lots(1, set()) == 1
    assert _policy_lots(1, {1, 2}) == 2
    assert _policy_lots(10, {1}) == 10


def test_uncovered_forward_winners_are_haircut_but_losses_remain():
    assert _promotion_trade_pnl({"realized_pnl": 500}) == 0.0
    assert _promotion_trade_pnl({"realized_pnl": -500}) == -500.0
    assert _promotion_trade_pnl({
        "realized_pnl": 500, "execution_realized_pnl": 420,
    }) == 420.0


def test_trade_must_record_the_exact_account_capital_gate_at_entry():
    assert _trade_has_qualifying_account_capital({
        "capital_gate_evidence": [
            {"allowed": True, "scope": "account", "capital": 200_000,
             "basis": "fixed"},
        ],
    })
    assert not _trade_has_qualifying_account_capital({
        "capital_gate_evidence": [
            {"allowed": True, "scope": "deployment", "capital": 200_000,
             "basis": "fixed"},
        ],
    })
