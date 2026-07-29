"""Every cost the model applied must be visible, and a gross run must say so.

Two findings from the 2026-07-30 audit.

**Spread is hidden inside "gross".** Both engines compute their gross P&L from
SPREAD-ADJUSTED fills (`premium_momentum.py:226-230`,
`option_backtest.py:706-762`), so the Option Execution summary
`gross X · charges −Y · net Z` implies total cost = Y when the real cost is
spread + Y. Measured on the audited premium run: charges ₹14,923.98 displayed,
true model cost **₹54,617.57** — understated **3.7×** (P1 gross 394,378.39 vs
P2 gross 354,684.80 on identical trades, the delta being pure spread).

The arithmetic was never wrong; "gross" simply means "before statutory charges",
not "before all costs", and nothing said so. Fix: report the spread explicitly.

**A run with no cost model shows no cost UI at all.** The audited +197% run had
`cost_config: null`, `charges: 0`, and the summary is gated on
`cost_config.enabled` — so the panel rendered nothing and the user had no signal
that the headline was fully gross. Worse, the run-level "Apply realistic costs"
toggle WAS on: it governs index-side slippage, which a premium-native strategy
does not have, so it is completely inert there. Silence is not acceptable for a
number that is 100% gross.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.option_backtest import _compute_metrics  # noqa: E402

_CARD = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "pages",
                     "BacktestLab.jsx")


def _trade(entry_spread, exit_spread, qty, charges, gross):
    return {"status": "PAIRED", "direction": "CE",
            "option_pnl_pts": 1.0, "option_pnl_value": gross - charges,
            "gross_option_pnl_value": gross, "total_charges": charges,
            "entry_spread_pts": entry_spread, "exit_spread_pts": exit_spread,
            "quantity": qty}


# --------------------------------------------------------------------------- #
# spread becomes a first-class, visible cost
# --------------------------------------------------------------------------- #

def test_spread_cost_is_reported():
    m = _compute_metrics([_trade(0.3092, 0.2545, 325, 46.1, -5269.23)])
    assert m["total_spread_cost_value"] == round((0.3092 + 0.2545) * 325, 2)


def test_spread_cost_sums_across_trades():
    m = _compute_metrics([_trade(0.5, 0.5, 100, 10.0, 100.0),
                          _trade(1.0, 1.0, 200, 10.0, 100.0)])
    assert m["total_spread_cost_value"] == round(1.0 * 100 + 2.0 * 200, 2)


def test_total_cost_is_reported_so_the_user_need_not_add_up():
    """The number the audit found understated 3.7x."""
    m = _compute_metrics([_trade(0.3092, 0.2545, 325, 46.1, -5269.23)])
    assert m["total_cost_value"] == round(m["total_spread_cost_value"] + m["total_charges"], 2)


def test_a_run_with_no_cost_model_reports_zero_spread_not_none():
    """Distinguishable from 'not computed', and safe to render."""
    m = _compute_metrics([_trade(0.0, 0.0, 325, 0.0, 1000.0)])
    assert m["total_spread_cost_value"] == 0.0
    assert m["total_cost_value"] == 0.0


def test_unpaired_trades_contribute_nothing():
    rows = [_trade(0.5, 0.5, 100, 10.0, 100.0), {"status": "MISSING_ENTRY_CANDLE"}]
    m = _compute_metrics(rows)
    assert m["total_spread_cost_value"] == 100.0


def test_malformed_spread_fields_do_not_crash_or_poison_the_total():
    rows = [_trade(0.5, 0.5, 100, 10.0, 100.0),
            {"status": "PAIRED", "option_pnl_pts": 0.0, "option_pnl_value": 0.0,
             "entry_spread_pts": None, "exit_spread_pts": "x", "quantity": None,
             "total_charges": None}]
    m = _compute_metrics(rows)
    assert m["total_spread_cost_value"] == 100.0


def test_existing_metric_keys_are_untouched():
    """Additive only — every consumer of this dict must keep working."""
    m = _compute_metrics([_trade(0.5, 0.5, 100, 10.0, 100.0)])
    for k in ("paired_trade_count", "wins", "losses", "win_rate",
              "total_option_pnl_pts", "total_option_pnl_value", "total_charges",
              "total_gross_option_pnl_value"):
        assert k in m


# --------------------------------------------------------------------------- #
# the UI must show it, and must not stay silent on a gross run
# --------------------------------------------------------------------------- #

def _src():
    with open(_CARD, encoding="utf-8") as fh:
        return fh.read()


def test_the_cost_summary_shows_the_spread_line():
    src = _src()
    i = src.index("option-cost-summary")
    body = src[i:i + 2500]
    assert "total_spread_cost_value" in body, "spread is still invisible"


def test_the_cost_summary_shows_a_combined_total():
    src = _src()
    i = src.index("option-cost-summary")
    assert "total_cost_value" in src[i:i + 2500]


def test_a_gross_run_gets_an_explicit_warning():
    """Previously the whole cost block was gated off, so a 100%-gross headline
    rendered with no indication at all."""
    assert "option-costs-off-warning" in _src()


def test_the_warning_explains_the_inert_run_level_toggle():
    """The user had 'Apply realistic costs' ON and still got a gross number."""
    src = _src()
    i = src.index("option-costs-off-warning")
    body = src[i:i + 1400].lower()
    assert "gross" in body
    assert "apply rupee costs" in body or "option execution" in body
