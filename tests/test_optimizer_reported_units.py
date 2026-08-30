"""A reported number must carry the quantity it actually is.

Two confirmed instances of one defect class, both found by reconciling stored jobs against
recomputed truth rather than by reading the UI:

1. `best_so_far.value` was rendered as "spot obj". It is NOT: the field holds the Stage-1
   spot objective while the trial loop runs and is REPLACED at promotion with the option
   rupee P&L (or calmar). Checked across 12 consecutive completed jobs — wrong in 12/12.
   Example: 684,602 displayed as the spot objective where the real spot objective
   (total_pnl_pts x lot_size) was 333,689; another job showed 53,878 where the real figure
   was 218,607, ~4x out in the opposite direction.

2. `best_value_metric` was derived from `evaluation_mode` alone, so a job promoted by a
   CALMAR survival objective stored a calmar RATIO under the label `option_pnl_value`.
   Stored evidence: jobs fbf72695 (best_value 11.3084, real option P&L Rs 696,158.70) and
   427a5cb5 (4.904 vs Rs 1,535.39). The history table sorted those ratios against rupees.

Both are fixed by making the label follow the value AT THE POINT THE VALUE IS CHOSEN,
rather than re-deriving it later from configuration.
"""
from __future__ import annotations

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPT_PY = os.path.join(ROOT, "backend", "app", "optimizer.py")
OPT_JSX = os.path.join(ROOT, "frontend", "src", "pages", "Optimizer.jsx")


@pytest.fixture(scope="module")
def py() -> str:
    with open(OPT_PY, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def jsx() -> str:
    with open(OPT_JSX, encoding="utf-8") as fh:
        return fh.read()


# --- defect 1: the spot objective must survive promotion ---------------------

def test_spot_objective_is_carried_at_every_promotion_site(py):
    """Three sites replace best_so_far: survival winner, survival fallback, no-survival."""
    assert py.count('"spot_objective": best.get("spot_objective")') == 2      # survival + no-survival
    assert py.count('"spot_objective": fallback.get("spot_objective")') == 1  # no-survivor fallback


def test_spot_objective_is_actually_persisted(py):
    """best_so_far_doc is a whitelist — a field absent from it never reaches Mongo."""
    i = py.index("def best_so_far_doc(")
    body = py[i:i + 3000]
    assert '"spot_objective": (best_so_far or {}).get("spot_objective")' in body


def test_ui_no_longer_labels_the_promoted_value_as_the_spot_objective(jsx):
    i = jsx.index("const spotObjective")
    body = jsx[max(0, i - 1400):i + 400]
    # it must read the dedicated field, not `value`
    assert "bsf.spot_objective" in body
    assert "const promoted = bsf.spot_objective != null;" in body


def test_ui_hides_the_spot_note_rather_than_inventing_one(jsx):
    """Legacy jobs have no spot_objective; showing `value` there is the original bug."""
    i = jsx.index("promoted option ₹ (net of costs)")
    body = jsx[i:i + 400]
    assert "spotObjective != null ?" in body
    assert ": null}" in body


def test_running_jobs_still_show_a_live_spot_objective(jsx):
    """Before promotion `value` IS the spot objective — that path must be kept."""
    i = jsx.index("const spotObjective")
    body = jsx[i:i + 300]
    assert "finished || cancelled ? null : bsf.value" in body


# --- defect 2: the metric label must follow the value ------------------------

def test_metric_label_is_recorded_where_the_value_is_chosen(py):
    assert py.count('"value_metric": "option_pnl_value"') == 2   # fallback + no-survival
    i = py.index('"value_metric": ("survival_calmar"')
    body = py[i:i + 220]
    assert 'if survival.objective == "calmar"' in body
    assert '"option_pnl_value"' in body


def test_persisted_label_prefers_the_recorded_one(py):
    i = py.index('"best_value_metric":')
    body = py[i:i + 300]
    assert 'best_so_far.get("value_metric")' in body
    # the old derivation survives only as a fallback for jobs that recorded nothing
    assert 'if evaluation_mode == "option_rerank"' in body


def test_a_calmar_promotion_cannot_be_labelled_as_rupees(py):
    """The value and its label are chosen in the same expression, so they cannot drift."""
    i = py.index('"value": (best["survival"].get("calmar")')
    body = py[i:i + 400]
    assert '"value_metric":' in body
    assert 'survival_calmar' in body


# --- rendering ---------------------------------------------------------------

def test_formatter_renders_each_metric_in_its_own_unit(jsx):
    i = jsx.index("const fmtBestMetric")
    body = jsx[i:i + 1400]
    # rupees are formatted inline: the component-local fmtINR is NOT in module scope,
    # and referencing it here blanked the whole page at runtime while still compiling.
    assert 'metric === "option_pnl_value"' in body and "₹${fmtNum(Math.abs(v), 0)}" in body
    assert "fmtINR(v)" not in body
    assert 'metric === "survival_calmar"' in body and "calmar" in body
    # unknown/legacy metric falls back to a bare number rather than guessing a unit
    assert "toFixed(3)" in body


def test_formatter_refuses_a_unit_the_value_contradicts(jsx):
    """Legacy jobs store option_pnl_value as the label even for a calmar promotion
    (value 11.3084 vs a real option P&L of 696,158.70). Formatting that as "Rs 11"
    would assert a wrong unit instead of merely omitting one."""
    i = jsx.index("const fmtBestMetric")
    body = jsx[i:i + 1400]
    assert "corroborate" in body
    assert "Math.abs(Number(v) - Number(corroborate)) < 0.5" in body
    assert "&& trusted" in body


def test_history_column_and_toast_use_the_unit_aware_formatter(jsx):
    assert "fmtBestMetric(j.best_so_far.value, j.best_value_metric, j.best_metrics?.option_pnl_value)" in jsx
    assert "fmtBestMetric(j.best_value, j.best_value_metric, j.best_metrics?.option_pnl_value)" in jsx
    # the bare formatter must no longer be used for these two
    assert "fmtBest(j.best_so_far.value)" not in jsx
    assert "fmtBest(j.best_value)" not in jsx
