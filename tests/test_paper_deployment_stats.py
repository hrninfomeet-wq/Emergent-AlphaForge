"""Pure-math pins for paper_analytics.deployment_period_stats.

Semantics under test (Paper page per-deployment drill-down):
  * deployment-isolated equity: starting_capital + this deployment's own
    cumulative realized P&L, folded in closed_at order
  * pnl_min/pnl_max = worst/best point of the running P&L WITHIN the bucket
  * max_drawdown_value = peak-to-trough of equity within the bucket
  * max_deployed_value = peak CONCURRENT entry premium (sweep line over
    [created_at, closed_at|now) spans; open trades hold capital until now)
  * buckets that only ever held open positions have None capitals
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.paper_analytics import deployment_period_stats  # noqa: E402

NOW_MS = 1783300000000  # 2026-07-06T? — after every fixture timestamp

TRADES = [
    {"status": "CLOSED", "realized_pnl": 500, "created_at": "2026-06-29T04:00:00Z",
     "closed_at": "2026-06-29T05:00:00Z", "entry_price": 100, "quantity": 75},
    {"status": "CLOSED", "realized_pnl": -800, "created_at": "2026-06-29T04:30:00Z",
     "closed_at": "2026-06-29T06:00:00Z", "entry_price": 120, "quantity": 75},
    {"status": "CLOSED", "realized_pnl": 300, "created_at": "2026-06-30T04:00:00Z",
     "closed_at": "2026-06-30T05:00:00Z", "entry_price": 90, "quantity": 75},
    {"status": "OPEN", "created_at": "2026-07-03T04:00:00Z",
     "entry_price": 110, "quantity": 75},
]


def _run():
    return deployment_period_stats(TRADES, starting_capital=100000, now_ms=NOW_MS)


def _day(out, key):
    return next(r for r in out["periods"]["day"] if r["bucket"] == key)


def test_day_bucket_pnl_extremes_and_drawdown():
    d = _day(_run(), "2026-06-29")
    assert d["trades"] == 2
    assert d["net_pnl"] == -300.0
    assert d["pnl_max"] == 500.0        # after the +500 win
    assert d["pnl_min"] == -300.0       # end of day after the -800 loss
    assert d["max_drawdown_value"] == -800.0  # 100500 peak -> 99700 trough
    assert d["capital_max"] == 100500.0
    assert d["capital_min"] == 99700.0


def test_day_bucket_overlapping_deployed_capital():
    d = _day(_run(), "2026-06-29")
    # both trades were open 04:30-05:00 -> 100*75 + 120*75 concurrent
    assert d["max_deployed_value"] == 16500.0


def test_next_day_capital_carries_prior_equity():
    d = _day(_run(), "2026-06-30")
    assert d["capital_min"] == 99700.0   # opening equity after -300 lifetime
    assert d["capital_max"] == 100000.0
    assert d["max_deployed_value"] == 6750.0


def test_open_only_day_has_null_capitals_but_deployed_value():
    d = _day(_run(), "2026-07-03")
    assert d["trades"] == 0
    assert d["capital_min"] is None and d["capital_max"] is None
    assert d["max_deployed_value"] == 8250.0  # 110 * 75, still open


def test_week_bucket_aggregates_the_monday_week():
    out = _run()
    wk = next(r for r in out["periods"]["week"] if r["bucket"] == "2026-06-29")
    assert wk["trades"] == 3
    assert wk["net_pnl"] == 0.0
    assert wk["pnl_min"] == -300.0 and wk["pnl_max"] == 500.0
    assert wk["max_deployed_value"] == 16500.0


def test_month_and_year_buckets_exist():
    out = _run()
    assert any(r["bucket"] == "2026-06" for r in out["periods"]["month"])
    assert any(r["bucket"] == "2026" for r in out["periods"]["year"])
